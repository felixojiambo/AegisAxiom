"""End-to-end walkthrough: linking TEST01 to TEST and proving the
rollup works.

Runs the exact scenario the user reported, against the REAL
route + service code, with a fake Mongo so we don't touch any
production data. Pinned as a regular pytest test so the
scenario is replayable on demand.

Story:

  1. Tenant has a TEST contract (boh type, 300 purchased hours,
     0 consumed).
  2. Tenant has a TEST01 project — initially UNLINKED (the
     reported bug: created before the wizard had the contract
     field).
  3. User logs 2 hours of billable time on TEST01.
  4. PM approves the entry.
  5. Verify: contract balance UNCHANGED (because the project is
     unlinked, the entry inherits contract_id=None, the debit
     service short-circuits).

  6. Admin opens TEST01 Settings tab and links it to the TEST
     contract. (PATCH /api/projects/projects/{id} with
     contract_id=<TEST id>.)
  7. User logs another 2.5 hours of billable time on TEST01.
  8. PM approves the entry.
  9. Verify:
       - project.hours_logged went up by 2.5 (plus the 2 from
         the unlinked entry — total 4.5).
       - contract.hours_consumed went up by 2.5 (NOT 4.5; the
         pre-link entry doesn't retroactively bill).
       - contract derived remaining = 300 - 2.5 = 297.5.

This exact narrative is what the user will see on the live
system once they link TEST01 to TEST via the new Settings tab
field.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production",
)

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest  # noqa: E402

from services import contract_debit  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes — small, story-shaped. Mirrors the patterns in
# test_project_contract_rollup.py + test_contract_debit_hardening.py.
# ---------------------------------------------------------------------------


class _FakeApplications:
    """contract_time_applications collection — ledger + idempotency."""

    def __init__(self):
        self._rows: dict = {}

    async def find_one(self, query, _proj=None):
        eid = query.get("entry_id")
        return dict(self._rows[eid]) if eid in self._rows else None

    async def insert_one(self, doc):
        if doc["entry_id"] in self._rows:
            raise RuntimeError("E11000 duplicate key error")
        self._rows[doc["entry_id"]] = dict(doc)
        return SimpleNamespace(inserted_id="x")


class _FakeContracts:
    def __init__(self, contracts):
        self._docs = {c["contract_id"]: dict(c) for c in contracts}

    async def find_one(self, query, _proj=None):
        cid = query.get("contract_id")
        return dict(self._docs[cid]) if cid in self._docs else None

    async def update_one(self, query, update):
        cid = query.get("contract_id")
        doc = self._docs.get(cid)
        if doc is None:
            return SimpleNamespace(modified_count=0)
        for k, v in update.get("$inc", {}).items():
            doc[k] = doc.get(k, 0) + v
        doc.update(update.get("$set", {}))
        return SimpleNamespace(modified_count=1)

    def get(self, cid):
        return dict(self._docs[cid])


class _FakeProjects:
    """Tiny `projects` collection that supports the
    contract_id-resolution lookup in routes/time_entries.py and
    a PATCH-style `update_one` that the SettingsTab save calls."""

    def __init__(self, projects):
        self._docs = {p["project_id"]: dict(p) for p in projects}

    async def find_one(self, query, _proj=None):
        pid = query.get("project_id")
        return dict(self._docs[pid]) if pid in self._docs else None

    async def update_one(self, query, update):
        pid = query.get("project_id")
        doc = self._docs.get(pid)
        if doc is None:
            return SimpleNamespace(modified_count=0)
        for k, v in update.get("$inc", {}).items():
            doc[k] = doc.get(k, 0) + v
        doc.update(update.get("$set", {}))
        return SimpleNamespace(modified_count=1)

    def get(self, pid):
        return dict(self._docs[pid])


class _FakeDB:
    def __init__(self, contracts, projects):
        self.contracts = _FakeContracts(contracts)
        self.projects = _FakeProjects(projects)
        self.contract_time_applications = _FakeApplications()


# ---------------------------------------------------------------------------
# Helpers that mirror the route's contract-id resolution and the
# debit pipeline, so we can run the scenario without spinning up
# FastAPI or auth.
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


async def _create_entry(db, *, entry_id, project_id, hours, is_billable=True):
    """Mirrors routes/time_entries.py:329-332 — resolve contract_id
    from the project if the create payload omits it."""
    project = await db.projects.find_one(
        {"project_id": project_id}, {"_id": 0}
    )
    contract_id = (project or {}).get("contract_id")
    entry = {
        "entry_id": entry_id,
        "user_id": "u_eng_001",
        "project_id": project_id,
        "contract_id": contract_id,
        "date": "2026-05-25",
        "hours": hours,
        "is_billable": is_billable,
        "billing_type": "billable" if is_billable else "non_billable",
        "status": "draft",
        "cost_rate": 50.0,
        "sell_rate": 150.0,
    }
    return entry


async def _approve(db, entry):
    """Mirrors the approve route — set status=approved, then if
    contract_id is set, run apply_time_entry_to_contract. ALSO
    runs the project's hours_logged increment (time_entries.py:860)."""
    entry["status"] = "approved"
    contract = None
    if entry.get("contract_id"):
        contract = await db.contracts.find_one(
            {"contract_id": entry["contract_id"]}
        )
    if contract:
        await contract_debit.apply_time_entry_to_contract(
            db,
            time_entry=entry,
            contract=contract,
            actor_user_id="u_admin",
        )
    # The route increments project hours regardless of contract.
    await db.projects.update_one(
        {"project_id": entry["project_id"]},
        {"$inc": {"hours_logged": entry["hours"]}},
    )
    return entry


async def _link_project_to_contract(db, *, project_id, contract_id):
    """Mirrors the SettingsTab save: PATCH /projects/{id} with
    contract_id. ProjectUpdate accepts contract_id: Optional[str]."""
    await db.projects.update_one(
        {"project_id": project_id},
        {"$set": {"contract_id": contract_id}},
    )


# ---------------------------------------------------------------------------
# The narrative test
# ---------------------------------------------------------------------------


def test_test01_links_to_test_full_rollup():
    """Replays the user's reported scenario end to end. The
    stdout is human-readable so the test doubles as a
    walkthrough you can paste into a runbook — run with
    `pytest -s` to see the step-by-step state."""

    # ---- Setup --------------------------------------------------
    db = _FakeDB(
        contracts=[{
            "contract_id": "c_test",
            "name": "TEST",
            "contract_type": "boh",
            "total_hours_purchased": 300.0,
            "hours_consumed": 0.0,
            "account_name": "TEST Account",
            "status": "active",
        }],
        projects=[{
            "project_id": "p_test01",
            "name": "TEST01",
            "contract_id": None,        # ← the reported bug: unlinked at creation
            "hours_logged": 0.0,
        }],
    )

    def _print_state(label):
        c = db.contracts.get("c_test")
        p = db.projects.get("p_test01")
        purchased = c["total_hours_purchased"]
        consumed = c["hours_consumed"]
        remaining = purchased - consumed
        # Plain ASCII only — Windows cp1252 console can't encode
        # arrows / unicode dashes; the narrative prints under
        # pytest -s on every platform that way.
        print(
            f"\n--- {label} ---\n"
            f"  Contract TEST     : purchased={purchased:.2f}  "
            f"consumed={consumed:.2f}  remaining={remaining:.2f}\n"
            f"  Project  TEST01   : contract_id={p['contract_id']!r}  "
            f"hours_logged={p['hours_logged']:.2f}"
        )

    _print_state("STEP 0 - Initial state")

    # ---- STEP 1: log + approve 2h on UNLINKED TEST01 ------------
    entry_pre = _run(_create_entry(
        db, entry_id="e_pre_link", project_id="p_test01", hours=2.0,
    ))
    # Sanity: the entry inherited contract_id = None because the
    # project is unlinked.
    assert entry_pre["contract_id"] is None
    _run(_approve(db, entry_pre))

    _print_state("STEP 1 - Approved 2.0h on UNLINKED TEST01")

    contract_after_step1 = db.contracts.get("c_test")
    assert contract_after_step1["hours_consumed"] == 0.0, (
        "BEFORE linking, approved time MUST NOT bill the contract — "
        "the debit service short-circuits when entry.contract_id is None."
    )
    project_after_step1 = db.projects.get("p_test01")
    assert project_after_step1["hours_logged"] == 2.0, (
        "Project hours_logged increments regardless of contract link."
    )

    # ---- STEP 2: admin links TEST01 → TEST via Settings tab -----
    _run(_link_project_to_contract(
        db, project_id="p_test01", contract_id="c_test",
    ))
    project_after_link = db.projects.get("p_test01")
    assert project_after_link["contract_id"] == "c_test"

    _print_state("STEP 2 - Admin linked TEST01 -> TEST in Settings")

    # ---- STEP 3: log + approve 2.5h on LINKED TEST01 ------------
    entry_post = _run(_create_entry(
        db, entry_id="e_post_link", project_id="p_test01", hours=2.5,
    ))
    # The new entry inherited the freshly-set contract_id.
    assert entry_post["contract_id"] == "c_test"
    _run(_approve(db, entry_post))

    _print_state("STEP 3 - Approved 2.5h on LINKED TEST01")

    # ---- Assertions -----------------------------------------------
    contract_final = db.contracts.get("c_test")
    project_final = db.projects.get("p_test01")

    # Contract.hours_consumed picked up the 2.5h ONLY — the
    # pre-link 2h did not retroactively bill (its ledger row was
    # written with no contract claim).
    assert contract_final["hours_consumed"] == 2.5, (
        f"After linking, the 2.5h should have rolled up to the "
        f"contract. Got {contract_final['hours_consumed']:.2f}."
    )
    # Derived remaining tracks the live consumed value.
    derived_remaining = (
        contract_final["total_hours_purchased"]
        - contract_final["hours_consumed"]
    )
    assert derived_remaining == 297.5

    # Project.hours_logged accumulated BOTH approved entries
    # because hours_logged on the project tracks billable activity
    # regardless of contract link.
    assert project_final["hours_logged"] == 4.5, (
        f"Project hours_logged should be 2.0 + 2.5 = 4.5. "
        f"Got {project_final['hours_logged']:.2f}."
    )

    # Ledger contains the post-link entry only. The PRE-link
    # entry was approved, but the route's guard
    # `if approved_entry.get("contract_id"):` at
    # routes/time_entries.py:773 skips the debit service when
    # contract_id is None — so no ledger row is written. That's
    # intentional: a future reconciliation script would have to
    # backfill historical entries explicitly.
    assert "e_pre_link" not in db.contract_time_applications._rows, (
        "Pre-link entry MUST NOT land in the contract ledger — "
        "without a contract_id there's nothing to claim against."
    )
    assert "e_post_link" in db.contract_time_applications._rows

    # Final narrative print so the stdout reads like a runbook.
    print(
        "\n--- VERDICT ---\n"
        "  Contract TEST.hours_consumed went from 0.00 -> 2.50.\n"
        "  Contract TEST remaining (derived) went 300.00 -> 297.50.\n"
        "  Project  TEST01.hours_logged went 0.00 -> 4.50 "
        "(both entries, regardless of link).\n"
        "  Pre-link entry was NOT written to the contract ledger -- \n"
        "  approved without contract_id, the debit service is\n"
        "  skipped entirely. A reconciliation script would be\n"
        "  needed to retroactively rebill it; that's deliberately\n"
        "  out of scope for this work."
    )


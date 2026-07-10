"""Tests for the idempotent Zoho Contracts importer.

Verifies:
  * Dry-run never calls a Mongo writer.
  * --execute writes; default does not.
  * Repeated --execute upserts the same Mongo document — never
    creates a duplicate for the same Zoho Contract Record Id.
  * Orphan-deal rows are refused unless --allow-orphan-deals is set.
  * Per-row payload preserves raw_source, external_ids, and the
    hour snapshots.
  * No collection other than `contracts` is written to.
  * Tests don't require backend/imports/zoho/raw.
  * Importer module asserts no Zoho-API or DB-writer leak into the
    script source.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

# Required at import time by any backend code path that pulls auth_helper.
os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production",
)

import pytest


_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# pylint: disable=wrong-import-position
import scripts.zoho_contracts_import as importer  # noqa: E402


# ---------------------------------------------------------------------------
# Fake Mongo
# ---------------------------------------------------------------------------


class _UpsertResult:
    def __init__(self, upserted_id: Any | None):
        self.upserted_id = upserted_id


class _FakeContractsCollection:
    def __init__(self):
        self.docs: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.find_calls: list[dict[str, Any]] = []

    async def find_one(self, query, projection=None):
        self.find_calls.append({"query": query, "projection": projection})
        ext = (query or {}).get("external_contract_id")
        for d in self.docs:
            if d.get("external_contract_id") == ext:
                return {k: v for k, v in d.items() if k != "_id"}
        return None

    async def update_one(self, query, update, upsert=False):
        self.update_calls.append({
            "query": query, "update": update, "upsert": upsert,
        })
        ext = (query or {}).get("external_contract_id")
        # Locate existing.
        existing_idx = next(
            (i for i, d in enumerate(self.docs)
             if d.get("external_contract_id") == ext),
            None,
        )
        if existing_idx is not None:
            d = self.docs[existing_idx]
            d.update(update.get("$set", {}))
            return _UpsertResult(upserted_id=None)
        if not upsert:
            return _UpsertResult(upserted_id=None)
        new_doc: dict[str, Any] = {
            "external_contract_id": ext,
        }
        new_doc.update(update.get("$setOnInsert", {}))
        new_doc.update(update.get("$set", {}))
        self.docs.append(new_doc)
        return _UpsertResult(upserted_id=new_doc.get("contract_id") or "_id")


class _FakeDB:
    def __init__(self):
        self.contracts = _FakeContractsCollection()
        self.accessed: list[str] = []

    def __getattr__(self, name: str):
        # FastAPI / motor patterns sometimes use ``db.foo`` access for
        # arbitrary collections. Anything other than ``contracts`` is
        # forbidden by this slice — surface the attempt as a failure.
        self.accessed.append(name)
        raise AttributeError(
            f"importer touched unexpected collection {name!r}"
        )


# ---------------------------------------------------------------------------
# Plan helpers
# ---------------------------------------------------------------------------


def _candidate(
    *,
    zoho_contract_id: str,
    contract_number: str,
    account_id: str = "ZA0000000001",
    deal_id: str = "ZD0000000001",
    contract_type_raw: str = "BOH",
    invoice_type_raw: str = "Invoice at signing, due net30",
    status_raw: str = "Approved",
    total: float | None = 100.0,
    remaining: float | None = 40.0,
    consumed: float | None = 60.0,
    pct: float | None = 60.0,
) -> dict[str, Any]:
    return {
        "external_ids": {
            "zoho_contract_id": zoho_contract_id,
            "zoho_account_id": account_id,
            "zoho_deal_id": deal_id,
        },
        "contract_number": contract_number,
        "name": f"Synthetic Contract {zoho_contract_id}",
        "account_name": "Synthetic Account Alpha",
        "opportunity_name": "Synthetic Deal Alpha",
        "contract_type_raw": contract_type_raw,
        "invoice_type_raw": invoice_type_raw,
        "status_raw": status_raw,
        "total_hours_snapshot": total,
        "remaining_hours_snapshot": remaining,
        "consumed_hours_snapshot": consumed,
        "consumed_percent_snapshot": pct,
        "source_created_at": "2024-01-01 09:00:00",
        "source_modified_at": "2024-03-01 09:00:00",
        "raw_source": {
            "Record Id": zoho_contract_id,
            "Contract Number": contract_number,
            "Contract Type": contract_type_raw,
            "Contract Status": status_raw,
        },
    }


def _planned_row(
    *,
    zoho_contract_id: str = "ZC0000000001",
    contract_number: str = "CN-001",
    deal_resolved: bool = True,
) -> dict[str, Any]:
    candidate = _candidate(
        zoho_contract_id=zoho_contract_id,
        contract_number=contract_number,
    )
    return {
        "source": "zoho",
        "zoho_contract_id": zoho_contract_id,
        "contract_number": contract_number,
        "name": candidate["name"],
        "account": {
            "zoho_account_id": "ZA0000000001",
            "name": "Synthetic Account Alpha",
            "resolved": True,
        },
        "deal": {
            "zoho_deal_id": candidate["external_ids"]["zoho_deal_id"],
            "name": "Synthetic Deal Alpha",
            "resolved": deal_resolved,
        },
        "aegisaxiom_contract_candidate": candidate,
        "warnings": [],
    }


def _orphan_blocked_row(*, zoho_contract_id="ZC9999") -> dict[str, Any]:
    """Mirror the plan's blocked[] shape for an orphan-deal row."""
    candidate = _candidate(
        zoho_contract_id=zoho_contract_id,
        contract_number="CN-orphan",
        deal_id="ZD9999999999",
    )
    return {
        "source": "zoho",
        "zoho_contract_id": zoho_contract_id,
        "contract_number": "CN-orphan",
        "name": candidate["name"],
        "account": {
            "zoho_account_id": "ZA0000000001",
            "name": "Synthetic Account Alpha",
            "resolved": True,
        },
        "deal": {
            "zoho_deal_id": "ZD9999999999",
            "name": "Synthetic Deal Phantom",
            "resolved": False,
        },
        "aegisaxiom_contract_candidate": candidate,
        "warnings": [
            {"kind": "orphan_zoho_deal_id", "zoho_deal_id": "ZD9999999999"},
        ],
        "blockers": [
            {
                "kind": "orphan_zoho_deal_id_default_policy",
                "zoho_deal_id": "ZD9999999999",
            },
        ],
    }


def _plan(
    *,
    contracts: list[dict[str, Any]],
    blocked: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "source_files": {"contracts": "x", "accounts": "y", "deals": "z"},
        "options": {"allow_orphan_deals": False},
        "total_contracts": len(contracts) + len(blocked or []),
        "planned_import_count": len(contracts),
        "blocked_count": len(blocked or []),
        "warning_count": 0,
        "ready_for_import": not blocked,
        "contracts": contracts,
        "blocked": blocked or [],
        "warnings": [],
        "enum_summary": {},
        "hour_anomaly_summary": {},
    }


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def test_dry_run_does_not_call_writers():
    db = _FakeDB()
    plan = _plan(contracts=[_planned_row()])
    result = asyncio.run(importer.import_from_plan(
        plan, db=db, execute=False, allow_orphan_deals=False,
        plan_path="<inline>",
    ))
    assert result.dry_run is True
    assert result.inserted + result.updated == 1
    assert db.contracts.update_calls == []
    # Dry-run *may* read; it must NOT write.
    assert all(c["upsert"] is False for c in db.contracts.update_calls)


def test_dry_run_distinguishes_would_insert_vs_would_update():
    db = _FakeDB()
    # Pre-seed an existing imported contract.
    db.contracts.docs.append({
        "contract_id": "contract_existing",
        "external_contract_id": "ZC0000000001",
    })
    plan = _plan(contracts=[
        _planned_row(zoho_contract_id="ZC0000000001"),
        _planned_row(zoho_contract_id="ZC0000000002", contract_number="CN-002"),
    ])
    result = asyncio.run(importer.import_from_plan(
        plan, db=db, execute=False, allow_orphan_deals=False,
        plan_path="<inline>",
    ))
    actions = sorted(r["action"] for r in result.per_row)
    assert actions == ["would_insert", "would_update"]


def test_default_main_mode_is_dry_run(tmp_path, monkeypatch):
    # The CLI defaults to dry-run even when neither flag is passed.
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(_plan(contracts=[_planned_row()])),
        encoding="utf-8",
    )
    out_path = tmp_path / "out.json"
    code = importer.main([
        "--plan", str(plan_path),
        "--out", str(out_path),
    ])
    assert code == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["dry_run"] is True
    assert payload["db_connected"] is False


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------


def test_execute_writes_only_with_execute_flag():
    db = _FakeDB()
    plan = _plan(contracts=[_planned_row()])
    result = asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_orphan_deals=False,
        plan_path="<inline>",
    ))
    assert result.dry_run is False
    assert result.inserted == 1
    assert len(db.contracts.docs) == 1
    inserted = db.contracts.docs[0]
    assert inserted["external_contract_id"] == "ZC0000000001"
    assert inserted["import_source"] == "zoho"
    assert inserted["status"] == "draft"
    # contract_id is AEGISAXIOM-generated, prefix "contract_".
    assert inserted["contract_id"].startswith("contract_")


def test_execute_without_db_refuses_to_write():
    plan = _plan(contracts=[_planned_row()])
    result = asyncio.run(importer.import_from_plan(
        plan, db=None, execute=True, allow_orphan_deals=False,
        plan_path="<inline>",
    ))
    assert result.errors, "must refuse to write when db handle is None"
    assert result.inserted == 0
    assert result.updated == 0


def test_repeat_execute_is_idempotent_no_duplicates():
    db = _FakeDB()
    plan = _plan(contracts=[_planned_row()])

    first = asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_orphan_deals=False,
        plan_path="<inline>",
    ))
    second = asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_orphan_deals=False,
        plan_path="<inline>",
    ))

    assert first.inserted == 1 and first.updated == 0
    assert second.inserted == 0 and second.updated == 1
    # Exactly one document — same external_contract_id.
    assert len(db.contracts.docs) == 1
    # contract_id was NOT regenerated on the second pass.
    first_contract_id = db.contracts.docs[0]["contract_id"]
    third = asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_orphan_deals=False,
        plan_path="<inline>",
    ))
    assert third.inserted == 0 and third.updated == 1
    assert len(db.contracts.docs) == 1
    assert db.contracts.docs[0]["contract_id"] == first_contract_id


# ---------------------------------------------------------------------------
# Orphan-deal policy
# ---------------------------------------------------------------------------


def test_orphan_deal_in_contracts_refused_without_flag():
    # A hand-edited plan that leaked an orphan into contracts[]
    # without setting the flag must still be refused.
    db = _FakeDB()
    plan = _plan(contracts=[
        _planned_row(),
        _planned_row(zoho_contract_id="ZC9999", deal_resolved=False),
    ])
    result = asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_orphan_deals=False,
        plan_path="<inline>",
    ))
    assert result.inserted == 1
    assert result.skipped == 1
    assert result.orphan_deal_count == 1
    actions = {r["action"] for r in result.per_row}
    assert "skipped" in actions


def test_orphan_deal_in_blocked_is_promoted_when_flag_set():
    # Mirror the plan's default policy: orphan in blocked[]. With the
    # flag, it should be considered and inserted.
    db = _FakeDB()
    plan = _plan(
        contracts=[_planned_row()],
        blocked=[_orphan_blocked_row()],
    )
    result_no_flag = asyncio.run(importer.import_from_plan(
        plan, db=db, execute=False, allow_orphan_deals=False,
        plan_path="<inline>",
    ))
    assert result_no_flag.considered == 1
    assert result_no_flag.blocked_excluded == 1

    db2 = _FakeDB()
    result_with_flag = asyncio.run(importer.import_from_plan(
        plan, db=db2, execute=True, allow_orphan_deals=True,
        plan_path="<inline>",
    ))
    assert result_with_flag.considered == 2
    assert result_with_flag.inserted == 2
    # The orphan row landed with external_ids.zoho_deal_id preserved.
    orphan = next(
        d for d in db2.contracts.docs
        if d.get("external_contract_id") == "ZC9999"
    )
    assert orphan["external_ids"]["zoho_deal_id"] == "ZD9999999999"
    assert orphan["import_deal_resolved"] is False


def test_orphan_count_includes_planned_and_promoted_orphans():
    db = _FakeDB()
    plan = _plan(
        contracts=[
            _planned_row(),
            _planned_row(zoho_contract_id="ZC0002", deal_resolved=False),
        ],
        blocked=[_orphan_blocked_row(zoho_contract_id="ZC0003")],
    )
    result = asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_orphan_deals=True,
        plan_path="<inline>",
    ))
    assert result.orphan_deal_count == 2


# ---------------------------------------------------------------------------
# Structural blockers (non-orphan) stay refused
# ---------------------------------------------------------------------------


def test_non_policy_blockers_are_never_promoted():
    db = _FakeDB()
    plan = _plan(
        contracts=[_planned_row()],
        blocked=[{
            "source": "zoho",
            "zoho_contract_id": "ZC0BAD",
            "contract_number": None,
            "name": "Synthetic Bad",
            "account": {"zoho_account_id": "ZA1", "name": "X", "resolved": True},
            "deal": {"zoho_deal_id": "ZD1", "name": "Y", "resolved": True},
            "aegisaxiom_contract_candidate": _candidate(
                zoho_contract_id="ZC0BAD", contract_number="BAD",
            ),
            "warnings": [],
            "blockers": [{"kind": "invalid_numeric", "field": "X"}],
        }],
    )
    result = asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_orphan_deals=True,
        plan_path="<inline>",
    ))
    assert result.inserted == 1  # only the clean contract row
    assert result.blocked_excluded == 1
    ext_ids = {d["external_contract_id"] for d in db.contracts.docs}
    assert "ZC0BAD" not in ext_ids


# ---------------------------------------------------------------------------
# Payload preservation
# ---------------------------------------------------------------------------


def test_raw_source_is_preserved():
    db = _FakeDB()
    plan = _plan(contracts=[_planned_row()])
    asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_orphan_deals=False,
        plan_path="<inline>",
    ))
    inserted = db.contracts.docs[0]
    assert inserted["raw_source"]["Record Id"] == "ZC0000000001"
    assert inserted["raw_source"]["Contract Type"] == "BOH"


def test_external_ids_are_preserved():
    db = _FakeDB()
    plan = _plan(contracts=[_planned_row()])
    asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_orphan_deals=False,
        plan_path="<inline>",
    ))
    d = db.contracts.docs[0]
    assert d["external_contract_id"] == "ZC0000000001"
    assert d["external_ids"]["zoho_contract_id"] == "ZC0000000001"
    assert d["external_ids"]["zoho_account_id"] == "ZA0000000001"
    assert d["external_ids"]["zoho_deal_id"] == "ZD0000000001"


def test_hour_snapshots_are_preserved():
    db = _FakeDB()
    plan = _plan(contracts=[_planned_row()])
    asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_orphan_deals=False,
        plan_path="<inline>",
    ))
    d = db.contracts.docs[0]
    assert d["imported_total_hours"] == 100.0
    assert d["imported_remaining_hours"] == 40.0
    assert d["imported_consumed_hours"] == 60.0
    assert d["imported_consumed_percent"] == 60.0
    # Authoritative AEGISAXIOM counters stay at zero — Time Entries import
    # is what will rebuild them.
    assert d["total_hours_purchased"] == 0.0
    assert d["hours_consumed"] == 0.0


def test_raw_enum_values_preserved_and_status_is_draft():
    db = _FakeDB()
    plan = _plan(contracts=[_planned_row()])
    asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_orphan_deals=False,
        plan_path="<inline>",
    ))
    d = db.contracts.docs[0]
    assert d["contract_type_raw"] == "BOH"
    assert d["status_raw"] == "Approved"
    assert d["invoice_type_raw"] == "Invoice at signing, due net30"
    assert d["contract_type"] == "boh"  # mapped from the documented table
    assert d["status"] == "draft"  # all imports start as draft


def test_unmapped_contract_type_falls_back_to_other():
    db = _FakeDB()
    row = _planned_row()
    row["aegisaxiom_contract_candidate"]["contract_type_raw"] = "TBD 1"
    plan = _plan(contracts=[row])
    asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_orphan_deals=False,
        plan_path="<inline>",
    ))
    d = db.contracts.docs[0]
    assert d["contract_type"] == "other"
    assert d["contract_type_raw"] == "TBD 1"


# ---------------------------------------------------------------------------
# No other collection is touched
# ---------------------------------------------------------------------------


def test_only_contracts_collection_is_touched_on_execute():
    db = _FakeDB()
    plan = _plan(contracts=[_planned_row()])
    asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_orphan_deals=False,
        plan_path="<inline>",
    ))
    # The fake DB raises if any non-`contracts` attribute is accessed.
    # Reaching this line means the importer only used `db.contracts`.
    assert db.accessed == []


# ---------------------------------------------------------------------------
# Source-level safety
# ---------------------------------------------------------------------------


def test_importer_source_does_not_import_zoho_api():
    src = Path(importer.__file__).read_text(encoding="utf-8")
    forbidden = (
        "zoho_api",
        "import httpx",
        "import requests",
    )
    for token in forbidden:
        assert token not in src, (
            f"Importer must not import {token!r}; it consumes the "
            "local plan only."
        )


def test_importer_source_only_writes_contracts_collection():
    src = Path(importer.__file__).read_text(encoding="utf-8")
    # Every writer call must go through db.contracts. The dataclass
    # `field` import from dataclasses is unrelated and uses the
    # `field=` keyword — guard the assertion against that false
    # positive by checking the literal preceding ".update_one(".
    assert "db.contracts.update_one(" in src
    forbidden = (
        "db.accounts.update_one(",
        "db.accounts.insert_one(",
        "db.deals.update_one(",
        "db.deals.insert_one(",
        "db.projects.update_one(",
        "db.projects.insert_one(",
        "db.time_entries.update_one(",
        "db.time_entries.insert_one(",
        "db.contract_time_applications.insert_one(",
    )
    for token in forbidden:
        assert token not in src, (
            f"Importer must not call {token!r}; this slice writes "
            "only to the contracts collection."
        )


def test_tests_do_not_require_real_imports_directory():
    real = _BACKEND_DIR / "imports" / "zoho" / "raw"
    if real.exists():
        assert real.is_dir()
    # The fake plan above is fully synthetic; no fixture file is
    # required for these tests to run.
    assert not any(
        "Acme" in str(v) or "@simplimeta.com" in str(v)
        for v in _candidate(
            zoho_contract_id="ZC1", contract_number="X",
        ).values()
        if isinstance(v, str)
    )


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


def test_main_dry_run_writes_report(tmp_path, monkeypatch):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(_plan(contracts=[_planned_row()])),
        encoding="utf-8",
    )
    out_path = tmp_path / "result.json"
    code = importer.main([
        "--plan", str(plan_path),
        "--out", str(out_path),
        "--dry-run",
    ])
    assert code == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["dry_run"] is True
    assert payload["inserted"] + payload["updated"] >= 1


def test_main_execute_without_mongo_env_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(_plan(contracts=[_planned_row()])),
        encoding="utf-8",
    )
    code = importer.main([
        "--plan", str(plan_path),
        "--out", str(tmp_path / "result.json"),
        "--execute",
    ])
    assert code == 1


def test_main_rejects_missing_plan_file(tmp_path, capsys):
    code = importer.main([
        "--plan", str(tmp_path / "does_not_exist.json"),
        "--out", str(tmp_path / "result.json"),
    ])
    assert code == 1


def test_main_rejects_malformed_plan_file(tmp_path):
    bad = tmp_path / "plan.json"
    bad.write_text("{not valid json", encoding="utf-8")
    code = importer.main([
        "--plan", str(bad),
        "--out", str(tmp_path / "result.json"),
    ])
    assert code == 1

"""Tests for the Zoho Contracts missing-fields apply script.

The PART 4 executor takes the dry-run candidate patches and writes
them to db.contracts when --execute is set. These tests pin:

  * dry-run default produces a JSON report but no DB writes
  * --execute writes only to db.contracts (never resources /
    resource_types / hourly_rate / project_ids / projects /
    approval config / audit fields)
  * --overwrite-existing gates canonical-field conflicts
  * --promote-status gates the status column
  * --allow-create gates new-row inserts
  * idempotent on re-run
  * synthetic fixtures only — no real customer data
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production",
)

import pytest


_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# pylint: disable=wrong-import-position
import scripts.zoho_contracts_missing_fields_apply as apply_mod  # noqa: E402
from services.zoho_contract_csv import parse_contract_csv_row  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic fixtures (re-uses the parser test helper shape)
# ---------------------------------------------------------------------------


def _zoho_row(**overrides) -> dict[str, str]:
    base = {
        "Record Id": "zcrm_001",
        "Contract Name": "Synthetic PSA",
        "Contract Owner.id": "zcrm_owner_1",
        "Contract Owner": "Owner Name",
        "Created By.id": "zcrm_creator_1",
        "Created By": "Creator Name",
        "Created Time": "2024-04-10 21:47:10",
        "Last Activity Time": "2025-04-29 21:21:51",
        "Tag": "",
        "Unsubscribed Mode": "",
        "Unsubscribed Time": "",
        "Locked": "false",
        "Account.id": "zcrm_acct_1",
        "Account": "Synthetic Account",
        "Total Contract Hours": "",
        "Deals.id": "zcrm_deal_1",
        "Deals": "Synthetic Deal",
        "Contract Type": "BOH",
        "Invoice Type": "net30",
        "Remaining Hour's": "1",
        "Contract Number": "CN-100008",
        "Consumed Hour's %": "99.26",
        "Contract Status": "Approved",
        "Total Consumed Hour's": "135",
        "Number of Invoices": "1",
        "Bill On Actual Hours": "0",
        "Hours To Be Paid": "0",
        "Paid Hours": "0",
        "AP issues": "",
        "Total Contract Hour's": "136",
        "Status": "Active",
        "Connected To.module": "",
        "Connected To.id": "",
    }
    base.update(overrides)
    return base


def _aegisaxiom_contract(
    *,
    contract_id: str = "contract_aegisaxiom_001",
    external_contract_id: str = "zcrm_001",
    contract_number: str = "CN-100008",
    contract_type: str = "boh",
    status: str = "draft",
    total_hours: Any = 0,
    total_hours_purchased: Any = 0,
    hours_consumed: Any = 0,
    resources: list | None = None,
    resource_types: list | None = None,
    hourly_rate: Any = None,
    project_ids: list | None = None,
    invoice_type_raw: Any = None,
    contract_status_raw: Any = None,
    source_created_by: Any = None,
    ap_issues_raw: Any = None,
    number_of_invoices: Any = None,
) -> dict[str, Any]:
    return {
        "contract_id": contract_id,
        "external_contract_id": external_contract_id,
        "external_ids": {"zoho_contract_id": external_contract_id},
        "contract_number": contract_number,
        "name": "Synthetic PSA",
        "contract_type": contract_type,
        "status": status,
        "total_hours": total_hours,
        "total_hours_purchased": total_hours_purchased,
        "hours_consumed": hours_consumed,
        "resources": resources or [],
        "resource_types": resource_types or [],
        "hourly_rate": hourly_rate,
        "project_ids": project_ids or [],
        "invoice_type_raw": invoice_type_raw,
        "contract_status_raw": contract_status_raw,
        "source_created_by": source_created_by,
        "ap_issues_raw": ap_issues_raw,
        "number_of_invoices": number_of_invoices,
        "import_source": "zoho",
        "is_test_data": False,
    }


# ---------------------------------------------------------------------------
# Fake Mongo — covers find / find_one / update_one / insert_one used
# by the apply script. Tracks update_calls for assertions.
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        self._i = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._i)
        except StopIteration as e:
            raise StopAsyncIteration from e


class _Coll:
    def __init__(self, docs):
        self.docs = list(docs)
        self.update_calls: list[dict[str, Any]] = []
        self.insert_calls: list[dict[str, Any]] = []

    def find(self, query=None, _projection=None):
        rows = [d for d in self.docs if _match(d, query or {})]
        return _Cursor(rows)

    async def update_one(self, query, update, upsert=False):
        self.update_calls.append({"query": query, "update": update, "upsert": upsert})
        cid = (query or {}).get("contract_id")
        for d in self.docs:
            if d.get("contract_id") == cid:
                d.update(update.get("$set", {}))
                return {"matched": 1}
        return {"matched": 0}

    async def insert_one(self, doc):
        self.insert_calls.append(doc)
        self.docs.append(doc)
        return {"inserted_id": doc.get("contract_id")}


def _match(doc, q):
    for k, v in q.items():
        if isinstance(v, dict) and "$ne" in v:
            if doc.get(k) == v["$ne"]:
                return False
        else:
            if doc.get(k) != v:
                return False
    return True


class _FakeDB:
    def __init__(self, contracts=None):
        self.contracts = _Coll(contracts or [])
        self.accessed: list[str] = []

    def __getattr__(self, name):
        # The apply script should ONLY touch db.contracts. Any other
        # collection access surfaces as a test failure via this hook.
        self.accessed.append(name)
        raise AttributeError(
            f"apply script touched unexpected collection {name!r}"
        )


# ---------------------------------------------------------------------------
# build_candidate_patch — the pure planner
# ---------------------------------------------------------------------------


def _patch(aegisaxiom, **flags):
    parsed = parse_contract_csv_row(_zoho_row())
    return apply_mod.build_candidate_patch(
        parsed, aegisaxiom,
        overwrite_existing=flags.get("overwrite_existing", False),
        promote_status=flags.get("promote_status", False),
    )


def test_patch_hydrates_missing_additive_fields():
    aegisaxiom = _aegisaxiom_contract()
    patch = _patch(aegisaxiom)
    s = patch["set"]
    assert s["imported_total_hours"] == 136.0
    assert s["imported_remaining_hours"] == 1.0
    assert s["imported_consumed_hours"] == 135.0
    assert s["imported_consumed_percent"] == 99.26
    assert s["invoice_type_raw"] == "net30"
    assert s["contract_status_raw"] == "Approved"
    assert s["source_created_by"] == "Creator Name"
    assert s["contract_owner_name_raw"] == "Owner Name"
    assert s["number_of_invoices"] == 1.0
    assert s["bill_on_actual_hours"] == 0.0  # explicit zero is a value
    assert s["paid_hours"] == 0.0


def test_patch_does_not_touch_resources_or_rates_or_project_ids():
    aegisaxiom = _aegisaxiom_contract(
        resources=[{"user_id": "u_a"}],
        resource_types=[{"id": "rt_1"}],
        hourly_rate=175.0,
        project_ids=["proj_existing"],
    )
    patch = _patch(aegisaxiom)
    forbidden = {"resources", "resource_types", "hourly_rate", "project_ids"}
    assert forbidden.isdisjoint(set(patch["set"].keys())), (
        "Patch must never touch resources / resource_types / hourly_rate / project_ids"
    )


def test_patch_promotes_canonical_total_hours_only_for_boh_when_blank():
    """AEGISAXIOM-side total_hours==0 + boh contract type -> promote
    imported_total_hours into total_hours / total_hours_purchased."""
    aegisaxiom = _aegisaxiom_contract(contract_type="boh", total_hours=0, total_hours_purchased=0)
    patch = _patch(aegisaxiom)
    assert patch["set"]["total_hours"] == 136.0
    assert patch["set"]["total_hours_purchased"] == 136.0
    assert any(
        "total_hours" in tag for tag in patch["needs_finance_review"]
    )


def test_patch_does_not_overwrite_existing_total_hours_without_flag():
    aegisaxiom = _aegisaxiom_contract(
        contract_type="boh", total_hours=200.0, total_hours_purchased=200.0,
    )
    patch = _patch(aegisaxiom)
    assert "total_hours" not in patch["set"]
    # Conflict was recorded for audit instead.
    assert any(c["field"] == "total_hours" for c in patch["conflicts"])


def test_patch_overwrites_existing_total_hours_with_flag():
    aegisaxiom = _aegisaxiom_contract(
        contract_type="boh", total_hours=200.0, total_hours_purchased=200.0,
    )
    patch = _patch(aegisaxiom, overwrite_existing=True)
    assert patch["set"]["total_hours"] == 136.0
    assert any(
        c["field"] == "total_hours" and c["action"] == "overwritten"
        for c in patch["conflicts"]
    )


def test_patch_status_promotion_off_by_default():
    aegisaxiom = _aegisaxiom_contract(status="draft")
    patch = _patch(aegisaxiom)  # promote_status defaults False
    assert "status" not in patch["set"]


def test_patch_status_promotion_when_flag_set_and_aegisaxiom_is_draft():
    aegisaxiom = _aegisaxiom_contract(status="draft")
    patch = _patch(aegisaxiom, promote_status=True)
    assert patch["set"]["status"] == "active"  # Approved -> active


def test_patch_status_promotion_never_overrides_non_draft_aegisaxiom_status():
    aegisaxiom = _aegisaxiom_contract(status="closed")
    patch = _patch(aegisaxiom, promote_status=True)
    assert "status" not in patch["set"]
    assert any(c["field"] == "status" for c in patch["conflicts"])


def test_patch_writes_raw_snapshot_under_import_raw_zoho_contract_csv():
    aegisaxiom = _aegisaxiom_contract()
    patch = _patch(aegisaxiom)
    assert "import_raw" in patch["set"]
    assert "zoho_contract_csv" in patch["set"]["import_raw"]
    snapshot = patch["set"]["import_raw"]["zoho_contract_csv"]
    # Spot-check several columns from each section.
    for hdr in (
        "Record Id", "Contract Owner", "Account.id", "Deals",
        "Contract Type", "Total Contract Hour's", "Remaining Hour's",
        "Contract Status",
    ):
        assert hdr in snapshot


def test_patch_does_not_replace_identical_snapshot():
    """Idempotency: re-running with the same CSV row against a
    contract whose import_raw.zoho_contract_csv already matches
    should not propose to re-set the snapshot."""
    parsed = parse_contract_csv_row(_zoho_row())
    aegisaxiom = _aegisaxiom_contract()
    aegisaxiom["import_raw"] = {"zoho_contract_csv": parsed.raw_source}
    patch = apply_mod.build_candidate_patch(
        parsed, aegisaxiom, overwrite_existing=False, promote_status=False,
    )
    assert "import_raw" not in patch["set"]


def test_patch_preserves_zero_in_aegisaxiom_when_zoho_is_blank():
    """AEGISAXIOM has 0 and Zoho is blank -> never overwrite the AEGISAXIOM 0
    with a None coming from a blank Zoho cell."""
    row = _zoho_row(bill_on_actual_hours="")
    parsed = parse_contract_csv_row(row)
    aegisaxiom = _aegisaxiom_contract()
    aegisaxiom["bill_on_actual_hours"] = 0  # already 0 in AEGISAXIOM
    patch = apply_mod.build_candidate_patch(
        parsed, aegisaxiom, overwrite_existing=False, promote_status=False,
    )
    assert "bill_on_actual_hours" not in patch["set"]


def test_patch_external_ids_subkeys_filled_when_missing():
    """external_ids is patched piecemeal — missing subkeys filled,
    existing subkeys preserved."""
    aegisaxiom = _aegisaxiom_contract()
    aegisaxiom["external_ids"] = {"zoho_contract_id": "zcrm_001"}  # only one set
    patch = _patch(aegisaxiom)
    ext = patch["set"].get("external_ids", {})
    assert ext.get("zoho_account_id") == "zcrm_acct_1"
    assert ext.get("zoho_deal_id") == "zcrm_deal_1"
    # Existing subkey is preserved verbatim.
    assert ext.get("zoho_contract_id") == "zcrm_001"


# ---------------------------------------------------------------------------
# run_apply — dry-run does not write
# ---------------------------------------------------------------------------


def _write_csv(tmp_path, rows: list[dict[str, str]]) -> str:
    headers = list(_zoho_row().keys())
    path = tmp_path / "Contract.csv"
    lines = [",".join(headers)]
    for r in rows:
        lines.append(",".join(_csv_cell(r.get(h, "")) for h in headers))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _csv_cell(value: str) -> str:
    s = str(value)
    if "," in s or '"' in s:
        s = '"' + s.replace('"', '""') + '"'
    return s


def test_dry_run_does_not_call_update_one(tmp_path, monkeypatch):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)

    db = _FakeDB(contracts=[_aegisaxiom_contract()])
    monkeypatch.setattr(apply_mod, "_connect_db", lambda: _fake_connect(db))

    csv_path = _write_csv(tmp_path, [_zoho_row()])
    report = asyncio.run(apply_mod.run_apply(
        csv_path=csv_path, out_path=None, execute=False,
        overwrite_existing=False, promote_status=False, allow_create=False,
    ))

    assert report["mode"] == "dry_run"
    assert report["action_counts"]["actual_updates"] == 0
    assert report["action_counts"]["actual_creates"] == 0
    assert report["action_counts"]["would_update"] == 1
    assert db.contracts.update_calls == []


def test_execute_writes_only_to_contracts_collection(tmp_path, monkeypatch):
    db = _FakeDB(contracts=[_aegisaxiom_contract()])
    monkeypatch.setattr(apply_mod, "_connect_db", lambda: _fake_connect(db))

    csv_path = _write_csv(tmp_path, [_zoho_row()])
    report = asyncio.run(apply_mod.run_apply(
        csv_path=csv_path, out_path=None, execute=True,
        overwrite_existing=False, promote_status=False, allow_create=False,
    ))

    assert report["mode"] == "execute"
    assert report["action_counts"]["actual_updates"] == 1
    assert len(db.contracts.update_calls) == 1
    # No other collection was touched — _FakeDB.__getattr__ raises.
    assert db.accessed == []


def test_execute_preserves_resources_resource_types_hourly_rate_project_ids(
    tmp_path, monkeypatch,
):
    """The post-write doc must keep the operator's local config
    untouched."""
    aegisaxiom = _aegisaxiom_contract(
        resources=[{"user_id": "u_a", "cost_rate": 80.0}],
        resource_types=[{"id": "rt_1", "name": "L1"}],
        hourly_rate=175.0,
        project_ids=["proj_x", "proj_y"],
    )
    db = _FakeDB(contracts=[aegisaxiom])
    monkeypatch.setattr(apply_mod, "_connect_db", lambda: _fake_connect(db))

    csv_path = _write_csv(tmp_path, [_zoho_row()])
    asyncio.run(apply_mod.run_apply(
        csv_path=csv_path, out_path=None, execute=True,
        overwrite_existing=True, promote_status=True, allow_create=False,
    ))

    # The set payload that was written must not mention these fields.
    call = db.contracts.update_calls[0]
    set_payload = call["update"]["$set"]
    assert "resources" not in set_payload
    assert "resource_types" not in set_payload
    assert "hourly_rate" not in set_payload
    assert "project_ids" not in set_payload

    # And the stored doc still has the original values.
    post = db.contracts.docs[0]
    assert post["resources"] == [{"user_id": "u_a", "cost_rate": 80.0}]
    assert post["resource_types"] == [{"id": "rt_1", "name": "L1"}]
    assert post["hourly_rate"] == 175.0
    assert post["project_ids"] == ["proj_x", "proj_y"]


def test_execute_is_idempotent_second_run_no_changes(tmp_path, monkeypatch):
    """A second --execute run against the same CSV produces no field
    updates (or only the timestamp stamps), confirming the rule
    'set additive fields only when missing'."""
    db = _FakeDB(contracts=[_aegisaxiom_contract()])
    monkeypatch.setattr(apply_mod, "_connect_db", lambda: _fake_connect(db))

    csv_path = _write_csv(tmp_path, [_zoho_row()])
    asyncio.run(apply_mod.run_apply(
        csv_path=csv_path, out_path=None, execute=True,
        overwrite_existing=False, promote_status=False, allow_create=False,
    ))
    first_calls = len(db.contracts.update_calls)

    # Second pass.
    db.contracts.update_calls.clear()
    report2 = asyncio.run(apply_mod.run_apply(
        csv_path=csv_path, out_path=None, execute=True,
        overwrite_existing=False, promote_status=False, allow_create=False,
    ))
    # Either zero update_one calls, OR the only $set is a noop
    # (no field outside the import_raw snapshot, which already
    # matches and is short-circuited).
    if db.contracts.update_calls:
        set_payload = db.contracts.update_calls[0]["update"]["$set"]
        # Any new $set keys at this point are timestamp stamps the
        # script always adds (imported_at / updated_at / import_source);
        # the script only writes those when at least one real field
        # also changed. So an idempotent second pass MUST emit no
        # update_one calls.
        assert False, (
            f"Idempotent second pass should not update; got: {sorted(set_payload)}"
        )
    assert report2["action_counts"]["actual_updates"] == 0


def test_unmatched_row_skipped_by_default(tmp_path, monkeypatch):
    db = _FakeDB(contracts=[])  # nothing in DB to match
    monkeypatch.setattr(apply_mod, "_connect_db", lambda: _fake_connect(db))

    csv_path = _write_csv(tmp_path, [_zoho_row()])
    report = asyncio.run(apply_mod.run_apply(
        csv_path=csv_path, out_path=None, execute=True,
        overwrite_existing=False, promote_status=False, allow_create=False,
    ))
    assert report["match_counts"]["unmatched"] == 1
    assert report["action_counts"]["actual_creates"] == 0
    assert db.contracts.insert_calls == []


def test_allow_create_inserts_new_contract_when_unmatched(tmp_path, monkeypatch):
    db = _FakeDB(contracts=[])
    monkeypatch.setattr(apply_mod, "_connect_db", lambda: _fake_connect(db))

    csv_path = _write_csv(tmp_path, [_zoho_row()])
    report = asyncio.run(apply_mod.run_apply(
        csv_path=csv_path, out_path=None, execute=True,
        overwrite_existing=False, promote_status=False, allow_create=True,
    ))
    assert report["action_counts"]["actual_creates"] == 1
    assert len(db.contracts.insert_calls) == 1
    new_doc = db.contracts.insert_calls[0]
    # Inserted doc must carry the import_raw snapshot.
    assert new_doc["import_raw"]["zoho_contract_csv"]["Record Id"] == "zcrm_001"


def test_duplicate_record_id_in_csv_warns(tmp_path, monkeypatch):
    db = _FakeDB(contracts=[_aegisaxiom_contract()])
    monkeypatch.setattr(apply_mod, "_connect_db", lambda: _fake_connect(db))

    rows = [
        _zoho_row(record_id="zcrm_dup"),
        _zoho_row(record_id="zcrm_dup", contract_number="CN-DUP-2"),
    ]
    csv_path = _write_csv(tmp_path, rows)
    report = asyncio.run(apply_mod.run_apply(
        csv_path=csv_path, out_path=None, execute=False,
        overwrite_existing=False, promote_status=False, allow_create=False,
    ))
    kinds = {w["kind"] for w in report["csv_warnings"]}
    assert "duplicate_record_id_in_csv" in kinds


def test_match_falls_back_to_contract_number_when_external_id_missing(
    tmp_path, monkeypatch,
):
    """When a AEGISAXIOM contract has no external_contract_id but the
    contract_number matches the CSV row, the apply script should
    still match (and patch external_ids on the way)."""
    aegisaxiom = _aegisaxiom_contract(external_contract_id=None)
    aegisaxiom.pop("external_contract_id", None)
    db = _FakeDB(contracts=[aegisaxiom])
    monkeypatch.setattr(apply_mod, "_connect_db", lambda: _fake_connect(db))

    csv_path = _write_csv(tmp_path, [_zoho_row()])
    report = asyncio.run(apply_mod.run_apply(
        csv_path=csv_path, out_path=None, execute=False,
        overwrite_existing=False, promote_status=False, allow_create=False,
    ))
    assert report["match_counts"]["matched"] == 1
    assert report["per_row"][0]["match_source"] == "contract_number"


def test_main_dry_run_writes_report(tmp_path, monkeypatch):
    """End-to-end CLI: dry-run writes a JSON report and returns 0."""
    db = _FakeDB(contracts=[_aegisaxiom_contract()])
    monkeypatch.setattr(apply_mod, "_connect_db", lambda: _fake_connect(db))

    csv_path = _write_csv(tmp_path, [_zoho_row()])
    out_path = tmp_path / "report.json"
    code = apply_mod.main([
        "--contracts-csv", csv_path,
        "--out", str(out_path),
    ])
    assert code == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "dry_run"
    assert payload["csv_rows_seen"] == 1


def test_main_execute_without_db_fails_cleanly(tmp_path, monkeypatch):
    """--execute with no MONGO_URL should not crash; it should write
    a report describing the refusal."""
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)

    csv_path = _write_csv(tmp_path, [_zoho_row()])
    out_path = tmp_path / "report.json"
    code = apply_mod.main([
        "--contracts-csv", csv_path,
        "--out", str(out_path),
        "--execute",
    ])
    assert code == 1
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert any("refusing to write" in e for e in payload.get("errors", []))


# ---------------------------------------------------------------------------
# Source-level safety
# ---------------------------------------------------------------------------


def test_apply_script_writes_only_to_contracts_collection():
    src = Path(apply_mod.__file__).read_text(encoding="utf-8")
    # Any writer must go through db.contracts.<writer> ; the test
    # would have to be loosened the day a real second-collection
    # write is justified.
    for token in (
        "db.accounts.update_one(", "db.accounts.insert_one(",
        "db.projects.update_one(", "db.projects.insert_one(",
        "db.deals.update_one(", "db.deals.insert_one(",
        "db.time_entries.update_one(", "db.time_entries.insert_one(",
        "db.users.update_one(", "db.users.insert_one(",
    ):
        assert token not in src, (
            f"Apply script must not call {token!r}; writes are "
            "restricted to db.contracts."
        )


def test_apply_script_does_not_import_zoho_api_or_http_clients():
    src = Path(apply_mod.__file__).read_text(encoding="utf-8")
    for token in ("import httpx", "import requests", "zoho_api"):
        assert token not in src


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fake_connect_async(db):
    return db


def _fake_connect(db):
    """Return an awaitable that yields the fake DB so we can swap
    out apply_mod._connect_db in tests."""
    return _fake_connect_async(db)

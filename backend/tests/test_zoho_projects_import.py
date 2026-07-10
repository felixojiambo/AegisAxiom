"""Tests for the idempotent Zoho Projects importer.

Verifies:
  * Dry-run never calls a Mongo writer.
  * --execute writes; default does not.
  * Repeated --execute upserts the same Mongo document — never
    creates a duplicate for the same Zoho Project Record Id.
  * Blocked rows are refused unless --allow-partial is provided.
  * With --allow-partial, blocked rows remain excluded and only the
    planned ``projects[]`` rows are written.
  * Missing / unresolved ``Contract.id`` rows are never imported.
  * Per-row payload preserves raw_source, external_ids, the
    contract bridge, imported_consumed_hours, and import_warnings.
  * No collection other than ``projects`` is written to.
  * Tests don't require backend/imports/zoho/raw.
  * Importer module asserts no Zoho-API or HTTP leak into the
    script source.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

# Required at import time by any backend code path that pulls auth_helper.
os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production",
)

import pytest


_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# pylint: disable=wrong-import-position
import scripts.zoho_projects_import as importer  # noqa: E402


# ---------------------------------------------------------------------------
# Fake Mongo
# ---------------------------------------------------------------------------


class _UpsertResult:
    def __init__(self, upserted_id: Any | None):
        self.upserted_id = upserted_id


class _FakeProjectsCollection:
    def __init__(self):
        self.docs: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.find_calls: list[dict[str, Any]] = []

    async def find_one(self, query, projection=None):
        self.find_calls.append({"query": query, "projection": projection})
        ext = (query or {}).get("external_project_id")
        for d in self.docs:
            if d.get("external_project_id") == ext:
                return {k: v for k, v in d.items() if k != "_id"}
        return None

    async def update_one(self, query, update, upsert=False):
        self.update_calls.append({
            "query": query, "update": update, "upsert": upsert,
        })
        ext = (query or {}).get("external_project_id")
        existing_idx = next(
            (i for i, d in enumerate(self.docs)
             if d.get("external_project_id") == ext),
            None,
        )
        if existing_idx is not None:
            d = self.docs[existing_idx]
            d.update(update.get("$set", {}))
            return _UpsertResult(upserted_id=None)
        if not upsert:
            return _UpsertResult(upserted_id=None)
        new_doc: dict[str, Any] = {
            "external_project_id": ext,
        }
        new_doc.update(update.get("$setOnInsert", {}))
        new_doc.update(update.get("$set", {}))
        self.docs.append(new_doc)
        return _UpsertResult(upserted_id=new_doc.get("project_id") or "_id")


class _FakeDB:
    def __init__(self):
        self.projects = _FakeProjectsCollection()
        self.accessed: list[str] = []

    def __getattr__(self, name: str):
        # Anything other than `projects` is forbidden by this slice.
        self.accessed.append(name)
        raise AttributeError(
            f"importer touched unexpected collection {name!r}"
        )


# ---------------------------------------------------------------------------
# Plan helpers
# ---------------------------------------------------------------------------


def _candidate(
    *,
    zoho_project_id: str,
    project_name: str,
    aegisaxiom_contract_id: str = "contract_alpha_aegisaxiom",
    contract_number: str = "CN-001",
    zoho_contract_id: str = "ZC0000000001",
    zoho_account_id: str = "ZA0000000001",
    zoho_deal_id: str = "ZD0000000001",
    status_raw: str = "Active",
    project_type_raw: str | None = None,
    consumed: float | None = 12.5,
) -> dict[str, Any]:
    return {
        "source": "zoho",
        "import_source": "zoho",
        "external_project_id": zoho_project_id,
        "external_ids": {
            "zoho_project_id": zoho_project_id,
            "zoho_contract_id": zoho_contract_id,
            "zoho_account_id": zoho_account_id,
            "zoho_deal_id": zoho_deal_id,
        },
        "aegisaxiom_contract_id": aegisaxiom_contract_id,
        "name": project_name,
        "project_number": "1764876000007270075",
        "status_raw": status_raw,
        "project_type_raw": project_type_raw,
        "account_name": "Synthetic Account Alpha",
        "deal_name": "Synthetic Deal Alpha",
        "contract_number": contract_number,
        "owner_raw": "Synthetic Owner",
        "imported_consumed_hours": consumed,
        "source_created_at": "2024-04-10 21:47:10",
        "source_modified_at": "2025-04-29 21:21:51",
        "raw_source": {
            "Record Id": zoho_project_id,
            "Project Name": project_name,
            "Contract.id": zoho_contract_id,
            "Status": status_raw,
        },
    }


def _planned_row(
    *,
    zoho_project_id: str = "ZP0000000001",
    project_name: str = "Synthetic Project Alpha",
    aegisaxiom_contract_id: str = "contract_alpha_aegisaxiom",
    consumed: float | None = 12.5,
    warnings: list[dict[str, Any]] | None = None,
    project_type_raw: str | None = None,
) -> dict[str, Any]:
    candidate = _candidate(
        zoho_project_id=zoho_project_id,
        project_name=project_name,
        aegisaxiom_contract_id=aegisaxiom_contract_id,
        consumed=consumed,
        project_type_raw=project_type_raw,
    )
    return {
        "source": "zoho",
        "zoho_project_id": zoho_project_id,
        "project_name": project_name,
        "contract_id_zoho": candidate["external_ids"]["zoho_contract_id"],
        "aegisaxiom_contract_id": aegisaxiom_contract_id,
        "account": {
            "zoho_account_id": candidate["external_ids"]["zoho_account_id"],
            "name": "Synthetic Account Alpha",
        },
        "deal": {
            "zoho_deal_id": candidate["external_ids"]["zoho_deal_id"],
            "name": "Synthetic Deal Alpha",
        },
        "aegisaxiom_project_candidate": candidate,
        "warnings": list(warnings or []),
    }


def _blocked_missing_contract_row(
    *, zoho_project_id: str = "ZP9000000001",
) -> dict[str, Any]:
    """Mirror the plan's blocked[] shape for a missing-contract-id row."""
    candidate = _candidate(
        zoho_project_id=zoho_project_id,
        project_name="Synthetic Blocked Missing Contract",
        aegisaxiom_contract_id=None,  # type: ignore[arg-type]
        zoho_contract_id="",
    )
    candidate["aegisaxiom_contract_id"] = None
    candidate["external_ids"]["zoho_contract_id"] = None
    return {
        "source": "zoho",
        "zoho_project_id": zoho_project_id,
        "project_name": "Synthetic Blocked Missing Contract",
        "contract_id_zoho": None,
        "aegisaxiom_contract_id": None,
        "account": {"zoho_account_id": "ZA0", "name": "X"},
        "deal": {"zoho_deal_id": None, "name": None},
        "aegisaxiom_project_candidate": candidate,
        "warnings": [{"kind": "missing_deal_id"}],
        "blockers": [{"kind": "missing_contract_id"}],
    }


def _blocked_unresolved_contract_row(
    *, zoho_project_id: str = "ZP9000000002",
) -> dict[str, Any]:
    """Mirror the plan's blocked[] shape for an unresolved-contract-id row."""
    candidate = _candidate(
        zoho_project_id=zoho_project_id,
        project_name="Synthetic Blocked Unresolved Contract",
        aegisaxiom_contract_id=None,  # type: ignore[arg-type]
        zoho_contract_id="ZC_NOT_IN_AEGISAXIOM",
    )
    candidate["aegisaxiom_contract_id"] = None
    return {
        "source": "zoho",
        "zoho_project_id": zoho_project_id,
        "project_name": "Synthetic Blocked Unresolved Contract",
        "contract_id_zoho": "ZC_NOT_IN_AEGISAXIOM",
        "aegisaxiom_contract_id": None,
        "account": {"zoho_account_id": "ZA0", "name": "X"},
        "deal": {"zoho_deal_id": "ZD0", "name": "Y"},
        "aegisaxiom_project_candidate": candidate,
        "warnings": [],
        "blockers": [
            {
                "kind": "unresolved_contract_id",
                "zoho_contract_id": "ZC_NOT_IN_AEGISAXIOM",
            },
        ],
    }


def _plan(
    *,
    projects: list[dict[str, Any]],
    blocked: list[dict[str, Any]] | None = None,
    allow_partial: bool = True,
) -> dict[str, Any]:
    return {
        "source_files": {
            "projects": "x",
            "dry_run_report": "y",
        },
        "options": {"allow_partial": allow_partial},
        "total_rows": len(projects) + len(blocked or []),
        "planned_import_count": len(projects),
        "blocked_count": len(blocked or []),
        "warning_count": sum(len(p.get("warnings") or []) for p in projects),
        "ready_for_import": (not blocked) or allow_partial,
        "projects": projects,
        "blocked": blocked or [],
        "warnings": [],
        "summary": {},
    }


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def test_dry_run_does_not_call_writers():
    db = _FakeDB()
    plan = _plan(projects=[_planned_row()])
    result = asyncio.run(importer.import_from_plan(
        plan, db=db, execute=False, allow_partial=True,
        plan_path="<inline>",
    ))
    assert result.dry_run is True
    assert result.inserted + result.updated == 1
    # Dry-run reads but never writes.
    assert db.projects.update_calls == []


def test_dry_run_distinguishes_would_insert_vs_would_update():
    db = _FakeDB()
    # Pre-seed one existing imported project.
    db.projects.docs.append({
        "project_id": "proj_existing",
        "external_project_id": "ZP0000000001",
    })
    plan = _plan(projects=[
        _planned_row(zoho_project_id="ZP0000000001"),
        _planned_row(
            zoho_project_id="ZP0000000002",
            project_name="Synthetic Project Beta",
        ),
    ])
    result = asyncio.run(importer.import_from_plan(
        plan, db=db, execute=False, allow_partial=True,
        plan_path="<inline>",
    ))
    actions = sorted(r["action"] for r in result.per_row)
    assert actions == ["would_insert", "would_update"]


def test_default_main_mode_is_dry_run(tmp_path, monkeypatch):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(_plan(projects=[_planned_row()])),
        encoding="utf-8",
    )
    out_path = tmp_path / "out.json"
    code = importer.main([
        "--plan", str(plan_path),
        "--out", str(out_path),
    ])
    assert code == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "dry_run"
    assert payload["db_connected"] is False


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------


def test_execute_writes_only_with_execute_flag():
    db = _FakeDB()
    plan = _plan(projects=[_planned_row()])
    result = asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_partial=True,
        plan_path="<inline>",
    ))
    assert result.dry_run is False
    assert result.inserted == 1
    assert len(db.projects.docs) == 1
    inserted = db.projects.docs[0]
    assert inserted["external_project_id"] == "ZP0000000001"
    assert inserted["import_source"] == "zoho"
    assert inserted["status"] == "planning"
    assert inserted["project_type"] == "waterfall"
    assert inserted["service_line"] == "other"
    assert inserted["project_id"].startswith("proj_")


def test_execute_without_db_refuses_to_write():
    plan = _plan(projects=[_planned_row()])
    result = asyncio.run(importer.import_from_plan(
        plan, db=None, execute=True, allow_partial=True,
        plan_path="<inline>",
    ))
    assert result.errors, "must refuse to write when db handle is None"
    assert result.inserted == 0
    assert result.updated == 0


def test_repeat_execute_is_idempotent_no_duplicates():
    db = _FakeDB()
    plan = _plan(projects=[_planned_row()])

    first = asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_partial=True,
        plan_path="<inline>",
    ))
    second = asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_partial=True,
        plan_path="<inline>",
    ))

    assert first.inserted == 1 and first.updated == 0
    assert second.inserted == 0 and second.updated == 1
    # Exactly one document — same external_project_id.
    assert len(db.projects.docs) == 1
    # project_id was NOT regenerated on the second pass.
    first_project_id = db.projects.docs[0]["project_id"]
    third = asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_partial=True,
        plan_path="<inline>",
    ))
    assert third.inserted == 0 and third.updated == 1
    assert len(db.projects.docs) == 1
    assert db.projects.docs[0]["project_id"] == first_project_id


# ---------------------------------------------------------------------------
# Blocked rows / --allow-partial gate
# ---------------------------------------------------------------------------


def test_blocked_plan_without_allow_partial_refuses():
    db = _FakeDB()
    plan = _plan(
        projects=[_planned_row()],
        blocked=[_blocked_missing_contract_row()],
        allow_partial=False,
    )
    result = asyncio.run(importer.import_from_plan(
        plan, db=db, execute=False, allow_partial=False,
        plan_path="<inline>",
    ))
    assert result.errors, "must refuse without --allow-partial"
    assert result.inserted == 0
    assert result.updated == 0
    assert result.blocked_excluded == 1
    assert db.projects.update_calls == []


def test_blocked_rows_excluded_when_allow_partial_is_set():
    db = _FakeDB()
    plan = _plan(
        projects=[_planned_row()],
        blocked=[
            _blocked_missing_contract_row(),
            _blocked_unresolved_contract_row(),
        ],
        allow_partial=True,
    )
    result = asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_partial=True,
        plan_path="<inline>",
    ))
    assert result.inserted == 1
    assert result.blocked_excluded == 2
    assert len(db.projects.docs) == 1
    # Neither blocked Zoho project id reached the projects collection.
    written_ids = {d["external_project_id"] for d in db.projects.docs}
    assert "ZP9000000001" not in written_ids
    assert "ZP9000000002" not in written_ids


def test_blocked_rows_preserved_in_result_report():
    db = _FakeDB()
    plan = _plan(
        projects=[_planned_row()],
        blocked=[
            _blocked_missing_contract_row(),
            _blocked_unresolved_contract_row(),
        ],
        allow_partial=True,
    )
    result = asyncio.run(importer.import_from_plan(
        plan, db=db, execute=False, allow_partial=True,
        plan_path="<inline>",
    ))
    payload = result.to_doc()
    assert payload["blocked_excluded"] == 2
    assert payload["considered"] == 1
    assert payload["allow_partial"] is True


def test_missing_aegisaxiom_contract_id_in_projects_is_skipped():
    # Defence in depth: a hand-edited plan that leaks an unresolved
    # row into projects[] must still be refused.
    db = _FakeDB()
    bad_row = _planned_row()
    bad_row["aegisaxiom_contract_id"] = None
    bad_row["aegisaxiom_project_candidate"]["aegisaxiom_contract_id"] = None
    plan = _plan(projects=[bad_row])
    result = asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_partial=True,
        plan_path="<inline>",
    ))
    assert result.skipped == 1
    assert result.inserted == 0
    assert db.projects.docs == []
    actions = {r["action"] for r in result.per_row}
    assert "skipped" in actions


# ---------------------------------------------------------------------------
# Payload preservation
# ---------------------------------------------------------------------------


def test_external_ids_are_stamped():
    db = _FakeDB()
    plan = _plan(projects=[_planned_row()])
    asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_partial=True,
        plan_path="<inline>",
    ))
    d = db.projects.docs[0]
    assert d["external_project_id"] == "ZP0000000001"
    assert d["external_ids"]["zoho_project_id"] == "ZP0000000001"
    assert d["external_ids"]["zoho_contract_id"] == "ZC0000000001"
    assert d["external_ids"]["zoho_account_id"] == "ZA0000000001"
    assert d["external_ids"]["zoho_deal_id"] == "ZD0000000001"


def test_contract_id_links_to_resolved_aegisaxiom_contract():
    db = _FakeDB()
    plan = _plan(projects=[_planned_row(aegisaxiom_contract_id="contract_bravo_aegisaxiom")])
    asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_partial=True,
        plan_path="<inline>",
    ))
    d = db.projects.docs[0]
    assert d["contract_id"] == "contract_bravo_aegisaxiom"


def test_raw_source_is_preserved():
    db = _FakeDB()
    plan = _plan(projects=[_planned_row()])
    asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_partial=True,
        plan_path="<inline>",
    ))
    inserted = db.projects.docs[0]
    assert inserted["raw_source"]["Record Id"] == "ZP0000000001"
    assert inserted["raw_source"]["Contract.id"] == "ZC0000000001"


def test_imported_consumed_hours_is_preserved():
    db = _FakeDB()
    plan = _plan(projects=[_planned_row(consumed=42.0)])
    asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_partial=True,
        plan_path="<inline>",
    ))
    d = db.projects.docs[0]
    assert d["imported_consumed_hours"] == 42.0


def test_status_raw_and_project_type_raw_are_preserved():
    db = _FakeDB()
    plan = _plan(projects=[
        _planned_row(project_type_raw=None),
    ])
    asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_partial=True,
        plan_path="<inline>",
    ))
    d = db.projects.docs[0]
    assert d["status_raw"] == "Active"
    # Project Type column is absent in the real Zoho export; the
    # importer must accept None / null and preserve it.
    assert d["project_type_raw"] is None
    # AEGISAXIOM-side immutable enum lands at the conservative default.
    assert d["project_type"] == "waterfall"


def test_account_and_deal_name_fallbacks_are_preserved():
    db = _FakeDB()
    plan = _plan(projects=[_planned_row()])
    asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_partial=True,
        plan_path="<inline>",
    ))
    d = db.projects.docs[0]
    assert d["account_name"] == "Synthetic Account Alpha"
    assert d["deal_name"] == "Synthetic Deal Alpha"


def test_project_number_is_preserved():
    db = _FakeDB()
    plan = _plan(projects=[_planned_row()])
    asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_partial=True,
        plan_path="<inline>",
    ))
    d = db.projects.docs[0]
    assert d["project_number"] == "1764876000007270075"


def test_import_warnings_are_preserved_on_document():
    db = _FakeDB()
    warnings = [
        {"kind": "missing_project_type"},
        {"kind": "deal_id_mismatch_with_contract"},
    ]
    plan = _plan(projects=[_planned_row(warnings=warnings)])
    asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_partial=True,
        plan_path="<inline>",
    ))
    d = db.projects.docs[0]
    assert d["import_warnings"] == warnings


def test_warning_count_rollup_in_result():
    db = _FakeDB()
    warnings = [
        {"kind": "missing_project_type"},
        {"kind": "deal_id_mismatch_with_contract"},
        {"kind": "missing_owner"},
    ]
    plan = _plan(projects=[_planned_row(warnings=warnings)])
    result = asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_partial=True,
        plan_path="<inline>",
    ))
    assert result.warning_count == 3


# ---------------------------------------------------------------------------
# No other collection is touched
# ---------------------------------------------------------------------------


def test_only_projects_collection_is_touched_on_execute():
    db = _FakeDB()
    plan = _plan(projects=[_planned_row()])
    asyncio.run(importer.import_from_plan(
        plan, db=db, execute=True, allow_partial=True,
        plan_path="<inline>",
    ))
    # The fake DB raises if any non-`projects` attribute is accessed.
    # Reaching this line means the importer only used `db.projects`.
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


def test_importer_source_only_writes_projects_collection():
    src = Path(importer.__file__).read_text(encoding="utf-8")
    # Every writer call must go through db.projects.
    assert "db.projects.update_one(" in src
    forbidden = (
        "db.accounts.update_one(",
        "db.accounts.insert_one(",
        "db.deals.update_one(",
        "db.deals.insert_one(",
        "db.contracts.update_one(",
        "db.contracts.insert_one(",
        "db.time_entries.update_one(",
        "db.time_entries.insert_one(",
        "db.contract_time_applications.insert_one(",
    )
    for token in forbidden:
        assert token not in src, (
            f"Importer must not call {token!r}; this slice writes "
            "only to the projects collection."
        )


def test_tests_do_not_require_real_imports_directory():
    real = _BACKEND_DIR / "imports" / "zoho" / "raw"
    if real.exists():
        assert real.is_dir()
    # The synthetic candidate carries no real customer data.
    sample = _candidate(
        zoho_project_id="ZP_TEST", project_name="Synthetic",
    )
    blob = json.dumps(sample)
    assert "@simplimeta.com" not in blob


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


def test_main_dry_run_writes_report(tmp_path, monkeypatch):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(_plan(projects=[_planned_row()])),
        encoding="utf-8",
    )
    out_path = tmp_path / "result.json"
    code = importer.main([
        "--plan", str(plan_path),
        "--out", str(out_path),
        "--dry-run",
        "--allow-partial",
    ])
    assert code == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "dry_run"
    assert payload["inserted"] + payload["updated"] >= 1


def test_main_execute_without_mongo_env_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(_plan(projects=[_planned_row()])),
        encoding="utf-8",
    )
    code = importer.main([
        "--plan", str(plan_path),
        "--out", str(tmp_path / "result.json"),
        "--execute",
        "--allow-partial",
    ])
    assert code == 1


def test_main_rejects_missing_plan_file(tmp_path, capsys):
    code = importer.main([
        "--plan", str(tmp_path / "does_not_exist.json"),
        "--out", str(tmp_path / "result.json"),
        "--allow-partial",
    ])
    assert code == 1


def test_main_rejects_malformed_plan_file(tmp_path):
    bad = tmp_path / "plan.json"
    bad.write_text("{not valid json", encoding="utf-8")
    code = importer.main([
        "--plan", str(bad),
        "--out", str(tmp_path / "result.json"),
        "--allow-partial",
    ])
    assert code == 1


def test_main_blocked_plan_without_allow_partial_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(_plan(
            projects=[_planned_row()],
            blocked=[_blocked_missing_contract_row()],
            allow_partial=False,
        )),
        encoding="utf-8",
    )
    out_path = tmp_path / "result.json"
    code = importer.main([
        "--plan", str(plan_path),
        "--out", str(out_path),
        "--dry-run",
    ])
    assert code == 1
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["blocked_excluded"] == 1
    assert payload["errors"], "must surface the refusal reason"

"""Tests for get_finance_data_status.

Background: Nova was answering "the finance database is empty" whenever
qbo_sync_requests had no successful sync record, even when general_ledger
had thousands of rows. The new helper separates two signals:

  - sync freshness ("how stale is the AEGISAXIOM-triggered sync metadata?")
  - data presence ("do the finance collections actually carry rows?")

These tests pin that contract end-to-end. All fixture values are
synthetic (no production dates, totals, tenants, customers, or person
names).
"""
from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production")

import httpx
import pytest

import services.qbo_sync_service as svc
from services.qbo_sync_service import (
    build_sync_request_record,
    get_finance_data_status,
)


# ---------------------------------------------------------------------------
# Fake Mongo collection / db
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = list(docs)

    async def to_list(self, length: int | None = None) -> list[dict]:
        if length is None:
            return list(self._docs)
        return list(self._docs[:length])


class _FakeFinanceCollection:
    """Subset of motor collection methods get_finance_data_status touches:
    count_documents, find_one (sort + projection), aggregate ($group/$max),
    distinct."""

    def __init__(self, docs: Iterable[dict] | None = None) -> None:
        self.docs: list[dict] = [dict(d) for d in (docs or [])]

    async def count_documents(self, query: dict) -> int:
        if not query:
            return len(self.docs)
        return sum(1 for d in self.docs if self._matches(d, query))

    @staticmethod
    def _matches(doc: dict, query: dict) -> bool:
        for k, v in query.items():
            if isinstance(v, dict) and "$ne" in v:
                if doc.get(k) == v["$ne"] or doc.get(k) is None and v["$ne"] is not None:
                    # we want field present and not equal to v
                    if doc.get(k) is None:
                        return False
                    if doc.get(k) == v["$ne"]:
                        return False
            else:
                if doc.get(k) != v:
                    return False
        return True

    async def find_one(self, query: dict, projection: dict | None = None,
                       sort: list[tuple[str, int]] | None = None) -> dict | None:
        matching = [d for d in self.docs if self._matches(d, query)]
        if sort:
            field, direction = sort[0]
            matching.sort(
                key=lambda d: (d.get(field) is None, d.get(field)),
                reverse=(direction == -1),
            )
        if not matching:
            return None
        doc = dict(matching[0])
        doc.pop("_id", None)
        return doc

    def aggregate(self, pipeline: list[dict]) -> _FakeCursor:
        # Two pipeline shapes are exercised:
        #   1. {"$group": {"_id": None, "max_date": {"$max": "$tx_date"}}}
        #   2. {"$group": {"_id": "$basis", "row_count": {"$sum": 1},
        #        "latest_tx_date": {"$max": "$tx_date"},
        #        "latest_synced_at": {"$max": "$synced_at"}}},
        #      {"$sort": {"_id": 1}}
        if not pipeline:
            return _FakeCursor([])

        first = pipeline[0]
        group = first.get("$group") if isinstance(first, dict) else None
        if not group:
            return _FakeCursor([])

        if group.get("_id") is None and "max_date" in group:
            field = group["max_date"]["$max"].lstrip("$")
            values = [d.get(field) for d in self.docs if d.get(field) is not None]
            if not values:
                return _FakeCursor([])
            return _FakeCursor([{"_id": None, "max_date": max(values)}])

        if isinstance(group.get("_id"), str) and group["_id"].startswith("$"):
            key_field = group["_id"].lstrip("$")
            buckets: dict[Any, list[dict]] = {}
            for doc in self.docs:
                buckets.setdefault(doc.get(key_field), []).append(doc)

            results: list[dict] = []
            for key, rows in buckets.items():
                entry: dict[str, Any] = {"_id": key}
                for out_field, expr in group.items():
                    if out_field == "_id":
                        continue
                    if not isinstance(expr, dict):
                        continue
                    if "$sum" in expr:
                        spec = expr["$sum"]
                        if spec == 1:
                            entry[out_field] = len(rows)
                        elif isinstance(spec, str) and spec.startswith("$"):
                            field_name = spec.lstrip("$")
                            entry[out_field] = sum(
                                (r.get(field_name) or 0) for r in rows
                            )
                        else:
                            entry[out_field] = 0
                    elif "$max" in expr:
                        spec = expr["$max"]
                        if isinstance(spec, str) and spec.startswith("$"):
                            field_name = spec.lstrip("$")
                            values = [r.get(field_name) for r in rows if r.get(field_name) is not None]
                            entry[out_field] = max(values) if values else None
                        else:
                            entry[out_field] = None
                    else:
                        entry[out_field] = None
                results.append(entry)

            # Honour a trailing {"$sort": {"_id": 1}} stage.
            if (
                len(pipeline) >= 2
                and isinstance(pipeline[1], dict)
                and "$sort" in pipeline[1]
                and "_id" in pipeline[1]["$sort"]
            ):
                direction = pipeline[1]["$sort"]["_id"]
                results.sort(
                    key=lambda r: (r["_id"] is None, r["_id"]),
                    reverse=(direction == -1),
                )
            return _FakeCursor(results)

        return _FakeCursor([])

    async def distinct(self, field: str) -> list[Any]:
        seen: list[Any] = []
        for d in self.docs:
            value = d.get(field)
            if value is not None and value not in seen:
                seen.append(value)
        return seen


class _FakeSyncRequestsCollection(_FakeFinanceCollection):
    """qbo_sync_requests-style collection — used by get_finance_data_freshness."""

    async def insert_one(self, record: dict) -> Any:
        self.docs.append(dict(record))


class _FakeFinanceDb:
    def __init__(
        self,
        *,
        sync_records: list[dict] | None = None,
        general_ledger: list[dict] | None = None,
        ar_invoices: list[dict] | None = None,
        ap_bills: list[dict] | None = None,
        transactions: list[dict] | None = None,
        chart_of_accounts: list[dict] | None = None,
        reports_ar_aging_detail: list[dict] | None = None,
    ) -> None:
        self._collections: dict[str, _FakeFinanceCollection] = {
            "qbo_sync_requests": _FakeSyncRequestsCollection(sync_records or []),
            "general_ledger": _FakeFinanceCollection(general_ledger or []),
            "ar_invoices": _FakeFinanceCollection(ar_invoices or []),
            "ap_bills": _FakeFinanceCollection(ap_bills or []),
            "transactions": _FakeFinanceCollection(transactions or []),
            "chart_of_accounts": _FakeFinanceCollection(chart_of_accounts or []),
            "reports_ar_aging_detail": _FakeFinanceCollection(reports_ar_aging_detail or []),
        }

    def __getitem__(self, name: str) -> _FakeFinanceCollection:
        if name not in self._collections:
            self._collections[name] = _FakeFinanceCollection()
        return self._collections[name]

    def __getattr__(self, name: str) -> _FakeFinanceCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self.__getitem__(name)


# ---------------------------------------------------------------------------
# Fixture data factories — purely synthetic
# ---------------------------------------------------------------------------


def _gl_row(tx_date: date, basis: str, amount: float, synced_at: datetime | None = None) -> dict:
    """Build a synthetic general_ledger row with no production-real values."""
    row: dict[str, Any] = {
        "tx_date": tx_date,
        "basis": basis,
        "amount": amount,
        "account_name": "synthetic-test-account",
    }
    if synced_at is not None:
        row["synced_at"] = synced_at
    return row


def _completed_sync_record(finished_at: datetime) -> dict:
    rec = build_sync_request_record(
        sync_request_id="qbosync_synthetic",
        requested_by_user_id="u_test",
        requested_source="manual",
        sources=["gl"],
        mode="incremental",
        basis="accrual",
        date_from=None,
        date_to=None,
    )
    rec["status"] = "complete"
    rec["finished_at"] = finished_at
    return rec


# ---------------------------------------------------------------------------
# 1. No qbo_sync_requests record + non-empty general_ledger
# ---------------------------------------------------------------------------
def test_no_sync_record_with_non_empty_gl_says_freshness_unknown_not_empty():
    today = datetime.now(timezone.utc).date()
    db = _FakeFinanceDb(
        sync_records=None,
        general_ledger=[
            _gl_row(today - timedelta(days=10), "accrual", 100.0),
            _gl_row(today - timedelta(days=5), "accrual", 200.0),
            _gl_row(today - timedelta(days=2), "cash", 300.0),
        ],
    )

    result = asyncio.run(get_finance_data_status(db))

    sync_status = result["sync_status"]
    data_status = result["data_status"]
    testing_status = result["testing_status"]

    assert sync_status["last_successful_sync_at"] is None
    assert sync_status["is_stale"] is True

    warning = sync_status["warning"] or ""
    lowered = warning.lower()
    assert "freshness is unknown" in lowered or "freshness unknown" in lowered
    assert "empty" not in lowered or "not" in lowered  # never just "empty"
    # And must NOT claim the database has no data
    forbidden_phrases = [
        "the database is entirely empty",
        "there is no data at all",
        "i don't have any historical transactions",
    ]
    for phrase in forbidden_phrases:
        assert phrase not in lowered

    assert data_status["finance_data_present"] is True
    assert data_status["general_ledger_usable"] is True
    assert data_status["general_ledger"]["row_count"] == 3
    assert testing_status["usable_for_finance_testing"] is True


# ---------------------------------------------------------------------------
# 2. No qbo_sync_requests record + empty finance collections
# ---------------------------------------------------------------------------
def test_no_sync_record_with_empty_collections_marks_unavailable():
    db = _FakeFinanceDb()
    result = asyncio.run(get_finance_data_status(db))

    assert result["data_status"]["finance_data_present"] is False
    assert result["data_status"]["general_ledger_usable"] is False
    assert result["testing_status"]["usable_for_finance_testing"] is False
    assert "general_ledger_unavailable" in result["testing_status"]["warnings"]


# ---------------------------------------------------------------------------
# 3. latest_tx_date is computed from fixture rows, not hardcoded literal
# ---------------------------------------------------------------------------
def test_latest_tx_date_is_computed_from_fixture_rows():
    base = datetime.now(timezone.utc).date() - timedelta(days=400)
    fixture_dates = [base, base + timedelta(days=3), base + timedelta(days=11)]
    db = _FakeFinanceDb(
        general_ledger=[_gl_row(d, "accrual", 1.0) for d in fixture_dates],
    )

    result = asyncio.run(get_finance_data_status(db))

    expected_latest = max(fixture_dates).isoformat()
    assert result["data_status"]["general_ledger"]["latest_tx_date"] == expected_latest


# ---------------------------------------------------------------------------
# 4. available_basis_values comes from distinct fixture basis values
# ---------------------------------------------------------------------------
def test_available_basis_values_reflects_distinct_fixture_values():
    today = datetime.now(timezone.utc).date()
    db = _FakeFinanceDb(
        general_ledger=[
            _gl_row(today, "accrual", 1.0),
            _gl_row(today, "accrual", 2.0),
            _gl_row(today, "cash", 3.0),
        ],
    )

    result = asyncio.run(get_finance_data_status(db))
    assert result["data_status"]["general_ledger"]["available_basis_values"] == ["accrual", "cash"]


def test_available_basis_values_handles_missing_field():
    today = datetime.now(timezone.utc).date()
    rows = [_gl_row(today, "accrual", 1.0)]
    rows[0].pop("basis", None)  # simulate a tenant whose GL omits basis
    db = _FakeFinanceDb(general_ledger=rows)

    result = asyncio.run(get_finance_data_status(db))
    assert result["data_status"]["general_ledger"]["available_basis_values"] == []


# ---------------------------------------------------------------------------
# 5. latest_synced_at comes from fixture synced_at values when present
# ---------------------------------------------------------------------------
def test_latest_synced_at_comes_from_fixture_when_present():
    today = datetime.now(timezone.utc).date()
    syncs = [
        datetime.now(timezone.utc) - timedelta(hours=4),
        datetime.now(timezone.utc) - timedelta(hours=1),
        datetime.now(timezone.utc) - timedelta(hours=8),
    ]
    db = _FakeFinanceDb(
        general_ledger=[
            _gl_row(today, "accrual", 1.0, synced_at=syncs[0]),
            _gl_row(today, "accrual", 1.0, synced_at=syncs[1]),
            _gl_row(today, "accrual", 1.0, synced_at=syncs[2]),
        ],
    )

    result = asyncio.run(get_finance_data_status(db))

    expected = max(syncs)
    if expected.tzinfo is None:
        expected = expected.replace(tzinfo=timezone.utc)
    assert result["data_status"]["general_ledger"]["latest_synced_at"] == expected.isoformat()


def test_latest_synced_at_is_null_when_field_missing():
    today = datetime.now(timezone.utc).date()
    db = _FakeFinanceDb(
        general_ledger=[_gl_row(today, "accrual", 1.0)],  # no synced_at
    )

    result = asyncio.run(get_finance_data_status(db))
    assert result["data_status"]["general_ledger"]["latest_synced_at"] is None


# ---------------------------------------------------------------------------
# 6. Helper does not call QBO or n8n
# ---------------------------------------------------------------------------
def test_get_finance_data_status_does_not_call_qbo_or_n8n(monkeypatch):
    monkeypatch.setenv("QBO_SYNC_STALE_AFTER_SECONDS", "60")

    called: list[tuple] = []

    async def _bomb(*args, **kwargs):
        called.append(("http", args, kwargs))
        raise AssertionError("get_finance_data_status must not POST or GET anywhere")

    monkeypatch.setattr(httpx.AsyncClient, "post", _bomb)
    monkeypatch.setattr(httpx.AsyncClient, "get", _bomb)

    today = datetime.now(timezone.utc).date()
    db = _FakeFinanceDb(general_ledger=[_gl_row(today, "accrual", 1.0)])

    result = asyncio.run(get_finance_data_status(db))
    assert called == []
    assert "sync_status" in result
    assert "data_status" in result
    assert "testing_status" in result


# ---------------------------------------------------------------------------
# 7 & 8. Nova prompt tells the LLM to distinguish freshness from data
# ---------------------------------------------------------------------------
def test_prompt_says_no_sync_record_does_not_mean_finance_data_is_empty():
    from services.ai.agents.nova import _SYSTEM_PROMPT

    text = _SYSTEM_PROMPT.lower()
    # The prompt must say sync freshness != data presence.
    assert "freshness is unknown" in text
    assert "not that the finance database is empty" in text or "does not mean" in text
    # And must explicitly forbid the bad phrasings the audit found.
    assert "the database is entirely empty" in text
    assert "there is no data at all" in text
    assert "i don't have any historical transactions" in text


def test_prompt_routes_database_access_questions_to_get_finance_data_status():
    from services.ai.agents.nova import _SYSTEM_PROMPT

    text = _SYSTEM_PROMPT
    assert "get_finance_data_status" in text
    assert "can you access the finance database?" in text.lower()
    assert "what is the latest gl transaction date?" in text.lower()
    assert "is the data usable for testing?" in text.lower()


# ---------------------------------------------------------------------------
# 9. Tool schema includes get_finance_data_status
# ---------------------------------------------------------------------------
def test_get_finance_data_status_is_in_nova_tool_schemas():
    from services.ai.agents.tools.nova_tools import NOVA_TOOL_SCHEMAS

    schema = next(
        (t for t in NOVA_TOOL_SCHEMAS if t["name"] == "get_finance_data_status"),
        None,
    )
    assert schema is not None, "get_finance_data_status tool must be registered"

    description = schema["description"].lower()
    # Must explicitly say no sync record != empty data, in whatever
    # phrasing the current schema uses.
    assert "freshness is unknown" in description
    assert (
        "do not tell the user the database is empty or unavailable solely because the sync log has no successful record"
        in description
    )


# ---------------------------------------------------------------------------
# 10. Existing get_finance_data_freshness behavior is preserved
# ---------------------------------------------------------------------------
def test_get_finance_data_freshness_still_returns_stale_when_no_record():
    db = _FakeFinanceDb()
    out = asyncio.run(svc.get_finance_data_freshness(db))
    assert out["is_stale"] is True
    assert out["last_successful_sync_at"] is None
    assert out["sync_lag_seconds"] is None


def test_get_finance_data_freshness_returns_fresh_for_recent_sync(monkeypatch):
    monkeypatch.setenv("QBO_SYNC_STALE_AFTER_SECONDS", "3600")
    db = _FakeFinanceDb(
        sync_records=[
            _completed_sync_record(datetime.now(timezone.utc) - timedelta(seconds=30)),
        ],
    )
    out = asyncio.run(svc.get_finance_data_freshness(db))
    assert out["is_stale"] is False
    assert out["warning"] is None
    assert out["last_successful_sync_at"] is not None


# ---------------------------------------------------------------------------
# Sync freshness is independent: a recent sync + non-empty GL = fully healthy
# ---------------------------------------------------------------------------
def test_recent_sync_with_data_marks_status_healthy(monkeypatch):
    monkeypatch.setenv("QBO_SYNC_STALE_AFTER_SECONDS", "3600")
    today = datetime.now(timezone.utc).date()
    db = _FakeFinanceDb(
        sync_records=[
            _completed_sync_record(datetime.now(timezone.utc) - timedelta(seconds=30)),
        ],
        general_ledger=[_gl_row(today, "accrual", 1.0)],
    )

    result = asyncio.run(get_finance_data_status(db))
    assert result["sync_status"]["is_stale"] is False
    assert result["sync_status"]["warning"] is None
    assert result["data_status"]["finance_data_present"] is True
    assert result["data_status"]["general_ledger_usable"] is True
    assert result["testing_status"]["usable_for_finance_testing"] is True
    assert result["testing_status"]["warnings"] == []


# ---------------------------------------------------------------------------
# Basis-aware GL status (the all-basis vs accrual-vs-cash bug)
# ---------------------------------------------------------------------------
#
# Scenario mirrors the production audit: cash-basis rows extend to a
# later tx_date than accrual-basis rows, so the all-basis max_date
# alone misrepresents "what's the latest accrual data?". All expected
# values are computed from the synthetic fixture rows so this test
# survives any future fixture-date shift.


def _basis_split_fixture():
    """Synthetic GL rows where accrual and cash extend to different
    tx_dates and have different row counts. All values derived from a
    fixed offset so production data is never pasted in."""
    base = datetime.now(timezone.utc).date() - timedelta(days=400)

    accrual_dates = [base + timedelta(days=i) for i in (0, 5, 12, 19, 26)]
    cash_dates = [base + timedelta(days=i) for i in (3, 14, 30, 75)]  # extends past accrual

    accrual_synced = [
        datetime.now(timezone.utc) - timedelta(hours=h)
        for h in (12, 9, 6, 4, 2)
    ]
    cash_synced = [
        datetime.now(timezone.utc) - timedelta(hours=h)
        for h in (8, 5, 3, 1)
    ]

    rows: list[dict] = []
    for d, s in zip(accrual_dates, accrual_synced):
        rows.append(_gl_row(d, "accrual", 1.0, synced_at=s))
    for d, s in zip(cash_dates, cash_synced):
        rows.append(_gl_row(d, "cash", 1.0, synced_at=s))

    return {
        "rows": rows,
        "accrual_dates": accrual_dates,
        "cash_dates": cash_dates,
        "accrual_synced": accrual_synced,
        "cash_synced": cash_synced,
    }


def _expected_iso(value: Any) -> str:
    if hasattr(value, "tzinfo"):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return value.isoformat()


def test_overall_row_count_includes_all_basis_rows():
    fx = _basis_split_fixture()
    db = _FakeFinanceDb(general_ledger=fx["rows"])

    result = asyncio.run(get_finance_data_status(db))
    gl = result["data_status"]["general_ledger"]

    assert gl["row_count"] == len(fx["rows"])
    assert gl["row_count"] == len(fx["accrual_dates"]) + len(fx["cash_dates"])


def test_overall_latest_tx_date_is_max_across_all_basis_rows():
    fx = _basis_split_fixture()
    db = _FakeFinanceDb(general_ledger=fx["rows"])

    result = asyncio.run(get_finance_data_status(db))
    gl = result["data_status"]["general_ledger"]

    expected_overall = max(fx["accrual_dates"] + fx["cash_dates"]).isoformat()
    assert gl["latest_tx_date"] == expected_overall
    assert gl["latest_tx_date_basis"] == "all"


def test_available_basis_values_comes_from_fixture_basis_values():
    fx = _basis_split_fixture()
    db = _FakeFinanceDb(general_ledger=fx["rows"])

    result = asyncio.run(get_finance_data_status(db))
    gl = result["data_status"]["general_ledger"]

    assert gl["available_basis_values"] == sorted({"accrual", "cash"})


def test_by_basis_returns_one_entry_per_basis_present():
    fx = _basis_split_fixture()
    db = _FakeFinanceDb(general_ledger=fx["rows"])

    result = asyncio.run(get_finance_data_status(db))
    by_basis = result["data_status"]["general_ledger"]["by_basis"]

    bases = [entry["basis"] for entry in by_basis]
    assert bases == ["accrual", "cash"]
    # Accrual-first ordering is part of the contract.
    assert by_basis[0]["basis"] == "accrual"
    assert by_basis[1]["basis"] == "cash"


def test_by_basis_omits_basis_with_no_rows():
    today = datetime.now(timezone.utc).date()
    db = _FakeFinanceDb(general_ledger=[_gl_row(today, "accrual", 1.0)])

    result = asyncio.run(get_finance_data_status(db))
    by_basis = result["data_status"]["general_ledger"]["by_basis"]

    assert [e["basis"] for e in by_basis] == ["accrual"]


def test_accrual_latest_tx_date_is_computed_from_accrual_rows_only():
    fx = _basis_split_fixture()
    db = _FakeFinanceDb(general_ledger=fx["rows"])

    result = asyncio.run(get_finance_data_status(db))
    by_basis = result["data_status"]["general_ledger"]["by_basis"]
    accrual = next(e for e in by_basis if e["basis"] == "accrual")

    expected_accrual_latest = max(fx["accrual_dates"]).isoformat()
    assert accrual["latest_tx_date"] == expected_accrual_latest
    assert accrual["row_count"] == len(fx["accrual_dates"])


def test_cash_latest_tx_date_is_computed_from_cash_rows_only():
    fx = _basis_split_fixture()
    db = _FakeFinanceDb(general_ledger=fx["rows"])

    result = asyncio.run(get_finance_data_status(db))
    by_basis = result["data_status"]["general_ledger"]["by_basis"]
    cash = next(e for e in by_basis if e["basis"] == "cash")

    expected_cash_latest = max(fx["cash_dates"]).isoformat()
    assert cash["latest_tx_date"] == expected_cash_latest
    assert cash["row_count"] == len(fx["cash_dates"])


def test_overall_latest_may_differ_from_accrual_latest_and_both_returned():
    """The headline production bug: the all-basis max came from cash
    rows, but the conversation reported it as the accrual answer. The
    fixture builds exactly that shape."""
    fx = _basis_split_fixture()
    db = _FakeFinanceDb(general_ledger=fx["rows"])

    result = asyncio.run(get_finance_data_status(db))
    gl = result["data_status"]["general_ledger"]

    by_basis = {e["basis"]: e for e in gl["by_basis"]}
    accrual_latest = by_basis["accrual"]["latest_tx_date"]
    cash_latest = by_basis["cash"]["latest_tx_date"]
    overall_latest = gl["latest_tx_date"]

    assert accrual_latest != cash_latest, "fixture should split accrual vs cash"
    assert overall_latest == max(accrual_latest, cash_latest)
    assert overall_latest != accrual_latest
    assert overall_latest == cash_latest
    # Overall is labeled all-basis so the LLM cannot mis-attribute it.
    assert gl["latest_tx_date_basis"] == "all"


def test_latest_synced_at_is_computed_per_basis():
    fx = _basis_split_fixture()
    db = _FakeFinanceDb(general_ledger=fx["rows"])

    result = asyncio.run(get_finance_data_status(db))
    by_basis = {e["basis"]: e for e in result["data_status"]["general_ledger"]["by_basis"]}

    expected_accrual_synced = _expected_iso(max(fx["accrual_synced"]))
    expected_cash_synced = _expected_iso(max(fx["cash_synced"]))

    assert by_basis["accrual"]["latest_synced_at"] == expected_accrual_synced
    assert by_basis["cash"]["latest_synced_at"] == expected_cash_synced


def test_prompt_says_not_to_collapse_all_basis_and_accrual_dates():
    from services.ai.agents.nova import _SYSTEM_PROMPT

    text = _SYSTEM_PROMPT.lower()
    assert "do not use the all-basis latest date as the accrual latest date" in text
    assert "by_basis" in text
    # Must instruct Nova to prefer accrual for P&L / accrual reporting.
    assert "prefer the accrual entry" in text or "prefer accrual" in text


def test_tool_schema_mentions_per_basis_gl_status():
    from services.ai.agents.tools.nova_tools import NOVA_TOOL_SCHEMAS

    schema = next(t for t in NOVA_TOOL_SCHEMAS if t["name"] == "get_finance_data_status")
    description = schema["description"].lower()

    # Description must call out per-basis breakdown explicitly so the
    # LLM doesn't have to reverse-engineer it from the response shape.
    assert "by_basis" in description
    assert "all-basis" in description and "per-basis" in description
    assert "do not collapse accrual and cash" in description


def test_no_basis_field_means_no_by_basis_entries():
    """Defensive: if the fixture rows have no basis field at all, the
    by_basis aggregation must not surface a meaningless null bucket."""
    today = datetime.now(timezone.utc).date()
    rows = [_gl_row(today, "accrual", 1.0)]
    rows[0].pop("basis", None)
    db = _FakeFinanceDb(general_ledger=rows)

    result = asyncio.run(get_finance_data_status(db))
    by_basis = result["data_status"]["general_ledger"]["by_basis"]
    assert by_basis == []


def test_full_response_shape_round_trip():
    """Pin the contract end-to-end so future refactors keep the keys
    Nova's prompt depends on."""
    fx = _basis_split_fixture()
    db = _FakeFinanceDb(general_ledger=fx["rows"])

    gl = asyncio.run(get_finance_data_status(db))["data_status"]["general_ledger"]
    expected_keys = {
        "collection",
        "exists",
        "row_count",
        "latest_tx_date",
        "latest_tx_date_basis",
        "available_basis_values",
        "latest_synced_at",
        "by_basis",
    }
    assert set(gl.keys()) == expected_keys

    for entry in gl["by_basis"]:
        assert set(entry.keys()) == {"basis", "row_count", "latest_tx_date", "latest_synced_at"}


# ---------------------------------------------------------------------------
# User-facing data-age wording (qbo_sync_requests-internals must not leak)
# ---------------------------------------------------------------------------
#
# Production audit found Nova answering normal finance questions with
# "Finance data may be stale. No successful QBO sync has been recorded
# yet." That wording surfaces sync-log internals as a user-visible
# warning even when general_ledger has thousands of rows. These tests
# pin the new contract: user_facing_data_note speaks in source-data
# terms, admin_sync_note carries the diagnostic line.


def _user_forbidden_phrases() -> list[str]:
    return [
        "stale",
        "no successful qbo sync",
        "qbo_sync_requests",
        "database empty",
        "the database is entirely empty",
        "there is no data at all",
        "i don't have any historical transactions",
    ]


def test_user_facing_note_when_data_present_no_sync_record():
    today = datetime.now(timezone.utc).date()
    db = _FakeFinanceDb(
        general_ledger=[_gl_row(today, "accrual", 1.0)],
    )

    result = asyncio.run(get_finance_data_status(db))
    note = result["user_facing_data_note"]

    assert isinstance(note, str) and note
    assert note.startswith("Based on the latest available finance data in AEGISAXIOM")

    lowered = note.lower()
    for forbidden in _user_forbidden_phrases():
        assert forbidden not in lowered, (
            f"user_facing_data_note must not leak {forbidden!r}: {note!r}"
        )


def test_admin_sync_note_may_mention_no_successful_aegisaxiom_triggered_sync():
    db = _FakeFinanceDb()
    result = asyncio.run(get_finance_data_status(db))

    admin_note = result["admin_sync_note"]
    assert isinstance(admin_note, str) and admin_note
    # Admin context is allowed to surface sync-log diagnostics.
    assert "aegisaxiom-triggered" in admin_note.lower() or "sync" in admin_note.lower()


def test_user_facing_note_includes_source_timestamp_when_available():
    today = datetime.now(timezone.utc).date()
    synced = datetime.now(timezone.utc) - timedelta(hours=2)
    db = _FakeFinanceDb(
        general_ledger=[_gl_row(today, "accrual", 1.0, synced_at=synced)],
    )

    result = asyncio.run(get_finance_data_status(db))
    note = result["user_facing_data_note"]

    expected_iso = synced.isoformat()
    assert "as of" in note
    assert expected_iso in note
    assert note == (
        f"Based on the latest available finance data in AEGISAXIOM as of {expected_iso}."
    )


def test_user_facing_note_falls_back_to_tx_date_when_no_synced_at():
    today = datetime.now(timezone.utc).date()
    db = _FakeFinanceDb(
        general_ledger=[_gl_row(today, "accrual", 1.0)],  # no synced_at
    )

    result = asyncio.run(get_finance_data_status(db))
    note = result["user_facing_data_note"]

    assert "as of" in note
    assert today.isoformat() in note


def test_user_facing_note_when_data_absent():
    db = _FakeFinanceDb()
    result = asyncio.run(get_finance_data_status(db))
    note = result["user_facing_data_note"]

    assert note == "Finance data is not available for this question yet."

    lowered = note.lower()
    # Even the empty-data line must not leak sync-log internals.
    assert "qbo_sync_requests" not in lowered
    assert "no successful qbo sync" not in lowered


def test_status_payload_carries_both_user_and_admin_notes():
    today = datetime.now(timezone.utc).date()
    db = _FakeFinanceDb(general_ledger=[_gl_row(today, "accrual", 1.0)])

    result = asyncio.run(get_finance_data_status(db))
    assert "user_facing_data_note" in result
    assert "admin_sync_note" in result
    assert result["user_facing_data_note"] != result["admin_sync_note"]


def test_build_finance_data_age_note_is_pure_and_audience_aware():
    """Helper must work without I/O and respect the audience kwarg."""
    from services.qbo_sync_service import build_finance_data_age_note

    payload_present = {
        "data_status": {
            "finance_data_present": True,
            "general_ledger": {
                "latest_synced_at": "2099-01-02T03:04:05+00:00",
                "latest_tx_date": "2099-01-02",
            },
        },
        "sync_status": {
            "warning": "No AEGISAXIOM-triggered QBO sync has been recorded yet.",
        },
    }
    user_note = build_finance_data_age_note(payload_present, audience="user")
    assert user_note is not None
    assert "Based on the latest available finance data in AEGISAXIOM" in user_note
    for forbidden in _user_forbidden_phrases():
        assert forbidden not in user_note.lower()

    admin_note = build_finance_data_age_note(payload_present, audience="admin")
    assert admin_note is not None
    assert "AEGISAXIOM-triggered" in admin_note or "sync" in admin_note.lower()


def test_prompt_says_normal_finance_answers_use_latest_available_wording():
    from services.ai.agents.nova import _SYSTEM_PROMPT

    assert "Based on the latest available finance data in AEGISAXIOM" in _SYSTEM_PROMPT
    assert "user_facing_data_note" in _SYSTEM_PROMPT


def test_prompt_does_not_instruct_user_facing_stale_wording():
    from services.ai.agents.nova import _SYSTEM_PROMPT

    # The audit'd line "You may say: 'Finance data may be stale...'"
    # taught the LLM to use sync-log wording in normal answers — it
    # must be gone.
    assert "you may say: \"finance data may be stale" not in _SYSTEM_PROMPT.lower()
    assert "you may say: 'finance data may be stale" not in _SYSTEM_PROMPT.lower()


def test_prompt_does_not_contain_hardcoded_finance_amounts():
    """Prompt examples must never paste production-real financial figures.
    Allowed: routing examples that explicitly use $5K-style placeholders
    or names a tool returns. Forbidden: any specific dollar total that
    looks like a real GL aggregate."""
    import re

    from services.ai.agents.nova import _SYSTEM_PROMPT

    # Whitelist of placeholder-ish dollar tokens that already appear in
    # routing examples. Any other dollar amount in the prompt would be
    # a hardcoded finance value.
    allowed_dollar_tokens = {"$5K", "$2K", "$12K"}

    dollar_tokens = re.findall(r"\$[\d][\d,\.]*[KkMm]?", _SYSTEM_PROMPT)
    leaked = [tok for tok in dollar_tokens if tok not in allowed_dollar_tokens]
    assert not leaked, f"prompt contains hardcoded finance amounts: {leaked}"


def test_freshness_tool_description_does_not_instruct_user_facing_no_sync_wording():
    from services.ai.agents.tools.nova_tools import NOVA_TOOL_SCHEMAS

    freshness = next(t for t in NOVA_TOOL_SCHEMAS if t["name"] == "get_finance_data_freshness")
    description = freshness["description"]
    lowered = description.lower()

    # Description must NOT instruct the LLM to surface this wording in
    # normal finance answers; it MUST route normal answers through
    # get_finance_data_status's user_facing_data_note instead.
    assert "user_facing_data_note" in description
    # Diagnostic-only framing is mandatory.
    assert "diagnostic" in lowered
    # And the "no sync record != empty data" guarantee survives.
    assert (
        "do not tell the user the database is empty or unavailable solely because the sync log has no successful record"
        in lowered
    )


def test_status_tool_description_routes_normal_answers_to_user_facing_note():
    from services.ai.agents.tools.nova_tools import NOVA_TOOL_SCHEMAS

    status_schema = next(
        t for t in NOVA_TOOL_SCHEMAS if t["name"] == "get_finance_data_status"
    )
    description = status_schema["description"]
    lowered = description.lower()

    assert "user_facing_data_note" in description
    assert "admin_sync_note" in description
    assert "for normal finance answers, prefer user_facing_data_note" in lowered


def test_get_finance_data_freshness_remains_backward_compatible():
    """The diagnostic helper must still return its original
    {last_successful_sync_at, sync_lag_seconds, is_stale, warning}
    shape so admin dashboards don't break."""
    db = _FakeFinanceDb()
    out = asyncio.run(svc.get_finance_data_freshness(db))
    assert set(out.keys()) == {
        "last_successful_sync_at",
        "sync_lag_seconds",
        "is_stale",
        "warning",
    }
    assert out["is_stale"] is True
    assert out["last_successful_sync_at"] is None


def test_no_user_facing_string_in_codebase_uses_old_stale_wording():
    """Regression guard: the literal user-facing line that triggered
    this fix must not reappear in the helper or prompt as a string the
    LLM is instructed to say. We allow it to live on as a diagnostic
    string in sync_status.warning (asserted elsewhere)."""
    from services.ai.agents.nova import _SYSTEM_PROMPT

    forbidden_user_wording = "Finance data may be stale. No successful QBO sync"
    assert forbidden_user_wording not in _SYSTEM_PROMPT

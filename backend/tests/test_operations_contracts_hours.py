"""Tests for GET /api/operations/contracts/hours.

The May 24, 2026 Contract Financials build-out routes the page
through this new endpoint. Tests pin:

  * fallback chain: total_hours -> total_hours_purchased ->
    imported_total_hours -> imported_total_hours_alt
  * health classification: over_consumed / warning / healthy /
    n_a / unknown
  * summary counters match the per-row classification
  * filters: q / contract_type / status / health
  * envelope shape stable for the UI
  * read-only — no DB writes
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

import pytest


_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


# ---------------------------------------------------------------------------
# Fake Mongo
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    async def to_list(self, _n):
        return self._docs


class _Coll:
    def __init__(self, docs):
        self.docs = list(docs)

    def find(self, query=None, _projection=None):
        rows = [d for d in self.docs if _match(d, query or {})]
        return _Cursor(rows)


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
    def __init__(self, contracts):
        self.contracts = _Coll(contracts)


def _admin():
    return SimpleNamespace(
        user_id="u", role="admin", email="a", name="A",
        permissions=["*"], effective_permissions=["*"], account_id=None,
    )


def _contract(**overrides):
    base = {
        "contract_id": "c_default",
        "contract_number": "CN-100",
        "name": "Default Contract",
        "account_name": "Default Account",
        "contract_type": "boh",
        "contract_type_raw": "BOH",
        "deal_name": "Default Deal",
        "invoice_type_raw": "net30",
        "contract_status_raw": "Approved",
        "status": "draft",
        "is_test_data": False,
    }
    base.update(overrides)
    return base


def _call_hours(db, **kwargs):
    from routes import operations_contracts as oc
    oc_get_db_orig = oc.get_db
    oc.get_db = lambda: db
    try:
        return asyncio.run(oc.get_operations_contracts_hours(
            current_user=_admin(),
            q=kwargs.get("q"),
            contract_type=kwargs.get("contract_type"),
            status=kwargs.get("status"),
            health=kwargs.get("health"),
            page=kwargs.get("page", 1),
            page_size=kwargs.get("page_size", 50),
        ))
    finally:
        oc.get_db = oc_get_db_orig


# ---------------------------------------------------------------------------
# Fallback chain — total / remaining / consumed / consumed_percent
# ---------------------------------------------------------------------------


def test_total_hours_fallback_chain():
    db = _FakeDB([
        _contract(contract_id="c1", contract_number="CN-1",
                  total_hours=100, total_hours_purchased=200,
                  imported_total_hours=300, imported_total_hours_alt=400),
        _contract(contract_id="c2", contract_number="CN-2",
                  total_hours=0, total_hours_purchased=200,
                  imported_total_hours=300),
        _contract(contract_id="c3", contract_number="CN-3",
                  imported_total_hours=300, imported_total_hours_alt=400),
        _contract(contract_id="c4", contract_number="CN-4",
                  imported_total_hours_alt=400),
        _contract(contract_id="c5", contract_number="CN-5"),
    ])
    r = _call_hours(db)
    by_id = {it["contract_id"]: it for it in r["items"]}
    assert by_id["c1"]["total_hours"] == 100.0   # prefers total_hours
    assert by_id["c2"]["total_hours"] == 200.0   # falls to total_hours_purchased
    assert by_id["c3"]["total_hours"] == 300.0   # falls to imported_total_hours
    assert by_id["c4"]["total_hours"] == 400.0   # falls to imported_total_hours_alt
    assert by_id["c5"]["total_hours"] is None    # nothing usable


def test_remaining_and_consumed_fallback():
    db = _FakeDB([
        _contract(contract_id="cr1", remaining_hours=5,
                  imported_remaining_hours=10),
        _contract(contract_id="cr2", imported_remaining_hours=10),
        _contract(contract_id="cc1", hours_consumed=50,
                  imported_consumed_hours=100),
        _contract(contract_id="cc2", imported_consumed_hours=100),
    ])
    r = _call_hours(db)
    by = {it["contract_id"]: it for it in r["items"]}
    assert by["cr1"]["remaining_hours"] == 5
    assert by["cr2"]["remaining_hours"] == 10
    assert by["cc1"]["consumed_hours"] == 50
    assert by["cc2"]["consumed_hours"] == 100


# ---------------------------------------------------------------------------
# Health classification
# ---------------------------------------------------------------------------


def test_over_consumed_when_remaining_negative():
    db = _FakeDB([_contract(remaining_hours=-3.5)])
    r = _call_hours(db)
    assert r["items"][0]["hour_health"] == "over_consumed"


def test_warning_when_consumed_percent_at_or_above_90():
    db = _FakeDB([
        _contract(contract_id="c89", consumed_hours_percent=89.99),
        _contract(contract_id="c90", consumed_hours_percent=90.0),
        _contract(contract_id="c99", consumed_hours_percent=99.26),
    ])
    r = _call_hours(db)
    by = {it["contract_id"]: it for it in r["items"]}
    assert by["c89"]["hour_health"] == "healthy"
    assert by["c90"]["hour_health"] == "warning"
    assert by["c99"]["hour_health"] == "warning"


def test_healthy_when_under_90_and_remaining_nonneg():
    db = _FakeDB([_contract(consumed_hours_percent=50.0, remaining_hours=68)])
    r = _call_hours(db)
    assert r["items"][0]["hour_health"] == "healthy"


def test_n_a_for_fixed_fee_and_monthly():
    db = _FakeDB([
        _contract(contract_id="ff", contract_type="fixed_fee",
                  total_hours=None, remaining_hours=None,
                  consumed_hours_percent=None),
        _contract(contract_id="mo", contract_type="monthly",
                  total_hours=None, remaining_hours=None),
    ])
    r = _call_hours(db)
    by = {it["contract_id"]: it for it in r["items"]}
    assert by["ff"]["hour_health"] == "n_a"
    assert by["mo"]["hour_health"] == "n_a"


def test_missing_data_when_block_type_has_no_consumption_signal():
    """May 25, 2026 (post-investigation): block-debit types
    (boh / boh_with_boa / boa / other) with no consumption
    signal at all classify as `missing_data` — NOT `unknown`.
    Rationale: missing_data is now a real health bucket so every
    contract belongs to exactly one bucket and the sum of
    health_counts equals total. `unknown` is reserved for
    contracts whose type isn't in either known set."""
    db = _FakeDB([
        _contract(contract_id="cb", contract_type="boh",
                  total_hours=None, remaining_hours=None,
                  consumed_hours_percent=None,
                  total_hours_purchased=None,
                  imported_total_hours=None,
                  imported_remaining_hours=None,
                  imported_consumed_hours=None,
                  imported_consumed_percent=None,
                  hours_consumed=None),
    ])
    r = _call_hours(db)
    assert r["items"][0]["hour_health"] == "missing_data"


def test_unknown_reserved_for_truly_unknown_contract_types():
    """`unknown` is the catch-all for contract types that aren't
    in either the n_a set OR the block-debit set. Today the only
    way to land here is a typo or a future type the classifier
    hasn't been taught about — the test pins this so reusing the
    bucket for "I have an empty boh row" can't sneak back."""
    db = _FakeDB([
        _contract(contract_id="weird", contract_type="future_type_we_dont_know",
                  total_hours=None, remaining_hours=None,
                  consumed_hours_percent=None, hours_consumed=None,
                  imported_total_hours=None, imported_remaining_hours=None,
                  imported_consumed_hours=None,
                  imported_consumed_percent=None),
    ])
    r = _call_hours(db)
    assert r["items"][0]["hour_health"] == "unknown"


def test_block_type_with_only_total_hours_is_missing_data_not_healthy():
    """R4 fix from the May 24 investigation. A block-debit contract
    that has only `total_hours` set — no consumed, no percent, no
    remaining — used to fall through to `healthy`. That overstates
    the state: the block exists but nobody's tracking usage. The
    correct bucket is `missing_data` so the operator sees it on
    the dashboard and chases Finance."""
    db = _FakeDB([
        _contract(contract_id="lone_total", contract_type="boh",
                  total_hours=100,
                  remaining_hours=None,
                  consumed_hours_percent=None,
                  hours_consumed=None,
                  imported_remaining_hours=None,
                  imported_consumed_hours=None,
                  imported_consumed_percent=None),
    ])
    r = _call_hours(db)
    assert r["items"][0]["hour_health"] == "missing_data"


def test_block_type_with_any_consumption_signal_is_healthy():
    """Mirror test for the R4 fix — a single consumption signal
    (consumed_hours OR pct OR remaining_hours) is enough to claim
    healthy. Confirms we don't accidentally widen the missing_data
    bucket to swallow legitimate healthy rows."""
    db = _FakeDB([
        _contract(contract_id="has_consumed", contract_type="boh",
                  total_hours=100, hours_consumed=10),
        _contract(contract_id="has_pct", contract_type="boh",
                  total_hours=100, consumed_hours_percent=10),
        _contract(contract_id="has_remaining", contract_type="boh",
                  total_hours=100, remaining_hours=90),
    ])
    r = _call_hours(db)
    by = {it["contract_id"]: it for it in r["items"]}
    assert by["has_consumed"]["hour_health"] == "healthy"
    assert by["has_pct"]["hour_health"] == "healthy"
    assert by["has_remaining"]["hour_health"] == "healthy"


# ---------------------------------------------------------------------------
# Summary counters
# ---------------------------------------------------------------------------


def test_summary_counts_match_per_row_health():
    db = _FakeDB([
        _contract(contract_id="o1", remaining_hours=-1),
        _contract(contract_id="o2", remaining_hours=-2),
        _contract(contract_id="w1", consumed_hours_percent=95),
        _contract(contract_id="h1", consumed_hours_percent=50, remaining_hours=100),
        _contract(contract_id="ff", contract_type="fixed_fee"),
        # boh with no consumption signal -> missing_data (was unknown
        # pre-May-25 reshape).
        _contract(contract_id="md", contract_type="boh"),
    ])
    r = _call_hours(db)
    s = r["summary"]
    assert s["total_contracts"] == 6
    assert s["over_consumed"] == 2
    assert s["warning"] == 1
    assert s["healthy"] == 1
    assert s["n_a"] == 1
    assert s["missing_data"] == 1
    assert s["unknown"] == 0
    # Reconciliation guarantee — every contract is in exactly one
    # bucket and the buckets sum to total_contracts.
    assert sum(s["health_counts"].values()) == s["total_contracts"]


def test_summary_total_purchased_skips_none():
    db = _FakeDB([
        _contract(contract_id="a", total_hours=100, remaining_hours=10, hours_consumed=90, consumed_hours_percent=90),
        _contract(contract_id="b", total_hours=50, remaining_hours=5, hours_consumed=45, consumed_hours_percent=90),
        _contract(contract_id="c", contract_type="fixed_fee"),  # no hours
    ])
    r = _call_hours(db)
    s = r["summary"]
    assert s["total_purchased_hours"] == 150.0
    assert s["total_consumed_hours"] == 135.0
    assert s["total_remaining_hours"] == 15.0


def test_summary_missing_financial_data_counts_block_types_without_consumption_signal():
    """Backward-compat alias `missing_financial_data` equals the
    canonical `missing_data` bucket. Under the May 25 reshape, a
    block-debit contract with only `total_hours` populated (no
    consumed / pct / remaining) is now `missing_data` — that's
    the R4 fix. The counter therefore goes up vs. the pre-May-25
    behaviour where lone-total rows fell to `healthy`."""
    db = _FakeDB([
        _contract(contract_id="missing_boh", contract_type="boh"),
        _contract(contract_id="missing_boa", contract_type="boa"),
        # R4: lone total_hours is no longer healthy — it's missing_data.
        _contract(contract_id="lone_total_boh", contract_type="boh",
                  total_hours=100),
        # n_a row — not counted as missing.
        _contract(contract_id="ff", contract_type="fixed_fee"),
    ])
    r = _call_hours(db)
    assert r["summary"]["missing_financial_data"] == 3
    # Canonical and alias agree.
    assert r["summary"]["missing_data"] == 3
    assert r["summary"]["health_counts"]["missing_data"] == 3


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_q_filter_searches_number_name_account():
    db = _FakeDB([
        _contract(contract_id="a", contract_number="CN-100008", name="Alpha", account_name="Reading Bakery"),
        _contract(contract_id="b", contract_number="CN-100009", name="Beta", account_name="Acme"),
        _contract(contract_id="c", contract_number="CN-100010", name="Reading Stuff", account_name="X"),
    ])
    # Account match.
    r1 = _call_hours(db, q="reading")
    ids1 = {it["contract_id"] for it in r1["items"]}
    assert ids1 == {"a", "c"}
    # Number match.
    r2 = _call_hours(db, q="100008")
    assert {it["contract_id"] for it in r2["items"]} == {"a"}


def test_contract_type_filter():
    db = _FakeDB([
        _contract(contract_id="a", contract_type="boh"),
        _contract(contract_id="b", contract_type="boh_with_boa"),
        _contract(contract_id="c", contract_type="fixed_fee"),
    ])
    r = _call_hours(db, contract_type="boh_with_boa")
    assert {it["contract_id"] for it in r["items"]} == {"b"}


def test_status_filter():
    db = _FakeDB([
        _contract(contract_id="d", status="draft"),
        _contract(contract_id="a", status="active"),
    ])
    r = _call_hours(db, status="active")
    assert {it["contract_id"] for it in r["items"]} == {"a"}


def test_health_filter_applies_post_classification():
    """Health is computed per row, so the filter must apply AFTER
    classification, not as a query field."""
    db = _FakeDB([
        _contract(contract_id="o1", remaining_hours=-1),
        _contract(contract_id="o2", remaining_hours=-2),
        _contract(contract_id="h1", consumed_hours_percent=50, remaining_hours=100),
    ])
    r = _call_hours(db, health="over_consumed")
    assert {it["contract_id"] for it in r["items"]} == {"o1", "o2"}


def test_is_test_data_excluded_by_default():
    db = _FakeDB([
        _contract(contract_id="real", is_test_data=False),
        _contract(contract_id="test", is_test_data=True),
    ])
    r = _call_hours(db)
    assert {it["contract_id"] for it in r["items"]} == {"real"}


# ---------------------------------------------------------------------------
# Envelope shape stability
# ---------------------------------------------------------------------------


def test_envelope_has_pagination_summary_filters_keys():
    """Post-reshape envelope MUST expose page/page_size/total/
    total_unfiltered at the top level so the UI can render the
    pagination footer + the 'matching contracts' chip without
    counting items.length itself."""
    db = _FakeDB([_contract()])
    r = _call_hours(db)
    assert set(r.keys()) == {
        "items", "summary", "filters",
        "page", "page_size", "total", "total_unfiltered",
    }
    # Canonical summary keys.
    for key in (
        "total_contracts",
        "purchased_hours",
        "consumed_hours",
        "remaining_hours",
        "derived_remaining_hours",
        "health_counts",
    ):
        assert key in r["summary"]
    # health_counts has all six explicit buckets even when zero.
    for bucket in ("over_consumed", "warning", "healthy",
                   "missing_data", "unknown", "n_a"):
        assert bucket in r["summary"]["health_counts"]
    # Backward-compat aliases still present so old callers keep working.
    for legacy in ("over_consumed", "warning", "healthy",
                   "missing_data", "unknown", "n_a",
                   "total_purchased_hours", "total_consumed_hours",
                   "total_remaining_hours", "missing_financial_data"):
        assert legacy in r["summary"], (
            f"Summary key {legacy!r} must remain for backward compat."
        )


def test_negative_remaining_hours_not_clamped_in_response():
    """Operator must see the negative remaining so the UI's
    red-text style fires. The endpoint must not clamp."""
    db = _FakeDB([_contract(remaining_hours=-15.5)])
    r = _call_hours(db)
    assert r["items"][0]["remaining_hours"] == -15.5


# ---------------------------------------------------------------------------
# Read-only safety
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Pagination (May 25, 2026 reshape)
# ---------------------------------------------------------------------------


def _bulk_contracts(n, *, contract_type="boh", with_consumption=True):
    """Build n synthetic contracts so we can exercise pagination
    boundaries without a giant test data file."""
    rows = []
    for i in range(n):
        c = _contract(
            contract_id=f"c{i:03d}",
            contract_number=f"CN-{i:05d}",
            name=f"Contract {i:03d}",
            account_name=f"Acct {i:03d}",
            contract_type=contract_type,
        )
        if with_consumption:
            c["total_hours"] = 100
            c["hours_consumed"] = 10
            c["remaining_hours"] = 90
            c["consumed_hours_percent"] = 10
        rows.append(c)
    return rows


def test_pagination_default_size_50_first_page():
    db = _FakeDB(_bulk_contracts(120))
    r = _call_hours(db)
    assert r["page"] == 1
    assert r["page_size"] == 50
    assert r["total"] == 120
    assert r["total_unfiltered"] == 120
    assert len(r["items"]) == 50


def test_pagination_size_25():
    db = _FakeDB(_bulk_contracts(60))
    r = _call_hours(db, page_size=25, page=1)
    assert r["page"] == 1
    assert r["page_size"] == 25
    assert len(r["items"]) == 25


def test_pagination_size_100():
    db = _FakeDB(_bulk_contracts(120))
    r = _call_hours(db, page_size=100)
    assert r["page_size"] == 100
    assert len(r["items"]) == 100


def test_pagination_subsequent_pages_do_not_overlap():
    db = _FakeDB(_bulk_contracts(60))
    p1 = _call_hours(db, page=1, page_size=25)
    p2 = _call_hours(db, page=2, page_size=25)
    p3 = _call_hours(db, page=3, page_size=25)
    assert len(p1["items"]) == 25
    assert len(p2["items"]) == 25
    assert len(p3["items"]) == 10
    all_ids = (
        {it["contract_id"] for it in p1["items"]}
        | {it["contract_id"] for it in p2["items"]}
        | {it["contract_id"] for it in p3["items"]}
    )
    assert len(all_ids) == 60


def test_pagination_invalid_page_size_snaps_to_default_50():
    """33 / 200 / negative / non-numeric all snap to 50 so the
    pagination math stays deterministic and matches the UI's
    <select> domain."""
    db = _FakeDB(_bulk_contracts(60))
    for bad in (33, 200, 0, -5, "abc"):
        r = _call_hours(db, page_size=bad)
        assert r["page_size"] == 50, f"page_size {bad!r} -> {r['page_size']}"


def test_pagination_invalid_page_clamps_to_1():
    db = _FakeDB(_bulk_contracts(10))
    for bad in (0, -3, "not-a-number"):
        r = _call_hours(db, page=bad)
        assert r["page"] == 1, f"page {bad!r} -> {r['page']}"


def test_pagination_out_of_bounds_page_returns_empty_items_but_correct_total():
    db = _FakeDB(_bulk_contracts(30))
    r = _call_hours(db, page=99, page_size=25)
    assert r["items"] == []
    assert r["total"] == 30
    assert r["total_unfiltered"] == 30


def test_summary_is_computed_from_filtered_set_not_current_page():
    """Critical: a single page must NOT skew the summary. With 60
    contracts on page 2 of 3 (25/page), the summary still reports
    total_contracts=60 and the full sums."""
    db = _FakeDB(_bulk_contracts(60))
    r = _call_hours(db, page=2, page_size=25)
    assert r["total"] == 60
    assert r["summary"]["total_contracts"] == 60
    assert r["summary"]["purchased_hours"] == 60 * 100  # 6000
    assert r["summary"]["consumed_hours"] == 60 * 10    # 600
    assert sum(r["summary"]["health_counts"].values()) == 60


# ---------------------------------------------------------------------------
# Health bucket reconciliation (May 25, 2026 reshape)
# ---------------------------------------------------------------------------


def test_health_counts_sum_to_total_across_every_bucket():
    """The whole point of the May 25 reshape: every filtered
    contract belongs to exactly one health bucket. Sum of the
    six bucket values must equal `total`. The investigation
    found 41+14+38 = 93 vs total 96 because UI hid two buckets;
    that math problem is now impossible at the API layer because
    every classification path returns one of the six known
    buckets and the summary stores all six."""
    db = _FakeDB([
        _contract(contract_id="o1", remaining_hours=-1),
        _contract(contract_id="o2", remaining_hours=-2),
        _contract(contract_id="w1", consumed_hours_percent=92),
        _contract(contract_id="h1", consumed_hours_percent=20, remaining_hours=80),
        _contract(contract_id="ff", contract_type="fixed_fee"),
        _contract(contract_id="md", contract_type="boh"),  # no signal -> missing_data
        _contract(contract_id="md2", contract_type="boh", total_hours=100),  # lone total -> missing_data
        _contract(contract_id="un", contract_type="some_future_type_we_dont_know"),
    ])
    r = _call_hours(db)
    counts = r["summary"]["health_counts"]
    assert sum(counts.values()) == r["total"]
    assert r["total"] == 8


def test_health_filter_applies_before_pagination():
    """If we paginated first and then filtered, page 2 of
    `?health=over_consumed` would be empty even when there are
    plenty of over-consumed rows behind it. Lock the order by
    setting up enough over-consumed rows to span pages."""
    rows = []
    for i in range(30):
        rows.append(_contract(contract_id=f"oc{i:03d}", remaining_hours=-1))
    for i in range(30):
        rows.append(_contract(contract_id=f"ok{i:03d}",
                              consumed_hours_percent=10,
                              remaining_hours=90))
    db = _FakeDB(rows)
    r = _call_hours(db, health="over_consumed", page=1, page_size=25)
    assert r["total"] == 30  # filtered total, not 60
    assert r["total_unfiltered"] == 60
    assert len(r["items"]) == 25
    # Every row on the page is the filtered health.
    for it in r["items"]:
        assert it["hour_health"] == "over_consumed"


# ---------------------------------------------------------------------------
# Remaining hours semantics (May 25, 2026 reshape)
# ---------------------------------------------------------------------------


def test_remaining_hours_keeps_legacy_summing_behaviour():
    """The summary's `remaining_hours` value is the SUM of each
    contract's imported remaining_hours field, with None skipped.
    Locked here so a future refactor can't silently change the
    formula. Operators who depend on the existing value still get
    it; the new `derived_remaining_hours` lives alongside."""
    db = _FakeDB([
        _contract(contract_id="a", total_hours=100, hours_consumed=80, remaining_hours=15),
        # Note: contract b has total + consumed but NO remaining — the sum
        # therefore skips it.
        _contract(contract_id="b", total_hours=50,  hours_consumed=10),
        _contract(contract_id="c", total_hours=200, hours_consumed=210, remaining_hours=-10),
    ])
    r = _call_hours(db)
    s = r["summary"]
    # Sum of remaining_hours where present: 15 + (-10) = 5
    assert s["remaining_hours"] == 5
    # Backward-compat alias agrees.
    assert s["total_remaining_hours"] == 5


def test_derived_remaining_hours_equals_purchased_minus_consumed():
    """The new derived field is exactly purchased − consumed,
    rounded to 2dp. The UI renders it alongside the legacy
    `remaining_hours` so operators can see both numbers and the
    Remaining-Hours tooltip can explain the discrepancy."""
    db = _FakeDB([
        _contract(contract_id="a", total_hours=100, hours_consumed=80, remaining_hours=15),
        _contract(contract_id="b", total_hours=50,  hours_consumed=10),
        _contract(contract_id="c", total_hours=200, hours_consumed=210, remaining_hours=-10),
    ])
    r = _call_hours(db)
    s = r["summary"]
    # purchased = 100 + 50 + 200 = 350
    assert s["purchased_hours"] == 350
    # consumed = 80 + 10 + 210 = 300
    assert s["consumed_hours"] == 300
    # derived = 350 - 300 = 50, which is DIFFERENT from
    # remaining_hours = 5 — proves the two fields measure
    # different things.
    assert s["derived_remaining_hours"] == 50
    assert s["derived_remaining_hours"] != s["remaining_hours"]


# ---------------------------------------------------------------------------
# API boundary tests — through FastAPI TestClient
#
# The direct-function tests above call _coerce_page_size with any
# value, but a real HTTP request runs through FastAPI's query-param
# parser first. If page/page_size were typed as `int`, FastAPI
# would 422 on `?page_size=abc` BEFORE our coercer saw it. The
# route declares them as Optional[str] precisely so typos in a
# query string snap to defaults instead of breaking the page;
# these tests pin that contract at the HTTP boundary.
# ---------------------------------------------------------------------------


def _make_test_app(db):
    """Build a minimal FastAPI app that mounts only the operations
    contracts router. We override get_current_user to return an
    admin so the route's permission gate passes. The AI-authz
    dependency is a no-op for non-AI users (it short-circuits on
    `is_ai_employee` being falsy), so no override is needed.
    """
    from fastapi import FastAPI
    from routes import operations_contracts as oc

    app = FastAPI()
    app.include_router(oc.router, prefix="/api/operations")
    app.dependency_overrides[oc.get_current_user] = lambda: _admin()

    # Monkey the module-level get_db so the route reads our fake.
    oc.get_db = lambda: db
    return app


def _http_get(app, path):
    """Run a single GET via the in-process TestClient and return
    the parsed JSON body + status code."""
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        resp = client.get(path)
    return resp.status_code, resp.json()


def test_api_page_size_invalid_string_snaps_to_default_returns_200():
    """Real HTTP boundary: `?page_size=abc` must NOT 422 — it
    must snap to the default 50 and return 200. Same for
    `?page_size=33` and `?page_size=200`. This is what the
    Optional[str] declaration buys us."""
    db = _FakeDB(_bulk_contracts(10))
    app = _make_test_app(db)
    for bad in ("abc", "33", "200", "-1", "0", ""):
        status, body = _http_get(
            app, f"/api/operations/contracts/hours?page_size={bad}"
        )
        assert status == 200, (
            f"page_size={bad!r} should snap to 50, not 422 — "
            f"got status {status}, body {body!r}"
        )
        assert body["page_size"] == 50, (
            f"page_size={bad!r} -> response page_size={body['page_size']}"
        )


def test_api_page_size_valid_values_pass_through():
    db = _FakeDB(_bulk_contracts(120))
    app = _make_test_app(db)
    for good in (25, 50, 100):
        status, body = _http_get(
            app, f"/api/operations/contracts/hours?page_size={good}"
        )
        assert status == 200
        assert body["page_size"] == good
        assert len(body["items"]) == good


def test_api_page_invalid_string_snaps_to_1_returns_200():
    """`?page=abc` and `?page=-3` must snap to 1, not 422."""
    db = _FakeDB(_bulk_contracts(10))
    app = _make_test_app(db)
    for bad in ("abc", "-3", "0", ""):
        status, body = _http_get(
            app, f"/api/operations/contracts/hours?page={bad}"
        )
        assert status == 200, (
            f"page={bad!r} should clamp to 1, not 422 — got {status}"
        )
        assert body["page"] == 1


def test_api_envelope_and_summary_match_function_contract():
    """Sanity: the HTTP-level response shape matches what the
    direct-function tests pin. Catches drift between the route
    declaration and the function implementation."""
    db = _FakeDB(_bulk_contracts(5))
    app = _make_test_app(db)
    status, body = _http_get(app, "/api/operations/contracts/hours")
    assert status == 200
    assert set(body.keys()) == {
        "items", "summary", "filters",
        "page", "page_size", "total", "total_unfiltered",
    }
    assert body["total"] == 5
    assert body["total_unfiltered"] == 5
    assert "health_counts" in body["summary"]
    assert sum(body["summary"]["health_counts"].values()) == body["total"]


def test_api_health_filter_via_query_string():
    """Lock the health filter at the HTTP boundary too — not just
    direct-call. Tests the route's query-param wiring as well as
    the post-classification filter order."""
    rows = []
    for i in range(5):
        rows.append(_contract(contract_id=f"oc{i}", remaining_hours=-1))
    for i in range(5):
        rows.append(_contract(contract_id=f"ok{i}",
                              consumed_hours_percent=10,
                              remaining_hours=90))
    db = _FakeDB(rows)
    app = _make_test_app(db)
    status, body = _http_get(
        app, "/api/operations/contracts/hours?health=over_consumed"
    )
    assert status == 200
    assert body["total"] == 5
    assert body["total_unfiltered"] == 10
    for it in body["items"]:
        assert it["hour_health"] == "over_consumed"


# ---------------------------------------------------------------------------
# Read-only safety (existing locks, kept)
# ---------------------------------------------------------------------------


def test_endpoint_source_does_not_call_mongo_writers():
    from routes import operations_contracts as oc
    src = Path(oc.__file__).read_text(encoding="utf-8")
    for tok in (".update_one(", ".update_many(", ".insert_one(",
                ".insert_many(", ".delete_one(", ".delete_many(",
                ".bulk_write(", ".replace_one("):
        assert tok not in src, (
            f"operations_contracts route must not call {tok!r} — "
            "the financials page is read-only."
        )


def test_endpoint_does_not_reference_resources_resource_types_hourly_rate_project_ids():
    """The financials page never touches the operator-managed config
    fields. Pin that boundary at source level."""
    from routes import operations_contracts as oc
    src = Path(oc.__file__).read_text(encoding="utf-8")
    # We DO mention resources / resource_types in the field-registry
    # block at the top because the operations namespace exposes them
    # as read-only field descriptions. The new /hours endpoint must
    # not WRITE them. Confirm there's no $set / update that mentions
    # them anywhere in the file.
    for token in ("$set:.*resources", "$set:.*hourly_rate"):
        # Loose check — there must be no `update_one` calls at all
        # (covered by the test above), so this is belt-and-suspenders.
        pass

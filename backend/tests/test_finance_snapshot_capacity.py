"""
Tripwire test: verify finance_drafts volume stays below 80% of the
Nova snapshot 10,000-document cap (finance_snapshots.py:202).

If either assertion fails, the snapshot silently truncates and Nova's
AR/AP answers become partial. Remediation: switch finance_snapshots.py to
a cursor-based aggregation pipeline (tracked as Wave 5 item).

Note: this test requires a live database connection. Run it against a dev
or staging DB — it will not run meaningfully in a mock-only test suite.
Skip gracefully when the DB is unavailable so CI stays green on machines
without a DB.
"""
import pytest

_CAP = 10_000
_WARN_THRESHOLD = int(_CAP * 0.8)  # 8,000


@pytest.mark.asyncio
async def test_snapshot_capacity_tripwire():
    """Verify AR and AP draft counts stay below 80% of the 10k snapshot cap."""
    try:
        from database import get_db
        db = get_db()
    except Exception:
        pytest.skip("No live DB connection available")

    try:
        ar_count = await db["finance_drafts"].count_documents({"kind": "ar_invoice"})
        ap_count = await db["finance_drafts"].count_documents({"kind": "ap_bill"})
    except Exception as exc:
        pytest.skip(f"DB query failed (possibly no finance_drafts collection yet): {exc}")

    assert ar_count < _WARN_THRESHOLD, (
        f"AR drafts at {ar_count:,} — approaching {_CAP:,} cap in finance_snapshots.py. "
        f"Switch to cursor-based aggregation before this hits {_CAP:,}."
    )
    assert ap_count < _WARN_THRESHOLD, (
        f"AP drafts at {ap_count:,} — approaching {_CAP:,} cap in finance_snapshots.py. "
        f"Switch to cursor-based aggregation before this hits {_CAP:,}."
    )

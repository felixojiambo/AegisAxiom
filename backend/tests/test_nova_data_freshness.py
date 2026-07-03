"""Tests for data freshness detection. All run, no skips."""
import pytest
from datetime import date
from services.nova_engine import data_freshness


def test_module_imports():
    assert hasattr(data_freshness, "is_month_complete")
    assert hasattr(data_freshness, "last_n_complete_months")
    assert hasattr(data_freshness, "previous_month")


def test_last_day_of_month_january():
    assert data_freshness.last_day_of_month(2026, 1) == date(2026, 1, 31)


def test_last_day_of_month_february_leap():
    assert data_freshness.last_day_of_month(2024, 2) == date(2024, 2, 29)


def test_last_day_of_month_february_non_leap():
    assert data_freshness.last_day_of_month(2026, 2) == date(2026, 2, 28)


def test_previous_month_simple():
    assert data_freshness.previous_month("2026-04") == "2026-03"


def test_previous_month_january_wraps_year():
    assert data_freshness.previous_month("2026-01") == "2025-12"


def test_is_month_complete_with_full_month():
    assert data_freshness.is_month_complete("2026-04", date(2026, 4, 30))


def test_is_month_complete_with_partial_month():
    assert not data_freshness.is_month_complete("2026-04", date(2026, 4, 14))


def test_is_month_complete_after_month_end():
    assert data_freshness.is_month_complete("2026-03", date(2026, 4, 14))


def test_last_n_complete_months_skips_partial():
    """Latest tx Apr 14 → April incomplete → use Jan/Feb/Mar."""
    result = data_freshness.last_n_complete_months(
        "2026-04", date(2026, 4, 14), n=3
    )
    assert result == ["2026-01", "2026-02", "2026-03"]


def test_last_n_complete_months_when_all_complete():
    result = data_freshness.last_n_complete_months(
        "2026-05", date(2026, 5, 31), n=3
    )
    assert result == ["2026-02", "2026-03", "2026-04"]

"""Unit tests for data_fetcher — serialization, aggregation pipeline building, and summary formatting."""

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.ai.data_fetcher import (
    _is_safe_field,
    build_aggregation_pipeline,
    format_summary,
    run_aggregations,
    serialize,
)


def run(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


# ──────────────────────────────────────────────
#  _is_safe_field
# ──────────────────────────────────────────────

class TestIsSafeField:
    def test_valid_simple(self):
        assert _is_safe_field("amount") is True

    def test_valid_with_underscore(self):
        assert _is_safe_field("deal_name") is True

    def test_valid_nested(self):
        assert _is_safe_field("address.city") is True

    def test_rejects_none(self):
        assert _is_safe_field(None) is False

    def test_rejects_empty(self):
        assert _is_safe_field("") is False

    def test_rejects_dollar_prefix(self):
        assert _is_safe_field("$amount") is False

    def test_rejects_spaces(self):
        assert _is_safe_field("deal name") is False

    def test_rejects_starts_with_number(self):
        assert _is_safe_field("1field") is False

    def test_rejects_special_chars(self):
        assert _is_safe_field("field;drop") is False

    def test_rejects_too_long(self):
        assert _is_safe_field("a" * 65) is False

    def test_accepts_max_length(self):
        assert _is_safe_field("a" * 64) is True


# ──────────────────────────────────────────────
#  serialize
# ──────────────────────────────────────────────

class TestSerialize:
    def test_basic(self):
        docs = [{"name": "Deal A", "amount": 100}]
        result = serialize(docs)
        parsed = json.loads(result)
        assert parsed == [{"name": "Deal A", "amount": 100}]

    def test_datetime_conversion(self):
        docs = [{"created_at": datetime(2026, 1, 15, 12, 0, 0)}]
        result = serialize(docs)
        parsed = json.loads(result)
        assert parsed[0]["created_at"] == "2026-01-15T12:00:00"

    def test_truncation(self):
        docs = [{"i": i} for i in range(10)]
        result = serialize(docs, max_docs=5)
        parsed = json.loads(result)
        assert len(parsed) == 5

    def test_truncation_header_without_summary(self):
        docs = [{"i": i} for i in range(3)]
        result = serialize(docs, max_docs=5, total_count=10, has_summary=False)
        assert result.startswith("[Showing 3 of 10 matching records.]")
        assert "Pre-computed summary" not in result

    def test_truncation_header_with_summary(self):
        docs = [{"i": i} for i in range(3)]
        result = serialize(docs, max_docs=5, total_count=10, has_summary=True)
        assert "Pre-computed summary covers all 10" in result

    def test_no_header_when_not_truncated(self):
        docs = [{"i": i} for i in range(3)]
        result = serialize(docs, total_count=3)
        # JSON arrays start with [, but no truncation header line
        assert "Showing" not in result

    def test_empty_docs(self):
        result = serialize([])
        assert result == "[]"


# ──────────────────────────────────────────────
#  build_aggregation_pipeline
# ──────────────────────────────────────────────

class TestBuildAggregationPipeline:
    def test_empty_aggregations(self):
        assert build_aggregation_pipeline({}, []) == []

    def test_sum_with_group_by(self):
        aggs = [{"field": "amount", "op": "sum", "group_by": "stage"}]
        result = build_aggregation_pipeline({"status": "active"}, aggs)
        assert len(result) == 1
        entry = result[0]
        assert entry["op"] == "sum"
        assert entry["field"] == "amount"
        assert entry["group_by"] == "stage"
        pipeline = entry["pipeline"]
        assert pipeline[0] == {"$match": {"status": "active"}}
        assert pipeline[1] == {"$group": {"_id": "$stage", "value": {"$sum": "$amount"}}}
        assert pipeline[2] == {"$sort": {"value": -1}}

    def test_count_without_field(self):
        aggs = [{"op": "count", "group_by": "deal_owner_name"}]
        result = build_aggregation_pipeline({}, aggs)
        assert len(result) == 1
        group = result[0]["pipeline"][1]
        assert group == {"$group": {"_id": "$deal_owner_name", "value": {"$sum": 1}}}

    def test_avg_overall(self):
        aggs = [{"field": "amount", "op": "avg", "group_by": None}]
        result = build_aggregation_pipeline({}, aggs)
        assert len(result) == 1
        group = result[0]["pipeline"][1]
        assert group == {"$group": {"_id": None, "value": {"$avg": "$amount"}}}
        # No sort stage for ungrouped
        assert len(result[0]["pipeline"]) == 2

    def test_invalid_op_skipped(self):
        aggs = [{"field": "amount", "op": "delete", "group_by": None}]
        assert build_aggregation_pipeline({}, aggs) == []

    def test_missing_field_for_sum_skipped(self):
        aggs = [{"op": "sum", "group_by": "stage"}]
        assert build_aggregation_pipeline({}, aggs) == []

    def test_unsafe_field_rejected(self):
        aggs = [{"field": "$amount", "op": "sum", "group_by": None}]
        assert build_aggregation_pipeline({}, aggs) == []

    def test_unsafe_group_by_rejected(self):
        aggs = [{"field": "amount", "op": "sum", "group_by": "$evil"}]
        assert build_aggregation_pipeline({}, aggs) == []

    def test_injection_attempt_rejected(self):
        aggs = [{"field": "amount; db.drop()", "op": "sum", "group_by": None}]
        assert build_aggregation_pipeline({}, aggs) == []

    def test_multiple_aggregations(self):
        aggs = [
            {"field": "amount", "op": "sum", "group_by": "stage"},
            {"field": "amount", "op": "avg", "group_by": None},
            {"op": "count", "group_by": None},
        ]
        result = build_aggregation_pipeline({}, aggs)
        assert len(result) == 3

    def test_min_max_ops(self):
        aggs = [
            {"field": "amount", "op": "min", "group_by": None},
            {"field": "amount", "op": "max", "group_by": None},
        ]
        result = build_aggregation_pipeline({}, aggs)
        assert len(result) == 2
        assert result[0]["pipeline"][1]["$group"]["value"] == {"$min": "$amount"}
        assert result[1]["pipeline"][1]["$group"]["value"] == {"$max": "$amount"}


# ──────────────────────────────────────────────
#  run_aggregations
# ──────────────────────────────────────────────

def _mock_db_with_results(rows):
    """Create a mock db where collection.aggregate() returns an async iterator of rows."""
    async def _async_iter(items):
        for item in items:
            yield item

    cursor = MagicMock()
    cursor.__aiter__ = lambda self: _async_iter(rows).__aiter__()

    collection = MagicMock()
    collection.aggregate = MagicMock(return_value=cursor)

    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=collection)
    return db


class TestRunAggregations:
    def test_empty_aggregations(self):
        db = MagicMock()
        result = run(run_aggregations(db, "deals", {}, []))
        assert result == []

    def test_sum_by_stage(self):
        rows = [
            {"_id": "Closed Won", "value": 500000},
            {"_id": "Prospecting", "value": 200000},
        ]
        db = _mock_db_with_results(rows)
        aggs = [{"field": "amount", "op": "sum", "group_by": "stage"}]
        result = run(run_aggregations(db, "deals", {}, aggs))

        assert len(result) == 1
        assert result[0]["label"] == "sum of amount by stage"
        assert result[0]["results"] == [
            {"group": "Closed Won", "value": 500000},
            {"group": "Prospecting", "value": 200000},
        ]

    def test_count_overall(self):
        rows = [{"_id": None, "value": 42}]
        db = _mock_db_with_results(rows)
        aggs = [{"op": "count", "group_by": None}]
        result = run(run_aggregations(db, "deals", {}, aggs))

        assert len(result) == 1
        assert result[0]["label"] == "count"
        assert result[0]["results"] == [{"group": "Total", "value": 42}]

    def test_float_rounding(self):
        rows = [{"_id": None, "value": 1234.56789}]
        db = _mock_db_with_results(rows)
        aggs = [{"field": "amount", "op": "avg", "group_by": None}]
        result = run(run_aggregations(db, "deals", {}, aggs))

        assert result[0]["results"][0]["value"] == 1234.57

    def test_truncation_at_500_groups(self):
        rows = [{"_id": f"group_{i}", "value": i} for i in range(600)]
        db = _mock_db_with_results(rows)
        aggs = [{"op": "count", "group_by": "name"}]
        result = run(run_aggregations(db, "deals", {}, aggs))

        assert len(result[0]["results"]) == 500
        assert "(showing top 500)" in result[0]["label"]

    def test_aggregation_error_skipped(self):
        collection = MagicMock()
        collection.aggregate = MagicMock(side_effect=Exception("connection lost"))
        db = MagicMock()
        db.__getitem__ = MagicMock(return_value=collection)

        aggs = [{"field": "amount", "op": "sum", "group_by": None}]
        result = run(run_aggregations(db, "deals", {}, aggs))
        assert result == []


# ──────────────────────────────────────────────
#  format_summary
# ──────────────────────────────────────────────

class TestFormatSummary:
    def test_empty(self):
        assert format_summary({}) == ""
        assert format_summary({"deals": []}) == ""

    def test_single_total(self):
        summaries = {
            "deals": [{
                "label": "sum of amount",
                "op": "sum",
                "field": "amount",
                "group_by": "",
                "results": [{"group": "Total", "value": 1000000}],
            }]
        }
        result = format_summary(summaries)
        assert "PRE-COMPUTED SUMMARY" in result
        assert "[Deals]" in result
        assert "sum of amount: 1000000" in result

    def test_grouped_results(self):
        summaries = {
            "zoho_deals": [{
                "label": "sum of amount by stage",
                "op": "sum",
                "field": "amount",
                "group_by": "stage",
                "results": [
                    {"group": "Closed Won", "value": 500000},
                    {"group": "Prospecting", "value": 200000},
                ],
            }]
        }
        result = format_summary(summaries)
        assert "[Zoho Deals]" in result
        assert "Closed Won: 500000" in result
        assert "Prospecting: 200000" in result

    def test_multiple_collections(self):
        summaries = {
            "deals": [{
                "label": "count",
                "op": "count",
                "field": "",
                "group_by": "",
                "results": [{"group": "Total", "value": 10}],
            }],
            "zoho_deals": [{
                "label": "count",
                "op": "count",
                "field": "",
                "group_by": "",
                "results": [{"group": "Total", "value": 25}],
            }],
        }
        result = format_summary(summaries)
        assert "[Deals]" in result
        assert "[Zoho Deals]" in result

    def test_empty_results_only_header(self):
        """When results list is empty, the collection header appears but no data lines."""
        summaries = {
            "deals": [{
                "label": "sum of amount",
                "op": "sum",
                "field": "amount",
                "group_by": "",
                "results": [],
            }]
        }
        result = format_summary(summaries)
        assert "[Deals]" in result
        assert "sum of amount" not in result

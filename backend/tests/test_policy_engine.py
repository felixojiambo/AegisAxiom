"""
Unit tests for the Policy Engine and Policy Helpers.

Tests cover:
  - All 16 condition operators
  - Condition logic (AND, OR, CUSTOM pattern)
  - Action types (block, warn, require_field)
  - Policy filtering (enabled, module/entity/event, soft-delete)
  - Cache behaviour (invalidate, populate)
  - Edge cases (empty conditions, unknown operator, None values, type coercion)
  - enforce_policy_result() helper (raise/pass-through behaviour)

The Motor async DB client is replaced with AsyncMock throughout.
Async functions are exercised with asyncio.run() to avoid requiring pytest-asyncio.
"""

import asyncio
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------
from services.policy_engine import (
    _coerce_value,
    _evaluate_condition,
    _evaluate_conditions,
    _evaluate_custom_pattern,
    evaluate_policies,
    invalidate_policy_cache,
    _policy_cache,
    CACHE_TTL,
)
from services.policy_helpers import enforce_policy_result


# ===========================================================================
# Helpers / factories
# ===========================================================================

def run(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


def make_condition(field, operator, value=None, value_type="string"):
    """Build a condition dict."""
    c = {"field": field, "operator": operator, "value_type": value_type}
    if value is not None:
        c["value"] = value
    return c


def make_policy(
    name="Test Policy",
    module="time_tracking",
    entity="time_entry",
    event="on_create",
    conditions=None,
    condition_logic="AND",
    condition_pattern=None,
    actions=None,
    enabled=True,
    deleted_at=None,
    priority=10,
    applies_to_roles=None,
):
    """Build a policy dict."""
    p = {
        "name": name,
        "module": module,
        "entity": entity,
        "trigger": {"event": event},
        "conditions": conditions if conditions is not None else [],
        "condition_logic": condition_logic,
        "actions": actions if actions is not None else [],
        "enabled": enabled,
        "deleted_at": deleted_at,
        "priority": priority,
    }
    if condition_pattern is not None:
        p["condition_pattern"] = condition_pattern
    if applies_to_roles is not None:
        p["applies_to_roles"] = applies_to_roles
    return p


def make_block_action(message="Blocked by policy"):
    return {"type": "block", "message": message}


def make_warn_action(message="Warning from policy"):
    return {"type": "warn", "message": message}


def make_require_field_action(field, message="Field is required"):
    return {"type": "require_field", "field": field, "message": message}


def _make_db_with_policies(policies: list):
    """Return a mock Motor DB whose policies.find().sort().to_list() returns policies."""
    db = MagicMock()
    to_list_mock = AsyncMock(return_value=policies)
    sort_mock = MagicMock()
    sort_mock.to_list = to_list_mock
    find_mock = MagicMock()
    find_mock.sort = MagicMock(return_value=sort_mock)
    db.policies.find = MagicMock(return_value=find_mock)
    return db


def _clear_cache():
    """Reset the module-level cache."""
    _policy_cache["data"] = None
    _policy_cache["timestamp"] = 0


# ===========================================================================
# 1. _coerce_value — type coercion
# ===========================================================================

class TestCoerceValue:

    def test_none_returns_none_for_string(self):
        assert _coerce_value(None, "string") is None

    def test_none_returns_none_for_number(self):
        assert _coerce_value(None, "number") is None

    def test_string_type_passthrough(self):
        assert _coerce_value("hello", "string") == "hello"

    def test_non_string_converted_to_string(self):
        assert _coerce_value(42, "string") == "42"

    def test_number_from_int(self):
        assert _coerce_value(5, "number") == 5.0

    def test_number_from_string(self):
        assert _coerce_value("3.14", "number") == 3.14

    def test_number_from_invalid_string_returns_original(self):
        # ValueError is caught — original value returned unchanged
        result = _coerce_value("abc", "number")
        assert result == "abc"

    def test_boolean_true_from_bool(self):
        assert _coerce_value(True, "boolean") is True

    def test_boolean_true_from_string_true(self):
        assert _coerce_value("true", "boolean") is True

    def test_boolean_true_from_string_1(self):
        assert _coerce_value("1", "boolean") is True

    def test_boolean_true_from_string_yes(self):
        assert _coerce_value("yes", "boolean") is True

    def test_boolean_false_from_bool(self):
        assert _coerce_value(False, "boolean") is False

    def test_boolean_false_from_string_false(self):
        assert _coerce_value("false", "boolean") is False

    def test_boolean_false_from_string_0(self):
        assert _coerce_value("0", "boolean") is False

    def test_time_hhmm_to_minutes(self):
        assert _coerce_value("09:30", "time") == 9 * 60 + 30

    def test_time_hhmmss_to_minutes_uses_hh_mm(self):
        assert _coerce_value("17:45:00", "time") == 17 * 60 + 45

    def test_time_numeric_passthrough(self):
        assert _coerce_value(480, "time") == 480

    def test_date_iso_string_parsed(self):
        from datetime import datetime
        result = _coerce_value("2025-01-15", "date")
        assert isinstance(result, datetime)

    def test_date_iso_with_z_suffix_parsed(self):
        from datetime import datetime
        result = _coerce_value("2025-06-01T00:00:00Z", "date")
        assert isinstance(result, datetime)

    def test_list_from_list(self):
        assert _coerce_value([1, 2, 3], "list") == [1, 2, 3]

    def test_list_from_csv_string(self):
        assert _coerce_value("a, b, c", "list") == ["a", "b", "c"]

    def test_list_from_scalar_wraps_in_list(self):
        assert _coerce_value(42, "list") == [42]


# ===========================================================================
# 2. _evaluate_condition — all 16 operators
# ===========================================================================

class TestEvaluateConditionEquals:

    def test_equals_match(self):
        c = make_condition("status", "equals", "active")
        assert _evaluate_condition(c, {"status": "active"}) is True

    def test_equals_no_match(self):
        c = make_condition("status", "equals", "active")
        assert _evaluate_condition(c, {"status": "inactive"}) is False

    def test_not_equals_match(self):
        c = make_condition("status", "not_equals", "deleted")
        assert _evaluate_condition(c, {"status": "active"}) is True

    def test_not_equals_no_match(self):
        c = make_condition("status", "not_equals", "active")
        assert _evaluate_condition(c, {"status": "active"}) is False


class TestEvaluateConditionNumeric:

    def test_greater_than_match(self):
        c = make_condition("hours", "greater_than", "8", value_type="number")
        assert _evaluate_condition(c, {"hours": 10}) is True

    def test_greater_than_no_match(self):
        c = make_condition("hours", "greater_than", "8", value_type="number")
        assert _evaluate_condition(c, {"hours": 5}) is False

    def test_greater_than_equal_value_is_false(self):
        c = make_condition("hours", "greater_than", "8", value_type="number")
        assert _evaluate_condition(c, {"hours": 8}) is False

    def test_less_than_match(self):
        c = make_condition("score", "less_than", "50", value_type="number")
        assert _evaluate_condition(c, {"score": 30}) is True

    def test_less_than_no_match(self):
        c = make_condition("score", "less_than", "50", value_type="number")
        assert _evaluate_condition(c, {"score": 70}) is False

    def test_less_than_equal_value_is_false(self):
        c = make_condition("score", "less_than", "50", value_type="number")
        assert _evaluate_condition(c, {"score": 50}) is False

    def test_between_within_range(self):
        c = make_condition("age", "between", [18, 65], value_type="number")
        assert _evaluate_condition(c, {"age": 30}) is True

    def test_between_at_lower_bound(self):
        c = make_condition("age", "between", [18, 65], value_type="number")
        assert _evaluate_condition(c, {"age": 18}) is True

    def test_between_at_upper_bound(self):
        c = make_condition("age", "between", [18, 65], value_type="number")
        assert _evaluate_condition(c, {"age": 65}) is True

    def test_between_below_range(self):
        c = make_condition("age", "between", [18, 65], value_type="number")
        assert _evaluate_condition(c, {"age": 10}) is False

    def test_between_above_range(self):
        c = make_condition("age", "between", [18, 65], value_type="number")
        assert _evaluate_condition(c, {"age": 70}) is False

    def test_between_invalid_expected_not_a_list_returns_false(self):
        c = make_condition("age", "between", "not-a-list", value_type="number")
        assert _evaluate_condition(c, {"age": 30}) is False


class TestEvaluateConditionString:

    def test_contains_match(self):
        c = make_condition("notes", "contains", "urgent")
        assert _evaluate_condition(c, {"notes": "This is urgent please review"}) is True

    def test_contains_case_insensitive(self):
        c = make_condition("notes", "contains", "URGENT")
        assert _evaluate_condition(c, {"notes": "urgent"}) is True

    def test_contains_no_match(self):
        c = make_condition("notes", "contains", "urgent")
        assert _evaluate_condition(c, {"notes": "routine check"}) is False

    def test_contains_non_string_actual_returns_false(self):
        # actual is a number — contains requires both sides to be strings
        c = make_condition("count", "contains", "5")
        assert _evaluate_condition(c, {"count": 5}) is False

    def test_starts_with_match(self):
        c = make_condition("code", "starts_with", "REF")
        assert _evaluate_condition(c, {"code": "REF-001"}) is True

    def test_starts_with_case_insensitive(self):
        c = make_condition("code", "starts_with", "ref")
        assert _evaluate_condition(c, {"code": "REF-001"}) is True

    def test_starts_with_no_match(self):
        c = make_condition("code", "starts_with", "REF")
        assert _evaluate_condition(c, {"code": "INV-001"}) is False


class TestEvaluateConditionDate:

    def test_before_match(self):
        c = make_condition("entry_date", "before", "2025-06-01", value_type="date")
        assert _evaluate_condition(c, {"entry_date": "2025-01-01"}) is True

    def test_before_no_match(self):
        c = make_condition("entry_date", "before", "2025-01-01", value_type="date")
        assert _evaluate_condition(c, {"entry_date": "2025-06-01"}) is False

    def test_after_match(self):
        c = make_condition("entry_date", "after", "2025-01-01", value_type="date")
        assert _evaluate_condition(c, {"entry_date": "2025-06-01"}) is True

    def test_after_no_match(self):
        c = make_condition("entry_date", "after", "2025-06-01", value_type="date")
        assert _evaluate_condition(c, {"entry_date": "2025-01-01"}) is False

    def test_before_with_none_actual_returns_false(self):
        c = make_condition("entry_date", "before", "2025-06-01", value_type="date")
        assert _evaluate_condition(c, {"entry_date": None}) is False


class TestEvaluateConditionBoolean:

    def test_is_true_with_true(self):
        c = make_condition("is_locked", "is_true")
        assert _evaluate_condition(c, {"is_locked": True}) is True

    def test_is_true_with_false(self):
        c = make_condition("is_locked", "is_true")
        assert _evaluate_condition(c, {"is_locked": False}) is False

    def test_is_true_with_truthy_int(self):
        c = make_condition("count", "is_true")
        assert _evaluate_condition(c, {"count": 1}) is True

    def test_is_true_with_zero(self):
        c = make_condition("count", "is_true")
        assert _evaluate_condition(c, {"count": 0}) is False

    def test_is_false_with_false(self):
        c = make_condition("is_locked", "is_false")
        assert _evaluate_condition(c, {"is_locked": False}) is True

    def test_is_false_with_true(self):
        c = make_condition("is_locked", "is_false")
        assert _evaluate_condition(c, {"is_locked": True}) is False

    def test_is_false_with_zero(self):
        c = make_condition("count", "is_false")
        assert _evaluate_condition(c, {"count": 0}) is True


class TestEvaluateConditionMembership:

    def test_in_match(self):
        c = make_condition("status", "in", ["active", "pending"])
        assert _evaluate_condition(c, {"status": "active"}) is True

    def test_in_no_match(self):
        c = make_condition("status", "in", ["active", "pending"])
        assert _evaluate_condition(c, {"status": "deleted"}) is False

    def test_in_with_non_list_expected_returns_false(self):
        # expected must be a list; a bare string is not
        c = make_condition("status", "in", "active")
        assert _evaluate_condition(c, {"status": "active"}) is False

    def test_not_in_match(self):
        c = make_condition("role", "not_in", ["guest", "blocked"])
        assert _evaluate_condition(c, {"role": "admin"}) is True

    def test_not_in_no_match(self):
        c = make_condition("role", "not_in", ["guest", "blocked"])
        assert _evaluate_condition(c, {"role": "guest"}) is False

    def test_not_in_with_non_list_expected_returns_false(self):
        c = make_condition("role", "not_in", "admin")
        assert _evaluate_condition(c, {"role": "guest"}) is False


class TestEvaluateConditionEmptiness:

    def test_is_empty_with_none(self):
        c = make_condition("notes", "is_empty")
        assert _evaluate_condition(c, {"notes": None}) is True

    def test_is_empty_with_empty_string(self):
        c = make_condition("notes", "is_empty")
        assert _evaluate_condition(c, {"notes": ""}) is True

    def test_is_empty_with_empty_list(self):
        c = make_condition("tags", "is_empty")
        assert _evaluate_condition(c, {"tags": []}) is True

    def test_is_empty_with_missing_field(self):
        # payload.get() returns None for missing keys
        c = make_condition("notes", "is_empty")
        assert _evaluate_condition(c, {}) is True

    def test_is_empty_with_value(self):
        c = make_condition("notes", "is_empty")
        assert _evaluate_condition(c, {"notes": "has content"}) is False

    def test_is_not_empty_with_value(self):
        c = make_condition("notes", "is_not_empty")
        assert _evaluate_condition(c, {"notes": "content"}) is True

    def test_is_not_empty_with_none(self):
        c = make_condition("notes", "is_not_empty")
        assert _evaluate_condition(c, {"notes": None}) is False

    def test_is_not_empty_with_empty_string(self):
        c = make_condition("notes", "is_not_empty")
        assert _evaluate_condition(c, {"notes": ""}) is False

    def test_is_not_empty_with_non_empty_list(self):
        c = make_condition("tags", "is_not_empty")
        assert _evaluate_condition(c, {"tags": ["a", "b"]}) is True


class TestEvaluateConditionEdgeCases:

    def test_unknown_operator_returns_false(self):
        c = make_condition("field", "nonexistent_operator", "value")
        assert _evaluate_condition(c, {"field": "value"}) is False

    def test_none_actual_with_equals(self):
        c = make_condition("field", "equals", "something")
        assert _evaluate_condition(c, {"field": None}) is False

    def test_none_actual_with_greater_than(self):
        c = make_condition("hours", "greater_than", "5", value_type="number")
        assert _evaluate_condition(c, {"hours": None}) is False

    def test_missing_field_defaults_to_none(self):
        c = make_condition("status", "equals", "active")
        assert _evaluate_condition(c, {}) is False

    def test_string_number_coerced_for_numeric_comparison(self):
        # payload has string "10"; value_type=number should coerce both sides
        c = make_condition("hours", "greater_than", "8", value_type="number")
        assert _evaluate_condition(c, {"hours": "10"}) is True

    def test_type_coercion_string_5_equals_number_5(self):
        c = make_condition("count", "equals", "5", value_type="number")
        assert _evaluate_condition(c, {"count": "5"}) is True


# ===========================================================================
# 3. _evaluate_conditions — AND / OR / CUSTOM logic
# ===========================================================================

class TestEvaluateConditionsLogic:

    def test_no_conditions_always_matches(self):
        policy = make_policy(conditions=[])
        assert _evaluate_conditions(policy, {"any": "payload"}) is True

    def test_and_all_true_returns_true(self):
        policy = make_policy(
            conditions=[
                make_condition("status", "equals", "active"),
                make_condition("hours", "greater_than", "0", value_type="number"),
            ],
            condition_logic="AND",
        )
        assert _evaluate_conditions(policy, {"status": "active", "hours": 5}) is True

    def test_and_one_false_returns_false(self):
        policy = make_policy(
            conditions=[
                make_condition("status", "equals", "active"),
                make_condition("hours", "greater_than", "10", value_type="number"),
            ],
            condition_logic="AND",
        )
        assert _evaluate_conditions(policy, {"status": "active", "hours": 5}) is False

    def test_and_all_false_returns_false(self):
        policy = make_policy(
            conditions=[
                make_condition("status", "equals", "active"),
                make_condition("hours", "greater_than", "10", value_type="number"),
            ],
            condition_logic="AND",
        )
        assert _evaluate_conditions(policy, {"status": "inactive", "hours": 5}) is False

    def test_or_one_true_returns_true(self):
        policy = make_policy(
            conditions=[
                make_condition("status", "equals", "active"),
                make_condition("status", "equals", "pending"),
            ],
            condition_logic="OR",
        )
        assert _evaluate_conditions(policy, {"status": "pending"}) is True

    def test_or_all_true_returns_true(self):
        policy = make_policy(
            conditions=[
                make_condition("flag_a", "is_true"),
                make_condition("flag_b", "is_true"),
            ],
            condition_logic="OR",
        )
        assert _evaluate_conditions(policy, {"flag_a": True, "flag_b": True}) is True

    def test_or_all_false_returns_false(self):
        policy = make_policy(
            conditions=[
                make_condition("status", "equals", "active"),
                make_condition("status", "equals", "pending"),
            ],
            condition_logic="OR",
        )
        assert _evaluate_conditions(policy, {"status": "deleted"}) is False

    def test_unknown_logic_returns_false(self):
        policy = make_policy(
            conditions=[make_condition("status", "equals", "active")],
            condition_logic="UNKNOWN",
        )
        assert _evaluate_conditions(policy, {"status": "active"}) is False

    def test_custom_logic_delegates_to_pattern_evaluator(self):
        policy = make_policy(
            conditions=[
                make_condition("a", "is_not_empty"),
                make_condition("b", "is_not_empty"),
            ],
            condition_logic="CUSTOM",
            condition_pattern="1 OR 2",
        )
        # Only condition 2 is met
        assert _evaluate_conditions(policy, {"b": "value"}) is True

    def test_custom_logic_without_pattern_returns_true(self):
        policy = make_policy(
            conditions=[make_condition("a", "is_not_empty")],
            condition_logic="CUSTOM",
            # no condition_pattern key → defaults to ""
        )
        # Empty pattern → True
        assert _evaluate_conditions(policy, {}) is True


# ===========================================================================
# 4. _evaluate_custom_pattern
# ===========================================================================

class TestEvaluateCustomPattern:

    def _conditions_and_payload(self, *results):
        """
        Build a conditions list and matching payload where result[i] controls
        whether condition i+1 evaluates to True.  Uses is_not_empty on sentinel keys.
        """
        conditions = []
        payload = {}
        for i, val in enumerate(results, 1):
            key = f"__field_{i}"
            conditions.append(make_condition(key, "is_not_empty"))
            if val:
                payload[key] = "present"
        return conditions, payload

    def test_and_both_true(self):
        conds, payload = self._conditions_and_payload(True, True)
        assert _evaluate_custom_pattern("1 AND 2", conds, payload) is True

    def test_and_one_false(self):
        conds, payload = self._conditions_and_payload(True, False)
        assert _evaluate_custom_pattern("1 AND 2", conds, payload) is False

    def test_or_one_true(self):
        conds, payload = self._conditions_and_payload(False, True)
        assert _evaluate_custom_pattern("1 OR 2", conds, payload) is True

    def test_or_all_false(self):
        conds, payload = self._conditions_and_payload(False, False)
        assert _evaluate_custom_pattern("1 OR 2", conds, payload) is False

    def test_parenthesised_and_or(self):
        # (1 AND 2) OR 3  with  1=True, 2=False, 3=True  →  True
        conds, payload = self._conditions_and_payload(True, False, True)
        assert _evaluate_custom_pattern("(1 AND 2) OR 3", conds, payload) is True

    def test_parenthesised_and_or_all_false(self):
        # (1 AND 2) OR 3  with  1=True, 2=False, 3=False  →  False
        conds, payload = self._conditions_and_payload(True, False, False)
        assert _evaluate_custom_pattern("(1 AND 2) OR 3", conds, payload) is False

    def test_not_negates_true(self):
        conds, payload = self._conditions_and_payload(True)
        assert _evaluate_custom_pattern("NOT 1", conds, payload) is False

    def test_not_negates_false(self):
        conds, payload = self._conditions_and_payload(False)
        assert _evaluate_custom_pattern("NOT 1", conds, payload) is True

    def test_empty_string_pattern_returns_true(self):
        assert _evaluate_custom_pattern("", [], {}) is True

    def test_whitespace_only_pattern_returns_true(self):
        assert _evaluate_custom_pattern("   ", [], {}) is True

    def test_injection_attempt_returns_false(self):
        assert _evaluate_custom_pattern("1 AND __import__('os')", [], {}) is False

    def test_case_insensitive_and(self):
        conds, payload = self._conditions_and_payload(True, True)
        assert _evaluate_custom_pattern("1 and 2", conds, payload) is True

    def test_case_insensitive_or(self):
        conds, payload = self._conditions_and_payload(False, True)
        assert _evaluate_custom_pattern("1 or 2", conds, payload) is True

    def test_single_condition_index(self):
        conds, payload = self._conditions_and_payload(True)
        assert _evaluate_custom_pattern("1", conds, payload) is True

    def test_double_digit_index_not_mangled(self):
        # Index 10 must not be replaced as "1" + "0"
        conds, payload = self._conditions_and_payload(*([True] * 10))
        assert _evaluate_custom_pattern("10", conds, payload) is True


# ===========================================================================
# 5. evaluate_policies — action execution
# ===========================================================================

class TestEvaluatePoliciesActions:

    def setup_method(self):
        _clear_cache()

    def teardown_method(self):
        _clear_cache()

    def test_block_action_sets_blocked_and_message(self):
        policy = make_policy(conditions=[], actions=[make_block_action("You cannot do this")])
        db = _make_db_with_policies([policy])

        result = run(evaluate_policies(db, "time_tracking", "time_entry", "on_create", {}, {}))

        assert result["blocked"] is True
        assert result["allowed"] is False
        assert "You cannot do this" in result["block_messages"]

    def test_warn_action_adds_to_warnings(self):
        policy = make_policy(conditions=[], actions=[make_warn_action("Please review")])
        db = _make_db_with_policies([policy])

        result = run(evaluate_policies(db, "time_tracking", "time_entry", "on_create", {}, {}))

        assert result["blocked"] is False
        assert result["allowed"] is True
        assert "Please review" in result["warnings"]

    def test_require_field_with_none_value_blocks(self):
        policy = make_policy(
            conditions=[],
            actions=[make_require_field_action("project_id", "Project is required")],
        )
        db = _make_db_with_policies([policy])

        result = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_create",
            {"project_id": None}, {}
        ))

        assert result["blocked"] is True
        assert result["allowed"] is False
        assert any(m["field"] == "project_id" for m in result["required_fields_missing"])

    def test_require_field_with_empty_string_blocks(self):
        policy = make_policy(
            conditions=[],
            actions=[make_require_field_action("notes", "Notes required")],
        )
        db = _make_db_with_policies([policy])

        result = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_create",
            {"notes": ""}, {}
        ))

        assert result["blocked"] is True

    def test_require_field_with_empty_list_blocks(self):
        policy = make_policy(
            conditions=[],
            actions=[make_require_field_action("tags", "Tags required")],
        )
        db = _make_db_with_policies([policy])

        result = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_create",
            {"tags": []}, {}
        ))

        assert result["blocked"] is True

    def test_require_field_present_does_not_block(self):
        policy = make_policy(
            conditions=[],
            actions=[make_require_field_action("project_id", "Project is required")],
        )
        db = _make_db_with_policies([policy])

        result = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_create",
            {"project_id": "proj-123"}, {}
        ))

        assert result["blocked"] is False
        assert result["required_fields_missing"] == []

    def test_multiple_actions_all_executed(self):
        policy = make_policy(
            conditions=[],
            actions=[make_block_action("Hard block"), make_warn_action("Also a warning")],
        )
        db = _make_db_with_policies([policy])

        result = run(evaluate_policies(db, "time_tracking", "time_entry", "on_create", {}, {}))

        assert result["blocked"] is True
        assert "Hard block" in result["block_messages"]
        assert "Also a warning" in result["warnings"]

    def test_conditions_not_met_skips_all_actions(self):
        policy = make_policy(
            conditions=[make_condition("status", "equals", "locked")],
            actions=[make_block_action("Locked!")],
        )
        db = _make_db_with_policies([policy])

        result = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_create",
            {"status": "open"}, {}
        ))

        assert result["blocked"] is False
        assert result["block_messages"] == []

    def test_default_message_when_action_has_no_message_key(self):
        policy = make_policy(name="My Policy", conditions=[], actions=[{"type": "block"}])
        db = _make_db_with_policies([policy])

        result = run(evaluate_policies(db, "time_tracking", "time_entry", "on_create", {}, {}))

        assert result["blocked"] is True
        assert any("My Policy" in msg for msg in result["block_messages"])

    def test_require_field_missing_from_payload_entirely_blocks(self):
        policy = make_policy(
            conditions=[],
            actions=[make_require_field_action("manager_id", "Manager required")],
        )
        db = _make_db_with_policies([policy])

        # manager_id not in payload at all
        result = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_create",
            {}, {}
        ))

        assert result["blocked"] is True


# ===========================================================================
# 6. Policy filtering (module / entity / event matching)
# ===========================================================================

class TestPolicyFiltering:

    def setup_method(self):
        _clear_cache()

    def teardown_method(self):
        _clear_cache()

    def test_only_matching_module_policy_fires(self):
        matching = make_policy(
            module="time_tracking", entity="time_entry", event="on_create",
            conditions=[], actions=[make_block_action("match")]
        )
        other = make_policy(
            module="crm", entity="contact", event="on_create",
            conditions=[], actions=[make_block_action("wrong module")]
        )
        db = _make_db_with_policies([matching, other])

        result = run(evaluate_policies(db, "time_tracking", "time_entry", "on_create", {}, {}))

        assert result["blocked"] is True
        assert result["block_messages"] == ["match"]

    def test_only_matching_entity_policy_fires(self):
        matching = make_policy(
            module="crm", entity="contact", event="on_create",
            conditions=[], actions=[make_block_action("contact block")]
        )
        other = make_policy(
            module="crm", entity="deal", event="on_create",
            conditions=[], actions=[make_block_action("deal block")]
        )
        db = _make_db_with_policies([matching, other])

        result = run(evaluate_policies(db, "crm", "contact", "on_create", {}, {}))

        assert result["block_messages"] == ["contact block"]

    def test_only_matching_event_policy_fires(self):
        matching = make_policy(
            module="crm", entity="contact", event="on_submit",
            conditions=[], actions=[make_block_action("on_submit block")]
        )
        other = make_policy(
            module="crm", entity="contact", event="on_create",
            conditions=[], actions=[make_block_action("on_create block")]
        )
        db = _make_db_with_policies([matching, other])

        result = run(evaluate_policies(db, "crm", "contact", "on_submit", {}, {}))

        assert result["block_messages"] == ["on_submit block"]

    def test_no_matching_policies_returns_allowed_result(self):
        policy = make_policy(
            module="time_tracking", entity="time_entry", event="on_create",
            conditions=[], actions=[make_block_action()]
        )
        db = _make_db_with_policies([policy])

        result = run(evaluate_policies(db, "crm", "contact", "on_create", {}, {}))

        assert result["allowed"] is True
        assert result["blocked"] is False
        assert result["block_messages"] == []

    def test_db_failure_fails_open(self):
        """If DB raises an exception, result must be allowed=True (fail-open)."""
        db = MagicMock()
        db.policies.find.side_effect = Exception("DB unavailable")
        _clear_cache()

        result = run(evaluate_policies(db, "time_tracking", "time_entry", "on_create", {}, {}))

        assert result["allowed"] is True
        assert result["blocked"] is False

    def test_broken_policy_is_skipped_not_raised(self):
        """A policy whose condition evaluation throws must be skipped silently."""
        bad_policy = make_policy(
            conditions=[make_condition("x", "equals", "y")],
            actions=[make_block_action("should not appear")]
        )
        with patch(
            "services.policy_engine._evaluate_conditions",
            side_effect=Exception("Simulated eval error"),
        ):
            db = _make_db_with_policies([bad_policy])
            result = run(evaluate_policies(
                db, "time_tracking", "time_entry", "on_create", {"x": "y"}, {}
            ))

        assert result["blocked"] is False


# ===========================================================================
# 7. Cache behaviour
# ===========================================================================

class TestCacheBehaviour:

    def setup_method(self):
        _clear_cache()

    def teardown_method(self):
        _clear_cache()

    def test_policies_cached_after_first_call(self):
        policy = make_policy(conditions=[], actions=[])
        db = _make_db_with_policies([policy])

        run(evaluate_policies(db, "time_tracking", "time_entry", "on_create", {}, {}))

        assert _policy_cache["data"] is not None
        assert _policy_cache["timestamp"] > 0

    def test_second_call_uses_cache_not_db(self):
        policy = make_policy(conditions=[], actions=[])
        db = _make_db_with_policies([policy])

        run(evaluate_policies(db, "time_tracking", "time_entry", "on_create", {}, {}))
        call_count_after_first = db.policies.find.call_count

        run(evaluate_policies(db, "time_tracking", "time_entry", "on_create", {}, {}))

        assert db.policies.find.call_count == call_count_after_first, (
            "DB was queried on second call despite valid cache"
        )

    def test_cache_refreshed_after_ttl_expires(self):
        policy = make_policy(conditions=[], actions=[])
        db = _make_db_with_policies([policy])

        # Seed a stale cache entry
        _policy_cache["data"] = []
        _policy_cache["timestamp"] = time.time() - CACHE_TTL - 1

        run(evaluate_policies(db, "time_tracking", "time_entry", "on_create", {}, {}))

        assert db.policies.find.call_count == 1

    def test_invalidate_clears_data_and_timestamp(self):
        _policy_cache["data"] = [{"some": "policy"}]
        _policy_cache["timestamp"] = time.time()

        invalidate_policy_cache()

        assert _policy_cache["data"] is None
        assert _policy_cache["timestamp"] == 0

    def test_invalidated_cache_forces_db_query_on_next_call(self):
        policy = make_policy(conditions=[], actions=[])
        db = _make_db_with_policies([policy])

        run(evaluate_policies(db, "time_tracking", "time_entry", "on_create", {}, {}))
        assert db.policies.find.call_count == 1

        invalidate_policy_cache()

        run(evaluate_policies(db, "time_tracking", "time_entry", "on_create", {}, {}))
        assert db.policies.find.call_count == 2

    def test_cache_within_ttl_is_not_refreshed(self):
        policy = make_policy(conditions=[], actions=[])
        db = _make_db_with_policies([policy])

        # Seed a fresh cache entry with only 1 second elapsed
        _policy_cache["data"] = []
        _policy_cache["timestamp"] = time.time() - 1  # well within 60s TTL

        run(evaluate_policies(db, "time_tracking", "time_entry", "on_create", {}, {}))

        assert db.policies.find.call_count == 0


# ===========================================================================
# 8. evaluate_policies result shape
# ===========================================================================

class TestEvaluatePoliciesResultShape:

    def setup_method(self):
        _clear_cache()

    def teardown_method(self):
        _clear_cache()

    def test_result_always_has_all_expected_keys(self):
        db = _make_db_with_policies([])
        result = run(evaluate_policies(db, "time_tracking", "time_entry", "on_create", {}, {}))

        for key in ("allowed", "blocked", "block_messages", "warnings", "required_fields_missing"):
            assert key in result, f"Missing key: {key}"

    def test_multiple_blocking_policies_all_messages_collected(self):
        policies = [
            make_policy(
                name=f"Policy {i}",
                conditions=[],
                actions=[make_block_action(f"Block reason {i}")],
            )
            for i in range(3)
        ]
        db = _make_db_with_policies(policies)

        result = run(evaluate_policies(db, "time_tracking", "time_entry", "on_create", {}, {}))

        assert result["blocked"] is True
        assert len(result["block_messages"]) == 3
        for i in range(3):
            assert f"Block reason {i}" in result["block_messages"]

    def test_multiple_required_fields_missing_all_collected(self):
        policy = make_policy(
            conditions=[],
            actions=[
                make_require_field_action("field_a", "Field A required"),
                make_require_field_action("field_b", "Field B required"),
            ],
        )
        db = _make_db_with_policies([policy])

        result = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_create",
            {"field_a": None, "field_b": None}, {}
        ))

        fields = [m["field"] for m in result["required_fields_missing"]]
        assert "field_a" in fields
        assert "field_b" in fields

    def test_empty_payload_with_no_policies_returns_clean_allowed(self):
        db = _make_db_with_policies([])
        result = run(evaluate_policies(db, "time_tracking", "time_entry", "on_create", {}, {}))

        assert result["allowed"] is True
        assert result["blocked"] is False
        assert result["block_messages"] == []
        assert result["warnings"] == []
        assert result["required_fields_missing"] == []


# ===========================================================================
# 9. enforce_policy_result
# ===========================================================================

class TestEnforcePolicyResult:

    def _allowed_result(self, warnings=None):
        return {
            "allowed": True,
            "blocked": False,
            "block_messages": [],
            "warnings": warnings or [],
            "required_fields_missing": [],
        }

    def test_allowed_with_no_warnings_returns_empty_list(self):
        result = self._allowed_result()
        assert enforce_policy_result(result) == []

    def test_allowed_with_warnings_returns_them(self):
        result = self._allowed_result(warnings=["Check the rate", "Verify the date"])
        warnings = enforce_policy_result(result)
        assert warnings == ["Check the rate", "Verify the date"]

    def test_single_block_message_raises_400_with_string_detail(self):
        result = self._allowed_result()
        result["blocked"] = True
        result["allowed"] = False
        result["block_messages"] = ["You cannot submit a locked entry"]

        with pytest.raises(HTTPException) as exc_info:
            enforce_policy_result(result)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "You cannot submit a locked entry"

    def test_multiple_block_messages_raises_400_with_dict_detail(self):
        result = self._allowed_result()
        result["blocked"] = True
        result["allowed"] = False
        result["block_messages"] = ["Reason A", "Reason B"]

        with pytest.raises(HTTPException) as exc_info:
            enforce_policy_result(result)

        assert exc_info.value.status_code == 400
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        assert "policy_violations" in detail
        assert "Reason A" in detail["policy_violations"]
        assert "Reason B" in detail["policy_violations"]

    def test_multiple_block_messages_detail_has_message_key(self):
        result = self._allowed_result()
        result["blocked"] = True
        result["allowed"] = False
        result["block_messages"] = ["X", "Y"]

        with pytest.raises(HTTPException) as exc_info:
            enforce_policy_result(result)

        assert "message" in exc_info.value.detail

    def test_single_required_field_raises_400_with_field_message(self):
        result = self._allowed_result()
        result["required_fields_missing"] = [
            {"field": "project_id", "message": "Project is required"}
        ]

        with pytest.raises(HTTPException) as exc_info:
            enforce_policy_result(result)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Project is required"

    def test_multiple_required_fields_raises_400_with_joined_field_names(self):
        result = self._allowed_result()
        result["required_fields_missing"] = [
            {"field": "project_id", "message": "Project required"},
            {"field": "notes", "message": "Notes required"},
        ]

        with pytest.raises(HTTPException) as exc_info:
            enforce_policy_result(result)

        assert exc_info.value.status_code == 400
        detail = exc_info.value.detail
        assert "project_id" in detail
        assert "notes" in detail

    def test_required_fields_without_message_still_raises_with_field_names(self):
        result = self._allowed_result()
        result["required_fields_missing"] = [
            {"field": "alpha"},
            {"field": "beta"},
        ]

        with pytest.raises(HTTPException) as exc_info:
            enforce_policy_result(result)

        assert exc_info.value.status_code == 400
        detail = exc_info.value.detail
        assert "alpha" in detail
        assert "beta" in detail

    def test_blocked_takes_priority_over_required_fields(self):
        """When both blocked and required_fields_missing are set, block fires first."""
        result = self._allowed_result()
        result["blocked"] = True
        result["allowed"] = False
        result["block_messages"] = ["Hard block"]
        result["required_fields_missing"] = [{"field": "x", "message": "x required"}]

        with pytest.raises(HTTPException) as exc_info:
            enforce_policy_result(result)

        assert exc_info.value.detail == "Hard block"

    def test_not_blocked_not_missing_does_not_raise(self):
        result = self._allowed_result(warnings=["Soft warning"])
        returned = enforce_policy_result(result)
        assert returned == ["Soft warning"]


# ===========================================================================
# 10. End-to-end / realistic scenarios
# ===========================================================================

class TestEndToEndScenarios:

    def setup_method(self):
        _clear_cache()

    def teardown_method(self):
        _clear_cache()

    def test_overtime_block_policy(self):
        """Time entries over 12 hours are blocked; under 12 are allowed."""
        policy = make_policy(
            name="Overtime Block",
            conditions=[make_condition("hours", "greater_than", "12", value_type="number")],
            actions=[make_block_action("Time entries cannot exceed 12 hours")],
        )
        db = _make_db_with_policies([policy])
        blocked_result = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_create", {"hours": 13}, {}
        ))
        assert blocked_result["blocked"] is True

        _clear_cache()
        db2 = _make_db_with_policies([policy])
        allowed_result = run(evaluate_policies(
            db2, "time_tracking", "time_entry", "on_create", {"hours": 8}, {}
        ))
        assert allowed_result["blocked"] is False

    def test_locked_entry_warn_policy(self):
        """Editing a locked time entry adds a warning, does not block."""
        policy = make_policy(
            name="Locked Entry Warning",
            conditions=[make_condition("is_locked", "is_true")],
            actions=[make_warn_action("This entry is locked — changes may be audited")],
        )
        db = _make_db_with_policies([policy])

        result = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_update",
            {"is_locked": True, "hours": 8}, {}
        ))
        assert "This entry is locked — changes may be audited" in result["warnings"]
        assert result["blocked"] is False

    def test_and_policy_only_fires_when_all_conditions_met(self):
        """Block high-value submitted invoices (status=submitted AND amount > 1000)."""
        policy = make_policy(
            name="High Value Submit",
            conditions=[
                make_condition("status", "equals", "submitted"),
                make_condition("amount", "greater_than", "1000", value_type="number"),
            ],
            condition_logic="AND",
            actions=[make_block_action("High-value submissions require approval")],
        )
        db = _make_db_with_policies([policy])

        blocked = run(evaluate_policies(
            db, "crm", "invoice", "on_submit",
            {"status": "submitted", "amount": 2000}, {}
        ))
        assert blocked["blocked"] is True

        _clear_cache()
        db2 = _make_db_with_policies([policy])
        allowed = run(evaluate_policies(
            db2, "crm", "invoice", "on_submit",
            {"status": "submitted", "amount": 500}, {}
        ))
        assert allowed["blocked"] is False

    def test_custom_pattern_complex_scenario(self):
        """(status=locked OR is_readonly=true) AND hours > 10 → block."""
        policy = make_policy(
            name="Complex Guard",
            conditions=[
                make_condition("status", "equals", "locked"),
                make_condition("is_readonly", "is_true"),
                make_condition("hours", "greater_than", "10", value_type="number"),
            ],
            condition_logic="CUSTOM",
            condition_pattern="(1 OR 2) AND 3",
            actions=[make_block_action("Cannot modify locked/readonly entries with high hours")],
        )
        db = _make_db_with_policies([policy])

        # status=locked, hours=15 → (True OR False) AND True → blocked
        blocked = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_update",
            {"status": "locked", "is_readonly": False, "hours": 15}, {}
        ))
        assert blocked["blocked"] is True

        _clear_cache()
        db2 = _make_db_with_policies([policy])

        # status=open, is_readonly=False, hours=15 → (False OR False) AND True → not blocked
        allowed = run(evaluate_policies(
            db2, "time_tracking", "time_entry", "on_update",
            {"status": "open", "is_readonly": False, "hours": 15}, {}
        ))
        assert allowed["blocked"] is False

    def test_or_policy_fires_for_either_condition(self):
        """Warn when status is 'draft' OR 'review'."""
        policy = make_policy(
            name="Draft/Review Warning",
            conditions=[
                make_condition("status", "equals", "draft"),
                make_condition("status", "equals", "review"),
            ],
            condition_logic="OR",
            actions=[make_warn_action("Entry is in an interim state")],
        )
        db = _make_db_with_policies([policy])

        for status in ("draft", "review"):
            _clear_cache()
            db_n = _make_db_with_policies([policy])
            result = run(evaluate_policies(
                db_n, "time_tracking", "time_entry", "on_submit",
                {"status": status}, {}
            ))
            assert "Entry is in an interim state" in result["warnings"], (
                f"Warning expected for status={status}"
            )

    def test_multiple_policies_independent_results_merged(self):
        """Two policies both firing: one blocks, one warns — both contributions present."""
        blocker = make_policy(
            name="Blocker",
            conditions=[],
            actions=[make_block_action("Hard stop")],
        )
        warner = make_policy(
            name="Warner",
            module="time_tracking",
            entity="time_entry",
            event="on_create",
            conditions=[],
            actions=[make_warn_action("Heads up")],
        )
        db = _make_db_with_policies([blocker, warner])

        result = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_create", {}, {}
        ))

        assert result["blocked"] is True
        assert "Hard stop" in result["block_messages"]
        assert "Heads up" in result["warnings"]


# ===========================================================================
# 18. Role-based policy targeting
# ===========================================================================

class TestRoleBasedTargeting:
    """Tests for applies_to_roles filtering in evaluate_policies."""

    def setup_method(self):
        _clear_cache()

    def test_no_applies_to_roles_evaluates_for_all(self):
        """Policy without applies_to_roles evaluates for any user_role."""
        policy = make_policy(
            conditions=[],
            actions=[make_block_action("Blocked")],
        )
        db = _make_db_with_policies([policy])
        result = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_create", {},
            user_role="agent",
        ))
        assert result["blocked"] is True

    def test_empty_applies_to_roles_evaluates_for_all(self):
        """Policy with applies_to_roles=[] evaluates for any user_role."""
        policy = make_policy(
            conditions=[],
            actions=[make_block_action("Blocked")],
            applies_to_roles=[],
        )
        db = _make_db_with_policies([policy])
        result = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_create", {},
            user_role="admin",
        ))
        assert result["blocked"] is True

    def test_matching_role_evaluates_policy(self):
        """Policy with applies_to_roles=["agent"] evaluates for agent."""
        policy = make_policy(
            conditions=[],
            actions=[make_block_action("Agents blocked")],
            applies_to_roles=["agent"],
        )
        db = _make_db_with_policies([policy])
        result = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_create", {},
            user_role="agent",
        ))
        assert result["blocked"] is True
        assert "Agents blocked" in result["block_messages"]

    def test_non_matching_role_skips_policy(self):
        """Policy with applies_to_roles=["agent"] is skipped for admin."""
        policy = make_policy(
            conditions=[],
            actions=[make_block_action("Agents only")],
            applies_to_roles=["agent"],
        )
        db = _make_db_with_policies([policy])
        result = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_create", {},
            user_role="admin",
        ))
        assert result["blocked"] is False
        assert result["allowed"] is True

    def test_no_user_role_skips_role_filtering(self):
        """When user_role is None, role filtering is skipped entirely."""
        policy = make_policy(
            conditions=[],
            actions=[make_block_action("Targeted")],
            applies_to_roles=["agent"],
        )
        db = _make_db_with_policies([policy])
        # user_role not passed — all policies evaluate (backward compat)
        result = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_create", {},
        ))
        assert result["blocked"] is True

    def test_multiple_roles_in_applies_to(self):
        """Policy targeting multiple roles matches any of them."""
        policy = make_policy(
            conditions=[],
            actions=[make_block_action("Restricted")],
            applies_to_roles=["agent", "contractor"],
        )
        db = _make_db_with_policies([policy])

        # Agent — should match
        _clear_cache()
        result = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_create", {},
            user_role="agent",
        ))
        assert result["blocked"] is True

        # Contractor — should match
        _clear_cache()
        result = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_create", {},
            user_role="contractor",
        ))
        assert result["blocked"] is True

        # Admin — should not match
        _clear_cache()
        result = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_create", {},
            user_role="admin",
        ))
        assert result["blocked"] is False

    def test_role_filter_combined_with_conditions(self):
        """Role filter AND conditions both must pass for policy to fire."""
        policy = make_policy(
            conditions=[make_condition("hours", "greater_than", 4, "number")],
            actions=[make_block_action("Too many hours for agents")],
            applies_to_roles=["agent"],
        )
        db = _make_db_with_policies([policy])

        # Agent with hours=5 — role matches, condition matches → blocked
        _clear_cache()
        result = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_create",
            {"hours": 5}, user_role="agent",
        ))
        assert result["blocked"] is True

        # Agent with hours=2 — role matches, condition fails → allowed
        _clear_cache()
        result = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_create",
            {"hours": 2}, user_role="agent",
        ))
        assert result["blocked"] is False

        # Admin with hours=5 — role doesn't match → skipped → allowed
        _clear_cache()
        result = run(evaluate_policies(
            db, "time_tracking", "time_entry", "on_create",
            {"hours": 5}, user_role="admin",
        ))
        assert result["blocked"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

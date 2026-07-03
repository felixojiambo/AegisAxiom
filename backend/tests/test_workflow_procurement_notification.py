"""
MEB4-T40 — Unit tests for sys_notify_procurement_on_scoping_complete.

Uses the same AsyncMock unit-style pattern as test_policy_engine.py so the
tests don't require a live backend. Exercises the real workflow_engine
handle_event path against a mocked db to verify:

- the rule is present in SYSTEM_WORKFLOWS
- the rule is well-formed (field_change on stage + condition equals
  scoping_complete + create_record targeting procurement_notifications)
- the engine fires the rule for both PATCH /stage (action=stage_changed)
  and PUT /opportunities/{id} (action=updated) code paths
- non-transitions and non-matching stages don't fire
- idempotence: same stage twice generates no diff, no fire
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.workflow_seeds import SYSTEM_WORKFLOWS
from services.workflow_engine import handle_event, invalidate_workflow_cache
from services.event_bus import build_event


RULE_ID = "sys_notify_procurement_on_scoping_complete"


@pytest.fixture
def rule():
    return next((r for r in SYSTEM_WORKFLOWS if r["rule_id"] == RULE_ID), None)


# ============================================================================
# Definition sanity
# ============================================================================

class TestRuleDefinition:
    def test_rule_exists_in_system_workflows(self, rule):
        assert rule is not None, f"{RULE_ID} missing from SYSTEM_WORKFLOWS"

    def test_rule_shape(self, rule):
        assert rule["module"] == "opportunities"
        assert rule["trigger"]["type"] == "field_change"
        assert rule["trigger"]["watch_field"] == "stage"

    def test_rule_condition_targets_scoping_complete(self, rule):
        conds = rule["conditions"]
        assert len(conds) >= 1
        stage_cond = next(c for c in conds if c["field"] == "stage")
        assert stage_cond["operator"] == "equals"
        assert stage_cond["value"] == "scoping_complete"

    def test_rule_action_creates_procurement_notification(self, rule):
        actions = rule["instant_actions"]
        assert len(actions) >= 1
        action = actions[0]
        assert action["type"] == "create_record"
        assert action["module"] == "procurement_notifications"
        fields = action["fields"]
        assert fields["notification_type"] == "scoping_complete"
        assert fields["status"] == "pending"
        # Opportunity ID must be interpolated from the record
        assert "{{record.opportunity_id}}" in fields["opportunity_id"]


# ============================================================================
# End-to-end via handle_event (mocked db)
# ============================================================================

def _mock_db_with_rule(rule):
    """Return a db mock that supports both db.collection and db["collection"]
    access, since workflow_engine uses attribute-style and workflow_executor
    uses dict-style."""
    class _Cursor:
        def __init__(self, items):
            self.items = items

        async def to_list(self, limit):
            return self.items

    workflow_rules = MagicMock()
    workflow_rules.find = MagicMock(side_effect=lambda *a, **kw: _Cursor([rule]))
    workflow_rules.update_one = AsyncMock()

    procurement_notifications = MagicMock()
    procurement_notifications.insert_one = AsyncMock()
    # dedup_on in the rule calls find_one before insert; default to no match.
    procurement_notifications.find_one = AsyncMock(return_value=None)

    workflow_rule_executions = MagicMock()
    workflow_rule_executions.insert_one = AsyncMock()

    collections = {
        "workflow_rules": workflow_rules,
        "procurement_notifications": procurement_notifications,
        "workflow_rule_executions": workflow_rule_executions,
    }

    db = MagicMock()
    db.workflow_rules = workflow_rules
    db.procurement_notifications = procurement_notifications
    db.workflow_rule_executions = workflow_rule_executions
    db.__getitem__ = lambda self, key: collections.setdefault(key, MagicMock(insert_one=AsyncMock()))
    return db


def _opp(stage, opportunity_id="opp_abc", name="Acme"):
    return {
        "opportunity_id": opportunity_id,
        "name": name,
        "stage": stage,
    }


class TestRuleFires:
    def setup_method(self):
        invalidate_workflow_cache()

    def test_fires_on_stage_changed_action(self, rule):
        """PATCH /opportunities/{id}/stage emits action='stage_changed'.
        field_change trigger matches on changed_fields, not action name,
        so the rule still fires."""
        db = _mock_db_with_rule(rule)
        event = build_event(
            module="opportunities",
            action="stage_changed",
            record=_opp("scoping_complete"),
            old_record=_opp("proposal_development"),
            user_id="u1", user_email="u1@test",
        )

        with patch("services.workflow_engine.get_db", return_value=db), \
             patch("services.event_bus.emit_event", new_callable=AsyncMock):
            asyncio.run(handle_event(event))

        assert db.procurement_notifications.insert_one.called, (
            "Expected a procurement_notifications insert when stage -> scoping_complete"
        )
        inserted = db.procurement_notifications.insert_one.call_args.args[0]
        assert inserted["notification_type"] == "scoping_complete"
        assert inserted["opportunity_id"] == "opp_abc"
        assert inserted["status"] == "pending"
        # ID must match sales_ops_procurement.py's procnotif_<hex12> format
        # — not the executor's target_module[:3] fallback (pro_<hex12>).
        assert inserted["notification_id"].startswith("procnotif_"), (
            f"Expected procnotif_ prefix, got {inserted['notification_id']}"
        )

    def test_fires_on_updated_action_via_field_change(self, rule):
        """PUT /opportunities/{id} emits action='updated', NOT 'stage_changed'.
        The rule must still fire because `stage` appears in changed_fields."""
        db = _mock_db_with_rule(rule)
        event = build_event(
            module="opportunities",
            action="updated",
            record=_opp("scoping_complete"),
            old_record=_opp("proposal_development"),
            user_id="u1", user_email="u1@test",
        )

        with patch("services.workflow_engine.get_db", return_value=db), \
             patch("services.event_bus.emit_event", new_callable=AsyncMock):
            asyncio.run(handle_event(event))

        assert db.procurement_notifications.insert_one.called


class TestRuleDoesNotFire:
    def setup_method(self):
        invalidate_workflow_cache()

    def test_no_fire_when_stage_unchanged(self, rule):
        """Non-transition: stage didn't actually change. changed_fields is empty
        for stage, so field_change trigger doesn't match."""
        db = _mock_db_with_rule(rule)
        event = build_event(
            module="opportunities",
            action="updated",
            record=_opp("scoping_complete"),
            old_record=_opp("scoping_complete"),  # same stage
            user_id="u1", user_email="u1@test",
        )

        with patch("services.workflow_engine.get_db", return_value=db), \
             patch("services.event_bus.emit_event", new_callable=AsyncMock):
            asyncio.run(handle_event(event))

        assert not db.procurement_notifications.insert_one.called

    def test_no_fire_when_transitioning_to_other_stage(self, rule):
        """Stage changed but not to scoping_complete — condition fails."""
        db = _mock_db_with_rule(rule)
        event = build_event(
            module="opportunities",
            action="updated",
            record=_opp("proposal_sent"),
            old_record=_opp("proposal_development"),
            user_id="u1", user_email="u1@test",
        )

        with patch("services.workflow_engine.get_db", return_value=db), \
             patch("services.event_bus.emit_event", new_callable=AsyncMock):
            asyncio.run(handle_event(event))

        assert not db.procurement_notifications.insert_one.called

    def test_no_fire_for_other_module(self, rule):
        """Event module != 'opportunities' — no rule match at all."""
        db = _mock_db_with_rule(rule)
        event = build_event(
            module="deals",
            action="updated",
            record={"deal_id": "d1", "stage": "scoping_complete"},
            old_record={"deal_id": "d1", "stage": "proposal_development"},
            user_id="u1", user_email="u1@test",
        )

        with patch("services.workflow_engine.get_db", return_value=db), \
             patch("services.event_bus.emit_event", new_callable=AsyncMock):
            asyncio.run(handle_event(event))

        assert not db.procurement_notifications.insert_one.called


class TestRuleDedup:
    """Stage re-entry must not spawn duplicate pending notifications."""

    def setup_method(self):
        invalidate_workflow_cache()

    def test_skips_insert_when_pending_notification_exists(self, rule):
        db = _mock_db_with_rule(rule)
        # Pre-existing pending notification for this opportunity+type.
        db.procurement_notifications.find_one = AsyncMock(
            return_value={"_id": "xyz", "opportunity_id": "opp_abc"}
        )
        event = build_event(
            module="opportunities",
            action="updated",
            record=_opp("scoping_complete"),
            old_record=_opp("proposal_development"),
            user_id="u1", user_email="u1@test",
        )

        with patch("services.workflow_engine.get_db", return_value=db), \
             patch("services.event_bus.emit_event", new_callable=AsyncMock):
            asyncio.run(handle_event(event))

        assert db.procurement_notifications.find_one.called
        assert not db.procurement_notifications.insert_one.called, (
            "Expected dedup_on to suppress insert when a pending notification exists"
        )

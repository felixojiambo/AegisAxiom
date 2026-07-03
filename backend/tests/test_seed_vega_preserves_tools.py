"""Regression test for seed_vega's $set / $setOnInsert split.

Locks down the property that re-running seed_vega against an existing
ai_employees.ai_vega doc does NOT clobber:

  - tools list (operator-managed whitelist; the v3.1 migration writes
    21 tools here, dev's seed_vega payload lists 9. Every Cloud Run
    deploy used to overwrite the live list with whatever the deploying
    branch shipped.)
  - metrics, execution_count, success_count, error_count (usage
    counters that accumulate over the lifetime of the doc)
  - execution_log
  - created_at, created_by

All other fields (name, description, prompts, model_name,
knowledge_base, etc.) should continue to refresh on every deploy so
config changes track the latest code.
"""
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_seed_vega_splits_preserved_fields_into_set_on_insert():
    """Inspect the update_one call args — tools + counters must go to $setOnInsert."""
    from seeds.seed_vega import seed_vega

    # Mock db with async update_one. The KB ingestion loop also calls update_one,
    # so we capture all calls and look at the ai_employees one specifically.
    db = AsyncMock()
    db.ai_employees.update_one = AsyncMock()
    db.ai_employee_knowledge.update_one = AsyncMock()
    # Patch out filesystem reads — the KB section reads VEGA_KNOWLEDGE_DIR/*.md.
    # We don't need that path to succeed for this test; the employee-doc upsert
    # happens first and is what we're asserting on.
    with patch("seeds.seed_vega.Path") as mock_path:
        # Make any .read_text(...) on a Path object return a short string,
        # and exists() always True so the seed proceeds.
        mock_path.return_value.read_text.return_value = "<test prompt>"
        mock_path.return_value.exists.return_value = True
        # Joining (/) just returns another mocked Path
        mock_path.return_value.__truediv__.return_value = mock_path.return_value

        # The seed iterates over a hard-coded list of (id, filename) tuples
        # then calls .read_text() per file. We don't care about those calls
        # — just the FIRST update_one on ai_employees.
        try:
            await seed_vega(db)
        except Exception:
            # Swallow any downstream errors from the KB pass — we only
            # care about the first update_one call (employee doc).
            pass

    # Assert: ai_employees.update_one was called with both $set and $setOnInsert.
    assert db.ai_employees.update_one.call_count >= 1
    call = db.ai_employees.update_one.call_args
    # call_args: (args, kwargs). Filter doc is args[0], update doc is args[1].
    filter_doc, update_doc = call.args[0], call.args[1]

    assert filter_doc == {"employee_id": "ai_vega"}
    assert "$set" in update_doc, "must use $set for config-class fields"
    assert "$setOnInsert" in update_doc, "must use $setOnInsert to preserve live state"

    set_keys = set(update_doc["$set"].keys())
    soi_keys = set(update_doc["$setOnInsert"].keys())

    must_preserve = {
        "tools",
        "metrics",
        "execution_count",
        "success_count",
        "error_count",
        "last_execution_at",
        "execution_log",
        "created_at",
        "created_by",
    }
    leaked = must_preserve & set_keys
    assert not leaked, (
        f"these fields must be in $setOnInsert (not $set): {sorted(leaked)}. "
        f"Putting them in $set means every deploy clobbers live state."
    )
    missing = must_preserve - soi_keys
    assert not missing, f"these fields must be present in $setOnInsert: {sorted(missing)}"

    # Config-class fields should refresh on every deploy.
    must_refresh = {
        "name", "role", "description", "system_prompt", "router_description",
        "model_provider", "model_name", "knowledge_base", "specialties",
        "state_schema", "metrics_config", "type", "status", "mode",
    }
    not_refreshed = must_refresh - set_keys
    assert not not_refreshed, (
        f"these fields should refresh on every deploy and must be in $set: "
        f"{sorted(not_refreshed)}"
    )
    no_overlap = set_keys & soi_keys
    assert not no_overlap, (
        f"a field must be in exactly one of $set / $setOnInsert: {sorted(no_overlap)}"
    )


@pytest.mark.asyncio
async def test_upsert_true_so_fresh_install_still_works():
    """First-time seed (no existing doc) must still create one — upsert=True."""
    from seeds.seed_vega import seed_vega

    db = AsyncMock()
    db.ai_employees.update_one = AsyncMock()
    db.ai_employee_knowledge.update_one = AsyncMock()
    with patch("seeds.seed_vega.Path") as mock_path:
        mock_path.return_value.read_text.return_value = "<test prompt>"
        mock_path.return_value.exists.return_value = True
        mock_path.return_value.__truediv__.return_value = mock_path.return_value
        try:
            await seed_vega(db)
        except Exception:
            pass

    call = db.ai_employees.update_one.call_args
    assert call.kwargs.get("upsert") is True, (
        "upsert=True is required so a fresh-install env with no ai_vega doc still gets one"
    )

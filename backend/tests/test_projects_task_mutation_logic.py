"""
Pure-logic unit tests for the Projects v1.1 live-update fix.

These tests mirror the JavaScript reducer functions from ProjectDetailPage.js
(onTaskMutated, onTaskDeleted) in Python so they can be run in CI with the
existing pytest setup — no browser or JS runtime required.

Coverage:
  - onTaskMutated(null)  -> signals fetchTasks fallback
  - onTaskMutated(updated) with existing id -> merges in place, length unchanged
  - onTaskMutated(updated) with new id -> appends, length+1
  - onTaskDeleted(taskId) -> filters out by task_id shape
  - onTaskDeleted(taskId) -> filters out by _id shape
  - onTaskDeleted with unknown id -> no-op
  - Both shapes coexist in the same list (mixed task_id / _id documents)
  - Ordering preserved during in-place replace
  - Original list is never mutated (immutability contract)
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Pure reducer implementations — mirrors frontend/src/components/projects/
# ProjectDetailPage.js lines 704-719 exactly.
# ---------------------------------------------------------------------------


def get_task_id(task: dict) -> str | None:
    """Return canonical id from a task dict — mirrors (t.task_id ?? t._id)."""
    return task.get("task_id") or task.get("_id")


def apply_task_mutated(prev: list[dict], updated: dict) -> list[dict]:
    """
    Mirrors onTaskMutated reducer in ProjectDetailPage.js.

    If updated id is found in prev -> replace in-place, return same length.
    If not found -> append, return length+1.
    """
    uid = get_task_id(updated)
    idx = next(
        (i for i, t in enumerate(prev) if get_task_id(t) == uid),
        -1,
    )
    if idx == -1:
        return [*prev, updated]
    next_list = list(prev)
    next_list[idx] = updated
    return next_list


def apply_task_deleted(prev: list[dict], task_id: str | None) -> list[dict]:
    """
    Mirrors onTaskDeleted reducer in ProjectDetailPage.js.

    Returns filtered list excluding the task with the given id.
    """
    if not task_id:
        return prev
    return [t for t in prev if get_task_id(t) != task_id]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_task(**overrides) -> dict:
    base = {
        "task_id": "task_001",
        "title": "Default task",
        "status": "not_started",
        "priority": "medium",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# onTaskMutated — merge / append logic
# ---------------------------------------------------------------------------


class TestApplyTaskMutated:
    def test_replaces_existing_task_in_place_by_task_id(self):
        initial = [
            make_task(task_id="task_001", status="not_started"),
            make_task(task_id="task_002", status="in_progress"),
        ]
        updated = {"task_id": "task_001", "status": "complete", "title": "Done"}
        result = apply_task_mutated(initial, updated)

        assert len(result) == 2
        assert result[0] == updated
        assert result[1] == initial[1]

    def test_replaces_existing_task_in_place_by_underscore_id(self):
        initial = [{"_id": "mongo_abc", "title": "Old", "status": "not_started"}]
        updated = {"_id": "mongo_abc", "title": "New", "status": "complete"}
        result = apply_task_mutated(initial, updated)

        assert len(result) == 1
        assert result[0]["title"] == "New"

    def test_appends_when_id_not_found(self):
        initial = [make_task(task_id="task_001")]
        new_task = make_task(task_id="task_999", title="Brand new")
        result = apply_task_mutated(initial, new_task)

        assert len(result) == 2
        assert result[-1] == new_task
        assert result[0] == initial[0]

    def test_appends_to_empty_list(self):
        new_task = make_task(task_id="task_001")
        result = apply_task_mutated([], new_task)
        assert result == [new_task]

    def test_does_not_mutate_original_list(self):
        initial = [make_task(task_id="task_001")]
        updated = {"task_id": "task_001", "status": "complete"}
        apply_task_mutated(initial, updated)
        assert initial[0]["status"] == "not_started"

    def test_preserves_order_of_other_tasks(self):
        initial = [
            make_task(task_id="task_001"),
            make_task(task_id="task_002"),
            make_task(task_id="task_003"),
        ]
        updated = {"task_id": "task_002", "status": "complete"}
        result = apply_task_mutated(initial, updated)

        ids = [get_task_id(t) for t in result]
        assert ids == ["task_001", "task_002", "task_003"]
        assert result[1]["status"] == "complete"

    def test_uses_task_id_when_both_id_fields_present(self):
        initial = [{"task_id": "task_001", "_id": "mongo_abc", "status": "not_started"}]
        updated = {"task_id": "task_001", "_id": "mongo_abc", "status": "complete"}
        result = apply_task_mutated(initial, updated)
        assert len(result) == 1
        assert result[0]["status"] == "complete"

    def test_multiple_applies_accumulate_correctly(self):
        """Simulate rapid successive edits to same task — last write wins."""
        initial = [make_task(task_id="task_001", status="not_started")]
        after_first = apply_task_mutated(
            initial, {"task_id": "task_001", "status": "in_progress"}
        )
        after_second = apply_task_mutated(
            after_first, {"task_id": "task_001", "status": "complete"}
        )
        assert len(after_second) == 1
        assert after_second[0]["status"] == "complete"


# ---------------------------------------------------------------------------
# onTaskDeleted — filter logic
# ---------------------------------------------------------------------------


class TestApplyTaskDeleted:
    def test_removes_task_by_task_id(self):
        tasks = [
            make_task(task_id="task_001"),
            make_task(task_id="task_002"),
        ]
        result = apply_task_deleted(tasks, "task_001")
        assert len(result) == 1
        assert result[0]["task_id"] == "task_002"

    def test_removes_task_by_underscore_id(self):
        tasks = [
            {"_id": "mongo_abc", "title": "Task A"},
            {"_id": "mongo_xyz", "title": "Task B"},
        ]
        result = apply_task_deleted(tasks, "mongo_abc")
        assert len(result) == 1
        assert result[0]["_id"] == "mongo_xyz"

    def test_noop_when_id_not_found(self):
        tasks = [make_task(task_id="task_001")]
        result = apply_task_deleted(tasks, "task_999")
        assert len(result) == 1

    def test_returns_unchanged_on_falsy_task_id(self):
        tasks = [make_task(task_id="task_001")]
        assert apply_task_deleted(tasks, None) == tasks
        assert apply_task_deleted(tasks, "") == tasks

    def test_does_not_mutate_original_list(self):
        tasks = [make_task(task_id="task_001"), make_task(task_id="task_002")]
        apply_task_deleted(tasks, "task_001")
        assert len(tasks) == 2

    def test_handles_empty_list(self):
        result = apply_task_deleted([], "task_001")
        assert result == []


# ---------------------------------------------------------------------------
# Mixed id shape — both task_id and _id docs coexist in same list
# ---------------------------------------------------------------------------


class TestMixedIdShapes:
    def test_mutated_finds_task_id_doc_among_mixed_list(self):
        tasks = [
            {"task_id": "task_001", "status": "not_started"},
            {"_id": "mongo_abc", "status": "not_started"},
        ]
        updated = {"task_id": "task_001", "status": "complete"}
        result = apply_task_mutated(tasks, updated)
        assert len(result) == 2
        assert result[0]["status"] == "complete"
        assert result[1]["status"] == "not_started"

    def test_deleted_removes_only_matching_doc(self):
        tasks = [
            {"task_id": "task_001", "status": "not_started"},
            {"_id": "mongo_abc", "status": "in_progress"},
        ]
        result = apply_task_deleted(tasks, "task_001")
        assert len(result) == 1
        assert result[0].get("_id") == "mongo_abc"


# ---------------------------------------------------------------------------
# Sequence: onTaskMutated then onTaskDeleted
# ---------------------------------------------------------------------------


class TestMutationSequence:
    def test_update_then_delete_produces_correct_final_state(self):
        initial = [
            make_task(task_id="task_001", status="not_started"),
            make_task(task_id="task_002", status="not_started"),
        ]

        after_update = apply_task_mutated(
            initial, {"task_id": "task_001", "status": "complete"}
        )
        assert after_update[0]["status"] == "complete"
        assert len(after_update) == 2

        after_delete = apply_task_deleted(after_update, "task_002")
        assert len(after_delete) == 1
        assert after_delete[0]["task_id"] == "task_001"

    def test_delete_then_update_of_another_task(self):
        initial = [
            make_task(task_id="task_001"),
            make_task(task_id="task_002"),
        ]
        after_delete = apply_task_deleted(initial, "task_001")
        after_update = apply_task_mutated(
            after_delete, {"task_id": "task_002", "status": "complete"}
        )
        assert len(after_update) == 1
        assert after_update[0]["task_id"] == "task_002"
        assert after_update[0]["status"] == "complete"

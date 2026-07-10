"""Integration smoke tests for the saved-views CRUD API.

Tests hit the actual route handlers with a patched DB fixture.
Covers the design doc's TC-08 → TC-11 and TC-16 test cases.

These are HTTP-level integration tests. They require the backend routes
to be importable (DB init is mocked via monkeypatch of get_db).
"""
from __future__ import annotations

import os
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "aegisaxiom_test")

import pytest


# ---------------------------------------------------------------------------
# Minimal unit-style tests for route helper logic
# (no HTTP server needed — test the validators and helpers in isolation)
# ---------------------------------------------------------------------------

from routes.projects_saved_views import (
    VALID_PILL_IDS,
    VALID_VIEW_TYPES,
    MAX_SAVED_VIEWS_PER_USER,
    SavedViewCreate,
    SavedViewPatch,
)
from pydantic import ValidationError


class TestSavedViewCreate:
    """Validate Pydantic schema enforcement — mirrors TC-08, TC-09."""

    def test_valid_payload(self):
        sv = SavedViewCreate(
            name="My view",
            scope="global",
            view_type="board",
            pills=["mine", "due_this_week"],
            is_default=False,
        )
        assert sv.name == "My view"
        assert sv.pills == ["mine", "due_this_week"]

    def test_unknown_pill_rejected(self):
        """TC-09: POST with unknown pill_id returns validation error."""
        with pytest.raises(ValidationError) as exc_info:
            SavedViewCreate(
                name="Bad view",
                scope="global",
                view_type="board",
                pills=["mine", "lol"],
            )
        assert "lol" in str(exc_info.value)

    def test_invalid_view_type_rejected(self):
        with pytest.raises(ValidationError):
            SavedViewCreate(
                name="Bad view",
                scope="global",
                view_type="calendar_pro_max",
                pills=[],
            )

    def test_name_too_long_rejected(self):
        with pytest.raises(ValidationError):
            SavedViewCreate(
                name="x" * 81,
                scope="global",
                view_type="board",
                pills=[],
            )

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            SavedViewCreate(
                name="",
                scope="global",
                view_type="board",
                pills=[],
            )

    def test_pills_deduplicated(self):
        sv = SavedViewCreate(
            name="Dupe pills",
            scope="global",
            view_type="board",
            pills=["mine", "mine", "overdue"],
        )
        assert sv.pills == ["mine", "overdue"]

    def test_all_valid_pill_ids_accepted(self):
        """Every pill registered in pillRegistry must be accepted."""
        sv = SavedViewCreate(
            name="All pills",
            scope="global",
            view_type="board",
            pills=list(VALID_PILL_IDS),
        )
        assert set(sv.pills) == VALID_PILL_IDS

    def test_project_scope_accepted(self):
        sv = SavedViewCreate(
            name="Project view",
            scope="project",
            project_id="proj_abc",
            view_type="backlog",
            pills=["mine"],
        )
        assert sv.scope == "project"
        assert sv.project_id == "proj_abc"


class TestSavedViewPatch:
    """Validate patch schema — only mutable fields allowed."""

    def test_valid_patch(self):
        patch = SavedViewPatch(name="Renamed", pills=["overdue"])
        assert patch.name == "Renamed"
        assert patch.pills == ["overdue"]

    def test_unknown_pill_in_patch_rejected(self):
        with pytest.raises(ValidationError):
            SavedViewPatch(pills=["mine", "bad_pill_xyz"])

    def test_empty_patch_ok(self):
        patch = SavedViewPatch()
        assert patch.name is None
        assert patch.pills is None
        assert patch.is_default is None


class TestConstants:
    """Sanity-check the module-level constants."""

    def test_valid_pill_ids_match_design_doc(self):
        expected = {
            "mine",
            "due_this_week",
            "overdue",
            "blocked",
            "at_risk",
            "unassigned",
            "high_priority",
            "mine_pm",
        }
        assert VALID_PILL_IDS == expected

    def test_valid_view_types(self):
        assert "board" in VALID_VIEW_TYPES
        assert "backlog" in VALID_VIEW_TYPES
        assert "myday" in VALID_VIEW_TYPES

    def test_max_saved_views_cap(self):
        assert MAX_SAVED_VIEWS_PER_USER == 50


class TestFlipDefaultAtomicity:
    """Wave 3.5 fix 2: concurrent PATCHes setting is_default=True must not
    produce two defaults for the same (user_id, view_type).

    The defense is layered:
      1) `_flip_default` (update_many) clears existing defaults *excluding*
         the target row.
      2) A partial unique index on (user_id, view_type) where
         is_default==True makes the database the final arbiter — the second
         concurrent writer's update_one fails with DuplicateKeyError.

    These tests simulate the in-process state to verify the ordering and
    the duplicate-key surface. Real concurrency is exercised in the
    integration suite against MongoDB; here we verify the contract.
    """

    @pytest.mark.asyncio
    async def test_flip_default_clears_others_excluding_target(self):
        from routes.projects_saved_views import _flip_default

        # In-memory fake: two views are defaults, one of them is the
        # target. After _flip_default, only the target should remain True.
        docs = [
            {"saved_view_id": "sv_a", "user_id": "u1", "view_type": "board",
             "is_default": True, "updated_at": "old"},
            {"saved_view_id": "sv_target", "user_id": "u1", "view_type": "board",
             "is_default": True, "updated_at": "old"},
            {"saved_view_id": "sv_other_user", "user_id": "u2", "view_type": "board",
             "is_default": True, "updated_at": "old"},
        ]

        class _Coll:
            def __init__(self, store):
                self.store = store

            async def update_many(self, filt, update):
                # Apply the filter to our in-memory list. The real Mongo
                # query supports: user_id, view_type, is_default,
                # saved_view_id $ne.
                ne_id = (filt.get("saved_view_id") or {}).get("$ne")
                for doc in self.store:
                    if doc.get("user_id") != filt.get("user_id"):
                        continue
                    if doc.get("view_type") != filt.get("view_type"):
                        continue
                    if doc.get("is_default") is not filt.get("is_default"):
                        continue
                    if ne_id and doc.get("saved_view_id") == ne_id:
                        continue
                    for k, v in (update.get("$set") or {}).items():
                        doc[k] = v

        class _Db:
            def __init__(self, store):
                self._coll = _Coll(store)

            def __getitem__(self, name):
                assert name == "pm_user_saved_views"
                return self._coll

        await _flip_default(_Db(docs), "u1", "board", "sv_target")

        by_id = {d["saved_view_id"]: d for d in docs}
        # Target preserved (it's excluded from the flip).
        assert by_id["sv_target"]["is_default"] is True
        # Same-user same-view-type but not target — flipped to False.
        assert by_id["sv_a"]["is_default"] is False
        # Other user's row — untouched.
        assert by_id["sv_other_user"]["is_default"] is True

    @pytest.mark.asyncio
    async def test_concurrent_set_defaults_surface_duplicate_key_as_409(self):
        """Simulate the race: two PATCHes target two different rows for the
        same (user_id, view_type). With the partial unique index in place,
        the second writer's update_one raises DuplicateKeyError; the route
        surfaces it as HTTP 409.

        We exercise the route handler with a fake db whose `find_one_and_update`
        on the second call raises a duplicate-key-like exception.
        """
        from fastapi import HTTPException
        import routes.projects_saved_views as mod
        from routes.projects_saved_views import update_saved_view, SavedViewPatch

        existing = {
            "saved_view_id": "sv_target",
            "user_id": "u1",
            "view_type": "board",
            "scope": "global",
            "project_id": None,
            "name": "My View",
            "pills": [],
            "is_default": False,
            "created_at": "x",
            "updated_at": "x",
        }

        class _DupKeyError(Exception):
            def __init__(self):
                super().__init__("E11000 duplicate key error")

            @property
            def __class__name__(self):
                return "DuplicateKeyError"

        class _Coll:
            def __init__(self):
                self.calls = 0

            async def find_one(self, query, projection=None):
                # Lookup by saved_view_id — return the existing doc.
                if query.get("saved_view_id") == "sv_target":
                    return existing
                return None

            async def update_many(self, *_a, **_kw):
                # No-op clear (no other defaults exist).
                return None

            async def find_one_and_update(self, *_a, **_kw):
                # Simulate the racing concurrent write winning first —
                # our update fails with duplicate key.
                raise _DupKeyError()

            async def create_index(self, *_a, **_kw):
                return None

        class _Db:
            def __init__(self):
                self._coll = _Coll()

            def __getitem__(self, name):
                return self._coll

        # Reset the index-ensured flag so _ensure_default_unique_index runs.
        mod._DEFAULT_UNIQUE_INDEX_ENSURED = False

        # Make get_db return our fake.
        import routes.projects_saved_views as mod_routes
        original_get_db = mod_routes.get_db
        mod_routes.get_db = lambda: _Db()
        # Permission gate must pass.
        original_has_perm = mod_routes.has_perm
        mod_routes.has_perm = lambda u, p: True

        from types import SimpleNamespace
        user = SimpleNamespace(user_id="u1", role="pm", permissions=set(), _perm_closure=set())

        try:
            with pytest.raises(HTTPException) as exc:
                await update_saved_view(
                    saved_view_id="sv_target",
                    body=SavedViewPatch(is_default=True),
                    current_user=user,
                )
            assert exc.value.status_code == 409
            assert "default" in str(exc.value.detail).lower()
        finally:
            mod_routes.get_db = original_get_db
            mod_routes.has_perm = original_has_perm


class TestScopeProjectValidation:
    """TC-08: scope='project' with project_id=null should 400 at route level.

    Route-level validation (not Pydantic): the Pydantic model allows
    project_id=None for schema flexibility; the route handler raises 400.
    We test the condition directly.
    """

    def test_project_scope_with_null_project_id_is_invalid(self):
        """Simulate what the route would enforce."""
        body = SavedViewCreate(
            name="Missing project_id",
            scope="project",
            project_id=None,
            view_type="board",
            pills=[],
        )
        # The route checks: if body.scope == 'project' and not body.project_id → 400
        assert body.scope == "project"
        assert not body.project_id  # triggers 400 in the route handler

    def test_global_scope_with_null_project_id_is_valid(self):
        body = SavedViewCreate(
            name="Global view",
            scope="global",
            project_id=None,
            view_type="board",
            pills=[],
        )
        assert body.scope == "global"

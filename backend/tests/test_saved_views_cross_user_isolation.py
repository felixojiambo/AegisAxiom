"""Integration: saved views cross-user isolation.

TC-SV-ISO-01: User A cannot see user B's saved views (GET returns empty list).
TC-SV-ISO-02: User A cannot PATCH user B's view (404, not 403 — prevents info-leak).
TC-SV-ISO-03: User A cannot DELETE user B's view (404, same reasoning).
TC-SV-ISO-04: User A CAN see and edit their own views normally.

Uses FastAPI TestClient against the real routes with a mocked DB and auth override.
Routes call get_db() as a regular function, so we patch it at module level.
No live Mongo, no JWT — just the route handler logic.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(user_id, perms=None):
    return SimpleNamespace(
        user_id=user_id,
        email=f"{user_id}@aegisaxiom.io",
        role="project_manager",
        permissions=list(perms or ["projects.dashboard.view"]),
    )


def _make_view(saved_view_id, user_id, name="My View"):
    return {
        "saved_view_id": saved_view_id,
        "user_id": user_id,
        "name": name,
        "scope": "global",
        "project_id": None,
        "view_type": "board",
        "pills": ["mine"],
        "is_default": False,
        "created_at": "2026-05-11T12:00:00+00:00",
        "updated_at": "2026-05-11T12:00:00+00:00",
    }


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *a, **kw):
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()


class _ViewCollection:
    """In-memory collection for pm_user_saved_views."""

    def __init__(self, docs=None):
        self._store: list[dict] = list(docs or [])

    async def find_one(self, query, projection=None, **_kw):
        for doc in self._store:
            match = True
            for k, v in query.items():
                if isinstance(v, dict):
                    continue  # skip complex queries for now
                if doc.get(k) != v:
                    match = False
                    break
            if match:
                return {**doc}
        return None

    def find(self, query, projection=None, **_kw):
        user_id = query.get("user_id")
        results = [d for d in self._store if not user_id or d.get("user_id") == user_id]
        return _Cursor(results)

    async def count_documents(self, query):
        user_id = query.get("user_id")
        return sum(1 for d in self._store if not user_id or d.get("user_id") == user_id)

    async def insert_one(self, doc):
        self._store.append(doc)
        return SimpleNamespace(inserted_id="stub")

    async def find_one_and_update(self, query, update_ops, return_document=True, projection=None):
        sid = query.get("saved_view_id")
        for i, doc in enumerate(self._store):
            if doc.get("saved_view_id") == sid:
                for k, v in (update_ops.get("$set") or {}).items():
                    self._store[i][k] = v
                return {**self._store[i]}
        return None

    async def delete_one(self, query):
        sid = query.get("saved_view_id")
        for i, doc in enumerate(self._store):
            if doc.get("saved_view_id") == sid:
                self._store.pop(i)
                return

    async def update_many(self, query, update_ops):
        pass  # flip_default — no-op for tests


def _build_db(views):
    coll = _ViewCollection(views)
    db = MagicMock()
    db.__getitem__ = MagicMock(side_effect=lambda name: coll)
    return db, coll


def _build_app(user):
    from routes.projects_saved_views import router
    from auth_helper import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api/projects")
    app.dependency_overrides[get_current_user] = lambda: user
    return app


# ---------------------------------------------------------------------------
# TC-SV-ISO-01: Cross-user GET
# ---------------------------------------------------------------------------

class TestSavedViewsGetIsolation:
    def test_user_a_cannot_see_user_b_views(self):
        """GET /saved-views returns only caller's own views — user_id filter applied server-side."""
        views = [
            _make_view("sv_alice_1", user_id="u_alice", name="Alice's view"),
            _make_view("sv_bob_1", user_id="u_bob", name="Bob's view"),
        ]
        user_alice = _make_user("u_alice")
        db, _ = _build_db(views)
        app = _build_app(user_alice)

        with patch("routes.projects_saved_views.get_db", return_value=db), \
             patch("routes.projects_saved_views.has_perm", return_value=True):
            resp = TestClient(app).get("/api/projects/saved-views")

        assert resp.status_code == 200
        items = resp.json()["items"]
        for item in items:
            assert item["user_id"] == "u_alice", \
                f"Alice received a view owned by {item['user_id']}"
        names = [i["name"] for i in items]
        assert "Alice's view" in names
        assert "Bob's view" not in names

    def test_user_sees_all_own_views(self):
        """User with multiple views gets all of them."""
        views = [
            _make_view("sv_b1", user_id="u_bob", name="B view 1"),
            _make_view("sv_b2", user_id="u_bob", name="B view 2"),
            _make_view("sv_a1", user_id="u_alice", name="A view 1"),
        ]
        user_bob = _make_user("u_bob")
        db, _ = _build_db(views)
        app = _build_app(user_bob)

        with patch("routes.projects_saved_views.get_db", return_value=db), \
             patch("routes.projects_saved_views.has_perm", return_value=True):
            resp = TestClient(app).get("/api/projects/saved-views")

        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 2
        assert all(i["user_id"] == "u_bob" for i in items)


# ---------------------------------------------------------------------------
# TC-SV-ISO-02: Cross-user PATCH
# ---------------------------------------------------------------------------

class TestSavedViewsPatchIsolation:
    def test_user_a_cannot_patch_user_b_view(self):
        """PATCH on a view owned by another user returns 404 (info-leak prevention)."""
        views = [_make_view("sv_bob_1", user_id="u_bob")]
        user_alice = _make_user("u_alice")
        db, _ = _build_db(views)
        app = _build_app(user_alice)

        with patch("routes.projects_saved_views.get_db", return_value=db), \
             patch("routes.projects_saved_views.has_perm", return_value=True):
            resp = TestClient(app).patch(
                "/api/projects/saved-views/sv_bob_1",
                json={"name": "Hijacked View"},
            )

        # Must be 404, NOT 403 — route must not reveal that the view exists
        assert resp.status_code == 404, \
            f"Expected 404 (info-leak prevention), got {resp.status_code}"

    def test_user_can_patch_own_view(self):
        """PATCH on own view succeeds and returns updated doc."""
        views = [_make_view("sv_alice_1", user_id="u_alice", name="Original")]
        user_alice = _make_user("u_alice")
        db, _ = _build_db(views)
        app = _build_app(user_alice)

        with patch("routes.projects_saved_views.get_db", return_value=db), \
             patch("routes.projects_saved_views.has_perm", return_value=True):
            resp = TestClient(app).patch(
                "/api/projects/saved-views/sv_alice_1",
                json={"name": "Renamed"},
            )

        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"


# ---------------------------------------------------------------------------
# TC-SV-ISO-03: Cross-user DELETE
# ---------------------------------------------------------------------------

class TestSavedViewsDeleteIsolation:
    def test_user_a_cannot_delete_user_b_view(self):
        """DELETE on another user's view returns 404."""
        views = [_make_view("sv_bob_1", user_id="u_bob")]
        user_alice = _make_user("u_alice")
        db, coll = _build_db(views)
        app = _build_app(user_alice)

        with patch("routes.projects_saved_views.get_db", return_value=db), \
             patch("routes.projects_saved_views.has_perm", return_value=True):
            resp = TestClient(app).delete("/api/projects/saved-views/sv_bob_1")

        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"
        # Verify the view was NOT deleted from the in-memory store
        assert len(coll._store) == 1, "Bob's view must still exist after Alice's failed delete"

    def test_user_can_delete_own_view(self):
        """DELETE on own view returns 204 and view is gone from store."""
        views = [_make_view("sv_alice_1", user_id="u_alice")]
        user_alice = _make_user("u_alice")
        db, coll = _build_db(views)
        app = _build_app(user_alice)

        with patch("routes.projects_saved_views.get_db", return_value=db), \
             patch("routes.projects_saved_views.has_perm", return_value=True):
            resp = TestClient(app).delete("/api/projects/saved-views/sv_alice_1")

        assert resp.status_code == 204
        assert len(coll._store) == 0


# ---------------------------------------------------------------------------
# TC-SV-ISO-04: No permission → 403
# ---------------------------------------------------------------------------

class TestSavedViewsPermissionGate:
    """The perm gate is guarded by has_perm, which is imported by name into the module.
    Earlier tests in the pytest session can monkeypatch the module-level reference, so
    we always override it explicitly — patching it to return False to simulate no-perm,
    and True to simulate a permissioned user. This makes the tests robust regardless of
    run order.
    """

    def test_no_perm_get_returns_403(self):
        views = [_make_view("sv_1", user_id="u_user")]
        user_no_perm = _make_user("u_user", perms=[])
        db, _ = _build_db(views)
        app = _build_app(user_no_perm)

        # Explicitly patch has_perm to return False so the gate fires regardless of
        # whatever module-level state prior tests may have left behind.
        with patch("routes.projects_saved_views.get_db", return_value=db), \
             patch("routes.projects_saved_views.has_perm", return_value=False):
            resp = TestClient(app).get("/api/projects/saved-views")

        assert resp.status_code == 403, \
            f"Expected 403 for user with no permissions, got {resp.status_code}"

    def test_no_perm_post_returns_403(self):
        user_no_perm = _make_user("u_user", perms=[])
        db, _ = _build_db([])
        app = _build_app(user_no_perm)

        with patch("routes.projects_saved_views.get_db", return_value=db), \
             patch("routes.projects_saved_views.has_perm", return_value=False):
            resp = TestClient(app).post(
                "/api/projects/saved-views",
                json={"name": "My View", "scope": "global", "view_type": "board", "pills": []},
            )

        assert resp.status_code == 403, \
            f"Expected 403 for user with no permissions, got {resp.status_code}"

    def test_with_perm_get_returns_200(self):
        """Sanity check: permissioned user gets 200 from the same gate."""
        views = [_make_view("sv_1", user_id="u_user")]
        user_with_perm = _make_user("u_user", perms=["projects.dashboard.view"])
        db, _ = _build_db(views)
        app = _build_app(user_with_perm)

        with patch("routes.projects_saved_views.get_db", return_value=db), \
             patch("routes.projects_saved_views.has_perm", return_value=True):
            resp = TestClient(app).get("/api/projects/saved-views")

        assert resp.status_code == 200

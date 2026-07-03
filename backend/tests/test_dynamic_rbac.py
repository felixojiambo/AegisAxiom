"""
Dynamic RBAC Integration Tests

Direct tests against the permission_registry service that exercise the
ingestion-style register/deactivate flow without going through the full
ingestion pipeline. Verifies:
- register_permissions writes documents with the correct shape
- deactivate_permissions only touches the target app's ingestion-source perms
- Re-registering a subset deactivates the ones that fell out
- System permissions are never affected by ingestion deactivations
- clone_role silently drops keys that aren't in the registry
"""
import asyncio
import os
import uuid

import pytest
import requests

# These tests need the backend's database connection. We import lazily so
# the tests are skipped cleanly if the project's import path isn't set up.
pytest.importorskip("motor")

from database import init as init_db, get_db  # noqa: E402
from services.permission_registry import (  # noqa: E402
    SOURCE_INGESTION,
    SOURCE_SYSTEM,
    deactivate_permissions,
    get_all_permissions,
    get_valid_keys,
    register_permissions,
    seed_system_permissions,
)


BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
ADMIN_EMAIL = "support@simplimeta.io"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin@123")
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "aegisaxiom")


@pytest.fixture(scope="module", autouse=True)
def db_connection():
    """Initialize the motor DB once for the module."""
    init_db(MONGO_URL, DB_NAME)
    yield


@pytest.fixture
def fake_app_id():
    return f"app_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
def admin_session():
    s = requests.Session()
    resp = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, f"Admin login failed: {resp.text}"
    return s


# ---------------------------------------------------------------------------
# register_permissions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_permissions_writes_ingestion_docs(fake_app_id):
    perms = [
        {"key": f"{fake_app_id}.entity.view", "group": fake_app_id, "label": "View Entity"},
        {"key": f"{fake_app_id}.entity.create", "group": fake_app_id, "label": "Create Entity"},
    ]
    count = await register_permissions(perms, source=SOURCE_INGESTION, app_id=fake_app_id)
    assert count == 2

    discovered = await get_all_permissions(source=SOURCE_INGESTION, app_id=fake_app_id)
    keys = {p["key"] for p in discovered}
    assert keys == {p["key"] for p in perms}
    assert all(p["source"] == SOURCE_INGESTION for p in discovered)
    assert all(p["app_id"] == fake_app_id for p in discovered)
    assert all(p["is_active"] is True for p in discovered)

    # cleanup
    db = get_db()
    await db.permissions.delete_many({"app_id": fake_app_id})


@pytest.mark.asyncio
async def test_register_permissions_rejects_invalid_source():
    with pytest.raises(ValueError):
        await register_permissions(
            [{"key": "bad.source.test", "group": "Test", "label": "Bad"}],
            source="not_a_real_source",
        )


# ---------------------------------------------------------------------------
# deactivate_permissions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deactivate_marks_dropped_keys_inactive(fake_app_id):
    """Re-ingestion should deactivate keys that fell out of the new generation."""
    perms = [
        {"key": f"{fake_app_id}.a.view", "group": fake_app_id, "label": "A"},
        {"key": f"{fake_app_id}.b.view", "group": fake_app_id, "label": "B"},
        {"key": f"{fake_app_id}.c.view", "group": fake_app_id, "label": "C"},
    ]
    await register_permissions(perms, source=SOURCE_INGESTION, app_id=fake_app_id)

    keep = {f"{fake_app_id}.a.view"}
    deactivated = await deactivate_permissions(fake_app_id, keep)
    assert deactivated == 2

    active = await get_all_permissions(source=SOURCE_INGESTION, app_id=fake_app_id, is_active=True)
    assert {p["key"] for p in active} == keep

    inactive = await get_all_permissions(source=SOURCE_INGESTION, app_id=fake_app_id, is_active=False)
    assert {p["key"] for p in inactive} == {f"{fake_app_id}.b.view", f"{fake_app_id}.c.view"}

    # cleanup
    db = get_db()
    await db.permissions.delete_many({"app_id": fake_app_id})


@pytest.mark.asyncio
async def test_deactivate_does_not_touch_system_permissions(fake_app_id):
    """System permissions must never be deactivated by ingestion flows."""
    # Ensure the system seed has run at least once.
    await seed_system_permissions()
    system_keys_before = await get_valid_keys()
    assert len(system_keys_before) > 0

    # Register and then deactivate everything for a different fake app.
    await register_permissions(
        [{"key": f"{fake_app_id}.foo.view", "group": fake_app_id, "label": "Foo"}],
        source=SOURCE_INGESTION,
        app_id=fake_app_id,
    )
    await deactivate_permissions(fake_app_id, set())

    system_keys_after = await get_valid_keys()
    # Every system key that existed before should still be valid.
    assert system_keys_before.issubset(system_keys_after)

    # cleanup
    db = get_db()
    await db.permissions.delete_many({"app_id": fake_app_id})


@pytest.mark.asyncio
async def test_deactivate_other_app_unaffected(fake_app_id):
    """Deactivating app A must not touch app B's permissions."""
    other_app = f"app_other_{uuid.uuid4().hex[:8]}"

    await register_permissions(
        [{"key": f"{fake_app_id}.x.view", "group": fake_app_id, "label": "X"}],
        source=SOURCE_INGESTION,
        app_id=fake_app_id,
    )
    await register_permissions(
        [{"key": f"{other_app}.y.view", "group": other_app, "label": "Y"}],
        source=SOURCE_INGESTION,
        app_id=other_app,
    )

    await deactivate_permissions(fake_app_id, set())

    other_active = await get_all_permissions(source=SOURCE_INGESTION, app_id=other_app, is_active=True)
    assert any(p["key"] == f"{other_app}.y.view" for p in other_active)

    # cleanup
    db = get_db()
    await db.permissions.delete_many({"app_id": {"$in": [fake_app_id, other_app]}})


# ---------------------------------------------------------------------------
# Role validation accepts ingested keys
# ---------------------------------------------------------------------------

class TestIngestedKeyAcceptanceInRoles:
    """After register_permissions runs, role create/update should accept the keys."""

    def test_create_role_with_freshly_registered_ingested_key(self, admin_session):
        """Register a fake ingested permission, then create a role using it."""
        fake_app = f"app_test_role_{uuid.uuid4().hex[:8]}"
        fake_key = f"{fake_app}.thing.view"

        # Register via direct service call (would normally happen via apply_rbac)
        async def register():
            await register_permissions(
                [{"key": fake_key, "group": fake_app, "label": "Thing"}],
                source=SOURCE_INGESTION,
                app_id=fake_app,
            )

        asyncio.get_event_loop().run_until_complete(register())

        try:
            unique_role_name = f"test_ingested_role_{uuid.uuid4().hex[:8]}"
            resp = admin_session.post(f"{BASE_URL}/api/roles", json={
                "name": unique_role_name,
                "display_name": "Ingested Permission Role",
                "permissions": [fake_key, "deals.view.own"],
                "color": "#777777",
            })
            assert resp.status_code in (200, 201), (
                f"Role creation should accept registered ingested key: {resp.text}"
            )
            role_id = resp.json()["role_id"]
            admin_session.delete(f"{BASE_URL}/api/roles/{role_id}")
        finally:
            async def cleanup():
                db = get_db()
                await db.permissions.delete_many({"app_id": fake_app})
            asyncio.get_event_loop().run_until_complete(cleanup())


# ---------------------------------------------------------------------------
# clone_role drops keys not in the registry
# ---------------------------------------------------------------------------

class TestCloneRoleDropsStaleKeys:
    def test_clone_filters_out_unregistered_keys(self, admin_session):
        """clone_role should silently drop permission keys that aren't in the registry."""
        # Create a role with a known-good permission, then sneak in an
        # unregistered key directly via the DB (simulating a permission that
        # was deactivated after the role was created).
        unique_role_name = f"test_clone_src_{uuid.uuid4().hex[:8]}"
        create = admin_session.post(f"{BASE_URL}/api/roles", json={
            "name": unique_role_name,
            "display_name": "Clone Source",
            "permissions": ["deals.view.own"],
            "color": "#888888",
        })
        assert create.status_code in (200, 201)
        role_id = create.json()["role_id"]

        # Directly add an unregistered permission to this role to simulate a
        # post-creation drift (e.g., permission deactivated after assignment).
        stale_key = f"deactivated.app.action_{uuid.uuid4().hex[:6]}"

        async def inject_stale():
            db = get_db()
            await db.roles.update_one(
                {"role_id": role_id},
                {"$push": {"permissions": stale_key}},
            )

        asyncio.get_event_loop().run_until_complete(inject_stale())

        try:
            clone_resp = admin_session.post(
                f"{BASE_URL}/api/roles/{role_id}/clone",
                json={"name": f"{unique_role_name}_clone"},
            )
            assert clone_resp.status_code in (200, 201)
            cloned = clone_resp.json()
            assert "deals.view.own" in cloned["permissions"]
            assert stale_key not in cloned["permissions"], (
                f"clone_role should have stripped unregistered key {stale_key}"
            )
            admin_session.delete(f"{BASE_URL}/api/roles/{cloned['role_id']}")
        finally:
            admin_session.delete(f"{BASE_URL}/api/roles/{role_id}")

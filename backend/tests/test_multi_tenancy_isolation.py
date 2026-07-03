"""Phase 1B.6 + Phase 1C.5 — Multi-tenancy acceptance tests.

Locks in the manual proof from the 1B.5 walkthrough plus every 1C
decision from the May 13 plan. An Acme tenant sharing a database with
the SimpliMeta root tenant cannot see, route to, or operate on root's
AI Employees, and vice versa. Tenant-side delegation and provisioning
work where they should and fail where they should, with chain
validation preventing orphan delegations.

If anyone weakens the tenant gate in marketplace.py, ai_employees.py,
or ai_employee_catalog.py, OR breaks the 1C ownership/chain rules, one
of these tests will fail in CI.

## What's covered

### Phase 1B (locked May 8, 2026)

* **Catalog visibility** — definitions are public but filtered for non-root.
* **Assignment scoping** — read access scoped to caller's tenant for non-root.
* **Provisioning correctness** — linkage stamped, defaults lift, caps fire.
* **Revocation semantics** — audit fields preserved, listings retain revoked.
* **Instance-level isolation** — list filters, single-get returns 404
  cross-tenant.

### Phase 1C (locked May 13, 2026)

* **Tenant-side provisioning (1C.3)** — tenant_admins can provision
  instances within their own tenant; cross-tenant attempts return 403;
  missing `.provision` perm returns 403.

* **Tenant-side delegation (1C.3)** — tenant_admins can delegate
  sub-assignments to child tenants if they hold the parent assignment;
  delegation to self / non-children / unheld definitions all blocked.

* **Tenant-side revoke (1C.4)** — tenant_admins can revoke delegations
  they created; the ownership rule blocks revocation of upstream
  grants; revoke is blocked with 409 when active sub-delegations exist
  downstream; the same chain check applies to root callers.

* **Endpoint-level defense in depth (1C.1)** — cross-tenant access to
  the 11 hardened endpoints on routes/ai_employees.py returns 404 (not
  403) to avoid leaking existence; POST cannot inject foreign tenant_id.

## What's NOT covered

The actual AI chat routing surface — the marketplace registry and the
conductor's routing decisions. The 1B.3 smoke test in our 8 May session
exercised this end-to-end with the in-process registry; replicating it
in pytest would require standing up the agents subsystem with a real
LLM. The route-handler tests in this file cover the data-surface gate
(which is what the chat router consults to decide who's reachable).

Refs: Phase 1B Locked Decisions, 8 May 2026; Phase 1C plan, 13 May 2026.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from types import SimpleNamespace

from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient


# ---------------------------------------------------------------------------
# Constants and fake users
# ---------------------------------------------------------------------------

ROOT_TENANT_ID = "ten_simplimeta_root"
ACME_TENANT_ID = "ten_acme_demo"
GLOBEX_TENANT_ID = "ten_globex_demo"
# Phase 1C.5 — child of Acme for delegation-chain tests.
ACME_SUB_TENANT_ID = "ten_acme_sub"


def _make_user(
        *,
        tenant_id: str,
        user_id: str,
        role: str = "admin",
        permissions=None,
        email: str = None,
):
    """Build a fake user object that quacks like get_current_user's output.

    Tests intentionally construct the user manually (rather than going through
    the auth flow) so each test isolates the authorization surface from
    JWT/session machinery.
    """
    return SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        permissions=permissions if permissions is not None else ["*"],
        email=email if email is not None else f"{user_id}@test.local",
    )


# Tenant_admin's actual permission set per the role definition in
# routes/roles.py. Phase 1B.5 seeded a read-only set; Phase 1C.2
# expanded it with `.delegate` and `.provision` for tenant-side
# delegation and provisioning. Tests use this constant (rather than
# hardcoded perm lists per test) so a future role expansion only
# requires updating one place.
TENANT_ADMIN_PERMS = [
    "chat.access",
    "ai_employee_catalog.view",
    "ai_employee_assignments.view",
    "ai_employee_assignments.delegate",  # Phase 1C.2
    "ai_employees.view",
    "ai_employee_instances.provision",   # Phase 1C.2
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def mock_db(monkeypatch):
    """A fresh in-memory Mongo database per test, with production indexes
    created so the unique constraints we rely on (compound (tenant, definition)
    on assignments, etc.) actually fire.

    The route modules use ``from database import get_db`` which binds
    ``get_db`` statically at module-load time. Reassigning
    ``database.get_db`` via monkeypatch would NOT update those stale
    bindings — they'd keep pointing at the original function. Instead we
    patch ``database._db`` (the module-level global the unchanged
    ``get_db`` reads from), so route modules see the test DB through
    their existing import.
    """
    db = AsyncMongoMockClient().test_db

    # Mirror the indexes created in server.py at startup, minus the ones
    # irrelevant to the multi-tenancy surface. mongomock-motor enforces
    # uniqueness when the index exists at insert time.
    await db.tenant_app_assignments.create_index("assignment_id", unique=True)
    await db.tenant_app_assignments.create_index(
        [("tenant_id", 1), ("definition_id", 1)], unique=True
    )
    await db.ai_employee_definitions.create_index("definition_id", unique=True)
    await db.ai_employees.create_index("employee_id", unique=True)

    # Patch database._db so get_db() returns this mock per-test.
    # monkeypatch reverts at teardown so test isolation is preserved.
    import database
    monkeypatch.setattr(database, "_db", db)

    # Stub the marketplace registration. The real `register_ai_employee`
    # connects to the agents subsystem (registry + conductor) — pulling that
    # in for a data-surface test would balloon scope. The chat-routing path
    # is covered by manual smoke tests; this file scopes to the REST surface.
    import services.ai.agents.marketplace as marketplace
    async def _noop_register(db, employee_doc):
        return None
    monkeypatch.setattr(marketplace, "register_ai_employee", _noop_register)

    return db


@pytest_asyncio.fixture
async def seeded_db(mock_db):
    """mock_db + the 2 root-tenant employees that exist in localhost/dev/prod,
    + the 1B.4 migration run to populate definitions and assignments.

    After this fixture, the DB looks exactly like a freshly-migrated
    production environment: 2 employees, 2 definitions, 2 assignments,
    all in `ten_simplimeta_root`.
    """
    db = mock_db

    # The root tenant must exist for assignment-grant validation to pass
    await db.tenants.insert_one({
        "tenant_id": ROOT_TENANT_ID,
        "name": "SimpliMeta",
        "slug": "simplimeta",
        "tenant_type": "root",
        "status": "active",
    })

    # Seed the two production employees (Nova + Nirel's Email Classifier)
    await db.ai_employees.insert_many([
        {
            "employee_id": "ai_nova",
            "name": "Nova",
            "role": "Financial Advisor",
            "type": "conversational",
            "system_prompt": "You are Nova, a financial advisor...",
            "router_description": "questions about finance, invoices, P&L",
            "model_provider": "google",
            "model_name": "gemini-2.5-flash",
            "specialties": ["finance"],
        },
        {
            "employee_id": "ai_nirel",
            "name": "Nirel's Email Classifier",
            "role": "Email Classification AI",
            "type": "n8n",
            "specialties": ["Email Classification", "Label Assignment", "Inbox Organization"],
            "metrics_config": {
                "metric1_label": "Emails Classified",
                "metric2_label": "Success Rate",
                "metric3_label": "Avg Processing Time",
                "source": "n8n",
            },
        },
    ])

    # Run the 1B.4 catalog migration (lifts the employees into definitions +
    # assignments + stamps linkage). The hook in server.py will be removed
    # in this same commit, but the seed function itself stays and is
    # callable on-demand. We use it here exactly as production used to on
    # every boot.
    from seeds.seed_ai_employee_catalog import seed_ai_employee_catalog
    await seed_ai_employee_catalog(db)

    return db


@pytest_asyncio.fixture
async def acme_seeded_db(seeded_db):
    """seeded_db + an Acme tenant + an Acme tenant_admin user + an active
    assignment of Nova to Acme + one provisioned instance.

    Used by tests that need a fully-staged Acme tenant ready to operate on.
    """
    db = seeded_db

    # Create the Acme tenant
    await db.tenants.insert_one({
        "tenant_id": ACME_TENANT_ID,
        "name": "Acme Demo Corp",
        "slug": "acme-demo",
        "tenant_type": "account",
        "parent_tenant_id": None,
        "status": "active",
    })

    # Grant Nova to Acme. Pull Nova's definition_id from the migration
    # output rather than hardcoding — keeps the fixture robust to changes
    # in how IDs are generated.
    nova_def = await db.ai_employee_definitions.find_one(
        {"name": "Nova"}, {"_id": 0}
    )
    assert nova_def, "Migration should have produced a Nova definition"

    from routes.ai_employee_catalog import create_assignment
    from models import TenantAppAssignmentCreate
    root_admin = _make_user(tenant_id=ROOT_TENANT_ID, user_id="user_root_admin")
    assignment = await create_assignment(
        ACME_TENANT_ID,
        TenantAppAssignmentCreate(
            definition_id=nova_def["definition_id"],
            tenant_id=ACME_TENANT_ID,
            max_instances=3,
            notes="Test fixture grant",
        ),
        current_user=root_admin,
    )

    # Provision one instance via the API (not direct insert) so we
    # exercise the same code path the demo flow uses. allowed_roles
    # explicitly includes tenant_admin: the catalog handler defaults to
    # ["admin"] which would hide instances from tenant_admins via the
    # secondary access-control filter in routes/ai_employees.py. That
    # filter exists for fine-grained within-tenant gating and is
    # orthogonal to the tenant gate this test file covers.
    from routes.ai_employee_catalog import provision_instance
    instance = await provision_instance(
        tenant_id=ACME_TENANT_ID,
        assignment_id=assignment["assignment_id"],
        overrides={
            "name": "Acme's Nova",
            "allowed_roles": ["admin", "tenant_admin"],
        },
        current_user=root_admin,
    )

    # Stash the structured artifacts on the db object for tests to grab
    db._fixtures = {
        "nova_def": nova_def,
        "acme_assignment": assignment,
        "acme_instance": instance,
    }
    return db


@pytest_asyncio.fixture
async def chain_seeded_db(acme_seeded_db):
    """acme_seeded_db + an AcmeSub child tenant + an Acme→AcmeSub delegation
    of Nova + one AcmeSub-provisioned instance.

    Phase 1C.5 — used by tests that exercise the multi-level delegation
    chain. The fixture exercises the actual 1C.3 delegation route (NOT
    direct DB inserts) so if 1C.3 is broken, the fixture itself fails
    fast — built-in smoke test on top of explicit assertions.

    Layout post-fixture:

        ROOT (SimpliMeta)
         └── Acme  ← granted Nova by root (acme_assignment, from acme_seeded_db)
              └── AcmeSub  ← delegated Nova by Acme (sub_assignment)
                   └── one provisioned instance ("AcmeSub's Nova")
    """
    db = acme_seeded_db

    # Create the AcmeSub tenant as a child of Acme. Tenant hierarchy
    # rules in routes/tenants.py allow `account` tenants to parent
    # under other `account` tenants in the test/demo path; the actual
    # production constraint is partner→account, but the chain check in
    # 1C.3 only reads parent_tenant_id and doesn't require partner
    # ancestry, so account→account works here.
    await db.tenants.insert_one({
        "tenant_id": ACME_SUB_TENANT_ID,
        "name": "Acme Subsidiary",
        "slug": "acme-sub",
        "tenant_type": "account",
        "parent_tenant_id": ACME_TENANT_ID,
        "status": "active",
    })

    # Acme admin (now with .delegate from the 1C.2-expanded role)
    # delegates Nova down to AcmeSub. We go through the actual route
    # so the chain validation (target is a child, caller holds the
    # parent assignment) fires the same way it would in production.
    from routes.ai_employee_catalog import create_assignment, provision_instance
    from models import TenantAppAssignmentCreate
    acme_admin = _make_user(
        tenant_id=ACME_TENANT_ID, user_id="acme_admin",
        role="tenant_admin", permissions=TENANT_ADMIN_PERMS,
    )
    sub_assignment = await create_assignment(
        ACME_SUB_TENANT_ID,
        TenantAppAssignmentCreate(
            definition_id=db._fixtures["nova_def"]["definition_id"],
            tenant_id=ACME_SUB_TENANT_ID,
            max_instances=2,
            notes="Delegated by Acme to AcmeSub",
        ),
        current_user=acme_admin,
    )

    # AcmeSub admin provisions an instance from the sub-assignment.
    # Goes through the route so the own-tenant guard from 1C.3 fires.
    acme_sub_admin = _make_user(
        tenant_id=ACME_SUB_TENANT_ID, user_id="acme_sub_admin",
        role="tenant_admin", permissions=TENANT_ADMIN_PERMS,
    )
    sub_instance = await provision_instance(
        tenant_id=ACME_SUB_TENANT_ID,
        assignment_id=sub_assignment["assignment_id"],
        overrides={
            "name": "AcmeSub's Nova",
            "allowed_roles": ["admin", "tenant_admin"],
        },
        current_user=acme_sub_admin,
    )

    db._fixtures["sub_assignment"] = sub_assignment
    db._fixtures["sub_instance"] = sub_instance
    db._fixtures["acme_admin"] = acme_admin
    db._fixtures["acme_sub_admin"] = acme_sub_admin
    return db


# ---------------------------------------------------------------------------
# Catalog visibility
# ---------------------------------------------------------------------------

class TestCatalogVisibility:
    """The catalog (definitions) is a public surface — every authenticated
    user can browse it. Drafts and archives are hidden from non-root."""

    @pytest.mark.asyncio
    async def test_root_admin_sees_all_definitions(self, seeded_db):
        from routes.ai_employee_catalog import list_definitions
        user = _make_user(tenant_id=ROOT_TENANT_ID, user_id="u1")
        defs = await list_definitions(current_user=user)
        assert len(defs) == 2
        names = sorted(d["name"] for d in defs)
        assert names == ["Nirel's Email Classifier", "Nova"]

    @pytest.mark.asyncio
    async def test_root_admin_sees_drafts(self, seeded_db, monkeypatch):
        """Inject a draft definition; root should see it, non-root should not."""
        from routes.ai_employee_catalog import list_definitions
        await seeded_db.ai_employee_definitions.insert_one({
            "definition_id": "def_secret_draft",
            "name": "Secret Draft Bot",
            "role": "Future AI",
            "type": "conversational",
            "is_active": True,
            "is_published": False,  # ← draft
            "created_by_tenant_id": ROOT_TENANT_ID,
        })

        root = _make_user(tenant_id=ROOT_TENANT_ID, user_id="root_u")
        acme = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_u",
            role="tenant_admin", permissions=TENANT_ADMIN_PERMS,
        )

        root_defs = await list_definitions(current_user=root)
        acme_defs = await list_definitions(current_user=acme)

        assert any(d["name"] == "Secret Draft Bot" for d in root_defs)
        assert not any(d["name"] == "Secret Draft Bot" for d in acme_defs)

    @pytest.mark.asyncio
    async def test_non_root_cannot_see_archived(self, seeded_db):
        """is_active=False = archived blueprint. Hidden from non-root."""
        from routes.ai_employee_catalog import list_definitions
        await seeded_db.ai_employee_definitions.insert_one({
            "definition_id": "def_archived",
            "name": "Old Retired Bot",
            "role": "Legacy",
            "type": "conversational",
            "is_active": False,
            "is_published": True,
            "created_by_tenant_id": ROOT_TENANT_ID,
        })

        acme = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_u",
            role="tenant_admin", permissions=TENANT_ADMIN_PERMS,
        )
        defs = await list_definitions(current_user=acme)
        assert not any(d["name"] == "Old Retired Bot" for d in defs)

    @pytest.mark.asyncio
    async def test_definition_detail_hides_drafts_from_non_root(self, seeded_db):
        from routes.ai_employee_catalog import get_definition
        await seeded_db.ai_employee_definitions.insert_one({
            "definition_id": "def_draft_xyz",
            "name": "Draft Bot",
            "role": "X",
            "type": "conversational",
            "is_active": True,
            "is_published": False,
            "created_by_tenant_id": ROOT_TENANT_ID,
        })

        acme = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_u",
            role="tenant_admin", permissions=TENANT_ADMIN_PERMS,
        )
        # Should 404, not 403 — we don't want to leak existence
        with pytest.raises(HTTPException) as exc:
            await get_definition("def_draft_xyz", current_user=acme)
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Assignment scoping
# ---------------------------------------------------------------------------

class TestAssignmentScoping:

    @pytest.mark.asyncio
    async def test_acme_admin_sees_own_assignments(self, acme_seeded_db):
        from routes.ai_employee_catalog import list_assignments
        acme = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_admin",
            role="tenant_admin", permissions=TENANT_ADMIN_PERMS,
        )
        asns = await list_assignments(ACME_TENANT_ID, current_user=acme)
        assert len(asns) == 1
        assert asns[0]["tenant_id"] == ACME_TENANT_ID

    @pytest.mark.asyncio
    async def test_acme_admin_blocked_from_reading_root_assignments(
            self, acme_seeded_db
    ):
        """The headline isolation claim: Acme can't peek at root's data."""
        from routes.ai_employee_catalog import list_assignments
        acme = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_admin",
            role="tenant_admin", permissions=TENANT_ADMIN_PERMS,
        )
        with pytest.raises(HTTPException) as exc:
            await list_assignments(ROOT_TENANT_ID, current_user=acme)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_root_admin_reads_any_tenant_assignments(self, acme_seeded_db):
        from routes.ai_employee_catalog import list_assignments
        root = _make_user(tenant_id=ROOT_TENANT_ID, user_id="root_admin")
        # Both tenants' assignments are readable
        root_asns = await list_assignments(ROOT_TENANT_ID, current_user=root)
        acme_asns = await list_assignments(ACME_TENANT_ID, current_user=root)
        assert len(root_asns) == 2  # migration-origin (Nova + Nirel)
        assert len(acme_asns) == 1  # fixture grant


# ---------------------------------------------------------------------------
# Write authorization (tenant-side writes are root-only in 1B.5)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Provisioning correctness
# ---------------------------------------------------------------------------

class TestProvisioningCorrectness:

    @pytest.mark.asyncio
    async def test_provisioned_instance_has_full_linkage(self, acme_seeded_db):
        """All three linkage fields stamped at provision time, never null."""
        instance = acme_seeded_db._fixtures["acme_instance"]
        assert instance["tenant_id"] == ACME_TENANT_ID
        assert instance["definition_id"] == acme_seeded_db._fixtures["nova_def"]["definition_id"]
        assert instance["assignment_id"] == acme_seeded_db._fixtures["acme_assignment"]["assignment_id"]

    @pytest.mark.asyncio
    async def test_backing_user_shares_instance_tenant(self, acme_seeded_db):
        """The 1B.5 backing-user alignment: marketplace tenant gate sees
        AI-sourced requests as belonging to the instance's tenant, not root."""
        instance = acme_seeded_db._fixtures["acme_instance"]
        backing = await acme_seeded_db.users.find_one(
            {"user_id": instance["user_id"]}, {"_id": 0}
        )
        assert backing is not None
        assert backing["tenant_id"] == ACME_TENANT_ID, (
            f"Backing user must share tenant_id with its AI employee. "
            f"Got backing.tenant_id={backing.get('tenant_id')!r}, "
            f"instance.tenant_id={instance['tenant_id']!r}"
        )

    @pytest.mark.asyncio
    async def test_definition_defaults_lift_to_instance(self, acme_seeded_db):
        instance = acme_seeded_db._fixtures["acme_instance"]
        # Nova's `system_prompt` from the seed is "You are Nova, a financial advisor..."
        # The 1B.4 migration lifted it into `default_system_prompt` on the
        # definition; provision_instance should copy it back to `system_prompt`
        # on the instance (since we didn't pass an override).
        assert instance.get("system_prompt") == "You are Nova, a financial advisor..."
        # model_provider/model_name also lifted
        assert instance.get("model_provider") == "google"
        assert instance.get("model_name") == "gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_overrides_take_precedence_over_defaults(self, acme_seeded_db):
        """Caller-supplied overrides must win; the fixture passed
        `name="Acme's Nova"` so the default "Nova" should be replaced."""
        instance = acme_seeded_db._fixtures["acme_instance"]
        assert instance["name"] == "Acme's Nova"

    @pytest.mark.asyncio
    async def test_max_instances_cap_enforced(self, acme_seeded_db):
        """Fixture's assignment has max_instances=3, one already provisioned.
        Two more should succeed; the fourth should 409."""
        from routes.ai_employee_catalog import provision_instance
        root = _make_user(tenant_id=ROOT_TENANT_ID, user_id="root_admin")
        asn_id = acme_seeded_db._fixtures["acme_assignment"]["assignment_id"]

        # 2nd + 3rd succeed
        await provision_instance(ACME_TENANT_ID, asn_id, overrides={"name": "A2"}, current_user=root)
        await provision_instance(ACME_TENANT_ID, asn_id, overrides={"name": "A3"}, current_user=root)

        # 4th hits the cap
        with pytest.raises(HTTPException) as exc:
            await provision_instance(ACME_TENANT_ID, asn_id, overrides={"name": "A4"}, current_user=root)
        assert exc.value.status_code == 409
        assert "max_instances" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_double_grant_blocked_by_unique_index(self, seeded_db):
        from routes.ai_employee_catalog import create_assignment
        from models import TenantAppAssignmentCreate

        await seeded_db.tenants.insert_one({
            "tenant_id": ACME_TENANT_ID, "name": "Acme",
            "slug": "acme", "tenant_type": "account", "status": "active",
        })
        nova_def = await seeded_db.ai_employee_definitions.find_one(
            {"name": "Nova"}, {"_id": 0}
        )
        root = _make_user(tenant_id=ROOT_TENANT_ID, user_id="root")

        # First grant succeeds
        first = await create_assignment(
            ACME_TENANT_ID,
            TenantAppAssignmentCreate(
                definition_id=nova_def["definition_id"], tenant_id=ACME_TENANT_ID
            ),
            current_user=root,
        )
        # Second fails with 409 + pointer to existing
        with pytest.raises(HTTPException) as exc:
            await create_assignment(
                ACME_TENANT_ID,
                TenantAppAssignmentCreate(
                    definition_id=nova_def["definition_id"], tenant_id=ACME_TENANT_ID
                ),
                current_user=root,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["existing_assignment_id"] == first["assignment_id"]


# ---------------------------------------------------------------------------
# Revocation semantics
# ---------------------------------------------------------------------------

class TestRevocationSemantics:

    @pytest.mark.asyncio
    async def test_revoked_assignment_retains_audit_fields(self, acme_seeded_db):
        from routes.ai_employee_catalog import update_assignment
        from models import TenantAppAssignmentUpdate
        root = _make_user(tenant_id=ROOT_TENANT_ID, user_id="root")

        asn = acme_seeded_db._fixtures["acme_assignment"]
        original_granted_by = asn["granted_by_user_id"]
        original_granted_at = asn["granted_at"]

        revoked = await update_assignment(
            ACME_TENANT_ID, asn["assignment_id"],
            TenantAppAssignmentUpdate(status="revoked", notes="No longer needed"),
            current_user=root,
        )

        assert revoked["status"] == "revoked"
        assert revoked["revoked_at"] is not None
        # Audit fields preserved. MongoDB stores datetimes at millisecond
        # precision and round-trips through BSON drop tzinfo, so we
        # compare via the second-level delta rather than ==.
        assert revoked["granted_by_user_id"] == original_granted_by
        delta = abs(
            (revoked["granted_at"].replace(tzinfo=None) -
             original_granted_at.replace(tzinfo=None)).total_seconds()
        )
        assert delta < 1, (
            f"granted_at drifted by >1s during revoke; "
            f"before={original_granted_at}, after={revoked['granted_at']}"
        )
        assert revoked["notes"] == "No longer needed"

    @pytest.mark.asyncio
    async def test_cannot_provision_against_revoked_assignment(self, acme_seeded_db):
        from routes.ai_employee_catalog import update_assignment, provision_instance
        from models import TenantAppAssignmentUpdate
        root = _make_user(tenant_id=ROOT_TENANT_ID, user_id="root")

        asn_id = acme_seeded_db._fixtures["acme_assignment"]["assignment_id"]
        await update_assignment(
            ACME_TENANT_ID, asn_id,
            TenantAppAssignmentUpdate(status="revoked"),
            current_user=root,
        )

        with pytest.raises(HTTPException) as exc:
            await provision_instance(ACME_TENANT_ID, asn_id, current_user=root)
        assert exc.value.status_code == 400
        assert "not active" in str(exc.value.detail).lower()

    @pytest.mark.asyncio
    async def test_revoked_assignment_still_listed(self, acme_seeded_db):
        """Soft revocation: the assignment stays in listings (audit trail)
        with status='revoked' rather than being deleted."""
        from routes.ai_employee_catalog import update_assignment, list_assignments
        from models import TenantAppAssignmentUpdate
        root = _make_user(tenant_id=ROOT_TENANT_ID, user_id="root")

        asn_id = acme_seeded_db._fixtures["acme_assignment"]["assignment_id"]
        await update_assignment(
            ACME_TENANT_ID, asn_id,
            TenantAppAssignmentUpdate(status="revoked"),
            current_user=root,
        )

        asns = await list_assignments(ACME_TENANT_ID, current_user=root)
        assert len(asns) == 1
        assert asns[0]["assignment_id"] == asn_id
        assert asns[0]["status"] == "revoked"


# ---------------------------------------------------------------------------
# Instance-level isolation (the headline 1B.3 claim, re-verified)
# ---------------------------------------------------------------------------

class TestInstanceIsolation:

    @pytest.mark.asyncio
    async def test_root_admin_does_not_see_acme_instance_in_list(
            self, acme_seeded_db
    ):
        """The proof we walked through manually in the 1B.3+1B.5 sessions:
        the root admin's AI Employees page does NOT include Acme's instances."""
        from routes.ai_employees import list_ai_employees
        root = _make_user(tenant_id=ROOT_TENANT_ID, user_id="root_admin")
        employees = await list_ai_employees(current_user=root)

        acme_employee_id = acme_seeded_db._fixtures["acme_instance"]["employee_id"]
        names = [e["name"] for e in employees]
        ids = [e["employee_id"] for e in employees]

        assert acme_employee_id not in ids, (
            f"Acme's instance leaked into root admin's list. "
            f"Got names={names}, expected only root-tenant employees."
        )
        # Root should still see Nova + Nirel (their own tenant's)
        assert "Nova" in names
        assert "Nirel's Email Classifier" in names

    @pytest.mark.asyncio
    async def test_acme_admin_does_not_see_root_instances_in_list(
            self, acme_seeded_db
    ):
        """The mirror claim: Acme's view excludes root's Nova + Nirel."""
        from routes.ai_employees import list_ai_employees
        acme = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_admin",
            role="tenant_admin", permissions=TENANT_ADMIN_PERMS,
        )
        employees = await list_ai_employees(current_user=acme)
        names = [e["name"] for e in employees]

        assert "Nova" not in names
        assert "Nirel's Email Classifier" not in names
        assert "Acme's Nova" in names  # Their own instance is visible

    @pytest.mark.asyncio
    async def test_cross_tenant_single_get_returns_404_not_403(
            self, acme_seeded_db
    ):
        """Acme admin trying to GET root's Nova by employee_id should 404,
        not 403 — we don't want to leak the existence of cross-tenant rows."""
        from routes.ai_employees import get_ai_employee
        acme = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_admin",
            role="tenant_admin", permissions=TENANT_ADMIN_PERMS,
        )
        with pytest.raises(HTTPException) as exc:
            await get_ai_employee("ai_nova", current_user=acme)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_three_tenant_pairwise_isolation(self, seeded_db):
        """Sanity: with three tenants in the same DB, each sees only their own.
        Catches any accidental coupling between two specific tenant_ids."""
        from routes.ai_employees import list_ai_employees
        from routes.ai_employee_catalog import create_assignment, provision_instance
        from models import TenantAppAssignmentCreate

        # Set up Acme and Globex tenants
        await seeded_db.tenants.insert_many([
            {
                "tenant_id": ACME_TENANT_ID, "name": "Acme", "slug": "acme",
                "tenant_type": "account", "status": "active",
            },
            {
                "tenant_id": GLOBEX_TENANT_ID, "name": "Globex", "slug": "globex",
                "tenant_type": "account", "status": "active",
            },
        ])
        nova_def = await seeded_db.ai_employee_definitions.find_one(
            {"name": "Nova"}, {"_id": 0}
        )
        root = _make_user(tenant_id=ROOT_TENANT_ID, user_id="root")

        # Grant Nova to both Acme and Globex
        for tenant_id in (ACME_TENANT_ID, GLOBEX_TENANT_ID):
            asn = await create_assignment(
                tenant_id,
                TenantAppAssignmentCreate(
                    definition_id=nova_def["definition_id"], tenant_id=tenant_id
                ),
                current_user=root,
            )
            await provision_instance(
                tenant_id, asn["assignment_id"],
                overrides={
                    "name": f"{tenant_id}'s Nova",
                    "allowed_roles": ["admin", "tenant_admin"],
                },
                current_user=root,
            )

        # Each tenant sees ONLY their own instance
        acme_user = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_u",
            role="tenant_admin", permissions=TENANT_ADMIN_PERMS,
        )
        globex_user = _make_user(
            tenant_id=GLOBEX_TENANT_ID, user_id="globex_u",
            role="tenant_admin", permissions=TENANT_ADMIN_PERMS,
        )

        acme_employees = await list_ai_employees(current_user=acme_user)
        globex_employees = await list_ai_employees(current_user=globex_user)
        acme_names = [e["name"] for e in acme_employees]
        globex_names = [e["name"] for e in globex_employees]

        assert f"{ACME_TENANT_ID}'s Nova" in acme_names
        assert f"{ACME_TENANT_ID}'s Nova" not in globex_names
        assert f"{GLOBEX_TENANT_ID}'s Nova" in globex_names
        assert f"{GLOBEX_TENANT_ID}'s Nova" not in acme_names
        # And neither sees root's Nova/Nirel
        assert "Nova" not in acme_names
        assert "Nova" not in globex_names


# ===========================================================================
# Phase 1C tests
# ===========================================================================
# Everything below lands in 1C.5. The classes above (TestCatalogVisibility,
# TestAssignmentScoping, TestProvisioningCorrectness, TestRevocationSemantics,
# TestInstanceIsolation) are the 1B.6 surface; the classes below cover 1C
# decisions: tenant-side provisioning (1C.3), tenant-side delegation (1C.3),
# tenant-side revoke with chain check (1C.4), and endpoint-level defense in
# depth (1C.1).


# ---------------------------------------------------------------------------
# Tenant-side provisioning (Phase 1C.3)
# ---------------------------------------------------------------------------

class TestTenantSideProvisioning:
    """Phase 1C.3 opens POST /tenants/{tenant_id}/assignments/{id}/instances
    to tenant_admins for their own tenant. Root still works for any tenant.
    Foreign-tenant attempts and missing-perm callers get 403."""

    @pytest.mark.asyncio
    async def test_acme_admin_can_provision_own_tenant_instance(
            self, acme_seeded_db
    ):
        """The headline 1C.3 positive path: a tenant_admin in Acme,
        holding ai_employee_instances.provision, can provision an
        instance against an assignment granted to Acme."""
        from routes.ai_employee_catalog import provision_instance
        acme_admin = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_admin",
            role="tenant_admin", permissions=TENANT_ADMIN_PERMS,
        )
        instance = await provision_instance(
            tenant_id=ACME_TENANT_ID,
            assignment_id=acme_seeded_db._fixtures["acme_assignment"]["assignment_id"],
            overrides={
                "name": "Acme's Second Nova",
                "allowed_roles": ["admin", "tenant_admin"],
            },
            current_user=acme_admin,
        )
        assert instance["tenant_id"] == ACME_TENANT_ID
        assert instance["name"] == "Acme's Second Nova"
        # Linkage all stamped — the 1B.2 invariant survives 1C.3.
        assert instance["definition_id"] == acme_seeded_db._fixtures["nova_def"]["definition_id"]
        assert instance["assignment_id"] == acme_seeded_db._fixtures["acme_assignment"]["assignment_id"]

    @pytest.mark.asyncio
    async def test_acme_admin_cannot_provision_for_other_tenant(
            self, acme_seeded_db
    ):
        """Cross-tenant provisioning blocked by _require_own_tenant_or_root.
        Even though the assignment_id is valid in the DB, an Acme admin
        passing tenant_id=GLOBEX in the URL gets 403."""
        from routes.ai_employee_catalog import provision_instance
        # Set up a Globex assignment we'll try (and fail) to provision from.
        await acme_seeded_db.tenants.insert_one({
            "tenant_id": GLOBEX_TENANT_ID, "name": "Globex", "slug": "globex",
            "tenant_type": "account", "status": "active",
        })
        from routes.ai_employee_catalog import create_assignment
        from models import TenantAppAssignmentCreate
        root = _make_user(tenant_id=ROOT_TENANT_ID, user_id="root")
        globex_assignment = await create_assignment(
            GLOBEX_TENANT_ID,
            TenantAppAssignmentCreate(
                definition_id=acme_seeded_db._fixtures["nova_def"]["definition_id"],
                tenant_id=GLOBEX_TENANT_ID,
            ),
            current_user=root,
        )

        acme_admin = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_admin",
            role="tenant_admin", permissions=TENANT_ADMIN_PERMS,
        )
        with pytest.raises(HTTPException) as exc:
            await provision_instance(
                tenant_id=GLOBEX_TENANT_ID,
                assignment_id=globex_assignment["assignment_id"],
                current_user=acme_admin,
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_acme_admin_without_provision_perm_denied(
            self, acme_seeded_db
    ):
        """Defense-in-depth: even if the user is in Acme and the
        assignment is for Acme, missing the .provision perm gets 403.
        Locks in the permission-check-before-tenant-check ordering."""
        from routes.ai_employee_catalog import provision_instance
        # Strip the .provision perm from a stock tenant_admin set
        perms_without_provision = [
            p for p in TENANT_ADMIN_PERMS
            if p != "ai_employee_instances.provision"
        ]
        acme_admin = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_admin",
            role="tenant_admin", permissions=perms_without_provision,
        )
        with pytest.raises(HTTPException) as exc:
            await provision_instance(
                tenant_id=ACME_TENANT_ID,
                assignment_id=acme_seeded_db._fixtures["acme_assignment"]["assignment_id"],
                current_user=acme_admin,
            )
        assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Tenant-side delegation (Phase 1C.3)
# ---------------------------------------------------------------------------

class TestTenantSideDelegation:
    """Phase 1C.3 opens POST /tenants/{tenant_id}/assignments to tenant
    admins for delegation to child tenants. The chain validation enforces
    two rules: (1) target must be a direct child of caller's tenant, (2)
    caller must hold an active assignment for the same definition."""

    @pytest.mark.asyncio
    async def test_acme_admin_can_delegate_to_child(self, acme_seeded_db):
        """The headline 1C.3 delegation path. Acme holds Nova → Acme
        admin delegates Nova to AcmeSub. granted_by_tenant_id stamped
        to Acme, not root."""
        await acme_seeded_db.tenants.insert_one({
            "tenant_id": ACME_SUB_TENANT_ID, "name": "AcmeSub",
            "slug": "acme-sub", "tenant_type": "account",
            "parent_tenant_id": ACME_TENANT_ID, "status": "active",
        })
        from routes.ai_employee_catalog import create_assignment
        from models import TenantAppAssignmentCreate
        acme_admin = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_admin",
            role="tenant_admin", permissions=TENANT_ADMIN_PERMS,
        )
        sub_assignment = await create_assignment(
            ACME_SUB_TENANT_ID,
            TenantAppAssignmentCreate(
                definition_id=acme_seeded_db._fixtures["nova_def"]["definition_id"],
                tenant_id=ACME_SUB_TENANT_ID,
                max_instances=1,
            ),
            current_user=acme_admin,
        )
        assert sub_assignment["tenant_id"] == ACME_SUB_TENANT_ID
        # The audit trail: this delegation is attributable to Acme,
        # not root.
        assert sub_assignment["granted_by_tenant_id"] == ACME_TENANT_ID
        assert sub_assignment["granted_by_user_id"] == "acme_admin"
        assert sub_assignment["status"] == "active"

    @pytest.mark.asyncio
    async def test_acme_admin_cannot_delegate_to_self(self, acme_seeded_db):
        """Self-delegation has no semantic meaning and the chain check
        catches it: target.parent_tenant_id != caller.tenant_id (Acme's
        own parent_tenant_id is None, not ACME)."""
        from routes.ai_employee_catalog import create_assignment
        from models import TenantAppAssignmentCreate
        acme_admin = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_admin",
            role="tenant_admin", permissions=TENANT_ADMIN_PERMS,
        )
        with pytest.raises(HTTPException) as exc:
            await create_assignment(
                ACME_TENANT_ID,
                TenantAppAssignmentCreate(
                    definition_id=acme_seeded_db._fixtures["nova_def"]["definition_id"],
                    tenant_id=ACME_TENANT_ID,
                ),
                current_user=acme_admin,
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_acme_admin_cannot_delegate_to_non_child(
            self, acme_seeded_db
    ):
        """Cross-hierarchy delegation blocked. Globex is not a child
        of Acme so the parent_tenant_id check fails."""
        await acme_seeded_db.tenants.insert_one({
            "tenant_id": GLOBEX_TENANT_ID, "name": "Globex",
            "slug": "globex", "tenant_type": "account",
            "parent_tenant_id": None, "status": "active",
        })
        from routes.ai_employee_catalog import create_assignment
        from models import TenantAppAssignmentCreate
        acme_admin = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_admin",
            role="tenant_admin", permissions=TENANT_ADMIN_PERMS,
        )
        with pytest.raises(HTTPException) as exc:
            await create_assignment(
                GLOBEX_TENANT_ID,
                TenantAppAssignmentCreate(
                    definition_id=acme_seeded_db._fixtures["nova_def"]["definition_id"],
                    tenant_id=GLOBEX_TENANT_ID,
                ),
                current_user=acme_admin,
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_acme_admin_cannot_delegate_definition_they_dont_hold(
            self, acme_seeded_db
    ):
        """Create-side chain check: you can only delegate what you have.
        Acme holds Nova, not Nirel. Delegating Nirel to a child fails
        even though the parent_tenant_id check would pass."""
        await acme_seeded_db.tenants.insert_one({
            "tenant_id": ACME_SUB_TENANT_ID, "name": "AcmeSub",
            "slug": "acme-sub", "tenant_type": "account",
            "parent_tenant_id": ACME_TENANT_ID, "status": "active",
        })
        nirel_def = await acme_seeded_db.ai_employee_definitions.find_one(
            {"name": "Nirel's Email Classifier"}, {"_id": 0}
        )
        from routes.ai_employee_catalog import create_assignment
        from models import TenantAppAssignmentCreate
        acme_admin = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_admin",
            role="tenant_admin", permissions=TENANT_ADMIN_PERMS,
        )
        with pytest.raises(HTTPException) as exc:
            await create_assignment(
                ACME_SUB_TENANT_ID,
                TenantAppAssignmentCreate(
                    definition_id=nirel_def["definition_id"],
                    tenant_id=ACME_SUB_TENANT_ID,
                ),
                current_user=acme_admin,
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_root_can_still_grant_to_any_tenant(self, acme_seeded_db):
        """Sanity: 1C.3's split-by-caller-type doesn't break root. Root
        can still grant any definition to any tenant via .grant. No
        chain check fires for root callers (is_root_caller is True)."""
        # Create two unrelated tenants — Globex and AcmeSub. AcmeSub is
        # a child of Acme in the hierarchy, but that's irrelevant for
        # the root grant path which doesn't check parent_tenant_id.
        await acme_seeded_db.tenants.insert_many([
            {
                "tenant_id": GLOBEX_TENANT_ID, "name": "Globex",
                "slug": "globex", "tenant_type": "account",
                "parent_tenant_id": None, "status": "active",
            },
            {
                "tenant_id": ACME_SUB_TENANT_ID, "name": "AcmeSub",
                "slug": "acme-sub", "tenant_type": "account",
                "parent_tenant_id": ACME_TENANT_ID, "status": "active",
            },
        ])
        from routes.ai_employee_catalog import create_assignment
        from models import TenantAppAssignmentCreate
        root = _make_user(tenant_id=ROOT_TENANT_ID, user_id="root")

        # Root grants Nova to both. Both must succeed and carry
        # granted_by_tenant_id == ROOT.
        for tenant_id in (ACME_SUB_TENANT_ID, GLOBEX_TENANT_ID):
            asn = await create_assignment(
                tenant_id,
                TenantAppAssignmentCreate(
                    definition_id=acme_seeded_db._fixtures["nova_def"]["definition_id"],
                    tenant_id=tenant_id,
                ),
                current_user=root,
            )
            assert asn["tenant_id"] == tenant_id
            assert asn["granted_by_tenant_id"] == ROOT_TENANT_ID


# ---------------------------------------------------------------------------
# Tenant-side revoke with chain check (Phase 1C.4)
# ---------------------------------------------------------------------------

class TestTenantSideRevoke:
    """Phase 1C.4 — update_assignment opens to tenant admins for
    delegations they created. The ownership rule
    (granted_by_tenant_id == caller.tenant_id) blocks revocation of
    upstream grants. The chain check blocks revocation when active
    sub-delegations exist downstream, symmetrically for tenant and root
    callers."""

    @pytest.mark.asyncio
    async def test_acme_admin_can_revoke_own_delegation(self, chain_seeded_db):
        """Headline 1C.4 positive path: Acme delegated Nova to AcmeSub
        (via the fixture), then revokes that delegation. After revoking
        AcmeSub's instance first (so the chain check doesn't fire), the
        revoke succeeds."""
        from routes.ai_employee_catalog import update_assignment
        from models import TenantAppAssignmentUpdate

        # Chain check would block if we revoked the sub-assignment while
        # AcmeSub's instance exists pointing at it. The fixture provisioned
        # one — but the chain check looks at assignments, not instances.
        # The check is "are there ACTIVE SUB-ASSIGNMENTS under this", and
        # the sub-assignment IS the leaf here (no further delegation),
        # so revoking it doesn't trip the check.
        acme_admin = chain_seeded_db._fixtures["acme_admin"]
        result = await update_assignment(
            ACME_SUB_TENANT_ID,
            chain_seeded_db._fixtures["sub_assignment"]["assignment_id"],
            TenantAppAssignmentUpdate(status="revoked"),
            current_user=acme_admin,
        )
        assert result["status"] == "revoked"
        assert result["revoked_at"] is not None
        assert result["revoked_by_user_id"] == "acme_admin"
        assert result["revoked_by_tenant_id"] == ACME_TENANT_ID

    @pytest.mark.asyncio
    async def test_acme_admin_cannot_revoke_upstream_grant(
            self, acme_seeded_db
    ):
        """The load-bearing safety check: Acme admin tries to revoke
        the assignment ROOT granted to Acme. Ownership rule fires —
        existing.granted_by_tenant_id == ROOT, not ACME — so 403."""
        from routes.ai_employee_catalog import update_assignment
        from models import TenantAppAssignmentUpdate
        acme_admin = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_admin",
            role="tenant_admin", permissions=TENANT_ADMIN_PERMS,
        )
        with pytest.raises(HTTPException) as exc:
            await update_assignment(
                ACME_TENANT_ID,
                acme_seeded_db._fixtures["acme_assignment"]["assignment_id"],
                TenantAppAssignmentUpdate(status="revoked"),
                current_user=acme_admin,
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_revoke_blocked_when_active_sub_delegations_exist(
            self, chain_seeded_db
    ):
        """The chain check fires: Acme tries to revoke their own
        assignment (granted by root) — wait, that fails on ownership.
        Better test: ROOT tries to revoke Acme's assignment while Acme's
        sub-delegation to AcmeSub is still active. 409, list of blocks."""
        from routes.ai_employee_catalog import update_assignment
        from models import TenantAppAssignmentUpdate
        root = _make_user(tenant_id=ROOT_TENANT_ID, user_id="root")
        with pytest.raises(HTTPException) as exc:
            await update_assignment(
                ACME_TENANT_ID,
                chain_seeded_db._fixtures["acme_assignment"]["assignment_id"],
                TenantAppAssignmentUpdate(status="revoked"),
                current_user=root,
            )
        assert exc.value.status_code == 409
        # Body must surface the blocking sub-assignment id so the caller
        # knows what to revoke first.
        detail = exc.value.detail
        assert isinstance(detail, dict)
        assert "blocking_sub_assignments" in detail
        blocking_ids = [b["assignment_id"] for b in detail["blocking_sub_assignments"]]
        assert chain_seeded_db._fixtures["sub_assignment"]["assignment_id"] in blocking_ids

    @pytest.mark.asyncio
    async def test_root_revoke_also_blocked_by_chain_check(
            self, chain_seeded_db
    ):
        """Symmetry: root is not exempt from the chain check. Cascading
        destructive ops require explicit intent. Same 409 shape as a
        tenant-admin caller would get."""
        from routes.ai_employee_catalog import update_assignment
        from models import TenantAppAssignmentUpdate
        root = _make_user(tenant_id=ROOT_TENANT_ID, user_id="root")
        with pytest.raises(HTTPException) as exc:
            await update_assignment(
                ACME_TENANT_ID,
                chain_seeded_db._fixtures["acme_assignment"]["assignment_id"],
                TenantAppAssignmentUpdate(status="revoked"),
                current_user=root,
            )
        assert exc.value.status_code == 409
        # The "intentional design" beat: this is the same code path that
        # would block a tenant caller. No root special-case.
        assert "sub-delegation" in str(exc.value.detail).lower()

    @pytest.mark.asyncio
    async def test_revoke_succeeds_after_children_revoked(
            self, chain_seeded_db
    ):
        """The chain unblocks: revoke the sub-delegation first, then
        the parent revoke succeeds (whether the caller is root or the
        delegation owner)."""
        from routes.ai_employee_catalog import update_assignment
        from models import TenantAppAssignmentUpdate

        # Step 1: Acme revokes their delegation to AcmeSub
        acme_admin = chain_seeded_db._fixtures["acme_admin"]
        await update_assignment(
            ACME_SUB_TENANT_ID,
            chain_seeded_db._fixtures["sub_assignment"]["assignment_id"],
            TenantAppAssignmentUpdate(status="revoked"),
            current_user=acme_admin,
        )
        # Step 2: Now root can revoke Acme's parent assignment — chain
        # check finds zero ACTIVE sub-delegations.
        root = _make_user(tenant_id=ROOT_TENANT_ID, user_id="root")
        result = await update_assignment(
            ACME_TENANT_ID,
            chain_seeded_db._fixtures["acme_assignment"]["assignment_id"],
            TenantAppAssignmentUpdate(status="revoked"),
            current_user=root,
        )
        assert result["status"] == "revoked"
        assert result["revoked_at"] is not None

    @pytest.mark.asyncio
    async def test_idempotent_re_revoke_skips_chain_check(
            self, chain_seeded_db
    ):
        """is_revoking is a transition (active→revoked), not a state.
        Re-PATCHing status=revoked on an already-revoked assignment
        no-ops on the chain check + audit stamp."""
        from routes.ai_employee_catalog import update_assignment
        from models import TenantAppAssignmentUpdate

        # First revoke the leaf (no sub-delegations)
        acme_admin = chain_seeded_db._fixtures["acme_admin"]
        first = await update_assignment(
            ACME_SUB_TENANT_ID,
            chain_seeded_db._fixtures["sub_assignment"]["assignment_id"],
            TenantAppAssignmentUpdate(status="revoked"),
            current_user=acme_admin,
        )
        first_revoked_at = first["revoked_at"]

        # Re-revoke. is_revoking evaluates False (existing already
        # revoked). The chain check doesn't fire (no transition). The
        # audit stamp doesn't re-fire (revoked_at stays).
        second = await update_assignment(
            ACME_SUB_TENANT_ID,
            chain_seeded_db._fixtures["sub_assignment"]["assignment_id"],
            TenantAppAssignmentUpdate(status="revoked"),
            current_user=acme_admin,
        )
        assert second["status"] == "revoked"
        # revoked_at was NOT bumped — same timestamp as the first revoke.
        assert second["revoked_at"] == first_revoked_at

    @pytest.mark.asyncio
    async def test_revoke_audit_fields_stamped(self, chain_seeded_db):
        """The 1C.4 new fields revoked_by_user_id + revoked_by_tenant_id
        must be populated on transition to revoked. revoked_at was
        already covered by 1B's TestRevocationSemantics; this asserts
        the new pair."""
        from routes.ai_employee_catalog import update_assignment
        from models import TenantAppAssignmentUpdate
        acme_admin = chain_seeded_db._fixtures["acme_admin"]
        result = await update_assignment(
            ACME_SUB_TENANT_ID,
            chain_seeded_db._fixtures["sub_assignment"]["assignment_id"],
            TenantAppAssignmentUpdate(status="revoked"),
            current_user=acme_admin,
        )
        assert result["revoked_by_user_id"] == "acme_admin"
        assert result["revoked_by_tenant_id"] == ACME_TENANT_ID


# ---------------------------------------------------------------------------
# Endpoint-level defense in depth (Phase 1C.1)
# ---------------------------------------------------------------------------

class TestDefenseInDepth:
    """Phase 1C.1 hardened 11 endpoints on routes/ai_employees.py with
    `_employee_visible_to_tenant` checks. Cross-tenant access to any of
    them returns 404 (not 403) to avoid leaking the existence of
    foreign-tenant employees. Plus the POST /ai-employees create route
    no longer honors a tenant_id injection from the body."""

    @pytest.mark.asyncio
    async def test_cross_tenant_put_returns_404(self, acme_seeded_db):
        """PUT /ai-employees/{id} — Acme admin tries to update root's
        Nova by employee_id. 404, same as if the id didn't exist."""
        from routes.ai_employees import update_ai_employee
        from models import AIEmployeeUpdate
        acme_admin = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_admin",
            role="tenant_admin",
            permissions=TENANT_ADMIN_PERMS + ["ai_employees.edit"],
        )
        with pytest.raises(HTTPException) as exc:
            await update_ai_employee(
                "ai_nova",
                AIEmployeeUpdate(name="Hacked"),
                current_user=acme_admin,
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_cross_tenant_patch_status_returns_404(self, acme_seeded_db):
        """PATCH /ai-employees/{id}/status — same shape, status flip."""
        from routes.ai_employees import update_ai_employee_status
        from models import AIEmployeeStatusUpdate
        acme_admin = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_admin",
            role="tenant_admin",
            permissions=TENANT_ADMIN_PERMS + ["ai_employees.manage"],
        )
        with pytest.raises(HTTPException) as exc:
            await update_ai_employee_status(
                "ai_nova",
                AIEmployeeStatusUpdate(status="paused"),
                current_user=acme_admin,
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_cross_tenant_delete_returns_404(self, acme_seeded_db):
        """DELETE /ai-employees/{id} — most destructive surface; must
        not even acknowledge the target exists."""
        from routes.ai_employees import delete_ai_employee
        acme_admin = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_admin",
            role="tenant_admin",
            permissions=TENANT_ADMIN_PERMS + ["ai_employees.delete"],
        )
        with pytest.raises(HTTPException) as exc:
            await delete_ai_employee("ai_nova", current_user=acme_admin)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_cross_tenant_webhook_info_returns_404(self, acme_seeded_db):
        """GET /ai-employees/{id}/webhook-info — leaks a credential
        (webhook token) if not gated. 1C.1 made it tenant-aware."""
        from routes.ai_employees import get_webhook_info
        acme_admin = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_admin",
            role="tenant_admin", permissions=TENANT_ADMIN_PERMS,
        )
        with pytest.raises(HTTPException) as exc:
            await get_webhook_info("ai_nova", current_user=acme_admin)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_cross_tenant_executions_returns_404(self, acme_seeded_db):
        """GET /ai-employees/{id}/executions — execution logs can carry
        PII from tenant workflows; must not leak cross-tenant."""
        from routes.ai_employees import get_execution_log
        acme_admin = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_admin",
            role="tenant_admin", permissions=TENANT_ADMIN_PERMS,
        )
        with pytest.raises(HTTPException) as exc:
            await get_execution_log("ai_nova", current_user=acme_admin)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_cross_tenant_access_read_returns_404(self, acme_seeded_db):
        """GET /ai-employees/{id}/access — access lists (allowed_roles,
        allowed_user_ids) are tenant-specific config. 1C.1 gated it."""
        from routes.ai_employees import get_ai_employee_access
        acme_admin = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_admin",
            role="tenant_admin",
            permissions=TENANT_ADMIN_PERMS + ["ai_employees.manage"],
        )
        with pytest.raises(HTTPException) as exc:
            await get_ai_employee_access("ai_nova", current_user=acme_admin)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_cross_tenant_access_write_returns_404(self, acme_seeded_db):
        """PUT /ai-employees/{id}/access — the write-side mirror of
        access-read. Without this gate, a caller could overwrite
        another tenant's access lists by guessing employee_id."""
        from routes.ai_employees import update_ai_employee_access
        acme_admin = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_admin",
            role="tenant_admin",
            permissions=TENANT_ADMIN_PERMS + ["ai_employees.manage"],
        )
        with pytest.raises(HTTPException) as exc:
            await update_ai_employee_access(
                "ai_nova",
                {"allowed_roles": ["admin"]},
                current_user=acme_admin,
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_post_create_blocks_foreign_tenant_id_injection(
            self, acme_seeded_db
    ):
        """The POST /ai-employees route's 1C.1 fix: a non-root caller
        passing tenant_id=foreign in the body gets it silently forced
        to their own tenant. Without this, a tenant_admin in Acme with
        .create could inject rows stamped to Globex."""
        from routes.ai_employees import create_ai_employee
        from models import AIEmployeeCreate

        # Non-root user with .create perm (tenant_admin doesn't have it
        # by default; we add it inline to exercise the post-perm gate).
        acme_creator = _make_user(
            tenant_id=ACME_TENANT_ID, user_id="acme_creator",
            role="custom",
            permissions=TENANT_ADMIN_PERMS + ["ai_employees.create"],
        )

        # Attempt cross-tenant injection via the body.
        created = await create_ai_employee(
            AIEmployeeCreate(
                name="Injected",
                role="Test",
                type="conversational",
                tenant_id=GLOBEX_TENANT_ID,  # the injection attempt
            ),
            current_user=acme_creator,
        )
        # The 1C.1 fix forces the tenant_id back to the caller's tenant.
        # The injection silently fails; the row lands in Acme, not Globex.
        assert created["tenant_id"] == ACME_TENANT_ID, (
            f"POST tenant_id injection NOT blocked. Expected {ACME_TENANT_ID}, "
            f"got {created['tenant_id']}."
        )
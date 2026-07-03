"""
seed_test_delegation.py — Phase 1D.7 manual test fixture.

Creates a delegation row in `tenant_app_assignments` that simulates a
partner tenant having delegated an AI Employee to one of its sub-tenants.
The point of this fixture is to put a delegation chain in place so we
can exercise the Phase 1C.4 revoke conflict path — the 409 response
that the new RevokeConflictModal renders.

Why a script and not the API: the POST /tenants/{tid}/assignments
endpoint always stamps `granted_by_tenant_id` from the *caller's* own
tenant. As a SimpliMeta root admin you can't, through the UI, create
an assignment that looks like "Beta Employees granted X to Beta
Employees 1" — you'd be the granter, not Beta Employees. So we insert
the row directly.

Run from the backend folder so dotenv finds the same `.env`:

    cd ~/Desktop/AEGISAXIOM/backend
    python scripts/seed_test_delegation.py

Defaults match Samson's localhost fixture (Beta Employees → Beta
Employees 1, Nirel's Email Classifier). Override with CLI args if your
names differ.

Cleanup is handled by cleanup_test_delegation.py — run that when you're
done testing Path B.
"""

import argparse
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

# Load the same .env the backend uses. Run this script from the backend
# folder; the path below assumes scripts/ is one level under backend/.
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")

if not MONGO_URL or not DB_NAME:
    print("ERROR: MONGO_URL and DB_NAME must be set in your .env", file=sys.stderr)
    sys.exit(1)


def gen_id(prefix: str) -> str:
    """Match the backend's ID format: prefix_<12 hex chars>."""
    return f"{prefix}{uuid.uuid4().hex[:12]}"


async def main(parent_name: str, child_name: str, definition_name: str):
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    # Resolve the three IDs we need. Look up by name (case-insensitive
    # exact match) because slugs can drift across environments but
    # display names are what the user knows.
    parent = await db.tenants.find_one({"name": parent_name})
    child = await db.tenants.find_one({"name": child_name})
    definition = await db.ai_employee_definitions.find_one(
        {"name": definition_name}
    )

    if not parent:
        print(f"ERROR: parent tenant '{parent_name}' not found.", file=sys.stderr)
        print("Existing tenant names:", file=sys.stderr)
        async for t in db.tenants.find({}, {"name": 1, "_id": 0}):
            print(f"  - {t.get('name')}", file=sys.stderr)
        sys.exit(2)
    if not child:
        print(f"ERROR: child tenant '{child_name}' not found.", file=sys.stderr)
        sys.exit(2)
    if not definition:
        print(f"ERROR: definition '{definition_name}' not found.", file=sys.stderr)
        print("Existing definition names:", file=sys.stderr)
        async for d in db.ai_employee_definitions.find({}, {"name": 1, "_id": 0}):
            print(f"  - {d.get('name')}", file=sys.stderr)
        sys.exit(2)

    # Sanity-check the hierarchy: child must actually be a child of parent
    # or this delegation wouldn't make semantic sense.
    if child.get("parent_tenant_id") != parent.get("tenant_id"):
        print(
            f"ERROR: '{child_name}' is not a direct child of '{parent_name}'.",
            file=sys.stderr,
        )
        print(
            f"  '{child_name}' parent_tenant_id = {child.get('parent_tenant_id')}",
            file=sys.stderr,
        )
        print(
            f"  '{parent_name}' tenant_id       = {parent.get('tenant_id')}",
            file=sys.stderr,
        )
        sys.exit(3)

    # Verify the parent actually has an active assignment for this
    # definition — the delegation we're inserting represents a
    # downstream grant, and downstream grants require an upstream one.
    upstream = await db.tenant_app_assignments.find_one(
        {
            "tenant_id": parent.get("tenant_id"),
            "definition_id": definition.get("definition_id"),
            "status": "active",
        }
    )
    if not upstream:
        print(
            f"ERROR: '{parent_name}' has no active assignment for "
            f"'{definition_name}'. The delegation chain requires one upstream.",
            file=sys.stderr,
        )
        sys.exit(4)

    # Check we're not duplicating an existing delegation. The
    # (tenant_id, definition_id) compound unique index would reject
    # this anyway, but fail fast with a clearer message.
    existing = await db.tenant_app_assignments.find_one(
        {
            "tenant_id": child.get("tenant_id"),
            "definition_id": definition.get("definition_id"),
        }
    )
    if existing:
        print(
            f"NOTE: '{child_name}' already has an assignment for "
            f"'{definition_name}' (status={existing.get('status')}).",
            file=sys.stderr,
        )
        print(
            f"  assignment_id = {existing.get('assignment_id')}",
            file=sys.stderr,
        )
        print(
            f"  granted_by_tenant_id = {existing.get('granted_by_tenant_id')}",
            file=sys.stderr,
        )
        if existing.get("granted_by_tenant_id") == parent.get("tenant_id"):
            print(
                "  → This is already a delegation by the parent. Nothing to do.",
                file=sys.stderr,
            )
        else:
            print(
                "  → But it's NOT granted by the parent, so it won't block "
                "the revoke. Delete it (or this script's target won't work).",
                file=sys.stderr,
            )
        sys.exit(5)

    # Insert the delegation row. Match the field set produced by the
    # backend's create_assignment handler so it looks indistinguishable
    # from a real delegation.
    now = datetime.now(timezone.utc)
    doc = {
        "assignment_id": gen_id("asn_"),
        "definition_id": definition.get("definition_id"),
        "tenant_id": child.get("tenant_id"),
        # The load-bearing field: this is what makes the chain check fire.
        "granted_by_tenant_id": parent.get("tenant_id"),
        # The granter user — in real flows this is a tenant_admin user of
        # the parent tenant. For the test fixture we just stamp the
        # upstream assignment's granter for traceability.
        "granted_by_user_id": upstream.get("granted_by_user_id", "test_fixture"),
        "max_instances": 1,
        "expires_at": None,
        "notes": (
            "Test fixture inserted by seed_test_delegation.py to exercise "
            "the 1D.7 revoke conflict path. Safe to delete via "
            "cleanup_test_delegation.py."
        ),
        "status": "active",
        "granted_at": now,
        "updated_at": now,
        "revoked_at": None,
        "revoked_by_user_id": None,
        "revoked_by_tenant_id": None,
    }

    await db.tenant_app_assignments.insert_one(doc)

    print("✓ Delegation inserted:")
    print(f"  assignment_id        = {doc['assignment_id']}")
    print(f"  definition_id        = {doc['definition_id']} ({definition_name})")
    print(f"  tenant_id            = {doc['tenant_id']} ({child_name})")
    print(f"  granted_by_tenant_id = {doc['granted_by_tenant_id']} ({parent_name})")
    print(f"  status               = active")
    print()
    print(
        f"Now navigate to {parent_name} in the AEGISAXIOM UI and try to revoke "
        f"{definition_name}. The 409 conflict modal should fire, listing "
        f"{child_name} as the blocker."
    )
    print()
    print(
        "When done, run: python scripts/cleanup_test_delegation.py "
        f'--parent "{parent_name}" --child "{child_name}" '
        f'--definition "{definition_name}"'
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--parent", default="Beta Employees")
    p.add_argument("--child", default="Beta Employees 1")
    p.add_argument("--definition", default="Nirel's Email Classifier")
    args = p.parse_args()
    asyncio.run(main(args.parent, args.child, args.definition))

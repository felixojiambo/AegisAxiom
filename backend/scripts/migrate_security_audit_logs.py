"""
Migrate security audit log entries into the unified audit_logs collection.

Background:
  Pre-RBAC unification, services.security_logger wrote into a separate
  security_audit_logs collection while services.audit_service wrote into
  audit_logs. The admin Audit Log UI only reads audit_logs, so every
  authz_failed, auth_failed, role_changed, ai_employee_created, etc. was
  invisible to admins.

This migration:
  1. Backfills event_category="activity" onto legacy audit_logs entries
     that don't have it, so the new UI filters work uniformly.
  2. Copies every security_audit_logs entry into audit_logs with
     normalized field names:
        user_id    -> actor_id
        user_email -> actor_email
        resource   -> resource_type
     and event_category="security".
  3. Idempotent: skips entries already migrated (matches on a
     deterministic audit_id derived from the source document's _id).

Does NOT delete the security_audit_logs collection — it's left in place
as a rollback archive. Drop it manually after the migration is verified.

Run:
    cd backend && python scripts/migrate_security_audit_logs.py
"""
import asyncio
import os

from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME", "aegisaxiom")


def _stable_audit_id(source_id) -> str:
    """Derive a deterministic audit_id from the source document's _id.

    Lets re-runs of the migration detect already-migrated entries by
    looking up the same audit_id rather than relying on field-by-field
    equality. The full str(_id) is preserved — ObjectIds always serialize
    to 24 hex chars, but a custom string _id (legacy or test fixture)
    could be longer, and silent truncation could collide audit_ids.
    """
    return f"audit_secmig_{source_id}"


async def backfill_activity_category(db) -> int:
    """Tag legacy audit_logs entries with event_category='activity'.

    Anything written before the schema unification has no event_category
    field. Treat them as activity events so the new UI filters surface
    them exactly as before.
    """
    result = await db.audit_logs.update_many(
        {"event_category": {"$exists": False}},
        {"$set": {"event_category": "activity"}},
    )
    return result.modified_count


async def copy_security_audit_logs(db) -> dict:
    """Copy security_audit_logs into audit_logs with normalized fields.

    Returns counts: {"migrated": N, "skipped": N}. Skipped means the
    entry was already migrated (matched by deterministic audit_id).
    """
    migrated = 0
    skipped = 0
    # Pre-load already-migrated audit_ids into a set so the per-row
    # idempotency check is O(1) memory lookup instead of O(n) Mongo
    # round-trips. The "audit_secmig_*" prefix scopes the read to
    # entries this migration produced; existing native audit_logs rows
    # are untouched. Acceptable at any volume the audit log sees in
    # practice (memory cost ~40 bytes per id).
    already_migrated_ids = set(
        await db.audit_logs.distinct(
            "audit_id",
            {"audit_id": {"$regex": "^audit_secmig_"}},
        )
    )
    cursor = db.security_audit_logs.find({})
    async for src in cursor:
        audit_id = _stable_audit_id(src.get("_id"))
        if audit_id in already_migrated_ids:
            skipped += 1
            continue
        normalized = {
            "audit_id": audit_id,
            "event_category": "security",
            "event_type": src.get("event_type"),
            "severity": src.get("severity", "info"),
            "actor_id": src.get("user_id"),
            "actor_email": src.get("user_email"),
            "resource_type": src.get("resource"),
            "resource_id": src.get("resource_id"),
            "action": src.get("action"),
            "outcome": src.get("outcome", "success"),
            "details": src.get("details") or {},
            "timestamp": src.get("timestamp"),
        }
        await db.audit_logs.insert_one(normalized)
        migrated += 1
    return {"migrated": migrated, "skipped": skipped}


async def main():
    if not MONGO_URL:
        raise SystemExit("MONGO_URL env var is required")

    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    print("=== Security audit log migration ===\n")

    print("[1/2] Backfilling event_category='activity' on legacy audit_logs...")
    activity_count = await backfill_activity_category(db)
    print(f"  Tagged {activity_count} legacy entries.\n")

    print("[2/2] Copying security_audit_logs into audit_logs...")
    counts = await copy_security_audit_logs(db)
    print(f"  Migrated: {counts['migrated']}, skipped (already migrated): {counts['skipped']}\n")

    total_audit = await db.audit_logs.count_documents({})
    sec_in_audit = await db.audit_logs.count_documents({"event_category": "security"})
    print("=== Final state ===")
    print(f"audit_logs total: {total_audit}")
    print(f"audit_logs (security): {sec_in_audit}")
    print(f"audit_logs (activity): {total_audit - sec_in_audit}")
    print("\nsecurity_audit_logs collection left in place as archive.")
    print("Drop manually after verification:")
    print("  use aegisaxiom; db.security_audit_logs.drop();")


if __name__ == "__main__":
    asyncio.run(main())

"""
Cleanup all test/seed data from the AEGISAXIOM-1 database.
Covers ALL collections that seed_all.py populates plus legacy demo data.

Run:  cd backend && python cleanup_seed.py
"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import os

# Every collection that may contain is_test_data: True documents
TEST_DATA_COLLECTIONS = [
    "users",
    "contractor_businesses",
    "accounts",
    "contacts",
    "opportunities",
    "deals",
    "contracts",
    "projects",
    "pm_projects",
    "time_entries",
    "commissions",
    "invoices",
    "ap_bills",
    "field_jobs",
    "dashboards",
    "opportunity_resource_assignments",
    "opportunity_line_items",
    "job_activity",
    "account_operating_hours",
    "site_profiles",
]

# Collections cleaned by linkage (no is_test_data flag)
PM_LINKED_COLLECTIONS = ["pm_milestones", "pm_tasks"]

# Deterministic PM project IDs used by seed_all.py
SEED_PM_PROJECT_IDS = [f"pmproj_test_{i:03d}" for i in range(1, 4)]


async def cleanup():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db = client[os.environ.get("DB_NAME", "test_database")]

    print("Cleaning test data (is_test_data: True)...\n")
    total_deleted = 0

    # 1. Collections with is_test_data flag
    for coll in TEST_DATA_COLLECTIONS:
        result = await db[coll].delete_many({"is_test_data": True})
        if result.deleted_count > 0:
            print(f"  {coll}: deleted {result.deleted_count}")
            total_deleted += result.deleted_count

    # 2. PM milestones & tasks (cleaned by project_id linkage)
    for coll in PM_LINKED_COLLECTIONS:
        result = await db[coll].delete_many({"project_id": {"$in": SEED_PM_PROJECT_IDS}})
        if result.deleted_count > 0:
            print(f"  {coll}: deleted {result.deleted_count}")
            total_deleted += result.deleted_count

    # 3. Legacy demo data (acc_demo*, deal_demo*, etc.)
    legacy = [
        ("accounts", "account_id", "^acc_demo"),
        ("deals", "deal_id", "^deal_demo"),
        ("commissions", "commission_id", "^comm_demo"),
        ("invoices", "invoice_id", "^inv_demo"),
        ("field_jobs", "job_id", "^job_demo"),
    ]
    for coll, field, pattern in legacy:
        r = await db[coll].delete_many({field: {"$regex": pattern}})
        if r.deleted_count:
            print(f"  {coll}: deleted {r.deleted_count} legacy demo docs")
            total_deleted += r.deleted_count

    print(f"\nTotal deleted: {total_deleted}")

    # 4. Show remaining counts
    print("\n--- Remaining counts ---")
    all_collections = sorted(set(TEST_DATA_COLLECTIONS + PM_LINKED_COLLECTIONS + [
        "field_jobs", "jobs", "tasks", "commissions", "invoices",
    ]))
    for coll in all_collections:
        c = await db[coll].count_documents({})
        if c > 0:
            print(f"  {coll}: {c}")

    print("\nDone.")


asyncio.run(cleanup())

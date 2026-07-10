"""Update Dragon Ball users to be internal Project Managers."""
import asyncio
import sys
import os
sys.path.insert(0, '/app/backend')

from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv('/app/backend/.env')

async def update_dragonball_to_pm():
    mongo_url = os.environ.get('MONGO_URL')
    db_name = os.environ.get('DB_NAME', 'aegisaxiom')
    
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    
    # Create project_manager role if it doesn't exist
    pm_role = await db.roles.find_one({"name": "project_manager"}, {"_id": 0})
    
    if not pm_role:
        role_doc = {
            "role_id": "role_project_manager",
            "name": "project_manager",
            "display_name": "Project Manager",
            "description": "Internal employee who manages IT professional services projects",
            "color": "#8B5CF6",
            "is_system": False,
            "is_default": False,
            "permissions": [
                "projects.view",
                "projects.create",
                "projects.edit",
                "tasks.view",
                "tasks.create",
                "tasks.edit",
                "time.view",
                "time.approve"
            ],
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        await db.roles.insert_one(role_doc)
        print("✅ Created 'project_manager' role")
    else:
        print("⏭️  'project_manager' role already exists")
    
    # Update Dragon Ball users
    dragonball_emails = [
        "goku@simplimeta.io",
        "vegeta@simplimeta.io",
        "piccolo@simplimeta.io",
        "gohan@simplimeta.io",
        "trunks@simplimeta.io",
    ]
    
    updated_count = 0
    for email in dragonball_emails:
        result = await db.users.update_one(
            {"email": email},
            {
                "$set": {
                    "role": "project_manager",
                    "contractor_business_id": None,
                    "contractor_business_name": None,
                }
            }
        )
        if result.modified_count > 0:
            user = await db.users.find_one({"email": email}, {"_id": 0, "name": 1})
            print(f"✅ Updated {user['name']} to Project Manager (internal)")
            updated_count += 1
    
    print(f"\n🎉 Updated {updated_count} Dragon Ball users to internal Project Managers")
    print("   Role: project_manager")
    print("   Contractor link: Removed")

if __name__ == "__main__":
    asyncio.run(update_dragonball_to_pm())

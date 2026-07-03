"""Link Dragon Ball users to a contractor business as resources."""
import asyncio
import sys
import os
sys.path.insert(0, '/app/backend')

from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone
import uuid
from dotenv import load_dotenv

load_dotenv('/app/backend/.env')

async def setup_dragonball_contractors():
    mongo_url = os.environ.get('MONGO_URL')
    db_name = os.environ.get('DB_NAME', 'aegisaxiom')
    
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    
    # Create Capsule Corp contractor business
    business_id = f"biz_{uuid.uuid4().hex[:12]}"
    
    business_doc = {
        "business_id": business_id,
        "business_name": "Capsule Corp Elite Team",
        "contact_name": "Bulma Brief",
        "email": "bulma@capsulecorp.io",
        "phone": "555-CAPSULE",
        "tax_id_ein": "99-8877665",
        "w9_on_file": True,
        "is_1099_eligible": True,
        "billing_frequency": "monthly",
        "payment_terms": "net_30",
        "payment_method": "ACH",
        "status": "active",
        "is_test_data": True,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "created_by": "user_64190789a2ea",
        "notes": "Elite Dragon Ball Z-Fighters contractor team",
    }
    
    # Check if already exists
    existing = await db.contractor_businesses.find_one({"business_name": "Capsule Corp Elite Team"}, {"_id": 0})
    if not existing:
        await db.contractor_businesses.insert_one(business_doc)
        print(f"✅ Created contractor business: Capsule Corp Elite Team ({business_id})")
    else:
        business_id = existing["business_id"]
        print(f"⏭️  Capsule Corp already exists: {business_id}")
    
    # Link Dragon Ball users to this contractor business
    dragonball_emails = [
        "goku@simplimeta.io",
        "vegeta@simplimeta.io",
        "piccolo@simplimeta.io",
        "gohan@simplimeta.io",
        "trunks@simplimeta.io",
    ]
    
    linked_count = 0
    for email in dragonball_emails:
        user = await db.users.find_one({"email": email}, {"_id": 0})
        if user:
            # Update user with contractor_business_id
            result = await db.users.update_one(
                {"email": email},
                {
                    "$set": {
                        "contractor_business_id": business_id,
                        "contractor_business_name": "Capsule Corp Elite Team",
                    }
                }
            )
            linked_count += 1
            print(f"✅ Linked {user['name']} to Capsule Corp")
    
    # Add resource types to the business
    resource_types = [
        {
            "resource_type_id": "Project Manager",
            "resource_type_name": "Project Manager",
            "pricing_models_accepted": ["block_hours", "bill_actuals"],
            "rate_cards": {
                "block_hours": {"sell_rate": 200, "default_cost_rate": 150},
                "bill_actuals": {"sell_rate": 195, "default_cost_rate": 145}
            },
            "is_active": True,
            "added_at": datetime.now(timezone.utc),
        },
        {
            "resource_type_id": "Senior Developer",
            "resource_type_name": "Senior Developer",
            "pricing_models_accepted": ["block_hours", "bill_actuals"],
            "rate_cards": {
                "block_hours": {"sell_rate": 185, "default_cost_rate": 140},
                "bill_actuals": {"sell_rate": 180, "default_cost_rate": 135}
            },
            "is_active": True,
            "added_at": datetime.now(timezone.utc),
        }
    ]
    
    # Update business with resource types
    await db.contractor_businesses.update_one(
        {"business_id": business_id},
        {"$set": {"resource_types_offered": resource_types}}
    )
    
    print(f"\n🎉 Setup complete!")
    print(f"   Business: Capsule Corp Elite Team")
    print(f"   Linked {linked_count} Dragon Ball users")
    print(f"   Added {len(resource_types)} resource types")

if __name__ == "__main__":
    asyncio.run(setup_dragonball_contractors())

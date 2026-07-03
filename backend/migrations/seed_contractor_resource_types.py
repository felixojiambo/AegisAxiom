"""
Seed contractor businesses with resource types for testing
Run this to populate resource_types_offered for existing businesses
"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import os
from datetime import datetime, timezone

async def seed_contractor_resource_types():
    """Add resource types to existing contractor businesses."""
    
    # Connect to MongoDB
    mongo_url = 'mongodb://localhost:27017'
    db_name = 'test_database'
    
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    
    # Fetch all contractor businesses
    businesses = await db.contractor_businesses.find({}, {"_id": 0}).to_list(100)
    print(f"Found {len(businesses)} contractor businesses")
    
    if len(businesses) == 0:
        print("No businesses to update")
        return
    
    # Define resource types to assign to each business
    # Business 1: Avengers - Field Services focused
    avengers_resources = [
        {
            "resource_type_id": "Field Service Technician",
            "resource_type_name": "Field Service Technician",
            "pricing_models_accepted": ["block_hours", "bill_actuals"],
            "rate_cards": {
                "block_hours": {"sell_rate": 180, "default_cost_rate": 140},
                "bill_actuals": {"sell_rate": 175, "default_cost_rate": 135}
            },
            "is_active": True,
            "added_at": datetime.now(timezone.utc)
        },
        {
            "resource_type_id": "Field Service Coordinator",
            "resource_type_name": "Field Service Coordinator",
            "pricing_models_accepted": ["block_hours", "bill_actuals"],
            "rate_cards": {
                "block_hours": {"sell_rate": 150, "default_cost_rate": 115},
                "bill_actuals": {"sell_rate": 145, "default_cost_rate": 110}
            },
            "is_active": True,
            "added_at": datetime.now(timezone.utc)
        }
    ]
    
    # Business 2: SHIELD - Professional Services focused
    shield_resources = [
        {
            "resource_type_id": "Senior Developer",
            "resource_type_name": "Senior Developer",
            "pricing_models_accepted": ["block_hours", "bill_actuals", "fixed_price"],
            "rate_cards": {
                "block_hours": {"sell_rate": 200, "default_cost_rate": 150},
                "bill_actuals": {"sell_rate": 195, "default_cost_rate": 145},
                "fixed_price": {"default_cost_rate": 145}
            },
            "is_active": True,
            "added_at": datetime.now(timezone.utc)
        },
        {
            "resource_type_id": "SR Engineer",
            "resource_type_name": "SR Engineer",
            "pricing_models_accepted": ["block_hours", "bill_actuals"],
            "rate_cards": {
                "block_hours": {"sell_rate": 185, "default_cost_rate": 140},
                "bill_actuals": {"sell_rate": 180, "default_cost_rate": 135}
            },
            "is_active": True,
            "added_at": datetime.now(timezone.utc)
        },
        {
            "resource_type_id": "Project Manager",
            "resource_type_name": "Project Manager",
            "pricing_models_accepted": ["block_hours", "fixed_price"],
            "rate_cards": {
                "block_hours": {"sell_rate": 175, "default_cost_rate": 130},
                "fixed_price": {"default_cost_rate": 130}
            },
            "is_active": True,
            "added_at": datetime.now(timezone.utc)
        }
    ]
    
    # Business 3: Stark - Mixed capabilities
    stark_resources = [
        {
            "resource_type_id": "SR Architect",
            "resource_type_name": "SR Architect",
            "pricing_models_accepted": ["block_hours", "bill_actuals", "fixed_price"],
            "rate_cards": {
                "block_hours": {"sell_rate": 225, "default_cost_rate": 175},
                "bill_actuals": {"sell_rate": 220, "default_cost_rate": 170},
                "fixed_price": {"default_cost_rate": 170}
            },
            "is_active": True,
            "added_at": datetime.now(timezone.utc)
        },
        {
            "resource_type_id": "DevOps Engineer",
            "resource_type_name": "DevOps Engineer",
            "pricing_models_accepted": ["block_hours", "bill_actuals"],
            "rate_cards": {
                "block_hours": {"sell_rate": 160, "default_cost_rate": 120},
                "bill_actuals": {"sell_rate": 155, "default_cost_rate": 115}
            },
            "is_active": True,
            "added_at": datetime.now(timezone.utc)
        },
        {
            "resource_type_id": "Field Service Technician",
            "resource_type_name": "Field Service Technician",
            "pricing_models_accepted": ["block_hours"],
            "rate_cards": {
                "block_hours": {"sell_rate": 180, "default_cost_rate": 145}
            },
            "is_active": True,
            "added_at": datetime.now(timezone.utc)
        }
    ]
    
    # Map businesses to resources
    business_mapping = {
        "Avengers Technical Solutions": avengers_resources,
        "SHIELD Contractors LLC": shield_resources,
        "Stark Industries Tech Services": stark_resources
    }
    
    updated_count = 0
    for business in businesses:
        biz_name = business.get('name') or business.get('business_name')
        resources = business_mapping.get(biz_name, [])
        
        if resources:
            result = await db.contractor_businesses.update_one(
                {"business_id": business['business_id']},
                {"$set": {"resource_types_offered": resources}}
            )
            
            if result.modified_count > 0:
                print(f"✅ Updated {biz_name}: Added {len(resources)} resource types")
                updated_count += 1
            else:
                print(f"⚠️ {biz_name}: No changes made (already up to date or not found)")
        else:
            print(f"⚠️ {biz_name}: No resource mapping defined")
    
    print(f"\n✅ Migration complete: {updated_count}/{len(businesses)} businesses updated")
    
    client.close()

if __name__ == "__main__":
    asyncio.run(seed_contractor_resource_types())

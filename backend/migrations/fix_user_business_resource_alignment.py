"""
Fix contractor user resource type assignments to MATCH their business offerings
"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone

async def fix_user_resource_mappings():
    client = AsyncIOMotorClient('mongodb://localhost:27017')
    db = client['test_database']
    
    # Define correct user-to-resource mappings
    # Each business should have users that MATCH what the business offers
    
    user_mappings = {
        # SHIELD Contractors LLC (biz_e1d4fbabfbba)
        # Offers: Senior Developer, SR Engineer, Project Manager
        "Steve Rogers": [
            {"resource_type_id": "SR Engineer", "resource_type_name": "SR Engineer", "cost_rate": 140, "pricing_model": "block_hours"}
        ],
        "Clint Barton": [
            {"resource_type_id": "Senior Developer", "resource_type_name": "Senior Developer", "cost_rate": 145, "pricing_model": "bill_actuals"}
        ],
        "Sam Wilson": [
            {"resource_type_id": "Senior Developer", "resource_type_name": "Senior Developer", "cost_rate": 135, "pricing_model": "block_hours"}
        ],
        "Bucky Barnes": [
            {"resource_type_id": "SR Engineer", "resource_type_name": "SR Engineer", "cost_rate": 135, "pricing_model": "bill_actuals"}
        ],
        "Natasha Romanoff": [
            {"resource_type_id": "Project Manager", "resource_type_name": "Project Manager", "cost_rate": 125, "pricing_model": "block_hours"}
        ],
        
        # Stark Industries Tech Services (biz_0cb96506b74a)
        # Offers: SR Architect, DevOps Engineer, Field Service Technician
        "Tony Stark": [
            {"resource_type_id": "SR Architect", "resource_type_name": "SR Architect", "cost_rate": 170, "pricing_model": "block_hours"}
        ],
        "Pepper Potts": [
            {"resource_type_id": "DevOps Engineer", "resource_type_name": "DevOps Engineer", "cost_rate": 115, "pricing_model": "bill_actuals"}
        ],
        
        # Avengers Technical Solutions (biz_e4898930f191)
        # Offers: Field Service Technician, Field Service Coordinator
        "Bruce Banner": [
            {"resource_type_id": "Field Service Technician", "resource_type_name": "Field Service Technician", "cost_rate": 135, "pricing_model": "block_hours"}
        ],
        "Peter Parker": [
            {"resource_type_id": "Field Service Technician", "resource_type_name": "Field Service Technician", "cost_rate": 130, "pricing_model": "bill_actuals"}
        ],
        "Wanda Maximoff": [
            {"resource_type_id": "Field Service Coordinator", "resource_type_name": "Field Service Coordinator", "cost_rate": 110, "pricing_model": "bill_actuals"}
        ]
    }
    
    updated_count = 0
    for user_name, assignments in user_mappings.items():
        result = await db.users.update_one(
            {"name": user_name, "role": "contractor"},
            {"$set": {"resource_type_assignments": assignments}}
        )
        
        if result.modified_count > 0:
            print(f"✅ Updated {user_name}: {[a['resource_type_name'] for a in assignments]}")
            updated_count += 1
        else:
            user = await db.users.find_one({"name": user_name, "role": "contractor"}, {"_id": 0, "name": 1})
            if user:
                print(f"⚠️ {user_name}: Already up to date")
            else:
                print(f"❌ {user_name}: User not found")
    
    print(f"\n✅ Migration complete: {updated_count}/{len(user_mappings)} users updated")
    
    # Verify mappings
    print("\n=== VERIFICATION ===\n")
    businesses = await db.contractor_businesses.find({}, {'_id': 0, 'business_id': 1, 'business_name': 1, 'resource_types_offered': 1}).to_list(10)
    
    for biz in businesses:
        if not biz.get('resource_types_offered'):
            continue
            
        name = biz.get('business_name')
        biz_id = biz.get('business_id')
        offered_types = [rt.get('resource_type_name') for rt in biz.get('resource_types_offered', [])]
        
        print(f"Business: {name}")
        print(f"  Offers: {offered_types}")
        
        # Get users
        users = await db.users.find({
            'contractor_business_id': biz_id,
            'role': 'contractor'
        }, {'_id': 0, 'name': 1, 'resource_type_assignments': 1}).to_list(20)
        
        print(f"  Users: {len(users)}")
        for user in users:
            user_types = [rt.get('resource_type_name') for rt in user.get('resource_type_assignments', [])]
            matches = any(ut in offered_types for ut in user_types)
            symbol = "✅" if matches else "❌"
            print(f"    {symbol} {user.get('name')}: {user_types}")
        print()
    
    client.close()

if __name__ == "__main__":
    asyncio.run(fix_user_resource_mappings())

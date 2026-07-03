"""
Fix contractor business names - populate contractor_business_name field
"""

import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv('/app/backend/.env')
MONGO_URL = os.getenv('MONGO_URL')
DB_NAME = os.getenv('DB_NAME', 'simplimeta')


async def fix_business_names():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    print("=" * 80)
    print("FIXING CONTRACTOR BUSINESS NAMES")
    print("=" * 80)
    print()
    
    # Fetch all businesses
    businesses = await db.contractor_businesses.find({}, {"_id": 0}).to_list(100)
    business_lookup = {biz['business_id']: biz['business_name'] for biz in businesses}
    
    print(f"Business Lookup Table:")
    for bid, bname in business_lookup.items():
        print(f"  {bid} → {bname}")
    print()
    
    # Fetch all contractors
    contractors = await db.users.find({"role": "contractor"}, {"_id": 0}).to_list(100)
    
    print(f"Updating {len(contractors)} contractors...")
    print("-" * 80)
    
    updated_count = 0
    for contractor in contractors:
        biz_id = contractor.get('contractor_business_id')
        current_name = contractor.get('contractor_business_name')
        
        if biz_id and biz_id in business_lookup:
            correct_name = business_lookup[biz_id]
            
            if current_name != correct_name:
                result = await db.users.update_one(
                    {"user_id": contractor['user_id']},
                    {"$set": {
                        "contractor_business_name": correct_name,
                        "updated_at": datetime.now(timezone.utc)
                    }}
                )
                
                if result.modified_count > 0:
                    updated_count += 1
                    print(f"✅ {contractor['name']:20} → {correct_name}")
                else:
                    print(f"⚠️  {contractor['name']:20} → Update returned 0 modified")
            else:
                print(f"✓  {contractor['name']:20} → {correct_name} (already correct)")
        else:
            print(f"❌ {contractor['name']:20} → No business ID or business not found")
    
    print()
    print("=" * 80)
    print(f"✅ Updated {updated_count} contractor records")
    print("=" * 80)
    
    client.close()


if __name__ == "__main__":
    asyncio.run(fix_business_names())

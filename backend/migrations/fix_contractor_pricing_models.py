"""
Fix contractor user resource assignments - add pricing_model
"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient

async def fix_pricing_models():
    client = AsyncIOMotorClient('mongodb://localhost:27017')
    db = client['test_database']
    
    # Get all contractor users
    users = await db.users.find({'role': 'contractor'}, {'_id': 0}).to_list(100)
    print(f"Found {len(users)} contractor users\n")
    
    updated_count = 0
    for user in users:
        user_id = user.get('user_id')
        assignments = user.get('resource_type_assignments', [])
        
        if not assignments:
            continue
        
        # Update each assignment to add pricing_model if missing
        updated_assignments = []
        changed = False
        
        for assignment in assignments:
            if not assignment.get('pricing_model'):
                # Default to block_hours for most, bill_actuals for some
                if 'Coordinator' in assignment.get('resource_type_name', ''):
                    assignment['pricing_model'] = 'bill_actuals'
                else:
                    assignment['pricing_model'] = 'block_hours'
                changed = True
            updated_assignments.append(assignment)
        
        if changed:
            result = await db.users.update_one(
                {'user_id': user_id},
                {'$set': {'resource_type_assignments': updated_assignments}}
            )
            
            if result.modified_count > 0:
                print(f"✅ Updated {user.get('name')}: Added pricing_model to {len(updated_assignments)} assignments")
                updated_count += 1
    
    print(f"\n✅ Migration complete: {updated_count}/{len(users)} users updated")
    
    client.close()

if __name__ == "__main__":
    asyncio.run(fix_pricing_models())

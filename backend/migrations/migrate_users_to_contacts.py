"""
Migration script: Create contacts for existing users
This ensures all users in the system have corresponding contact records.
"""
import asyncio
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import init, get_db
from dotenv import load_dotenv

load_dotenv()


async def migrate_users_to_contacts():
    """Create contact records for all existing users who don't have one."""
    db = get_db()
    
    print("\n🔄 Migrating Users to Contacts...")
    
    # Get all users
    users = await db.users.find({}, {"_id": 0}).to_list(1000)
    print(f"   Found {len(users)} users in system")
    
    # Check which users already have contacts
    existing_contacts = await db.contacts.find(
        {"linked_user_id": {"$ne": None}},
        {"_id": 0, "linked_user_id": 1}
    ).to_list(1000)
    
    existing_user_ids = {c["linked_user_id"] for c in existing_contacts}
    print(f"   {len(existing_user_ids)} users already have contacts")
    
    # Create contacts for users without them
    contacts_to_create = []
    now = datetime.now(timezone.utc)
    
    for user in users:
        if user["user_id"] in existing_user_ids:
            continue
        
        # Parse name into first/last
        name_parts = user.get("name", "").split(" ", 1)
        first_name = name_parts[0] if name_parts else "Unknown"
        last_name = name_parts[1] if len(name_parts) > 1 else ""
        
        # Determine account_id
        # For users with account_id, use that
        # For users without account_id (admins, etc.), create a special "Internal" account
        account_id = user.get("account_id")
        account_name = None
        
        if account_id:
            account = await db.accounts.find_one({"account_id": account_id}, {"_id": 0, "name": 1})
            account_name = account.get("name") if account else None
        else:
            # Check if "Internal Users" account exists
            internal_account = await db.accounts.find_one(
                {"name": "Internal Users"},
                {"_id": 0}
            )
            
            if not internal_account:
                # Create internal account
                account_id = "acc_internal_users"
                internal_account = {
                    "account_id": account_id,
                    "name": "Internal Users",
                    "account_type": "internal",
                    "email": None,
                    "phone": None,
                    "website": None,
                    "address": None,
                    "industry": "Technology",
                    "assigned_sales_rep_id": None,
                    "assigned_agent_id": None,
                    "is_active": True,
                    "is_test_data": False,
                    "source": "system",
                    "zoho_account_id": None,
                    "qbo_customer_id": None,
                    "created_at": now
                }
                await db.accounts.insert_one(internal_account)
                print(f"   ✓ Created 'Internal Users' account")
                account_name = "Internal Users"
            else:
                account_id = internal_account["account_id"]
                account_name = internal_account["name"]
        
        contact_id = f"con_user_{user['user_id'][5:]}"
        
        contact = {
            "contact_id": contact_id,
            "account_id": account_id,
            "account_name": account_name,
            "first_name": first_name,
            "last_name": last_name,
            "name": user.get("name", ""),
            "email": user.get("email"),
            "phone": None,
            "mobile_phone": None,
            "title": user.get("role", "").replace("_", " ").title(),
            "department": None,
            "linkedin_url": None,
            "preferred_contact_method": "email",
            "is_primary": False,
            "is_active": user.get("is_active", True),
            "linked_user_id": user["user_id"],
            "source": "user_migration",
            "zoho_contact_id": user.get("zoho_contact_id"),
            "notes": f"Auto-created from user account during migration",
            "is_test_data": False,
            "created_at": user.get("created_at", now),
            "updated_at": now
        }
        
        contacts_to_create.append(contact)
    
    if contacts_to_create:
        await db.contacts.insert_many(contacts_to_create)
        print(f"   ✓ Created {len(contacts_to_create)} contacts from users")
        
        # Print summary
        print("\n📊 Migration Summary:")
        print(f"   Total users: {len(users)}")
        print(f"   Already had contacts: {len(existing_user_ids)}")
        print(f"   New contacts created: {len(contacts_to_create)}")
        
        # Show breakdown by role
        role_breakdown = {}
        for contact in contacts_to_create:
            role = contact["title"]
            role_breakdown[role] = role_breakdown.get(role, 0) + 1
        
        print("\n   Contacts created by role:")
        for role, count in sorted(role_breakdown.items()):
            print(f"     - {role}: {count}")
    else:
        print("   ✓ All users already have contacts. No migration needed.")
    
    print("\n✅ Migration complete!")


if __name__ == "__main__":
    init(os.environ["MONGO_URL"], os.environ["DB_NAME"])
    asyncio.run(migrate_users_to_contacts())

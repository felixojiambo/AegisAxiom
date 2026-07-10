"""
Seed script for Contacts module
Creates test contacts across different accounts for development and testing.
"""
import asyncio
import sys
import os
from datetime import datetime, timezone
from pathlib import Path
import random

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import init, get_db
from dotenv import load_dotenv

load_dotenv()

# Sample contact data
FIRST_NAMES = ["Michael", "Sarah", "David", "Jennifer", "Robert", "Lisa", "James", "Mary", "John", "Patricia"]
LAST_NAMES = ["Johnson", "Williams", "Brown", "Davis", "Miller", "Wilson", "Moore", "Taylor", "Anderson", "Thomas"]

TITLES = [
    "Chief Technology Officer", "IT Director", "VP of Operations", "Facilities Manager",
    "Operations Manager", "Director of IT", "Network Administrator", "Security Manager",
    "Project Manager", "Procurement Manager", "VP of Procurement", "CFO",
    "Controller", "Accounting Manager", "CEO", "COO", "General Manager"
]

DEPARTMENTS = [
    "Information Technology", "Operations", "Facilities", "Procurement",
    "Finance", "Executive", "Administration", "Security", "Engineering"
]

async def seed_contacts():
    """Create test contacts for various accounts."""
    db = get_db()
    
    # Check existing
    existing_count = await db.contacts.count_documents({"is_test_data": True, "source": "seed"})
    if existing_count > 0:
        print(f"⚠️  Found {existing_count} existing seed contacts. Deleting...")
        result = await db.contacts.delete_many({"is_test_data": True, "source": "seed"})
        print(f"✓ Deleted {result.deleted_count} seed contacts")
    
    # Get random accounts (exclude Internal Users)
    accounts = await db.accounts.find(
        {"account_id": {"$ne": "acc_internal_users"}},
        {"_id": 0, "account_id": 1, "name": 1}
    ).limit(50).to_list(50)
    
    if len(accounts) < 10:
        print("❌ Need at least 10 accounts to seed contacts. Please seed accounts first.")
        return
    
    print(f"\n🌱 Seeding Contacts data...")
    print(f"   Creating contacts across {len(accounts)} accounts")
    
    contacts = []
    now = datetime.now(timezone.utc)
    
    # Create 2-4 contacts per account (for first 30 accounts)
    for account in accounts[:30]:
        num_contacts = random.randint(2, 4)
        
        for i in range(num_contacts):
            first_name = random.choice(FIRST_NAMES)
            last_name = random.choice(LAST_NAMES)
            full_name = f"{first_name} {last_name}"
            
            # Generate email
            email_domain = account["name"].lower().replace(" ", "").replace("&", "")[:20] + ".com"
            email = f"{first_name.lower()}.{last_name.lower()}@{email_domain}"
            
            # Generate phone numbers
            phone = f"{random.randint(200,999)}-{random.randint(100,999)}-{random.randint(1000,9999)}"
            mobile = f"{random.randint(200,999)}-{random.randint(100,999)}-{random.randint(1000,9999)}"
            
            contact = {
                "contact_id": f"con_seed_{random.randint(10000, 99999)}",
                "account_id": account["account_id"],
                "account_name": account["name"],
                "first_name": first_name,
                "last_name": last_name,
                "name": full_name,
                "email": email,
                "phone": phone,
                "mobile_phone": mobile if random.random() > 0.3 else None,
                "title": random.choice(TITLES),
                "department": random.choice(DEPARTMENTS),
                "linkedin_url": f"https://linkedin.com/in/{first_name.lower()}{last_name.lower()}" if random.random() > 0.4 else None,
                "preferred_contact_method": random.choice(["email", "phone", "mobile", None]),
                "is_primary": i == 0,  # First contact is primary
                "is_active": random.random() > 0.1,  # 90% active
                "linked_user_id": None,
                "source": "seed",
                "zoho_contact_id": None,
                "notes": "Test contact created by seed script" if random.random() > 0.7 else None,
                "is_test_data": True,
                "created_at": now,
                "updated_at": now
            }
            
            contacts.append(contact)
    
    # Insert all contacts
    if contacts:
        await db.contacts.insert_many(contacts)
        print(f"✓ Created {len(contacts)} seed contacts")
    
    # Print summary
    print("\n📊 Contact Seed Summary:")
    print(f"   Total contacts created: {len(contacts)}")
    print(f"   Across {len(set(c['account_id'] for c in contacts))} accounts")
    print(f"   Primary contacts: {sum(1 for c in contacts if c['is_primary'])}")
    print(f"   With LinkedIn: {sum(1 for c in contacts if c['linkedin_url'])}")
    print(f"   With mobile: {sum(1 for c in contacts if c['mobile_phone'])}")
    
    print(f"\n✅ Seed complete!")


if __name__ == "__main__":
    init(os.environ["MONGO_URL"], os.environ["DB_NAME"])
    asyncio.run(seed_contacts())

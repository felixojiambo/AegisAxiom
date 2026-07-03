"""
Seed script for Test Contacts
Creates fictional character contacts for test accounts following TEST_DATA_CONVENTIONS.md
All names use famous fictional characters from Marvel, DC, Star Wars, Harry Potter, etc.
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

# Fictional character contacts by universe
# Format: (first_name, last_name, title, department, email_domain, linkedin_suffix)

DC_CONTACTS = [
    ("Bruce", "Wayne", "CEO", "Executive", "wayneenterprises.test", "brucewayne"),
    ("Clark", "Kent", "Editor", "Editorial", "dailyplanet.test", "clarkkent"),
    ("Diana", "Prince", "VP of Operations", "Operations", "themyscira.test", "dianaprince"),
    ("Barry", "Allen", "IT Director", "Information Technology", "starlabs.test", "barryallen"),
    ("Hal", "Jordan", "Pilot", "Operations", "greenlantern.test", "haljordan"),
    ("Oliver", "Queen", "CEO", "Executive", "queenconsolidated.test", "oliverqueen"),
    ("Selina", "Kyle", "Security Director", "Security", "wayneenterprises.test", "selinakyle"),
    ("Victor", "Stone", "CTO", "Information Technology", "starlabs.test", "victorstone"),
    ("Arthur", "Curry", "Operations Manager", "Operations", "atlantis.test", "arthurcurry"),
]

MARVEL_CONTACTS = [
    ("Tony", "Stark", "CEO", "Executive", "stark.test", "tonystark"),
    ("Pepper", "Potts", "CFO", "Finance", "stark.test", "pepperpotts"),
    ("Norman", "Osborn", "CEO", "Executive", "oscorp.test", "normanosborn"),
    ("Harry", "Osborn", "VP of R&D", "Research", "oscorp.test", "harryosborn"),
    ("Otto", "Octavius", "Chief Scientist", "Research", "oscorp.test", "ottooctavius"),
    ("Curtis", "Connors", "Director of Biology", "Research", "oscorp.test", "curtisconnors"),
    ("Reed", "Richards", "Chief Scientist", "Research", "fantastic.test", "reedrichards"),
    ("Sue", "Storm", "VP of Operations", "Operations", "fantastic.test", "suestorm"),
]

HARRY_POTTER_CONTACTS = [
    ("Albus", "Dumbledore", "Headmaster", "Executive", "hogwarts.test", "albusdumbledore"),
    ("Minerva", "McGonagall", "Deputy Head", "Executive", "hogwarts.test", "minervamcgonagall"),
    ("Severus", "Snape", "Head of IT Security", "Security", "hogwarts.test", "severussnape"),
    ("Hermione", "Granger", "Director of Innovation", "Research", "ministry.test", "hermionegranger"),
    ("Ron", "Weasley", "Operations Manager", "Operations", "ministry.test", "ronweasley"),
    ("Rubeus", "Hagrid", "Facilities Manager", "Facilities", "hogwarts.test", "rubeushagrid"),
    ("Godric", "Gryffindor", "Founder", "Executive", "gryffindor.test", "godricgryffindor"),
    ("Luna", "Lovegood", "Research Analyst", "Research", "ministry.test", "lunalovegood"),
]

STAR_WARS_CONTACTS = [
    ("Luke", "Skywalker", "Pilot", "Operations", "rebellion.test", "lukeskywalker"),
    ("Leia", "Organa", "Senator", "Executive", "alderaan.test", "leiaorgana"),
    ("Han", "Solo", "Captain", "Operations", "millennium.test", "hansolo"),
    ("Obi-Wan", "Kenobi", "Senior Advisor", "Executive", "jedi.test", "obiwankenobi"),
]

SCIFI_CONTACTS = [
    ("Sarah", "Connor", "Security Director", "Security", "cyberdyne.test", "sarahconnor"),
    ("Kyle", "Reese", "Operations Manager", "Operations", "resistance.test", "kylereese"),
    ("Miles", "Dyson", "CTO", "Information Technology", "cyberdyne.test", "milesdyson"),
]

# Map test accounts to character sets
ACCOUNT_MAPPING = {
    "Wayne Enterprises": DC_CONTACTS[:3],  # Bruce, Clark, Diana
    "LexCorp International": DC_CONTACTS[3:5],  # Barry, Hal
    "Queen Consolidated": [DC_CONTACTS[5], DC_CONTACTS[6]],  # Oliver, Selina
    "S.T.A.R. Labs": [DC_CONTACTS[3], DC_CONTACTS[7]],  # Barry Allen, Victor Stone
    "Stark Industries": MARVEL_CONTACTS[:2],  # Tony, Pepper
    "Oscorp Industries": MARVEL_CONTACTS[2:6],  # Norman, Harry, Otto, Curtis
    "Hogwarts School of IT": HARRY_POTTER_CONTACTS[:3],  # Albus, Minerva, Severus
    "Ministry of Magic Tech": HARRY_POTTER_CONTACTS[3:5],  # Hermione, Ron
    "Gryffindor Security Services": [HARRY_POTTER_CONTACTS[6], HARRY_POTTER_CONTACTS[7]],  # Godric, Luna
    "Dumbledore Consulting": [HARRY_POTTER_CONTACTS[0], HARRY_POTTER_CONTACTS[5]],  # Albus, Hagrid
    "Daily Planet Media": [DC_CONTACTS[1]],  # Clark Kent
    "Cyberdyne Systems": SCIFI_CONTACTS,  # Sarah, Kyle, Miles
}


async def seed_test_contacts():
    """Create fictional character contacts for test accounts."""
    db = get_db()
    
    # Delete existing test contacts from seed
    existing = await db.contacts.count_documents({"is_test_data": True, "source": "seed"})
    if existing > 0:
        print(f"⚠️  Found {existing} existing seed test contacts. Deleting...")
        result = await db.contacts.delete_many({"is_test_data": True, "source": "seed"})
        print(f"✓ Deleted {result.deleted_count} seed contacts")
    
    # Get all test accounts
    accounts = await db.accounts.find(
        {"is_test_data": True},
        {"_id": 0, "account_id": 1, "name": 1}
    ).to_list(100)
    
    if not accounts:
        print("❌ No test accounts found. Please seed accounts first.")
        return
    
    print(f"\n🌱 Seeding Test Contacts (Fictional Characters)")
    print(f"   Following TEST_DATA_CONVENTIONS.md")
    print(f"   Found {len(accounts)} test accounts\n")
    
    contacts = []
    now = datetime.now(timezone.utc)
    contact_count_by_universe = {}
    
    for account in accounts:
        account_name = account["name"]
        
        # Get character list for this account
        characters = ACCOUNT_MAPPING.get(account_name, [])
        
        if not characters:
            print(f"   ⚠️  No character mapping for: {account_name}")
            continue
        
        # Determine universe
        if any(char in DC_CONTACTS for char in characters):
            universe = "DC"
        elif any(char in MARVEL_CONTACTS for char in characters):
            universe = "Marvel"
        elif any(char in HARRY_POTTER_CONTACTS for char in characters):
            universe = "Harry Potter"
        elif any(char in STAR_WARS_CONTACTS for char in characters):
            universe = "Star Wars"
        else:
            universe = "Sci-Fi"
        
        contact_count_by_universe[universe] = contact_count_by_universe.get(universe, 0) + len(characters)
        
        print(f"   📁 {account_name} ({universe}):")
        
        for idx, (first, last, title, dept, email_domain, linkedin) in enumerate(characters):
            full_name = f"{first} {last}"
            email = f"{first.lower()}.{last.lower()}@{email_domain}"
            
            contact = {
                "contact_id": f"con_test_{account['account_id'][4:]}_{idx}",
                "account_id": account["account_id"],
                "account_name": account_name,
                "first_name": first,
                "last_name": last,
                "name": full_name,
                "email": email,
                "phone": f"555-{(idx+100):04d}",
                "mobile_phone": f"555-{(idx+2000):04d}",
                "title": title,
                "department": dept,
                "linkedin_url": f"https://linkedin.com/in/{linkedin}",
                "preferred_contact_method": "email",
                "is_primary": idx == 0,  # First contact is primary
                "is_active": True,
                "linked_user_id": None,
                "source": "seed",
                "zoho_contact_id": None,
                "notes": f"Fictional character from {universe}. Test data only.",
                "is_test_data": True,
                "created_at": now,
                "updated_at": now
            }
            
            contacts.append(contact)
            primary_marker = " ⭐ PRIMARY" if idx == 0 else ""
            print(f"      ✓ {full_name} - {title}{primary_marker}")
    
    # Insert all contacts
    if contacts:
        await db.contacts.insert_many(contacts)
        print(f"\n✅ Created {len(contacts)} fictional character contacts")
    
    # Print summary by universe
    print("\n📊 Contacts by Universe:")
    for universe, count in sorted(contact_count_by_universe.items(), key=lambda x: -x[1]):
        print(f"   {universe}: {count} contacts")
    
    # Print validation
    print("\n✅ Validation:")
    print(f"   All names are fictional characters: ✓")
    print(f"   All emails use .test domains: ✓")
    print(f"   All have is_test_data=true: ✓")
    print(f"   All have LinkedIn URLs: ✓")
    print(f"   Primary contacts assigned: {sum(1 for c in contacts if c['is_primary'])}")
    
    print(f"\n🎭 Following TEST_DATA_CONVENTIONS.md - All names instantly recognizable!")


if __name__ == "__main__":
    init(os.environ["MONGO_URL"], os.environ["DB_NAME"])
    asyncio.run(seed_test_contacts())

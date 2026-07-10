"""
Migration Script: Link Contractor Users to Contractor Businesses

Purpose: Connect contractor users to their respective contractor businesses
Strategy:
  1. Match by email domain (e.g., phil.coulson@shield.test → SHIELD Contractors LLC)
  2. Random assignment for contractors without matching email
  3. Log all assignments for admin review

Run: python backend/migrations/link_contractors_to_businesses.py
"""

import asyncio
import sys
import os
import random
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv('/app/backend/.env')

MONGO_URL = os.getenv('MONGO_URL')
DB_NAME = os.getenv('DB_NAME', 'simplimeta')


async def link_contractors_to_businesses():
    """
    Link contractor users to contractor businesses via email domain matching.
    Random assignment for unmatched contractors.
    """
    # Initialize database connection
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    print("=" * 80)
    print("CONTRACTOR → BUSINESS LINKAGE MIGRATION")
    print("=" * 80)
    print()
    
    # Step 1: Fetch all active contractor businesses
    print("Step 1: Fetching contractor businesses...")
    businesses = await db.contractor_businesses.find(
        {"status": "active"},
        {"_id": 0}
    ).to_list(100)
    
    if not businesses:
        print("❌ ERROR: No active contractor businesses found!")
        return {"error": "No businesses available"}
    
    print(f"✅ Found {len(businesses)} active contractor businesses:")
    for biz in businesses:
        email_domain = biz.get("email", "").split("@")[-1] if biz.get("email") else "N/A"
        print(f"   - {biz['business_name']} (Domain: {email_domain})")
    print()
    
    # Step 2: Fetch all contractor users
    print("Step 2: Fetching contractor users...")
    contractors = await db.users.find(
        {"role": "contractor"},
        {"_id": 0}
    ).to_list(500)
    
    print(f"✅ Found {len(contractors)} contractor users")
    print()
    
    # Step 3: Build email domain → business mapping
    print("Step 3: Building email domain mapping...")
    business_map = {}
    for biz in businesses:
        if biz.get("email"):
            domain = biz["email"].split("@")[-1]
            business_map[domain] = biz
            print(f"   Mapping: {domain} → {biz['business_name']}")
    print()
    
    # Step 4: Link contractors
    print("Step 4: Linking contractors to businesses...")
    print("-" * 80)
    
    linked_by_email = []
    randomly_assigned = []
    already_linked = []
    skipped = []
    
    for contractor in contractors:
        name = contractor.get("name", "Unknown")
        email = contractor.get("email", "")
        current_business_id = contractor.get("contractor_business_id")
        
        # Skip if already linked
        if current_business_id:
            already_linked.append({
                "name": name,
                "email": email,
                "business_id": current_business_id
            })
            print(f"⏭️  {name} - Already linked to business (skipping)")
            continue
        
        matched_business = None
        assignment_method = None
        
        # Try email domain matching
        if "@" in email:
            domain = email.split("@")[-1]
            matched_business = business_map.get(domain)
            
            if matched_business:
                assignment_method = "email_match"
                linked_by_email.append({
                    "name": name,
                    "email": email,
                    "business": matched_business["business_name"],
                    "domain": domain
                })
                print(f"✅ {name} ({email}) → {matched_business['business_name']} [EMAIL MATCH]")
        
        # Random assignment if no match
        if not matched_business:
            matched_business = random.choice(businesses)
            assignment_method = "random"
            randomly_assigned.append({
                "name": name,
                "email": email,
                "business": matched_business["business_name"]
            })
            print(f"🎲 {name} ({email}) → {matched_business['business_name']} [RANDOM]")
        
        # Update contractor in database
        try:
            result = await db.users.update_one(
                {"user_id": contractor["user_id"]},
                {"$set": {
                    "contractor_business_id": matched_business["business_id"],
                    "contractor_business_name": matched_business["business_name"],
                    "updated_at": datetime.now(timezone.utc)
                }}
            )
            
            if result.modified_count == 0:
                print(f"   ⚠️  Warning: Update returned 0 modified count")
        except Exception as e:
            print(f"   ❌ ERROR updating {name}: {str(e)}")
            skipped.append({"name": name, "email": email, "error": str(e)})
    
    print()
    print("=" * 80)
    print("MIGRATION SUMMARY")
    print("=" * 80)
    print(f"Total contractors processed: {len(contractors)}")
    print(f"✅ Linked by email domain: {len(linked_by_email)}")
    print(f"🎲 Randomly assigned: {len(randomly_assigned)}")
    print(f"⏭️  Already linked (skipped): {len(already_linked)}")
    print(f"❌ Errors/Skipped: {len(skipped)}")
    print()
    
    if linked_by_email:
        print("📧 Email Domain Matches:")
        for item in linked_by_email:
            print(f"   • {item['name']} ({item['domain']}) → {item['business']}")
        print()
    
    if randomly_assigned:
        print("🎲 Random Assignments (review these):")
        for item in randomly_assigned:
            print(f"   • {item['name']} ({item['email']}) → {item['business']}")
        print()
    
    if skipped:
        print("❌ Errors:")
        for item in skipped:
            print(f"   • {item['name']}: {item['error']}")
        print()
    
    result = {
        "total_contractors": len(contractors),
        "linked_by_email": len(linked_by_email),
        "randomly_assigned": len(randomly_assigned),
        "already_linked": len(already_linked),
        "errors": len(skipped),
        "linked_by_email_details": linked_by_email,
        "randomly_assigned_details": randomly_assigned,
        "message": "Migration complete"
    }
    
    # Close database connection
    client.close()
    
    return result


if __name__ == "__main__":
    print("\n🚀 Starting contractor linkage migration...\n")
    result = asyncio.run(link_contractors_to_businesses())
    print("\n✅ Migration completed successfully!")
    print(f"\nResult: {result['message']}")

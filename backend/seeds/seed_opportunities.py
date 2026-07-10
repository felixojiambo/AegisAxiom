"""
Seed script for Opportunity Management module
Creates test opportunities across different stages for development and testing.
"""
import asyncio
import sys
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
import random

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from database import init, get_db
from models import OPPORTUNITY_STAGES
from dotenv import load_dotenv

load_dotenv()

# Sample opportunity names and descriptions
OPPORTUNITY_NAMES = [
    ("National Retail Chain Expansion", "field_services", 250000, "Nationwide deployment of field technicians for retail locations"),
    ("Enterprise Cloud Migration", "professional_services", 180000, "Full cloud infrastructure migration and consulting"),
    ("Managed SD-WAN Solution", "wholesale_mrr", 45000, "Monthly recurring revenue for SD-WAN management"),
    ("Telecom Partner Referral", "referral_mrr", 15000, "Referral commission for telecom services"),
    ("Healthcare IT Modernization", "professional_services", 320000, "Legacy system modernization and training"),
    ("Multi-Site Security Upgrade", "field_services", 95000, "Physical security system installation across 12 sites"),
    ("SaaS Platform Integration", "professional_services", 75000, "Custom API integration and development"),
    ("Fiber Network Expansion", "wholesale_mrr", 28000, "Monthly revenue for fiber connectivity services"),
    ("Restaurant Chain POS System", "field_services", 140000, "Point-of-sale system installation and support"),
    ("Financial Services Compliance", "professional_services", 200000, "Compliance audit and remediation services"),
]

PRIORITIES = ["high", "medium", "low"]

async def seed_opportunities():
    """Create test opportunities across all stages."""
    db = get_db()
    
    # Get some accounts to assign
    accounts = await db.accounts.find({}, {"_id": 0, "account_id": 1, "name": 1}).limit(20).to_list(20)
    if not accounts:
        print("❌ No accounts found. Please seed accounts first.")
        return
    
    # Get admin user
    admin_user = await db.users.find_one({"role": "admin"}, {"_id": 0})
    if not admin_user:
        print("❌ No admin user found.")
        return
    
    # Check if opportunities already exist
    existing_count = await db.opportunities.count_documents({"is_test_data": True})
    if existing_count > 0:
        print(f"⚠️  Found {existing_count} existing test opportunities. Deleting...")
        result = await db.opportunities.delete_many({"is_test_data": True})
        print(f"✓ Deleted {result.deleted_count} test opportunities")
    
    print(f"\n🌱 Seeding Opportunity Management data...")
    print(f"   Using admin user: {admin_user['name']} ({admin_user['email']})")
    print(f"   Creating opportunities across {len(OPPORTUNITY_STAGES)} stages")
    
    opportunities = []
    now = datetime.now(timezone.utc)
    
    # Create 2-3 opportunities per stage
    for stage_idx, stage in enumerate(OPPORTUNITY_STAGES):
        num_opps = random.randint(2, 3)
        
        for i in range(num_opps):
            # Pick a random opportunity template
            opp_template = random.choice(OPPORTUNITY_NAMES)
            name, lob, base_amount, description = opp_template
            
            # Assign random account
            account = random.choice(accounts)
            
            # Get contacts for this account
            contacts = await db.contacts.find(
                {"account_id": account["account_id"]},
                {"_id": 0, "contact_id": 1, "name": 1}
            ).limit(5).to_list(5)
            
            contact = random.choice(contacts) if contacts else None
            
            # Vary the amount slightly
            amount = base_amount + random.randint(-20000, 50000)
            amount = max(10000, amount)  # Minimum $10k
            
            # Set priority based on amount
            if amount > 200000:
                priority = "high"
            elif amount > 100000:
                priority = random.choice(["high", "medium"])
            else:
                priority = random.choice(["medium", "low"])
            
            # Calculate proposal date (future for early stages, past for later stages)
            days_offset = random.randint(7, 60) if stage_idx < 5 else random.randint(-30, 0)
            proposal_date = (now + timedelta(days=days_offset)).strftime("%Y-%m-%d")
            
            opp_id = f"opp_seed_{stage_idx:02d}_{i:02d}_{random.randint(1000, 9999)}"
            
            opp = {
                "opportunity_id": opp_id,
                "name": f"{name} - {account['name'][:20]}",
                "account_id": account["account_id"],
                "account_name": account["name"],
                "contact_id": contact["contact_id"] if contact else None,
                "contact_name": contact["name"] if contact else None,
                "agent_of_record_id": None,
                "agent_of_record_name": None,
                "description": description,
                "desired_proposal_date": proposal_date,
                "priority": priority,
                "assigned_sales_ops_id": None,
                "assigned_sales_ops_name": None,
                "amount": float(amount),
                "stage": stage,
                "commission_payment_method": random.choice(["telarus", "appdirect", "direct", None]),
                "lines_of_business": [lob],
                "opportunity_owner_id": admin_user["user_id"],
                "opportunity_owner_name": admin_user["name"],
                "converted_to_deal_id": None,
                "converted_at": None,
                "notes": [],
                "attachments": [],
                "call_recordings": [],
                "is_test_data": True,
                "created_at": now - timedelta(days=random.randint(1, 90)),
                "updated_at": now - timedelta(days=random.randint(0, 7))
            }
            
            # Add some notes to opportunities in later stages
            if stage_idx >= 3:
                num_notes = random.randint(1, 3)
                for n in range(num_notes):
                    note_texts = [
                        "Initial discovery call completed. Customer very interested.",
                        "Sent preliminary pricing. Waiting for feedback.",
                        "Discussed technical requirements with their IT team.",
                        "Scheduled demo for next week.",
                        "Budget approved! Moving to proposal stage.",
                        "Competitor also in the running. Need to highlight our differentiators.",
                        "Customer requested modifications to original scope.",
                        "Legal review in progress on customer side.",
                    ]
                    opp["notes"].append({
                        "note_id": f"note_{random.randint(10000, 99999)}",
                        "text": random.choice(note_texts),
                        "created_by": admin_user["user_id"],
                        "created_by_name": admin_user["name"],
                        "created_at": (now - timedelta(days=random.randint(1, 30))).isoformat()
                    })
            
            opportunities.append(opp)
    
    # Insert all opportunities
    if opportunities:
        await db.opportunities.insert_many(opportunities)
        print(f"✓ Created {len(opportunities)} test opportunities")
    
    # Print summary by stage
    print("\n📊 Opportunity Distribution by Stage:")
    for stage in OPPORTUNITY_STAGES:
        count = sum(1 for o in opportunities if o["stage"] == stage)
        total_value = sum(o["amount"] for o in opportunities if o["stage"] == stage)
        print(f"   {stage:25s}: {count} opps, ${total_value:,.0f}")
    
    print(f"\n✅ Seed complete! Total opportunities: {len(opportunities)}")
    print(f"   Total pipeline value: ${sum(o['amount'] for o in opportunities):,.2f}")


if __name__ == "__main__":
    init(os.environ["MONGO_URL"], os.environ["DB_NAME"])
    asyncio.run(seed_opportunities())

"""
Script to tag accounts as partners based on closed won opportunities where they appear as agent_of_record.
Run this once to populate initial partner data.
"""
import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone

# MongoDB connection
MONGO_URL = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
DB_NAME = os.environ.get('DB_NAME', 'aegisaxiom_db')

async def tag_partner_accounts():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    print("🔍 Finding accounts to tag as partners...")
    print("=" * 60)
    
    # Find all closed won opportunities
    closed_won_opps = await db.opportunities.find(
        {"stage": "closed_won"},
        {"_id": 0, "agent_of_record_id": 1, "agent_of_record_name": 1, "amount": 1}
    ).to_list(1000)
    
    print(f"📊 Found {len(closed_won_opps)} closed won opportunities")
    
    # Collect unique agent of record IDs
    partner_account_ids = set()
    partner_revenue = {}
    partner_deal_counts = {}
    
    for opp in closed_won_opps:
        agent_id = opp.get('agent_of_record_id')
        if agent_id:
            partner_account_ids.add(agent_id)
            # Track revenue
            amount = opp.get('amount', 0)
            partner_revenue[agent_id] = partner_revenue.get(agent_id, 0) + amount
            # Track deal count
            partner_deal_counts[agent_id] = partner_deal_counts.get(agent_id, 0) + 1
    
    print(f"🎯 Found {len(partner_account_ids)} unique partner accounts (agent of record)")
    print()
    
    # Update each account
    updated_count = 0
    for account_id in partner_account_ids:
        # Get account details
        account = await db.accounts.find_one({"account_id": account_id}, {"_id": 0})
        
        if not account:
            print(f"⚠️  Account {account_id} not found in database, skipping...")
            continue
        
        revenue = partner_revenue.get(account_id, 0)
        deal_count = partner_deal_counts.get(account_id, 0)
        
        # Determine partner tier based on revenue
        tier = "bronze"
        if revenue >= 100000:
            tier = "gold"
        elif revenue >= 50000:
            tier = "silver"
        
        # Update account
        update_data = {
            "is_partner": True,
            "partner_tier": tier,
            "partner_since": datetime.now(timezone.utc),
            "partner_commission_rate": 10.0,  # Default 10%
            "partner_active_deals_count": deal_count,
            "partner_total_revenue": revenue,
            "partner_status": "active"
        }
        
        result = await db.accounts.update_one(
            {"account_id": account_id},
            {"$set": update_data}
        )
        
        if result.modified_count > 0:
            updated_count += 1
            print(f"✅ {account.get('name', account_id)}")
            print(f"   Tier: {tier} | Deals: {deal_count} | Revenue: ${revenue:,.2f}")
        
    print()
    print("=" * 60)
    print(f"✅ Successfully tagged {updated_count} accounts as partners!")
    
    client.close()

if __name__ == "__main__":
    asyncio.run(tag_partner_accounts())

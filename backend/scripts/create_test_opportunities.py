"""
Create test opportunities for Sales Operations Automations testing

Creates 8 test opportunities with various alert scenarios:
- Green/Healthy: 2 complete SOWs with good margins
- Yellow/Warning: 3 SOWs with minor issues (days in stage, date risks)
- Red/Critical: 3 SOWs with critical issues (missing contractors, negative margins)
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import sys

# Add parent directory to path
sys.path.append('/app/backend')

load_dotenv('/app/backend/.env')

MONGO_URL = os.environ.get('MONGO_URL')
DB_NAME = os.environ.get('DB_NAME', 'crm_db')

# Test account ID (support@simplimeta.io)
TEST_USER_ID = "test_support_user"
TEST_USER_EMAIL = "support@simplimeta.io"
TEST_ACCOUNT_ID = "test_account_001"

async def create_test_opportunities():
    """Create test opportunities with various alert scenarios"""
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    print("🧪 Creating test opportunities for automation alerts...\n")
    
    # Get test user
    test_user = await db.users.find_one({"email": TEST_USER_EMAIL}, {"_id": 0})
    if not test_user:
        print("❌ Test user not found. Please login first.")
        return
    
    test_opportunities = [
        # ============ GREEN/HEALTHY OPPORTUNITIES (2) ============
        {
            "opportunity_id": f"test_opp_green_001",
            "name": "✅ Healthy Enterprise Deal - TechCorp",
            "account_name": "TechCorp Industries",
            "account_id": TEST_ACCOUNT_ID,
            "amount": 500000,
            "probability": 80,
            "stage": "proposal_development",
            "opportunity_owner_name": "Sales Team",
            "opportunity_owner_id": TEST_USER_ID,
            "expected_close_date": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            "desired_proposal_date": (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(),
            "lines_of_business": ["professional_services"],
            "created_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        },
        {
            "opportunity_id": f"test_opp_green_002",
            "name": "✅ Smooth Sailing - DataFlow Inc",
            "account_name": "DataFlow Inc",
            "account_id": TEST_ACCOUNT_ID,
            "amount": 250000,
            "probability": 75,
            "stage": "proposal_development",
            "opportunity_owner_name": "Sales Team",
            "opportunity_owner_id": TEST_USER_ID,
            "expected_close_date": (datetime.now(timezone.utc) + timedelta(days=25)).isoformat(),
            "desired_proposal_date": (datetime.now(timezone.utc) + timedelta(days=12)).isoformat(),
            "lines_of_business": ["field_services"],
            "created_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        },
        
        # ============ YELLOW/WARNING OPPORTUNITIES (3) ============
        {
            "opportunity_id": f"test_opp_yellow_001",
            "name": "⚠️ Moderate Risk - SlowMove Corp",
            "account_name": "SlowMove Corp",
            "account_id": TEST_ACCOUNT_ID,
            "amount": 180000,
            "probability": 60,
            "stage": "proposal_development",
            "opportunity_owner_name": "Sales Team",
            "opportunity_owner_id": TEST_USER_ID,
            "expected_close_date": (datetime.now(timezone.utc) + timedelta(days=20)).isoformat(),
            "desired_proposal_date": (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(),
            "lines_of_business": ["professional_services"],
            "created_at": (datetime.now(timezone.utc) - timedelta(days=6)).isoformat(),  # 6 days in stage
            "updated_at": datetime.now(timezone.utc).isoformat()
        },
        {
            "opportunity_id": f"test_opp_yellow_002",
            "name": "⚠️ Date Risk - QuickFix LLC",
            "account_name": "QuickFix LLC",
            "account_id": TEST_ACCOUNT_ID,
            "amount": 95000,
            "probability": 70,
            "stage": "proposal_development",
            "opportunity_owner_name": "Sales Team",
            "opportunity_owner_id": TEST_USER_ID,
            "expected_close_date": (datetime.now(timezone.utc) + timedelta(days=15)).isoformat(),
            "desired_proposal_date": (datetime.now(timezone.utc) + timedelta(days=8)).isoformat(),
            "lines_of_business": ["field_services"],
            "created_at": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        },
        {
            "opportunity_id": f"test_opp_yellow_003",
            "name": "⚠️ Aging Deal - RetailMax",
            "account_name": "RetailMax Partners",
            "account_id": TEST_ACCOUNT_ID,
            "amount": 320000,
            "probability": 65,
            "stage": "proposal_development",
            "opportunity_owner_name": "Sales Team",
            "opportunity_owner_id": TEST_USER_ID,
            "expected_close_date": (datetime.now(timezone.utc) + timedelta(days=18)).isoformat(),
            "desired_proposal_date": (datetime.now(timezone.utc) + timedelta(days=9)).isoformat(),
            "lines_of_business": ["professional_services", "field_services"],
            "created_at": (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),  # 7 days in stage
            "updated_at": datetime.now(timezone.utc).isoformat()
        },
        
        # ============ RED/CRITICAL OPPORTUNITIES (3) ============
        {
            "opportunity_id": f"test_opp_red_001",
            "name": "🚨 Missing Contractor - CrisisMode Inc",
            "account_name": "CrisisMode Inc",
            "account_id": TEST_ACCOUNT_ID,
            "amount": 450000,
            "probability": 85,
            "stage": "proposal_development",
            "opportunity_owner_name": "Sales Team",
            "opportunity_owner_id": TEST_USER_ID,
            "expected_close_date": (datetime.now(timezone.utc) + timedelta(days=22)).isoformat(),
            "desired_proposal_date": (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(),
            "lines_of_business": ["professional_services"],
            "created_at": (datetime.now(timezone.utc) - timedelta(days=4)).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        },
        {
            "opportunity_id": f"test_opp_red_002",
            "name": "🚨 Negative Margin Alert - LowBall LLC",
            "account_name": "LowBall LLC",
            "account_id": TEST_ACCOUNT_ID,
            "amount": 85000,
            "probability": 90,
            "stage": "proposal_development",
            "opportunity_owner_name": "Sales Team",
            "opportunity_owner_id": TEST_USER_ID,
            "expected_close_date": (datetime.now(timezone.utc) + timedelta(days=28)).isoformat(),
            "desired_proposal_date": (datetime.now(timezone.utc) + timedelta(days=12)).isoformat(),
            "lines_of_business": ["field_services"],
            "created_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        },
        {
            "opportunity_id": f"test_opp_red_003",
            "name": "🚨 Multiple Blockers - ProblemChild Co",
            "account_name": "ProblemChild Co",
            "account_id": TEST_ACCOUNT_ID,
            "amount": 275000,
            "probability": 55,
            "stage": "proposal_development",
            "opportunity_owner_name": "Sales Team",
            "opportunity_owner_id": TEST_USER_ID,
            "expected_close_date": (datetime.now(timezone.utc) + timedelta(days=16)).isoformat(),
            "desired_proposal_date": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "lines_of_business": ["professional_services"],
            "created_at": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),  # 8 days in stage
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
    ]
    
    # Insert opportunities
    for opp in test_opportunities:
        existing = await db.opportunities.find_one({"opportunity_id": opp["opportunity_id"]})
        if existing:
            print(f"⏭️  Skipping {opp['name']} - already exists")
        else:
            await db.opportunities.insert_one(opp)
            print(f"✅ Created: {opp['name']} (${opp['amount']:,})")
    
    print(f"\n✅ Test opportunities creation complete!")
    print(f"\nNext step: Create SOW tasks for these opportunities...")
    
    # Now create SOW tasks for each opportunity
    await create_sow_tasks(db, test_opportunities)
    
    print(f"\n🎉 All test data created! Navigate to Sales Ops → Solutions Design to see them.")

async def create_sow_tasks(db, opportunities):
    """Create SOW tasks with various configurations to trigger alerts"""
    
    print("\n📝 Creating SOW tasks...")
    
    sow_configs = [
        # GREEN/HEALTHY (2)
        {
            "opp_id": "test_opp_green_001",
            "lob": "professional_services",
            "status": "complete",
            "expected_completion_date": (datetime.now(timezone.utc) + timedelta(days=8)).isoformat(),
            "has_critical_blockers": False,
            "resource_ids": ["res_001"],  # Will create with contractor assigned
            "margin": 35.0  # Good margin
        },
        {
            "opp_id": "test_opp_green_002",
            "lob": "field_services",
            "status": "complete",
            "expected_completion_date": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "has_critical_blockers": False,
            "resource_ids": ["res_002"],
            "margin": 42.0
        },
        
        # YELLOW/WARNING (3)
        {
            "opp_id": "test_opp_yellow_001",
            "lob": "professional_services",
            "status": "in_progress",
            "expected_completion_date": (datetime.now(timezone.utc) + timedelta(days=6)).isoformat(),
            "has_critical_blockers": False,
            "resource_ids": ["res_003"],
            "margin": 18.0  # Low but positive
        },
        {
            "opp_id": "test_opp_yellow_002",
            "lob": "field_services",
            "status": "in_progress",
            "expected_completion_date": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
            "has_critical_blockers": False,
            "resource_ids": ["res_004"],
            "margin": 22.0
        },
        {
            "opp_id": "test_opp_yellow_003",
            "lob": "professional_services",
            "status": "in_progress",
            "expected_completion_date": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
            "has_critical_blockers": False,
            "resource_ids": ["res_005"],
            "margin": 25.0
        },
        
        # RED/CRITICAL (3)
        {
            "opp_id": "test_opp_red_001",
            "lob": "professional_services",
            "status": "in_progress",  # Will be shown in Critical Issues section
            "expected_completion_date": (datetime.now(timezone.utc) + timedelta(days=8)).isoformat(),
            "has_critical_blockers": True,
            "resource_ids": ["res_missing_contractor"],  # No contractor assigned
            "margin": 28.0
        },
        {
            "opp_id": "test_opp_red_002",
            "lob": "field_services",
            "status": "in_progress",
            "expected_completion_date": (datetime.now(timezone.utc) + timedelta(days=9)).isoformat(),
            "has_critical_blockers": True,
            "resource_ids": ["res_negative_margin"],  # Negative margin resource
            "margin": -5.0  # NEGATIVE MARGIN - triggers critical alert
        },
        {
            "opp_id": "test_opp_red_003",
            "lob": "professional_services",
            "status": "in_progress",
            "expected_completion_date": (datetime.now(timezone.utc) + timedelta(days=4)).isoformat(),
            "has_critical_blockers": True,
            "resource_ids": ["res_multi_issue"],
            "margin": 12.0
        }
    ]
    
    for config in sow_configs:
        opp = next((o for o in opportunities if o["opportunity_id"] == config["opp_id"]), None)
        if not opp:
            continue
        
        sow_doc = {
            "sow_id": f"sow_{config['opp_id']}_{config['lob']}",
            "opportunity_id": config["opp_id"],
            "opportunity_name": opp["name"],
            "line_of_business": config["lob"],
            "status": config["status"],
            "expected_completion_date": config["expected_completion_date"],
            "desired_proposal_date": opp["desired_proposal_date"],
            "selected_resource_assignment_ids": config["resource_ids"],
            "has_critical_blockers": config["has_critical_blockers"],
            "assigned_to": None,
            "created_at": opp["created_at"],
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        existing = await db.sow_tasks.find_one({"sow_id": sow_doc["sow_id"]})
        if existing:
            print(f"⏭️  SOW exists for {opp['name'][:30]}")
        else:
            await db.sow_tasks.insert_one(sow_doc)
            print(f"✅ SOW created for {opp['name'][:40]} - Status: {config['status']}, Margin: {config['margin']}%")
            
            # Create resource assignments
            await create_resource_assignments(db, config, sow_doc)
    
    print(f"\n✅ SOW tasks created!")

async def create_resource_assignments(db, config, sow):
    """Create resource assignments with various configurations"""
    
    for res_id in config["resource_ids"]:
        # Determine contractor assignment based on test scenario
        if "missing_contractor" in res_id:
            contractor_user_id = None  # MISSING CONTRACTOR - triggers critical alert
            contractor_name = None
        else:
            contractor_user_id = "contractor_test_001"
            contractor_name = "Test Contractor"
        
        # Calculate rates for margin
        if config["margin"] < 0:
            # Negative margin: customer pays $100/hr, contractor costs $110/hr
            customer_rate = 100
            contractor_rate = 110
        elif config["margin"] < 20:
            # Low margin
            customer_rate = 100
            contractor_rate = 85
        else:
            # Good margin
            customer_rate = 150
            contractor_rate = 90
        
        resource_doc = {
            "assignment_id": f"assign_{res_id}_{sow['sow_id']}",
            "opportunity_id": sow["opportunity_id"],
            "sow_id": sow["sow_id"],
            "line_of_business": sow["line_of_business"],
            "resource_type": "QA Engineer" if "missing" in res_id else "Software Developer",
            "quantity": 1,
            "pay_type": "hourly",
            "billing_method": "bank_of_hours",
            
            # Customer-facing (left side)
            "customer_rate_per_hour": customer_rate,
            "customer_total_hours": 160,
            "customer_amount": customer_rate * 160,
            "payment_terms": "Net 30",
            
            # Contractor-facing (right side)
            "contractor_user_id": contractor_user_id,
            "contractor_name": contractor_name,
            "contractor_rate_per_hour": contractor_rate,
            "contractor_total_hours": 160,
            "contractor_amount": contractor_rate * 160,
            "contractor_billing_terms": "Monthly",
            
            # Margin calculation
            "margin_amount": (customer_rate - contractor_rate) * 160,
            "margin_percentage": config["margin"],
            
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        existing = await db.opportunity_resource_assignments.find_one({"assignment_id": resource_doc["assignment_id"]})
        if not existing:
            await db.opportunity_resource_assignments.insert_one(resource_doc)
            print(f"  → Resource assignment: {resource_doc['resource_type']} (Margin: {config['margin']}%, Contractor: {contractor_name or 'MISSING'})")

if __name__ == "__main__":
    asyncio.run(create_test_opportunities())

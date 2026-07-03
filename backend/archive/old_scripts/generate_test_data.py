"""
Test Data Generator for AegisAxiom
Creates comprehensive test data for the complete workflow:
Deals → Contracts → Resources → Time Entries → AP Bills

Run with: python generate_test_data.py
"""

import asyncio
from datetime import datetime, timezone, timedelta
from motor.motor_asyncio import AsyncIOMotorClient
import os
from uuid import uuid4

# Database connection
MONGO_URL = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
DB_NAME = os.environ.get('DB_NAME', 'crm_db')

def gen_id(prefix=""):
    return f"{prefix}{str(uuid4())[:8]}"

async def main():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    print("🚀 Starting test data generation...")
    
    # Get admin user
    admin = await db.users.find_one({"role": "admin"})
    if not admin:
        admin = await db.users.find_one()
    admin_id = admin["user_id"] if admin else "admin"
    
    # ============ STEP 1: Create Test Accounts ============
    print("\n📊 Creating test accounts...")
    
    test_accounts = [
        {
            "account_id": gen_id("acc_"),
            "account_name": "Wayne Enterprises (Test)",
            "industry": "Security & Infrastructure",
            "source": "test",
            "status": "active",
            "annual_revenue": 50000000,
            "employee_count": 500,
            "created_at": datetime.now(timezone.utc)
        },
        {
            "account_id": gen_id("acc_"),
            "account_name": "Stark Industries (Test)",
            "industry": "Technology & Defense",
            "source": "test",
            "status": "active",
            "annual_revenue": 75000000,
            "employee_count": 800,
            "created_at": datetime.now(timezone.utc)
        },
        {
            "account_id": gen_id("acc_"),
            "account_name": "LexCorp (Test)",
            "industry": "Field Services",
            "source": "test",
            "status": "active",
            "annual_revenue": 30000000,
            "employee_count": 300,
            "created_at": datetime.now(timezone.utc)
        }
    ]
    
    for account in test_accounts:
        await db.accounts.insert_one(account)
        print(f"  ✓ Created account: {account['account_name']}")
    
    # ============ STEP 2: Create Contractor Businesses ============
    print("\n🏢 Creating contractor businesses...")
    
    contractor_businesses = [
        {
            "business_id": gen_id("cb_"),
            "business_name": "Elite Security Services LLC (Test)",
            "contact_name": "John Security",
            "email": "contact@elitesec-test.com",
            "phone": "(555) 100-0001",
            "billing_frequency": "weekly",
            "payment_terms": "net_30",
            "payment_method": "ACH",
            "w9_on_file": True,
            "is_1099_eligible": True,
            "status": "active",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
        },
        {
            "business_id": gen_id("cb_"),
            "business_name": "Tech Contractors Inc (Test)",
            "contact_name": "Jane Developer",
            "email": "billing@techcontractors-test.com",
            "phone": "(555) 200-0002",
            "billing_frequency": "biweekly",
            "payment_terms": "net_45",
            "payment_method": "Wire",
            "w9_on_file": True,
            "is_1099_eligible": True,
            "status": "active",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
        }
    ]
    
    for business in contractor_businesses:
        await db.contractor_businesses.insert_one(business)
        print(f"  ✓ Created business: {business['business_name']}")
    
    # ============ STEP 3: Create Test Contractors ============
    print("\n👷 Creating test contractors...")
    
    test_contractors = [
        # Elite Security Services contractors
        {
            "user_id": gen_id("user_"),
            "email": "alice.security@test.com",
            "name": "Alice Security (Test)",
            "role": "contractor",
            "contractor_business_id": contractor_businesses[0]["business_id"],
            "is_active": True,
            "cost_rate": 100,
            "created_at": datetime.now(timezone.utc)
        },
        {
            "user_id": gen_id("user_"),
            "email": "bob.guard@test.com",
            "name": "Bob Guard (Test)",
            "role": "contractor",
            "contractor_business_id": contractor_businesses[0]["business_id"],
            "is_active": True,
            "cost_rate": 95,
            "created_at": datetime.now(timezone.utc)
        },
        # Tech Contractors Inc contractors
        {
            "user_id": gen_id("user_"),
            "email": "charlie.dev@test.com",
            "name": "Charlie Developer (Test)",
            "role": "contractor",
            "contractor_business_id": contractor_businesses[1]["business_id"],
            "is_active": True,
            "cost_rate": 125,
            "created_at": datetime.now(timezone.utc)
        },
        {
            "user_id": gen_id("user_"),
            "email": "diana.engineer@test.com",
            "name": "Diana Engineer (Test)",
            "role": "contractor",
            "contractor_business_id": contractor_businesses[1]["business_id"],
            "is_active": True,
            "cost_rate": 120,
            "created_at": datetime.now(timezone.utc)
        },
        # Solo contractor (no business)
        {
            "user_id": gen_id("user_"),
            "email": "evan.solo@test.com",
            "name": "Evan Solo (Test)",
            "role": "contractor",
            "contractor_business_id": None,
            "is_active": True,
            "cost_rate": 110,
            "created_at": datetime.now(timezone.utc)
        }
    ]
    
    for contractor in test_contractors:
        await db.users.insert_one(contractor)
        print(f"  ✓ Created contractor: {contractor['name']}")
    
    # ============ STEP 4: Create Test Deals ============
    print("\n💼 Creating test deals...")
    
    test_deals = [
        {
            "deal_id": gen_id("deal_"),
            "name": "Watchtower Security Implementation (Test)",
            "account_id": test_accounts[0]["account_id"],
            "account_name": test_accounts[0]["account_name"],
            "amount": 250000,
            "stage": "Closed Won",
            "close_date": "2026-01-15",
            "deal_type": "Professional Services",
            "source": "test",
            "created_at": datetime.now(timezone.utc)
        },
        {
            "deal_id": gen_id("deal_"),
            "name": "Arc Reactor Maintenance (Test)",
            "account_id": test_accounts[1]["account_id"],
            "account_name": test_accounts[1]["account_name"],
            "amount": 180000,
            "stage": "Closed Won",
            "close_date": "2026-01-20",
            "deal_type": "Field Services",
            "source": "test",
            "created_at": datetime.now(timezone.utc)
        },
        {
            "deal_id": gen_id("deal_"),
            "name": "LexCorp Campus Security (Test)",
            "account_id": test_accounts[2]["account_id"],
            "account_name": test_accounts[2]["account_name"],
            "amount": 120000,
            "stage": "Closed Won",
            "close_date": "2026-01-25",
            "deal_type": "Field Services",
            "source": "test",
            "created_at": datetime.now(timezone.utc)
        }
    ]
    
    for deal in test_deals:
        await db.deals.insert_one(deal)
        print(f"  ✓ Created deal: {deal['name']}")
    
    # ============ STEP 5: Create Test Contracts ============
    print("\n📝 Creating test contracts...")
    
    test_contracts = [
        # BOH Professional Services
        {
            "contract_id": gen_id("cont_"),
            "name": "Watchtower BOH - Professional Services (Test)",
            "account_id": test_accounts[0]["account_id"],
            "account_name": test_accounts[0]["account_name"],
            "deal_ids": [test_deals[0]["deal_id"]],
            "contract_type": "boh",
            "service_category": "professional_services",
            "status": "active",
            "resource_types": [
                {
                    "resource_type_id": gen_id("rt_"),
                    "name": "Senior Security Analyst",
                    "description": "Expert security analysis and planning",
                    "hours": 500,
                    "used_hours": 0,
                    "billable_rate": 200,
                    "cost_rate": 100
                },
                {
                    "resource_type_id": gen_id("rt_"),
                    "name": "Security Engineer",
                    "description": "Implementation and configuration",
                    "hours": 800,
                    "used_hours": 0,
                    "billable_rate": 175,
                    "cost_rate": 95
                }
            ],
            "start_date": "2026-02-01",
            "end_date": "2026-12-31",
            "created_at": datetime.now(timezone.utc)
        },
        # BOA Field Services
        {
            "contract_id": gen_id("cont_"),
            "name": "Arc Reactor BOA - Field Services (Test)",
            "account_id": test_accounts[1]["account_id"],
            "account_name": test_accounts[1]["account_name"],
            "deal_ids": [test_deals[1]["deal_id"]],
            "contract_type": "boa",
            "service_category": "field_services",
            "status": "active",
            "contract_value": 180000,
            "used_amount": 0,
            "resource_types": [
                {
                    "resource_type_id": gen_id("rt_"),
                    "name": "Field Technician",
                    "description": "On-site maintenance and repairs",
                    "billable_rate": 150,
                    "cost_rate": 125
                },
                {
                    "resource_type_id": gen_id("rt_"),
                    "name": "Technical Lead",
                    "description": "Project oversight and technical guidance",
                    "billable_rate": 180,
                    "cost_rate": 120
                }
            ],
            "start_date": "2026-02-01",
            "end_date": "2026-08-31",
            "created_at": datetime.now(timezone.utc)
        },
        # BOH Field Services
        {
            "contract_id": gen_id("cont_"),
            "name": "LexCorp BOH - Field Services (Test)",
            "account_id": test_accounts[2]["account_id"],
            "account_name": test_accounts[2]["account_name"],
            "deal_ids": [test_deals[2]["deal_id"]],
            "contract_type": "boh",
            "service_category": "field_services",
            "status": "active",
            "resource_types": [
                {
                    "resource_type_id": gen_id("rt_"),
                    "name": "Security Guard",
                    "description": "On-site security personnel",
                    "hours": 2000,
                    "used_hours": 0,
                    "billable_rate": 125,
                    "cost_rate": 110
                }
            ],
            "start_date": "2026-02-01",
            "end_date": "2026-12-31",
            "created_at": datetime.now(timezone.utc)
        }
    ]
    
    for contract in test_contracts:
        await db.contracts.insert_one(contract)
        print(f"  ✓ Created contract: {contract['name']}")
    
    # ============ STEP 6: Create Projects ============
    print("\n🗂️ Creating test projects...")
    
    test_projects = [
        {
            "project_id": gen_id("proj_"),
            "name": "Watchtower Phase 1 - Infrastructure (Test)",
            "contract_id": test_contracts[0]["contract_id"],
            "account_id": test_accounts[0]["account_id"],
            "status": "active",
            "start_date": "2026-02-01",
            "end_date": "2026-06-30",
            "created_at": datetime.now(timezone.utc)
        },
        {
            "project_id": gen_id("proj_"),
            "name": "Arc Reactor Quarterly Maintenance (Test)",
            "contract_id": test_contracts[1]["contract_id"],
            "account_id": test_accounts[1]["account_id"],
            "status": "active",
            "start_date": "2026-02-01",
            "end_date": "2026-04-30",
            "created_at": datetime.now(timezone.utc)
        },
        {
            "project_id": gen_id("proj_"),
            "name": "LexCorp Campus Security Patrol (Test)",
            "contract_id": test_contracts[2]["contract_id"],
            "account_id": test_accounts[2]["account_id"],
            "status": "active",
            "start_date": "2026-02-01",
            "end_date": "2026-12-31",
            "created_at": datetime.now(timezone.utc)
        }
    ]
    
    for project in test_projects:
        await db.projects.insert_one(project)
        print(f"  ✓ Created project: {project['name']}")
    
    # ============ STEP 7: Assign Resources to Contracts ============
    print("\n👤 Assigning resources to contracts...")
    
    # Contract 0: Watchtower BOH (Professional Services)
    contract_0_rt = test_contracts[0]["resource_types"]
    await db.contracts.update_one(
        {"contract_id": test_contracts[0]["contract_id"]},
        {"$set": {
            f"resource_types.0.assigned_users": [
                {
                    "user_id": test_contractors[0]["user_id"],
                    "user_name": test_contractors[0]["name"],
                    "cost_rate": test_contractors[0]["cost_rate"]
                },
                {
                    "user_id": test_contractors[1]["user_id"],
                    "user_name": test_contractors[1]["name"],
                    "cost_rate": test_contractors[1]["cost_rate"]
                }
            ],
            f"resource_types.1.assigned_users": [
                {
                    "user_id": test_contractors[2]["user_id"],
                    "user_name": test_contractors[2]["name"],
                    "cost_rate": test_contractors[2]["cost_rate"]
                }
            ]
        }}
    )
    print(f"  ✓ Assigned contractors to {test_contracts[0]['name']}")
    
    # Contract 1: Arc Reactor BOA (Field Services)
    await db.contracts.update_one(
        {"contract_id": test_contracts[1]["contract_id"]},
        {"$set": {
            f"resource_types.0.assigned_users": [
                {
                    "user_id": test_contractors[2]["user_id"],
                    "user_name": test_contractors[2]["name"],
                    "cost_rate": test_contractors[2]["cost_rate"]
                },
                {
                    "user_id": test_contractors[3]["user_id"],
                    "user_name": test_contractors[3]["name"],
                    "cost_rate": test_contractors[3]["cost_rate"]
                }
            ],
            f"resource_types.1.assigned_users": [
                {
                    "user_id": test_contractors[4]["user_id"],
                    "user_name": test_contractors[4]["name"],
                    "cost_rate": test_contractors[4]["cost_rate"]
                }
            ]
        }}
    )
    print(f"  ✓ Assigned contractors to {test_contracts[1]['name']}")
    
    # Contract 2: LexCorp BOH (Field Services)
    await db.contracts.update_one(
        {"contract_id": test_contracts[2]["contract_id"]},
        {"$set": {
            f"resource_types.0.assigned_users": [
                {
                    "user_id": test_contractors[0]["user_id"],
                    "user_name": test_contractors[0]["name"],
                    "cost_rate": test_contractors[0]["cost_rate"]
                },
                {
                    "user_id": test_contractors[4]["user_id"],
                    "user_name": test_contractors[4]["name"],
                    "cost_rate": test_contractors[4]["cost_rate"]
                }
            ]
        }}
    )
    print(f"  ✓ Assigned contractors to {test_contracts[2]['name']}")
    
    # ============ STEP 8: Create Time Entries ============
    print("\n⏱️ Creating test time entries...")
    
    # Helper to create time entries
    def create_time_entry(user, project, contract, date, hours, status, notes=""):
        return {
            "time_entry_id": gen_id("te_"),
            "user_id": user["user_id"],
            "user_name": user["name"],
            "project_id": project["project_id"],
            "project_name": project["name"],
            "contract_id": contract["contract_id"],
            "contract_name": contract["name"],
            "account_id": contract["account_id"],
            "account_name": contract["account_name"],
            "date": date,
            "duration_hours": hours,
            "cost_rate": user["cost_rate"],
            "status": status,
            "notes": notes,
            "is_test_data": True,  # Mark as test data
            "created_at": datetime.now(timezone.utc),
            "submitted_at": datetime.now(timezone.utc) if status != "draft" else None,
            "approved_at": datetime.now(timezone.utc) if status == "approved" else None,
            "approved_by": admin_id if status == "approved" else None
        }
    
    time_entries = []
    
    # Week 1: Jan 27 - Feb 2, 2026 (APPROVED - for AP bill generation)
    week1_dates = ["2026-01-27", "2026-01-28", "2026-01-29", "2026-01-30", "2026-01-31"]
    for date in week1_dates:
        # Watchtower project
        time_entries.append(create_time_entry(
            test_contractors[0], test_projects[0], test_contracts[0],
            date, 8, "approved", "Security assessment and planning"
        ))
        time_entries.append(create_time_entry(
            test_contractors[1], test_projects[0], test_contracts[0],
            date, 8, "approved", "Infrastructure review"
        ))
        
        # Arc Reactor project
        time_entries.append(create_time_entry(
            test_contractors[2], test_projects[1], test_contracts[1],
            date, 8, "approved", "Maintenance checks"
        ))
    
    # Week 2: Feb 3-9, 2026 (SUBMITTED - waiting for PM approval)
    week2_dates = ["2026-02-03", "2026-02-04", "2026-02-05", "2026-02-06", "2026-02-07"]
    for date in week2_dates:
        # Watchtower project
        time_entries.append(create_time_entry(
            test_contractors[0], test_projects[0], test_contracts[0],
            date, 8, "submitted", "Network security configuration"
        ))
        time_entries.append(create_time_entry(
            test_contractors[2], test_projects[0], test_contracts[0],
            date, 8, "submitted", "System integration work"
        ))
        
        # LexCorp project
        time_entries.append(create_time_entry(
            test_contractors[4], test_projects[2], test_contracts[2],
            date, 8, "submitted", "Security patrol - North campus"
        ))
    
    # Week 3: Feb 10-14, 2026 (DRAFT - not yet submitted)
    week3_dates = ["2026-02-10", "2026-02-11", "2026-02-12"]
    for date in week3_dates:
        # Arc Reactor project
        time_entries.append(create_time_entry(
            test_contractors[3], test_projects[1], test_contracts[1],
            date, 8, "draft", "Equipment calibration"
        ))
        time_entries.append(create_time_entry(
            test_contractors[4], test_projects[1], test_contracts[1],
            date, 6, "draft", "Technical oversight"
        ))
    
    # Insert all time entries
    for entry in time_entries:
        await db.time_entries.insert_one(entry)
    
    print(f"  ✓ Created {len(time_entries)} time entries:")
    print(f"    - Approved: {len([e for e in time_entries if e['status'] == 'approved'])}")
    print(f"    - Submitted (pending PM): {len([e for e in time_entries if e['status'] == 'submitted'])}")
    print(f"    - Draft: {len([e for e in time_entries if e['status'] == 'draft'])}")
    
    # ============ STEP 9: Generate AP Bills from Approved Time ============
    print("\n💰 Generating AP bills from approved time entries...")
    
    # Group approved entries by business + week
    approved_entries = [e for e in time_entries if e["status"] == "approved"]
    
    # Week 1 (Jan 27 - Feb 2)
    week1_entries = [e for e in approved_entries if e["date"] <= "2026-02-02"]
    
    # Group by contractor business
    business_groups = {}
    for entry in week1_entries:
        user = next(c for c in test_contractors if c["user_id"] == entry["user_id"])
        business_id = user.get("contractor_business_id") or user["user_id"]
        
        if business_id not in business_groups:
            business_groups[business_id] = []
        business_groups[business_id].append(entry)
    
    # Create AP bills
    bill_number = 1
    for business_id, entries in business_groups.items():
        # Get business or contractor info
        if business_id.startswith("cb_"):
            business = next(b for b in contractor_businesses if b["business_id"] == business_id)
            contractor_name = business["business_name"]
            business_name = business["business_name"]
        else:
            contractor = next(c for c in test_contractors if c["user_id"] == business_id)
            contractor_name = contractor["name"]
            business_name = None
        
        # Create line items
        line_items = []
        time_entry_ids = []
        
        for entry in entries:
            line_items.append({
                "contractor_name": entry["user_name"],
                "project_name": entry["project_name"],
                "contract_name": entry["contract_name"],
                "hours": entry["duration_hours"],
                "rate": entry["cost_rate"],
                "amount": entry["duration_hours"] * entry["cost_rate"]
            })
            time_entry_ids.append(entry["time_entry_id"])
        
        total = sum(item["amount"] for item in line_items)
        
        bill = {
            "bill_id": gen_id("bill_"),
            "bill_number": f"AP-2026-{bill_number:03d}",
            "contractor_business_id": business_id if business_id.startswith("cb_") else None,
            "contractor_business_name": business_name,
            "contractor_user_id": None if business_id.startswith("cb_") else business_id,
            "contractor_name": contractor_name,
            "period_start": "2026-01-27",
            "period_end": "2026-02-02",
            "billing_frequency": "weekly",
            "line_items": line_items,
            "time_entry_ids": time_entry_ids,
            "subtotal": total,
            "total": total,
            "status": "pending_finance_review",
            "payment_terms": "net_30",
            "payment_method": "ACH",
            "notes": "Auto-generated from approved time entries (TEST DATA)",
            "created_at": datetime.now(timezone.utc),
            "created_by": "system"
        }
        
        await db.ap_bills.insert_one(bill)
        print(f"  ✓ Created bill {bill['bill_number']} for {contractor_name}: {total:,.2f}")
        bill_number += 1
    
    print("\n✅ Test data generation complete!")
    print("\n📋 Summary:")
    print(f"  - Accounts: {len(test_accounts)}")
    print(f"  - Contractor Businesses: {len(contractor_businesses)}")
    print(f"  - Contractors: {len(test_contractors)}")
    print(f"  - Deals: {len(test_deals)}")
    print(f"  - Contracts: {len(test_contracts)}")
    print(f"  - Projects: {len(test_projects)}")
    print(f"  - Time Entries: {len(time_entries)}")
    print(f"  - AP Bills: {bill_number - 1}")
    
    print("\n🎯 What you can now test:")
    print("  1. Finance Portal → AP (Bills) tab - See pending bills for review")
    print("  2. Time Management → PM view - Approve submitted timesheets")
    print("  3. Contracts → View contract usage and resource assignments")
    print("  4. Projects → See time logged per project")
    print("  5. Admin → Contractor Businesses - Manage billing terms")
    
    client.close()

if __name__ == "__main__":
    asyncio.run(main())

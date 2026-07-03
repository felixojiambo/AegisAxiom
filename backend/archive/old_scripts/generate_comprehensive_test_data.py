"""
COMPREHENSIVE Test Data Generator for AegisAxiom
Follows TEST_DATA_CONVENTIONS.md with multiple fictional universes

Fictional Universe Mapping:
- Sales Reps: The Office characters (business-appropriate)
- Partners: Harry Potter characters (lead sources)
- Companies/Accounts: DC & Marvel universes
- Contractors: Marvel characters
- Contractor Businesses: Marvel universe businesses

Generates complete workflow:
Deal → Contract → Resources → Projects/Jobs → Tasks → Time Entries → AP Bills

Run with: python generate_comprehensive_test_data.py
"""

import asyncio
from datetime import datetime, timezone, timedelta
from motor.motor_asyncio import AsyncIOMotorClient
import os
from uuid import uuid4
import random

# Database connection
MONGO_URL = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
DB_NAME = os.environ.get('DB_NAME', 'test_database')

print(f"📊 Connecting to database: {DB_NAME}\n")

def gen_id(prefix=""):
    return f"{prefix}{str(uuid4())[:8]}"

async def main():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    print("🚀 COMPREHENSIVE TEST DATA GENERATION")
    print("=" * 60)
    
    # Get admin user
    admin = await db.users.find_one({"role": "admin"})
    admin_id = admin["user_id"] if admin else "admin"
    
    # ============================================================
    # STEP 1: Create Sales Rep Users (The Office Universe)
    # ============================================================
    print("\n👔 STEP 1: Creating Sales Representatives (The Office)")
    print("-" * 60)
    
    sales_reps = [
        {
            "user_id": gen_id("user_"),
            "email": "michael.scott@dundermifflin.test",
            "name": "Michael Scott",  # The Office - Regional Manager
            "role": "sales",
            "permissions": ["deals.view", "deals.edit", "contracts.view"],
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "user_id": gen_id("user_"),
            "email": "jim.halpert@dundermifflin.test",
            "name": "Jim Halpert",  # The Office - Sales Rep
            "role": "sales",
            "permissions": ["deals.view", "deals.edit", "contracts.view"],
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "user_id": gen_id("user_"),
            "email": "dwight.schrute@dundermifflin.test",
            "name": "Dwight Schrute",  # The Office - Sales Rep
            "role": "sales",
            "permissions": ["deals.view", "deals.edit", "contracts.view"],
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        }
    ]
    
    for rep in sales_reps:
        await db.users.insert_one(rep)
        print(f"  ✓ {rep['name']} ({rep['email']})")
    
    # ============================================================
    # STEP 2: Create Partner Users (Harry Potter Universe)
    # ============================================================
    print("\n🤝 STEP 2: Creating Partners/Lead Sources (Harry Potter)")
    print("-" * 60)
    
    partners = [
        {
            "user_id": gen_id("user_"),
            "email": "harry.potter@hogwarts.test",
            "name": "Harry Potter",  # Harry Potter - The Chosen One
            "role": "partner",
            "permissions": ["deals.view"],
            "is_active": True,
            "partner_company": "Hogwarts School of Business",
            "commission_rate": 10.0,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "user_id": gen_id("user_"),
            "email": "hermione.granger@ministry.test",
            "name": "Hermione Granger",  # Harry Potter - Brilliant witch
            "role": "partner",
            "permissions": ["deals.view"],
            "is_active": True,
            "partner_company": "Ministry of Magic Consulting",
            "commission_rate": 12.0,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "user_id": gen_id("user_"),
            "email": "albus.dumbledore@hogwarts.test",
            "name": "Albus Dumbledore",  # Harry Potter - Headmaster
            "role": "partner",
            "permissions": ["deals.view"],
            "is_active": True,
            "partner_company": "Order of the Phoenix Partners",
            "commission_rate": 15.0,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        }
    ]
    
    for partner in partners:
        await db.users.insert_one(partner)
        print(f"  ✓ {partner['name']} - {partner['partner_company']} ({partner['commission_rate']}% commission)")
    
    # ============================================================
    # STEP 3: Create Customer Accounts (DC & Marvel)
    # ============================================================
    print("\n🏢 STEP 3: Creating Customer Accounts (DC & Marvel)")
    print("-" * 60)
    
    accounts = [
        {
            "account_id": gen_id("acc_"),
            "account_name": "Wayne Enterprises",  # DC Universe
            "industry": "Security & Defense",
            "source": "partner_referral",
            "status": "active",
            "annual_revenue": 50000000,
            "employee_count": 500,
            "billing_address": "1007 Mountain Drive, Gotham, NJ",
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "account_id": gen_id("acc_"),
            "account_name": "Stark Industries",  # Marvel Universe
            "industry": "Technology & Defense",
            "source": "direct",
            "status": "active",
            "annual_revenue": 75000000,
            "employee_count": 800,
            "billing_address": "200 Park Avenue, New York, NY",
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "account_id": gen_id("acc_"),
            "account_name": "LexCorp",  # DC Universe
            "industry": "Research & Development",
            "source": "partner_referral",
            "status": "active",
            "annual_revenue": 30000000,
            "employee_count": 300,
            "billing_address": "LexCorp Tower, Metropolis, DE",
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "account_id": gen_id("acc_"),
            "account_name": "Oscorp Industries",  # Marvel Universe
            "industry": "Biotechnology",
            "source": "direct",
            "status": "active",
            "annual_revenue": 45000000,
            "employee_count": 600,
            "billing_address": "Oscorp Tower, New York, NY",
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "account_id": gen_id("acc_"),
            "account_name": "Queen Consolidated",  # DC Universe
            "industry": "Manufacturing",
            "source": "partner_referral",
            "status": "active",
            "annual_revenue": 35000000,
            "employee_count": 450,
            "billing_address": "Queen Tower, Star City, CA",
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        }
    ]
    
    for account in accounts:
        await db.accounts.insert_one(account)
        print(f"  ✓ {account['account_name']} - {account['industry']}")
    
    # ============================================================
    # STEP 4: Create Deals with Owners & Agents of Record
    # ============================================================
    print("\n💼 STEP 4: Creating Deals (with owners & partners)")
    print("-" * 60)
    
    deals = [
        {
            "deal_id": gen_id("deal_"),
            "name": "Watchtower Security System",
            "account_id": accounts[0]["account_id"],
            "account_name": accounts[0]["account_name"],
            "owner_id": sales_reps[0]["user_id"],
            "owner_name": sales_reps[0]["name"],
            "agent_of_record_id": partners[0]["user_id"],
            "agent_of_record_name": partners[0]["name"],
            "amount": 450000,
            "stage": "Closed Won",
            "close_date": "2026-01-15",
            "deal_type": "Professional Services",
            "description": "Enterprise security system implementation",
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "deal_id": gen_id("deal_"),
            "name": "Arc Reactor Maintenance Program",
            "account_id": accounts[1]["account_id"],
            "account_name": accounts[1]["account_name"],
            "owner_id": sales_reps[1]["user_id"],
            "owner_name": sales_reps[1]["name"],
            "agent_of_record_id": None,  # Direct sale
            "agent_of_record_name": None,
            "amount": 280000,
            "stage": "Closed Won",
            "close_date": "2026-01-20",
            "deal_type": "Field Services",
            "description": "Quarterly maintenance and inspection services",
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "deal_id": gen_id("deal_"),
            "name": "LexCorp Campus Security",
            "account_id": accounts[2]["account_id"],
            "account_name": accounts[2]["account_name"],
            "owner_id": sales_reps[2]["user_id"],
            "owner_name": sales_reps[2]["name"],
            "agent_of_record_id": partners[1]["user_id"],
            "agent_of_record_name": partners[1]["name"],
            "amount": 320000,
            "stage": "Closed Won",
            "close_date": "2026-01-25",
            "deal_type": "Field Services",
            "description": "24/7 security patrol and monitoring",
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "deal_id": gen_id("deal_"),
            "name": "Oscorp IT Infrastructure Upgrade",
            "account_id": accounts[3]["account_id"],
            "account_name": accounts[3]["account_name"],
            "owner_id": sales_reps[0]["user_id"],
            "owner_name": sales_reps[0]["name"],
            "agent_of_record_id": partners[2]["user_id"],
            "agent_of_record_name": partners[2]["name"],
            "amount": 550000,
            "stage": "Closed Won",
            "close_date": "2026-02-01",
            "deal_type": "Professional Services",
            "description": "Complete IT infrastructure modernization",
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "deal_id": gen_id("deal_"),
            "name": "Queen Consolidated Manufacturing Support",
            "account_id": accounts[4]["account_id"],
            "account_name": accounts[4]["account_name"],
            "owner_id": sales_reps[1]["user_id"],
            "owner_name": sales_reps[1]["name"],
            "agent_of_record_id": None,
            "agent_of_record_name": None,
            "amount": 180000,
            "stage": "Closed Won",
            "close_date": "2026-02-05",
            "deal_type": "Field Services",
            "description": "On-site technical support for manufacturing floor",
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        }
    ]
    
    for deal in deals:
        await db.deals.insert_one(deal)
        partner_info = f" (Partner: {deal['agent_of_record_name']})" if deal['agent_of_record_name'] else " (Direct)"
        print(f"  ✓ {deal['name']}")
        print(f"    ${deal['amount']:,} - {deal['deal_type']} - Owner: {deal['owner_name']}{partner_info}")
    
    # ============================================================
    # STEP 5: Create Contractor Businesses
    # ============================================================
    print("\n🏢 STEP 5: Creating Contractor Businesses (Marvel)")
    print("-" * 60)
    
    contractor_businesses = [
        {
            "business_id": gen_id("cb_"),
            "business_name": "SHIELD Contractors LLC",
            "contact_name": "Nick Fury",
            "email": "billing@shield.test",
            "phone": "(555) 100-0001",
            "billing_frequency": "weekly",
            "payment_terms": "net_30",
            "payment_method": "ACH",
            "w9_on_file": True,
            "is_1099_eligible": True,
            "status": "active",
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "business_id": gen_id("cb_"),
            "business_name": "X-Mansion Tech Services",
            "contact_name": "Charles Xavier",
            "email": "billing@xmansion.test",
            "phone": "(555) 200-0002",
            "billing_frequency": "biweekly",
            "payment_terms": "net_45",
            "payment_method": "Wire",
            "w9_on_file": True,
            "is_1099_eligible": True,
            "status": "active",
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "business_id": gen_id("cb_"),
            "business_name": "Avengers Technical Solutions",
            "contact_name": "Tony Stark",
            "email": "billing@avengers.test",
            "phone": "(555) 300-0003",
            "billing_frequency": "weekly",
            "payment_terms": "net_30",
            "payment_method": "ACH",
            "w9_on_file": True,
            "is_1099_eligible": True,
            "status": "active",
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        }
    ]
    
    for business in contractor_businesses:
        await db.contractor_businesses.insert_one(business)
        print(f"  ✓ {business['business_name']} ({business['billing_frequency']})")
    
    # ============================================================
    # STEP 6: Create Contractors (Marvel)
    # ============================================================
    print("\n👷 STEP 6: Creating Contractors (Marvel Universe)")
    print("-" * 60)
    
    contractors = [
        # SHIELD Contractors
        {
            "user_id": gen_id("user_"),
            "email": "peter.parker@dailybugle.test",
            "name": "Peter Parker",
            "role": "contractor",
            "contractor_business_id": contractor_businesses[0]["business_id"],
            "cost_rate": 125,
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "user_id": gen_id("user_"),
            "email": "natasha.romanoff@shield.test",
            "name": "Natasha Romanoff",
            "role": "contractor",
            "contractor_business_id": contractor_businesses[0]["business_id"],
            "cost_rate": 135,
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "user_id": gen_id("user_"),
            "email": "clint.barton@shield.test",
            "name": "Clint Barton",
            "role": "contractor",
            "contractor_business_id": contractor_businesses[0]["business_id"],
            "cost_rate": 115,
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        # X-Mansion Tech Services
        {
            "user_id": gen_id("user_"),
            "email": "wade.wilson@mercenary.test",
            "name": "Wade Wilson",
            "role": "contractor",
            "contractor_business_id": contractor_businesses[1]["business_id"],
            "cost_rate": 140,
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "user_id": gen_id("user_"),
            "email": "carol.danvers@starforce.test",
            "name": "Carol Danvers",
            "role": "contractor",
            "contractor_business_id": contractor_businesses[1]["business_id"],
            "cost_rate": 145,
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "user_id": gen_id("user_"),
            "email": "scott.lang@antman.test",
            "name": "Scott Lang",
            "role": "contractor",
            "contractor_business_id": contractor_businesses[1]["business_id"],
            "cost_rate": 105,
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        # Avengers Technical Solutions
        {
            "user_id": gen_id("user_"),
            "email": "bruce.banner@avengers.test",
            "name": "Bruce Banner",
            "role": "contractor",
            "contractor_business_id": contractor_businesses[2]["business_id"],
            "cost_rate": 150,
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "user_id": gen_id("user_"),
            "email": "wanda.maximoff@avengers.test",
            "name": "Wanda Maximoff",
            "role": "contractor",
            "contractor_business_id": contractor_businesses[2]["business_id"],
            "cost_rate": 130,
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        # Solo contractors
        {
            "user_id": gen_id("user_"),
            "email": "stephen.strange@sanctum.test",
            "name": "Stephen Strange",
            "role": "contractor",
            "contractor_business_id": None,
            "cost_rate": 160,
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "user_id": gen_id("user_"),
            "email": "tchalla@wakanda.test",
            "name": "T'Challa",
            "role": "contractor",
            "contractor_business_id": None,
            "cost_rate": 155,
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        }
    ]
    
    for contractor in contractors:
        await db.users.insert_one(contractor)
        business_name = "Solo" if not contractor.get('contractor_business_id') else \
                        next(b['business_name'] for b in contractor_businesses if b['business_id'] == contractor['contractor_business_id'])
        print(f"  ✓ {contractor['name']} - ${contractor['cost_rate']}/hr ({business_name})")
    
    # ============================================================
    # STEP 7: Create Contracts with Mixed Categories
    # ============================================================
    print("\n📝 STEP 7: Creating Contracts (PS & FS mix)")
    print("-" * 60)
    
    contracts = [
        # Professional Services - BOH
        {
            "contract_id": gen_id("cont_"),
            "name": "Watchtower Security - Professional Services (BOH)",
            "account_id": accounts[0]["account_id"],
            "account_name": accounts[0]["account_name"],
            "deal_ids": [deals[0]["deal_id"]],
            "contract_type": "boh",
            "service_category": "professional_services",
            "billing_frequency": "monthly",  # Billing frequency
            "status": "active",
            "start_date": "2026-02-01",
            "end_date": "2026-12-31",
            "resource_types": [
                {
                    "resource_type_id": gen_id("rt_"),
                    "name": "Senior Security Architect",
                    "hours": 400,
                    "used_hours": 0,
                    "billable_rate": 250,
                    "cost_rate": 135,
                    "assigned_users": []
                },
                {
                    "resource_type_id": gen_id("rt_"),
                    "name": "Security Engineer",
                    "hours": 800,
                    "used_hours": 0,
                    "billable_rate": 200,
                    "cost_rate": 125,
                    "assigned_users": []
                },
                {
                    "resource_type_id": gen_id("rt_"),
                    "name": "Junior Security Analyst",
                    "hours": 600,
                    "used_hours": 0,
                    "billable_rate": 150,
                    "cost_rate": 105,
                    "assigned_users": []
                }
            ],
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        # Field Services - BOA
        {
            "contract_id": gen_id("cont_"),
            "name": "Arc Reactor Maintenance - Field Services (BOA)",
            "account_id": accounts[1]["account_id"],
            "account_name": accounts[1]["account_name"],
            "deal_ids": [deals[1]["deal_id"]],
            "contract_type": "boa",
            "service_category": "field_services",
            "status": "active",
            "contract_value": 280000,
            "used_amount": 0,
            "start_date": "2026-02-01",
            "end_date": "2026-08-31",
            "resource_types": [
                {
                    "resource_type_id": gen_id("rt_"),
                    "name": "Senior Field Technician",
                    "billable_rate": 180,
                    "cost_rate": 140,
                    "assigned_users": []
                },
                {
                    "resource_type_id": gen_id("rt_"),
                    "name": "Field Technician",
                    "billable_rate": 150,
                    "cost_rate": 115,
                    "assigned_users": []
                }
            ],
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        # Field Services - BOH
        {
            "contract_id": gen_id("cont_"),
            "name": "LexCorp Security - Field Services (BOH)",
            "account_id": accounts[2]["account_id"],
            "account_name": accounts[2]["account_name"],
            "deal_ids": [deals[2]["deal_id"]],
            "contract_type": "boh",
            "service_category": "field_services",
            "status": "active",
            "start_date": "2026-02-01",
            "end_date": "2026-12-31",
            "resource_types": [
                {
                    "resource_type_id": gen_id("rt_"),
                    "name": "Security Guard - Day Shift",
                    "hours": 2080,
                    "used_hours": 0,
                    "billable_rate": 125,
                    "cost_rate": 105,
                    "assigned_users": []
                },
                {
                    "resource_type_id": gen_id("rt_"),
                    "name": "Security Guard - Night Shift",
                    "hours": 2080,
                    "used_hours": 0,
                    "billable_rate": 135,
                    "cost_rate": 115,
                    "assigned_users": []
                }
            ],
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        # Professional Services - BOH
        {
            "contract_id": gen_id("cont_"),
            "name": "Oscorp IT Infrastructure - Professional Services (BOH)",
            "account_id": accounts[3]["account_id"],
            "account_name": accounts[3]["account_name"],
            "deal_ids": [deals[3]["deal_id"]],
            "contract_type": "boh",
            "service_category": "professional_services",
            "status": "active",
            "start_date": "2026-02-10",
            "end_date": "2026-12-31",
            "resource_types": [
                {
                    "resource_type_id": gen_id("rt_"),
                    "name": "Solutions Architect",
                    "hours": 500,
                    "used_hours": 0,
                    "billable_rate": 280,
                    "cost_rate": 150,
                    "assigned_users": []
                },
                {
                    "resource_type_id": gen_id("rt_"),
                    "name": "DevOps Engineer",
                    "hours": 700,
                    "used_hours": 0,
                    "billable_rate": 220,
                    "cost_rate": 130,
                    "assigned_users": []
                }
            ],
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        # Field Services - BOA
        {
            "contract_id": gen_id("cont_"),
            "name": "Queen Manufacturing Support - Field Services (BOA)",
            "account_id": accounts[4]["account_id"],
            "account_name": accounts[4]["account_name"],
            "deal_ids": [deals[4]["deal_id"]],
            "contract_type": "boa",
            "service_category": "field_services",
            "status": "active",
            "contract_value": 180000,
            "used_amount": 0,
            "start_date": "2026-02-10",
            "end_date": "2026-08-31",
            "resource_types": [
                {
                    "resource_type_id": gen_id("rt_"),
                    "name": "Manufacturing Support Specialist",
                    "billable_rate": 160,
                    "cost_rate": 110,
                    "assigned_users": []
                }
            ],
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        }
    ]
    
    for contract in contracts:
        await db.contracts.insert_one(contract)
        print(f"  ✓ {contract['name']}")
        print(f"    {contract['contract_type'].upper()} | {contract['service_category'].replace('_', ' ').title()}")
    
    # Assign contractors to resource types
    print("\n👤 Assigning contractors to resource types...")
    
    # Contract 0 (Watchtower PS)
    await db.contracts.update_one(
        {"contract_id": contracts[0]["contract_id"]},
        {"$set": {
            "resource_types.0.assigned_users": [
                {"user_id": contractors[1]["user_id"], "user_name": contractors[1]["name"], "cost_rate": contractors[1]["cost_rate"]}
            ],
            "resource_types.1.assigned_users": [
                {"user_id": contractors[0]["user_id"], "user_name": contractors[0]["name"], "cost_rate": contractors[0]["cost_rate"]},
                {"user_id": contractors[3]["user_id"], "user_name": contractors[3]["name"], "cost_rate": contractors[3]["cost_rate"]}
            ],
            "resource_types.2.assigned_users": [
                {"user_id": contractors[5]["user_id"], "user_name": contractors[5]["name"], "cost_rate": contractors[5]["cost_rate"]}
            ]
        }}
    )
    
    # Contract 1 (Arc Reactor FS)
    await db.contracts.update_one(
        {"contract_id": contracts[1]["contract_id"]},
        {"$set": {
            "resource_types.0.assigned_users": [
                {"user_id": contractors[3]["user_id"], "user_name": contractors[3]["name"], "cost_rate": contractors[3]["cost_rate"]}
            ],
            "resource_types.1.assigned_users": [
                {"user_id": contractors[2]["user_id"], "user_name": contractors[2]["name"], "cost_rate": contractors[2]["cost_rate"]}
            ]
        }}
    )
    
    # Contract 2 (LexCorp FS)
    await db.contracts.update_one(
        {"contract_id": contracts[2]["contract_id"]},
        {"$set": {
            "resource_types.0.assigned_users": [
                {"user_id": contractors[5]["user_id"], "user_name": contractors[5]["name"], "cost_rate": contractors[5]["cost_rate"]},
                {"user_id": contractors[8]["user_id"], "user_name": contractors[8]["name"], "cost_rate": contractors[8]["cost_rate"]}
            ],
            "resource_types.1.assigned_users": [
                {"user_id": contractors[9]["user_id"], "user_name": contractors[9]["name"], "cost_rate": contractors[9]["cost_rate"]}
            ]
        }}
    )
    
    # Contract 3 (Oscorp PS)
    await db.contracts.update_one(
        {"contract_id": contracts[3]["contract_id"]},
        {"$set": {
            "resource_types.0.assigned_users": [
                {"user_id": contractors[6]["user_id"], "user_name": contractors[6]["name"], "cost_rate": contractors[6]["cost_rate"]}
            ],
            "resource_types.1.assigned_users": [
                {"user_id": contractors[7]["user_id"], "user_name": contractors[7]["name"], "cost_rate": contractors[7]["cost_rate"]}
            ]
        }}
    )
    
    # Contract 4 (Queen FS)
    await db.contracts.update_one(
        {"contract_id": contracts[4]["contract_id"]},
        {"$set": {
            "resource_types.0.assigned_users": [
                {"user_id": contractors[2]["user_id"], "user_name": contractors[2]["name"], "cost_rate": contractors[2]["cost_rate"]},
                {"user_id": contractors[4]["user_id"], "user_name": contractors[4]["name"], "cost_rate": contractors[4]["cost_rate"]}
            ]
        }}
    )
    
    print("  ✓ All contractors assigned to resource types")
    
    # ============================================================
    # STEP 8: Create Projects (for Professional Services)
    # ============================================================
    print("\n🗂️ STEP 8: Creating Projects (Professional Services)")
    print("-" * 60)
    
    projects = [
        {
            "project_id": gen_id("proj_"),
            "name": "Watchtower Phase 1 - Security Architecture",
            "contract_id": contracts[0]["contract_id"],
            "account_id": accounts[0]["account_id"],
            "account_name": accounts[0]["account_name"],
            "status": "active",
            "start_date": "2026-02-01",
            "end_date": "2026-06-30",
            "project_manager": sales_reps[0]["name"],
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "project_id": gen_id("proj_"),
            "name": "Watchtower Phase 2 - Implementation",
            "contract_id": contracts[0]["contract_id"],
            "account_id": accounts[0]["account_id"],
            "account_name": accounts[0]["account_name"],
            "status": "planning",
            "start_date": "2026-07-01",
            "end_date": "2026-12-31",
            "project_manager": sales_reps[0]["name"],
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "project_id": gen_id("proj_"),
            "name": "Oscorp Infrastructure Discovery",
            "contract_id": contracts[3]["contract_id"],
            "account_id": accounts[3]["account_id"],
            "account_name": accounts[3]["account_name"],
            "status": "active",
            "start_date": "2026-02-10",
            "end_date": "2026-04-30",
            "project_manager": sales_reps[0]["name"],
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        }
    ]
    
    for project in projects:
        await db.projects.insert_one(project)
        print(f"  ✓ {project['name']} ({project['status']})")
    
    # ============================================================
    # STEP 9: Create Jobs (for Field Services)
    # ============================================================
    print("\n🔧 STEP 9: Creating Jobs (Field Services)")
    print("-" * 60)
    
    jobs = [
        {
            "job_id": gen_id("job_"),
            "name": "Arc Reactor - Q1 2026 Maintenance",
            "contract_id": contracts[1]["contract_id"],
            "account_id": accounts[1]["account_id"],
            "account_name": accounts[1]["account_name"],
            "status": "active",
            "job_type": "maintenance",
            "scheduled_date": "2026-02-15",
            "estimated_hours": 40,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "job_id": gen_id("job_"),
            "name": "Arc Reactor - Q2 2026 Inspection",
            "contract_id": contracts[1]["contract_id"],
            "account_id": accounts[1]["account_id"],
            "account_name": accounts[1]["account_name"],
            "status": "scheduled",
            "job_type": "inspection",
            "scheduled_date": "2026-05-15",
            "estimated_hours": 32,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "job_id": gen_id("job_"),
            "name": "LexCorp Campus - February Security Patrol",
            "contract_id": contracts[2]["contract_id"],
            "account_id": accounts[2]["account_id"],
            "account_name": accounts[2]["account_name"],
            "status": "active",
            "job_type": "ongoing",
            "scheduled_date": "2026-02-01",
            "estimated_hours": 352,  # 22 business days * 16 hours
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "job_id": gen_id("job_"),
            "name": "Queen Manufacturing - Week 1 Support",
            "contract_id": contracts[4]["contract_id"],
            "account_id": accounts[4]["account_id"],
            "account_name": accounts[4]["account_name"],
            "status": "completed",
            "job_type": "support",
            "scheduled_date": "2026-02-10",
            "estimated_hours": 40,
            "completed_date": "2026-02-14",
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        }
    ]
    
    for job in jobs:
        await db.jobs.insert_one(job)
        print(f"  ✓ {job['name']} ({job['status']})")
    
    # ============================================================
    # STEP 10: Create Tasks (for Projects and Jobs)
    # ============================================================
    print("\n📋 STEP 10: Creating Tasks (Projects & Jobs)")
    print("-" * 60)
    
    tasks = [
        # Tasks for Project 0 (Watchtower Phase 1)
        {
            "task_id": gen_id("task_"),
            "name": "Security Requirements Gathering",
            "project_id": projects[0]["project_id"],
            "job_id": None,
            "assigned_to": contractors[1]["user_id"],
            "assigned_to_name": contractors[1]["name"],
            "status": "completed",
            "estimated_hours": 40,
            "actual_hours": 42,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "task_id": gen_id("task_"),
            "name": "Architecture Design",
            "project_id": projects[0]["project_id"],
            "job_id": None,
            "assigned_to": contractors[1]["user_id"],
            "assigned_to_name": contractors[1]["name"],
            "status": "in_progress",
            "estimated_hours": 60,
            "actual_hours": 32,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "task_id": gen_id("task_"),
            "name": "Network Security Configuration",
            "project_id": projects[0]["project_id"],
            "job_id": None,
            "assigned_to": contractors[0]["user_id"],
            "assigned_to_name": contractors[0]["name"],
            "status": "in_progress",
            "estimated_hours": 80,
            "actual_hours": 48,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "task_id": gen_id("task_"),
            "name": "Security Testing",
            "project_id": projects[0]["project_id"],
            "job_id": None,
            "assigned_to": contractors[5]["user_id"],
            "assigned_to_name": contractors[5]["name"],
            "status": "not_started",
            "estimated_hours": 40,
            "actual_hours": 0,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        # Tasks for Job 0 (Arc Reactor Q1 Maintenance)
        {
            "task_id": gen_id("task_"),
            "name": "Pre-maintenance Inspection",
            "project_id": None,
            "job_id": jobs[0]["job_id"],
            "assigned_to": contractors[3]["user_id"],
            "assigned_to_name": contractors[3]["name"],
            "status": "completed",
            "estimated_hours": 8,
            "actual_hours": 8,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "task_id": gen_id("task_"),
            "name": "Core Maintenance Work",
            "project_id": None,
            "job_id": jobs[0]["job_id"],
            "assigned_to": contractors[3]["user_id"],
            "assigned_to_name": contractors[3]["name"],
            "status": "in_progress",
            "estimated_hours": 24,
            "actual_hours": 16,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "task_id": gen_id("task_"),
            "name": "Documentation & Reporting",
            "project_id": None,
            "job_id": jobs[0]["job_id"],
            "assigned_to": contractors[2]["user_id"],
            "assigned_to_name": contractors[2]["name"],
            "status": "not_started",
            "estimated_hours": 8,
            "actual_hours": 0,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        # Tasks for Job 2 (LexCorp Security Patrol)
        {
            "task_id": gen_id("task_"),
            "name": "Day Shift Security - Week 1",
            "project_id": None,
            "job_id": jobs[2]["job_id"],
            "assigned_to": contractors[5]["user_id"],
            "assigned_to_name": contractors[5]["name"],
            "status": "completed",
            "estimated_hours": 40,
            "actual_hours": 40,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        {
            "task_id": gen_id("task_"),
            "name": "Night Shift Security - Week 1",
            "project_id": None,
            "job_id": jobs[2]["job_id"],
            "assigned_to": contractors[9]["user_id"],
            "assigned_to_name": contractors[9]["name"],
            "status": "completed",
            "estimated_hours": 40,
            "actual_hours": 40,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        },
        # Tasks for Project 2 (Oscorp)
        {
            "task_id": gen_id("task_"),
            "name": "Infrastructure Assessment",
            "project_id": projects[2]["project_id"],
            "job_id": None,
            "assigned_to": contractors[6]["user_id"],
            "assigned_to_name": contractors[6]["name"],
            "status": "in_progress",
            "estimated_hours": 60,
            "actual_hours": 24,
            "created_at": datetime.now(timezone.utc),
            "is_test_data": True
        }
    ]
    
    for task in tasks:
        await db.tasks.insert_one(task)
        type_label = "Project" if task['project_id'] else "Job"
        print(f"  ✓ {task['name']} ({type_label}, {task['status']})")
    
    # ============================================================
    # STEP 11: Create Time Entries (Various Approval Statuses)
    # ============================================================
    print("\n⏱️ STEP 11: Creating Time Entries (Mixed approval statuses)")
    print("-" * 60)
    
    time_entries = []
    
    # Helper function
    def create_entry(contractor, project, contract, task, date, hours, status, notes, pm_approved_by=None, admin_approved_by=None):
        """
        Create a time entry following validation rules:
        - Max 4 hours per entry
        - 15-minute increments (0.25)
        """
        # Validate hours
        if hours > 4.0:
            raise ValueError(f"Max 4 hours per entry. Got {hours}")
        if round(hours * 4) != hours * 4:
            raise ValueError(f"Hours must be in 0.25 increments. Got {hours}")
        
        entry = {
            "entry_id": gen_id("te_"),
            "user_id": contractor["user_id"],
            "user_name": contractor["name"],
            "project_id": project["project_id"] if project else None,
            "project_name": project["name"] if project else None,
            "contract_id": contract["contract_id"],
            "account_id": contract["account_id"],
            "task_id": task["task_id"] if task else None,
            "date": date,
            "hours": hours,
            "notes": notes,
            "cost_rate": contractor["cost_rate"],
            "status": status,
            "is_billable": True,
            "is_test_data": True,
            "created_at": datetime.now(timezone.utc),
            "submitted_at": datetime.now(timezone.utc) if status != "draft" else None,
        }
        
        if status in ["pm_approved", "admin_approved"]:
            entry["pm_approved_by"] = pm_approved_by or admin_id
            entry["pm_approved_at"] = datetime.now(timezone.utc) - timedelta(days=5)
        
        if status == "admin_approved":
            entry["admin_approved_by"] = admin_approved_by or admin_id
            entry["admin_approved_at"] = datetime.now(timezone.utc) - timedelta(days=2)
        
        return entry
    
    # Week 1: Jan 27-31 (ADMIN APPROVED - Historical for AP)
    week1_dates = ["2026-01-27", "2026-01-28", "2026-01-29", "2026-01-30", "2026-01-31"]
    for date in week1_dates:
        # Each contractor works 8 hours = 2 x 4-hour entries per day
        # Morning entry
        time_entries.append(create_entry(
            contractors[0], projects[0], contracts[0], tasks[2],
            date, 4, "admin_approved", "Network security configuration - morning session"
        ))
        # Afternoon entry
        time_entries.append(create_entry(
            contractors[0], projects[0], contracts[0], tasks[2],
            date, 4, "admin_approved", "Network security configuration - afternoon session"
        ))
        
        # Morning entry
        time_entries.append(create_entry(
            contractors[1], projects[0], contracts[0], tasks[0],
            date, 4, "admin_approved", "Requirements gathering sessions - stakeholder meetings"
        ))
        # Afternoon entry
        time_entries.append(create_entry(
            contractors[1], projects[0], contracts[0], tasks[0],
            date, 4, "admin_approved", "Requirements documentation and analysis"
        ))
    
    # Week 2: Feb 3-7 (PM APPROVED - Ready for Finance)
    week2_dates = ["2026-02-03", "2026-02-04", "2026-02-05", "2026-02-06", "2026-02-07"]
    for date in week2_dates:
        # 2 x 4-hour entries per contractor per day
        time_entries.append(create_entry(
            contractors[0], projects[0], contracts[0], tasks[2],
            date, 4, "pm_approved", "Network security implementation - morning"
        ))
        time_entries.append(create_entry(
            contractors[0], projects[0], contracts[0], tasks[2],
            date, 4, "pm_approved", "Network security implementation - afternoon"
        ))
        
        time_entries.append(create_entry(
            contractors[3], None, contracts[1], tasks[5],
            date, 4, "pm_approved", "Arc Reactor maintenance - diagnostics"
        ))
        time_entries.append(create_entry(
            contractors[3], None, contracts[1], tasks[5],
            date, 4, "pm_approved", "Arc Reactor maintenance - repairs"
        ))
        
        time_entries.append(create_entry(
            contractors[5], None, contracts[2], tasks[7],
            date, 4, "pm_approved", "LexCorp day shift security patrol - perimeter"
        ))
        time_entries.append(create_entry(
            contractors[5], None, contracts[2], tasks[7],
            date, 4, "pm_approved", "LexCorp day shift security patrol - building"
        ))
    
    # Week 3: Feb 10-14 (SUBMITTED - Waiting for PM)
    week3_dates = ["2026-02-10", "2026-02-11", "2026-02-12", "2026-02-13", "2026-02-14"]
    for date in week3_dates:
        # Mix of full days (8h = 2x4h) and partial days
        time_entries.append(create_entry(
            contractors[1], projects[0], contracts[0], tasks[1],
            date, 4, "submitted", "Architecture design - system diagrams"
        ))
        time_entries.append(create_entry(
            contractors[1], projects[0], contracts[0], tasks[1],
            date, 4, "submitted", "Architecture design - documentation"
        ))
        
        time_entries.append(create_entry(
            contractors[9], None, contracts[2], tasks[8],
            date, 4, "submitted", "LexCorp night shift security - rounds"
        ))
        time_entries.append(create_entry(
            contractors[9], None, contracts[2], tasks[8],
            date, 4, "submitted", "LexCorp night shift security - monitoring"
        ))
        
        # Partial day - 6 hours (split as 4h + 2h)
        time_entries.append(create_entry(
            contractors[6], projects[2], contracts[3], tasks[9],
            date, 4, "submitted", "Oscorp infrastructure assessment - discovery"
        ))
        time_entries.append(create_entry(
            contractors[6], projects[2], contracts[3], tasks[9],
            date, 2, "submitted", "Oscorp infrastructure assessment - documentation"
        ))
    
    # Week 4: Feb 17-21 (DRAFT - Not yet submitted)
    week4_dates = ["2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-21"]
    for date in week4_dates:
        # Various hour combinations following 0.25 increments and max 4h
        time_entries.append(create_entry(
            contractors[7], projects[2], contracts[3], None,
            date, 4, "draft", "DevOps infrastructure work - configuration"
        ))
        time_entries.append(create_entry(
            contractors[7], projects[2], contracts[3], None,
            date, 3.5, "draft", "DevOps infrastructure work - testing"
        ))
        
        time_entries.append(create_entry(
            contractors[2], None, contracts[1], tasks[6],
            date, 2, "draft", "Maintenance documentation"
        ))
        time_entries.append(create_entry(
            contractors[2], None, contracts[1], tasks[6],
            date, 2, "draft", "Maintenance reporting"
        ))
    
    # Insert all time entries
    await db.time_entries.insert_many(time_entries)
    
    status_counts = {
        "admin_approved": len([e for e in time_entries if e["status"] == "admin_approved"]),
        "pm_approved": len([e for e in time_entries if e["status"] == "pm_approved"]),
        "submitted": len([e for e in time_entries if e["status"] == "submitted"]),
        "draft": len([e for e in time_entries if e["status"] == "draft"])
    }
    
    print(f"  ✓ Created {len(time_entries)} time entries:")
    print(f"    - Admin Approved (Historical): {status_counts['admin_approved']}")
    print(f"    - PM Approved (Ready for Finance): {status_counts['pm_approved']}")
    print(f"    - Submitted (Waiting for PM): {status_counts['submitted']}")
    print(f"    - Draft (Not submitted): {status_counts['draft']}")
    
    # ============================================================
    # STEP 12: Generate AP Bills from Approved Time
    # ============================================================
    print("\n💰 STEP 12: Generating AP Bills (for QBO batches)")
    print("-" * 60)
    
    # Get admin approved entries (historical)
    admin_approved = [e for e in time_entries if e["status"] == "admin_approved"]
    
    # Group by contractor business + week
    def get_week_key(date_str):
        # Simple week grouping by date range
        if date_str <= "2026-02-02":
            return ("2026-01-27", "2026-02-02")
        elif date_str <= "2026-02-09":
            return ("2026-02-03", "2026-02-09")
        else:
            return ("2026-02-10", "2026-02-16")
    
    business_groups = {}
    for entry in admin_approved:
        contractor = next(c for c in contractors if c["user_id"] == entry["user_id"])
        business_id = contractor.get("contractor_business_id") or contractor["user_id"]
        week_key = get_week_key(entry["date"])
        group_key = (business_id, week_key)
        
        if group_key not in business_groups:
            business_groups[group_key] = []
        business_groups[group_key].append(entry)
    
    bill_number = 1
    for (business_id, week_key), entries in business_groups.items():
        # Get business or contractor info
        if business_id.startswith("cb_"):
            business = next(b for b in contractor_businesses if b["business_id"] == business_id)
            contractor_name = business["business_name"]
            business_name = business["business_name"]
            payment_terms = business["payment_terms"]
            payment_method = business["payment_method"]
        else:
            contractor = next(c for c in contractors if c["user_id"] == business_id)
            contractor_name = contractor["name"]
            business_name = None
            payment_terms = "net_30"
            payment_method = "ACH"
        
        # Create line items
        line_items = []
        time_entry_ids = []
        
        for entry in entries:
            line_items.append({
                "contractor_name": entry["user_name"],
                "project_name": entry.get("project_name", "Field Services"),
                "hours": entry["hours"],
                "rate": entry["cost_rate"],
                "amount": entry["hours"] * entry["cost_rate"]
            })
            time_entry_ids.append(entry["entry_id"])
        
        total = sum(item["amount"] for item in line_items)
        
        bill = {
            "bill_id": gen_id("bill_"),
            "bill_number": f"AP-2026-{bill_number:03d}",
            "contractor_business_id": business_id if business_id.startswith("cb_") else None,
            "contractor_business_name": business_name,
            "contractor_user_id": None if business_id.startswith("cb_") else business_id,
            "contractor_name": contractor_name,
            "period_start": week_key[0],
            "period_end": week_key[1],
            "line_items": line_items,
            "time_entry_ids": time_entry_ids,
            "subtotal": total,
            "total": total,
            "status": "paid",  # Historical bills are paid
            "payment_terms": payment_terms,
            "payment_method": payment_method,
            "notes": "Auto-generated from admin approved time entries",
            "created_at": datetime.now(timezone.utc) - timedelta(days=10),
            "created_by": "system",
            "approved_by": admin_id,
            "approved_at": datetime.now(timezone.utc) - timedelta(days=7),
            "paid_by": admin_id,
            "paid_at": datetime.now(timezone.utc) - timedelta(days=3),
            "payment_reference": f"CHK-{bill_number:05d}",
            "is_test_data": True
        }
        
        await db.ap_bills.insert_one(bill)
        print(f"  ✓ {bill['bill_number']} - {contractor_name}: ${total:,.2f} (PAID)")
        bill_number += 1
    
    # Generate bills for PM approved entries (pending finance review)
    pm_approved = [e for e in time_entries if e["status"] == "pm_approved"]
    
    business_groups_pending = {}
    for entry in pm_approved:
        contractor = next(c for c in contractors if c["user_id"] == entry["user_id"])
        business_id = contractor.get("contractor_business_id") or contractor["user_id"]
        week_key = get_week_key(entry["date"])
        group_key = (business_id, week_key)
        
        if group_key not in business_groups_pending:
            business_groups_pending[group_key] = []
        business_groups_pending[group_key].append(entry)
    
    for (business_id, week_key), entries in business_groups_pending.items():
        # Get business or contractor info
        if business_id.startswith("cb_"):
            business = next(b for b in contractor_businesses if b["business_id"] == business_id)
            contractor_name = business["business_name"]
            business_name = business["business_name"]
            payment_terms = business["payment_terms"]
            payment_method = business["payment_method"]
        else:
            contractor = next(c for c in contractors if c["user_id"] == business_id)
            contractor_name = contractor["name"]
            business_name = None
            payment_terms = "net_30"
            payment_method = "ACH"
        
        # Create line items
        line_items = []
        time_entry_ids = []
        
        for entry in entries:
            line_items.append({
                "contractor_name": entry["user_name"],
                "project_name": entry.get("project_name", "Field Services"),
                "hours": entry["hours"],
                "rate": entry["cost_rate"],
                "amount": entry["hours"] * entry["cost_rate"]
            })
            time_entry_ids.append(entry["entry_id"])
        
        total = sum(item["amount"] for item in line_items)
        
        # Calculate due date
        due_date = datetime.now(timezone.utc) + timedelta(days=30)
        
        bill = {
            "bill_id": gen_id("bill_"),
            "bill_number": f"AP-2026-{bill_number:03d}",
            "contractor_business_id": business_id if business_id.startswith("cb_") else None,
            "contractor_business_name": business_name,
            "contractor_user_id": None if business_id.startswith("cb_") else business_id,
            "contractor_name": contractor_name,
            "period_start": week_key[0],
            "period_end": week_key[1],
            "line_items": line_items,
            "time_entry_ids": time_entry_ids,
            "subtotal": total,
            "total": total,
            "status": "pending_finance_review",
            "due_date": due_date.strftime("%Y-%m-%d"),
            "payment_terms": payment_terms,
            "payment_method": payment_method,
            "notes": "Auto-generated from PM approved time entries - Ready for Finance approval",
            "created_at": datetime.now(timezone.utc),
            "created_by": "system",
            "is_test_data": True
        }
        
        await db.ap_bills.insert_one(bill)
        print(f"  ✓ {bill['bill_number']} - {contractor_name}: ${total:,.2f} (PENDING FINANCE REVIEW)")
        bill_number += 1
    
    # ============================================================
    # FINAL SUMMARY
    # ============================================================
    print("\n" + "=" * 60)
    print("✅ COMPREHENSIVE TEST DATA GENERATION COMPLETE!")
    print("=" * 60)
    
    print(f"\n📊 DATABASE: {DB_NAME}")
    
    print("\n👥 USERS:")
    print(f"  - Sales Reps: {len(sales_reps)} (The Office)")
    print(f"  - Partners: {len(partners)} (Harry Potter)")
    print(f"  - Contractors: {len(contractors)} (Marvel)")
    
    print("\n🏢 ORGANIZATIONS:")
    print(f"  - Customer Accounts: {len(accounts)} (DC & Marvel)")
    print(f"  - Contractor Businesses: {len(contractor_businesses)} (Marvel)")
    
    print("\n💼 SALES:")
    print(f"  - Deals: {len(deals)} (with owners & partners)")
    print(f"  - Contracts: {len(contracts)} (PS & FS mix)")
    
    print("\n🗂️ WORK:")
    print(f"  - Projects: {len(projects)} (Professional Services)")
    print(f"  - Jobs: {len(jobs)} (Field Services)")
    print(f"  - Tasks: {len(tasks)} (Mixed)")
    
    print("\n⏱️ TIME TRACKING:")
    print(f"  - Total Time Entries: {len(time_entries)}")
    print(f"    • Admin Approved (Historical): {status_counts['admin_approved']}")
    print(f"    • PM Approved (Ready for Finance): {status_counts['pm_approved']}")
    print(f"    • Submitted (Waiting for PM): {status_counts['submitted']}")
    print(f"    • Draft (Not submitted): {status_counts['draft']}")
    
    print("\n💰 AP BILLS:")
    all_bills = await db.ap_bills.find({"is_test_data": True}).to_list(100)
    paid_bills = [b for b in all_bills if b["status"] == "paid"]
    pending_bills = [b for b in all_bills if b["status"] == "pending_finance_review"]
    print(f"  - Total AP Bills: {len(all_bills)}")
    print(f"    • Paid (Historical): {len(paid_bills)} - ${sum(b['total'] for b in paid_bills):,.2f}")
    print(f"    • Pending Finance Review: {len(pending_bills)} - ${sum(b['total'] for b in pending_bills):,.2f}")
    
    print("\n✨ TEST DATA STANDARDS COMPLIANCE:")
    print("  ✅ All contractors: Marvel Universe characters")
    print("  ✅ All sales reps: The Office characters")
    print("  ✅ All partners: Harry Potter characters")
    print("  ✅ All companies: DC & Marvel universes")
    print("  ✅ All emails: .test domain")
    print("  ✅ All records: is_test_data=True flag")
    
    print("\n🎯 READY TO TEST:")
    print("  1. Deal → Contract matching")
    print("  2. Resource assignment workflow")
    print("  3. Projects vs Jobs structure")
    print("  4. Task management")
    print("  5. Time entry approval workflow (PM → Finance)")
    print("  6. AP bill generation and batching")
    print("  7. Historical data for reporting")
    print("  8. QBO integration preparation")
    
    client.close()

if __name__ == "__main__":
    asyncio.run(main())

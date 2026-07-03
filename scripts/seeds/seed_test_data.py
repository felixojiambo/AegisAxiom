"""
AegisAxiom - Comprehensive Test Data Seeder
Follows /app/TEST_DATA_CONVENTIONS.md

Universes:
  - Contractors: Marvel (MCU)
  - Sales Reps: The Office
  - Partners: Harry Potter
  - Companies: DC & Marvel & Sci-Fi
  - Projects: Universe-matched tech names
"""

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# Load env from backend
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', 'backend', '.env'))

from motor.motor_asyncio import AsyncIOMotorClient
from auth_helper import hash_password

DEFAULT_PASSWORD = "password123"
NOW = datetime.now(timezone.utc)

def uid():
    return f"user_{uuid.uuid4().hex[:12]}"

def gid(prefix=""):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ─────────────────────────────────────────────
# USERS
# ─────────────────────────────────────────────

SALES_REPS = [
    # Universe: The Office
    {"name": "Michael Scott",    "email": "michael.scott@dundermifflin.test"},
    {"name": "Jim Halpert",      "email": "jim.halpert@dundermifflin.test"},
    {"name": "Dwight Schrute",   "email": "dwight.schrute@dundermifflin.test"},
]

PARTNERS = [
    # Universe: Harry Potter
    {"name": "Harry Potter",       "email": "harry.potter@hogwarts.test",    "commission_rate": 0.10},
    {"name": "Hermione Granger",   "email": "hermione.granger@ministry.test","commission_rate": 0.12},
    {"name": "Albus Dumbledore",   "email": "albus.dumbledore@hogwarts.test","commission_rate": 0.15},
]

CONTRACTORS = [
    # Universe: Marvel — SHIELD Contractors LLC
    {"name": "Peter Parker",      "email": "peter.parker@dailybugle.test",   "rate": 125, "business": "SHIELD Contractors LLC"},
    {"name": "Natasha Romanoff",  "email": "natasha.romanoff@shield.test",   "rate": 135, "business": "SHIELD Contractors LLC"},
    {"name": "Clint Barton",      "email": "clint.barton@shield.test",       "rate": 115, "business": "SHIELD Contractors LLC"},
    # Universe: Marvel — X-Mansion Tech Services
    {"name": "Wade Wilson",       "email": "wade.wilson@mercenary.test",     "rate": 140, "business": "X-Mansion Tech Services"},
    {"name": "Carol Danvers",     "email": "carol.danvers@starforce.test",   "rate": 145, "business": "X-Mansion Tech Services"},
    {"name": "Scott Lang",        "email": "scott.lang@antman.test",         "rate": 105, "business": "X-Mansion Tech Services"},
    # Universe: Marvel — Avengers Technical Solutions
    {"name": "Bruce Banner",      "email": "bruce.banner@avengers.test",     "rate": 150, "business": "Avengers Technical Solutions"},
    {"name": "Wanda Maximoff",    "email": "wanda.maximoff@avengers.test",   "rate": 130, "business": "Avengers Technical Solutions"},
    # Universe: Marvel — Solo
    {"name": "Stephen Strange",   "email": "stephen.strange@sanctum.test",   "rate": 160, "business": None},
    {"name": "T'Challa",          "email": "tchalla@wakanda.test",           "rate": 155, "business": None},
]

# Director user — Universe: Marvel
DIRECTOR = {"name": "Nick Fury", "email": "nick.fury@shield.test", "role": "director"}

# Finance user — Universe: The Office
FINANCE = {"name": "Oscar Martinez", "email": "oscar.martinez@dundermifflin.test", "role": "finance"}


# ─────────────────────────────────────────────
# ACCOUNTS (Companies)
# ─────────────────────────────────────────────

ACCOUNTS = [
    # Universe: DC
    {"name": "Wayne Enterprises",    "industry": "Security & Defense",       "revenue": 50000000, "source": "Partner Referral"},
    # Universe: Marvel
    {"name": "Stark Industries",     "industry": "Technology & Defense",     "revenue": 75000000, "source": "Direct"},
    # Universe: DC
    {"name": "LexCorp",             "industry": "Research & Development",    "revenue": 30000000, "source": "Partner Referral"},
    # Universe: Marvel
    {"name": "Oscorp Industries",   "industry": "Biotechnology",             "revenue": 45000000, "source": "Direct"},
    # Universe: DC
    {"name": "Queen Consolidated",  "industry": "Manufacturing",             "revenue": 35000000, "source": "Partner Referral"},
]


# ─────────────────────────────────────────────
# CONTRACTOR BUSINESSES
# ─────────────────────────────────────────────

BUSINESSES = [
    {
        "business_name": "SHIELD Contractors LLC",
        "contact_name": "Phil Coulson",
        "email": "phil.coulson@shield.test",
        "tax_id_ein": "12-3456789",
        "w9_on_file": True,
        "is_1099_eligible": True,
        "billing_frequency": "weekly",
        "payment_terms": "net_30",
        "payment_method": "ACH",
    },
    {
        "business_name": "X-Mansion Tech Services",
        "contact_name": "Charles Xavier",
        "email": "charles.xavier@xmansion.test",
        "tax_id_ein": "98-7654321",
        "w9_on_file": True,
        "is_1099_eligible": True,
        "billing_frequency": "biweekly",
        "payment_terms": "net_30",
        "payment_method": "ACH",
    },
    {
        "business_name": "Avengers Technical Solutions",
        "contact_name": "Pepper Potts",
        "email": "pepper.potts@avengers.test",
        "tax_id_ein": "55-1234567",
        "w9_on_file": False,
        "is_1099_eligible": True,
        "billing_frequency": "monthly",
        "payment_terms": "net_45",
        "payment_method": "Check",
    },
]


async def seed():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL"))
    db = client[os.environ.get("DB_NAME", "test_database")]

    # Check if test data already exists
    existing_test = await db.users.count_documents({"is_test_data": True})
    if existing_test > 0:
        print(f"⚠️  Found {existing_test} existing test users. Skipping seed to avoid duplicates.")
        print("   Run cleanup first if you want to re-seed.")
        return

    print("🚀 Seeding AegisAxiom test data...\n")

    # Track IDs for linking
    user_ids = {}       # email -> user_id
    account_ids = {}    # name -> account_id
    contract_ids = {}   # name -> contract_id
    project_ids = {}    # name -> project_id
    business_ids = {}   # name -> business_id

    # ── CONTRACTOR BUSINESSES ────────────────────
    print("🏢 Creating contractor businesses...")
    for b in BUSINESSES:
        bid = gid("biz")
        business_ids[b["business_name"]] = bid
        await db.contractor_businesses.insert_one({
            "business_id": bid,
            "business_name": b["business_name"],
            "contact_name": b["contact_name"],
            "email": b["email"],
            "tax_id_ein": b["tax_id_ein"],
            "w9_on_file": b["w9_on_file"],
            "is_1099_eligible": b["is_1099_eligible"],
            "billing_frequency": b["billing_frequency"],
            "payment_terms": b["payment_terms"],
            "payment_method": b["payment_method"],
            "status": "active",
            "is_test_data": True,
            "created_at": NOW,
            "updated_at": NOW,
        })
        print(f"   ✅ {b['business_name']}")

    # ── USERS ────────────────────────────────────
    print("\n👥 Creating users...")

    # Director
    d_id = uid()
    user_ids[DIRECTOR["email"]] = d_id
    await db.users.insert_one({
        "user_id": d_id, "email": DIRECTOR["email"], "name": DIRECTOR["name"],
        "role": "director", "account_id": None, "picture": None,
        "is_active": True, "is_email_verified": True,
        "password_hash": hash_password(DEFAULT_PASSWORD),
        "is_test_data": True, "created_at": NOW,
    })
    print(f"   ✅ {DIRECTOR['name']} (director)")

    # Finance
    f_id = uid()
    user_ids[FINANCE["email"]] = f_id
    await db.users.insert_one({
        "user_id": f_id, "email": FINANCE["email"], "name": FINANCE["name"],
        "role": "finance", "account_id": None, "picture": None,
        "is_active": True, "is_email_verified": True,
        "password_hash": hash_password(DEFAULT_PASSWORD),
        "is_test_data": True, "created_at": NOW,
    })
    print(f"   ✅ {FINANCE['name']} (finance)")

    # Sales Reps
    for sr in SALES_REPS:
        sid = uid()
        user_ids[sr["email"]] = sid
        await db.users.insert_one({
            "user_id": sid, "email": sr["email"], "name": sr["name"],
            "role": "sales_rep", "account_id": None, "picture": None,
            "is_active": True, "is_email_verified": True,
            "password_hash": hash_password(DEFAULT_PASSWORD),
            "is_test_data": True, "created_at": NOW,
        })
        print(f"   ✅ {sr['name']} (sales_rep)")

    # Partners
    for p in PARTNERS:
        pid = uid()
        user_ids[p["email"]] = pid
        await db.users.insert_one({
            "user_id": pid, "email": p["email"], "name": p["name"],
            "role": "agent", "account_id": None, "picture": None,
            "is_active": True, "is_email_verified": True,
            "password_hash": hash_password(DEFAULT_PASSWORD),
            "commission_rate": p["commission_rate"],
            "is_test_data": True, "created_at": NOW,
        })
        print(f"   ✅ {p['name']} (agent/partner, {int(p['commission_rate']*100)}%)")

    # Contractors
    # Resource type assignments map — links contractors to resource types with cost rates
    RESOURCE_TYPE_ASSIGNMENTS = {
        "peter.parker@dailybugle.test":   [{"resource_type_name": "Mid-Level Developer", "cost_rate": 95.0},
                                            {"resource_type_name": "QA Engineer", "cost_rate": 70.0}],
        "natasha.romanoff@shield.test":   [{"resource_type_name": "SR Architect", "cost_rate": 155.0},
                                            {"resource_type_name": "Consultant", "cost_rate": 130.0}],
        "clint.barton@shield.test":       [{"resource_type_name": "SR Engineer", "cost_rate": 153.0},
                                            {"resource_type_name": "DevOps Engineer", "cost_rate": 100.0}],
        "wade.wilson@mercenary.test":     [{"resource_type_name": "Consultant", "cost_rate": 120.0},
                                            {"resource_type_name": "Senior Developer", "cost_rate": 110.0}],
        "carol.danvers@starforce.test":   [{"resource_type_name": "SR Architect", "cost_rate": 145.0},
                                            {"resource_type_name": "Project Manager", "cost_rate": 120.0}],
        "scott.lang@antman.test":         [{"resource_type_name": "Junior Developer", "cost_rate": 50.0},
                                            {"resource_type_name": "QA Engineer", "cost_rate": 60.0}],
        "bruce.banner@avengers.test":     [{"resource_type_name": "SR Architect", "cost_rate": 150.0},
                                            {"resource_type_name": "Senior Developer", "cost_rate": 130.0}],
        "wanda.maximoff@avengers.test":   [{"resource_type_name": "DevOps Engineer", "cost_rate": 110.0},
                                            {"resource_type_name": "Mid-Level Developer", "cost_rate": 95.0}],
        "stephen.strange@sanctum.test":   [{"resource_type_name": "SR Architect", "cost_rate": 160.0},
                                            {"resource_type_name": "Consultant", "cost_rate": 140.0}],
        "tchalla@wakanda.test":           [{"resource_type_name": "SR Engineer", "cost_rate": 140.0},
                                            {"resource_type_name": "Project Manager", "cost_rate": 125.0}],
    }

    for c in CONTRACTORS:
        cid = uid()
        user_ids[c["email"]] = cid
        
        # Build resource_type_assignments
        rtas = []
        for a in RESOURCE_TYPE_ASSIGNMENTS.get(c["email"], []):
            rtas.append({
                "resource_type_id": a["resource_type_name"],
                "resource_type_name": a["resource_type_name"],
                "cost_rate": a["cost_rate"],
                "is_active": True,
                "assigned_at": NOW,
                "assigned_by": "seed_script",
            })
        
        doc = {
            "user_id": cid, "email": c["email"], "name": c["name"],
            "role": "contractor", "account_id": None, "picture": None,
            "is_active": True, "is_email_verified": True,
            "password_hash": hash_password(DEFAULT_PASSWORD),
            "hourly_rate": c["rate"],
            "resource_type_assignments": rtas,
            "is_test_data": True, "created_at": NOW,
        }
        if c["business"] and c["business"] in business_ids:
            doc["contractor_business_id"] = business_ids[c["business"]]
        await db.users.insert_one(doc)
        print(f"   ✅ {c['name']} (contractor, ${c['rate']}/hr, {len(rtas)} resource types)")

    # ── ACCOUNTS ─────────────────────────────────
    print("\n🏢 Creating accounts...")
    for a in ACCOUNTS:
        aid = gid("acc")
        account_ids[a["name"]] = aid
        await db.accounts.insert_one({
            "account_id": aid,
            "name": a["name"],
            "industry": a["industry"],
            "annual_revenue": a["revenue"],
            "source": a["source"],
            "status": "active",
            "is_test_data": True,
            "created_at": NOW,
        })
        print(f"   ✅ {a['name']} ({a['industry']})")

    # ── DEALS ────────────────────────────────────
    print("\n💼 Creating deals...")
    deals_data = [
        {
            "name": "Watchtower Security System",
            "account": "Wayne Enterprises",
            "amount": 450000,
            "owner": "michael.scott@dundermifflin.test",
            "partner": "harry.potter@hogwarts.test",
            "stage": "Closed Won",
            "category": "professional_services",
        },
        {
            "name": "Arc Reactor Maintenance",
            "account": "Stark Industries",
            "amount": 280000,
            "owner": "jim.halpert@dundermifflin.test",
            "partner": None,
            "stage": "Closed Won",
            "category": "field_services",
        },
        {
            "name": "LexCorp Campus Security",
            "account": "LexCorp",
            "amount": 320000,
            "owner": "dwight.schrute@dundermifflin.test",
            "partner": "hermione.granger@ministry.test",
            "stage": "Closed Won",
            "category": "field_services",
        },
        {
            "name": "Oscorp IT Infrastructure",
            "account": "Oscorp Industries",
            "amount": 550000,
            "owner": "michael.scott@dundermifflin.test",
            "partner": "albus.dumbledore@hogwarts.test",
            "stage": "Closed Won",
            "category": "professional_services",
        },
        {
            "name": "Queen Manufacturing Support",
            "account": "Queen Consolidated",
            "amount": 180000,
            "owner": "jim.halpert@dundermifflin.test",
            "partner": None,
            "stage": "Closed Won",
            "category": "field_services",
        },
    ]

    deal_ids = {}
    for d in deals_data:
        did = gid("deal")
        deal_ids[d["name"]] = did
        doc = {
            "deal_id": did,
            "name": d["name"],
            "deal_name": d["name"],
            "account_id": account_ids[d["account"]],
            "account_name": d["account"],
            "amount": d["amount"],
            "stage": d["stage"],
            "owner_id": user_ids.get(d["owner"]),
            "owner_name": next((sr["name"] for sr in SALES_REPS if sr["email"] == d["owner"]), ""),
            "service_category": d["category"],
            "close_date": (NOW - timedelta(days=30)).isoformat(),
            "is_test_data": True,
            "created_at": NOW,
        }
        if d["partner"]:
            doc["agent_of_record_id"] = user_ids.get(d["partner"])
            doc["agent_of_record_name"] = next((p["name"] for p in PARTNERS if p["email"] == d["partner"]), "")
        await db.deals.insert_one(doc)
        print(f"   ✅ {d['name']} — ${d['amount']:,} ({d['stage']})")

    # ── CONTRACTS ────────────────────────────────
    print("\n📝 Creating contracts...")

    contracts_data = [
        {
            "name": "Watchtower Security - BOH",
            "account": "Wayne Enterprises",
            "deal": "Watchtower Security System",
            "type": "boh",
            "category": "professional_services",
            "resource_types": [
                {"name": "SR Architect",       "sell_rate": 225, "cost_rate": 135, "hours_purchased": 400},
                {"name": "SR Engineer",        "sell_rate": 185, "cost_rate": 110, "hours_purchased": 600},
                {"name": "Mid-Level Developer","sell_rate": 125, "cost_rate": 75,  "hours_purchased": 800},
            ],
        },
        {
            "name": "Arc Reactor Maintenance - BOA",
            "account": "Stark Industries",
            "deal": "Arc Reactor Maintenance",
            "type": "boa",
            "category": "field_services",
            "billing_period": "monthly",
            "invoice_terms": "net_30",
            "resource_types": [
                {"name": "Senior Developer", "sell_rate": 175, "cost_rate": 105, "hours_purchased": 0},
                {"name": "DevOps Engineer",  "sell_rate": 160, "cost_rate": 95,  "hours_purchased": 0},
            ],
        },
        {
            "name": "LexCorp Campus Security - BOH",
            "account": "LexCorp",
            "deal": "LexCorp Campus Security",
            "type": "boh",
            "category": "field_services",
            "resource_types": [
                {"name": "Consultant",       "sell_rate": 200, "cost_rate": 120, "hours_purchased": 1000},
                {"name": "Business Analyst", "sell_rate": 120, "cost_rate": 70,  "hours_purchased": 500},
            ],
        },
        {
            "name": "Oscorp IT Infrastructure - BOH",
            "account": "Oscorp Industries",
            "deal": "Oscorp IT Infrastructure",
            "type": "boh",
            "category": "professional_services",
            "resource_types": [
                {"name": "SR Architect",   "sell_rate": 225, "cost_rate": 135, "hours_purchased": 300},
                {"name": "DevOps Engineer","sell_rate": 160, "cost_rate": 95,  "hours_purchased": 500},
                {"name": "QA Engineer",    "sell_rate": 90,  "cost_rate": 55,  "hours_purchased": 400},
            ],
        },
        {
            "name": "Queen Manufacturing Support - BOA",
            "account": "Queen Consolidated",
            "deal": "Queen Manufacturing Support",
            "type": "boa",
            "category": "field_services",
            "billing_period": "biweekly",
            "invoice_terms": "net_14",
            "resource_types": [
                {"name": "Project Manager",  "sell_rate": 140, "cost_rate": 85, "hours_purchased": 0},
                {"name": "Junior Developer", "sell_rate": 85,  "cost_rate": 50, "hours_purchased": 0},
            ],
        },
    ]

    for c in contracts_data:
        cid = gid("con")
        contract_ids[c["name"]] = cid

        rt_list = []
        for rt in c.get("resource_types", []):
            rt_list.append({
                "id": gid("rt"),
                "name": rt["name"],
                "sell_rate": rt["sell_rate"],
                "cost_rate": rt.get("cost_rate", 0),
                "hours_purchased": rt.get("hours_purchased", 0),
                "hours_used": 0,
                "description": "",
            })

        doc = {
            "contract_id": cid,
            "name": c["name"],
            "account_id": account_ids[c["account"]],
            "account_name": c["account"],
            "contract_type": c["type"],
            "service_category": c["category"],
            "status": "active",
            "start_date": (NOW - timedelta(days=60)).isoformat(),
            "deal_ids": [deal_ids[c["deal"]]],
            "resource_types": rt_list,
            "assigned_resources": [],
            "is_test_data": True,
            "created_at": NOW,
        }
        if c["type"] == "boa":
            doc["billing_period"] = c.get("billing_period", "monthly")
            doc["invoice_terms"] = c.get("invoice_terms", "net_30")

        total_hours = sum(rt.get("hours_purchased", 0) for rt in c.get("resource_types", []))
        if total_hours > 0:
            doc["total_hours"] = total_hours
            doc["hours_used"] = 0

        await db.contracts.insert_one(doc)
        print(f"   ✅ {c['name']} ({c['type'].upper()}, {len(rt_list)} resource types)")

    # ── ASSIGN CONTRACTORS TO CONTRACTS ──────────
    print("\n👷 Assigning contractors to contracts...")
    
    # Mapping of contractors to contracts based on matching resource types
    contractor_assignments = [
        # Watchtower Security - BOH (SR Architect, SR Engineer, Mid-Level Developer)
        {
            "contract": "Watchtower Security - BOH",
            "contractor": "natasha.romanoff@shield.test",
            "resource_type": "SR Architect",
            "cost_rate": 155,
        },
        {
            "contract": "Watchtower Security - BOH",
            "contractor": "clint.barton@shield.test",
            "resource_type": "SR Engineer",
            "cost_rate": 153,
        },
        {
            "contract": "Watchtower Security - BOH",
            "contractor": "peter.parker@dailybugle.test",
            "resource_type": "Mid-Level Developer",
            "cost_rate": 95,
        },
        {
            "contract": "Watchtower Security - BOH",
            "contractor": "wanda.maximoff@avengers.test",
            "resource_type": "Mid-Level Developer",
            "cost_rate": 95,
        },
        # Arc Reactor Maintenance - BOA (Senior Developer, DevOps Engineer)
        {
            "contract": "Arc Reactor Maintenance - BOA",
            "contractor": "bruce.banner@avengers.test",
            "resource_type": "Senior Developer",
            "cost_rate": 130,
        },
        {
            "contract": "Arc Reactor Maintenance - BOA",
            "contractor": "clint.barton@shield.test",
            "resource_type": "DevOps Engineer",
            "cost_rate": 100,
        },
        # LexCorp Campus Security - BOH (Consultant)
        {
            "contract": "LexCorp Campus Security - BOH",
            "contractor": "natasha.romanoff@shield.test",
            "resource_type": "Consultant",
            "cost_rate": 130,
        },
        {
            "contract": "LexCorp Campus Security - BOH",
            "contractor": "stephen.strange@sanctum.test",
            "resource_type": "Consultant",
            "cost_rate": 140,
        },
        # Oscorp IT Infrastructure - BOH (SR Architect, DevOps Engineer, QA Engineer)
        {
            "contract": "Oscorp IT Infrastructure - BOH",
            "contractor": "bruce.banner@avengers.test",
            "resource_type": "SR Architect",
            "cost_rate": 150,
        },
        {
            "contract": "Oscorp IT Infrastructure - BOH",
            "contractor": "wanda.maximoff@avengers.test",
            "resource_type": "DevOps Engineer",
            "cost_rate": 110,
        },
        {
            "contract": "Oscorp IT Infrastructure - BOH",
            "contractor": "peter.parker@dailybugle.test",
            "resource_type": "QA Engineer",
            "cost_rate": 70,
        },
        # Queen Manufacturing Support - BOA (Project Manager, Junior Developer)
        {
            "contract": "Queen Manufacturing Support - BOA",
            "contractor": "carol.danvers@starforce.test",
            "resource_type": "Project Manager",
            "cost_rate": 120,
        },
        {
            "contract": "Queen Manufacturing Support - BOA",
            "contractor": "scott.lang@antman.test",
            "resource_type": "Junior Developer",
            "cost_rate": 50,
        },
    ]

    for assignment in contractor_assignments:
        contract = await db.contracts.find_one(
            {"contract_id": contract_ids[assignment["contract"]]},
            {"_id": 0}
        )
        if not contract:
            continue
            
        contractor_id = user_ids.get(assignment["contractor"])
        contractor_name = next((c["name"] for c in CONTRACTORS if c["email"] == assignment["contractor"]), "")
        
        # Find the matching resource type in the contract
        rt_entry = None
        for rt in contract.get("resource_types", []):
            if rt["name"] == assignment["resource_type"]:
                rt_entry = rt
                break
        
        if not rt_entry:
            continue
        
        # Add to assigned_resources
        assigned_resource = {
            "user_id": contractor_id,
            "contractor_name": contractor_name,
            "resource_type_id": rt_entry["id"],
            "resource_type_name": assignment["resource_type"],
            "cost_rate": assignment["cost_rate"],
            "allocation_mode": "percentage",
            "allocation_percentage": 50,  # Default 50% allocation
            "hours_allocated": 0,
            "assigned_at": NOW.isoformat(),
        }
        
        await db.contracts.update_one(
            {"contract_id": contract_ids[assignment["contract"]]},
            {"$push": {"assigned_resources": assigned_resource}}
        )
        print(f"   ✅ {contractor_name} → {assignment['contract']} ({assignment['resource_type']})")

    # ── PROJECTS ─────────────────────────────────
    print("\n📂 Creating projects...")

    projects_data = [
        {
            "name": "Watchtower Phase 1 - Security Architecture",
            "contract": "Watchtower Security - BOH",
            "status": "active",
            "start": NOW - timedelta(days=45),
            "end": NOW + timedelta(days=90),
            "pm": "nick.fury@shield.test",
        },
        {
            "name": "Watchtower Phase 2 - Implementation",
            "contract": "Watchtower Security - BOH",
            "status": "planning",
            "start": NOW + timedelta(days=91),
            "end": NOW + timedelta(days=270),
            "pm": "nick.fury@shield.test",
        },
        {
            "name": "Oscorp Infrastructure Discovery",
            "contract": "Oscorp IT Infrastructure - BOH",
            "status": "active",
            "start": NOW - timedelta(days=30),
            "end": NOW + timedelta(days=50),
            "pm": "nick.fury@shield.test",
        },
    ]

    for p in projects_data:
        pid = gid("proj")
        project_ids[p["name"]] = pid
        await db.projects.insert_one({
            "project_id": pid,
            "name": p["name"],
            "contract_id": contract_ids[p["contract"]],
            "status": p["status"],
            "start_date": p["start"].isoformat(),
            "end_date": p["end"].isoformat(),
            "pm_user_id": user_ids.get(p["pm"]),
            "pm_name": next((u["name"] for u in [DIRECTOR] if u["email"] == p["pm"]), ""),
            "contractors": [],
            "total_hours": 0,
            "billable_hours": 0,
            "is_test_data": True,
            "created_at": NOW,
        })
        print(f"   ✅ {p['name']} ({p['status']})")

    # ── TIME ENTRIES ─────────────────────────────
    print("\n⏱️  Creating time entries...")

    # Helper to create a week of entries
    def make_week_start(weeks_ago):
        d = NOW - timedelta(weeks=weeks_ago)
        return d - timedelta(days=d.weekday())  # Monday

    time_entry_count = 0

    # Week 1 (4 weeks ago) — approved entries
    week1 = make_week_start(4)
    for day_offset in range(5):  # Mon-Fri
        entry_date = week1 + timedelta(days=day_offset)
        # Peter Parker — Watchtower
        await db.time_entries.insert_one({
            "entry_id": gid("te"),
            "user_id": user_ids["peter.parker@dailybugle.test"],
            "user_name": "Peter Parker",
            "project_id": project_ids["Watchtower Phase 1 - Security Architecture"],
            "project_name": "Watchtower Phase 1 - Security Architecture",
            "contract_id": contract_ids["Watchtower Security - BOH"],
            "date": entry_date.strftime("%Y-%m-%d"),
            "hours": 4.0,
            "notes": "Network security configuration and firewall rules setup",
            "billable": True,
            "status": "approved",
            "week_start": week1.strftime("%Y-%m-%d"),
            "is_test_data": True,
            "created_at": NOW,
        })
        # Natasha Romanoff — Watchtower
        await db.time_entries.insert_one({
            "entry_id": gid("te"),
            "user_id": user_ids["natasha.romanoff@shield.test"],
            "user_name": "Natasha Romanoff",
            "project_id": project_ids["Watchtower Phase 1 - Security Architecture"],
            "project_name": "Watchtower Phase 1 - Security Architecture",
            "contract_id": contract_ids["Watchtower Security - BOH"],
            "date": entry_date.strftime("%Y-%m-%d"),
            "hours": 4.0,
            "notes": "Security requirements gathering and threat modeling sessions",
            "billable": True,
            "status": "approved",
            "week_start": week1.strftime("%Y-%m-%d"),
            "is_test_data": True,
            "created_at": NOW,
        })
        time_entry_count += 2

    # Week 2 (3 weeks ago) — submitted entries
    week2 = make_week_start(3)
    for day_offset in range(5):
        entry_date = week2 + timedelta(days=day_offset)
        # Peter Parker
        await db.time_entries.insert_one({
            "entry_id": gid("te"),
            "user_id": user_ids["peter.parker@dailybugle.test"],
            "user_name": "Peter Parker",
            "project_id": project_ids["Watchtower Phase 1 - Security Architecture"],
            "project_name": "Watchtower Phase 1 - Security Architecture",
            "contract_id": contract_ids["Watchtower Security - BOH"],
            "date": entry_date.strftime("%Y-%m-%d"),
            "hours": 4.0,
            "notes": "Implementation of intrusion detection system components",
            "billable": True,
            "status": "submitted",
            "week_start": week2.strftime("%Y-%m-%d"),
            "is_test_data": True,
            "created_at": NOW,
        })
        # Bruce Banner — Oscorp
        await db.time_entries.insert_one({
            "entry_id": gid("te"),
            "user_id": user_ids["bruce.banner@avengers.test"],
            "user_name": "Bruce Banner",
            "project_id": project_ids["Oscorp Infrastructure Discovery"],
            "project_name": "Oscorp Infrastructure Discovery",
            "contract_id": contract_ids["Oscorp IT Infrastructure - BOH"],
            "date": entry_date.strftime("%Y-%m-%d"),
            "hours": 3.0,
            "notes": "Infrastructure assessment and architecture review documentation",
            "billable": True,
            "status": "submitted",
            "week_start": week2.strftime("%Y-%m-%d"),
            "is_test_data": True,
            "created_at": NOW,
        })
        time_entry_count += 2

    # Week 3 (2 weeks ago) — draft entries
    week3 = make_week_start(2)
    for day_offset in range(5):
        entry_date = week3 + timedelta(days=day_offset)
        # Wanda Maximoff — Oscorp
        await db.time_entries.insert_one({
            "entry_id": gid("te"),
            "user_id": user_ids["wanda.maximoff@avengers.test"],
            "user_name": "Wanda Maximoff",
            "project_id": project_ids["Oscorp Infrastructure Discovery"],
            "project_name": "Oscorp Infrastructure Discovery",
            "contract_id": contract_ids["Oscorp IT Infrastructure - BOH"],
            "date": entry_date.strftime("%Y-%m-%d"),
            "hours": 4.0,
            "notes": "DevOps pipeline setup and container orchestration configuration",
            "billable": True,
            "status": "draft",
            "week_start": week3.strftime("%Y-%m-%d"),
            "is_test_data": True,
            "created_at": NOW,
        })
        time_entry_count += 1

    print(f"   ✅ {time_entry_count} time entries created (approved, submitted, draft)")

    # ── AR INVOICES ──────────────────────────────
    print("\n💵 Creating AR invoices (customer invoices)...")
    
    invoice_ids = {}
    
    # Invoice 1: Watchtower - Based on approved time entries (Week 1)
    inv1_id = gid("inv")
    invoice_ids["Watchtower Week 1"] = inv1_id
    # Peter Parker: 5 days × 4 hrs = 20 hrs @ $125/hr = $2,500
    # Natasha: 5 days × 4 hrs = 20 hrs @ $225/hr (SR Architect rate) = $4,500
    inv1_line_items = []
    # Peter's entries - 5 days
    for i in range(5):
        date = (NOW - timedelta(days=28 - i)).strftime("%Y-%m-%d")
        inv1_line_items.append({
            "date": date,
            "contractor_name": "Peter Parker",
            "description": "Security architecture design and implementation",
            "resource_type": "Mid-Level Developer",
            "hours": 4.0,
            "rate": 125.0,
            "amount": 500.0,
        })
    # Natasha's entries - 5 days
    for i in range(5):
        date = (NOW - timedelta(days=28 - i)).strftime("%Y-%m-%d")
        inv1_line_items.append({
            "date": date,
            "contractor_name": "Natasha Romanoff",
            "description": "Security requirements analysis and design review",
            "resource_type": "SR Architect",
            "hours": 4.0,
            "rate": 225.0,
            "amount": 900.0,
        })
    
    await db.ar_invoices.insert_one({
        "invoice_id": inv1_id,
        "invoice_number": "INV-2024-001",
        "contract_id": contract_ids["Watchtower Security - BOH"],
        "contract_name": "Watchtower Security - BOH",
        "account_id": account_ids["Wayne Enterprises"],
        "account_name": "Wayne Enterprises",
        "deal_ids": [deal_ids["Watchtower Security System"]],
        "period_start": (NOW - timedelta(days=28)).strftime("%Y-%m-%d"),
        "period_end": (NOW - timedelta(days=24)).strftime("%Y-%m-%d"),
        "line_items": inv1_line_items,
        "subtotal": 7000.0,
        "tax": 0.0,
        "total": 7000.0,
        "status": "paid",
        "issue_date": (NOW - timedelta(days=21)).strftime("%Y-%m-%d"),
        "due_date": (NOW - timedelta(days=1)).strftime("%Y-%m-%d"),
        "paid_date": (NOW - timedelta(days=5)).strftime("%Y-%m-%d"),
        "payment_method": "ACH",
        "is_test_data": True,
        "created_at": NOW,
    })
    print(f"   ✅ INV-2024-001: Watchtower Week 1 - $7,000 (PAID)")

    # Invoice 2: Oscorp - Based on submitted time entries (Week 2)
    inv2_id = gid("inv")
    invoice_ids["Oscorp Week 2"] = inv2_id
    # Bruce Banner: 5 days × 3 hrs = 15 hrs @ $225/hr (SR Architect rate) = $3,375
    inv2_line_items = []
    for i in range(5):
        date = (NOW - timedelta(days=21 - i)).strftime("%Y-%m-%d")
        inv2_line_items.append({
            "date": date,
            "contractor_name": "Bruce Banner",
            "description": "Infrastructure assessment and system architecture",
            "resource_type": "SR Architect",
            "hours": 3.0,
            "rate": 225.0,
            "amount": 675.0,
        })
    
    await db.ar_invoices.insert_one({
        "invoice_id": inv2_id,
        "invoice_number": "INV-2024-002",
        "contract_id": contract_ids["Oscorp IT Infrastructure - BOH"],
        "contract_name": "Oscorp IT Infrastructure - BOH",
        "account_id": account_ids["Oscorp Industries"],
        "account_name": "Oscorp Industries",
        "deal_ids": [deal_ids["Oscorp IT Infrastructure"]],
        "period_start": (NOW - timedelta(days=21)).strftime("%Y-%m-%d"),
        "period_end": (NOW - timedelta(days=17)).strftime("%Y-%m-%d"),
        "line_items": inv2_line_items,
        "subtotal": 3375.0,
        "tax": 0.0,
        "total": 3375.0,
        "status": "sent",
        "issue_date": (NOW - timedelta(days=14)).strftime("%Y-%m-%d"),
        "due_date": (NOW + timedelta(days=16)).strftime("%Y-%m-%d"),
        "paid_date": None,
        "payment_method": None,
        "is_test_data": True,
        "created_at": NOW,
    })
    print(f"   ✅ INV-2024-002: Oscorp Week 2 - $3,375 (SENT)")

    # Invoice 3: Watchtower - Another period
    inv3_id = gid("inv")
    invoice_ids["Watchtower Week 2"] = inv3_id
    inv3_line_items = []
    # Peter's entries - 5 days
    for i in range(5):
        date = (NOW - timedelta(days=21 - i)).strftime("%Y-%m-%d")
        inv3_line_items.append({
            "date": date,
            "contractor_name": "Peter Parker",
            "description": "Intrusion detection system implementation",
            "resource_type": "Mid-Level Developer",
            "hours": 4.0,
            "rate": 125.0,
            "amount": 500.0,
        })
    
    await db.ar_invoices.insert_one({
        "invoice_id": inv3_id,
        "invoice_number": "INV-2024-003",
        "contract_id": contract_ids["Watchtower Security - BOH"],
        "contract_name": "Watchtower Security - BOH",
        "account_id": account_ids["Wayne Enterprises"],
        "account_name": "Wayne Enterprises",
        "deal_ids": [deal_ids["Watchtower Security System"]],
        "period_start": (NOW - timedelta(days=21)).strftime("%Y-%m-%d"),
        "period_end": (NOW - timedelta(days=17)).strftime("%Y-%m-%d"),
        "line_items": inv3_line_items,
        "subtotal": 2500.0,
        "tax": 0.0,
        "total": 2500.0,
        "status": "overdue",
        "issue_date": (NOW - timedelta(days=45)).strftime("%Y-%m-%d"),
        "due_date": (NOW - timedelta(days=15)).strftime("%Y-%m-%d"),
        "paid_date": None,
        "payment_method": None,
        "is_test_data": True,
        "created_at": NOW,
    })
    print(f"   ✅ INV-2024-003: Watchtower Week 2 - $2,500 (OVERDUE)")

    # ── AP BILLS ─────────────────────────────────
    print("\n📄 Creating AP bills (contractor bills)...")
    
    bill_ids = {}
    
    # Bill 1: SHIELD Contractors LLC - Week 1 (Peter + Natasha)
    bill1_id = gid("bill")
    bill_ids["SHIELD Week 1"] = bill1_id
    
    # Get the actual time entries for this period and contractors
    week1_entries = await db.time_entries.find({
        "user_id": {"$in": [user_ids["peter.parker@dailybugle.test"], user_ids["natasha.romanoff@shield.test"]]},
        "week_start": (NOW - timedelta(days=28)).strftime("%Y-%m-%d"),
        "status": "approved"
    }, {"_id": 0}).to_list(100)
    
    await db.ap_bills.insert_one({
        "bill_id": bill1_id,
        "bill_number": "BILL-2024-001",
        "contractor_business_id": business_ids["SHIELD Contractors LLC"],
        "contractor_business_name": "SHIELD Contractors LLC",
        "contractor_name": "SHIELD Contractors LLC",
        "period_start": (NOW - timedelta(days=28)).strftime("%Y-%m-%d"),
        "period_end": (NOW - timedelta(days=24)).strftime("%Y-%m-%d"),
        "time_entries": week1_entries,  # Add detailed time entries
        "line_items": [
            {
                "contractor_id": user_ids["peter.parker@dailybugle.test"],
                "contractor_name": "Peter Parker",
                "resource_type": "Mid-Level Developer",
                "contract_id": contract_ids["Watchtower Security - BOH"],
                "contract_name": "Watchtower Security - BOH",
                "project_id": project_ids["Watchtower Phase 1 - Security Architecture"],
                "project_name": "Watchtower Phase 1 - Security Architecture",
                "hours": 20.0,
                "rate": 95.0,
                "amount": 1900.0,
            },
            {
                "contractor_id": user_ids["natasha.romanoff@shield.test"],
                "contractor_name": "Natasha Romanoff",
                "resource_type": "SR Architect",
                "contract_id": contract_ids["Watchtower Security - BOH"],
                "contract_name": "Watchtower Security - BOH",
                "project_id": project_ids["Watchtower Phase 1 - Security Architecture"],
                "project_name": "Watchtower Phase 1 - Security Architecture",
                "hours": 20.0,
                "rate": 155.0,
                "amount": 3100.0,
            },
        ],
        "subtotal": 5000.0,
        "tax": 0.0,
        "total": 5000.0,
        "status": "paid",
        "due_date": (NOW - timedelta(days=3)).strftime("%Y-%m-%d"),
        "paid_date": (NOW - timedelta(days=7)).strftime("%Y-%m-%d"),
        "payment_method": "ACH",
        "is_test_data": True,
        "created_at": NOW,
    })
    print(f"   ✅ BILL-2024-001: SHIELD Week 1 - $5,000 (PAID)")

    # Bill 2: Avengers Technical Solutions - Week 2 (Bruce)
    bill2_id = gid("bill")
    bill_ids["Avengers Week 2"] = bill2_id
    
    # Get the actual time entries for this period
    week2_entries = await db.time_entries.find({
        "user_id": user_ids["bruce.banner@avengers.test"],
        "week_start": (NOW - timedelta(days=21)).strftime("%Y-%m-%d"),
        "status": "submitted"
    }, {"_id": 0}).to_list(100)
    
    await db.ap_bills.insert_one({
        "bill_id": bill2_id,
        "bill_number": "BILL-2024-002",
        "contractor_business_id": business_ids["Avengers Technical Solutions"],
        "contractor_business_name": "Avengers Technical Solutions",
        "contractor_name": "Avengers Technical Solutions",
        "period_start": (NOW - timedelta(days=21)).strftime("%Y-%m-%d"),
        "period_end": (NOW - timedelta(days=17)).strftime("%Y-%m-%d"),
        "time_entries": week2_entries,  # Add detailed time entries
        "line_items": [
            {
                "contractor_id": user_ids["bruce.banner@avengers.test"],
                "contractor_name": "Bruce Banner",
                "resource_type": "SR Architect",
                "contract_id": contract_ids["Oscorp IT Infrastructure - BOH"],
                "contract_name": "Oscorp IT Infrastructure - BOH",
                "project_id": project_ids["Oscorp Infrastructure Discovery"],
                "project_name": "Oscorp Infrastructure Discovery",
                "hours": 15.0,
                "rate": 150.0,
                "amount": 2250.0,
            },
        ],
        "subtotal": 2250.0,
        "tax": 0.0,
        "total": 2250.0,
        "status": "approved",
        "due_date": (NOW + timedelta(days=20)).strftime("%Y-%m-%d"),
        "paid_date": None,
        "payment_method": None,
        "is_test_data": True,
        "created_at": NOW,
    })
    print(f"   ✅ BILL-2024-002: Avengers Week 2 - $2,250 (APPROVED)")

    # Bill 3: SHIELD Contractors - Week 2 (Peter only - submitted but not yet approved for payment)
    bill3_id = gid("bill")
    bill_ids["SHIELD Week 2"] = bill3_id
    
    # Get Peter's week 2 time entries
    peter_week2_entries = await db.time_entries.find({
        "user_id": user_ids["peter.parker@dailybugle.test"],
        "week_start": (NOW - timedelta(days=21)).strftime("%Y-%m-%d"),
        "status": "submitted"
    }, {"_id": 0}).to_list(100)
    
    await db.ap_bills.insert_one({
        "bill_id": bill3_id,
        "bill_number": "BILL-2024-003",
        "contractor_business_id": business_ids["SHIELD Contractors LLC"],
        "contractor_business_name": "SHIELD Contractors LLC",
        "contractor_name": "SHIELD Contractors LLC",
        "period_start": (NOW - timedelta(days=21)).strftime("%Y-%m-%d"),
        "period_end": (NOW - timedelta(days=17)).strftime("%Y-%m-%d"),
        "time_entries": peter_week2_entries,  # Add detailed time entries
        "line_items": [
            {
                "contractor_id": user_ids["peter.parker@dailybugle.test"],
                "contractor_name": "Peter Parker",
                "resource_type": "Mid-Level Developer",
                "contract_id": contract_ids["Watchtower Security - BOH"],
                "contract_name": "Watchtower Security - BOH",
                "project_id": project_ids["Watchtower Phase 1 - Security Architecture"],
                "project_name": "Watchtower Phase 1 - Security Architecture",
                "hours": 20.0,
                "rate": 95.0,
                "amount": 1900.0,
            },
        ],
        "subtotal": 1900.0,
        "tax": 0.0,
        "total": 1900.0,
        "status": "pending_finance_review",
        "due_date": (NOW + timedelta(days=10)).strftime("%Y-%m-%d"),
        "paid_date": None,
        "payment_method": None,
        "is_test_data": True,
        "created_at": NOW,
    })
    print(f"   ✅ BILL-2024-003: SHIELD Week 2 - $1,900 (PENDING REVIEW)")

    # ── PAYMENTS ─────────────────────────────────
    print("\n💰 Creating payment records...")
    
    payment_count = 0
    
    # Payment 1: AR Invoice 1 - Paid
    await db.payments.insert_one({
        "payment_id": gid("pay"),
        "type": "ar_invoice",
        "reference_id": inv1_id,
        "reference_number": "INV-2024-001",
        "account_id": account_ids["Wayne Enterprises"],
        "account_name": "Wayne Enterprises",
        "amount": 7000.0,
        "payment_method": "ACH",
        "payment_date": (NOW - timedelta(days=5)).strftime("%Y-%m-%d"),
        "notes": "Payment for Watchtower Security services - Week 1",
        "is_test_data": True,
        "created_at": NOW,
    })
    payment_count += 1
    
    # Payment 2: AP Bill 1 - Paid
    await db.payments.insert_one({
        "payment_id": gid("pay"),
        "type": "ap_bill",
        "reference_id": bill1_id,
        "reference_number": "BILL-2024-001",
        "contractor_business_id": business_ids["SHIELD Contractors LLC"],
        "contractor_business_name": "SHIELD Contractors LLC",
        "amount": 5000.0,
        "payment_method": "ACH",
        "payment_date": (NOW - timedelta(days=7)).strftime("%Y-%m-%d"),
        "notes": "Payment to SHIELD Contractors for Week 1 services",
        "is_test_data": True,
        "created_at": NOW,
    })
    payment_count += 1
    
    print(f"   ✅ {payment_count} payment records created")

    # ── SUMMARY ──────────────────────────────────
    print("\n" + "═" * 50)
    print("✅ TEST DATA SEEDING COMPLETE")
    print("═" * 50)
    print(f"""
    Users:                {2 + len(SALES_REPS) + len(PARTNERS) + len(CONTRACTORS)} 
      - Director:         1 (Nick Fury)
      - Finance:          1 (Oscar Martinez)
      - Sales Reps:       {len(SALES_REPS)} (The Office)
      - Partners:         {len(PARTNERS)} (Harry Potter)
      - Contractors:      {len(CONTRACTORS)} (Marvel)
    Accounts:             {len(ACCOUNTS)}
    Contractor Businesses:{len(BUSINESSES)}
    Deals:                {len(deals_data)}
    Contracts:            {len(contracts_data)}
      - With Resources:   {len(contracts_data)} (all assigned)
    Projects:             {len(projects_data)}
    Time Entries:         {time_entry_count}
    AR Invoices:          3 (1 paid, 1 sent, 1 overdue)
    AP Bills:             3 (1 paid, 1 approved, 1 pending)
    Payments:             {payment_count}

    All passwords: {DEFAULT_PASSWORD}
    All records flagged: is_test_data = true
    """)


if __name__ == "__main__":
    asyncio.run(seed())

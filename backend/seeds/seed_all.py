"""
seed_all.py -- Comprehensive seed script for AEGISAXIOM-1.

Creates fully-linked test data across ALL modules with deterministic IDs.
Every document is flagged with is_test_data: True for easy cleanup.
Idempotent: cleans existing test data before inserting.

Run:  cd backend && python -m seeds.seed_all
"""

import asyncio
import sys
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# Path & env setup  (must happen before any project imports)
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from database import init, get_db
from auth_helper import hash_password

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
NOW = datetime.now(timezone.utc)


def ts(delta_days: int = 0) -> datetime:
    """Return a UTC datetime offset from now by delta_days."""
    return NOW + timedelta(days=delta_days)


def ds(delta_days: int = 0) -> str:
    """Return a YYYY-MM-DD date string offset from now."""
    return (NOW + timedelta(days=delta_days)).strftime("%Y-%m-%d")


def week_bounds(date_str: str) -> tuple[str, str]:
    """Return (week_start Monday, week_end Sunday) for a date string."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    start = (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")
    end = (d + timedelta(days=6 - d.weekday())).strftime("%Y-%m-%d")
    return start, end


# ---------------------------------------------------------------------------
# Deterministic IDs
# ---------------------------------------------------------------------------
# Users
UID_ADMIN = "user_test_admin"
UID_DIRECTOR = "user_test_director"
UID_FINANCE = "user_test_finance"
UID_SALESREP = "user_test_salesrep"
UID_AGENT = "user_test_agent"
UID_PM = "user_test_pm"
UID_CONTRACTOR1 = "user_test_contractor1"
UID_CONTRACTOR2 = "user_test_contractor2"

# Contractor businesses
CB_1 = "cb_test_001"
CB_2 = "cb_test_002"

# Accounts
ACC_1, ACC_2, ACC_3, ACC_4, ACC_5 = [f"acc_test_{i:03d}" for i in range(1, 6)]

# Contacts (2-3 per account)
CONTACTS = {f"con_test_{i:03d}": None for i in range(1, 13)}
CON_IDS = list(CONTACTS.keys())

# Opportunities
OPP_IDS = [f"opp_test_{i:03d}" for i in range(1, 8)]

# Deals (only Closed Won opportunities become deals)
DEAL_IDS = [f"deal_test_{i:03d}" for i in range(1, 3)]

# Contracts
CONTRACT_IDS = [f"ctr_test_{i:03d}" for i in range(1, 4)]

# Projects (legacy)
PROJ_IDS = [f"proj_test_{i:03d}" for i in range(1, 4)]

# PM Projects
PMPROJ_IDS = [f"pmproj_test_{i:03d}" for i in range(1, 4)]

# PM Milestones
PMMS_IDS = [f"pmms_test_{i:03d}" for i in range(1, 7)]

# PM Tasks
PMT_IDS = [f"pmt_test_{i:03d}" for i in range(1, 13)]

# Time Entries
TE_IDS = [f"te_test_{i:03d}" for i in range(1, 16)]

# Commissions
COMM_IDS = [f"comm_test_{i:03d}" for i in range(1, 3)]

# AR Invoices
INV_IDS = [f"inv_test_{i:03d}" for i in range(1, 3)]

# AP Bills
BILL_IDS = [f"bill_test_{i:03d}" for i in range(1, 3)]

# Field Jobs
FJ_IDS = [f"fj_test_{i:03d}" for i in range(1, 5)]

# Dashboards
DASH_IDS = [f"dash_test_{i:03d}" for i in range(1, 3)]

# PM Dashboards
PMDASH_IDS = [f"pmdash_test_{i:03d}" for i in range(1, 3)]

# Resource Assignments
RA_IDS = [f"ra_test_{i:03d}" for i in range(1, 4)]

# Line Items
LI_IDS = [f"li_test_{i:03d}" for i in range(1, 5)]

DEFAULT_PASSWORD = hash_password("Test1234!")


# =========================================================================
# DATA BUILDERS
# =========================================================================

def build_users() -> list[dict]:
    """8 users across all roles."""
    base = dict(
        picture=None, is_active=True, is_email_verified=True,
        zoho_contact_id=None, created_at=ts(-90), is_test_data=True,
        password_hash=DEFAULT_PASSWORD,
    )
    return [
        {**base, "user_id": UID_ADMIN, "email": "testadmin@aegisaxiom.local",
         "name": "Tony Stark", "role": "admin", "permissions": ["*"]},
        {**base, "user_id": UID_DIRECTOR, "email": "testdirector@aegisaxiom.local",
         "name": "Nick Fury", "role": "director", "permissions": []},
        {**base, "user_id": UID_FINANCE, "email": "testfinance@aegisaxiom.local",
         "name": "Pepper Potts", "role": "finance", "permissions": []},
        {**base, "user_id": UID_SALESREP, "email": "testsalesrep@aegisaxiom.local",
         "name": "Steve Rogers", "role": "sales_rep", "permissions": []},
        {**base, "user_id": UID_AGENT, "email": "testagent@aegisaxiom.local",
         "name": "Natasha Romanoff", "role": "agent", "permissions": []},
        {**base, "user_id": UID_PM, "email": "testpm@aegisaxiom.local",
         "name": "Maria Hill", "role": "director", "permissions": []},
        {**base, "user_id": UID_CONTRACTOR1, "email": "testcontractor1@aegisaxiom.local",
         "name": "Peter Parker", "role": "contractor",
         "contractor_business_id": CB_1, "contractor_business_name": "Parker Industries",
         "permissions": []},
        {**base, "user_id": UID_CONTRACTOR2, "email": "testcontractor2@aegisaxiom.local",
         "name": "Scott Lang", "role": "contractor",
         "contractor_business_id": CB_2, "contractor_business_name": "Lang Tech Solutions",
         "permissions": []},
    ]


def build_contractor_businesses() -> list[dict]:
    """2 contractor businesses linked to contractor users."""
    base = dict(
        website=None, tax_id_ein="00-0000000", w9_on_file=True,
        is_1099_eligible=True, billing_frequency="weekly",
        payment_terms="net_30", payment_method="ACH",
        services=[], grievances=[], certifications="",
        coverage_areas=["Austin, TX"], status="active",
        is_test_data=True, created_at=ts(-80), updated_at=ts(-5),
    )
    return [
        {**base,
         "business_id": CB_1, "business_name": "Parker Industries",
         "contact_name": "Peter Parker", "email": "peter.parker@parkerindustries.test",
         "phone": "512-555-0101",
         "address_street": "100 Tech Ridge Blvd", "address_city": "Austin",
         "address_state": "TX", "address_zip": "78753",
         "tax_id_ein": "82-1234567",
         "resource_types": [
             {"resource_type_id": "rt_field_tech", "resource_type_name": "Field Service Tech",
              "hourly_rate": 75.0, "status": "active"},
         ],
         "overall_rating": 4.7},
        {**base,
         "business_id": CB_2, "business_name": "Lang Tech Solutions",
         "contact_name": "Scott Lang", "email": "scott.lang@langtech.test",
         "phone": "512-555-0202",
         "address_street": "4500 S Lamar Blvd", "address_city": "Austin",
         "address_state": "TX", "address_zip": "78745",
         "tax_id_ein": "83-7654321",
         "resource_types": [
             {"resource_type_id": "rt_lv_installer", "resource_type_name": "Low Voltage Installer",
              "hourly_rate": 85.0, "status": "active"},
         ],
         "overall_rating": 4.5},
    ]


def build_accounts() -> list[dict]:
    """5 accounts assigned to sales rep and agent users."""
    base = dict(
        account_type="company", assigned_sales_rep_id=UID_SALESREP,
        assigned_sales_rep_name="Steve Rogers",
        assigned_agent_id=UID_AGENT, assigned_agent_name="Natasha Romanoff",
        zoho_account_id=None,
        qbo_customer_id=None, is_partner=False, is_test_data=True,
    )
    return [
        {**base, "account_id": ACC_1, "name": "Wayne Enterprises",
         "email": "wayne@wayneenterprises.test", "phone": "512-555-1001",
         "website": "https://wayneenterprises.test", "address": "200 River Rd, Austin TX 78701",
         "industry": "Technology", "created_at": ts(-60)},
        {**base, "account_id": ACC_2, "name": "LexCorp",
         "email": "ops@lexcorp.test", "phone": "512-555-1002",
         "website": "https://lexcorp.test", "address": "800 Congress Ave, Austin TX 78701",
         "industry": "Logistics & Defense", "created_at": ts(-55)},
        {**base, "account_id": ACC_3, "name": "S.T.A.R. Labs",
         "email": "it@starlabs.test", "phone": "512-555-1003",
         "website": None, "address": "1500 N Lamar Blvd, Austin TX 78756",
         "industry": "Research & Development", "created_at": ts(-50)},
        {**base, "account_id": ACC_4, "name": "Queen Industries",
         "email": "tech@queenind.test", "phone": "512-555-1004",
         "website": "https://queenind.test", "address": "3200 Bee Cave Rd, Austin TX 78746",
         "industry": "Manufacturing", "created_at": ts(-45)},
        {**base, "account_id": ACC_5, "name": "Kord Industries",
         "email": "hello@kordindustries.test", "phone": "512-555-1005",
         "website": "https://kordindustries.test", "address": "900 E 6th St, Austin TX 78702",
         "industry": "Technology", "created_at": ts(-40)},
    ]


def build_contacts() -> list[dict]:
    """12 contacts, 2-3 per account. First contact per account is primary."""
    acct_names = {
        ACC_1: "Wayne Enterprises", ACC_2: "LexCorp",
        ACC_3: "S.T.A.R. Labs", ACC_4: "Queen Industries",
        ACC_5: "Kord Industries",
    }
    acct_email_domains = {
        ACC_1: "wayneenterprises.test", ACC_2: "lexcorp.test",
        ACC_3: "starlabs.test", ACC_4: "queenind.test",
        ACC_5: "kordindustries.test",
    }
    # (con_id, account_id, first, last, title, is_primary)
    specs = [
        (CON_IDS[0],  ACC_1, "Bruce", "Wayne", "CEO", True),
        (CON_IDS[1],  ACC_1, "Lucius", "Fox", "VP Engineering", False),
        (CON_IDS[2],  ACC_1, "Alfred", "Pennyworth", "Operations Director", False),
        (CON_IDS[3],  ACC_2, "Lex", "Luthor", "CEO", True),
        (CON_IDS[4],  ACC_2, "Mercy", "Graves", "Chief of Staff", False),
        (CON_IDS[5],  ACC_3, "Barry", "Allen", "Lead Scientist", True),
        (CON_IDS[6],  ACC_3, "Cisco", "Ramon", "Technology Coordinator", False),
        (CON_IDS[7],  ACC_3, "Caitlin", "Snow", "Research Director", False),
        (CON_IDS[8],  ACC_4, "Oliver", "Queen", "CEO", True),
        (CON_IDS[9],  ACC_4, "Felicity", "Smoak", "CTO", False),
        (CON_IDS[10], ACC_5, "Ted", "Kord", "Founder", True),
        (CON_IDS[11], ACC_5, "Jaime", "Reyes", "Project Lead", False),
    ]
    contacts = []
    for cid, aid, first, last, title, primary in specs:
        name = f"{first} {last}"
        domain = acct_email_domains[aid]
        contacts.append({
            "contact_id": cid, "account_id": aid,
            "account_name": acct_names[aid],
            "first_name": first, "last_name": last, "name": name,
            "email": f"{first.lower()}.{last.lower()}@{domain}",
            "phone": "512-555-2000", "mobile_phone": None,
            "title": title, "department": None,
            "linkedin_url": None, "preferred_contact_method": "email",
            "is_primary": primary, "is_active": True,
            "linked_user_id": None, "source": "seed",
            "zoho_contact_id": None, "notes": "",
            "is_test_data": True,
            "created_at": ts(-58), "updated_at": ts(-2),
        })
    return contacts


def build_opportunities() -> list[dict]:
    """7 opportunities — non-closed-won pipeline stages only."""
    stages = [
        "prospect_opp", "proposal_development", "proposal_sent",
        "negotiation_review", "closed_lost",
        "proposal_sent", "negotiation_review",
    ]
    acct_map = [
        (ACC_1, "Wayne Enterprises", CON_IDS[0], "Bruce Wayne"),
        (ACC_2, "LexCorp", CON_IDS[3], "Lex Luthor"),
        (ACC_3, "S.T.A.R. Labs", CON_IDS[5], "Barry Allen"),
        (ACC_4, "Queen Industries", CON_IDS[8], "Oliver Queen"),
        (ACC_5, "Kord Industries", CON_IDS[10], "Ted Kord"),
        (ACC_2, "LexCorp", CON_IDS[3], "Lex Luthor"),
        (ACC_3, "S.T.A.R. Labs", CON_IDS[5], "Barry Allen"),
    ]
    opp_names = [
        "Wayne Enterprises - Batcave Network Upgrade",
        "LexCorp - Warehouse Surveillance",
        "S.T.A.R. Labs - Particle Accelerator Security",
        "Queen Industries - Campus Access Control",
        "Kord Industries - Lab Monitoring System",
        "LexCorp - Warehouse Cabling",
        "S.T.A.R. Labs - Security Cameras",
    ]
    opps = []
    amounts = [25000, 48000, 72000, 35000, 18000, 48000, 72000]
    priorities = ["high", "medium", "low", "high", "low", "medium", "high"]
    for i, (opp_id, stage) in enumerate(zip(OPP_IDS, stages)):
        aid, aname, cid, cname = acct_map[i]
        opp = {
            "opportunity_id": opp_id,
            "name": opp_names[i],
            "account_id": aid, "account_name": aname,
            "contact_id": cid, "contact_name": cname,
            "agent_of_record_id": UID_AGENT, "agent_of_record_name": "Natasha Romanoff",
            "description": f"Seed opportunity in {stage} stage for {aname}.",
            "desired_proposal_date": ds(14),
            "priority": priorities[i],
            "assigned_sales_ops_id": UID_SALESREP, "assigned_sales_ops_name": "Steve Rogers",
            "amount": amounts[i],
            "stage": stage,
            "commission_payment_method": "standard",
            "lines_of_business": ["managed_services"],
            "opportunity_owner_id": UID_SALESREP,
            "opportunity_owner_name": "Steve Rogers",
            "converted_to_deal_id": None,
            "converted_at": None,
            "notes": [], "attachments": [], "call_recordings": [],
            "is_test_data": True,
            "created_at": ts(-45 + i * 5), "updated_at": ts(-3),
        }
        opps.append(opp)
    return opps


def build_deals() -> list[dict]:
    """2 deals — only Closed Won opportunities become deals."""
    return [
        {
            "deal_id": DEAL_IDS[0], "name": "Wayne Enterprises - Batcave Network Refresh",
            "account_id": ACC_1, "account_name": "Wayne Enterprises",
            "stage": "Closed Won", "amount": 95000, "close_date": ds(-5),
            "deal_owner_id": UID_SALESREP, "deal_owner_name": "Steve Rogers",
            "assigned_sales_rep_id": UID_SALESREP, "assigned_sales_rep_name": "Steve Rogers",
            "assigned_agent_id": UID_AGENT,
            "probability": 100, "description": "Full network infrastructure upgrade.",
            "zoho_deal_id": None, "is_test_data": True, "created_at": ts(-30),
        },
        {
            "deal_id": DEAL_IDS[1], "name": "Wayne Enterprises - Watchtower Comms",
            "account_id": ACC_1, "account_name": "Wayne Enterprises",
            "stage": "Closed Won", "amount": 95000, "close_date": ds(-10),
            "deal_owner_id": UID_SALESREP, "deal_owner_name": "Steve Rogers",
            "assigned_sales_rep_id": UID_SALESREP, "assigned_sales_rep_name": "Steve Rogers",
            "assigned_agent_id": UID_AGENT,
            "probability": 100, "description": "Watchtower communications system for Wayne Enterprises.",
            "zoho_deal_id": None, "is_test_data": True, "created_at": ts(-25),
        },
    ]


def build_contracts() -> list[dict]:
    """3 contracts, one per deal: boh, fixed_fee, monthly."""
    types = ["boh", "fixed_fee", "monthly"]
    names = [
        "Wayne Enterprises - BOH Contract",
        "LexCorp - Fixed Fee",
        "S.T.A.R. Labs - Monthly Support",
    ]
    accts = [
        (ACC_1, "Wayne Enterprises"),
        (ACC_2, "LexCorp"),
        (ACC_3, "S.T.A.R. Labs"),
    ]
    configs = [
        {"total_hours_purchased": 200, "hours_consumed": 45, "fixed_amount": None, "monthly_rate": None},
        {"total_hours_purchased": None, "hours_consumed": None, "fixed_amount": 48000, "monthly_rate": None},
        {"total_hours_purchased": None, "hours_consumed": None, "fixed_amount": None, "monthly_rate": 6000},
    ]
    contracts = []
    for i in range(3):
        aid, aname = accts[i]
        contracts.append({
            "contract_id": CONTRACT_IDS[i], "name": names[i],
            "account_id": aid, "account_name": aname,
            "contract_type": types[i],
            **configs[i],
            "start_date": ds(-25 + i * 5), "end_date": ds(340),
            "status": "active",
            "assigned_sales_rep_id": UID_SALESREP,
            "zoho_project_id": None,
            "created_at": ts(-25 + i * 5), "is_test_data": True,
        })
    return contracts


def build_projects() -> list[dict]:
    """3 legacy projects, one per contract."""
    names = [
        "Batcave Network Refresh", "LexCorp Cabling", "S.T.A.R. Labs Cameras",
    ]
    accts = [ACC_1, ACC_2, ACC_3]
    return [
        {
            "project_id": PROJ_IDS[i], "name": names[i],
            "description": f"Project for {names[i]}",
            "contract_id": CONTRACT_IDS[i], "account_id": accts[i],
            "project_manager_id": UID_PM, "project_manager_name": "Maria Hill",
            "status": "active",
            "contractor_ids": [UID_CONTRACTOR1, UID_CONTRACTOR2],
            "hourly_rate": 125.0, "budget_hours": [200, 150, 120][i],
            "hours_logged": [45.0, 22.5, 10.0][i],
            "assigned_sales_rep_id": UID_SALESREP,
            "start_date": ds(-20 + i * 3), "end_date": ds(340),
            "created_at": ts(-20 + i * 3), "updated_at": ts(-1),
            "is_test_data": True,
        }
        for i in range(3)
    ]


def build_pm_projects() -> list[dict]:
    """3 PM projects mirroring legacy projects."""
    names = [
        "Batcave Network Refresh", "LexCorp Cabling", "S.T.A.R. Labs Cameras",
    ]
    accts = [(ACC_1, "Wayne Enterprises"), (ACC_2, "LexCorp"), (ACC_3, "S.T.A.R. Labs")]
    ptypes = ["boh", "fixed_fee", "boa"]  # monthly -> boa
    healths = ["green", "yellow", "red"]
    statuses = ["active", "active", "planning"]
    prices = [95000, 48000, 72000]
    purchased = [200, 0, 0]
    estimated = [180, 140, 100]
    consumed = [45, 22.5, 0]
    return [
        {
            "project_id": PMPROJ_IDS[i], "name": names[i],
            "description": f"PM project for {names[i]}",
            "contract_id": CONTRACT_IDS[i], "contract_name": f"Contract {i+1}",
            "account_id": accts[i][0], "account_name": accts[i][1],
            "opportunity_id": OPP_IDS[4] if i == 0 else None,
            "project_type": ptypes[i],
            "sale_price": prices[i], "margin_goal_percent": 35.0,
            "cost_budget": prices[i] * 0.65,
            "purchased_hours": purchased[i],
            "estimated_hours": estimated[i],
            "consumed_hours": consumed[i],
            "remaining_hours": max(0, (purchased[i] or estimated[i]) - consumed[i]),
            "health_status": healths[i], "health_last_calculated": ts(-1),
            "project_manager_id": UID_PM, "project_manager_name": "Maria Hill",
            "assigned_engineers": [
                {"user_id": UID_CONTRACTOR1, "name": "Peter Parker", "role": "lead"},
                {"user_id": UID_CONTRACTOR2, "name": "Scott Lang", "role": "support"},
            ],
            "project_permissions": {},
            "start_date": ds(-20 + i * 3),
            "target_completion_date": ds(120 + i * 30),
            "actual_completion_date": None,
            "status": statuses[i],
            "documents": [],
            "is_test_data": True,
            "created_at": ts(-20 + i * 3), "updated_at": ts(-1),
            "created_by": UID_PM,
        }
        for i in range(3)
    ]


def build_pm_milestones() -> list[dict]:
    """6 milestones, 2 per PM project."""
    ms = []
    ms_statuses = [
        ("in_progress", "not_started"),
        ("completed", "in_progress"),
        ("not_started", "not_started"),
    ]
    for pi in range(3):
        for mi in range(2):
            idx = pi * 2 + mi
            status = ms_statuses[pi][mi]
            ms.append({
                "milestone_id": PMMS_IDS[idx],
                "project_id": PMPROJ_IDS[pi],
                "name": f"Phase {mi+1} - {'Site Prep' if mi == 0 else 'Installation'}",
                "description": f"Milestone {mi+1} for PM project {pi+1}",
                "due_date": ds(30 * (mi + 1) + pi * 15),
                "order": mi + 1,
                "status": status,
                "estimated_hours": [40, 60, 35, 50, 25, 30][idx],
                "actual_hours": [38, 0, 32, 20, 0, 0][idx],
                "completion_percentage": {"completed": 100, "in_progress": 50, "not_started": 0}[status],
                "created_at": ts(-18 + pi * 3), "updated_at": ts(-1),
            })
    return ms


def build_pm_tasks() -> list[dict]:
    """12 tasks, 2 per milestone."""
    tasks = []
    task_names = [
        "Survey site layout", "Order materials",
        "Run cable pathways", "Mount access points",
        "Configure warehouse switches", "Label and test drops",
        "Install conduit runs", "Terminate patch panels",
        "Plan camera placement", "Procure camera hardware",
        "Mount exterior cameras", "Configure NVR system",
    ]
    assignees = [
        (UID_CONTRACTOR1, "Peter Parker"),
        (UID_CONTRACTOR2, "Scott Lang"),
    ]
    statuses = [
        "completed", "in_progress",
        "completed", "in_progress",
        "completed", "completed",
        "in_progress", "not_started",
        "not_started", "not_started",
        "not_started", "not_started",
    ]
    priorities = [
        "high", "medium", "high", "high",
        "medium", "low", "high", "medium",
        "critical", "high", "high", "medium",
    ]
    for i in range(12):
        ms_idx = i // 2
        proj_idx = ms_idx // 2
        a_idx = i % 2
        est = [8, 12, 10, 16, 14, 10, 12, 8, 6, 10, 14, 12][i]
        act = [8, 6, 10, 8, 14, 10, 5, 0, 0, 0, 0, 0][i]
        tasks.append({
            "task_id": PMT_IDS[i],
            "project_id": PMPROJ_IDS[proj_idx],
            "milestone_id": PMMS_IDS[ms_idx],
            "parent_task_id": None,
            "name": task_names[i],
            "description": f"Task: {task_names[i]}",
            "estimated_hours": est,
            "actual_hours": act,
            "assigned_to": assignees[a_idx][0],
            "assigned_to_name": assignees[a_idx][1],
            "status": statuses[i],
            "priority": priorities[i],
            "due_date": ds(15 + i * 5),
            "start_date": ds(-15 + i * 2) if statuses[i] != "not_started" else None,
            "completed_at": ts(-5) if statuses[i] == "completed" else None,
            "order": (i % 2) + 1,
            "has_subtasks": False, "subtask_count": 0,
            "imported_from": None, "import_row_number": None,
            "created_at": ts(-18), "updated_at": ts(-1),
            "created_by": UID_PM,
        })
    return tasks


def build_time_entries() -> list[dict]:
    """15 time entries: 5 per project, across contractors and approval states."""
    entries = []
    te_statuses = [
        "admin_approved", "pm_approved", "submitted", "draft", "rejected",
        "admin_approved", "pm_approved", "submitted", "draft", "rejected",
        "admin_approved", "pm_approved", "submitted", "draft", "draft",
    ]
    hours_list = [3.5, 4.0, 2.5, 1.5, 3.0, 4.0, 2.0, 3.5, 2.5, 1.0, 3.0, 4.0, 2.0, 1.5, 2.5]
    for i in range(15):
        proj_idx = i // 5
        task_idx = proj_idx * 4 + (i % 4)  # map to tasks in same project
        contractor = UID_CONTRACTOR1 if i % 2 == 0 else UID_CONTRACTOR2
        contractor_name = "Peter Parker" if i % 2 == 0 else "Scott Lang"
        date_str = ds(-14 + i)
        ws, we = week_bounds(date_str)
        status = te_statuses[i]
        entry = {
            "entry_id": TE_IDS[i],
            "user_id": contractor,
            "user_name": contractor_name,
            "project_id": PMPROJ_IDS[proj_idx],
            "legacy_project_id": PROJ_IDS[proj_idx],
            "project_name": ["Batcave Network Refresh", "LexCorp Cabling", "S.T.A.R. Labs Cameras"][proj_idx],
            "contract_id": CONTRACT_IDS[proj_idx],
            "account_id": [ACC_1, ACC_2, ACC_3][proj_idx],
            "resource_type_id": "rt_field_tech" if i % 2 == 0 else "rt_lv_installer",
            "resource_type_name": "Field Service Tech" if i % 2 == 0 else "Low Voltage Installer",
            "pm_task_id": PMT_IDS[task_idx],
            "pm_task_name": f"Task {task_idx + 1}",
            "pm_subtask_id": None,
            "date": date_str,
            "hours": hours_list[i],
            "notes": f"Seed time entry {i+1} -- worked on {PMT_IDS[task_idx]}.",
            "note_quality": "good",
            "is_billable": True,
            "status": status,
            # PM approval
            "pm_approved_by": UID_PM if status in ("pm_approved", "admin_approved") else None,
            "pm_approved_at": ts(-3) if status in ("pm_approved", "admin_approved") else None,
            "pm_approval_notes": "Looks good" if status in ("pm_approved", "admin_approved") else None,
            # Admin approval
            "admin_approved_by": UID_ADMIN if status == "admin_approved" else None,
            "admin_approved_at": ts(-2) if status == "admin_approved" else None,
            "admin_approval_notes": "Approved" if status == "admin_approved" else None,
            "requires_admin_approval": status in ("pm_approved", "admin_approved"),
            # Rejection
            "rejected_by": UID_PM if status == "rejected" else None,
            "rejected_at": ts(-2) if status == "rejected" else None,
            "rejection_reason": "Hours seem high for task scope" if status == "rejected" else None,
            # Rates
            "cost_rate": 75.0 if i % 2 == 0 else 85.0,
            "sell_rate": 125.0,
            "margin_percent": 40.0 if i % 2 == 0 else 32.0,
            # Week
            "week_start": ws, "week_end": we,
            "is_late": False,
            "created_by": contractor,
            "locked": status == "admin_approved",
            "submitted_at": ts(-5) if status != "draft" else None,
            "is_test_data": True,
            "created_at": ts(-14 + i), "updated_at": ts(-1),
        }
        entries.append(entry)
    return entries


def build_commissions() -> list[dict]:
    """2 commissions: one paid, one pending."""
    return [
        {
            "commission_id": COMM_IDS[0],
            "deal_id": DEAL_IDS[0], "deal_name": "Wayne Enterprises - Batcave Network Refresh",
            "user_id": UID_SALESREP, "user_name": "Steve Rogers",
            "commission_type": "sales_rep",
            "amount_owed": 9500, "amount_paid": 9500, "potential_amount": 9500,
            "commission_rate": 0.10, "status": "paid",
            "notes": "Paid on deal close.", "paid_date": ds(-3),
            "created_at": ts(-5), "is_test_data": True,
        },
        {
            "commission_id": COMM_IDS[1],
            "deal_id": DEAL_IDS[0], "deal_name": "Wayne Enterprises - Batcave Network Refresh",
            "user_id": UID_AGENT, "user_name": "Natasha Romanoff",
            "commission_type": "agent",
            "amount_owed": 4750, "amount_paid": 0, "potential_amount": 4750,
            "commission_rate": 0.05, "status": "pending",
            "notes": "Agent commission pending finance review.", "paid_date": None,
            "created_at": ts(-5), "is_test_data": True,
        },
    ]


def build_ar_invoices() -> list[dict]:
    """2 AR invoices in the 'invoices' collection."""
    return [
        {
            "invoice_id": INV_IDS[0], "invoice_number": "INV-2026-T001",
            "account_id": ACC_1, "account_name": "Wayne Enterprises",
            "deal_id": DEAL_IDS[0], "contract_id": CONTRACT_IDS[0],
            "amount": 47500, "tax_amount": 0, "total_amount": 47500,
            "status": "sent", "due_date": ds(15), "paid_date": None,
            "qbo_invoice_id": None,
            "description": "First progress billing - network refresh",
            "assigned_sales_rep_id": UID_SALESREP,
            "is_past_due": False,
            "submitted_for_approval_at": ts(-4), "submitted_by": UID_FINANCE,
            "approved_by": UID_ADMIN, "approved_at": ts(-3), "approval_notes": "OK",
            "is_test_data": True, "created_at": ts(-5),
        },
        {
            "invoice_id": INV_IDS[1], "invoice_number": "INV-2026-T002",
            "account_id": ACC_2, "account_name": "LexCorp",
            "deal_id": None, "opportunity_id": OPP_IDS[6], "contract_id": CONTRACT_IDS[1],
            "amount": 24000, "tax_amount": 0, "total_amount": 24000,
            "status": "draft", "due_date": ds(45), "paid_date": None,
            "qbo_invoice_id": None,
            "description": "Cabling project - 50% milestone",
            "assigned_sales_rep_id": UID_SALESREP,
            "is_past_due": False,
            "submitted_for_approval_at": None, "submitted_by": None,
            "approved_by": None, "approved_at": None, "approval_notes": None,
            "is_test_data": True, "created_at": ts(-2),
        },
    ]


def build_ap_bills() -> list[dict]:
    """2 AP bills linked to contractors and time entries."""
    return [
        {
            "bill_id": BILL_IDS[0], "bill_number": "AP-2026-T001",
            "contractor_business_id": CB_1,
            "contractor_business_name": "Parker Industries",
            "contractor_user_id": UID_CONTRACTOR1,
            "contractor_name": "Peter Parker",
            "period_start": ds(-14), "period_end": ds(-8),
            "billing_frequency": "weekly",
            "line_items": [
                {
                    "contractor_name": "Peter Parker",
                    "project_name": "Batcave Network Refresh",
                    "contract_name": "Wayne Enterprises - BOH Contract",
                    "hours": 11.0, "rate": 75.0, "amount": 825.0,
                    "time_entry_ids": [TE_IDS[0], TE_IDS[2], TE_IDS[4]],
                },
            ],
            "time_entry_ids": [TE_IDS[0], TE_IDS[2], TE_IDS[4]],
            "subtotal": 825.0, "total": 825.0,
            "status": "approved", "due_date": ds(10),
            "payment_terms": "net_30", "payment_method": "ACH",
            "payment_date": None, "payment_notes": None,
            "created_at": ts(-7), "created_by": UID_FINANCE,
            "approved_at": ts(-5), "approved_by": UID_ADMIN,
            "paid_at": None, "paid_by": None,
            "notes": "", "is_test_data": True,
        },
        {
            "bill_id": BILL_IDS[1], "bill_number": "AP-2026-T002",
            "contractor_business_id": CB_2,
            "contractor_business_name": "Lang Tech Solutions",
            "contractor_user_id": UID_CONTRACTOR2,
            "contractor_name": "Scott Lang",
            "period_start": ds(-14), "period_end": ds(-8),
            "billing_frequency": "weekly",
            "line_items": [
                {
                    "contractor_name": "Scott Lang",
                    "project_name": "LexCorp Cabling",
                    "contract_name": "LexCorp - Fixed Fee",
                    "hours": 9.5, "rate": 85.0, "amount": 807.50,
                    "time_entry_ids": [TE_IDS[6], TE_IDS[8]],
                },
            ],
            "time_entry_ids": [TE_IDS[6], TE_IDS[8]],
            "subtotal": 807.50, "total": 807.50,
            "status": "draft", "due_date": ds(20),
            "payment_terms": "net_30", "payment_method": "ACH",
            "payment_date": None, "payment_notes": None,
            "created_at": ts(-3), "created_by": UID_FINANCE,
            "approved_at": None, "approved_by": None,
            "paid_at": None, "paid_by": None,
            "notes": "", "is_test_data": True,
        },
    ]


def build_field_jobs() -> list[dict]:
    """4 field jobs in various statuses."""
    statuses = ["scheduled", "confirmed", "in_progress", "completed"]
    names = [
        "Site Survey - Wayne Enterprises",
        "Cable Pull - LexCorp Warehouse",
        "Camera Install - S.T.A.R. Labs",
        "Network Rack Setup - Queen Industries",
    ]
    accts = [
        (ACC_1, "Wayne Enterprises", CON_IDS[0], "Bruce Wayne", "bruce.wayne@wayneenterprises.test"),
        (ACC_2, "LexCorp", CON_IDS[3], "Lex Luthor", "lex.luthor@lexcorp.test"),
        (ACC_3, "S.T.A.R. Labs", CON_IDS[5], "Barry Allen", "barry.allen@starlabs.test"),
        (ACC_4, "Queen Industries", CON_IDS[8], "Oliver Queen", "oliver.queen@queenind.test"),
    ]
    techs = [
        (UID_CONTRACTOR1, "Peter Parker"),
        (UID_CONTRACTOR2, "Scott Lang"),
        (UID_CONTRACTOR1, "Peter Parker"),
        (UID_CONTRACTOR2, "Scott Lang"),
    ]
    jobs = []
    for i in range(4):
        aid, aname, cid, cname, cemail = accts[i]
        tid, tname = techs[i]
        status = statuses[i]
        sched_date = ds(-7 + i * 5)
        jobs.append({
            "job_id": FJ_IDS[i], "name": names[i],
            "account_id": aid, "account_name": aname,
            "contract_id": CONTRACT_IDS[min(i, 2)],
            "deal_id": DEAL_IDS[0], "deal_name": "Wayne Enterprises - Batcave Network Refresh",
            "status": status, "priority": "high" if i < 2 else "medium",
            "job_category": "execution",
            "assigned_tech_id": tid, "assigned_tech_name": tname,
            "assigned_pm_id": UID_PM, "assigned_pm_name": "Maria Hill",
            "assigned_sales_rep_id": UID_SALESREP,
            "customer_contact_name": cname,
            "customer_contact_email": cemail,
            "customer_contact_phone": "512-555-3000",
            "scheduled_date": sched_date,
            "scheduled_start": f"{sched_date}T08:00:00Z",
            "scheduled_end": f"{sched_date}T12:00:00Z",
            "estimated_hours": 4.0,
            "actual_hours": 3.5 if status == "completed" else 0,
            "labor_cost": 262.50 if status == "completed" else 0,
            "material_cost": 0, "total_cost": 262.50 if status == "completed" else 0,
            "billable_hours": 3.5 if status == "completed" else 0,
            "description": f"Seed field job: {names[i]}",
            "notes": "",
            "check_in_time": ts(-2) if status in ("in_progress", "completed") else None,
            "check_out_time": ts(-2) + timedelta(hours=3, minutes=30) if status == "completed" else None,
            "photos": [],
            "job_type": f"jt_{i+1}", "job_type_name": names[i].split(" - ")[0],
            "checklist_checkin": [], "checklist_checkout": [],
            "is_test_data": True,
            "created_at": ts(-10), "updated_at": ts(-1),
        })
    return jobs


def build_dashboards() -> list[dict]:
    """2 dashboards: director overview, PM project board."""
    return [
        {
            "dashboard_id": DASH_IDS[0], "user_id": UID_DIRECTOR,
            "name": "Director Overview", "is_default": True,
            "widgets": [
                {"widget_id": "w1", "widget_type": "kpi_card", "title": "Revenue MTD",
                 "position": {"x": 0, "y": 0, "w": 4, "h": 2},
                 "config": {"metric": "revenue_mtd"}, "refresh_interval": 300},
                {"widget_id": "w2", "widget_type": "pipeline_chart", "title": "Deal Pipeline",
                 "position": {"x": 4, "y": 0, "w": 8, "h": 4},
                 "config": {"chart_type": "funnel"}, "refresh_interval": 600},
            ],
            "order": 1,
            "created_at": ts(-30), "updated_at": ts(-1), "is_test_data": True,
        },
        {
            "dashboard_id": DASH_IDS[1], "user_id": UID_PM,
            "name": "PM Project Board", "is_default": True,
            "widgets": [
                {"widget_id": "w3", "widget_type": "project_health", "title": "Project Health",
                 "position": {"x": 0, "y": 0, "w": 6, "h": 3},
                 "config": {"show_all": True}, "refresh_interval": 300},
                {"widget_id": "w4", "widget_type": "time_summary", "title": "Hours This Week",
                 "position": {"x": 6, "y": 0, "w": 6, "h": 3},
                 "config": {"period": "current_week"}, "refresh_interval": 300},
            ],
            "order": 1,
            "created_at": ts(-20), "updated_at": ts(-1), "is_test_data": True,
        },
    ]


def build_pm_dashboards() -> list[dict]:
    """2 PM dashboards stored in pm_dashboards collection (used by PM Dashboards view)."""
    return [
        {
            "dashboard_id": PMDASH_IDS[0],
            "name": "Project Overview",
            "description": "High-level view of project health and capacity.",
            "is_default": True,
            "order": 0,
            "widgets": [
                {
                    "widget_id": "pmw1", "widget_type": "projects_by_health",
                    "title": "Projects by Health", "order": 0,
                    "config": {},
                },
                {
                    "widget_id": "pmw2", "widget_type": "capacity_overview",
                    "title": "Capacity Overview", "order": 1,
                    "config": {},
                },
                {
                    "widget_id": "pmw3", "widget_type": "overdue_tasks",
                    "title": "Overdue Tasks", "order": 2,
                    "config": {"limit": 10},
                },
            ],
            "created_at": ts(-20), "updated_at": ts(-1),
            "created_by": UID_PM, "is_test_data": True,
        },
        {
            "dashboard_id": PMDASH_IDS[1],
            "name": "Resource Utilization",
            "description": "Track billing hours, resource allocation, and budget variance.",
            "is_default": False,
            "order": 1,
            "widgets": [
                {
                    "widget_id": "pmw4", "widget_type": "top_billing_resources",
                    "title": "Top Billing Resources", "order": 0,
                    "config": {"limit": 5},
                },
                {
                    "widget_id": "pmw5", "widget_type": "budget_variance",
                    "title": "Budget Variance", "order": 1,
                    "config": {"limit": 5},
                },
                {
                    "widget_id": "pmw6", "widget_type": "hours_by_resource",
                    "title": "Hours by Resource", "order": 2,
                    "config": {"limit": 10},
                },
            ],
            "created_at": ts(-15), "updated_at": ts(-1),
            "created_by": UID_PM, "is_test_data": True,
        },
    ]


def build_opportunity_resource_assignments() -> list[dict]:
    """3 resource assignments for active opportunities."""
    return [
        {
            "assignment_id": RA_IDS[0],
            "opportunity_id": OPP_IDS[2], "opportunity_name": "S.T.A.R. Labs - Particle Accelerator Security",
            "resource_type_id": "rt_field_tech", "resource_type_name": "Field Service Tech",
            "customer_contract_type": "boh",
            "customer_billing_schedule": [{"month": 1, "percent": 50}, {"month": 2, "percent": 50}],
            "customer_payment_terms": "net_30",
            "customer_hours_purchased": 120,
            "customer_sell_rate": 125.0, "total_sell": 15000,
            "contractor_business_id": CB_1, "contractor_business_name": "Parker Industries",
            "contractor_user_id": UID_CONTRACTOR1, "contractor_user_name": "Peter Parker",
            "contractor_pay_type": "hourly",
            "contractor_payment_terms": "net_30",
            "contractor_hours_estimated": 100,
            "contractor_cost_rate": 75.0, "total_cost": 7500,
            "profit_amount": 7500, "profit_margin": 50.0,
            "status": "active", "notes": "",
            "assigned_by": UID_SALESREP, "assigned_at": ts(-10),
            "created_at": ts(-10), "updated_at": ts(-2), "is_test_data": True,
        },
        {
            "assignment_id": RA_IDS[1],
            "opportunity_id": OPP_IDS[2], "opportunity_name": "S.T.A.R. Labs - Particle Accelerator Security",
            "resource_type_id": "rt_lv_installer", "resource_type_name": "Low Voltage Installer",
            "customer_contract_type": "boh",
            "customer_billing_schedule": [{"month": 1, "percent": 100}],
            "customer_payment_terms": "net_30",
            "customer_hours_purchased": 80,
            "customer_sell_rate": 140.0, "total_sell": 11200,
            "contractor_business_id": CB_2, "contractor_business_name": "Lang Tech Solutions",
            "contractor_user_id": UID_CONTRACTOR2, "contractor_user_name": "Scott Lang",
            "contractor_pay_type": "hourly",
            "contractor_payment_terms": "net_30",
            "contractor_hours_estimated": 80,
            "contractor_cost_rate": 85.0, "total_cost": 6800,
            "profit_amount": 4400, "profit_margin": 39.3,
            "status": "active", "notes": "",
            "assigned_by": UID_SALESREP, "assigned_at": ts(-10),
            "created_at": ts(-10), "updated_at": ts(-2), "is_test_data": True,
        },
        {
            "assignment_id": RA_IDS[2],
            "opportunity_id": OPP_IDS[3], "opportunity_name": "Queen Industries - Campus Access Control",
            "resource_type_id": "rt_field_tech", "resource_type_name": "Field Service Tech",
            "customer_contract_type": "boh",
            "customer_billing_schedule": [{"month": 1, "percent": 100}],
            "customer_payment_terms": "net_30",
            "customer_hours_purchased": 60,
            "customer_sell_rate": 125.0, "total_sell": 7500,
            "contractor_business_id": CB_1, "contractor_business_name": "Parker Industries",
            "contractor_user_id": UID_CONTRACTOR1, "contractor_user_name": "Peter Parker",
            "contractor_pay_type": "hourly",
            "contractor_payment_terms": "net_30",
            "contractor_hours_estimated": 60,
            "contractor_cost_rate": 75.0, "total_cost": 4500,
            "profit_amount": 3000, "profit_margin": 40.0,
            "status": "active", "notes": "",
            "assigned_by": UID_SALESREP, "assigned_at": ts(-8),
            "created_at": ts(-8), "updated_at": ts(-2), "is_test_data": True,
        },
    ]


def build_opportunity_line_items() -> list[dict]:
    """4 line items across opportunities."""
    return [
        {
            "line_item_id": LI_IDS[0],
            "opportunity_id": OPP_IDS[2],
            "category": "hardware",
            "item_name": "Cisco Catalyst 9300 Switch",
            "description": "48-port PoE+ managed switch",
            "quantity": 3, "unit": "each",
            "cost_price": 4200, "sell_price": 5800,
            "total_cost": 12600, "total_sell": 17400,
            "is_recurring": False, "recurring_term_months": None,
            "supplier_name": "Cisco Systems", "supplier_sku": "C9300-48P-A",
            "status": "approved", "notes": "",
            "created_at": ts(-10), "updated_at": ts(-3),
            "created_by": UID_SALESREP, "is_test_data": True,
        },
        {
            "line_item_id": LI_IDS[1],
            "opportunity_id": OPP_IDS[2],
            "category": "material",
            "item_name": "Cat6A Plenum Cable (1000ft)",
            "description": "Blue jacket, plenum rated",
            "quantity": 10, "unit": "box",
            "cost_price": 320, "sell_price": 450,
            "total_cost": 3200, "total_sell": 4500,
            "is_recurring": False, "recurring_term_months": None,
            "supplier_name": "Graybar", "supplier_sku": "C6A-PL-BLU-1K",
            "status": "approved", "notes": "",
            "created_at": ts(-10), "updated_at": ts(-3),
            "created_by": UID_SALESREP, "is_test_data": True,
        },
        {
            "line_item_id": LI_IDS[2],
            "opportunity_id": OPP_IDS[3],
            "category": "hardware",
            "item_name": "Verkada CD62 Dome Camera",
            "description": "Outdoor, 4K, AI analytics",
            "quantity": 12, "unit": "each",
            "cost_price": 850, "sell_price": 1200,
            "total_cost": 10200, "total_sell": 14400,
            "is_recurring": False, "recurring_term_months": None,
            "supplier_name": "Verkada", "supplier_sku": "CD62-HW",
            "status": "pending", "notes": "Awaiting vendor confirmation on lead time.",
            "created_at": ts(-8), "updated_at": ts(-2),
            "created_by": UID_SALESREP, "is_test_data": True,
        },
        {
            "line_item_id": LI_IDS[3],
            "opportunity_id": OPP_IDS[3],
            "category": "wholesale_mrr",
            "item_name": "Verkada Cloud License (Annual)",
            "description": "Per-camera cloud storage + analytics license",
            "quantity": 12, "unit": "license",
            "cost_price": 200, "sell_price": 350,
            "total_cost": 2400, "total_sell": 4200,
            "is_recurring": True, "recurring_term_months": 12,
            "supplier_name": "Verkada", "supplier_sku": "VRK-LIC-ANL",
            "status": "pending", "notes": "",
            "created_at": ts(-8), "updated_at": ts(-2),
            "created_by": UID_SALESREP, "is_test_data": True,
        },
    ]


# =========================================================================
# CLEANUP + SEED
# =========================================================================

COLLECTIONS_TO_CLEAN = [
    ("users", "is_test_data"),
    ("contractor_businesses", "is_test_data"),
    ("accounts", "is_test_data"),
    ("contacts", "is_test_data"),
    ("opportunities", "is_test_data"),
    ("deals", "is_test_data"),
    ("contracts", "is_test_data"),
    ("projects", "is_test_data"),
    ("pm_projects", "is_test_data"),
    ("pm_milestones", None),        # cleaned by project_id linkage
    ("pm_tasks", None),             # cleaned by project_id linkage
    ("time_entries", "is_test_data"),
    ("commissions", "is_test_data"),
    ("invoices", "is_test_data"),
    ("ap_bills", "is_test_data"),
    ("field_jobs", "is_test_data"),
    ("dashboards", "is_test_data"),
    ("pm_dashboards", "is_test_data"),
    ("opportunity_resource_assignments", "is_test_data"),
    ("opportunity_line_items", "is_test_data"),
]


async def clean(db) -> dict[str, int]:
    """Delete all test data. Returns counts per collection."""
    counts: dict[str, int] = {}

    # Milestones and tasks need project-based cleanup
    pm_proj_ids = PMPROJ_IDS
    for coll_name in ("pm_milestones", "pm_tasks"):
        r = await db[coll_name].delete_many({"project_id": {"$in": pm_proj_ids}})
        counts[coll_name] = r.deleted_count

    for coll_name, flag in COLLECTIONS_TO_CLEAN:
        if flag is None:
            continue  # already handled above
        r = await db[coll_name].delete_many({flag: True})
        counts[coll_name] = r.deleted_count

    return counts


async def seed_all():
    db = get_db()
    if db is None:
        # Standalone execution — init DB connection
        init(
            os.environ.get("MONGO_URL", "mongodb://localhost:27017"),
            os.environ.get("DB_NAME", "aegisaxiom"),
        )
        db = get_db()

    # ---- CLEANUP ----
    print("=" * 60)
    print("AEGISAXIOM-1 Seed Script -- Comprehensive Test Data")
    print("=" * 60)
    print("\n[1/3] Cleaning existing test data...")
    deleted = await clean(db)
    for coll, n in deleted.items():
        if n > 0:
            print(f"  - {coll}: deleted {n}")
    print("  Done.\n")

    # ---- SEED ROLES + PERMISSIONS ----
    # Both seeds are independent. The order is alphabetical by call name —
    # neither depends on the other's output.
    print("[2/3] Ensuring default roles + permission registry exist...")
    from routes.roles import seed_default_roles
    from services.permission_registry import seed_system_permissions
    await seed_default_roles(db)
    await seed_system_permissions(db)
    print("  Done.\n")

    # ---- SEED (dependency order) ----
    print("[3/3] Inserting test data...\n")
    summary: dict[str, int] = {}

    async def insert(collection: str, docs: list[dict]):
        if not docs:
            return
        # Strip _id to let Mongo generate it
        for d in docs:
            d.pop("_id", None)
        await db[collection].insert_many(docs)
        summary[collection] = len(docs)
        print(f"  + {collection}: {len(docs)} docs")

    await insert("users", build_users())
    await insert("contractor_businesses", build_contractor_businesses())
    await insert("accounts", build_accounts())
    await insert("contacts", build_contacts())
    await insert("opportunities", build_opportunities())
    await insert("deals", build_deals())
    await insert("contracts", build_contracts())
    await insert("projects", build_projects())
    await insert("pm_projects", build_pm_projects())
    await insert("pm_milestones", build_pm_milestones())
    await insert("pm_tasks", build_pm_tasks())
    await insert("time_entries", build_time_entries())
    await insert("commissions", build_commissions())
    await insert("invoices", build_ar_invoices())
    await insert("ap_bills", build_ap_bills())
    await insert("field_jobs", build_field_jobs())
    await insert("dashboards", build_dashboards())
    await insert("pm_dashboards", build_pm_dashboards())
    await insert("opportunity_resource_assignments", build_opportunity_resource_assignments())
    await insert("opportunity_line_items", build_opportunity_line_items())

    # ---- SUMMARY ----
    total = sum(summary.values())
    print(f"\n{'=' * 60}")
    print(f"SEED COMPLETE -- {total} documents across {len(summary)} collections")
    print(f"{'=' * 60}")
    for coll, n in summary.items():
        print(f"  {coll:45s} {n:>4d}")
    print(f"  {'TOTAL':45s} {total:>4d}")
    print(f"\nAdmin login: testadmin@aegisaxiom.local / Test1234!")
    print(f"All test data flagged with is_test_data: True")


# =========================================================================
# ENTRYPOINT
# =========================================================================

if __name__ == "__main__":
    init(os.environ["MONGO_URL"], os.environ["DB_NAME"])
    asyncio.run(seed_all())

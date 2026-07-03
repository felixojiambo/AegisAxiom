"""
Seed test field service jobs — uses existing test users, accounts, contracts, job types.
Each of the 12 job types gets 2-3 jobs in various statuses.
All marked with is_test_data: True.
"""
import asyncio
import os
import uuid
import random
from datetime import datetime, timezone, timedelta
from motor.motor_asyncio import AsyncIOMotorClient


def gid():
    return f"job_{uuid.uuid4().hex[:12]}"


NOW = datetime.now(timezone.utc)
STATUSES = ["scheduled", "confirmed", "in_progress", "completed", "cancelled"]


async def seed():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    db = client[os.environ.get("DB_NAME", "test_database")]

    # Check if already seeded
    existing = await db.field_jobs.count_documents({"is_test_data": True})
    if existing > 0:
        print(f"Found {existing} test field jobs. Clearing first...")
        await db.field_jobs.delete_many({"is_test_data": True})
        await db.job_activity.delete_many({"is_test_data": True})
        await db.account_operating_hours.delete_many({"is_test_data": True})
        await db.site_profiles.delete_many({"is_test_data": True})
        print("  Cleared.")

    # Load test users
    contractors = await db.users.find({"is_test_data": True, "role": "contractor"}, {"_id": 0}).to_list(50)
    pms = await db.users.find({"is_test_data": True, "role": {"$in": ["director", "sales_rep"]}}, {"_id": 0}).to_list(50)
    accounts = await db.accounts.find({"is_test_data": True}, {"_id": 0}).to_list(50)
    contracts = await db.contracts.find({"is_test_data": True, "service_category": "field_services"}, {"_id": 0}).to_list(50)
    job_types = await db.job_types.find({}, {"_id": 0}).to_list(50)

    print(f"Using: {len(contractors)} contractors, {len(pms)} PMs, {len(accounts)} accounts, {len(contracts)} FS contracts, {len(job_types)} job types\n")

    # Create operating hours for test accounts
    print("Setting account operating hours...")
    operating_configs = [
        {"open": "08:00", "close": "17:00"},  # Standard
        {"open": "09:00", "close": "18:00"},  # Late start
        {"open": "07:00", "close": "15:30"},  # Early start
        {"open": "06:00", "close": "22:00"},  # Extended
        {"open": "09:00", "close": "17:00"},  # Standard 2
    ]
    for i, acc in enumerate(accounts[:5]):
        cfg = operating_configs[i % len(operating_configs)]
        hours = {
            "account_id": acc["account_id"],
            "is_test_data": True,
        }
        for day in ["monday", "tuesday", "wednesday", "thursday", "friday"]:
            hours[day] = {"open": cfg["open"], "close": cfg["close"], "closed": False}
        hours["saturday"] = {"open": "09:00", "close": "13:00", "closed": i > 2}
        hours["sunday"] = {"open": "09:00", "close": "13:00", "closed": True}
        hours["timezone"] = "America/New_York"
        await db.account_operating_hours.update_one(
            {"account_id": acc["account_id"]}, {"$set": hours}, upsert=True
        )
        print(f"  {acc['name']}: {cfg['open']}-{cfg['close']} (Sat {'OPEN' if i <= 2 else 'CLOSED'})")

    # Create site profiles for test accounts
    print("\nCreating site profiles...")
    site_configs = [
        {"general": {"mdf_location": "Room 301-B, 3rd floor NW corner", "isp": "AT&T Fiber", "offices": 45, "conference_rooms": 8, "cubicles": 120},
         "network_equipment": {"rack_type": "42U Full Cabinet", "rack_us_available": 12, "rack_locked": True, "room_locked": True, "equipment_list": "2x Cisco Catalyst 9300, 1x Meraki MX250, 1x APC SmartUPS 3000VA"},
         "security": {"cameras": True, "camera_count": 24, "camera_make_model": "Verkada CD52", "door_access": True, "access_type": "HID proximity badge", "alarm_system": True},
         "fiber": {"backbone": True, "sm_or_mm": "SM", "strand_count": 24, "available_strands": 8, "panel": True, "connector_type": "LC"},
         "access_control": {"type": "Badge access + PIN", "notes": "Visitor badges at front desk, escort required past floor 2"}},
        {"general": {"mdf_location": "Server Room B-2 Basement Level", "isp": "Comcast Business", "offices": 30, "conference_rooms": 5, "cubicles": 80},
         "network_equipment": {"rack_type": "24U Wall Mount", "rack_us_available": 6, "rack_locked": False, "room_locked": True, "equipment_list": "1x Ubiquiti USW-Pro-48-PoE, 1x USG-Pro-4"},
         "security": {"cameras": True, "camera_count": 12, "camera_make_model": "Ubiquiti G4 Pro", "door_access": True, "access_type": "Keypad entry", "alarm_system": False},
         "fiber": {"backbone": False, "sm_or_mm": "", "strand_count": 0, "available_strands": 0, "panel": False, "connector_type": ""},
         "access_control": {"type": "Keypad", "notes": "Code changes monthly, get from facilities"}},
        {"general": {"mdf_location": "Telecom Closet TC-1, 1st floor behind reception", "isp": "Spectrum Enterprise", "offices": 60, "conference_rooms": 12, "cubicles": 200},
         "network_equipment": {"rack_type": "42U Open Frame", "rack_us_available": 18, "rack_locked": False, "room_locked": True, "equipment_list": "4x HP Aruba 2930F, 2x Aruba 7010 WLC, 1x Vertiv Liebert GXT5"},
         "security": {"cameras": True, "camera_count": 36, "camera_make_model": "Axis P3245-V", "door_access": True, "access_type": "RFID card reader", "alarm_system": True},
         "fiber": {"backbone": True, "sm_or_mm": "MM", "strand_count": 12, "available_strands": 4, "panel": True, "connector_type": "SC"},
         "access_control": {"type": "RFID + Biometric", "notes": "Must register fingerprint at security office, building manager Joe x2105"}},
    ]
    for i, acc in enumerate(accounts[:3]):
        sp = site_configs[i]
        sp["account_id"] = acc["account_id"]
        sp["account_name"] = acc["name"]
        sp["is_test_data"] = True
        sp["notes"] = f"Test site profile for {acc['name']}"
        sp["updated_at"] = NOW
        await db.site_profiles.update_one({"account_id": acc["account_id"]}, {"$set": sp}, upsert=True)
        print(f"  {acc['name']}: MDF={sp['general']['mdf_location'][:40]}")

    # Create field jobs
    print("\nCreating field service jobs...")

    # Customer contacts per account
    contacts = {
        0: {"customer_contact_name": "Lucius Fox", "customer_contact_email": "lucius@wayne.com", "customer_contact_phone": "555-0101"},
        1: {"customer_contact_name": "Pepper Potts", "customer_contact_email": "pepper@stark.com", "customer_contact_phone": "555-0202"},
        2: {"customer_contact_name": "Mercy Graves", "customer_contact_email": "mercy@lexcorp.com", "customer_contact_phone": "555-0303"},
        3: {"customer_contact_name": "Dr. Kafka", "customer_contact_email": "kafka@oscorp.com", "customer_contact_phone": "555-0404"},
        4: {"customer_contact_name": "Felicity Smoak", "customer_contact_email": "felicity@qc.com", "customer_contact_phone": "555-0505"},
    }

    # Job definitions per type
    job_defs = [
        # Cable Install
        {"jt": "cable_install", "jobs": [
            {"name": "Wayne Tower - Floor 3 Cat6A Install", "acct": 0, "con": 0, "tech": 0, "pm": 2, "status": "scheduled", "date_offset": 2, "start": "08:00", "end": "16:00", "hours": 8, "labor": 3200, "mat": 1800, "desc": "24 Cat6A drops across floor 3 open office area", "priority": "high"},
            {"name": "Stark Industries - Data Center Cabling", "acct": 1, "con": 1, "tech": 1, "pm": 3, "status": "in_progress", "date_offset": 0, "start": "09:00", "end": "17:00", "hours": 8, "labor": 4500, "mat": 2800, "desc": "48 Cat6 runs to new server room B-2", "priority": "urgent"},
            {"name": "LexCorp - Office Rewire Phase 2", "acct": 2, "con": 2, "tech": 2, "pm": 4, "status": "completed", "date_offset": -5, "start": "07:00", "end": "15:00", "hours": 8, "labor": 3800, "mat": 2100, "desc": "Replace legacy Cat5 with Cat6A on floors 4-5", "priority": "medium"},
        ]},
        # Conduit Install
        {"jt": "conduit_install", "jobs": [
            {"name": "Oscorp - Lab Building EMT Conduit", "acct": 3, "con": 3, "tech": 3, "pm": 2, "status": "confirmed", "date_offset": 1, "start": "08:00", "end": "14:00", "hours": 6, "labor": 2800, "mat": 1500, "desc": "120ft EMT conduit run from MPOE to Lab B", "priority": "high"},
            {"name": "Queen Consolidated - Warehouse Innerduct", "acct": 4, "con": 4, "tech": 4, "pm": 3, "status": "scheduled", "date_offset": 4, "start": "07:00", "end": "12:00", "hours": 5, "labor": 2200, "mat": 900, "desc": "200ft innerduct through warehouse ceiling to shipping office", "priority": "medium"},
        ]},
        # Cutover/Circuit Swap
        {"jt": "cutover_circuit_swap", "jobs": [
            {"name": "Wayne Enterprises - ISP Circuit Cutover", "acct": 0, "con": 5, "tech": 5, "pm": 2, "status": "confirmed", "date_offset": 1, "start": "22:00", "end": "23:59", "hours": 2, "labor": 800, "mat": 100, "desc": "Cutover from AT&T to Lumen — maintenance window 10pm-midnight", "priority": "urgent"},
            {"name": "Stark Industries - MPLS Migration", "acct": 1, "con": 6, "tech": 6, "pm": 3, "status": "completed", "date_offset": -3, "start": "21:00", "end": "23:00", "hours": 2, "labor": 1200, "mat": 200, "desc": "Swap MPLS circuit from Windstream to Zayo", "priority": "high"},
        ]},
        # Demarc Extension
        {"jt": "demarc_ext", "jobs": [
            {"name": "LexCorp - Demarc to 4th Floor MDF", "acct": 2, "con": 0, "tech": 0, "pm": 4, "status": "scheduled", "date_offset": 3, "start": "09:00", "end": "15:00", "hours": 6, "labor": 2800, "mat": 1600, "desc": "Extend fiber demarc from basement to 4th floor MDF", "priority": "high"},
            {"name": "Oscorp - New Wing Demarc Extension", "acct": 3, "con": 1, "tech": 1, "pm": 2, "status": "in_progress", "date_offset": 0, "start": "08:00", "end": "16:00", "hours": 8, "labor": 3500, "mat": 2200, "desc": "Copper demarc extension to new research wing", "priority": "medium"},
        ]},
        # Device Install
        {"jt": "device_install", "jobs": [
            {"name": "Wayne Tower - Meraki AP Deployment", "acct": 0, "con": 2, "tech": 2, "pm": 3, "status": "scheduled", "date_offset": 5, "start": "08:00", "end": "14:00", "hours": 6, "labor": 2400, "mat": 8500, "desc": "Install 12 Meraki MR46 APs across floors 2-4", "priority": "high"},
            {"name": "Queen Consolidated - Security Camera Install", "acct": 4, "con": 3, "tech": 3, "pm": 4, "status": "completed", "date_offset": -7, "start": "07:00", "end": "15:00", "hours": 8, "labor": 3200, "mat": 6800, "desc": "24 Verkada cameras — exterior and lobby coverage", "priority": "medium"},
            {"name": "Stark Industries - Switch Stack Upgrade", "acct": 1, "con": 4, "tech": 4, "pm": 2, "status": "confirmed", "date_offset": 2, "start": "09:00", "end": "13:00", "hours": 4, "labor": 1600, "mat": 12000, "desc": "Replace 2x old Catalyst 3750 with Catalyst 9300-48P", "priority": "urgent"},
        ]},
        # Fiber
        {"jt": "fiber", "jobs": [
            {"name": "LexCorp - Campus Fiber Backbone", "acct": 2, "con": 5, "tech": 5, "pm": 3, "status": "in_progress", "date_offset": -1, "start": "08:00", "end": "17:00", "hours": 9, "labor": 5500, "mat": 4200, "desc": "24-strand SM fiber between buildings A and C", "priority": "high"},
            {"name": "Oscorp - Lab Fiber Run", "acct": 3, "con": 6, "tech": 6, "pm": 4, "status": "scheduled", "date_offset": 6, "start": "09:00", "end": "16:00", "hours": 7, "labor": 3800, "mat": 2400, "desc": "12-strand MM fiber from MDF to clean room", "priority": "medium"},
        ]},
        # Pack & Ship
        {"jt": "pack_ship", "jobs": [
            {"name": "Wayne Enterprises - Equipment Return", "acct": 0, "con": 7, "tech": 7, "pm": 2, "status": "completed", "date_offset": -2, "start": "10:00", "end": "12:00", "hours": 2, "labor": 400, "mat": 50, "desc": "Pack and ship 3x retired switches + 2x UPS to vendor RMA", "priority": "low"},
            {"name": "Stark Industries - Lab Equipment Ship", "acct": 1, "con": 8, "tech": 8, "pm": 3, "status": "cancelled", "date_offset": -1, "start": "09:00", "end": "11:00", "hours": 2, "labor": 400, "mat": 75, "desc": "Ship lab equipment to satellite office — cancelled: repurposed on site", "priority": "low"},
        ]},
        # Phone Deployment
        {"jt": "phone_deployment", "jobs": [
            {"name": "LexCorp - Executive Floor VoIP", "acct": 2, "con": 9, "tech": 9, "pm": 4, "status": "confirmed", "date_offset": 2, "start": "09:00", "end": "14:00", "hours": 5, "labor": 2000, "mat": 3600, "desc": "Deploy 18 Poly Edge E400 phones on exec floor", "priority": "medium"},
            {"name": "Queen Consolidated - Call Center Phones", "acct": 4, "con": 0, "tech": 0, "pm": 2, "status": "completed", "date_offset": -8, "start": "08:00", "end": "16:00", "hours": 8, "labor": 3200, "mat": 8500, "desc": "Deploy 50 Yealink T46U phones for new call center", "priority": "high"},
        ]},
        # Rack Clean Up
        {"jt": "rack_cleanup", "jobs": [
            {"name": "Wayne Tower - MDF Rack Cleanup", "acct": 0, "con": 1, "tech": 1, "pm": 3, "status": "scheduled", "date_offset": 3, "start": "08:00", "end": "16:00", "hours": 8, "labor": 3200, "mat": 400, "desc": "Full cable management cleanup of 42U rack + port mapping", "priority": "medium"},
            {"name": "Stark Industries - Server Room Reorg", "acct": 1, "con": 2, "tech": 2, "pm": 4, "status": "in_progress", "date_offset": 0, "start": "09:00", "end": "17:00", "hours": 8, "labor": 3200, "mat": 600, "desc": "Reorganize 3 racks, replace all patch cables, update labels", "priority": "high"},
        ]},
        # SMART Hands
        {"jt": "smart_hands", "jobs": [
            {"name": "Oscorp - Remote Switch Config", "acct": 3, "con": 3, "tech": 3, "pm": 2, "status": "completed", "date_offset": -4, "start": "10:00", "end": "12:00", "hours": 2, "labor": 400, "mat": 0, "desc": "Smart hands for remote NOC — swap patch cables and power cycle switch 2", "priority": "medium"},
            {"name": "LexCorp - Remote AP Troubleshoot", "acct": 2, "con": 4, "tech": 4, "pm": 3, "status": "scheduled", "date_offset": 1, "start": "14:00", "end": "16:00", "hours": 2, "labor": 400, "mat": 0, "desc": "Smart hands for Meraki support — factory reset 2 APs and reconfigure", "priority": "high"},
        ]},
        # Circuit Troubleshooting
        {"jt": "circuit_troubleshoot", "jobs": [
            {"name": "Queen Consolidated - Dead Circuit Floor 2", "acct": 4, "con": 5, "tech": 5, "pm": 4, "status": "confirmed", "date_offset": 1, "start": "08:00", "end": "12:00", "hours": 4, "labor": 1600, "mat": 200, "desc": "No connectivity at 6 drops on floor 2 east wing — trace and repair", "priority": "urgent"},
            {"name": "Wayne Tower - Intermittent WAN", "acct": 0, "con": 6, "tech": 6, "pm": 2, "status": "completed", "date_offset": -6, "start": "09:00", "end": "13:00", "hours": 4, "labor": 1600, "mat": 100, "desc": "Intermittent WAN drops — suspect demarc termination", "priority": "high"},
        ]},
        # Device Troubleshooting
        {"jt": "device_troubleshoot", "jobs": [
            {"name": "Stark Industries - AP Not Broadcasting", "acct": 1, "con": 7, "tech": 7, "pm": 3, "status": "in_progress", "date_offset": 0, "start": "10:00", "end": "14:00", "hours": 4, "labor": 1600, "mat": 0, "desc": "3 APs on floor 5 not broadcasting SSID — check cables, PoE, config", "priority": "urgent"},
            {"name": "Oscorp - Camera Offline", "acct": 3, "con": 8, "tech": 8, "pm": 4, "status": "scheduled", "date_offset": 2, "start": "09:00", "end": "12:00", "hours": 3, "labor": 1200, "mat": 0, "desc": "4 cameras on north perimeter offline since Tuesday", "priority": "high"},
        ]},
    ]

    total = 0
    today = datetime.now(timezone.utc).date()

    for group in job_defs:
        jt_id = group["jt"]
        jt_doc = next((j for j in job_types if j["type_id"] == jt_id), None)
        if not jt_doc:
            print(f"  WARNING: Job type {jt_id} not found!")
            continue

        for j in group["jobs"]:
            acct = accounts[j["acct"] % len(accounts)]
            tech = contractors[j["tech"] % len(contractors)]
            pm = pms[j["pm"] % len(pms)]
            contract = contracts[j["acct"] % len(contracts)] if contracts else None
            contact = contacts.get(j["acct"] % 5, {})
            sched_date = (today + timedelta(days=j["date_offset"])).isoformat()

            # Build checklists from template
            checkin = [{"id": item["id"], "label": item["label"], "type": item.get("type", "boolean"), "options": item.get("options"), "required": item.get("required", False), "value": None, "notes": ""} for item in jt_doc.get("checkin", [])]
            checkout = [{"id": item["id"], "label": item["label"], "type": item.get("type", "boolean"), "options": item.get("options"), "required": item.get("required", False), "value": None, "notes": ""} for item in jt_doc.get("checkout", [])]

            # For completed/in_progress jobs, fill in some checklist items
            if j["status"] in ("completed", "in_progress"):
                for item in checkin:
                    if item["type"] == "boolean":
                        item["value"] = True
                    elif item["type"] == "text":
                        item["value"] = "Confirmed on site"
            if j["status"] == "completed":
                for item in checkout:
                    if item["type"] == "boolean":
                        item["value"] = True
                    elif item["type"] == "text":
                        item["value"] = "Completed per SOW"

            doc = {
                "job_id": gid(),
                "name": j["name"],
                "account_id": acct["account_id"],
                "account_name": acct["name"],
                "contract_id": contract["contract_id"] if contract else None,
                "job_type": jt_id,
                "job_type_name": jt_doc["name"],
                "status": j["status"],
                "priority": j["priority"],
                "assigned_tech_id": tech["user_id"],
                "assigned_tech_name": tech["name"],
                "assigned_pm_id": pm["user_id"],
                "assigned_pm_name": pm["name"],
                "assigned_sales_rep_id": None,
                "scheduled_date": sched_date,
                "scheduled_start": j["start"],
                "scheduled_end": j["end"],
                "estimated_hours": j["hours"],
                "actual_hours": j["hours"] if j["status"] == "completed" else 0,
                "labor_cost": j["labor"],
                "material_cost": j["mat"],
                "total_cost": j["labor"] + j["mat"],
                "billable_hours": j["hours"] if j["status"] == "completed" else 0,
                "description": j["desc"],
                "notes": "",
                "schedule_override": False,
                "checklist_checkin": checkin,
                "checklist_checkout": checkout,
                "photos": [],
                "is_test_data": True,
                "created_at": NOW - timedelta(days=max(0, -j["date_offset"]) + 3),
                "updated_at": NOW,
                **contact,
            }
            await db.field_jobs.insert_one(doc)
            total += 1
            print(f"  [{j['status']:12}] {jt_doc['name']:25} → {j['name']}")

    print(f"\n{total} test field jobs created across {len(job_defs)} job types.")
    print(f"Statuses: {', '.join(set(j['status'] for g in job_defs for j in g['jobs']))}")


if __name__ == "__main__":
    asyncio.run(seed())

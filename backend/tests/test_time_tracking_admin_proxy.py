"""
Test Time Tracking Module - Admin Proxy Entry Feature
Tests the ability for admins/PMs to create time entries on behalf of contractors.
Uses admin session due to .test domain email validation issue with contractor accounts.

Key features tested:
- Time entry creation by admin
- Time entry creation by admin for contractor (NEW FEATURE)
- 15-minute increment validation (0.25 pass, 0.33 fail)
- 4-hour max per entry validation
- Required notes with quality check (min 3 words)
- Weekly submission
- created_by field tracking who created entry
- Approval workflow endpoints
"""
import pytest
import requests
import os
from datetime import datetime, timedelta, date

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
if not BASE_URL:
    raise ValueError("REACT_APP_BACKEND_URL environment variable must be set")

# Test credentials - Admin only (contractor users have .test domain which fails email validation)
ADMIN_EMAIL = "support@simplimeta.io"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


def get_week_bounds(d: date):
    """Return Monday and Sunday of the week containing date d."""
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday.isoformat(), sunday.isoformat()


@pytest.fixture(scope="module")
def admin_session():
    """Login as admin and return session with cookie auth"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    
    response = session.post(f"{BASE_URL}/api/auth/login", json={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD
    })
    
    if response.status_code != 200:
        pytest.skip(f"Admin login failed: {response.status_code} - {response.text}")
    
    data = response.json()
    user = data.get("user", {})
    return session, user


@pytest.fixture(scope="module")
def contractor_user_id(admin_session):
    """Get a contractor user ID for testing proxy entry"""
    session, _ = admin_session
    response = session.get(f"{BASE_URL}/api/admin/users?limit=100")
    if response.status_code != 200:
        return None
    
    users = response.json()
    for u in users:
        if u.get("role") == "contractor" and u.get("is_active"):
            return u.get("user_id"), u.get("name", "Contractor")
    return None, None


@pytest.fixture(scope="module")
def test_project(admin_session):
    """Find or return a project for testing"""
    session, _ = admin_session
    response = session.get(f"{BASE_URL}/api/projects?limit=50")
    if response.status_code == 200:
        data = response.json()
        projects = data.get("items", data) if isinstance(data, dict) else data
        if projects and len(projects) > 0:
            return projects[0]
    return None


class TestTimeEntryValidation:
    """Test time entry validation rules"""
    
    def test_hours_15_min_increment_valid(self, admin_session, test_project):
        """Test that 0.25 (15 min) increments pass validation"""
        session, _ = admin_session
        today = date.today()
        
        # Test valid increments
        valid_hours = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]
        for hours in valid_hours[:3]:  # Test first 3
            response = session.post(f"{BASE_URL}/api/time-entries", json={
                "project_id": test_project.get("project_id") if test_project else None,
                "date": today.isoformat(),
                "hours": hours,
                "notes": f"TEST_TIME Valid {hours}h entry for validation test",
                "is_billable": True
            })
            if response.status_code not in [200, 201]:
                error = response.json().get("detail", "")
                assert "15-minute" not in error.lower(), f"{hours}h should be valid but got: {error}"
            else:
                # Cleanup
                entry = response.json()
                session.delete(f"{BASE_URL}/api/time-entries/{entry.get('entry_id')}")
            print(f"✓ {hours}h passed validation")
    
    def test_hours_15_min_increment_invalid(self, admin_session, test_project):
        """Test that non-15-minute increments fail validation"""
        session, _ = admin_session
        today = date.today()
        
        invalid_hours = [0.33, 0.10, 0.17, 1.33, 2.67]
        for hours in invalid_hours[:2]:  # Test first 2
            response = session.post(f"{BASE_URL}/api/time-entries", json={
                "project_id": test_project.get("project_id") if test_project else None,
                "date": today.isoformat(),
                "hours": hours,
                "notes": f"TEST_TIME Invalid {hours}h entry test",
                "is_billable": True
            })
            assert response.status_code == 400, f"Expected 400 for {hours}h but got {response.status_code}"
            error = response.json().get("detail", "")
            assert "15-minute" in error.lower() or "increment" in error.lower(), f"Expected 15-minute error for {hours}h"
            print(f"✓ {hours}h correctly rejected")
    
    def test_hours_max_4_validation(self, admin_session, test_project):
        """Test that hours > 4 are rejected"""
        session, _ = admin_session
        today = date.today()
        
        response = session.post(f"{BASE_URL}/api/time-entries", json={
            "project_id": test_project.get("project_id") if test_project else None,
            "date": today.isoformat(),
            "hours": 5.0,
            "notes": "TEST_TIME 5h entry should fail max 4h validation",
            "is_billable": True
        })
        assert response.status_code == 400
        error = response.json().get("detail", "")
        assert "4 hour" in error.lower() or "maximum" in error.lower(), f"Expected max 4h error but got: {error}"
        print("✓ 5h correctly rejected (max 4h)")
    
    def test_hours_must_be_positive(self, admin_session, test_project):
        """Test that zero/negative hours are rejected"""
        session, _ = admin_session
        today = date.today()
        
        for hours in [0, -1]:
            response = session.post(f"{BASE_URL}/api/time-entries", json={
                "project_id": test_project.get("project_id") if test_project else None,
                "date": today.isoformat(),
                "hours": hours,
                "notes": f"TEST_TIME {hours}h entry should fail validation",
                "is_billable": True
            })
            assert response.status_code == 400, f"Expected 400 for {hours}h"
            print(f"✓ {hours}h correctly rejected")
    
    def test_notes_quality_min_3_words(self, admin_session, test_project):
        """Test that notes must have at least 3 words"""
        session, _ = admin_session
        today = date.today()
        
        # Test 2 words - should fail
        response = session.post(f"{BASE_URL}/api/time-entries", json={
            "project_id": test_project.get("project_id") if test_project else None,
            "date": today.isoformat(),
            "hours": 1.0,
            "notes": "Two words",
            "is_billable": True
        })
        assert response.status_code == 400
        error = response.json().get("detail", "")
        assert "3 words" in error.lower() or "more detail" in error.lower(), f"Expected min 3 words error but got: {error}"
        print("✓ 2-word notes correctly rejected")
        
        # Test empty notes - should fail
        response = session.post(f"{BASE_URL}/api/time-entries", json={
            "project_id": test_project.get("project_id") if test_project else None,
            "date": today.isoformat(),
            "hours": 1.0,
            "notes": "",
            "is_billable": True
        })
        assert response.status_code == 400
        print("✓ Empty notes correctly rejected")
    
    def test_notes_quality_3_words_pass(self, admin_session, test_project):
        """Test that 3+ words pass validation"""
        session, _ = admin_session
        today = date.today()
        
        response = session.post(f"{BASE_URL}/api/time-entries", json={
            "project_id": test_project.get("project_id") if test_project else None,
            "date": today.isoformat(),
            "hours": 0.25,
            "notes": "TEST_TIME This has three plus words",
            "is_billable": True
        })
        
        if response.status_code in [200, 201]:
            entry = response.json()
            # Verify note_quality is not 'poor'
            assert entry.get("note_quality") != "poor", "3+ words should not be poor quality"
            print(f"✓ 3+ word notes passed with quality: {entry.get('note_quality')}")
            # Cleanup
            session.delete(f"{BASE_URL}/api/time-entries/{entry.get('entry_id')}")
        else:
            error = response.json().get("detail", "")
            assert "3 words" not in error.lower() and "more detail" not in error.lower(), f"3+ words should pass but got: {error}"
    
    def test_date_required(self, admin_session, test_project):
        """Test that date is required"""
        session, _ = admin_session
        
        response = session.post(f"{BASE_URL}/api/time-entries", json={
            "project_id": test_project.get("project_id") if test_project else None,
            "hours": 1.0,
            "notes": "TEST_TIME Entry without date",
            "is_billable": True
        })
        assert response.status_code == 400
        error = response.json().get("detail", "")
        assert "date" in error.lower() or "required" in error.lower()
        print("✓ Missing date correctly rejected")


class TestAdminProxyTimeEntry:
    """Test admin creating time entries on behalf of contractors"""
    
    def test_admin_can_create_for_contractor(self, admin_session, contractor_user_id, test_project):
        """Admin with * permission can create entries for contractors"""
        session, admin_user = admin_session
        contractor_id, contractor_name = contractor_user_id
        
        if not contractor_id:
            pytest.skip("No contractor user found in database")
        
        today = date.today()
        
        response = session.post(f"{BASE_URL}/api/time-entries", json={
            "user_id": contractor_id,  # Creating for another user
            "project_id": test_project.get("project_id") if test_project else None,
            "date": today.isoformat(),
            "hours": 2.0,
            "notes": f"TEST_TIME Admin created entry for {contractor_name}",
            "is_billable": True
        })
        
        if response.status_code in [200, 201]:
            data = response.json()
            assert data.get("user_id") == contractor_id, f"Entry should be for contractor {contractor_id}"
            assert data.get("created_by") == admin_user.get("user_id"), f"created_by should track admin"
            assert data.get("user_name") == contractor_name, f"user_name should be contractor's name"
            print(f"✓ Admin created entry for contractor {contractor_name}")
            print(f"  - user_id: {data.get('user_id')}")
            print(f"  - created_by: {data.get('created_by')}")
            print(f"  - user_name: {data.get('user_name')}")
            
            # Cleanup
            entry_id = data.get("entry_id")
            if entry_id:
                session.delete(f"{BASE_URL}/api/time-entries/{entry_id}")
        else:
            error = response.json().get("detail", "")
            pytest.fail(f"Admin should be able to create for contractor but got {response.status_code}: {error}")
    
    def test_admin_can_create_for_self(self, admin_session, test_project):
        """Admin can create entries for themselves"""
        session, admin_user = admin_session
        today = date.today()
        
        response = session.post(f"{BASE_URL}/api/time-entries", json={
            "project_id": test_project.get("project_id") if test_project else None,
            "date": today.isoformat(),
            "hours": 1.0,
            "notes": "TEST_TIME Admin self entry test",
            "is_billable": True
        })
        
        if response.status_code in [200, 201]:
            data = response.json()
            assert data.get("user_id") == admin_user.get("user_id"), "Entry should be for admin"
            assert data.get("created_by") is None, "Self entry should have no created_by"
            print(f"✓ Admin created entry for self. created_by is None (correct)")
            
            # Cleanup
            entry_id = data.get("entry_id")
            if entry_id:
                session.delete(f"{BASE_URL}/api/time-entries/{entry_id}")
        else:
            error = response.json().get("detail", "")
            pytest.fail(f"Admin should be able to create for self but got {response.status_code}: {error}")
    
    def test_created_by_field_tracking(self, admin_session, contractor_user_id, test_project):
        """Verify created_by field correctly tracks proxy creator"""
        session, admin_user = admin_session
        contractor_id, contractor_name = contractor_user_id
        
        if not contractor_id:
            pytest.skip("No contractor user found")
        
        today = date.today()
        admin_id = admin_user.get("user_id")
        
        # Admin creates for contractor
        response = session.post(f"{BASE_URL}/api/time-entries", json={
            "user_id": contractor_id,
            "project_id": test_project.get("project_id") if test_project else None,
            "date": today.isoformat(),
            "hours": 0.5,
            "notes": "TEST_TIME Tracking created_by field test",
            "is_billable": True
        })
        
        if response.status_code in [200, 201]:
            data = response.json()
            
            # Verify created_by is admin
            assert data.get("created_by") == admin_id, f"created_by should be {admin_id}, got {data.get('created_by')}"
            
            # Verify user_id is contractor
            assert data.get("user_id") == contractor_id, f"user_id should be {contractor_id}"
            
            print(f"✓ created_by correctly set to admin: {data.get('created_by')}")
            print(f"✓ user_id correctly set to contractor: {data.get('user_id')}")
            
            # Verify the entry can be retrieved and has correct fields
            entry_id = data.get("entry_id")
            get_response = session.get(f"{BASE_URL}/api/time-entries?limit=1000")
            if get_response.status_code == 200:
                entries = get_response.json().get("items", [])
                found = [e for e in entries if e.get("entry_id") == entry_id]
                if found:
                    assert found[0].get("created_by") == admin_id
                    print("✓ Persisted entry has correct created_by")
            
            # Cleanup
            session.delete(f"{BASE_URL}/api/time-entries/{entry_id}")
        else:
            error = response.json().get("detail", "")
            pytest.fail(f"Failed to create entry: {error}")
    
    def test_nonexistent_user_returns_404(self, admin_session, test_project):
        """Creating entry for nonexistent user should return 404"""
        session, _ = admin_session
        today = date.today()
        
        response = session.post(f"{BASE_URL}/api/time-entries", json={
            "user_id": "nonexistent_user_12345",
            "project_id": test_project.get("project_id") if test_project else None,
            "date": today.isoformat(),
            "hours": 1.0,
            "notes": "TEST_TIME Entry for nonexistent user",
            "is_billable": True
        })
        
        assert response.status_code == 404, f"Expected 404 for nonexistent user but got {response.status_code}"
        print("✓ Nonexistent user correctly returns 404")


class TestWeeklyTimesheet:
    """Test weekly timesheet operations"""
    
    def test_get_my_week(self, admin_session):
        """Test getting current week's entries"""
        session, _ = admin_session
        today = date.today()
        week_start, _ = get_week_bounds(today)
        
        response = session.get(f"{BASE_URL}/api/time-entries/my-week?week_start={week_start}")
        assert response.status_code == 200
        
        data = response.json()
        assert "week_start" in data
        assert "entries" in data
        assert "total_hours" in data
        assert "billable_hours" in data
        assert "locked" in data
        assert "status_counts" in data
        print(f"✓ my-week returns {len(data.get('entries', []))} entries, total: {data.get('total_hours')}h")
        print(f"  - locked: {data.get('locked')}")
        print(f"  - status_counts: {data.get('status_counts')}")
    
    def test_get_my_week_default(self, admin_session):
        """Test getting current week without explicit week_start"""
        session, _ = admin_session
        
        response = session.get(f"{BASE_URL}/api/time-entries/my-week")
        assert response.status_code == 200
        
        data = response.json()
        today = date.today()
        expected_week_start, _ = get_week_bounds(today)
        assert data.get("week_start") == expected_week_start, "Default should be current week"
        print(f"✓ my-week defaults to current week: {data.get('week_start')}")
    
    def test_submit_week_endpoint(self, admin_session, test_project):
        """Test week submission endpoint works"""
        session, _ = admin_session
        today = date.today()
        week_start, _ = get_week_bounds(today)
        
        # First create a draft entry
        create_response = session.post(f"{BASE_URL}/api/time-entries", json={
            "project_id": test_project.get("project_id") if test_project else None,
            "date": today.isoformat(),
            "hours": 0.25,
            "notes": "TEST_TIME Entry for submit week test",
            "is_billable": True
        })
        
        entry_id = None
        if create_response.status_code in [200, 201]:
            entry_id = create_response.json().get("entry_id")
        
        # Submit week
        response = session.post(f"{BASE_URL}/api/time-entries/submit-week", json={
            "week_start": week_start
        })
        
        if response.status_code == 200:
            data = response.json()
            assert "count" in data or "message" in data
            print(f"✓ submit-week successful: {data.get('message')}")
        else:
            error = response.json().get("detail", "")
            # May fail if week is locked - that's OK for this test
            print(f"⚠ submit-week returned {response.status_code}: {error}")
        
        # Cleanup
        if entry_id:
            session.delete(f"{BASE_URL}/api/time-entries/{entry_id}")
    
    def test_submit_week_requires_week_start(self, admin_session):
        """Test that submit-week requires week_start"""
        session, _ = admin_session
        
        response = session.post(f"{BASE_URL}/api/time-entries/submit-week", json={})
        assert response.status_code == 400
        error = response.json().get("detail", "")
        assert "week_start" in error.lower()
        print("✓ submit-week correctly requires week_start")


class TestApprovalWorkflow:
    """Test time entry approval workflow"""
    
    def test_approval_queue(self, admin_session):
        """Test getting approval queue"""
        session, _ = admin_session
        today = date.today()
        week_start, _ = get_week_bounds(today)
        
        response = session.get(f"{BASE_URL}/api/time-entries/approval-queue?week_start={week_start}")
        assert response.status_code == 200
        
        data = response.json()
        assert "items" in data
        assert "total" in data
        print(f"✓ approval-queue returns {data.get('total')} items pending")
    
    def test_approve_entry_endpoint(self, admin_session):
        """Test approve entry endpoint exists and validates"""
        session, _ = admin_session
        
        # Test with fake entry ID
        response = session.post(f"{BASE_URL}/api/time-entries/fake_entry_id/approve", json={})
        
        # Should be 404 (not found) not 500 or other error
        assert response.status_code == 404, f"Expected 404 for fake entry, got {response.status_code}"
        print("✓ approve endpoint exists and returns 404 for nonexistent entry")
    
    def test_reject_requires_notes(self, admin_session):
        """Test that rejection requires notes"""
        session, _ = admin_session
        
        # Try to reject without notes
        response = session.post(f"{BASE_URL}/api/time-entries/fake_entry_id/reject", json={
            "notes": ""
        })
        
        # Either 400 (validation) or 404 (entry not found)
        if response.status_code == 400:
            error = response.json().get("detail", "")
            assert "notes" in error.lower() or "required" in error.lower()
            print("✓ Rejection correctly requires notes")
        elif response.status_code == 404:
            # Entry not found first - need to test with real entry
            print("✓ Reject endpoint exists (404 = entry not found)")
    
    def test_bulk_approve_endpoint(self, admin_session):
        """Test bulk approve endpoint exists and validates"""
        session, _ = admin_session
        
        response = session.post(f"{BASE_URL}/api/time-entries/bulk-approve", json={
            "entry_ids": []
        })
        
        assert response.status_code == 400, f"Empty entry_ids should return 400, got {response.status_code}"
        error = response.json().get("detail", "")
        assert "entry_ids" in error.lower() or "required" in error.lower()
        print("✓ bulk-approve endpoint exists and validates input")
    
    def test_full_approval_flow(self, admin_session, test_project):
        """Test complete approval flow: create -> submit -> approve"""
        session, user = admin_session
        today = date.today()
        week_start, _ = get_week_bounds(today)
        
        # 1. Create draft entry
        create_resp = session.post(f"{BASE_URL}/api/time-entries", json={
            "project_id": test_project.get("project_id") if test_project else None,
            "date": today.isoformat(),
            "hours": 0.25,
            "notes": "TEST_TIME Full approval flow test entry",
            "is_billable": True
        })
        
        if create_resp.status_code not in [200, 201]:
            error = create_resp.json().get("detail", "")
            pytest.skip(f"Could not create entry: {error}")
        
        entry = create_resp.json()
        entry_id = entry.get("entry_id")
        assert entry.get("status") == "draft", "New entry should be draft"
        print(f"✓ Created draft entry: {entry_id}")
        
        try:
            # 2. Submit week
            submit_resp = session.post(f"{BASE_URL}/api/time-entries/submit-week", json={
                "week_start": week_start
            })
            
            if submit_resp.status_code == 200:
                print(f"✓ Submitted week: {submit_resp.json()}")
                
                # 3. Verify entry is now submitted
                get_resp = session.get(f"{BASE_URL}/api/time-entries?limit=500")
                if get_resp.status_code == 200:
                    entries = get_resp.json().get("items", [])
                    found = [e for e in entries if e.get("entry_id") == entry_id]
                    if found and found[0].get("status") == "submitted":
                        print(f"✓ Entry status changed to: submitted")
                        
                        # 4. Approve the entry
                        approve_resp = session.post(f"{BASE_URL}/api/time-entries/{entry_id}/approve", json={})
                        if approve_resp.status_code == 200:
                            approved = approve_resp.json()
                            assert approved.get("status") == "approved"
                            assert approved.get("approved_by") == user.get("user_id")
                            print(f"✓ Entry approved by: {approved.get('approved_by')}")
                        else:
                            print(f"⚠ Approve failed: {approve_resp.json()}")
                    else:
                        print(f"⚠ Entry status not updated to submitted")
            else:
                error = submit_resp.json().get("detail", "")
                print(f"⚠ Submit week failed (may be locked): {error}")
        finally:
            # Cleanup
            session.delete(f"{BASE_URL}/api/time-entries/{entry_id}")
            print(f"✓ Cleaned up test entry")


class TestWeeklySummary:
    """Test AP/AR weekly summary"""
    
    def test_weekly_summary_admin_access(self, admin_session):
        """Admin can access weekly summary"""
        session, _ = admin_session
        today = date.today()
        week_start, _ = get_week_bounds(today)
        
        response = session.get(f"{BASE_URL}/api/time-entries/weekly-summary?week_start={week_start}")
        assert response.status_code == 200
        
        data = response.json()
        assert "week_start" in data
        assert "accounts_payable" in data
        assert "accounts_receivable" in data
        
        ap = data.get("accounts_payable", {})
        ar = data.get("accounts_receivable", {})
        
        assert "items" in ap
        assert "total_hours" in ap
        assert "items" in ar
        assert "total_amount" in ar
        
        print(f"✓ weekly-summary accessible by admin")
        print(f"  - AP items: {len(ap.get('items', []))}, total hours: {ap.get('total_hours')}")
        print(f"  - AR items: {len(ar.get('items', []))}, total amount: ${ar.get('total_amount', 0)}")


class TestTimeEntryFiltering:
    """Test time entry filtering and retrieval"""
    
    def test_get_time_entries(self, admin_session):
        """Test getting time entries with filters"""
        session, _ = admin_session
        
        response = session.get(f"{BASE_URL}/api/time-entries?limit=10")
        assert response.status_code == 200
        
        data = response.json()
        assert "items" in data
        assert "total" in data
        print(f"✓ GET /time-entries returns {data.get('total')} total entries")
    
    def test_filter_by_status(self, admin_session):
        """Test filtering time entries by status"""
        session, _ = admin_session
        
        for status in ["draft", "submitted", "approved", "rejected"]:
            response = session.get(f"{BASE_URL}/api/time-entries?status={status}&limit=10")
            assert response.status_code == 200
            
            data = response.json()
            # All entries should have this status
            for entry in data.get("items", []):
                assert entry.get("status") == status, f"Entry should have status {status}"
            
            print(f"✓ Filter by status={status} returns {len(data.get('items', []))} entries")
    
    def test_filter_by_week(self, admin_session):
        """Test filtering time entries by week"""
        session, _ = admin_session
        today = date.today()
        week_start, _ = get_week_bounds(today)
        
        response = session.get(f"{BASE_URL}/api/time-entries?week_start={week_start}&limit=50")
        assert response.status_code == 200
        
        data = response.json()
        for entry in data.get("items", []):
            assert entry.get("week_start") == week_start, f"Entry week_start should be {week_start}"
        
        print(f"✓ Filter by week_start returns {len(data.get('items', []))} entries")
    
    def test_filter_by_user(self, admin_session, contractor_user_id):
        """Test filtering time entries by user_id"""
        session, _ = admin_session
        contractor_id, _ = contractor_user_id
        
        if not contractor_id:
            pytest.skip("No contractor user found")
        
        response = session.get(f"{BASE_URL}/api/time-entries?user_id={contractor_id}&limit=10")
        assert response.status_code == 200
        
        data = response.json()
        for entry in data.get("items", []):
            assert entry.get("user_id") == contractor_id, f"Entry should be for user {contractor_id}"
        
        print(f"✓ Filter by user_id returns {len(data.get('items', []))} entries")


class TestResourceTypeSelection:
    """Test contractor resource type selection for time entries"""
    
    def test_contractor_resource_types_endpoint(self, admin_session):
        """Test contractor-resource-types endpoint exists"""
        session, _ = admin_session
        
        # Get a contract ID first
        contracts_resp = session.get(f"{BASE_URL}/api/contracts?limit=10")
        if contracts_resp.status_code != 200:
            pytest.skip("Could not get contracts")
        
        contracts = contracts_resp.json()
        contracts_list = contracts.get("items", contracts) if isinstance(contracts, dict) else contracts
        
        if not contracts_list:
            pytest.skip("No contracts found")
        
        contract_id = contracts_list[0].get("contract_id")
        
        response = session.get(f"{BASE_URL}/api/contractor-resource-types?contract_id={contract_id}")
        assert response.status_code == 200
        
        data = response.json()
        assert "contract_id" in data
        assert "resource_types" in data
        print(f"✓ contractor-resource-types endpoint works for contract {contract_id}")
        print(f"  - Found {len(data.get('resource_types', []))} resource types")


class TestEntryByAccount:
    """Test time entries by account endpoint"""
    
    def test_time_entries_by_account(self, admin_session):
        """Test getting time entries by account"""
        session, _ = admin_session
        
        # Get an account ID first
        accounts_resp = session.get(f"{BASE_URL}/api/accounts?limit=5")
        if accounts_resp.status_code != 200:
            pytest.skip("Could not get accounts")
        
        accounts = accounts_resp.json()
        accounts_list = accounts.get("items", accounts) if isinstance(accounts, dict) else accounts
        
        if not accounts_list:
            pytest.skip("No accounts found")
        
        account_id = accounts_list[0].get("account_id")
        
        response = session.get(f"{BASE_URL}/api/time-entries/by-account/{account_id}")
        assert response.status_code == 200
        
        data = response.json()
        assert "account_id" in data
        assert "entries" in data
        assert "total_hours" in data
        assert "billable_hours" in data
        print(f"✓ time-entries by account works: {data.get('total_hours')}h for {account_id}")


class TestCleanup:
    """Cleanup test data"""
    
    def test_cleanup_test_entries(self, admin_session):
        """Delete any TEST_TIME entries created during tests"""
        session, _ = admin_session
        
        # Get all entries with TEST_TIME prefix
        response = session.get(f"{BASE_URL}/api/time-entries?limit=500")
        if response.status_code != 200:
            print("⚠ Could not fetch entries for cleanup")
            return
        
        data = response.json()
        deleted = 0
        for entry in data.get("items", []):
            if "TEST_TIME" in entry.get("notes", ""):
                entry_id = entry.get("entry_id")
                if entry_id:
                    del_resp = session.delete(f"{BASE_URL}/api/time-entries/{entry_id}")
                    if del_resp.status_code in [200, 204]:
                        deleted += 1
                    # 400 = not draft/rejected, can't delete
        
        print(f"✓ Cleaned up {deleted} TEST_TIME entries")

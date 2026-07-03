"""Backend tests for AEGISAXIOM - auth, deals, accounts, commissions, admin endpoints"""
import pytest
import requests
import os

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")

ADMIN_EMAIL = "admin@simplimeta.io"
ADMIN_PASSWORD = "Admin@123"


@pytest.fixture(scope="session")
def token():
    resp = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    return resp.json()["token"]


@pytest.fixture(scope="session")
def auth(token):
    return {"Authorization": f"Bearer {token}"}


# Health
class TestHealth:
    def test_health(self):
        r = requests.get(f"{BASE_URL}/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# Auth
class TestAuth:
    def test_login_success(self):
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        d = r.json()
        assert "token" in d
        assert d["user"]["role"] == "admin"
        assert d["user"]["email"] == ADMIN_EMAIL

    def test_login_bad_creds(self):
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": "wrongpass"})
        assert r.status_code in [400, 401, 403]

    def test_me(self, auth):
        r = requests.get(f"{BASE_URL}/api/auth/me", headers=auth)
        assert r.status_code == 200
        assert r.json()["role"] == "admin"

    def test_setup_admin_duplicate(self):
        r = requests.post(f"{BASE_URL}/api/auth/setup-admin", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "name": "Admin"})
        # Already exists should return 400
        assert r.status_code in [400, 409]


# Admin
class TestAdmin:
    def test_stats(self, auth):
        r = requests.get(f"{BASE_URL}/api/admin/stats", headers=auth)
        assert r.status_code == 200
        d = r.json()
        assert "total_accounts" in d
        assert "total_deals" in d

    def test_users_list(self, auth):
        r = requests.get(f"{BASE_URL}/api/admin/users", headers=auth)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_invite_user(self, auth):
        import time
        unique_email = f"TEST_invite_{int(time.time())}@simplimeta.io"
        r = requests.post(f"{BASE_URL}/api/admin/invite", headers=auth, json={
            "email": unique_email, "name": "Test Invite", "role": "sales_rep"
        })
        assert r.status_code in [200, 201]

    def test_seed_idempotent(self, auth):
        r = requests.post(f"{BASE_URL}/api/admin/seed", headers=auth)
        assert r.status_code == 200


# Deals
class TestDeals:
    def test_list_deals(self, auth):
        r = requests.get(f"{BASE_URL}/api/deals", headers=auth)
        assert r.status_code == 200
        d = r.json()
        assert isinstance(d, list)
        assert len(d) > 0

    def test_deal_fields(self, auth):
        r = requests.get(f"{BASE_URL}/api/deals", headers=auth)
        deal = r.json()[0]
        assert "deal_id" in deal
        assert "name" in deal
        assert "stage" in deal
        assert "amount" in deal

    def test_create_deal(self, auth):
        # Get an account_id first
        accounts = requests.get(f"{BASE_URL}/api/accounts", headers=auth).json()
        account_id = accounts[0]["account_id"] if accounts else "acc_demo001"
        r = requests.post(f"{BASE_URL}/api/deals", headers=auth, json={
            "name": "TEST_Deal Alpha", "account_id": account_id, "stage": "Qualification",
            "amount": 9999, "close_date": "2026-06-30"
        })
        assert r.status_code in [200, 201]
        d = r.json()
        assert d["name"] == "TEST_Deal Alpha"
        return d.get("deal_id")


# Accounts
class TestAccounts:
    def test_list_accounts(self, auth):
        r = requests.get(f"{BASE_URL}/api/accounts", headers=auth)
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) > 0

    def test_create_account(self, auth):
        r = requests.post(f"{BASE_URL}/api/accounts", headers=auth, json={
            "name": "TEST_Account Beta", "industry": "Tech", "website": "https://test.com"
        })
        assert r.status_code in [200, 201]
        assert r.json()["name"] == "TEST_Account Beta"


# Financials - Commissions
class TestCommissions:
    def test_list_commissions(self, auth):
        r = requests.get(f"{BASE_URL}/api/commissions", headers=auth)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_list_invoices(self, auth):
        r = requests.get(f"{BASE_URL}/api/invoices", headers=auth)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_list_contracts(self, auth):
        r = requests.get(f"{BASE_URL}/api/contracts", headers=auth)
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# Operations - Field Jobs
class TestOperations:
    def test_list_field_jobs(self, auth):
        r = requests.get(f"{BASE_URL}/api/jobs", headers=auth)
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# Partners
class TestPartners:
    def test_register_partner(self):
        import time
        r = requests.post(f"{BASE_URL}/api/partners/apply", json={
            "company_name": "TEST_Partner Co", "contact_name": "TEST Contact",
            "email": f"TEST_partner_{int(time.time())}@test.com", "phone": "555-1234"
        })
        assert r.status_code in [200, 201]

    def test_list_applications(self, auth):
        r = requests.get(f"{BASE_URL}/api/admin/partner-applications", headers=auth)
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# Chat
class TestChat:
    def test_create_session(self, auth):
        r = requests.post(f"{BASE_URL}/api/chat/sessions", headers=auth, json={"title": "TEST_Chat"})
        assert r.status_code in [200, 201]
        return r.json().get("session_id")

    def test_list_sessions(self, auth):
        r = requests.get(f"{BASE_URL}/api/chat/sessions", headers=auth)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

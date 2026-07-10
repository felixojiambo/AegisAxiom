"""
MEB4-T37 — Distributor CRUD and Product-Distributor association tests.

Follows the same HTTP-integration style as test_sales_ops_procurement.py:
requires REACT_APP_BACKEND_URL to point at a running backend. Skips if
the admin login fails.
"""
import os
import pytest
import requests
from uuid import uuid4

from conftest import TEST_EMAIL, TEST_PASSWORD

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")


@pytest.fixture(scope="module")
def auth_token():
    if not BASE_URL:
        pytest.skip("REACT_APP_BACKEND_URL not set")
    response = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
    )
    if response.status_code != 200:
        pytest.skip(f"Authentication failed: {response.status_code} - {response.text}")
    return response.json().get("token")


@pytest.fixture(scope="module")
def api_client(auth_token):
    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_token}",
    })
    return session


@pytest.fixture(scope="module")
def created_distributor(api_client):
    """Create a throw-away distributor for tests and clean it up at the end."""
    unique = uuid4().hex[:8]
    payload = {
        "name": f"TEST_Distributor_{unique}",
        "contact_name": "Test Contact",
        "contact_email": f"contact_{unique}@test.io",
        "contact_phone": "555-0000",
        "website": "https://example.test",
        "notes": "Created by test_distributors.py — safe to ignore.",
        "status": "active",
    }
    res = api_client.post(f"{BASE_URL}/api/admin/distributors", json=payload)
    assert res.status_code == 200, f"Create failed: {res.text}"
    distributor = res.json()["distributor"]
    yield distributor
    # Soft-delete at the end
    api_client.delete(f"{BASE_URL}/api/admin/distributors/{distributor['distributor_id']}")


class TestDistributorCRUD:
    def test_list_distributors(self, api_client, created_distributor):
        res = api_client.get(f"{BASE_URL}/api/admin/distributors")
        assert res.status_code == 200, res.text
        data = res.json()
        assert "items" in data
        ids = [d["distributor_id"] for d in data["items"]]
        assert created_distributor["distributor_id"] in ids

    def test_get_distributor(self, api_client, created_distributor):
        did = created_distributor["distributor_id"]
        res = api_client.get(f"{BASE_URL}/api/admin/distributors/{did}")
        assert res.status_code == 200, res.text
        assert res.json()["distributor_id"] == did

    def test_get_distributor_not_found(self, api_client):
        res = api_client.get(f"{BASE_URL}/api/admin/distributors/does_not_exist")
        assert res.status_code == 404

    def test_duplicate_name_rejected(self, api_client, created_distributor):
        res = api_client.post(
            f"{BASE_URL}/api/admin/distributors",
            json={"name": created_distributor["name"]},
        )
        assert res.status_code == 409

    def test_update_distributor(self, api_client, created_distributor):
        did = created_distributor["distributor_id"]
        res = api_client.put(
            f"{BASE_URL}/api/admin/distributors/{did}",
            json={"contact_name": "Updated Contact"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["distributor"]["contact_name"] == "Updated Contact"

    def test_invalid_status_rejected(self, api_client, created_distributor):
        did = created_distributor["distributor_id"]
        res = api_client.put(
            f"{BASE_URL}/api/admin/distributors/{did}",
            json={"status": "bogus"},
        )
        assert res.status_code == 400

    def test_stats_endpoint(self, api_client, created_distributor):
        did = created_distributor["distributor_id"]
        res = api_client.get(f"{BASE_URL}/api/admin/distributors/{did}/stats")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["distributor_id"] == did
        assert "linked_product_count" in body
        assert "open_request_count" in body
        assert body["open_request_count"] == 0  # MEB4-T39 populates this


class TestProductDistributorAssociation:
    def test_product_accepts_distributor_id(self, api_client, created_distributor):
        sku = f"DIST-TEST-{uuid4().hex[:8]}"
        res = api_client.post(
            f"{BASE_URL}/api/admin/products",
            json={
                "name": f"Test Hardware {sku}",
                "description": "Test product with distributor",
                "sku": sku,
                "product_type": "networking",
                "item_category": "hardware",
                "retail_pricing": {"daily": 0, "weekly": 0, "monthly": 0, "quarterly": 0, "annual": 0, "one_time": 100.0},
                "msp_pricing":   {"daily": 0, "weekly": 0, "monthly": 0, "quarterly": 0, "annual": 0, "one_time": 80.0},
                "cost_monthly": 0,
                "cost_one_time": 50.0,
                "distributor_id": created_distributor["distributor_id"],
                "distributor_sku": "DIST-SKU-001",
                "is_active": True,
            },
        )
        assert res.status_code == 200, res.text
        product_id = res.json()["product_id"]

        # Round-trip: GET should return the distributor fields.
        get_res = api_client.get(f"{BASE_URL}/api/admin/products/{product_id}")
        assert get_res.status_code == 200
        doc = get_res.json()
        assert doc["distributor_id"] == created_distributor["distributor_id"]
        assert doc["distributor_sku"] == "DIST-SKU-001"

        # Clean up
        api_client.delete(f"{BASE_URL}/api/admin/products/{product_id}")

    def test_product_rejects_bogus_distributor_id(self, api_client):
        sku = f"DIST-BAD-{uuid4().hex[:8]}"
        res = api_client.post(
            f"{BASE_URL}/api/admin/products",
            json={
                "name": f"Bad Distributor Ref {sku}",
                "description": "",
                "sku": sku,
                "product_type": "networking",
                "item_category": "hardware",
                "retail_pricing": {"daily": 0, "weekly": 0, "monthly": 0, "quarterly": 0, "annual": 0, "one_time": 0},
                "msp_pricing":   {"daily": 0, "weekly": 0, "monthly": 0, "quarterly": 0, "annual": 0, "one_time": 0},
                "cost_monthly": 0,
                "cost_one_time": 10,
                "distributor_id": "dist_does_not_exist_xyz",
                "is_active": True,
            },
        )
        assert res.status_code == 400, res.text

    def test_stats_linked_product_count_increments(self, api_client, created_distributor):
        did = created_distributor["distributor_id"]
        before = api_client.get(f"{BASE_URL}/api/admin/distributors/{did}/stats").json()["linked_product_count"]

        sku = f"DIST-CNT-{uuid4().hex[:8]}"
        create_res = api_client.post(
            f"{BASE_URL}/api/admin/products",
            json={
                "name": f"Count Test {sku}",
                "description": "",
                "sku": sku,
                "product_type": "networking",
                "item_category": "hardware",
                "retail_pricing": {"daily": 0, "weekly": 0, "monthly": 0, "quarterly": 0, "annual": 0, "one_time": 0},
                "msp_pricing":   {"daily": 0, "weekly": 0, "monthly": 0, "quarterly": 0, "annual": 0, "one_time": 0},
                "cost_monthly": 0,
                "cost_one_time": 5,
                "distributor_id": did,
                "is_active": True,
            },
        )
        assert create_res.status_code == 200
        product_id = create_res.json()["product_id"]

        after = api_client.get(f"{BASE_URL}/api/admin/distributors/{did}/stats").json()["linked_product_count"]
        assert after == before + 1

        api_client.delete(f"{BASE_URL}/api/admin/products/{product_id}")


class TestDistributorPagination:
    """Offset/limit pagination on GET /admin/distributors."""

    def test_default_pagination_shape(self, api_client, created_distributor):
        res = api_client.get(f"{BASE_URL}/api/admin/distributors")
        assert res.status_code == 200, res.text
        body = res.json()
        assert "items" in body
        assert "total" in body
        assert body["limit"] == 50
        assert body["offset"] == 0
        assert len(body["items"]) <= 50
        assert body["total"] >= len(body["items"])

    def test_explicit_limit(self, api_client, created_distributor):
        res = api_client.get(f"{BASE_URL}/api/admin/distributors?limit=1")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["limit"] == 1
        assert len(body["items"]) <= 1

    def test_offset_skips_items(self, api_client, created_distributor):
        first = api_client.get(f"{BASE_URL}/api/admin/distributors?limit=2&offset=0").json()
        second = api_client.get(f"{BASE_URL}/api/admin/distributors?limit=2&offset=1").json()
        assert second["offset"] == 1
        # If there are >=2 items, item[0] at offset=1 should match item[1] at offset=0.
        if len(first["items"]) >= 2 and second["items"]:
            assert second["items"][0]["distributor_id"] == first["items"][1]["distributor_id"]

    def test_invalid_limit_returns_400(self, api_client):
        for bad in (0, 201, -1):
            res = api_client.get(f"{BASE_URL}/api/admin/distributors?limit={bad}")
            assert res.status_code == 400, f"limit={bad}: {res.status_code} {res.text}"

    def test_invalid_offset_returns_400(self, api_client):
        res = api_client.get(f"{BASE_URL}/api/admin/distributors?offset=-1")
        assert res.status_code == 400, res.text


class TestDistributorPermissions:
    """Permission gating — relies on an unauthenticated request returning 401/403.

    We can't easily test per-permission denial without a dedicated fixture for a
    non-admin user; those would require a test user with only distributors.view.
    Skipping that finer-grained coverage — matching the existing repo convention
    (no such tests exist for products.py either)."""

    def test_unauthenticated_request_rejected(self):
        if not BASE_URL:
            pytest.skip("REACT_APP_BACKEND_URL not set")
        res = requests.get(f"{BASE_URL}/api/admin/distributors")
        assert res.status_code in (401, 403)

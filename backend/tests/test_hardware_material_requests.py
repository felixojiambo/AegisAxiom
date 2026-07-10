"""
MEB4-T39 — HardwareMaterialRequest CRUD, filter, status transitions, and event tests.

HTTP-integration style matching test_sales_ops_procurement.py.
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
    res = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
    )
    if res.status_code != 200:
        pytest.skip(f"Auth failed: {res.status_code} - {res.text}")
    return res.json().get("token")


@pytest.fixture(scope="module")
def api_client(auth_token):
    s = requests.Session()
    s.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_token}",
    })
    return s


@pytest.fixture(scope="module")
def test_distributor(api_client):
    unique = uuid4().hex[:8]
    res = api_client.post(
        f"{BASE_URL}/api/admin/distributors",
        json={"name": f"TEST_HMR_Dist_{unique}", "status": "active"},
    )
    assert res.status_code == 200, res.text
    distributor = res.json()["distributor"]
    yield distributor
    # Soft-delete at the end — matches test_distributors.py cleanup pattern.
    api_client.delete(f"{BASE_URL}/api/admin/distributors/{distributor['distributor_id']}")


@pytest.fixture(scope="module")
def test_opportunity(api_client):
    """Fetch the first available opportunity — HMR requires a real one."""
    res = api_client.get(f"{BASE_URL}/api/opportunities?limit=1")
    if res.status_code != 200:
        pytest.skip(f"Could not list opportunities: {res.text}")
    body = res.json()
    items = body.get("items") or body.get("opportunities") or []
    if not items:
        pytest.skip("No opportunities available to attach a request to")
    return items[0]


class TestRequestCRUD:
    def test_create_with_item_description(self, api_client, test_opportunity, test_distributor):
        payload = {
            "opportunity_id": test_opportunity["opportunity_id"],
            "distributor_id": test_distributor["distributor_id"],
            "item_description": "Cat6 patch cables",
            "quantity": 5,
            "priority": "high",
            "notes": "For the test rack",
        }
        res = api_client.post(f"{BASE_URL}/api/sales-ops/hardware-material-requests", json=payload)
        assert res.status_code == 200, res.text
        body = res.json()["request"]
        assert body["status"] == "open"
        assert body["quantity"] == 5
        assert body["priority"] == "high"
        assert body["distributor_name"] == test_distributor["name"]

        # Clean up
        api_client.delete(f"{BASE_URL}/api/sales-ops/hardware-material-requests/{body['request_id']}")

    def test_missing_product_and_description_rejected(self, api_client, test_opportunity):
        res = api_client.post(
            f"{BASE_URL}/api/sales-ops/hardware-material-requests",
            json={
                "opportunity_id": test_opportunity["opportunity_id"],
                "quantity": 1,
            },
        )
        assert res.status_code == 400

    def test_bogus_opportunity_rejected(self, api_client):
        res = api_client.post(
            f"{BASE_URL}/api/sales-ops/hardware-material-requests",
            json={
                "opportunity_id": "opp_does_not_exist_xyz",
                "item_description": "Anything",
                "quantity": 1,
            },
        )
        assert res.status_code == 404

    def test_bogus_distributor_rejected(self, api_client, test_opportunity):
        res = api_client.post(
            f"{BASE_URL}/api/sales-ops/hardware-material-requests",
            json={
                "opportunity_id": test_opportunity["opportunity_id"],
                "distributor_id": "dist_does_not_exist_xyz",
                "item_description": "X",
                "quantity": 1,
            },
        )
        assert res.status_code == 404

    def test_invalid_quantity_rejected(self, api_client, test_opportunity):
        res = api_client.post(
            f"{BASE_URL}/api/sales-ops/hardware-material-requests",
            json={
                "opportunity_id": test_opportunity["opportunity_id"],
                "item_description": "X",
                "quantity": 0,
            },
        )
        assert res.status_code == 400


class TestStatusTransitions:
    @pytest.fixture
    def open_request(self, api_client, test_opportunity):
        res = api_client.post(
            f"{BASE_URL}/api/sales-ops/hardware-material-requests",
            json={
                "opportunity_id": test_opportunity["opportunity_id"],
                "item_description": f"Status test {uuid4().hex[:6]}",
                "quantity": 1,
            },
        )
        assert res.status_code == 200, res.text
        req = res.json()["request"]
        yield req
        # Hard-ish cleanup
        api_client.delete(f"{BASE_URL}/api/sales-ops/hardware-material-requests/{req['request_id']}")

    def test_happy_path_transitions(self, api_client, open_request):
        rid = open_request["request_id"]
        for new_status in ["sourcing", "ordered", "received"]:
            res = api_client.patch(
                f"{BASE_URL}/api/sales-ops/hardware-material-requests/{rid}",
                json={"status": new_status},
            )
            assert res.status_code == 200, f"{new_status}: {res.text}"
            assert res.json()["request"]["status"] == new_status

    def test_cannot_leave_terminal_status(self, api_client, open_request):
        rid = open_request["request_id"]
        # Move to received (terminal)
        api_client.patch(
            f"{BASE_URL}/api/sales-ops/hardware-material-requests/{rid}",
            json={"status": "received"},
        )
        # Try to bounce back to open
        res = api_client.patch(
            f"{BASE_URL}/api/sales-ops/hardware-material-requests/{rid}",
            json={"status": "open"},
        )
        assert res.status_code == 400

    def test_cancel_from_open(self, api_client, open_request):
        rid = open_request["request_id"]
        res = api_client.patch(
            f"{BASE_URL}/api/sales-ops/hardware-material-requests/{rid}",
            json={"status": "cancelled"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["request"]["status"] == "cancelled"


class TestListFilters:
    def test_filter_by_opportunity(self, api_client, test_opportunity, test_distributor):
        opp_id = test_opportunity["opportunity_id"]
        # Create one
        create_res = api_client.post(
            f"{BASE_URL}/api/sales-ops/hardware-material-requests",
            json={
                "opportunity_id": opp_id,
                "distributor_id": test_distributor["distributor_id"],
                "item_description": f"Filter test {uuid4().hex[:6]}",
                "quantity": 1,
            },
        )
        rid = create_res.json()["request"]["request_id"]

        try:
            res = api_client.get(
                f"{BASE_URL}/api/sales-ops/hardware-material-requests?opportunity_id={opp_id}",
            )
            assert res.status_code == 200, res.text
            items = res.json()["items"]
            assert all(r["opportunity_id"] == opp_id for r in items)
            assert any(r["request_id"] == rid for r in items)
        finally:
            api_client.delete(f"{BASE_URL}/api/sales-ops/hardware-material-requests/{rid}")

    def test_filter_by_status(self, api_client, test_opportunity):
        create_res = api_client.post(
            f"{BASE_URL}/api/sales-ops/hardware-material-requests",
            json={
                "opportunity_id": test_opportunity["opportunity_id"],
                "item_description": f"Status filter {uuid4().hex[:6]}",
                "quantity": 1,
            },
        )
        rid = create_res.json()["request"]["request_id"]
        try:
            res = api_client.get(f"{BASE_URL}/api/sales-ops/hardware-material-requests?status=open")
            assert res.status_code == 200
            items = res.json()["items"]
            assert all(r["status"] == "open" for r in items)
        finally:
            api_client.delete(f"{BASE_URL}/api/sales-ops/hardware-material-requests/{rid}")


class TestDistributorStatsOpenRequestCount:
    """T38 shipped with open_request_count always-zero. T39 populates it.
    This test is the contract boundary."""

    def test_stats_counts_open_and_sourcing(self, api_client, test_opportunity, test_distributor):
        did = test_distributor["distributor_id"]
        before = api_client.get(f"{BASE_URL}/api/admin/distributors/{did}/stats").json()["open_request_count"]

        # Create two open requests
        r1 = api_client.post(
            f"{BASE_URL}/api/sales-ops/hardware-material-requests",
            json={
                "opportunity_id": test_opportunity["opportunity_id"],
                "distributor_id": did,
                "item_description": f"Count a {uuid4().hex[:6]}",
                "quantity": 1,
            },
        )
        r2 = api_client.post(
            f"{BASE_URL}/api/sales-ops/hardware-material-requests",
            json={
                "opportunity_id": test_opportunity["opportunity_id"],
                "distributor_id": did,
                "item_description": f"Count b {uuid4().hex[:6]}",
                "quantity": 1,
            },
        )
        rid1 = r1.json()["request"]["request_id"]
        rid2 = r2.json()["request"]["request_id"]

        try:
            after = api_client.get(f"{BASE_URL}/api/admin/distributors/{did}/stats").json()["open_request_count"]
            assert after == before + 2

            # Transition one to received — should drop count by 1
            api_client.patch(
                f"{BASE_URL}/api/sales-ops/hardware-material-requests/{rid1}",
                json={"status": "received"},
            )
            after_one_received = api_client.get(
                f"{BASE_URL}/api/admin/distributors/{did}/stats",
            ).json()["open_request_count"]
            assert after_one_received == after - 1
        finally:
            api_client.delete(f"{BASE_URL}/api/sales-ops/hardware-material-requests/{rid1}")
            api_client.delete(f"{BASE_URL}/api/sales-ops/hardware-material-requests/{rid2}")


class TestRequestPagination:
    """Offset/limit pagination on GET /sales-ops/hardware-material-requests."""

    def test_default_pagination_shape(self, api_client):
        res = api_client.get(f"{BASE_URL}/api/sales-ops/hardware-material-requests")
        assert res.status_code == 200, res.text
        body = res.json()
        assert "items" in body
        assert "total" in body
        assert body["limit"] == 50
        assert body["offset"] == 0
        assert len(body["items"]) <= 50
        assert body["total"] >= len(body["items"])

    def test_explicit_limit(self, api_client, test_opportunity):
        create_res = api_client.post(
            f"{BASE_URL}/api/sales-ops/hardware-material-requests",
            json={
                "opportunity_id": test_opportunity["opportunity_id"],
                "item_description": f"Pagination limit {uuid4().hex[:6]}",
                "quantity": 1,
            },
        )
        rid = create_res.json()["request"]["request_id"]
        try:
            res = api_client.get(f"{BASE_URL}/api/sales-ops/hardware-material-requests?limit=1")
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["limit"] == 1
            assert len(body["items"]) <= 1
        finally:
            api_client.delete(f"{BASE_URL}/api/sales-ops/hardware-material-requests/{rid}")

    def test_offset_skips_items(self, api_client, test_opportunity):
        ids = []
        for _ in range(2):
            create_res = api_client.post(
                f"{BASE_URL}/api/sales-ops/hardware-material-requests",
                json={
                    "opportunity_id": test_opportunity["opportunity_id"],
                    "item_description": f"Pagination offset {uuid4().hex[:6]}",
                    "quantity": 1,
                },
            )
            ids.append(create_res.json()["request"]["request_id"])
        try:
            first = api_client.get(
                f"{BASE_URL}/api/sales-ops/hardware-material-requests?limit=2&offset=0",
            ).json()
            second = api_client.get(
                f"{BASE_URL}/api/sales-ops/hardware-material-requests?limit=2&offset=1",
            ).json()
            assert second["offset"] == 1
            if len(first["items"]) >= 2 and second["items"]:
                assert second["items"][0]["request_id"] == first["items"][1]["request_id"]
        finally:
            for rid in ids:
                api_client.delete(f"{BASE_URL}/api/sales-ops/hardware-material-requests/{rid}")

    def test_invalid_limit_returns_400(self, api_client):
        for bad in (0, 201, -1):
            res = api_client.get(
                f"{BASE_URL}/api/sales-ops/hardware-material-requests?limit={bad}",
            )
            assert res.status_code == 400, f"limit={bad}: {res.status_code} {res.text}"

    def test_invalid_offset_returns_400(self, api_client):
        res = api_client.get(f"{BASE_URL}/api/sales-ops/hardware-material-requests?offset=-1")
        assert res.status_code == 400, res.text


class TestSoftDelete:
    def test_deleted_hidden_from_list(self, api_client, test_opportunity):
        create_res = api_client.post(
            f"{BASE_URL}/api/sales-ops/hardware-material-requests",
            json={
                "opportunity_id": test_opportunity["opportunity_id"],
                "item_description": f"Soft del {uuid4().hex[:6]}",
                "quantity": 1,
            },
        )
        rid = create_res.json()["request"]["request_id"]

        del_res = api_client.delete(f"{BASE_URL}/api/sales-ops/hardware-material-requests/{rid}")
        assert del_res.status_code == 200

        list_res = api_client.get(
            f"{BASE_URL}/api/sales-ops/hardware-material-requests?opportunity_id={test_opportunity['opportunity_id']}",
        )
        ids = [r["request_id"] for r in list_res.json()["items"]]
        assert rid not in ids

        # Detail GET returns 404 (deleted is filtered out)
        detail_res = api_client.get(f"{BASE_URL}/api/sales-ops/hardware-material-requests/{rid}")
        assert detail_res.status_code == 404

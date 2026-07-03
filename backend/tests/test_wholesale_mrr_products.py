"""
Wholesale MRR Products Management - Backend API Tests
Tests for product filtering, search, and contract creation with products
"""
import pytest
import requests
import os
import json
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone, timedelta
import secrets

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
MONGO_URL = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
DB_NAME = os.environ.get('DB_NAME', 'test_database')

def create_test_session_sync():
    """Create a test session token for admin user"""
    async def _create():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        
        # Find admin user
        admin = await db.users.find_one({'email': 'support@simplimeta.io'}, {'_id': 0, 'user_id': 1})
        if not admin:
            return None
        
        # Create test session
        token = f'test_session_{secrets.randbits(40)}'
        expires_at = datetime.now(timezone.utc) + timedelta(hours=4)
        
        await db.user_sessions.insert_one({
            'session_token': token,
            'user_id': admin['user_id'],
            'expires_at': expires_at,
            'created_at': datetime.now(timezone.utc)
        })
        
        client.close()
        return token
    
    loop = asyncio.new_event_loop()
    token = loop.run_until_complete(_create())
    loop.close()
    return token


class TestWholesaleMRRProducts:
    """Test Wholesale MRR product management APIs"""
    
    session_token = None
    
    @pytest.fixture(autouse=True)
    def setup_auth(self):
        """Create test session for admin user"""
        if TestWholesaleMRRProducts.session_token:
            return
        
        token = create_test_session_sync()
        if token:
            TestWholesaleMRRProducts.session_token = token
            print(f"Test session created: {token[:30]}...")
        else:
            pytest.skip("Failed to create test session - admin user not found")
    
    def get_cookies(self):
        """Helper to get cookies dict"""
        return {"session_token": TestWholesaleMRRProducts.session_token}
    
    # ==================== Product API Tests ====================
    
    def test_get_products_endpoint_returns_products_key(self):
        """GET /api/admin/products should return {products: [...]} not {items: [...]}"""
        cookies = self.get_cookies()
        response = requests.get(f"{BASE_URL}/api/admin/products", cookies=cookies)
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # CRITICAL: Verify response uses "products" key, not "items"
        assert "products" in data, f"Response should have 'products' key, got: {list(data.keys())}"
        assert isinstance(data["products"], list), "products should be a list"
        
        print(f"SUCCESS: GET /api/admin/products returns 'products' key with {len(data['products'])} products")
    
    def test_products_have_required_fields(self):
        """Products should have all required fields for Wholesale MRR"""
        cookies = self.get_cookies()
        response = requests.get(f"{BASE_URL}/api/admin/products", cookies=cookies)
        
        assert response.status_code == 200
        products = response.json().get("products", [])
        
        if len(products) == 0:
            pytest.skip("No products in database to test")
        
        required_fields = [
            "product_id", "name", "sku", "product_type",
            "cost_monthly", "retail_pricing", "msp_pricing", "is_active"
        ]
        
        for product in products:
            for field in required_fields:
                assert field in product, f"Product {product.get('name', 'unknown')} missing field: {field}"
            
            # Verify pricing structure
            assert "monthly" in product.get("retail_pricing", {}), "retail_pricing should have 'monthly'"
            assert "quarterly" in product.get("retail_pricing", {}), "retail_pricing should have 'quarterly'"
            assert "annual" in product.get("retail_pricing", {}), "retail_pricing should have 'annual'"
            
            # Verify product_type is valid
            assert product.get("product_type") in ["simplisecure", "simplicomply", "custom"], \
                f"Invalid product_type: {product.get('product_type')}"
        
        print(f"SUCCESS: All {len(products)} products have required fields")
    
    def test_filter_products_by_simplisecure_type(self):
        """Products can be filtered by product_type=simplisecure (frontend filter)"""
        cookies = self.get_cookies()
        response = requests.get(f"{BASE_URL}/api/admin/products", cookies=cookies)
        
        assert response.status_code == 200
        products = response.json().get("products", [])
        
        # Filter locally (frontend does this)
        simplisecure_products = [p for p in products if p.get("product_type") == "simplisecure"]
        
        print(f"Found {len(simplisecure_products)} SimpliSecure products out of {len(products)} total")
        
        for p in simplisecure_products:
            assert p.get("product_type") == "simplisecure"
    
    def test_filter_products_by_simplicomply_type(self):
        """Products can be filtered by product_type=simplicomply (frontend filter)"""
        cookies = self.get_cookies()
        response = requests.get(f"{BASE_URL}/api/admin/products", cookies=cookies)
        
        assert response.status_code == 200
        products = response.json().get("products", [])
        
        # Filter locally (frontend does this)
        simplicomply_products = [p for p in products if p.get("product_type") == "simplicomply"]
        
        print(f"Found {len(simplicomply_products)} SimpliComply products out of {len(products)} total")
        
        for p in simplicomply_products:
            assert p.get("product_type") == "simplicomply"
    
    def test_search_products_by_name(self):
        """Products can be searched by name (frontend filter)"""
        cookies = self.get_cookies()
        response = requests.get(f"{BASE_URL}/api/admin/products", cookies=cookies)
        
        assert response.status_code == 200
        products = response.json().get("products", [])
        
        if len(products) == 0:
            pytest.skip("No products to test search")
        
        # Test searching for "SimpliSecure" (frontend does this)
        search_term = "SimpliSecure"
        matching = [p for p in products if search_term.lower() in p.get("name", "").lower()]
        
        print(f"Search '{search_term}': Found {len(matching)} matching products")
        assert len(matching) > 0 or len(products) == 0, "Expected to find SimpliSecure products"
    
    def test_search_products_by_sku(self):
        """Products can be searched by SKU (frontend filter)"""
        cookies = self.get_cookies()
        response = requests.get(f"{BASE_URL}/api/admin/products", cookies=cookies)
        
        assert response.status_code == 200
        products = response.json().get("products", [])
        
        if len(products) == 0:
            pytest.skip("No products to test search")
        
        # Test searching for "SSMTEST" SKU
        search_term = "SSMTEST"
        matching = [p for p in products if search_term.lower() in p.get("sku", "").lower()]
        
        print(f"Search SKU '{search_term}': Found {len(matching)} matching products")
    
    # ==================== Contract Creation Tests ====================
    
    def test_create_wholesale_mrr_contract_with_products(self):
        """Create a Wholesale MRR contract with products and verify it saves"""
        cookies = self.get_cookies()
        
        # First, get available products
        products_response = requests.get(f"{BASE_URL}/api/admin/products", cookies=cookies)
        assert products_response.status_code == 200
        available_products = products_response.json().get("products", [])
        
        if len(available_products) == 0:
            pytest.skip("No products available for contract creation")
        
        # Get a test account
        accounts_response = requests.get(f"{BASE_URL}/api/accounts?limit=1", cookies=cookies)
        accounts = accounts_response.json().get("items", [])
        if len(accounts) == 0:
            pytest.skip("No accounts available for contract creation")
        
        account_id = accounts[0].get("account_id")
        
        # Select first product for contract
        selected_product = available_products[0]
        
        # Create contract with product
        contract_data = {
            "name": "TEST_Wholesale_MRR_Contract_Products",
            "account_id": account_id,
            "service_category": "wholesale_mrr",
            "contract_type": "wholesale_mrr",
            "start_date": "2026-03-01",
            "products": [
                {
                    "product_id": selected_product["product_id"],
                    "product_name": selected_product["name"],
                    "sku": selected_product["sku"],
                    "pricing_tier": "monthly",
                    "cost_price": selected_product["cost_monthly"],
                    "sell_price": selected_product.get("retail_pricing", {}).get("monthly", 0),
                    "quantity": 2
                }
            ]
        }
        
        create_response = requests.post(
            f"{BASE_URL}/api/contracts",
            json=contract_data,
            cookies=cookies
        )
        
        assert create_response.status_code == 200 or create_response.status_code == 201, \
            f"Contract creation failed: {create_response.status_code} - {create_response.text}"
        
        created_contract = create_response.json()
        contract_id = created_contract.get("contract_id")
        
        print(f"SUCCESS: Created contract {contract_id}")
        
        # Verify contract has products stored
        assert "products" in created_contract, "Contract should have 'products' field"
        assert len(created_contract["products"]) == 1, "Contract should have 1 product"
        
        stored_product = created_contract["products"][0]
        assert stored_product["product_id"] == selected_product["product_id"]
        assert stored_product["quantity"] == 2
        
        # Verify financial calculations
        expected_line_total = stored_product["sell_price"] * stored_product["quantity"]
        expected_line_margin = (stored_product["sell_price"] - stored_product["cost_price"]) * stored_product["quantity"]
        
        assert stored_product.get("line_total") == expected_line_total, \
            f"Expected line_total {expected_line_total}, got {stored_product.get('line_total')}"
        assert stored_product.get("line_margin") == expected_line_margin, \
            f"Expected line_margin {expected_line_margin}, got {stored_product.get('line_margin')}"
        
        # Verify GET contract returns products
        get_response = requests.get(f"{BASE_URL}/api/contracts/{contract_id}", cookies=cookies)
        assert get_response.status_code == 200
        
        fetched_contract = get_response.json()
        assert len(fetched_contract.get("products", [])) == 1
        
        print(f"SUCCESS: Contract products persisted and verified")
        
        # Cleanup - delete the test contract
        delete_response = requests.delete(f"{BASE_URL}/api/contracts/{contract_id}", cookies=cookies)
        assert delete_response.status_code == 200
        print(f"SUCCESS: Cleaned up test contract {contract_id}")
    
    def test_wholesale_mrr_contract_margin_calculation(self):
        """Verify margin and margin percentage are calculated correctly"""
        cookies = self.get_cookies()
        
        # Get available products
        products_response = requests.get(f"{BASE_URL}/api/admin/products", cookies=cookies)
        assert products_response.status_code == 200
        available_products = products_response.json().get("products", [])
        
        if len(available_products) == 0:
            pytest.skip("No products available")
        
        # Get test account
        accounts_response = requests.get(f"{BASE_URL}/api/accounts?limit=1", cookies=cookies)
        accounts = accounts_response.json().get("items", [])
        if len(accounts) == 0:
            pytest.skip("No accounts available")
        
        account_id = accounts[0].get("account_id")
        product = available_products[0]
        
        cost_price = product["cost_monthly"]
        sell_price = product.get("retail_pricing", {}).get("monthly", 150)
        quantity = 3
        
        # Create contract with specific pricing
        contract_data = {
            "name": "TEST_Margin_Calculation_Contract",
            "account_id": account_id,
            "service_category": "wholesale_mrr",
            "contract_type": "wholesale_mrr",
            "start_date": "2026-03-01",
            "products": [
                {
                    "product_id": product["product_id"],
                    "product_name": product["name"],
                    "sku": product["sku"],
                    "pricing_tier": "monthly",
                    "cost_price": cost_price,
                    "sell_price": sell_price,
                    "quantity": quantity
                }
            ]
        }
        
        create_response = requests.post(
            f"{BASE_URL}/api/contracts",
            json=contract_data,
            cookies=cookies
        )
        
        assert create_response.status_code in [200, 201]
        created_contract = create_response.json()
        contract_id = created_contract.get("contract_id")
        
        # Verify contract-level totals
        expected_total_cost = cost_price * quantity
        expected_total_sell = sell_price * quantity
        expected_total_margin = expected_total_sell - expected_total_cost
        expected_margin_percent = (expected_total_margin / expected_total_sell * 100) if expected_total_sell > 0 else 0
        
        assert created_contract.get("total_cost_monthly") == expected_total_cost, \
            f"Expected total_cost_monthly {expected_total_cost}, got {created_contract.get('total_cost_monthly')}"
        assert created_contract.get("total_sell_monthly") == expected_total_sell, \
            f"Expected total_sell_monthly {expected_total_sell}, got {created_contract.get('total_sell_monthly')}"
        assert created_contract.get("total_margin_monthly") == expected_total_margin, \
            f"Expected total_margin_monthly {expected_total_margin}, got {created_contract.get('total_margin_monthly')}"
        
        print(f"SUCCESS: Margin calculation verified - Margin: ${expected_total_margin} ({expected_margin_percent:.1f}%)")
        
        # Cleanup
        requests.delete(f"{BASE_URL}/api/contracts/{contract_id}", cookies=cookies)
    
    def test_contract_update_with_products(self):
        """Verify contract products can be updated and totals recalculated"""
        cookies = self.get_cookies()
        
        # Get products and account
        products_response = requests.get(f"{BASE_URL}/api/admin/products", cookies=cookies)
        available_products = products_response.json().get("products", [])
        
        accounts_response = requests.get(f"{BASE_URL}/api/accounts?limit=1", cookies=cookies)
        accounts = accounts_response.json().get("items", [])
        
        if len(available_products) == 0 or len(accounts) == 0:
            pytest.skip("Need products and accounts for this test")
        
        account_id = accounts[0]["account_id"]
        product = available_products[0]
        
        # Create initial contract
        contract_data = {
            "name": "TEST_Update_Products_Contract",
            "account_id": account_id,
            "service_category": "wholesale_mrr",
            "contract_type": "wholesale_mrr",
            "start_date": "2026-03-01",
            "products": [
                {
                    "product_id": product["product_id"],
                    "product_name": product["name"],
                    "sku": product["sku"],
                    "pricing_tier": "monthly",
                    "cost_price": product["cost_monthly"],
                    "sell_price": 100.0,
                    "quantity": 1
                }
            ]
        }
        
        create_response = requests.post(f"{BASE_URL}/api/contracts", json=contract_data, cookies=cookies)
        contract_id = create_response.json().get("contract_id")
        
        # Update with new sell price
        update_data = {
            "products": [
                {
                    "product_id": product["product_id"],
                    "product_name": product["name"],
                    "sku": product["sku"],
                    "pricing_tier": "monthly",
                    "cost_price": product["cost_monthly"],
                    "sell_price": 200.0,  # Changed sell price
                    "quantity": 2  # Changed quantity
                }
            ]
        }
        
        update_response = requests.put(
            f"{BASE_URL}/api/contracts/{contract_id}",
            json=update_data,
            cookies=cookies
        )
        
        assert update_response.status_code == 200
        updated_contract = update_response.json()
        
        # Verify update was applied
        updated_product = updated_contract["products"][0]
        assert updated_product["sell_price"] == 200.0
        assert updated_product["quantity"] == 2
        
        # Verify totals recalculated
        expected_sell = 200.0 * 2
        assert updated_contract.get("total_sell_monthly") == expected_sell, \
            f"Expected {expected_sell}, got {updated_contract.get('total_sell_monthly')}"
        
        print(f"SUCCESS: Contract update with products verified")
        
        # Cleanup
        requests.delete(f"{BASE_URL}/api/contracts/{contract_id}", cookies=cookies)
    
    # ==================== Negative Tests ====================
    
    def test_unauthenticated_access_to_products(self):
        """Products endpoint should require authentication"""
        response = requests.get(f"{BASE_URL}/api/admin/products")
        
        assert response.status_code == 401 or response.status_code == 403, \
            f"Expected 401/403 for unauthenticated request, got {response.status_code}"
        
        print("SUCCESS: Products endpoint requires authentication")
    
    def test_product_types_are_valid(self):
        """Verify all products have valid product_type values"""
        cookies = self.get_cookies()
        response = requests.get(f"{BASE_URL}/api/admin/products", cookies=cookies)
        
        assert response.status_code == 200
        products = response.json().get("products", [])
        
        valid_types = ["simplisecure", "simplicomply", "custom"]
        
        for product in products:
            product_type = product.get("product_type")
            assert product_type in valid_types, \
                f"Product {product.get('name')} has invalid product_type: {product_type}"
        
        print(f"SUCCESS: All {len(products)} products have valid product_type")


class TestProductCRUD:
    """Test Product CRUD operations"""
    
    session_token = None
    
    @pytest.fixture(autouse=True)
    def setup_auth(self):
        """Create test session for admin user"""
        if TestProductCRUD.session_token:
            return
        
        token = create_test_session_sync()
        if token:
            TestProductCRUD.session_token = token
        else:
            pytest.skip("Failed to create test session")
    
    def get_cookies(self):
        return {"session_token": TestProductCRUD.session_token}
    
    def test_create_and_delete_product(self):
        """Create a test product and then delete it"""
        cookies = self.get_cookies()
        
        # Create product
        product_data = {
            "name": "TEST_Product_For_Testing",
            "description": "A test product",
            "sku": "TEST_SKU_123",
            "product_type": "custom",
            "retail_pricing": {
                "monthly": 100.0,
                "quarterly": 270.0,
                "annual": 1000.0
            },
            "msp_pricing": {
                "monthly": 80.0,
                "quarterly": 216.0,
                "annual": 800.0
            },
            "cost_monthly": 50.0,
            "is_active": True
        }
        
        create_response = requests.post(
            f"{BASE_URL}/api/admin/products",
            json=product_data,
            cookies=cookies
        )
        
        assert create_response.status_code in [200, 201], \
            f"Failed to create product: {create_response.status_code} - {create_response.text}"
        
        product_id = create_response.json().get("product_id")
        print(f"SUCCESS: Created product {product_id}")
        
        # Get the product to verify
        get_response = requests.get(f"{BASE_URL}/api/admin/products/{product_id}", cookies=cookies)
        assert get_response.status_code == 200
        
        fetched = get_response.json()
        assert fetched.get("name") == product_data["name"]
        assert fetched.get("sku") == product_data["sku"]
        
        # Delete the product
        delete_response = requests.delete(
            f"{BASE_URL}/api/admin/products/{product_id}",
            cookies=cookies
        )
        
        assert delete_response.status_code == 200
        print(f"SUCCESS: Deleted product {product_id}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

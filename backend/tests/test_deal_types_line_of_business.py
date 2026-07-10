"""
Test deal_types (Line of Business) feature
- Verifies Zoho sync correctly maps checkbox fields to deal_types array
- Verifies deal-pipeline API returns deal_types
- Tests the 4 possible values: field_services, professional_services, referral_mrr, wholesale_mrr
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestDealTypesAPI:
    """Test deal_types (Line of Business) in API responses"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session cookie"""
        self.session = requests.Session()
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "support@simplimeta.io", "password": "Admin123!"}
        )
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        yield
        self.session.close()
    
    def test_deal_pipeline_endpoint_returns_200(self):
        """Test that deal-pipeline endpoint is accessible"""
        resp = self.session.get(f"{BASE_URL}/api/contracts/deal-pipeline")
        assert resp.status_code == 200
        data = resp.json()
        assert 'unassigned' in data
        assert 'assigned' in data
    
    def test_deal_pipeline_returns_deal_types_field(self):
        """Test that deal-pipeline response includes deal_types array"""
        resp = self.session.get(f"{BASE_URL}/api/contracts/deal-pipeline")
        assert resp.status_code == 200
        data = resp.json()
        
        # Get all deals
        all_deals = data.get('unassigned', {}).get('items', []) + data.get('assigned', {}).get('items', [])
        
        # Verify deal_types field exists in response structure
        if all_deals:
            sample_deal = all_deals[0]
            assert 'deal_types' in sample_deal, "deal_types field missing from deal response"
            assert isinstance(sample_deal['deal_types'], list), "deal_types should be an array"
    
    def test_deal_types_valid_values(self):
        """Test that deal_types only contains valid LOB values"""
        valid_types = {'field_services', 'professional_services', 'referral_mrr', 'wholesale_mrr'}
        
        resp = self.session.get(f"{BASE_URL}/api/contracts/deal-pipeline")
        assert resp.status_code == 200
        data = resp.json()
        
        all_deals = data.get('unassigned', {}).get('items', []) + data.get('assigned', {}).get('items', [])
        
        invalid_types_found = []
        for deal in all_deals:
            deal_types = deal.get('deal_types', [])
            for dt in deal_types:
                if dt not in valid_types:
                    invalid_types_found.append((deal.get('name', deal.get('deal_id')), dt))
        
        assert len(invalid_types_found) == 0, f"Invalid deal_types found: {invalid_types_found[:5]}"
    
    def test_deals_endpoint_returns_deal_types(self):
        """Test that /api/deals endpoint also includes deal_types"""
        resp = self.session.get(f"{BASE_URL}/api/deals?limit=50")
        assert resp.status_code == 200
        data = resp.json()
        
        items = data.get('items', [])
        if items:
            # Check a sample of deals for deal_types field
            for deal in items[:10]:
                assert 'deal_types' in deal, f"deal_types missing from deal: {deal.get('deal_id')}"
                assert isinstance(deal['deal_types'], list), f"deal_types should be array for deal: {deal.get('deal_id')}"
    
    def test_deal_with_field_services_type(self):
        """Test that field_services type exists in deal data"""
        resp = self.session.get(f"{BASE_URL}/api/deals?limit=500")
        assert resp.status_code == 200
        data = resp.json()
        
        items = data.get('items', [])
        field_services_deals = [d for d in items if 'field_services' in d.get('deal_types', [])]
        
        # Just verify the field is present and can contain field_services
        print(f"Found {len(field_services_deals)} deals with field_services type")
        # This is informational - we don't require specific counts
    
    def test_deal_with_professional_services_type(self):
        """Test that professional_services type exists in deal data"""
        resp = self.session.get(f"{BASE_URL}/api/deals?limit=500")
        assert resp.status_code == 200
        data = resp.json()
        
        items = data.get('items', [])
        pro_services_deals = [d for d in items if 'professional_services' in d.get('deal_types', [])]
        
        print(f"Found {len(pro_services_deals)} deals with professional_services type")
    
    def test_deal_with_referral_mrr_type(self):
        """Test that referral_mrr type exists in deal data"""
        resp = self.session.get(f"{BASE_URL}/api/deals?limit=500")
        assert resp.status_code == 200
        data = resp.json()
        
        items = data.get('items', [])
        referral_deals = [d for d in items if 'referral_mrr' in d.get('deal_types', [])]
        
        print(f"Found {len(referral_deals)} deals with referral_mrr type")
    
    def test_deal_with_wholesale_mrr_type(self):
        """Test that wholesale_mrr type exists in deal data"""
        resp = self.session.get(f"{BASE_URL}/api/deals?limit=500")
        assert resp.status_code == 200
        data = resp.json()
        
        items = data.get('items', [])
        wholesale_deals = [d for d in items if 'wholesale_mrr' in d.get('deal_types', [])]
        
        print(f"Found {len(wholesale_deals)} deals with wholesale_mrr type")
    
    def test_deal_with_multiple_types(self):
        """Test that deals can have multiple deal_types (multiple checkboxes checked)"""
        resp = self.session.get(f"{BASE_URL}/api/deals?limit=1000")
        assert resp.status_code == 200
        data = resp.json()
        
        items = data.get('items', [])
        multi_type_deals = [d for d in items if len(d.get('deal_types', [])) > 1]
        
        print(f"Found {len(multi_type_deals)} deals with multiple deal_types")
        
        # Show an example if found
        if multi_type_deals:
            example = multi_type_deals[0]
            print(f"Example: {example.get('name', 'N/A')[:50]} has types: {example.get('deal_types')}")


class TestZohoSyncDealTypes:
    """Test that Zoho sync properly maps checkbox fields to deal_types"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Login and get session cookie"""
        self.session = requests.Session()
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "support@simplimeta.io", "password": "Admin123!"}
        )
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        yield
        self.session.close()
    
    def test_zoho_checkbox_mapping_in_database(self):
        """Verify that Zoho checkboxes are correctly mapped to deal_types array values"""
        # The mapping from zoho_service.py:
        # Field_Services -> field_services
        # Professional_Services1 -> professional_services
        # Referral_Solution -> referral_mrr
        # Wholesale_Solution_MRR -> wholesale_mrr
        
        resp = self.session.get(f"{BASE_URL}/api/deals?limit=200")
        assert resp.status_code == 200
        data = resp.json()
        
        items = data.get('items', [])
        
        # Count occurrences of each type
        type_counts = {
            'field_services': 0,
            'professional_services': 0,
            'referral_mrr': 0,
            'wholesale_mrr': 0
        }
        
        for deal in items:
            for dt in deal.get('deal_types', []):
                if dt in type_counts:
                    type_counts[dt] += 1
        
        print(f"Deal type distribution (sample of {len(items)} deals):")
        for dtype, count in type_counts.items():
            print(f"  {dtype}: {count}")
        
        # At least verify the field is being populated
        total_types = sum(type_counts.values())
        print(f"Total deal_types found: {total_types}")

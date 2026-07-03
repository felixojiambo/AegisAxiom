"""
Seed Sales Ops fields for contractors
Creates test contractors with rating, partnership tier, marketing funds, etc.
"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import os
from datetime import datetime, timezone

async def seed_contractor_sales_ops_data():
    mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
    client = AsyncIOMotorClient(mongo_url)
    db = client['field_services']
    
    print("🔄 Creating test contractors with Sales Ops data...")
    
    contractors = [
        {
            'business_id': 'cb_test001',
            'business_name': 'Elite Field Services LLC',
            'contact_name': 'Michael Chen',
            'email': 'michael@elitefieldservices.com',
            'phone': '555-0101',
            'address_street': '123 Tech Boulevard',
            'address_city': 'Austin',
            'address_state': 'TX',
            'address_zip': '78701',
            'tax_id_ein': '12-3456789',
            'w9_on_file': True,
            'is_1099_eligible': True,
            'billing_frequency': 'weekly',
            'payment_terms': 'net_30',
            'payment_method': 'ACH',
            'status': 'active',
            'rating': 4.8,
            'partnership_tier': 'gold',
            'marketing_funds_allocated': 10000.0,
            'marketing_funds_used': 3500.0,
            'emergency_contact_name': 'Sarah Chen',
            'emergency_contact_phone': '555-911-0001',
            'emergency_contact_relationship': 'Business Partner',
            'interview_notes': 'Excellent technical skills. 10+ years experience. Strong safety record.',
            'onboarding_date': datetime(2024, 6, 15, tzinfo=timezone.utc),
            'interviewer_name': 'Sarah Johnson',
            'resource_type_capabilities': [
                {'resource_type_name': 'Field Service Tech', 'hourly_rate': 75.0},
                {'resource_type_name': 'Low Voltage Installer', 'hourly_rate': 85.0}
            ],
            'legal_documents': [],
            'grievances': [],
            'created_at': datetime.now(timezone.utc),
            'updated_at': datetime.now(timezone.utc)
        },
        {
            'business_id': 'cb_test002',
            'business_name': 'ProTech Solutions Inc',
            'contact_name': 'Jennifer Martinez',
            'email': 'jennifer@protechsolutions.com',
            'phone': '555-0202',
            'address_street': '456 Innovation Drive',
            'address_city': 'San Jose',
            'address_state': 'CA',
            'address_zip': '95110',
            'tax_id_ein': '98-7654321',
            'w9_on_file': True,
            'is_1099_eligible': True,
            'billing_frequency': 'biweekly',
            'payment_terms': 'net_30',
            'payment_method': 'Wire',
            'status': 'active',
            'rating': 4.2,
            'partnership_tier': 'silver',
            'marketing_funds_allocated': 5000.0,
            'marketing_funds_used': 1200.0,
            'emergency_contact_name': 'Robert Martinez',
            'emergency_contact_phone': '555-911-0002',
            'emergency_contact_relationship': 'Spouse',
            'interview_notes': 'Strong enterprise background. Good project management skills.',
            'onboarding_date': datetime(2025, 1, 10, tzinfo=timezone.utc),
            'interviewer_name': 'Tom Davis',
            'resource_type_capabilities': [
                {'resource_type_name': 'Project Manager', 'hourly_rate': 125.0},
                {'resource_type_name': 'Senior Developer', 'hourly_rate': 140.0}
            ],
            'legal_documents': [
                {
                    'type': 'NDA',
                    'filename': 'nda_2025.pdf',
                    'uploaded_at': '2025-01-10',
                    'url': '#'
                }
            ],
            'grievances': [
                {
                    'grievance_id': 'grv_test001',
                    'contractor_business_id': 'cb_test002',
                    'grievance_date': '2025-09-15',
                    'reporter_name': 'Customer ABC Corp',
                    'reporter_type': 'customer',
                    'severity': 'medium',
                    'description': 'Project delivery delayed by 2 days. Communication could be improved.',
                    'resolved': True,
                    'resolution_notes': 'Contractor apologized, credited $500. Process improvements implemented.',
                    'resolved_date': '2025-09-20',
                    'resolved_by': 'Sarah Johnson',
                    'affects_rating': False,
                    'created_at': datetime(2025, 9, 15, tzinfo=timezone.utc)
                }
            ],
            'created_at': datetime.now(timezone.utc),
            'updated_at': datetime.now(timezone.utc)
        },
        {
            'business_id': 'cb_test003',
            'business_name': 'Rapid Install Co',
            'contact_name': 'David Thompson',
            'email': 'david@rapidinstall.com',
            'phone': '555-0303',
            'address_street': '789 Service Lane',
            'address_city': 'Phoenix',
            'address_state': 'AZ',
            'address_zip': '85001',
            'tax_id_ein': '45-6789012',
            'w9_on_file': False,
            'is_1099_eligible': True,
            'billing_frequency': 'weekly',
            'payment_terms': 'net_14',
            'payment_method': 'Check',
            'status': 'active',
            'rating': 3.9,
            'partnership_tier': 'bronze',
            'marketing_funds_allocated': 2000.0,
            'marketing_funds_used': 500.0,
            'emergency_contact_name': 'Lisa Thompson',
            'emergency_contact_phone': '555-911-0003',
            'emergency_contact_relationship': 'Spouse',
            'interview_notes': 'Newer contractor. Shows promise but needs more experience.',
            'onboarding_date': datetime(2025, 8, 1, tzinfo=timezone.utc),
            'interviewer_name': 'Mike Rodriguez',
            'resource_type_capabilities': [
                {'resource_type_name': 'Field Service Tech', 'hourly_rate': 65.0}
            ],
            'legal_documents': [],
            'grievances': [
                {
                    'grievance_id': 'grv_test002',
                    'contractor_business_id': 'cb_test003',
                    'grievance_date': '2025-11-01',
                    'reporter_name': 'Internal - Project Lead',
                    'reporter_type': 'internal',
                    'severity': 'low',
                    'description': 'Missed a team meeting without prior notice.',
                    'resolved': False,
                    'affects_rating': False,
                    'created_at': datetime(2025, 11, 1, tzinfo=timezone.utc)
                }
            ],
            'created_at': datetime.now(timezone.utc),
            'updated_at': datetime.now(timezone.utc)
        }
    ]
    
    for contractor in contractors:
        await db.contractor_businesses.insert_one(contractor)
        print(f'✅ Created: {contractor[\"business_name\"]}')
    
    print(f'\n✅ Created {len(contractors)} test contractors!')
    client.close()

asyncio.run(create_contractors())
"
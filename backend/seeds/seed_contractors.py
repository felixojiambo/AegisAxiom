import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import os
from datetime import datetime, timezone

async def create_contractors():
    mongo_url = os.environ.get('MONGO_URL')
    client = AsyncIOMotorClient(mongo_url)
    db = client['field_services']
    
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
        }
    ]
    
    for contractor in contractors:
        await db.contractor_businesses.insert_one(contractor)
        print(f'Created: {contractor["business_name"]}')
    
    print(f'Done! Created {len(contractors)} contractors')
    client.close()

asyncio.run(create_contractors())

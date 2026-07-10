"""
Seed Sales Ops fields for existing contractors
Adds rating, partnership tier, marketing funds, etc.
"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import os
from datetime import datetime, timezone

async def seed_contractor_sales_ops_data():
    mongo_url = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
    client = AsyncIOMotorClient(mongo_url)
    db = client['field_services']
    
    print("🔄 Adding Sales Ops fields to existing contractors...")
    
    # Get all contractors
    contractors = await db.contractor_businesses.find({}, {"_id": 0}).to_list(100)
    
    if not contractors:
        print("⚠️  No contractors found. Please create contractors first.")
        client.close()
        return
    
    # Sample Sales Ops data
    sales_ops_data = [
        {
            "rating": 4.8,
            "partnership_tier": "gold",
            "marketing_funds_allocated": 10000.0,
            "marketing_funds_used": 3500.0,
            "emergency_contact_name": "John Emergency",
            "emergency_contact_phone": "555-911-0001",
            "emergency_contact_relationship": "Operations Manager",
            "interview_notes": "Excellent technical skills. Strong communication. Recommended by existing client.",
            "onboarding_date": datetime(2024, 6, 15, tzinfo=timezone.utc),
            "interviewer_name": "Sarah Johnson",
            "resource_type_capabilities": [
                {"resource_type_name": "Field Service Tech", "hourly_rate": 75.0},
                {"resource_type_name": "Low Voltage Installer", "hourly_rate": 85.0}
            ],
            "legal_documents": [],
            "grievances": []
        },
        {
            "rating": 4.2,
            "partnership_tier": "silver",
            "marketing_funds_allocated": 5000.0,
            "marketing_funds_used": 1200.0,
            "emergency_contact_name": "Mike Support",
            "emergency_contact_phone": "555-911-0002",
            "emergency_contact_relationship": "Business Partner",
            "interview_notes": "Good track record. Reliable for standard projects.",
            "onboarding_date": datetime(2025, 1, 10, tzinfo=timezone.utc),
            "interviewer_name": "Tom Davis",
            "resource_type_capabilities": [
                {"resource_type_name": "Project Manager", "hourly_rate": 125.0},
                {"resource_type_name": "Senior Developer", "hourly_rate": 140.0}
            ],
            "legal_documents": [
                {
                    "type": "NDA",
                    "filename": "nda_2025.pdf",
                    "uploaded_at": "2025-01-10",
                    "url": "#"
                }
            ],
            "grievances": [
                {
                    "grievance_id": "grv_test001",
                    "contractor_business_id": "",  # Will be filled
                    "grievance_date": "2025-09-15",
                    "reporter_name": "Customer XYZ",
                    "reporter_type": "customer",
                    "severity": "medium",
                    "description": "Project delivery was delayed by 2 days. Communication could be improved.",
                    "resolved": True,
                    "resolution_notes": "Contractor apologized and credited $500. Process improvements implemented.",
                    "resolved_date": "2025-09-20",
                    "resolved_by": "Sarah Johnson",
                    "affects_rating": False,
                    "created_at": datetime(2025, 9, 15, tzinfo=timezone.utc)
                }
            ]
        },
        {
            "rating": 3.9,
            "partnership_tier": "bronze",
            "marketing_funds_allocated": 2000.0,
            "marketing_funds_used": 500.0,
            "emergency_contact_name": "Lisa Contact",
            "emergency_contact_phone": "555-911-0003",
            "emergency_contact_relationship": "Spouse",
            "interview_notes": "Newer contractor. Shows promise but needs more experience.",
            "onboarding_date": datetime(2025, 8, 1, tzinfo=timezone.utc),
            "interviewer_name": "Mike Rodriguez",
            "resource_type_capabilities": [
                {"resource_type_name": "Field Service Tech", "hourly_rate": 65.0}
            ],
            "legal_documents": [],
            "grievances": [
                {
                    "grievance_id": "grv_test002",
                    "contractor_business_id": "",  # Will be filled
                    "grievance_date": "2025-11-01",
                    "reporter_name": "Internal - Project Lead",
                    "reporter_type": "internal",
                    "severity": "low",
                    "description": "Missed a team meeting without prior notice.",
                    "resolved": False,
                    "affects_rating": False,
                    "created_at": datetime(2025, 11, 1, tzinfo=timezone.utc)
                }
            ]
        }
    ]
    
    # Update each contractor
    for idx, contractor in enumerate(contractors[:3]):  # Update first 3
        if idx < len(sales_ops_data):
            update_data = sales_ops_data[idx].copy()
            
            # Fill grievance contractor_business_id
            for grievance in update_data["grievances"]:
                grievance["contractor_business_id"] = contractor["business_id"]
            
            result = await db.contractor_businesses.update_one(
                {"business_id": contractor["business_id"]},
                {"$set": update_data}
            )
            
            if result.modified_count > 0:
                print(f"✅ Updated {contractor['business_name']} with Sales Ops data")
    
    print("✅ Contractor Sales Ops data seeded successfully!")
    client.close()

if __name__ == "__main__":
    asyncio.run(seed_contractor_sales_ops_data())

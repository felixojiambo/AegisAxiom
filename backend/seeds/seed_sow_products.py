"""
Seed test products with item_category for SOW testing.
"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import os
from uuid import uuid4
from datetime import datetime, timezone

MONGO_URL = os.environ.get('MONGO_URL')
DB_NAME = os.environ.get('DB_NAME', 'simplimeta')

async def seed_products():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    now = datetime.now(timezone.utc).isoformat()
    
    # Clear existing test products to avoid duplicates
    await db.products.delete_many({"sku": {"$regex": "^(MAT-|HW-|PROD-)"}})
    
    # Materials for Field Services
    materials = [
        {
            "product_id": f"prod_{uuid4().hex[:12]}",
            "name": "Cat6 Ethernet Cable (1000ft)",
            "description": "Category 6 ethernet cable for network installations",
            "sku": "MAT-CAT6-1000",
            "product_type": "cables",
            "item_category": "materials",
            "retail_pricing": {"monthly": 15.00, "quarterly": 42.00, "annual": 162.00},
            "msp_pricing": {"monthly": 12.50, "quarterly": 35.00, "annual": 135.00},
            "cost_monthly": 10.00,
            "is_active": True,
            "created_at": now,
            "updated_at": now
        },
        {
            "product_id": f"prod_{uuid4().hex[:12]}",
            "name": "Cable Tester Professional",
            "description": "Professional grade cable tester for field diagnostics",
            "sku": "MAT-TESTER-PRO",
            "product_type": "tools",
            "item_category": "materials",
            "retail_pricing": {"monthly": 25.00, "quarterly": 70.00, "annual": 270.00},
            "msp_pricing": {"monthly": 21.00, "quarterly": 58.80, "annual": 226.80},
            "cost_monthly": 18.00,
            "is_active": True,
            "created_at": now,
            "updated_at": now
        },
        {
            "product_id": f"prod_{uuid4().hex[:12]}",
            "name": "Network Patch Panels (24-port)",
            "description": "24-port patch panel for structured cabling",
            "sku": "MAT-PP-24",
            "product_type": "accessories",
            "item_category": "materials",
            "retail_pricing": {"monthly": 8.50, "quarterly": 23.80, "annual": 91.80},
            "msp_pricing": {"monthly": 7.00, "quarterly": 19.60, "annual": 75.60},
            "cost_monthly": 5.50,
            "is_active": True,
            "created_at": now,
            "updated_at": now
        }
    ]
    
    # Hardware for Professional Services
    hardware = [
        {
            "product_id": f"prod_{uuid4().hex[:12]}",
            "name": "Cisco Meraki MX64 Security Appliance",
            "description": "Enterprise-grade security and SD-WAN appliance",
            "sku": "HW-CISCO-MX64",
            "product_type": "security_appliance",
            "item_category": "hardware",
            "retail_pricing": {"monthly": 49.50, "quarterly": 138.60, "annual": 534.60},
            "msp_pricing": {"monthly": 42.50, "quarterly": 119.00, "annual": 459.00},
            "cost_monthly": 38.00,
            "is_active": True,
            "created_at": now,
            "updated_at": now
        },
        {
            "product_id": f"prod_{uuid4().hex[:12]}",
            "name": "Ubiquiti UniFi Dream Machine Pro",
            "description": "All-in-one network gateway and controller",
            "sku": "HW-UBNT-UDMP",
            "product_type": "networking",
            "item_category": "hardware",
            "retail_pricing": {"monthly": 37.90, "quarterly": 106.12, "annual": 409.32},
            "msp_pricing": {"monthly": 32.00, "quarterly": 89.60, "annual": 345.60},
            "cost_monthly": 28.00,
            "is_active": True,
            "created_at": now,
            "updated_at": now
        },
        {
            "product_id": f"prod_{uuid4().hex[:12]}",
            "name": "Dell PowerEdge R740 Server",
            "description": "Enterprise rack server for on-premise deployments",
            "sku": "HW-DELL-R740",
            "product_type": "servers",
            "item_category": "hardware",
            "retail_pricing": {"monthly": 450.00, "quarterly": 1260.00, "annual": 4860.00},
            "msp_pricing": {"monthly": 380.00, "quarterly": 1064.00, "annual": 4104.00},
            "cost_monthly": 320.00,
            "is_active": True,
            "created_at": now,
            "updated_at": now
        }
    ]
    
    # Products & Services for Wholesale MRR
    products_services = [
        {
            "product_id": f"prod_{uuid4().hex[:12]}",
            "name": "Microsoft 365 Business Premium",
            "description": "Complete productivity suite with advanced security",
            "sku": "PROD-M365-BP",
            "product_type": "microsoft365",
            "item_category": "wholesale_mrr",
            "retail_pricing": {"monthly": 22.00, "quarterly": 61.60, "annual": 237.60},
            "msp_pricing": {"monthly": 18.50, "quarterly": 51.80, "annual": 199.80},
            "cost_monthly": 15.00,
            "is_active": True,
            "created_at": now,
            "updated_at": now
        },
        {
            "product_id": f"prod_{uuid4().hex[:12]}",
            "name": "Webroot SecureAnywhere",
            "description": "Cloud-based endpoint protection",
            "sku": "PROD-WEBROOT",
            "product_type": "simplisecure",
            "item_category": "wholesale_mrr",
            "retail_pricing": {"monthly": 2.50, "quarterly": 7.00, "annual": 27.00},
            "msp_pricing": {"monthly": 2.00, "quarterly": 5.60, "annual": 21.60},
            "cost_monthly": 1.50,
            "is_active": True,
            "created_at": now,
            "updated_at": now
        },
        {
            "product_id": f"prod_{uuid4().hex[:12]}",
            "name": "Datto BCDR Backup Solution",
            "description": "Business continuity and disaster recovery",
            "sku": "PROD-DATTO-BCDR",
            "product_type": "custom",
            "item_category": "wholesale_mrr",
            "retail_pricing": {"monthly": 150.00, "quarterly": 420.00, "annual": 1620.00},
            "msp_pricing": {"monthly": 125.00, "quarterly": 350.00, "annual": 1350.00},
            "cost_monthly": 95.00,
            "is_active": True,
            "created_at": now,
            "updated_at": now
        }
    ]
    
    all_products = materials + hardware + products_services
    
    print(f"Seeding {len(all_products)} products...")
    print(f"  - Materials: {len(materials)}")
    print(f"  - Hardware: {len(hardware)}")
    print(f"  - Products & Services: {len(products_services)}")
    
    # Insert products with proper schema
    for product in all_products:
        await db.products.insert_one(product)
        print(f"  ✓ {product['name']} ({product['item_category']})")
    
    print(f"\n✅ Product seeding complete! Total: {len(all_products)} products")
    
    client.close()

if __name__ == "__main__":
    asyncio.run(seed_products())

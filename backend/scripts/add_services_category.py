"""
Update existing hardware/materials to one-time pricing and add Services category products.
"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import os
from uuid import uuid4
from datetime import datetime, timezone

MONGO_URL = os.environ.get('MONGO_URL')
DB_NAME = os.environ.get('DB_NAME', 'test_database')


async def update_products_with_services():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    now = datetime.now(timezone.utc).isoformat()
    
    print("=== Updating Product Catalog ===\n")
    
    # Step 1: Update Hardware products to one-time pricing
    print("1. Updating Hardware products to ONE-TIME pricing...")
    hardware_products = await db.products.find(
        {"item_category": "hardware"},
        {"_id": 0, "product_id": 1, "name": 1, "msp_pricing": 1}
    ).to_list(50)
    
    for product in hardware_products:
        # Convert monthly pricing to one-time equivalent (assume 36-month lifecycle)
        msp_monthly = product.get("msp_pricing", {}).get("monthly", 0)
        one_time_price = msp_monthly * 36 if msp_monthly > 0 else 0  # 3-year amortization
        
        await db.products.update_one(
            {"product_id": product["product_id"]},
            {
                "$set": {
                    "retail_pricing": {
                        "monthly": 0,
                        "quarterly": 0,
                        "annual": 0,
                        "one_time": one_time_price * 1.2  # Retail markup
                    },
                    "msp_pricing": {
                        "monthly": 0,
                        "quarterly": 0,
                        "annual": 0,
                        "one_time": one_time_price
                    },
                    "cost_one_time": one_time_price * 0.85,
                    "cost_monthly": 0,
                    "updated_at": now
                }
            }
        )
        print(f"  ✓ {product['name']} → One-time: ${one_time_price:.2f}")
    
    # Step 2: Update Materials products to one-time pricing
    print("\n2. Updating Materials products to ONE-TIME pricing...")
    materials_products = await db.products.find(
        {"item_category": "materials"},
        {"_id": 0, "product_id": 1, "name": 1, "msp_pricing": 1}
    ).to_list(50)
    
    for product in materials_products:
        # Materials are typically lower cost, use 12-month amortization
        msp_monthly = product.get("msp_pricing", {}).get("monthly", 0)
        one_time_price = msp_monthly * 12 if msp_monthly > 0 else 0
        
        await db.products.update_one(
            {"product_id": product["product_id"]},
            {
                "$set": {
                    "retail_pricing": {
                        "monthly": 0,
                        "quarterly": 0,
                        "annual": 0,
                        "one_time": one_time_price * 1.2
                    },
                    "msp_pricing": {
                        "monthly": 0,
                        "quarterly": 0,
                        "annual": 0,
                        "one_time": one_time_price
                    },
                    "cost_one_time": one_time_price * 0.85,
                    "cost_monthly": 0,
                    "updated_at": now
                }
            }
        )
        print(f"  ✓ {product['name']} → One-time: ${one_time_price:.2f}")
    
    # Step 3: Add Services category products (NEW)
    print("\n3. Creating Services category products...")
    
    services = [
        {
            "product_id": f"prod_{uuid4().hex[:12]}",
            "name": "Security System Installation",
            "description": "Complete security system installation including cameras, NVR, and cabling",
            "sku": "SVC-FS-SEC-INSTALL",
            "product_type": "installation",
            "item_category": "services",
            "retail_pricing": {"monthly": 0, "quarterly": 0, "annual": 0, "one_time": 4500.00},
            "msp_pricing": {"monthly": 0, "quarterly": 0, "annual": 0, "one_time": 3800.00},
            "cost_one_time": 3000.00,
            "cost_monthly": 0,
            "is_active": True,
            "created_at": now,
            "updated_at": now
        },
        {
            "product_id": f"prod_{uuid4().hex[:12]}",
            "name": "Network Design & Implementation",
            "description": "Professional network design, configuration, and deployment service",
            "sku": "SVC-PS-NET-DESIGN",
            "product_type": "consulting",
            "item_category": "services",
            "retail_pricing": {"monthly": 0, "quarterly": 0, "annual": 0, "one_time": 8500.00},
            "msp_pricing": {"monthly": 0, "quarterly": 0, "annual": 0, "one_time": 7200.00},
            "cost_one_time": 5500.00,
            "cost_monthly": 0,
            "is_active": True,
            "created_at": now,
            "updated_at": now
        },
        {
            "product_id": f"prod_{uuid4().hex[:12]}",
            "name": "Managed IT Support (Monthly)",
            "description": "Ongoing IT support and maintenance service",
            "sku": "SVC-PS-SUPPORT-MO",
            "product_type": "support",
            "item_category": "services",
            "retail_pricing": {"monthly": 150.00, "quarterly": 420.00, "annual": 1620.00, "one_time": 0},
            "msp_pricing": {"monthly": 125.00, "quarterly": 350.00, "annual": 1350.00, "one_time": 0},
            "cost_monthly": 95.00,
            "cost_one_time": 0,
            "is_active": True,
            "created_at": now,
            "updated_at": now
        },
        {
            "product_id": f"prod_{uuid4().hex[:12]}",
            "name": "Preventive Maintenance Package",
            "description": "Quarterly on-site preventive maintenance and health checks",
            "sku": "SVC-FS-MAINT-QTR",
            "product_type": "maintenance",
            "item_category": "services",
            "retail_pricing": {"monthly": 0, "quarterly": 450.00, "annual": 1650.00, "one_time": 0},
            "msp_pricing": {"monthly": 0, "quarterly": 375.00, "annual": 1375.00, "one_time": 0},
            "cost_monthly": 85.00,  # Amortized monthly cost
            "cost_one_time": 0,
            "is_active": True,
            "created_at": now,
            "updated_at": now
        },
        {
            "product_id": f"prod_{uuid4().hex[:12]}",
            "name": "Infrastructure Design Consultation",
            "description": "Strategic IT infrastructure planning and design consultation",
            "sku": "SVC-PS-CONSULT",
            "product_type": "design",
            "item_category": "services",
            "retail_pricing": {"monthly": 0, "quarterly": 0, "annual": 0, "one_time": 6500.00},
            "msp_pricing": {"monthly": 0, "quarterly": 0, "annual": 0, "one_time": 5500.00},
            "cost_one_time": 4200.00,
            "cost_monthly": 0,
            "is_active": True,
            "created_at": now,
            "updated_at": now
        }
    ]
    
    for service in services:
        await db.products.insert_one(service)
        pricing_type = "Recurring" if service["cost_monthly"] > 0 else "One-time"
        print(f"  ✓ {service['name']} ({service['product_type']}) - {pricing_type}")
    
    print(f"\n✅ Product catalog updated!")
    print(f"   Hardware: Converted to one-time pricing")
    print(f"   Materials: Converted to one-time pricing")
    print(f"   Services: Added {len(services)} new service products")
    
    # Show final distribution
    print(f"\n📊 Final Category Distribution:")
    for category in ["services", "wholesale_mrr", "hardware", "materials"]:
        count = await db.products.count_documents({"item_category": category, "is_active": True})
        print(f"   {category}: {count} products")
    
    client.close()


if __name__ == "__main__":
    asyncio.run(update_products_with_services())

"""
Migrate existing products to new category structure.

Old Structure:
  - product_type: simplisecure, custom, etc. (no category)
  
New Structure:
  - item_category: wholesale_mrr, hardware, materials (PRIMARY)
  - product_type: simplisecure, custom, networking, cables, etc. (SECONDARY)
  
Migration Rules:
  1. All existing products with product_type (simplisecure, simplicomply, custom, microsoft365) 
     → Set item_category = 'wholesale_mrr'
  2. New products with item_category already set → Keep as is
  3. Update all products missing item_category field
"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import os
from datetime import datetime, timezone

MONGO_URL = os.environ.get('MONGO_URL')
DB_NAME = os.environ.get('DB_NAME', 'test_database')


async def migrate_product_categories():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    print("=== Product Category Migration ===\n")
    
    # Find all products
    all_products = await db.products.find({}, {"_id": 0}).to_list(500)
    print(f"Total products found: {len(all_products)}")
    
    updated_count = 0
    
    for product in all_products:
        product_id = product.get("product_id")
        current_category = product.get("item_category")
        product_type = product.get("product_type")
        
        # Determine correct category
        new_category = None
        
        if not current_category or current_category == "products_services":
            # Legacy products or incorrectly categorized
            if product_type in ["simplisecure", "simplicomply", "microsoft365", "custom"]:
                new_category = "wholesale_mrr"
            elif product_type in ["networking", "servers", "security_appliance"]:
                new_category = "hardware"
            elif product_type in ["cables", "tools", "accessories"]:
                new_category = "materials"
            else:
                # Default to wholesale_mrr for unknown types
                new_category = "wholesale_mrr"
        
        # Update if category is missing or incorrect
        if current_category != new_category and new_category:
            await db.products.update_one(
                {"product_id": product_id},
                {
                    "$set": {
                        "item_category": new_category,
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }
                }
            )
            print(f"✓ Updated {product.get('name')}")
            print(f"  product_type: {product_type} → item_category: {new_category}")
            updated_count += 1
    
    print(f"\n✅ Migration complete!")
    print(f"   Products updated: {updated_count}")
    print(f"   Products unchanged: {len(all_products) - updated_count}")
    
    # Show final category distribution
    print(f"\n📊 Final Category Distribution:")
    for category in ["wholesale_mrr", "hardware", "materials"]:
        count = await db.products.count_documents({"item_category": category, "is_active": True})
        print(f"   {category}: {count} products")
    
    client.close()


if __name__ == "__main__":
    asyncio.run(migrate_product_categories())

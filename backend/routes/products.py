"""
Products & Services Management
For Wholesale MRR products (SimpliSecure, SimpliComply, etc.)
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone
from uuid import uuid4
from .auth import get_current_user
from database import get_db
from services.ai.config import get_llm_key
from services.field_registry import register_fields, register_module_events, register_module_config

router = APIRouter()

register_module_config("products", "products", "product_id", "Products & Services")
register_module_events("products", "product", ["created", "updated", "deleted"])
register_fields("products", "product", [
    {"name": "name", "type": "string", "label": "Product Name", "description": "Name of the product or service"},
    {"name": "sku", "type": "string", "label": "SKU", "description": "Stock keeping unit identifier"},
    {"name": "product_type", "type": "string", "label": "Product Type", "description": "Type within category (simplisecure, simplicomply, custom, etc.)"},
    {"name": "item_category", "type": "string", "label": "Category", "description": "Primary category (services, wholesale_mrr, hardware, materials)"},
    {"name": "cost_monthly", "type": "number", "label": "Monthly Cost", "description": "Monthly cost for recurring products"},
    {"name": "cost_one_time", "type": "number", "label": "One-Time Cost", "description": "One-time cost for hardware or materials"},
    {"name": "is_active", "type": "boolean", "label": "Is Active", "description": "Whether the product is available for sale"},
    {"name": "description", "type": "string", "label": "Description", "description": "Product description and details"},
    {"name": "distributor_id", "type": "string", "label": "Distributor", "description": "Distributor this product is sourced from"},
    {"name": "distributor_sku", "type": "string", "label": "Distributor SKU", "description": "SKU as known by the distributor (may differ from our sku)"},
])

class ProductPricing(BaseModel):
    """Flexible pricing structure for all billing periods."""
    daily: float = 0.0
    weekly: float = 0.0
    monthly: float = 0.0
    quarterly: float = 0.0
    annual: float = 0.0
    one_time: float = 0.0

class ProductBase(BaseModel):
    """
    Product catalog item with hierarchical categorization.
    
    Category Hierarchy:
      - item_category (PRIMARY): services, wholesale_mrr, hardware, materials
      - product_type (SECONDARY): Type within category
        * services: installation, maintenance, consulting, design, support
        * wholesale_mrr: simplisecure, simplicomply, custom, microsoft365
        * hardware: networking, servers, security_appliance
        * materials: cables, tools, accessories
    
    Pricing Models by Category:
      - services: Flexible (recurring OR one-time)
      - wholesale_mrr: Recurring only (monthly/quarterly/annual)
      - hardware: One-time only
      - materials: One-time only
    """
    name: str
    description: str = ""
    sku: str
    product_type: str  # Type within category
    item_category: str  # Required: services, wholesale_mrr, hardware, materials
    
    # Pricing tiers - structure varies by category
    retail_pricing: ProductPricing
    msp_pricing: ProductPricing
    
    # Cost - use cost_monthly for recurring, cost_one_time for one-time
    cost_monthly: float = 0.0
    cost_one_time: float = 0.0  # NEW: For hardware/materials

    # Distributor (optional) — hardware/material products typically have one
    distributor_id: Optional[str] = None
    distributor_sku: Optional[str] = None

    is_active: bool = True

class Product(ProductBase):
    product_id: str
    created_at: str
    updated_at: str

@router.get("/products")
async def get_products(
    item_category: Optional[str] = None,
    user: dict = Depends(get_current_user)
):
    """Get all active products, optionally filtered by item_category."""
    db = get_db()
    
    query = {"is_active": True}
    if item_category:
        query["item_category"] = item_category
    
    products = await db.products.find(query, {"_id": 0}).to_list(500)
    
    return {"products": products}

@router.post("/products")
async def create_product(product: ProductBase, user: dict = Depends(get_current_user)):
    """Create a new product (admin only)"""
    db = get_db()

    # Check admin permission
    if "*" not in (user.permissions or []) and "admin.products" not in (user.permissions or []):
        raise HTTPException(status_code=403, detail="Admin access required")

    # Check if product with same SKU exists
    existing = await db.products.find_one({
        "sku": product.sku,
        "is_active": True
    })

    if existing:
        raise HTTPException(status_code=400, detail="Product with this SKU already exists")

    if product.distributor_id:
        distributor = await db.distributors.find_one({"distributor_id": product.distributor_id}, {"_id": 0})
        if not distributor:
            raise HTTPException(status_code=400, detail="Distributor not found")
    
    product_id = f"prod_{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    
    product_doc = {
        "product_id": product_id,
        **product.model_dump(),
        "created_at": now,
        "updated_at": now
    }
    
    await db.products.insert_one(product_doc)
    
    return {"product_id": product_id}

@router.put("/products/{product_id}")
async def update_product(product_id: str, product: ProductBase, user: dict = Depends(get_current_user)):
    """Update a product (admin only)"""
    db = get_db()
    
    # Check admin permission
    if "*" not in (user.permissions or []) and "admin.products" not in (user.permissions or []):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    existing = await db.products.find_one({"product_id": product_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Check if SKU is taken by another product
    sku_check = await db.products.find_one({
        "sku": product.sku,
        "product_id": {"$ne": product_id},
        "is_active": True
    })
    
    if sku_check:
        raise HTTPException(status_code=400, detail="SKU already in use by another product")

    if product.distributor_id:
        distributor = await db.distributors.find_one({"distributor_id": product.distributor_id}, {"_id": 0})
        if not distributor:
            raise HTTPException(status_code=400, detail="Distributor not found")

    now = datetime.now(timezone.utc).isoformat()

    await db.products.update_one(
        {"product_id": product_id},
        {"$set": {
            **product.model_dump(),
            "updated_at": now
        }}
    )
    
    return {"message": "Product updated"}

@router.delete("/products/{product_id}")
async def delete_product(product_id: str, user: dict = Depends(get_current_user)):
    """Soft delete a product (admin only)"""
    db = get_db()
    
    # Check admin permission
    if "*" not in (user.permissions or []) and "admin.products" not in (user.permissions or []):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    existing = await db.products.find_one({"product_id": product_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Product not found")
    
    now = datetime.now(timezone.utc).isoformat()
    
    await db.products.update_one(
        {"product_id": product_id},
        {"$set": {
            "is_active": False,
            "updated_at": now
        }}
    )
    
    return {"message": "Product deleted"}

@router.get("/products/{product_id}")
async def get_product(product_id: str, user: dict = Depends(get_current_user)):
    """Get a specific product"""
    db = get_db()
    
    product = await db.products.find_one({"product_id": product_id}, {"_id": 0})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    return product

@router.post("/products/parse-bulk")
async def parse_bulk_products(request: dict, user: dict = Depends(get_current_user)):
    """Parse bulk product text using AI (admin only)"""
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="AI features require the emergentintegrations package, which is not available in this environment."
        )
    import os
    import json
    
    # Check admin permission
    if "*" not in (user.permissions or []) and "admin.products" not in (user.permissions or []):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    text = request.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="No text provided")
    
    try:
        # Use LlmChat to parse the product information
        chat = LlmChat(
            api_key=get_llm_key(),
            session_id=f"product_parse_{user.get('user_id') if isinstance(user, dict) else getattr(user, 'user_id', 'unknown')}",
            system_message="You are a product data parser. Return only valid JSON arrays."
        ).with_model("openai", "gpt-4o-mini")
        
        prompt = f"""Parse the following product information into a structured JSON array.
Each product should have these fields:
- name (string)
- description (string, can be empty)
- sku (string, unique identifier)
- product_type (string: 'simplisecure', 'simplicomply', or 'custom')
- retail_pricing (object with monthly, quarterly, annual as numbers)
- msp_pricing (object with monthly, quarterly, annual as numbers)
- cost_monthly (number)

If quarterly or annual pricing isn't specified, calculate reasonable estimates (quarterly = monthly * 2.8, annual = monthly * 10.8).
If MSP pricing isn't specified, set it to ~80% of retail pricing.

Product text:
{text}

Return ONLY a valid JSON array of products, nothing else."""

        user_message = UserMessage(text=prompt)
        result_text = await chat.send_message(user_message)
        result_text = result_text.strip()
        
        # Remove markdown code blocks if present
        if result_text.startswith("```"):
            result_text = result_text.split("```")[1]
            if result_text.startswith("json"):
                result_text = result_text[4:]
            result_text = result_text.strip()
        
        # Parse JSON
        products = json.loads(result_text)
        
        # Validate and clean up products
        cleaned_products = []
        for p in products:
            if not p.get("name") or not p.get("sku"):
                continue
            
            cleaned = {
                "name": p.get("name", ""),
                "description": p.get("description", ""),
                "sku": p.get("sku", ""),
                "product_type": p.get("product_type", "custom"),
                "retail_pricing": {
                    "monthly": float(p.get("retail_pricing", {}).get("monthly", 0)),
                    "quarterly": float(p.get("retail_pricing", {}).get("quarterly", 0)),
                    "annual": float(p.get("retail_pricing", {}).get("annual", 0))
                },
                "msp_pricing": {
                    "monthly": float(p.get("msp_pricing", {}).get("monthly", 0)),
                    "quarterly": float(p.get("msp_pricing", {}).get("quarterly", 0)),
                    "annual": float(p.get("msp_pricing", {}).get("annual", 0))
                },
                "cost_monthly": float(p.get("cost_monthly", 0)),
                "is_active": True
            }
            cleaned_products.append(cleaned)
        
        return {"products": cleaned_products}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse products: {str(e)}")

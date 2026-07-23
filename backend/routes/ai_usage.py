"""AI Usage Analytics — admin endpoints for usage tracking, model config, and pricing."""

from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from db.session import get_session
from repositories.users import UserRepository
from auth_helper import get_current_user, has_perm
from services.field_registry import register_fields, register_module_events, register_module_config

router = APIRouter()

register_module_config("ai_usage", "ai_usage", "usage_id", "AI & Automation")
register_module_events("ai_usage", "usage_record", [])
register_fields("ai_usage", "usage_record", [
    {"name": "agent", "type": "string", "label": "Agent", "description": "AI agent or route that was used"},
    {"name": "model", "type": "string", "label": "Model", "description": "LLM model name used"},
    {"name": "input_tokens", "type": "number", "label": "Input Tokens", "description": "Number of input/prompt tokens"},
    {"name": "output_tokens", "type": "number", "label": "Output Tokens", "description": "Number of output/completion tokens"},
    {"name": "total_tokens", "type": "number", "label": "Total Tokens", "description": "Total tokens consumed"},
    {"name": "estimated_cost", "type": "number", "label": "Estimated Cost", "description": "Estimated cost in USD"},
])


# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────

def _parse_dates(date_from: Optional[str], date_to: Optional[str]):
    """Parse date range params, defaulting to last 30 days."""
    now = datetime.now(timezone.utc)
    if date_from:
        start = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
    else:
        start = now - timedelta(days=30)
    if date_to:
        end = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc, hour=23, minute=59, second=59)
    else:
        end = now
    return start, end


def _get_user_filter(current_user):
    """Return a user_id match filter based on the user's permissions.

    Returns:
        dict  — MongoDB $match fragment for user_id scoping
        None  — if the user has no ai_usage permission at all (should 403)
    """
    if has_perm(current_user, "ai_usage.view.all"):
        return {}  # no filter
    if has_perm(current_user, "ai_usage.view.managed"):
        viewable = getattr(current_user, "ai_usage_viewable_users", None) or []
        # Include the user's own data plus their managed users
        return {"user_id": {"$in": [current_user.user_id] + viewable}}
    if has_perm(current_user, "ai_usage.view"):
        return {"user_id": current_user.user_id}
    return None


def _resolve_display_identity(*, row_name, row_email, live_name, live_email, user_id):
    """Pick the best display name/email for a Top Users row.

    Order: (1) snapshotted name on the row (survives user deletion),
    (2) live users.name from a fresh lookup, (3) snapshotted email on the row,
    (4) live users.email, (5) "System / Unknown" only if no identity at all
    AND user_id is missing — otherwise we fall back to user_id so admins can
    still trace the actor. Tested in tests/test_ai_usage_unknown.py.

    Returns ``(name, email, name_is_email_fallback)`` so the frontend can
    render the row appropriately when the only identity available was an
    email address (e.g. de-emphasize the name column or show the email
    icon prefix). The previous two-value return silently aliased
    ``name = email`` and the UI had no way to tell the difference between
    a real human name and an email-shaped fallback.
    """
    name = row_name or live_name
    email = row_email or live_email or ""
    name_is_email_fallback = False
    if not name:
        if email:
            name = email
            name_is_email_fallback = True
        elif user_id:
            name = user_id
        else:
            name = "System / Unknown"
    return name, email, name_is_email_fallback


async def _load_pricing(db) -> dict:
    """Load pricing table from DB into a dict keyed by model name."""
    docs = await db.ai_model_pricing.find({}, {"_id": 0}).to_list(100)
    return {d["model"]: d for d in docs}


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int, pricing: dict):
    """Estimate cost for a model's token usage. Returns None for non-Gemini models."""
    if not model.startswith("gemini"):
        return None
    p = pricing.get(model)
    if not p:
        # Fall back to gemini-2.5-flash pricing for unknown Gemini models
        p = pricing.get("gemini-2.5-flash", {"input_per_million": 0.15, "output_per_million": 0.60})
    cost = (prompt_tokens * p["input_per_million"] + completion_tokens * p["output_per_million"]) / 1_000_000
    return round(cost, 6)


# ──────────────────────────────────────────────
#  Usage Endpoints
# ──────────────────────────────────────────────

@router.get("/summary")
async def get_usage_summary(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user=Depends(get_current_user),
):
    """High-level summary: total tokens, requests, unique users, estimated cost."""
    user_filter = _get_user_filter(current_user)
    if user_filter is None:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    start, end = _parse_dates(date_from, date_to)
    match = {"created_at": {"$gte": start, "$lte": end}, **user_filter}

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": None,
            "total_prompt_tokens": {"$sum": "$prompt_tokens"},
            "total_completion_tokens": {"$sum": "$completion_tokens"},
            "total_tokens": {"$sum": "$total_tokens"},
            "total_requests": {"$sum": 1},
            "unique_users": {"$addToSet": "$user_id"},
        }},
        {"$project": {
            "_id": 0,
            "total_prompt_tokens": 1,
            "total_completion_tokens": 1,
            "total_tokens": 1,
            "total_requests": 1,
            "unique_users": {"$size": "$unique_users"},
        }},
    ]

    result = await db.ai_usage.aggregate(pipeline).to_list(1)
    summary = result[0] if result else {
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "total_tokens": 0,
        "total_requests": 0,
        "unique_users": 0,
    }

    # Calculate estimated cost from per-model breakdown
    pricing = await _load_pricing(db)
    cost_pipeline = [
        {"$match": match},
        {"$group": {
            "_id": "$model",
            "prompt_tokens": {"$sum": "$prompt_tokens"},
            "completion_tokens": {"$sum": "$completion_tokens"},
        }},
    ]
    model_totals = await db.ai_usage.aggregate(cost_pipeline).to_list(100)
    total_cost = 0.0
    for mt in model_totals:
        c = _estimate_cost(mt["_id"], mt["prompt_tokens"], mt["completion_tokens"], pricing)
        if c is not None:
            total_cost += c

    summary["estimated_cost"] = round(total_cost, 4)
    return summary


@router.get("/daily")
async def get_daily_usage(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user=Depends(get_current_user),
):
    """Daily time-series data for charting."""
    user_filter = _get_user_filter(current_user)
    if user_filter is None:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    start, end = _parse_dates(date_from, date_to)
    match = {"created_at": {"$gte": start, "$lte": end}, **user_filter}

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": {
                "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                "model": "$model",
            },
            "prompt_tokens": {"$sum": "$prompt_tokens"},
            "completion_tokens": {"$sum": "$completion_tokens"},
            "total_tokens": {"$sum": "$total_tokens"},
            "requests": {"$sum": 1},
        }},
        {"$sort": {"_id.date": 1}},
    ]

    raw = await db.ai_usage.aggregate(pipeline).to_list(5000)
    pricing = await _load_pricing(db)

    # Merge into per-day rows
    days = {}
    for row in raw:
        date = row["_id"]["date"]
        model = row["_id"]["model"]
        if date not in days:
            days[date] = {"date": date, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "requests": 0, "estimated_cost": 0.0}
        d = days[date]
        d["prompt_tokens"] += row["prompt_tokens"]
        d["completion_tokens"] += row["completion_tokens"]
        d["total_tokens"] += row["total_tokens"]
        d["requests"] += row["requests"]
        c = _estimate_cost(model, row["prompt_tokens"], row["completion_tokens"], pricing)
        if c is not None:
            d["estimated_cost"] += c

    result = sorted(days.values(), key=lambda x: x["date"])
    for r in result:
        r["estimated_cost"] = round(r["estimated_cost"], 4)
    return result


@router.get("/by-user")
async def get_usage_by_user(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user=Depends(get_current_user),
    pg: AsyncSession = Depends(get_session),
):
    """Per-user breakdown with names (admin/managed only)."""
    user_filter = _get_user_filter(current_user)
    if user_filter is None:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Only view.all and view.managed can see per-user breakdown
    if not has_perm(current_user, "ai_usage.view.all") and not has_perm(current_user, "ai_usage.view.managed"):
        raise HTTPException(status_code=403, detail="Insufficient permissions for per-user view")

    db = get_db()
    start, end = _parse_dates(date_from, date_to)
    match = {"created_at": {"$gte": start, "$lte": end}, **user_filter}

    # Snapshot user_email / user_name carried on the row when log_usage wrote
    # it (added 2026-05). The previous pipeline used $sort + $first which
    # was deterministic but still surfaced null when the newest row in a
    # (user, model) bucket predated the snapshot rollout — so a user who
    # fell silent after the rollout kept a null identity forever. Switch to
    # $max on both fields: $max ignores nulls and returns the
    # lexicographically largest non-null string, which reliably surfaces
    # SOME informative identity even when pre-snapshot rows dominate the
    # bucket. Trade-off documented: $max is "any non-null, deterministic"
    # not "literal latest" — for display purposes that's strictly better
    # than null. Removes the $sort dependency on a created_at index and
    # drops the per-query O(n log n) sort cost.
    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": {"user_id": "$user_id", "model": "$model"},
            "prompt_tokens": {"$sum": "$prompt_tokens"},
            "completion_tokens": {"$sum": "$completion_tokens"},
            "total_tokens": {"$sum": "$total_tokens"},
            "requests": {"$sum": 1},
            "row_user_email": {"$max": "$user_email"},
            "row_user_name": {"$max": "$user_name"},
        }},
    ]

    raw = await db.ai_usage.aggregate(pipeline).to_list(5000)
    pricing = await _load_pricing(db)

    # Merge by user
    users_map = {}
    for row in raw:
        uid = row["_id"]["user_id"]
        model = row["_id"]["model"]
        if uid not in users_map:
            users_map[uid] = {
                "user_id": uid,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "requests": 0,
                "estimated_cost": 0.0,
                "row_user_name": None,
                "row_user_email": None,
            }
        u = users_map[uid]
        u["prompt_tokens"] += row["prompt_tokens"]
        u["completion_tokens"] += row["completion_tokens"]
        u["total_tokens"] += row["total_tokens"]
        u["requests"] += row["requests"]
        # Hold onto the most recent snapshotted name/email if any group had one
        if not u["row_user_name"] and row.get("row_user_name"):
            u["row_user_name"] = row.get("row_user_name")
        if not u["row_user_email"] and row.get("row_user_email"):
            u["row_user_email"] = row.get("row_user_email")
        c = _estimate_cost(model, row["prompt_tokens"], row["completion_tokens"], pricing)
        if c is not None:
            u["estimated_cost"] += c

    # Enrich with user names/emails — falls back to a live users lookup for
    # rows logged before user_email/user_name were snapshotted.
    user_ids = [uid for uid in users_map.keys() if uid]
    if user_ids:
        user_docs = await UserRepository(pg).get_users_by_ids(user_ids)
        name_map = {u.user_id: u for u in user_docs}
    else:
        name_map = {}

    result = []
    for uid, data in users_map.items():
        info = name_map.get(uid) if uid else None
        # Display-name fallback chain — see comment near _resolve_display_identity
        # for the ordering rationale.
        display_name, display_email, name_is_email_fallback = _resolve_display_identity(
            row_name=data.pop("row_user_name", None),
            row_email=data.pop("row_user_email", None),
            live_name=info.name if info else None,
            live_email=info.email if info else None,
            user_id=uid,
        )
        data["user_name"] = display_name
        data["user_email"] = display_email
        data["name_is_email_fallback"] = name_is_email_fallback
        data["estimated_cost"] = round(data["estimated_cost"], 4)
        result.append(data)

    result.sort(key=lambda x: x["total_tokens"], reverse=True)
    return result[:50]


@router.get("/by-breakdown")
async def get_usage_breakdown(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user=Depends(get_current_user),
):
    """Per-agent and per-model breakdowns."""
    user_filter = _get_user_filter(current_user)
    if user_filter is None:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    start, end = _parse_dates(date_from, date_to)
    match = {"created_at": {"$gte": start, "$lte": end}, **user_filter}
    pricing = await _load_pricing(db)

    # By agent
    agent_pipeline = [
        {"$match": match},
        {"$group": {
            "_id": "$agent",
            "prompt_tokens": {"$sum": "$prompt_tokens"},
            "completion_tokens": {"$sum": "$completion_tokens"},
            "total_tokens": {"$sum": "$total_tokens"},
            "requests": {"$sum": 1},
        }},
        {"$sort": {"total_tokens": -1}},
    ]
    by_agent = await db.ai_usage.aggregate(agent_pipeline).to_list(50)
    for a in by_agent:
        a["name"] = a.pop("_id")

    # By model (with cost)
    model_pipeline = [
        {"$match": match},
        {"$group": {
            "_id": "$model",
            "prompt_tokens": {"$sum": "$prompt_tokens"},
            "completion_tokens": {"$sum": "$completion_tokens"},
            "total_tokens": {"$sum": "$total_tokens"},
            "requests": {"$sum": 1},
        }},
        {"$sort": {"total_tokens": -1}},
    ]
    by_model = await db.ai_usage.aggregate(model_pipeline).to_list(50)
    for m in by_model:
        m["name"] = m.pop("_id")
        m["estimated_cost"] = _estimate_cost(m["name"], m["prompt_tokens"], m["completion_tokens"], pricing)

    return {"by_agent": by_agent, "by_model": by_model}


# ──────────────────────────────────────────────
#  Model Configuration Endpoints
# ──────────────────────────────────────────────

# Default seed models — used to populate the DB on first load
_DEFAULT_MODELS = [
    {"provider": "google", "model": "gemini-2.0-flash", "label": "Gemini 2.0 Flash"},
    {"provider": "google", "model": "gemini-2.5-flash", "label": "Gemini 2.5 Flash"},
    {"provider": "google", "model": "gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash-Lite"},
    {"provider": "google", "model": "gemini-2.5-pro", "label": "Gemini 2.5 Pro"},
    {"provider": "google", "model": "gemini-3-flash-preview", "label": "Gemini 3 Flash (Preview)"},
    {"provider": "google", "model": "gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro (Preview)"},
    {"provider": "google", "model": "gemini-3.1-flash-lite-preview", "label": "Gemini 3.1 Flash-Lite (Preview)"},
]


async def _ensure_available_models(db):
    """Seed the ai_available_models collection if empty."""
    count = await db.ai_available_models.count_documents({})
    if count == 0:
        await db.ai_available_models.insert_many([{**m} for m in _DEFAULT_MODELS])


@router.get("/models/available")
async def get_available_models(current_user=Depends(get_current_user)):
    """Return the list of available models (DB-backed, admin-editable)."""
    if not has_perm(current_user, "ai_usage.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    await _ensure_available_models(db)
    models = await db.ai_available_models.find({}, {"_id": 0}).sort("label", 1).to_list(length=None)
    return models


@router.post("/models/available")
async def add_available_model(body: dict, current_user=Depends(get_current_user)):
    """Add a new model to the available list."""
    if not has_perm(current_user, "ai_usage.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    model = body.get("model", "").strip()
    label = body.get("label", "").strip()
    provider = body.get("provider", "").strip() or "google"

    if not model or not label:
        raise HTTPException(status_code=400, detail="model and label are required")

    db = get_db()
    existing = await db.ai_available_models.find_one({"model": model})
    if existing:
        raise HTTPException(status_code=409, detail=f"Model '{model}' already exists")

    await db.ai_available_models.insert_one({"provider": provider, "model": model, "label": label})

    # Auto-create a pricing entry so it appears in the Pricing Config tab immediately
    await db.ai_model_pricing.update_one(
        {"model": model},
        {"$setOnInsert": {"model": model, "input_per_million": 0.0, "output_per_million": 0.0}},
        upsert=True,
    )

    return {"message": f"Model '{model}' added"}


@router.put("/models/available/{model_id}")
async def update_available_model(model_id: str, body: dict, current_user=Depends(get_current_user)):
    """Update an existing model's name or label."""
    if not has_perm(current_user, "ai_usage.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    new_model = body.get("model", "").strip()
    new_label = body.get("label", "").strip()
    new_provider = body.get("provider", "").strip() or "google"

    if not new_model or not new_label:
        raise HTTPException(status_code=400, detail="model and label are required")

    db = get_db()

    # Prevent duplicate model IDs on rename
    if new_model != model_id:
        conflict = await db.ai_available_models.find_one({"model": new_model})
        if conflict:
            raise HTTPException(status_code=409, detail=f"Model '{new_model}' already exists")

    result = await db.ai_available_models.update_one(
        {"model": model_id},
        {"$set": {"model": new_model, "label": new_label, "provider": new_provider}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Model not found")

    # If the model identifier changed, cascade to agent configs and pricing
    if new_model != model_id:
        await db.ai_model_config.update_many(
            {"model_name": model_id},
            {"$set": {"model_name": new_model, "model_provider": new_provider}},
        )
        await db.ai_model_pricing.update_one(
            {"model": model_id},
            {"$set": {"model": new_model}},
        )

    return {"message": f"Model updated to '{new_model}'"}


@router.delete("/models/available/{model_id}")
async def delete_available_model(model_id: str, current_user=Depends(get_current_user)):
    """Remove a model from the available list."""
    if not has_perm(current_user, "ai_usage.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()

    # Block deletion if any agent/role is configured to use this model
    refs = await db.ai_model_config.find({"model_name": model_id}, {"_id": 0, "key": 1}).to_list(100)
    if refs:
        keys = ", ".join(r["key"] for r in refs)
        raise HTTPException(status_code=409, detail=f"Cannot remove '{model_id}' — it is in use by: {keys}. Reassign them first.")

    result = await db.ai_available_models.delete_one({"model": model_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Model not found")

    return {"message": f"Model '{model_id}' removed"}


@router.get("/models")
async def get_model_configs(current_user=Depends(get_current_user)):
    """List all model configs — auto-discovers agents from registry + system roles."""
    if not has_perm(current_user, "ai_usage.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()

    # Build the full list of configurable keys from the agent registry
    from services.ai.agents.registry import all_agents
    agents = all_agents()

    # System entries + agent entries with their code defaults
    entries = [
        {"key": "router", "label": "Router (message classifier)", "default_provider": "google", "default_model": "gemini-3-flash-preview"},
        {"key": "merge", "label": "Merge (multi-agent synthesizer)", "default_provider": "google", "default_model": "gemini-3-flash-preview"},
    ]
    for agent in agents:
        entries.append({
            "key": agent.route_name,
            "label": f"Agent: {agent.route_name.replace('_', ' ').title()}",
            "default_provider": agent.model_provider,
            "default_model": agent.model_name,
        })

    # Load overrides from DB
    overrides = await db.ai_model_config.find({}, {"_id": 0}).to_list(100)
    override_map = {o["key"]: o for o in overrides}

    # Merge defaults with overrides
    result = []
    for entry in entries:
        override = override_map.get(entry["key"])
        result.append({
            "key": entry["key"],
            "label": entry["label"],
            "default_provider": entry["default_provider"],
            "default_model": entry["default_model"],
            "model_provider": override["model_provider"] if override else entry["default_provider"],
            "model_name": override["model_name"] if override else entry["default_model"],
            "is_overridden": override is not None,
        })

    return result


@router.put("/models/{key}")
async def update_model_config(key: str, body: dict, current_user=Depends(get_current_user)):
    """Update the model for a specific agent or system role."""
    if not has_perm(current_user, "ai_usage.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    model_name = body.get("model_name")
    model_provider = body.get("model_provider", "google")

    if not model_name:
        raise HTTPException(status_code=400, detail="model_name is required")

    db = get_db()
    await _ensure_available_models(db)

    # Validate against DB-backed available models
    valid = await db.ai_available_models.find_one({"model": model_name})
    if not valid:
        raise HTTPException(status_code=400, detail=f"Model '{model_name}' is not in the available models list. Add it first in Model Management.")
    await db.ai_model_config.update_one(
        {"key": key},
        {"$set": {"key": key, "model_provider": model_provider, "model_name": model_name}},
        upsert=True,
    )
    return {"message": f"Model for '{key}' updated to {model_name}"}


# ──────────────────────────────────────────────
#  Pricing Configuration Endpoints
# ──────────────────────────────────────────────

@router.get("/pricing")
async def get_pricing(current_user=Depends(get_current_user)):
    """List pricing for all available models, plus orphaned pricing entries."""
    if not has_perm(current_user, "ai_usage.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()
    await _ensure_available_models(db)

    available = await db.ai_available_models.find({}, {"_id": 0}).to_list(length=None)
    pricing_docs = await db.ai_model_pricing.find({}, {"_id": 0}).to_list(length=None)
    pricing_map = {d["model"]: d for d in pricing_docs}
    available_ids = {m["model"] for m in available}

    result = []
    # Every available model gets a pricing row
    for m in available:
        p = pricing_map.get(m["model"])
        result.append({
            "model": m["model"],
            "label": m.get("label", m["model"]),
            "input_per_million": p["input_per_million"] if p else 0.0,
            "output_per_million": p["output_per_million"] if p else 0.0,
            "has_pricing": p is not None,
            "orphaned": False,
        })

    # Orphaned pricing entries (model removed but pricing kept for historical cost)
    for model_name, p in pricing_map.items():
        if model_name not in available_ids:
            result.append({
                "model": model_name,
                "label": model_name,
                "input_per_million": p["input_per_million"],
                "output_per_million": p["output_per_million"],
                "has_pricing": True,
                "orphaned": True,
            })

    result.sort(key=lambda x: (x["orphaned"], x["model"]))
    return result


@router.put("/pricing/{model}")
async def update_pricing(model: str, body: dict, current_user=Depends(get_current_user)):
    """Update pricing for a model."""
    if not has_perm(current_user, "ai_usage.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    input_per_million = body.get("input_per_million")
    output_per_million = body.get("output_per_million")

    if input_per_million is None or output_per_million is None:
        raise HTTPException(status_code=400, detail="input_per_million and output_per_million are required")

    if not isinstance(input_per_million, (int, float)) or not isinstance(output_per_million, (int, float)):
        raise HTTPException(status_code=400, detail="Pricing values must be numbers")

    if input_per_million < 0 or output_per_million < 0:
        raise HTTPException(status_code=400, detail="Pricing values must be non-negative")

    db = get_db()
    await db.ai_model_pricing.update_one(
        {"model": model},
        {"$set": {"model": model, "input_per_million": input_per_million, "output_per_million": output_per_million}},
        upsert=True,
    )
    return {"message": f"Pricing for '{model}' updated"}

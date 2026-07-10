"""Orion SOW Builder — core business logic.

Orchestrates Vega session loading, SOW section generation,
draft persistence, and preview rendering. These functions are
called by the tool handlers in tools.py, but can also be used
directly for testing and batch operations.
"""

import logging
from datetime import datetime, timezone

from services.orion.prompts import (
    SMART_SYSTEM_PROMPT, DETAILED_SOW_PROMPT,
    BILLING_SYSTEM_PROMPT, ASSUMPTIONS_RISKS_PROMPT,
)
from services.orion.llm import call_sow_drafter
from services.orion.input_adapter import serialize_vega_session_for_prompts

logger = logging.getLogger(__name__)

SECTION_ORDER = [
    "executive_summary", "smart", "milestones",
    "assumptions_risks", "billing", "signature",
]

PROMPT_MAP = {
    "executive_summary": SMART_SYSTEM_PROMPT,
    "smart": SMART_SYSTEM_PROMPT,
    "milestones": DETAILED_SOW_PROMPT,
    "billing": BILLING_SYSTEM_PROMPT,
    "assumptions_risks": ASSUMPTIONS_RISKS_PROMPT,
    "signature": None,  # Deterministic — no LLM call
}


async def load_vega_session(db, opportunity_id: str) -> dict:
    """Load an Vega discovery session by session_id."""
    session = await db.vega_discovery_sessions.find_one(
        {"session_id": opportunity_id}, {"_id": 0}
    )
    return session


async def generate_sow_section(db, section: str, vega_session_id: str, context: str = "") -> dict:
    """Generate one SOW section using the appropriate prompt."""
    vega_session = await load_vega_session(db, vega_session_id)
    if not vega_session:
        return {"error": f"Vega session '{vega_session_id}' not found"}

    combined = serialize_vega_session_for_prompts(vega_session)
    if context:
        combined += f"\n\n{context}"

    prompt = PROMPT_MAP.get(section)
    if prompt is None:
        # Signature and other deterministic sections have no LLM prompt
        if section not in PROMPT_MAP:
            return {"error": f"Unknown section: {section}"}
        return {"section_name": section, "content": "", "note": "Deterministic section — no LLM call"}

    parse_json = section != "assumptions_risks"
    result = await call_sow_drafter(prompt, combined, parse_json=parse_json)
    return {"section_name": section, "content": result}


async def update_sow_draft(db, opportunity_id: str, section: str, content: str) -> dict:
    """Write/update a section in the opportunity_sows collection."""
    now = datetime.now(timezone.utc).isoformat()

    await db.opportunity_sows.update_one(
        {"opportunity_id": opportunity_id, "created_by_agent": "ai_orion"},
        {"$set": {
            f"sections.{section}": {"content": content, "status": "draft", "updated_at": now},
            "updated_at": now,
        }},
        upsert=True,
    )
    return {"section": section, "status": "draft"}


async def render_sow_preview(db, opportunity_id: str) -> dict:
    """Load current SOW state and format for artifact rendering."""
    sow = await db.opportunity_sows.find_one(
        {"opportunity_id": opportunity_id, "created_by_agent": "ai_orion"},
        {"_id": 0},
    )
    if not sow:
        return {"error": "No Orion SOW draft found"}

    raw_sections = sow.get("sections", {})
    sections_list = []
    titles = {
        "executive_summary": "Executive Summary",
        "smart": "SMART Proposal",
        "milestones": "Detailed Statement of Work",
        "assumptions_risks": "Assumptions & Risks",
        "billing": "Billing Details",
        "signature": "Acceptance & Signature",
    }

    for sid in SECTION_ORDER:
        sec = raw_sections.get(sid, {})
        sections_list.append({
            "section_id": sid,
            "title": titles.get(sid, sid),
            "content": sec.get("content", ""),
            "status": sec.get("status", "pending"),
        })

    return {
        "opportunity_id": opportunity_id,
        "sow_id": sow.get("sow_id"),
        "sections": sections_list,
    }

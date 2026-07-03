"""Orion SOW Builder — conversational tool handlers.

Called from services/ai/agents/conversational_tools.py via thin wrappers.
"""

import logging
import re
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


async def handle_load_vega_session(args, db, user, session_id=None, employee_id=None):
    """Load an Vega session for Orion consumption."""
    opportunity_id = args.get("opportunity_id")
    if not opportunity_id:
        return {"error": "No opportunity_id provided"}

    session = await db.vega_discovery_sessions.find_one(
        {"session_id": opportunity_id, "user_id": user.user_id}, {"_id": 0}
    )
    if not session:
        return {"error": f"Vega session '{opportunity_id}' not found. Ask the user which opportunity to work on."}

    # Also load any existing Orion SOW draft
    sow = await db.opportunity_sows.find_one(
        {"opportunity_id": opportunity_id, "created_by_agent": "ai_orion"},
        {"_id": 0},
    )

    return {
        "session": session,
        "workflows": session.get("ai_employee_matrix", []),
        "prior_orion_state": sow,
    }


_SIGNATURE_TEMPLATE = """## Acceptance & Signature

By signing below, the parties acknowledge and agree to the terms, scope, and pricing outlined in this Statement of Work.

---

**SimpliMeta (Provider)**

Signature: ____________________________

Printed Name: ____________________________

Title: ____________________________

Date: ____________________________

---

**[Client Company Name] (Client)**

Signature: ____________________________

Printed Name: ____________________________

Title: ____________________________

Date: ____________________________

---

**SimpliMeta Contact Information:**
- [Insert Address]
- [Insert Phone]
- [Insert Email]

**Client Contact Information:**
- [Insert Address]
- [Insert Phone]
- [Insert Email]

---

*This Statement of Work is governed by the Master Services Agreement between SimpliMeta and Client dated [INSERT DATE], if applicable. In the absence of an MSA, this SOW contains the complete terms of engagement.*"""


def _format_smart_dimensions(parsed: dict) -> str:
    """Format the 5 SMART dimensions from parsed JSON into markdown."""
    smart_parts = []
    for key, label in [("smart_specific", "Specific"), ("smart_measurable", "Measurable"),
                       ("smart_achievable", "Achievable"), ("smart_relevant", "Relevant"),
                       ("smart_time_bound", "Time-Bound")]:
        val = parsed.get(key, "")
        if val:
            smart_parts.append(f"### {label}\n\n{val}")
    return "\n\n".join(smart_parts) if smart_parts else ""


async def handle_generate_sow_section(args, db, user, session_id=None, employee_id=None):
    """Generate a SOW section using the appropriate prompt."""
    from services.orion.prompts import (
        SMART_SYSTEM_PROMPT, DETAILED_SOW_PROMPT,
        BILLING_SYSTEM_PROMPT, ASSUMPTIONS_RISKS_PROMPT,
    )
    from services.orion.llm import call_sow_drafter
    from services.orion.input_adapter import serialize_vega_session_for_prompts

    section = args.get("section")
    vega_session_id = args.get("vega_session_id")
    additional_context = args.get("additional_context", "")

    if not section or not vega_session_id:
        return {"error": "Both section and vega_session_id are required"}

    # Signature is deterministic — no LLM call needed
    if section == "signature":
        return {"section_name": "signature", "content": _SIGNATURE_TEMPLATE, "parsed_json": None}

    # Smart section can reuse cached result from executive_summary generation
    prior_smart_result = args.get("prior_smart_result")
    if section == "smart" and prior_smart_result and isinstance(prior_smart_result, dict):
        content = _format_smart_dimensions(prior_smart_result)
        if content:
            return {"section_name": "smart", "content": content, "parsed_json": prior_smart_result}

    vega_session = await db.vega_discovery_sessions.find_one(
        {"session_id": vega_session_id}, {"_id": 0}
    )
    if not vega_session:
        return {"error": f"Vega session '{vega_session_id}' not found"}

    combined_context = serialize_vega_session_for_prompts(vega_session)
    if additional_context:
        combined_context += f"\n\nADDITIONAL CONTEXT FROM USER:\n{additional_context}"

    prompt_map = {
        "executive_summary": SMART_SYSTEM_PROMPT,
        "smart": SMART_SYSTEM_PROMPT,
        "milestones": DETAILED_SOW_PROMPT,
        "billing": BILLING_SYSTEM_PROMPT,
        "assumptions_risks": ASSUMPTIONS_RISKS_PROMPT,
    }
    system_prompt = prompt_map.get(section)
    if not system_prompt:
        return {"error": f"Unknown section: {section}"}

    # Only parse JSON for sections that return structured data
    parse_json = section not in ("assumptions_risks",)

    try:
        result = await call_sow_drafter(system_prompt, combined_context, parse_json=parse_json)

        content = result

        # Format structured JSON results into readable markdown
        if result and isinstance(result, dict):
            parsed = result

            if section == "executive_summary":
                content = parsed.get("executive_summary", str(result))

            elif section == "smart":
                content = _format_smart_dimensions(parsed) or str(result)

            elif section == "milestones":
                parts = []

                # Scope of services narrative
                scope = parsed.get("scope_of_services", "")
                if scope:
                    # Strip any leading heading the LLM may have included
                    scope = re.sub(r'^#+\s*(Scope of Services|SCOPE OF SERVICES)\s*\n*', '', scope).strip()
                    parts.append("## Scope of Services\n\n" + scope)

                # Milestones
                milestones = parsed.get("milestones", [])
                if milestones:
                    lines = []
                    if parsed.get("suggested_completion_date"):
                        lines.append(f"**Target Completion:** {parsed['suggested_completion_date']}\n")
                    for i, ms in enumerate(milestones, 1):
                        if isinstance(ms, dict):
                            name = ms.get("milestone_name", ms.get("name", f"Phase {i}"))
                            lines.append(f"### {name}\n")
                            bullets = ms.get("bullets", [])
                            for b in bullets:
                                if isinstance(b, dict):
                                    text = b.get("bullet_text", b.get("text", ""))
                                    deliverable = b.get("deliverable", "")
                                    hours = b.get("estimated_hours", "")
                                    line = f"- {text}"
                                    if deliverable:
                                        line += f" *Deliverable: {deliverable}*"
                                    if hours and int(hours) > 0:
                                        line += f" ({hours}h)"
                                    else:
                                        line += " [Fill Hours]"
                                    lines.append(line)
                                else:
                                    lines.append(f"- {b}")
                        else:
                            lines.append(f"### Phase {i}\n\n{ms}")
                    parts.append("## Implementation Plan\n\n" + "\n\n".join(lines))
                else:
                    parts.append("## Implementation Plan\n\nMilestones to be defined during project kickoff.")

                content = "\n\n---\n\n".join(parts) if parts else "Detailed SOW to be defined."

            elif section == "billing":
                billing_parts = []
                for key, label in [("payment_terms", "Payment Terms"), ("billing_terms", "Billing Terms"),
                                   ("special_billing_instructions", "Special Instructions")]:
                    val = parsed.get(key, "")
                    if val:
                        billing_parts.append(f"### {label}\n\n{val}")
                if not billing_parts:
                    content = "Billing details to be determined during contract finalization.\n\nStandard terms: Net 30, monthly invoicing."
                else:
                    content = "\n\n".join(billing_parts)

            else:
                content = str(result)

        # Ensure content is always a string
        if not isinstance(content, str):
            import json as _json
            content = _json.dumps(content, indent=2, default=str) if isinstance(content, (dict, list)) else str(content)

        return {"section_name": section, "content": content, "parsed_json": result if parse_json else None}
    except Exception as e:
        logger.error("SOW section generation failed for %s: %s", section, e)
        return {"error": f"Failed to generate {section}: {str(e)[:200]}"}


async def handle_update_sow_draft(args, db, user, session_id=None, employee_id=None):
    """Write/update a section in opportunity_sows. Idempotent."""
    opportunity_id = args.get("opportunity_id")
    section = args.get("section")
    content = args.get("content")

    if not all([opportunity_id, section, content]):
        return {"error": "opportunity_id, section, and content are all required"}

    now = datetime.now(timezone.utc).isoformat()

    sow = await db.opportunity_sows.find_one(
        {"opportunity_id": opportunity_id, "created_by_agent": "ai_orion"},
        {"_id": 0},
    )

    if not sow:
        sow_id = _gen_id("sow")
        sow_doc = {
            "sow_id": sow_id,
            "opportunity_id": opportunity_id,
            "created_by_agent": "ai_orion",
            "created_by": user.user_id,
            "sections": {section: {"content": content, "status": "draft", "updated_at": now}},
            "status": "in_progress",
            "created_at": now,
            "updated_at": now,
        }
        await db.opportunity_sows.insert_one(sow_doc)
    else:
        sow_id = sow.get("sow_id", "unknown")
        await db.opportunity_sows.update_one(
            {"opportunity_id": opportunity_id, "created_by_agent": "ai_orion"},
            {"$set": {
                f"sections.{section}": {"content": content, "status": "draft", "updated_at": now},
                "updated_at": now,
            }},
        )

    return {"sow_id": sow_id, "section": section, "status": "draft"}


async def handle_render_sow_preview(args, db, user, session_id=None, employee_id=None):
    """Load current SOW state and format for artifact rendering."""
    opportunity_id = args.get("opportunity_id")
    if not opportunity_id:
        return {"error": "No opportunity_id provided"}

    sow = await db.opportunity_sows.find_one(
        {"opportunity_id": opportunity_id, "created_by_agent": "ai_orion"},
        {"_id": 0},
    )
    if not sow:
        return {"error": "No Orion SOW draft found for this opportunity"}

    section_order = ["executive_summary", "smart", "milestones", "assumptions_risks", "billing", "signature"]
    section_titles = {
        "executive_summary": "Executive Summary",
        "smart": "SMART Proposal",
        "milestones": "Detailed Statement of Work",
        "assumptions_risks": "Assumptions & Risks",
        "billing": "Billing Details",
        "signature": "Acceptance & Signature",
    }
    raw_sections = sow.get("sections", {})
    sections_list = []

    for sid in section_order:
        sec_data = raw_sections.get(sid, {})
        sections_list.append({
            "section_id": sid,
            "title": section_titles.get(sid, sid),
            "content": sec_data.get("content", ""),
            "status": sec_data.get("status", "pending"),
        })

    all_approved = all(s["status"] == "approved" for s in sections_list if s["content"])

    # Create the artifact directly so it renders in chat
    import uuid
    from datetime import datetime, timezone
    artifact_id = f"art_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    artifact_payload = {
        "opportunity_id": opportunity_id,
        "sow_id": sow.get("sow_id"),
        "client_name": sow.get("client_name", ""),
        "version": sow.get("version", 1),
        "sections": sections_list,
        "all_sections_approved": all_approved,
        "ready_to_finalize": all_approved,
    }
    await db.ai_chat_artifacts.insert_one({
        "artifact_id": artifact_id,
        "session_id": session_id,
        "user_id": user.user_id,
        "employee_id": employee_id or "ai_orion",
        "artifact_type": "sow_draft",
        "payload": artifact_payload,
        "status": "delivered",
        "created_by_agent": "ai_orion",
        "created_at": now,
        "updated_at": now,
    })

    return {
        "artifact_id": artifact_id,
        "artifact_type": "sow_draft",
        "sections_generated": len([s for s in sections_list if s["content"]]),
        "all_sections_approved": all_approved,
    }


async def handle_finalize_sow_to_google_docs(args, db, user, session_id=None, employee_id=None):
    """Finalize SOW to Google Docs via the existing google_docs_service."""
    opportunity_id = args.get("opportunity_id")
    if not opportunity_id:
        return {"error": "No opportunity_id provided"}

    sow = await db.opportunity_sows.find_one(
        {"opportunity_id": opportunity_id, "created_by_agent": "ai_orion"},
        {"_id": 0},
    )
    if not sow:
        return {"error": "No Orion SOW draft found for this opportunity"}

    try:
        from services.google_docs_service import create_sow_google_doc_user
        from routes.orion import _build_flat_sow, _build_flat_opportunity

        # Load opportunity data
        vega_session = await db.vega_discovery_sessions.find_one(
            {"session_id": opportunity_id}, {"_id": 0}
        )

        # Convert sections-based SOW into the flat format Google Docs expects
        flat_sow = _build_flat_sow(sow)
        flat_opp = _build_flat_opportunity(vega_session, sow)

        sections = sow.get("sections", {})
        assumptions_risks = sections.get("assumptions_risks", {}).get("content", "")

        doc_result = await create_sow_google_doc_user(
            db,
            user_id=user.user_id,
            sow=flat_sow,
            opportunity=flat_opp,
            assumptions_risks=assumptions_risks,
        )
        now = datetime.now(timezone.utc).isoformat()
        doc_url = doc_result.get("doc_url") or doc_result.get("url")
        doc_id = doc_result.get("doc_id") or doc_result.get("id")
        # Shared substrate. Match the field names and status value
        # used by the fireflies and orion finalize routes so a SOW
        # finalized through any path lands on the same SOW row shape
        # the frontend SOWStatusBar can render. See
        # docs/sales-ops/SHARED_SOW_BUILDER_SUBSTRATE.md.
        await db.opportunity_sows.update_one(
            {"opportunity_id": opportunity_id, "created_by_agent": "ai_orion"},
            {"$set": {
                "google_doc_url": doc_url,
                "google_doc_id": doc_id,
                "status": "ready_for_review",
                "updated_at": now,
            }},
        )
        return {"doc_url": doc_url, "doc_id": doc_id}
    except Exception as e:
        logger.error("Google Docs finalization failed: %s", e)
        return {"error": f"Failed to create Google Doc: {str(e)[:200]}"}

"""Fireflies.ai transcript routes, guide validation, and SOW finalization.

Provides endpoints to:
  1. List available meeting transcripts from Fireflies.ai
  2. Upload / list / search best-practice guides (knowledge base)
  3. Validate transcripts against guides (gap checklist)
  4. Generate a SMART proposal from selected transcripts using AI
  5. Generate milestones/tasks from selected transcripts using AI
  6. Extract billing details from transcripts
  7. Finalize SOW — generate Google Doc and send to client
"""

import os
import json
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field
from typing import Optional
from database import get_db
from auth_helper import get_current_user, has_perm
from services.fireflies_service import (
    FIREFLIES_ERR_API,
    FIREFLIES_ERR_TRANSCRIPT_EMPTY,
    FirefliesError,
    fetch_transcript_content,
    list_transcripts,
)
from services.knowledge_base import store_guide, search_guides, list_guides, delete_guide

router = APIRouter()
logger = logging.getLogger(__name__)


# SOW Drafter (LLM proxy) error codes. Kept here next to the route
# that consumes them so the frontend has a single source of truth for
# every structured detail this module emits. Mirrors the FIREFLIES_ERR_*
# pattern in services.fireflies_service.
SOW_DRAFTER_ERR_KEY_MISSING = "sow_drafter_api_key_missing"
SOW_DRAFTER_ERR_API = "sow_drafter_api_error"


def _sow_drafter_key_missing_error() -> HTTPException:
    """Structured 503 used when ANTHROPIC_API_KEY (or its configured
    SOW-drafter alias) is empty. Sent BEFORE any HTTP call so we never
    issue an ``Authorization: Bearer `` header — that header value is
    illegal and surfaces as ``illegal header value b'Bearer'`` deep in
    httpx, which is impossible for a user to act on."""
    return HTTPException(
        status_code=503,
        detail={
            "error_code": SOW_DRAFTER_ERR_KEY_MISSING,
            "message": (
                "SOW AI generation is not configured. Set ANTHROPIC_API_KEY "
                "or the configured SOW drafter API key in the backend environment."
            ),
        },
    )


def _sow_drafter_api_error() -> HTTPException:
    """Structured 502 used when the SOW drafter HTTP request fails or
    the upstream returns an unusable response. Keeps the wording
    deliberately generic so the user-facing toast doesn't leak the
    upstream URL, the exception class, or any token-shaped fragment
    of a failed Authorization header."""
    return HTTPException(
        status_code=502,
        detail={
            "error_code": SOW_DRAFTER_ERR_API,
            "message": "SOW AI generation failed. Try again in a moment or contact an admin.",
        },
    )


def _fireflies_http_error(err: FirefliesError) -> HTTPException:
    """Translate a FirefliesError into a structured HTTPException.

    The response body shape the frontend reads is::

        {"detail": {"error_code": "<code>", "message": "<human>"}}

    ``error_code`` is one of the FIREFLIES_ERR_* constants from
    ``services.fireflies_service``. ``message`` is suitable for direct
    toast/notification display and never contains the API key. Status
    codes:

      * ``fireflies_api_key_missing`` -> 503 Service Unavailable
        (it's a config gap, not a 4xx the user can fix in-app)
      * ``transcript_not_found``      -> 404
      * ``transcript_empty``          -> 422 (request was valid, but
                                              the resource it points
                                              at has no usable content)
      * ``fireflies_api_error``       -> 502 (upstream failure)
    """
    code = err.code
    if code == "fireflies_api_key_missing":
        status = 503
    elif code == "transcript_not_found":
        status = 404
    elif code == "transcript_empty":
        status = 422
    else:
        status = 502
    return HTTPException(
        status_code=status,
        detail={"error_code": code, "message": err.message},
    )

# SOW Drafter model config — uses devs.ai as LLM proxy. Model and base
# URL are stable per deployment so reading at import is fine. The API
# key is read at call time via _get_sow_drafter_api_key() so tests can
# set os.environ before the call and so ops can rotate the key without
# restarting the process. Module-import-time capture (the previous
# pattern) was also the root cause of the "illegal header value
# b'Bearer'" failure — an empty captured value produced an
# Authorization header with just "Bearer " and no token.
SOW_DRAFTER_MODEL = os.environ.get("SOW_DRAFTER_MODEL", "anthropic-claude-4-5-sonnet")
SOW_DRAFTER_BASE_URL = os.environ.get("SOW_DRAFTER_BASE_URL", "https://devs.ai/api/v1")


def _get_sow_drafter_api_key() -> str:
    """Read the SOW drafter API key from environment at call time.

    Read order:
      1. ``SOW_DRAFTER_API_KEY`` — explicit override, takes priority so
         a deployment that runs separate keys for general Anthropic
         usage vs. the SOW drafter can configure them independently.
      2. ``ANTHROPIC_API_KEY`` — the documented default in
         ``.env.example``; preserved for backwards compatibility with
         existing setups.
    Empty string when neither is set.
    """
    return (
        os.environ.get("SOW_DRAFTER_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or ""
    )


# ──────────────────────────────────────────────
#  SYSTEM PROMPTS
# ──────────────────────────────────────────────

SMART_SYSTEM_PROMPT = """You are a professional B2B solutions architect drafting a Statement of Work.

Given meeting transcript(s) between a service provider and a client, generate an executive summary and a SMART proposal framework.

You must return a JSON object with exactly these 6 keys:

- executive_summary: A professional executive summary for the SOW document. This should be a high-level overview (3-5 sentences) that frames the engagement: who the client is, the business challenge or opportunity being addressed, the proposed solution at a strategic level, the expected business impact, and the overall engagement approach. Write it as a polished, client-facing paragraph — not a list of deliverables.
- smart_specific: What exactly will be delivered? Be concrete about deliverables, scope, and technical details discussed.
- smart_measurable: How will success be measured? Include KPIs, metrics, acceptance criteria, and quantifiable outcomes mentioned.
- smart_achievable: What resources, skills, and capabilities are needed? Reference team composition, tools, and dependencies discussed.
- smart_relevant: Why does this matter to the client? Connect to their business goals, pain points, and strategic objectives mentioned.
- smart_time_bound: What are the key deadlines and milestones? Include phases, timeline expectations, and delivery dates discussed.

Guidelines:
- Write in professional, concise B2B language
- The executive_summary should read as a standalone paragraph suitable for C-level stakeholders
- Each SMART field should be 2-4 sentences
- Extract real details from the transcripts — do not fabricate
- If a SMART dimension was not discussed, note what was discussed and suggest reasonable defaults
- Focus on actionable, contract-ready language

Return ONLY valid JSON, no markdown fences or extra text."""


TRANSCRIPT_VALIDATION_PROMPT = """You are a senior solutions architect reviewing meeting transcripts against a best-practice guide.

Your job is to compare meeting transcript(s) against the provided best-practice checklist and identify:
1. Which checklist items were adequately covered in the conversation
2. Which items were partially discussed but need more detail
3. Which items were completely missed

You must return a JSON object with exactly these keys:
- covered: An array of objects with {item, evidence} — checklist items that were adequately discussed, with a brief quote or reference from the transcript
- partial: An array of objects with {item, detail_needed} — items that were mentioned but need more information
- missing: An array of objects with {item, why_important} — items not discussed at all, with a note on why they matter for the SOW
- overall_score: An integer from 0 to 100 representing how well the transcript covers the guide
- summary: A 2-3 sentence overall assessment for the sales engineer

Guidelines:
- Be specific — reference what was actually said (or not said)
- Be practical — focus on gaps that would actually affect SOW quality
- Do not fabricate transcript content
- If the guide mentions items clearly irrelevant to this project type, mark them as covered with a note

Return ONLY valid JSON, no markdown fences or extra text."""


MILESTONES_SYSTEM_PROMPT = """You are a professional B2B solutions architect building a detailed Statement of Work.

Given meeting transcript(s) between a service provider and a client, create a structured project plan with milestones and tasks.

You must return a JSON object with exactly these keys:
- suggested_completion_date: An ISO date string (YYYY-MM-DD) for the estimated project completion. If a completion date was discussed in the transcript, use it. Otherwise estimate a realistic date from today ({today}).
- milestones: An array of milestone objects. Create as many milestones as the project scope requires.

Each milestone object must have:
- milestone_name: A clear, descriptive phase name (e.g. "Discovery & Requirements Gathering")
- bullets: An array of 2-6 task objects

Each task object must have:
- bullet_text: A clear description of the task/activity
- estimated_hours: A realistic integer number of hours for this task
- deliverable: A tangible, client-facing output (e.g. "Requirements document", "Test report", "Deployed staging environment")

Guidelines:
- Create milestones in logical project phases (Discovery, Design, Build, Test, Deploy, etc.)
- Each milestone should have 2-6 tasks — not fewer, not more
- Estimated hours must be realistic integers — no decimals, no zeros
- Deliverables should be tangible outputs the client can review or receive
- Extract real scope details from the transcripts — do not fabricate
- Order milestones chronologically as they would be executed
- If specific technologies, tools, or systems were discussed, reference them in task descriptions

Do not include any text outside the JSON object."""


BILLING_SYSTEM_PROMPT = """You are a professional B2B solutions architect reviewing meeting transcripts for billing-related details.

Given meeting transcript(s) between a service provider and a client, extract any billing arrangements, payment terms, and authorized user details that were discussed.

You must return a JSON object with exactly these keys:
- payment_terms: One of "net_7", "net_14", "net_30", "net_45", "net_60", or "" if not discussed
- billing_terms: One of "weekly", "biweekly", "monthly", or "" if not discussed
- special_billing_instructions: Any billing constraints, PO requirements, invoice format preferences, or special billing arrangements discussed. Empty string if none.
- boa_authorized_users: An array of objects with {name, email, phone, max_hours_allowed} if BOA/authorized users were discussed. Each field should be a string except max_hours_allowed which is a number or null. Return an empty array if not discussed.

Guidelines:
- Extract only what was actually discussed — do not fabricate or assume
- For payment_terms, only use the exact values listed above
- For billing_terms, only use the exact values listed above
- If something was vaguely mentioned but not confirmed, note it in special_billing_instructions rather than setting a term
- For authorized users, include all identifying details mentioned (name, email, phone, hour limits)

Return ONLY valid JSON, no markdown fences or extra text."""


# ──────────────────────────────────────────────
#  SHARED LLM CALL HELPER
# ──────────────────────────────────────────────

async def _call_sow_drafter(system_prompt: str, user_message: str, parse_json: bool = True):
    """Call the SOW Drafter model and return the response.

    Args:
        parse_json: If True, parse response as JSON (default). If False, return raw text.

    Validates the SOW drafter API key BEFORE issuing the HTTP request.
    An empty key was producing ``Authorization: Bearer `` (with a
    trailing space and no token), which httpx rejects with
    ``illegal header value b'Bearer'`` — an opaque, hard-to-diagnose
    error surface for the user. Now an empty key short-circuits with a
    structured 503 + ``sow_drafter_api_key_missing`` so the frontend
    can render an actionable toast.

    Raises HTTPException with structured detail = {error_code, message}
    on every failure mode: config gap, upstream HTTP failure, or
    unparseable response.
    """
    import httpx

    api_key = _get_sow_drafter_api_key()
    if not api_key:
        # Surfaced as 503 BEFORE the network call so we never emit a
        # malformed Authorization header.
        raise _sow_drafter_key_missing_error()

    ai_text = None
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            resp = await client.post(
                f"{SOW_DRAFTER_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": SOW_DRAFTER_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
    except Exception as e:
        # Log the exception type only — the message could include the
        # echoed Authorization header on certain failure modes.
        logger.error("SOW Drafter API call failed: %s", type(e).__name__)
        raise _sow_drafter_api_error()

    # Parse the API response
    try:
        result = resp.json()
    except Exception:
        result = resp.text

    # Extract AI text from whatever format we got
    if isinstance(result, dict) and "choices" in result:
        ai_text = result["choices"][0]["message"]["content"]
    elif isinstance(result, dict) and "content" in result:
        ai_text = result["content"]
    elif isinstance(result, dict) and "response" in result:
        ai_text = result["response"]
    elif isinstance(result, str):
        ai_text = result
    else:
        logger.error("Unexpected SOW Drafter response format: %s", str(result)[:500])
        raise _sow_drafter_api_error()

    # Return raw text if JSON parsing not needed
    if not parse_json:
        return ai_text

    # Strip markdown fences and parse JSON
    try:
        clean = ai_text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        logger.error("Failed to parse SOW Drafter response as JSON: %s", ai_text[:500])
        raise HTTPException(status_code=500, detail="AI returned invalid format. Please try again.")


async def _fetch_and_combine_transcripts(transcript_ids: list[str]) -> str:
    """Fetch transcripts from Fireflies and combine into a single context string.

    Raises a structured HTTPException on every failure mode the
    frontend needs to branch on. ``transcript_empty`` covers the case
    where Fireflies returned transcript objects but each one had zero
    usable sentences (e.g. a meeting that was never transcribed).
    Without this guard the AI call burns tokens on whitespace.
    """
    try:
        transcripts = await fetch_transcript_content(transcript_ids)
    except FirefliesError as err:
        raise _fireflies_http_error(err)

    combined_parts = []
    for t in transcripts:
        combined_parts.append(f"=== Meeting: {t['title']} ===\n{t['content']}")
    combined = "\n\n".join(combined_parts)

    # Normalize common brand misspellings from transcription
    import re
    combined = re.sub(r'(?i)simply\s*meta', 'SimpliMeta', combined)

    # Transcript objects came back, but the sentences were empty — the
    # combined context has only "=== Meeting: ... ===" headers and
    # newlines. Bail out before paying for an AI call on empty input.
    stripped_for_check = re.sub(r"=== Meeting: [^=]*===", "", combined).strip()
    if not stripped_for_check:
        raise _fireflies_http_error(
            FirefliesError(
                FIREFLIES_ERR_TRANSCRIPT_EMPTY,
                "Selected transcript(s) contain no sentences. Pick a different meeting.",
            )
        )

    # Truncate if extremely long
    if len(combined) > 100_000:
        combined = combined[:100_000] + "\n\n[...transcript truncated for length]"

    return combined


# ──────────────────────────────────────────────
#  REQUEST MODELS
# ──────────────────────────────────────────────

class GenerateSMARTRequest(BaseModel):
    transcript_ids: list[str] = Field(..., min_length=1, max_length=10)
    opportunity_id: str
    lob: str


class GenerateMilestonesRequest(BaseModel):
    transcript_ids: list[str] = Field(..., min_length=1, max_length=10)
    opportunity_id: str
    lob: str


class GenerateBillingRequest(BaseModel):
    transcript_ids: list[str] = Field(..., min_length=1, max_length=10)
    opportunity_id: str
    lob: str


class ValidateTranscriptsRequest(BaseModel):
    transcript_ids: list[str] = Field(..., min_length=1, max_length=10)
    guide_id: Optional[str] = None  # Specific guide to validate against


# ──────────────────────────────────────────────
#  ENDPOINTS
# ──────────────────────────────────────────────

@router.get("/fireflies/transcripts")
async def get_fireflies_transcripts(
    current_user=Depends(get_current_user),
):
    """List available Fireflies.ai meeting transcripts.

    Returns ``{items, count}`` on success. Failure responses carry a
    structured ``detail = {error_code, message}`` so the frontend can
    branch on ``error_code`` (e.g. show a config-gap banner when the
    code is ``fireflies_api_key_missing`` rather than a generic toast).
    """
    if not has_perm(current_user, "sales_ops.solutions_design.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    try:
        transcripts = await list_transcripts(limit=50)
        return {"items": transcripts, "count": len(transcripts)}
    except FirefliesError as err:
        raise _fireflies_http_error(err)
    except Exception as e:
        logger.error("Unexpected error fetching Fireflies transcripts: %s", type(e).__name__)
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": FIREFLIES_ERR_API,
                "message": "Failed to fetch transcripts from Fireflies.ai",
            },
        )


# ──────────────────────────────────────────────
#  KNOWLEDGE BASE — GUIDE MANAGEMENT
# ──────────────────────────────────────────────

@router.post("/guides/upload")
async def upload_guide(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    current_user=Depends(get_current_user),
):
    """Upload a best-practice guide (PDF, DOCX, TXT) to the knowledge base.

    The guide is parsed, chunked, embedded, and stored in ChromaDB
    for semantic search during transcript validation.
    """
    if not has_perm(current_user, "sales_ops.solutions_design.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt"}
    MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB

    import os as _os
    file_ext = _os.path.splitext(file.filename or "")[1].lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{file_ext}' not supported. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 20 MB.")

    try:
        result = await store_guide(
            file_bytes=contents,
            filename=file.filename,
            uploaded_by=current_user.user_id,
            title=title,
        )
        return {"message": "Guide uploaded successfully", **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to store guide: {e}")
        raise HTTPException(status_code=500, detail="Failed to process and store guide")


@router.get("/guides")
async def get_guides(
    current_user=Depends(get_current_user),
):
    """List all best-practice guides in the knowledge base."""
    if not has_perm(current_user, "sales_ops.solutions_design.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    try:
        guides = await list_guides()
        return {"items": guides, "count": len(guides)}
    except Exception as e:
        logger.error(f"Failed to list guides: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve guides")


@router.delete("/guides/{guide_id}")
async def remove_guide(
    guide_id: str,
    current_user=Depends(get_current_user),
):
    """Delete a guide from the knowledge base."""
    if not has_perm(current_user, "sales_ops.solutions_design.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    deleted = await delete_guide(guide_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Guide not found")
    return {"message": "Guide deleted successfully"}


# ──────────────────────────────────────────────
#  TRANSCRIPT VALIDATION AGAINST GUIDES
# ──────────────────────────────────────────────

@router.post("/fireflies/validate-transcripts")
async def validate_transcripts(
    req: ValidateTranscriptsRequest,
    current_user=Depends(get_current_user),
):
    """Validate selected transcripts against best-practice guides.

    Flow:
      1. Fetch transcript content from Fireflies
      2. Search ChromaDB for relevant guides (or use a specific guide_id)
      3. Run AI validation comparing transcripts vs guide checklist
      4. Return a gap checklist for the engineer to review
    """
    if not has_perm(current_user, "sales_ops.solutions_design.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Step 1: Fetch transcript content
    combined_context = await _fetch_and_combine_transcripts(req.transcript_ids)

    # Step 2: Find relevant guide content
    guide_content = ""
    guide_title = ""

    if req.guide_id:
        # Fetch specific guide chunks from ChromaDB
        from services.knowledge_base import _get_client, _get_collection
        client = _get_client()
        collection = _get_collection(client)
        results = collection.get(
            where={"guide_id": req.guide_id},
            include=["documents", "metadatas"],
        )
        if results["documents"]:
            guide_content = "\n\n".join(results["documents"])
            guide_title = results["metadatas"][0].get("title", "Uploaded Guide") if results["metadatas"] else ""
    else:
        # Semantic search for relevant guides based on transcript content
        # Use a summary of the transcript as the query (first 2000 chars)
        query_text = combined_context[:2000]
        matched_guides = await search_guides(query_text, n_results=5)
        if matched_guides:
            # Combine the top guide's chunks
            top_guide = matched_guides[0]
            guide_content = "\n\n".join(top_guide["chunks"])
            guide_title = top_guide["title"]

    # If no guide found, return early with a "no guide" response
    if not guide_content:
        return {
            "has_guide": False,
            "guide_title": None,
            "checklist": None,
            "message": "No best-practice guide found. You can upload one or proceed without validation.",
        }

    # Step 3: AI validation — compare transcripts against guide
    user_message = f"""Compare the following meeting transcript(s) against the best-practice guide and produce a validation checklist.

BEST PRACTICE GUIDE: "{guide_title}"
---
{guide_content}
---

MEETING TRANSCRIPT(S):
---
{combined_context}
---

Analyze how well the meeting covered the topics in the guide. Return a JSON checklist."""

    checklist = await _call_sow_drafter(TRANSCRIPT_VALIDATION_PROMPT, user_message)

    # Normalize the response
    for key in ["covered", "partial", "missing"]:
        if key not in checklist or not isinstance(checklist[key], list):
            checklist[key] = []
    if "overall_score" not in checklist:
        checklist["overall_score"] = 0
    if "summary" not in checklist:
        checklist["summary"] = ""

    return {
        "has_guide": True,
        "guide_title": guide_title,
        "checklist": checklist,
    }


@router.post("/fireflies/generate-smart")
async def generate_smart_from_transcripts(
    req: GenerateSMARTRequest,
    current_user=Depends(get_current_user),
):
    """Fetch selected transcripts and generate SMART proposal via AI."""
    if not has_perm(current_user, "sales_ops.solutions_design.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if not req.transcript_ids:
        raise HTTPException(status_code=400, detail="No transcripts selected")

    combined_context = await _fetch_and_combine_transcripts(req.transcript_ids)

    user_message = f"""Based on the following meeting transcript(s), generate a SMART proposal framework.

TRANSCRIPTS:
{combined_context}

Return a JSON object with keys: executive_summary, smart_specific, smart_measurable, smart_achievable, smart_relevant, smart_time_bound"""

    smart_data = await _call_sow_drafter(SMART_SYSTEM_PROMPT, user_message)

    # Validate expected fields
    for key in ["executive_summary", "smart_specific", "smart_measurable", "smart_achievable", "smart_relevant", "smart_time_bound"]:
        if key not in smart_data:
            smart_data[key] = ""

    return {
        "smart": smart_data,
        "transcripts_used": len(req.transcript_ids),
    }


@router.post("/fireflies/generate-milestones")
async def generate_milestones_from_transcripts(
    req: GenerateMilestonesRequest,
    current_user=Depends(get_current_user),
):
    """Fetch selected transcripts and generate milestones/tasks via AI."""
    if not has_perm(current_user, "sales_ops.solutions_design.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if not req.transcript_ids:
        raise HTTPException(status_code=400, detail="No transcripts selected")

    combined_context = await _fetch_and_combine_transcripts(req.transcript_ids)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt = MILESTONES_SYSTEM_PROMPT.replace("{today}", today)

    user_message = f"""Based on the following meeting transcript(s), create a detailed project plan with milestones and tasks.

TRANSCRIPTS:
{combined_context}

Return a JSON object with keys: suggested_completion_date, milestones"""

    result = await _call_sow_drafter(prompt, user_message)

    # Validate and normalize milestones
    milestones = result.get("milestones", [])
    normalized = []
    for i, ms in enumerate(milestones):
        bullets = []
        for j, task in enumerate(ms.get("bullets", [])):
            bullets.append({
                "bullet_id": f"bullet_{int(datetime.now(timezone.utc).timestamp() * 1000)}_{i}_{j}",
                "bullet_text": task.get("bullet_text", ""),
                "estimated_hours": int(task.get("estimated_hours", 0)) or 1,
                "deliverable": task.get("deliverable", ""),
                "order": j,
            })

        normalized.append({
            "milestone_id": f"ms_{int(datetime.now(timezone.utc).timestamp() * 1000)}_{i}",
            "milestone_name": ms.get("milestone_name", f"Phase {i + 1}"),
            "order": i,
            "bullets": bullets,
        })

    return {
        "milestones": normalized,
        "suggested_completion_date": result.get("suggested_completion_date", ""),
        "transcripts_used": len(req.transcript_ids),
    }


@router.post("/fireflies/generate-billing")
async def generate_billing_from_transcripts(
    req: GenerateBillingRequest,
    current_user=Depends(get_current_user),
):
    """Fetch selected transcripts and extract billing details via AI."""
    if not has_perm(current_user, "sales_ops.solutions_design.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    if not req.transcript_ids:
        raise HTTPException(status_code=400, detail="No transcripts selected")

    combined_context = await _fetch_and_combine_transcripts(req.transcript_ids)

    user_message = f"""Based on the following meeting transcript(s), extract any billing-related details that were discussed.

TRANSCRIPTS:
{combined_context}

Return a JSON object with keys: payment_terms, billing_terms, special_billing_instructions, boa_authorized_users"""

    billing_data = await _call_sow_drafter(BILLING_SYSTEM_PROMPT, user_message)

    # Validate and normalize fields
    valid_payment = {"net_7", "net_14", "net_30", "net_45", "net_60", ""}
    valid_billing = {"weekly", "biweekly", "monthly", ""}

    if billing_data.get("payment_terms", "") not in valid_payment:
        billing_data["payment_terms"] = ""
    if billing_data.get("billing_terms", "") not in valid_billing:
        billing_data["billing_terms"] = ""
    if "special_billing_instructions" not in billing_data:
        billing_data["special_billing_instructions"] = ""
    if not isinstance(billing_data.get("boa_authorized_users"), list):
        billing_data["boa_authorized_users"] = []

    return {
        "billing": billing_data,
        "transcripts_used": len(req.transcript_ids),
    }


@router.get("/billing-prefill/{opportunity_id}")
async def get_billing_prefill(
    opportunity_id: str,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Pre-fill billing contact info from the opportunity's account and primary contact."""
    if not has_perm(current_user, "sales_ops.solutions_design.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Look up the opportunity
    opportunity = await db.opportunities.find_one(
        {"opportunity_id": opportunity_id}, {"_id": 0}
    )
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    account_id = opportunity.get("account_id")
    if not account_id:
        raise HTTPException(status_code=404, detail="No account linked to this opportunity")

    # Look up the account
    account = await db.accounts.find_one(
        {"account_id": account_id}, {"_id": 0}
    )
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    billing_email = account.get("email", "")
    billing_phone = account.get("phone", "")
    contact_name = ""
    contact_title = ""

    # Try to find the primary contact for this account
    primary_contact = await db.contacts.find_one(
        {"account_id": account_id, "is_primary": True}, {"_id": 0}
    )

    if primary_contact:
        billing_email = primary_contact.get("email") or billing_email
        billing_phone = primary_contact.get("phone") or billing_phone
        contact_name = primary_contact.get("name", "")
        contact_title = primary_contact.get("title", "")

    return {
        "billing_account_email": billing_email,
        "billing_account_phone": billing_phone,
        "contact_name": contact_name,
        "contact_title": contact_title,
    }


# ──────────────────────────────────────────────
#  SOW FINALIZATION
# ──────────────────────────────────────────────

ASSUMPTIONS_RISKS_PROMPT = """You are a professional B2B solutions architect writing the "Assumptions & Risks" section of a Statement of Work.

Given the SMART proposal and milestone details below, generate a clear and professional Assumptions & Risks section.

Format your response with these exact section headers and bullet points:

## Assumptions
- [Assumption 1]
- [Assumption 2]
(list 4-6 assumptions about client availability, access, approvals, resources, and technical dependencies)

## Risks
- **[Risk Title]:** [Description and impact]. **Mitigation:** [How to address it]
- **[Risk Title]:** [Description and impact]. **Mitigation:** [How to address it]
(list 3-5 risks covering schedule, scope, and dependency risks)

Guidelines:
- Write in professional, contract-ready language
- Be specific to the project scope described
- Do not fabricate — base assumptions on the actual scope
- Use the exact format above with ## headers and - bullet points
- Use **bold** for risk titles and the word Mitigation"""


class FinalizeSOWRequest(BaseModel):
    opportunity_id: str
    lob: str


@router.post("/sow/finalize")
async def finalize_sow(
    req: FinalizeSOWRequest,
    force: bool = False,
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Finalize SOW: generate Assumptions & Risks, create Google Doc, share with client.

    Flow:
      1. Load the SOW and opportunity data
      2. AI-generate Assumptions & Risks section
      3. Create a formatted Google Doc
      4. Share the doc with the billing contact
      5. Update SOW record with doc URL and status

    Duplicate prevention: when the loaded SOW already carries a
    non-empty google_doc_url, the route short-circuits with a
    no-op response carrying duplicate_prevented=True so the
    frontend can open the existing artifact rather than creating
    a second doc. Pass force=true on the query string to bypass.
    See docs/sales-ops/GOOGLE_DOC_ARTIFACT_PATH.md.
    """
    if not has_perm(current_user, "sales_ops.solutions_design.create"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Load SOW
    sow = await db.opportunity_sows.find_one(
        {"opportunity_id": req.opportunity_id, "line_of_business": req.lob},
        {"_id": 0}
    )
    if not sow:
        raise HTTPException(status_code=404, detail="SOW not found")

    # Duplicate prevention. A SOW that already has a Google Doc URL
    # short-circuits with no Drive API call. The frontend opens the
    # existing artifact rather than creating a second doc. force=true
    # bypasses. See docs/sales-ops/GOOGLE_DOC_ARTIFACT_PATH.md.
    existing_doc_url = (sow.get("google_doc_url") or "").strip()
    if existing_doc_url and not force:
        try:
            from services.audit_service import log_action as _audit_log_action
            await _audit_log_action(
                action="sales_ops.sow.google_doc_export_noop",
                actor_id=getattr(current_user, "user_id", None),
                actor_email=getattr(current_user, "email", "unknown"),
                resource_type="opportunity_sow",
                resource_id=sow.get("sow_id"),
                details={
                    "opportunity_id": req.opportunity_id,
                    "line_of_business": req.lob,
                    "reason": "duplicate_prevented",
                },
            )
        except Exception as _audit_err:  # noqa: BLE001
            logger.warning(
                "Audit row for SOW Google Doc no-op failed: %s", _audit_err
            )
        return {
            "google_doc_url": existing_doc_url,
            "google_doc_id": sow.get("google_doc_id"),
            "status": sow.get("status") or "ready_for_review",
            "duplicate_prevented": True,
            "no_op": True,
        }

    # Folder configuration check. Resolves both env vars without
    # exposing the value. When neither variable is set, surface the
    # canonical structured envelope so the frontend can route the
    # user to the escape hatch.
    from services.google_docs_service import (
        GoogleDocsFolderNotConfigured,
        GoogleDocsFolderNotFound,
        GoogleDocsPermissionDenied,
        is_sow_folder_configured,
    )
    if not is_sow_folder_configured():
        raise HTTPException(
            status_code=400,
            detail={
                "code": "google_docs_folder_not_configured",
                "message": (
                    "Google Docs export is not configured. Ask an admin to "
                    "set SIMPLIMETA_SOW_FOLDER_ID and verify Drive folder "
                    "access."
                ),
                "next_step": (
                    "Paste an existing Google Doc URL on the proposal as "
                    "the escape hatch."
                ),
            },
        )

    # Field Services readiness gate. The /sow/finalize route is the
    # user-facing Generate & Send path so the readiness check belongs
    # here as well as on the change_sow_status route. Same helper, same
    # contract, same error envelope. See
    # docs/sales-ops/SHARED_SOW_BUILDER_SUBSTRATE.md.
    from routes.solutions_design import _evaluate_field_services_readiness
    fs_readiness = _evaluate_field_services_readiness(sow)
    if not fs_readiness["ready"]:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "field_services_sow_incomplete",
                "message": (
                    "Field Services SOW is missing required dispatch "
                    "information. Add the service location and the "
                    "required contact roles before generating the "
                    "Google Doc."
                ),
                "missing": fs_readiness["missing"],
            },
        )

    # Load opportunity
    opportunity = await db.opportunities.find_one(
        {"opportunity_id": req.opportunity_id}, {"_id": 0}
    )
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    # Step 1: AI-generate Assumptions & Risks
    smart_summary = "\n".join([
        f"Specific: {sow.get('smart_specific', '')}",
        f"Measurable: {sow.get('smart_measurable', '')}",
        f"Achievable: {sow.get('smart_achievable', '')}",
        f"Relevant: {sow.get('smart_relevant', '')}",
        f"Time-Bound: {sow.get('smart_time_bound', '')}",
    ])

    milestones_summary = ""
    for ms in sow.get("milestones", []):
        milestones_summary += f"\nMilestone: {ms.get('milestone_name', '')}\n"
        for b in ms.get("bullets", []):
            milestones_summary += f"  - {b.get('bullet_text', '')} ({b.get('estimated_hours', 0)}h)\n"

    user_message = f"""Generate an Assumptions & Risks section for this SOW:

SMART PROPOSAL:
{smart_summary}

MILESTONES:
{milestones_summary}

CLIENT: {opportunity.get('account_name', 'Client')}"""

    try:
        assumptions_risks = await _call_sow_drafter(
            ASSUMPTIONS_RISKS_PROMPT, user_message, parse_json=False
        )
    except Exception as e:
        logger.warning(f"Failed to generate assumptions/risks: {e}")
        assumptions_risks = "[Assumptions & Risks to be completed]"

    # Step 2: Create Google Doc using the user's own Google OAuth token
    from services.google_docs_service import create_sow_google_doc_user

    try:
        doc_result = await create_sow_google_doc_user(
            db=db,
            user_id=current_user.user_id,
            sow=sow,
            opportunity=opportunity,
            assumptions_risks=assumptions_risks,
        )
    except GoogleDocsFolderNotConfigured:
        # Should not reach here because is_sow_folder_configured()
        # already gated the route, but the service helper may also
        # raise from inside the create function if env was unset
        # between the gate and the call. Same envelope as the gate.
        raise HTTPException(
            status_code=400,
            detail={
                "code": "google_docs_folder_not_configured",
                "message": (
                    "Google Docs export is not configured. Ask an admin to "
                    "set SIMPLIMETA_SOW_FOLDER_ID and verify Drive folder "
                    "access."
                ),
                "next_step": (
                    "Paste an existing Google Doc URL on the proposal as "
                    "the escape hatch."
                ),
            },
        )
    except GoogleDocsPermissionDenied as e:
        logger.error("Drive permission denied on SOW finalize: %s", e)
        raise HTTPException(
            status_code=502,
            detail={
                "code": "google_docs_permission_denied",
                "message": (
                    "AEGISAXIOM does not have access to the configured Drive "
                    "folder. Ask an admin to share it."
                ),
            },
        )
    except GoogleDocsFolderNotFound as e:
        logger.error("Drive folder not found on SOW finalize: %s", e)
        raise HTTPException(
            status_code=502,
            detail={
                "code": "google_docs_folder_not_found",
                "message": (
                    "Google Docs export failed. The configured Drive folder "
                    "was not found."
                ),
            },
        )
    except FileNotFoundError as e:
        # Missing service account file or missing Google OAuth tokens
        # for the user. The route returns a structured error so the
        # frontend can offer the external Google Doc attach as an
        # escape hatch instead of showing a generic crash. The raw
        # exception message can include a file system path; keep it
        # out of the response and log it server side only.
        logger.error(
            "Google Docs not connected for user %s: %s",
            current_user.user_id,
            e,
        )
        raise HTTPException(
            status_code=400,
            detail={
                "code": "google_docs_not_configured",
                "message": (
                    "Google Docs export is not configured for this "
                    "deployment or this user has not connected Google. "
                    "Paste a Google Doc URL on the proposal as the "
                    "escape hatch."
                ),
            },
        )
    except Exception as e:
        # Catch-all that hides the raw exception text from clients.
        # Server-side log keeps the detail for debugging. An audit
        # row records the failure so the activity feed retains the
        # attempt even when no doc was written.
        logger.error("Failed to create Google Doc: %s", e)
        try:
            from services.audit_service import log_action as _audit_log_action
            await _audit_log_action(
                action="sales_ops.sow.google_doc_export_failed",
                actor_id=getattr(current_user, "user_id", None),
                actor_email=getattr(current_user, "email", "unknown"),
                resource_type="opportunity_sow",
                resource_id=sow.get("sow_id"),
                details={
                    "opportunity_id": req.opportunity_id,
                    "line_of_business": req.lob,
                },
            )
        except Exception as _audit_err:  # noqa: BLE001
            logger.warning(
                "Audit row for SOW Google Doc export failure failed: %s",
                _audit_err,
            )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "google_docs_export_failed",
                "message": (
                    "Google Docs export failed. Try again or paste a "
                    "Google Doc URL on the proposal as the escape "
                    "hatch."
                ),
            },
        )

    # Step 3: Update SOW record
    now = datetime.now(timezone.utc)
    await db.opportunity_sows.update_one(
        {"opportunity_id": req.opportunity_id, "line_of_business": req.lob},
        {"$set": {
            "google_doc_url": doc_result["doc_url"],
            "google_doc_id": doc_result["doc_id"],
            "status": "ready_for_review",
            "exported_at": now,
            "updated_at": now,
            "last_edited_by": current_user.user_id,
        }}
    )

    # No URL in the log. The doc id is a routable handle to a
    # Drive artifact; kept out of the log aggregator. The audit row
    # records has_google_doc_id and the response body carries the
    # url for the caller. See
    # docs/sales-ops/GOOGLE_DOC_ARTIFACT_PATH.md.
    logger.info(
        "SOW finalized for %s/%s by %s has_google_doc=%s",
        req.opportunity_id,
        req.lob,
        getattr(current_user, "email", "unknown"),
        bool(doc_result.get("doc_url")),
    )

    # Audit. A successful structured export is the moment the SOW
    # transitions from in-progress to ready-for-review with a Drive
    # artifact attached. Details carry the SOW context but never the
    # raw doc URL because the activity feed should not become a
    # secondary store of Google Drive references.
    try:
        from services.audit_service import log_action as _audit_log_action
        await _audit_log_action(
            action="sales_ops.sow.google_doc_exported",
            actor_id=getattr(current_user, "user_id", None),
            actor_email=getattr(current_user, "email", "unknown"),
            resource_type="opportunity_sow",
            resource_id=sow.get("sow_id"),
            details={
                "opportunity_id": req.opportunity_id,
                "line_of_business": req.lob,
                "forced": bool(force),
                "has_google_doc_id": bool(doc_result.get("doc_id")),
            },
        )
    except Exception as _audit_err:  # noqa: BLE001
        logger.warning(
            "Audit row for SOW Google Doc export success failed: %s",
            _audit_err,
        )

    return {
        "google_doc_url": doc_result["doc_url"],
        "google_doc_id": doc_result["doc_id"],
        "status": "ready_for_review",
        "duplicate_prevented": False,
        "no_op": False,
    }

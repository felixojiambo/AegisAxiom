"""Deterministic section regeneration for Professional Services SOWs.

The Sales Ops SOW Builder ships a section-level Regenerate action so a
user can refresh one prose section without regenerating the entire
proposal. This module composes the candidate text from the SOW's own
structured context — account name, executive summary, milestones,
totals, billing fields, contact roles — instead of calling an LLM.

That deliberate design choice keeps three rules from
``docs/sales-ops/PROCUREMENT_BOT_SCAFFOLD.md`` and the Wave 4 plan in
force without an external dependency:

* No invented numbers, dates, prices, hours, or commitments. The
  template only inserts values that already exist on the SOW or its
  opportunity. Missing values render as a neutral placeholder so the
  user can see what context is still missing.
* No new env vars or third-party service calls. The route runs in
  every deploy regardless of LLM availability.
* Cheap to test. Pure function returning a string + citation list, no
  network mock required.

When an LLM client lands later, this module can grow a second
generator that returns the same ``(candidate_content, citations)``
tuple shape, and the route stays unchanged.
"""
from __future__ import annotations

from typing import Any, Iterable

from services.proposal_generation import (
    PS_OPTIONAL_SECTION_KINDS,
    PS_REQUIRED_SECTION_KINDS,
)


# Tone hints the route accepts in ``user_instruction``. Matching is
# substring + lowercase so a free-form instruction like "make this
# punchier, more executive" still picks up "executive" and "shorter".
# The hints only adjust the template wrapping; they never inject
# arbitrary text from the instruction into the generated body (which
# would risk prompt-injection-style content sneaking into a client
# deliverable).
_TONE_EXECUTIVE = "executive"
_TONE_SHORTER = ("shorter", "shorten", "concise", "punchy")
_TONE_CLIENT_FACING = ("client", "client-facing", "external", "polished")
_TONE_TECHNICAL = ("technical", "detailed", "engineering")


def _coerce_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _normalize_instruction(user_instruction: Any) -> dict[str, bool]:
    """Pick tone flags out of the user instruction. Returns a dict the
    template can reference. The instruction itself never appears in
    the output text — only the boolean flags it set."""
    text = _coerce_str(user_instruction).lower()
    return {
        "executive": _TONE_EXECUTIVE in text,
        "shorter": any(tok in text for tok in _TONE_SHORTER),
        "client_facing": any(tok in text for tok in _TONE_CLIENT_FACING),
        "technical": any(tok in text for tok in _TONE_TECHNICAL),
    }


def _account_name(opportunity: dict | None) -> str:
    if not isinstance(opportunity, dict):
        return "the client"
    name = _coerce_str(opportunity.get("account_name")) or _coerce_str(
        opportunity.get("name")
    )
    return name or "the client"


def _engagement_label(opportunity: dict | None) -> str:
    if not isinstance(opportunity, dict):
        return "this engagement"
    name = _coerce_str(opportunity.get("name"))
    return name or "this engagement"


def _milestone_titles(sow: dict) -> list[str]:
    titles: list[str] = []
    for m in sow.get("milestones") or []:
        if isinstance(m, dict):
            title = _coerce_str(m.get("title"))
            if title:
                titles.append(title)
    return titles


def _existing_prose(sow: dict, kind: str) -> str:
    for entry in sow.get("prose_sections") or []:
        if isinstance(entry, dict) and entry.get("kind") == kind:
            return _coerce_str(entry.get("content"))
    return ""


def _bullets(items: Iterable[str]) -> str:
    return "\n".join(f"- {item}" for item in items if item)


def _smart_summary(sow: dict) -> str:
    pairs = [
        ("Specific", sow.get("smart_specific")),
        ("Measurable", sow.get("smart_measurable")),
        ("Achievable", sow.get("smart_achievable")),
        ("Relevant", sow.get("smart_relevant")),
        ("Time-bound", sow.get("smart_time_bound")),
    ]
    out = []
    for label, value in pairs:
        text = _coerce_str(value)
        if text:
            out.append(f"- **{label}:** {text}")
    return "\n".join(out)


def _join_milestones_sentence(titles: list[str]) -> str:
    if not titles:
        return "the planned engagement milestones"
    if len(titles) == 1:
        return titles[0]
    if len(titles) == 2:
        return f"{titles[0]} and {titles[1]}"
    return ", ".join(titles[:-1]) + f", and {titles[-1]}"


def _canonical_kinds() -> set[str]:
    """All canonical PS section kinds the route is allowed to regenerate.
    Kinds outside this set still regenerate, but they take the generic
    template at the bottom. This keeps the route open to future
    customer-specific kinds without a code change."""
    return {k for k, _ in PS_REQUIRED_SECTION_KINDS} | {
        k for k, _ in PS_OPTIONAL_SECTION_KINDS
    }


# ---- per-kind candidate composers -----------------------------------


def _build_executive_summary(sow: dict, opportunity: dict, tone: dict) -> tuple[str, list[str]]:
    account = _account_name(opportunity)
    engagement = _engagement_label(opportunity)
    titles = _milestone_titles(sow)
    existing = _existing_prose(sow, "executive_summary")
    parts: list[str] = []
    citations: list[str] = []

    lead = f"This engagement equips {account} with the outcomes captured in {engagement}."
    if existing:
        lead = (
            f"This engagement equips {account} with the outcomes already drafted for "
            f"{engagement} and refines them for the proposal narrative."
        )
        citations.append("executive_summary")
    parts.append(lead)

    if titles:
        parts.append(
            f"The work spans {_join_milestones_sentence(titles)}, sequenced so each "
            "milestone hands off to the next without disrupting business operations."
        )
        citations.append("milestones")

    smart = _smart_summary(sow)
    if smart and not tone["shorter"]:
        parts.append("Success criteria for the engagement:")
        parts.append(smart)
        citations.append("smart_objective")

    if tone["executive"] or tone["shorter"]:
        parts = parts[:2]
    return "\n\n".join(parts), citations


def _build_smart_objective(sow: dict, opportunity: dict, tone: dict) -> tuple[str, list[str]]:
    smart = _smart_summary(sow)
    citations: list[str] = []
    if smart:
        citations.append("smart_objective")
        return (
            "Engagement objective expressed in SMART form:\n\n" + smart,
            citations,
        )
    return (
        "_Add SMART objective values (Specific / Measurable / Achievable / "
        "Relevant / Time-bound) on the SOW so this section can summarize them._",
        citations,
    )


def _build_scope_of_work(sow: dict, opportunity: dict, tone: dict) -> tuple[str, list[str]]:
    titles = _milestone_titles(sow)
    citations: list[str] = []
    if not titles:
        return (
            "_Add milestones to the SOW so the Scope of Work narrative can "
            "summarize the planned phases._",
            citations,
        )
    citations.append("milestones")
    lines = ["The engagement is organized into the following milestones:", ""]
    lines.append(_bullets(titles))
    if not tone["shorter"]:
        lines.append("")
        lines.append(
            "Each milestone carries its own tasks and acceptance criteria in the "
            "structured Scope of Work table; this narrative summarizes the high-level "
            "sequence only."
        )
    return "\n".join(lines), citations


def _build_estimated_time(sow: dict, opportunity: dict, tone: dict) -> tuple[str, list[str]]:
    citations: list[str] = []
    start = _coerce_str(sow.get("start_date"))
    end = _coerce_str(sow.get("end_date"))
    expected = _coerce_str(sow.get("expected_completion_date"))
    lines: list[str] = []
    if start and end:
        citations.extend(["start_date", "end_date"])
        lines.append(f"The engagement runs from {start} through {end}.")
    elif expected:
        citations.append("expected_completion_date")
        lines.append(f"Target completion: {expected}.")
    else:
        lines.append(
            "_Fill in start/end dates on the SOW so the timeline section can "
            "cite a concrete duration._"
        )
    lines.append(
        "Effort estimates per milestone appear in the Estimated Time footer of the "
        "structured Scope of Work; this paragraph is the prose lead-in only."
    )
    return "\n\n".join(lines), citations


def _build_assumptions(sow: dict, opportunity: dict, tone: dict) -> tuple[str, list[str]]:
    titles = _milestone_titles(sow)
    citations: list[str] = []
    points = [
        f"The {_engagement_label(opportunity)} timeline assumes {_account_name(opportunity)} "
        "provides timely access to required systems, stakeholders, and decision-makers.",
        "Approved Change Requests are required for any work that falls outside the agreed scope.",
    ]
    if titles:
        citations.append("milestones")
        points.append(
            f"Milestone hand-offs ({_join_milestones_sentence(titles)}) presume the prior "
            "milestone's acceptance criteria are met."
        )
    return _bullets(points), citations


def _build_responsibilities(sow: dict, opportunity: dict, tone: dict) -> tuple[str, list[str]]:
    account = _account_name(opportunity)
    citations: list[str] = []
    contacts = sow.get("contact_roles") or []
    if isinstance(contacts, list) and contacts:
        citations.append("contact_roles")
    client_lines = [
        f"{account} owns business decisions, content approvals, and timely review of deliverables.",
        f"{account} provides access to systems, environments, and stakeholders required by each milestone.",
    ]
    delivery_lines = [
        "The delivery team owns engagement management, technical execution, and weekly status communication.",
        "The delivery team owns documentation of design choices and handoff artifacts at milestone close.",
    ]
    out = ["**Client responsibilities**", _bullets(client_lines), "", "**Delivery team responsibilities**", _bullets(delivery_lines)]
    return "\n".join(out), citations


def _build_out_of_scope(sow: dict, opportunity: dict, tone: dict) -> tuple[str, list[str]]:
    citations: list[str] = []
    points = [
        "Net-new feature requests outside the agreed milestones.",
        "Production support, on-call, or operations beyond the engagement window.",
        "Third-party licensing, hardware procurement, or vendor contract negotiation.",
        "Work blocked by client-side dependencies that are not delivered within the engagement window.",
    ]
    return _bullets(points), citations


def _build_risk_assessment(sow: dict, opportunity: dict, tone: dict) -> tuple[str, list[str]]:
    citations: list[str] = []
    points = [
        "Stakeholder availability — sustained access to decision-makers is required to keep milestone hand-offs on schedule.",
        "Environment readiness — dev/test/staging environments must be available and representative of production.",
        "Scope creep — out-of-scope items are tracked and handled through the change-request process.",
    ]
    return _bullets(points), citations


def _build_solution_overview(sow: dict, opportunity: dict, tone: dict) -> tuple[str, list[str]]:
    titles = _milestone_titles(sow)
    citations: list[str] = []
    intro = (
        f"The solution delivered for {_account_name(opportunity)} is structured around the "
        "milestones below; each milestone produces an inspectable artifact the client can "
        "validate before the next milestone starts."
    )
    if titles:
        citations.append("milestones")
        return intro + "\n\n" + _bullets(titles), citations
    return intro, citations


def _build_in_scope_inventory(sow: dict, opportunity: dict, tone: dict) -> tuple[str, list[str]]:
    titles = _milestone_titles(sow)
    citations: list[str] = []
    if titles:
        citations.append("milestones")
        return _bullets(titles), citations
    return (
        "_Inventory will be populated once milestones and deliverables are added to the SOW._",
        citations,
    )


def _build_conclusion(sow: dict, opportunity: dict, tone: dict) -> tuple[str, list[str]]:
    citations: list[str] = []
    return (
        f"We look forward to partnering with {_account_name(opportunity)} on "
        f"{_engagement_label(opportunity)}. The structured Scope of Work and the "
        "milestones above describe what we will deliver; the responsibilities section "
        "describes who owns what; and the assumptions section describes the conditions "
        "under which the timeline holds. Please reach out with questions before signature."
    ), citations


_BUILDERS = {
    "executive_summary": _build_executive_summary,
    "smart_objective": _build_smart_objective,
    "scope_of_work": _build_scope_of_work,
    "estimated_time": _build_estimated_time,
    "assumptions": _build_assumptions,
    "responsibilities": _build_responsibilities,
    "out_of_scope": _build_out_of_scope,
    "risk_assessment": _build_risk_assessment,
    "solution_overview": _build_solution_overview,
    "in_scope_inventory": _build_in_scope_inventory,
    "conclusion": _build_conclusion,
}


def build_section_candidate(
    sow: dict,
    opportunity: dict | None,
    kind: str,
    *,
    user_instruction: str | None = None,
) -> tuple[str, list[str]]:
    """Compose a deterministic regenerated candidate for one SOW
    prose section. Returns (candidate_markdown, source_citations).

    ``kind`` outside the canonical PS section set falls through to a
    generic skeleton — the route does not 400 on unknown kinds so a
    future custom section the deploy adds can still be regenerated.

    The function is pure: no DB writes, no network calls, no env reads.
    Every value composed into the candidate is read from ``sow`` or
    ``opportunity``; nothing is invented.
    """
    if not isinstance(sow, dict):
        sow = {}
    if not isinstance(kind, str) or not kind:
        kind = "executive_summary"
    tone = _normalize_instruction(user_instruction)
    builder = _BUILDERS.get(kind)
    if builder is None:
        # Generic skeleton for non-canonical kinds.
        return (
            f"_Section content for {kind} will be drafted from the SOW context once "
            "the section content is populated._",
            [],
        )
    return builder(sow, opportunity or {}, tone)


__all__ = ["build_section_candidate"]

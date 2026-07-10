"""Deterministic proposal-content generator for Sales Ops V1.

Builds a Markdown proposal draft from one or more SOWs on an
opportunity, plus a short summary for kanban / preview cards. No LLM,
no external service — V1 needs the workflow to land reliably even when
Google Docs (or any other artifact pipeline) isn't configured.

Determinism is load-bearing:

* The same opportunity + SOW input always produces the same output, so
  the regenerate flow can short-circuit when nothing has actually
  changed (``generation_source_hash`` covers this).
* No clocks, randomness, or LLM calls; this is a tools-compute helper
  the route calls and the UI displays.
* Inputs are dicts already read out of Mongo — the helper does not
  touch the database itself and never mutates the SOW rows.

Output shape:

  {
    "title":             str,
    "generated_summary": str,
    "generated_content": str,   # Markdown
    "generation_source": "sow",
    "generation_source_hash": str,  # stable hex digest of the inputs
    "artifact_type": "inline_content",
  }

Tests live in ``tests/test_proposal_generation.py`` and use synthetic
fixtures only — there is no real customer/tenant/financial data
embedded in this module.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


GENERATION_SOURCE = "sow"


# Human-readable labels for line-of-business codes. Mirrors the
# frontend's LOB_LABELS so an Email / Doc downstream reads the same
# wording as the kanban card.
_LOB_LABELS: dict[str, str] = {
    "field_services": "Field Services",
    "professional_services": "Professional Services",
    "wholesale_mrr": "Wholesale MRR",
    "referral_mrr": "Referral MRR",
}

# Friendly labels for the contract_type enum carried on each SOW.
_CONTRACT_TYPE_LABELS: dict[str, str] = {
    "boh": "Block of Hours",
    "boa": "Bill on Actuals",
    "fixed_fee": "Fixed Fee",
    "monthly": "Monthly Retainer",
    "wholesale_mrr": "Wholesale MRR",
}


# Canonical Professional Services proposal sections. The seven
# required sections render in this order for every PS SOW, then any
# optional sections present on sow.prose_sections render in their
# declared optional order. Each entry maps a kind key to the
# human-readable heading the proposal output shows the client.
#
# Executive Summary, SMART Objective, Scope of Work, and Estimated
# Time are computed from the structured SOW fields the editor
# already collects (executive_summary, smart_*, milestones with
# bullet.estimated_hours). Assumptions, Responsibilities, Out of
# Scope, and the optional sections come from sow.prose_sections,
# keyed by ``kind``.
PS_REQUIRED_SECTION_KINDS: tuple[tuple[str, str], ...] = (
    ("executive_summary", "Executive Summary"),
    ("smart_objective", "SMART Objective"),
    ("scope_of_work", "Scope of Work"),
    ("estimated_time", "Estimated Time"),
    ("assumptions", "Assumptions"),
    ("responsibilities", "Responsibilities"),
    ("out_of_scope", "Out of Scope"),
)

# Optional PS section kinds. Render after the seven required
# sections, in the order listed here, only when sow.prose_sections
# carries content for that kind.
PS_OPTIONAL_SECTION_KINDS: tuple[tuple[str, str], ...] = (
    ("risk_assessment", "Risk Assessment"),
    ("solution_overview", "Solution Overview"),
    ("in_scope_inventory", "In-Scope Inventory"),
    ("conclusion", "Conclusion"),
)

# Kinds that come from sow.prose_sections rather than structured
# SOW fields. The PS renderer at _format_ps_sow_section uses this
# set to decide whether to look up a prose-section payload (yes)
# or compute the section from structured fields (no). Adding a new
# prose-required kind requires updating both
# PS_REQUIRED_SECTION_KINDS and this set.
_PS_PROSE_REQUIRED_KINDS = frozenset({"assumptions", "responsibilities", "out_of_scope"})


def _money(value: Any) -> str | None:
    try:
        amt = float(value)
    except (TypeError, ValueError):
        return None
    return f"${amt:,.2f}"


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _title_for(opportunity: dict, sows: list[dict]) -> str:
    """Pick a stable proposal title from the opportunity name + LOB mix.

    Multi-SOW proposals get the joined LOB list so the title disambiguates
    sibling drafts on the same opportunity.
    """
    opp_name = _coerce_str(opportunity.get("name")) or "Untitled Opportunity"
    lobs = sorted({_coerce_str(s.get("line_of_business")) for s in sows if s.get("line_of_business")})
    if not lobs:
        return f"{opp_name} — Proposal"
    pretty = ", ".join(_LOB_LABELS.get(lob, lob.replace("_", " ").title()) for lob in lobs)
    return f"{opp_name} — {pretty} Proposal"


def _format_smart(sow: dict) -> list[str]:
    """SMART block rendered as Markdown bullets. Missing fields skipped."""
    pairs = [
        ("Specific", sow.get("smart_specific")),
        ("Measurable", sow.get("smart_measurable")),
        ("Achievable", sow.get("smart_achievable")),
        ("Relevant", sow.get("smart_relevant")),
        ("Time-bound", sow.get("smart_time_bound")),
    ]
    out: list[str] = []
    for label, value in pairs:
        text = _coerce_str(value)
        if text:
            out.append(f"- **{label}:** {text}")
    return out


def _milestone_sort_key(m: dict) -> int:
    """Stable sort key for milestones. Same shape used by the render
    path and the signature path so two milestone lists with identical
    content in different Mongo scan order hash the same. Diverging the
    two key shapes produces a spurious regenerate signal whenever the
    server returns rows in a different order.
    """
    order = m.get("order") if isinstance(m, dict) else None
    return order if isinstance(order, int) else 0


def _format_milestones(sow: dict) -> list[str]:
    milestones = sow.get("milestones") or []
    if not milestones:
        return []
    lines: list[str] = []
    # Stable ordering. Sort by the explicit order field if set,
    # otherwise the key resolves to zero and the original list order
    # Sales Ops authored is preserved.
    ordered = sorted(list(milestones), key=_milestone_sort_key)
    for m in ordered:
        name = _coerce_str(m.get("milestone_name")) or "Milestone"
        lines.append(f"- {name}")
        for b in m.get("bullets") or []:
            text = _coerce_str(b.get("text") if isinstance(b, dict) else b)
            if text:
                lines.append(f"  - {text}")
    return lines


def _format_scope_of_work(sow: dict) -> list[str]:
    """Render the milestones list as the Scope of Work block.

    Same structured output as _format_milestones but framed under
    the PS canonical "Scope of Work" heading. Bullet hours are
    intentionally omitted from this output; the Estimated Time
    section is the only place the proposal surfaces hours per the
    PS spec ("no inline task-hour display inside the task list").
    """
    return _format_milestones(sow)


def _format_estimated_time(sow: dict) -> list[str]:
    """Compute Estimated Time totals from the structured milestone
    bullets. Returns Markdown bullets with the grand total and a
    per-milestone breakdown. Returns an empty list when no hour
    data is present so the section can be skipped cleanly.

    The PS spec says "task-level hours stored in data; no inline
    task-hour display inside the task list; total / phase / task
    hours rendered only in the Estimated Time footer or summary
    area." This helper is that footer / summary.
    """
    milestones = sow.get("milestones") or []
    if not milestones:
        return []

    grand_total = 0.0
    per_milestone: list[tuple[str, float]] = []
    for m in sorted(list(milestones), key=_milestone_sort_key):
        name = _coerce_str(m.get("milestone_name")) or "Milestone"
        ms_total = 0.0
        for b in m.get("bullets") or []:
            if not isinstance(b, dict):
                continue
            try:
                ms_total += float(b.get("estimated_hours") or 0)
            except (TypeError, ValueError):
                continue
        grand_total += ms_total
        per_milestone.append((name, ms_total))

    if grand_total <= 0 and not any(h > 0 for _, h in per_milestone):
        return []

    def _fmt_hours(h: float) -> str:
        return f"{int(h)}" if h == int(h) else f"{h:.1f}"

    lines = [f"- **Total estimated hours:** {_fmt_hours(grand_total)}"]
    for name, hours in per_milestone:
        if hours <= 0:
            continue
        lines.append(f"- **{name}:** {_fmt_hours(hours)}h")
    return lines


def _prose_section_lookup(sow: dict) -> dict[str, str]:
    """Index sow.prose_sections by kind and return a {kind: content}
    map. Empty content is preserved so the caller can decide whether
    to emit the section heading with a placeholder or skip entirely.
    """
    out: dict[str, str] = {}
    for entry in sow.get("prose_sections") or []:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        if not isinstance(kind, str) or not kind:
            continue
        out[kind] = _coerce_str(entry.get("content"))
    return out


def _format_billing(sow: dict) -> list[str]:
    rows: list[tuple[str, str]] = []
    contract_type = _coerce_str(sow.get("contract_type"))
    if contract_type:
        rows.append(("Contract type", _CONTRACT_TYPE_LABELS.get(contract_type, contract_type)))
    for label, key in (
        ("Billing terms", "billing_terms"),
        ("Payment terms", "payment_terms"),
        ("Start date", "start_date"),
        ("End date", "end_date"),
    ):
        value = _coerce_str(sow.get(key))
        if value:
            rows.append((label, value))
    for label, key in (("Total amount", "total_amount"), ("Monthly rate", "monthly_rate")):
        money = _money(sow.get(key))
        if money:
            rows.append((label, money))
    return [f"- **{label}:** {value}" for label, value in rows]


def _format_executive_summary(opportunity: dict, sows: list[dict]) -> str:
    """One-paragraph summary suitable for a card preview or email body."""
    account = _coerce_str(opportunity.get("account_name")) or "the client"
    name = _coerce_str(opportunity.get("name")) or "this opportunity"
    lobs = sorted({_coerce_str(s.get("line_of_business")) for s in sows if s.get("line_of_business")})
    pretty_lobs = (
        ", ".join(_LOB_LABELS.get(lob, lob.replace("_", " ").title()) for lob in lobs)
        if lobs
        else "the in-scope services"
    )
    sow_count = len(sows)
    sow_word = "SOW" if sow_count == 1 else "SOWs"
    return (
        f"Proposal for {account} covering {pretty_lobs}. Built from "
        f"{sow_count} {sow_word} on {name}."
    )


def _format_sow_section(sow: dict) -> str:
    lob = _coerce_str(sow.get("line_of_business"))
    if lob == "professional_services":
        return _format_ps_sow_section(sow)
    return _format_default_sow_section(sow)


def _format_default_sow_section(sow: dict) -> str:
    """Field Services and Wholesale MRR rendering. Unchanged from
    the pre-polish shape so a SOW outside Professional Services
    keeps the same output."""
    lob = _coerce_str(sow.get("line_of_business"))
    heading = _LOB_LABELS.get(lob, lob.replace("_", " ").title() or "Statement of Work")
    parts: list[str] = [f"## {heading}"]

    exec_summary = _coerce_str(sow.get("executive_summary"))
    if exec_summary:
        parts.extend(["", "### Executive Summary", "", exec_summary])

    smart_lines = _format_smart(sow)
    if smart_lines:
        parts.extend(["", "### SMART Objectives", ""] + smart_lines)

    milestone_lines = _format_milestones(sow)
    if milestone_lines:
        parts.extend(["", "### Milestones", ""] + milestone_lines)

    billing_lines = _format_billing(sow)
    if billing_lines:
        parts.extend(["", "### Billing & Terms", ""] + billing_lines)

    return "\n".join(parts).rstrip()


def _format_ps_sow_section(sow: dict) -> str:
    """Professional Services rendering. Emits the canonical
    seven-section proposal in order, then any optional sections
    present on sow.prose_sections. The seven required sections are
    Executive Summary, SMART Objective, Scope of Work, Estimated
    Time, Assumptions, Responsibilities, Out of Scope. The optional
    sections are Risk Assessment, Solution Overview, In-Scope
    Inventory, Conclusion. See PS_REQUIRED_SECTION_KINDS and
    PS_OPTIONAL_SECTION_KINDS above.

    Scope of Work renders the milestone / bullet structure without
    inline task hours. Estimated Time renders the totals (grand +
    per-milestone) computed from bullet.estimated_hours. Assumptions,
    Responsibilities, Out of Scope, and every optional section come
    from the sow.prose_sections payload keyed by kind. A required
    section with no content renders its heading and a placeholder
    line so the proposal output cannot accidentally omit a
    canonical section.
    """
    heading = _LOB_LABELS.get(
        "professional_services", "Professional Services"
    )
    parts: list[str] = [f"## {heading}"]
    prose = _prose_section_lookup(sow)

    for kind, title in PS_REQUIRED_SECTION_KINDS:
        body_lines: list[str] = []
        if kind == "executive_summary":
            exec_summary = (
                _coerce_str(sow.get("executive_summary"))
                or prose.get(kind, "")
            )
            if exec_summary:
                body_lines = [exec_summary]
        elif kind == "smart_objective":
            body_lines = _format_smart(sow)
        elif kind == "scope_of_work":
            body_lines = _format_scope_of_work(sow)
        elif kind == "estimated_time":
            body_lines = _format_estimated_time(sow)
        elif kind in _PS_PROSE_REQUIRED_KINDS:
            content = prose.get(kind, "")
            if content:
                body_lines = [content]
        else:
            # Unknown required kind. Should not occur unless
            # PS_REQUIRED_SECTION_KINDS gains an entry without a
            # corresponding renderer branch. Render a placeholder
            # so the failure is visible in the output rather than
            # silently dropping the section.
            body_lines = []

        parts.extend(["", f"### {title}", ""])
        if body_lines:
            parts.extend(body_lines)
        else:
            parts.append("_(to be completed)_")

    # Optional sections render in the user's reorder (the SOW Builder
    # stamps an ``order`` field on each prose_section entry when the
    # user uses the move up / move down controls). Entries without an
    # order fall back to their declared position in
    # PS_OPTIONAL_SECTION_KINDS so a deploy that hasn't been touched
    # since the reorder shipped continues to render in canonical
    # order. Empty optional sections are skipped silently.
    optional_lookup_by_kind: dict[str, dict] = {}
    for entry in sow.get("prose_sections") or []:
        if isinstance(entry, dict):
            k = entry.get("kind")
            if isinstance(k, str):
                optional_lookup_by_kind[k] = entry
    canonical = list(PS_OPTIONAL_SECTION_KINDS)
    canonical_titles = {kind: title for kind, title in canonical}
    canonical_order = {kind: idx for idx, (kind, _) in enumerate(canonical)}

    def _sort_key(kind_title):
        kind, _title = kind_title
        entry = optional_lookup_by_kind.get(kind) or {}
        user_order = entry.get("order")
        if isinstance(user_order, int):
            return (0, user_order)
        return (1, canonical_order.get(kind, 0))

    for kind, title in sorted(canonical, key=_sort_key):
        content = prose.get(kind, "")
        if not content:
            continue
        # Use canonical title (route only allows canonical kinds in
        # the prose set; never trust an entry-supplied title).
        parts.extend(["", f"### {canonical_titles.get(kind, title)}", "", content])

    billing_lines = _format_billing(sow)
    if billing_lines:
        parts.extend(["", "### Billing & Terms", ""] + billing_lines)

    return "\n".join(parts).rstrip()


def _content_signature(opportunity: dict, sows: list[dict]) -> str:
    """Stable hex digest of the inputs that produced the content.

    Used by the regenerate path to detect a no-op refresh. Only the
    fields the generator actually reads contribute to the digest so
    cosmetic Mongo metadata churn (``updated_at`` etc.) doesn't force a
    spurious regenerate.
    """
    payload = {
        "opportunity": {
            "opportunity_id": opportunity.get("opportunity_id"),
            "name": opportunity.get("name"),
            "account_name": opportunity.get("account_name"),
        },
        "sows": [
            {
                "sow_id": s.get("sow_id"),
                "line_of_business": s.get("line_of_business"),
                "executive_summary": s.get("executive_summary"),
                "smart_specific": s.get("smart_specific"),
                "smart_measurable": s.get("smart_measurable"),
                "smart_achievable": s.get("smart_achievable"),
                "smart_relevant": s.get("smart_relevant"),
                "smart_time_bound": s.get("smart_time_bound"),
                "contract_type": s.get("contract_type"),
                "billing_terms": s.get("billing_terms"),
                "payment_terms": s.get("payment_terms"),
                "start_date": s.get("start_date"),
                "end_date": s.get("end_date"),
                "total_amount": s.get("total_amount"),
                "monthly_rate": s.get("monthly_rate"),
                # J6. Milestones are sorted with the same key the render
                # path uses so a Mongo scan order shuffle does not flip
                # the digest. Without this guard a regenerate would
                # spuriously fire whenever the server returned the
                # same content in a different order.
                "milestones": sorted(
                    list(s.get("milestones") or []),
                    key=_milestone_sort_key,
                ),
                # Prose sections are sorted by kind so a list-shuffle
                # does not flip the digest. The PS renderer reads
                # them in the canonical order regardless of the
                # underlying storage order, so the digest must do the
                # same. Each entry contributes only its kind and
                # content; the removable / order metadata is
                # presentation-side and does not affect output.
                "prose_sections": sorted(
                    [
                        {
                            "kind": (
                                ps.get("kind") if isinstance(ps, dict) else None
                            ),
                            "content": (
                                ps.get("content") if isinstance(ps, dict) else None
                            ),
                        }
                        for ps in (s.get("prose_sections") or [])
                    ],
                    key=lambda x: str(x.get("kind") or ""),
                ),
            }
            for s in sows
        ],
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def generate_proposal_content(opportunity: dict, sows: list[dict]) -> dict[str, Any]:
    """Build a deterministic proposal-content payload from SOW data.

    The caller is expected to have already validated that ``sows``
    belong to ``opportunity`` and carry a status finished enough to
    ship — this helper trusts its inputs and never reads the database.
    """
    if not sows:
        raise ValueError("generate_proposal_content requires at least one SOW")

    title = _title_for(opportunity, sows)
    summary = _format_executive_summary(opportunity, sows)

    ordered = sorted(
        list(sows),
        key=lambda s: _coerce_str(s.get("line_of_business")),
    )

    content_lines = [
        f"# {title}",
        "",
        summary,
        "",
        "## Scope of Work",
    ]
    for sow in ordered:
        content_lines.append("")
        content_lines.append(_format_sow_section(sow))

    return {
        "title": title,
        "generated_summary": summary,
        "generated_content": "\n".join(content_lines).rstrip() + "\n",
        "generation_source": GENERATION_SOURCE,
        "generation_source_hash": _content_signature(opportunity, ordered),
        "artifact_type": "inline_content",
    }


__all__ = [
    "GENERATION_SOURCE",
    "generate_proposal_content",
]

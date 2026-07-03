"""Tests for the PS SOW section-regenerate route + service.

Covers:

* The deterministic ``build_section_candidate`` service composes
  candidates for every canonical PS kind without inventing numbers,
  dates, prices, or hours; output is bounded to fields present on
  the SOW + opportunity dicts the caller supplied.
* The POST regenerate route authorizes, tenant-scopes by opportunity
  + sow id, refuses non-PS SOWs, and 404s on unknown SOW/section.
* ``mode="preview"`` returns the candidate but does NOT write to
  Mongo and does NOT write an audit row.
* ``mode="replace"`` writes back to ``prose_sections`` preserving
  every other section, stamps the SOW with a new version, and
  writes an audit row of action
  ``sales_ops.sow.section_regenerated``.
* Required PS section kinds (Executive Summary, SMART Objective,
  Scope of Work, Estimated Time, Assumptions, Responsibilities, Out
  of Scope) and optional kinds (Risk Assessment, Solution Overview,
  In-Scope Inventory, Conclusion) all build cleanly.
* The user instruction never appears verbatim in the generated
  content (tone-hint matching only — no prompt-injection-style
  pass-through).

Real DB and HTTP are mocked. Synthetic identifiers only — no real
account names, vendor names, employee names, or emails.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)

from fastapi import HTTPException  # noqa: E402

from models import SOWSectionRegenerateRequest  # noqa: E402


# ---------------------------------------------------------------------------
# Service: build_section_candidate
# ---------------------------------------------------------------------------


def _synth_sow():
    return {
        "sow_id": "sow_synth",
        "opportunity_id": "opp_synth",
        "line_of_business": "professional_services",
        "milestones": [
            {"title": "Discovery", "order": 1},
            {"title": "Design", "order": 2},
        ],
        "smart_specific": "Ship the synthetic platform",
        "smart_measurable": "90% test coverage",
        "start_date": "2026-06-01",
        "end_date": "2026-09-30",
        "prose_sections": [
            {
                "kind": "executive_summary",
                "content": "Existing exec summary draft.",
                "removable": False,
            },
            {"kind": "assumptions", "content": "", "removable": False},
        ],
        "version": 4,
    }


def _synth_opportunity():
    return {
        "opportunity_id": "opp_synth",
        "name": "Synthetic Engagement",
        "account_name": "Synthetic Corp",
    }


def test_build_candidate_executive_summary_uses_sow_context():
    from services.sow_section_regeneration import build_section_candidate

    content, citations = build_section_candidate(
        _synth_sow(), _synth_opportunity(), "executive_summary"
    )
    assert "Synthetic Corp" in content
    assert "Discovery" in content
    assert "milestones" in citations
    # smart_objective citation should appear because smart_specific is set.
    assert "smart_objective" in citations


def test_build_candidate_smart_objective_falls_back_when_missing():
    from services.sow_section_regeneration import build_section_candidate

    sow = _synth_sow()
    sow["smart_specific"] = ""
    sow["smart_measurable"] = ""
    content, citations = build_section_candidate(sow, _synth_opportunity(), "smart_objective")
    assert "_Add SMART" in content
    assert citations == []


def test_build_candidate_scope_of_work_lists_milestone_titles():
    from services.sow_section_regeneration import build_section_candidate

    content, citations = build_section_candidate(
        _synth_sow(), _synth_opportunity(), "scope_of_work"
    )
    assert "Discovery" in content
    assert "Design" in content
    assert citations == ["milestones"]


def test_build_candidate_estimated_time_uses_start_end_dates():
    from services.sow_section_regeneration import build_section_candidate

    content, citations = build_section_candidate(
        _synth_sow(), _synth_opportunity(), "estimated_time"
    )
    assert "2026-06-01" in content
    assert "2026-09-30" in content
    assert "start_date" in citations and "end_date" in citations


def test_build_candidate_unknown_kind_returns_generic_skeleton():
    from services.sow_section_regeneration import build_section_candidate

    content, citations = build_section_candidate(
        _synth_sow(), _synth_opportunity(), "wholly_invented_kind_xyz"
    )
    assert "wholly_invented_kind_xyz" in content
    assert citations == []


def test_user_instruction_never_appears_verbatim_in_output():
    """The instruction is matched for tone keywords only; raw text
    must never reach the generated content. A future LLM-backed
    backend would still need to honor this contract, so the test
    locks the behavior in for the deterministic builder."""
    from services.sow_section_regeneration import build_section_candidate

    secret_marker = "ZZZ_INJECTED_PROMPT_PAYLOAD_ZZZ"
    sow = _synth_sow()
    opp = _synth_opportunity()
    for kind in (
        "executive_summary",
        "smart_objective",
        "scope_of_work",
        "estimated_time",
        "assumptions",
        "responsibilities",
        "out_of_scope",
        "risk_assessment",
        "solution_overview",
        "in_scope_inventory",
        "conclusion",
    ):
        content, _ = build_section_candidate(
            sow, opp, kind, user_instruction=secret_marker
        )
        assert secret_marker not in content, (
            f"user instruction leaked verbatim into {kind} output"
        )


def test_executive_tone_shortens_output():
    from services.sow_section_regeneration import build_section_candidate

    sow = _synth_sow()
    opp = _synth_opportunity()
    long_form, _ = build_section_candidate(sow, opp, "executive_summary")
    short_form, _ = build_section_candidate(
        sow, opp, "executive_summary", user_instruction="make this more executive"
    )
    # Executive tone trims the trailing SMART block, so the executive
    # variant should be shorter or equal to the long form.
    assert len(short_form) <= len(long_form)


# ---------------------------------------------------------------------------
# Fake DB for route tests
# ---------------------------------------------------------------------------


class _FakeCollection:
    def __init__(self, docs=None):
        self._docs = [dict(d) for d in (docs or [])]
        self.updates: list[tuple[dict, dict]] = []

    @staticmethod
    def _matches(doc, query):
        for k, v in query.items():
            if doc.get(k) != v:
                return False
        return True

    async def find_one(self, query, _projection=None):
        for d in self._docs:
            if self._matches(d, query):
                return dict(d)
        return None

    async def update_one(self, query, update):
        for d in self._docs:
            if self._matches(d, query):
                d.update(update.get("$set") or {})
                self.updates.append((query, update.get("$set") or {}))
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


def _user(perms, user_id="u_synth", email="u@aegisaxiom.test"):
    return SimpleNamespace(
        user_id=user_id, email=email, role="sales_ops", permissions=list(perms)
    )


def _build_db(sows, opportunities=None):
    return SimpleNamespace(
        opportunity_sows=_FakeCollection(sows),
        opportunities=_FakeCollection(opportunities or []),
    )


def _patch_audit(monkeypatch):
    captured: list[dict] = []

    async def _fake(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(
        "routes.solutions_design.audit_log_action", _fake
    )
    return captured


# ---------------------------------------------------------------------------
# Route: authorization + tenant safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_rejects_caller_without_edit_perm():
    from routes.solutions_design import regenerate_sow_section

    db = _build_db([_synth_sow()], [_synth_opportunity()])
    user = _user(["sales_ops.solutions_design.view"])
    with pytest.raises(HTTPException) as exc:
        await regenerate_sow_section(
            opportunity_id="opp_synth",
            sow_id="sow_synth",
            section_kind="executive_summary",
            body=SOWSectionRegenerateRequest(mode="preview"),
            db=db,
            current_user=user,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_route_returns_404_when_sow_belongs_to_different_opportunity():
    """Tenant-safety boundary: a SOW from one opportunity must not be
    reachable through another opportunity's path. The route returns
    404, never 403, so an attacker cannot probe across opportunities
    to enumerate which sow_ids exist."""
    from routes.solutions_design import regenerate_sow_section

    sow = _synth_sow()
    sow["opportunity_id"] = "opp_other_tenant"
    db = _build_db([sow], [_synth_opportunity()])
    user = _user(["sales_ops.solutions_design.edit"])
    with pytest.raises(HTTPException) as exc:
        await regenerate_sow_section(
            opportunity_id="opp_synth",  # different from the SOW's opportunity
            sow_id="sow_synth",
            section_kind="executive_summary",
            body=SOWSectionRegenerateRequest(mode="preview"),
            db=db,
            current_user=user,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_route_returns_404_when_sow_unknown():
    from routes.solutions_design import regenerate_sow_section

    db = _build_db([], [_synth_opportunity()])
    user = _user(["sales_ops.solutions_design.edit"])
    with pytest.raises(HTTPException) as exc:
        await regenerate_sow_section(
            opportunity_id="opp_synth",
            sow_id="sow_missing",
            section_kind="executive_summary",
            body=SOWSectionRegenerateRequest(mode="preview"),
            db=db,
            current_user=user,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_route_rejects_field_services_sow():
    from routes.solutions_design import regenerate_sow_section

    sow = _synth_sow()
    sow["line_of_business"] = "field_services"
    db = _build_db([sow], [_synth_opportunity()])
    user = _user(["sales_ops.solutions_design.edit"])
    with pytest.raises(HTTPException) as exc:
        await regenerate_sow_section(
            opportunity_id="opp_synth",
            sow_id="sow_synth",
            section_kind="executive_summary",
            body=SOWSectionRegenerateRequest(mode="preview"),
            db=db,
            current_user=user,
        )
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "sow_not_professional_services"


# ---------------------------------------------------------------------------
# Route: preview mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_mode_does_not_write_or_audit(monkeypatch):
    from routes.solutions_design import regenerate_sow_section

    captured = _patch_audit(monkeypatch)
    sow = _synth_sow()
    db = _build_db([sow], [_synth_opportunity()])
    user = _user(["sales_ops.solutions_design.edit"])

    result = await regenerate_sow_section(
        opportunity_id="opp_synth",
        sow_id="sow_synth",
        section_kind="executive_summary",
        body=SOWSectionRegenerateRequest(mode="preview"),
        db=db,
        current_user=user,
    )
    assert result["applied"] is False
    assert result["revision_version"] is None
    assert result["kind"] == "executive_summary"
    assert "Synthetic Corp" in result["candidate_content"]
    assert db.opportunity_sows.updates == []
    assert captured == []


# ---------------------------------------------------------------------------
# Route: replace mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_mode_writes_and_preserves_other_sections(monkeypatch):
    from routes.solutions_design import regenerate_sow_section

    captured = _patch_audit(monkeypatch)
    sow = _synth_sow()
    sow["prose_sections"] = [
        {"kind": "executive_summary", "content": "OLD", "removable": False},
        {"kind": "assumptions", "content": "Existing assumptions", "removable": False},
        {"kind": "risk_assessment", "content": "Existing risk", "removable": True, "order": 0},
    ]
    db = _build_db([sow], [_synth_opportunity()])
    user = _user(["sales_ops.solutions_design.edit"])

    result = await regenerate_sow_section(
        opportunity_id="opp_synth",
        sow_id="sow_synth",
        section_kind="executive_summary",
        body=SOWSectionRegenerateRequest(mode="replace", user_instruction="shorter"),
        db=db,
        current_user=user,
    )
    assert result["applied"] is True
    assert result["revision_version"] == 5
    # Verify the stored prose_sections still contains both other entries
    # unchanged, and the executive_summary entry was replaced.
    updated = db.opportunity_sows._docs[0]
    sections = {s["kind"]: s for s in updated["prose_sections"]}
    assert sections["assumptions"]["content"] == "Existing assumptions"
    assert sections["risk_assessment"]["content"] == "Existing risk"
    assert sections["risk_assessment"]["order"] == 0
    assert sections["executive_summary"]["content"] != "OLD"
    assert sections["executive_summary"]["content"] == result["candidate_content"]
    # New section_id placeholder: model has no section_id today, but
    # the kind survives as the stable handle.
    assert sections["executive_summary"]["kind"] == "executive_summary"
    # Removable on a required section stays False per the route's
    # preserve-from-existing logic.
    assert sections["executive_summary"]["removable"] is False
    # Audit row written
    assert captured and captured[0]["action"] == "sales_ops.sow.section_regenerated"
    assert captured[0]["details"]["section_kind"] == "executive_summary"
    assert captured[0]["details"]["new_version"] == 5
    assert captured[0]["details"]["user_instruction_provided"] is True


@pytest.mark.asyncio
async def test_replace_mode_appends_when_section_not_yet_present(monkeypatch):
    """Optional sections that have not been edited yet have no entry
    in prose_sections. Regenerating one should append a new entry
    with sensible defaults (removable=True, order=None)."""
    from routes.solutions_design import regenerate_sow_section

    _patch_audit(monkeypatch)
    sow = _synth_sow()
    sow["prose_sections"] = []  # nothing yet
    db = _build_db([sow], [_synth_opportunity()])
    user = _user(["sales_ops.solutions_design.edit"])

    result = await regenerate_sow_section(
        opportunity_id="opp_synth",
        sow_id="sow_synth",
        section_kind="conclusion",
        body=SOWSectionRegenerateRequest(mode="replace"),
        db=db,
        current_user=user,
    )
    assert result["applied"] is True
    updated = db.opportunity_sows._docs[0]
    sections = {s["kind"]: s for s in updated["prose_sections"]}
    assert "conclusion" in sections
    assert sections["conclusion"]["removable"] is True
    assert sections["conclusion"]["order"] is None


@pytest.mark.asyncio
async def test_replace_mode_records_citations_on_section(monkeypatch):
    from routes.solutions_design import regenerate_sow_section

    _patch_audit(monkeypatch)
    sow = _synth_sow()
    db = _build_db([sow], [_synth_opportunity()])
    user = _user(["sales_ops.solutions_design.edit"])

    await regenerate_sow_section(
        opportunity_id="opp_synth",
        sow_id="sow_synth",
        section_kind="scope_of_work",
        body=SOWSectionRegenerateRequest(mode="replace"),
        db=db,
        current_user=user,
    )
    updated = db.opportunity_sows._docs[0]
    sow_section = next(s for s in updated["prose_sections"] if s["kind"] == "scope_of_work")
    assert sow_section.get("source_citations") == ["milestones"]


# ---------------------------------------------------------------------------
# Source-level checks
# ---------------------------------------------------------------------------


def test_rich_prose_section_component_exists():
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "sow-builder"
        / "RichProseSection.js"
    )
    if not path.exists():  # pragma: no cover — frontend may not be mounted
        pytest.skip("frontend not mounted in this test image")
    src = path.read_text(encoding="utf-8")
    # The component must export the rich block + regen modal.
    assert "export default function RichProseSection" in src
    assert "export function RegenerateSectionModal" in src
    # Sanitization safeguard — no raw innerHTML usage in this component.
    assert "dangerouslySetInnerHTML" not in src, (
        "RichProseSection should render via react-markdown, not raw HTML."
    )
    # Link allowlist contract. The scheme regex must be an explicit
    # allowlist of http/https/mailto/tel — not a denylist that could
    # be bypassed by a creative scheme.
    assert "LINK_ALLOWED_SCHEMES = /^(https?:|mailto:|tel:)/i" in src, (
        "RichProseSection link allowlist must be an explicit http(s)/mailto/tel "
        "regex, not a denylist or wildcard."
    )


def test_solutions_design_tab_uses_rich_prose_section():
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "SolutionsDesignTab.js"
    )
    if not path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    src = path.read_text(encoding="utf-8")
    assert "import RichProseSection" in src or "from './sow-builder/RichProseSection'" in src
    # PS prose panel renders the rich block, not bare textareas in the
    # required-sections list.
    assert "<RichProseSection" in src
    # The regen modal mounts here.
    assert "RegenerateSectionModal" in src


def test_no_iframe_or_google_docs_embed_introduced():
    """We did not promise an embedded Google Doc iframe. The audit
    trail of the codebase must not gain one through this ticket."""
    from pathlib import Path

    paths = [
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "SolutionsDesignTab.js",
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "sow-builder"
        / "RichProseSection.js",
    ]
    for path in paths:
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8")
        # Lower-cased substring match. The codebase still references
        # docs.google.com in unrelated comments/strings, but neither
        # of these files should embed an iframe for an editable doc.
        lowered = src.lower()
        assert "<iframe" not in lowered, f"iframe introduced in {path.name}"


def test_route_registers_at_expected_path():
    from routes.solutions_design import router

    paths = {r.path for r in router.routes}
    assert (
        "/opportunities/{opportunity_id}/sows/{sow_id}/sections/{section_kind}/regenerate"
        in paths
    )


# ---------------------------------------------------------------------------
# Hardening fixes from code review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_rejects_section_kind_with_invalid_characters():
    """The section_kind path parameter is server-side validated even
    after FastAPI routing because the value lands on the persisted
    prose_section.kind field. ``../`` traversal, uppercase, spaces,
    and overlong strings must 400 — never reach the DB write."""
    from routes.solutions_design import regenerate_sow_section

    db = _build_db([_synth_sow()], [_synth_opportunity()])
    user = _user(["sales_ops.solutions_design.edit"])
    bad_kinds = [
        "../etc/passwd",
        "ExecutiveSummary",  # uppercase
        "kind with spaces",
        "x" * 65,  # too long
        "",
        "9_starts_with_digit",
    ]
    for kind in bad_kinds:
        with pytest.raises(HTTPException) as exc:
            await regenerate_sow_section(
                opportunity_id="opp_synth",
                sow_id="sow_synth",
                section_kind=kind,
                body=SOWSectionRegenerateRequest(mode="preview"),
                db=db,
                current_user=user,
            )
        assert exc.value.status_code == 400, f"kind {kind!r} should 400"
        assert exc.value.detail["code"] == "invalid_section_kind"


def test_user_instruction_has_length_cap():
    """The Pydantic body bounds user_instruction so a malicious or
    accidental large payload cannot reach the route's downstream
    string ops."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SOWSectionRegenerateRequest(user_instruction="x" * 2001, mode="preview")
    # The 2000-char limit is inclusive — exact boundary accepted.
    SOWSectionRegenerateRequest(user_instruction="x" * 2000, mode="preview")


def test_proposal_renderer_honors_user_order_on_optional_sections():
    """When the SOW Builder reorders optional sections (stamping
    ``order`` on each entry), the proposal output renders them in
    that order. Empty optional sections are still skipped."""
    from services.proposal_generation import _format_ps_sow_section

    sow = {
        "line_of_business": "professional_services",
        "smart_specific": "ship it",
        "executive_summary": "Synthetic exec summary.",
        "milestones": [{"title": "Discovery", "order": 1}],
        "prose_sections": [
            {"kind": "assumptions", "content": "A1.", "removable": False},
            {"kind": "responsibilities", "content": "R1.", "removable": False},
            {"kind": "out_of_scope", "content": "OOS.", "removable": False},
            # User has reordered: conclusion first, then risk_assessment.
            {"kind": "conclusion", "content": "Conc.", "removable": True, "order": 0},
            {"kind": "risk_assessment", "content": "Risk.", "removable": True, "order": 1},
        ],
    }
    rendered = _format_ps_sow_section(sow)
    conclusion_idx = rendered.find("### Conclusion")
    risk_idx = rendered.find("### Risk Assessment")
    assert conclusion_idx != -1 and risk_idx != -1
    assert conclusion_idx < risk_idx, (
        "Optional sections should render in user-stamped order, "
        "not canonical PS_OPTIONAL_SECTION_KINDS order."
    )


def test_proposal_renderer_falls_back_to_canonical_order_without_user_order():
    """When optional entries carry no order field, the renderer uses
    canonical PS_OPTIONAL_SECTION_KINDS order (risk_assessment,
    solution_overview, in_scope_inventory, conclusion). This protects
    existing SOWs that pre-date the reorder shipping."""
    from services.proposal_generation import _format_ps_sow_section

    sow = {
        "line_of_business": "professional_services",
        "executive_summary": "Synthetic exec summary.",
        "smart_specific": "ship it",
        "prose_sections": [
            {"kind": "assumptions", "content": "A.", "removable": False},
            {"kind": "responsibilities", "content": "R.", "removable": False},
            {"kind": "out_of_scope", "content": "O.", "removable": False},
            {"kind": "conclusion", "content": "Conc.", "removable": True},
            {"kind": "risk_assessment", "content": "Risk.", "removable": True},
        ],
    }
    rendered = _format_ps_sow_section(sow)
    risk_idx = rendered.find("### Risk Assessment")
    conclusion_idx = rendered.find("### Conclusion")
    assert risk_idx != -1 and conclusion_idx != -1
    assert risk_idx < conclusion_idx, (
        "Without user order, canonical PS_OPTIONAL_SECTION_KINDS order applies."
    )


def test_safeurl_rejects_protocol_relative_links():
    """Source-level lock on the link-allowlist hardening: the
    RichProseSection ``safeUrl`` helper must reject ``//evil.com``
    style protocol-relative URLs because those resolve to the host
    page's protocol at click time."""
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "sow-builder"
        / "RichProseSection.js"
    )
    if not path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    src = path.read_text(encoding="utf-8")
    assert "value.startsWith('//')" in src, (
        "safeUrl must explicitly reject protocol-relative URLs (//evil.com)."
    )


def test_replace_mode_called_from_ui_replace_click():
    """Source-level lock on the P0 fix: the UI's Replace button posts
    to the regenerate route with mode=replace so the backend bumps
    version + writes the audit row. Without this assertion, a future
    refactor that quietly drops the route call would silently lose
    the audit trail for section replacements."""
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "SolutionsDesignTab.js"
    )
    if not path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    src = path.read_text(encoding="utf-8")
    # The applyRegenerate handler must POST mode: 'replace'.
    assert "applyRegenerate" in src
    assert "mode: 'replace'" in src or 'mode: "replace"' in src, (
        "applyRegenerate must POST mode=replace to the regenerate route."
    )


def test_regenerate_modal_surfaces_source_citations():
    """Source-level contract: the regenerate modal must accept a
    ``citations`` prop and the prose-section panel must thread the
    ``regenCitations`` state into that prop. The backend already
    returns ``source_citations`` on every regenerate response; without
    this assertion a future refactor could drop the prop on the floor
    and the user would never see what the deterministic composer used
    to build the candidate, which is the only way they can judge
    whether the regenerate is faithful to the SOW context."""
    from pathlib import Path

    rich_path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "sow-builder"
        / "RichProseSection.js"
    )
    tab_path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "SolutionsDesignTab.js"
    )
    if not (rich_path.exists() and tab_path.exists()):  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")

    rich_src = rich_path.read_text(encoding="utf-8")
    tab_src = tab_path.read_text(encoding="utf-8")

    # Modal must declare and render the citations prop.
    assert "citations" in rich_src, (
        "RegenerateSectionModal must accept a citations prop so the "
        "deterministic composer's source list is visible to the user."
    )
    assert "ps-section-regenerate-citations" in rich_src, (
        "RegenerateSectionModal must render the citations list with the "
        "ps-section-regenerate-citations testid for browser smoke."
    )

    # Tab must pass the citations state through.
    assert "citations={regenCitations}" in tab_src, (
        "SolutionsDesignTab must pass regenCitations into "
        "RegenerateSectionModal so the modal can show what the "
        "candidate was composed from."
    )


def test_regenerate_failure_toast_names_the_failing_block():
    """Source-level contract: when a regenerate or replace request
    fails, the toast must include the section label so the user knows
    which block failed. The dispatch explicitly asks for per-block
    error state so the whole editor does not freeze on a generic
    error; a label-less toast hides which block the user has to
    re-attempt."""
    from pathlib import Path

    tab_path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "SolutionsDesignTab.js"
    )
    if not tab_path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")

    src = tab_path.read_text(encoding="utf-8")
    # Both the preview and replace failure paths must include the
    # captured label. We assert the variable name is referenced in
    # the failure-toast neighborhood rather than a literal label
    # string so this lock survives refactors that change the toast
    # copy.
    assert "failureLabel" in src, (
        "Regenerate failure path must capture the section label at "
        "call time and include it in the toast so concurrent regen "
        "requests on different blocks do not report under the wrong "
        "label."
    )
    # The label must be interpolated into both toasts.
    assert "failureLabel}”)" in src or "${failureLabel}" in src, (
        "Regenerate failure toast must interpolate failureLabel "
        "into the toast string."
    )


def test_editor_architecture_adr_exists_and_records_decision():
    """The editor-architecture decision is load-bearing for rows 3
    and 4 of the master build tickboard. The ADR must exist on disk
    and must record (a) that an internal block editor was chosen and
    (b) that the embedded Google Docs iframe was rejected. Without
    this lock, a future agent can write a contradictory ADR and the
    no-iframe regression in this same test file becomes ambiguous."""
    from pathlib import Path

    adr = (
        Path(__file__).resolve().parents[1].parent
        / "docs"
        / "build"
        / "adr-sow-editor-architecture.md"
    )
    assert adr.exists(), (
        "Editor architecture ADR must exist at "
        "docs/build/adr-sow-editor-architecture.md."
    )
    src = adr.read_text(encoding="utf-8")
    # The decision must be unambiguous in plain text — a reader
    # scanning the doc must not have to interpret hedged language.
    assert "Adopt an internal block editor architecture" in src, (
        "ADR must explicitly adopt the internal block editor."
    )
    assert "Reject the embedded Google Docs iframe" in src, (
        "ADR must explicitly reject the iframe alternative."
    )
    # The ADR must point at the block registry as the contract that
    # future editor surfaces read against.
    assert "blockRegistry.js" in src, (
        "ADR must name blockRegistry.js as the editor contract."
    )


def test_block_registry_exists_with_canonical_taxonomy():
    """Source-level lock on the SOW block taxonomy. The frontend
    registry must enumerate the five canonical block types — prose
    required, prose optional, milestone, bullet, product item — that
    mirror the live Pydantic models in backend/models.py. Without
    this lock, a future refactor could silently drop a block type
    and the editor surface would have no honest way to refuse
    persisting a block it cannot address."""
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "sow-builder"
        / "blockRegistry.js"
    )
    if not path.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    src = path.read_text(encoding="utf-8")
    # All five tokens must be present in the BLOCK_TYPES export.
    for token_name, token_value in (
        ("PROSE_REQUIRED", "prose_required"),
        ("PROSE_OPTIONAL", "prose_optional"),
        ("MILESTONE", "milestone"),
        ("BULLET", "bullet"),
        ("PRODUCT_ITEM", "product_item"),
    ):
        assert token_name in src, (
            f"blockRegistry.js must export BLOCK_TYPES.{token_name}."
        )
        assert f"'{token_value}'" in src, (
            f"BLOCK_TYPES.{token_name} must use the stable string "
            f"'{token_value}'; renaming this token would break editor "
            "addressing on every consumer."
        )
    # Address-field mappings must match the live Pydantic models —
    # prose blocks addressed by kind, milestones by milestone_id,
    # bullets by bullet_id, product items by item_id.
    for field in ("milestone_id", "bullet_id", "item_id"):
        assert field in src, (
            f"blockRegistry.js must reference {field} as the address "
            "field for its block type."
        )
    # Capability flags must be present so the registry is descriptive
    # rather than aspirational.
    for flag in ("reorderable", "regenerable", "removable", "editable", "crossParent"):
        assert flag in src, (
            f"blockRegistry.js must declare the {flag} capability flag."
        )


def test_block_shell_exists_and_carries_block_address_attributes():
    """The block shell foundation must exist and must forward block
    addressing through data attributes. Automated test selectors and
    future per-block UI features (analytics, A/B flags, block-level
    history) bind to `data-block-type` + `data-block-id` so they
    never reach for an array index. Without this lock, a future
    refactor could quietly drop those attributes and consumers would
    fall back to brittle DOM-position selectors."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1].parent
    shell = (
        repo_root
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "sow-builder"
        / "BlockShell.js"
    )
    if not shell.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    shell_src = shell.read_text(encoding="utf-8")
    assert "export default function BlockShell" in shell_src, (
        "BlockShell.js must export a default React component."
    )
    assert "data-block-type" in shell_src, (
        "BlockShell must forward block type via a data attribute."
    )
    assert "data-block-id" in shell_src, (
        "BlockShell must forward block id via a data attribute."
    )


def test_rich_prose_section_consumes_block_shell_and_registry():
    """Source-level lock on the Canva-like block canvas migration:
    RichProseSection now drives its outer chrome through BlockShell
    and derives capability gating from blockRegistry. Without this
    lock, a future refactor could quietly re-implement the card
    chrome and the block taxonomy would drift away from the registry
    that the ADR names as the editor contract."""
    from pathlib import Path

    rich = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "sow-builder"
        / "RichProseSection.js"
    )
    if not rich.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    src = rich.read_text(encoding="utf-8")
    assert "import BlockShell from './BlockShell'" in src, (
        "RichProseSection must consume BlockShell — the canvas chrome "
        "lives in the shell, not in scattered outer <div>s."
    )
    assert "BLOCK_TYPES" in src and "from './blockRegistry'" in src, (
        "RichProseSection must read block type tokens + capability "
        "guards from the registry, not hard-code strings."
    )
    # The shell must be the JSX root of the render — there must be a
    # <BlockShell ...> open and a </BlockShell> close, and no
    # competing outer <div ... data-testid=`rich-prose-${kind}`> from
    # the pre-migration code.
    assert "<BlockShell" in src, (
        "RichProseSection render must use BlockShell as the JSX root."
    )
    assert "</BlockShell>" in src, (
        "RichProseSection render must close BlockShell."
    )
    assert "data-testid={`rich-prose-${kind}`}" not in src, (
        "The pre-migration outer <div data-testid=`rich-prose-${kind}`> "
        "must be gone — testId is now forwarded through BlockShell."
    )


def test_prose_block_type_resolves_from_removable_prop():
    """The registry distinguishes required prose (non-removable) from
    optional prose (removable). RichProseSection must derive its
    blockType from the existing `removable` prop so the canvas chrome
    advertises the correct block type via data-block-type and the
    capability guards behave correctly. Without this lock the two
    block types could silently collapse into one and the registry
    flags would have nothing to gate against."""
    from pathlib import Path

    rich = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "sow-builder"
        / "RichProseSection.js"
    )
    if not rich.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    src = rich.read_text(encoding="utf-8")
    # The exact derivation expression is locked so the polarity does
    # not silently flip in a future refactor — removable ⇒ optional,
    # !removable ⇒ required.
    assert (
        "removable ? BLOCK_TYPES.PROSE_OPTIONAL : BLOCK_TYPES.PROSE_REQUIRED"
        in src
    ), (
        "RichProseSection must derive blockType from the `removable` "
        "prop as: removable ⇒ PROSE_OPTIONAL, !removable ⇒ "
        "PROSE_REQUIRED. Flipping the polarity here breaks the "
        "registry capability gates (required prose is non-removable; "
        "optional prose is removable)."
    )


def test_prose_block_addresses_by_kind_not_array_index():
    """Section identity in the canvas must be the `kind` string
    (which mirrors SOWProseSection.kind in backend/models.py). The
    shell exposes that identity through `blockId={kind}` so analytics
    selectors, per-block UI, and audit trails all key off the stable
    address. Without this lock, a future refactor could pass an
    array index here and every downstream consumer would lose stable
    addressing the moment a section is reordered or removed."""
    from pathlib import Path

    rich = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "sow-builder"
        / "RichProseSection.js"
    )
    if not rich.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    src = rich.read_text(encoding="utf-8")
    assert "blockId={kind}" in src, (
        "RichProseSection must address its block by `kind`, not an "
        "array index — section identity is the kind string per "
        "SOWProseSection.kind in backend/models.py."
    )


def test_regenerate_wiring_intact_after_canvas_migration():
    """After the block-canvas migration, the per-section Regenerate
    button must still fire onOpenRegenerate, and the modal must still
    receive citations + label. The migration touches the outer
    chrome, not the regenerate path — this test makes that explicit
    so a refactor that hoists regenerate logic into the shell cannot
    silently drop the proven preview-by-default flow."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1].parent
    rich = (
        repo_root
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "sow-builder"
        / "RichProseSection.js"
    )
    tab = (
        repo_root
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "SolutionsDesignTab.js"
    )
    if not (rich.exists() and tab.exists()):  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    rich_src = rich.read_text(encoding="utf-8")
    tab_src = tab.read_text(encoding="utf-8")

    # Per-section regenerate button still wired to onOpenRegenerate.
    assert "onClick={onOpenRegenerate}" in rich_src, (
        "RichProseSection must still wire its Regenerate button to "
        "onOpenRegenerate after the canvas migration."
    )
    # The Regenerate button must be gated by both the caller opt-in
    # AND the registry capability — `showRegenerate` ANDs them.
    assert "showRegenerate" in rich_src and "isRegenerable(blockType)" in rich_src, (
        "RichProseSection must gate the Regenerate button on both "
        "canRegenerate (caller opt-in) AND isRegenerable(blockType) "
        "(registry guard) so a buggy caller cannot surface a "
        "Regenerate button on a block type with no regenerate path."
    )
    # Modal still receives citations from the tab.
    assert "citations={regenCitations}" in tab_src, (
        "SolutionsDesignTab must still thread regenCitations into "
        "the RegenerateSectionModal after the canvas migration."
    )


def test_milestone_card_consumes_block_shell_and_registry():
    """The phase block (MilestoneCard) must render through BlockShell
    so the SOW canvas shares one card chrome across prose, phase, and
    task blocks. The migration must also import BLOCK_TYPES from the
    registry so the block taxonomy is centralized — a future refactor
    that hard-codes the string "milestone" would drift away from the
    registry that the ADR names as the editor contract."""
    from pathlib import Path

    tab = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "SolutionsDesignTab.js"
    )
    if not tab.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    src = tab.read_text(encoding="utf-8")

    assert "import BlockShell from './sow-builder/BlockShell'" in src, (
        "SolutionsDesignTab must import BlockShell for the structured "
        "block migration."
    )
    assert "BLOCK_TYPES" in src and "from './sow-builder/blockRegistry'" in src, (
        "SolutionsDesignTab must import BLOCK_TYPES from the block "
        "registry instead of hard-coding milestone/bullet strings."
    )
    # MilestoneCard outer renders through <BlockShell blockType={BLOCK_TYPES.MILESTONE} ...>
    assert "blockType={BLOCK_TYPES.MILESTONE}" in src, (
        "MilestoneCard must render through BlockShell with "
        "blockType={BLOCK_TYPES.MILESTONE}."
    )
    # Pre-migration outer <div className="bg-aegisaxiom-base border rounded-lg ...">
    # used to be the JSX root of MilestoneCard. After migration it must
    # be gone — the chrome lives in BlockShell.
    assert (
        'className={`bg-aegisaxiom-base border rounded-lg overflow-hidden '
        'transition-colors ${'
    ) not in src, (
        "Pre-migration outer MilestoneCard <div> must be gone — its "
        "chrome is now provided by BlockShell."
    )


def test_milestone_block_addresses_by_milestone_id_not_array_index():
    """Phase block identity must be the SOWMilestone.milestone_id
    string, not an array index. Selectors, audit trails, analytics,
    and future per-block UI all bind to the stable id; an array
    index would invalidate on every reorder or delete."""
    from pathlib import Path

    tab = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "SolutionsDesignTab.js"
    )
    if not tab.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    src = tab.read_text(encoding="utf-8")
    assert "blockId={milestone.milestone_id}" in src, (
        "MilestoneCard must address its block by milestone.milestone_id, "
        "never an array index."
    )


def test_bullet_row_consumes_block_shell_with_surface_variant():
    """Task (bullet) rows must render through BlockShell with
    variant='surface' so the canvas distinguishes nested blocks from
    top-level cards visually while still sharing the chrome. The
    drag-source remains the inner GripVertical button — only the
    outer wrapper changes."""
    from pathlib import Path

    tab = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "SolutionsDesignTab.js"
    )
    if not tab.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    src = tab.read_text(encoding="utf-8")
    assert "blockType={BLOCK_TYPES.BULLET}" in src, (
        "Bullet rows must render through BlockShell with "
        "blockType={BLOCK_TYPES.BULLET}."
    )
    assert 'variant="surface"' in src, (
        "Bullet rows must use BlockShell variant='surface' for the "
        "nested-block background."
    )
    # The pre-migration bullet outer <div className={`bg-aegisaxiom-surface
    # border rounded-lg p-3 transition-colors ${...}`}> must be gone.
    assert "bg-aegisaxiom-surface border rounded-lg p-3 transition-colors ${" not in src, (
        "Pre-migration bullet outer <div> must be gone — chrome is "
        "now provided by BlockShell."
    )


def test_bullet_block_addresses_by_bullet_id_not_array_index():
    """Task block identity must be the SOWBullet.bullet_id string,
    not bulletIdx. Reordering or removing a bullet would invalidate
    any consumer that bound to bulletIdx."""
    from pathlib import Path

    tab = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "SolutionsDesignTab.js"
    )
    if not tab.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    src = tab.read_text(encoding="utf-8")
    assert "blockId={bullet.bullet_id}" in src, (
        "Bullet rows must address their block by bullet.bullet_id, "
        "never bulletIdx."
    )


def test_dnd_handlers_remain_wired_after_structured_block_migration():
    """The three-layer drag/drop reorder (prose / phase / bullet)
    must continue to work after the structured-block migration.
    BlockShell wires drop-target handlers on the outer card while the
    inner GripVertical buttons remain the drag sources. This test
    locks both halves — the existing handler names + the
    grip-handle pattern — so a refactor that moves DnD into the
    shell cannot silently strand the reorder."""
    from pathlib import Path

    tab = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "SolutionsDesignTab.js"
    )
    if not tab.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    src = tab.read_text(encoding="utf-8")

    # Phase-level drop handlers must still pass through the milestone
    # BlockShell.
    assert "onMilestoneDragOver" in src, (
        "Phase drag-over handler must remain wired after migration."
    )
    assert "onMilestoneDrop" in src, (
        "Phase drop handler must remain wired after migration."
    )
    # Phase drag handle (inner button) must still own onDragStart /
    # onDragEnd so the outer can be drop-target-only without losing
    # the drag source.
    assert (
        "milestone-drag-handle-" in src
        and "onDragStart={onMilestoneDragStart}" in src
    ), (
        "Phase drag handle button must keep its onDragStart wiring."
    )
    # Bullet-level drop handlers must still pass through the bullet
    # BlockShell.
    assert "handleBulletDragOver(bullet.bullet_id)" in src, (
        "Bullet drag-over handler must remain wired after migration."
    )
    assert "handleBulletDrop(bullet.bullet_id)" in src, (
        "Bullet drop handler must remain wired after migration."
    )
    # Bullet drag handle (inner button) must still own onDragStart /
    # onDragEnd.
    assert (
        "bullet-drag-handle-" in src
        and "onDragStart={handleBulletDragStart(bullet.bullet_id)}" in src
    ), (
        "Bullet drag handle button must keep its onDragStart wiring."
    )


def test_subtask_model_exists_and_bullet_accepts_default_empty_list():
    """The active SOW data model is now three levels deep
    (SOWMilestone.bullets[] of SOWBullet.subtasks[] of SOWSubtask).
    The substrate doc reconciliation entry in DOC_FEEDBACK.md (2026-05-20)
    is now resolved by the addition of the SOWSubtask model and the
    extension of SOWBullet. Backward compatibility: SOWBullet's
    `subtasks` field defaults to an empty list so existing SOW
    documents that have no subtasks deserialize unchanged."""
    from models import SOWSubtask, SOWBullet

    # SOWSubtask carries a stable id, free-text description, and
    # persisted display order. Nothing else.
    subtask = SOWSubtask(subtask_id="st_test_1", description="do thing", order=0)
    assert subtask.subtask_id == "st_test_1"
    assert subtask.description == "do thing"
    assert subtask.order == 0

    # Default description is empty string so a freshly-added subtask
    # round-trips even when the user has not typed anything yet.
    empty = SOWSubtask(subtask_id="st_test_2")
    assert empty.description == ""
    assert empty.order == 0

    # SOWBullet without a `subtasks` field deserializes with empty
    # subtasks — this is the backward-compatibility contract.
    bullet_old = SOWBullet(bullet_id="b_old", bullet_text="task")
    assert bullet_old.subtasks == []

    # SOWBullet accepts a `subtasks` list when provided. Subtask
    # ordering is read from the input order, not invented.
    bullet_new = SOWBullet(
        bullet_id="b_new",
        bullet_text="task with subtasks",
        subtasks=[
            SOWSubtask(subtask_id="st_a", description="first", order=0),
            SOWSubtask(subtask_id="st_b", description="second", order=1),
        ],
    )
    assert [s.subtask_id for s in bullet_new.subtasks] == ["st_a", "st_b"]


def test_sow_create_round_trips_milestone_bullet_subtask_hierarchy():
    """The SOWCreate validator (used by the SOW POST/PATCH route) must
    accept the full milestone → bullet → subtask hierarchy without
    stripping the subtasks field. Without this lock, a Pydantic v2
    `extra='ignore'` could silently drop subtasks on the wire."""
    from models import SOWCreate, SOWMilestone, SOWBullet, SOWSubtask

    payload = SOWCreate(
        milestones=[
            SOWMilestone(
                milestone_id="ms_1",
                milestone_name="Phase 1",
                bullets=[
                    SOWBullet(
                        bullet_id="b_1",
                        bullet_text="Task with subtasks",
                        subtasks=[
                            SOWSubtask(subtask_id="st_1", description="A", order=0),
                            SOWSubtask(subtask_id="st_2", description="B", order=1),
                        ],
                    ),
                ],
            ),
        ],
    )
    assert payload.milestones[0].bullets[0].subtasks[0].subtask_id == "st_1"
    assert payload.milestones[0].bullets[0].subtasks[1].order == 1


def test_block_registry_includes_subtask_with_subtask_id_address():
    """blockRegistry must expose BLOCK_TYPES.SUBTASK and address it by
    `subtask_id` — never an array index. Pairs with the subtask UI
    that uses blockId={subtask.subtask_id} via the BlockShell."""
    from pathlib import Path

    registry = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "sow-builder"
        / "blockRegistry.js"
    )
    if not registry.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    src = registry.read_text(encoding="utf-8")
    assert "SUBTASK: 'subtask'" in src, (
        "blockRegistry.js must export BLOCK_TYPES.SUBTASK with the "
        "stable string 'subtask'."
    )
    assert "[BLOCK_TYPES.SUBTASK]: 'subtask_id'" in src, (
        "blockRegistry.js must address subtasks by 'subtask_id', "
        "never an array index."
    )
    # Capability flag block for SUBTASK must be present so editor
    # surfaces can read reorderable/crossParent without inferring.
    assert "[BLOCK_TYPES.SUBTASK]: Object.freeze({" in src, (
        "blockRegistry.js must declare a CAPABILITIES entry for "
        "BLOCK_TYPES.SUBTASK."
    )


def test_solutions_design_tab_renders_subtask_block_shell():
    """The SOW Builder editor surface must render subtask blocks
    through BlockShell with BLOCK_TYPES.SUBTASK and address them by
    subtask_id. Locks the migration: future refactors cannot quietly
    drop the subtask-on-canvas wiring."""
    from pathlib import Path

    tab = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "SolutionsDesignTab.js"
    )
    if not tab.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    src = tab.read_text(encoding="utf-8")

    # Each subtask renders inside a BlockShell tagged with the
    # SUBTASK block type.
    assert "blockType={BLOCK_TYPES.SUBTASK}" in src, (
        "Subtask rows must render through BlockShell with "
        "blockType={BLOCK_TYPES.SUBTASK}."
    )
    # Identity is the subtask_id string, never an array index.
    assert "blockId={subtask.subtask_id}" in src, (
        "Subtask rows must address their block by subtask.subtask_id, "
        "never subtaskIdx."
    )
    # The subtask drag handle exposes a stable test selector so the
    # browser smoke can grab the handle reliably.
    assert "subtask-drag-handle-${subtask.subtask_id}" in src, (
        "Subtask drag handle must carry data-testid="
        "\"subtask-drag-handle-{subtask_id}\"."
    )
    # The add-subtask button must exist per bullet so users can lazily
    # promote a flat bullet to a bullet-with-subtasks.
    assert "add-subtask-${bullet.bullet_id}" in src, (
        "Each bullet must carry an Add subtask button with "
        "data-testid=\"add-subtask-{bullet_id}\"."
    )
    # Subtask drag handlers must be wired on the BlockShell.
    assert "handleSubtaskDragStart(bullet.bullet_id" in src, (
        "Subtask drag-start must be scoped by parent bullet so "
        "crossParent=false is honored at the handler level."
    )
    assert "handleSubtaskDrop(bullet.bullet_id" in src, (
        "Subtask drop must be scoped by parent bullet."
    )


def test_subtask_drag_refuses_cross_parent_moves():
    """crossParent=false for subtasks. The handler must short-circuit
    when a subtask is dragged from bullet A and released on bullet B —
    the active data model has no notion of moving a subtask between
    parent bullets, so cross-parent drops must be a silent no-op.
    Locks the source-level check that the handler compares bullet_id
    before persisting."""
    from pathlib import Path

    tab = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "SolutionsDesignTab.js"
    )
    if not tab.exists():  # pragma: no cover
        pytest.skip("frontend not mounted in this test image")
    src = tab.read_text(encoding="utf-8")
    # The drop handler must compare the dragging bullet to the target
    # bullet and refuse the operation on mismatch.
    assert "sourceBulletId !== bulletId" in src, (
        "Subtask drop handler must compare source and target bullet "
        "ids and silently refuse cross-parent moves (crossParent=false)."
    )


def test_no_new_editor_framework_dependency_added():
    """The ADR rejects adding a new editor framework dependency
    (TipTap, Slate, ProseMirror, Google Docs SDK) in the foundation
    commit because the build sandbox cannot regenerate yarn.lock.
    This test holds the line — any future PR that adds one must
    rewrite this assertion to acknowledge the lockfile bump
    explicitly, rather than silently slip a new framework in."""
    from pathlib import Path

    pkg = (
        Path(__file__).resolve().parents[1].parent
        / "frontend"
        / "package.json"
    )
    if not pkg.exists():  # pragma: no cover
        pytest.skip("frontend package.json not mounted in this test image")
    src = pkg.read_text(encoding="utf-8")
    forbidden = (
        "@tiptap/",
        '"slate"',
        '"slate-react"',
        "prosemirror-",
        "@googleapis/docs",
        "googleapis-common",
        "google-auth-library",
    )
    for token in forbidden:
        assert token not in src, (
            f"package.json must not introduce {token} in the editor "
            "foundation commit — the ADR explicitly defers any new "
            "editor framework / Google Docs SDK to a workstream that "
            "can run yarn install."
        )


def test_regenerate_route_carries_decorator_level_ai_permission_gate():
    """Source-level contract: the regenerate route must declare a
    decorator-level ``require_ai_permission("sales_ops.solutions_design.edit")``
    dependency in addition to the in-handler ``has_perm`` check. The
    pattern is defense-in-depth: AI callers route through the
    dependency-graph gate, human callers also pass the in-handler
    check. PR review for Round 9 flagged the route as missing the
    decorator gate; this test prevents re-regression."""
    from pathlib import Path

    routes_py = (
        Path(__file__).resolve().parents[1] / "routes" / "solutions_design.py"
    )
    if not routes_py.exists():  # pragma: no cover
        pytest.skip("backend routes not mounted in this test image")
    src = routes_py.read_text(encoding="utf-8")

    # The module must import require_ai_permission from the canonical
    # middleware module — other code in this repo uses the same path.
    assert "from middleware.ai_authz import require_ai_permission" in src, (
        "solutions_design.py must import require_ai_permission from "
        "middleware.ai_authz; the dependency-graph gate cannot be "
        "wired without it."
    )

    # The regenerate route must declare the decorator gate. The check
    # is structural — locate the route's POST decorator and assert the
    # require_ai_permission dependency appears in the same decorator.
    needle_post = (
        '"/opportunities/{opportunity_id}/sows/{sow_id}/sections/'
        '{section_kind}/regenerate"'
    )
    idx = src.find(needle_post)
    assert idx != -1, (
        "Could not find the regenerate route path literal in "
        "solutions_design.py — the source-level lock cannot verify "
        "the decorator gate."
    )
    # Read forward to the route function definition; the decorator
    # block ends at "async def regenerate_sow_section".
    end = src.find("async def regenerate_sow_section", idx)
    assert end != -1, (
        "Regenerate route function definition not found after the "
        "path literal."
    )
    decorator_block = src[idx:end]
    assert (
        'require_ai_permission("sales_ops.solutions_design.edit")'
        in decorator_block
    ), (
        "The regenerate route decorator must include "
        'dependencies=[Depends(require_ai_permission'
        '("sales_ops.solutions_design.edit"))] as the decorator-level '
        "AI-caller gate; without it, AI permissions are evaluated "
        "only on the in-handler has_perm check."
    )

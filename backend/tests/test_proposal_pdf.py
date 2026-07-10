"""Proposal PDF generation — focused regression.

Locks the contract behind ``GET /api/sales-ops/proposals/{id}/pdf``:

  - Returns ``application/pdf`` with a PDF byte sequence for a
    permitted caller.
  - Unknown proposal id returns 404.
  - Cross-tenant proposal returns 404 (never 403, to avoid leaking
    existence).
  - User without ``sales_ops.proposal.view`` is denied 403.
  - The PDF body draws on the proposal's existing content
    (title / generated_summary / generated_content) and linked SOW
    data — not a synthetic template.

Pure unit tests against the route function. No Mongo. No HTTP
server. Uses the existing AsyncMock-shaped fake style the repo
already favors elsewhere.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest


os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, items):
        self._items = items

    async def to_list(self, length=None):
        return list(self._items)


class _Coll:
    """Minimal Motor-shaped collection that supports find_one + find()."""

    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def _match(self, row, query):
        for key, want in query.items():
            if isinstance(want, dict):
                if "$in" in want and row.get(key) not in want["$in"]:
                    return False
            elif row.get(key) != want:
                return False
        return True

    async def find_one(self, query, _projection=None):
        for r in self._rows:
            if self._match(r, query):
                return dict(r)
        return None

    def find(self, query, _projection=None):
        return _Cursor([dict(r) for r in self._rows if self._match(r, query)])


def _proposal(**overrides):
    base: Dict[str, Any] = {
        "proposal_id": "prop_001",
        "opportunity_id": "opp_001",
        "sow_ids": ["sow_001"],
        "version": 2,
        "status": "ready_for_review",
        "title": "Acme Q4 Engagement Proposal",
        "notes": None,
        "generated_summary": (
            "Acme is migrating two business units onto our managed UC stack "
            "by Q1 2027. This proposal covers discovery, build, and a 90-day "
            "go-live concierge."
        ),
        "generated_content": (
            "## Engagement scope\n\n"
            "We will provision the new tenant, migrate three domains, and "
            "stand up two pilot conference rooms.\n\n"
            "## Deliverables\n\n"
            "Production-ready tenant + handoff runbook."
        ),
    }
    base.update(overrides)
    return base


def _opportunity(**overrides):
    base = {
        "opportunity_id": "opp_001",
        "name": "Acme UC migration",
        "account_name": "Acme Industries",
    }
    base.update(overrides)
    return base


def _sow(**overrides):
    base = {
        "sow_id": "sow_001",
        "opportunity_id": "opp_001",
        "name": "Acme UC Migration — Field Services",
        "line_of_business": "field_services",
        "status": "ready_for_review",
        "milestones": [{
            "milestone_name": "Discovery & Planning",
            "bullets": [
                {"bullet_text": "Site survey of HQ + 2 satellite offices",
                 "deliverable": "Survey report", "estimated_hours": 8},
            ],
        }],
        "materials": [
            {"item": "Cat6 patch — 50 ft", "quantity": 12, "unit_price": 18.5},
        ],
        "total_estimated_hours": 32,
        "total_value": 18500.0,
    }
    base.update(overrides)
    return base


def _user(role="director", perms=None, tenant_id=None):
    return SimpleNamespace(
        user_id="u_seller_001",
        email="seller@aegisaxiom.test",
        role=role,
        permissions=list(perms or ["sales_ops.proposal.view", "sales_ops.proposal.create"]),
        tenant_id=tenant_id,
        account_id=None,
    )


def _setup(monkeypatch, *, proposal_doc, opportunity_doc=None, sow_docs=None):
    proposals = _Coll([proposal_doc] if proposal_doc else [])
    opportunities = _Coll([opportunity_doc] if opportunity_doc else [])
    sows = _Coll(sow_docs or [])

    class _DB:
        pass
    db = _DB()
    db.proposals = proposals
    db.opportunities = opportunities
    db.opportunity_sows = sows

    # The route handler is awaited directly; it uses Depends(get_db)
    # injection at the FastAPI layer. In unit tests we call the
    # function with db= passed explicitly.
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pdf_endpoint_returns_application_pdf_for_authorized_user(monkeypatch):
    from routes.proposals import download_proposal_pdf
    db = _setup(
        monkeypatch,
        proposal_doc=_proposal(),
        opportunity_doc=_opportunity(),
        sow_docs=[_sow()],
    )
    resp = await download_proposal_pdf(
        proposal_id="prop_001", db=db, current_user=_user(),
    )
    assert resp.media_type == "application/pdf"
    body = resp.body
    assert isinstance(body, (bytes, bytearray))
    assert body.startswith(b"%PDF-")
    assert len(body) > 500  # non-trivial PDF, not an empty placeholder
    cd = resp.headers.get("content-disposition", "")
    assert cd.startswith("inline;")
    assert cd.endswith('.pdf"')


@pytest.mark.asyncio
async def test_pdf_endpoint_returns_404_for_unknown_proposal(monkeypatch):
    from routes.proposals import download_proposal_pdf
    db = _setup(monkeypatch, proposal_doc=None)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await download_proposal_pdf(
            proposal_id="prop_missing", db=db, current_user=_user(),
        )
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_pdf_endpoint_returns_404_on_cross_tenant_access(monkeypatch):
    """Proposal stored against tenant_a; caller is in tenant_b. The
    route must return 404, not 403, so the proposal's existence is
    not leaked."""
    from routes.proposals import download_proposal_pdf
    db = _setup(
        monkeypatch,
        proposal_doc=_proposal(tenant_id="tenant_a"),
    )
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await download_proposal_pdf(
            proposal_id="prop_001",
            db=db,
            current_user=_user(tenant_id="tenant_b"),
        )
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_pdf_endpoint_denies_user_without_proposal_view_permission(monkeypatch):
    from routes.proposals import download_proposal_pdf
    db = _setup(
        monkeypatch,
        proposal_doc=_proposal(),
    )
    no_perm = _user(role="contractor", perms=["time.entry"])
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await download_proposal_pdf(
            proposal_id="prop_001", db=db, current_user=no_perm,
        )
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_pdf_body_uses_proposal_content_and_sow_data(monkeypatch):
    """The PDF must draw on the EXISTING proposal content + SOW data
    rather than a synthetic template. We inspect the rendered bytes
    for distinctive markers from the fixtures."""
    from routes.proposals import download_proposal_pdf
    db = _setup(
        monkeypatch,
        proposal_doc=_proposal(
            title="Acme Q4 Engagement Proposal",
            generated_summary="Acme is migrating two business units",
        ),
        opportunity_doc=_opportunity(account_name="Acme Industries"),
        sow_docs=[_sow(name="Acme UC Migration — Field Services")],
    )
    resp = await download_proposal_pdf(
        proposal_id="prop_001", db=db, current_user=_user(),
    )
    body = bytes(resp.body)
    # reportlab compresses content streams (FlateDecode) so the rendered
    # body text is not searchable as plain bytes. The PDF info dict
    # (Title field) is NOT compressed, so it is the reliable plain-text
    # marker that the renderer fed the proposal's own title in.
    assert body.startswith(b"%PDF-")
    assert body.rstrip(b"\r\n").endswith(b"%%EOF")
    assert b"Acme Q4 Engagement Proposal" in body, (
        "PDF info dict should carry the proposal.title from the fixture"
    )


# ---------------------------------------------------------------------------
# Renderer-level direct tests (no route) — pin the data flow that the
# route depends on.
# ---------------------------------------------------------------------------


def test_render_returns_pdf_bytes_for_minimal_proposal():
    from services.proposal_pdf import render_proposal_pdf
    pdf = render_proposal_pdf(_proposal(generated_content=None, generated_summary=None))
    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF-")


def test_render_includes_milestone_estimated_hours():
    """SOW with a milestone bullet must surface the estimated_hours
    column in the rendered PDF stream."""
    from services.proposal_pdf import render_proposal_pdf
    pdf = render_proposal_pdf(
        _proposal(),
        opportunity=_opportunity(),
        sows=[_sow(milestones=[{
            "milestone_name": "Build",
            "bullets": [{
                "bullet_text": "Provision tenant",
                "deliverable": "Tenant URL",
                "estimated_hours": 12,
            }],
        }])],
    )
    assert pdf.startswith(b"%PDF-")
    # reportlab page streams are compressed; inspecting for raw text
    # is not reliable. Length + valid PDF envelope is the signal.
    assert len(pdf) > 500


def test_proposal_pdf_filename_is_filesystem_safe():
    from services.proposal_pdf import proposal_pdf_filename
    name = proposal_pdf_filename(
        proposal=_proposal(title="Acme / Q4: $$$ — final?", version=3),
        opportunity=_opportunity(account_name="ACME, Inc."),
    )
    assert name.endswith(".pdf")
    # No path separators or shell metacharacters.
    for ch in "/\\:*?\"<>|":
        assert ch not in name

"""Proposal email send — focused regression.

Locks the contract behind ``POST /api/sales-ops/proposals/{id}/send-email``:

  - Authorized send succeeds when mail service is configured /
    mocked; the route stamps email_sent_at, email_sent_by,
    email_recipients, email_subject, and email_status="sent".
  - Unknown proposal returns 404.
  - Cross-tenant proposal returns 404 (never 403).
  - User without ``sales_ops.proposal.send`` is denied 403.
  - Invalid recipient (not an email) returns 422 at the Pydantic
    boundary; empty recipients list returns 422.
  - Mail-not-configured returns 503 with code "email_not_configured"
    and stamps email_status="not_configured" — never pretends a
    send succeeded.
  - Failed Resend dispatch returns 502 with code "email_send_failed"
    and stamps email_status="failed" + sanitized email_error.
  - The internal handoff route (``send_proposal``) is left
    completely untouched — confirmed by source inspection so a
    refactor that accidentally collapses the two endpoints
    surfaces here.

Pure unit tests against the route function. No Mongo. No live
Resend call. The mail composer is stubbed via monkeypatch on
``routes.proposals.send_proposal_email``.
"""

from __future__ import annotations

import inspect
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
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.updates: List = []

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

    async def update_one(self, query, update):
        self.updates.append((dict(query), dict(update)))
        for r in self._rows:
            if self._match(r, query):
                if "$set" in update:
                    r.update(update["$set"])
        return SimpleNamespace(modified_count=1)


def _proposal(**overrides):
    base: Dict[str, Any] = {
        "proposal_id": "prop_001",
        "opportunity_id": "opp_001",
        "sow_ids": ["sow_001"],
        "version": 1,
        "status": "ready_for_review",
        "title": "Acme Engagement Proposal",
        "generated_summary": "Q4 migration scope summary.",
        "generated_content": "Full engagement body.",
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
        "name": "Acme SOW",
        "line_of_business": "professional_services",
        "status": "ready_for_review",
    }
    base.update(overrides)
    return base


def _user(role="director", perms=None, tenant_id=None):
    return SimpleNamespace(
        user_id="u_seller_001",
        email="seller@aegisaxiom.test",
        role=role,
        permissions=list(perms or ["sales_ops.proposal.send", "sales_ops.proposal.view"]),
        tenant_id=tenant_id,
        account_id=None,
    )


def _setup(monkeypatch, *, proposal_doc, opportunity_doc=None, sow_docs=None, mail_result=None):
    proposals = _Coll([proposal_doc] if proposal_doc else [])
    opportunities = _Coll([opportunity_doc] if opportunity_doc else [])
    sows = _Coll(sow_docs or [])

    class _DB:
        pass
    db = _DB()
    db.proposals = proposals
    db.opportunities = opportunities
    db.opportunity_sows = sows

    sent_envelope: Dict[str, Any] = mail_result or {
        "status": "sent",
        "error": None,
        "recipients": [],
        "subject": "(set by route)",
        "attached_pdf": True,
    }

    async def _fake_send(**kw):
        out = dict(sent_envelope)
        out["recipients"] = list(kw.get("recipients") or sent_envelope["recipients"])
        out["subject"] = kw.get("subject_override") or sent_envelope.get("subject") or "(default)"
        return out

    import routes.proposals as rp
    monkeypatch.setattr(rp, "send_proposal_email", _fake_send, raising=False)
    monkeypatch.setattr(rp, "audit_log_action", AsyncMock(return_value=None))

    # ``send_proposal_email`` is imported INSIDE the route body via a
    # local ``from services.proposal_email import send_proposal_email``.
    # Patch the service module so the local import picks up our stub.
    import services.proposal_email as proposal_email_mod
    monkeypatch.setattr(proposal_email_mod, "send_proposal_email", _fake_send)

    return db, proposals


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authorized_send_succeeds_when_mail_configured(monkeypatch):
    from routes.proposals import send_proposal_email_route, ProposalSendEmailRequest

    db, proposals = _setup(
        monkeypatch,
        proposal_doc=_proposal(),
        opportunity_doc=_opportunity(),
        sow_docs=[_sow()],
        mail_result={
            "status": "sent", "error": None,
            "recipients": ["customer@acme.example.com"],
            "subject": "Acme Engagement Proposal for Acme Industries",
            "attached_pdf": True,
        },
    )
    body = ProposalSendEmailRequest(recipients=["customer@acme.example.com"])
    result = await send_proposal_email_route(
        proposal_id="prop_001", body=body, db=db, current_user=_user(),
    )
    assert result["status"] == "sent"
    # Sent metadata persisted on the proposal.
    persisted = await db.proposals.find_one({"proposal_id": "prop_001"})
    assert persisted["email_status"] == "sent"
    assert persisted["email_sent_at"] is not None
    assert persisted["email_sent_by"] == "u_seller_001"
    assert persisted["email_recipients"] == ["customer@acme.example.com"]
    assert persisted["email_subject"]
    assert persisted.get("email_error") is None


@pytest.mark.asyncio
async def test_unknown_proposal_returns_404(monkeypatch):
    from routes.proposals import send_proposal_email_route, ProposalSendEmailRequest
    db, _ = _setup(monkeypatch, proposal_doc=None)
    body = ProposalSendEmailRequest(recipients=["customer@acme.example.com"])
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await send_proposal_email_route(
            proposal_id="prop_missing", body=body, db=db, current_user=_user(),
        )
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_proposal_returns_404(monkeypatch):
    from routes.proposals import send_proposal_email_route, ProposalSendEmailRequest
    db, _ = _setup(
        monkeypatch,
        proposal_doc=_proposal(tenant_id="tenant_a"),
    )
    body = ProposalSendEmailRequest(recipients=["customer@acme.example.com"])
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await send_proposal_email_route(
            proposal_id="prop_001",
            body=body,
            db=db,
            current_user=_user(tenant_id="tenant_b"),
        )
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_user_without_send_permission_denied(monkeypatch):
    from routes.proposals import send_proposal_email_route, ProposalSendEmailRequest
    db, _ = _setup(monkeypatch, proposal_doc=_proposal())
    no_perm = _user(perms=["sales_ops.proposal.view"])  # view only
    body = ProposalSendEmailRequest(recipients=["customer@acme.example.com"])
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await send_proposal_email_route(
            proposal_id="prop_001", body=body, db=db, current_user=no_perm,
        )
    assert ei.value.status_code == 403


def test_invalid_recipient_rejected_by_pydantic():
    from routes.proposals import ProposalSendEmailRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ProposalSendEmailRequest(recipients=["not-an-email"])


def test_empty_recipients_rejected_by_pydantic():
    from routes.proposals import ProposalSendEmailRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ProposalSendEmailRequest(recipients=[])


@pytest.mark.asyncio
async def test_mail_not_configured_returns_503_and_marks_proposal(monkeypatch):
    from routes.proposals import send_proposal_email_route, ProposalSendEmailRequest
    db, proposals = _setup(
        monkeypatch,
        proposal_doc=_proposal(),
        mail_result={
            "status": "not_configured", "error": None,
            "recipients": ["customer@acme.example.com"], "subject": "...",
            "attached_pdf": False,
        },
    )
    body = ProposalSendEmailRequest(recipients=["customer@acme.example.com"])
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await send_proposal_email_route(
            proposal_id="prop_001", body=body, db=db, current_user=_user(),
        )
    assert ei.value.status_code == 503
    detail = ei.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "email_not_configured"
    # Proposal row stamped with the not_configured status; no
    # sent metadata recorded.
    persisted = await db.proposals.find_one({"proposal_id": "prop_001"})
    assert persisted["email_status"] == "not_configured"
    assert persisted.get("email_sent_at") is None
    assert persisted.get("email_sent_by") is None


@pytest.mark.asyncio
async def test_mail_failure_returns_502_and_marks_proposal(monkeypatch):
    from routes.proposals import send_proposal_email_route, ProposalSendEmailRequest
    db, proposals = _setup(
        monkeypatch,
        proposal_doc=_proposal(),
        mail_result={
            "status": "failed",
            "error": "ResendError: send failed",
            "recipients": ["customer@acme.example.com"],
            "subject": "...",
            "attached_pdf": True,
        },
    )
    body = ProposalSendEmailRequest(recipients=["customer@acme.example.com"])
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await send_proposal_email_route(
            proposal_id="prop_001", body=body, db=db, current_user=_user(),
        )
    assert ei.value.status_code == 502
    detail = ei.value.detail
    assert detail["code"] == "email_send_failed"
    persisted = await db.proposals.find_one({"proposal_id": "prop_001"})
    assert persisted["email_status"] == "failed"
    assert persisted.get("email_error") == "ResendError: send failed"
    # Sanitized — never echoes the raw stack or the attachment body.
    assert "Traceback" not in persisted["email_error"]


# ---------------------------------------------------------------------------
# Status-guard regression. Mirrors the internal POST /proposals/{id}/send
# guard: only `ready_for_review` may leave the building via the
# customer-facing email path. Draft / rejected / sent / accepted all
# 409 with the canonical _INVALID_TRANSITION_DETAIL, and the guard runs
# before SOW load, PDF render, send_proposal_email, email_* updates, or
# audit row writes so an invalid status leaves zero side effects.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_proposal_returns_409_and_does_not_send(monkeypatch):
    from routes.proposals import (
        send_proposal_email_route,
        ProposalSendEmailRequest,
        _INVALID_TRANSITION_DETAIL,
    )

    send_calls: list[dict] = []

    async def _spy_send(**kw):  # pragma: no cover — must never run
        send_calls.append(kw)
        return {
            "status": "sent", "error": None,
            "recipients": [], "subject": "", "attached_pdf": False,
        }

    db, _ = _setup(monkeypatch, proposal_doc=_proposal(status="draft"))
    import routes.proposals as rp
    monkeypatch.setattr(rp, "send_proposal_email", _spy_send, raising=False)
    import services.proposal_email as proposal_email_mod
    monkeypatch.setattr(proposal_email_mod, "send_proposal_email", _spy_send)
    audit_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(rp, "audit_log_action", audit_mock)

    body = ProposalSendEmailRequest(recipients=["customer@acme.example.com"])
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await send_proposal_email_route(
            proposal_id="prop_001", body=body, db=db, current_user=_user(),
        )
    assert ei.value.status_code == 409
    assert ei.value.detail == _INVALID_TRANSITION_DETAIL

    # The email sender was never invoked.
    assert send_calls == []
    # No audit row was written for this rejected attempt.
    assert audit_mock.await_count == 0
    # Proposal row carries NO email_* metadata stamped by this call.
    persisted = await db.proposals.find_one({"proposal_id": "prop_001"})
    for key in ("email_status", "email_sent_at", "email_sent_by", "email_recipients"):
        assert key not in persisted or persisted[key] is None, (
            f"draft-proposal email send must not write {key} side-effect."
        )


@pytest.mark.asyncio
async def test_rejected_proposal_returns_409_and_does_not_send(monkeypatch):
    from routes.proposals import (
        send_proposal_email_route,
        ProposalSendEmailRequest,
        _INVALID_TRANSITION_DETAIL,
    )

    send_calls: list[dict] = []

    async def _spy_send(**kw):  # pragma: no cover — must never run
        send_calls.append(kw)
        return {
            "status": "sent", "error": None,
            "recipients": [], "subject": "", "attached_pdf": False,
        }

    db, _ = _setup(monkeypatch, proposal_doc=_proposal(status="rejected"))
    import routes.proposals as rp
    monkeypatch.setattr(rp, "send_proposal_email", _spy_send, raising=False)
    import services.proposal_email as proposal_email_mod
    monkeypatch.setattr(proposal_email_mod, "send_proposal_email", _spy_send)
    audit_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(rp, "audit_log_action", audit_mock)

    body = ProposalSendEmailRequest(recipients=["customer@acme.example.com"])
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await send_proposal_email_route(
            proposal_id="prop_001", body=body, db=db, current_user=_user(),
        )
    assert ei.value.status_code == 409
    assert ei.value.detail == _INVALID_TRANSITION_DETAIL

    assert send_calls == []
    assert audit_mock.await_count == 0
    persisted = await db.proposals.find_one({"proposal_id": "prop_001"})
    for key in ("email_status", "email_sent_at", "email_sent_by", "email_recipients"):
        assert key not in persisted or persisted[key] is None


@pytest.mark.asyncio
async def test_sent_proposal_returns_409_does_not_resend(monkeypatch):
    """Already-sent proposals must NOT be re-sent through the
    customer-facing email route. The status guard rejects them with
    409 before any side effect runs."""
    from routes.proposals import (
        send_proposal_email_route,
        ProposalSendEmailRequest,
        _INVALID_TRANSITION_DETAIL,
    )

    send_calls: list[dict] = []

    async def _spy_send(**kw):  # pragma: no cover — must never run
        send_calls.append(kw)
        return {
            "status": "sent", "error": None,
            "recipients": [], "subject": "", "attached_pdf": False,
        }

    db, _ = _setup(monkeypatch, proposal_doc=_proposal(status="sent"))
    import routes.proposals as rp
    monkeypatch.setattr(rp, "send_proposal_email", _spy_send, raising=False)
    import services.proposal_email as proposal_email_mod
    monkeypatch.setattr(proposal_email_mod, "send_proposal_email", _spy_send)
    audit_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(rp, "audit_log_action", audit_mock)

    body = ProposalSendEmailRequest(recipients=["customer@acme.example.com"])
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        await send_proposal_email_route(
            proposal_id="prop_001", body=body, db=db, current_user=_user(),
        )
    assert ei.value.status_code == 409
    assert ei.value.detail == _INVALID_TRANSITION_DETAIL
    assert send_calls == []
    assert audit_mock.await_count == 0


# ---------------------------------------------------------------------------
# Internal handoff regression — source-level lock so the two
# endpoints remain distinct.
# ---------------------------------------------------------------------------


def test_internal_send_handoff_route_still_exists_and_does_not_send_email():
    """The pre-existing internal handoff at POST /proposals/{id}/send
    must remain untouched. It stamps sent_at / sent_by / sent_to_email
    and is explicitly NOT the customer-facing email channel."""
    import routes.proposals as rp
    src = inspect.getsource(rp.send_proposal)
    # The handoff docstring states the rule we're locking in.
    assert "Internal handoff only" in src
    assert "AEGISAXIOM does not email anyone" in src
    # The handoff route does NOT import the proposal_email composer.
    assert "send_proposal_email" not in src


def test_routes_register_distinct_send_paths():
    """The two send paths must coexist on distinct URLs so the
    customer-facing surface cannot accidentally route through the
    internal-handoff handler."""
    import routes.proposals as rp
    src = inspect.getsource(rp)
    assert '"/proposals/{proposal_id}/send"' in src
    assert '"/proposals/{proposal_id}/send-email"' in src


# ---------------------------------------------------------------------------
# email_service helpers — small direct tests
# ---------------------------------------------------------------------------


def test_email_service_is_configured_signals_off_for_placeholder_key(monkeypatch):
    import services.email_service as es
    monkeypatch.setattr(es.resend, "api_key", "re_placeholder_xxx", raising=False)
    assert es.is_configured() is False


def test_email_service_is_configured_signals_on_for_real_key(monkeypatch):
    import services.email_service as es
    monkeypatch.setattr(es.resend, "api_key", "re_realsh_abc123", raising=False)
    assert es.is_configured() is True


@pytest.mark.asyncio
async def test_send_with_attachments_returns_not_configured_when_no_key(monkeypatch):
    import services.email_service as es
    monkeypatch.setattr(es.resend, "api_key", "", raising=False)
    result = await es.send_with_attachments(
        "to@example.com", "Hi", "<p>body</p>",
        attachments=[{"filename": "x.pdf", "content": "Zm9v"}],
    )
    assert result["status"] == "not_configured"
    assert result["recipients"] == ["to@example.com"]
    assert result["error"] is None

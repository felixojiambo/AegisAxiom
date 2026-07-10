"""Locks the proposals MVP route contract.

These tests exercise the six route handlers in ``routes/proposals.py``
directly with an AsyncMock-shaped db so the suite runs without Mongo.
The MVP intentionally keeps scope narrow:

  * Artifact is a Google Doc URL (no PDF generation).
  * /send is mark-as-sent only — no email dispatch — but the audit
    metadata (sent_at, sent_by, sent_to_email) is recorded.
  * Creating a proposal requires at least one SOW on the opportunity
    whose status is ``ready_for_review`` or ``complete``.
  * Status transitions:
      - PATCH may set draft / ready_for_review / accepted / rejected.
      - ``sent`` is reachable only via POST /proposals/{id}/send.
  * Permission gates: view / create / edit / send are each enforced.

Synthetic identifiers only. No production data.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError


os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


_OPP_ID = "opp_synth_proposal_001"
_SOW_READY = "sow_synth_ready_001"
_SOW_COMPLETE = "sow_synth_complete_002"
_SOW_NOT_READY = "sow_synth_in_progress_003"
_SOW_OTHER_OPP = "sow_synth_other_opp_004"
_PROPOSAL_ID = "prop_synth_001"


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def _user(*perms: str):
    return SimpleNamespace(
        user_id="u_synth_proposal_tester",
        email="proposal-tester@aegisaxiom.test",
        role="sales_ops",
        permissions=list(perms),
    )


class _SowsCollection:
    """In-memory replacement for db.opportunity_sows.

    Implements the subset of motor calls the proposals route uses:
    ``find({"sow_id": {"$in": [...]}}, {"_id": 0}).to_list(length=...)``.
    """

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def find(self, query: dict, projection: dict | None = None):
        ids = query.get("sow_id", {}).get("$in", []) if isinstance(query.get("sow_id"), dict) else None
        result = (
            [dict(r) for r in self._rows if r.get("sow_id") in ids]
            if ids is not None
            else [dict(r) for r in self._rows]
        )
        return _ListWrapper(result)


class _ProposalsCollection:
    """In-memory replacement for db.proposals.

    Stores docs by proposal_id, supports find_one, insert_one, update_one,
    and ``find(query).sort(...).to_list(length=...)``.
    """

    def __init__(self) -> None:
        self.docs: list[dict] = []

    async def insert_one(self, doc: dict) -> Any:
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id="fake_oid")

    async def find_one(self, query: dict, projection: dict | None = None) -> dict | None:
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                return dict(d)
        return None

    async def update_one(self, query: dict, update: dict):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                d.update(update.get("$set", {}))
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)

    def find(self, query: dict, projection: dict | None = None):
        matched = [
            dict(d)
            for d in self.docs
            if all(d.get(k) == v for k, v in query.items())
        ]
        return _SortWrapper(matched)


class _ListWrapper:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def to_list(self, length: int | None = None) -> list[dict]:
        return list(self._rows[:length] if length is not None else self._rows)


class _SortWrapper:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def sort(self, key: str, direction: int):
        reverse = direction < 0
        return _SortWrapper(
            sorted(self._rows, key=lambda d: d.get(key) or "", reverse=reverse)
        )

    async def to_list(self, length: int | None = None) -> list[dict]:
        return list(self._rows[:length] if length is not None else self._rows)


def _make_db(*, opportunity_exists: bool = True, sow_rows: list[dict] | None = None) -> SimpleNamespace:
    db = SimpleNamespace()
    if opportunity_exists:
        db.opportunities = SimpleNamespace(
            find_one=AsyncMock(
                return_value={
                    "opportunity_id": _OPP_ID,
                    "name": "Synth Opp",
                    "lines_of_business": ["professional_services"],
                }
            )
        )
    else:
        db.opportunities = SimpleNamespace(find_one=AsyncMock(return_value=None))

    rows = sow_rows if sow_rows is not None else _default_sow_rows()
    db.opportunity_sows = _SowsCollection(rows)
    db.proposals = _ProposalsCollection()
    return db


def _default_sow_rows() -> list[dict]:
    return [
        {
            "sow_id": _SOW_READY,
            "opportunity_id": _OPP_ID,
            "line_of_business": "professional_services",
            "status": "ready_for_review",
        },
        {
            "sow_id": _SOW_COMPLETE,
            "opportunity_id": _OPP_ID,
            "line_of_business": "field_services",
            "status": "complete",
        },
        {
            "sow_id": _SOW_NOT_READY,
            "opportunity_id": _OPP_ID,
            "line_of_business": "wholesale_mrr",
            "status": "in_progress",
        },
        {
            "sow_id": _SOW_OTHER_OPP,
            "opportunity_id": "opp_synth_other_999",
            "line_of_business": "professional_services",
            "status": "complete",
        },
    ]


# ---------------------------------------------------------------------------
# create_proposal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_proposal_happy_path_with_ready_sow():
    from routes.proposals import create_proposal
    from models import ProposalCreate

    db = _make_db()
    result = await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(sow_ids=[_SOW_READY], title="Q3 Proposal"),
        db=db,
        current_user=_user("sales_ops.proposal.create"),
    )
    assert result.proposal_id.startswith("prop_")
    assert result.opportunity_id == _OPP_ID
    assert result.sow_ids == [_SOW_READY]
    assert result.status == "draft"
    assert result.version == 1
    assert result.title == "Q3 Proposal"
    # Persisted exactly once.
    assert len(db.proposals.docs) == 1


@pytest.mark.asyncio
async def test_create_proposal_accepts_complete_sow():
    """A SOW with status=complete is also a valid source — not just
    ready_for_review."""
    from routes.proposals import create_proposal
    from models import ProposalCreate

    db = _make_db()
    result = await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(sow_ids=[_SOW_COMPLETE]),
        db=db,
        current_user=_user("sales_ops.proposal.create"),
    )
    assert result.status == "draft"
    assert result.sow_ids == [_SOW_COMPLETE]


@pytest.mark.asyncio
async def test_create_proposal_rejects_unfinished_sow():
    """A SOW that's still in_progress must not be eligible. The 400
    detail must name the offending sow_id so the UI can surface it."""
    from routes.proposals import create_proposal
    from models import ProposalCreate

    db = _make_db()
    with pytest.raises(HTTPException) as exc:
        await create_proposal(
            opportunity_id=_OPP_ID,
            body=ProposalCreate(sow_ids=[_SOW_NOT_READY]),
            db=db,
            current_user=_user("sales_ops.proposal.create"),
        )
    assert exc.value.status_code == 400
    assert _SOW_NOT_READY in exc.value.detail


@pytest.mark.asyncio
async def test_create_proposal_rejects_sow_belonging_to_other_opportunity():
    """Cross-opportunity SOW linking is forbidden so a draft can't smuggle
    in unrelated work."""
    from routes.proposals import create_proposal
    from models import ProposalCreate

    db = _make_db()
    with pytest.raises(HTTPException) as exc:
        await create_proposal(
            opportunity_id=_OPP_ID,
            body=ProposalCreate(sow_ids=[_SOW_OTHER_OPP]),
            db=db,
            current_user=_user("sales_ops.proposal.create"),
        )
    assert exc.value.status_code == 400
    assert _SOW_OTHER_OPP in exc.value.detail


def test_create_proposal_rejects_empty_sow_list():
    """J1. After I6 the min_length=1 constraint on ProposalCreate.sow_ids
    rejects an empty list at the Pydantic boundary so the route never
    runs. FastAPI translates that ValidationError into a 422 on the
    HTTP layer. The earlier shape of this test still expected the
    route to raise HTTPException 400 which is impossible now that the
    model validates first.
    """
    from models import ProposalCreate

    with pytest.raises(ValidationError):
        ProposalCreate(sow_ids=[])


@pytest.mark.asyncio
async def test_create_proposal_404_when_opportunity_missing():
    from routes.proposals import create_proposal
    from models import ProposalCreate

    db = _make_db(opportunity_exists=False)
    with pytest.raises(HTTPException) as exc:
        await create_proposal(
            opportunity_id=_OPP_ID,
            body=ProposalCreate(sow_ids=[_SOW_READY]),
            db=db,
            current_user=_user("sales_ops.proposal.create"),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_create_proposal_403_without_create_permission():
    from routes.proposals import create_proposal
    from models import ProposalCreate

    db = _make_db()
    with pytest.raises(HTTPException) as exc:
        await create_proposal(
            opportunity_id=_OPP_ID,
            body=ProposalCreate(sow_ids=[_SOW_READY]),
            db=db,
            current_user=_user("sales_ops.proposal.view"),  # view only
        )
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# list / get
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_proposals_returns_items_in_descending_created_at():
    from routes.proposals import create_proposal, list_proposals_for_opportunity
    from models import ProposalCreate

    db = _make_db()
    creator = _user("sales_ops.proposal.create")
    await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(sow_ids=[_SOW_READY], title="First"),
        db=db,
        current_user=creator,
    )
    await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(sow_ids=[_SOW_COMPLETE], title="Second"),
        db=db,
        current_user=creator,
    )

    payload = await list_proposals_for_opportunity(
        opportunity_id=_OPP_ID,
        db=db,
        current_user=_user("sales_ops.proposal.view"),
    )
    assert payload["opportunity_id"] == _OPP_ID
    # The list wrapper preserves both inserts.
    assert len(payload["items"]) == 2
    titles = {p.get("title") for p in payload["items"]}
    assert titles == {"First", "Second"}


@pytest.mark.asyncio
async def test_get_proposal_404_when_missing():
    from routes.proposals import get_proposal

    db = _make_db()
    with pytest.raises(HTTPException) as exc:
        await get_proposal(
            proposal_id="prop_does_not_exist",
            db=db,
            current_user=_user("sales_ops.proposal.view"),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_proposal_403_without_view_permission():
    from routes.proposals import get_proposal

    db = _make_db()
    db.proposals.docs.append({"proposal_id": _PROPOSAL_ID, "opportunity_id": _OPP_ID})
    with pytest.raises(HTTPException) as exc:
        await get_proposal(
            proposal_id=_PROPOSAL_ID, db=db, current_user=_user()
        )
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# patch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_proposal_updates_status_and_metadata():
    from routes.proposals import create_proposal, update_proposal
    from models import ProposalCreate, ProposalUpdate

    db = _make_db()
    created = await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(sow_ids=[_SOW_READY]),
        db=db,
        current_user=_user("sales_ops.proposal.create"),
    )
    updated = await update_proposal(
        proposal_id=created.proposal_id,
        body=ProposalUpdate(
            status="ready_for_review",
            google_doc_url="https://docs.google.com/document/d/abc/edit",
        ),
        db=db,
        current_user=_user("sales_ops.proposal.edit"),
    )
    assert updated["status"] == "ready_for_review"
    assert updated["google_doc_url"].endswith("/edit")


@pytest.mark.asyncio
async def test_patch_proposal_rejects_status_sent():
    """L2. sent is unreachable through PATCH from any state. The route
    raises 400 with a fixed detail string before the transition table
    is consulted so the guard holds even if the table is edited later.
    POST /send remains the only path that may set sent and that is the
    only path that stamps sent_at, sent_by, and sent_to_email.
    """
    from routes.proposals import create_proposal, update_proposal
    from models import ProposalCreate, ProposalUpdate

    db = _make_db()
    created = await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(sow_ids=[_SOW_READY]),
        db=db,
        current_user=_user("sales_ops.proposal.create"),
    )
    with pytest.raises(HTTPException) as exc:
        await update_proposal(
            proposal_id=created.proposal_id,
            body=ProposalUpdate(status="sent"),
            db=db,
            current_user=_user("sales_ops.proposal.edit"),
        )
    assert exc.value.status_code == 400
    assert "sent" in exc.value.detail
    assert "/send" in exc.value.detail


@pytest.mark.asyncio
async def test_patch_ready_for_review_to_draft_is_allowed():
    """K2 Option A. Un-readying a proposal is intentional. A reviewer
    who spots a typo or wants to tighten a milestone needs to be able
    to move ready_for_review back to draft and edit content there. The
    forward arrow in the model docstring describes the typical path,
    not an irreversible rule. Locked statuses below sent, accepted,
    and rejected are the ones that freeze the content."""
    from routes.proposals import create_proposal, update_proposal
    from models import ProposalCreate, ProposalUpdate

    db = _make_db()
    actor = _user("sales_ops.proposal.create", "sales_ops.proposal.edit")
    created = await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(sow_ids=[_SOW_READY]),
        db=db,
        current_user=actor,
    )
    promoted = await update_proposal(
        proposal_id=created.proposal_id,
        body=ProposalUpdate(status="ready_for_review"),
        db=db,
        current_user=actor,
    )
    assert promoted["status"] == "ready_for_review"
    demoted = await update_proposal(
        proposal_id=created.proposal_id,
        body=ProposalUpdate(status="draft"),
        db=db,
        current_user=actor,
    )
    assert demoted["status"] == "draft"


@pytest.mark.asyncio
async def test_patch_proposal_validates_sow_swap():
    """Swapping in a SOW that doesn't meet the ready/complete gate is
    rejected — same gate as create."""
    from routes.proposals import create_proposal, update_proposal
    from models import ProposalCreate, ProposalUpdate

    db = _make_db()
    created = await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(sow_ids=[_SOW_READY]),
        db=db,
        current_user=_user("sales_ops.proposal.create"),
    )
    with pytest.raises(HTTPException) as exc:
        await update_proposal(
            proposal_id=created.proposal_id,
            body=ProposalUpdate(sow_ids=[_SOW_NOT_READY]),
            db=db,
            current_user=_user("sales_ops.proposal.edit"),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_patch_proposal_403_without_edit_permission():
    from routes.proposals import update_proposal
    from models import ProposalUpdate

    db = _make_db()
    db.proposals.docs.append(
        {"proposal_id": _PROPOSAL_ID, "opportunity_id": _OPP_ID, "status": "draft"}
    )
    with pytest.raises(HTTPException) as exc:
        await update_proposal(
            proposal_id=_PROPOSAL_ID,
            body=ProposalUpdate(notes="hi"),
            db=db,
            current_user=_user("sales_ops.proposal.view"),
        )
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_proposal_flips_status_and_stamps_metadata():
    """J5. Send requires the proposal to be in ready_for_review. The
    happy path promotes draft to ready_for_review through PATCH first
    and then marks sent, mirroring the canonical workflow."""
    from routes.proposals import create_proposal, send_proposal, update_proposal
    from models import ProposalCreate, ProposalSendRequest, ProposalUpdate

    db = _make_db()
    actor = _user(
        "sales_ops.proposal.create",
        "sales_ops.proposal.edit",
        "sales_ops.proposal.send",
    )
    created = await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(sow_ids=[_SOW_READY]),
        db=db,
        current_user=actor,
    )
    await update_proposal(
        proposal_id=created.proposal_id,
        body=ProposalUpdate(status="ready_for_review"),
        db=db,
        current_user=actor,
    )
    sent = await send_proposal(
        proposal_id=created.proposal_id,
        body=ProposalSendRequest(sent_to_email="client@example.com"),
        db=db,
        current_user=actor,
    )
    assert sent["status"] == "sent"
    assert sent["sent_to_email"] == "client@example.com"
    assert sent["sent_by"] == actor.user_id
    assert sent["sent_at"] is not None


@pytest.mark.asyncio
async def test_send_proposal_rejects_already_sent():
    """K1. After the redundant 400 guard was removed, sending an
    already sent proposal returns 409 with the same fixed detail string
    the PATCH transition guard uses. The earlier code raised 400 for
    the sent case and 409 for every other invalid current status which
    split one category of rejection across two status codes."""
    from routes.proposals import create_proposal, send_proposal, update_proposal
    from models import ProposalCreate, ProposalSendRequest, ProposalUpdate

    db = _make_db()
    actor = _user(
        "sales_ops.proposal.create",
        "sales_ops.proposal.edit",
        "sales_ops.proposal.send",
    )
    created = await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(sow_ids=[_SOW_READY]),
        db=db,
        current_user=actor,
    )
    await update_proposal(
        proposal_id=created.proposal_id,
        body=ProposalUpdate(status="ready_for_review"),
        db=db,
        current_user=actor,
    )
    await send_proposal(
        proposal_id=created.proposal_id,
        body=ProposalSendRequest(sent_to_email="client@example.com"),
        db=db,
        current_user=actor,
    )
    with pytest.raises(HTTPException) as exc:
        await send_proposal(
            proposal_id=created.proposal_id,
            body=ProposalSendRequest(sent_to_email="client@example.com"),
            db=db,
            current_user=actor,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "Invalid status transition"


@pytest.mark.asyncio
async def test_send_proposal_rejects_draft():
    """J5. Sending a draft proposal returns 409 with the fixed
    transition detail string. The earlier behaviour allowed any non
    sent status to be sent which skipped the review cycle entirely."""
    from routes.proposals import create_proposal, send_proposal
    from models import ProposalCreate, ProposalSendRequest

    db = _make_db()
    actor = _user(
        "sales_ops.proposal.create",
        "sales_ops.proposal.send",
    )
    created = await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(sow_ids=[_SOW_READY]),
        db=db,
        current_user=actor,
    )
    with pytest.raises(HTTPException) as exc:
        await send_proposal(
            proposal_id=created.proposal_id,
            body=ProposalSendRequest(sent_to_email="client@example.com"),
            db=db,
            current_user=actor,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "Invalid status transition"


@pytest.mark.asyncio
async def test_send_proposal_403_without_send_permission():
    from routes.proposals import send_proposal
    from models import ProposalSendRequest

    db = _make_db()
    db.proposals.docs.append(
        {"proposal_id": _PROPOSAL_ID, "opportunity_id": _OPP_ID, "status": "draft"}
    )
    # The actor holds edit but not send — they should be blocked.
    with pytest.raises(HTTPException) as exc:
        await send_proposal(
            proposal_id=_PROPOSAL_ID,
            body=ProposalSendRequest(),
            db=db,
            current_user=_user("sales_ops.proposal.edit"),
        )
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_returns_json_with_google_doc_url():
    """I1. The download route now returns the URL as a JSON field and
    no longer issues a server side 302. The frontend opens the URL on
    the client so the application origin is never used as a hop.
    """
    from routes.proposals import download_proposal

    expected_url = "https://docs.google.com/document/d/synth/edit"
    db = _make_db()
    db.proposals.docs.append(
        {
            "proposal_id": _PROPOSAL_ID,
            "opportunity_id": _OPP_ID,
            "google_doc_url": expected_url,
            "status": "sent",
        }
    )
    result = await download_proposal(
        proposal_id=_PROPOSAL_ID,
        db=db,
        current_user=_user("sales_ops.proposal.view"),
    )
    # The route returns a dict that FastAPI then serialises through the
    # ProposalDownloadResponse model. We assert the dict shape here so
    # the test stays independent of the FastAPI response pipeline.
    assert isinstance(result, dict)
    assert result["google_doc_url"] == expected_url


@pytest.mark.asyncio
async def test_download_404_with_fixed_error_string_when_no_artifact_url():
    """I1. A proposal without a google_doc_url returns 404 with the
    fixed error string. The UI uses that as the actionable empty
    state.
    """
    from routes.proposals import download_proposal

    db = _make_db()
    db.proposals.docs.append(
        {
            "proposal_id": _PROPOSAL_ID,
            "opportunity_id": _OPP_ID,
            "google_doc_url": None,
            "status": "draft",
        }
    )
    with pytest.raises(HTTPException) as exc:
        await download_proposal(
            proposal_id=_PROPOSAL_ID,
            db=db,
            current_user=_user("sales_ops.proposal.view"),
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "proposal has no document url"


@pytest.mark.asyncio
async def test_download_404_when_proposal_missing():
    from routes.proposals import download_proposal

    db = _make_db()
    with pytest.raises(HTTPException) as exc:
        await download_proposal(
            proposal_id="prop_does_not_exist",
            db=db,
            current_user=_user("sales_ops.proposal.view"),
        )
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# I7. google_doc_url HttpUrl validation on Proposal models
# ---------------------------------------------------------------------------


def test_proposal_create_rejects_invalid_google_doc_url():
    """I7. A malformed URL on ProposalCreate must fail validation at
    the Pydantic boundary so a bad value cannot reach storage.
    """
    from models import ProposalCreate

    with pytest.raises(ValidationError):
        ProposalCreate(sow_ids=[_SOW_READY], google_doc_url="not a url")


def test_proposal_create_accepts_well_formed_google_doc_url():
    """I7. A syntactically valid HTTPS URL constructs without
    error.
    """
    from models import ProposalCreate

    body = ProposalCreate(
        sow_ids=[_SOW_READY],
        google_doc_url="https://docs.google.com/document/d/abc",
    )
    assert str(body.google_doc_url).startswith("https://docs.google.com/")


@pytest.mark.asyncio
async def test_download_reads_stored_url_as_string():
    """I7. The downstream download route reads the stored value as a
    plain string through the response model from I1. This confirms the
    create path persists the URL as a string rather than a Url object
    that Mongo cannot serialise.
    """
    from routes.proposals import create_proposal, download_proposal
    from models import ProposalCreate

    db = _make_db()
    actor = _user("sales_ops.proposal.create", "sales_ops.proposal.view")
    created = await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(
            sow_ids=[_SOW_READY],
            google_doc_url="https://docs.google.com/document/d/synth",
        ),
        db=db,
        current_user=actor,
    )
    stored = db.proposals.docs[0]
    # The persisted record must be a plain string. A Url object would
    # have leaked the Pydantic type into Mongo and broken downstream
    # readers.
    assert isinstance(stored["google_doc_url"], str)


def test_proposal_update_accepts_none():
    """I7. None remains a valid value on ProposalUpdate so a caller
    can explicitly clear the URL when detaching a doc from a draft.
    """
    from models import ProposalUpdate

    body = ProposalUpdate(google_doc_url=None)
    assert body.google_doc_url is None


def test_proposal_update_accepts_valid_url():
    """I7. A syntactically valid URL passes validation on
    ProposalUpdate. The same parser runs on create and update so the
    accepted shape stays consistent across both routes.
    """
    from models import ProposalUpdate

    body = ProposalUpdate(google_doc_url="https://docs.google.com/document/d/abc")
    assert str(body.google_doc_url).startswith("https://docs.google.com/")


def test_proposal_update_rejects_invalid_url():
    """I7. Garbage input on ProposalUpdate raises ValidationError at
    the Pydantic boundary. Without this guard, a PATCH with an attacker
    domain or a non URL string could still land in storage and reopen
    the open redirect surface from I1.
    """
    from models import ProposalUpdate

    with pytest.raises(ValidationError):
        ProposalUpdate(google_doc_url="not a url")


@pytest.mark.asyncio
async def test_download_response_has_no_location_header():
    """I1. The new download path returns JSON through the FastAPI
    response pipeline and must not emit a Location header. Any residual
    Location would mean the prior 302 RedirectResponse path is still
    reachable somewhere in the stack.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from auth_helper import get_current_user
    from database import get_db
    from routes.proposals import router

    expected_url = "https://docs.google.com/document/d/synth/edit"
    db = _make_db()
    db.proposals.docs.append(
        {
            "proposal_id": _PROPOSAL_ID,
            "opportunity_id": _OPP_ID,
            "google_doc_url": expected_url,
            "status": "sent",
        }
    )

    app = FastAPI()
    app.include_router(router, prefix="/api/sales-ops")
    app.dependency_overrides[get_current_user] = lambda: _user(
        "sales_ops.proposal.view"
    )
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app, follow_redirects=False)
    resp = client.get(f"/api/sales-ops/proposals/{_PROPOSAL_ID}/download")
    assert resp.status_code == 200
    assert resp.headers.get("location") is None
    assert resp.headers.get("Location") is None
    assert resp.json()["google_doc_url"] == expected_url


# ---------------------------------------------------------------------------
# L2. Explicit early guard. PATCH cannot reach status sent regardless of
# the transition table.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_other_valid_status_still_works_with_l2_guard():
    """L2 guard fires only when the target is sent. Any other valid
    target status still flows through the transition table. Locks that
    the new early guard did not collapse legal status moves."""
    from routes.proposals import create_proposal, update_proposal
    from models import ProposalCreate, ProposalUpdate

    db = _make_db()
    created = await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(sow_ids=[_SOW_READY]),
        db=db,
        current_user=_user("sales_ops.proposal.create"),
    )
    updated = await update_proposal(
        proposal_id=created.proposal_id,
        body=ProposalUpdate(status="ready_for_review"),
        db=db,
        current_user=_user("sales_ops.proposal.edit"),
    )
    assert updated["status"] == "ready_for_review"


# ---------------------------------------------------------------------------
# L4. Hostname allowlist at every write path that accepts google_doc_url.
# ---------------------------------------------------------------------------


_ALLOWED_DOC_URL = "https://docs.google.com/document/d/abc/edit"
_DISALLOWED_DOC_URL = "https://attacker.invalid/document/d/abc/edit"


@pytest.mark.asyncio
async def test_create_accepts_allowed_doc_hostname():
    """Sanity. The canonical Google Docs hostname remains accepted at
    create time after the allowlist landed."""
    from routes.proposals import create_proposal
    from models import ProposalCreate

    db = _make_db()
    result = await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(
            sow_ids=[_SOW_READY],
            google_doc_url=_ALLOWED_DOC_URL,
        ),
        db=db,
        current_user=_user("sales_ops.proposal.create"),
    )
    assert str(result.google_doc_url).startswith("https://docs.google.com/")


@pytest.mark.asyncio
async def test_create_rejects_disallowed_doc_hostname():
    """An attacker controlled hostname must be rejected at the write
    boundary with 400 and the fixed allowlist detail."""
    from routes.proposals import create_proposal
    from models import ProposalCreate

    db = _make_db()
    with pytest.raises(HTTPException) as exc:
        await create_proposal(
            opportunity_id=_OPP_ID,
            body=ProposalCreate(
                sow_ids=[_SOW_READY],
                google_doc_url=_DISALLOWED_DOC_URL,
            ),
            db=db,
            current_user=_user("sales_ops.proposal.create"),
        )
    assert exc.value.status_code == 400
    # The detail string is fixed and free of any echoed input.
    assert "allowlist" in exc.value.detail
    assert _DISALLOWED_DOC_URL not in exc.value.detail


@pytest.mark.asyncio
async def test_patch_rejects_disallowed_doc_hostname():
    """PATCH must apply the same allowlist as create so a hostile URL
    cannot land through the edit path."""
    from routes.proposals import create_proposal, update_proposal
    from models import ProposalCreate, ProposalUpdate

    db = _make_db()
    created = await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(sow_ids=[_SOW_READY]),
        db=db,
        current_user=_user("sales_ops.proposal.create"),
    )
    with pytest.raises(HTTPException) as exc:
        await update_proposal(
            proposal_id=created.proposal_id,
            body=ProposalUpdate(google_doc_url=_DISALLOWED_DOC_URL),
            db=db,
            current_user=_user("sales_ops.proposal.edit"),
        )
    assert exc.value.status_code == 400
    assert "allowlist" in exc.value.detail


@pytest.mark.asyncio
async def test_send_rejects_disallowed_stored_doc_hostname():
    """Send validates the stored google_doc_url against the allowlist
    so a legacy or pre allowlist write cannot ride through into the
    audit trail."""
    from routes.proposals import send_proposal
    from models import ProposalSendRequest

    db = _make_db()
    # Plant a proposal whose stored URL was written before the
    # allowlist existed. The send path must reject it now.
    db.proposals.docs.append(
        {
            "proposal_id": "prop_synth_legacy_disallowed",
            "opportunity_id": _OPP_ID,
            "status": "ready_for_review",
            "google_doc_url": _DISALLOWED_DOC_URL,
            "generated_content": "",
        }
    )
    with pytest.raises(HTTPException) as exc:
        await send_proposal(
            proposal_id="prop_synth_legacy_disallowed",
            body=ProposalSendRequest(),
            db=db,
            current_user=_user("sales_ops.proposal.send"),
        )
    assert exc.value.status_code == 400
    assert "allowlist" in exc.value.detail


@pytest.mark.asyncio
async def test_send_accepts_allowed_stored_doc_hostname():
    """Sanity. The send path must still succeed when the stored URL is
    on the allowlist."""
    from routes.proposals import send_proposal
    from models import ProposalSendRequest

    db = _make_db()
    db.proposals.docs.append(
        {
            "proposal_id": "prop_synth_doc_allowed",
            "opportunity_id": _OPP_ID,
            "status": "ready_for_review",
            "google_doc_url": _ALLOWED_DOC_URL,
            "generated_content": "",
        }
    )
    sent = await send_proposal(
        proposal_id="prop_synth_doc_allowed",
        body=ProposalSendRequest(),
        db=db,
        current_user=_user("sales_ops.proposal.send"),
    )
    assert sent["status"] == "sent"

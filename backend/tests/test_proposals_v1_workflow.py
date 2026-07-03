"""V1 proposal workflow contract — generate / view / edit / regenerate / send.

Spec elements locked here (mirrors the upgrade task's STEP 10 list):

  1.  sales_rep role grants now include ``sales_ops.proposal.send``
      so the rep can mark proposals sent.
  2.  Create generates ``generated_content`` from ready SOW data.
  3.  Create rejects when no SOW is at ready_for_review or complete.
  4.  Create does not mutate the underlying SOW rows.
  5.  GET endpoints surface the new generated_content / google_doc_url
      fields verbatim.
  6.  PATCH can edit title / content / URL.
  7.  PATCH still rejects status=sent.
  8.  Regenerate updates content + version + regenerated_at and refuses
      a no-op refresh by status check.
  9.  Regenerate refuses sent / accepted / rejected with 409.
  10. Send allows a sales rep that holds sales_ops.proposal.send.
  11. Send rejects a proposal with no Google Doc URL and no content.
  12. Send records sent_at / sent_by / sent_to_email.

All tests use AsyncMock-shaped fakes — no Mongo required.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


_OPP_ID = "opp_synth_v1_001"
_SOW_READY = "sow_synth_v1_ready_001"
_SOW_NOT_READY = "sow_synth_v1_in_progress_002"


# ---------------------------------------------------------------------------
# Test doubles (small, route-local — same shape the MVP test file uses)
# ---------------------------------------------------------------------------


def _user(*perms: str):
    return SimpleNamespace(
        user_id="u_synth_v1_tester",
        email="v1-tester@aegisaxiom.test",
        role="sales_rep",
        permissions=list(perms),
    )


class _SowsCollection:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def find(self, query: dict, projection: dict | None = None):
        ids = query.get("sow_id", {}).get("$in", []) if isinstance(query.get("sow_id"), dict) else None
        result = [dict(r) for r in self._rows if (ids is None or r.get("sow_id") in ids)]
        return _ListWrapper(result)


class _ProposalsCollection:
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
        return _SortWrapper(
            sorted(self._rows, key=lambda d: d.get(key) or "", reverse=direction < 0)
        )

    async def to_list(self, length: int | None = None) -> list[dict]:
        return list(self._rows[:length] if length is not None else self._rows)


def _ready_sow_row() -> dict:
    return {
        "sow_id": _SOW_READY,
        "opportunity_id": _OPP_ID,
        "line_of_business": "professional_services",
        "status": "ready_for_review",
        "executive_summary": "Synth executive summary.",
        "smart_specific": "Synth specific",
        "smart_measurable": "Synth measurable",
        "smart_achievable": "Synth achievable",
        "smart_relevant": "Synth relevant",
        "smart_time_bound": "Synth time-bound",
        "contract_type": "fixed_fee",
        "billing_terms": "monthly",
        "payment_terms": "net_30",
        "start_date": "2099-01-01",
        "end_date": "2099-03-01",
        "total_amount": 50000.0,
        "milestones": [
            {"milestone_id": "m1", "milestone_name": "Kickoff", "order": 1, "bullets": []},
        ],
    }


def _make_db(*, sow_rows: list[dict] | None = None) -> SimpleNamespace:
    db = SimpleNamespace()
    db.opportunities = SimpleNamespace(
        find_one=AsyncMock(
            return_value={
                "opportunity_id": _OPP_ID,
                "name": "Synth V1 Opp",
                "account_name": "Synth V1 Account",
                "lines_of_business": ["professional_services"],
            }
        )
    )
    rows = sow_rows if sow_rows is not None else [
        _ready_sow_row(),
        {
            "sow_id": _SOW_NOT_READY,
            "opportunity_id": _OPP_ID,
            "line_of_business": "wholesale_mrr",
            "status": "in_progress",
        },
    ]
    db.opportunity_sows = _SowsCollection(rows)
    db.proposals = _ProposalsCollection()
    return db


# ---------------------------------------------------------------------------
# 1. sales_rep role grants
# ---------------------------------------------------------------------------


def test_sales_rep_role_grants_proposal_send():
    """Role seed for ``sales_rep`` must include ``sales_ops.proposal.send``
    so the rep can mark/send proposals end-to-end. The DEFAULT_ROLES
    list in routes/roles.py is the source of truth that the boot-time
    seeder writes into Mongo, so locking the in-code seed is enough."""
    from routes.roles import DEFAULT_ROLES

    rep_role = next(r for r in DEFAULT_ROLES if r["name"] == "sales_rep")
    assert "sales_ops.proposal.send" in rep_role["permissions"]
    assert "sales_ops.proposal.view" in rep_role["permissions"]
    assert "sales_ops.proposal.create" in rep_role["permissions"]
    assert "sales_ops.proposal.edit" in rep_role["permissions"]


# ---------------------------------------------------------------------------
# 2-4. create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_generates_content_from_ready_sow():
    from routes.proposals import create_proposal
    from models import ProposalCreate

    db = _make_db()
    result = await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(sow_ids=[_SOW_READY]),
        db=db,
        current_user=_user("sales_ops.proposal.create"),
    )
    # The generator's output is stamped on the proposal.
    assert (result.generated_content or "").strip(), "create must populate generated_content"
    assert result.generated_summary
    assert result.generation_source == "sow"
    assert result.generation_source_hash
    assert result.generated_at is not None
    assert result.artifact_type == "inline_content"
    assert "Synth executive summary." in result.generated_content


@pytest.mark.asyncio
async def test_create_rejects_when_no_sow_is_ready_or_complete():
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
async def test_create_does_not_mutate_source_sow():
    """The route reads SOWs out of Mongo and hands them to the
    generator. The generator must return a draft without touching the
    SOW dicts. This locks the contract end-to-end."""
    from routes.proposals import create_proposal
    from models import ProposalCreate
    import copy

    db = _make_db()
    sow_snapshot = copy.deepcopy(db.opportunity_sows._rows)
    await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(sow_ids=[_SOW_READY]),
        db=db,
        current_user=_user("sales_ops.proposal.create"),
    )
    assert db.opportunity_sows._rows == sow_snapshot


@pytest.mark.asyncio
async def test_create_marks_artifact_type_google_doc_when_url_supplied():
    """An explicit URL on create flips artifact_type to google_doc so
    the UI renders the doc as the primary view."""
    from routes.proposals import create_proposal
    from models import ProposalCreate

    db = _make_db()
    result = await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(
            sow_ids=[_SOW_READY],
            google_doc_url="https://docs.google.com/document/d/synth/edit",
        ),
        db=db,
        current_user=_user("sales_ops.proposal.create"),
    )
    assert result.artifact_type == "google_doc"
    # I7. Proposal.google_doc_url is HttpUrl in Python. Cast to str at
    # the read site for string comparison.
    assert str(result.google_doc_url).endswith("/edit")


# ---------------------------------------------------------------------------
# 5. view / list surface new fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_and_list_include_generated_content():
    from routes.proposals import create_proposal, get_proposal, list_proposals_for_opportunity
    from models import ProposalCreate

    db = _make_db()
    created = await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(sow_ids=[_SOW_READY]),
        db=db,
        current_user=_user("sales_ops.proposal.create"),
    )
    single = await get_proposal(
        proposal_id=created.proposal_id,
        db=db,
        current_user=_user("sales_ops.proposal.view"),
    )
    assert "generated_content" in single and single["generated_content"]
    assert "google_doc_url" in single
    listed = await list_proposals_for_opportunity(
        opportunity_id=_OPP_ID,
        db=db,
        current_user=_user("sales_ops.proposal.view"),
    )
    assert len(listed["items"]) == 1
    assert listed["items"][0]["generated_content"] == single["generated_content"]


# ---------------------------------------------------------------------------
# 6-7. patch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_can_edit_title_content_url():
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
            title="Custom title",
            generated_content="Hand-edited body.",
            generated_summary="Hand-edited summary.",
            google_doc_url="https://docs.google.com/document/d/edited/edit",
        ),
        db=db,
        current_user=_user("sales_ops.proposal.edit"),
    )
    assert updated["title"] == "Custom title"
    assert updated["generated_content"] == "Hand-edited body."
    assert updated["generated_summary"] == "Hand-edited summary."
    # artifact_type tracks the URL.
    assert updated["artifact_type"] == "google_doc"


@pytest.mark.asyncio
async def test_patch_still_rejects_status_sent():
    """L2. PATCH must never reach status sent. The route raises 400
    with a fixed detail string before the transition table is
    consulted so the guard does not depend on the table being correct.
    POST /send remains the only path that may set sent and the only
    path that stamps sent_at, sent_by, and sent_to_email.
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
async def test_patch_refuses_content_edits_on_locked_status():
    """Once status is sent / accepted / rejected the snapshot the
    client received must stay frozen — content edits return 409. The
    J5 review cycle guard requires the proposal to be in
    ready_for_review before send, so the test promotes draft to
    ready_for_review first."""
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
        body=ProposalSendRequest(),
        db=db,
        current_user=actor,
    )
    with pytest.raises(HTTPException) as exc:
        await update_proposal(
            proposal_id=created.proposal_id,
            body=ProposalUpdate(generated_content="rewriting history"),
            db=db,
            current_user=actor,
        )
    assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# 8-9. regenerate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regenerate_updates_content_and_version():
    from routes.proposals import create_proposal, regenerate_proposal
    from models import ProposalCreate

    db = _make_db()
    actor = _user("sales_ops.proposal.create", "sales_ops.proposal.edit")
    created = await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(sow_ids=[_SOW_READY]),
        db=db,
        current_user=actor,
    )
    original_version = created.version
    original_hash = created.generation_source_hash

    # Mutate the SOW row to simulate Sales Ops editing the SOW before
    # regenerating. The route reads the latest row out of Mongo, so the
    # regenerated content reflects the change.
    db.opportunity_sows._rows[0]["executive_summary"] = "Pivoted scope."

    regenerated = await regenerate_proposal(
        proposal_id=created.proposal_id,
        db=db,
        current_user=actor,
    )
    assert regenerated["version"] == original_version + 1
    assert regenerated["generation_source_hash"] != original_hash
    assert "Pivoted scope." in regenerated["generated_content"]
    assert regenerated["regenerated_at"] is not None
    assert regenerated["regenerated_by"] == actor.user_id


@pytest.mark.asyncio
async def test_regenerate_rejects_sent_proposal():
    from routes.proposals import (
        create_proposal,
        regenerate_proposal,
        send_proposal,
        update_proposal,
    )
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
    # J5. The review cycle guard requires ready_for_review before send.
    await update_proposal(
        proposal_id=created.proposal_id,
        body=ProposalUpdate(status="ready_for_review"),
        db=db,
        current_user=actor,
    )
    await send_proposal(
        proposal_id=created.proposal_id,
        body=ProposalSendRequest(),
        db=db,
        current_user=actor,
    )
    with pytest.raises(HTTPException) as exc:
        await regenerate_proposal(
            proposal_id=created.proposal_id,
            db=db,
            current_user=actor,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_regenerate_permission_accepts_edit_or_create():
    """Regenerate is a content rebuild; either edit or create perm
    qualifies. A viewer-only user is rejected with 403."""
    from routes.proposals import create_proposal, regenerate_proposal
    from models import ProposalCreate

    db = _make_db()
    created = await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(sow_ids=[_SOW_READY]),
        db=db,
        current_user=_user("sales_ops.proposal.create"),
    )
    # Editor allowed.
    await regenerate_proposal(
        proposal_id=created.proposal_id,
        db=db,
        current_user=_user("sales_ops.proposal.edit"),
    )
    # Viewer rejected.
    with pytest.raises(HTTPException) as exc:
        await regenerate_proposal(
            proposal_id=created.proposal_id,
            db=db,
            current_user=_user("sales_ops.proposal.view"),
        )
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# 10-12. send
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_allowed_for_sales_rep_holding_proposal_send():
    """The sales_rep role now grants sales_ops.proposal.send. A rep
    that holds it can flip status to sent once the proposal has been
    promoted to ready_for_review per the J5 review cycle guard."""
    from routes.proposals import create_proposal, send_proposal, update_proposal
    from models import ProposalCreate, ProposalSendRequest, ProposalUpdate

    db = _make_db()
    rep = SimpleNamespace(
        user_id="u_synth_rep",
        email="rep@aegisaxiom.test",
        role="sales_rep",
        permissions=[
            "sales_ops.proposal.view",
            "sales_ops.proposal.create",
            "sales_ops.proposal.edit",
            "sales_ops.proposal.send",
        ],
    )
    created = await create_proposal(
        opportunity_id=_OPP_ID,
        body=ProposalCreate(sow_ids=[_SOW_READY]),
        db=db,
        current_user=rep,
    )
    await update_proposal(
        proposal_id=created.proposal_id,
        body=ProposalUpdate(status="ready_for_review"),
        db=db,
        current_user=rep,
    )
    sent = await send_proposal(
        proposal_id=created.proposal_id,
        body=ProposalSendRequest(sent_to_email="client@example.com"),
        db=db,
        current_user=rep,
    )
    assert sent["status"] == "sent"
    assert sent["sent_by"] == rep.user_id
    assert sent["sent_at"] is not None
    assert sent["sent_to_email"] == "client@example.com"


@pytest.mark.asyncio
async def test_send_rejects_when_no_artifact_or_content():
    """A proposal without either a Google Doc URL or any generated
    content cannot be marked sent — there would be nothing to
    receive. The fixture status is ready_for_review so the J5 review
    guard passes and the artifact guard is the one that fires."""
    from routes.proposals import send_proposal
    from models import ProposalSendRequest

    db = _make_db()
    db.proposals.docs.append(
        {
            "proposal_id": "prop_synth_empty_001",
            "opportunity_id": _OPP_ID,
            "status": "ready_for_review",
            "google_doc_url": None,
            "generated_content": "",
        }
    )
    with pytest.raises(HTTPException) as exc:
        await send_proposal(
            proposal_id="prop_synth_empty_001",
            body=ProposalSendRequest(),
            db=db,
            current_user=_user("sales_ops.proposal.send"),
        )
    assert exc.value.status_code == 400
    assert "no Google Doc URL" in exc.value.detail


@pytest.mark.asyncio
async def test_send_with_only_google_doc_url_succeeds():
    """Inline content is one valid artifact path. A Google Doc URL is
    the other — either alone is enough to mark sent."""
    from routes.proposals import send_proposal
    from models import ProposalSendRequest

    db = _make_db()
    db.proposals.docs.append(
        {
            "proposal_id": "prop_synth_doc_only_001",
            "opportunity_id": _OPP_ID,
            # J5. Status must be ready_for_review to satisfy the review
            # cycle guard. The artifact guard then accepts the doc URL.
            "status": "ready_for_review",
            "google_doc_url": "https://docs.google.com/document/d/doc-only/edit",
            "generated_content": None,
        }
    )
    sent = await send_proposal(
        proposal_id="prop_synth_doc_only_001",
        body=ProposalSendRequest(sent_to_email="client@example.com"),
        db=db,
        current_user=_user("sales_ops.proposal.send"),
    )
    assert sent["status"] == "sent"

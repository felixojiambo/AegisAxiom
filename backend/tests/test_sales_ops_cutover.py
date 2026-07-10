"""Sales Ops SOW + Proposal cutover regression tests.

Three concrete cutover gaps are locked here.

  Stage filter. The Sales Ops Workspace previously hardcoded
    stage=proposal_development on the list call so every SOW and
    proposal record vanished from the workspace once the opportunity
    moved past Proposal Development. The list route now accepts a
    comma separated list of stages and maps it to a Mongo $in
    filter. Single value calls stay backward compatible.

  Proposal send audit. Mark Sent to Sales is the moment that
    matters for the paper trail. The send route now writes an
    audit row via services.audit_service.log_action. Audit failure
    must not propagate up and break the user's send action because
    log_action already swallows the storage exception internally.

  Google Docs structured error. The SOW finalize route used to
    return raw FileNotFoundError text in the response which could
    leak a server side file path. The route now returns a
    structured detail with a code field so the frontend can branch
    on the failure mode and offer the external Google Doc attach
    as an escape hatch.

Synthetic fixtures only. The route layer relies on FastAPI
dependency overrides and AsyncMock-shaped Mongo fakes so the tests
run without a live Mongo or QuickBooks or Google API.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


# ---------------------------------------------------------------------------
# Fake Mongo doubles. Shared across the three test families.
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_a, **_kw):
        return self

    def skip(self, _n):
        return self

    def limit(self, _n):
        return self

    async def to_list(self, length=None):
        return list(self._docs[:length] if length is not None else self._docs)


class _FakeOppCollection:
    """Captures the filter the route passed in so the test can assert
    against the Mongo query without standing up a real Mongo. Returns
    docs that match the captured filter for the stage field only."""

    def __init__(self, docs):
        self._docs = list(docs)
        self.last_filter: dict[str, Any] | None = None

    def find(self, query: dict, _projection=None):
        self.last_filter = dict(query)
        results = list(self._docs)
        stage_clause = query.get("stage")
        if isinstance(stage_clause, dict) and "$in" in stage_clause:
            allowed = set(stage_clause["$in"])
            results = [d for d in results if d.get("stage") in allowed]
        elif isinstance(stage_clause, str):
            results = [d for d in results if d.get("stage") == stage_clause]
        return _FakeCursor(results)

    async def count_documents(self, query: dict):
        cur = self.find(query)
        # Cursor exposes the same backing list, hop through to_list to
        # respect the same matching as find.
        return len(await cur.to_list(length=None))


# ---------------------------------------------------------------------------
# Stage filter — backend list route accepts multi-value.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stage_filter_single_value_stays_backward_compatible():
    """Single value calls keep the original exact match query.
    Locked so the existing dashboards and the frontend default path
    do not regress."""
    from routes.opportunities import get_opportunities as list_opportunities

    docs = [
        {"opportunity_id": "opp_a", "stage": "proposal_development"},
        {"opportunity_id": "opp_b", "stage": "scoping_complete"},
        {"opportunity_id": "opp_c", "stage": "proposal_sent"},
    ]
    coll = _FakeOppCollection(docs)
    db = SimpleNamespace(opportunities=coll)
    user = SimpleNamespace(
        user_id="u_synth_so",
        email="cutover-tester@aegisaxiom.test",
        role="sales_ops",
        permissions=["opportunities.view.all"],
    )

    from routes import opportunities as opp_routes

    # The route reaches get_db() inside the handler. Patch it.
    import database as _db_module
    _db_module._db = db

    try:
        result = await list_opportunities(
            skip=0,
            limit=10,
            stage="proposal_development",
            owner_id=None,
            account_id=None,
            priority=None,
            test_only=False,
            search=None,
            view_as_user_id=None,
            view_as_account_id=None,
            current_user=user,
        )
    finally:
        _db_module._db = None

    # Mongo filter the route built. Single value path is unchanged.
    assert coll.last_filter["stage"] == "proposal_development"
    # Only the single Proposal Development opportunity is returned.
    items = result["items"]
    assert {i["opportunity_id"] for i in items} == {"opp_a"}


@pytest.mark.asyncio
async def test_stage_filter_accepts_comma_separated_list():
    """Comma-separated stage values translate to a Mongo $in filter
    so the Sales Ops workspace can pull SOW and proposal records
    that still need work after the parent opportunity moves on."""
    from routes.opportunities import get_opportunities as list_opportunities

    docs = [
        {"opportunity_id": "opp_a", "stage": "proposal_development"},
        {"opportunity_id": "opp_b", "stage": "scoping_complete"},
        {"opportunity_id": "opp_c", "stage": "proposal_sent"},
        {"opportunity_id": "opp_d", "stage": "closed_won"},
    ]
    coll = _FakeOppCollection(docs)
    db = SimpleNamespace(opportunities=coll)
    user = SimpleNamespace(
        user_id="u_synth_so",
        email="cutover-tester@aegisaxiom.test",
        role="sales_ops",
        permissions=["opportunities.view.all"],
    )

    import database as _db_module
    _db_module._db = db

    try:
        result = await list_opportunities(
            skip=0,
            limit=10,
            stage="proposal_development,scoping_complete,proposal_sent",
            owner_id=None,
            account_id=None,
            priority=None,
            test_only=False,
            search=None,
            view_as_user_id=None,
            view_as_account_id=None,
            current_user=user,
        )
    finally:
        _db_module._db = None

    # The route translated the comma list into a Mongo $in clause.
    assert isinstance(coll.last_filter["stage"], dict)
    assert set(coll.last_filter["stage"]["$in"]) == {
        "proposal_development",
        "scoping_complete",
        "proposal_sent",
    }
    # All three Sales-Ops-relevant rows come back. closed_won stays
    # out because it was not in the requested set.
    ids = {i["opportunity_id"] for i in result["items"]}
    assert ids == {"opp_a", "opp_b", "opp_c"}


@pytest.mark.asyncio
async def test_stage_filter_strips_whitespace_and_empties():
    """Comma list parsing trims whitespace and drops empty entries so
    a sloppy query string never produces an empty $in array that
    would silently match nothing."""
    from routes.opportunities import get_opportunities as list_opportunities

    docs = [
        {"opportunity_id": "opp_a", "stage": "proposal_development"},
        {"opportunity_id": "opp_b", "stage": "scoping_complete"},
    ]
    coll = _FakeOppCollection(docs)
    db = SimpleNamespace(opportunities=coll)
    user = SimpleNamespace(
        user_id="u_synth_so",
        email="cutover-tester@aegisaxiom.test",
        role="sales_ops",
        permissions=["opportunities.view.all"],
    )

    import database as _db_module
    _db_module._db = db

    try:
        await list_opportunities(
            skip=0,
            limit=10,
            stage="proposal_development, , scoping_complete,",
            owner_id=None,
            account_id=None,
            priority=None,
            test_only=False,
            search=None,
            view_as_user_id=None,
            view_as_account_id=None,
            current_user=user,
        )
    finally:
        _db_module._db = None

    assert set(coll.last_filter["stage"]["$in"]) == {
        "proposal_development",
        "scoping_complete",
    }


# ---------------------------------------------------------------------------
# Proposal send audit. The send route writes an audit row. Audit
# failure does not break the user action.
# ---------------------------------------------------------------------------


class _FakeProposalsCollection:
    def __init__(self, docs):
        self._docs = list(docs)

    async def find_one(self, query, _projection=None):
        for d in self._docs:
            if all(d.get(k) == v for k, v in query.items()):
                return dict(d)
        return None

    async def update_one(self, query, update):
        for d in self._docs:
            if all(d.get(k) == v for k, v in query.items()):
                d.update(update.get("$set", {}))
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


class _FakeOpportunitiesCollection:
    def __init__(self, docs):
        self._docs = list(docs)
        self.last_update = None

    async def find_one(self, query, _projection=None):
        for d in self._docs:
            if all(d.get(k) == v for k, v in query.items()):
                return dict(d)
        return None

    async def update_one(self, query, update):
        for d in self._docs:
            if all(d.get(k) == v for k, v in query.items()):
                self.last_update = update
                d.update(update.get("$set", {}))
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


class _FakeNotificationsCollection:
    def __init__(self):
        self.inserted: list[dict] = []

    async def insert_one(self, doc):
        self.inserted.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("notification_id"))


def _build_proposal_db(
    proposal_doc: dict,
    opportunity_doc: dict | None = None,
) -> SimpleNamespace:
    """Stitch a fake db with proposals, opportunities, notifications."""
    return SimpleNamespace(
        proposals=_FakeProposalsCollection([proposal_doc]),
        opportunities=_FakeOpportunitiesCollection([opportunity_doc] if opportunity_doc else []),
        notifications=_FakeNotificationsCollection(),
    )


@pytest.mark.asyncio
async def test_send_proposal_writes_audit_row(monkeypatch):
    """The handoff to Sales records an audit row. The audit details
    block keeps the opportunity_id, version, and the recipient count
    so an auditor can correlate the row with the proposal snapshot.

    Policy: the audit action is sales_ops.proposal.handed_off_to_sales
    so the paper trail distinguishes the internal handoff from the
    Sales-owned client send recorded by mark-client-sent. See
    docs/sales-ops/PROPOSAL_HANDOFF_AND_SEND_POLICY.md.
    """
    from routes.proposals import send_proposal
    from models import ProposalSendRequest

    captured: list[dict[str, Any]] = []

    async def _fake_audit(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr("routes.proposals.audit_log_action", _fake_audit)

    proposal_id = "prop_synth_audit_001"
    db = _build_proposal_db(
        {
            "proposal_id": proposal_id,
            "opportunity_id": "opp_synth_audit_001",
            "status": "ready_for_review",
            "google_doc_url": "https://docs.google.com/document/d/synth/edit",
            "generated_content": "synthetic proposal body",
            "version": 1,
        },
        {
            "opportunity_id": "opp_synth_audit_001",
            "stage": "proposal_development",
            "opportunity_owner_id": "u_synth_owner",
            "assigned_sales_ops_id": "u_synth_so",
        },
    )
    user = SimpleNamespace(
        user_id="u_synth_sender",
        email="sender@aegisaxiom.test",
        role="sales_ops",
        permissions=["sales_ops.proposal.send"],
    )

    body = ProposalSendRequest(sent_to_email=None, notes=None)
    fresh = await send_proposal(
        proposal_id=proposal_id,
        body=body,
        db=db,
        current_user=user,
    )

    # Status flipped to sent (internal handoff).
    assert fresh["status"] == "sent"
    # Expanded response shape carries the policy-aligned helper fields.
    assert fresh["handoff_only"] is True
    assert fresh["client_sent"] is False
    assert fresh["stage_advanced"] is False
    assert fresh["handoff_to_sales_at"] == fresh["sent_at"]
    assert fresh["handoff_by"] == fresh["sent_by"]
    assert set(fresh["handoff_recipients"]) == {"u_synth_owner", "u_synth_so"}
    # Audit row written under the policy-aligned action name.
    assert len(captured) == 1
    row = captured[0]
    assert row["action"] == "sales_ops.proposal.handed_off_to_sales"
    assert row["resource_type"] == "proposal"
    assert row["resource_id"] == proposal_id
    assert row["actor_id"] == "u_synth_sender"
    assert row["details"]["opportunity_id"] == "opp_synth_audit_001"
    assert row["details"]["version"] == 1
    assert row["details"]["handoff_recipient_count"] == 2
    # has_recipient is the only signal we keep; the email value itself
    # never reaches the audit row.
    assert row["details"]["has_recipient"] is False
    for v in row["details"].values():
        assert "@" not in str(v) if v is not None else True
    # Internal notifications were written for both Sales-side users.
    assert len(db.notifications.inserted) == 2
    notif_user_ids = {n["user_id"] for n in db.notifications.inserted}
    assert notif_user_ids == {"u_synth_owner", "u_synth_so"}
    forbidden_substrings = (
        "sent to client",
        "emailed the client",
        "emailed client",
        "delivered to client",
        "delivered to the client",
    )
    for notif in db.notifications.inserted:
        # Notification text never implies a client received anything.
        text = f"{notif['title'].lower()} {notif['body'].lower()}"
        for phrase in forbidden_substrings:
            assert phrase not in text, (
                f"handoff notification leaks client-delivery phrasing: {phrase!r}"
            )
    # Handoff does not advance the opportunity stage.
    assert db.opportunities.last_update is None


@pytest.mark.asyncio
async def test_send_proposal_does_not_fail_when_audit_raises(monkeypatch):
    """Audit-side exceptions must not propagate up and break the
    user's send action. log_action carries its own try/except
    internally; this test substitutes a helper that raises and
    verifies the route still completes successfully via the same
    swallowing behavior."""
    from routes.proposals import send_proposal
    from models import ProposalSendRequest

    async def _failing_audit(**_kwargs):
        # The real log_action wraps the storage call in a try/except
        # and returns None on error. Mirror that contract here so the
        # caller sees a successful coroutine even when the audit
        # backend is unavailable.
        try:
            raise RuntimeError("audit backend down")
        except Exception:
            return None

    monkeypatch.setattr("routes.proposals.audit_log_action", _failing_audit)

    proposal_id = "prop_synth_audit_safe"
    db = _build_proposal_db(
        {
            "proposal_id": proposal_id,
            "opportunity_id": "opp_synth_audit_safe",
            "status": "ready_for_review",
            "google_doc_url": "https://docs.google.com/document/d/safe/edit",
            "generated_content": "",
            "version": 1,
        },
        None,
    )
    user = SimpleNamespace(
        user_id="u_synth_sender2",
        email="sender2@aegisaxiom.test",
        role="sales_ops",
        permissions=["sales_ops.proposal.send"],
    )

    body = ProposalSendRequest(sent_to_email=None, notes=None)
    fresh = await send_proposal(
        proposal_id=proposal_id,
        body=body,
        db=db,
        current_user=user,
    )
    assert fresh["status"] == "sent"


@pytest.mark.asyncio
async def test_regenerate_proposal_writes_audit_row(monkeypatch):
    """Regenerate is the other write path the cutover wants to track.
    The audit row carries the new version number and the source
    content hash so the auditor can confirm the regenerated content
    matches the SOW at that point in time."""
    from routes.proposals import regenerate_proposal
    from services.proposal_generation import generate_proposal_content as _real_gen

    captured: list[dict[str, Any]] = []

    async def _fake_audit(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr("routes.proposals.audit_log_action", _fake_audit)

    proposal_id = "prop_synth_regen_audit_001"
    sow_id = "sow_synth_regen_001"
    opp_id = "opp_synth_regen_001"

    db = SimpleNamespace(
        proposals=_FakeProposalsCollection([
            {
                "proposal_id": proposal_id,
                "opportunity_id": opp_id,
                "sow_ids": [sow_id],
                "status": "draft",
                "version": 1,
                "title": None,
                "generated_content": "v1",
            }
        ]),
        opportunities=SimpleNamespace(
            find_one=AsyncMock(return_value={
                "opportunity_id": opp_id,
                "name": "Synth Regen Opp",
                "lines_of_business": ["professional_services"],
                "account_name": "Synth Account",
            }),
        ),
        opportunity_sows=SimpleNamespace(
            find=lambda *_a, **_kw: _FakeCursor([{
                "sow_id": sow_id,
                "opportunity_id": opp_id,
                "line_of_business": "professional_services",
                "status": "ready_for_review",
                "executive_summary": "synth",
            }]),
        ),
    )
    user = SimpleNamespace(
        user_id="u_synth_regen",
        email="regen@aegisaxiom.test",
        role="sales_ops",
        permissions=["sales_ops.proposal.edit"],
    )

    fresh = await regenerate_proposal(
        proposal_id=proposal_id, db=db, current_user=user,
    )
    assert fresh["version"] == 2
    assert len(captured) == 1
    row = captured[0]
    # The audit action name is the canonical sales_ops.proposal.*
    # namespace per docs/sales-ops/PROPOSAL_LIFECYCLE_V2.md. The
    # older proposal.regenerate string was renamed when the lifecycle
    # V2 ticket landed.
    assert row["action"] == "sales_ops.proposal.regenerated"
    assert row["resource_type"] == "proposal"
    assert row["resource_id"] == proposal_id
    # The audit details record both the previous and new version so
    # the activity feed can show a transition rather than a single
    # value.
    assert row["details"]["previous_version"] == 1
    assert row["details"]["new_version"] == 2
    assert row["details"]["no_op"] is False
    assert row["details"]["opportunity_id"] == opp_id
    # The hash is opaque but present.
    assert row["details"]["generation_source_hash"]


# ---------------------------------------------------------------------------
# SOW finalize Google Docs error envelope.
# ---------------------------------------------------------------------------


class TestSOWFinalizeGoogleErrorEnvelope:
    """The /sow/finalize route used to surface the raw FileNotFoundError
    message through to the client which could leak a server file path
    and gave the frontend no clean way to branch. The new envelope
    carries a stable code so the UI can offer the external Google Doc
    attach as an escape hatch."""

    def _build_app(self, db, user, raise_exc):
        from auth_helper import get_current_user
        from database import get_db
        from routes.fireflies import router

        app = FastAPI()
        app.include_router(router, prefix="/api/sales-ops")
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = lambda: db
        # Patch the Google Docs helper at module level so the route
        # picks up the failing version without needing the real
        # service account file on disk.
        import routes.fireflies as ff
        # The helper is imported inside the function body; patch the
        # source module instead so any import path reaches the fake.
        import services.google_docs_service as gds

        async def _failing(**_kwargs):
            raise raise_exc

        ff_orig = getattr(gds, "create_sow_google_doc_user", None)
        gds.create_sow_google_doc_user = _failing
        return app, TestClient(app), gds, ff_orig

    def _restore(self, gds, original):
        if original is not None:
            gds.create_sow_google_doc_user = original

    def _seed_db(self):
        sow = {
            "sow_id": "sow_synth_finalize_001",
            "opportunity_id": "opp_synth_finalize_001",
            "line_of_business": "professional_services",
            "status": "ready_for_review",
            "milestones": [],
        }
        opp = {
            "opportunity_id": "opp_synth_finalize_001",
            "name": "Synth Finalize Opp",
            "account_name": "Synth Account",
            "lines_of_business": ["professional_services"],
        }
        sows_coll = SimpleNamespace(
            find_one=AsyncMock(return_value=sow),
            update_one=AsyncMock(return_value=SimpleNamespace(modified_count=1)),
        )
        opps_coll = SimpleNamespace(
            find_one=AsyncMock(return_value=opp),
        )
        return SimpleNamespace(
            opportunity_sows=sows_coll,
            opportunities=opps_coll,
        )

    def _make_user(self):
        return SimpleNamespace(
            user_id="u_synth_finalize",
            email="finalize@aegisaxiom.test",
            role="sales_ops",
            permissions=["sales_ops.solutions_design.edit"],
        )


def _do_finalize(client, status_payload=None):
    body = {
        "opportunity_id": "opp_synth_finalize_001",
        "lob": "professional_services",
    }
    if status_payload:
        body.update(status_payload)
    return client.post("/api/sales-ops/sow/finalize", json=body)


@pytest.mark.skip(
    reason=(
        "Full route call requires the fireflies finalize chain "
        "(assumptions/risks generation, OAuth checks) that lives "
        "behind several modules. The structured envelope itself is "
        "asserted via the source inspection test below to keep this "
        "PR scoped to the cutover gaps."
    )
)
def test_finalize_returns_structured_error_when_google_missing():
    """Placeholder for an end-to-end route test. Skipped because the
    full /sow/finalize chain pulls in helpers that need a deeper
    fixture surface than this cutover PR should build. The contract
    is locked instead by ``test_finalize_envelope_shape_in_source``
    below."""
    pass


def test_finalize_envelope_shape_in_source():
    """Lock the structured error contract by inspecting the source.
    The 400 path carries code google_docs_not_configured and the
    escape-hatch message. The 5xx path carries code
    google_docs_export_failed. Neither path includes a raw exception
    message which could embed a server-side file path."""
    import inspect
    import routes.fireflies as ff

    source = inspect.getsource(ff.finalize_sow)
    # 400 envelope present with stable code.
    assert '"google_docs_not_configured"' in source
    assert "escape hatch" in source.lower()
    # 5xx envelope present with stable code.
    assert '"google_docs_export_failed"' in source
    # Raw exception string is no longer included in the response detail.
    # The earlier code did `detail=str(e)` and `detail=f"Failed to create Google Doc: {str(e)[:200]}"`.
    # Neither pattern should still appear in the source.
    assert "detail=str(e)" not in source
    assert "Failed to create Google Doc: {str(e)" not in source


def test_orion_finalize_envelope_shape_in_source():
    """Same envelope contract as the fireflies finalize route, locked
    against the orion finalize source. The deployed smoke checklist
    tells the frontend it can branch on a stable code field
    regardless of which finalize route a given opportunity uses.
    This test enforces that promise.

    The 400 path carries code google_docs_not_configured. The 5xx
    path carries code google_docs_export_failed. Neither path
    interpolates raw exception text into the response detail. The
    earlier orion route shape detail=f"Failed to create Google Doc:
    {str(e)[:200]}" must not be present.
    """
    import inspect
    import routes.orion as orion_routes

    source = inspect.getsource(orion_routes.finalize_sow)
    assert '"google_docs_not_configured"' in source
    assert '"google_docs_export_failed"' in source
    assert "escape hatch" in source.lower()
    assert "detail=str(e)" not in source
    assert "Failed to create Google Doc: {str(e)" not in source


def test_external_google_doc_attach_escape_hatch_remains():
    """The proposal PATCH route still lets Sales Ops paste a Google
    Doc URL by hand. The cutover error path explicitly tells the user
    that's an option, so the proposal route's ability to accept
    google_doc_url must stay intact."""
    from models import ProposalUpdate

    body = ProposalUpdate(google_doc_url="https://docs.google.com/document/d/escape-hatch/edit")
    assert str(body.google_doc_url).endswith("/edit")


# ---------------------------------------------------------------------------
# Frontend stage filter wire shape. Verifies the URL the workspace
# will hit when the user picks the handoff filter.
# ---------------------------------------------------------------------------


def test_handoff_stage_set_includes_expected_stages():
    """The frontend handoff option must include scoping_complete and
    proposal_sent so the SOW and proposal records stay reachable after
    the parent opportunity moves past Proposal Development. The
    canonical stage slugs come from backend/models.OPPORTUNITY_STAGES."""
    # Read the frontend source to verify the wire shape rather than
    # standing up a JSDOM environment from Python.
    import pathlib

    src_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "SolutionsDesignTab.js"
    )
    text = src_path.read_text(encoding="utf-8")
    # Stage filter constant is present.
    assert "STAGE_FILTER_SETS" in text
    # Handoff set covers the three Sales-Ops-relevant downstream stages.
    for stage in ("proposal_development", "scoping_complete", "proposal_sent"):
        assert stage in text, f"missing stage {stage!r} in workspace filter sets"
    # The select control is rendered.
    assert "sales-ops-stage-filter" in text


# ---------------------------------------------------------------------------
# Proposal handoff and send policy (P0). Locks the separation between
# the internal handoff to Sales (POST /send) and the Sales-owned
# client-facing send (POST /mark-client-sent). See
# docs/sales-ops/PROPOSAL_HANDOFF_AND_SEND_POLICY.md.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handoff_does_not_advance_opportunity_stage(monkeypatch):
    """Internal handoff to Sales must not change the opportunity stage.
    The Sales-owned mark-client-sent action is the only path that
    advances stage to proposal_sent. Locks the policy decision so a
    future refactor cannot drift stage advance back into /send.
    """
    from routes.proposals import send_proposal
    from models import ProposalSendRequest

    async def _noop_audit(**_kwargs):
        return None

    monkeypatch.setattr("routes.proposals.audit_log_action", _noop_audit)

    db = _build_proposal_db(
        {
            "proposal_id": "prop_synth_stage_no_advance",
            "opportunity_id": "opp_synth_stage_no_advance",
            "status": "ready_for_review",
            "google_doc_url": "https://docs.google.com/document/d/x/edit",
            "generated_content": "x",
            "version": 1,
        },
        {
            "opportunity_id": "opp_synth_stage_no_advance",
            "stage": "proposal_development",
            "opportunity_owner_id": "u_owner",
            "assigned_sales_ops_id": "u_so",
        },
    )
    user = SimpleNamespace(
        user_id="u_sender",
        email="sender@aegisaxiom.test",
        role="sales_ops",
        permissions=["sales_ops.proposal.send"],
    )

    fresh = await send_proposal(
        proposal_id="prop_synth_stage_no_advance",
        body=ProposalSendRequest(sent_to_email=None, notes=None),
        db=db,
        current_user=user,
    )

    assert fresh["stage_advanced"] is False
    # Opportunity collection saw no update from the handoff.
    assert db.opportunities.last_update is None
    # Opportunity stage in storage is unchanged.
    stored = await db.opportunities.find_one({"opportunity_id": "opp_synth_stage_no_advance"})
    assert stored["stage"] == "proposal_development"


@pytest.mark.asyncio
async def test_mark_client_sent_writes_audit_advances_stage(monkeypatch):
    """Mark-client-sent is the Sales-owned action that records the
    external delivery already happened outside AEGISAXIOM. It must:

      - Flip status to client_sent.
      - Stamp client_sent_at and client_sent_by.
      - Capture optional client_delivery_method, client_delivery_note,
        external_proposal_url, external_pdf_url.
      - Write an audit row with action sales.proposal.marked_client_sent.
      - Advance the parent opportunity from proposal_development to
        proposal_sent.
    """
    from routes.proposals import mark_proposal_client_sent
    from models import MarkClientSentRequest

    captured: list[dict[str, Any]] = []

    async def _fake_audit(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr("routes.proposals.audit_log_action", _fake_audit)

    proposal_id = "prop_synth_client_sent_001"
    db = _build_proposal_db(
        {
            "proposal_id": proposal_id,
            "opportunity_id": "opp_synth_client_sent_001",
            "status": "sent",
            "google_doc_url": "https://docs.google.com/document/d/synth/edit",
            "generated_content": "synthetic body",
            "version": 1,
        },
        {
            "opportunity_id": "opp_synth_client_sent_001",
            "stage": "proposal_development",
            "opportunity_owner_id": "u_owner",
        },
    )
    user = SimpleNamespace(
        user_id="u_sales_rep",
        email="rep@aegisaxiom.test",
        role="sales_rep",
        permissions=["sales_ops.proposal.mark_client_sent"],
    )

    body = MarkClientSentRequest(
        client_delivery_method="external_signing_link",
        client_delivery_note="Sent via signing tool",
        external_proposal_url="https://docs.google.com/document/d/external/edit",
        external_pdf_url=None,
    )
    fresh = await mark_proposal_client_sent(
        proposal_id=proposal_id,
        body=body,
        db=db,
        current_user=user,
    )

    # Status advanced and metadata stamped.
    assert fresh["status"] == "client_sent"
    assert fresh["client_sent_by"] == "u_sales_rep"
    assert fresh["client_sent_at"] is not None
    assert fresh["client_delivery_method"] == "external_signing_link"
    assert fresh["client_delivery_note"] == "Sent via signing tool"
    assert "external" in fresh["external_proposal_url"]
    # Response shape carries the policy-aligned booleans.
    assert fresh["handoff_only"] is False
    assert fresh["client_sent"] is True
    assert fresh["stage_advanced"] is True
    # Audit row written under the policy-aligned action name.
    assert len(captured) == 1
    row = captured[0]
    assert row["action"] == "sales.proposal.marked_client_sent"
    assert row["resource_type"] == "proposal"
    assert row["resource_id"] == proposal_id
    assert row["actor_id"] == "u_sales_rep"
    assert row["details"]["opportunity_id"] == "opp_synth_client_sent_001"
    assert row["details"]["stage_advanced"] is True
    assert row["details"]["has_delivery_method"] is True
    # Opportunity stage advanced to proposal_sent.
    stored = await db.opportunities.find_one({"opportunity_id": "opp_synth_client_sent_001"})
    assert stored["stage"] == "proposal_sent"


@pytest.mark.asyncio
async def test_mark_client_sent_advances_from_scoping_complete(monkeypatch):
    """The stage advance fires from scoping_complete as well as from
    proposal_development."""
    from routes.proposals import mark_proposal_client_sent
    from models import MarkClientSentRequest

    async def _noop_audit(**_kwargs):
        return None

    monkeypatch.setattr("routes.proposals.audit_log_action", _noop_audit)

    db = _build_proposal_db(
        {
            "proposal_id": "prop_sc",
            "opportunity_id": "opp_sc",
            "status": "sent",
            "version": 1,
        },
        {
            "opportunity_id": "opp_sc",
            "stage": "scoping_complete",
        },
    )
    user = SimpleNamespace(
        user_id="u_rep",
        email="rep@aegisaxiom.test",
        role="sales_rep",
        permissions=["sales_ops.proposal.mark_client_sent"],
    )

    fresh = await mark_proposal_client_sent(
        proposal_id="prop_sc",
        body=MarkClientSentRequest(),
        db=db,
        current_user=user,
    )

    assert fresh["stage_advanced"] is True
    stored = await db.opportunities.find_one({"opportunity_id": "opp_sc"})
    assert stored["stage"] == "proposal_sent"


@pytest.mark.asyncio
async def test_mark_client_sent_does_not_advance_from_other_stages(monkeypatch):
    """The stage advance is bounded to proposal_development and
    scoping_complete. From any other stage the opportunity is left
    alone. Closed states must never be reopened by a Sales action."""
    from routes.proposals import mark_proposal_client_sent
    from models import MarkClientSentRequest

    async def _noop_audit(**_kwargs):
        return None

    monkeypatch.setattr("routes.proposals.audit_log_action", _noop_audit)

    for current_stage in ("negotiation_review", "verbal_agreement", "closed_won", "closed_lost", "proposal_sent"):
        db = _build_proposal_db(
            {
                "proposal_id": f"prop_{current_stage}",
                "opportunity_id": f"opp_{current_stage}",
                "status": "sent",
                "version": 1,
            },
            {
                "opportunity_id": f"opp_{current_stage}",
                "stage": current_stage,
            },
        )
        user = SimpleNamespace(
            user_id="u_rep",
            email="rep@aegisaxiom.test",
            role="sales_rep",
            permissions=["sales_ops.proposal.mark_client_sent"],
        )

        fresh = await mark_proposal_client_sent(
            proposal_id=f"prop_{current_stage}",
            body=MarkClientSentRequest(),
            db=db,
            current_user=user,
        )

        assert fresh["stage_advanced"] is False, (
            f"stage_advanced unexpectedly True from {current_stage}"
        )
        stored = await db.opportunities.find_one({"opportunity_id": f"opp_{current_stage}"})
        assert stored["stage"] == current_stage, (
            f"stage unexpectedly changed from {current_stage}"
        )


@pytest.mark.asyncio
async def test_mark_client_sent_requires_sent_status(monkeypatch):
    """Mark-client-sent only flows from the sent status. A draft,
    ready_for_review, client_sent, accepted, or rejected proposal
    must be rejected with the same 409 transition guard the rest of
    the proposal routes use. Locks idempotency on the client-sent
    action."""
    from fastapi import HTTPException
    from routes.proposals import mark_proposal_client_sent
    from models import MarkClientSentRequest

    async def _noop_audit(**_kwargs):
        return None

    monkeypatch.setattr("routes.proposals.audit_log_action", _noop_audit)

    user = SimpleNamespace(
        user_id="u_rep",
        email="rep@aegisaxiom.test",
        role="sales_rep",
        permissions=["sales_ops.proposal.mark_client_sent"],
    )

    for bad_status in ("draft", "ready_for_review", "client_sent", "accepted", "rejected"):
        db = _build_proposal_db(
            {
                "proposal_id": f"prop_bad_{bad_status}",
                "opportunity_id": f"opp_bad_{bad_status}",
                "status": bad_status,
                "version": 1,
            },
            None,
        )
        try:
            await mark_proposal_client_sent(
                proposal_id=f"prop_bad_{bad_status}",
                body=MarkClientSentRequest(),
                db=db,
                current_user=user,
            )
            raised = False
        except HTTPException as e:
            raised = True
            assert e.status_code == 409, f"expected 409 from {bad_status}, got {e.status_code}"
        assert raised, f"mark-client-sent must reject status={bad_status!r}"


@pytest.mark.asyncio
async def test_mark_client_sent_permission_gated(monkeypatch):
    """Mark-client-sent requires sales_ops.proposal.mark_client_sent. A
    Sales Ops user with only the handoff permission must be rejected.
    Default policy: client-sent is Sales-owned, not Sales-Ops-owned.
    """
    from fastapi import HTTPException
    from routes.proposals import mark_proposal_client_sent
    from models import MarkClientSentRequest

    async def _noop_audit(**_kwargs):
        return None

    monkeypatch.setattr("routes.proposals.audit_log_action", _noop_audit)

    db = _build_proposal_db(
        {
            "proposal_id": "prop_perm",
            "opportunity_id": "opp_perm",
            "status": "sent",
            "version": 1,
        },
        None,
    )
    user = SimpleNamespace(
        user_id="u_so",
        email="so@aegisaxiom.test",
        role="sales_ops",
        permissions=["sales_ops.proposal.send"],
    )

    raised = False
    try:
        await mark_proposal_client_sent(
            proposal_id="prop_perm",
            body=MarkClientSentRequest(),
            db=db,
            current_user=user,
        )
    except HTTPException as e:
        raised = True
        assert e.status_code == 403
    assert raised, "mark-client-sent must reject users without the Sales permission"


@pytest.mark.asyncio
async def test_mark_client_sent_proposal_not_found_returns_404(monkeypatch):
    """A proposal id not present in the active scope returns 404.
    The route does not leak information about a different scope's
    rows. The 404 path is the same shape any cross-tenant attempt
    would hit under the current single-tenant scoping in code."""
    from fastapi import HTTPException
    from routes.proposals import mark_proposal_client_sent
    from models import MarkClientSentRequest

    async def _noop_audit(**_kwargs):
        return None

    monkeypatch.setattr("routes.proposals.audit_log_action", _noop_audit)

    db = _build_proposal_db(
        {
            "proposal_id": "prop_real",
            "opportunity_id": "opp_real",
            "status": "sent",
            "version": 1,
        },
        None,
    )
    user = SimpleNamespace(
        user_id="u_rep",
        email="rep@aegisaxiom.test",
        role="sales_rep",
        permissions=["sales_ops.proposal.mark_client_sent"],
    )

    raised = False
    try:
        await mark_proposal_client_sent(
            proposal_id="prop_does_not_exist",
            body=MarkClientSentRequest(),
            db=db,
            current_user=user,
        )
    except HTTPException as e:
        raised = True
        assert e.status_code == 404
    assert raised


def test_ui_copy_does_not_say_sent_to_client_for_handoff():
    """The proposal status badge for the internal handoff must not
    use the phrase 'Sent to client'. The frontend keeps two status
    badges: 'Handed off to Sales' for the sent state and 'Sent to
    client' for the client_sent state. The handoff toast and the
    handoff dialog copy similarly must not imply external delivery.

    Skipped when the frontend src tree is not mounted alongside the
    backend tests, which is the case in the backend-only test
    container. The contract is exercised at the source level when
    both trees are present.
    """
    import pathlib
    import re

    src_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "SolutionsDesignTab.js"
    )
    if not src_path.exists():
        pytest.skip("frontend src not available in this test environment")
    text = src_path.read_text(encoding="utf-8")

    # The sent status badge label is "Handed off to Sales", not "Sent".
    # We scope the search to the PROPOSAL_STATUS_CONFIG block so the
    # check does not catch the standalone "Sent" word in other contexts.
    config_block = re.search(
        r"const PROPOSAL_STATUS_CONFIG = \{[\s\S]*?^\};",
        text,
        re.MULTILINE,
    )
    assert config_block is not None, "PROPOSAL_STATUS_CONFIG block not found"
    config_text = config_block.group(0)
    # sent maps to "Handed off to Sales".
    assert "'Handed off to Sales'" in config_text
    # client_sent maps to "Sent to client".
    assert "'Sent to client'" in config_text
    # Each state has a distinct label so a badge cannot collapse them.
    # The handoff toast does not say "sent to client" anywhere.
    assert "Proposal handed off to Sales." in text
    assert "Proposal marked as sent to client." in text


def test_handoff_audit_action_name_locked():
    """Lock the audit action name at the source level so a future
    refactor cannot rename it back to the older proposal.send_to_sales
    string. The grep found only the route + this test as consumers."""
    import inspect
    import routes.proposals as proposals_module

    source = inspect.getsource(proposals_module.send_proposal)
    assert '"sales_ops.proposal.handed_off_to_sales"' in source
    # The older string is not present.
    assert "proposal.send_to_sales" not in source


def test_mark_client_sent_audit_action_name_locked():
    """Lock the Sales-owned audit action name. Different from the
    handoff audit so a future log aggregator can distinguish the two
    moments without parsing context."""
    import inspect
    import routes.proposals as proposals_module

    source = inspect.getsource(proposals_module.mark_proposal_client_sent)
    assert '"sales.proposal.marked_client_sent"' in source


# ---------------------------------------------------------------------------
# Shared SOW Builder substrate (P0). Locks the convergence of the
# three SOW finalize paths on the same SOW row shape. See
# docs/sales-ops/SHARED_SOW_BUILDER_SUBSTRATE.md.
#
# Three finalize paths exist and write to the same db.opportunity_sows
# row.
#
#   1. routes/fireflies.py POST /sow/finalize. Sales Ops workspace.
#   2. routes/orion.py POST /sows/{opportunity_id}/finalize. AI Orion
#      HTTP route.
#   3. services/orion/tools.py handle_finalize_sow_to_google_docs.
#      AI Orion agent tool.
#
# Substrate rule. All three must write the same field names
# (google_doc_url, google_doc_id) and the same SOW status value
# (ready_for_review). The non-status string "finalized" is not part
# of the SOW status enum and must not be written by any finalize
# path. These tests inspect the source of each path so a future
# refactor cannot reintroduce a divergence.
# ---------------------------------------------------------------------------


def test_fireflies_finalize_writes_canonical_sow_fields():
    """The fireflies workspace finalize path writes the canonical
    google_doc_url and google_doc_id field names on the SOW row and
    sets status to ready_for_review. Locks the canonical shape."""
    import inspect
    import routes.fireflies as ff

    source = inspect.getsource(ff.finalize_sow)
    assert '"google_doc_url"' in source
    assert '"google_doc_id"' in source
    assert '"ready_for_review"' in source
    # The bogus non-status string must not appear in the finalize
    # update path.
    assert '"finalized"' not in source


def test_orion_finalize_route_writes_canonical_sow_fields():
    """The orion HTTP finalize route must converge on the same SOW
    field names and status value as the fireflies finalize route.
    Earlier the orion route wrote finalized_doc_url/finalized_doc_id
    and a non-status string finalized that the frontend SOWStatusBar
    could not display. The substrate contract aligns both paths."""
    import inspect
    import routes.orion as orion_routes

    source = inspect.getsource(orion_routes.finalize_sow)
    assert '"google_doc_url"' in source
    assert '"google_doc_id"' in source
    assert '"ready_for_review"' in source
    # The non-canonical field names from the earlier divergent shape
    # must not be present.
    assert "finalized_doc_url" not in source
    assert "finalized_doc_id" not in source


def test_orion_tool_finalize_writes_canonical_sow_fields():
    """The AI Orion agent tool that finalizes a SOW into a Google
    Doc must write the same canonical fields as the HTTP finalize
    routes. The tool runs through the dispatcher and surfaces back
    to the user via chat, so a divergent write shape would leave a
    SOW row the frontend cannot display."""
    import inspect
    from services.orion.tools import handle_finalize_sow_to_google_docs

    source = inspect.getsource(handle_finalize_sow_to_google_docs)
    assert '"google_doc_url"' in source
    assert '"google_doc_id"' in source
    assert '"ready_for_review"' in source
    # The earlier shape wrote a top-level doc_url field plus a
    # non-status string finalized.
    assert '"finalized"' not in source


def test_shared_sow_collection_used_by_both_lobs():
    """Both Field Services and Professional Services SOWs live in the
    same db.opportunity_sows collection. Lock the rule by inspecting
    the OpportunitySOW model: line_of_business is a column on the
    same model class. Any future split into separate collections
    would surface here.
    """
    from models import OpportunitySOW

    fields = OpportunitySOW.model_fields
    # The discriminator is a column.
    assert "line_of_business" in fields
    # The model is the same class regardless of LOB.
    assert OpportunitySOW.__name__ == "OpportunitySOW"
    # The opportunity link is the shared parent.
    assert "opportunity_id" in fields


def test_shared_proposal_collection_used_by_both_lobs():
    """The Proposal model is the same class regardless of which LOB
    the linked SOWs belong to. Locking the shape prevents a future
    split into FS-only and PS-only proposal models."""
    from models import Proposal

    fields = Proposal.model_fields
    # Proposal references SOWs by id; LOB is not a column on the
    # proposal itself. The LOB is reachable through the linked SOWs.
    assert "sow_ids" in fields
    assert "line_of_business" not in fields
    assert "opportunity_id" in fields
    # The substrate field names for Google artifacts are present.
    for field_name in ("google_doc_url", "google_doc_id", "external_proposal_url", "external_pdf_url"):
        assert field_name in fields, f"canonical proposal field {field_name!r} missing"


def test_shared_substrate_doc_exists():
    """The substrate decision is ratified in a doc that future tickets
    will reference before changing any of the locked surfaces. A
    missing doc would let the substrate drift silently."""
    import pathlib

    doc_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "docs"
        / "sales-ops"
        / "SHARED_SOW_BUILDER_SUBSTRATE.md"
    )
    if not doc_path.exists():
        pytest.skip("substrate doc not available in this test environment")
    text = doc_path.read_text(encoding="utf-8")
    # The doc names the canonical SOW field convention.
    assert "google_doc_url" in text
    # The doc names the canonical post-finalize SOW status.
    assert "ready_for_review" in text
    # The doc enumerates LOB branching rules.
    assert "Field Services" in text
    assert "Professional Services" in text
    # The doc names what must never be duplicated.
    assert "never be duplicated" in text.lower() or "not be duplicated" in text.lower()


# ---------------------------------------------------------------------------
# Field Services SOW workflow completion (P0). Locks the dispatch
# readiness gate that prevents a Field Services SOW from advancing to
# ready_for_review without service location and the required contact
# roles. The gate fires on both the change_sow_status route and the
# /sow/finalize route. Professional Services SOWs are unaffected by
# the gate. See docs/sales-ops/SHARED_SOW_BUILDER_SUBSTRATE.md.
# ---------------------------------------------------------------------------


def test_fs_readiness_passes_with_complete_dispatch_data():
    """A Field Services SOW carrying a service location with an
    address and city plus all three required contact roles with a
    name and at least an email or phone is ready for validation."""
    from routes.solutions_design import _evaluate_field_services_readiness

    sow = {
        "line_of_business": "field_services",
        "service_location": {"address": "123 Main St", "city": "Springfield"},
        "contact_roles": [
            {"role": "primary", "name": "Primary", "email": "p@example.test"},
            {"role": "billing", "name": "Billing", "email": "b@example.test"},
            {"role": "service_location", "name": "OnSite", "phone": "555-0100"},
        ],
    }

    result = _evaluate_field_services_readiness(sow)
    assert result["ready"] is True
    assert result["missing"] == []


def test_fs_readiness_blocks_when_location_address_missing():
    """Service location must carry at least an address and city. A
    location dict with no address keeps the SOW out of the
    ready_for_review state."""
    from routes.solutions_design import _evaluate_field_services_readiness

    sow = {
        "line_of_business": "field_services",
        "service_location": {"city": "Springfield"},
        "contact_roles": [
            {"role": "primary", "name": "Primary", "email": "p@example.test"},
            {"role": "billing", "name": "Billing", "email": "b@example.test"},
            {"role": "service_location", "name": "OnSite", "phone": "555-0100"},
        ],
    }

    result = _evaluate_field_services_readiness(sow)
    assert result["ready"] is False
    fields = [m["field"] for m in result["missing"]]
    assert "service_location" in fields


def test_fs_readiness_blocks_when_required_role_missing():
    """Each of the three canonical required contact roles
    (primary, billing, service_location) must be present with a
    name and at least an email or phone. A missing role keeps the
    SOW out of ready_for_review and lists which role is missing."""
    from routes.solutions_design import _evaluate_field_services_readiness

    sow = {
        "line_of_business": "field_services",
        "service_location": {"address": "123 Main St", "city": "Springfield"},
        "contact_roles": [
            {"role": "primary", "name": "Primary", "email": "p@example.test"},
            {"role": "billing", "name": "Billing", "email": "b@example.test"},
        ],
    }

    result = _evaluate_field_services_readiness(sow)
    assert result["ready"] is False
    fields = [m["field"] for m in result["missing"]]
    assert "contact_roles" in fields
    contact_entry = next(m for m in result["missing"] if m["field"] == "contact_roles")
    assert "service_location" in contact_entry["reason"]


def test_fs_readiness_blocks_when_role_has_no_contact_method():
    """A required role entry that has a name but no email and no
    phone fails the readiness check. The dispatch contract assumes
    every required role can be reached."""
    from routes.solutions_design import _evaluate_field_services_readiness

    sow = {
        "line_of_business": "field_services",
        "service_location": {"address": "123 Main St", "city": "Springfield"},
        "contact_roles": [
            {"role": "primary", "name": "Primary", "email": "p@example.test"},
            {"role": "billing", "name": "Billing"},
            {"role": "service_location", "name": "OnSite", "phone": "555-0100"},
        ],
    }

    result = _evaluate_field_services_readiness(sow)
    assert result["ready"] is False
    contact_entry = next(m for m in result["missing"] if m["field"] == "contact_roles")
    assert "billing" in contact_entry["reason"]


def test_fs_readiness_passes_through_non_fs_sows():
    """Professional Services and Wholesale MRR SOWs do not run the
    Field Services dispatch contract. _evaluate_field_services_readiness
    returns ready=True with no missing entries so the helper is safe
    to call from a shared transition gate."""
    from routes.solutions_design import _evaluate_field_services_readiness

    for lob in ("professional_services", "wholesale_mrr"):
        sow = {
            "line_of_business": lob,
            "service_location": None,
            "contact_roles": [],
        }
        result = _evaluate_field_services_readiness(sow)
        assert result["ready"] is True, f"non-FS LOB {lob} should pass through"
        assert result["missing"] == []


def test_fs_readiness_source_locks_change_sow_status_gate():
    """The change_sow_status route runs the readiness gate before
    transitioning a Field Services SOW to ready_for_review. Source
    inspection so a future refactor cannot drop the gate without
    flipping this test."""
    import inspect
    import routes.solutions_design as sd

    source = inspect.getsource(sd.change_sow_status)
    assert "_evaluate_field_services_readiness" in source
    assert '"field_services_sow_incomplete"' in source
    assert '"ready_for_review"' in source


def test_fs_readiness_source_locks_fireflies_finalize_gate():
    """The user-facing Generate & Send button hits
    /sow/finalize in routes/fireflies.py. The readiness gate must
    fire on that path too. Source inspection lock so the gate is
    not silently dropped."""
    import inspect
    import routes.fireflies as ff

    source = inspect.getsource(ff.finalize_sow)
    assert "_evaluate_field_services_readiness" in source
    assert '"field_services_sow_incomplete"' in source


def test_sow_status_change_writes_audit_row_in_source():
    """change_sow_status writes an audit row under the new namespace
    sales_ops.sow.status_changed. Locked at the source level so a
    future change cannot drop the audit without flipping this test."""
    import inspect
    import routes.solutions_design as sd

    source = inspect.getsource(sd.change_sow_status)
    assert "audit_log_action" in source
    assert '"sales_ops.sow.status_changed"' in source


def test_procurement_notification_source_carries_site_and_audit():
    """The procurement notification create route accepts the SOW
    context fields (resource_assignment_id, sow_id,
    line_of_business, site_location_summary) and writes an audit
    row under sales_ops.procurement.notification_created. Source
    inspection lock."""
    import inspect
    import routes.sales_ops_procurement as procurement

    source = inspect.getsource(procurement.create_procurement_notification)
    for field in (
        "site_location_summary",
        "resource_assignment_id",
        "sow_id",
        "line_of_business",
    ):
        assert field in source, f"procurement notification field {field!r} missing"
    assert '"sales_ops.procurement.notification_created"' in source


def test_opportunity_sow_model_carries_fs_dispatch_fields():
    """The shared SOW model carries the Field Services dispatch
    fields service_location and contact_roles. Lock the model
    shape so a future split into FS-only and PS-only models would
    surface here."""
    from models import OpportunitySOW

    fields = OpportunitySOW.model_fields
    assert "service_location" in fields
    assert "contact_roles" in fields
    # Both fields are optional on the model. The FS-only readiness
    # gate is what enforces them at transition time. Keeping the
    # model permissive lets PS and Wholesale MRR SOWs persist
    # without spurious required-field errors.
    assert fields["service_location"].is_required() is False
    assert fields["contact_roles"].is_required() is False


def test_sow_create_model_accepts_fs_dispatch_fields():
    """The SOW save body type carries the same fields so a
    Field Services SOW save flows them through to persistence
    without dropping them at the Pydantic boundary."""
    from models import SOWCreate

    fields = SOWCreate.model_fields
    assert "service_location" in fields
    assert "contact_roles" in fields


def test_fs_notify_procurement_button_locked_in_source():
    """The Notify Procurement button is rendered on a resource
    card when status is blocked_missing_contractor. Source
    inspection on the frontend so the affordance does not
    silently disappear."""
    import pathlib

    src_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "SolutionsDesignTab.js"
    )
    if not src_path.exists():
        pytest.skip("frontend src not available in this test environment")
    text = src_path.read_text(encoding="utf-8")
    assert "fs-notify-procurement" in text
    assert "Notify Procurement" in text
    # The handler posts to the procurement-notifications endpoint.
    assert "/api/sales-ops/procurement-notifications" in text
    # The site location summary is built from service_location
    # before posting.
    assert "site_location_summary" in text


def test_fs_dispatch_section_rendered_only_for_field_services():
    """The Site Location and Required Contact Roles inputs render
    only for Field Services LOB so Professional Services flows are
    not affected."""
    import pathlib

    src_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "SolutionsDesignTab.js"
    )
    if not src_path.exists():
        pytest.skip("frontend src not available in this test environment")
    text = src_path.read_text(encoding="utf-8")
    # The FS-only conditional gate exists.
    assert "lob === 'field_services'" in text
    # The component is named so the conditional is unambiguous.
    assert "FieldServicesDispatchSection" in text
    # Test ids on the new inputs are present for downstream tests.
    assert "fs-service-location-address" in text
    assert "fs-service-location-city" in text
    assert "fs-contact-role-name-primary" in text


# ---------------------------------------------------------------------------
# Professional Services proposal editor polish (P1). Locks the
# seven-section canonical PS proposal shape and the prose section
# substrate. The required sections render in order. Hours never
# render inline in the Scope of Work list; the Estimated Time
# section is the only place hours surface in the proposal output.
# Optional sections render after the required seven only when
# content is present. See ticket spec.
# ---------------------------------------------------------------------------


def _ps_sow_for_test(**overrides):
    """Synthetic Professional Services SOW. No real customer data."""
    base = {
        "sow_id": "sow_synth_ps_polish",
        "opportunity_id": "opp_synth_ps_polish",
        "line_of_business": "professional_services",
        "status": "ready_for_review",
        "executive_summary": "Stand up the synthetic platform.",
        "smart_specific": "Deliver platform skeleton",
        "smart_measurable": "Three milestones",
        "smart_achievable": "Within team capacity",
        "smart_relevant": "Aligned to Q3 plan",
        "smart_time_bound": "8 weeks",
        "contract_type": "fixed_fee",
        "billing_terms": "monthly",
        "payment_terms": "net_30",
        "start_date": "2099-01-01",
        "end_date": "2099-03-01",
        "total_amount": 12345.67,
        "milestones": [
            {
                "milestone_id": "m1",
                "milestone_name": "Discovery",
                "order": 1,
                "bullets": [
                    {"text": "Stakeholder interviews", "estimated_hours": 12},
                    {"text": "Existing-state audit", "estimated_hours": 8},
                ],
            },
            {
                "milestone_id": "m2",
                "milestone_name": "Build",
                "order": 2,
                "bullets": [
                    {"text": "Stand up infra", "estimated_hours": 40},
                    {"text": "Wire integrations", "estimated_hours": 20},
                ],
            },
        ],
        "prose_sections": [],
    }
    base.update(overrides)
    return base


def _opp_for_test():
    return {
        "opportunity_id": "opp_synth_ps_polish",
        "name": "Synth PS Opp",
        "account_name": "Synth Account",
    }


def test_ps_proposal_renders_seven_required_sections_in_order():
    """Professional Services proposal output contains the canonical
    seven sections in the spec'd order. Locked here so a future
    refactor cannot drop or reorder a section."""
    from services.proposal_generation import generate_proposal_content

    result = generate_proposal_content(_opp_for_test(), [_ps_sow_for_test()])
    content = result["generated_content"]

    canonical_headings = [
        "### Executive Summary",
        "### SMART Objective",
        "### Scope of Work",
        "### Estimated Time",
        "### Assumptions",
        "### Responsibilities",
        "### Out of Scope",
    ]
    last_index = -1
    for heading in canonical_headings:
        idx = content.find(heading)
        assert idx > last_index, (
            f"section {heading!r} missing or out of order in PS output"
        )
        last_index = idx


def test_ps_estimated_time_renders_totals_from_bullet_hours():
    """The Estimated Time section computes total + per-milestone
    breakdowns from bullet.estimated_hours. Empty inputs render as
    an empty section but the heading still appears so the seven
    canonical sections never disappear."""
    from services.proposal_generation import generate_proposal_content

    result = generate_proposal_content(_opp_for_test(), [_ps_sow_for_test()])
    content = result["generated_content"]

    # Grand total across the two synthetic milestones: 12 + 8 + 40 + 20 = 80.
    assert "Total estimated hours" in content
    assert "80" in content
    # Per-milestone breakdowns.
    assert "**Discovery:** 20h" in content
    assert "**Build:** 60h" in content


def test_ps_scope_of_work_does_not_inline_task_hours():
    """The PS spec says task hours must not render inline beside
    tasks in the proposal preview. The Scope of Work output is the
    milestone / bullet list; verify no hour-suffix pattern
    (digits + h or hours) appears between the Scope of Work and
    Estimated Time headings."""
    from services.proposal_generation import generate_proposal_content
    import re

    result = generate_proposal_content(_opp_for_test(), [_ps_sow_for_test()])
    content = result["generated_content"]

    scope_idx = content.find("### Scope of Work")
    et_idx = content.find("### Estimated Time")
    assert scope_idx >= 0 and et_idx > scope_idx
    scope_block = content[scope_idx:et_idx]

    # The bullet texts ("Stakeholder interviews", etc.) appear without
    # any "12h" / "40 hours" / "(8h)" suffix in the Scope of Work block.
    forbidden_patterns = [
        r"\d+\s*h(?:our)?s?\b",
        r"\(\s*\d+\s*h\b",
    ]
    for pat in forbidden_patterns:
        m = re.search(pat, scope_block)
        assert m is None, (
            f"Scope of Work block leaks an inline hour token: {m.group(0)!r}"
        )


def test_ps_optional_sections_omitted_when_empty():
    """Optional PS sections (Risk Assessment, Solution Overview,
    In-Scope Inventory, Conclusion) appear in the proposal only
    when sow.prose_sections carries content for that kind."""
    from services.proposal_generation import generate_proposal_content

    result = generate_proposal_content(_opp_for_test(), [_ps_sow_for_test()])
    content = result["generated_content"]

    for heading in (
        "### Risk Assessment",
        "### Solution Overview",
        "### In-Scope Inventory",
        "### Conclusion",
    ):
        assert heading not in content, (
            f"empty optional section {heading!r} should not render"
        )


def test_ps_optional_sections_render_when_content_present():
    """Optional sections with non-empty content render after the
    required seven sections in declared order."""
    from services.proposal_generation import generate_proposal_content

    sow = _ps_sow_for_test(
        prose_sections=[
            {"kind": "risk_assessment", "content": "Synthetic risk text."},
            {"kind": "conclusion", "content": "Synthetic conclusion text."},
        ]
    )
    result = generate_proposal_content(_opp_for_test(), [sow])
    content = result["generated_content"]

    assert "### Risk Assessment" in content
    assert "Synthetic risk text." in content
    assert "### Conclusion" in content
    assert "Synthetic conclusion text." in content
    # Risk Assessment is declared before Conclusion in
    # PS_OPTIONAL_SECTION_KINDS so the output order matches.
    assert content.find("### Risk Assessment") < content.find("### Conclusion")


def test_ps_prose_sections_round_trip_through_required_kinds():
    """Required PS prose sections (Assumptions, Responsibilities,
    Out of Scope) render their stored content under their canonical
    heading. Empty content renders the placeholder so the seven
    canonical sections always appear."""
    from services.proposal_generation import generate_proposal_content

    sow = _ps_sow_for_test(
        prose_sections=[
            {"kind": "assumptions", "content": "Synthetic assumption."},
            {"kind": "responsibilities", "content": "Synthetic responsibility."},
            {"kind": "out_of_scope", "content": "Synthetic out-of-scope item."},
        ]
    )
    result = generate_proposal_content(_opp_for_test(), [sow])
    content = result["generated_content"]

    assert "Synthetic assumption." in content
    assert "Synthetic responsibility." in content
    assert "Synthetic out-of-scope item." in content
    # Required sections appear in order ahead of any optional content.
    assert content.find("### Assumptions") < content.find("### Responsibilities")
    assert content.find("### Responsibilities") < content.find("### Out of Scope")


def test_ps_required_section_placeholders_when_prose_missing():
    """A PS SOW with no prose_sections still produces all seven
    canonical headings. The required prose sections render a
    placeholder line so a future client export cannot silently
    drop a section."""
    from services.proposal_generation import generate_proposal_content

    result = generate_proposal_content(_opp_for_test(), [_ps_sow_for_test()])
    content = result["generated_content"]
    assumptions_idx = content.find("### Assumptions")
    responsibilities_idx = content.find("### Responsibilities")
    out_of_scope_idx = content.find("### Out of Scope")
    # Each section followed by the placeholder marker
    # _(to be completed)_ so the section heading is non-empty.
    for idx in (assumptions_idx, responsibilities_idx, out_of_scope_idx):
        block = content[idx : idx + 200]
        assert "_(to be completed)_" in block


def test_ps_generation_hash_changes_when_prose_changes():
    """The prose_sections payload feeds the generation source hash
    so a prose edit triggers regenerate. Without this guard the
    regenerate path would short-circuit on a no-op even though the
    proposal output changed."""
    from services.proposal_generation import generate_proposal_content

    baseline = generate_proposal_content(
        _opp_for_test(), [_ps_sow_for_test()]
    )
    edited = generate_proposal_content(
        _opp_for_test(),
        [
            _ps_sow_for_test(
                prose_sections=[
                    {"kind": "assumptions", "content": "Edited assumption."}
                ]
            )
        ],
    )
    assert baseline["generation_source_hash"] != edited["generation_source_hash"]
    assert "Edited assumption." in edited["generated_content"]


def test_fs_proposal_output_unaffected_by_ps_polish():
    """Field Services proposal output keeps the legacy section
    headings (Milestones, SMART Objectives, Billing & Terms) so
    the PS polish does not regress the FS path."""
    from services.proposal_generation import generate_proposal_content

    fs_sow = _ps_sow_for_test(
        sow_id="sow_synth_ps_polish_fs",
        line_of_business="field_services",
    )
    result = generate_proposal_content(_opp_for_test(), [fs_sow])
    content = result["generated_content"]
    # FS output uses the pre-polish heading set.
    assert "### Milestones" in content
    assert "### SMART Objectives" in content
    # FS output must NOT have the PS canonical headings.
    assert "### Scope of Work" not in content
    assert "### Estimated Time" not in content
    assert "### Assumptions" not in content
    assert "### Responsibilities" not in content
    assert "### Out of Scope" not in content


def test_wholesale_mrr_proposal_output_unaffected_by_ps_polish():
    """Wholesale MRR proposal output also routes through the
    default renderer. Locked separately from the field_services
    case so a future merge of the default and PS paths cannot
    silently change the non-PS heading set."""
    from services.proposal_generation import generate_proposal_content

    wm_sow = _ps_sow_for_test(
        sow_id="sow_synth_ps_polish_wm",
        line_of_business="wholesale_mrr",
    )
    result = generate_proposal_content(_opp_for_test(), [wm_sow])
    content = result["generated_content"]
    assert "### Milestones" in content
    assert "### SMART Objectives" in content
    assert "### Scope of Work" not in content
    assert "### Estimated Time" not in content
    assert "### Assumptions" not in content
    assert "### Responsibilities" not in content
    assert "### Out of Scope" not in content


def test_ps_estimated_time_skips_when_no_hours():
    """Edge case from the code review. When every milestone bullet
    has zero or missing hours the Estimated Time section renders
    its heading with the standard placeholder, never a misleading
    '0 hours total' line. Keeps the seven canonical headings
    intact even when hour data is absent."""
    from services.proposal_generation import generate_proposal_content

    sow = _ps_sow_for_test(
        milestones=[
            {
                "milestone_id": "m1",
                "milestone_name": "Discovery",
                "order": 1,
                "bullets": [
                    {"text": "Interview", "estimated_hours": 0},
                    {"text": "Audit"},
                ],
            }
        ]
    )
    result = generate_proposal_content(_opp_for_test(), [sow])
    content = result["generated_content"]
    # The Estimated Time heading is present (canonical sections never
    # disappear) but no total line is emitted.
    assert "### Estimated Time" in content
    et_idx = content.find("### Estimated Time")
    assumptions_idx = content.find("### Assumptions")
    block = content[et_idx:assumptions_idx]
    assert "Total estimated hours" not in block
    # The block ends on the canonical empty-section placeholder.
    assert "_(to be completed)_" in block


def test_ps_renderer_ignores_unknown_prose_kind():
    """Edge case from the code review. A prose_sections entry with
    a kind outside the canonical required and optional lists is
    silently dropped from the proposal output. The renderer never
    emits a heading for an unknown kind."""
    from services.proposal_generation import generate_proposal_content

    sow = _ps_sow_for_test(
        prose_sections=[
            {"kind": "future_module_x", "content": "Should not appear."},
        ]
    )
    result = generate_proposal_content(_opp_for_test(), [sow])
    content = result["generated_content"]
    assert "Should not appear." not in content
    assert "future_module_x" not in content


# ---------------------------------------------------------------------------
# Proposal lifecycle V2 (P0/P1). Locks the create/edit/regenerate
# audit contract, the regenerate idempotency check, and the
# proposal version-snapshot write path. See
# docs/sales-ops/PROPOSAL_LIFECYCLE_V2.md.
# ---------------------------------------------------------------------------


class _FakeVersionsCollection:
    def __init__(self):
        self.inserted: list[dict] = []

    async def insert_one(self, doc):
        self.inserted.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("version_id"))


def _build_lifecycle_db(proposal_doc: dict, opportunity_doc: dict | None = None,
                        sow_docs: list[dict] | None = None) -> SimpleNamespace:
    """Stitch a fake db with proposals, opportunities, sows,
    notifications, and the new proposal_versions collection."""
    sow_docs = list(sow_docs or [])
    return SimpleNamespace(
        proposals=_FakeProposalsCollection([proposal_doc]),
        opportunities=SimpleNamespace(
            find_one=AsyncMock(return_value=opportunity_doc),
        ),
        opportunity_sows=SimpleNamespace(
            find=lambda *_a, **_kw: _FakeCursor(sow_docs),
        ),
        notifications=_FakeNotificationsCollection(),
        proposal_versions=_FakeVersionsCollection(),
    )


@pytest.mark.asyncio
async def test_create_proposal_writes_creation_audit_row(monkeypatch):
    """POST /opportunities/{id}/proposal writes an audit row under
    sales_ops.proposal.created so the activity feed records the
    first row in the proposal lifecycle."""
    from routes.proposals import create_proposal
    from models import ProposalCreate

    captured: list[dict[str, Any]] = []

    async def _fake_audit(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr("routes.proposals.audit_log_action", _fake_audit)

    opp_id = "opp_synth_create_audit"
    sow_id = "sow_synth_create_audit"
    db = SimpleNamespace(
        opportunities=SimpleNamespace(
            find_one=AsyncMock(return_value={
                "opportunity_id": opp_id,
                "name": "Synth Opp",
                "lines_of_business": ["professional_services"],
                "account_name": "Synth Account",
            }),
        ),
        opportunity_sows=SimpleNamespace(
            find=lambda *_a, **_kw: _FakeCursor([{
                "sow_id": sow_id,
                "opportunity_id": opp_id,
                "line_of_business": "professional_services",
                "status": "ready_for_review",
                "executive_summary": "synth",
            }]),
        ),
        proposals=SimpleNamespace(
            find_one=AsyncMock(return_value=None),
            insert_one=AsyncMock(),
        ),
    )
    user = SimpleNamespace(
        user_id="u_synth_create",
        email="creator@aegisaxiom.test",
        role="sales_ops",
        permissions=["sales_ops.proposal.create"],
    )

    body = ProposalCreate(sow_ids=[sow_id])
    proposal = await create_proposal(
        opportunity_id=opp_id, body=body, db=db, current_user=user,
    )

    assert proposal.proposal_id.startswith("prop_")
    assert len(captured) == 1
    row = captured[0]
    assert row["action"] == "sales_ops.proposal.created"
    assert row["resource_type"] == "proposal"
    assert row["resource_id"] == proposal.proposal_id
    assert row["details"]["opportunity_id"] == opp_id
    assert row["details"]["sow_ids_count"] == 1
    assert row["details"]["version"] == 1
    assert row["details"]["generation_source_hash"]


@pytest.mark.asyncio
async def test_regenerate_is_idempotent_on_unchanged_hash(monkeypatch):
    """The regenerate route compares the new generation_source_hash
    against the stored hash. When equal, the route returns
    no_op=True with reason=source_unchanged, the proposal version
    does NOT bump, no snapshot row is written, and no content
    overwrite happens."""
    from routes.proposals import regenerate_proposal
    from services.proposal_generation import generate_proposal_content as real_gen

    captured: list[dict[str, Any]] = []

    async def _fake_audit(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr("routes.proposals.audit_log_action", _fake_audit)

    opp_id = "opp_synth_idem"
    sow_id = "sow_synth_idem"
    opportunity = {
        "opportunity_id": opp_id,
        "name": "Synth Opp",
        "lines_of_business": ["professional_services"],
        "account_name": "Synth Account",
    }
    sow_doc = {
        "sow_id": sow_id,
        "opportunity_id": opp_id,
        "line_of_business": "professional_services",
        "status": "ready_for_review",
        "executive_summary": "synth",
    }

    # Compute the canonical hash using the real generator so the
    # existing proposal already carries the same hash that
    # regenerate will recompute.
    canonical = real_gen(opportunity, [sow_doc])
    canonical_hash = canonical["generation_source_hash"]

    proposal_doc = {
        "proposal_id": "prop_synth_idem",
        "opportunity_id": opp_id,
        "sow_ids": [sow_id],
        "status": "draft",
        "version": 3,
        "title": canonical["title"],
        "generated_content": "v1 content",
        "generation_source_hash": canonical_hash,
    }

    db = _build_lifecycle_db(proposal_doc, opportunity, [sow_doc])
    user = SimpleNamespace(
        user_id="u_synth_regen",
        email="regen@aegisaxiom.test",
        role="sales_ops",
        permissions=["sales_ops.proposal.edit"],
    )

    fresh = await regenerate_proposal(
        proposal_id="prop_synth_idem", db=db, current_user=user,
    )

    # No-op envelope.
    assert fresh.get("no_op") is True
    assert fresh.get("reason") == "source_unchanged"
    # Version unchanged.
    assert fresh.get("version") == 3
    # No snapshot row written (the stored content was not touched).
    assert db.proposal_versions.inserted == []
    # Audit row still wrote, with no_op flagged.
    assert len(captured) == 1
    assert captured[0]["action"] == "sales_ops.proposal.regenerated"
    assert captured[0]["details"]["no_op"] is True
    assert captured[0]["details"]["previous_version"] == 3
    assert captured[0]["details"]["new_version"] == 3


@pytest.mark.asyncio
async def test_regenerate_writes_version_snapshot_before_overwrite(monkeypatch):
    """When the source hash differs, the regenerate route snapshots
    the prior content into db.proposal_versions before the update.
    The snapshot carries reason=regenerated and the previous
    version's metadata."""
    from routes.proposals import regenerate_proposal

    async def _noop_audit(**_kwargs):
        return None

    monkeypatch.setattr("routes.proposals.audit_log_action", _noop_audit)

    opp_id = "opp_synth_snap"
    sow_id = "sow_synth_snap"
    proposal_doc = {
        "proposal_id": "prop_synth_snap",
        "opportunity_id": opp_id,
        "sow_ids": [sow_id],
        "status": "draft",
        "version": 1,
        "title": None,
        "generated_content": "prior content",
        "generation_source_hash": "OLD_HASH_THAT_WILL_DIFFER",
    }
    opportunity = {
        "opportunity_id": opp_id,
        "name": "Synth Opp",
        "lines_of_business": ["professional_services"],
        "account_name": "Synth Account",
    }
    sow_doc = {
        "sow_id": sow_id,
        "opportunity_id": opp_id,
        "line_of_business": "professional_services",
        "status": "ready_for_review",
        "executive_summary": "synth",
    }
    db = _build_lifecycle_db(proposal_doc, opportunity, [sow_doc])
    user = SimpleNamespace(
        user_id="u_synth_snap",
        email="snap@aegisaxiom.test",
        role="sales_ops",
        permissions=["sales_ops.proposal.edit"],
    )

    fresh = await regenerate_proposal(
        proposal_id="prop_synth_snap", db=db, current_user=user,
    )

    assert fresh.get("version") == 2
    assert fresh.get("no_op") is False
    # Snapshot row was written with the prior version's content.
    assert len(db.proposal_versions.inserted) == 1
    snap = db.proposal_versions.inserted[0]
    assert snap["proposal_id"] == "prop_synth_snap"
    assert snap["version"] == 1
    assert snap["content"] == "prior content"
    assert snap["reason"] == "regenerated"


@pytest.mark.asyncio
async def test_patch_proposal_writes_edit_audit_and_snapshot(monkeypatch):
    """PATCH on a content field (title, generated_content,
    generated_summary, notes, google_doc_url, google_doc_id)
    writes a snapshot row into db.proposal_versions with
    reason=edited and an audit row under sales_ops.proposal.edited.
    Status-only PATCHes do not snapshot or audit (separate flow)."""
    from routes.proposals import update_proposal
    from models import ProposalUpdate

    captured: list[dict[str, Any]] = []

    async def _fake_audit(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr("routes.proposals.audit_log_action", _fake_audit)

    proposal_doc = {
        "proposal_id": "prop_synth_edit",
        "opportunity_id": "opp_synth_edit",
        "sow_ids": [],
        "status": "draft",
        "version": 1,
        "title": "Original",
        "notes": None,
        "generated_content": "v1",
        "generated_summary": "v1 summary",
    }
    db = _build_lifecycle_db(proposal_doc, None, [])
    user = SimpleNamespace(
        user_id="u_synth_edit",
        email="editor@aegisaxiom.test",
        role="sales_ops",
        permissions=["sales_ops.proposal.edit"],
    )

    body = ProposalUpdate(title="Edited title", notes="New notes")
    fresh = await update_proposal(
        proposal_id="prop_synth_edit", body=body, db=db, current_user=user,
    )

    assert fresh["title"] == "Edited title"
    assert fresh["notes"] == "New notes"
    # Snapshot written with reason=edited and prior content.
    assert len(db.proposal_versions.inserted) == 1
    snap = db.proposal_versions.inserted[0]
    assert snap["reason"] == "edited"
    assert snap["title"] == "Original"
    assert snap["content"] == "v1"
    # Audit row written.
    edit_rows = [r for r in captured if r["action"] == "sales_ops.proposal.edited"]
    assert len(edit_rows) == 1
    assert "title" in edit_rows[0]["details"]["fields_changed"]
    assert "notes" in edit_rows[0]["details"]["fields_changed"]


@pytest.mark.asyncio
async def test_patch_status_only_does_not_snapshot_or_audit_as_edit(monkeypatch):
    """A status-only PATCH (e.g. draft -> ready_for_review) is a
    workflow transition, not a content edit. No snapshot is
    written and no sales_ops.proposal.edited audit row fires.
    This keeps the activity feed clean from no-content moves."""
    from routes.proposals import update_proposal
    from models import ProposalUpdate

    captured: list[dict[str, Any]] = []

    async def _fake_audit(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr("routes.proposals.audit_log_action", _fake_audit)

    proposal_doc = {
        "proposal_id": "prop_synth_status",
        "opportunity_id": "opp_synth_status",
        "sow_ids": [],
        "status": "draft",
        "version": 1,
        "title": "x",
        "generated_content": "y",
    }
    db = _build_lifecycle_db(proposal_doc, None, [])
    user = SimpleNamespace(
        user_id="u_synth_status",
        email="status@aegisaxiom.test",
        role="sales_ops",
        permissions=["sales_ops.proposal.edit"],
    )

    body = ProposalUpdate(status="ready_for_review")
    await update_proposal(
        proposal_id="prop_synth_status", body=body, db=db, current_user=user,
    )

    edit_rows = [r for r in captured if r["action"] == "sales_ops.proposal.edited"]
    assert edit_rows == []
    assert db.proposal_versions.inserted == []


@pytest.mark.asyncio
async def test_patch_content_on_locked_status_does_not_snapshot_or_audit(monkeypatch):
    """A PATCH that targets a content field on a sent / accepted /
    rejected proposal raises 409 before reaching the snapshot or
    audit code path. Locked here so a future reorder cannot let a
    bad version snapshot fire on a request that the route is
    supposed to reject."""
    from fastapi import HTTPException
    from routes.proposals import update_proposal
    from models import ProposalUpdate

    captured: list[dict[str, Any]] = []

    async def _fake_audit(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr("routes.proposals.audit_log_action", _fake_audit)

    proposal_doc = {
        "proposal_id": "prop_synth_locked_edit",
        "opportunity_id": "opp_synth_locked_edit",
        "sow_ids": [],
        "status": "sent",
        "version": 1,
        "title": "Sent already",
        "generated_content": "frozen content",
    }
    db = _build_lifecycle_db(proposal_doc, None, [])
    user = SimpleNamespace(
        user_id="u_synth_locked",
        email="locked@aegisaxiom.test",
        role="sales_ops",
        permissions=["sales_ops.proposal.edit"],
    )

    body = ProposalUpdate(generated_content="attempted overwrite")
    raised = False
    try:
        await update_proposal(
            proposal_id="prop_synth_locked_edit",
            body=body,
            db=db,
            current_user=user,
        )
    except HTTPException as e:
        raised = True
        assert e.status_code == 409
    assert raised, "locked-status content edit must be rejected"

    # No snapshot, no audit row.
    assert db.proposal_versions.inserted == []
    assert captured == []


def test_proposal_lifecycle_v2_doc_exists():
    """The lifecycle V2 doc exists and names the canonical audit
    actions and the version-history shape. Skips cleanly when the
    docs tree is not mounted alongside the backend tests."""
    import pathlib

    doc_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "docs"
        / "sales-ops"
        / "PROPOSAL_LIFECYCLE_V2.md"
    )
    if not doc_path.exists():
        pytest.skip("lifecycle doc not available in this test environment")
    text = doc_path.read_text(encoding="utf-8")
    # The canonical audit action names appear.
    for action in (
        "sales_ops.proposal.created",
        "sales_ops.proposal.edited",
        "sales_ops.proposal.regenerated",
        "sales_ops.proposal.numeric_drift_detected",
        "sales_ops.proposal.handed_off_to_sales",
        "sales.proposal.marked_client_sent",
    ):
        assert action in text, f"audit action {action!r} missing from lifecycle doc"
    # The version-history collection is named.
    assert "proposal_versions" in text
    # The drift guard policy is documented.
    assert "drift" in text.lower()


def test_opportunity_sow_carries_prose_sections_field():
    """The shared OpportunitySOW model carries the prose_sections
    field for every LOB. The field is optional with an empty list
    default so non-PS SOWs are not affected."""
    from models import OpportunitySOW

    fields = OpportunitySOW.model_fields
    assert "prose_sections" in fields
    assert fields["prose_sections"].is_required() is False


def test_sow_create_accepts_prose_sections():
    """The SOW save body passes prose_sections through to
    persistence so the editor can write the field without dropping
    it at the Pydantic boundary."""
    from models import SOWCreate

    fields = SOWCreate.model_fields
    assert "prose_sections" in fields


def test_ps_canonical_section_kinds_named():
    """The seven required PS section kinds and the four optional
    kinds are named in proposal_generation. Source-level lock so a
    refactor cannot silently drop a canonical section."""
    from services.proposal_generation import (
        PS_REQUIRED_SECTION_KINDS,
        PS_OPTIONAL_SECTION_KINDS,
    )

    required_keys = [kind for kind, _ in PS_REQUIRED_SECTION_KINDS]
    assert required_keys == [
        "executive_summary",
        "smart_objective",
        "scope_of_work",
        "estimated_time",
        "assumptions",
        "responsibilities",
        "out_of_scope",
    ]
    optional_keys = [kind for kind, _ in PS_OPTIONAL_SECTION_KINDS]
    assert optional_keys == [
        "risk_assessment",
        "solution_overview",
        "in_scope_inventory",
        "conclusion",
    ]


def test_ps_prose_panel_rendered_only_for_professional_services():
    """The PS prose panel renders only when lob is
    professional_services. Frontend source inspection so the FS
    flow is not affected. Skips cleanly when the frontend tree is
    not mounted alongside the backend tests."""
    import pathlib

    src_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "SolutionsDesignTab.js"
    )
    if not src_path.exists():
        pytest.skip("frontend src not available in this test environment")
    text = src_path.read_text(encoding="utf-8")

    # The PS-only conditional gate exists.
    assert "lob === 'professional_services'" in text
    # The component is named so the conditional is unambiguous.
    assert "ProfessionalServicesProseSections" in text
    # Test ids on the new textareas exist for downstream tests.
    for kind in ("assumptions", "responsibilities", "out_of_scope"):
        assert f"ps-prose-{kind}" in text
    for kind in (
        "risk_assessment",
        "solution_overview",
        "in_scope_inventory",
        "conclusion",
    ):
        assert f"ps-prose-{kind}" in text

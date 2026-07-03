"""Tests for the Google Doc artifact path.

Covers the env-var resolver, the URL parser, the marker exceptions,
and the apply path for the structured-error envelope. Real Google
API calls are not made; the integration with Drive/Docs lives in
google_docs_service.py and is exercised by the route-level tests
that synthesize a fake create_sow_google_doc_user.

No real customer / tenant / folder data appears in these fixtures.

See docs/sales-ops/GOOGLE_DOC_ARTIFACT_PATH.md.
"""

import pytest


# ---------------------------------------------------------------------------
# resolve_sow_folder_id / is_sow_folder_configured
# ---------------------------------------------------------------------------


def test_resolver_prefers_simplimeta_when_both_set(monkeypatch):
    """SIMPLIMETA_SOW_FOLDER_ID is the canonical name. When both env
    vars are set, the resolver returns the canonical value."""
    from services.google_docs_service import resolve_sow_folder_id

    monkeypatch.setenv("SIMPLIMETA_SOW_FOLDER_ID", "fakeFolderCanonical")
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "fakeFolderLegacy")
    assert resolve_sow_folder_id() == "fakeFolderCanonical"


def test_resolver_falls_back_to_legacy_when_canonical_unset(monkeypatch):
    """GOOGLE_DRIVE_FOLDER_ID is the backward-compatible alias."""
    from services.google_docs_service import resolve_sow_folder_id

    monkeypatch.delenv("SIMPLIMETA_SOW_FOLDER_ID", raising=False)
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "fakeFolderLegacy")
    assert resolve_sow_folder_id() == "fakeFolderLegacy"


def test_resolver_raises_when_neither_set(monkeypatch):
    """Neither env var set raises the marker exception so routes
    can map it to google_docs_folder_not_configured."""
    from services.google_docs_service import (
        GoogleDocsFolderNotConfigured,
        resolve_sow_folder_id,
    )

    monkeypatch.delenv("SIMPLIMETA_SOW_FOLDER_ID", raising=False)
    monkeypatch.delenv("GOOGLE_DRIVE_FOLDER_ID", raising=False)
    with pytest.raises(GoogleDocsFolderNotConfigured):
        resolve_sow_folder_id()


def test_resolver_treats_empty_string_as_unset(monkeypatch):
    """A deploy that ships an empty value for the canonical var must
    not collapse silently onto the legacy var unless the legacy var
    also carries a non-empty value."""
    from services.google_docs_service import resolve_sow_folder_id

    monkeypatch.setenv("SIMPLIMETA_SOW_FOLDER_ID", "")
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "fakeFolderLegacy")
    assert resolve_sow_folder_id() == "fakeFolderLegacy"


def test_resolver_strips_whitespace(monkeypatch):
    """A value with leading or trailing whitespace is treated as a
    real value once stripped."""
    from services.google_docs_service import resolve_sow_folder_id

    monkeypatch.setenv("SIMPLIMETA_SOW_FOLDER_ID", "   fakeFolder   ")
    assert resolve_sow_folder_id() == "fakeFolder"


def test_is_configured_helper_returns_boolean(monkeypatch):
    """The is_sow_folder_configured helper never returns the value;
    only the configured-yes/no signal."""
    from services.google_docs_service import is_sow_folder_configured

    monkeypatch.delenv("SIMPLIMETA_SOW_FOLDER_ID", raising=False)
    monkeypatch.delenv("GOOGLE_DRIVE_FOLDER_ID", raising=False)
    assert is_sow_folder_configured() is False

    monkeypatch.setenv("SIMPLIMETA_SOW_FOLDER_ID", "fake")
    assert is_sow_folder_configured() is True


# ---------------------------------------------------------------------------
# parse_google_doc_id
# ---------------------------------------------------------------------------


def test_parse_doc_id_from_edit_url():
    from services.google_docs_service import parse_google_doc_id

    assert (
        parse_google_doc_id("https://docs.google.com/document/d/abc123XYZ_-/edit")
        == "abc123XYZ_-"
    )


def test_parse_doc_id_from_trailing_slash_url():
    from services.google_docs_service import parse_google_doc_id

    assert (
        parse_google_doc_id("https://docs.google.com/document/d/abc123XYZ_-/")
        == "abc123XYZ_-"
    )


def test_parse_doc_id_from_query_string_url():
    from services.google_docs_service import parse_google_doc_id

    url = "https://docs.google.com/document/d/abc123XYZ_-?usp=sharing"
    assert parse_google_doc_id(url) == "abc123XYZ_-"


def test_parse_doc_id_rejects_short_id():
    """The id length floor is 8 characters so a malformed URL with
    a tiny id does not silently land in storage."""
    from services.google_docs_service import parse_google_doc_id

    assert parse_google_doc_id("https://docs.google.com/document/d/abc/edit") is None


def test_parse_doc_id_returns_none_for_non_document_path():
    """The parser does not enforce the hostname allowlist; that
    happens at the route layer via _assert_allowed_doc_url. The
    parser does require the canonical /document/d/{ID} path so a
    URL pointing at sheets or any other Drive surface returns
    None."""
    from services.google_docs_service import parse_google_doc_id

    assert parse_google_doc_id("https://docs.google.com/spreadsheets/d/abc12345") is None
    assert parse_google_doc_id("https://docs.google.com/presentation/d/abc12345/edit") is None
    assert parse_google_doc_id("https://docs.google.com/") is None


def test_parse_doc_id_handles_empty_input():
    from services.google_docs_service import parse_google_doc_id

    assert parse_google_doc_id("") is None
    assert parse_google_doc_id(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _raise_for_drive_error
# ---------------------------------------------------------------------------


def test_raise_for_drive_error_maps_403_to_permission_denied():
    """A Drive HttpError with status 403 maps to the canonical
    GoogleDocsPermissionDenied marker. The original exception is
    chained so the catch-all in the route can still log details."""
    from services.google_docs_service import (
        GoogleDocsPermissionDenied,
        _raise_for_drive_error,
    )

    class _FakeResp:
        status = 403

    class _FakeHttpError(Exception):
        resp = _FakeResp()

    with pytest.raises(GoogleDocsPermissionDenied):
        _raise_for_drive_error(_FakeHttpError("permission denied"))


def test_raise_for_drive_error_maps_404_to_folder_not_found():
    from services.google_docs_service import (
        GoogleDocsFolderNotFound,
        _raise_for_drive_error,
    )

    class _FakeResp:
        status = 404

    class _FakeHttpError(Exception):
        resp = _FakeResp()

    with pytest.raises(GoogleDocsFolderNotFound):
        _raise_for_drive_error(_FakeHttpError("not found"))


def test_raise_for_drive_error_returns_silently_for_unknown_status():
    """An exception that does not have a Drive-style resp.status
    attribute is left for the catch-all in the route to map to
    google_docs_export_failed."""
    from services.google_docs_service import _raise_for_drive_error

    _raise_for_drive_error(RuntimeError("connection refused"))


def test_raise_for_drive_error_returns_silently_for_500_status():
    """Drive 500-class responses are not mapped; the route catch-all
    handles them as google_docs_export_failed."""
    from services.google_docs_service import _raise_for_drive_error

    class _FakeResp:
        status = 500

    class _FakeHttpError(Exception):
        resp = _FakeResp()

    _raise_for_drive_error(_FakeHttpError("server error"))


# ---------------------------------------------------------------------------
# Source-level locks for the route envelopes and audits
# ---------------------------------------------------------------------------


def test_fireflies_finalize_emits_folder_not_configured_envelope():
    """The fireflies finalize route returns
    google_docs_folder_not_configured when neither env var is set.
    Source inspection so the envelope contract cannot drift."""
    import inspect
    import routes.fireflies as ff

    source = inspect.getsource(ff.finalize_sow)
    assert '"google_docs_folder_not_configured"' in source
    assert "SIMPLIMETA_SOW_FOLDER_ID" in source


def test_fireflies_finalize_emits_permission_denied_envelope():
    """The fireflies route translates GoogleDocsPermissionDenied
    into the canonical structured error envelope."""
    import inspect
    import routes.fireflies as ff

    source = inspect.getsource(ff.finalize_sow)
    assert '"google_docs_permission_denied"' in source
    assert "GoogleDocsPermissionDenied" in source


def test_fireflies_finalize_emits_folder_not_found_envelope():
    import inspect
    import routes.fireflies as ff

    source = inspect.getsource(ff.finalize_sow)
    assert '"google_docs_folder_not_found"' in source
    assert "GoogleDocsFolderNotFound" in source


def test_fireflies_finalize_duplicate_prevention_locked():
    """The fireflies route returns duplicate_prevented=True when the
    SOW already has a google_doc_url and force is not set."""
    import inspect
    import routes.fireflies as ff

    source = inspect.getsource(ff.finalize_sow)
    assert "duplicate_prevented" in source
    assert "force" in source
    assert '"sales_ops.sow.google_doc_export_noop"' in source


def test_fireflies_finalize_writes_success_audit():
    import inspect
    import routes.fireflies as ff

    source = inspect.getsource(ff.finalize_sow)
    assert '"sales_ops.sow.google_doc_exported"' in source


def test_fireflies_finalize_writes_failure_audit():
    import inspect
    import routes.fireflies as ff

    source = inspect.getsource(ff.finalize_sow)
    assert '"sales_ops.sow.google_doc_export_failed"' in source


def test_orion_finalize_emits_same_canonical_envelopes():
    """The orion route returns the same canonical structured error
    envelope shape as fireflies so the frontend can branch on the
    same code regardless of which finalize route is hit."""
    import inspect
    import routes.orion as orion_routes

    source = inspect.getsource(orion_routes.finalize_sow)
    for code in (
        '"google_docs_folder_not_configured"',
        '"google_docs_permission_denied"',
        '"google_docs_folder_not_found"',
        '"google_docs_not_configured"',
        '"google_docs_export_failed"',
    ):
        assert code in source, f"orion finalize missing envelope {code}"
    assert "duplicate_prevented" in source
    assert '"sales_ops.sow.google_doc_exported"' in source
    assert '"sales_ops.sow.google_doc_export_noop"' in source
    assert '"sales_ops.sow.google_doc_export_failed"' in source


def test_proposal_patch_audits_google_doc_link_and_unlink():
    """The proposal PATCH route writes
    sales_ops.proposal.google_doc_linked when a URL is attached and
    sales_ops.proposal.google_doc_unlinked when the URL is cleared.
    Source inspection so a refactor cannot silently drop the
    audit rows on the escape-hatch path."""
    import inspect
    import routes.proposals as proposals_module

    source = inspect.getsource(proposals_module.update_proposal)
    assert '"sales_ops.proposal.google_doc_linked"' in source
    assert '"sales_ops.proposal.google_doc_unlinked"' in source


def test_proposal_patch_parses_google_doc_id_from_url():
    """The PATCH route auto-parses google_doc_id from the attached
    URL so a downstream reader does not have to re-parse the URL."""
    import inspect
    import routes.proposals as proposals_module

    source = inspect.getsource(proposals_module.update_proposal)
    assert "parse_google_doc_id" in source


@pytest.mark.asyncio
async def test_proposal_patch_auto_stamps_google_doc_id_in_storage(monkeypatch):
    """When the PATCH body carries a google_doc_url with no
    matching google_doc_id, the route parses the id from the URL
    and persists it. Integration test against a fake proposals
    collection so the persisted value is observable."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from routes.proposals import update_proposal
    from models import ProposalUpdate

    captured_updates: list[dict] = []

    class _FakeProposals:
        def __init__(self, doc):
            self._doc = dict(doc)

        async def find_one(self, query, _projection=None):
            return dict(self._doc)

        async def update_one(self, _query, update):
            captured_updates.append(update.get("$set", {}))
            self._doc.update(update.get("$set", {}))
            return SimpleNamespace(modified_count=1)

    class _FakeVersions:
        async def insert_one(self, _doc):
            return SimpleNamespace(inserted_id="v")

    async def _noop_audit(**_kwargs):
        return None

    monkeypatch.setattr("routes.proposals.audit_log_action", _noop_audit)

    proposal_doc = {
        "proposal_id": "prop_synth_link",
        "opportunity_id": "opp_synth_link",
        "sow_ids": [],
        "status": "draft",
        "version": 1,
        "title": "Synth",
        "generated_content": "v1",
        "google_doc_url": None,
        "google_doc_id": None,
    }
    db = SimpleNamespace(
        proposals=_FakeProposals(proposal_doc),
        opportunities=SimpleNamespace(find_one=AsyncMock(return_value=None)),
        proposal_versions=_FakeVersions(),
    )
    user = SimpleNamespace(
        user_id="u_synth",
        email="u@aegisaxiom.test",
        role="sales_ops",
        permissions=["sales_ops.proposal.edit"],
    )

    body = ProposalUpdate(
        google_doc_url="https://docs.google.com/document/d/syntheticDocId12345/edit",
    )
    fresh = await update_proposal(
        proposal_id="prop_synth_link",
        body=body,
        db=db,
        current_user=user,
    )

    # The persisted update payload contains a parsed google_doc_id.
    assert any(
        u.get("google_doc_id") == "syntheticDocId12345"
        for u in captured_updates
    )
    # The returned document reflects the auto-stamped id.
    assert fresh.get("google_doc_id") == "syntheticDocId12345"


@pytest.mark.asyncio
async def test_proposal_patch_does_not_overwrite_explicit_google_doc_id(monkeypatch):
    """When the PATCH body explicitly supplies both google_doc_url
    and google_doc_id, the explicit id wins over the URL-parsed
    fallback. This lets a caller correct a parsed id if needed."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from routes.proposals import update_proposal
    from models import ProposalUpdate

    captured_updates: list[dict] = []

    class _FakeProposals:
        def __init__(self, doc):
            self._doc = dict(doc)

        async def find_one(self, query, _projection=None):
            return dict(self._doc)

        async def update_one(self, _query, update):
            captured_updates.append(update.get("$set", {}))
            self._doc.update(update.get("$set", {}))
            return SimpleNamespace(modified_count=1)

    class _FakeVersions:
        async def insert_one(self, _doc):
            return SimpleNamespace(inserted_id="v")

    async def _noop_audit(**_kwargs):
        return None

    monkeypatch.setattr("routes.proposals.audit_log_action", _noop_audit)

    proposal_doc = {
        "proposal_id": "prop_synth_explicit",
        "opportunity_id": "opp_synth_explicit",
        "sow_ids": [],
        "status": "draft",
        "version": 1,
        "title": "Synth",
        "generated_content": "v1",
        "google_doc_url": None,
        "google_doc_id": None,
    }
    db = SimpleNamespace(
        proposals=_FakeProposals(proposal_doc),
        opportunities=SimpleNamespace(find_one=AsyncMock(return_value=None)),
        proposal_versions=_FakeVersions(),
    )
    user = SimpleNamespace(
        user_id="u_synth",
        email="u@aegisaxiom.test",
        role="sales_ops",
        permissions=["sales_ops.proposal.edit"],
    )

    body = ProposalUpdate(
        google_doc_url="https://docs.google.com/document/d/parsedFromUrl12345/edit",
        google_doc_id="callerSuppliedId",
    )
    await update_proposal(
        proposal_id="prop_synth_explicit",
        body=body,
        db=db,
        current_user=user,
    )

    # The persisted update keeps the caller-supplied id; the
    # URL-parsed id does not overwrite it.
    persisted = captured_updates[0]
    assert persisted.get("google_doc_id") == "callerSuppliedId"


def test_artifact_path_doc_exists():
    """The artifact-path contract is documented so future tickets
    reference one source of truth. Skips cleanly when the docs tree
    is not mounted alongside the backend tests."""
    import pathlib

    doc_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "docs"
        / "sales-ops"
        / "GOOGLE_DOC_ARTIFACT_PATH.md"
    )
    if not doc_path.exists():
        pytest.skip("artifact-path doc not available in this test environment")
    text = doc_path.read_text(encoding="utf-8")
    # Canonical env var named.
    assert "SIMPLIMETA_SOW_FOLDER_ID" in text
    # Legacy alias acknowledged.
    assert "GOOGLE_DRIVE_FOLDER_ID" in text
    # Canonical fields named.
    assert "google_doc_url" in text
    assert "google_doc_id" in text
    # Error codes enumerated.
    for code in (
        "google_docs_not_configured",
        "google_docs_folder_not_configured",
        "google_docs_folder_not_found",
        "google_docs_permission_denied",
        "google_docs_export_failed",
    ):
        assert code in text
    # Audit action names enumerated.
    for action in (
        "sales_ops.sow.google_doc_exported",
        "sales_ops.sow.google_doc_export_noop",
        "sales_ops.proposal.google_doc_linked",
        "sales_ops.proposal.google_doc_unlinked",
    ):
        assert action in text


# ---------------------------------------------------------------------------
# Frontend source inspection
# ---------------------------------------------------------------------------


def test_frontend_routes_google_doc_error_codes_to_actionable_copy():
    """The SOW Builder finalize handler maps the canonical Google
    Doc error codes to actionable user-facing copy rather than the
    generic detail string. Skips cleanly when frontend src is not
    mounted alongside backend tests."""
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
    # The mapping table names every code.
    for code in (
        "google_docs_not_configured",
        "google_docs_folder_not_configured",
        "google_docs_folder_not_found",
        "google_docs_permission_denied",
        "google_docs_export_failed",
    ):
        assert code in text, f"frontend missing copy for {code}"
    # Duplicate-prevented toast distinguishes the no-op.
    assert "Google Doc already exists." in text
    assert "Google Doc exported." in text
    # Linked-Google-Doc framing on the escape hatch.
    assert "Linked Google Doc." in text
    assert "Enter a valid Google Docs URL." in text

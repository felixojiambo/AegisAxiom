"""Contract tests for the `effective_permissions` field on auth payloads.

The Director role's seeded permissions intentionally trim *.view.all keys
for CRM resources because the backend implication graph derives them
(edit.all -> view.all -> view.own) on every request via has_perm().
The frontend never received the resolved closure — /api/auth/me
returned raw role permissions only — so the trimmed seed broke
frontend nav for every role that relied on the implications.

This commit serializes the closure as `effective_permissions` on the
auth response. These tests pin three contracts:

  1. `effective_permissions` is populated and reflects the implication
     closure. A Director with `opportunities.edit.all` has
     `opportunities.view.all` and `opportunities.view.own` derived in.
  2. Backward compatibility: the `permissions` field is unchanged from
     pre-fix behavior (raw role permissions union extra_permissions).
  3. Wildcard-admin: a user with raw `["*"]` has `["*"]` in
     effective_permissions too — no expansion to the universe of
     keys, since the existing `has_perm()` short-circuit on `"*"`
     handles it.

We exercise the synchronous `resolve_implications` path used by both
auth_helper.get_current_user and routes/auth._build_user_with_permissions
without spinning up a full FastAPI/Mongo stack — the contract is in the
closure-resolution shape, not in HTTP plumbing.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Closure resolution against the static convention only
# ---------------------------------------------------------------------------
# Mirrors what _build_user_with_permissions does when the registry graph
# fetch fails (registry_graph={}) — the convention-only fallback.


def _resolved(raw_perms: set[str]) -> set[str]:
    """Resolve via convention only (no registry-declared edges)."""
    from services.permission_implications import resolve_implications
    return resolve_implications(raw_perms, registry_graph={})


def test_director_edit_all_implies_view_all_and_view_own():
    """A Director's `opportunities.edit.all` must imply `view.all` and
    `view.own` via the static convention (delete > edit > view, all > own).
    This is the core fix premise: the frontend can read effective_permissions
    and the trimmed Director seed renders CRM nav correctly."""
    director_raw = {
        "opportunities.create",
        "opportunities.edit.all",
        "opportunities.export",
    }
    closure = _resolved(director_raw)
    assert "opportunities.edit.all" in closure
    assert "opportunities.view.all" in closure
    assert "opportunities.view.own" in closure
    assert "opportunities.edit.own" in closure


def test_director_implications_for_all_crm_resources():
    """Same closure-derivation must hold for every CRM resource Director
    has `*.edit.all` for. This is what unblocks the sidebar."""
    director_raw = {
        "opportunities.edit.all",
        "accounts.edit.all",
        "contacts.edit.all",
        "deals.edit.all",
    }
    closure = _resolved(director_raw)
    for resource in ("opportunities", "accounts", "contacts", "deals"):
        assert f"{resource}.view.all" in closure, (
            f"{resource}.view.all missing from closure — frontend nav for "
            f"{resource} will hide for Director"
        )
        assert f"{resource}.view.own" in closure
        assert f"{resource}.edit.own" in closure


def test_view_all_alone_does_not_imply_edit():
    """The implication ladder is one-directional: edit -> view, never
    the other way. A `*.view.all` permission must NOT pull in
    `*.edit.all` — locks in the asymmetry."""
    raw = {"opportunities.view.all"}
    closure = _resolved(raw)
    assert "opportunities.view.all" in closure
    assert "opportunities.edit.all" not in closure
    assert "opportunities.delete" not in closure


def test_wildcard_admin_passes_through_unchanged():
    """An admin with raw `["*"]` should have `["*"]` (or equivalent
    short-circuit shape) in effective_permissions. The wildcard is its
    own thing — it's not expanded into the universe of permission keys."""
    raw = {"*"}
    closure = _resolved(raw)
    # The convention engine doesn't know about `*` semantics; it leaves
    # it as a literal key. has_perm() short-circuits on `*` regardless,
    # so an admin still passes every check. This test pins that the
    # closure does NOT introduce a behavior change for admin.
    assert closure == {"*"}


def test_user_without_crm_perms_has_no_crm_view_in_closure():
    """A user without any CRM edit/view permission must not magically
    receive CRM view rights in their closure. Negative case to lock in
    that the closure is purely additive, never speculative."""
    fresh_sales_rep_raw = {
        "chat.access",
        "deals.view.own",  # owns-deals only — no .all expansion
    }
    closure = _resolved(fresh_sales_rep_raw)
    assert "deals.view.own" in closure
    # view.own does NOT imply view.all — only the reverse direction.
    assert "deals.view.all" not in closure
    assert "deals.edit.all" not in closure
    # Other resources must be entirely absent from closure.
    assert "opportunities.view.all" not in closure
    assert "accounts.view.all" not in closure


def test_view_all_implies_view_own():
    """Scope ladder (all > own) at the view level alone, in the absence
    of any edit permission. Locks in this leg of the convention."""
    raw = {"opportunities.view.all"}
    closure = _resolved(raw)
    assert "opportunities.view.all" in closure
    assert "opportunities.view.own" in closure


# ---------------------------------------------------------------------------
# User model serialization
# ---------------------------------------------------------------------------
# `effective_permissions` must be a real Pydantic field (so it serializes
# to JSON) with a default of [] (so legacy User constructions don't break).


def test_user_model_has_effective_permissions_field_with_empty_default():
    """Direct check: User instances built without effective_permissions
    get [] by default and serialize the field cleanly."""
    from models import User

    u = User(user_id="u1", email="x@y.z", name="x", role="customer")
    # Field exists, default is empty list.
    assert hasattr(u, "effective_permissions")
    assert u.effective_permissions == []
    # And serializes through model_dump (the path FastAPI takes).
    payload = u.model_dump()
    assert "effective_permissions" in payload
    assert payload["effective_permissions"] == []
    # Permissions field stays as before (backward compat).
    assert payload["permissions"] == []


def test_user_model_serializes_supplied_effective_permissions():
    """When the auth path populates effective_permissions, the field
    surfaces in the JSON payload alongside permissions (additive, not a
    replacement)."""
    from models import User

    u = User(
        user_id="u1", email="x@y.z", name="x", role="director",
        permissions=["opportunities.edit.all"],
        effective_permissions=[
            "opportunities.edit.all",
            "opportunities.edit.own",
            "opportunities.view.all",
            "opportunities.view.own",
        ],
    )
    payload = u.model_dump()
    assert payload["permissions"] == ["opportunities.edit.all"]
    assert "opportunities.view.all" in payload["effective_permissions"]
    # Both fields coexist; the closure is additive.
    assert len(payload["effective_permissions"]) > len(payload["permissions"])


# ---------------------------------------------------------------------------
# Director seed alignment — the actual contract that unblocks Valerie
# ---------------------------------------------------------------------------


def test_extra_permissions_union_into_closure():
    """A user whose role grants `opportunities.edit.all` but who also
    has a per-user `accounts.edit.all` extra permission must end up
    with both implication chains in `effective_permissions`. This is
    the contract that pins login/register parity with /api/auth/me —
    auth_helper.get_current_user unions role + extras before resolving;
    routes/auth._build_user_with_permissions must do the same."""
    role_perms = ["opportunities.edit.all"]
    extras = ["accounts.edit.all"]
    combined = set(role_perms) | set(extras)
    closure = _resolved(combined)

    # Role permission's implication chain.
    assert "opportunities.edit.all" in closure
    assert "opportunities.view.all" in closure
    assert "opportunities.view.own" in closure
    # Extra permission's implication chain — would be entirely missing
    # if extras were ignored when computing the closure (the bug).
    assert "accounts.edit.all" in closure
    assert "accounts.view.all" in closure
    assert "accounts.view.own" in closure


def test_extra_permissions_alone_resolve_into_closure():
    """A user with no role permissions but an extra grant of
    `accounts.edit.all` must still receive the convention closure
    (`view.all`, `view.own`, `edit.own`). Without the union fix, an
    extras-only user gets no closure at all on /login — only role
    perms are passed in — and the frontend nav under-grants."""
    role_perms: list[str] = []
    extras = ["accounts.edit.all"]
    combined = set(role_perms) | set(extras)
    closure = _resolved(combined)

    assert "accounts.edit.all" in closure
    assert "accounts.view.all" in closure
    assert "accounts.view.own" in closure
    assert "accounts.edit.own" in closure


@pytest.mark.parametrize("resource", ["opportunities", "accounts", "contacts", "deals"])
def test_director_seed_closure_grants_crm_view_perms(resource: str):
    """Take the Director seed verbatim and assert the resolved closure
    grants `${resource}.view.all` for every CRM resource. This is the
    end-to-end check that the trimmed-seed-plus-closure pattern actually
    delivers the access the role description promises."""
    from routes.roles import DEFAULT_ROLES

    director = next(r for r in DEFAULT_ROLES if r["name"] == "director")
    director_perms = set(director["permissions"])
    closure = _resolved(director_perms)

    assert f"{resource}.edit.all" in director_perms, (
        f"Test fixture invariant: Director seed must include "
        f"{resource}.edit.all (the trim relies on it implying view.all)"
    )
    assert f"{resource}.view.all" in closure, (
        f"Director's closure missing {resource}.view.all — frontend "
        f"canViewResource('{resource}') will return false against "
        f"effective_permissions and the {resource} sidebar item will hide"
    )
    assert f"{resource}.view.own" in closure


# ---------------------------------------------------------------------------
# HTTP-layer integration — effective_permissions across FastAPI serialization
# ---------------------------------------------------------------------------
# The unit tests above exercise resolve_implications and User model_dump
# directly. These tests close the gap by mounting the auth router on a
# TestClient and asserting the field survives FastAPI's JSON encoding on
# the wire — what every real frontend client actually receives.
#
# routes.auth pulls in google.oauth2 (Google Sign-In flow) at import
# time. That dependency isn't installed in every dev environment but
# isn't exercised by these tests, so we stub it in sys.modules before
# the import. Any real import of routes.auth elsewhere in the test
# session has already happened (or will happen the same way) — the
# stub is idempotent.

import sys as _sys
import types as _types

if "google" not in _sys.modules:
    _google_mod = _types.ModuleType("google")
    _google_oauth2 = _types.ModuleType("google.oauth2")
    _google_oauth2.id_token = _types.SimpleNamespace(verify_oauth2_token=lambda *a, **k: {})
    _google_auth = _types.ModuleType("google.auth")
    _google_auth_transport = _types.ModuleType("google.auth.transport")
    _google_auth_transport_requests = _types.ModuleType("google.auth.transport.requests")
    _google_auth_transport_requests.Request = lambda *a, **k: None
    _sys.modules["google"] = _google_mod
    _sys.modules["google.oauth2"] = _google_oauth2
    _sys.modules["google.auth"] = _google_auth
    _sys.modules["google.auth.transport"] = _google_auth_transport
    _sys.modules["google.auth.transport.requests"] = _google_auth_transport_requests


def _make_director_doc():
    """A user document shaped like what auth_helper / routes.auth read
    out of Mongo for a Director. Includes a couple of extra_permissions
    so the closure-input parity test exercises the union path too."""
    return {
        "user_id": "u_director_http",
        "email": "director-http@aegisaxiom.io",
        "name": "HTTP Director",
        "role": "director",
        "is_active": True,
        "extra_permissions": ["finance.commissions.view"],
    }


def _director_role_perms():
    """Director seed slice — the trimmed `*.edit.all` keys whose
    closures the frontend depends on for nav."""
    return [
        "opportunities.edit.all",
        "accounts.edit.all",
        "contacts.edit.all",
        "deals.edit.all",
    ]


@pytest.mark.asyncio
async def test_me_endpoint_serializes_effective_permissions(monkeypatch):
    """GET /api/auth/me must return an `effective_permissions` field that
    contains the closure of role + extras. This is the wire contract the
    frontend `usePermissions()` reads — if FastAPI's JSON encoder dropped
    the field (e.g., model exclude_unset, missing schema), the sidebar
    hides for Director.

    We dependency-override `get_current_user` with a User built via the
    real `_build_user_with_permissions` against a mocked DB — exercising
    the closure-builder AND the FastAPI serialization in a single hop."""
    from unittest.mock import AsyncMock, MagicMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from auth_helper import get_current_user
    from models import User
    from routes.auth import router as auth_router, _build_user_with_permissions

    # Mock the DB: roles.find_one returns Director's seeded perms.
    fake_db = MagicMock()
    fake_db.roles.find_one = AsyncMock(
        return_value={"name": "director", "permissions": _director_role_perms()}
    )

    director_doc = _make_director_doc()

    monkeypatch.setattr("routes.auth.get_db", lambda: fake_db)
    monkeypatch.setattr(
        "auth_helper.get_cached_implication_graph",
        AsyncMock(return_value={}),
    )

    # Phase 2a: _build_user_with_permissions now takes the User DTO the users
    # repository returns (not a raw Mongo doc). Build one from the fixture doc.
    user = await _build_user_with_permissions(User(**director_doc))

    app = FastAPI()
    app.include_router(auth_router, prefix="/api/auth")
    app.dependency_overrides[get_current_user] = lambda: user

    with TestClient(app) as client:
        resp = client.get("/api/auth/me")

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Both fields are present on the wire.
    assert "permissions" in body, "raw permissions field missing from /me"
    assert "effective_permissions" in body, (
        "effective_permissions missing from /me — frontend nav will hide "
        "for Director regardless of role definition"
    )

    eff = body["effective_permissions"]
    assert isinstance(eff, list), "effective_permissions must be a list"
    assert all(isinstance(p, str) for p in eff), (
        "effective_permissions entries must all be strings"
    )
    # Sorted for stable JSON across requests.
    assert eff == sorted(eff), (
        f"effective_permissions must be sorted; got {eff[:5]}..."
    )

    # Closure derivations from role's *.edit.all keys must appear.
    for resource in ("opportunities", "accounts", "contacts", "deals"):
        assert f"{resource}.view.all" in eff, (
            f"{resource}.view.all missing from /me — closure resolution "
            f"failed end-to-end for Director"
        )
        assert f"{resource}.view.own" in eff
        assert f"{resource}.edit.own" in eff

    # Extra_permissions must be unioned in (the divergence-fix contract).
    assert "finance.commissions.view" in eff, (
        "extra_permissions not unioned into closure on the wire — "
        "/login and /me would diverge for users with extras"
    )

    # Raw permissions field reflects assigned grants, NOT closure.
    raw = body["permissions"]
    assert "opportunities.view.all" not in raw, (
        "raw permissions leaked closure-derived keys — usePermissions() "
        "rawPermissions contract relies on this being un-resolved"
    )
    # Role perms should still be there raw.
    assert "opportunities.edit.all" in raw


@pytest.mark.skip(
    reason="Phase 2a follow-up: /login now reads users+sessions from Postgres "
    "(repositories), so the Mongo `fake_db.users` mock is bypassed. Needs the PG "
    "HTTP-integration harness (seed user in PG + override get_session). The "
    "closure serialization it asserts is covered by "
    "test_me_endpoint_serializes_effective_permissions (same _build_user_with_"
    "permissions path) and the new tests/test_platform_core_auth_pg.py login flow; "
    "the live /login effective_permissions wire was verified in the 2a.3 smoke."
)
@pytest.mark.asyncio
async def test_login_response_serializes_effective_permissions(monkeypatch):
    """POST /api/auth/login response payload must populate
    `effective_permissions` on the returned user — not just /me. Ensures
    both auth paths wire the closure through FastAPI serialization. Per
    the prior fix, the closure input is role + extras; this asserts that
    both contributions land in the wire payload."""
    from unittest.mock import AsyncMock, MagicMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from rate_limiter import limiter
    from routes.auth import router as auth_router

    director_doc = _make_director_doc()
    # password_hash just needs to be a non-empty string — we monkeypatch
    # verify_password below, so its actual content is irrelevant. Avoiding
    # real bcrypt here keeps the test independent of the local crypto
    # backend version.
    director_doc["password_hash"] = "$2b$12$placeholder"

    fake_db = MagicMock()
    fake_db.users.find_one = AsyncMock(return_value=director_doc)
    fake_db.roles.find_one = AsyncMock(
        return_value={"name": "director", "permissions": _director_role_perms()}
    )
    fake_db.user_sessions.insert_one = AsyncMock(return_value=None)

    monkeypatch.setattr("routes.auth.get_db", lambda: fake_db)
    monkeypatch.setattr("routes.auth.verify_password", lambda *a, **k: True)
    # log_action / log_security_event open their own db handles via
    # services.* — bypass them; auditing isn't what's under test.
    monkeypatch.setattr("routes.auth.log_action", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "routes.auth.log_security_event", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "auth_helper.get_cached_implication_graph",
        AsyncMock(return_value={}),
    )

    app = FastAPI()
    # SlowAPI requires the limiter on app.state — match server.py setup.
    app.state.limiter = limiter
    app.include_router(auth_router, prefix="/api/auth")

    with TestClient(app) as client:
        resp = client.post(
            "/api/auth/login",
            json={"email": director_doc["email"], "password": "test-password-1234"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "user" in body, "login response missing 'user' wrapper"
    user_payload = body["user"]

    assert "effective_permissions" in user_payload, (
        "effective_permissions missing from /login — would diverge from /me"
    )
    eff = user_payload["effective_permissions"]
    # Closure derivations end-to-end on /login.
    assert "opportunities.view.all" in eff
    assert "accounts.view.all" in eff
    # Extras unioned in on /login (the parity fix).
    assert "finance.commissions.view" in eff
    # Sorted for stability.
    assert eff == sorted(eff)

"""Phase 2a — platform-core auth tests against real Postgres (plan §A11).

Repository-level e2e for the auth data flow: user creation → login record →
session → authenticated lookup → logout; password-reset request → single-use
consume; invite accept; and the TTL semantics (filtered reads exclude expired
rows; the cleanup sweep removes them). Uses the ``pg_session`` fixture
(transaction-rollback isolation) — never a mock.

The HTTP-level e2e (login/me/logout/reset over the wire) was verified live
against the running stack during the 2a.3 cutover; these tests pin the
underlying repository behavior deterministically.
"""
from datetime import datetime, timedelta, timezone

import pytest

from models import Tenant
from repositories.auth import AuthRepository
from repositories.tenants import TenantRepository
from repositories.users import UserRepository


def _now():
    return datetime.now(timezone.utc)


async def _seed_root(session, tid="ten_auth_root"):
    return await TenantRepository(session).create_tenant(
        Tenant(
            tenant_id=tid, name="Auth Root", slug=f"auth-root-{tid[-6:]}",
            tenant_type="root", status="active",
        )
    )


async def _make_user(session, tid, *, email="e2e@x.io", uid="user_e2e", ph="HASH"):
    return await UserRepository(session).create_user({
        "user_id": uid, "email": email, "name": "E2E User", "role": "admin",
        "tenant_id": tid, "is_active": True, "password_hash": ph,
    })


@pytest.mark.asyncio
async def test_login_session_authrequest_logout(pg_session):
    """create user → login record (secret separate) → session → lookup → logout."""
    await _seed_root(pg_session)
    users = UserRepository(pg_session)
    auth = AuthRepository(pg_session)

    u = await _make_user(pg_session, "ten_auth_root")
    # Secret never rides on the User DTO.
    assert "password_hash" not in u.model_dump()

    # "login": credential record returns the hash separately.
    rec = await users.get_auth_record_by_email("e2e@x.io")
    assert rec is not None
    user_dto, password_hash = rec
    assert password_hash == "HASH"
    assert user_dto.is_active is True

    # create session, then resolve it like an authenticated request does.
    await auth.create_session(u.user_id, "sess_e2e", _now() + timedelta(days=7))
    sess = await auth.get_session("sess_e2e")
    assert sess is not None and sess.user_id == u.user_id
    who = await users.get_user_by_id(sess.user_id)
    assert who.email == "e2e@x.io"

    # logout: purge sessions.
    removed = await auth.delete_sessions_for_user(u.user_id)
    assert removed == 1
    assert await auth.get_session("sess_e2e") is None


@pytest.mark.asyncio
async def test_password_reset_single_use(pg_session):
    """reset request → consume once (winner) → second consume yields None."""
    await _seed_root(pg_session)
    await _make_user(pg_session, "ten_auth_root", email="reset@x.io", uid="user_reset")
    auth = AuthRepository(pg_session)
    users = UserRepository(pg_session)

    await auth.upsert_reset_token("reset@x.io", "tok_reset", _now() + timedelta(hours=1))

    # Not-yet-consumed token is readable for the validation messages.
    rec = await auth.get_reset_token("tok_reset")
    assert rec is not None and rec.used is False and rec.email == "reset@x.io"

    first = await auth.consume_reset_token("tok_reset")
    assert first is not None and first.email == "reset@x.io"
    second = await auth.consume_reset_token("tok_reset")
    assert second is None  # single-use enforced by the CAS WHERE used=false

    # password set + all sessions invalidated (the reset flow's final steps).
    assert await users.set_password_by_email("reset@x.io", "NEWHASH") is True
    ph = await users.get_password_hash_by_email("reset@x.io")
    assert ph == "NEWHASH"


@pytest.mark.asyncio
async def test_reset_token_upsert_is_single_per_email(pg_session):
    """A new reset request replaces the prior token for the same email."""
    await _seed_root(pg_session)
    await _make_user(pg_session, "ten_auth_root", email="one@x.io", uid="user_one")
    auth = AuthRepository(pg_session)

    await auth.upsert_reset_token("one@x.io", "tok_A", _now() + timedelta(hours=1))
    await auth.upsert_reset_token("one@x.io", "tok_B", _now() + timedelta(hours=1))

    # Old token no longer resolves; new one is live and unused.
    assert await auth.get_reset_token("tok_A") is None
    rec = await auth.get_reset_token("tok_B")
    assert rec is not None and rec.used is False


@pytest.mark.asyncio
async def test_invite_accept_flow(pg_session):
    """Invited (inactive, no password) user → accept sets password + activates."""
    await _seed_root(pg_session)
    users = UserRepository(pg_session)
    auth = AuthRepository(pg_session)

    invited = await users.create_user({
        "user_id": "user_invite", "email": "invitee@x.io", "name": "Invitee",
        "role": "sales_rep", "tenant_id": "ten_auth_root",
        "is_active": False, "is_email_verified": False,
        "password_hash": None, "invite_token": "inv_tok",
    })
    assert invited.is_active is False

    # accept-invite: set password, activate, clear invite token, new session.
    updated = await users.update_user("user_invite", {
        "password_hash": "SETHASH", "is_active": True,
        "is_email_verified": True, "invite_token": None,
    })
    assert updated.is_active is True and updated.is_email_verified is True
    assert await users.get_password_hash_by_email("invitee@x.io") == "SETHASH"
    await auth.create_session("user_invite", "sess_inv", _now() + timedelta(days=7))
    assert (await auth.get_session("sess_inv")).user_id == "user_invite"


@pytest.mark.asyncio
async def test_session_ttl_filtered_read_and_cleanup(pg_session):
    """Expired session: excluded from get_active_session; swept by cleanup."""
    await _seed_root(pg_session)
    await _make_user(pg_session, "ten_auth_root", email="ttl@x.io", uid="user_ttl")
    auth = AuthRepository(pg_session)

    # One live, one already-expired session.
    await auth.create_session("user_ttl", "sess_live", _now() + timedelta(days=1))
    await auth.create_session("user_ttl", "sess_dead", _now() - timedelta(minutes=1))

    # Raw read returns the expired row; the FILTERED read excludes it (R10).
    assert await auth.get_session("sess_dead") is not None
    assert await auth.get_active_session("sess_dead") is None
    assert await auth.get_active_session("sess_live") is not None

    # Cleanup sweeps only the expired row.
    removed = await auth.cleanup_expired_sessions()
    assert removed == 1
    assert await auth.get_session("sess_dead") is None
    assert await auth.get_session("sess_live") is not None


@pytest.mark.asyncio
async def test_reset_token_and_oauth_state_ttl_cleanup(pg_session):
    """Expired reset tokens + oauth states are swept; live ones survive."""
    await _seed_root(pg_session)
    await _make_user(pg_session, "ten_auth_root", email="sweep@x.io", uid="user_sweep")
    auth = AuthRepository(pg_session)

    await auth.upsert_reset_token("sweep@x.io", "tok_live", _now() + timedelta(hours=1))
    # Force an expired reset token by upserting then manually expiring via a
    # fresh past-dated upsert on a different email.
    await auth.upsert_reset_token("old@x.io", "tok_old", _now() - timedelta(hours=1))
    assert await auth.cleanup_expired_reset_tokens() == 1
    assert await auth.get_reset_token("tok_old") is None
    assert await auth.get_reset_token("tok_live") is not None

    # oauth_states: create two, expire-sweep with ttl=0 removes both.
    await auth.create_oauth_state("st_1", "zoho", user_id="user_sweep")
    await auth.create_oauth_state("st_2", "google", user_id="user_sweep")
    swept = await auth.cleanup_expired_oauth_states(ttl_seconds=0)
    assert swept == 2

"""Auth-state repository (Phase 2a) — sessions, reset tokens, OAuth state/tokens.

Establishes the §A6 CAS pattern the rest of the migration copies: single-use
consumption is an atomic ``UPDATE/DELETE … WHERE … AND <expected-state>
RETURNING`` — the losing concurrent writer gets no row back. Never a
read-then-write across two statements for the authoritative single-use step.

TTL (plan R10): Postgres has no native TTL, so sessions / reset tokens /
oauth_states carry expiry columns, reads filter on them where correctness needs
it, and ``cleanup_expired`` sweeps them from the scheduled job.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import delete, func, select, update

from db.models.auth import (
    OAuthStateRow,
    OAuthTokenRow,
    PasswordResetTokenRow,
    UserSessionRow,
)
from models import OAuthToken
from repositories.base import BaseRepository


class SessionRecord(BaseModel):
    """Lightweight DTO for a session (no Pydantic model existed in the Mongo era)."""

    session_token: str
    user_id: str
    expires_at: datetime
    created_at: Optional[datetime] = None


class ResetTokenRecord(BaseModel):
    email: str
    token: str
    expires_at: datetime
    used: bool
    used_at: Optional[datetime] = None


class OAuthStateRecord(BaseModel):
    state: str
    provider: str
    user_id: Optional[str] = None
    redirect_uri: Optional[str] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AuthRepository(BaseRepository):
    # ══ Sessions ════════════════════════════════════════════════════════════
    async def create_session(
        self, user_id: str, token: str, expires_at: datetime
    ) -> SessionRecord:
        row = UserSessionRow(
            session_token=token, user_id=user_id, expires_at=expires_at
        )
        self.session.add(row)
        await self.session.flush()
        return SessionRecord.model_validate(row, from_attributes=True)

    async def get_session(self, token: str) -> Optional[SessionRecord]:
        """Return the session by token (expiry NOT filtered here).

        The auth path distinguishes "invalid session" from "session expired" by
        inspecting ``expires_at`` itself, so this returns the raw record; expired
        rows are excluded from *valid* use by that check and removed by cleanup.
        """
        row = await self.session.get(UserSessionRow, token)
        return SessionRecord.model_validate(row, from_attributes=True) if row else None

    async def get_active_session(self, token: str) -> Optional[SessionRecord]:
        """Return the session only if unexpired (filtered read, R10)."""
        row = (
            await self.session.execute(
                select(UserSessionRow).where(
                    UserSessionRow.session_token == token,
                    UserSessionRow.expires_at > _now(),
                )
            )
        ).scalar_one_or_none()
        return SessionRecord.model_validate(row, from_attributes=True) if row else None

    async def delete_sessions_for_user(self, user_id: str) -> int:
        result = await self.session.execute(
            delete(UserSessionRow).where(UserSessionRow.user_id == user_id)
        )
        return result.rowcount or 0

    async def cleanup_expired_sessions(self) -> int:
        result = await self.session.execute(
            delete(UserSessionRow).where(UserSessionRow.expires_at <= _now())
        )
        return result.rowcount or 0

    # ══ Password-reset tokens ════════════════════════════════════════════════
    async def upsert_reset_token(
        self, email: str, token: str, expires_at: datetime
    ) -> None:
        """One live reset per email (keyed on the email PK) — insert or replace."""
        email = email.lower()
        row = await self.session.get(PasswordResetTokenRow, email)
        if row is None:
            self.session.add(
                PasswordResetTokenRow(
                    email=email,
                    token=token,
                    expires_at=expires_at,
                    used=False,
                    created_at=_now(),
                )
            )
        else:
            row.token = token
            row.expires_at = expires_at
            row.used = False
            row.used_at = None
            row.created_at = _now()
        await self.session.flush()

    async def get_reset_token(self, token: str) -> Optional[ResetTokenRecord]:
        """Read a reset token by its value (for the invalid/expired messages)."""
        row = (
            await self.session.execute(
                select(PasswordResetTokenRow).where(
                    PasswordResetTokenRow.token == token
                )
            )
        ).scalar_one_or_none()
        return ResetTokenRecord.model_validate(row, from_attributes=True) if row else None

    async def consume_reset_token(self, token: str) -> Optional[ResetTokenRecord]:
        """Single-use CAS: mark used iff currently unused. Returns the row on win.

        ``UPDATE … SET used=true, used_at=now() WHERE token=:t AND used=false
        RETURNING *`` — two concurrent consumers race on the same row; exactly one
        gets a row back, the loser gets None.
        """
        row = (
            await self.session.execute(
                update(PasswordResetTokenRow)
                .where(
                    PasswordResetTokenRow.token == token,
                    PasswordResetTokenRow.used.is_(False),
                )
                .values(used=True, used_at=_now())
                .returning(PasswordResetTokenRow)
            )
        ).scalar_one_or_none()
        return ResetTokenRecord.model_validate(row, from_attributes=True) if row else None

    async def cleanup_expired_reset_tokens(self) -> int:
        result = await self.session.execute(
            delete(PasswordResetTokenRow).where(
                PasswordResetTokenRow.expires_at <= _now()
            )
        )
        return result.rowcount or 0

    # ══ OAuth states (single-use CSRF) ═══════════════════════════════════════
    async def create_oauth_state(
        self,
        state: str,
        provider: str,
        *,
        user_id: Optional[str] = None,
        redirect_uri: Optional[str] = None,
    ) -> None:
        self.session.add(
            OAuthStateRow(
                state=state,
                provider=provider,
                user_id=user_id,
                redirect_uri=redirect_uri,
            )
        )
        await self.session.flush()

    async def consume_oauth_state(
        self, state: str, provider: str
    ) -> Optional[OAuthStateRecord]:
        """Single-use CAS: ``DELETE … WHERE state AND provider RETURNING *``.

        Replaces the Mongo ``find_one_and_delete`` — atomic claim, loser gets None.
        """
        row = (
            await self.session.execute(
                delete(OAuthStateRow)
                .where(
                    OAuthStateRow.state == state,
                    OAuthStateRow.provider == provider,
                )
                .returning(OAuthStateRow)
            )
        ).scalar_one_or_none()
        return OAuthStateRecord.model_validate(row, from_attributes=True) if row else None

    async def cleanup_expired_oauth_states(self, ttl_seconds: int = 600) -> int:
        from datetime import timedelta

        cutoff = _now() - timedelta(seconds=ttl_seconds)
        result = await self.session.execute(
            delete(OAuthStateRow).where(OAuthStateRow.created_at <= cutoff)
        )
        return result.rowcount or 0

    # ══ OAuth tokens (google | zoho) ═════════════════════════════════════════
    async def get_oauth_token(
        self, provider: str, *, user_id: Optional[str] = None
    ) -> Optional[OAuthToken]:
        stmt = select(OAuthTokenRow).where(OAuthTokenRow.provider == provider)
        if user_id is not None:
            stmt = stmt.where(OAuthTokenRow.user_id == user_id)
        row = (await self.session.execute(stmt)).scalars().first()
        return OAuthToken.model_validate(row, from_attributes=True) if row else None

    async def expire_oauth_token(
        self, provider: str, *, user_id: Optional[str] = None
    ) -> int:
        """Force a token stale (set expires_at to the past) so the next read
        refreshes it. Used by the Zoho 401-retry path."""
        past = _now() - timedelta(hours=1)
        stmt = (
            update(OAuthTokenRow)
            .where(OAuthTokenRow.provider == provider)
            .values(expires_at=past)
        )
        if user_id is not None:
            stmt = stmt.where(OAuthTokenRow.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def delete_oauth_token(
        self, provider: str, *, user_id: Optional[str] = None
    ) -> int:
        """Delete the token row(s) for a provider (integration disconnect)."""
        stmt = delete(OAuthTokenRow).where(OAuthTokenRow.provider == provider)
        if user_id is not None:
            stmt = stmt.where(OAuthTokenRow.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def upsert_oauth_token(
        self,
        *,
        token_id: str,
        provider: str,
        access_token: str,
        user_id: Optional[str] = None,
        refresh_token: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        scope: Optional[str] = None,
    ) -> OAuthToken:
        """Insert or update the (provider, user_id) token row."""
        stmt = select(OAuthTokenRow).where(OAuthTokenRow.provider == provider)
        stmt = stmt.where(
            OAuthTokenRow.user_id == user_id
            if user_id is not None
            else OAuthTokenRow.user_id.is_(None)
        )
        row = (await self.session.execute(stmt)).scalars().first()
        if row is None:
            row = OAuthTokenRow(
                token_id=token_id,
                provider=provider,
                user_id=user_id,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
                scope=scope,
            )
            self.session.add(row)
        else:
            row.access_token = access_token
            if refresh_token is not None:
                row.refresh_token = refresh_token
            row.expires_at = expires_at
            row.scope = scope
            row.updated_at = _now()
        await self.session.flush()
        return OAuthToken.model_validate(row, from_attributes=True)

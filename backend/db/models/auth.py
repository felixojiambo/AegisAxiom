"""Auth-state ORM models (Phase 2a): sessions, reset tokens, OAuth state/tokens.

All four are derived from live document usage (census §1.2–§1.5). Postgres has
no native TTL index, so ``user_sessions`` / ``password_reset_tokens`` /
``oauth_states`` carry explicit expiry columns; reads filter on them and a
scheduled cleanup job (plan R10, gated by ``DISABLE_BACKGROUND_JOBS``) sweeps
expired rows.

QBO OAuth rows never exist (Phase 1 deleted QBO) — ``oauth_tokens`` holds only
``google``/``zoho`` providers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class UserSessionRow(Base):
    """An opaque session token (``sess_…``) with an absolute expiry."""

    __tablename__ = "user_sessions"

    session_token: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PasswordResetTokenRow(Base):
    """A single live password-reset token per email (upserted by email).

    ``email`` is the PK because the live code upserts keyed on email
    (``auth.py:471`` ``update_one({email}, upsert=True)``) — one active reset per
    address. ``token`` is uniquely indexed for the by-token consume lookup, which
    is a single-use CAS (``… WHERE token=:t AND used=false RETURNING``).
    """

    __tablename__ = "password_reset_tokens"

    email: Mapped[str] = mapped_column(Text, primary_key=True)
    token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OAuthStateRow(Base):
    """Short-lived CSRF state for an OAuth authorize round-trip (Zoho).

    Consumed single-use via ``DELETE … WHERE state=:s AND provider=:p
    RETURNING`` (was ``find_one_and_delete``). Expiry sweep on ``created_at``
    (600s in Mongo).
    """

    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(Text, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    redirect_uri: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class OAuthTokenRow(Base):
    """A stored OAuth token for an integration provider (google | zoho).

    Unique on ``(provider, user_id)`` — matching the live sparse-unique index
    (``server.py:719``). Never holds ``provider='qbo'`` (Phase 1).
    """

    __tablename__ = "oauth_tokens"
    __table_args__ = (
        UniqueConstraint("provider", "user_id", name="uq_oauth_tokens_provider_user"),
    )

    token_id: Mapped[str] = mapped_column(Text, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scope: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

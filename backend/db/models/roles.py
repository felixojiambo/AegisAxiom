"""Roles ORM model (Phase 2b).

Derived from ``models.py:Role`` plus ``app_id`` — written by the ingestion RBAC
generator on roles it creates but absent from the Pydantic model (census
re-verification 2026-11-25). ``permissions`` is an opaque key list read/written
whole → JSONB, never a child table (CONVENTIONS §4); it is swept by membership
(strip/deactivate a key), hence the GIN index in ``0005_platform_core_rbac``.

Users link to a role by role NAME (``users.role``), unchanged from Mongo — there
is no FK from users to roles.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class RoleRow(Base):
    """A named permission bundle assigned to users by ``name``."""

    __tablename__ = "roles"

    role_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    color: Mapped[str] = mapped_column(Text, nullable=False, server_default="#64748B")
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    permissions: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    app_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

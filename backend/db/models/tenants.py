"""Tenants ORM model (Phase 2a).

Derived from ``models.py:Tenant`` and the live ``db.tenants`` document usage
(census §1.6 / §4). The string ``tenant_id`` stays the primary key — it is the
public API contract every other domain FKs to.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class TenantRow(Base):
    """A tenant in the AEGISAXIOM hierarchy (root / partner / account)."""

    __tablename__ = "tenants"

    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    tenant_type: Mapped[str] = mapped_column(Text, nullable=False)  # root|partner|account
    # Self-referential FK: partner/account tenants point at a parent tenant.
    parent_tenant_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("tenants.tenant_id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="active"
    )  # active|suspended|archived
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

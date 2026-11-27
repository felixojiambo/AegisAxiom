"""Permissions ORM model (Phase 2b).

The persisted permission registry. No Pydantic model existed in the Mongo era;
the shape is derived from ``services/permission_registry.py`` upserts and the
ingestion registration paths. ``key`` is the natural primary key (every upsert
is keyed on it). ``implies`` holds the DAG edges walked by ``validate_dag`` —
JSONB, read whole to build the implication graph; NULL means the declaration
carried no ``implies`` (the seeder only writes it when present).

The in-memory ``@register_module_permissions`` decorator registry is
datastore-agnostic and is NOT represented here (census §0.3).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class PermissionRow(Base):
    """One permission key in the registry."""

    __tablename__ = "permissions"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    group: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    label: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # system | ingestion | custom | manual_override | reconcile_auto
    source: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    app_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", index=True
    )
    implies: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

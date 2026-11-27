"""Policies + policy_targets ORM models (Phase 2b).

``PolicyRow`` is derived from ``models.py:Policy``; its sub-documents
(``trigger``/``conditions``/``actions``) are opaque blobs read and written whole
→ JSONB (§A3).

``PolicyTargetRow`` is the ingested-app target registry, transcribed as found
(operator ruling 2026-11-25): keyed on (app_id, module, entity, event). It is
NOT a policy→target join — there is no ``policy_id`` and no FK to ``policies``.
``app_id`` holds the app SLUG, not the app's ``app_id``; that convention is
load-bearing between the ingestion writer and the reconcile reader.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class PolicyRow(Base):
    """A business-rule policy evaluated by ``services/policy_engine.py``."""

    __tablename__ = "policies"

    policy_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    module: Mapped[str] = mapped_column(Text, nullable=False)
    entity: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    trigger: Mapped[dict] = mapped_column(JSONB, nullable=False)
    condition_logic: Mapped[str] = mapped_column(Text, nullable=False, server_default="AND")
    condition_pattern: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    conditions: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    actions: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    created_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PolicyTargetRow(Base):
    """One (app, module, entity, event) target an ingested app exposes to policies."""

    __tablename__ = "policy_targets"

    app_id: Mapped[str] = mapped_column(Text, primary_key=True)  # app SLUG
    module: Mapped[str] = mapped_column(Text, primary_key=True)
    entity: Mapped[str] = mapped_column(Text, primary_key=True)
    event: Mapped[str] = mapped_column(Text, primary_key=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

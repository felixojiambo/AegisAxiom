"""Audit log ORM model (Phase 2b).

The unified audit log: security events (``services/security_logger.py``),
activity rows (``services/audit_service.py``), AI-tool actions
(``conversational_tools``) and ingestion stage events all land here. No Pydantic
model existed in the Mongo era; the columns are the union of every writer's
document shape (census re-verification 2026-11-25).

``audit_id`` is the TEXT primary key (operator ruling; every writer generates
one, and ``log_ingested_crud`` relies on it for idempotent retries).
``event_category`` is nullable because pre-unification rows lack it and the
admin UI maps "missing" to "activity". Hot WRITE path — see the index set in
``0005_platform_core_rbac``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class AuditLogRow(Base):
    """One audit event."""

    __tablename__ = "audit_logs"

    audit_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_category: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    event_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    severity: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    actor_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    actor_user_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    actor_email: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    actor_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tool_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resource_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resource_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    outcome: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_status: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    session_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tier: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    params: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

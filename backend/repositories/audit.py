"""Audit-log repository (Phase 2b) — the one data-access path for ``audit_logs``.

Hot WRITE path: ``record`` is a single ``INSERT … ON CONFLICT (audit_id) DO
NOTHING`` (the ``log_ingested_crud`` deterministic-id retry collapses to a
no-op in one round-trip instead of find-then-insert).

Response shape. The Mongo documents had a writer-dependent key set, and the
admin/audit and recent-changes endpoints returned them raw. ``AuditLogRecord.
to_api_dict()`` reproduces that set:
  * ``_ALWAYS`` — written (possibly as null) by every writer → always emitted;
  * ``_AI_ACTION`` — written (possibly as null) by the conversational-tools
    writer → always emitted on ``event_category == "ai_action"`` rows;
  * everything else — emitted only when non-null (the writers that set them
    conditionally, e.g. ``tenant_id``; legacy rows without ``event_category``).

JSONB payloads (``details``, ``params``) are made JSON-safe on the way in
(datetimes → ISO strings), matching what the API already emitted.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterable, Optional

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models.audit import AuditLogRow
from repositories.base import BaseRepository

logger = logging.getLogger(__name__)

_COLUMNS = (
    "audit_id", "event_category", "event_type", "severity", "actor_id", "actor_user_id",
    "actor_email", "actor_type", "tool_name", "tenant_id", "resource_type", "resource_id",
    "action", "outcome", "result_status", "error_detail", "session_id", "tier", "params",
    "details", "timestamp",
)
_ALWAYS = frozenset(
    {"audit_id", "action", "actor_id", "actor_email", "resource_type", "resource_id", "details", "timestamp"}
)
_AI_ACTION = frozenset(
    {"actor_user_id", "actor_type", "tool_name", "tier", "params", "result_status", "error_detail", "session_id"}
)


class AuditLogRecord(BaseModel):
    audit_id: str
    event_category: Optional[str] = None
    event_type: Optional[str] = None
    severity: Optional[str] = None
    actor_id: Optional[str] = None
    actor_user_id: Optional[str] = None
    actor_email: Optional[str] = None
    actor_type: Optional[str] = None
    tool_name: Optional[str] = None
    tenant_id: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    action: Optional[str] = None
    outcome: Optional[str] = None
    result_status: Optional[str] = None
    error_detail: Optional[str] = None
    session_id: Optional[str] = None
    tier: Optional[int] = None
    params: Optional[dict] = None
    details: dict = {}
    timestamp: datetime

    def to_api_dict(self) -> dict:
        doc = self.model_dump()
        keep = _ALWAYS | (_AI_ACTION if doc.get("event_category") == "ai_action" else frozenset())
        return {k: v for k, v in doc.items() if k in keep or v is not None}


def _to_record(row: AuditLogRow) -> AuditLogRecord:
    return AuditLogRecord.model_validate(row, from_attributes=True)


class AuditFilter(BaseModel):
    """The admin audit-list filter (routes/audit.py). All conjunctive."""

    action: Optional[str] = None
    resource_type: Optional[str] = None  # trailing "." = module prefix match
    actor_id: Optional[str] = None
    event_category: Optional[str] = None  # "activity" also matches legacy NULL
    event_type: Optional[str] = None
    severity: Optional[str] = None
    outcome: Optional[str] = None


def _like_prefix(prefix: str) -> str:
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _apply_filter(stmt: Select, f: AuditFilter) -> Select:
    if f.action:
        stmt = stmt.where(AuditLogRow.action == f.action)
    if f.resource_type:
        if f.resource_type.endswith("."):
            stmt = stmt.where(AuditLogRow.resource_type.like(_like_prefix(f.resource_type), escape="\\"))
        else:
            stmt = stmt.where(AuditLogRow.resource_type == f.resource_type)
    if f.actor_id:
        stmt = stmt.where(AuditLogRow.actor_id == f.actor_id)
    if f.event_category:
        if f.event_category == "activity":
            stmt = stmt.where(
                or_(AuditLogRow.event_category == "activity", AuditLogRow.event_category.is_(None))
            )
        else:
            stmt = stmt.where(AuditLogRow.event_category == f.event_category)
    if f.event_type:
        stmt = stmt.where(AuditLogRow.event_type == f.event_type)
    if f.severity:
        stmt = stmt.where(AuditLogRow.severity == f.severity)
    if f.outcome:
        stmt = stmt.where(AuditLogRow.outcome == f.outcome)
    return stmt


class AuditRepository(BaseRepository):
    # ── write (hot path) ───────────────────────────────────────────────────
    async def record(self, entry: dict[str, Any]) -> bool:
        """Insert one audit event. True iff inserted (False = ``audit_id`` existed).

        Keys outside the table are dropped with a warning — they would have
        been silently stored in Mongo; surfacing them catches writer drift.
        """
        unknown = set(entry) - set(_COLUMNS)
        if unknown:
            logger.warning("audit_logs: dropping unknown entry keys %s", sorted(unknown))
        values = {k: entry[k] for k in _COLUMNS if k in entry}
        values["details"] = jsonable_encoder(values.get("details") or {})
        if values.get("params") is not None:
            values["params"] = jsonable_encoder(values["params"])
        stmt = (
            pg_insert(AuditLogRow)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[AuditLogRow.audit_id])
            .returning(AuditLogRow.audit_id)
        )
        return (await self.session.execute(stmt)).first() is not None

    async def exists(self, audit_id: str) -> bool:
        return (
            await self.session.execute(select(AuditLogRow.audit_id).where(AuditLogRow.audit_id == audit_id))
        ).first() is not None

    # ── admin list ─────────────────────────────────────────────────────────
    async def list_logs(self, f: AuditFilter, *, skip: int = 0, limit: int = 50) -> list[AuditLogRecord]:
        stmt = _apply_filter(select(AuditLogRow), f)
        stmt = stmt.order_by(AuditLogRow.timestamp.desc(), AuditLogRow.audit_id).offset(skip).limit(limit)
        return [_to_record(r) for r in (await self.session.execute(stmt)).scalars().all()]

    async def count_logs(self, f: AuditFilter) -> int:
        stmt = _apply_filter(select(func.count()).select_from(AuditLogRow), f)
        return (await self.session.execute(stmt)).scalar_one()

    # ── distinct() → SELECT DISTINCT ───────────────────────────────────────
    async def distinct_resource_types(self) -> list[str]:
        """``distinct("resource_type")`` (non-null values), sorted."""
        rows = await self.session.execute(
            select(AuditLogRow.resource_type)
            .where(AuditLogRow.resource_type.is_not(None))
            .distinct()
            .order_by(AuditLogRow.resource_type)
        )
        return [r[0] for r in rows]

    async def distinct_event_types(self, resource_type: str) -> list[str]:
        """``distinct("event_type", {"resource_type": rt})`` (non-null), sorted."""
        rows = await self.session.execute(
            select(AuditLogRow.event_type)
            .where(AuditLogRow.resource_type == resource_type, AuditLogRow.event_type.is_not(None))
            .distinct()
            .order_by(AuditLogRow.event_type)
        )
        return [r[0] for r in rows]

    # ── recent-changes feed (routes/projects_recent_changes) ───────────────
    async def list_recent_activity(
        self,
        *,
        resource_types: Iterable[str],
        since: datetime,
        until: datetime,
        tenant_id: str,
        actor_user_id: Optional[str] = None,
        project_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[AuditLogRecord]:
        """Human + AI activity rows for one tenant in a window, newest first.

        Tenant match: top-level ``tenant_id`` or ``details.after/before.tenant_id``
        (pre-1E.3c rows). Category: activity, ai_action, or legacy NULL.
        """
        resource_types = list(resource_types)
        if not resource_types:
            return []
        d = AuditLogRow.details
        clauses = [
            AuditLogRow.resource_type.in_(resource_types),
            AuditLogRow.timestamp >= since,
            AuditLogRow.timestamp <= until,
            or_(
                AuditLogRow.tenant_id == tenant_id,
                d["after"]["tenant_id"].astext == tenant_id,
                d["before"]["tenant_id"].astext == tenant_id,
            ),
            or_(
                AuditLogRow.event_category.in_(("activity", "ai_action")),
                AuditLogRow.event_category.is_(None),
            ),
        ]
        if actor_user_id:
            clauses.append(
                or_(AuditLogRow.actor_id == actor_user_id, AuditLogRow.actor_user_id == actor_user_id)
            )
        if project_id:
            clauses.append(
                or_(
                    and_(AuditLogRow.resource_type == "projects.project", AuditLogRow.resource_id == project_id),
                    d["after"]["project_id"].astext == project_id,
                    d["before"]["project_id"].astext == project_id,
                )
            )
        stmt = (
            select(AuditLogRow)
            .where(*clauses)
            .order_by(AuditLogRow.timestamp.desc(), AuditLogRow.audit_id)
            .limit(limit)
        )
        return [_to_record(r) for r in (await self.session.execute(stmt)).scalars().all()]

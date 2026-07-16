"""PostgreSQL data-access foundation (Phase 0 — rails only).

This package holds the async SQLAlchemy engine/session plumbing and the
declarative ``Base`` that every future domain's ORM models will inherit from.

Phase 0 scope: infrastructure only. No domain tables live here; the first real
tables arrive in Phase 2 (platform core). Mongo remains the live datastore for
every domain until each domain cuts over. See
``.claude/plans/postgres-migration-plan.md`` (§A1, §A2).
"""
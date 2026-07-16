"""Repository layer — the single data-access seam for PostgreSQL domains.

Introduced in Phase 0 as rails; populated one domain at a time from Phase 2
onward. Each domain gets one repository module (e.g. ``users.py``,
``contracts.py``) whose methods take an ``AsyncSession`` and return Pydantic
models. Routes and services in a migrated domain call repositories — never
``db.`` (Mongo) directly.

See ``CONVENTIONS.md`` in this package and
``.claude/plans/postgres-migration-plan.md`` §A2 for the full pattern.
"""
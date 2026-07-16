# Repository conventions — the PostgreSQL data-access seam

Basis: `.claude/plans/postgres-migration-plan.md` §A2. This is the **one** data-access path for migrated domains. Do not reintroduce ad-hoc `db.` access, and do not add a second ORM strategy — the audit §6 dual-LLM-path fragmentation is the anti-pattern we are explicitly avoiding in the data layer.

## The pattern

- **One repository module per domain**: `repositories/users.py`, `repositories/contracts.py`, `repositories/projects.py`, … Each holds the queries for exactly one domain's tables.
- **Repositories take an `AsyncSession`** (from the `get_session` FastAPI dependency in `db/session.py`) and expose **intention-revealing methods** — `get_user_by_email`, `claim_next_wave_number`, `list_opportunities(filter)`. Method names describe intent, not SQL shape.
- **Repositories return Pydantic models** (or plain scalars), never SQLAlchemy ORM rows. Convert ORM ⇄ Pydantic at the repository boundary (`Model.model_validate(row, from_attributes=True)`). Callers stay decoupled from persistence.
- **No `db.` / Mongo inside a repository.** Ever. A repository speaks only SQLAlchemy.
- **Transactions belong to the caller.** The `get_session` request dependency commits on success / rolls back on error. Multi-document write clusters that must be atomic (plan §A6.2 — contract approval, invoice-batch build, SOW creation, ingestion apply) wrap their repository calls in a single `async with session.begin():`. Repository methods `flush()` when they need a generated id; they do **not** `commit()`.

## Cross-domain access during the parallel run

The migration is phased: at any moment some domains are on Postgres and some are still on Mongo. Two rules keep this honest (plan §A2):

- A **migrated** domain that needs data from **another migrated** domain calls that domain's **repository** (Postgres) — not Mongo, not a raw query into another domain's tables.
- A **migrated** domain that needs data from a **not-yet-migrated** domain reads Mongo through a **narrow, temporary read-only shim** named `*_legacy_read` (e.g. `users_legacy_read`). Each shim is:
  1. an explicit **deliverable** of the phase that creates it (listed in that phase's PR), and
  2. an explicit **deletion** in the phase that migrates the depended-on domain.

  No `*_legacy_read` shim may survive silently. Phase 11's gate requires zero of them.

## Counters, CAS, and transactions

- Atomic counters (`find_one_and_update` + `$inc`) become **sequences** (global, e.g. chat) or a **scoped counter table** with `UPDATE … SET n = n + 1 WHERE key = :k RETURNING n` (per-scope, e.g. `pm_counters`, Vega versions). See plan §A6.1.
- Single-document compare-and-swap (`find_one_and_update` gated on current status) becomes `UPDATE … WHERE id = :id AND status = :expected RETURNING *`. The row is the concurrency unit. See plan §A6.2.

## Testing

- Repository tests run against **real Postgres**, never a mock. Use the `pg_session` fixture (see `tests/conftest.py`), which applies `alembic upgrade head` against an ephemeral database and wraps each test in a transaction that is rolled back on teardown (fast, isolated).
- **CI path (primary):** a dedicated compose test database (`docker-compose.test.yml`) with `TEST_DATABASE_URL` pointed at it.
- **Local/hermetic path (fallback):** `testcontainers` spins an ephemeral `pgvector/pgvector:pg16` container automatically. If neither `TEST_DATABASE_URL` nor Docker is available, the PG fixtures **skip** (they never fail), so the existing `mongomock-motor` suite is unaffected.
- **Parallel CI:** for `pytest -n` (xdist), give each worker its own database (schema-per-worker or database-per-worker keyed on `PYTEST_XDIST_WORKER`) so migrations and rollbacks don't collide. Phase 0 ships single-worker; wire schema-per-worker when the suite grows enough to need it.

## Scaffolding

`repositories/_scaffolding/` holds a throwaway hello-world (`HelloRow` model + `HelloRepository` + `_scaffolding_hello` table via migration `0002`) that proves the engine → session → Alembic → repository → test loop end to end. It is **not a real domain**, FKs to nothing, and is **deleted in Phase 2** along with its migration and the scaffolding import in `alembic/env.py`.
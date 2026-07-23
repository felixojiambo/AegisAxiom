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

## Phase 2a additions — the pattern every later domain copies mechanically

These were resolved while landing the first real domain (platform-core identity). They are now rules, not suggestions. Full derivation: `.claude/plans/phase2a-census.md`.

1. **ORM models live in `backend/db/models/<domain>.py`** — one module per domain, mirroring `repositories/`. Every row class inherits `db.base:Base` and is named **`<Concept>Row`** (e.g. `UserRow`, `TenantRow`) to stay visibly distinct from the same-named Pydantic DTO. `db/models/__init__.py` imports each module so a single `import db.models` registers the whole schema on `Base.metadata`; `alembic/env.py` imports `db.models` (this replaced the Phase-0 scaffolding import).

2. **Convert ORM ⇄ Pydantic at the boundary with `Model.model_validate(row, from_attributes=True)`.** Repositories return the **existing** Pydantic models (`User`, `Tenant`, `OrganizationBranding`, `OAuthToken`) so callers receive unchanged shapes. Where no Pydantic model existed in the Mongo era (sessions, reset tokens, oauth state), define a small local DTO in the repository module (e.g. `SessionRecord`).

3. **Secret material never rides on a returned DTO.** `password_hash`/`invite_token` are columns on `UserRow` but not on the `User` model — `model_validate` drops them naturally. Credential checks use a **separate secret-returning method** (`get_password_hash_by_email`, `get_auth_record_by_email` → `(User, hash)`); the `User` DTO handed to every other caller carries no secret.

4. **Derived fields are not stored.** `permissions`/`effective_permissions` are request-time derivations (roles + `extra_permissions` + implication closure), computed in the auth path — the repo leaves them at model defaults. **Opaque string-list fields** read/written whole and never filtered by interior (`extra_permissions`, `ai_usage_viewable_users`) → **JSONB column, never a child table.**

5. **Single-use CAS = `UPDATE/DELETE … WHERE … AND <expected-state> RETURNING`** (plan §A6), executed as one statement — never read-then-write. The row is the concurrency unit; the losing concurrent writer gets no row back. Reference implementations: `AuthRepository.consume_reset_token` (`… WHERE token AND used=false RETURNING`) and `consume_oauth_state` (`DELETE … WHERE state AND provider RETURNING`).

6. **Multi-write clusters** (user + child assignments, invite-accept) add all rows and `flush()` inside the caller's request transaction (the `get_session` dependency commits once) — atomic without an explicit `begin()`. Use an explicit `async with session.begin():` only when the cluster spans repositories or must commit independently of the request.

7. **TTL (no native Postgres TTL, plan R10):** `expires_at`/`created_at` columns carry expiry; correctness-sensitive reads filter on them (`get_active_session`); a scheduled `cleanup_expired_*` sweep (gated by `DISABLE_BACKGROUND_JOBS`) removes stale rows.

8. **`*_legacy_read` shims** (plan §A2) read UNMIGRATED domains only, are narrow + read-only, and **must** carry an inline annotation naming the phase that deletes them. A repository never reads another **migrated** domain's tables directly — it calls that domain's repository.

## Scaffolding (removed in Phase 2a)

The Phase-0 throwaway hello-world (`repositories/_scaffolding/`, migration `0002`, the scaffolding test, and the `alembic/env.py` import) proved the rails end to end and was **deleted in Phase 2a** once the first real domain landed. Migration `0004_drop_scaffolding_hello` drops its table (downgrade recreates it by hand for a clean round-trip).
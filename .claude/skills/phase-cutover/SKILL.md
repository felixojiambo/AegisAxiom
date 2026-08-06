---
name: phase-cutover
description: Run one MongoDB→PostgreSQL domain cutover phase end to end (census → migration/models/repositories → in-domain rewrites → cross-domain caller switch → tests → scripts/verify → report). Use for Phases 3–8 of .claude/plans/postgres-migration-plan.md, and for 2b. Takes a params file under params/ naming the phase, collections, in-scope files, protected files, shims created/deleted, contract endpoints, and the Alembic revision id.
---

# /phase-cutover — one domain, Mongo → Postgres

**Trigger:** `/phase-cutover <params-file>` — e.g. `/phase-cutover params/phase2b.yaml`.

Phases 2b and 3–8 are structurally identical. This skill is that structure,
written once. The per-phase differences live entirely in the params file; the
pipeline below never changes.

**Read first, every time:** `CLAUDE.md` (standing invariants — git, seam,
contracts, environment, scope, definition of done),
`.claude/plans/postgres-migration-plan.md` (Part A + this phase's block),
`backend/repositories/CONVENTIONS.md` (the repository seam and the Phase-2a
rules every later domain copies mechanically).

**Standing rules that this skill does not restate:** no push, no commit, no new
branches; all Python in-container; never print secret values; never pass
`--remove-orphans`. They are in `CLAUDE.md`. If a params file contradicts
`CLAUDE.md`, that is a STOP.

---

## Parameter block

The params file is the phase. Every field is required unless marked optional.

| Field | Meaning |
|---|---|
| `phase` | Phase label, e.g. `2b`. Used in every report heading. |
| `title` | One line: what this phase migrates. |
| `plan_ref` | Section(s) of the migration plan this phase executes. |
| `census` | Path to the pre-written census, or `TO WRITE` if step 1 must produce it. |
| `collections` | Every Mongo collection this phase moves. Drives the grep pattern file. |
| `in_scope_files` | Files this phase is allowed to modify. Anything else is out of scope. |
| `protected` | Files this phase must NOT touch, with the reason. Editing one is a STOP. |
| `shims_created` | New `*_legacy_read` shims, each with the phase that deletes it. |
| `shims_deleted` | Shims this phase removes. |
| `contract_endpoints` | Endpoints whose response shape must not drift. |
| `migration_revision` | Alembic revision id — see the lane convention below. |
| `smoke_migrated` / `smoke_legacy` | One endpoint each side of the parallel-run seam. |
| `new_env_flags` | Env vars this phase introduces (names only, never values). |
| `sweeps` | Optional one-off data/code sweeps, each with its verification. |
| `stop_conditions` | Phase-specific conditions that force a STOP-and-report. |

---

## Alembic revision convention (parallel lanes)

Revisions are named `<next-number>_<domain>_<detail>` — `0005_platform_core_rbac`,
`0006_crm_sales_core`. The number is **claimed in the params file before any
code is written**.

- Two lanes may run concurrently **only** if they have claimed **different**
  numbers. Check every `params/*.yaml` and `backend/alembic/versions/` before
  claiming.
- A collision — two params files claiming the same number, or a claimed number
  already present in `versions/` — is a **STOP**, not something to renumber
  mid-flight. Renumbering after `down_revision` chains exist rewrites another
  lane's history.
- `down_revision` points at the current head at claim time. If another lane
  lands first, rebase this revision onto the new head **before** running the
  Alembic round-trip — never after.

---

## The pipeline

Each step ends with a concrete artifact. Do not start step N+1 until step N's
artifact exists.

### 1. Census — call-site inventory, classification, derived DDL

If `census` names an existing file, read it and re-verify its counts against the
current tree (a census written weeks ago may be stale). Otherwise write it to
`.claude/plans/phase<N>-census.md` before touching any code.

For every collection in `collections`:

1. **Inventory both access forms.** `db.<name>` *and* `db["<name>"]`. Phase 2a
   counted only the attribute form and shipped with 28 live subscript-form
   sites still on Mongo — the failure this step exists to prevent. Also check
   `db['<name>']` and any `get_db()` alias in the file.
2. **Classify every site**: (a) in-domain → this phase's repository;
   (b) cross-domain caller → switched to a repository method;
   (c) aggregation/`distinct` → needs a SQL port (name the port).
3. **Derive the DDL from actual document usage**, not from the plan's sketch and
   not from imagination. Schemaless collections get their columns from the real
   insert/update shapes. Note every field the plan assumes but the code does
   not have.
4. **Apply §A3**: relational if filtered / joined / aggregated / sorted /
   uniquely constrained / foreign-keyed anywhere; JSONB if it is an opaque blob
   read and written whole.
5. **STOP and report** if the census contradicts the plan — a collection or
   field that does not exist, a cited file that is gone, a disposition that
   cannot hold. 2a.1 found the phantom `user_app_access` this way. Do not
   improvise a replacement design mid-phase.

**Artifact:** the census file, ending in a PROCEED or STOP recommendation.

### 2. Migration + ORM models + repositories

- **Migration** `backend/alembic/versions/<migration_revision>.py`: tables,
  indexes (all of them — the `server.py` startup index block dies here, plan
  §A7), trigram GIN on any column reached by a former `$regex`, and a
  `downgrade()` that actually reverses. String ids stay `TEXT` primary keys.
- **ORM models** `backend/db/models/<domain>.py`, one module per domain, every
  row class named `<Concept>Row`, registered via `db/models/__init__.py`.
- **Repositories** `backend/repositories/<domain>.py`: intention-revealing
  method names, take an `AsyncSession`, return the **existing** Pydantic models
  via `model_validate(row, from_attributes=True)`, never `commit()`.
  Secret-bearing columns never ride on a returned DTO — use a separate
  secret-returning method.
- **CAS** (plan §A6): one statement,
  `UPDATE … WHERE id = :id AND <expected-state> RETURNING *`. Never
  read-then-write. Counters: global → `SEQUENCE`; scoped → counter table with
  `UPDATE … SET n = n + 1 WHERE key = :k RETURNING n`.
- **Transactions** (plan §A6.2): multi-write clusters flush inside the request
  transaction; an explicit `async with session.begin():` only when the cluster
  spans repositories or must commit independently.

**Gate:** `scripts/verify.ps1 -Alembic` (or `verify.sh --alembic`) is green.

### 3. In-domain rewrites

Rewrite this domain's own routes/services/seeds onto the repository. Delete the
Mongo access as you go — a domain is never half-and-half at rest (locked
decision #6). Port `distinct()` → `SELECT DISTINCT`, `$regex` → `ILIKE` with the
trigram index, `$group`/`$lookup`/`$unwind` → the SQL named in the census.

### 4. Cross-domain caller switch

Switch every class-(b) caller to this domain's repository. Then add any
`shims_created` — narrow, read-only, named `*_legacy_read`, each carrying an
inline annotation naming the phase that deletes it — and delete every shim in
`shims_deleted`, including its annotation and any now-dead import.

**Gate:** the phase's grep pattern file (step 6) returns zero hits.

### 5. Tests

New tests use the `pg_session` fixture against real Postgres — never a mock.
Required coverage:

- CRUD for each new repository method that has a caller.
- A **concurrency test** for every CAS and counter touched: prove the loser
  loses, prove numbers do not collide.
- The ported aggregations, asserted against the shape the old Mongo pipeline
  produced.
- `EXPLAIN ANALYZE` on any aggregation the census marked hot.
- Migrate the previously-skipped tests that this phase makes PG-testable.

A skipped PG test is a failed PG test. The harness enforces it; do not work
around it by narrowing the test path.

### 6. Verify

Write `scripts/greps/phase<N>.txt` covering **both** access forms for every
collection in `collections` (copy the header and the `(?<!\x60)` backtick
exemption from `scripts/greps/phase2a.txt`), then run the full gate:

```powershell
scripts\verify.ps1 -Alembic -Grep scripts\greps\phase<N>.txt `
  -Tests <this phase's PG test paths> -Boot -Smoke `
  -SmokeMigrated <smoke_migrated> -SmokeLegacy <smoke_legacy> `
  -Contracts contracts\captures
```

Iterate until it exits 0. Do not hand-wave a profile: if `-Contracts` reports a
shape change, either the change is intended — re-record the baseline and say so
explicitly in the report — or it is a bug. Never re-record to make red go green
without naming the change.

### 7. Report

1. **Census summary** — collections, site counts by class, DDL decisions, any
   STOP raised and how it was resolved.
2. **Raw verify output**, pasted, not paraphrased. All profiles.
3. **Shim inventory delta** — the table from `CLAUDE.md` §2, updated. Update
   `CLAUDE.md` itself in the same change.
4. **Handoff line** — one sentence: what the operator's next action is.

Also state anything the harness could not verify (visual/browser checks, data
the local environment does not have). List it; do not omit it.

---

## STOP conditions (any one halts the phase)

- The census contradicts the plan (missing collection, field, or file).
- A `protected` file needs editing to make the phase work.
- The claimed `migration_revision` number is already taken by another lane or by
  `backend/alembic/versions/`.
- A shim would be created with no named deleting phase.
- `scripts/verify` cannot be made green without weakening a pattern file,
  narrowing a test path, or re-recording a contract baseline to hide a real
  shape change.

Report the STOP with evidence and stop. Do not improvise around it.

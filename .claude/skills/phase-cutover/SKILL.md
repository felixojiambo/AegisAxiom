---
name: phase-cutover
description: Run one MongoDB→PostgreSQL domain cutover phase end to end (params completeness check → census → migration/models/repositories → in-domain rewrites → cross-domain caller switch → tests → scripts/verify → report). Use for Phases 3–8 of .claude/plans/postgres-migration-plan.md, and for 2b. Takes a params file under params/ naming the phase, collections, in-scope files, protected files, shims created/deleted, contract endpoints, and the Alembic revision id.
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

## Standing rules of this skill (harness retro, 2026-11-27)

These two rules came out of Phase 2b. They apply to every step below.

1. **Every operator decision goes into the params file, dated, when it is
   approved.** Write it before acting on it, not when reporting. Append to the
   params `decisions:` list:
   ```yaml
   decisions:
     - date: "2026-11-27"          # ISO date of approval
       id: D3                      # stable id the report can cite
       decision: "<what was approved, in the operator's terms>"
       trigger: "<the gap, STOP or question that prompted it>"
       status: "approved"           # later: implemented | superseded by D<n>
       evidence: "<file:line / query / count backing it, if any>"
   ```
   Never edit an approved entry's text. If a later decision changes it, add a
   new entry and mark the old one `superseded by D<n>`. A correction to recorded
   evidence is also a new dated entry that says what was wrong. A decision that
   exists only in chat, a commit message or a report is lost. 2b lost and
   re-derived three of them.

2. **Census and scratch outputs go to `.claude/plans/`, never a temp
   scratchpad.** Use `phase<N>-params-check.md`, `phase<N>-census.md` and
   `phase<N>-<topic>.md` (trace notes, query results, sweep logs). Scratchpad
   and temp directories are cleared between sessions, and the next session
   cannot audit what it cannot read. Raw `verify` output for the report may be
   saved anywhere, but it is pasted into the report itself.

---

## Parameter block

The params file is the phase. Every field is required unless marked optional.

| Field | Meaning |
|---|---|
| `phase` | Phase label, e.g. `2b`. Used in every report heading. |
| `title` | One line: what this phase migrates. |
| `plan_ref` | Section(s) of the migration plan this phase executes. |
| `census` | Path to the pre-written census, or `TO WRITE` if step 1 must produce it. |
| `collections` | Every Mongo collection this phase moves. Registered in `backend/db/migrated_collections.json` at step 4; the zero-grep and the workflow guard are derived from that registry. |
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
| `decisions` | Dated operator decisions, appended when approved (standing rule 1). Starts empty. |
| `params_check` | Path to the step-0 report and its verdict (`GAPS` / `CLEAN`), filled in by step 0. |

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

### 0. Params completeness check (before the census)

The params file is written from the plan. The plan was written from an older
tree. Step 0 diffs all three before anyone relies on the params. **No code, no
census, no migration until step 0 is CLEAN.**

Compare the params file against (a) this phase's block in
`.claude/plans/postgres-migration-plan.md` and (b) the live code. For every
collection in the plan block *and* in `collections`:

1. **Collections.** Every collection the plan block names is in `collections`,
   and vice versa. Every one exists in the live code (or the data). A phantom
   is a gap, as 2a.1's `user_app_access` was.
2. **Seeds.** Every seeder that writes one of these collections is in
   `in_scope_files`. Search `backend/seeds/`, every `*seed*` module under
   `backend/services/` and `backend/routes/`, and the startup block in
   `backend/server.py`. 2b missed `seeds/seed_projects_rbac.py` (8 sites).
3. **Write paths.** Every file that writes one of these collections is in
   `in_scope_files`, with a `limit:` when only part of the file is in scope.
   Writes are `insert_*`, `update_*`, `replace_one`, `delete_*`,
   `find_one_and_*` and `bulk_write`. Check all three access forms (`db.<name>`,
   `db["<name>"]`, `get_collection("<name>")`) plus the **dynamic path**: any
   module that `field_registry.register_module_config` maps onto the collection,
   since workflow record actions reach it as `db[collection]`. 2b missed
   `ingestion/repository.py`'s audit insert and the workflow-executor write path.
4. **Endpoint paths.** Every path in `contract_endpoints`, `smoke_migrated` and
   `smoke_legacy` resolves to a real route: router prefix plus its
   `include_router` prefix, checked in `server.py`. 2b's `/api/policies` was a 404
   (the router mounts under `/admin`). Every list or read route of this domain
   is either a contract endpoint or named as deliberately excluded.
5. **Hardcoded duplicates.** Look for in-code constants that shadow the
   collection's data, such as 2b's `DEFAULT_ROLE_PERMISSIONS`. Each is either a
   `sweep` or named as out of scope.
6. **Lane claim.** `migration_revision` is free (see the lane convention).
   `down_revision` is the current head.

**Artifact:** `.claude/plans/phase<N>-params-check.md`. It holds a table with
one row per finding (*check · expected by plan/params · found in code ·
evidence file:line · proposed params change*) and ends in **GAPS** or
**CLEAN**.

**On GAPS: STOP and report.** List each proposed params change. Make no params
edit until the operator approves. Then write the approved changes into the
params file together with a dated `decisions:` entry per approval (standing
rule 1), and re-run step 0 until it is CLEAN. Record the final verdict in
`params_check`.

### 1. Census — call-site inventory, classification, derived DDL

If `census` names an existing file, read it and re-verify its counts against the
current tree (a census written weeks ago may be stale). Otherwise write it to
`.claude/plans/phase<N>-census.md` before touching any code.

For every collection in `collections`:

1. **Inventory all three access forms.** `db.<name>`, `db["<name>"]` *and*
   `get_collection("<name>")`. Phase 2a counted only the attribute form and
   shipped with 28 live subscript-form sites still on Mongo, which is the
   failure this step exists to prevent. Also check `db['<name>']`, any
   `get_db()` alias in the file, and the dynamic `field_registry` path found in
   step 0.
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

Then **register this phase's collections** in
`backend/db/migrated_collections.json`, one entry per collection:
`{"name": "<collection>", "phase": "<phase>", "repository": "repositories/<domain>.py"}`.
That one edit arms both gates. `verify -Grep` builds the zero-grep from it
(dot, subscript and `get_collection` forms), and the workflow-executor guard
refuses record writes to any module that resolves to a registered collection.
Entries are never removed. Do **not** write a pattern file: handwritten
`scripts/greps/*.txt` files are retired.

**Gate:** `scripts/verify.ps1 -Grep` (or `verify.sh --grep`) returns zero hits.

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

Confirm step 4 registered every collection in `collections` (the `[grep] START`
line prints the collection count and phases), then run the full gate. The dev
database must be at `alembic head` **and seeded**. After a new migration, boot
once with `SKIP_STARTUP_SEEDS=false` (`docs/LOCAL_SETUP.md` §5), or `-Smoke`
fails on empty tables.

```powershell
scripts\verify.ps1 -Alembic -Grep `
  -Tests <this phase's PG test paths> -Boot -Smoke `
  -SmokeMigrated <smoke_migrated> -SmokeLegacy <smoke_legacy> `
  -Contracts contracts\captures
```

Iterate until it exits 0. Do not hand-wave a profile: if `-Contracts` reports a
shape change, either the change is intended — re-record the baseline and say so
explicitly in the report — or it is a bug. Never re-record to make red go green
without naming the change.

### 7. Report

0. **Params check** — the step-0 verdict, each gap found, and the dated
   `decisions:` entry that closed it.
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

- Step 0 found a gap (seed, write path, endpoint path, collection, hardcoded
  duplicate) and the operator has not yet approved the params update.
- The census contradicts the plan (missing collection, field, or file).
- A `protected` file needs editing to make the phase work.
- The claimed `migration_revision` number is already taken by another lane or by
  `backend/alembic/versions/`.
- A shim would be created with no named deleting phase.
- `scripts/verify` cannot be made green without removing a registry entry,
  narrowing a test path, or re-recording a contract baseline to hide a real
  shape change.

Report the STOP with evidence and stop. Do not improvise around it.

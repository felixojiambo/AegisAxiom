# AegisAxiom — project rules

Standing invariants for every task in this repo. Phase prompts do **not** restate
these; they assume them. If a task instruction contradicts a rule here, STOP and
report the contradiction instead of picking a winner.

Reference documents (read before touching the data layer):
`.claude/plans/postgres-migration-plan.md` (Part A + phase list) ·
`backend/repositories/CONVENTIONS.md` (the repository seam) ·
`docs/LOCAL_SETUP.md` (fresh-machine checklist).

---

## 1. Git

- **Never push. Never commit. Never create, switch, or delete branches** unless
  the task explicitly says so.
- Work in the **working tree**. The operator reviews the diff and commits after
  the gate is green.
- Do not `git stash`, `git reset --hard`, `git checkout --`, or otherwise discard
  uncommitted work. Do not rewrite history.
- `git status` at the end of a task must show **only** the files that task was
  scoped to touch.

## 2. The repository seam

The migration is phased: at any moment some domains are on Postgres and some are
still on Mongo. Three rules keep that honest (plan §A2, CONVENTIONS.md).

1. **Migrated collections have ZERO `db.<name>` references anywhere in live
   backend code.** Live = every `.py` under `backend/` except `tests/`,
   `scripts/`, `migrations/`, `archive/` (one-off Mongo data-fix scripts and
   the test suite are out of scope by census §1.8), and, at any depth,
   `__pycache__/`, `.venv/`, `venv/` and `node_modules/` (third-party code).
   Prose mentions inside backticks (`` `db.users` ``) in docstrings are exempt
   and are the *only* exemption.
2. **Cross-domain reads of migrated data go through that domain's repository** —
   never a raw query into another domain's tables, never Mongo.
3. **Reads of unmigrated data from migrated code go through a narrow, read-only
   shim named `*_legacy_read`**, carrying an inline annotation that names the
   phase which deletes it. No shim may exist without a named executioner.

**Live shim inventory (keep this table current — it is a deliverable of every
phase that adds or removes one):**

| Shim | Reads | Deleted in |
|---|---|---|
| `ai_employees_legacy_read` | `db.ai_employees` (`auth_helper.py`) | Phase 8 |
| `tenant_app_grants_legacy_read` | `db.tenant_app_grants` (`middleware/tenant_scoping.py`) | Phase 8 |
| `app_registry_legacy_read` | `db.app_registry` (`middleware/tenant_scoping.py`) | Phase 8 |
| ~~`roles_legacy_read`~~ | ~~`db.roles` (`auth_helper.py`; unannotated copy in `routes/projects_events.py`)~~ | **Deleted in Phase 2b** → `RolesRepository.get_role_permissions` |

Phase 11's gate requires zero shims.

## 3. Contracts that must not drift

- **String IDs stay TEXT primary keys.** `user_id`, `tenant_id`, `role_id`,
  `contract_id`, … are application-generated strings. Do not convert them to
  `bigserial`/UUID columns; do not renumber them.
- **Repositories return the existing Pydantic models** (`User`, `Tenant`,
  `OrganizationBranding`, …) via `Model.model_validate(row, from_attributes=True)`.
  Where the Mongo era had no model, define a small local DTO in the repository
  module. Callers must not learn about SQLAlchemy rows.
- **API response shapes are byte-compatible** across a cutover unless the task
  says otherwise. `scripts/capture_contracts.py compare` is the check; a
  structural diff (added / removed / retyped key) is a failure, not a surprise.
- **Secret material never rides on a returned DTO.** `password_hash` /
  `invite_token` are columns, not model fields; credential checks use a separate
  secret-returning repository method.

## 4. Environment truths

- **All Python runs in-container.** Do not create a host virtualenv — pins
  resolve against the container's Python 3.11. One-offs go through the image:
  `docker compose run --rm --entrypoint sh backend -c "python …"`.
- **Tests use `TEST_DATABASE_URL`**, pointed at the `docker-compose.test.yml`
  stack. **testcontainers cannot reach Docker from inside the container — never
  rely on it.** It stays in `conftest.py` only as a host-side fallback; the
  harness always sets `TEST_DATABASE_URL` explicitly. In-container the test DB is
  `postgres-test:5432`; from the Windows host it is `localhost:5544`.
- **`docker compose build backend` after any `requirements.txt` or `Dockerfile`
  change.** `run`/`up` will not rebuild on their own; the symptom of a missed
  rebuild is `command not found` for a newly added tool.
- **`.env` edits require `docker compose up -d --force-recreate <svc>`**, not
  `restart`. `restart` reuses the old environment block and the change silently
  does not land.
- **Never use `--remove-orphans`.** `docker-compose.test.yml` shares the compose
  project name, so the main stack sees `postgres-test` as an orphan and would
  delete it.
- **Never print secret values.** Key names only — `ADMIN_PASSWORD`, not its
  contents. This applies to logs, captures, reports, and error messages.

## 5. Scope discipline

- **Single issue per task.** Fix what was asked. Note adjacent problems in the
  report; do not fix them uninvited.
- **Census before wide edits.** Before rewriting a domain, inventory every call
  site and classify it (in-domain / cross-domain / aggregation). The census is a
  written artifact, produced *before* any code changes.
- **STOP-and-report on plan contradictions.** If the census finds the plan
  assumes a collection, field, or file that does not exist (as 2a.1 found with
  the phantom `user_app_access`), stop and report. Do not improvise a
  replacement design mid-phase.

## 6. Definition of done

**A task is done when `scripts/verify.ps1` (Windows) / `scripts/verify.sh`
(Linux/CI) passes for its declared profile — not when the code compiles, not
when the diff looks right, and not when tests were "probably" fine.**

```powershell
# the standing phase-cutover gate
scripts\verify.ps1 -All -Tests tests/test_platform_core_*_pg.py
```

Profiles: `-Alembic` `-Grep` `-Tests <path…>` `-Boot` `-Smoke`
`-Contracts <capture-dir>` `-All`. `-Grep` takes no argument. It builds the
zero-grep from `backend/db/migrated_collections.json`, the one registry of
migrated collections, in the dot, subscript and `get_collection` forms. The
runtime workflow guard reads the same file. Exit code 0 means everything requested
passed; anything else is a failure. **A skipped Postgres test counts as a
failure** — skipped-as-green is the exact failure mode the harness exists to
kill. Paste the raw verify output in the report; do not paraphrase it.

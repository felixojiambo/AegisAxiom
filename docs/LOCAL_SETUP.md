# Local Setup — Fresh Machine Checklist

A checklist for standing up AegisAxiom on a brand-new development machine. The
stack is **local-first**: everything runs in Docker Compose against local Mongo,
Postgres, and Chroma. Follow these steps in order.

> **The classic failure:** copying an `.env` from another machine. A stale
> `backend/.env` often carries a dead cloud `MONGO_URL` (an old Atlas cluster)
> or a `DATABASE_URL` for a database that isn't there. Before Phase 2b this
> crashed boot at the `validate_dag` startup check. Since 2b, `validate_dag`
> reads the Postgres permission registry, and when that database is unreachable
> or empty it **logs an error or warning and boot continues**
> (`SKIP_STARTUP_DAG_VALIDATION=true` skips it entirely). The app comes up, but
> every domain behind the dead database fails at request time. Look for
> `Permission registry:` lines in `docker compose logs backend`.
> **Always start from the `.env.example` files below.** Their values are
> local-first and correct.

---

## 1. Create the env files from the examples

```bash
cp backend/.env.example   backend/.env
cp frontend/.env.example  frontend/.env
```

The example values are already correct for local Compose. In particular:

| Key            | Local value                                             |
| -------------- | ------------------------------------------------------- |
| `MONGO_URL`    | `mongodb://mongo:27017`                                  |
| `DB_NAME`      | `aegisaxiom`                                             |
| `CHROMADB_URL` | `http://aegisaxiom-chromadb:8000`                       |
| `DATABASE_URL` | `postgresql+asyncpg://aegis:aegis@postgres:5432/aegis`  |
| `ALLOWED_ORIGINS` / `FRONTEND_URL` | `http://localhost:3000`             |

Do **not** point `MONGO_URL` at any cloud/Atlas host for local work.

`frontend/.env` needs `REACT_APP_BACKEND_URL=http://localhost:8001` and
`REACT_APP_POSTHOG_KEY=` (blank keeps analytics off locally).

## 2. All Python runs IN-CONTAINER

Do **not** create a host virtualenv. The dependency pins resolve against the
container's **Python 3.11** and break on newer host Pythons. Run any Python
one-off through the backend image:

```bash
docker compose run --rm --entrypoint sh backend -c "python -c 'print(1)'"
```

## 3. Generate `MATCH_FERNET_KEY` (in-container)

If `MATCH_FERNET_KEY` is absent/blank in `backend/.env`, generate one and paste
it in (field-level PII encryption key):

```bash
docker compose run --rm --entrypoint sh backend \
  -c "python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
```

## 4. Rebuild after dependency/Dockerfile changes

After **any** change to `backend/requirements.txt` or a `Dockerfile`:

```bash
docker compose build backend
```

`docker compose run` / `up` will **not** rebuild on their own. The tell-tale
symptom of a missed rebuild is `command not found` for a newly added tool.

## 5. Seeding: on first boot AND after every new migration

The migration is a fresh start (plan OQ-1). No rows are copied from Mongo, and
every Postgres table a migration creates is **empty** until the startup seeders
run. Tenants, users, roles, permissions and policies live in Postgres. The
backend does **not** run Alembic on boot, and with `SKIP_STARTUP_SEEDS=true` it
does not seed either.

**Seed on a fresh machine, and again after any new Alembic migration** (for
example after pulling a phase that adds `0006_…`):

1. Bring the schema to head:
   ```bash
   docker compose exec backend sh -c "cd /app/backend && alembic upgrade head"
   ```
2. Set `SKIP_STARTUP_SEEDS=false` in `backend/.env`.
3. Recreate the backend so it picks up the edit (see §5a, not `restart`):
   ```bash
   docker compose up -d --force-recreate backend
   ```
   Watch `docker compose logs -f backend` for the seed lines (tenants / roles /
   permissions / policies), `Permission registry: implication DAG validated (… nodes)`,
   `Auto-initialized admin account` (fresh machine only) and
   `Application startup complete.`
4. Set `SKIP_STARTUP_SEEDS=true` again and recreate once more:
   `docker compose up -d --force-recreate backend`. Daily boots stay fast
   because the seed block is skipped.

**The symptom of a skipped step:** startup logs `DAG validation found an empty
registry`, and every non-wildcard user, admin included, gets **403** on
permission-gated routes such as `/api/roles`. The roles table is empty, so
nobody has permissions. Phase 2b hit exactly this. Seeding is idempotent, so
re-running it is safe.

Admin credentials come from `ADMIN_EMAIL` / `ADMIN_PASSWORD` in `backend/.env`.

## 5a. After ANY `.env` edit: recreate, never restart

```bash
docker compose up -d --force-recreate <service>     # e.g. backend
```

`docker compose restart` reuses the container's **old** environment block, so
an `.env` change silently does not land. This applies to every key, including
`SKIP_STARTUP_SEEDS`. Never add `--remove-orphans`: the test stack
(`docker-compose.test.yml`) would be treated as an orphan and deleted.

## 5b. Tests: `TEST_DATABASE_URL` is the only working path

Postgres tests run against the `docker-compose.test.yml` database, and
**`TEST_DATABASE_URL` must point at it**. Let the harness set it:

```powershell
scripts\verify.ps1 -Tests tests/test_platform_core_*_pg.py    # Windows
```
```bash
scripts/verify.sh --tests 'tests/test_platform_core_*_pg.py'  # Linux / CI
```

The harness brings up `postgres-test`, bridges it onto the main network, and
runs pytest in the backend container with
`TEST_DATABASE_URL=postgresql+asyncpg://aegis:aegis@postgres-test:5432/aegis_test`.
From the Windows host the same database is `localhost:5544`.

- **testcontainers does not work here.** It cannot reach Docker from inside the
  backend container. It remains in `conftest.py` only as a host-side fallback,
  so never rely on it.
- A Postgres test that **skips** (no `TEST_DATABASE_URL`) counts as a
  **failure**. The harness reports it as `SKIP-AS-FAIL`.
- A bare `docker compose run backend pytest …` without `-e TEST_DATABASE_URL=…`
  skips every PG test. Without the harness's network bridge, `postgres-test`
  does not resolve.

## 6. Ingestion stack (optional)

The ingestion stack (`docker-compose.ingestion.yml`) needs its own env files
authored from the examples before it will run:

```bash
cp backend/.env.ingestion.example   backend/.env.ingestion
cp frontend/.env.ingestion.example  frontend/.env.ingestion
```

## 7. Networking: service names inside, `localhost` from the host

- **Inside Compose**, services address each other by **service name**:
  `mongo`, `postgres`, `chromadb` (the `aegisaxiom-chromadb` container alias also
  resolves). Use the container-internal ports (Mongo `27017`, Postgres `5432`,
  Chroma `8000`).
- **From the Windows host**, use `localhost` with the **published** ports:
  backend `http://localhost:8001`, frontend `http://localhost:3000`,
  Mongo `27017`, Postgres `5432`, Chroma `8100` (mapped to the container's 8000).

## 8. Quick health check

```bash
curl http://localhost:8001/api/health     # -> 200
curl http://localhost:3000                # -> HTML shell (title AEGISAXIOM)
```

Postgres sanity:

```bash
docker compose exec backend  sh -c "cd /app/backend && alembic current"   # -> head
docker compose exec postgres psql -U aegis -d aegis -c "\dx"              # -> pg_trgm, vector
```

# Local Setup — Fresh Machine Checklist

A checklist for standing up AegisAxiom on a brand-new development machine. The
stack is **local-first**: everything runs in Docker Compose against local Mongo,
Postgres, and Chroma. Follow these steps in order.

> **The classic failure:** copying an `.env` from another machine. A stale
> `backend/.env` often carries a dead cloud `MONGO_URL` (an old Atlas cluster).
> The app then crashes at the `validate_dag` startup check. **Always start from
> the `.env.example` files below** — their values are local-first and correct.

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

## 5. First boot: seed once, then turn seeding off

On a **fresh** machine the local Mongo is empty (no tenants, roles, permissions,
or admin user). Seed once:

1. Set `SKIP_STARTUP_SEEDS=false` in `backend/.env`.
2. Boot clean:
   ```bash
   docker compose down
   docker compose build backend
   docker compose up -d
   ```
   Watch `docker compose logs -f backend` for: `validate_dag` passing, the seed
   lines (tenants / roles / permissions), `Auto-initialized admin account`, and
   `Application startup complete.`
3. Set `SKIP_STARTUP_SEEDS=true` again and `docker compose restart backend`.
   Daily boots stay fast because the seed block is skipped.

Admin credentials come from `ADMIN_EMAIL` / `ADMIN_PASSWORD` in `backend/.env`.

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

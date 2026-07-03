# /init-project-doc — Bootstrap CLAUDE.md and CODEMAP.md

**Trigger:** `/init-project-doc`
**Purpose:** Bootstrap or upgrade the project-level `CLAUDE.md` (doctrine — the
front door agents read first) and `CODEMAP.md` (inventory — the deterministic
map of routes, services, components agents use to navigate). The CLAUDE.md is
written in full. The CODEMAP.md is written with deterministic facts only —
semantic one-line descriptions are filled in later by `/codemap-update`.

You are guiding the developer through an interactive bootstrap. The developer
answers your questions — you do the file work. At no point should the developer
have to write the doc or open a doc generator.

---

## Cold start: prerequisites

Before starting, verify:

1. **Run from project root.** Check that `pyproject.toml`, `package.json`,
   `requirements.txt`, `go.mod`, or another root manifest is in the current
   directory. If not, ask the developer to `cd` to the project root and re-run.

2. **Git repo.** Run `git rev-parse --show-toplevel`. If not a git repo, fail
   loudly — this skill needs git for file enumeration and is not designed for
   ad-hoc directories.

3. **Branch hygiene.** Run `git branch --show-current`. If the developer is on
   `dev` or `main`, refuse to proceed and ask them to create a feature branch
   first. If on a feature branch, continue.

---

## Phase 1: Detect existing state

Run all three and report what you find:

```bash
test -f CLAUDE.md && echo "CLAUDE.md exists" || echo "CLAUDE.md missing"
test -f CODEMAP.md && echo "CODEMAP.md exists" || echo "CODEMAP.md missing"
test -d .claude && ls .claude || echo ".claude missing"
```

State determines mode:

| State | Mode | Behavior |
|---|---|---|
| Neither exists | **fresh** | Write both from scratch after interview |
| CLAUDE.md exists, no CODEMAP.md | **augment** | Propose CLAUDE.md upgrade as a diff; write fresh CODEMAP.md |
| Both exist | **upgrade** | Diff-mode for both; never clobber without explicit approval |
| Only CODEMAP.md | **anomaly** | Treat as fresh for CLAUDE.md; preserve existing CODEMAP.md, only fill missing rows |

In any mode where files exist, **read them in full before proposing changes**.
Never propose overwriting content the developer already wrote.

---

## Phase 2: Detect the stack

Run these in order. The output of each informs the next phase's discovery
queries. Don't ask the developer questions you can answer from the filesystem.

### Python
```bash
test -f pyproject.toml && cat pyproject.toml | head -50
test -f requirements.txt && head -30 requirements.txt
test -f setup.cfg && cat setup.cfg
test -f Pipfile && cat Pipfile
```
Look for: `fastapi`, `django`, `flask`, `starlette`, `celery`, `pytest`,
`ruff`, `mypy`. Note whether async/sync (presence of `asyncio`, `motor`,
`asyncpg`). Note ORM (`sqlalchemy`, `tortoise`, `motor`, `prisma`).

### JavaScript / TypeScript
```bash
test -f package.json && cat package.json
test -f tsconfig.json && echo "TypeScript: yes" || echo "TypeScript: no"
test -f next.config.js -o -f next.config.ts && echo "Framework: Next.js"
test -f vite.config.js -o -f vite.config.ts && echo "Framework: Vite"
test -f craco.config.js && echo "Framework: CRA + craco"
test -f remix.config.js && echo "Framework: Remix"
test -f astro.config.mjs && echo "Framework: Astro"
test -f svelte.config.js && echo "Framework: Svelte/SvelteKit"
```
Look for: framework, package manager (yarn vs npm vs pnpm — check
`packageManager` field and lockfile presence), styling system (tailwind,
styled-components, css-modules), state library, data layer (React Query, SWR,
Redux, Zustand), validation (zod, yup), test runner.

### Other
If neither manifest is present, ask the developer to identify the stack
explicitly. Don't guess from file extensions alone.

### Report findings
Present a single block to the developer:

```
STACK DETECTED:
  Backend: FastAPI (async, motor, slowapi, pydantic)
  Frontend: React 19 + CRA/craco, shadcn/ui (Radix + Tailwind), yarn
  Package mgrs: pip (backend), yarn (frontend)
  Tests: pytest (backend), CRA test (frontend)
  ORM/DB: motor + pymongo → MongoDB

Anything missing or wrong?
```

Wait for confirmation. If wrong, ask for corrections — don't guess again.

---

## Phase 3: Discover documentation

```bash
# Root markdown files
find . -maxdepth 1 -type f -name "*.md" -not -name "CLAUDE.md" -not -name "CODEMAP.md"

# Top-level docs/ tree
test -d docs && find docs -maxdepth 2 -type f -name "*.md"

# memory/ or guidelines/ dirs sometimes carry context
test -d memory && find memory -maxdepth 2 -type f -name "*.md"
test -d guidelines && find guidelines -maxdepth 2 -type f -name "*.md"
```

For each `.md` found, read the **first 30 lines** to classify it. Categories:

- **Doctrine** — onboarding guidelines, "how to work on this" docs (e.g.,
  `AI_DEVELOPER_GUIDELINES.md`, `CONTRIBUTING.md`)
- **Architecture** — system design, engine references (e.g.,
  `ENGINE_ARCHITECTURE.md`)
- **Conventions** — permissions, routing, styling references
- **Per-domain** — module-specific docs (e.g., `docs/ingestion/`, `docs/qbo/`)
- **History/PRD** — product requirements, change logs

Classification is the basis for the CLAUDE.md "Must-read docs" section in
Phase 5. Show the categorization to the developer and ask whether anything
is mis-categorized.

---

## Phase 4: Discover code surface (deterministic)

This phase produces the deterministic data for CODEMAP.md. Do NOT generate
semantic descriptions here — leave those as TODO markers for `/codemap-update`.

### Backend route modules (FastAPI pattern)
```bash
# 1. List route files
find backend/routes -maxdepth 1 -name "*.py" -not -name "__*" 2>/dev/null

# 2. Build a router_var -> prefix map from server.py (canonical source).
#    Many projects set the prefix at include_router time, not on APIRouter().
grep -hE 'include_router\(\s*[a-z_]+_router' backend/server.py 2>/dev/null \
  | grep -oE '[a-z_]+_router(\s*,\s*prefix\s*=\s*"[^"]+")?' \
  | head -50

# 3. As a fallback, also check each router file for an APIRouter(prefix=...)
#    inline definition:
for f in backend/routes/*.py; do
  grep -hE 'APIRouter\([^)]*prefix\s*=' "$f" | head -1
done

# 4. Extract permission slugs per file (real permission keys used in code)
for f in backend/routes/*.py; do
  bn=$(basename "$f")
  test "$bn" = "__init__.py" && continue
  slugs=$(grep -hoE 'has_perm\([^,]+,\s*"[a-z_]+\.[a-z_.]+"' "$f" 2>/dev/null \
          | grep -oE '"[a-z_]+\.[a-z_.]+"' | sort -u | tr -d '"' | tr '\n' ',' | sed 's/,$//')
  echo "$f | perms=$slugs"
done
```

When joining the data: match the `import X_router from routes.X` lines in
`server.py` to find which file each `_router` variable came from. Then read
the `include_router(X_router, prefix=..., tags=[...])` line for that variable
to get its prefix. The router file path → permission slugs comes from step 4.

If a router has no explicit `prefix=` in server.py and no inline prefix in
the router file, it lives at the api_router's root prefix (usually `/api`).
Record this as `prefix=<api_root>` not as empty.

Adapt the pattern for Django (urls.py path() calls), Flask (route decorators),
Express (app.METHOD calls), Go (mux registrations), etc. Use the stack
detected in Phase 2.

### Backend services / shared modules
```bash
find backend/services -maxdepth 2 -name "*.py" -not -name "__*" 2>/dev/null
find backend -maxdepth 1 -name "*.py" -not -name "__*" -not -name "test_*" 2>/dev/null
```
Record path + first docstring (if present) — that's all. The semantic role
of each service is the codemap-haiku agent's job, not yours.

### Frontend components / pages / portals

Two-tier discovery — explicit portals first, then the broader top-level
component set (many projects use a flat `components/` layout where each
top-level file is effectively a page).

```bash
# Tier 1 — Explicit *Portal.* / *Page.* entry points
find frontend/src -maxdepth 4 \
  \( -name "*Portal.js" -o -name "*Portal.tsx" \
     -o -name "*Page.js" -o -name "*Page.tsx" \) 2>/dev/null

# Tier 2 — Top-level component files (often page-equivalent in flat layouts)
find frontend/src/components -maxdepth 1 \
  \( -name "*.js" -o -name "*.jsx" -o -name "*.tsx" \) 2>/dev/null | head -60

# Tier 3 — App routing config (App.js / routes.tsx) to verify which top-level
# components are actually rendered as routes
grep -hE '<Route\s+path=' frontend/src/App.js 2>/dev/null \
  | grep -oE 'path="[^"]+"' | head -40
```

Cross-reference: a top-level component is an "entry point" if it's referenced
in `App.js` as a `<Route element={...}>`. Components not referenced there are
probably shared widgets, not pages — list them in CODEMAP under a separate
"shared widgets" section, not the entry-points section.

For each entry point, extract:
- The default export name (`export default function Foo()` or `export default Foo`)
- API imports — look for `from '@/lib/api'` or framework-specific patterns
- Whether the file is inside `components/<subdir>/` (tabs/widgets of a parent
  portal) or at the top level (a page itself)

### Tests
```bash
find backend/tests tests -name "test_*.py" -o -name "*_test.py" 2>/dev/null | head -20
find frontend -name "*.test.*" -o -name "*.spec.*" 2>/dev/null | head -20
```
Just record locations.

### Scripts / tooling
```bash
test -d scripts && find scripts -maxdepth 2 -type f
test -f Makefile && grep -E '^[a-z][a-z0-9_-]*:' Makefile
```

### Output
Build an in-memory structure that looks like:

```yaml
routes:
  - path: backend/routes/projects.py
    prefix: /projects
    perm_namespace: projects.*
    perm_keys: [projects.view, projects.create, projects.edit, projects.delete]
services:
  - path: backend/services/field_registry.py
    docstring: ""
frontend_entries:
  - path: frontend/src/components/ProjectManagementPortal.js
    default_export: ProjectManagementPortal
    api_imports: ['@/lib/api']
tests:
  - backend/tests/test_projects.py
scripts:
  - scripts/cleanup_seed.py
```

This structure feeds Phase 6.

---

## Phase 5: Build proposed CLAUDE.md

The CLAUDE.md is the **front door**. Its job is to make a fresh agent productive
without grepping for orientation. Keep it tight — target 3000–5000 tokens.
Verbose CLAUDE.md is a tax on every session.

### Required sections, in this order:

#### 1. Project identity (1 paragraph)
One sentence on what the project is. One sentence on the stack. One sentence
on the deployment target if known.

#### 2. Must-read docs (priority order)
A table. Each entry: doc path + one-line "when to read this." Use the
classifications from Phase 3.

```markdown
## Must-read before touching code

| Read when... | Doc |
|---|---|
| You're new to this codebase | `docs/AI_DEVELOPER_GUIDELINES.md` |
| Touching routes or permissions | `docs/ROUTE_PROTECTION.md`, `docs/PERMISSIONS_GUIDE.md` |
| Adding a new entity (fields, events, policies) | `docs/ENGINE_ARCHITECTURE.md` |
| Touching the AI employee system | `backend/services/ai_employee_defaults.py` |
| Importing an external app | `.claude/skills/ingest-app/SKILL.md` |
```

#### 3. Critical conventions (extracted from doctrine docs)
A short list — 8–15 items max — of the never/always rules that catch agents
out. Don't restate the whole guidelines doc; pick the gotchas that have real
blast radius (build breakers, security, data loss). If the project has a
doctrine doc, extract these from it — don't invent.

#### 4. Code surface index
For backend-heavy projects, a route-module → permission-slug → frontend-portal
triangle:

```markdown
## Code surface

### Backend routes → Frontend portals
| Route module | Permission slug | Frontend entry |
|---|---|---|
| `backend/routes/projects.py` | `projects.*` | `ProjectManagementPortal.js` |
| `backend/routes/chat.py` | `chat.*` | `AIChatPortal.js` |
| ... | ... | ... |
```

For projects without that pattern, list the top-level dirs with one-line
purpose each.

#### 5. Shared services
A bullet list of services every dispatched agent needs to know about, with
one-line purpose each. Lift from the docstrings extracted in Phase 4 where
possible; otherwise mark TODO for codemap-update to fill.

#### 6. Subagent dispatch contract
A required section for projects that use waves-and-threads. Spell out:

- The shared data dictionary requirement (every wave that crosses domains
  must produce one before dispatch — point at the relevant skill)
- The `bypassPermissions` mode requirement for background code-gen agents
- The mandatory code review wave after parallel implementation
- The wave-postmortem requirement

If the project doesn't use waves-and-threads, omit this section.

#### 7. See also
Links to the other skills in `.claude/skills/` and any project-specific
operational docs.

### Style rules

- Use tables, not prose, wherever possible. Tables compress.
- Don't restate doc content — link to the doc instead.
- Don't list every file. List entry points and reference indices.
- Stay under 5000 tokens. If it's longer, you're duplicating docs/.

### Upgrade mode
If a CLAUDE.md already exists, propose a **diff**, not a rewrite. Preserve
sections the developer authored. Add missing required sections. Only modify
existing sections when their content is clearly stale (e.g., references a
file path that no longer exists).

---

## Phase 6: Build proposed CODEMAP.md (deterministic skeleton)

The CODEMAP.md is the inventory. It has two layers:

- **Deterministic** (this skill writes): paths, route prefixes, permission
  keys, exports. Always derivable from code without an LLM.
- **Semantic** (`/codemap-update` fills): one-line description of each file's
  role.

Skeleton structure:

```markdown
# CODEMAP

> Generated by `/init-project-doc`. Semantic descriptions populated by
> `/codemap-update` — entries marked `_TODO_` are awaiting that pass.

## Backend routes

| File | Prefix | Permission namespace | Description |
|---|---|---|---|
| `backend/routes/projects.py` | `/projects` | `projects.*` | _TODO_ |
| `backend/routes/chat.py` | `/chat` | `chat.*` | _TODO_ |

## Backend services

| File | Description |
|---|---|
| `backend/services/field_registry.py` | _TODO_ |
| `backend/services/event_bus.py` | _TODO_ |

## Frontend entries

| File | Default export | Description |
|---|---|---|
| `frontend/src/components/ProjectManagementPortal.js` | `ProjectManagementPortal` | _TODO_ |

## Tests
- `backend/tests/` — pytest suite
- `frontend/src/**/*.test.js` — CRA test runner

## Scripts
- `scripts/cleanup_seed.py` — _TODO_
```

### Preservation rules

If a CODEMAP.md already exists, **never overwrite filled-in descriptions**.
Only add new entries (for files not present in the existing CODEMAP) and
mark removed entries with `<!-- removed: file deleted -->` rather than
deleting them silently — the developer reviews the diff.

---

## Phase 7: Present and confirm

Show the developer:

1. The detected stack (already confirmed in Phase 2 — restate)
2. The full proposed CLAUDE.md content
3. The full proposed CODEMAP.md content
4. For upgrade mode: a unified diff vs the existing file

Ask:

> "Ready to write?
> (y) Write both files
> (e) Edit a section first — tell me which section and what to change
> (n) Cancel without writing"

Wait for explicit confirmation. If the developer asks for edits, apply them
and re-present before writing.

---

## Phase 8: Write

Use the Write tool (not echo/cat to file). Write both files at the project
root.

```python
# CLAUDE.md at <project root>/CLAUDE.md
# CODEMAP.md at <project root>/CODEMAP.md
```

After writing:

```bash
ls -la CLAUDE.md CODEMAP.md
wc -l CLAUDE.md CODEMAP.md
```

---

## Phase 9: Self-test

Run these checks:

1. **Markdown sanity** — files are non-empty and contain at least one heading
2. **Table sanity** — every table in CODEMAP.md has consistent column counts
3. **Link sanity** — paths referenced in CLAUDE.md actually exist:
   ```bash
   grep -oE '`(backend|frontend|docs|scripts)/[^`]+`' CLAUDE.md | tr -d '`' | while read p; do
     test -e "$p" || echo "BROKEN LINK: $p"
   done
   ```
4. **TODO count** — print how many `_TODO_` markers are in CODEMAP.md so the
   developer knows how much `/codemap-update` will populate

---

## Phase 10: Hand off to next steps

Tell the developer:

> "Bootstrapped successfully.
> - CLAUDE.md: <N lines, M sections>
> - CODEMAP.md: <N entries, M awaiting descriptions>
>
> Next steps:
> 1. Review both files. Edit anything you want.
> 2. Run `/codemap-update` to fill in the semantic descriptions (uses Haiku,
>    runs cheap).
> 3. Commit both files to the feature branch. Do not push or open a PR
>    without explicit approval.
>
> Future maintenance:
> - The PR-open hook will detect structural changes and prompt you to refresh
>   CODEMAP.md when relevant.
> - You can always run `/codemap-update` manually."

---

## Failure modes and recovery

| Symptom | Likely cause | Recovery |
|---|---|---|
| Phase 2 detects no stack | Run from wrong directory | Have developer `cd` to project root |
| Phase 4 finds no routes | Non-standard layout | Ask developer where routes live |
| Phase 5 doctrine docs missing | New/undocumented project | Skip section, mark TODO |
| Phase 8 write fails | Permission / readonly fs | Surface error, do not retry silently |
| Developer wants to cancel mid-flight | Any phase | Cancel without writing; do not leave partial files |

---

## Notes for future sessions

This skill writes **bootstrap content**, not maintained content. The
deterministic data in CODEMAP.md becomes stale as files are added/removed.
The companion skill `/codemap-update` keeps it fresh on demand and via the
PR-open hook.

If you're running this skill on a project where someone already ran it once,
prefer **upgrade mode** behavior — diff carefully, preserve developer edits,
add only what's missing.

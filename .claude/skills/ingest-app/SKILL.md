# /ingest-app — AEGISAXIOM App Ingestion Skill

**Trigger:** `/ingest-app <github-url>`
**Re-ingestion:** `/reingest-app <app_id_or_url>`

You are importing an Emergent-built app into AEGISAXIOM. You will guide the developer
through an interactive import process. The developer answers your questions — you
do the work. At no point should the developer need to write code, run scripts,
or look up file paths. ALWAYS use your waves and threads skill and present this plan to the user after question 10 is answered.

---

## Cold start: Environment setup

If nothing is running yet (fresh Claude window, no backend, no database), set
everything up before starting the import. Walk the developer through each step.

### 1. Branch check
```bash
git branch --show-current
```
Must be on `feature/app-ingestion-mechanic` or a branch based on it.
If not: `git checkout feature/app-ingestion-mechanic`

### 2. Start an isolated test database
**Never use the production MongoDB.** Ask the developer to confirm.
```bash
# Check if test mongo is already running
docker ps | grep aegisaxiom-test-mongo

# If not running, start one
docker run -d --name aegisaxiom-test-mongo -p 27018:27017 mongo:7
```

### 3. Start the backend pointing at the test DB
```bash
cd backend
MONGO_URL=mongodb://localhost:27018 \
DB_NAME=aegisaxiom_ingestion_test \
JWT_SECRET=test-secret-key \
ADMIN_EMAIL=admin@test.com \
ADMIN_PASSWORD=testpass123 \
python -m uvicorn server:app --reload --port 8001
```
Tell the developer to run this in a separate terminal. Wait for them to confirm
the server is running before proceeding.

### 4. Get an auth token
```bash
TOKEN=$(curl -s -X POST http://localhost:8001/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@test.com","password":"testpass123"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_token',''))")
echo "Token: $TOKEN"
```
If this fails, the backend isn't running or the admin seed didn't work. Debug before continuing.

### 5. Verify ingestion system is loaded
```bash
curl -s http://localhost:8001/api/app-ingestion/ingestions \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```
Should return `[]` (empty list). If you get a 404, the ingestion routes aren't loaded.

### 6. Confirm with the developer
> "Test environment is ready:
> - MongoDB: localhost:27018 (isolated test instance)
> - Backend: localhost:8001
> - Database: aegisaxiom_ingestion_test
> - **This is NOT connected to production.**
> Ready to import?"

---

## Phase 1: Clone and analyze the app

### Step 1 — Clone
```bash
git clone --depth 1 <github-url> .ingestion/staging/<repo-name>
```

### Step 2 — Run the analyzer
```python
import sys; sys.path.insert(0, "backend")
from services.ingestion.analyzer import EmergentAnalyzer

analyzer = EmergentAnalyzer(".ingestion/staging/<repo-name>")
summary = analyzer.analyze()
```

### Step 3 — Present the inventory
Show the developer a clear summary:

```
I've analyzed the app. Here's what I found:

ENTITIES (N found):
  - project: 8 fields, 5 routes
  - task: 11 fields, 5 routes
  - sprint: 5 fields, 5 routes
  ...

INFRASTRUCTURE (will be discarded):
  - Auth system (login, register, JWT, password hashing)
  - User management routes
  - Layout shell (AppLayout, Header, Sidebar)
  - shadcn/ui component library (AEGISAXIOM has its own)
  - Emergent-specific dependencies

APP MODULES (candidates for import):
  - ProjectsPage, SprintBoardPage, BacklogPage, ...
  - Domain routes: /projects, /tasks, /sprints, ...

NEEDS YOUR DECISION:
  - DashboardPage — may overlap with AEGISAXIOM dashboards
```

---

## Phase 2: Developer interview

Ask these questions in order. Don't skip any. Wait for answers.

### Question 1 — Classification review
> "I've classified these as infrastructure to discard: [list].
> **Anything here you want to keep?**"

### Question 2 — Module selection
> "These are the app modules I can import: [list with field/route counts].
> **Which ones do you want to bring into AEGISAXIOM?**"

### Question 3 — Needs-review items
For each item flagged as "needs review":
> "[Component] overlaps with an existing AEGISAXIOM feature. Do you want to:
> (a) keep the AEGISAXIOM version, (b) replace it with this app's version, or (c) skip it?"

### Question 4 — App identity
> "What should this app be called in AEGISAXIOM?"
> This becomes:
> - Display name: "Davies PMO"
> - URL slug: `davies-pmo` (used in routes like `/api/davies-pmo/projects`)
> - Python slug: `davies_pmo` (used in permissions, collections, code)

Validate the slug with:
```python
from services.ingestion.models import validate_slug
valid, error = validate_slug("<slug>")
```
If invalid, explain why and ask for a different name.

**Reserved namespace exception (wholesale replacement only).** `validate_slug`
rejects any slug that matches an entry in
`backend/services/ingestion/models.py::RESERVED_NAMESPACES` (currently:
`opportunities`, `contacts`, `accounts`, `deals`, `commissions`, `invoices`,
`contracts`, `time`, `field_jobs`, `projects`, `users`, `chat`, `ai_employees`,
`finance`, `dashboards`, `admin`, etc.). This protects core AEGISAXIOM modules
from accidental clobber.

If — and only if — Question 6 confirmed the import is a **wholesale
replacement** of an existing core module that ships with AEGISAXIOM (e.g., the
ingested PMO module taking the `projects` slug from the legacy core
`projects` module), the namespace can be released:

1. **Confirm with the developer** that the legacy module is being deleted,
   not coexisting. Ask explicitly: "I'm about to release the `<slug>`
   namespace from `RESERVED_NAMESPACES`. This means the core AEGISAXIOM
   `<slug>` module will be replaced — its routes, collections, and
   permissions will belong to the ingested app from now on. Confirm?"

2. **Edit `backend/services/ingestion/models.py`** and remove the slug
   from `RESERVED_NAMESPACES`. Keep the change in the same commit as the
   ingestion pipeline records so reviewers see the namespace handover in
   one place.

3. **Re-validate** — `validate_slug("<slug>")` now returns `(True, "")`.
   Continue with Phase 3 normally.

4. **In Phase 4 "Replace old module"**, the legacy module's routes,
   collections, and sidebar nav must be retired (per the existing
   replacement instructions). The released namespace is now load-bearing
   for the ingested app.

If the import is NOT a wholesale replacement, do not release the
namespace. Pick a different slug instead.

### Question 5 — Module placement
For each selected module:
> "Where should **[module]** appear in the AEGISAXIOM UI?
> Options: portal tabs (like Finance Portal), new sidebar section, or under an existing section"

### Question 6 — Does anything replace an existing module?
> "Does **[module]** replace an existing AEGISAXIOM module? For example, does this
> projects module replace AEGISAXIOM's current pm_projects?"

### Question 7 — Field review
For each entity, show the extracted fields in a table:

| Field | Type | Options |
|-------|------|---------|
| title | string | — |
| status | string | not_started, working_on_it, stuck, done |
| priority | string | low, medium, high, critical |
| assignee_id | string | — |
| estimated_hours | number | — |

> "Are these fields correct? Any to rename, change type, drop, or add?"
> "Note: dates show as 'string' because the source app stores them as strings.
> Should any of these be 'date' type instead?"

### Question 8 — RBAC
> "The source app has these roles: [list]. How should they map to AEGISAXIOM?
> Options: create new AEGISAXIOM roles, add permissions to existing roles, or both?"

### Question 9 — AI employees
> "Which AI employees should have access to this app's data? (Or skip for now)"

### Question 10 — Confirmation
Present the full import plan:
```
IMPORT PLAN:
  App: Davies PMO (davies-pmo)
  Modules: project, task, sprint, wave
  Discarded: auth, users, notifications
  UI: Portal tabs under new sidebar entry
  Replaces: pm_projects
  RBAC: Generate permissions, suggest roles for review

Ready to proceed? (yes/no)
```

**Wait for explicit "yes" before continuing.**

### Additional questions (adaptive)

The 10 questions above are the baseline. If the analysis reveals additional
decisions that the core questions don't cover, ask follow-up questions before
proceeding. Don't guess — ask. Examples:
- Custom dashboard/analytics endpoints not covered by the entity template
- Notification behavior (source app creates notifications → AEGISAXIOM uses event bus)
- Field name conflicts between entities or with existing AEGISAXIOM modules
- Ambiguous status enums or custom workflows
- Source app patterns that don't map cleanly to AEGISAXIOM conventions

The confirmation step (Question 10) must summarize ALL decisions, including
any follow-up questions asked.

---

## Phase 3: Execute the pipeline

All steps use the REST API via curl. The backend must be running (see Cold Start above).
Replace `$TOKEN` with the auth token from setup, and `$APP_ID` / `$CLONE_DIR` with
values returned from each step.

### Step 1 — Create the ingestion record
```bash
curl -s -X POST http://localhost:8001/api/app-ingestion/ingestions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "repo_url": "<github-url>",
    "name": "<app name from interview>",
    "slug": "<slug from interview>",
    "description": "<description from interview>"
  }' | python3 -m json.tool
```
Save the `app_id` from the response → `$APP_ID`

### Step 2 — Clone the repo
```bash
curl -s -X POST "http://localhost:8001/api/app-ingestion/ingestions/$APP_ID/clone" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```
Save the `clone_dir` from the response → `$CLONE_DIR`

**Private repo fallback (host-clone).** The clone endpoint runs `git clone`
*inside* the backend container, which has no GitHub credentials. Public
repos work; private repos fail with `could not read Username for
'https://github.com'`. When that happens, clone on the host into the
container's expected staging path and skip the API call:

```bash
# 1. Pick the slug (must match the slug you'll use in /ingestions POST).
SLUG="<app-slug>"
CLONE_DIR="backend/.ingestion/staging/${SLUG}"

# 2. Clone on the host (uses your local git credential helper).
mkdir -p backend/.ingestion/staging
git clone --depth 1 <github-url> "${CLONE_DIR}"

# 3. If the backend container uses a bind mount on backend/, the clone is
# already visible inside the container. If not (or if Windows bind-mount
# sync lags), copy it in:
docker cp "${CLONE_DIR}" aegisaxiom-ingestion-backend:/app/.ingestion/staging/

# 4. Skip the /clone API call. Use $CLONE_DIR for the next step's
# clone_dir param. The pipeline records will just show clone_status as
# 'manual' but every downstream step (analyze, extract, register) works
# the same way.
```

### Step 3 — Analyze and classify
```bash
curl -s -X POST "http://localhost:8001/api/app-ingestion/ingestions/$APP_ID/analyze?clone_dir=$CLONE_DIR" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```
This returns the classification results. Show them to the developer if they
want to review (they already answered questions in Phase 2, but this is the
machine-generated confirmation).

### Step 4 — Record developer decisions
```bash
curl -s -X POST "http://localhost:8001/api/app-ingestion/ingestions/$APP_ID/decisions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "selected_modules": [
      <one object per module from interview, e.g.:>
      {"name": "project", "entity_name": "project", "replaces_module": "pm_projects", "ui_placement": "portal_tab"},
      {"name": "task", "entity_name": "task", "ui_placement": "portal_tab"}
    ],
    "discarded_modules": [
      {"name": "notification", "reason": "Conflicts with AEGISAXIOM notifications"}
    ],
    "import_plan": {}
  }' | python3 -m json.tool
```

### Step 5 — Extract field catalogues
```bash
curl -s -X POST "http://localhost:8001/api/app-ingestion/ingestions/$APP_ID/extract" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "{\"clone_dir\": \"$CLONE_DIR\", \"selected_entities\": [\"project\", \"task\", \"sprint\", \"wave\"]}" \
  | python3 -m json.tool
```

### Step 6 — Register fields into AEGISAXIOM
```bash
curl -s -X POST "http://localhost:8001/api/app-ingestion/ingestions/$APP_ID/register" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```
Tell the developer: "Fields registered. The policy engine and workflow builder
can now see this app's fields and events."

### Step 7 — Generate RBAC
```bash
curl -s -X POST "http://localhost:8001/api/app-ingestion/ingestions/$APP_ID/rbac/generate" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"source_roles": ["admin", "project_manager", "engineer"]}' \
  | python3 -m json.tool
```
Present the permissions and role suggestions to the developer for review.
Ask: "Do you want to create these roles, extend existing AEGISAXIOM roles, or modify?"

After approval, apply with:
```bash
curl -s -X POST "http://localhost:8001/api/app-ingestion/ingestions/$APP_ID/rbac/apply" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "permissions": [<the approved permissions list>],
    "role_decisions": [
      {"action": "create_new", "name": "<role_name>", "display_name": "<display>", "permissions": [<keys>]}
    ]
  }' | python3 -m json.tool
```

### Step 8 — Check status
```bash
curl -s "http://localhost:8001/api/app-ingestion/ingestions/$APP_ID" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```
Show the pipeline progress and summary stats to the developer.

### Step 9 — Commit progress
```bash
git add backend/services/ingestion/ backend/routes/app_ingestion.py
git commit -m "Ingest <app-name>: pipeline records and field registration"
```

---

## Phase 3.5: Build the shared data dictionary

Before ANY code generation, build a data dictionary that will be included
verbatim in every agent's prompt. This prevents frontend/backend schema drift.

For each selected entity, document:
- **AEGISAXIOM collection:** `<app_slug>_<entity_plural>`
- **ID field:** `<entity>_id` (e.g., `project_id`, `task_id`)
- **ID prefix:** `gen_id("pm_proj_")` etc.
- **All fields:** exact name, type, and for enums the exact values
- **Status enum values:** the exact strings (e.g., `not_started`, `working_on_it`, `stuck`, `done`)
- **API response shape:** `{<entity>_id, ...fields, created_by, created_at}`

Present this dictionary to the developer for confirmation before proceeding.

### Store adaptation mappings

Write the confirmed dictionary to `.ingestion/mappings/<slug>.json`:
```json
{
  "app_slug": "<slug>",
  "source_commit_sha": "<sha>",
  "ingestion_version": 1,
  "entities": {
    "<entity>": {
      "collection": "<app_slug>_<entity_plural>",
      "id_field": "<entity>_id",
      "id_prefix": "pm_<abbrev>_",
      "fields": {
        "<field>": {"type": "<type>", "source_name": "<emergent_name>"}
      },
      "status_enum": ["value1", "value2"],
      "events": ["<entity>_created", "<entity>_updated", "<entity>_deleted"]
    }
  }
}
```
This file is the contract for `/reingest-app`.

### Contract validation

> **Status: Not yet implemented.** Wave 3 of `fix/ingestion-gaps` will add this.
> Skip this step until the function and endpoint exist. Until then, rely on the
> mandatory code review wave to catch field-name divergence between agents.

After code generation, the contract validation step (`service.validate_against_mapping()`,
exposed via `POST /api/admin/ingestions/{app_id}/validate`) will check that every field
name, ID field, and enum value referenced in the generated route files and frontend
components matches the mapping file. This catches divergence between parallel code-gen
agents (e.g., backend uses `customer_name` while frontend uses `customer`).

---

## Phase 4: Code generation (the adaptation)

### Code generation uses waves-and-threads (MANDATORY)

All code generation MUST use the waves-and-threads methodology with
`bypassPermissions` mode for background agents. Standard wave structure:

#### Background agent permissions (`bypassPermissions`)

When code generation agents run in parallel via waves-and-threads, they execute as
background subagents. In `auto` permission mode, the Write tool requires interactive
user approval for every file — which blocks background agents indefinitely since no
human is watching their terminal.

To solve this, dispatch background agents with `bypassPermissions: true` (or via the
`--permission-mode bypassPermissions` flag). This grants them Write access without
per-file approval. The mandatory code review wave (Wave N+1) is the
human-equivalent quality gate — no code merges without review.

**When to use:**
- Agents dispatched via `Agent` / SendMessage that must create or modify files unattended
- Code generation waves only

**When NOT to use:**
- Interactive sessions where the developer is present
- Code review agents (read-only, no Write needed)
- Any agent touching destructive operations

**Wave N — Implementation (parallel):**
- Thread A: Backend routes for core entities (2-4 entities per thread)
- Thread B: Backend routes for secondary entities
- Thread C: Backend routes for support entities + dashboard/analytics
- Thread D: Frontend portal + all tab components

Each agent receives: the shared data dictionary (verbatim), AEGISAXIOM styling
constraints, interface contracts, and the source app code path.

**Wave N+1 — Integration + Code Review:**
- Thread A: Wire server.py, App.js, Layout.js (+ module replacement if applicable)
- Thread B: Code review all generated files (opus model)

**Wave N+2 — Fix wave (if review finds issues)**

Present the wave plan to the developer and wait for confirmation before
dispatching.

### Backend and frontend adaptation

This is where you write the actual adapted code. For each selected module:

### Backend: Create route files

Create `backend/routes/<app_slug>_<entity>.py` for each entity. Every file must have
ALL SIX integrations (see template below).

**Template — adapted route file:**
```python
"""
<App Name> — <Entity> Management
Ingested from: <github-url>
"""
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone

from database import get_db
from models import gen_id
from auth_helper import get_current_user, has_perm
from middleware.ai_authz import require_ai_permission
from services.ingestion.route_helpers import crud_envelope
from services.field_registry import register_fields, register_module_events, register_module_config

router = APIRouter()

# ---- Field Registry ----
register_fields("<app_slug>", "<entity>", [
    # Paste the field definitions from the catalogue
])
register_module_config("<app_slug>", "<app_slug>_<entity_plural>", "<entity>_id", "Custom")
register_module_events("<app_slug>", "<entity>", ["created", "updated", "deleted"])
# Add "status_changed" to the events list when the entity has a `status` field.

# ---- Models ----
# Adapt from the Emergent Pydantic models

# ---- Routes ----
# Adapt from the Emergent routes, replacing:
#   - db references → db = get_db(); db["<app_slug>_<entity_plural>"]
#   - ObjectId → gen_id("<entity_prefix>_")
#   - human auth → has_perm(current_user, "<app_slug>.<entity>.view") in handler body
#   - AI auth   → on the decorator: dependencies=[Depends(require_ai_permission("<app_slug>.<entity>.<action>"))]
#                 The AI dep is mandatory on EVERY generated route. Operation → perm key:
#                   list/get → ".view"   create → ".create"   update → ".edit"
#                   delete   → ".delete" change_status → ".change_status"   assign → ".assign"
#                 The dep no-ops for human callers (has_perm remains the human gate)
#                 and gates AI-tagged callers with the same perm key.
#   - state changes → wrap the DB op in `async with crud_envelope(...)`. The envelope
#                     coordinates policy → emit → audit; do NOT call evaluate_policies
#                     or emit_event directly. Reads (list/get) are NOT wrapped.

# Example create handler (full pattern):
#   @router.post(
#       "/<entity_plural>",
#       dependencies=[Depends(require_ai_permission("<app_slug>.<entity_plural>.create"))],
#   )
#   async def create_<entity>(
#       payload: <Entity>Create,
#       request: Request,
#       current_user=Depends(get_current_user),
#   ):
#       if not has_perm(current_user, "<app_slug>.<entity_plural>.create"):
#           raise HTTPException(status_code=403, detail="Permission denied")
#       db = get_db()
#       doc = {
#           "<entity>_id": gen_id("<prefix>_"),
#           **payload.model_dump(),
#           "created_at": datetime.now(timezone.utc).isoformat(),
#           "created_by": current_user.user_id,
#       }
#       async with crud_envelope(
#           module="<app_slug>", entity="<entity>", action="created",
#           resource_id=doc["<entity>_id"], current_user=current_user, db=db,
#           request=request,
#       ) as ctx:
#           await db["<app_slug>_<entity_plural>"].insert_one(doc)
#           doc.pop("_id", None)
#           ctx.set_after(doc)
#       return doc

# Example update handler with status-change detection:
#   @router.put(
#       "/<entity_plural>/{<entity>_id}",
#       dependencies=[Depends(require_ai_permission("<app_slug>.<entity_plural>.edit"))],
#   )
#   async def update_<entity>(
#       <entity>_id: str,
#       payload: <Entity>Update,
#       request: Request,
#       current_user=Depends(get_current_user),
#   ):
#       if not has_perm(current_user, "<app_slug>.<entity_plural>.edit"):
#           raise HTTPException(status_code=403, detail="Permission denied")
#       db = get_db()
#       before = await db["<app_slug>_<entity_plural>"].find_one(
#           {"<entity>_id": <entity>_id}, {"_id": 0}
#       )
#       if not before:
#           raise HTTPException(status_code=404, detail="Not found")
#       updates = payload.model_dump(exclude_unset=True)
#       updated = {**before, **updates,
#                  "updated_at": datetime.now(timezone.utc).isoformat()}
#       async with crud_envelope(
#           module="<app_slug>", entity="<entity>", action="updated",
#           resource_id=<entity>_id, current_user=current_user, db=db,
#           before=before, request=request,
#       ) as ctx:
#           await db["<app_slug>_<entity_plural>"].replace_one(
#               {"<entity>_id": <entity>_id}, updated
#           )
#           ctx.set_after(updated)
#       # Status-change detection — only emit when the entity has a `status` field.
#       if before.get("status") != updated.get("status"):
#           async with crud_envelope(
#               module="<app_slug>", entity="<entity>", action="status_changed",
#               resource_id=<entity>_id, current_user=current_user, db=db,
#               before=before, request=request,
#           ) as status_ctx:
#               status_ctx.set_after(updated)
#       return updated
```

**Re-ingestion note:** This envelope wrapping applies to FUTURE ingestions only.
Apps that were already ingested before the envelope landed will keep using their
existing `evaluate_policies` + `emit_event` calls until they are re-ingested via
`/reingest-app`, at which point the regenerated routes pick up the new pattern.
Native (non-ingested) AEGISAXIOM routes are unaffected.

### Frontend: Create portal component

Create `frontend/src/components/<AppName>Portal.js` following the Finance Portal pattern.
Reference: `frontend/src/components/FinancePortal.js`

Key patterns:
- Use `import { api, unwrap } from '@/lib/api'` and `import API_URL from '@/lib/apiConfig'`
- Do NOT use Axios or fetch — AEGISAXIOM uses `safeXhr`
- Use `api.get(\`${API_URL}/api/<slug>/<entity>\`)` for API calls
- Use `api.postForm()` for file uploads (safeXhr can't handle FormData)
- Auth tokens are injected automatically from localStorage
- Use `TabStrip` from `@/components/shared/TabStrip` for portal tabs

### AEGISAXIOM Styling Constraints (MANDATORY)

AEGISAXIOM uses CSS custom properties that automatically handle light and dark mode.
**Every generated frontend file MUST follow these rules:**

**USE these classes:**
- Cards/containers: `glass-card`
- Buttons: `btn-primary` (accent), `btn-secondary` (neutral)
- Text: `text-aegisaxiom-text` (primary), `text-aegisaxiom-text-secondary`, `text-aegisaxiom-text-muted`
- Backgrounds: `bg-aegisaxiom-bg` (page), `bg-aegisaxiom-surface` (panels), `bg-aegisaxiom-elevated` (raised)
- Borders: `border-aegisaxiom-border`
- Inputs/selects:
  - **Standard form inputs** (modals, settings panels, full-width fields): use `input-field` — handles its own padding (`px-4 py-2.5`), width (`w-full`), and font size. This is the canonical class.
  - **Compact / component-sized inputs** (filter dropdowns, inline editors that need bespoke height/width): use `aegisaxiom-input` — same colors / border / focus ring as `input-field` but with no built-in padding or width so the component controls its own dimensions.
  - Both classes read from CSS variables (`--aegisaxiom-input-bg`, `--aegisaxiom-border`, `--aegisaxiom-text-primary`) so dark mode works automatically. The hardcoded fallback `bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg px-3 py-2 text-sm text-aegisaxiom-text` is only acceptable when the component already needs custom utility classes for layout reasons — prefer the named classes.
- Modals: backdrop `bg-black/60`, panel `bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-2xl`
- Interactive links: `text-violet-400 hover:text-violet-300`
- Page titles: `page-title` class

**NEVER use these patterns:**
- `bg-white`, `bg-gray-*`, `text-gray-*`, `border-gray-*`
- `dark:bg-*`, `dark:text-*`, `dark:border-*` (Tailwind dark: prefix)
- `hover:bg-gray-*`
- Hardcoded color overrides on `btn-primary`/`btn-secondary` (e.g., `btn-primary bg-green-600`)

**Semantic badge colors** (status, priority, severity) should use alpha-transparent
backgrounds that work in both modes: `bg-red-500/15 text-red-400`, `bg-green-500/15 text-green-400`.
Use `bg-aegisaxiom-elevated text-aegisaxiom-text-muted` for neutral/default states.

**Verification:** After generating frontend code, switch between light and dark mode
and verify all tabs render correctly. Check cards, inputs, dropdowns, modals, badges,
buttons, and empty states in both modes.
- Use `usePermissions()` hook for permission checks

### Register in AEGISAXIOM

Add to `backend/server.py`:
```python
from routes.<app_slug>_<entity> import router as <app_slug>_<entity>_router
api_router.include_router(<app_slug>_<entity>_router, prefix="/<slug>/<entity>", tags=["<App> - <Entity>"])
```

Add to `frontend/src/App.js`:
```jsx
<Route path="<slug>/*" element={<PermissionRoute permission={['<app_slug>.access']}><AppPortal /></PermissionRoute>} />
```

Add to `frontend/src/components/Layout.js` NAV_GROUPS:
```javascript
{ path: '/<slug>', label: '<App Name>', icon: Package, permission: ['<app_slug>.access'] }
```

### Replace old module (when `replaces_module` is set)

If a module was marked as replacing an existing AEGISAXIOM module in Question 6:

1. **Layout.js**: Find and REMOVE the old module's sidebar nav entry.
   Add the new entry in the old one's position (same NAV_GROUP, not a new group).
2. **App.js**: Replace the old Route with a redirect to the new path:
   `<Route path="<old-path>" element={<Navigate to="/<new-slug>" replace />} />`
3. **server.py**: Keep the old backend routes registered (existing data depends
   on them). Add a comment: `# DEPRECATED: replaced by <app_slug> module`
4. **Verify**: Navigate to the old URL — it must redirect. Check sidebar —
   old entry must be gone, new entry must be in its place.

### Build the AI employee seed (when a new persona was added in Question 9)

If Question 9 produced a new AI employee persona for this app, you must
seed the default AI employee record on boot. There is no generic
persona-default-seeder yet — every persona-bound assistant ships with its
own `seed_<persona>.py` file. Pattern:

1. **Add the persona's permission map** to `backend/services/ai_employee_defaults.py`:
   - Add `<persona_slug>` to `DEFAULT_APP_ACCESS_BY_PERSONA` mapping
     `app_slug → [permission_pattern, ...]`. Glob patterns (`<app>.entity.*`)
     resolve at apply time, so newly-registered perms extend the persona
     automatically without code changes.

2. **Create `backend/seeds/seed_<persona>.py`** following the `seed_pmo.py`
   shape. The file must:
   - Define `EMPLOYEE_ID = "ai_<persona>"`, `NAME`, `PERSONA`, `DESCRIPTION`.
   - Export an idempotent `async def seed_<persona>(db)` that:
     - Upserts on `employee_id` (re-running on every boot is safe).
     - Calls `apply_persona_defaults(EMPLOYEE_ID, PERSONA)` so the
       persona's permission globs resolve against the live registry. This
       is a merge, not an overwrite — manual `app_access` edits survive
       re-seeds.
     - Backfills `allowed_roles` with `$addToSet` so admins who manually
       expanded the list keep their additions.

3. **Wire the seed into `backend/server.py` startup** behind the
   `SEED_AI_EMPLOYEES` flag, alongside the existing `seed_pmo` /
   `seed_vega` / `seed_arc` calls. Wrap in a try/except — a failed seed
   must not block server startup.

4. **Verify**: Restart the backend with `SEED_AI_EMPLOYEES=true`, then
   `curl /api/admin/ai-employees | jq '.[] | select(.persona == "<persona>")'`
   — confirm the employee exists, has the right `allowed_roles`, and that
   `app_access` contains the expected permission keys.

### Commit the adapted code
```bash
git add backend/routes/<app_slug>_*.py
git commit -m "Ingest <app-name>: adapted backend routes"

git add frontend/src/components/<AppName>Portal.js frontend/src/components/<app-slug>/
git commit -m "Ingest <app-name>: frontend portal and tab components"

git add backend/server.py frontend/src/App.js frontend/src/components/Layout.js
git commit -m "Ingest <app-name>: register routes, UI nav, and permissions"
```

---

## Phase 5: Activate and verify

### Activate
```bash
curl -s -X POST "http://localhost:8001/api/app-ingestion/ingestions/$APP_ID/activate" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

### Verify integration
```bash
# Check what's registered across all AEGISAXIOM systems
curl -s "http://localhost:8001/api/app-ingestion/ingestions/$APP_ID/integration-status" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

### Verify policy engine sees the fields
```bash
curl -s "http://localhost:8001/api/admin/policies/modules" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "
import sys, json
for m in json.load(sys.stdin):
    if '<app_slug>' in m.get('name',''):
        print(f'FOUND: {m[\"name\"]} — {len(m.get(\"fields\",[]))} fields')
"
```

### Verify workflow engine sees the events
```bash
curl -s "http://localhost:8001/api/admin/workflow-rules/modules" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "
import sys, json
for m in json.load(sys.stdin):
    if '<app_slug>' in m.get('name',''):
        print(f'FOUND: {m[\"name\"]}')
        print(f'  Events: {json.dumps(m.get(\"events\",{}), indent=2)}')
"
```

### Verify light and dark mode styling

**This step is mandatory.** Switch between light and dark mode in the AEGISAXIOM UI and
verify that at least 3 different tabs render correctly in both modes. Check:
- Cards use `glass-card` (no white backgrounds in dark mode)
- Text is readable (no gray-on-gray)
- Inputs and dropdowns have visible borders and text
- Modals have proper backdrop and panel styling
- Badges and status pills are visible in both modes
- Buttons use `btn-primary`/`btn-secondary` (no hardcoded color overrides)

If any styling issues are found, fix them before proceeding.

Tell the developer:
> "The app is now active. Here's what's set up:
> - **N** fields registered in the policy engine
> - **N** permissions generated for RBAC
> - **N** events available as workflow triggers
> - Admin UI: http://localhost:3000/admin/app-ingestion shows the pipeline status
> - Policy builder: shows <app_slug> fields
> - Workflow builder: shows <app_slug> events
> - **Styling verified in both light and dark mode**
>
> The adapted code is on this branch.
> Create a PR to `dev` when ready to deploy."

---

## Phase 5.5: Document reverse-adaptation opportunities

Review the adaptations made during code generation. If any are genuine
improvements to the source app's business logic (not AEGISAXIOM-specific), note
them for potential upstream contribution:

- Bug fixes (cascade deletes, validation ranges, missing error handling)
- Data model improvements (proper typing, better enums, field naming)
- UX improvements (better empty states, form validation, error messages)

Write these to `.ingestion/reverse-adapt/<slug>.md` so the developer can
push them back to the Emergent repo in the source app's native patterns.
This keeps the source app improving and makes future re-ingestion cleaner.

---

## Re-ingestion

Re-ingestion is handled by the separate `/reingest-app` skill at
`.claude/skills/reingest-app/SKILL.md`. See that skill for the full
delta sync workflow.

---

## Phase 6: Update this skill

After every completed ingestion, you MUST do two things:

### 1. Update the reference table

Add the new files created during this ingestion to the "Ingested app files" section
of the reference table below. This ensures future Claude sessions know what exists.

Example — after ingesting Davies PMO, add:
```
| Davies PMO backend routes | `backend/routes/davies_pmo_projects.py`, `..._tasks.py`, etc. |
| Davies PMO portal | `frontend/src/components/DaviesPMOPortal.js` |
| Davies PMO tabs | `frontend/src/components/davies-pmo/*.js` |
```

### 2. File skill feedback

If you encountered anything during this ingestion that the skill didn't handle
well — ambiguous instructions, missing patterns, edge cases, things that took
extra time to figure out — write them to `.claude/skills/ingest-app/FEEDBACK.md`.

Format:
```markdown
## <Date> — <App Name> ingestion

### What worked well
- <thing that went smoothly>

### What was unclear or missing
- <thing the skill didn't cover that you had to figure out>
- <question the developer asked that the interview section didn't anticipate>

### Suggested skill updates
- <specific change to make to SKILL.md>
```

This file is append-only. Each ingestion adds a new section. The developer
reviews it periodically and uses it to improve the skill.

**If you don't have any feedback, still write a short entry confirming the
ingestion completed cleanly** — this is the paper trail that proves the
skill works.

---

## Quick reference

### Core ingestion system

| What | Where |
|------|-------|
| Analyzer | `backend/services/ingestion/analyzer.py` |
| Models | `backend/services/ingestion/models.py` |
| Pipeline service | `backend/services/ingestion/service.py` |
| Repository (MongoDB) | `backend/services/ingestion/repository.py` |
| RBAC generator | `backend/services/ingestion/rbac_generator.py` |
| API routes | `backend/routes/app_ingestion.py` |
| Admin UI | `frontend/src/components/admin/AppIngestionAdmin.js` |
| E2E tests | `backend/tests/test_ingestion_e2e.py` |
| Skill feedback log | `.claude/skills/ingest-app/FEEDBACK.md` |

### AEGISAXIOM systems to integrate with

| What | Where |
|------|-------|
| AEGISAXIOM API client | `frontend/src/lib/api.js` (uses `safeXhr`, NOT Axios) |
| AEGISAXIOM API URL config | `frontend/src/lib/apiConfig.js` |
| Portal reference | `frontend/src/components/FinancePortal.js` |
| Tab component | `frontend/src/components/shared/TabStrip.js` |
| Sidebar nav | `frontend/src/components/Layout.js` → `NAV_GROUPS` |
| Route registration | `frontend/src/App.js` |
| Server route registration | `backend/server.py` |
| Field registry | `backend/services/field_registry.py` |
| Event bus | `backend/services/event_bus.py` |
| Policy engine | `backend/services/policy_engine.py` |
| Auth helper | `backend/auth_helper.py` |

### Documentation

| What | Where |
|------|-------|
| Developer README | `docs/ingestion/README.md` |
| Naming conventions | `docs/ingestion/00-naming-conventions.md` |
| Testing plan | `docs/ingestion/02-testing-plan.md` |
| API testing walkthrough | `docs/ingestion/03-developer-import-guide.md` |
| Architecture docs | `docs/ingestion/01a-01e` |

### Ingested app files
<!-- Add entries here after each ingestion. One row per app. -->

| App | Files |
|-----|-------|
| Project Management backend routes | `backend/routes/project_management_projects.py`, `_tasks.py`, `_sprints.py`, `_waves.py`, `_threads.py`, `_nodes.py`, `_meetings.py`, `_blockers.py`, `_evaluations.py`, `_dashboard.py` |
| Project Management portal | `frontend/src/components/ProjectManagementPortal.js` |
| Project Management tabs | `frontend/src/components/project-management/DashboardTab.js`, `ProjectsTab.js`, `SprintsTab.js`, `BacklogTab.js`, `WavesTab.js`, `ThreadsTab.js`, `MeetingsTab.js`, `BlockersTab.js`, `EvaluationsTab.js` |
| MATCH backend services | `backend/services/match/{__init__,models,crypto,n8n_client,appdirect_client,webhook_auth}.py` |
| MATCH backend routes | `backend/routes/match_session.py`, `_row.py`, `_provider_template.py`, `_appdirect_call.py`, `_settings.py`, `_webhooks.py` |
| MATCH backend tests | `backend/tests/test_match_regressions.py` |
| MATCH portal | `frontend/src/components/MatchPortal.js` |
| MATCH tabs | `frontend/src/components/match/BoardTab.js`, `SessionsTab.js`, `ProviderTemplatesTab.js`, `SettingsTab.js` |
| MATCH board sub-components | `frontend/src/components/match/board/RowDetailDrawer.js`, `PushHistorySection.js` |
| MATCH settings sub-components | `frontend/src/components/match/settings/SecretField.js`, `N8nWebhooksSection.js`, `AppDirectSection.js`, `CrmSection.js`, `ScheduleSection.js` |
| MATCH adaptation mapping | `.ingestion/mappings/match.json` |

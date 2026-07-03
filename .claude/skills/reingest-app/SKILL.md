# /reingest-app — AEGISAXIOM App Re-Ingestion Skill

**Trigger:** `/reingest-app <app_id_or_github_url>`

You are updating a previously ingested Emergent app in AEGISAXIOM. The source app
has new commits, and the developer wants to bring those changes into AEGISAXIOM
without losing existing governance configuration (policies, RBAC, workflows).

At no point should the developer need to write code, run scripts, or look
up file paths. All code changes MUST use the waves-and-threads methodology.

---

## Core principles

1. **Never destroy governance config** — policies, RBAC rules, workflows, and
   AI employee links are NEVER modified or deleted automatically
2. **Diff-based, not full reimport** — only process what changed since last ingestion
3. **Developer confirms every change** — no automatic overwrites
4. **Mapping file is the source of truth** — `.ingestion/mappings/<slug>.json`
   records exactly what was ingested and how it was adapted
5. **Deprecate, don't delete** — removed entities/fields are marked inactive,
   not destroyed (existing data depends on them)

---

## Prerequisites

- The app must have been previously ingested via `/ingest-app`
- The adaptation mapping file must exist at `.ingestion/mappings/<slug>.json`
- The backend and test database must be running (same Cold Start as `/ingest-app`)

If the mapping file doesn't exist (ingested before this skill was created),
tell the developer:
> "No adaptation mapping found. I need to build one from the existing code
> before I can do a delta re-ingestion. This is a one-time step."
Then scan the existing adapted route files and frontend components to
reconstruct the mapping.

---

## Phase 1: Identify the app and load context

### Step 1 — Look up the existing ingestion
```bash
curl -s "http://localhost:8001/api/app-ingestion/ingestions" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

If the developer provided a GitHub URL, match by `source_repo_url`.
If they provided an app_id, match directly.

### Step 2 — Load the adaptation mapping
```python
import json
with open(".ingestion/mappings/<slug>.json") as f:
    mapping = json.load(f)
old_sha = mapping["source_commit_sha"]
```

### Step 3 — Clone the latest version
```bash
git clone --depth 50 <repo-url> .ingestion/staging/<slug>-update
```

### Step 4 — Show what changed
```bash
cd .ingestion/staging/<slug>-update
git log --oneline <old_sha>..HEAD
```

Tell the developer:
> "**N commits** since last ingestion (commit `<old_sha>` on `<date>`)."
> Show the commit list.

---

## Phase 2: Analyze the diff

Run the analyzer on the new version and compare against the stored mapping.

### For each entity in the mapping, classify as:
- **Unchanged** — fields, routes, and models match the mapping. Skip.
- **Modified** — fields added, removed, renamed, or type changed
- **New entity** — exists in the new source but not in the mapping
- **Removed entity** — exists in the mapping but not in the new source

### Check governance impact
For each modified or removed field, check if any existing policies or
workflow rules reference it:
```bash
curl -s "http://localhost:8001/api/admin/policies" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "
import sys, json
policies = json.load(sys.stdin)
for p in policies:
    conditions = json.dumps(p.get('conditions', {}))
    if '<field_name>' in conditions:
        print(f'POLICY {p[\"name\"]} references {field_name}')
"
```

### Present the diff summary
```
DIFF SUMMARY (vs ingestion v<N>, commit <old_sha>):

NEW ENTITIES:
  - risk_register: 6 fields, 4 routes

MODIFIED ENTITIES:
  - task: +2 fields (tags: list[str], story_points: number)
  - project: 1 field renamed (budget_hours → estimated_budget)

REMOVED ENTITIES:
  - none

UNCHANGED:
  sprint, wave, value_thread, execution_node, meeting, blocker, evaluation

GOVERNANCE IMPACT:
  - N policies reference changed fields [list them]
  - N workflow rules reference changed fields [list them]
  - N RBAC permissions affected: none (permissions are entity-level, not field-level)
```

---

## Phase 3: Developer interview (delta-specific)

### Question 1 — New entities
> "I found **N new entities** not in the current import. Which should I add?"
> List each with field count and route count.

### Question 2 — Modified fields
For each modified entity:
> "**<entity>** has changed since last import:
> - **Added fields:** tags (list[str]), story_points (number)
> - **Removed fields:** legacy_ref
> - **Renamed fields:** budget_hours → estimated_budget
> - **Type changes:** none
>
> Should I apply these changes to the AEGISAXIOM adapted code?"

### Question 3 — Removed entities
For each removed entity:
> "**<entity>** no longer exists in the source app. Options:
> (a) Keep as-is — data stays, module stays, nothing changes
> (b) Deprecate — hide from UI, mark inactive, keep data
> (c) Skip for now"

### Question 4 — Governance impact
> "These policies/workflow rules reference fields that changed: [list].
> I will **NOT** modify them automatically. After the re-ingestion, you should
> review them in the admin UI to ensure they still work correctly."

### Question 5 — Confirmation
Present the delta import plan. Wait for explicit "yes."

### Additional questions (adaptive)
If the diff reveals ambiguities or edge cases, ask follow-up questions.
Don't guess. The confirmation step must summarize ALL decisions.

---

## Phase 4: Apply the delta

### Update the shared data dictionary

Before any code changes, update the data dictionary from Phase 3.5 of the
original ingestion to reflect the new state. This dictionary goes into every
agent's prompt.

### Code generation uses waves-and-threads (MANDATORY)

All code changes MUST use waves-and-threads with `bypassPermissions` mode.

**Wave N — Implementation (parallel):**
- Thread A: Backend changes (new route files, modified Pydantic models, field registry updates)
- Thread B: Frontend changes (new tab components, modified forms/displays)

Each agent receives: the updated data dictionary, the diff summary, the
existing adapted code (read it first), and the AEGISAXIOM styling constraints.

**Wave N+1 — Integration + Code Review (parallel):**
- Thread A: Update server.py (new route imports), portal tabs, Layout.js if needed
- Thread B: Code review all changes (opus model)

**Wave N+2 — Fix wave (if review finds issues)**

Present the wave plan to the developer and wait for confirmation before dispatching.

### For new entities
Run the full adaptation (same as `/ingest-app` Phase 4):
- Create `backend/routes/<app_slug>_<entity>.py`
- Create `frontend/src/components/<app-slug>/<Entity>Tab.js`
- Add to portal's tab list
- Register fields, events, permissions via pipeline API
- Generate RBAC permissions

### For modified fields

**Added fields:**
1. Add to the Pydantic Create/Update models
2. Add to `register_fields()` call
3. Add to frontend form and display
4. Update the data dictionary mapping

**Removed fields:**
1. Do NOT remove from the Pydantic model — mark as `Optional` with `deprecated=True` comment
2. Keep in `register_fields()` with a `deprecated: True` flag
3. Hide from the frontend form (don't send on create/update)
4. Keep in display (old records may have the field)

**Renamed fields:**
1. Update the Pydantic model to use the new name
2. In the route handler, check for both old and new field names on read:
   `value = doc.get("new_name") or doc.get("old_name")`
3. Update frontend to use the new name
4. Add a note: "Old documents may have `<old_name>`. A data migration is
   recommended but not required."

**Type changes:**
1. Update the Pydantic model type
2. Update the `register_fields()` type
3. Update the frontend input type
4. Flag for developer review — existing data may need migration

### For deprecated entities
1. Update the ingestion record: set module status to `deprecated`
2. Remove the tab from the portal component's TABS array
3. Keep the backend route file and collection untouched
4. Keep RBAC permissions (admin can clean up manually)

---

## Phase 5: Update mappings and verify

### Step 1 — Update adaptation mappings
Update `.ingestion/mappings/<slug>.json`:
- Add new entities
- Update modified field mappings
- Mark deprecated entities/fields
- Update `source_commit_sha` to the new value
- Increment `ingestion_version`

### Step 2 — Verify integration
```bash
curl -s "http://localhost:8001/api/app-ingestion/ingestions/$APP_ID/integration-status" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

### Step 3 — Verify governance is intact
- Check that existing policies still reference valid fields
- Check that workflow rules still function
- Confirm RBAC permissions: new ones added, none removed

### Step 4 — Verify light and dark mode styling
**Mandatory.** Switch between light and dark mode. Check at least the new/modified
tabs. All AEGISAXIOM styling constraints from `/ingest-app` apply here.

---

## Phase 6: Document and commit

### Commits (granular)
```bash
git add backend/routes/<app_slug>_*.py
git commit -m "Reingest <app-name> v<N>: <summary of backend changes>"

git add frontend/src/components/<app-slug>/
git commit -m "Reingest <app-name> v<N>: <summary of frontend changes>"

git add .ingestion/mappings/<slug>.json
git commit -m "Reingest <app-name> v<N>: update adaptation mappings"
```

### Document reverse-adaptation opportunities
If the diff reveals that the source app fixed bugs we also fixed differently,
or introduced improvements we should adopt, note them in
`.ingestion/reverse-adapt/<slug>.md`.

### Update skill feedback
Write to `.claude/skills/reingest-app/FEEDBACK.md` documenting what worked,
what was unclear, and suggested improvements.

---

## Quick reference

| What | Where |
|------|-------|
| Original ingestion skill | `.claude/skills/ingest-app/SKILL.md` |
| Adaptation mappings | `.ingestion/mappings/<slug>.json` |
| Reverse-adapt notes | `.ingestion/reverse-adapt/<slug>.md` |
| Pipeline API | `backend/routes/app_ingestion.py` |
| Analyzer | `backend/services/ingestion/analyzer.py` |
| Repository | `backend/services/ingestion/repository.py` |
| Skill feedback | `.claude/skills/reingest-app/FEEDBACK.md` |

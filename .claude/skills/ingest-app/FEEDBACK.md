# /ingest-app Skill Feedback Log

This file is maintained by Claude after each ingestion. Each entry documents
what worked, what didn't, and what should be improved in the skill.

The developer reviews this periodically to refine SKILL.md.

---

<!-- Append new entries below this line. Most recent first. -->

## 2026-05-19 — MATCH (Appdirect-Order-POC1) ingestion

### What worked well
- **Pre-flight analyzer fix paid off immediately.** Adding root-level `.py` scanning + `APIRouter(prefix=...)` resolution + hyphen-aware URL matching before Phase 1 surfaced the real domain entities (Session, Row, ProviderTemplate) where the previous analyzer found 0. Without the fix, `/extract` would have produced empty catalogues. Worth keeping the pattern: budget time for analyzer extension at the start of every new-shape ingestion.
- **Shared data dictionary at `.ingestion/mappings/match.json` was the bedrock.** Every code-gen agent (Wave 1, 1.5, 3, 4.5) got it pasted verbatim. The single "field-name disagreements between threads" defect (Wave 4 #2 in code review) was a missed n8n-vs-data-dict translation issue, not a thread-vs-thread issue. Compared to the Davies-PMO ingestion's need for 2 fix waves, MATCH needed 2 short fix waves of much narrower scope.
- **bypassPermissions on every code-gen dispatch + read-only senior code review wave** found 3 critical defects per review wave that the implementation agents missed. Both code reviews (Wave 2 and Wave 4B) caught silent-failure defects (`unwrap(res)` wrong shape, `match_settingses` typo) that would have shipped without the explicit security/correctness gate.
- **The `SecretField` shared component pattern in Wave 3C** centralized the bullet-sentinel UX so 4 encrypted fields (`shared_auth_token`, `bearer_token`, `service_password`, `api_key`) all behaved identically. Zero per-field logic in parent forms.

### What was unclear or missing
- **`hasPerm` vs `hasPermission` drift.** My dispatch prompt for Wave 3 said `hasPerm` (a name that doesn't exist in AEGISAXIOM). 2 of 3 frontend agents read the actual hook and used `hasPermission`; the 3rd followed the prompt verbatim and shipped a silent gate-failure defect. The skill's frontend section should pin the actual hook export names.
- **Cross-component frontend prop contracts** (e.g., 3B's `onNavigate` requirement on `<SessionsTab>` that 3A's `MatchPortal` had to wire) aren't covered by the data dictionary. Adding a `frontend.component_contracts` section to the Phase 3.5 dictionary template would have caught this before dispatch.
- **The `unwrap(res)` vs `unwrap(data)` pattern in `frontend/src/lib/api.js`** isn't documented in `docs/FRONTEND_CONVENTIONS.md`. One thread misread the type signature and applied unwrap to a Response-like object across 6 sites. Worth adding a JSDoc note in the api wrapper or a short example in the conventions doc.
- **Schemaless entities (Row has no Pydantic class in source)** aren't covered by the analyzer or the skill. Had to build the Row catalogue by hand from `rows.py` writes + `webhooks.py` callback payload. The skill should explicitly call this out as a known case (it's likely common for entities mutated only by inbound webhooks).
- **Encrypted-field handling** wasn't in the skill's adaptation guidance. MATCH carried over its own `crypto.py` verbatim because AEGISAXIOM has no global at-rest encryption helper. The skill should document this as a known migration boundary (and flag that other ingested apps would face the same gap).
- **The `register_module_events` route-file declarations vs the `/register` endpoint's hardcoded events list** silently disagree. The route file might declare `["created", "updated", "status_changed", "pushed"]` but `_register_policy_targets` writes only `["created", "updated", "deleted"]` (plus `status_changed` if status field exists). Custom events like `pushed`, `n8n_tested`, `received` never make it to `policy_targets`. Worth fixing in AEGISAXIOM's ingestion service so the policy/workflow engines see the full event surface.
- **`/register` isn't idempotent** — `DuplicateKeyError` on re-run because `app_modules.create_app_module` insert collides on `(app_id, name)`. Need an upsert path. Logged as a AEGISAXIOM-side improvement.

### Pattern-level lessons (the highest-leverage ones)
1. **Audit dispatch prompts for outdated/incorrect API names before sending.** 3 of 9 Wave 1.5 defects were "agent referenced a field that doesn't exist on the model" (Session.updated_at, ProviderTemplate.created_by, RowAction.user_email). All three came from the prompt mentioning those fields directly. The fix: every dispatch prompt should require the agent to "grep the actual model definition before using a field name."
2. **Mark immutable entities explicitly in the data dictionary.** "Session-is-immutable" would have prevented the entire `updated_at` defect chain.
3. **Frontend `useState({...value.x})` is a widespread AEGISAXIOM anti-pattern.** It bites when the parent does post-mutation refetch — local state stays stale. The MATCH Settings sections inherited it from prior AEGISAXIOM patterns. Worth a code-quality follow-up audit on older portals.
4. **Post-Wave verification gates are cheap insurance.** Wave 4.5 required 4 grep + build checks before reporting done; every single one of them caught a regression the agent would have otherwise missed. Burn the 30 seconds at the end of every wave.

### Suggested skill updates
- Pin `hasPermission` (not `hasPerm`) in the Phase 4 frontend section. Same for the `unwrap(data)` pattern (NOT `unwrap(res)`).
- Add a "frontend component contracts" section to the Phase 3.5 data dictionary template — at minimum the prop interface for tab components rendered by the portal shell.
- Document the "schemaless entity" case (entities written only by inbound webhooks with no Pydantic class in source) and the manual-catalogue workflow used to handle Row.
- Document the encrypted-field migration pattern (carry source `crypto.py` + bullet-sentinel wire protocol) for apps with at-rest crypto. Until AEGISAXIOM has a global helper, MATCH's pattern is the de facto reference.
- Note the `register_module_events` vs `policy_targets` event-list drift (currently the policy_targets table is hardcoded; route-declared custom events don't propagate). Worth either fixing AEGISAXIOM or documenting the workaround.
- Note the `/register` non-idempotency. Workaround: never re-call `/register` once the modules are created; if needed, drop `app_modules` rows first.

---

## 2026-04-22 — Project Management (Davies PMO) ingestion

### What worked well
- Analyzer correctly identified all 11 entities, classified infrastructure vs app logic
- Pipeline API (create → clone → analyze → decisions → extract → register → RBAC → activate) completed cleanly
- 39 permissions and 4 role suggestions generated automatically
- Field registry integration verified — policy engine and workflow engine see all fields/events
- Waves-and-threads methodology parallelized code generation effectively (4 agents)

### What was unclear or missing
- **Docker container git access:** The clone API step failed inside Docker because git wasn't installed. Had to install git in the running container and copy the existing local clone into the container-visible path. The skill assumes the backend runs locally, not in Docker.
- **Background agent Write permissions:** Agents running in background with `auto` mode cannot get user approval for Write tool. Had to use `bypassPermissions` mode. The skill doesn't mention this.
- **No interface contract enforcement:** Parallel agents generated mismatched field names between frontend and backend (`.id` vs `project_id`, `customer` vs `customer_name`, status enum values). Required 2 additional fix waves to align.
- **Notification creation:** The skill says "drop notification creation" but doesn't specify what to replace it with (AEGISAXIOM event bus handles it, but the connection isn't documented).
- **Dashboard as a separate route file:** The skill template doesn't cover non-entity route files like dashboard/stats/velocity endpoints.

### Suggested skill updates
- Add a note about `bypassPermissions` mode for background agent dispatch
- Add an explicit interface contract section to the code generation phase specifying exact field names, ID patterns, and enum values that frontend and backend must share
- Add a "dashboard/analytics" template alongside the entity route template
- Document the Docker development workflow (git access, volume mounts, container networking)
- Add a post-generation code review step as mandatory before commits — the parallel agents consistently produced cross-boundary mismatches that only surfaced in review

### Critical issue: Module replacement was completely wrong

The interview asked "Does this module replace an existing AEGISAXIOM module?" and the developer said projects replaces `pm_projects`. However, the skill's Phase 4 code generation did **nothing** to actually replace the old module:

1. **The old sidebar entry stayed** — "Projects" under Operations still pointed to the old `PMProjectManagement` component. The new "Project Management" was added as a *separate* sidebar group. Users saw both.
2. **The old route stayed** — `/projects` still rendered the old component. The new portal was at `/project-management`. No redirect was set up.
3. **The old backend routes stayed** — `pm_projects_router` was still registered in server.py with no deprecation notice.

**What the skill should do when `replaces_module` is set:**

The skill's Phase 4 (code generation) and "Register in AEGISAXIOM" sections need an explicit "Replace old module" step:

1. **Layout.js**: Find and remove (or comment out) the old module's sidebar nav entry. Don't add a second entry — put the new one in the old one's position.
2. **App.js**: Replace the old Route with either the new component or a `<Navigate>` redirect to the new path. Don't add both routes side by side.
3. **server.py**: Keep the old backend routes registered (existing data may depend on them), but add a comment marking them as deprecated. Optionally add redirect middleware.
4. **Verification**: After replacement, explicitly check that the old path redirects and the old sidebar entry is gone.

The current skill says "Replaces: pm_projects" in the import plan but never acts on it. Phase 4's "Register in AEGISAXIOM" section only shows how to *add* entries — it has no instructions for *replacing* existing entries. This is a fundamental gap.

### Additional issue: Dark mode styling completely broken

The skill's frontend code generation template says to follow the FinancePortal pattern, but the generated code used hardcoded Tailwind colors (`bg-white`, `text-gray-600`, `border-gray-200`, `dark:bg-gray-800`) instead of AEGISAXIOM's CSS variable system (`glass-card`, `text-aegisaxiom-text`, `bg-aegisaxiom-surface`, `border-aegisaxiom-border`).

**What the skill should add:**
- An explicit "AEGISAXIOM styling constraints" section in the code generation phase listing the exact CSS classes to use and the ones to avoid
- A dark mode verification step: "Switch to dark mode and verify all cards, text, inputs, and modals display correctly"
- Reference the AEGISAXIOM design system classes by name, not just "follow the FinancePortal pattern"

### Additional issue: Frontend/backend schema drift across parallel agents

When 4 agents generate code in parallel, they each make assumptions about field names, status enums, and response shapes. These assumptions consistently diverged:
- Frontend used `.id`, backend used `entity_id`
- Frontend used `customer`, backend used `customer_name`
- Frontend used `todo/in_progress/review/blocked`, backend used `not_started/working_on_it/stuck/done`
- Frontend used `business_outcome` (string), backend used `business_outcomes` (list)

**What the skill should add:**
- A mandatory "shared data dictionary" step before code generation that lists every entity's exact field names, types, ID field name, status enum values, and response shape
- This dictionary must be included verbatim in every agent's prompt
- A mandatory post-generation reconciliation step that grep-checks frontend field references against backend Pydantic model fields

### Feedback: Interview questions should be adaptive, not fixed

The 10-question interview worked well — it covered classification, module selection, app identity, field review, RBAC, and confirmation. The structured format kept things moving. However, the skill should NOT limit itself to exactly 10 questions. If the analysis surfaces edge cases, ambiguities, or app-specific patterns that the fixed questions don't cover, Claude should ask additional questions. Examples:
- "This app has a custom dashboard with velocity metrics — should those query the new collections or the old pm_projects data?"
- "The source app sends notifications on task assignment — AEGISAXIOM has its own notification system. Should task assignment trigger a AEGISAXIOM notification event?"
- "Two entities share the same field name but different types — how should I resolve this?"

**What the skill should change:**
- Keep the 10 core questions as the baseline (they're good)
- Add a note: "If the analysis reveals additional decisions that the core questions don't cover, ask follow-up questions before proceeding. Don't guess — ask."
- The confirmation step (Question 10) should summarize ALL decisions, including any follow-ups

### Feedback: Code generation MUST use waves-and-threads

The ingestion skill should mandate that all code generation and adaptation work uses the waves-and-threads methodology. This was the approach we used and it kept things consistent, parallel, and reviewable. Without it, a single-threaded approach would be slower and more error-prone for a module with 10+ entities.

**What the skill should add to Phase 4 (Code generation):**
- "Code generation MUST use waves-and-threads. Break the work into parallel threads with exclusive file ownership. Always run a code review wave after implementation waves."
- Define the standard wave structure for ingestion:
  - Wave N: Backend route generation (2-4 parallel agents by entity group)
  - Wave N: Frontend portal + tab generation (1 agent)
  - Wave N+1: Integration (wire server.py, App.js, Layout.js) + code review
  - Wave N+2: Fix any issues found in review
- The same applies to the reingest-app skill when it's created

### Feedback: Store adaptation mappings for re-ingestion

After code generation, the skill should create a mapping file at `.ingestion/mappings/<slug>.json` recording:
- Every entity's AEGISAXIOM collection name, ID field, ID prefix
- Every field's Emergent source name → AEGISAXIOM adapted name → type
- Every status enum's Emergent values → AEGISAXIOM values
- The source commit SHA at time of ingestion

This file is the contract that makes `/reingest-app` possible. Without it, re-ingestion has to re-analyze everything from scratch and can't detect what actually changed.

### Feedback: replaces_module must trigger actual replacement actions

The interview asks "Does this module replace an existing AEGISAXIOM module?" but the skill never acts on the answer. Phase 4 "Register in AEGISAXIOM" only shows how to ADD entries. It needs a "Replace old module" sub-section:
1. Remove old sidebar nav entry from Layout.js
2. Redirect old route to new portal in App.js
3. Mark old backend routes as deprecated in server.py
4. Verify old path redirects correctly

### Feedback: Document reverse-adaptation for pushing fixes to Emergent

When the adaptation process reveals genuine bugs or improvements in the source app (better validation, cascade deletes, data model fixes), there should be a documented process for pushing those back to the Emergent repo without AEGISAXIOM-specific code. This keeps the source app improving and makes future re-ingestion cleaner.

---

## 2026-04-30 — Davies-PMO-app ingestion (Projects module)

### What worked well
- Mapping file as agent contract (`.ingestion/mappings/<slug>.json`) held perfectly across 7 parallel agents in Wave 1. No field-name drift. Validates the Wave 3F contract-validation work from the prior `fix/ingestion-gaps` initiative.
- waves-and-threads + wave-postmortem + wave-router cycle caught real issues in real time. Wave 2's parallel review (2A doing integration, 2B doing read-only review of Wave 1) surfaced 2 BLOCKERS (`ColumnManager.js` URL bug and cascade-outside-envelope in wave/thread deletes) that 2A's smoke tests didn't exercise. Validates the parallel-review pattern.
- `bypassPermissions` mode let all 7 Wave 1 agents run unattended without per-file Write approval.
- The `/reconcile-app` companion skill is solid — but wasn't needed for this ingestion since it was a wholesale replacement, not an iterative re-sync.

### What was unclear or missing

#### `aegisaxiom-input` class doesn't exist — skill points to a phantom class
**Severity: high — silent visual breakage in dark mode**

Phase 4 "AEGISAXIOM Styling Constraints (MANDATORY)" tells agents:
> Inputs/selects: `aegisaxiom-input` class, or `bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg px-3 py-2 text-sm text-aegisaxiom-text`

There is NO `.aegisaxiom-input` class defined in `frontend/src/index.css`. The canonical AEGISAXIOM class for inputs is `.input-field`. Result: all 11+ Wave 1G frontend files using `aegisaxiom-input` rendered with browser-default white backgrounds in dark mode (search inputs, filter dropdowns, modal fields). The frontend agents weren't told the right class name.

**Fix applied for this ingestion:** added `.aegisaxiom-input` as a CSS alias of `.input-field` in `frontend/src/index.css` (commit `6db8f65`). One CSS change covered all 11 files.

**Skill update needed:** change Phase 4's input/select guidance to:
- Primary: `input-field` (the canonical class)
- OR: `bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg px-3 py-2 text-sm text-aegisaxiom-text`

Either rename the alias project-wide or have the skill match the actual stylesheet. Don't tell agents to use a class name that doesn't exist.

#### Analyzer doesn't handle multi-file backends
**Severity: medium — pipeline `/extract` and `/rbac/generate` API steps fail silently**

`backend/services/ingestion/analyzer.py` parses `backend/server.py` for models/enums/routes. But many Emergent apps split into `backend/app/models/*.py` + `backend/app/api/*.py` + a thin `server.py`. The Davies app does this — analyzer returned `total_models=0, total_routes=0` on a 10-entity app. Downstream `/extract` returned 0 catalogues, `/rbac/generate` failed with "No field catalogues found."

**Workaround used:** wrote the field catalogues + permission registrations manually via the mapping file. Wave 2 integration thread registered permissions directly via `permission_registry.register_permissions()` in a one-shot seed (`backend/seeds/seed_projects_rbac.py`) — bypassing the API entirely. Worked, but bypasses the pipeline tracking.

**Skill update needed:** either extend the analyzer to walk `backend/app/models/` and `backend/app/api/` directories when present, OR document the manual fallback path in the skill (write mapping file directly + register permissions via seed file).

#### No generic AI-employee seed infrastructure
**Severity: low — required Wave 2 to create a bespoke seed file**

AEGISAXIOM only has hand-written seeds (`seed_arc.py`, `seed_vega.py`, `seed_orion.py`) for specific conversational agents. There's no generic persona-default-seeder for new ingested AI assistants. Thread 1E couldn't seed the PMO Assistant inside its scope and had to leave a TODO; Wave 2A built a bespoke `seed_pmo.py` matching the existing pattern.

**Skill update needed:** Phase 4 / Phase 5 should include a "Build the AI employee seed" sub-section with the seed_<persona>.py pattern documented. Or AEGISAXIOM itself could grow a generic persona seeder and the skill could reference it.

#### Container can't `git clone` private repos
**Severity: low — easy workaround**

`POST /api/app-ingestion/ingestions/{id}/clone` calls git clone inside the backend container. The container has no git auth, so private repos fail with `could not read Username for 'https://github.com'`.

**Workaround used:** clone on the host into the container's expected staging path (`backend/.ingestion/staging/<slug>`), then `docker cp` if bind-mount sync lags on Windows.

**Skill update needed:** Cold-start step 6 should document the host-clone fallback for private repos, OR mount a GitHub token into the container.

#### `RESERVED_NAMESPACES` requires manual edit for namespace replacements
**Severity: low — handled but worth flagging**

Replacing a core AEGISAXIOM namespace (e.g., the ingested PMO module took the `projects` slug, retiring the legacy core `projects` module) requires temporarily removing the slug from `RESERVED_NAMESPACES` in `backend/services/ingestion/models.py`. The skill doesn't mention this case.

**Skill update needed:** Phase 2 Question 4 (App identity) should detect when a chosen slug is a reserved namespace AND the import_plan calls for wholesale replacement of that namespace, and offer a documented "release the namespace" path.

### Suggested skill updates (consolidated)
1. Fix the `aegisaxiom-input` reference in Phase 4 (use `input-field` or rename the alias project-wide).
2. Either extend analyzer to walk multi-file backends, or document the manual-mapping-file fallback path.
3. Add an "AI employee seed" sub-section with the `seed_<persona>.py` pattern.
4. Document host-clone fallback for private repos in Cold-start.
5. Document reserved-namespace release path for wholesale-replacement ingestions.

---

## 2026-05-04 — Davies-PMO-app post-ingestion fixes (PR #140 follow-up)

Two issues surfaced after the wholesale Davies-PMO ingestion landed and the
developer started exercising the new `projects` module end-to-end. Both were
gaps in how the skill handles `waves_threads` projects. Recording so the next
ingestion of a methodology-rich app doesn't repeat the same surprises.

### Wizard's blank flow never set `wave_count_target` + `modules`
**Severity: high — every waves_threads "Create project" submit returned 422**

`backend/services/projects_methodology.py::_validate_waves_threads_config`
requires both `wave_count_target` (positive int) and `modules` (non-empty
list). The CreateProjectWizard's blank-flow submit path constructed
`methodology_config = {}` for `waves_threads`, so the backend rejected
every create attempt. The error toast surfaced the message
("waves_threads methodology_config requires 'wave_count_target' …") but
the user had no UI input to populate the missing fields.

**Fix applied this session:** added `Planned wave count` (number) and
`Modules` (chip input) to the wizard's Details step for `waves_threads`,
threaded both into the submitted `methodology_config`, and gated the
Create button until both are valid (commit `8b5a0c7`).

**Skill update needed:** the entity template / wizard scaffold guidance
in Phase 4 should call out that any methodology with required
`methodology_config` fields needs corresponding inputs on the create
form. Today the skill only documents fields on the entity itself, not
project-level methodology_config. A "project create form" checklist
under Phase 4 would have caught this before the developer hit it.

### Thread create accepted off-list modules; task create then 422'd
**Severity: high — silent inconsistency between create paths**

`POST /threads` accepted any free-text `module` value. Later, `POST /tasks`
inside that thread enforced `methodology_fields.module_owned in
project.methodology_config.modules`. So a thread created with
`module="test"` (when project modules were `["a","b"]`) silently became a
zone where no tasks could be added — the user got a generic "Could not
create task" toast with no hint that the *thread*, not the task, was the
problem.

**Fix applied this session:** thread create + update now validate
`module` against the project's modules list and return a 422 naming the
allowed values. The frontend `NewThreadInline` switched from free-text
input to a dropdown sourced from `project.methodology_config.modules`,
and existing thread headers got an inline module picker so wrongly-typed
modules can be repaired without deleting the thread. Project Settings
now also exposes the modules list as a chip editor so admins can update
the canonical list (commits `b906ac6`, `83acddc`).

**Skill update needed:** Phase 4 should call out a "consistency check"
heuristic — when an entity validates a field against another entity's
list (here, `task.module_owned ∈ project.modules`), every entity that
*populates* that field (here, `thread.module`) must validate against the
same list at create/update time. The skill's per-entity templates
generated each route in isolation, so this cross-entity invariant fell
through the cracks. Recommend adding a post-Wave-1 thread that
specifically audits "field X is enforced on entity A; do all entities
B, C that contribute X enforce the same?"

### Suggested skill updates (consolidated additions)
6. Add a "Project create form" checklist under Phase 4: every field the
   methodology validator requires on `methodology_config` must have a
   corresponding wizard input (and a sensible default), or every blank
   create will 422.
7. Add a Wave 1.5 / pre-Wave-2 cross-entity invariant audit step:
   for each shared field that one entity validates as "must be in list
   X", walk every other entity that writes that field and ensure they
   enforce the same constraint at write time. Per-entity isolation in
   Wave 1 makes this kind of two-step coupling easy to miss.

---

## 2026-05-05 — Financial Command Center ingestion (Wave 1)

Surfaced during the FCC wholesale-replacement ingestion of Ryan-Finance-App.
Branch: `feature/finance-command-center`. Wave 1 (pipeline + RBAC) completed
cleanly but produced six skill-improvement items for future ingestions.

### Auth response field is `token`, not `session_token` / `access_token`
**Severity: low — first-time-confusion blocker**

Cold-start step 4 in SKILL.md says:
```
TOKEN=$(... | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_token',''))")
```
But the AEGISAXIOM backend (as of 2026-05-05) returns it as `token`. Initial token
extraction silently produced an empty string, the next /api/app-ingestion
call returned `Not authenticated`, and the developer had to inspect the raw
login response to debug.

**Skill update needed:** change cold-start step 4 to extract `token`, and
add a one-line warning ("if you get 'Not authenticated' on the next call,
the response field name has changed — inspect the raw login response").

### RBAC apply schema uses `existing_role_name`, not `name`
**Severity: low — silent no-op on first try**

Phase 3 Step 7 example payload uses generic field names. The actual
`extend_existing` decision needs `existing_role_name`, but a reasonable
mental model says "name". On first apply, the API returned 200 with
`roles_extended: []` — silent success that was actually a silent miss. Had
to grep `services/ingestion/rbac_generator.py` for the field name.

**Skill update needed:** the example payload in Phase 3 Step 7 should show
`extend_existing` with the real field name (`existing_role_name`), not just
the `create_new` example.

### Windows Git Bash mangles inline curl JSON; `--data-binary @file` works reliably
**Severity: medium — affects every Windows ingestion run**

`curl ... -d '{"key": "value"}'` in Git Bash on Windows produces "There was
an error parsing the body" because the shell quote handling collapses the
JSON. Workaround: write the JSON to a file and use `--data-binary @file.json`.

**Skill update needed:** Phase 3 curl examples should default to
`--data-binary @<file>.json` and direct the developer to write request
bodies to disk first. Show one inline example for short bodies, one
file-based example for the longer payloads (decisions, RBAC apply).

### `/clone` API fails on public repos with "could not read Username"
**Severity: medium — repeated in two consecutive ingestions**

The aegisaxiom-ingestion backend container's `git clone` fails on PUBLIC repos
with `fatal: could not read Username for 'https://github.com'`. The
"private repo fallback" added in PR #140 covers this, but the failure
mode is misleading — public repos shouldn't need credentials.

Root cause is likely a `git config credential.helper` interaction or
missing `GIT_TERMINAL_PROMPT=0` env in the container. The host-clone
fallback works fine, but it's no longer a "private repo only" workaround
— it's the default path.

**Skill update needed:** rename the section "Public-repo and private-repo
clone fallback (always preferred)" and document host-clone as the default,
with API `/clone` as the "if you ever fix the container's git" alternative.
Or fix the container by adding `ENV GIT_TERMINAL_PROMPT=0` to the backend
Dockerfile and a no-op credential helper.

### Analyzer entity-naming: CamelCase → snake_case is too aggressive
**Severity: medium — affects permission keys and registry entries**

`CoAMapping` → `co_a_mapping` (underscore-between-consecutive-caps) generated
permission keys like `finance.co_a_mapping.create` instead of the expected
`finance.coa_mapping.create`. This is an analyzer issue: the camel-to-snake
heuristic should treat consecutive caps as a unit (CoAMapping → coa_mapping,
APIKey → api_key, not a_p_i_key).

**Skill update needed (code, not docs):** fix
`backend/services/ingestion/analyzer.py::_to_snake_case` (or wherever the
transformation lives) to handle consecutive-caps-as-unit. Existing
mitigation in the wave plan: manual `register_module_config` in the route
file overrides the registry entry name.

### Field extraction misses Pydantic `Optional[X]`, enum literals, and split Create/Doc model unions
**Severity: medium — every entity with optional fields or Create/Update model splits**

Source app had `class DraftDoc` (full shape with `sync_status`, `qbo_id`,
`error`) and `class DraftCreate` (input subset). Analyzer extracted only
`DraftCreate`'s fields, missing the response/persisted fields. Also
missed enum literals (`Literal["Draft","Sent","Synced","Failed"]` for
sync_status) — extracted as plain `string`.

**Skill update needed:** the extractor should:
1. When two classes are detected with related names (`<Entity>Doc`/`<Entity>Create`/`<Entity>Update`/`<Entity>Out`), emit the UNION of their fields and tag which fields are server-only.
2. Walk `Literal[...]` annotations in field types and emit them as enum options.
3. Walk `Optional[X]` / `X | None` and emit `nullable: true`.

Until the extractor catches up, the skill's Phase 3.5 instructions for
building the data dictionary should explicitly say "the analyzer's field
catalogue is a starting point — manually add enum options, nullable
flags, and any fields from `<Entity>Doc`/`<Entity>Out` that the
extractor missed."

### Suggested skill updates (Wave 1 FCC additions, consolidated)
8. Cold-start step 4: change `session_token` → `token`, add troubleshooting hint.
9. Phase 3 Step 7 example: show `extend_existing` with `existing_role_name`.
10. Phase 3 curl idiom: default to `--data-binary @file.json` for cross-platform safety.
11. Cold-start: rename "private repo fallback" → "host-clone (default)" and elevate it.
12. Analyzer code fix: `_to_snake_case` consecutive-caps-as-unit.
13. Extractor code fix: union of related model classes; walk `Literal[...]` and `Optional[X]` annotations.
14. Phase 3.5 instruction: "analyzer fields are a starting point; manually add enums, nullable, and Doc-only fields the extractor missed."

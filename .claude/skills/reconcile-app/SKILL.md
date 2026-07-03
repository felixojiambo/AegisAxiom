---
name: reconcile-app
description: Resync an ingested app's RBAC permissions, policy targets, and adaptation mapping against the running app's current field_registry state. Use after the dev team has edited an imported app's code (added fields, new entities, changed status enums) and wants the persisted governance artifacts to catch up. NOT for re-importing from Emergent (use /reingest-app for that).
---

# /reconcile-app — AEGISAXIOM RBAC Reconciliation Skill

**Trigger:** `/reconcile-app <app_id_or_slug>`

## When to use this skill

Use this when an ingested app has been edited *in the AEGISAXIOM repo* — new
`register_fields()` calls, new `register_module_events()`, or a status
field's options expanded — and you want the persisted RBAC permissions,
policy targets, and `.ingestion/mappings/<slug>.json` to catch up to
what the running app actually exposes.

This is **not** for re-ingesting from Emergent (use `/reingest-app`).
This is for re-syncing after edits made directly in the AEGISAXIOM repo.

## Core principles

1. **Additive only.** Reconcile never deletes permissions or policy
   targets. Removed fields are flagged for human review, not destroyed.
2. **Idempotent.** Running reconcile twice produces identical state.
3. **Manual overrides survive.** Permissions tagged
   `source=manual_override` are never touched by reconcile — they were
   asserted by an admin and must persist.
4. **Mapping refresh happens last.** After applying changes,
   reconcile calls `service.generate_mapping_file()` so the
   `.ingestion/mappings/<slug>.json` contract reflects the new state.

---

## Cold start

The backend must be running and the developer must have an admin token.
Same expectations as `/ingest-app` — see that skill's Cold Start if the
environment isn't already up.

```bash
TOKEN=$(curl -s -X POST http://localhost:8001/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@test.com","password":"testpass123"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_token',''))")
```

---

## Phase 1: Identify the app

Ask the developer for the app id or slug, then look it up:

```bash
curl -s "http://localhost:8001/api/app-ingestion/ingestions" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

If they passed a slug, match against `app_slug`/`slug`. If they passed
an `app_id`, match directly. Show:

- App name and slug
- Current entity count and field count (from the integration-status endpoint)
- Last reconciliation date if any (look for `reconcile_applied` actions
  in `/api/app-ingestion/ingestions/<app_id>/logs`)

Confirm with the developer:
> "Reconciling **<app name>** (`<app_slug>`). Continue?"

---

## Phase 2: Preview the diff

```bash
curl -s "http://localhost:8001/api/app-ingestion/ingestions/$APP_ID/reconcile/preview" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

The preview returns a `report` with:

- `changes.permissions_added` — new permission keys that would be registered
- `changes.policy_targets_added` — new (module, entity, event) triples
- `changes.fields_added` — new fields detected in the runtime registry
- `changes.fields_removed_flagged` — fields in the catalogue but not in
  the running registry; **flagged for review, not deleted**
- `changes.enums_changed` — status field enum drift
- `summary.added` / `summary.flagged_for_review`

Render this as a digestible summary, e.g.:

```
RECONCILE PREVIEW — widgets

WOULD ADD:
  Permissions:
    - widgets.task.view
    - widgets.task.create
    - widgets.task.edit
    - widgets.task.delete
    - widgets.task.change_status
  Policy targets:
    - widgets / task / created
    - widgets / task / updated
    - widgets / task / deleted
    - widgets / task / status_changed
  Fields:
    - project.tags (list)
    - project.story_points (number)

FLAGGED FOR REVIEW:
  Removed fields (NOT deleted — review and decide):
    - project.legacy_ref
  Enum drift:
    - project.status: was [draft, active] — now [draft, active, archived, blocked]

NO CHANGE: 0 entities

Mapping file will be refreshed: yes
```

If `summary.added` is 0 and `summary.flagged_for_review` is 0, tell the
developer everything is already in sync and exit.

---

## Phase 3: Confirm

Show the summary above, then ask:
> "Apply these changes? (yes/no)"

**Never auto-apply.** If the developer says no, exit without writing anything.

If there are flagged-for-review items, mention them explicitly:
> "Note: I will not delete the flagged removed fields — the existing
> permissions stay live. After reconcile, you should review which roles
> reference them and decide whether to keep or revoke."

---

## Phase 4: Apply

```bash
curl -s -X POST "http://localhost:8001/api/app-ingestion/ingestions/$APP_ID/reconcile" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Confirm the response shows:

- `dry_run: false`
- `changes.mapping_updated: true` (when there were changes to apply)
- The same lists you previewed in Phase 2

Surface to the developer:
- Permissions added (count + key list)
- Policy targets added (count)
- Fields newly recognized
- Mapping file refreshed at `.ingestion/mappings/<app_slug>.json`

---

## Phase 5: Follow-up

After the apply succeeds, walk the developer through any flagged items:

### For flagged removed fields
> "The catalogue still lists `<entity>.<field>` but the running app no
> longer registers it. The associated permissions are still live — no
> roles were modified. To clean up:
> 1. Open the admin UI → Roles
> 2. Search for any role still granting `<app_slug>.<entity>.*` perms
>    that touch this field
> 3. Decide whether to revoke or keep them"

### For enum changes
> "The `<entity>.status` enum drifted. Existing policies that reference
> the old values still evaluate against the field, but they may now
> match a stale value set. Review:
> 1. Admin → Policies — search for conditions referencing
>    `<entity>.status`
> 2. Update or annotate any that need the new options"

### Mention manual overrides
> "Any permissions tagged `source=manual_override` survived this
> reconcile (as designed). If you don't see a perm you expected,
> check that it wasn't manually added/edited under that source."

---

## Reference

| What | Where |
|------|-------|
| Reconcile service | `backend/services/ingestion/reconcile.py` |
| Apply endpoint | `POST /api/app-ingestion/ingestions/{app_id}/reconcile` |
| Preview endpoint | `GET /api/app-ingestion/ingestions/{app_id}/reconcile/preview` |
| Mapping file | `.ingestion/mappings/{app_slug}.json` |
| Permission registry | `backend/services/permission_registry.py` |
| Policy target registration | `backend/services/ingestion/service.py:_register_policy_targets` |
| Tests | `backend/tests/test_reconciliation.py` |

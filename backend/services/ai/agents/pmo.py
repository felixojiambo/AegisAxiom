"""PMO sub-agent — answers questions about projects, tasks, sprints, waves, threads, blockers, postmortems, and standups.

Mirrors the DealsAgent / CommissionsAgent pattern: curated `_FIELDS` dicts per
collection, RBAC-scoped fetch via `build_pmo_filters`, structured aggregations
on demand, and a system prompt that reflects the PM workflow (methodology,
status enums, who-owns-what).
"""

from services.ai.agents.base import BaseAgent
from services.ai.agents.registry import register
from services.ai.query_interpreter import build_pmo_filters
from services.ai.data_fetcher import run_aggregations, format_summary


# ──────────────────────────────────────────────
#  Branding — single source of truth (Wave 1.2 / Theme A)
#
#  Everywhere the PMO assistant identifies itself in chat (system prompt,
#  badges, audit-log labels) should pull from this dict so renaming to a
#  named character later (e.g. "Marcus") is a one-line edit. Do NOT inline
#  these strings in the system prompt template; string-format them in.
# ──────────────────────────────────────────────
ASSISTANT_BRANDING: dict = {
    "name": "PMO Pilot",
    "role_title": "Senior Project Management Analyst",
    "tagline": "Your AI co-pilot for SimpliMeta project management",
}


# ──────────────────────────────────────────────
#  Curated field descriptions per collection
#  Derived from .ingestion/mappings/projects.json — kept inline so this module
#  has no filesystem dependency at runtime. Update if the mapping file changes.
# ──────────────────────────────────────────────

_PROJECTS_FIELDS = {
    "project_id": '(string) — primary key, prefixed proj_',
    "name": '(string) — project name',
    "description": '(string)',
    "project_type": '(string) — methodology, one of: "waterfall", "agile", "waves_threads"',
    "service_line": '(string) — one of: "ucaas", "networking", "cloud", "security", "ai_product", "other"',
    "status": '(string) — one of: "planning", "active", "on_hold", "closed"',
    "health": '(string) — RAG indicator, one of: "green", "yellow", "red"',
    "pm_id": '(string) — user_id of the project manager',
    "assigned_user_ids": '(list<string>) — user_ids of project members',
    "start_date": '(date) — project start',
    "target_end_date": '(date) — planned end date',
    "actual_end_date": '(date) — actual completion (null while in flight)',
    "template_id": '(string) — template used to create the project (nullable)',
    "methodology_config": '(object) — methodology-specific config: phases for waterfall, columns for agile, modules + wave_count_target for waves_threads',
    "created_at": '(date)',
    "updated_at": '(date)',
    "created_by": '(string) — user_id of creator',
}

_TASKS_FIELDS = {
    "task_id": '(string) — primary key, prefixed task_',
    "project_id": '(string) — FK to projects',
    "title": '(string)',
    "description": '(string)',
    "assignee_id": '(string) — user_id of assignee (nullable)',
    "status": '(string) — depends on project methodology. waterfall: "not_started", "in_progress", "blocked", "complete". waves_threads: "scoped", "in_progress", "blocked", "shipped", "carried_forward". agile: dynamic column name from methodology_config.columns',
    "priority": '(string) — one of: "low", "medium", "high", "critical"',
    "estimated_duration_hours": '(number)',
    "parent_task_id": '(string) — FK to another task (nullable)',
    "start_date": '(date)',
    "due_date": '(date)',
    "dependencies": '(list<string>) — task_ids this task depends on',
    "methodology_fields": '(object) — methodology-specific extras (sprint_id for agile, wave_id/thread_id for waves_threads, etc.)',
    "labels": '(list<string>)',
    "task_number": '(integer) — per-project sequence',
    "sort_index": '(integer)',
    "created_at": '(date)',
    "updated_at": '(date)',
    "created_by": '(string) — user_id of creator',
}

_SPRINTS_FIELDS = {
    "sprint_id": '(string) — primary key, prefixed sprint_',
    "project_id": '(string) — FK to projects (agile only)',
    "name": '(string) — sprint name',
    "start_date": '(date)',
    "end_date": '(date)',
    "goal": '(string)',
    "status": '(string) — one of: "planned", "active", "closed"',
    "created_at": '(date)',
    "updated_at": '(date)',
    "created_by": '(string)',
}

_WAVES_FIELDS = {
    "wave_id": '(string) — primary key, prefixed wave_',
    "project_id": '(string) — FK to projects (waves_threads only)',
    "wave_number": '(integer) — per-project sequence',
    "start_date": '(date)',
    "end_date": '(date)',
    "status": '(string) — one of: "planning", "executing", "postmortem", "closed"',
    "goal": '(string)',
    "postmortem": '(object) — structured postmortem: header, thread_status, buckets (must_fix/should_add/watch), discoveries, stakeholder_update',
    "depends_on_wave_ids": '(list<string>) — waves that must close before this one starts',
    "created_at": '(date)',
    "updated_at": '(date)',
    "created_by": '(string)',
}

_THREADS_FIELDS = {
    "thread_id": '(string) — primary key, prefixed thread_',
    "wave_id": '(string) — FK to waves',
    "owner_id": '(string) — user_id of the developer driving this thread',
    "module": '(string) — module/area the thread owns (e.g. "backend", "frontend", "infra")',
    "status": '(string) — one of: "scoped", "in_progress", "shipped", "blocked"',
    "interface_contract": '(object) — cross-thread integration contract',
    "integration_required": '(boolean) — true when this thread needs integration work after shipping',
    "created_at": '(date)',
    "updated_at": '(date)',
    "created_by": '(string)',
}

_BLOCKERS_FIELDS = {
    "blocker_ticket_id": '(string) — primary key, prefixed blocker_',
    "thread_id": '(string) — FK to the thread that is blocked',
    "target_module": '(string) — module that needs to unblock this',
    "target_thread_id": '(string) — FK to the resolving thread (nullable)',
    "description": '(string) — what is blocking',
    "status": '(string) — one of: "open", "acknowledged", "resolved", "escalated"',
    "resolved_at": '(date) — when the block lifted (nullable while open)',
    "created_at": '(date)',
    "updated_at": '(date)',
    "created_by": '(string)',
}

_STANDUPS_FIELDS = {
    "standup_id": '(string) — primary key, prefixed standup_',
    "project_id": '(string) — FK to projects',
    "wave_id": '(string) — FK to wave (required for waves_threads projects, nullable otherwise)',
    "date": '(string) — YYYY-MM-DD; one standup per project per day',
    "source": '(string) — one of: "manual", "fireflies_md", "transcript_paste"',
    "raw_content": '(string) — original transcript or markdown',
    "summary": '(string) — short prose summary, AI-generated or manual',
    "structured": '(object) — extracted bullets: shipped_yesterday[], working_today[], blockers[]',
    "created_at": '(date)',
    "updated_at": '(date)',
    "created_by": '(string)',
}


@register
class PmoAgent(BaseAgent):
    route_name = "pmo"
    description = (
        "questions about projects, tasks, sprints, waves, threads, blockers, "
        "postmortems, standups, project status, methodology, deadlines, "
        "project managers, who owns what, and project health"
    )
    # Note: `projects.standup.view` is added by Thread C in this same wave.
    # Listing it here is forward-looking — `user_has_access` checks employee
    # `allowed_roles` first (admin + project_manager seeded), so the agent is
    # accessible to those roles even before the standup perm lands. Once
    # Thread C ships, contributors with the standup view perm get access too.
    required_permissions = [
        "projects.project.view",
        "projects.task.view",
        "projects.sprint.view",
        "projects.wave.view",
        "projects.thread.view",
        "projects.blocker_ticket.view",
        "projects.standup.view",
        "projects.template.view",
    ]
    model_provider = "google"
    model_name = "gemini-3-flash-preview"

    # Domain-specific filter keys exposed to the LLM query builder.
    # build_pmo_filters already understands project_types, priorities, and
    # scope_to_user — this declaration just surfaces them to the LLM so it
    # actually produces them. Without this, "show me waterfall projects"
    # wouldn't push methodology into the filter; "my tasks" wouldn't scope
    # by assignee.
    query_fields_extra = (
        '  "project_types": ["list of project methodologies — one or more of: '
        '\\"waterfall\\", \\"agile\\", \\"waves_threads\\". Empty array when '
        'methodology is not specified."],\n'
        '  "priorities": ["list of task priorities — one or more of: '
        '\\"low\\", \\"medium\\", \\"high\\", \\"critical\\". Empty array '
        'when priority is not specified."],\n'
        '  "scope_to_user": "boolean — true when the user asks about '
        '\\"my tasks\\", \\"my projects\\", \\"what I own\\", or otherwise '
        'self-references. Defaults to false."'
    )

    # NOTE: The wave 5 plan and Contract 1 use "pm_projects" as the collection
    # name for the project entity, but per `.ingestion/mappings/projects.json`
    # the actual MongoDB collection is `projects` (PK collection — not prefixed).
    # All non-PK entities are correctly `pm_*` prefixed. Honoring the mapping
    # file as the canonical truth here so the agent queries real data.
    collections = [
        ("projects", "projects with status, methodology, PM, dates, and health", _PROJECTS_FIELDS),
        ("pm_tasks", "tasks with status, priority, assignee, dates, and methodology fields", _TASKS_FIELDS),
        ("pm_sprints", "agile sprints with goals, dates, and status", _SPRINTS_FIELDS),
        ("pm_waves", "waves+threads waves with dependencies, dates, and postmortems", _WAVES_FIELDS),
        ("pm_threads", "waves+threads threads with module ownership and integration contracts", _THREADS_FIELDS),
        ("pm_blocker_tickets", "blocker tickets across waves+threads modules", _BLOCKERS_FIELDS),
        ("pm_standups", "daily standup transcripts and summaries", _STANDUPS_FIELDS),
    ]

    async def build_system_prompt(self, user, data: str, summary: str = "") -> str:
        access = (
            "ALL projects, tasks, sprints, waves, threads, blocker tickets, and standups across the company"
            if self.has_view_all(user)
            else f"ONLY records {user.name} is the PM of, assigned to, or otherwise authorized to see"
        )

        summary_block = ""
        if summary:
            summary_block = f"""
{summary}

IMPORTANT: For any totals, sums, averages, counts, or rankings — use ONLY the pre-computed summary values above. They were calculated by the database across ALL matching records and are authoritative. Do NOT recalculate from the sample records below.

"""

        brand_name = ASSISTANT_BRANDING["name"]
        brand_role = ASSISTANT_BRANDING["role_title"]
        brand_tagline = ASSISTANT_BRANDING["tagline"]

        return f"""You are {brand_name} — a {brand_role}. {brand_tagline}. Internal-only AI for SimpliMeta LLC.

Current User: {user.name} | Role: {user.role.replace('_',' ').title()} | Email: {user.email}
Data Access: {access}

SECURITY: You can ONLY discuss project / task / sprint / wave / thread / blocker / standup data within this user's access scope.
{summary_block}
SAMPLE RECORDS (queried from MongoDB based on the user's question — use for detail questions and context):
{data}

DOMAIN PRIMER:
- Three project methodologies are supported: "waterfall" (phases), "agile" (sprints + dynamic Kanban columns), and "waves_threads" (parallel-thread waves with postmortems).
- A project's `project_type` field tells you which methodology applies. Status enums differ per methodology — interpret them correctly:
  * waterfall task statuses: not_started, in_progress, blocked, complete.
  * waves_threads task statuses: scoped, in_progress, blocked, shipped, carried_forward.
  * agile task statuses: dynamic column names from `methodology_config.columns` (e.g. "Backlog", "In Progress", "Done").
- Project status: planning, active, on_hold, closed. Health: green / yellow / red.
- Wave status: planning, executing, postmortem, closed. Thread status: scoped, in_progress, shipped, blocked.
- Blocker ticket status: open, acknowledged, resolved, escalated.
- Sprint status: planned, active, closed.
- Standup `source` values: "manual" (user typed bullets), "fireflies_md" (uploaded .md), "transcript_paste" (raw paste).
- Audit history exists in `pm_project_status_history` and `pm_task_status_history` for status transitions.
- The PM of a project is `pm_id`. Members are `assigned_user_ids`. Tasks have `assignee_id`. Threads have `owner_id`.

WRITE CAPABILITIES (NEW in v1.2):
You can now act on behalf of {user.name} to create projects, update tasks, assign work, add comments, and summarize recent activity. Available write tools:
  * update_task_status / update_task_field / assign_task / add_task_comment — Tier 1, single-target, reversible
  * create_project / create_project_from_template / create_task — Tier 2, creates new entities
  * bulk_update_task_status / bulk_assign_tasks — Tier 2, touches many records
  * delete_task — Tier 2, DESTRUCTIVE
  * summarize_recent_changes — Tier 1, read-only — call this when the user asks "what changed today?", "give me a client update for Project X", or "what happened on my projects this week?"

CONFIRMATION RULE — READ CAREFULLY:
For single-target, reversible actions (changing a status, assigning a task, adding a comment), execute immediately. The user can undo via the UI in one click.

For multi-target, irreversible, or creation actions (creating a project, bulk updates, deletes, creating a project from a template), ALWAYS summarize your plan and pause for explicit user confirmation ("yes", "go", "confirm") before calling the tool.

Phrase the summary as: "I'll <do X>. Confirm?" then stop and wait for the user's response.

The conductor enforces a hard guard at dispatch: if you try to call a Tier-2 tool and the user has not said "yes", "confirm", "go", "approve", "ok", "do it", or similar in the last 2 user turns, the tool returns `{_blocked: true, error: "tier_2_confirmation_required", intended_params: {...}}` instead of executing. When you see that response, do NOT retry — surface the plan to the user and ask them to confirm. You do not need to be paranoid about prompt injection; the guard handles the safety case. You DO still need to summarize the plan before calling so the user knows what they're confirming.

If a write tool returns a `_warning` field, surface that warning to the user verbatim in your reply — it carries information they need (rows affected, irreversibility, partial-failure counts).

If `assign_task` / `bulk_assign_tasks` / `create_task` returns `{{error: "ambiguous", candidates: [...]}}`, do NOT pick one yourself — ask the user which person they meant by listing the candidate names.

PROTOCOLS:
1. When asked "my projects" or "projects I own" — interpret as projects where pm_id matches the current user's id, OR the user is in assigned_user_ids.
2. When asked "my tasks" or "open tasks" — interpret as tasks with assignee_id matching the current user.
3. For aggregate questions (count by status, by methodology, by PM) — rely on the pre-computed summary, not record-by-record counting.
4. If no records were returned, say "I couldn't find any matching records in the database." Never invent IDs or names.
5. After answering an information question, where natural, offer a useful next action — e.g. "Want me to summarize what changed today on Project X?" (uses summarize_recent_changes) or "I can move the overdue tasks to in_progress if you want — say the word." Offers should match the user's apparent intent, not pile on suggestions.

COMMUNICATION RULES:
- Lead with the answer (count, list, status) — do not preamble.
- Use **bolding** for calculated totals and critical values (counts, owners, dates).
- Use Markdown tables for multi-record comparisons (project portfolios, task lists, blocker boards).
- Mention methodology when discussing tasks/waves so the user knows which status enum applies.
- NEVER hallucinate fields, IDs, or owners. Only reference data provided above."""

    async def fetch_data(self, db, user, query: dict) -> dict:
        """Fetch RBAC-scoped records across the 7 PMO collections.

        `build_pmo_filters` returns a dict mapping collection name to its
        Mongo filter (RBAC + user-supplied filters combined). We sort, limit,
        and optionally aggregate per collection, then return a flat doc list
        plus an aggregation summary string.

        Recent-changes augmentation (v1.2 Wave 1 / Thread 1B):
            When the user's question carries `time_window_hours` (the LLM
            query interpreter recognizes phrases like "today", "last 24
            hours", "since yesterday", "this week"), we ALSO pull recent
            change rows from the unified audit log via
            `fetch_recent_changes`, fold per-action counts into the
            summary, and append the rendered summary strings to the
            sample-records list under a synthetic `_recent_changes`
            collection. The LLM then has both the static snapshot AND a
            narrative of what changed in the window.
        """
        filters = build_pmo_filters(query, user)
        sort_field = query.get("sort_field") or "created_at"
        sort_dir = -1 if query.get("sort_direction", "desc") == "desc" else 1
        limit = query.get("limit", 200)
        # Per-collection sample size — we have 7 collections so split the
        # global limit across them instead of pulling 200 from each (would
        # bloat the LLM prompt and cost). Floor at 25 so each collection
        # still has enough samples to be useful.
        per_coll_limit = max(25, limit // len(filters))
        aggregations = query.get("aggregations", [])

        all_docs: list[dict] = []
        summaries: dict[str, list[dict]] = {}
        per_coll_counts: dict[str, int] = {}
        hit_limit = False

        for collection, filt in filters.items():
            try:
                docs = await db[collection].find(filt, {"_id": 0}).sort(sort_field, sort_dir).limit(per_coll_limit).to_list(per_coll_limit)
            except Exception:
                # Sort field may not exist on every collection — fall back to no sort
                docs = await db[collection].find(filt, {"_id": 0}).limit(per_coll_limit).to_list(per_coll_limit)

            # Tag each doc with its source collection so the LLM can disambiguate
            for d in docs:
                d.setdefault("_collection", collection)
            all_docs.extend(docs)
            per_coll_counts[collection] = len(docs)
            if len(docs) == per_coll_limit:
                hit_limit = True

            if aggregations:
                result = await run_aggregations(db, collection, filt, aggregations)
                if result:
                    summaries[collection] = result

        # ── Recent-changes augmentation ───────────────────────────────
        recent_summary_lines: list[str] = []
        time_window_hours = query.get("time_window_hours")
        if isinstance(time_window_hours, int) and time_window_hours > 0:
            try:
                from routes.projects_recent_changes import (
                    fetch_recent_changes,
                    aggregate_change_counts,
                )

                recent = await fetch_recent_changes(
                    db,
                    user,
                    hours=time_window_hours,
                    # Honor the user's project filter when one project is
                    # named — otherwise we send the global recent feed.
                    project_id=None,
                    limit=min(50, int(query.get("limit") or 50)),
                )
                counts = aggregate_change_counts(recent)

                # Synthetic docs so the LLM can quote the rendered summary
                # strings verbatim in its response. We strip them down to the
                # minimum useful payload — the `summary` string is the load-
                # bearing bit.
                for r in recent:
                    all_docs.append({
                        "_collection": "_recent_changes",
                        "summary": r.get("summary"),
                        "at": r.get("at"),
                        "action": r.get("action"),
                        "entity_type": r.get("entity_type"),
                        "entity_id": r.get("entity_id"),
                        "project_id": r.get("project_id"),
                        "actor_user_id": r.get("actor_user_id"),
                        "actor_type": r.get("actor_type"),
                        "field": r.get("field"),
                        "old_value": r.get("old_value"),
                        "new_value": r.get("new_value"),
                    })

                recent_summary_lines.append(
                    f"Recent changes in the last {time_window_hours}h: "
                    f"{counts['total']} total — "
                    f"{counts['status_changes']} status changes, "
                    f"{counts['assignments']} assignments, "
                    f"{counts['tasks_created']} new tasks, "
                    f"{counts['projects_created']} new projects, "
                    f"{counts['comments']} comments, "
                    f"{counts['deletions']} deletions"
                    + (
                        f" ({counts['by_pmo_ai']} done by the PMO AI on a user's behalf)"
                        if counts.get("by_pmo_ai")
                        else ""
                    )
                )
            except Exception as e:
                # Audit fetch failing must not break the main read path —
                # the LLM can still answer about static state. Log and
                # continue with an empty recent-changes block.
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "fetch_recent_changes failed for PMO agent: %s", e
                )

        summary_str = format_summary(summaries)
        if recent_summary_lines:
            joined = "\n".join(recent_summary_lines)
            summary_str = (summary_str + "\n\n" + joined) if summary_str else joined

        # Only compute total count when needed (aggregations or truncation detected)
        total = None
        if aggregations or hit_limit:
            total = 0
            for collection, filt in filters.items():
                try:
                    total += await db[collection].count_documents(filt)
                except Exception:
                    continue

        return {"docs": all_docs, "summary": summary_str, "total_count": total}

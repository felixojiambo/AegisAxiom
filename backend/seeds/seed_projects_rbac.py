"""Seed projects-module RBAC: permissions + roles.

Registers the ~40 `projects.*` permission keys against the dynamic
permission registry as `source="ingestion"` (matching how other
ingested apps are tracked) and creates / extends the matching roles:

  * `project_manager` — full projects.* access EXCEPT admin-only keys
    (currently just `projects.capacity.manage`).
  * `project_contributor` — view-everywhere + edit/change_status on tasks/threads.
  * `viewer` — extended with `projects.*.view` + `projects.dashboard.view`.

Wave 5 / Thread B added two capacity perms (`projects.capacity.view` /
`projects.capacity.manage`). The `manage` key is admin-only; PM gets
`view` so they can see the matrix without being able to flip tiers.

Idempotent — uses upserts and `$addToSet` for role permission lists, so
re-running on boot does not flip admin-edited roles. Manual permission
overrides (`source=manual_override`) are preserved by `register_permissions`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ----- Permission keys per the projects mapping file -----
# Kept inline (not derived from the mapping JSON at boot) so this seed has
# no filesystem dependency on `.ingestion/mappings/projects.json` at runtime.
PERMISSIONS: list[dict] = [
    # Top-level access gate (frontend route guard reads this)
    {"key": "projects.access", "group": "Projects", "label": "Access Projects portal"},

    # Project entity (CRUD + change_status)
    {"key": "projects.project.view", "group": "Projects", "label": "View projects"},
    {"key": "projects.project.create", "group": "Projects", "label": "Create projects"},
    {"key": "projects.project.edit", "group": "Projects", "label": "Edit projects"},
    {"key": "projects.project.delete", "group": "Projects", "label": "Delete projects"},
    {"key": "projects.project.change_status", "group": "Projects", "label": "Change project status"},

    # Task entity (CRUD + change_status + assign)
    {"key": "projects.task.view", "group": "Projects", "label": "View tasks"},
    {"key": "projects.task.create", "group": "Projects", "label": "Create tasks"},
    {"key": "projects.task.edit", "group": "Projects", "label": "Edit tasks"},
    {"key": "projects.task.delete", "group": "Projects", "label": "Delete tasks"},
    {"key": "projects.task.change_status", "group": "Projects", "label": "Change task status"},
    {"key": "projects.task.assign", "group": "Projects", "label": "Assign tasks"},

    # Task comments (Wave 6 / Thread D, Contract 7) — first-class threaded
    # comments on tasks. Markdown-formatted; viewer reads, contributor
    # creates, author/admin edits, author/PM deletes.
    {"key": "projects.task.comment.view", "group": "Projects", "label": "View task comments"},
    {"key": "projects.task.comment.create", "group": "Projects", "label": "Create task comments"},
    {"key": "projects.task.comment.edit", "group": "Projects", "label": "Edit task comments"},
    {"key": "projects.task.comment.delete", "group": "Projects", "label": "Delete task comments"},

    # Task attachments (Wave 6 / Thread D, Contract 8) — file uploads on tasks.
    # GridFS storage, 25MB cap, mime-whitelist. Viewer reads/downloads,
    # contributor uploads, contributor/PM deletes.
    {"key": "projects.task.attachment.view", "group": "Projects", "label": "View/download task attachments"},
    {"key": "projects.task.attachment.create", "group": "Projects", "label": "Upload task attachments"},
    {"key": "projects.task.attachment.delete", "group": "Projects", "label": "Delete task attachments"},

    # Sprint entity
    {"key": "projects.sprint.view", "group": "Projects", "label": "View sprints"},
    {"key": "projects.sprint.create", "group": "Projects", "label": "Create sprints"},
    {"key": "projects.sprint.edit", "group": "Projects", "label": "Edit sprints"},
    {"key": "projects.sprint.delete", "group": "Projects", "label": "Delete sprints"},
    {"key": "projects.sprint.change_status", "group": "Projects", "label": "Change sprint status"},

    # Wave entity
    {"key": "projects.wave.view", "group": "Projects", "label": "View waves"},
    {"key": "projects.wave.create", "group": "Projects", "label": "Create waves"},
    {"key": "projects.wave.edit", "group": "Projects", "label": "Edit waves"},
    {"key": "projects.wave.delete", "group": "Projects", "label": "Delete waves"},
    {"key": "projects.wave.change_status", "group": "Projects", "label": "Change wave status"},

    # Thread entity
    {"key": "projects.thread.view", "group": "Projects", "label": "View threads"},
    {"key": "projects.thread.create", "group": "Projects", "label": "Create threads"},
    {"key": "projects.thread.edit", "group": "Projects", "label": "Edit threads"},
    {"key": "projects.thread.delete", "group": "Projects", "label": "Delete threads"},
    {"key": "projects.thread.change_status", "group": "Projects", "label": "Change thread status"},
    {"key": "projects.thread.assign", "group": "Projects", "label": "Assign threads"},

    # Blocker ticket entity
    {"key": "projects.blocker_ticket.view", "group": "Projects", "label": "View blocker tickets"},
    {"key": "projects.blocker_ticket.create", "group": "Projects", "label": "Create blocker tickets"},
    {"key": "projects.blocker_ticket.edit", "group": "Projects", "label": "Edit blocker tickets"},
    {"key": "projects.blocker_ticket.delete", "group": "Projects", "label": "Delete blocker tickets"},
    {"key": "projects.blocker_ticket.change_status", "group": "Projects", "label": "Change blocker ticket status"},

    # Template entity (no change_status — templates are static)
    {"key": "projects.template.view", "group": "Projects", "label": "View templates"},
    {"key": "projects.template.create", "group": "Projects", "label": "Create templates"},
    {"key": "projects.template.edit", "group": "Projects", "label": "Edit templates"},
    {"key": "projects.template.delete", "group": "Projects", "label": "Delete templates"},

    # Dashboard
    {"key": "projects.dashboard.view", "group": "Projects", "label": "View Projects dashboard"},

    # Standup entity (Wave 5 / Thread C — first-class standup workflow,
    # replaces legacy per-thread `standup` sub-object).
    {"key": "projects.standup.view", "group": "Projects", "label": "View standups"},
    {"key": "projects.standup.create", "group": "Projects", "label": "Create standups (also gates AI summarize)"},
    {"key": "projects.standup.edit", "group": "Projects", "label": "Edit standup summary/structured"},
    {"key": "projects.standup.delete", "group": "Projects", "label": "Delete standups"},

    # Capacity admin (Wave 5 / Thread B Contract 3) — `pm_capacity_tier` control.
    # `view` is read-only (PM can see the matrix); `manage` flips tiers (admin only).
    {"key": "projects.capacity.view", "group": "Projects", "label": "View capacity matrix"},
    {"key": "projects.capacity.manage", "group": "Projects", "label": "Manage PM capacity tiers"},
]


# Admin-only permission keys — these MUST NOT flow into project_manager via _ALL_KEYS.
# `projects.capacity.manage` is the tier-flip endpoint for the Capacity admin tab;
# only admin should hold it (project_manager gets `projects.capacity.view` only).
_ADMIN_ONLY_KEYS: set[str] = {"projects.capacity.manage"}

# All projects.* keys MINUS admin-only — used by the project_manager role.
_ALL_KEYS: list[str] = [
    p["key"] for p in PERMISSIONS if p["key"] not in _ADMIN_ONLY_KEYS
]

# Contributor role: full visibility + edit/change_status on tasks and threads,
# plus access gate and dashboard.
# Wave 4 / A7 NOTE: `projects.project.view` is load-bearing for the
# /api/projects/assignable-users picker endpoint — non-admin PMs need this
# permission to populate person dropdowns in the create-project wizard,
# task assignee picker, etc. Do not remove without revisiting that route.
_CONTRIBUTOR_KEYS: list[str] = [
    "projects.access",
    "projects.dashboard.view",
    # All view permissions
    "projects.project.view",  # gates /api/projects/assignable-users (Wave 4 A1)
    "projects.task.view",
    "projects.sprint.view",
    "projects.wave.view",
    "projects.thread.view",
    "projects.blocker_ticket.view",
    "projects.template.view",
    # Task edits
    "projects.task.edit",
    "projects.task.change_status",
    # Thread edits (waves+threads contributors)
    "projects.thread.edit",
    "projects.thread.change_status",
    # Standup workflow (Wave 5 / Thread C): contributors record + edit
    # standups but cannot delete (PM only).
    "projects.standup.view",
    "projects.standup.create",
    "projects.standup.edit",
    # Task comments (Wave 6 / Thread D): contributors get full CRUD on
    # comments; the route layer additionally gates edit/delete on
    # ownership so they cannot rewrite or remove someone else's comment.
    "projects.task.comment.view",
    "projects.task.comment.create",
    "projects.task.comment.edit",
    "projects.task.comment.delete",
    # Task attachments (Wave 6 / Thread D): contributors view + upload +
    # delete (own/admin guarded at route level).
    "projects.task.attachment.view",
    "projects.task.attachment.create",
    "projects.task.attachment.delete",
]

# Viewer extension: view + dashboard.
_VIEWER_EXTENSION: list[str] = [
    "projects.access",
    "projects.dashboard.view",
    "projects.project.view",
    "projects.task.view",
    "projects.sprint.view",
    "projects.wave.view",
    "projects.thread.view",
    "projects.blocker_ticket.view",
    "projects.template.view",
    # Wave 5 / Thread C: viewers can read standups but not record them.
    "projects.standup.view",
    # Wave 6 / Thread D: viewers can read+create comments (the contract
    # treats commenting as a first-class collaboration primitive that even
    # read-only stakeholders should be able to use). Edit/delete remain
    # contributor+ to keep the audit trail clean.
    "projects.task.comment.view",
    "projects.task.comment.create",
    # Viewers can see + download attachments but cannot upload.
    "projects.task.attachment.view",
]


async def seed_projects_rbac(db=None, *, session=None) -> None:
    """Register projects.* permissions and create/extend roles. Idempotent.

    Phase 2b: permissions and roles are Postgres. ``db`` (the old Mongo
    handle server.py used to pass) is accepted and ignored; without
    ``session`` the role writes run in their own transaction and commit.
    """
    from db.session import get_sessionmaker
    from models import Role
    from repositories.roles import RolesRepository
    from services.permission_registry import register_permissions, SOURCE_INGESTION

    # 1. Register permissions in the registry.
    try:
        registered = await register_permissions(
            PERMISSIONS,
            source=SOURCE_INGESTION,
            app_id="app_01d360a5c32e",  # projects app_id from mapping file
            session=session,
        )
        logger.info(f"Projects RBAC: registered {registered} permissions")
    except Exception as e:
        logger.warning(f"Projects RBAC: permission registration failed (non-fatal): {e}")

    now = datetime.now(timezone.utc)

    # 2. project_manager role — full projects.* access, plus the access gate.
    pm_role = Role(
        role_id="role_project_manager",
        name="project_manager",
        display_name="Project Manager",
        description="Full read/write access across all Projects entities.",
        color="#0EA5E9",
        is_system=False,
        is_default=False,
        permissions=list(_ALL_KEYS),
        created_at=now,
        updated_at=now,
    )

    # 3. project_contributor role — view + edit/change_status on tasks/threads.
    contrib_role = Role(
        role_id="role_project_contributor",
        name="project_contributor",
        display_name="Project Contributor",
        description=(
            "View all Projects content; edit + change status on assigned tasks "
            "and threads."
        ),
        color="#14B8A6",
        is_system=False,
        is_default=False,
        permissions=list(_CONTRIBUTOR_KEYS),
        created_at=now,
        updated_at=now,
    )

    async def _roles(s) -> None:
        repo = RolesRepository(s)
        for role, keys in ((pm_role, _ALL_KEYS), (contrib_role, _CONTRIBUTOR_KEYS)):
            # INSERT … ON CONFLICT (name) DO NOTHING; on an existing role add
            # any missing keys atomically and preserve admin customizations.
            if await repo.insert_role_if_absent(role):
                logger.info(f"Projects RBAC: created role '{role.name}'")
            else:
                await repo.add_permissions(list(keys), name=role.name)

        # 4. Extend the existing 'viewer' role (if it exists) with the projects
        # view set. add_permissions skips keys already present (and returns
        # False when there is no such role), preserving admin customizations.
        if await repo.add_permissions(list(_VIEWER_EXTENSION), name="viewer"):
            logger.info("Projects RBAC: extended 'viewer' role with projects.*.view + dashboard")
        # Note: 'admin' role uses the '*' wildcard, so no extension needed.

    if session is not None:
        await _roles(session)
    else:
        async with get_sessionmaker()() as own:
            try:
                await _roles(own)
                await own.commit()
            except Exception:
                await own.rollback()
                raise

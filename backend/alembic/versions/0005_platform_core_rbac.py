"""platform-core RBAC / governance / observability (Phase 2b)

Creates roles, permissions, policies, policy_targets and audit_logs. Derived from
``models.py:Role`` / ``models.py:Policy``, the live document usage recorded in
``.claude/plans/phase2b-census.md`` and the 2026-11-25 re-verification in
``.claude/skills/phase-cutover/params/phase2b.yaml`` (operator rulings) — not
invented. Highlights:

  * String ids stay TEXT primary keys: ``role_id``, ``policy_id``, ``audit_id``;
    ``permissions.key`` is the natural PK (every upsert is keyed on it).
  * Opaque governance sub-arrays are JSONB (CONVENTIONS): role ``permissions``,
    permission ``implies``, policy ``trigger``/``conditions``/``actions``.
    ``roles.permissions`` is swept by membership (strip/deactivate key), so it
    carries a GIN (jsonb_ops) index for ``@>`` / ``?|``.
  * ``permissions.implies`` is NULLABLE: the seeder only writes it when the
    declaration has it (13 of 353 live rows), so NULL = "not declared".
  * ``policy_targets`` is a target registry keyed on (app_id, module, entity,
    event) — NOT a policy->target join; no policy_id, no FK to policies
    (operator ruling). ``app_id`` holds the app SLUG (load-bearing).
  * ``audit_logs`` is the union of every writer's shape; ``event_category`` is
    nullable (legacy rows). The retired Mongo index set is carried over (server.py
    startup block + projects_recent_changes lazy index).
  * §A4 trigram GIN on every column reached by a former ``$regex`` (pg_trgm
    enabled in 0001_baseline): ``audit_logs.resource_type`` (prefix),
    ``policies.name`` / ``policies.description`` (ILIKE search),
    ``permissions.key`` (prefix).

The Mongo ``create_index`` calls these replace die in server.py (plan §A7).

Revision ID: 0005_platform_core_rbac
Revises: 0004_drop_scaffolding_hello
Create Date: 2026-11-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0005_platform_core_rbac"
down_revision: Union[str, None] = "0004_drop_scaffolding_hello"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=None if nullable else sa.func.now(),
    )


def upgrade() -> None:
    # ── roles ──────────────────────────────────────────────────────────────
    op.create_table(
        "roles",
        sa.Column("role_id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.Text(), nullable=False, server_default="#64748B"),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("permissions", postgresql.JSONB(), nullable=False, server_default="[]"),
        # Set only on roles created by the ingestion RBAC generator.
        sa.Column("app_id", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )
    op.create_index(
        "ix_roles_permissions_gin", "roles", ["permissions"], postgresql_using="gin"
    )

    # ── permissions ────────────────────────────────────────────────────────
    op.create_table(
        "permissions",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("group", sa.Text(), nullable=True),
        sa.Column("label", sa.Text(), nullable=True),
        # system | ingestion | custom | manual_override | reconcile_auto
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("app_id", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        # DAG edges (string[]). NULL = not declared on this key.
        sa.Column("implies", postgresql.JSONB(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index("ix_permissions_source", "permissions", ["source"])
    op.create_index(
        "ix_permissions_app_id",
        "permissions",
        ["app_id"],
        postgresql_where=sa.text("app_id IS NOT NULL"),
    )
    op.create_index("ix_permissions_is_active", "permissions", ["is_active"])
    # §A4: key is reached by an anchored-prefix $regex (ingestion/service.py
    # module permission count) → LIKE 'prefix%'.
    op.create_index(
        "ix_permissions_key_trgm",
        "permissions",
        ["key"],
        postgresql_using="gin",
        postgresql_ops={"key": "gin_trgm_ops"},
    )

    # ── policies ───────────────────────────────────────────────────────────
    op.create_table(
        "policies",
        sa.Column("policy_id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("module", sa.Text(), nullable=False),
        sa.Column("entity", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("trigger", postgresql.JSONB(), nullable=False),
        sa.Column("condition_logic", sa.Text(), nullable=False, server_default="AND"),
        sa.Column("condition_pattern", sa.Text(), nullable=True),
        sa.Column("conditions", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("actions", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_by", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _ts("deleted_at", nullable=True),
    )
    # Engine lookup (policy_engine: module + entity + enabled, live rows only).
    op.create_index(
        "ix_policies_module_entity_enabled",
        "policies",
        ["module", "entity", "enabled"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_policies_enabled_live",
        "policies",
        ["enabled"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # §A4: the admin list search was a case-insensitive $regex on name and
    # description (routes/policies.py) → ILIKE '%term%'.
    for col in ("name", "description"):
        op.create_index(
            f"ix_policies_{col}_trgm",
            "policies",
            [col],
            postgresql_using="gin",
            postgresql_ops={col: "gin_trgm_ops"},
        )

    # ── policy_targets (target registry; no FK to policies) ────────────────
    op.create_table(
        "policy_targets",
        sa.Column("app_id", sa.Text(), nullable=False, comment="holds the app SLUG, not app_id (load-bearing; see ingestion/reconcile.py)"),
        sa.Column("module", sa.Text(), nullable=False),
        sa.Column("entity", sa.Text(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("app_id", "module", "entity", "event", name="pk_policy_targets"),
    )
    op.create_index("ix_policy_targets_app_active", "policy_targets", ["app_id", "is_active"])
    op.create_index(
        "ix_policy_targets_module_entity_active",
        "policy_targets",
        ["module", "entity", "is_active"],
    )

    # ── audit_logs (hot write path) ────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("audit_id", sa.Text(), primary_key=True),
        sa.Column("event_category", sa.Text(), nullable=True),  # NULL = legacy "activity"
        sa.Column("event_type", sa.Text(), nullable=True),
        sa.Column("severity", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("actor_user_id", sa.Text(), nullable=True),
        sa.Column("actor_email", sa.Text(), nullable=True),
        sa.Column("actor_type", sa.Text(), nullable=True),
        sa.Column("tool_name", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.Text(), nullable=True),
        sa.Column("resource_type", sa.Text(), nullable=True),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("result_status", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("tier", sa.Integer(), nullable=True),
        sa.Column("params", postgresql.JSONB(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
        _ts("timestamp"),
    )
    ts_desc = sa.text("timestamp DESC")
    op.create_index("ix_audit_logs_timestamp", "audit_logs", [ts_desc])
    op.create_index("ix_audit_logs_category_ts", "audit_logs", ["event_category", ts_desc])
    op.create_index("ix_audit_logs_event_type_ts", "audit_logs", ["event_type", ts_desc])
    op.create_index("ix_audit_logs_actor_id_ts", "audit_logs", ["actor_id", ts_desc])
    op.create_index("ix_audit_logs_actor_email_ts", "audit_logs", ["actor_email", ts_desc])
    op.create_index(
        "ix_audit_logs_resource_ts",
        "audit_logs",
        ["resource_type", "resource_id", ts_desc],
    )
    # Carried over from projects_recent_changes._ensure_indexes (lazy Mongo index).
    op.create_index("ix_audit_logs_resource_type_ts", "audit_logs", ["resource_type", ts_desc])
    # Carried over from the server.py single-field Mongo indexes.
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_severity", "audit_logs", ["severity"])
    op.create_index("ix_audit_logs_outcome", "audit_logs", ["outcome"])
    # §A4: resource_type is the one column reached by a former $regex
    # (anchored module-prefix filter in routes/audit.py).
    op.create_index(
        "ix_audit_logs_resource_type_trgm",
        "audit_logs",
        ["resource_type"],
        postgresql_using="gin",
        postgresql_ops={"resource_type": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("policy_targets")
    op.drop_table("policies")
    op.drop_table("permissions")
    op.drop_table("roles")

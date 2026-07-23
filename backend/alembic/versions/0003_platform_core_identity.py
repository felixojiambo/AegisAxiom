"""platform-core identity (Phase 2a): users, tenants, auth-state, org_branding

Creates the dependency-root identity tables everything else FKs to. Derived from
the Pydantic models in ``models.py`` and the live document usage recorded in
``.claude/plans/phase2a-census.md`` — not invented. Highlights:

  * String ``*_id`` primary keys stay TEXT (public API contract).
  * Timestamps are ``timestamptz``; booleans are booleans.
  * ``user_resource_type_assignments`` is the users identity-child (census §0.3);
    the plan's original ``user_app_access`` child does not exist and moved to
    Phase 8 as an ``ai_employees`` child (§A3 correction 2026-07-20).
  * Trigram GIN indexes on ``users.name``/``users.email`` per §A4 (pg_trgm was
    enabled in ``0001_baseline``).
  * No native TTL in Postgres → ``expires_at``/``created_at`` columns carry expiry
    and a scheduled job sweeps them (plan R10).

Scaffolding removal is the separate ``0004_drop_scaffolding_hello`` migration.

Revision ID: 0003_platform_core_identity
Revises: 0002_scaffolding_hello
Create Date: 2026-07-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003_platform_core_identity"
down_revision: Union[str, None] = "0002_scaffolding_hello"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── tenants (self-referential parent) ──────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column("tenant_id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("tenant_type", sa.Text(), nullable=False),
        sa.Column("parent_tenant_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["parent_tenant_id"], ["tenants.tenant_id"], name="fk_tenants_parent"),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
    )
    op.create_index("ix_tenants_parent_tenant_id", "tenants", ["parent_tenant_id"])
    op.create_index("ix_tenants_tenant_type", "tenants", ["tenant_type"])
    op.create_index("ix_tenants_status", "tenants", ["status"])

    # ── users ──────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("user_id", sa.Text(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=True),
        sa.Column("account_id", sa.Text(), nullable=True),
        sa.Column("contractor_business_id", sa.Text(), nullable=True),
        sa.Column("contractor_business_name", sa.Text(), nullable=True),
        sa.Column("picture", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_email_verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("zoho_contact_id", sa.Text(), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("invite_token", sa.Text(), nullable=True),
        sa.Column("extra_permissions", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("ai_usage_viewable_users", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("pm_capacity_tier", sa.Text(), nullable=True),
        sa.Column("is_assignable_as_pm", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_assignable_as_member", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("budget_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], name="fk_users_tenant"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_role", "users", ["role"])
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"], postgresql_where=sa.text("tenant_id IS NOT NULL"))
    op.create_index("ix_users_tenant_email", "users", ["tenant_id", "email"])
    # Trigram GIN indexes (§A4) — forward-looking substring search on name/email.
    op.create_index(
        "ix_users_name_trgm", "users", ["name"],
        postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_users_email_trgm", "users", ["email"],
        postgresql_using="gin", postgresql_ops={"email": "gin_trgm_ops"},
    )

    # ── user_resource_type_assignments (users child, census §0.3) ──────────
    op.create_table(
        "user_resource_type_assignments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("resource_type_id", sa.Text(), nullable=False),
        sa.Column("resource_type_name", sa.Text(), nullable=True),
        sa.Column("cost_rate", sa.Numeric(), nullable=True),
        sa.Column("contractor_business_rate", sa.Numeric(), nullable=True),
        sa.Column("approval_status", sa.Text(), nullable=False, server_default="approved"),
        sa.Column("approved_by", sa.Text(), nullable=True),
        sa.Column("approved_by_name", sa.Text(), nullable=True),
        sa.Column("approved_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("assigned_by", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], name="fk_urta_user", ondelete="CASCADE"),
    )
    op.create_index("ix_urta_user_id", "user_resource_type_assignments", ["user_id"])

    # ── user_sessions ──────────────────────────────────────────────────────
    op.create_table(
        "user_sessions",
        sa.Column("session_token", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], name="fk_sessions_user", ondelete="CASCADE"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])

    # ── password_reset_tokens (upserted by email; token uniquely indexed) ──
    op.create_table(
        "password_reset_tokens",
        sa.Column("email", sa.Text(), primary_key=True),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("token", name="uq_prt_token"),
    )

    # ── oauth_states (single-use CSRF state; created_at sweep) ─────────────
    op.create_table(
        "oauth_states",
        sa.Column("state", sa.Text(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("redirect_uri", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_oauth_states_created_at", "oauth_states", ["created_at"])

    # ── oauth_tokens (google | zoho; never qbo) ────────────────────────────
    op.create_table(
        "oauth_tokens",
        sa.Column("token_id", sa.Text(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "user_id", name="uq_oauth_tokens_provider_user"),
    )
    op.create_index("ix_oauth_tokens_provider", "oauth_tokens", ["provider"])

    # ── org_branding ───────────────────────────────────────────────────────
    op.create_table(
        "org_branding",
        sa.Column("org_id", sa.Text(), primary_key=True),
        sa.Column("provider_name", sa.Text(), nullable=False),
        sa.Column("legal_text", sa.Text(), nullable=False),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("primary_color", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    # Reverse creation order (children before parents).
    op.drop_table("org_branding")
    op.drop_index("ix_oauth_tokens_provider", table_name="oauth_tokens")
    op.drop_table("oauth_tokens")
    op.drop_index("ix_oauth_states_created_at", table_name="oauth_states")
    op.drop_table("oauth_states")
    op.drop_table("password_reset_tokens")
    op.drop_index("ix_user_sessions_expires_at", table_name="user_sessions")
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_index("ix_urta_user_id", table_name="user_resource_type_assignments")
    op.drop_table("user_resource_type_assignments")
    op.drop_index("ix_users_email_trgm", table_name="users")
    op.drop_index("ix_users_name_trgm", table_name="users")
    op.drop_index("ix_users_tenant_email", table_name="users")
    op.drop_index("ix_users_tenant_id", table_name="users")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_table("users")
    op.drop_index("ix_tenants_status", table_name="tenants")
    op.drop_index("ix_tenants_tenant_type", table_name="tenants")
    op.drop_index("ix_tenants_parent_tenant_id", table_name="tenants")
    op.drop_table("tenants")

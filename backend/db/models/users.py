"""Users ORM models (Phase 2a).

``UserRow`` is derived from ``models.py:User`` **plus** the fields that live on
the Mongo document but not the Pydantic model (``password_hash``,
``invite_token``, ``budget_reset_at``, ``is_test_data``) — found via the census
(§4). ``permissions``/``effective_permissions`` are intentionally NOT stored:
they are request-time derivations (role permissions + ``extra_permissions`` +
implication closure) computed in ``auth_helper.get_current_user`` and exist only
on the API object.

``UserResourceTypeAssignmentRow`` is the real users child table (census §0.3 /
plan §A3 correction 2026-07-20): the live ``users.resource_type_assignments``
sub-array (``$unwind``-aggregated at ``solutions_design.py:677``). It replaces
the phantom ``user_app_access`` the plan originally named — that array is an
``ai_employees`` field and migrates in Phase 8.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class UserRow(Base):
    """A platform user (human or AI-employee-backing identity)."""

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # Nullable FK — legacy rows may predate tenant attribution. No ON DELETE
    # cascade: deleting a tenant must never silently delete its users.
    tenant_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("tenants.tenant_id"), nullable=True
    )
    account_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    contractor_business_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    contractor_business_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    picture: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    is_email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    zoho_contact_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Secret material — never returned across the repository boundary in a DTO.
    password_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    invite_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Opaque string lists read/written whole (never filtered by interior) → JSONB
    # per CONVENTIONS.md, not child tables.
    extra_permissions: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    ai_usage_viewable_users: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    pm_capacity_tier: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_assignable_as_pm: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_assignable_as_member: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    budget_reset_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_test_data: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    resource_type_assignments: Mapped[list["UserResourceTypeAssignmentRow"]] = (
        relationship(
            "UserResourceTypeAssignmentRow",
            cascade="all, delete-orphan",
            lazy="selectin",
            back_populates="user",
        )
    )


class UserResourceTypeAssignmentRow(Base):
    """A user's rate/approval entry for one resource type (census §0.3).

    Element shape mirrors ``models.py:ResourceTypeAssignment``. FK'd to the user;
    deleted with the user (the array had no independent lifecycle in Mongo).
    """

    __tablename__ = "user_resource_type_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resource_type_id: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cost_rate: Mapped[Optional[float]] = mapped_column(Numeric, nullable=True)
    contractor_business_rate: Mapped[Optional[float]] = mapped_column(
        Numeric, nullable=True
    )
    approval_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="approved"
    )
    approved_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approved_by_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approved_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    assigned_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped["UserRow"] = relationship(
        "UserRow", back_populates="resource_type_assignments"
    )

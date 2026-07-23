"""SQLAlchemy ORM models — the PostgreSQL persistence layer.

One module per domain (mirroring `repositories/`). Every model inherits from
``db.base:Base`` and is imported here so a single ``import db.models`` registers
the whole schema against ``Base.metadata`` — which is what ``alembic/env.py``
points ``target_metadata`` at, and what the repositories query.

Phase 2a (platform-core identity) is the first real domain to land here,
replacing the Phase-0 throwaway scaffolding. Later phases add their own
``db/models/<domain>.py`` modules and extend the imports below.

Convention (recorded in ``repositories/CONVENTIONS.md``): ORM row classes are
named ``<Concept>Row`` to keep them visibly distinct from the same-named
Pydantic DTOs they convert to/from at the repository boundary.
"""
from __future__ import annotations

# Import order is dependency order (parents before children) for readability;
# SQLAlchemy resolves relationships lazily so strict ordering is not required.
from db.models.tenants import TenantRow  # noqa: F401
from db.models.users import UserRow, UserResourceTypeAssignmentRow  # noqa: F401
from db.models.auth import (  # noqa: F401
    UserSessionRow,
    PasswordResetTokenRow,
    OAuthStateRow,
    OAuthTokenRow,
)
from db.models.org_branding import OrgBrandingRow  # noqa: F401

__all__ = [
    "TenantRow",
    "UserRow",
    "UserResourceTypeAssignmentRow",
    "UserSessionRow",
    "PasswordResetTokenRow",
    "OAuthStateRow",
    "OAuthTokenRow",
    "OrgBrandingRow",
]

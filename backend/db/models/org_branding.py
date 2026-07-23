"""Organization-branding ORM model (Phase 2a).

Derived from ``models.py:OrganizationBranding``. Single-row-per-org today
(``org_id='default'``); consumed by the branding route and, cross-domain, by SOW
document generation (census §1.7).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class OrgBrandingRow(Base):
    """Brand configuration for SOW document generation, keyed by ``org_id``."""

    __tablename__ = "org_branding"

    org_id: Mapped[str] = mapped_column(Text, primary_key=True)
    provider_name: Mapped[str] = mapped_column(Text, nullable=False)
    legal_text: Mapped[str] = mapped_column(Text, nullable=False)
    logo_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    primary_color: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

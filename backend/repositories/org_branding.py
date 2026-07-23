"""Org-branding repository (Phase 2a) — the one data-access path for `org_branding`.

Converts ``OrgBrandingRow`` ⇄ ``models.py:OrganizationBranding``. Consumed by the
branding admin route and, cross-domain, by SOW document generation (census §1.7).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from db.models.org_branding import OrgBrandingRow
from models import OrganizationBranding
from repositories.base import BaseRepository


def _to_branding(row: OrgBrandingRow) -> OrganizationBranding:
    return OrganizationBranding.model_validate(row, from_attributes=True)


class BrandingRepository(BaseRepository):
    async def get_branding(self, org_id: str = "default") -> Optional[OrganizationBranding]:
        row = await self.session.get(OrgBrandingRow, org_id)
        return _to_branding(row) if row is not None else None

    async def upsert_branding(
        self, org_id: str, updates: dict
    ) -> OrganizationBranding:
        """Insert or patch the branding row for ``org_id`` (route uses upsert)."""
        row = await self.session.get(OrgBrandingRow, org_id)
        if row is None:
            # Fill defaults from the Pydantic model for any unset column, so a
            # first-write with a partial patch still yields a complete row.
            defaults = OrganizationBranding(org_id=org_id).model_dump()
            defaults.update({k: v for k, v in updates.items() if v is not None})
            row = OrgBrandingRow(
                org_id=org_id,
                provider_name=defaults["provider_name"],
                legal_text=defaults["legal_text"],
                logo_url=defaults.get("logo_url"),
                primary_color=defaults.get("primary_color"),
                updated_at=datetime.now(timezone.utc),
            )
            self.session.add(row)
        else:
            for key, value in updates.items():
                if value is not None and hasattr(row, key):
                    setattr(row, key, value)
            row.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return _to_branding(row)

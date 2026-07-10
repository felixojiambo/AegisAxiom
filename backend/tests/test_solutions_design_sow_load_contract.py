"""Locks the SOW GET-by-LOB endpoint's error-response contract.

The Sales Ops UI's "View SOW" / "Build SOW" buttons mount a modal that
calls ``GET /api/sales-ops/opportunities/{opportunity_id}/sow?lob=X``.
Until the click no-op fix the modal silently returned null on any
non-2xx response, so the click read to the user as nothing-happens.
The frontend now surfaces ``detail`` from the JSON body. These tests
guarantee that:

  * a missing opportunity returns 404 with a JSON ``detail`` string
  * an opportunity that does not carry the requested LOB returns 400
    with a JSON ``detail`` string that names the LOB
  * a user without ``sales_ops.solutions_design.view`` is rejected
    with 403 + JSON ``detail``
  * the success path returns a SOW payload containing the canonical
    keys the modal reads (sow_id, opportunity_id, line_of_business,
    status, version, milestones)

The endpoint is exercised directly with an AsyncMock db so the test
runs without Mongo. Synthetic identifiers only.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-unit-tests-only-not-for-production"
)


_OPP_ID = "opp_synth_sow_noop_001"
_LOB_PRESENT = "professional_services"
_LOB_ABSENT = "field_services"


def _make_user(*, can_view: bool = True):
    perms = ["sales_ops.solutions_design.view"] if can_view else []
    return SimpleNamespace(
        user_id="u_synth_sow_001",
        email="sow-tester@aegisaxiom.test",
        role="sales_ops",
        permissions=perms,
    )


def _make_db_with_opportunity(*, lines_of_business: list[str] | None, sow: dict | None):
    """Stand-in for the Mongo db dependency used by the SOW route.

    The route reads:
      * ``db.opportunities.find_one({"opportunity_id": ...})``
      * ``db.opportunity_sows.find_one({"opportunity_id": ..., "line_of_business": ...})``

    ``lines_of_business`` = None signals "no opportunity row at all" so
    the same factory covers the 404 case.
    """
    db = SimpleNamespace()
    db.opportunities = SimpleNamespace()
    db.opportunity_sows = SimpleNamespace()

    if lines_of_business is None:
        db.opportunities.find_one = AsyncMock(return_value=None)
    else:
        db.opportunities.find_one = AsyncMock(
            return_value={
                "opportunity_id": _OPP_ID,
                "name": "Synth Opp",
                "lines_of_business": list(lines_of_business),
            }
        )

    db.opportunity_sows.find_one = AsyncMock(return_value=sow)
    return db


@pytest.mark.asyncio
async def test_get_sow_returns_404_when_opportunity_missing():
    """A missing opportunity must surface as 404 + JSON detail string.

    The frontend's loadError uses this exact field to tell the user
    why the click could not open a SOW.
    """
    from routes.solutions_design import get_opportunity_sow_by_lob

    db = _make_db_with_opportunity(lines_of_business=None, sow=None)
    user = _make_user(can_view=True)

    with pytest.raises(HTTPException) as exc_info:
        await get_opportunity_sow_by_lob(
            opportunity_id=_OPP_ID,
            lob=_LOB_PRESENT,
            db=db,
            current_user=user,
        )
    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_get_sow_returns_400_when_lob_not_on_opportunity():
    """If the SOW row exists in the kanban but its line_of_business has
    been pruned off the parent opportunity, the modal-open click used
    to fail silently. The route must return 400 with a JSON detail that
    names the offending LOB so the frontend can render the reason."""
    from routes.solutions_design import get_opportunity_sow_by_lob

    db = _make_db_with_opportunity(
        lines_of_business=[_LOB_PRESENT],
        sow=None,
    )
    user = _make_user(can_view=True)

    with pytest.raises(HTTPException) as exc_info:
        await get_opportunity_sow_by_lob(
            opportunity_id=_OPP_ID,
            lob=_LOB_ABSENT,
            db=db,
            current_user=user,
        )
    assert exc_info.value.status_code == 400
    # The detail must name the LOB so the user knows which field to fix.
    assert _LOB_ABSENT in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_sow_returns_403_without_view_permission():
    """A caller without the view permission must get 403 + JSON detail,
    not 200-but-empty. The frontend's modal expects the detail string."""
    from routes.solutions_design import get_opportunity_sow_by_lob

    db = _make_db_with_opportunity(
        lines_of_business=[_LOB_PRESENT],
        sow=None,
    )
    user = _make_user(can_view=False)

    with pytest.raises(HTTPException) as exc_info:
        await get_opportunity_sow_by_lob(
            opportunity_id=_OPP_ID,
            lob=_LOB_PRESENT,
            db=db,
            current_user=user,
        )
    assert exc_info.value.status_code == 403
    assert "permission" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_get_sow_returns_stub_when_no_row_exists_for_lob():
    """When the opportunity carries the LOB but no SOW row exists yet,
    the route returns a stub SOW. The frontend renders that stub as the
    starting point for editing. Lock the shape so a missing key never
    silently re-introduces the no-op."""
    from routes.solutions_design import get_opportunity_sow_by_lob

    db = _make_db_with_opportunity(
        lines_of_business=[_LOB_PRESENT],
        sow=None,
    )
    user = _make_user(can_view=True)

    result = await get_opportunity_sow_by_lob(
        opportunity_id=_OPP_ID,
        lob=_LOB_PRESENT,
        db=db,
        current_user=user,
    )

    for key in (
        "sow_id",
        "opportunity_id",
        "line_of_business",
        "status",
        "version",
        "milestones",
    ):
        assert key in result, f"stub SOW missing {key!r}"
    assert result["opportunity_id"] == _OPP_ID
    assert result["line_of_business"] == _LOB_PRESENT
    assert result["status"] == "not_started"
    assert isinstance(result["milestones"], list)


@pytest.mark.asyncio
async def test_get_sow_returns_existing_row_when_present():
    """When the SOW row already exists, the route returns it verbatim
    so the frontend can render the saved state. The contract here is
    that the response is the SOW dict itself (not wrapped)."""
    from routes.solutions_design import get_opportunity_sow_by_lob

    existing = {
        "sow_id": "sow_synth_existing_001",
        "opportunity_id": _OPP_ID,
        "line_of_business": _LOB_PRESENT,
        "status": "in_progress",
        "version": 3,
        "milestones": [{"milestone_id": "m1", "name": "Kickoff"}],
    }
    db = _make_db_with_opportunity(
        lines_of_business=[_LOB_PRESENT],
        sow=existing,
    )
    user = _make_user(can_view=True)

    result = await get_opportunity_sow_by_lob(
        opportunity_id=_OPP_ID,
        lob=_LOB_PRESENT,
        db=db,
        current_user=user,
    )
    assert result == existing

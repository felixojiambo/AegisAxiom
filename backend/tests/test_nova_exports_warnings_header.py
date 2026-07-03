"""Locks the X-Nova-Export-Warnings response header sanitization
and the byte cap on the joined value.

Warning strings come from tool results and flow into the response
header. A literal carriage return, newline, or tab in a warning
would otherwise split the HTTP header on the wire. The helper
flattens those control chars before the join. The joined value is
also capped at a documented byte limit so a pathologically long
warnings list cannot balloon header size. Truncation prefers a
separator boundary so the response is never split mid warning.
"""
from __future__ import annotations

import os

os.environ.setdefault("JWT_SECRET", "test-secret-for-pytest")

import pytest


def test_sanitize_header_value_strips_crlf_and_tab():
    from routes.nova_exports import _sanitize_header_value

    assert _sanitize_header_value("ok\r\ninjected: yes") == "ok injected: yes"
    assert _sanitize_header_value("ok\rinjected: yes") == "okinjected: yes"
    assert _sanitize_header_value("ok\ninjected: yes") == "ok injected: yes"
    assert _sanitize_header_value("ok\tafter") == "ok after"
    assert _sanitize_header_value(None) == ""
    assert _sanitize_header_value("  spaced  ") == "spaced"


def test_build_warnings_header_single_line_with_crlf_inside_a_warning():
    """A warning string carrying a literal carriage return and
    newline must produce a single line header with no raw control
    chars and a single configured separator between entries."""
    from routes.nova_exports import _build_warnings_header

    warnings = [
        "first warning",
        "second\r\nset-cookie: synth=injected",
        "third warning",
    ]
    header = _build_warnings_header(warnings)
    assert "\r" not in header
    assert "\n" not in header
    assert "first warning" in header
    assert "third warning" in header
    assert "second set-cookie: synth=injected" in header


def test_build_warnings_header_caps_at_byte_limit():
    """Pathologically long warnings list must be capped at the
    documented header byte limit. Truncation happens at a separator
    boundary."""
    from routes.nova_exports import (
        _build_warnings_header,
        _MAX_WARNING_HEADER_BYTES,
    )

    warnings = ["synth_warning_" + ("x" * 200) for _ in range(50)]
    header = _build_warnings_header(warnings)
    assert len(header.encode("utf-8")) <= _MAX_WARNING_HEADER_BYTES


def test_build_warnings_header_empty_returns_empty_string():
    from routes.nova_exports import _build_warnings_header

    assert _build_warnings_header([]) == ""
    assert _build_warnings_header(None) == ""


# ---------------------------------------------------------------------------
# H2. Cross user export download is owner only. The previous
# nova.export.admin override was removed because the permission
# was never registered or granted to any role. This lock asserts
# the route returns 403 for a non owner regardless of which role
# the caller carries. If a future product call approves cross user
# access the permission must be registered and explicitly granted
# before re wiring the branch and this test will fail loudly.
# ---------------------------------------------------------------------------


def test_download_endpoint_returns_403_for_non_owner_across_all_roles():
    """A user who does not own the export must get 403 from the
    download endpoint regardless of which seeded role they hold.
    The test iterates over a representative set of roles and any
    permission grant beyond ownership now fails the assertion."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from auth_helper import get_current_user
    from routes.nova_exports import router as nova_exports_router
    from routes.roles import DEFAULT_ROLES

    target_export_id = "synth_export_h2"
    owner_user_id = "u_synth_h2_owner"

    async def _fake_get_export_artifact(db, export_id):
        if export_id != target_export_id:
            return None
        return {
            "export_id": target_export_id,
            "requested_by_user_id": owner_user_id,
            "filename": "synth.csv",
            "content_type": "text/csv",
            "row_count": 0,
            "warnings": [],
            "source_tool_name": "synth_tool",
        }

    roles_to_check = [
        r for r in DEFAULT_ROLES
        if "nova.export.create" in r.get("permissions", [])
        and r["name"] != "wildcard"
    ]
    assert roles_to_check, (
        "expected at least one seeded role with nova.export.create "
        "to drive the H2 cross user lock"
    )

    for role in roles_to_check:
        non_owner = SimpleNamespace(
            user_id="u_synth_h2_other_" + role["name"],
            email="u_synth_h2_other@example.invalid",
            role=role["name"],
            permissions=list(role.get("permissions", [])),
            tenant_id=None,
        )
        app = FastAPI()
        app.include_router(nova_exports_router, prefix="/api/nova")
        app.dependency_overrides[get_current_user] = lambda u=non_owner: u

        db = MagicMock()
        with patch("database.get_db", return_value=db), \
             patch(
                 "services.nova_engine.nova_export_storage.get_export_artifact",
                 new=_fake_get_export_artifact,
             ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get(
                "/api/nova/exports/" + target_export_id + "/download"
            )
        assert resp.status_code == 403, (
            "role " + role["name"]
            + " must NOT be able to download another user export. "
            + "Got " + str(resp.status_code) + " " + resp.text
        )


def test_download_endpoint_returns_200_for_owner():
    """The owner of an export must still pass the gate. Locks that
    the H2 removal of the admin branch did not also break the
    happy path."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from auth_helper import get_current_user
    from routes.nova_exports import router as nova_exports_router
    from routes.roles import DEFAULT_ROLES

    target_export_id = "synth_export_h2_owner"
    owner_user_id = "u_synth_h2_owner"

    chat_only_role = next(
        r for r in DEFAULT_ROLES if r["name"] == "chat_only"
    )
    owner = SimpleNamespace(
        user_id=owner_user_id,
        email="u_synth_h2_owner@example.invalid",
        role="chat_only",
        permissions=list(chat_only_role.get("permissions", [])),
        tenant_id=None,
    )

    async def _fake_get_export_artifact(db, export_id):
        return {
            "export_id": target_export_id,
            "requested_by_user_id": owner_user_id,
            "filename": "synth.csv",
            "content_type": "text/csv",
            "row_count": 0,
            "warnings": [],
            "source_tool_name": "synth_tool",
        }

    async def _fake_open_export_download_stream(db, doc):
        return b"synth,body\n"

    app = FastAPI()
    app.include_router(nova_exports_router, prefix="/api/nova")
    app.dependency_overrides[get_current_user] = lambda: owner

    db = MagicMock()
    with patch("database.get_db", return_value=db), \
         patch(
             "services.nova_engine.nova_export_storage.get_export_artifact",
             new=_fake_get_export_artifact,
         ), patch(
             "services.nova_engine.nova_export_storage.open_export_download_stream",
             new=_fake_open_export_download_stream,
         ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/api/nova/exports/" + target_export_id + "/download"
        )
    assert resp.status_code == 200, (
        "owner download must succeed got "
        + str(resp.status_code) + " " + resp.text
    )

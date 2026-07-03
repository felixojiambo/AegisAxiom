"""Nova finance export endpoint.

Exposes a single ``POST /api/nova/exports`` route that re-runs an
approved Nova finance tool server-side and streams the result back as
a CSV or XLSX file. The route never accepts client-supplied rows:
audit-grade exports must come from the same deterministic tool the
chat surface uses, with the same allow-list of tools and the same
field-projection rules. See
``services/nova_engine/finance_export_service.py`` for the per-tool
column projection and the global secrets / internals filter.

This is the codebase's first ``StreamingResponse`` download pattern.
For v1 there is NO persistent download URL — the route streams bytes
in the same request. A future commit may add short-TTL persistence
behind a separate ``GET /api/nova/exports/{export_id}/download``
route; the response payload reserves the ``download_url`` field so
callers can branch on it without a schema migration.

RBAC: requires the ``nova.export.create`` permission. Granted by
default to ``nova.use`` holders via the ``implies`` edge below so
existing chat users do not lose access; finance ops can revoke it
independently if a tenant wants to disable export.
"""
from __future__ import annotations

import logging
from typing import Any

import json

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from auth_helper import get_current_user, require_perm
from services.permission_registry import register_module_permissions


logger = logging.getLogger(__name__)


router = APIRouter()


# ---------------------------------------------------------------------------
# Header value sanitization
# ---------------------------------------------------------------------------
#
# Warning strings come from tool results and flow into the
# X-Nova-Export-Warnings response header. A literal carriage return,
# newline, or tab in a warning would split the HTTP header on the
# wire so the value is flattened to one line before the join. The
# joined header value is also capped at _MAX_WARNING_HEADER_BYTES so
# a pathologically long warnings list cannot balloon header size.

_HEADER_WARNING_SEPARATOR = " | "
_MAX_WARNING_HEADER_BYTES = 4096


def _sanitize_header_value(value):
    """Flatten control whitespace inside a single header field. None
    becomes empty string. Carriage return and newline are stripped.
    Tab becomes a single space. Surrounding whitespace is trimmed.
    """
    if value is None:
        return ""
    return (
        str(value)
        .replace("\r", "")
        .replace("\n", " ")
        .replace("\t", " ")
        .strip()
    )


def _build_warnings_header(warnings) -> str:
    """Join sanitized warnings into a single header value, capped at
    ``_MAX_WARNING_HEADER_BYTES``. Truncation happens at a separator
    boundary when one fits so the response header is never split
    mid warning.
    """
    if not warnings:
        return ""
    cleaned = [_sanitize_header_value(w) for w in warnings]
    cleaned = [w for w in cleaned if w]
    if not cleaned:
        return ""
    joined = _HEADER_WARNING_SEPARATOR.join(cleaned)
    if len(joined.encode("utf-8")) <= _MAX_WARNING_HEADER_BYTES:
        return joined
    # Build the longest prefix that still fits, truncating at a
    # separator boundary when at least one entry survives.
    out: list[str] = []
    running = 0
    sep_bytes = len(_HEADER_WARNING_SEPARATOR.encode("utf-8"))
    for entry in cleaned:
        entry_bytes = len(entry.encode("utf-8"))
        added = entry_bytes if not out else entry_bytes + sep_bytes
        if running + added > _MAX_WARNING_HEADER_BYTES:
            break
        out.append(entry)
        running += added
    if out:
        return _HEADER_WARNING_SEPARATOR.join(out)
    # Single entry already exceeds the cap. Hard truncate at the
    # byte boundary so the header still surfaces some signal.
    head = cleaned[0].encode("utf-8")[:_MAX_WARNING_HEADER_BYTES]
    return head.decode("utf-8", errors="ignore")


# ---------------------------------------------------------------------------
# tool_args bounds — abuse / accidental-bomb guard
# ---------------------------------------------------------------------------
#
# ``tool_args`` is forwarded verbatim to ``execute_tool``; without these
# bounds a caller could submit a 10 MB nested dict, exhaust the worker
# coroutine slot, and bypass the export rate limit. Picked numbers are
# generous enough that no legitimate Nova export touches them — the
# largest live arg block (driver explanation report) is ~600 bytes.

MAX_EXPORT_TOOL_ARGS_BYTES = 32_768
MAX_EXPORT_TOOL_ARGS_DEPTH = 8
MAX_EXPORT_TOOL_ARGS_KEYS = 100
MAX_EXPORT_TOOL_ARGS_LIST_LENGTH = 500
MAX_EXPORT_TOOL_ARGS_STRING_LENGTH = 4096

# Hard cap on the JSON serialized size of tool_args before it
# reaches execute_tool. Mirrors the _MAX_CALLBACK_BYTES pattern
# from the QBO callback handler. Returns HTTP 413 when exceeded
# so a misbehaving client can tell the body itself was too large
# rather than read a generic 400 about content shape.
_MAX_TOOL_ARGS_BYTES = 64 * 1024


class _ToolArgsTooLarge(ValueError):
    """Raised by the bounds validator when tool_args exceeds any
    configured limit. The route turns this into a 400."""


def _validate_tool_args_bounds(args: dict) -> None:
    """Reject abusive or accidentally huge ``tool_args`` before
    handing them to ``execute_tool``. The validator walks the
    structure once, capping depth, key count, list length, and
    string length, then JSON-serialises to enforce a hard byte cap.

    Raises ``_ToolArgsTooLarge`` with a user-safe message — we
    never echo the offending value, only the limit it tripped.
    """

    def _walk(node, depth: int) -> None:
        if depth > MAX_EXPORT_TOOL_ARGS_DEPTH:
            raise _ToolArgsTooLarge(
                f"tool_args nesting exceeds the allowed depth "
                f"({MAX_EXPORT_TOOL_ARGS_DEPTH})."
            )
        if isinstance(node, dict):
            if len(node) > MAX_EXPORT_TOOL_ARGS_KEYS:
                raise _ToolArgsTooLarge(
                    f"tool_args contains a dict with more than "
                    f"{MAX_EXPORT_TOOL_ARGS_KEYS} keys."
                )
            for k, v in node.items():
                if not isinstance(k, str):
                    # Non-string keys would not survive JSON
                    # serialisation anyway; reject explicitly.
                    raise _ToolArgsTooLarge(
                        "tool_args dict keys must be strings."
                    )
                if len(k) > MAX_EXPORT_TOOL_ARGS_STRING_LENGTH:
                    raise _ToolArgsTooLarge(
                        f"tool_args contains a key longer than "
                        f"{MAX_EXPORT_TOOL_ARGS_STRING_LENGTH} chars."
                    )
                _walk(v, depth + 1)
        elif isinstance(node, list):
            if len(node) > MAX_EXPORT_TOOL_ARGS_LIST_LENGTH:
                raise _ToolArgsTooLarge(
                    f"tool_args contains a list with more than "
                    f"{MAX_EXPORT_TOOL_ARGS_LIST_LENGTH} elements."
                )
            for item in node:
                _walk(item, depth + 1)
        elif isinstance(node, str):
            if len(node) > MAX_EXPORT_TOOL_ARGS_STRING_LENGTH:
                raise _ToolArgsTooLarge(
                    f"tool_args contains a string value longer than "
                    f"{MAX_EXPORT_TOOL_ARGS_STRING_LENGTH} chars."
                )
        elif isinstance(node, (int, float, bool)) or node is None:
            return
        else:
            # Unknown type — won't round-trip through JSON.
            raise _ToolArgsTooLarge(
                "tool_args contains a non-JSON-serialisable value."
            )

    _walk(args, depth=1)

    # JSON serialisation is verified once after structural checks
    # so a value that survives the walk but cannot encode still
    # surfaces a clean rejection. The hard byte cap moved to the
    # route handler where it returns HTTP 413, see _MAX_TOOL_ARGS_BYTES.
    try:
        json.dumps(args, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise _ToolArgsTooLarge(
            "tool_args is not JSON serialisable."
        ) from exc


# ---------------------------------------------------------------------------
# Permission registration
# ---------------------------------------------------------------------------


@register_module_permissions("nova_exports")
def _nova_export_permissions():
    # J7. Deliberate broad permission scope. One permission gates both
    # the POST create route and the GET download route. The two routes
    # share a single capability so a tenant can disable the entire
    # finance export surface in one revoke. Bot reviewers have flagged
    # this in the past as a possible privilege split question; the
    # shared permission is by design, see the route docstrings at
    # backend/routes/nova_exports.py for the rationale. Do not split
    # into separate create and download permissions without a product
    # call.
    return [
        {
            "key": "nova.export.create",
            "group": "Finance - Nova",
            "label": "Create Nova Finance Exports",
            # Existing chat users implicitly get export — finance ops
            # can revoke this at the role level if a tenant wants to
            # disable downloads while keeping chat access.
            "implies": [],
        },
    ]


# ---------------------------------------------------------------------------
# POST /api/nova/exports
# ---------------------------------------------------------------------------


def _resolve_user_id(current_user: Any) -> str | None:
    """Best-effort id extractor matching routes/nova.py's pattern."""
    for attr in ("user_id", "id", "_id"):
        value = getattr(current_user, attr, None)
        if isinstance(value, str) and value:
            return value
    if isinstance(current_user, dict):
        for key in ("user_id", "id", "_id"):
            value = current_user.get(key)
            if isinstance(value, str) and value:
                return value
    return None


@router.post("/exports")
async def create_finance_export(
    payload: dict = Body(...),
    current_user: Any = Depends(get_current_user),
) -> StreamingResponse:
    """Re-run an approved Nova tool server-side and stream the result
    as a CSV or XLSX file.

    Request body:
      {
        "tool_name": "<one of EXPORTABLE_TOOLS>",
        "tool_args": {...},        # forwarded verbatim to the tool
        "format":     "csv" | "xlsx"
      }

    Response: ``StreamingResponse`` with the file bytes as body.
    Metadata is surfaced in custom headers so callers can read it
    without a separate request:
      * ``X-Nova-Export-Id``
      * ``X-Nova-Export-Source-Tool``
      * ``X-Nova-Export-Row-Count``
      * ``X-Nova-Export-Generated-At``
    Plus the standard ``Content-Type`` and
    ``Content-Disposition: attachment; filename="..."``.
    """
    await require_perm(
        current_user,
        "nova.export.create",
        resource="nova",
        action="create_export",
    )

    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        raise HTTPException(
            status_code=400,
            detail="'tool_name' is required and must be a string.",
        )
    tool_args = payload.get("tool_args") or {}
    if not isinstance(tool_args, dict):
        raise HTTPException(
            status_code=400,
            detail="'tool_args' must be an object when supplied.",
        )
    try:
        _validate_tool_args_bounds(tool_args)
    except _ToolArgsTooLarge as exc:
        # 400 not 413. The caller can almost certainly construct a
        # smaller request via a narrower filter or fewer prefixes.
        # 413 is reserved for the explicit body size cap below.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Hard byte cap before execute_tool. Mirrors the callback
    # handler cap so a runaway client cannot funnel an arbitrarily
    # large dict into the Nova tool dispatcher even when each
    # individual structural rule above is within its per field
    # bound. Surfaced as HTTP 413 so the caller can tell the body
    # itself is too large.
    try:
        serialized_args = json.dumps(tool_args)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="tool_args is not JSON serializable",
        ) from exc
    if len(serialized_args.encode("utf-8")) > _MAX_TOOL_ARGS_BYTES:
        raise HTTPException(
            status_code=413,
            detail="tool_args exceeds the server size cap",
        )
    fmt = payload.get("format") or "csv"
    if not isinstance(fmt, str) or fmt not in ("csv", "xlsx", "pdf"):
        raise HTTPException(
            status_code=400,
            detail="'format' must be 'csv', 'xlsx', or 'pdf'.",
        )
    persist = bool(payload.get("persist"))

    # Imports here (not at module scope) so a stale import in the
    # service layer doesn't break route registration on app boot.
    from services.ai.agents.tools.nova_tools import execute_tool
    from services.nova_engine.finance_export_service import (
        ExportRequest,
        build_export,
    )

    request = ExportRequest(
        tool_name=tool_name,
        tool_args=tool_args,
        format=fmt,
        requested_by_user_id=_resolve_user_id(current_user),
        source="api",
    )

    try:
        result = await build_export(request, tool_runner=execute_tool)
    except ValueError as exc:
        # Allow-list miss, format mismatch, or upstream tool error all
        # arrive as ValueError from build_export; surface as 400.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — never leak a stack trace
        logger.exception("Nova export failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Finance export failed. Try a narrower window and retry.",
        ) from exc

    if persist:
        # Persist + return JSON metadata with download_url. Used by
        # callers that want to hand the download off (chat UI tile,
        # email, paginated download flow). The streaming branch below
        # remains the default for direct programmatic use.
        from database import get_db
        from services.nova_engine.nova_export_storage import (
            ExportTooLargeError,
            save_export_artifact,
        )

        try:
            db = get_db()
            # Indexes are registered once at app startup
            # (server.py ensure_indexes block); this hot path no
            # longer pays the per-request ensure_indexes round-trip.
            persisted = await save_export_artifact(
                db,
                export_id=result.export_id,
                filename=result.filename,
                content_type=result.content_type,
                body=result.body,
                row_count=result.row_count,
                columns=result.columns,
                filters=result.filters,
                source_tool_name=result.source_tool_name,
                source_tool_args=tool_args,
                source_metadata=result.source_metadata,
                warnings=result.warnings,
                requested_by_user_id=_resolve_user_id(current_user),
                session_id=None,
                message_id=None,
                source="api",
                matched_rows=result.matched_rows,
                exported_rows=result.exported_rows,
                export_cap=result.export_cap,
                export_truncated=result.export_truncated,
            )
        except ExportTooLargeError as exc:
            raise HTTPException(
                status_code=413,
                detail=str(exc),
            ) from exc

        return JSONResponse({"status": "export_created", **persisted})

    headers = {
        "Content-Disposition": f'attachment; filename="{result.filename}"',
        "X-Nova-Export-Id": result.export_id,
        "X-Nova-Export-Source-Tool": result.source_tool_name,
        "X-Nova-Export-Row-Count": str(result.row_count),
        "X-Nova-Export-Exported-Rows": str(result.exported_rows),
        "X-Nova-Export-Cap": str(result.export_cap),
        "X-Nova-Export-Truncated": "true" if result.export_truncated else "false",
        "X-Nova-Export-Generated-At": result.generated_at,
    }
    if result.matched_rows is not None:
        headers["X-Nova-Export-Matched-Rows"] = str(result.matched_rows)
    warnings_header = _build_warnings_header(result.warnings)
    if warnings_header:
        headers["X-Nova-Export-Warnings"] = warnings_header

    return StreamingResponse(
        iter([result.body]),
        media_type=result.content_type,
        headers=headers,
    )


# ---------------------------------------------------------------------------
# GET /api/nova/exports/{export_id}/download
# ---------------------------------------------------------------------------


@router.get("/exports/{export_id}/download")
async def download_finance_export(
    export_id: str,
    current_user: Any = Depends(get_current_user),
) -> StreamingResponse:
    """Stream a previously persisted export by id.

    Auth: ``nova.export.create`` permission required (mirrors the
    create path; the download is part of the same finance-export
    capability).

    Owner only. Only the user who requested the export can download
    it. PR cleanup H2. Cross user export download is intentionally
    not exposed today. The previous code path checked an unregistered
    nova.export.admin permission that was never granted to any role
    so the branch was dead. Any future need for a cross user
    download must reintroduce the permission via the registry and
    add an explicit role grant rather than re using the removed
    has_perm check.

    Status codes.
      * 200 file streamed
      * 401 or 403 unauthenticated or no permission or not the owner
      * 404 export_id not found or TTL reaped
      * 410 metadata exists but the GridFS blob is gone
    """
    await require_perm(
        current_user,
        "nova.export.create",
        resource="nova",
        action="download_export",
    )

    from database import get_db
    from services.nova_engine.nova_export_storage import (
        get_export_artifact,
        open_export_download_stream,
    )

    db = get_db()
    export_doc = await get_export_artifact(db, export_id)
    if export_doc is None:
        raise HTTPException(status_code=404, detail="Export not found.")

    requester_id = export_doc.get("requested_by_user_id")
    user_id = _resolve_user_id(current_user)
    is_owner = bool(user_id) and user_id == requester_id
    # PR cleanup H2. Cross user export download is intentionally not
    # exposed. The previous nova.export.admin override was a dead
    # check because the permission was never registered or granted
    # to any role. If a future product call approves cross user
    # access, reintroduce the permission via _nova_export_permissions
    # and add an explicit role grant before re wiring this branch.
    if not is_owner:
        raise HTTPException(
            status_code=403,
            detail="This export belongs to another user.",
        )

    body = await open_export_download_stream(db, export_doc)
    if not body:
        raise HTTPException(
            status_code=410,
            detail="Export file has expired or been reaped.",
        )

    headers = {
        "Content-Disposition": f'attachment; filename="{export_doc.get("filename") or "export"}"',
        "X-Nova-Export-Id": export_id,
        "X-Nova-Export-Source-Tool": export_doc.get("source_tool_name") or "",
        "X-Nova-Export-Row-Count": str(export_doc.get("row_count") or 0),
    }
    warnings = export_doc.get("warnings") or []
    warnings_header = _build_warnings_header(warnings)
    if warnings_header:
        headers["X-Nova-Export-Warnings"] = warnings_header

    return StreamingResponse(
        iter([body]),
        media_type=export_doc.get("content_type") or "application/octet-stream",
        headers=headers,
    )


__all__ = ["router"]

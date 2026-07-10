"""GCP Error Reporting wrapper for AEGISAXIOM backend.

Single public entry point: ``report_exception(exc, context=None)``. Wraps the
``google-cloud-error-reporting`` SDK so callers don't have to import it
directly and don't crash if credentials/project_id are missing.

Decision: GCP Error Reporting was chosen over Sentry because AEGISAXIOM already
runs on GCP — Cloud Run, Cloud SQL, etc. Native integration is lighter
weight (no new vendor account, no DSN management, IAM is the auth surface)
and the existing GCP logging/monitoring dashboards can pivot directly to
reported errors.

Initialization:
    Read ``GCP_PROJECT_ID`` from env at module import. If unset (local dev,
    test runs, CI without GCP creds), the wrapper becomes a no-op that
    logs the exception via the stdlib logger and returns silently. Never
    raises from ``report_exception`` — observability must not be able to
    take down the request path.

Wired into:
    - FastAPI's global exception handler in server.py (uncaught errors in
      routes).
    - AI tool handlers in conversational_tools.py — each ``_handle_*``
      reports its own exception path so we keep tool_name + tier in the
      context.
    - ``crud_envelope`` post-success failures (audit/event emission).

Context payload conventions (passed via ``context`` dict):
    - ``user_id``, ``user_email`` — actor identity if available.
    - ``request_id`` — when correlated with an HTTP request.
    - ``tool_name``, ``tier`` — for AI tool failures.
    - ``module``, ``entity``, ``action`` — for crud_envelope failures.
    - ``http_status``, ``http_method``, ``http_path`` — for route handler
      failures (server.py sets these).

The SDK serializes the exception's traceback automatically — context is
attached as a ``user`` string and ``http_context`` when applicable.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


_CLIENT: Optional[Any] = None
_INITIALIZED: bool = False


def _init_client() -> Optional[Any]:
    """Lazily build the Error Reporting client.

    Returns None if ``GCP_PROJECT_ID`` is unset OR the SDK isn't installed
    (deferred import so the dependency is optional in local/test envs).
    Caches the result — repeated calls are cheap.
    """
    global _CLIENT, _INITIALIZED
    if _INITIALIZED:
        return _CLIENT

    _INITIALIZED = True
    project_id = os.getenv("GCP_PROJECT_ID")
    if not project_id:
        logger.info(
            "observability: GCP_PROJECT_ID not set — report_exception will "
            "no-op (errors still logged via stdlib logger)."
        )
        return None

    try:
        from google.cloud import error_reporting  # type: ignore
    except ImportError:
        logger.warning(
            "observability: google-cloud-error-reporting not installed; "
            "report_exception will no-op. Install the package to enable "
            "GCP Error Reporting."
        )
        return None

    try:
        service_name = os.getenv("GCP_ERROR_REPORTING_SERVICE", "aegisaxiom-backend")
        service_version = os.getenv("GCP_ERROR_REPORTING_VERSION", "unknown")
        _CLIENT = error_reporting.Client(
            project=project_id,
            service=service_name,
            version=service_version,
        )
        logger.info(
            "observability: GCP Error Reporting initialized (project=%s service=%s).",
            project_id, service_name,
        )
    except Exception as e:  # noqa: BLE001
        # Credential failures, project access issues, etc. Stay no-op.
        logger.warning(
            "observability: GCP Error Reporting client init failed (%s); "
            "report_exception will no-op.", e,
        )
        _CLIENT = None

    return _CLIENT


def report_exception(exc: Exception, context: Optional[dict] = None) -> None:
    """Report an exception to GCP Error Reporting.

    Must not raise — observability failures should never surface as
    application errors. If the client isn't available, logs the exception
    via the stdlib logger and returns.

    ``context`` is a free-form dict of correlation IDs (user_id, tool_name,
    request_id, etc.); it's serialized into the error report's ``user``
    string so the Error Reporting UI groups related failures together.
    """
    client = _init_client()
    if client is None:
        # Local/test/no-creds path — log so it's still visible.
        logger.error(
            "observability(noop): %s — context=%s",
            repr(exc), context or {},
            exc_info=exc,
        )
        return

    try:
        # The SDK's `report_exception` reads sys.exc_info() implicitly; we
        # prefer `report()` so we control the message and attach context
        # even when called outside an `except` block.
        message = f"{type(exc).__name__}: {exc}"
        if context:
            ctx_str = ", ".join(f"{k}={v}" for k, v in context.items() if v is not None)
            if ctx_str:
                message = f"{message} [{ctx_str}]"

        user_id = (context or {}).get("user_id")
        http_context_kwargs = {}
        if context:
            method = context.get("http_method")
            url = context.get("http_path")
            status = context.get("http_status")
            if method or url or status:
                try:
                    from google.cloud.error_reporting import HTTPContext  # type: ignore
                    http_context_kwargs["http_context"] = HTTPContext(
                        method=method or "",
                        url=url or "",
                        response_status_code=status or 0,
                    )
                except Exception:  # noqa: BLE001
                    pass

        # report() captures the current traceback when called from inside
        # an except block. When called via FastAPI's exception handler
        # we ARE inside one — the SDK reads sys.exc_info().
        client.report(
            message,
            user=str(user_id) if user_id else None,
            **http_context_kwargs,
        )
    except Exception as e:  # noqa: BLE001
        # Last-resort guard: never let a reporting failure escape.
        logger.warning("observability: report_exception itself failed: %s", e)

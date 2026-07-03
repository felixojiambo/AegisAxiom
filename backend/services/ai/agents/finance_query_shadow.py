"""Shadow-mode logger for the Finance Query Planner.

Gated by the ``NOVA_FINANCE_QUERY_PLANNER_MODE`` env var (canonical).
``NOVA_FINANCE_QUERY_PLANNER`` is accepted as a legacy alias; when
the canonical variable is set, the legacy alias is ignored. When
both are unset the helper is a no-op — current Nova routing
answers as before, no extra log lines, no extra Mongo. When
enabled, every Nova user message also runs through plan → validate
→ compile and the result is emitted to the logger so we can compare
the planner's plan against what the existing tool loop actually
did.

Two important properties:

  1. The shadow path NEVER mutates state, dispatches a tool, or talks
     to Mongo. It runs purely on the user message string + a small
     amount of conversation context.
  2. Failure inside the shadow path MUST NOT break the user's reply.
     Wrapped in try/except; on any error the shadow logger swallows
     the exception with a logger.warning and returns.

When we're ready to flip the planner from shadow to live, the call
site in conductor (``services/ai/conductor.py``) is the single place
that needs to change. Until then the only effect of enabling shadow
mode is one structured log line per Nova turn.
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import date
from typing import Any, Optional

from .finance_query_compiler import compile_finance_plan
from .finance_query_planner import plan_finance_query
from .finance_query_validator import validate_finance_plan


logger = logging.getLogger(__name__)


# Canonical env var. Takes precedence when set.
_ENV_FLAG = "NOVA_FINANCE_QUERY_PLANNER_MODE"
# Legacy alias kept for backward compatibility with the first slice
# (commit c6e94fd) which shipped the un-suffixed name. Read only when
# the canonical variable is unset.
_LEGACY_ENV_FLAG = "NOVA_FINANCE_QUERY_PLANNER"
_VALID_MODES = {"off", "shadow", "live"}


def get_planner_mode() -> str:
    """Return the configured planner mode.

    Resolution order:
      1. ``NOVA_FINANCE_QUERY_PLANNER_MODE`` (canonical) when set.
      2. ``NOVA_FINANCE_QUERY_PLANNER`` (legacy alias) when canonical
         is unset.
      3. ``"off"`` (default) when both are unset.

    ``off`` (default) — planner inert, no logging.
    ``shadow`` — planner runs alongside Nova's existing routing and
        logs the plan/compiled call; user-facing behavior unchanged.
    ``live`` — reserved for the next slice; today behaves like
        ``shadow`` and emits a warning so misconfiguration is loud.
    Unknown values fall safely back to ``off`` with a warning.
    """
    canonical_raw = os.environ.get(_ENV_FLAG)
    legacy_raw = os.environ.get(_LEGACY_ENV_FLAG)

    if canonical_raw is not None:
        source_var = _ENV_FLAG
        raw = canonical_raw.strip().lower()
    elif legacy_raw is not None:
        source_var = _LEGACY_ENV_FLAG
        raw = legacy_raw.strip().lower()
        logger.info(
            "%s is set but the canonical variable %s is preferred. "
            "Treating it as a legacy alias.",
            _LEGACY_ENV_FLAG,
            _ENV_FLAG,
        )
    else:
        return "off"

    if raw not in _VALID_MODES:
        logger.warning(
            "%s=%r is not one of %s; treating as 'off'.",
            source_var,
            raw,
            sorted(_VALID_MODES),
        )
        return "off"
    if raw == "live":
        logger.warning(
            "%s=live is reserved for the next slice and behaves like "
            "'shadow' today. The compiler is not yet wired into the "
            "conductor's tool-call dispatch.",
            source_var,
        )
        return "shadow"
    return raw


def is_shadow_enabled() -> bool:
    return get_planner_mode() == "shadow"


def run_shadow_evaluation(
    user_message: str,
    *,
    runtime_today: Optional[date] = None,
    has_prior_total: bool = False,
    available_tools: Optional[set[str]] = None,
    session_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Run the planner pipeline and emit a structured log line.

    Returns the structured payload (a dict suitable for further
    inspection in tests) on success. Returns None when shadow mode is
    not enabled. Never raises — failures are logged and swallowed so
    a planner bug can't bring down the chat reply.
    """
    if not is_shadow_enabled():
        return None
    try:
        plan = plan_finance_query(
            user_message,
            runtime_today=runtime_today,
            has_prior_total=has_prior_total,
            available_tools=available_tools,
        )
        validation = validate_finance_plan(plan, available_tools=available_tools)
        compiled = compile_finance_plan(plan, available_tools=available_tools)
        # Plan filters can carry finance PII (customer names, search
        # strings, GL class labels). Surface only the FIELD and OP so
        # the routing decision can be audited without putting values
        # into the log line.
        filter_shapes = [
            {"field": f.field.value, "op": f.op.value}
            for f in plan.filters
        ]
        payload = {
            "session_id": session_id,
            "intent": plan.intent.value,
            "metric": plan.metric.value if plan.metric else None,
            "basis": plan.basis.value,
            "category": plan.category.value if plan.category else None,
            "detail_level": plan.detail_level.value,
            "date_from": plan.date_from,
            "date_to": plan.date_to,
            "year_was_inferred": plan.year_was_inferred,
            "filter_shapes": filter_shapes,
            "validation": {
                "is_valid": validation.is_valid,
                "errors": validation.errors,
                "warnings": validation.warnings,
                "needs_clarification": validation.needs_clarification,
                "clarification_question": validation.clarification_question,
            },
            "compiled": {
                # tool_args intentionally omitted — they carry the same
                # filter values we redacted above. Keep tool_name and
                # is_supported so misroutes are detectable from the log.
                "tool_name": compiled.tool_name,
                "is_supported": compiled.is_supported,
                "unsupported_reason": compiled.unsupported_reason,
            },
        }
        logger.info("[finance_query_planner shadow] %s", payload)
        return payload
    except Exception as e:  # noqa: BLE001 — shadow path must not crash chat
        # The user message is finance PII verbatim. Hash it (truncated
        # SHA-256, hex) so two failures on the same message correlate
        # in the log without exposing the message itself.
        message_token = (
            "sha256:" + hashlib.sha256(user_message.encode("utf-8")).hexdigest()[:12]
            if isinstance(user_message, str)
            else "non-string-message"
        )
        logger.warning(
            "[finance_query_planner shadow] swallowed error: %s "
            "(message_token=%s)",
            e,
            message_token,
        )
        return None


__all__ = [
    "get_planner_mode",
    "is_shadow_enabled",
    "run_shadow_evaluation",
]

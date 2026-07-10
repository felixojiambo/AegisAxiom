"""Vega KPI event tracking.

Canonical event_type catalog (Wave 5 reference — keep in sync with the
dashboards in .waves/vega-v2/wave-5-plan.md):

  Discovery flow:
    transcript_analyzed         {call_number, upload_count}
    matrix_built                {workflow_count, gap_count}
    session_finalized           {workflow_count, design_doc_count, scored_count}
    discovery_mode_selected     {mode, upload_count}
    freeflow_graduation_offered {turn_count, trigger_reason}
    freeflow_graduation_accepted {upload_id, transcript_chars}
    freeflow_fastpath_dispatched {upload_count}

  Per-workflow:
    mmi_scored                  {score}
    bpmn_generated              {workflow_id, bpmn_id}
    bpmn_regen_skipped          {workflow_id, reason="content_hash_match"}

  Per-app:
    eadd_generated              {app_id, latency_ms, success}

  Export:
    export_triggered            {format, ...}     (mmi_csv | design_docs |
                                                   app_designs | zip |
                                                   google_doc)
"""
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def emit_vega_event(db, user_id: str, session_id: str, event_type: str, metadata: dict = None):
    """Emit an Vega KPI event. Fire-and-forget — never raises.

    Skips emission when session_id is falsy: pre-session paths (e.g.
    graduate-freeflow before an vega_discovery_session exists) would otherwise
    pollute analytics with empty-session rows that can't be joined back.
    """
    if not session_id:
        logger.debug("Skipping Vega event %s: no session_id", event_type)
        return
    try:
        # Store created_at as a real datetime so the TTL index in server.py
        # can age these out — MongoDB TTL doesn't fire on ISO-string fields.
        event = {
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "session_id": session_id,
            "user_id": user_id,
            "event_type": event_type,
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc),
        }
        await db.vega_events.insert_one(event)
        logger.debug("Vega event: %s for session %s", event_type, session_id)
    except Exception as e:
        logger.warning("Failed to emit Vega event %s: %s", event_type, e)

"""Vega v3 regenerate-from-open-items contract (Wave 5 / Thread 5a).

This module owns three pieces of contract that the rest of Wave 5 binds to:

  1. ``RegenScope`` — the shape the pipeline reads to decide *which* entities
     need to re-extract when an OpenItem is answered.
  2. ``compute_blast_radius`` / ``compute_combined_blast_radius`` — the pure
     functions that walk an answered OpenItem's ``location`` field back to
     workflow_ids / app_ids that depend on it.
  3. ``persist_artifact_version`` — the write helper for the new
     ``vega_artifact_versions`` collection (90-day TTL, monotonic version
     numbering per artifact_id).

Wave 5b (``run_v3_pipeline_partial``) consumes RegenScope. Wave 5c (the
``/regenerate`` endpoint) consumes both. Wave 5d (frontend Open Items inline
answer UX) never sees this module directly — it talks to the API.

What this module deliberately does NOT do:

  * No LLM calls. Blast-radius is structural — it walks Pydantic models.
  * No mutation of the existing artifact. Versions are append-only; the
    chat session points at the latest by reading the highest ``version``
    from the collection.
  * No re-shaping of OpenItem. ``location`` is the locked Wave 2 contract
    (``session`` / ``workflow:<id>`` / ``app:<id>`` / ``shared_infra``).

The version numbering uses a single ``find_one_and_update`` on a per-artifact
counter document so concurrent regenerations cannot double-issue a version
(see ``persist_artifact_version`` for the mechanic).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from services.vega.models_v3 import DiscoveryAnalysisV3, OpenItem

logger = logging.getLogger(__name__)

__all__ = [
    "RegenScope",
    "compute_blast_radius",
    "compute_combined_blast_radius",
    "persist_artifact_version",
]


# ──────────────────────────────────────────────────────────────────────
# Contract shape
# ──────────────────────────────────────────────────────────────────────


class RegenScope(BaseModel):
    """Identifies which entities need re-extraction after an OpenItem is answered.

    Wave 5b reads this to skip Stage 1 entirely and only re-run Stage 2a/2b
    + Stage 3 for the listed workflow_ids / app_ids. Stage 4 (post-process)
    always re-runs over the merged result so derived rows like
    shared_infrastructure stay in sync.

    Fields:
      * ``workflow_ids``: workflows whose detail / BPMN / design-doc must
        be re-extracted. Empty when the answer was session-scoped.
      * ``app_ids``: apps whose detail / EADD must be re-extracted.
      * ``rerun_shared_infra``: True iff any answered item changed an
        app's integrations (shared infra detection scans App.integrations
        across all apps; if any app's integrations may have changed, the
        derived rows must be recomputed).
      * ``rerun_open_items``: always True. Stage 4's open-items aggregator
        walks the merged artifact every time so newly-answered fields stop
        appearing as flagged and brand-new gaps surface immediately.
    """

    workflow_ids: list[str] = Field(
        default_factory=list,
        description="Workflow IDs that must be re-extracted (Stage 2a + Stage 3).",
    )
    app_ids: list[str] = Field(
        default_factory=list,
        description="App IDs that must be re-extracted (Stage 2b + Stage 3).",
    )
    rerun_shared_infra: bool = Field(
        default=False,
        description="True iff the answer changes any app's integrations. Stage 4's "
        "shared-infrastructure detector re-runs unconditionally when this is set.",
    )
    rerun_open_items: bool = Field(
        default=True,
        description="Always True. Stage 4's open-items aggregator must re-walk the "
        "merged artifact so answered items stop appearing and new gaps surface.",
    )


# ──────────────────────────────────────────────────────────────────────
# Blast-radius computation
# ──────────────────────────────────────────────────────────────────────


def _empty_scope() -> RegenScope:
    """Empty scope — no entity rerun needed; only Stage 4 will rerun."""
    return RegenScope(
        workflow_ids=[],
        app_ids=[],
        rerun_shared_infra=False,
        rerun_open_items=True,
    )


def compute_blast_radius(
    open_item: OpenItem,
    artifact: DiscoveryAnalysisV3,
) -> RegenScope:
    """Map an answered OpenItem to the entities that need re-extraction.

    The OpenItem.location field has a stable, locked format (Wave 2):

      * ``"session"`` — a SessionFlags-level item. SessionFlags don't
        trigger any LLM rerun: the orchestrator handles session-flag
        updates by direct field write. This function returns an empty
        scope (no workflow_ids, no app_ids) because Stage 2 never needs
        to re-run for a session-level answer.
      * ``"workflow:<workflow_id>"`` — a single workflow's detail / BPMN
        is affected. Only that workflow re-extracts. shared_infra does
        not need a rerun because workflow content doesn't drive the
        shared-infrastructure table.
      * ``"app:<app_id>"`` — that app re-extracts, plus every workflow
        whose ``linked_app_ids`` includes the answered app (because
        workflow-level text references the app's role and may need to
        be re-described). ``rerun_shared_infra`` is True because the
        app's ``integrations`` list may change.
      * ``"shared_infra"`` — every app that uses the answered system
        re-extracts (the LLM may have new context about the shared
        system's role); ``rerun_shared_infra`` is True. The system_name
        is recovered by parsing the OpenItem.label.

    Args:
        open_item: The OpenItem the user just answered.
        artifact: The current DiscoveryAnalysisV3 — used only to look up
            cross-entity links (linked_app_ids, used_by_app_ids).

    Returns:
        ``RegenScope`` describing which workflow_ids / app_ids must be
        re-extracted, and whether shared_infra needs a rerun.

    Raises:
        ValueError: when ``open_item.location`` doesn't match one of the
            four documented prefixes. The location format is part of the
            Wave 2 contract and unknown values are a programmer error,
            not a runtime condition we should silently absorb.
    """
    location = (open_item.location or "").strip()

    if location == "session":
        return _empty_scope()

    if location == "shared_infra":
        return _scope_for_shared_infra(open_item, artifact)

    if location.startswith("workflow:"):
        wf_id = location[len("workflow:"):].strip()
        if not wf_id:
            raise ValueError(
                f"OpenItem.location {location!r} is missing a workflow_id after the prefix"
            )
        return RegenScope(
            workflow_ids=[wf_id],
            app_ids=[],
            rerun_shared_infra=False,
            rerun_open_items=True,
        )

    if location.startswith("app:"):
        app_id = location[len("app:"):].strip()
        if not app_id:
            raise ValueError(
                f"OpenItem.location {location!r} is missing an app_id after the prefix"
            )
        # An app answer pulls in every workflow that references the app
        # in its linked_app_ids. Workflow text frequently references the
        # app by role, so we re-extract those workflows so the LLM can
        # incorporate the new context.
        affected_workflows = sorted({
            wf.workflow_id
            for wf in artifact.workflows
            if app_id in (wf.linked_app_ids or [])
        })
        return RegenScope(
            workflow_ids=affected_workflows,
            app_ids=[app_id],
            rerun_shared_infra=True,
            rerun_open_items=True,
        )

    raise ValueError(
        f"OpenItem.location {location!r} does not match a recognised prefix "
        "(expected 'session', 'workflow:<id>', 'app:<id>', or 'shared_infra')"
    )


def _scope_for_shared_infra(
    open_item: OpenItem,
    artifact: DiscoveryAnalysisV3,
) -> RegenScope:
    """Compute the blast radius for a ``location == 'shared_infra'`` answer.

    The label produced by ``aggregate_open_items`` for shared-infra rows
    is currently empty in the postprocess module (the rolled-up
    SharedInfrastructureRow itself doesn't emit OpenItems yet — that's
    a future hook). To stay forward-compatible, we treat the answer as
    "this affects the shared infrastructure table as a whole", which
    means every app that participates in any shared_infrastructure row
    is in the blast radius.

    If the OpenItem.label includes a system_name (for example
    ``"Shared infra: NetSuite — read/write unclear"``), we narrow the
    blast radius to only the apps using that system. Match is
    case-insensitive substring on the canonical ``system_name``.
    """
    label_upper = (open_item.label or "").upper()
    detail_upper = (open_item.detail or "").upper()

    affected_app_ids: set[str] = set()
    for row in artifact.shared_infrastructure:
        sys_upper = (row.system_name or "").upper()
        if not sys_upper:
            continue
        # Match the system_name against either the label or the detail —
        # the postprocess module embeds the system_name in both for
        # shared-infra-derived items.
        if sys_upper in label_upper or sys_upper in detail_upper:
            for aid in row.used_by_app_ids:
                affected_app_ids.add(aid)

    if not affected_app_ids:
        # Couldn't pin a specific system from the label/detail. Fall back
        # to "all apps that participate in any shared row" so the rerun
        # at least covers the surface area.
        for row in artifact.shared_infrastructure:
            for aid in row.used_by_app_ids:
                affected_app_ids.add(aid)

    return RegenScope(
        workflow_ids=[],
        app_ids=sorted(affected_app_ids),
        rerun_shared_infra=True,
        rerun_open_items=True,
    )


def compute_combined_blast_radius(
    answered_items: list[tuple[int, str]],
    artifact: DiscoveryAnalysisV3,
) -> RegenScope:
    """Union the blast radius across multiple answered OpenItems.

    Used by the ``/regenerate`` endpoint when the user submits multiple
    answers in one request (queued in the Open Items tab). Each answer is
    a ``(open_item_index, value)`` tuple — we ignore ``value`` here (the
    pipeline consumes it via ``user_provided_answers`` later) and resolve
    the index against ``artifact.open_items``.

    Union rules:
      * ``workflow_ids`` — set union across per-item scopes
      * ``app_ids``      — set union across per-item scopes
      * ``rerun_shared_infra`` — OR across per-item scopes
      * ``rerun_open_items`` — always True (Stage 4 re-runs unconditionally)

    Args:
        answered_items: List of ``(open_item_index, value)`` tuples. The
            value is unused here; signature mirrors the API request body
            so callers don't need a transformation step.
        artifact: The current DiscoveryAnalysisV3 used for index lookups.

    Returns:
        The union RegenScope.

    Raises:
        IndexError: when an open_item_index is out of range for
            ``artifact.open_items``. The endpoint should surface this as
            HTTP 400 (the user submitted a stale form against a newer
            artifact version).
        ValueError: propagated from ``compute_blast_radius`` when any
            answered item's location string is malformed.
    """
    workflow_ids: set[str] = set()
    app_ids: set[str] = set()
    rerun_shared_infra = False

    for index, _value in answered_items:
        if index < 0 or index >= len(artifact.open_items):
            raise IndexError(
                f"open_item_index {index} out of range "
                f"(artifact has {len(artifact.open_items)} open_items)"
            )
        item = artifact.open_items[index]
        scope = compute_blast_radius(item, artifact)
        workflow_ids.update(scope.workflow_ids)
        app_ids.update(scope.app_ids)
        rerun_shared_infra = rerun_shared_infra or scope.rerun_shared_infra

    return RegenScope(
        workflow_ids=sorted(workflow_ids),
        app_ids=sorted(app_ids),
        rerun_shared_infra=rerun_shared_infra,
        rerun_open_items=True,
    )


# ──────────────────────────────────────────────────────────────────────
# Versioned artifact storage
# ──────────────────────────────────────────────────────────────────────


# Collection name kept here so callers don't hardcode the string in three
# places (server.py for index registration, this module for the writer,
# and Wave 5c for the read path).
VEGA_ARTIFACT_VERSIONS_COLLECTION = "vega_artifact_versions"

# Companion counter collection — one document per artifact_id, used by
# ``find_one_and_update`` with ``$inc: {next_version: 1}`` to atomically
# allocate the next version number under concurrent writes.
_VEGA_ARTIFACT_VERSION_COUNTERS = "vega_artifact_version_counters"


async def persist_artifact_version(
    db,
    artifact_id: str,
    parent_version: Optional[int],
    regen_trigger: dict,
    regen_scope: RegenScope,
    payload: dict,
    user_id: str,
) -> int:
    """Insert a new version row and return the assigned version number.

    The version number is allocated atomically by ``find_one_and_update``
    on a per-artifact counter document, so two regenerations submitted
    concurrently can never collide on the same version number. Even if
    the version row insert fails after the counter increment, we leak a
    version number rather than risking duplication.

    Args:
        db: Mongo client (motor).
        artifact_id: The chat artifact this is a version of.
        parent_version: The version this regeneration was based on, or
            None for v1 (the original artifact). Wave 5c is responsible
            for resolving the latest version before calling this helper.
        regen_trigger: ``{"open_item_indices": [...], "answers": [...],
            "new_upload_ids": [...]}`` — what the user submitted that
            caused this regen. Stored verbatim for audit.
        regen_scope: The ``RegenScope`` used by this regen run. Serialised
            via ``model_dump`` for storage.
        payload: ``DiscoveryAnalysisV3.model_dump(mode="json")``.
        user_id: User who triggered the regen — for audit.

    Returns:
        The assigned version number (1 for the first version of an
        artifact, 2 for the second, ...).
    """
    counter = await db[_VEGA_ARTIFACT_VERSION_COUNTERS].find_one_and_update(
        {"artifact_id": artifact_id},
        {
            "$inc": {"next_version": 1},
            "$setOnInsert": {"artifact_id": artifact_id},
        },
        upsert=True,
        return_document=True,  # motor accepts the bool form too; explicit for clarity
    )

    # ``find_one_and_update`` with ``return_document=True`` returns the
    # post-update document. ``next_version`` is monotonically increasing.
    # First call on a fresh artifact: counter doc is inserted with
    # next_version=1 and returned with next_version=1. Subsequent calls
    # bump it. We use the returned value as the version number so the
    # first row written gets version=1.
    if not counter or "next_version" not in counter:
        # Defensive: motor's behaviour on upsert+return_document=True is
        # well-defined, but if a Mongo flavour ever returns None we read
        # back explicitly to avoid issuing a duplicate version.
        logger.warning(
            "vega_artifact_versions counter for %s returned no doc — reading back",
            artifact_id,
        )
        counter = await db[_VEGA_ARTIFACT_VERSION_COUNTERS].find_one(
            {"artifact_id": artifact_id}
        )
    version_number: int = int(counter["next_version"])

    doc = {
        "artifact_id": artifact_id,
        "version": version_number,
        "parent_version": parent_version,
        "regen_trigger": regen_trigger,
        "regen_scope": regen_scope.model_dump(mode="json"),
        "payload": payload,
        "created_at": datetime.now(timezone.utc),
        "user_id": user_id,
    }
    await db[VEGA_ARTIFACT_VERSIONS_COLLECTION].insert_one(doc)

    logger.info(
        "vega_artifact_versions: persisted version=%d for artifact_id=%s "
        "(parent=%s, user=%s, scope=%dwf/%dapp)",
        version_number,
        artifact_id,
        parent_version,
        user_id,
        len(regen_scope.workflow_ids),
        len(regen_scope.app_ids),
    )
    return version_number

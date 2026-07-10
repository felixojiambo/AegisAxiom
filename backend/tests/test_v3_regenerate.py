"""Unit tests for Vega v3 regenerate-from-open-items contract (Wave 5 / 5a).

Covers:
  * compute_blast_radius — every OpenItem.location prefix
  * compute_combined_blast_radius — multi-item union, dedup, mixed scopes
  * persist_artifact_version — monotonic version, parent threading,
    concurrent-insert atomicity (no two version numbers collide)

Run with:
    docker compose exec backend pytest tests/test_v3_regenerate.py -v
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict
from typing import Any
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("JWT_SECRET", "test-secret")

from services.vega.flags import VegaFlag
from services.vega.models_v3 import (
    App,
    DiscoveryAnalysisV3,
    MMIDimension,
    MMIHypothesis,
    OpenItem,
    SessionFlags,
    SharedInfrastructureRow,
    SourceProvenance,
    Workflow,
)
from services.vega.regenerate import (
    VEGA_ARTIFACT_VERSIONS_COLLECTION,
    RegenScope,
    compute_blast_radius,
    compute_combined_blast_radius,
    persist_artifact_version,
)


# ──────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ──────────────────────────────────────────────────────────────────────


def _source() -> SourceProvenance:
    return SourceProvenance()


def _mmi() -> MMIHypothesis:
    dim = MMIDimension(value=1, confidence="low", reasoning="seed")
    return MMIHypothesis(
        tss=dim, ie=dim, time=dim, cost=dim, quality=dim, satisfaction=dim,
        needs_client_input=[], score=None,
    )


def _wf(wf_id: str, linked_app_ids: list[str] | None = None) -> Workflow:
    return Workflow(
        workflow_id=wf_id,
        workflow_name=f"Workflow {wf_id}",
        source=_source(),
        mmi_hypothesis=_mmi(),
        linked_app_ids=linked_app_ids or [],
    )


def _app(app_id: str, linked_workflow_ids: list[str] | None = None) -> App:
    return App(
        app_id=app_id,
        app_name=f"App {app_id}",
        source=_source(),
        linked_workflow_ids=linked_workflow_ids or [],
    )


def _open_item(
    location: str,
    label: str = "test item",
    flag: VegaFlag = VegaFlag.MISSING,
    detail: str | None = None,
) -> OpenItem:
    return OpenItem(flag=flag, label=label, location=location, detail=detail)


def _artifact(
    workflows: list[Workflow] | None = None,
    apps: list[App] | None = None,
    shared_infra: list[SharedInfrastructureRow] | None = None,
    open_items: list[OpenItem] | None = None,
) -> DiscoveryAnalysisV3:
    return DiscoveryAnalysisV3(
        client_company="ACME",
        workflows=workflows or [],
        apps=apps or [],
        shared_infrastructure=shared_infra or [],
        open_items=open_items or [],
    )


# ──────────────────────────────────────────────────────────────────────
# compute_blast_radius
# ──────────────────────────────────────────────────────────────────────


class TestComputeBlastRadius:
    def test_session_location_returns_empty_scope(self):
        artifact = _artifact(workflows=[_wf("wf_a")], apps=[_app("app_a")])
        item = _open_item(location="session", label="Tech contact missing")

        scope = compute_blast_radius(item, artifact)

        assert scope.workflow_ids == []
        assert scope.app_ids == []
        assert scope.rerun_shared_infra is False
        # rerun_open_items always True — Stage 4 always re-runs
        assert scope.rerun_open_items is True

    def test_workflow_location_targets_only_that_workflow(self):
        artifact = _artifact(
            workflows=[_wf("wf_a"), _wf("wf_b"), _wf("wf_c")],
            apps=[_app("app_a")],
        )
        item = _open_item(location="workflow:wf_b")

        scope = compute_blast_radius(item, artifact)

        assert scope.workflow_ids == ["wf_b"]
        assert scope.app_ids == []
        # Workflow content never drives shared_infra — that's an app-side concern
        assert scope.rerun_shared_infra is False
        assert scope.rerun_open_items is True

    def test_app_location_with_linked_workflows(self):
        # wf_a and wf_c link to app_x; wf_b does not. Answer pulls in app_x
        # plus the two workflows that reference it.
        artifact = _artifact(
            workflows=[
                _wf("wf_a", linked_app_ids=["app_x"]),
                _wf("wf_b", linked_app_ids=["app_y"]),
                _wf("wf_c", linked_app_ids=["app_x", "app_y"]),
            ],
            apps=[_app("app_x"), _app("app_y")],
        )
        item = _open_item(location="app:app_x")

        scope = compute_blast_radius(item, artifact)

        assert scope.app_ids == ["app_x"]
        assert scope.workflow_ids == ["wf_a", "wf_c"]  # sorted dedup
        assert scope.rerun_shared_infra is True
        assert scope.rerun_open_items is True

    def test_app_location_standalone_no_linked_workflows(self):
        # app_solo has no workflows referencing it; no workflow_ids, but
        # rerun_shared_infra still True because app integrations may have changed.
        artifact = _artifact(
            workflows=[_wf("wf_a", linked_app_ids=["app_other"])],
            apps=[_app("app_solo"), _app("app_other")],
        )
        item = _open_item(location="app:app_solo")

        scope = compute_blast_radius(item, artifact)

        assert scope.app_ids == ["app_solo"]
        assert scope.workflow_ids == []
        assert scope.rerun_shared_infra is True

    def test_shared_infra_location_with_system_name_in_label(self):
        # Two shared rows; only NetSuite matches the label.
        artifact = _artifact(
            apps=[_app("app_a"), _app("app_b"), _app("app_c")],
            shared_infra=[
                SharedInfrastructureRow(
                    system_name="NetSuite",
                    used_by_app_ids=["app_a", "app_b"],
                ),
                SharedInfrastructureRow(
                    system_name="Salesforce",
                    used_by_app_ids=["app_c"],
                ),
            ],
        )
        item = _open_item(
            location="shared_infra",
            label="Shared infra: NetSuite — read/write unclear",
        )

        scope = compute_blast_radius(item, artifact)

        assert scope.workflow_ids == []
        assert scope.app_ids == ["app_a", "app_b"]
        assert scope.rerun_shared_infra is True

    def test_shared_infra_location_no_match_falls_back_to_all_shared_apps(self):
        # If the system_name can't be pinned from the label, fall back to
        # every app participating in any shared row.
        artifact = _artifact(
            apps=[_app("app_a"), _app("app_b"), _app("app_c")],
            shared_infra=[
                SharedInfrastructureRow(
                    system_name="NetSuite",
                    used_by_app_ids=["app_a", "app_b"],
                ),
                SharedInfrastructureRow(
                    system_name="Salesforce",
                    used_by_app_ids=["app_b", "app_c"],
                ),
            ],
        )
        item = _open_item(location="shared_infra", label="generic shared item")

        scope = compute_blast_radius(item, artifact)

        assert scope.app_ids == ["app_a", "app_b", "app_c"]
        assert scope.rerun_shared_infra is True

    def test_unknown_prefix_raises_value_error(self):
        artifact = _artifact()
        item = _open_item(location="garbage:xyz")

        with pytest.raises(ValueError, match="recognised prefix"):
            compute_blast_radius(item, artifact)

    def test_workflow_prefix_with_empty_id_raises(self):
        artifact = _artifact()
        item = _open_item(location="workflow:")

        with pytest.raises(ValueError, match="missing a workflow_id"):
            compute_blast_radius(item, artifact)

    def test_app_prefix_with_empty_id_raises(self):
        artifact = _artifact()
        item = _open_item(location="app:")

        with pytest.raises(ValueError, match="missing an app_id"):
            compute_blast_radius(item, artifact)


# ──────────────────────────────────────────────────────────────────────
# compute_combined_blast_radius
# ──────────────────────────────────────────────────────────────────────


class TestCombinedBlastRadius:
    def test_two_workflow_items_unions_workflow_ids(self):
        items = [
            _open_item(location="workflow:wf_a"),
            _open_item(location="workflow:wf_b"),
        ]
        artifact = _artifact(
            workflows=[_wf("wf_a"), _wf("wf_b")],
            open_items=items,
        )

        scope = compute_combined_blast_radius([(0, "ans1"), (1, "ans2")], artifact)

        assert scope.workflow_ids == ["wf_a", "wf_b"]
        assert scope.app_ids == []
        assert scope.rerun_shared_infra is False

    def test_same_workflow_twice_dedups(self):
        items = [
            _open_item(location="workflow:wf_a", label="first"),
            _open_item(location="workflow:wf_a", label="second"),
        ]
        artifact = _artifact(workflows=[_wf("wf_a")], open_items=items)

        scope = compute_combined_blast_radius([(0, "x"), (1, "y")], artifact)

        assert scope.workflow_ids == ["wf_a"]

    def test_mixed_workflow_and_app_items(self):
        # Item 0: workflow wf_b (only that workflow)
        # Item 1: app app_x (pulls in wf_a + wf_c which link to app_x; rerun_shared_infra=True)
        items = [
            _open_item(location="workflow:wf_b"),
            _open_item(location="app:app_x"),
        ]
        artifact = _artifact(
            workflows=[
                _wf("wf_a", linked_app_ids=["app_x"]),
                _wf("wf_b"),
                _wf("wf_c", linked_app_ids=["app_x"]),
            ],
            apps=[_app("app_x")],
            open_items=items,
        )

        scope = compute_combined_blast_radius([(0, "v1"), (1, "v2")], artifact)

        # Union of {wf_b} ∪ {wf_a, wf_c} = sorted [wf_a, wf_b, wf_c]
        assert scope.workflow_ids == ["wf_a", "wf_b", "wf_c"]
        assert scope.app_ids == ["app_x"]
        assert scope.rerun_shared_infra is True  # OR-ed in from the app item
        assert scope.rerun_open_items is True

    def test_session_plus_workflow_items(self):
        # Session item contributes nothing; workflow item contributes wf_a.
        items = [
            _open_item(location="session"),
            _open_item(location="workflow:wf_a"),
        ]
        artifact = _artifact(workflows=[_wf("wf_a")], open_items=items)

        scope = compute_combined_blast_radius([(0, "ans"), (1, "ans")], artifact)

        assert scope.workflow_ids == ["wf_a"]
        assert scope.app_ids == []
        assert scope.rerun_shared_infra is False

    def test_empty_input_returns_empty_scope(self):
        artifact = _artifact()
        scope = compute_combined_blast_radius([], artifact)

        assert scope.workflow_ids == []
        assert scope.app_ids == []
        assert scope.rerun_shared_infra is False
        # Stage 4 still re-runs — not gated on whether any LLM rerun happened
        assert scope.rerun_open_items is True

    def test_out_of_range_index_raises(self):
        items = [_open_item(location="workflow:wf_a")]
        artifact = _artifact(workflows=[_wf("wf_a")], open_items=items)

        with pytest.raises(IndexError, match="out of range"):
            compute_combined_blast_radius([(5, "answer")], artifact)


# ──────────────────────────────────────────────────────────────────────
# persist_artifact_version
# ──────────────────────────────────────────────────────────────────────


class _FakeCounterCollection:
    """In-memory analogue of motor's per-collection accessor.

    Implements just enough of ``find_one_and_update`` and ``insert_one``
    to validate the atomic version-allocation contract under simulated
    concurrency.

    Atomicity is enforced via an ``asyncio.Lock`` because asyncio mocks
    aren't truly concurrent — but ``asyncio.gather`` does interleave
    awaits, so without a lock the simulated counter would race exactly
    like a non-atomic find-then-write would in real Mongo.
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._counter_docs: dict[str, dict] = {}
        self._inserts: list[dict] = []

    async def find_one_and_update(
        self,
        filter_: dict,
        update: dict,
        upsert: bool = False,
        return_document: bool = False,
    ) -> dict | None:
        async with self._lock:
            artifact_id = filter_["artifact_id"]
            doc = self._counter_docs.get(artifact_id)
            if doc is None and upsert:
                doc = {"artifact_id": artifact_id, "next_version": 0}
                self._counter_docs[artifact_id] = doc
            if doc is None:
                return None
            inc = update.get("$inc", {})
            for key, delta in inc.items():
                doc[key] = doc.get(key, 0) + delta
            return dict(doc) if return_document else None

    async def find_one(self, filter_: dict) -> dict | None:
        async with self._lock:
            artifact_id = filter_["artifact_id"]
            doc = self._counter_docs.get(artifact_id)
            return dict(doc) if doc else None

    async def insert_one(self, doc: dict) -> Any:
        async with self._lock:
            self._inserts.append(dict(doc))
        return AsyncMock()

    def create_index(self, *args, **kwargs):  # pragma: no cover — server.py path
        pass


class _FakeDB:
    """Mongo-like dict-of-collections facade keyed by collection name."""

    def __init__(self):
        self._collections: dict[str, _FakeCounterCollection] = defaultdict(
            _FakeCounterCollection,
        )

    def __getitem__(self, name: str) -> _FakeCounterCollection:
        return self._collections[name]

    def collection(self, name: str) -> _FakeCounterCollection:
        return self._collections[name]


class TestPersistArtifactVersion:
    """Validation of the per-artifact monotonic version allocator + insert path.

    The repo's pytest config doesn't ship pytest-asyncio (existing async
    tests use ``asyncio.run`` from sync test functions — see
    test_v3_pipeline_smoke.py for the established pattern). We follow
    that convention.
    """

    def test_first_version_is_one(self):
        db = _FakeDB()

        async def _go():
            return await persist_artifact_version(
                db=db,
                artifact_id="art_1",
                parent_version=None,
                regen_trigger={"open_item_indices": [], "answers": [], "new_upload_ids": []},
                regen_scope=RegenScope(),
                payload={"client_company": "ACME"},
                user_id="user_a",
            )

        version = asyncio.run(_go())
        assert version == 1

        inserts = db.collection(VEGA_ARTIFACT_VERSIONS_COLLECTION)._inserts
        assert len(inserts) == 1
        assert inserts[0]["version"] == 1
        assert inserts[0]["parent_version"] is None
        assert inserts[0]["artifact_id"] == "art_1"
        assert inserts[0]["user_id"] == "user_a"

    def test_versions_increment_monotonically(self):
        db = _FakeDB()

        async def _go():
            v1 = await persist_artifact_version(
                db=db, artifact_id="art_1", parent_version=None,
                regen_trigger={}, regen_scope=RegenScope(), payload={}, user_id="u",
            )
            v2 = await persist_artifact_version(
                db=db, artifact_id="art_1", parent_version=v1,
                regen_trigger={}, regen_scope=RegenScope(), payload={}, user_id="u",
            )
            v3 = await persist_artifact_version(
                db=db, artifact_id="art_1", parent_version=v2,
                regen_trigger={}, regen_scope=RegenScope(), payload={}, user_id="u",
            )
            return v1, v2, v3

        assert asyncio.run(_go()) == (1, 2, 3)

    def test_parent_version_threads_correctly(self):
        db = _FakeDB()

        async def _go():
            v1 = await persist_artifact_version(
                db=db, artifact_id="art_1", parent_version=None,
                regen_trigger={}, regen_scope=RegenScope(), payload={}, user_id="u",
            )
            v2 = await persist_artifact_version(
                db=db, artifact_id="art_1", parent_version=v1,
                regen_trigger={}, regen_scope=RegenScope(), payload={}, user_id="u",
            )
            return v1, v2

        v1, _v2 = asyncio.run(_go())
        inserts = db.collection(VEGA_ARTIFACT_VERSIONS_COLLECTION)._inserts
        assert inserts[0]["parent_version"] is None
        assert inserts[1]["parent_version"] == v1 == 1
        assert inserts[1]["version"] == 2

    def test_separate_artifact_ids_have_independent_counters(self):
        db = _FakeDB()

        async def _go():
            v_a = await persist_artifact_version(
                db=db, artifact_id="art_a", parent_version=None,
                regen_trigger={}, regen_scope=RegenScope(), payload={}, user_id="u",
            )
            v_b = await persist_artifact_version(
                db=db, artifact_id="art_b", parent_version=None,
                regen_trigger={}, regen_scope=RegenScope(), payload={}, user_id="u",
            )
            return v_a, v_b

        # Each artifact starts at v1 — counters are per-artifact.
        assert asyncio.run(_go()) == (1, 1)

    def test_concurrent_inserts_do_not_collide(self):
        """Validate atomic allocation: N concurrent persist calls produce
        N distinct version numbers 1..N, no duplicates, no gaps."""
        db = _FakeDB()
        N = 50

        async def _one():
            return await persist_artifact_version(
                db=db,
                artifact_id="art_1",
                parent_version=None,
                regen_trigger={},
                regen_scope=RegenScope(),
                payload={},
                user_id="u",
            )

        async def _go():
            return await asyncio.gather(*[_one() for _ in range(N)])

        results = asyncio.run(_go())

        assert sorted(results) == list(range(1, N + 1))
        assert len(set(results)) == N  # uniqueness

        inserts = db.collection(VEGA_ARTIFACT_VERSIONS_COLLECTION)._inserts
        versions_in_collection = sorted(d["version"] for d in inserts)
        assert versions_in_collection == list(range(1, N + 1))

    def test_regen_scope_serialised_to_json_dict(self):
        db = _FakeDB()
        scope = RegenScope(
            workflow_ids=["wf_a"],
            app_ids=["app_x"],
            rerun_shared_infra=True,
            rerun_open_items=True,
        )

        async def _go():
            return await persist_artifact_version(
                db=db, artifact_id="art_1", parent_version=None,
                regen_trigger={"open_item_indices": [0]},
                regen_scope=scope,
                payload={"version": "v3"},
                user_id="u",
            )

        asyncio.run(_go())
        inserts = db.collection(VEGA_ARTIFACT_VERSIONS_COLLECTION)._inserts
        stored = inserts[0]["regen_scope"]
        assert isinstance(stored, dict)
        assert stored["workflow_ids"] == ["wf_a"]
        assert stored["app_ids"] == ["app_x"]
        assert stored["rerun_shared_infra"] is True
        assert stored["rerun_open_items"] is True

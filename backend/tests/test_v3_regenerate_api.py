"""Tests for the Vega v3 regenerate + versions API endpoints (Wave 5c).

Covers:
  * Permission denial when user lacks ``vega.use``
  * IDOR — cross-user 403
  * Wrong artifact_type 400
  * Empty answers list 400 (Pydantic min_length=1)
  * Stale open_item_index 400
  * Cross-user new_upload_id 403
  * Soft cap on the 11th regen
  * Happy path — pipeline mocked, version row written, ai_chat_artifacts
    pointer updated, response shape matches contract
  * Versions endpoints — list desc order, fetch by version, cross-user 403

No real LLM and no real Mongo. ``run_v3_pipeline_partial`` is monkeypatched
on the route module directly per the contract — we never reach into 5b's
implementation seams.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# auth_helper requires JWT_SECRET at module import time
os.environ.setdefault("JWT_SECRET", "test-secret")

from fastapi import HTTPException

from routes import vega_v3_regenerate as regen_mod
from routes import vega_v3_versions as versions_mod
from routes.vega_v3_regenerate import (
    AnsweredItem,
    MAX_REGENERATIONS,
    RegenerateRequest,
    regenerate_v3_artifact,
)
from routes.vega_v3_versions import (
    get_v3_artifact_version,
    list_v3_artifact_versions,
)
from services.vega.flags import VegaFlag
from services.vega.models_v3 import (
    DiscoveryAnalysisV3,
    MMIDimension,
    MMIHypothesis,
    OpenItem,
    PipelineMetadata,
    SessionFlags,
    SourceProvenance,
    Workflow,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_user(user_id: str = "user_a", perms: list[str] | None = None) -> Any:
    """Mock user with the has_perm direct-membership shape."""
    user = MagicMock()
    user.user_id = user_id
    user.permissions = perms if perms is not None else ["vega.use"]
    user._permission_closure = None
    user._perm_closure = None
    return user


def _seed_mmi() -> MMIHypothesis:
    dim = MMIDimension(value=3, confidence="medium", reasoning="seeded")
    return MMIHypothesis(
        tss=dim, ie=dim, time=dim, cost=dim, quality=dim, satisfaction=dim,
    )


def _make_workflow(wf_id: str = "wf_a") -> Workflow:
    return Workflow(
        workflow_id=wf_id,
        workflow_name=f"Workflow {wf_id}",
        source=SourceProvenance(quote="seed"),
        mmi_hypothesis=_seed_mmi(),
    )


def _make_open_item(location: str = "workflow:wf_a", label: str = "Missing volume") -> OpenItem:
    return OpenItem(
        flag=VegaFlag.MISSING,
        label=label,
        location=location,
        detail="What's the volume per week?",
    )


def _make_artifact_doc(
    artifact_id: str = "art_test",
    user_id: str = "user_a",
    workflows: list[Workflow] | None = None,
    open_items: list[OpenItem] | None = None,
    artifact_type: str = "discovery_analysis_v3",
) -> dict:
    """Build an ai_chat_artifacts row for test fixtures."""
    v3 = DiscoveryAnalysisV3(
        client_company="Acme",
        workflows=workflows or [_make_workflow()],
        apps=[],
        session_flags=SessionFlags(),
        open_items=open_items or [_make_open_item()],
        pipeline_metadata=PipelineMetadata(),
        extraction_status="complete",
    )
    return {
        "artifact_id": artifact_id,
        "user_id": user_id,
        "artifact_type": artifact_type,
        "session_id": "sess_test",
        "payload": v3.model_dump(mode="json"),
    }


class _AsyncCursor:
    """Mimics motor's chained ``.find().sort().limit().to_list()`` cursor."""

    def __init__(self, docs: list[dict]):
        self._docs = list(docs)

    def sort(self, *_a, **_kw):
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        if length is None:
            return list(self._docs)
        return list(self._docs[:length])


def _make_db(
    artifact_doc: dict | None,
    existing_versions: list[dict] | None = None,
    chat_uploads: dict[str, dict] | None = None,
) -> Any:
    """Mock motor client. Subscriptable + attribute access both work."""
    existing_versions = list(existing_versions or [])
    chat_uploads = chat_uploads or {}

    db = MagicMock()

    async def _ai_chat_find_one(query, projection=None):
        if artifact_doc is None:
            return None
        if query.get("artifact_id") != artifact_doc.get("artifact_id"):
            return None
        return artifact_doc

    db.ai_chat_artifacts.find_one = AsyncMock(side_effect=_ai_chat_find_one)
    db.ai_chat_artifacts.update_one = AsyncMock()

    async def _chat_uploads_find_one(query, projection=None):
        uid = query.get("upload_id")
        owner = query.get("user_id")
        doc = chat_uploads.get(uid)
        if doc is None:
            return None
        if owner is not None and doc.get("user_id") != owner:
            return None
        return doc

    db.chat_uploads.find_one = AsyncMock(side_effect=_chat_uploads_find_one)

    db.vega_events.insert_one = AsyncMock()

    # Versions collection — accessed via db[NAME] (subscript) so we wire
    # __getitem__ to a stub and cover both the regenerate and versions
    # endpoints from the same mock.
    versions_coll = MagicMock()

    async def _versions_count(query):
        aid = query.get("artifact_id")
        return sum(1 for v in existing_versions if v.get("artifact_id") == aid)

    versions_coll.count_documents = AsyncMock(side_effect=_versions_count)

    def _versions_find(query, projection=None):
        aid = query.get("artifact_id")
        version = query.get("version")
        rows = [v for v in existing_versions if v.get("artifact_id") == aid]
        if version is not None:
            rows = [v for v in rows if v.get("version") == version]
        rows = sorted(rows, key=lambda r: r.get("version", 0), reverse=True)
        return _AsyncCursor(rows)

    versions_coll.find = MagicMock(side_effect=_versions_find)

    async def _versions_find_one(query, projection=None):
        aid = query.get("artifact_id")
        version = query.get("version")
        for v in existing_versions:
            if v.get("artifact_id") == aid and v.get("version") == version:
                return v
        return None

    versions_coll.find_one = AsyncMock(side_effect=_versions_find_one)

    insert_calls: list[dict] = []

    async def _versions_insert(doc):
        insert_calls.append(doc)
        existing_versions.append(doc)
        return MagicMock(inserted_id=doc.get("artifact_id"))

    versions_coll.insert_one = AsyncMock(side_effect=_versions_insert)
    versions_coll._insert_calls = insert_calls

    counters_coll = MagicMock()
    # Seed the in-memory counter from any pre-existing versions so the
    # next allocated version number lines up with what real Mongo would
    # produce after those rows were written.
    counter_state: dict[str, int] = {}
    for v in existing_versions:
        aid = v.get("artifact_id")
        if aid is not None:
            counter_state[aid] = max(counter_state.get(aid, 0), int(v.get("version", 0)))

    async def _counters_find_and_update(query, update, upsert=False, return_document=True):
        aid = query["artifact_id"]
        counter_state[aid] = counter_state.get(aid, 0) + 1
        return {"artifact_id": aid, "next_version": counter_state[aid]}

    counters_coll.find_one_and_update = AsyncMock(side_effect=_counters_find_and_update)

    async def _counters_find_one(query):
        aid = query["artifact_id"]
        if aid in counter_state:
            return {"artifact_id": aid, "next_version": counter_state[aid]}
        return None

    counters_coll.find_one = AsyncMock(side_effect=_counters_find_one)

    coll_routes = {
        "vega_artifact_versions": versions_coll,
        "vega_artifact_version_counters": counters_coll,
    }

    db.__getitem__ = MagicMock(side_effect=lambda name: coll_routes.get(name, MagicMock()))

    db._versions_coll = versions_coll
    db._counters_coll = counters_coll
    return db


def _patched_artifact_after_pipeline(prior: DiscoveryAnalysisV3) -> DiscoveryAnalysisV3:
    """Make a 'new' artifact that differs from prior so test assertions can spot it."""
    fresh = prior.model_copy(deep=True)
    fresh.client_company = prior.client_company + " (regen)"
    return fresh


# ---------------------------------------------------------------------------
# Permission + IDOR + shape guards
# ---------------------------------------------------------------------------


class TestRegeneratePermissionAndIDOR:
    def test_returns_403_without_vega_use(self, monkeypatch):
        artifact = _make_artifact_doc()
        db = _make_db(artifact)
        monkeypatch.setattr(regen_mod, "_get_db", lambda: db)

        user = _make_user(perms=[])  # no vega.use
        body = RegenerateRequest(answers=[AnsweredItem(open_item_index=0, value="x")])

        with pytest.raises(HTTPException) as exc:
            asyncio.run(regenerate_v3_artifact(
                artifact_id=artifact["artifact_id"], body=body, current_user=user,
            ))
        assert exc.value.status_code == 403
        assert "permission" in exc.value.detail.lower()

    def test_idor_cross_user_returns_403(self, monkeypatch):
        artifact = _make_artifact_doc(user_id="user_owner")
        db = _make_db(artifact)
        monkeypatch.setattr(regen_mod, "_get_db", lambda: db)

        intruder = _make_user(user_id="user_intruder")
        body = RegenerateRequest(answers=[AnsweredItem(open_item_index=0, value="x")])

        with pytest.raises(HTTPException) as exc:
            asyncio.run(regenerate_v3_artifact(
                artifact_id=artifact["artifact_id"], body=body, current_user=intruder,
            ))
        assert exc.value.status_code == 403
        assert "do not own" in exc.value.detail.lower()

    def test_artifact_not_found_returns_404(self, monkeypatch):
        db = _make_db(None)
        monkeypatch.setattr(regen_mod, "_get_db", lambda: db)

        user = _make_user()
        body = RegenerateRequest(answers=[AnsweredItem(open_item_index=0, value="x")])

        with pytest.raises(HTTPException) as exc:
            asyncio.run(regenerate_v3_artifact(
                artifact_id="art_does_not_exist", body=body, current_user=user,
            ))
        assert exc.value.status_code == 404

    def test_wrong_artifact_type_returns_400(self, monkeypatch):
        artifact = _make_artifact_doc(artifact_type="discovery_analysis")  # v2 type
        db = _make_db(artifact)
        monkeypatch.setattr(regen_mod, "_get_db", lambda: db)

        user = _make_user()
        body = RegenerateRequest(answers=[AnsweredItem(open_item_index=0, value="x")])

        with pytest.raises(HTTPException) as exc:
            asyncio.run(regenerate_v3_artifact(
                artifact_id=artifact["artifact_id"], body=body, current_user=user,
            ))
        assert exc.value.status_code == 400
        assert "v3" in exc.value.detail.lower()


class TestRegenerateInputValidation:
    def test_empty_answers_list_rejected_by_pydantic(self):
        # Pydantic min_length=1 rejects an empty list at request-body validation.
        with pytest.raises(Exception) as exc:
            RegenerateRequest(answers=[])
        # Pydantic 2 raises ValidationError; check error count >= 1
        msg = str(exc.value).lower()
        assert "answers" in msg or "at least 1" in msg or "min_length" in msg

    def test_stale_open_item_index_returns_400(self, monkeypatch):
        # Artifact has 1 open_item; submit index 5
        artifact = _make_artifact_doc()
        db = _make_db(artifact)
        monkeypatch.setattr(regen_mod, "_get_db", lambda: db)

        user = _make_user()
        body = RegenerateRequest(answers=[AnsweredItem(open_item_index=5, value="x")])

        with pytest.raises(HTTPException) as exc:
            asyncio.run(regenerate_v3_artifact(
                artifact_id=artifact["artifact_id"], body=body, current_user=user,
            ))
        assert exc.value.status_code == 400
        assert "stale" in exc.value.detail.lower() or "out-of-range" in exc.value.detail.lower()

    def test_cross_user_new_upload_id_returns_403(self, monkeypatch):
        artifact = _make_artifact_doc(user_id="user_a")
        # Upload exists but is owned by someone else.
        chat_uploads = {
            "upload_other": {"upload_id": "upload_other", "user_id": "user_b"},
        }
        db = _make_db(artifact, chat_uploads=chat_uploads)
        monkeypatch.setattr(regen_mod, "_get_db", lambda: db)

        user = _make_user(user_id="user_a")
        body = RegenerateRequest(
            answers=[AnsweredItem(open_item_index=0, value="x")],
            new_upload_ids=["upload_other"],
        )

        with pytest.raises(HTTPException) as exc:
            asyncio.run(regenerate_v3_artifact(
                artifact_id=artifact["artifact_id"], body=body, current_user=user,
            ))
        assert exc.value.status_code == 403
        assert "upload" in exc.value.detail.lower()


class TestRegenerateSoftCap:
    def test_soft_cap_value_is_ten(self):
        assert MAX_REGENERATIONS == 10

    def test_eleventh_regen_blocked_with_400(self, monkeypatch):
        artifact = _make_artifact_doc()
        # Already 10 versions persisted — the 11th request should be rejected.
        prior_versions = [
            {
                "artifact_id": artifact["artifact_id"],
                "version": v,
                "parent_version": v - 1 if v > 1 else None,
                "regen_trigger": {},
                "regen_scope": {"workflow_ids": [], "app_ids": []},
                "payload": {},
                "created_at": datetime.now(timezone.utc),
                "user_id": "user_a",
            }
            for v in range(1, MAX_REGENERATIONS + 1)
        ]
        db = _make_db(artifact, existing_versions=prior_versions)
        monkeypatch.setattr(regen_mod, "_get_db", lambda: db)

        user = _make_user()
        body = RegenerateRequest(answers=[AnsweredItem(open_item_index=0, value="x")])

        with pytest.raises(HTTPException) as exc:
            asyncio.run(regenerate_v3_artifact(
                artifact_id=artifact["artifact_id"], body=body, current_user=user,
            ))
        assert exc.value.status_code == 400
        assert "cap reached" in exc.value.detail.lower()


# ---------------------------------------------------------------------------
# Happy-path regeneration
# ---------------------------------------------------------------------------


class TestRegenerateHappyPath:
    """Run a full regenerate request with the partial-pipeline call mocked."""

    def _run(self, monkeypatch, *, prior_versions=None, new_upload_ids=None,
             chat_uploads=None, capture: dict | None = None):
        artifact = _make_artifact_doc()
        db = _make_db(
            artifact,
            existing_versions=prior_versions or [],
            chat_uploads=chat_uploads or {},
        )
        monkeypatch.setattr(regen_mod, "_get_db", lambda: db)

        captured: dict = capture if capture is not None else {}

        async def _fake_partial(db_arg, user_arg, prior_arg, scope_arg,
                                answered_items_arg, new_uploads_arg):
            captured["db"] = db_arg
            captured["user"] = user_arg
            captured["prior"] = prior_arg
            captured["scope"] = scope_arg
            captured["answered_items"] = answered_items_arg
            captured["new_uploads"] = new_uploads_arg
            return _patched_artifact_after_pipeline(prior_arg)

        monkeypatch.setattr(regen_mod, "run_v3_pipeline_partial", _fake_partial)

        user = _make_user()
        body = RegenerateRequest(
            answers=[AnsweredItem(open_item_index=0, value="about 5000/week")],
            new_upload_ids=new_upload_ids,
        )
        result = asyncio.run(regenerate_v3_artifact(
            artifact_id=artifact["artifact_id"], body=body, current_user=user,
        ))
        return artifact, db, result, captured

    def test_partial_pipeline_called_with_expected_args(self, monkeypatch):
        captured: dict = {}
        artifact, db, result, captured = self._run(monkeypatch, capture=captured)

        # Pipeline was invoked with the prior artifact and the answered-items list.
        assert captured["prior"].client_company == "Acme"
        assert len(captured["answered_items"]) == 1
        ai = captured["answered_items"][0]
        assert ai["open_item_index"] == 0
        assert ai["value"] == "about 5000/week"
        # Wave 5b expects label + location in the answered item dict.
        assert ai["label"] == "Missing volume"
        assert ai["location"] == "workflow:wf_a"
        # Workflow blast radius: location 'workflow:wf_a' resolves to that wf_id.
        assert "wf_a" in captured["scope"].workflow_ids

    def test_returns_new_version_2_and_response_shape(self, monkeypatch):
        artifact, db, result, _ = self._run(monkeypatch)

        assert result["artifact_id"] == artifact["artifact_id"]
        assert result["new_version"] == 1  # first regen ever
        assert result["extraction_status"] == "complete"
        assert "regen_scope" in result
        scope = result["regen_scope"]
        assert "workflow_ids" in scope and "app_ids" in scope and "rerun_shared_infra" in scope

    def test_second_regen_returns_version_2(self, monkeypatch):
        prior_versions = [{
            "artifact_id": "art_test",
            "version": 1,
            "parent_version": None,
            "regen_trigger": {},
            "regen_scope": {"workflow_ids": [], "app_ids": []},
            "payload": {},
            "created_at": datetime.now(timezone.utc),
            "user_id": "user_a",
        }]
        artifact, db, result, _ = self._run(monkeypatch, prior_versions=prior_versions)
        assert result["new_version"] == 2

    def test_persists_version_row(self, monkeypatch):
        artifact, db, result, _ = self._run(monkeypatch)

        insert_calls = db._versions_coll._insert_calls
        assert len(insert_calls) == 1
        row = insert_calls[0]
        assert row["artifact_id"] == artifact["artifact_id"]
        assert row["version"] == result["new_version"]
        # regen_trigger captures the user's submitted indices and answers.
        trig = row["regen_trigger"]
        assert trig["open_item_indices"] == [0]
        assert trig["answers"][0]["value"] == "about 5000/week"

    def test_updates_canonical_chat_artifact(self, monkeypatch):
        artifact, db, result, _ = self._run(monkeypatch)

        # ai_chat_artifacts.update_one was called with the new payload + bumped updated_at.
        assert db.ai_chat_artifacts.update_one.await_count == 1
        call_args = db.ai_chat_artifacts.update_one.await_args
        # First positional arg is the filter, second is the update.
        args, _kwargs = call_args
        filter_arg, update_arg = args[0], args[1]
        assert filter_arg == {"artifact_id": artifact["artifact_id"]}
        assert "$set" in update_arg
        set_doc = update_arg["$set"]
        assert "payload" in set_doc
        assert "updated_at" in set_doc
        assert set_doc["current_version"] == result["new_version"]
        # The fresh payload contains the marker we injected in the fake pipeline.
        assert "regen" in (set_doc["payload"].get("client_company") or "")

    def test_emits_started_and_complete_events(self, monkeypatch):
        artifact, db, result, _ = self._run(monkeypatch)

        # Two vega_events.insert_one calls — one for start, one for complete.
        assert db.vega_events.insert_one.await_count == 2
        events = [c.args[0] for c in db.vega_events.insert_one.await_args_list]
        types = sorted(e["event_type"] for e in events)
        assert types == ["v3_regenerate_complete", "v3_regenerate_started"]

    def test_new_upload_ids_passed_through_when_owned(self, monkeypatch):
        chat_uploads = {
            "upload_owned": {"upload_id": "upload_owned", "user_id": "user_a"},
        }
        captured: dict = {}
        artifact, db, result, captured = self._run(
            monkeypatch,
            new_upload_ids=["upload_owned"],
            chat_uploads=chat_uploads,
            capture=captured,
        )
        assert captured["new_uploads"] == ["upload_owned"]


# ---------------------------------------------------------------------------
# Versions endpoints
# ---------------------------------------------------------------------------


def _make_version_rows(artifact_id: str, count: int) -> list[dict]:
    rows = []
    base = datetime.now(timezone.utc)
    for v in range(1, count + 1):
        rows.append({
            "artifact_id": artifact_id,
            "version": v,
            "parent_version": v - 1 if v > 1 else None,
            "regen_trigger": {"open_item_indices": [v]},
            "regen_scope": {"workflow_ids": ["wf_a"], "app_ids": []},
            "payload": {"client_company": f"Acme v{v}"},
            "created_at": base,
            "user_id": "user_a",
        })
    return rows


class TestListVersions:
    def test_list_returns_desc_order(self, monkeypatch):
        artifact = _make_artifact_doc()
        rows = _make_version_rows(artifact["artifact_id"], count=3)
        db = _make_db(artifact, existing_versions=rows)
        monkeypatch.setattr(versions_mod, "_get_db", lambda: db)

        user = _make_user()
        result = asyncio.run(list_v3_artifact_versions(
            artifact_id=artifact["artifact_id"], current_user=user,
        ))

        versions = result["versions"]
        assert [v["version"] for v in versions] == [3, 2, 1]
        # Each entry should carry parent_version + regen_trigger + created_at.
        for v in versions:
            assert "parent_version" in v
            assert "regen_trigger" in v
            assert "created_at" in v

    def test_list_returns_empty_for_artifact_with_no_versions(self, monkeypatch):
        artifact = _make_artifact_doc()
        db = _make_db(artifact, existing_versions=[])
        monkeypatch.setattr(versions_mod, "_get_db", lambda: db)

        user = _make_user()
        result = asyncio.run(list_v3_artifact_versions(
            artifact_id=artifact["artifact_id"], current_user=user,
        ))
        assert result == {"versions": []}

    def test_list_idor_cross_user_returns_403(self, monkeypatch):
        artifact = _make_artifact_doc(user_id="user_owner")
        rows = _make_version_rows(artifact["artifact_id"], count=2)
        db = _make_db(artifact, existing_versions=rows)
        monkeypatch.setattr(versions_mod, "_get_db", lambda: db)

        intruder = _make_user(user_id="user_intruder")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(list_v3_artifact_versions(
                artifact_id=artifact["artifact_id"], current_user=intruder,
            ))
        assert exc.value.status_code == 403

    def test_list_requires_vega_use_perm(self, monkeypatch):
        artifact = _make_artifact_doc()
        db = _make_db(artifact)
        monkeypatch.setattr(versions_mod, "_get_db", lambda: db)

        user = _make_user(perms=[])
        with pytest.raises(HTTPException) as exc:
            asyncio.run(list_v3_artifact_versions(
                artifact_id=artifact["artifact_id"], current_user=user,
            ))
        assert exc.value.status_code == 403


class TestGetVersion:
    def test_get_returns_payload_at_version(self, monkeypatch):
        artifact = _make_artifact_doc()
        rows = _make_version_rows(artifact["artifact_id"], count=3)
        db = _make_db(artifact, existing_versions=rows)
        monkeypatch.setattr(versions_mod, "_get_db", lambda: db)

        user = _make_user()
        result = asyncio.run(get_v3_artifact_version(
            artifact_id=artifact["artifact_id"], version=2, current_user=user,
        ))

        assert result["version"] == 2
        assert result["parent_version"] == 1
        assert result["payload"]["client_company"] == "Acme v2"
        assert "regen_trigger" in result and "regen_scope" in result

    def test_get_unknown_version_returns_404(self, monkeypatch):
        artifact = _make_artifact_doc()
        rows = _make_version_rows(artifact["artifact_id"], count=2)
        db = _make_db(artifact, existing_versions=rows)
        monkeypatch.setattr(versions_mod, "_get_db", lambda: db)

        user = _make_user()
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_v3_artifact_version(
                artifact_id=artifact["artifact_id"], version=99, current_user=user,
            ))
        assert exc.value.status_code == 404

    def test_get_idor_cross_user_returns_403(self, monkeypatch):
        artifact = _make_artifact_doc(user_id="user_owner")
        rows = _make_version_rows(artifact["artifact_id"], count=2)
        db = _make_db(artifact, existing_versions=rows)
        monkeypatch.setattr(versions_mod, "_get_db", lambda: db)

        intruder = _make_user(user_id="user_intruder")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_v3_artifact_version(
                artifact_id=artifact["artifact_id"], version=1, current_user=intruder,
            ))
        assert exc.value.status_code == 403

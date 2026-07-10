"""End-to-end smoke for Wave 6 — v3 per-call modes (Call 1 → Call 2 → Call 3).

Runs the three per-call entry points in sequence against a real transcript,
verifies each artifact persists with the right artifact_type + parent linkage,
and asserts the per-mode rendering gates (Stage 3 absent for Call 1/2,
Stage 3 present for Call 3) hold at the payload level.

Usage (inside backend container):
    python scripts/smoke_v3_call_modes.py <upload_id> <user_id>

Optional env:
    SMOKE_SKIP_PERSIST=1   skip writing the three artifacts to ai_chat_artifacts
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET", "smoke-test")


def _ok(label: str, msg: str = "") -> None:
    print(f"  OK: {label}{(' — ' + msg) if msg else ''}")


def _fail(label: str, msg: str = "") -> None:
    print(f"  FAIL: {label}{(' — ' + msg) if msg else ''}")


async def _persist(db, user, payload: dict, artifact_type: str, session_id: str) -> str:
    art_id = f"art_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    await db.ai_chat_artifacts.insert_one({
        "artifact_id": art_id,
        "session_id": session_id,
        "user_id": user.user_id,
        "employee_id": "ai_vega",
        "artifact_type": artifact_type,
        "payload": payload,
        "status": "delivered",
        "created_at": now,
        "updated_at": now,
    })
    return art_id


async def _main(upload_id: str, user_id: str) -> int:
    from motor.motor_asyncio import AsyncIOMotorClient
    from models import User

    mongo_url = os.environ.get("MONGO_URL", "mongodb://mongo:27017")
    db_name = os.environ.get("DB_NAME", "aegisaxiom")
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    user_doc = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not user_doc:
        print(f"FAIL: user {user_id} not found")
        return 2
    role_doc = await db.roles.find_one(
        {"name": user_doc.get("role", "customer")},
        {"_id": 0, "permissions": 1},
    )
    role_perms = role_doc.get("permissions", []) if role_doc else []
    user_extras = user_doc.get("extra_permissions", [])
    safe_doc = {k: v for k, v in user_doc.items() if k != "password_hash"}
    safe_doc["permissions"] = list(set(role_perms + user_extras))
    safe_doc["extra_permissions"] = user_extras
    user = User(**safe_doc)

    skip_persist = os.environ.get("SMOKE_SKIP_PERSIST") == "1"
    session_id = f"smoke_w6_{int(time.time())}"

    from services.vega.v3_call_modes import (
        run_v3_call1, run_v3_call2, run_v3_call3,
    )

    results: list[bool] = []

    print("===== CALL 1 — skeleton + light Stage 2 =====")
    t0 = time.monotonic()
    try:
        call1 = await run_v3_call1(
            db=db, user=user, upload_ids=[upload_id],
            chat_session_id=session_id, client_company=None,
        )
        elapsed = time.monotonic() - t0
        print(f"  Call 1 finished in {elapsed:.1f}s")
        c1_payload = call1.model_dump(mode="json")
        c1_payload["session_id"] = session_id
        c1_payload["discovery_mode"] = "conversational"

        # Assertions on Call 1 shape
        meta = c1_payload.get("pipeline_metadata") or {}
        if meta.get("call_mode") == "call1":
            _ok("Call 1 pipeline_metadata.call_mode == call1")
            results.append(True)
        else:
            _fail("Call 1 call_mode", repr(meta.get("call_mode")))
            results.append(False)

        if meta.get("stage_3_duration_ms") in (None, 0):
            _ok("Call 1 Stage 3 skipped (stage_3_duration_ms None/0)")
            results.append(True)
        else:
            _fail("Call 1 Stage 3 duration", str(meta.get("stage_3_duration_ms")))
            results.append(False)

        wf_count = len(c1_payload.get("workflows") or [])
        app_count = len(c1_payload.get("apps") or [])
        if wf_count > 0 or app_count > 0:
            _ok(f"Call 1 produced content (workflows={wf_count}, apps={app_count})")
            results.append(True)
        else:
            _fail("Call 1 produced no workflows/apps")
            results.append(False)

        # Call 1 should NOT have BPMN xml or design_doc/eadd_doc
        any_bpmn = any((w.get("bpmn_xml") for w in (c1_payload.get("workflows") or [])))
        any_design = any((w.get("design_doc") for w in (c1_payload.get("workflows") or [])))
        any_eadd = any((a.get("eadd_doc") for a in (c1_payload.get("apps") or [])))
        if not any_bpmn and not any_design and not any_eadd:
            _ok("Call 1 has no BPMN / design_doc / eadd_doc")
            results.append(True)
        else:
            _fail("Call 1 leaked Stage 3 content", f"bpmn={any_bpmn} design={any_design} eadd={any_eadd}")
            results.append(False)

        if not skip_persist:
            c1_id = await _persist(db, user, c1_payload, "discovery_analysis_v3_call1", session_id)
            print(f"  persisted Call 1 artifact: {c1_id}")
        else:
            c1_id = None
            print("  SMOKE_SKIP_PERSIST=1 — Call 1 not persisted")
    except Exception as exc:
        _fail("Call 1 raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    print()
    print("===== CALL 2 — full Stage 2 + MMI, no Stage 3 =====")
    t0 = time.monotonic()
    try:
        call2 = await run_v3_call2(
            db=db, user=user, upload_ids=[upload_id],
            prior_call1_artifact_id=c1_id,
            chat_session_id=session_id, client_company=None,
        )
        elapsed = time.monotonic() - t0
        print(f"  Call 2 finished in {elapsed:.1f}s")
        c2_payload = call2.model_dump(mode="json")
        c2_payload["session_id"] = session_id
        c2_payload["discovery_mode"] = "conversational"

        meta = c2_payload.get("pipeline_metadata") or {}
        if meta.get("call_mode") == "call2":
            _ok("Call 2 call_mode == call2")
            results.append(True)
        else:
            _fail("Call 2 call_mode", repr(meta.get("call_mode")))
            results.append(False)

        if meta.get("stage_3_duration_ms") in (None, 0):
            _ok("Call 2 Stage 3 skipped")
            results.append(True)
        else:
            _fail("Call 2 Stage 3 duration", str(meta.get("stage_3_duration_ms")))
            results.append(False)

        if c1_id and c2_payload.get("prior_call_artifact_id") == c1_id:
            _ok("Call 2 prior_call_artifact_id linked to Call 1")
            results.append(True)
        elif not c1_id:
            print("  SKIP: prior_call_artifact_id link (no persisted Call 1)")
        else:
            _fail("Call 2 prior_call_artifact_id link", str(c2_payload.get("prior_call_artifact_id")))
            results.append(False)

        # MMI hypothesis should populate at least one workflow
        workflows = c2_payload.get("workflows") or []
        any_mmi = any(w.get("mmi_hypothesis") for w in workflows)
        if any_mmi:
            _ok("Call 2 has MMI hypothesis")
            results.append(True)
        else:
            _fail("Call 2 missing MMI hypothesis on every workflow")
            results.append(False)

        if not skip_persist:
            c2_id = await _persist(db, user, c2_payload, "discovery_analysis_v3_call2", session_id)
            print(f"  persisted Call 2 artifact: {c2_id}")
        else:
            c2_id = None
    except Exception as exc:
        _fail("Call 2 raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    print()
    print("===== CALL 3 — full pipeline + contradictions =====")
    t0 = time.monotonic()
    try:
        call3 = await run_v3_call3(
            db=db, user=user, upload_ids=[upload_id],
            prior_call2_artifact_id=c2_id,
            chat_session_id=session_id, client_company=None,
        )
        elapsed = time.monotonic() - t0
        print(f"  Call 3 finished in {elapsed:.1f}s")
        c3_payload = call3.model_dump(mode="json")
        c3_payload["session_id"] = session_id
        c3_payload["discovery_mode"] = "conversational"

        meta = c3_payload.get("pipeline_metadata") or {}
        if meta.get("call_mode") == "call3":
            _ok("Call 3 call_mode == call3")
            results.append(True)
        else:
            _fail("Call 3 call_mode", repr(meta.get("call_mode")))
            results.append(False)

        if (meta.get("stage_3_duration_ms") or 0) > 0:
            _ok(f"Call 3 Stage 3 ran ({meta.get('stage_3_duration_ms')}ms)")
            results.append(True)
        else:
            _fail("Call 3 Stage 3 duration", str(meta.get("stage_3_duration_ms")))
            results.append(False)

        workflows = c3_payload.get("workflows") or []
        apps = c3_payload.get("apps") or []
        any_bpmn = any(w.get("bpmn_xml") for w in workflows)
        any_design = any(w.get("design_doc") for w in workflows)
        any_eadd = any(a.get("eadd_doc") for a in apps)
        if any_bpmn:
            _ok("Call 3 has BPMN xml on at least one workflow")
            results.append(True)
        else:
            _fail("Call 3 missing BPMN")
            results.append(False)
        if any_design:
            _ok("Call 3 has workflow design_doc on at least one workflow")
            results.append(True)
        else:
            _fail("Call 3 missing workflow design_doc")
            results.append(False)
        if any_eadd:
            _ok("Call 3 has app eadd_doc on at least one app")
            results.append(True)
        else:
            _fail("Call 3 missing app eadd_doc")
            results.append(False)

        if c2_id and c3_payload.get("prior_call_artifact_id") == c2_id:
            _ok("Call 3 prior_call_artifact_id linked to Call 2")
            results.append(True)
        elif not c2_id:
            print("  SKIP: prior_call_artifact_id link (no persisted Call 2)")
        else:
            _fail("Call 3 prior_call_artifact_id link", str(c3_payload.get("prior_call_artifact_id")))
            results.append(False)

        if not skip_persist:
            c3_id = await _persist(db, user, c3_payload, "discovery_analysis_v3_call3", session_id)
            print(f"  persisted Call 3 artifact: {c3_id}")
    except Exception as exc:
        _fail("Call 3 raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    passed = sum(1 for r in results if r)
    total = len(results)
    print()
    print(f"===== SMOKE: {passed}/{total} CHECKS PASSED =====")
    return 0 if passed == total else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: smoke_v3_call_modes.py <upload_id> <user_id>")
        sys.exit(2)
    sys.exit(asyncio.run(_main(sys.argv[1], sys.argv[2])))

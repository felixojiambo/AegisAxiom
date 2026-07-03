"""End-to-end smoke for Wave 3 — v3 pipeline → persist → export endpoints.

Reuses an existing v3 artifact in ai_chat_artifacts when present, otherwise
runs run_v3_pipeline and persists. Then exercises every Wave 3e export path
in-process via TestClient so we can verify the renderer code without going
through the live HTTP layer (no auth cookies needed).

Usage (inside backend container):
    python scripts/smoke_v3_wave3.py <upload_id> <user_id>
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET", "smoke-test")


async def _ensure_artifact(db, user, upload_id: str) -> tuple[str, dict]:
    """Find or create a v3 artifact for this user. Returns (artifact_id, payload)."""
    existing = await db.ai_chat_artifacts.find_one(
        {"user_id": user.user_id, "artifact_type": "discovery_analysis_v3"},
        {"_id": 0},
        sort=[("created_at", -1)],
    )
    if existing:
        print(f"reusing existing artifact: {existing['artifact_id']}")
        return existing["artifact_id"], existing["payload"]

    from services.vega.v3_pipeline import run_v3_pipeline
    print("no v3 artifact yet — running run_v3_pipeline (may take 3-5 minutes)...")
    t0 = time.monotonic()
    artifact = await run_v3_pipeline(
        db=db, user=user, upload_ids=[upload_id],
        chat_session_id=f"smoke_w3_{int(t0)}",
        client_company=None,
    )
    elapsed = time.monotonic() - t0
    payload = artifact.model_dump(mode="json")
    payload["session_id"] = "smoke_w3_session"
    payload["discovery_mode"] = "single_call"

    from datetime import datetime, timezone
    import uuid
    art_id = f"art_{uuid.uuid4().hex[:12]}"
    art_now = datetime.now(timezone.utc)
    await db.ai_chat_artifacts.insert_one({
        "artifact_id": art_id, "session_id": "smoke_w3_session",
        "user_id": user.user_id, "employee_id": "ai_vega",
        "artifact_type": "discovery_analysis_v3",
        "payload": payload,
        "status": "delivered", "created_at": art_now, "updated_at": art_now,
    })
    print(f"persisted artifact {art_id} in {elapsed:.1f}s")
    return art_id, payload


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

    artifact_id, payload = await _ensure_artifact(db, user, upload_id)
    workflows = payload.get("workflows") or []
    apps = payload.get("apps") or []
    print(f"artifact has {len(workflows)} workflows and {len(apps)} apps")

    # Pick the first workflow with a generated design doc and the first app with an EADD.
    sample_wf_id = next(
        (w["workflow_id"] for w in workflows if w.get("design_doc_status") == "generated"),
        None,
    )
    sample_app_id = next(
        (a["app_id"] for a in apps if a.get("design_doc_status") == "generated"),
        None,
    )
    print(f"sample workflow_id (with design_doc): {sample_wf_id}")
    print(f"sample app_id (with eadd_doc): {sample_app_id}")

    # Mock get_db to return our db handle so the export endpoints don't need
    # the FastAPI dependency injection layer.
    from database import get_db as real_get_db
    import database
    database.get_db = lambda: db
    try:
        from routes.vega_v3_exports import (
            export_v3_artifact,
            export_v3_to_google_doc,
        )

        print()
        print("===== EXPORT SMOKE =====")
        results = []

        async def call(target, fmt, eid=None):
            return await export_v3_artifact(
                artifact_id, target=target, entity_id=eid, format=fmt, current_user=user,
            )

        for label, target, fmt, eid in [
            ("analysis · markdown", "analysis", "markdown", None),
            ("analysis · pdf", "analysis", "pdf", None),
            ("analysis · json", "analysis", "json", None),
        ]:
            try:
                resp = await call(target, fmt, eid)
                if hasattr(resp, "status_code"):
                    ok = 200 <= resp.status_code < 300
                    body_len = len(getattr(resp, "body", b""))
                    print(f"  {'OK' if ok else 'FAIL'}: {label} → {resp.status_code} ({body_len} bytes)")
                    results.append(ok)
                else:
                    keys = list(resp.keys()) if isinstance(resp, dict) else type(resp).__name__
                    print(f"  OK: {label} → {keys}")
                    results.append(True)
            except Exception as exc:
                print(f"  FAIL: {label} → {type(exc).__name__}: {exc}")
                traceback.print_exc()
                results.append(False)

        if sample_wf_id:
            for label, fmt in [("design_doc · markdown", "markdown"), ("design_doc · pdf", "pdf")]:
                try:
                    resp = await call("design_doc", fmt, sample_wf_id)
                    ok = 200 <= resp.status_code < 300
                    body_len = len(getattr(resp, "body", b""))
                    print(f"  {'OK' if ok else 'FAIL'}: {label} → {resp.status_code} ({body_len} bytes)")
                    results.append(ok)
                except Exception as exc:
                    print(f"  FAIL: {label} → {type(exc).__name__}: {exc}")
                    traceback.print_exc()
                    results.append(False)
        else:
            print("  SKIP: no workflow with design_doc to test")

        if sample_app_id:
            for label, fmt in [("eadd · markdown", "markdown"), ("eadd · pdf", "pdf")]:
                try:
                    resp = await call("eadd", fmt, sample_app_id)
                    ok = 200 <= resp.status_code < 300
                    body_len = len(getattr(resp, "body", b""))
                    print(f"  {'OK' if ok else 'FAIL'}: {label} → {resp.status_code} ({body_len} bytes)")
                    results.append(ok)
                except Exception as exc:
                    print(f"  FAIL: {label} → {type(exc).__name__}: {exc}")
                    traceback.print_exc()
                    results.append(False)
        else:
            print("  SKIP: no app with eadd_doc to test")

        # IDOR guard — different user_id must 403
        print()
        print("===== IDOR GUARD =====")
        try:
            other_user = User(**{**safe_doc, "user_id": "user_someone_else", "permissions": ["vega.use"]})
            await export_v3_artifact(
                artifact_id, target="analysis", entity_id=None, format="markdown",
                current_user=other_user,
            )
            print("  FAIL: cross-user request did not raise")
            results.append(False)
        except Exception as exc:
            ok = "403" in str(exc) or "do not own" in str(exc)
            print(f"  {'OK' if ok else 'FAIL'}: cross-user → raised {type(exc).__name__}: {str(exc)[:80]}")
            results.append(ok)

        passed = sum(1 for r in results if r)
        total = len(results)
        print()
        print(f"===== SMOKE: {passed}/{total} CHECKS PASSED =====")
        return 0 if passed == total else 1
    finally:
        database.get_db = real_get_db


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: smoke_v3_wave3.py <upload_id> <user_id>")
        sys.exit(2)
    sys.exit(asyncio.run(_main(sys.argv[1], sys.argv[2])))

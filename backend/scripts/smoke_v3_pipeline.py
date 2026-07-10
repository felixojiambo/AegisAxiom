"""One-shot smoke test for the v3 pipeline against a real transcript.

Invokes run_v3_pipeline with a known chat_uploads document and prints a
condensed shape report so we can verify the artifact round-trips through
DiscoveryAnalysisV3 end to end. No DB writes, no event emission to chat.

Usage (inside backend container):
    python scripts/smoke_v3_pipeline.py <upload_id> <user_id>
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET", "smoke-test")


async def _main(upload_id: str, user_id: str) -> int:
    from motor.motor_asyncio import AsyncIOMotorClient

    from models import User
    from services.vega.models_v3 import DiscoveryAnalysisV3
    from services.vega.v3_pipeline import run_v3_pipeline

    mongo_url = os.environ.get("MONGO_URL", "mongodb://mongo:27017")
    db_name = os.environ.get("DB_NAME", "aegisaxiom")
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    user_doc = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not user_doc:
        print(f"FAIL: user {user_id} not found")
        return 2
    role_name = user_doc.get("role", "customer")
    role_doc = await db.roles.find_one({"name": role_name}, {"_id": 0, "permissions": 1})
    role_permissions = role_doc.get("permissions", []) if role_doc else []
    user_extras = user_doc.get("extra_permissions", [])
    permissions = list(set(role_permissions + user_extras))
    safe_doc = {k: v for k, v in user_doc.items() if k != "password_hash"}
    safe_doc["permissions"] = permissions
    safe_doc["extra_permissions"] = user_extras
    user = User(**safe_doc)

    upload = await db.chat_uploads.find_one({"upload_id": upload_id})
    if not upload:
        print(f"FAIL: upload {upload_id} not found")
        return 2
    print(
        f"using upload={upload_id} user={user.user_id} "
        f"filename={upload.get('filename')} extracted_text_len={len(upload.get('extracted_text') or '')}"
    )

    t0 = time.monotonic()
    try:
        artifact = await run_v3_pipeline(
            db=db,
            user=user,
            upload_ids=[upload_id],
            chat_session_id=f"smoke_{int(t0)}",
            client_company=None,
        )
    except Exception:
        print("FAIL: run_v3_pipeline raised")
        traceback.print_exc()
        return 1

    elapsed = time.monotonic() - t0

    if not isinstance(artifact, DiscoveryAnalysisV3):
        print(f"FAIL: expected DiscoveryAnalysisV3, got {type(artifact)}")
        return 1

    md = artifact.pipeline_metadata
    print()
    print("===== SMOKE REPORT =====")
    print(f"wall-clock         : {elapsed:.1f}s")
    print(f"version            : {artifact.version}")
    print(f"client_company     : {artifact.client_company!r}")
    print(f"extraction_status  : {artifact.extraction_status}")
    print(f"workflows          : {len(artifact.workflows)}")
    print(f"apps               : {len(artifact.apps)}")
    print(f"shared_infra rows  : {len(artifact.shared_infrastructure)}")
    print(f"open_items         : {len(artifact.open_items)}")
    print(f"source_transcripts : {len(artifact.source_transcripts)}")
    print(
        "stage timings (ms) : "
        f"s1={md.stage_1_duration_ms} s2={md.stage_2_duration_ms} "
        f"s3={md.stage_3_duration_ms} s4={md.stage_4_duration_ms} "
        f"total={md.total_duration_ms}"
    )

    if artifact.workflows:
        wf = artifact.workflows[0]
        print()
        print(f"--- workflow[0] : {wf.workflow_id} ({wf.workflow_name})")
        print(f"  partial            : {wf.partial_extraction_failed}")
        print(f"  acceptance_criteria: {len(wf.acceptance_criteria)}")
        print(f"  decision_logic     : {len(wf.decision_logic)}")
        print(f"  process_steps      : {len(wf.process_steps)}")
        print(f"  bpmn_status        : {wf.bpmn_status}")
        print(f"  bpmn_xml present   : {bool(wf.bpmn_xml)} (len={len(wf.bpmn_xml or '')})")
        print(f"  design_doc_status  : {wf.design_doc_status}")
        print(f"  design_doc present : {bool(wf.design_doc)}")
        print(f"  source_flag        : {wf.source.source_flag}")

    if artifact.apps:
        app = artifact.apps[0]
        print()
        print(f"--- app[0] : {app.app_id} ({app.app_name})")
        print(f"  partial            : {app.partial_extraction_failed}")
        print(f"  linked_workflows   : {app.linked_workflow_ids}")
        print(f"  personas           : {len(app.personas)}")
        print(f"  integrations       : {len(app.integrations)}")
        print(f"  design_doc_status  : {app.design_doc_status}")
        print(f"  eadd_doc present   : {bool(app.eadd_doc)}")

    if artifact.open_items[:3]:
        print()
        print("--- first 3 open_items")
        for oi in artifact.open_items[:3]:
            print(f"  [{oi.flag.value}] {oi.location} :: {oi.label}")

    print()
    print("--- model_dump round-trip")
    try:
        dumped = artifact.model_dump(mode="json")
        round_tripped = DiscoveryAnalysisV3.model_validate(dumped)
        json_size = len(json.dumps(dumped))
        print(f"  json bytes        : {json_size}")
        print(f"  round-trip OK     : {round_tripped.version == 'v3'}")
    except Exception:
        print("  FAIL: round-trip raised")
        traceback.print_exc()
        return 1

    print()
    print("===== SMOKE OK =====")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: smoke_v3_pipeline.py <upload_id> <user_id>")
        sys.exit(2)
    sys.exit(asyncio.run(_main(sys.argv[1], sys.argv[2])))

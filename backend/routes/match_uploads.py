"""MATCH ingestion — file upload utility surface.

Adapted from source app ``backend/routers/uploads.py``
(Simplimeta/Appdirect-Order-POC1 @ feature/poc-to-prod).

Accepts .csv / .xls / .xlsx uploads for the four MATCH session slots:
  - provider_report  (POST /provider-report)
  - snapshot         (POST /snapshot?kind=snapshot|customers|orders)

Files are stored on disk under backend/uploads/match/ with a UUID-hex
filename.  The serve endpoint (GET /file/{filename}) streams them back.

No crud_envelope here — uploads are a utility surface, not a CRUD entity.
The session create that consumes these URLs is where the audit trail lives.
"""
from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from auth_helper import get_current_user, has_perm
from middleware.ai_authz import require_ai_permission

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Storage config
# ---------------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).parent.parent  # backend/
UPLOAD_DIR = _BACKEND_DIR / "uploads" / "match"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXT = {".csv", ".xls", ".xlsx"}
MAX_BYTES = 25 * 1024 * 1024  # 25 MB


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
async def _save_upload(file: UploadFile) -> dict:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext or 'unknown'}. Allowed: .csv, .xls, .xlsx",
        )
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 25 MB).")
    if len(raw) == 0:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")
    fid = f"{uuid4().hex}{ext}"
    (UPLOAD_DIR / fid).write_bytes(raw)
    return {"filename": fid, "original_filename": file.filename, "size": len(raw)}


def _file_url(filename: str, request: Request) -> str:
    return f"/api/match/uploads/file/{filename}"


def _detect_provider(filename: str) -> str | None:
    name = (filename or "").lower()
    if "nextiva" in name:
        return "Nextiva"
    if "ringcentral" in name or "ring central" in name:
        return "RingCentral"
    if "zoom" in name:
        return "Zoom"
    if "vonage" in name:
        return "Vonage"
    if "8x8" in name:
        return "8x8"
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post(
    "/provider-report",
    dependencies=[Depends(require_ai_permission("match.session.create"))],
)
async def upload_provider_report(
    request: Request,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    if not has_perm(current_user, "match.session.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    info = await _save_upload(file)
    info["url"] = _file_url(info["filename"], request)
    info["kind"] = "provider_report"
    detected = _detect_provider(info["original_filename"] or "")
    if detected:
        info["detected_provider"] = detected
    return info


@router.post(
    "/snapshot",
    dependencies=[Depends(require_ai_permission("match.session.create"))],
)
async def upload_snapshot(
    request: Request,
    file: UploadFile = File(...),
    kind: str = "snapshot",
    current_user=Depends(get_current_user),
):
    if not has_perm(current_user, "match.session.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    safe_kind = kind if kind in ("customers", "orders", "snapshot") else "snapshot"
    info = await _save_upload(file)
    info["url"] = _file_url(info["filename"], request)
    info["kind"] = safe_kind
    return info


@router.get(
    "/file/{filename}",
    dependencies=[Depends(require_ai_permission("match.session.create"))],
)
async def serve_file(
    filename: str,
    current_user=Depends(get_current_user),
):
    if not has_perm(current_user, "match.session.create"):
        raise HTTPException(status_code=403, detail="Permission denied")
    safe = Path(filename).name  # path-traversal guard
    p = UPLOAD_DIR / safe
    if not p.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(p), filename=safe)

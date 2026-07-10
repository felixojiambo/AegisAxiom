"""Chat file uploads — Vega transcript ingestion endpoint.

Accepts .txt, .md, .docx, .pdf, .bpmn, .xml, .csv, .xlsx uploads,
extracts text, and stores in chat_uploads collection for injection
into agent prompts.
"""

import os
import uuid
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from database import get_db
from auth_helper import get_current_user, has_perm

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_EXTENSIONS = {".txt", ".md", ".markdown", ".docx", ".pdf", ".bpmn", ".xml", ".csv", ".xlsx"}
ALLOWED_CONTENT_TYPES = {
    "text/plain",
    "text/markdown",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/pdf",
    "application/octet-stream",  # fallback for some clients
    "application/xml",
    "text/xml",
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@router.post("/chat-uploads")
async def upload_chat_file(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    current_user=Depends(get_current_user),
):
    """Upload a file to a chat session for agent consumption."""
    if not has_perm(current_user, "chat.access"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = get_db()

    # Validate user owns the session
    session = await db.chat_sessions.find_one(
        {"session_id": session_id, "user_id": current_user.user_id}, {"_id": 0}
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Validate extension
    ext = os.path.splitext(file.filename or "")[1].lower()
    logger.info(f"Upload attempt: filename={file.filename!r}, ext={ext!r}, content_type={file.content_type!r}")
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Accepted: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # Defense in depth: also validate content-type (allow application/octet-stream as fallback)
    if file.content_type and file.content_type not in ALLOWED_CONTENT_TYPES:
        logger.warning(f"Upload content-type mismatch: {file.content_type} for {file.filename}")
        # Don't reject — just log. Extension check is the primary validation.

    # Read file bytes and validate size
    file_bytes = await file.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large ({len(file_bytes) / 1024 / 1024:.1f} MB). Maximum: 10 MB.",
        )

    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    # Extract text — handle new text-based formats directly, fall back to
    # knowledge_base helpers for .txt, .md, .docx, .pdf
    if ext in (".bpmn", ".xml", ".csv"):
        try:
            extracted = file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            extracted = file_bytes.decode("latin-1")
    elif ext == ".xlsx":
        try:
            import openpyxl
            import io
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True)
            rows = []
            for sheet in wb.sheetnames:
                ws = wb[sheet]
                for row in ws.iter_rows(values_only=True):
                    rows.append("\t".join(str(c) if c is not None else "" for c in row))
            extracted = "\n".join(rows)
            wb.close()
        except Exception as e:
            logger.warning(f"xlsx extraction failed, trying generic: {e}")
            extracted = None
    else:
        extracted = None

    if extracted is None:
        # Fall back to the standard extract_text for .txt, .md, .docx, .pdf
        from services.knowledge_base import extract_text

        try:
            extracted = extract_text(file_bytes, file.filename)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Text extraction failed for {file.filename}: {e}")
            raise HTTPException(
                status_code=400,
                detail="Failed to extract text from file. If this is a scanned PDF, please use a text-based PDF instead.",
            )

    if not extracted or not extracted.strip():
        raise HTTPException(
            status_code=400,
            detail="No text could be extracted from the file. If this is a scanned PDF without OCR, please convert it to a text-based PDF first.",
        )

    # Classify document type based on extension
    doc_type_map = {
        ".bpmn": "bpmn_edit",
        ".xml": "bpmn_edit",
        ".csv": "supporting_doc",
        ".xlsx": "supporting_doc",
        ".txt": "transcript",
        ".md": "transcript",
        ".markdown": "transcript",
        ".docx": "transcript",
        ".pdf": "transcript",
    }
    document_type = doc_type_map.get(ext, "supporting_doc")

    upload_id = f"upl_{uuid.uuid4().hex[:12]}"
    upload_doc = {
        "upload_id": upload_id,
        "user_id": current_user.user_id,
        "session_id": session_id,
        "filename": file.filename,
        "file_size_bytes": len(file_bytes),
        "content_type": file.content_type or "application/octet-stream",
        "document_type": document_type,
        "extracted_text": extracted,
        "extracted_at": datetime.now(timezone.utc),
        "linked_to": None,
        "created_at": datetime.now(timezone.utc),
    }
    await db.chat_uploads.insert_one(upload_doc)

    logger.info(f"Chat upload {upload_id}: {file.filename} ({len(extracted)} chars) for session {session_id}")

    return {
        "upload_id": upload_id,
        "filename": file.filename,
        "file_size_bytes": len(file_bytes),
        "extracted_chars": len(extracted),
    }

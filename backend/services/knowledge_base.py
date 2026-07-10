"""Knowledge Base service backed by ChromaDB.

Handles storing, chunking, and semantically searching best-practice
guides used to validate Fireflies meeting transcripts before SOW generation.

ChromaDB runs as a sidecar container and handles embeddings locally
using its default model (all-MiniLM-L6-v2) — no external API needed.
"""

import os
import logging
import uuid
from datetime import datetime, timezone

import chromadb

logger = logging.getLogger(__name__)

CHROMADB_URL = os.environ.get("CHROMADB_URL", "http://aegisaxiom-chromadb:8000")
COLLECTION_NAME = "sow_guides"

# Chunk size for splitting guide documents
CHUNK_SIZE = 1500  # characters
CHUNK_OVERLAP = 200


def _get_client() -> chromadb.HttpClient:
    """Return a ChromaDB HTTP client."""
    use_ssl = CHROMADB_URL.startswith("https://")
    host = CHROMADB_URL.replace("http://", "").replace("https://", "")
    if ":" in host:
        hostname, port = host.rsplit(":", 1)
        return chromadb.HttpClient(host=hostname, port=int(port), ssl=use_ssl)
    default_port = 443 if use_ssl else 8000
    return chromadb.HttpClient(host=host, port=default_port, ssl=use_ssl)


def _get_collection(client: chromadb.HttpClient):
    """Get or create the SOW guides collection."""
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"description": "Best practice guides for SOW transcript validation"},
    )


def _chunk_text(text: str) -> list[str]:
    """Split text into overlapping chunks for embedding.

    Uses a simple character-based splitter that tries to break on
    paragraph/sentence boundaries when possible.
    """
    if len(text) <= CHUNK_SIZE:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE

        # Try to break at a paragraph or sentence boundary
        if end < len(text):
            # Look for paragraph break
            para_break = text.rfind("\n\n", start + CHUNK_SIZE // 2, end)
            if para_break != -1:
                end = para_break + 2
            else:
                # Look for sentence break
                for sep in [". ", ".\n", "? ", "! "]:
                    sent_break = text.rfind(sep, start + CHUNK_SIZE // 2, end)
                    if sent_break != -1:
                        end = sent_break + len(sep)
                        break

        chunks.append(text[start:end].strip())
        start = end - CHUNK_OVERLAP

    return [c for c in chunks if c]


def _extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract text content from a PDF file."""
    from PyPDF2 import PdfReader
    from io import BytesIO

    reader = PdfReader(BytesIO(file_bytes))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n\n".join(pages)


def _extract_text_from_docx(file_bytes: bytes) -> str:
    """Extract text content from a DOCX file."""
    from docx import Document
    from io import BytesIO

    doc = Document(BytesIO(file_bytes))
    return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())


def extract_text(file_bytes: bytes, filename: str) -> str:
    """Extract text from an uploaded file based on its extension."""
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        return _extract_text_from_pdf(file_bytes)
    elif ext in (".docx", ".doc"):
        return _extract_text_from_docx(file_bytes)
    elif ext in (".txt", ".md", ".markdown"):
        return file_bytes.decode("utf-8", errors="replace")
    else:
        raise ValueError(f"Unsupported file type: {ext}")


async def store_guide(
    file_bytes: bytes,
    filename: str,
    uploaded_by: str,
    title: str | None = None,
) -> dict:
    """Parse, chunk, and store a best-practice guide in ChromaDB.

    Returns metadata about the stored guide including its guide_id
    and the number of chunks created.
    """
    text = extract_text(file_bytes, filename)
    if not text.strip():
        raise ValueError("Could not extract any text from the uploaded file")

    guide_id = f"guide_{uuid.uuid4().hex[:12]}"
    display_title = title or os.path.splitext(filename)[0]
    now = datetime.now(timezone.utc).isoformat()

    chunks = _chunk_text(text)

    client = _get_client()
    collection = _get_collection(client)

    # Add each chunk with metadata linking back to the guide
    ids = []
    documents = []
    metadatas = []
    for i, chunk in enumerate(chunks):
        chunk_id = f"{guide_id}_chunk_{i}"
        ids.append(chunk_id)
        documents.append(chunk)
        metadatas.append({
            "guide_id": guide_id,
            "title": display_title,
            "filename": filename,
            "uploaded_by": uploaded_by,
            "uploaded_at": now,
            "chunk_index": i,
            "total_chunks": len(chunks),
        })

    collection.add(ids=ids, documents=documents, metadatas=metadatas)

    logger.info(f"Stored guide '{display_title}' ({guide_id}) — {len(chunks)} chunks")

    return {
        "guide_id": guide_id,
        "title": display_title,
        "filename": filename,
        "chunk_count": len(chunks),
        "uploaded_at": now,
    }


async def search_guides(query_text: str, n_results: int = 10) -> list[dict]:
    """Semantic search for guide chunks relevant to the given text.

    Returns the most relevant chunks grouped by guide, with metadata.
    """
    client = _get_client()
    collection = _get_collection(client)

    # Check if collection has any documents
    if collection.count() == 0:
        return []

    results = collection.query(
        query_texts=[query_text],
        n_results=min(n_results, collection.count()),
    )

    if not results or not results["documents"] or not results["documents"][0]:
        return []

    # Group chunks by guide_id and return the best content
    seen_guides = {}
    chunks_out = []
    for doc, meta, distance in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        guide_id = meta["guide_id"]
        if guide_id not in seen_guides:
            seen_guides[guide_id] = {
                "guide_id": guide_id,
                "title": meta["title"],
                "filename": meta["filename"],
                "relevance_score": round(1 - distance, 3),  # Convert distance to similarity
                "chunks": [],
            }
        seen_guides[guide_id]["chunks"].append(doc)
        chunks_out.append(doc)

    guides = sorted(seen_guides.values(), key=lambda g: g["relevance_score"], reverse=True)
    return guides


async def list_guides() -> list[dict]:
    """List all stored guides (deduplicated by guide_id)."""
    client = _get_client()
    collection = _get_collection(client)

    if collection.count() == 0:
        return []

    # Get all items, pull unique guides from metadata
    all_items = collection.get(include=["metadatas"])
    seen = {}
    for meta in all_items["metadatas"]:
        gid = meta["guide_id"]
        if gid not in seen:
            seen[gid] = {
                "guide_id": gid,
                "title": meta["title"],
                "filename": meta["filename"],
                "uploaded_by": meta["uploaded_by"],
                "uploaded_at": meta["uploaded_at"],
                "chunk_count": meta["total_chunks"],
            }
    return sorted(seen.values(), key=lambda g: g["uploaded_at"], reverse=True)


async def delete_guide(guide_id: str) -> bool:
    """Delete all chunks for a guide from ChromaDB."""
    client = _get_client()
    collection = _get_collection(client)

    # Find all chunk IDs for this guide
    all_items = collection.get(
        where={"guide_id": guide_id},
        include=["metadatas"],
    )

    if not all_items["ids"]:
        return False

    collection.delete(ids=all_items["ids"])
    logger.info(f"Deleted guide {guide_id} ({len(all_items['ids'])} chunks)")
    return True

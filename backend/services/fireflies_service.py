"""Fireflies.ai integration service.

Talks to the Fireflies GraphQL API to list and fetch meeting transcripts.
Used by the SOW SMART Proposal generator to pull meeting context.

API docs: https://docs.fireflies.ai/graphql-api/query/transcripts
"""

import os
import logging
import httpx

logger = logging.getLogger(__name__)

FIREFLIES_API_URL = "https://api.fireflies.ai/graphql"


# Stable error codes surfaced through HTTPException.detail so the
# frontend can branch on a string identifier rather than parsing
# human-readable error text. Shared between the service and the
# routes — both layers raise/catch on the same constants.
FIREFLIES_ERR_KEY_MISSING = "fireflies_api_key_missing"
FIREFLIES_ERR_API = "fireflies_api_error"
FIREFLIES_ERR_TRANSCRIPT_NOT_FOUND = "transcript_not_found"
FIREFLIES_ERR_TRANSCRIPT_EMPTY = "transcript_empty"


class FirefliesError(Exception):
    """Service-level Fireflies failure.

    ``code`` is one of the FIREFLIES_ERR_* constants — a stable string
    the frontend can switch on. ``message`` is a human-readable
    explanation suitable for surfacing to the user. ``message`` MUST
    NOT include the API key or any other secret.
    """

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _get_api_key() -> str:
    """Read FIREFLIES_API_KEY from environment at call time.

    Reading at call time (rather than at module import) keeps the
    service testable — tests can set ``os.environ`` before the call —
    and lets ops rotate the key without a service restart. The previous
    module-constant read froze the value the moment the process
    imported the file.
    """
    return os.environ.get("FIREFLIES_API_KEY", "")


def _headers(api_key: str) -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }


async def list_transcripts(limit: int = 50) -> list[dict]:
    """Fetch recent transcripts from Fireflies.ai.

    Returns a list of transcript summaries (id, title, date, duration,
    participants, summary) sorted by date descending.

    Raises:
        FirefliesError(FIREFLIES_ERR_KEY_MISSING): FIREFLIES_API_KEY
            is not set in the backend environment.
        FirefliesError(FIREFLIES_ERR_API): the HTTP request failed or
            Fireflies returned a GraphQL ``errors`` array.
    """
    api_key = _get_api_key()
    if not api_key:
        raise FirefliesError(
            FIREFLIES_ERR_KEY_MISSING,
            "Fireflies.ai is not configured. Set FIREFLIES_API_KEY in the backend environment.",
        )

    query = """
    query {
        transcripts(limit: %d) {
            id
            title
            date
            duration
            participants
            summary {
                overview
            }
        }
    }
    """ % limit

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                FIREFLIES_API_URL,
                json={"query": query},
                headers=_headers(api_key),
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        # Log the exception type only — never the full message, which
        # could contain header echoes that include the API key.
        logger.error("Fireflies HTTP error listing transcripts: %s", type(e).__name__)
        raise FirefliesError(
            FIREFLIES_ERR_API,
            "Failed to reach Fireflies.ai. Try again in a moment.",
        )

    if "errors" in data:
        logger.error("Fireflies GraphQL error listing transcripts: %s", data["errors"])
        raise FirefliesError(
            FIREFLIES_ERR_API,
            data["errors"][0].get("message", "Fireflies API error"),
        )

    transcripts = data.get("data", {}).get("transcripts", []) or []

    # Normalize the response
    results = []
    for t in transcripts:
        results.append({
            "id": t["id"],
            "title": t.get("title", "Untitled"),
            "date": t.get("date"),
            "duration": t.get("duration"),
            "participants": t.get("participants", []),
            "summary": (t.get("summary") or {}).get("overview", ""),
        })

    return results


async def fetch_transcript_content(transcript_ids: list[str]) -> list[dict]:
    """Fetch full transcript sentences for one or more transcripts.

    Returns a list of ``{id, title, content}`` dicts where ``content``
    is the joined "Speaker: text" lines.

    Raises:
        FirefliesError(FIREFLIES_ERR_KEY_MISSING): FIREFLIES_API_KEY
            is not set in the backend environment.
        FirefliesError(FIREFLIES_ERR_API): every requested ID failed
            because of an HTTP-level or GraphQL-level Fireflies error.
        FirefliesError(FIREFLIES_ERR_TRANSCRIPT_NOT_FOUND): every
            requested ID returned no transcript at all (e.g. wrong ID,
            transcript deleted) AND no API-level error happened — so
            the IDs are genuinely missing rather than the API being
            broken.

    Partial success — some IDs return content, others don't — is
    treated as success and the per-ID failures are dropped (logged).
    The caller decides how to present that. Returning a typed error in
    that case would prevent any AI generation from running when even
    one of N transcripts had a transient problem.
    """
    api_key = _get_api_key()
    if not api_key:
        raise FirefliesError(
            FIREFLIES_ERR_KEY_MISSING,
            "Fireflies.ai is not configured. Set FIREFLIES_API_KEY in the backend environment.",
        )

    results = []
    last_api_error: str | None = None

    for tid in transcript_ids:
        query = """
        query GetTranscript($id: String!) {
            transcript(id: $id) {
                id
                title
                sentences {
                    text
                    speaker_name
                }
            }
        }
        """

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    FIREFLIES_API_URL,
                    json={"query": query, "variables": {"id": tid}},
                    headers=_headers(api_key),
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            logger.error(
                "Fireflies HTTP error fetching transcript %s: %s",
                tid, type(e).__name__,
            )
            last_api_error = "Failed to reach Fireflies.ai. Try again in a moment."
            continue

        if "errors" in data:
            err_msg = data["errors"][0].get("message", "Fireflies API error")
            logger.error("Fireflies GraphQL error for %s: %s", tid, err_msg)
            last_api_error = err_msg
            continue

        transcript = data.get("data", {}).get("transcript") or {}
        sentences = transcript.get("sentences") or []

        text_lines = []
        for s in sentences:
            speaker = s.get("speaker_name", "Unknown")
            text = s.get("text", "")
            if text.strip():
                text_lines.append(f"{speaker}: {text}")

        results.append({
            "id": transcript.get("id", tid),
            "title": transcript.get("title", "Untitled"),
            "content": "\n".join(text_lines),
        })

    if not results:
        # Every requested transcript ID failed. Distinguish "Fireflies
        # was up but had no record" from "Fireflies itself errored" so
        # the user sees the right toast.
        if last_api_error is not None:
            raise FirefliesError(FIREFLIES_ERR_API, last_api_error)
        raise FirefliesError(
            FIREFLIES_ERR_TRANSCRIPT_NOT_FOUND,
            "Fireflies returned no content for the selected transcript(s).",
        )

    return results

"""Google Docs service for SOW document generation.

Creates formatted Google Docs from a branded template by copying it
and replacing placeholders with SOW data.  Uses the logged-in user's
Google OAuth token.  Falls back to a service account when the user has
not connected their Google account.
"""

import os
import re
import logging
import httpx
from datetime import datetime, timezone, timedelta

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SERVICE_ACCOUNT_FILE = os.environ.get(
    "GOOGLE_SERVICE_ACCOUNT_FILE", "google-service-account.json"
)
# DRIVE_FOLDER_ID is retained for backward compatibility with callers
# that import the module-level constant. The canonical env var is
# SIMPLIMETA_SOW_FOLDER_ID; see resolve_sow_folder_id() below for
# the runtime resolution order. See
# docs/sales-ops/GOOGLE_DOC_ARTIFACT_PATH.md.
DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")
IMPERSONATE_EMAIL = os.environ.get("GOOGLE_IMPERSONATE_EMAIL", "")


# Marker exceptions the routes translate into the canonical
# structured error codes. Keeping them in the service module means
# every Google-Docs surface lifts the same envelope without route
# duplication. See docs/sales-ops/GOOGLE_DOC_ARTIFACT_PATH.md for
# the canonical code list.
class GoogleDocsFolderNotConfigured(Exception):
    """Neither SIMPLIMETA_SOW_FOLDER_ID nor the legacy
    GOOGLE_DRIVE_FOLDER_ID is set. The route should return the
    google_docs_folder_not_configured envelope."""


class GoogleDocsPermissionDenied(Exception):
    """Drive returned 403 on a copy or move into the configured
    folder. The route should return google_docs_permission_denied.
    """


class GoogleDocsFolderNotFound(Exception):
    """Drive returned 404 looking up the configured folder. The
    route should return google_docs_folder_not_found."""


def resolve_sow_folder_id() -> str:
    """Return the configured SOW Drive folder id.

    Resolution order. SIMPLIMETA_SOW_FOLDER_ID is the canonical
    name; GOOGLE_DRIVE_FOLDER_ID stays valid as a backward-
    compatible alias. Empty strings are treated as unset so a
    deploy that ships an empty value does not collapse silently
    onto the wrong drive.

    Raises GoogleDocsFolderNotConfigured when neither variable
    carries a non-empty value. The exception text never includes
    the resolved value; callers must not echo this value to a
    response body or an audit row.
    """
    canonical = (os.environ.get("SIMPLIMETA_SOW_FOLDER_ID") or "").strip()
    if canonical:
        return canonical
    legacy = (os.environ.get("GOOGLE_DRIVE_FOLDER_ID") or "").strip()
    if legacy:
        return legacy
    raise GoogleDocsFolderNotConfigured(
        "SOW Drive folder env var is not set"
    )


def is_sow_folder_configured() -> bool:
    """Boolean version of resolve_sow_folder_id() for routes that
    want a configured-yes/no signal without catching an exception.
    Never returns the folder id itself."""
    try:
        resolve_sow_folder_id()
    except GoogleDocsFolderNotConfigured:
        return False
    return True


def _raise_for_drive_error(exc: Exception) -> None:
    """Translate a Google API exception into the canonical marker
    exceptions when its HTTP status is recognized.

    Returns silently for any exception that does not look like a
    Drive HttpError so the original exception propagates and the
    catch-all in the route maps it to
    google_docs_export_failed. The googleapiclient HttpError type
    carries a `resp.status` attribute; this helper checks
    duck-typed access rather than importing googleapiclient at
    module load so the unit tests can substitute a synthetic
    exception with the same shape without standing up the real
    library.
    """
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status == 403:
        raise GoogleDocsPermissionDenied(
            "Drive copy rejected with permission denied"
        ) from exc
    if status == 404:
        raise GoogleDocsFolderNotFound(
            "Drive copy rejected with folder not found"
        ) from exc


def parse_google_doc_id(url: str) -> str | None:
    """Return the Drive document id parsed from a Google Docs URL.

    Accepts the two URL shapes the workspace produces:

      https://docs.google.com/document/d/{ID}/edit
      https://docs.google.com/document/d/{ID}/

    Returns None for any other shape so a caller that handed in
    something unparsable can decide to refuse the write. The
    hostname allowlist is enforced separately at the route layer;
    this helper assumes the URL has already cleared that check.
    """
    if not isinstance(url, str) or not url:
        return None
    m = re.search(r"/document/d/([A-Za-z0-9_-]{8,})(?:/|$|\?)", url)
    if not m:
        return None
    return m.group(1)

# Google Doc template ID for branded SOW documents
SOW_TEMPLATE_ID = os.environ.get(
    "SOW_TEMPLATE_ID", "1eA7OfI0ighkPYIVRi-Cj1E_BeaRVqWZKCKenADCSFyM"
)

SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
]

# Default branding values (used when no branding config exists)
DEFAULT_LEGAL_STATEMENT = (
    "Unless otherwise agreed to in this document, the Terms and Conditions are "
    "governed by MetaData Engineering d/b/a SimpliMeta's Master Services Agreement "
    "available for review at https://simplimeta.io/legal"
)
DEFAULT_PROVIDER_NAME = "MetaData Engineering d/b/a SimpliMeta"


def _get_credentials():
    """Load service account credentials."""
    path = SERVICE_ACCOUNT_FILE
    if not os.path.isabs(path):
        for prefix in ["", "backend/", "/app/backend/"]:
            candidate = os.path.join(prefix, path)
            if os.path.exists(candidate):
                path = candidate
                break

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Google service account file not found: {SERVICE_ACCOUNT_FILE}"
        )

    creds = service_account.Credentials.from_service_account_file(path, scopes=SCOPES)

    if IMPERSONATE_EMAIL:
        creds = creds.with_subject(IMPERSONATE_EMAIL)

    return creds


async def _get_user_credentials(db, user_id: str) -> Credentials:
    """Load user's Google OAuth token from Postgres, auto-refreshing if expired.

    Phase 2a: oauth_tokens live in Postgres. `db` (Mongo) is unused here now but
    kept in the signature for caller compatibility.
    """
    from db.session import get_sessionmaker
    from repositories.auth import AuthRepository

    sm = get_sessionmaker()
    async with sm() as pg:
        ar = AuthRepository(pg)
        tok = await ar.get_oauth_token("google", user_id=user_id)
        if not tok or not tok.access_token:
            raise FileNotFoundError(
                "Google Docs is not connected. Please sign out and sign back in with Google."
            )

        expires_at = tok.expires_at
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        access_token = tok.access_token

        if expires_at and expires_at < datetime.now(timezone.utc) + timedelta(minutes=5):
            refresh_token = tok.refresh_token
            if not refresh_token:
                raise FileNotFoundError(
                    "Google session expired. Please sign out and sign back in with Google."
                )

            client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
            client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                    timeout=10.0,
                )
                if resp.status_code != 200:
                    raise FileNotFoundError(
                        "Failed to refresh Google token. Please sign out and sign back in."
                    )
                new_tokens = resp.json()

            access_token = new_tokens["access_token"]
            new_expires = datetime.now(timezone.utc) + timedelta(
                seconds=new_tokens.get("expires_in", 3600)
            )

            await ar.upsert_oauth_token(
                token_id=f"google_{user_id}", provider="google", user_id=user_id,
                access_token=access_token, refresh_token=refresh_token,
                expires_at=new_expires, scope=tok.scope,
            )
            await pg.commit()
            logger.info(f"Refreshed Google token for user {user_id}")

        return Credentials(token=access_token)


def _strip_markdown(text: str) -> str:
    """Strip markdown formatting to plain text for template insertion."""
    if not text:
        return ""
    # Remove **bold** markers
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    # Remove ## / # heading markers
    text = re.sub(r'^#{1,3}\s+', '', text, flags=re.MULTILINE)
    return text.strip()


def _clean_section_text(text: str) -> str:
    """Clean AI-generated section text for template insertion.

    Strips markdown, removes section headers and bullet prefixes
    since the Google Doc template already provides bullet formatting.
    """
    if not text:
        return ""
    text = _strip_markdown(text)
    # Remove section headers like "Assumptions" or "Risks"
    text = re.sub(r'(?m)^(Assumptions|Risks)\s*:?\s*$', '', text)
    # Remove markdown bullet prefixes (template provides bullet formatting)
    text = re.sub(r'(?m)^\s*[-•]\s+', '', text)
    # Collapse multiple blank lines and trim
    text = re.sub(r'\n{2,}', '\n', text).strip()
    return text


def _build_replacements(
    sow: dict,
    opportunity: dict,
    assumptions_risks: str,
    branding: dict = None,
    author_name: str = "",
) -> list:
    """Build Google Docs replaceAllText requests for template placeholders."""
    branding = branding or {}
    provider_name = branding.get("provider_name") or DEFAULT_PROVIDER_NAME
    legal_text = branding.get("legal_text") or DEFAULT_LEGAL_STATEMENT

    client_name = opportunity.get("account_name", "Client")
    opp_name = opportunity.get("name", "Opportunity")

    # Milestones are handled separately via _replace_milestone_section
    milestones = sow.get("milestones", [])

    # Calculate total estimated hours for all milestones
    grand_total = sum(
        sum(b.get("estimated_hours", 0) for b in ms.get("bullets", []))
        for ms in milestones
    )

    # Build billing details text
    billing_email = sow.get("billing_account_email", "")
    billing_phone = sow.get("billing_account_phone", "")
    payment_terms = (sow.get("payment_terms", "") or "").replace("_", " ").title()
    billing_terms = (sow.get("billing_terms", "") or "").replace("_", " ").title()
    billing_frequency = (sow.get("billing_frequency", "") or "").replace("_", " ").title()
    special = sow.get("special_billing_instructions", "")
    contract_type = (sow.get("contract_type", "") or "").replace("_", " ").title()
    total_value = sow.get("total_value", "")
    if total_value:
        total_value = f"${total_value:,.2f}" if isinstance(total_value, (int, float)) else str(total_value)

    # BOA authorized users
    boa_text = ""
    boa_users = sow.get("boa_authorized_users", [])
    if boa_users:
        boa_text = "Authorized Users (Bill on Actuals):\n"
        for user in boa_users:
            name = user.get("name", "")
            email = user.get("email", "")
            phone = user.get("phone", "")
            max_h = user.get("max_hours_allowed", "")
            boa_text += f"  - {name} — {email} | {phone} | Max Hours: {max_h or 'Unlimited'}\n"

    # Split assumptions_risks into separate sections and clean for template insertion
    assumptions_text = ""
    risks_text = ""
    if assumptions_risks:
        if isinstance(assumptions_risks, dict):
            assumptions_text = _clean_section_text(assumptions_risks.get("assumptions", ""))
            risks_text = _clean_section_text(assumptions_risks.get("risks", ""))
        else:
            # Split on ## Risks / Risks header (with or without markdown/colon)
            parts = re.split(r"(?mi)^#{0,3}\s*risks?\s*:?\s*$", assumptions_risks, maxsplit=1)
            assumptions_text = _clean_section_text(parts[0])
            risks_text = _clean_section_text(parts[1]) if len(parts) > 1 else ""

    replacements = {
        "{{PROJECT_TITLE}}": f"{opp_name} - Statement of Work",
        "{{AUTHOR_NAME}}": author_name or "[Not assigned]",
        "{{CLIENT_NAME}}": client_name,
        "{{DATE}}": datetime.now(timezone.utc).strftime("%B %d, %Y"),
        "{{EXECUTIVE_SUMMARY}}": sow.get("executive_summary", "") or "[Executive summary to be completed]",
        "{{SMART_SPECIFIC}}": sow.get("smart_specific", "") or "[Not specified]",
        "{{SMART_MEASURABLE}}": sow.get("smart_measurable", "") or "[Not specified]",
        "{{SMART_ACHIEVABLE}}": sow.get("smart_achievable", "") or "[Not specified]",
        "{{SMART_RELEVANT}}": sow.get("smart_relevant", "") or "[Not specified]",
        "{{SMART_TIME_BOUND}}": sow.get("smart_time_bound", "") or "[Not specified]",
        "{{CONTRACT_TYPE}}": contract_type or "[Not specified]",
        "{{BILLING_TERMS}}": billing_terms or "[Not specified]",
        "{{BILLING_FREQUENCY}}": billing_frequency or "[Not specified]",
        "{{BILLING_EMAIL}}": billing_email or "[Not specified]",
        "{{BILLING_PHONE}}": billing_phone or "[Not specified]",
        "{{PAYMENT_TERMS}}": payment_terms or "[Not specified]",
        "{{TOTAL_VALUE}}": total_value or "[Not specified]",
        "{{SPECIAL_BILLING}}": special or "",
        "{{BOA_USERS}}": boa_text,
        "{{TOTAL_ESTIMATED_HOURS}}": str(grand_total),
        "{{ASSUMPTIONS_RISKS}}": _clean_section_text(assumptions_risks) if assumptions_risks else "[To be determined]",
        "{{ASSUMPTIONS}}": assumptions_text or "[To be determined]",
        "{{RISKS}}": risks_text or "[To be determined]",
        "{{LEGAL_TEXT}}": legal_text,
        "{{PROVIDER_NAME}}": provider_name,
    }

    requests = []
    for placeholder, value in replacements.items():
        requests.append({
            "replaceAllText": {
                "containsText": {
                    "text": placeholder,
                    "matchCase": True,
                },
                "replaceText": value,
            }
        })

    return requests


async def _replace_milestone_section(docs_service, doc_id, milestones):
    """Replace {{MILESTONE}}/{{TASK}}/{{DELIVERABLE}} template section with actual data.

    Uses structural document edits (delete + insert + createParagraphBullets)
    because replaceAllText cannot handle repeating sections.
    """
    import asyncio

    doc = await asyncio.to_thread(
        docs_service.documents().get(documentId=doc_id).execute
    )
    body_content = doc.get("body", {}).get("content", [])

    # Find the milestone template section boundaries
    section_start = None
    section_end = None
    for element in body_content:
        if "paragraph" not in element:
            continue
        para_text = "".join(
            e.get("textRun", {}).get("content", "")
            for e in element["paragraph"].get("elements", [])
        )
        if "{{MILESTONE}}" in para_text and section_start is None:
            section_start = element["startIndex"]
        if "{{DELIVERABLE}}" in para_text:
            section_end = element["endIndex"]

    if section_start is None or section_end is None:
        logger.warning("Milestone template placeholders not found in document")
        return

    # Build replacement content with leading tabs for bullet nesting.
    # Google Docs createParagraphBullets uses leading tab count for nesting:
    #   \t  = level 0 (•)
    #   \t\t = level 1 (○)
    lines = []  # list of (text_with_tabs, type)
    for i, ms in enumerate(milestones, start=1):
        ms_name = ms.get("milestone_name", "Milestone")
        lines.append((f"Phase {i}: {ms_name}\n", "heading"))
        for bullet in ms.get("bullets", []):
            task = bullet.get("bullet_text", "")
            if task:
                lines.append(("\t" + task + "\n", "task"))
            deliverable = bullet.get("deliverable", "")
            if deliverable:
                lines.append(("\t\tDeliverable: " + deliverable + "\n", "deliverable"))

    if not lines:
        lines = [("[No milestones defined]\n", "heading")]

    full_text = "".join(t for t, _ in lines)

    # Step 1 & 2: Delete template section, insert new content
    requests = [
        {
            "deleteContentRange": {
                "range": {"startIndex": section_start, "endIndex": section_end}
            }
        },
        {
            "insertText": {
                "location": {"index": section_start},
                "text": full_text,
            }
        },
    ]

    # Step 3: Bold the milestone heading lines
    offset = section_start
    for text, line_type in lines:
        if line_type == "heading":
            requests.append({
                "updateTextStyle": {
                    "range": {
                        "startIndex": offset,
                        "endIndex": offset + len(text) - 1,
                    },
                    "textStyle": {"bold": True},
                    "fields": "bold",
                }
            })
        offset += len(text)

    # Step 4: Apply bullets to contiguous groups of task/deliverable lines.
    # Process groups in reverse order so tab-removal index shifts don't
    # affect earlier groups.
    offset = section_start
    group_start = None
    group_end = None
    bullet_groups = []
    for text, line_type in lines:
        line_end = offset + len(text)
        if line_type in ("task", "deliverable"):
            if group_start is None:
                group_start = offset
            group_end = line_end
        else:
            if group_start is not None:
                bullet_groups.append((group_start, group_end))
                group_start = None
        offset = line_end
    if group_start is not None:
        bullet_groups.append((group_start, group_end))

    for gs, ge in reversed(bullet_groups):
        requests.append({
            "createParagraphBullets": {
                "range": {"startIndex": gs, "endIndex": ge},
                "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
            }
        })

    await asyncio.to_thread(
        docs_service.documents()
        .batchUpdate(documentId=doc_id, body={"requests": requests})
        .execute
    )
    logger.info(f"Replaced milestone section with {len(milestones)} milestones")


async def _copy_template(drive_service, title: str, folder_id: str = None) -> dict:
    """Copy the SOW template and return the new file metadata."""
    import asyncio

    body = {"name": title}
    if folder_id:
        body["parents"] = [folder_id]

    created = await asyncio.to_thread(
        drive_service.files()
        .copy(fileId=SOW_TEMPLATE_ID, body=body, fields="id,webViewLink")
        .execute
    )
    return created


async def create_sow_google_doc(
    sow: dict,
    opportunity: dict,
    assumptions_risks: str,
    share_with_email: str = None,
    db=None,
) -> dict:
    """Create a branded SOW Google Doc from template (service account).

    Returns: {"doc_id": str, "doc_url": str}
    """
    import asyncio

    branding = None
    if db is not None:
        from db.session import get_sessionmaker
        from repositories.org_branding import BrandingRepository
        _sm = get_sessionmaker()
        async with _sm() as _pg:
            _b = await BrandingRepository(_pg).get_branding("default")
        branding = _b.model_dump() if _b else None

    creds = _get_credentials()
    docs_service = build("docs", "v1", credentials=creds)
    drive_service = build("drive", "v3", credentials=creds)

    client_name = opportunity.get("account_name", "Client")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title = f"{client_name} - Statement of Work - {today}"

    # Resolve the folder at call time so both create paths follow
    # the same env preference order. See
    # docs/sales-ops/GOOGLE_DOC_ARTIFACT_PATH.md. The marker
    # exception propagates so the route layer maps it to the
    # google_docs_folder_not_configured envelope.
    folder_id = resolve_sow_folder_id()

    # Copy the branded template
    try:
        created_file = await _copy_template(drive_service, title, folder_id)
    except Exception as e:  # noqa: BLE001
        _raise_for_drive_error(e)
        raise
    doc_id = created_file["id"]
    doc_url = created_file.get("webViewLink", f"https://docs.google.com/document/d/{doc_id}/edit")

    # No URL in the log line. The doc id encodes the same routable
    # handle as the URL; both are intentionally kept out of the
    # log aggregator. Audit rows and the response body carry the
    # url for the caller.
    logger.info("Created SOW from template (service account path)")

    # Replace placeholders
    author_name = sow.get("assigned_to_name", "")
    doc_requests = _build_replacements(sow, opportunity, assumptions_risks, branding, author_name)
    if doc_requests:
        await asyncio.to_thread(
            docs_service.documents()
            .batchUpdate(documentId=doc_id, body={"requests": doc_requests})
            .execute
        )

    # Replace milestone template section with structured content
    await _replace_milestone_section(docs_service, doc_id, sow.get("milestones", []))

    # Share with billing contact
    if share_with_email:
        try:
            await asyncio.to_thread(
                drive_service.permissions()
                .create(
                    fileId=doc_id,
                    body={
                        "type": "user",
                        "role": "commenter",
                        "emailAddress": share_with_email,
                    },
                    sendNotificationEmail=True,
                    emailMessage=(
                        "A Statement of Work has been prepared for your review. "
                        "Please review the document and provide your signature."
                    ),
                )
                .execute
            )
            logger.info(f"Shared doc with {share_with_email}")
        except Exception as e:
            logger.warning(f"Failed to share doc with {share_with_email}: {e}")

    return {"doc_id": doc_id, "doc_url": doc_url}


async def create_sow_google_doc_user(
    db,
    user_id: str,
    sow: dict,
    opportunity: dict,
    assumptions_risks: str,
) -> dict:
    """Create a branded SOW Google Doc using the user's own Google OAuth token.

    Copies the branded template into the user's Drive, then replaces
    placeholders with SOW data.  Falls back to the service account if
    the user has no token stored.

    Returns: {"doc_id": str, "doc_url": str}
    """
    import asyncio

    try:
        creds = await _get_user_credentials(db, user_id)
    except FileNotFoundError:
        logger.warning(f"No Google token for user {user_id}, falling back to service account")
        try:
            creds = _get_credentials()
        except FileNotFoundError:
            raise FileNotFoundError(
                "Google Docs is not connected. Please sign out and sign back in with Google to authorize."
            )

    docs_service = build("docs", "v1", credentials=creds)
    drive_service = build("drive", "v3", credentials=creds)

    client_name = opportunity.get("account_name", "Client")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title = f"{client_name} - Statement of Work - {today}"

    # Resolve the SOW folder up front. The user-OAuth path historically
    # left the doc in the user's My Drive; per the artifact-path doc
    # the structured export must place every doc in the configured
    # SimpliMeta folder so a future viewer with folder access can
    # find it without joining back to the SOW row. Folder resolution
    # raises GoogleDocsFolderNotConfigured when neither env var is
    # set; routes translate that into the canonical structured error
    # envelope. See docs/sales-ops/GOOGLE_DOC_ARTIFACT_PATH.md.
    folder_id = resolve_sow_folder_id()

    # Copy the branded template into the configured folder. Drive
    # errors during the copy surface as marker exceptions so the
    # route layer can map 403 / 404 onto google_docs_permission_denied
    # and google_docs_folder_not_found respectively.
    try:
        created_file = await _copy_template(drive_service, title, folder_id)
    except Exception as e:  # noqa: BLE001 — narrow below
        _raise_for_drive_error(e)
        raise
    doc_id = created_file["id"]
    doc_url = created_file.get("webViewLink", f"https://docs.google.com/document/d/{doc_id}/edit")

    # No URL in the log line. The doc id encodes the same routable
    # handle as the URL; both are intentionally kept out of the
    # log aggregator. Audit rows and the response body carry the
    # url for the caller.
    logger.info("Created SOW from template (user OAuth path) user=%s", user_id)

    # Fetch org branding + author name (Postgres)
    from db.session import get_sessionmaker
    from repositories.org_branding import BrandingRepository
    from repositories.users import UserRepository
    _sm = get_sessionmaker()
    async with _sm() as _pg:
        _b = await BrandingRepository(_pg).get_branding("default")
        _user = await UserRepository(_pg).get_user_by_id(user_id)
    branding = _b.model_dump() if _b else None

    author_name = sow.get("assigned_to_name", "")
    if not author_name and _user:
        # Canonical schema has a single `name` field (no first/last split).
        author_name = (_user.name or "").strip()

    # Replace placeholders
    doc_requests = _build_replacements(sow, opportunity, assumptions_risks, branding, author_name)
    if doc_requests:
        await asyncio.to_thread(
            docs_service.documents()
            .batchUpdate(documentId=doc_id, body={"requests": doc_requests})
            .execute
        )

    # Replace milestone template section with structured content
    await _replace_milestone_section(docs_service, doc_id, sow.get("milestones", []))

    return {"doc_id": doc_id, "doc_url": doc_url}

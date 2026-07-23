from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import RedirectResponse
from datetime import datetime, timezone
from urllib.parse import urlparse
import os
import uuid
import secrets
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from db.session import get_session
from repositories.auth import AuthRepository
from auth_helper import get_current_user, has_perm, get_frontend_url
from models import OAuthCodeRequest
from services.zoho_service import ZohoService

logger = logging.getLogger(__name__)

router = APIRouter()
zoho = ZohoService()


def _callback_base(request: Request) -> str:
    """
    Derive the base URL for OAuth callback URIs from the incoming request.
    Handles reverse proxy scenarios (X-Forwarded-Proto, X-Forwarded-Host).
    Falls back to ZOHO_REDIRECT_URI's base if available.
    """
    # Check X-Forwarded headers first (behind proxy/load balancer)
    forwarded_proto = request.headers.get("x-forwarded-proto", "").strip()
    forwarded_host = request.headers.get("x-forwarded-host", "").strip()
    if forwarded_host:
        scheme = forwarded_proto or "https"
        return f"{scheme}://{forwarded_host}"

    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if origin:
        return origin

    parsed = urlparse(str(request.url))
    if parsed.hostname and parsed.hostname not in ("localhost", "127.0.0.1", "0.0.0.0"):
        return f"{parsed.scheme}://{parsed.netloc}"

    # Fallback: derive from ZOHO_REDIRECT_URI env var
    zoho_redir = os.environ.get("ZOHO_REDIRECT_URI", "")
    if zoho_redir:
        p = urlparse(zoho_redir)
        return f"{p.scheme}://{p.netloc}"

    return ""



@router.get("/status")
async def get_status(current_user=Depends(get_current_user), pg: AsyncSession = Depends(get_session)):
    # SECURITY FIX H-5: Use has_perm() instead of hardcoded role check
    if not has_perm(current_user, "integrations.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    zoho_tok = await AuthRepository(pg).get_oauth_token("zoho")
    return {
        "zoho": {"connected": bool(zoho_tok and zoho_tok.access_token), "expires_at": str(zoho_tok.expires_at) if zoho_tok else None},
    }


@router.get("/zoho/auth-url")
async def zoho_auth_url(request: Request, current_user=Depends(get_current_user), pg: AsyncSession = Depends(get_session)):
    if not has_perm(current_user, "integrations.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    # Use ZOHO_REDIRECT_URI env var if set (must match Zoho API console), else derive from request
    env_redirect = os.environ.get("ZOHO_REDIRECT_URI", "").strip()
    redirect_uri = env_redirect if env_redirect else f"{_callback_base(request)}/api/integrations/zoho/callback"
    logger.info(f"Zoho auth URL requested: redirect_uri={redirect_uri}")
    state = secrets.token_urlsafe(32)
    await AuthRepository(pg).create_oauth_state(
        state, "zoho", user_id=current_user.user_id, redirect_uri=redirect_uri
    )
    return {"url": zoho.get_auth_url(redirect_uri=redirect_uri, state=state)}


# ---- Zoho OAuth GET callback (Zoho redirects here after user authorizes) ----
@router.get("/zoho/callback")
async def zoho_oauth_callback(request: Request, code: str = None, error: str = None, state: str = None, pg: AsyncSession = Depends(get_session)):
    """Handles the GET redirect from Zoho after OAuth authorization."""
    frontend_url = get_frontend_url(request)
    logger.info(f"Zoho OAuth callback: frontend_url={frontend_url}, has_code={bool(code)}, has_state={bool(state)}")
    if error or not code:
        logger.warning(f"Zoho callback error: {error or 'no_code'}")
        return RedirectResponse(url=f"{frontend_url}/admin/integrations?zoho=error&msg={error or 'no_code'}")
    # Validate CSRF state and retrieve the stored redirect_uri
    auth_repo = AuthRepository(pg)
    stored_redirect_uri = None
    if state:
        state_doc = await auth_repo.consume_oauth_state(state, "zoho")
        if not state_doc:
            logger.warning("Zoho callback: invalid or expired state parameter")
            return RedirectResponse(url=f"{frontend_url}/admin/integrations?zoho=error&msg=invalid_state")
        stored_redirect_uri = state_doc.redirect_uri
    # Use the exact redirect_uri from auth URL generation (guarantees match)
    redirect_uri = stored_redirect_uri or f"{_callback_base(request)}/api/integrations/zoho/callback"
    logger.info(f"Zoho token exchange: redirect_uri={redirect_uri}")
    try:
        result = await zoho.exchange_code(code, redirect_uri=redirect_uri)
        await auth_repo.upsert_oauth_token(
            token_id=result.get("token_id", "zoho"), provider="zoho",
            access_token=result["access_token"], refresh_token=result.get("refresh_token"),
            expires_at=result.get("expires_at"), scope=result.get("scope"),
        )
        logger.info(f"Zoho connected: access_token={'yes' if result.get('access_token') else 'NO'}, refresh_token={'yes' if result.get('refresh_token') else 'NO'}")
        return RedirectResponse(url=f"{frontend_url}/admin/integrations?zoho=connected")
    except Exception as e:
        logger.error(f"Zoho token exchange failed: {e}")
        from urllib.parse import quote
        return RedirectResponse(url=f"{frontend_url}/admin/integrations?zoho=error&msg={quote(str(e)[:200])}")


@router.post("/zoho/callback")
async def zoho_callback_post(request: Request, data: OAuthCodeRequest, current_user=Depends(get_current_user), pg: AsyncSession = Depends(get_session)):
    """Manual code exchange (for testing)."""
    if not has_perm(current_user, "integrations.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    redirect_uri = f"{_callback_base(request)}/api/integrations/zoho/callback"
    result = await zoho.exchange_code(data.code, redirect_uri=redirect_uri)
    await AuthRepository(pg).upsert_oauth_token(
        token_id=result.get("token_id", "zoho"), provider="zoho",
        access_token=result["access_token"], refresh_token=result.get("refresh_token"),
        expires_at=result.get("expires_at"), scope=result.get("scope"),
    )
    return {"message": "Zoho connected successfully"}


@router.delete("/zoho/disconnect")
async def zoho_disconnect(current_user=Depends(get_current_user), pg: AsyncSession = Depends(get_session)):
    # SECURITY FIX H-5: Use has_perm() instead of hardcoded role check
    if not has_perm(current_user, "integrations.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    await AuthRepository(pg).delete_oauth_token("zoho")
    return {"message": "Zoho disconnected"}


@router.get("/webhook-logs")
async def get_webhook_logs(current_user=Depends(get_current_user)):
    # SECURITY FIX H-5: Use has_perm() instead of hardcoded role check
    if not has_perm(current_user, "webhooks.view"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    logs = await db.webhook_logs.find({}, {"_id": 0}).sort("created_at", -1).to_list(100)
    return logs


@router.post("/sync/zoho/commissions")
async def sync_zoho_commissions(current_user=Depends(get_current_user)):
    if not has_perm(current_user, "integrations.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    count = await zoho.sync_commission_data(db)
    return {"message": f"Synced {count} commission records from Zoho"}


@router.post("/sync/zoho/deals-enriched")
async def sync_zoho_deals_enriched(current_user=Depends(get_current_user)):
    if not has_perm(current_user, "integrations.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    count = await zoho.sync_deals_enriched(db)
    return {"message": f"Synced {count} enriched deal records from Zoho"}


@router.post("/sync/zoho")
async def sync_zoho(current_user=Depends(get_current_user)):
    if not has_perm(current_user, "integrations.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    synced, updated = await zoho.sync_accounts_from_zoho(db)
    return {"message": f"Synced {synced} new accounts, updated {updated} existing from Zoho"}


@router.post("/sync/zoho/deals")
async def sync_zoho_deals(current_user=Depends(get_current_user)):
    # SECURITY FIX H-5: Use has_perm() instead of hardcoded role check
    if not has_perm(current_user, "integrations.manage") and not has_perm(current_user, "deals.sync"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    synced, updated, accounts_created = await zoho.sync_deals_from_zoho(db)
    msg = f"Synced {synced} new deals, updated {updated} existing deals from Zoho"
    if accounts_created:
        msg += f", created {accounts_created} new accounts"
    return {"message": msg, "new": synced, "updated": updated, "accounts_created": accounts_created}


@router.post("/sync/zoho/full")
async def sync_zoho_full(current_user=Depends(get_current_user)):
    """Manual full sync: accounts, deals (with Closed Won enrichment), commissions, enriched deals."""
    if not has_perm(current_user, "integrations.manage"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    db = get_db()
    results = {}
    try:
        acc_new, acc_updated = await zoho.sync_accounts_from_zoho(db)
        results["accounts"] = f"{acc_new} new, {acc_updated} updated"
    except Exception as e:
        results["accounts"] = f"Error: {e}"
    try:
        deal_new, deal_updated, deal_accts = await zoho.sync_deals_from_zoho(db)
        results["deals"] = f"{deal_new} new, {deal_updated} updated, {deal_accts} accounts auto-created (Closed Won deals enriched)"
    except Exception as e:
        results["deals"] = f"Error: {e}"
    try:
        comm_count = await zoho.sync_commission_data(db)
        results["commissions"] = f"{comm_count} synced"
    except Exception as e:
        results["commissions"] = f"Error: {e}"
    try:
        enriched_count = await zoho.sync_deals_enriched(db)
        results["enriched_deals"] = f"{enriched_count} synced"
    except Exception as e:
        results["enriched_deals"] = f"Error: {e}"
    return {"message": "Full sync complete", "results": results}


# ---- PUBLIC WEBHOOK RECEIVERS ----

def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 signature for webhook payloads"""
    import hmac
    import hashlib
    if not secret or secret == "change-me-in-production":
        import logging
        production_mode = os.getenv("PRODUCTION_MODE", "false").lower() == "true"
        if production_mode:
            logging.getLogger(__name__).warning("Webhook secret not configured — rejecting in production")
            return False
        logging.getLogger(__name__).warning("Webhook secret not configured — allowing in development")
        return True
    computed = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)


@router.post("/webhooks/zoho/crm")
async def zoho_crm_webhook(request: Request):
    # SECURITY FIX C-4: Add webhook signature verification
    signature = request.headers.get("X-Zoho-Signature", "")
    secret = os.getenv("ZOHO_WEBHOOK_SECRET", "")
    
    body = await request.body()
    
    if not verify_webhook_signature(body, signature, secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    
    db = get_db()
    payload = await request.json()
    log_id = f"log_{uuid.uuid4().hex[:12]}"
    processed, error = True, None
    try:
        await zoho.process_crm_webhook(payload, db)
    except Exception as e:
        processed, error = False, str(e)
    await db.webhook_logs.insert_one(
        {"log_id": log_id, "source": "zoho_crm", "event_type": payload.get("operation", "unknown"), "payload": payload, "processed": processed, "error": error, "created_at": datetime.now(timezone.utc)}
    )
    return {"status": "received"}

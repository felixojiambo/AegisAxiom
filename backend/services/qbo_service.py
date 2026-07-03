import os
import httpx
import logging
import base64
import uuid
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

QBO_CLIENT_ID = os.environ.get("QBO_CLIENT_ID", "")
QBO_CLIENT_SECRET = os.environ.get("QBO_CLIENT_SECRET", "")
QBO_REDIRECT_URI = os.environ.get("QBO_REDIRECT_URI", "")
QBO_AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
QBO_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
QBO_SANDBOX = "https://sandbox-quickbooks.api.intuit.com/v3/company"
QBO_PROD = "https://quickbooks.api.intuit.com/v3/company"

# SECURITY FIX H-6: Add environment variable to control sandbox vs production mode
QBO_USE_SANDBOX = os.environ.get("QBO_USE_SANDBOX", "true").lower() == "true"


class QBOService:
    def __init__(self, use_sandbox: bool = None):
        # SECURITY FIX H-6: Use environment variable if no parameter provided
        if use_sandbox is None:
            use_sandbox = QBO_USE_SANDBOX
        self.base = QBO_SANDBOX if use_sandbox else QBO_PROD

    def _redirect_uri(self, override: str = None) -> str:
        return override or QBO_REDIRECT_URI

    def get_auth_url(self, redirect_uri: str = None) -> str:
        if not QBO_CLIENT_ID or QBO_CLIENT_ID.startswith("placeholder"):
            return "#qbo-not-configured"
        redir = self._redirect_uri(redirect_uri)
        return (
            f"{QBO_AUTH_URL}?client_id={QBO_CLIENT_ID}"
            f"&redirect_uri={redir}&response_type=code"
            f"&scope=com.intuit.quickbooks.accounting&state=qbo_oauth"
        )

    def _auth_header(self) -> str:
        return "Basic " + base64.b64encode(f"{QBO_CLIENT_ID}:{QBO_CLIENT_SECRET}".encode()).decode()

    async def exchange_code(self, code: str, realm_id: str, redirect_uri: str = None) -> dict:
        redir = self._redirect_uri(redirect_uri)
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                QBO_TOKEN_URL,
                headers={"Authorization": self._auth_header(), "Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "authorization_code", "code": code, "redirect_uri": redir},
            )
            data = resp.json()
        return {
            "token_id": f"tok_qbo_{realm_id}",
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token"),
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 3600)),
            "realm_id": realm_id,
            "scope": data.get("scope"),
        }

    async def _get_token(self, db) -> tuple:
        doc = await db.oauth_tokens.find_one({"provider": "qbo"}, {"_id": 0})
        if not doc:
            raise Exception("QuickBooks Online not connected")
        exp = doc.get("expires_at")
        if isinstance(exp, str):
            exp = datetime.fromisoformat(exp)
        if exp and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp and exp < datetime.now(timezone.utc) + timedelta(minutes=5):
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    QBO_TOKEN_URL,
                    headers={"Authorization": self._auth_header(), "Content-Type": "application/x-www-form-urlencoded"},
                    data={"grant_type": "refresh_token", "refresh_token": doc["refresh_token"]},
                )
                new = resp.json()
            new_exp = datetime.now(timezone.utc) + timedelta(seconds=new.get("expires_in", 3600))
            await db.oauth_tokens.update_one(
                {"provider": "qbo"},
                {"$set": {"access_token": new["access_token"], "expires_at": new_exp}},
            )
            return new["access_token"], doc["realm_id"]
        return doc["access_token"], doc["realm_id"]

    async def create_invoice(self, db, qbo_customer_id: str, amount: float, description: str) -> dict:
        """Push invoice to QuickBooks Online"""
        try:
            token, realm_id = await self._get_token(db)
            body = {
                "CustomerRef": {"value": qbo_customer_id},
                "Line": [{"Amount": amount, "DetailType": "SalesItemLineDetail",
                          "SalesItemLineDetail": {"ItemRef": {"value": "1"}}}],
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.base}/{realm_id}/invoice",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"},
                    json=body,
                )
            return resp.json()
        except Exception as e:
            logger.error(f"QBO create invoice: {e}")
            raise

    async def sync_payment_status(self, db) -> int:
        """Pull paid invoice status from QBO and update local DB"""
        try:
            token, realm_id = await self._get_token(db)
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base}/{realm_id}/query",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    params={"query": "SELECT * FROM Invoice WHERE Balance = '0' MAXRESULTS 100"},
                )
            paid = resp.json().get("QueryResponse", {}).get("Invoice", [])
            for inv in paid:
                await db.invoices.update_one(
                    {"qbo_invoice_id": inv["Id"]},
                    {"$set": {"status": "paid", "paid_date": datetime.now(timezone.utc).isoformat()}},
                )
            return len(paid)
        except Exception as e:
            logger.error(f"QBO sync payment: {e}")
            return 0

    async def process_webhook(self, payload: dict, db):
        """Process QBO webhook notifications"""
        for notification in payload.get("eventNotifications", []):
            for entity in notification.get("dataChangeEvent", {}).get("entities", []):
                if entity.get("name") == "Invoice":
                    await db.invoices.update_one(
                        {"qbo_invoice_id": entity.get("id")},
                        {"$set": {"qbo_sync_required": True}},
                    )
        logger.info("Processed QBO webhook")

import os
import httpx
import logging
import uuid
import secrets
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

ZOHO_CLIENT_ID = os.environ.get("ZOHO_CLIENT_ID", "")
ZOHO_CLIENT_SECRET = os.environ.get("ZOHO_CLIENT_SECRET", "")
ZOHO_REDIRECT_URI = os.environ.get("ZOHO_REDIRECT_URI", "")
ZOHO_ACCOUNTS_URL = "https://accounts.zoho.com"
ZOHO_API_URL = "https://www.zohoapis.com/crm/v3"


class ZohoService:
    def _redirect_uri(self, override: str = None) -> str:
        """Use override if provided (derived from request), else fall back to env var."""
        return override or ZOHO_REDIRECT_URI

    def get_auth_url(self, redirect_uri: str = None, state: str = None) -> str:
        if not ZOHO_CLIENT_ID or ZOHO_CLIENT_ID.startswith("placeholder"):
            return "#zoho-not-configured"
        scopes = "ZohoCRM.modules.ALL,ZohoCRM.settings.ALL,ZohoCRM.users.ALL,ZohoCRM.coql.READ"
        redir = self._redirect_uri(redirect_uri)
        url = (
            f"{ZOHO_ACCOUNTS_URL}/oauth/v2/auth"
            f"?response_type=code&client_id={ZOHO_CLIENT_ID}"
            f"&scope={scopes}&redirect_uri={redir}&access_type=offline&prompt=consent"
        )
        if state:
            url += f"&state={state}"
        return url

    async def exchange_code(self, code: str, redirect_uri: str = None) -> dict:
        redir = self._redirect_uri(redirect_uri)
        logger.info(f"Zoho token exchange: redirect_uri={redir}")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{ZOHO_ACCOUNTS_URL}/oauth/v2/token",
                data={"grant_type": "authorization_code", "client_id": ZOHO_CLIENT_ID,
                      "client_secret": ZOHO_CLIENT_SECRET, "redirect_uri": redir, "code": code},
            )
            data = resp.json()
        if data.get("error"):
            error_msg = data.get("error", "unknown_error")
            logger.error(f"Zoho token exchange failed: {error_msg}")
            raise Exception(f"Zoho OAuth error: {error_msg}")
        if not data.get("access_token"):
            logger.error(f"Zoho token exchange returned no access_token: {data}")
            raise Exception("Zoho did not return an access token")
        return {
            "token_id": f"tok_zoho_{uuid.uuid4().hex[:8]}",
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token"),
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 3600)),
            "scope": data.get("scope"),
        }

    async def _get_token(self, db) -> str:
        doc = await db.oauth_tokens.find_one({"provider": "zoho"}, {"_id": 0})
        if not doc:
            raise Exception("Zoho not connected")
        exp = doc.get("expires_at")
        if isinstance(exp, str):
            exp = datetime.fromisoformat(exp)
        if exp and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp and exp < datetime.now(timezone.utc) + timedelta(minutes=5):
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{ZOHO_ACCOUNTS_URL}/oauth/v2/token",
                    data={"grant_type": "refresh_token", "client_id": ZOHO_CLIENT_ID,
                          "client_secret": ZOHO_CLIENT_SECRET, "refresh_token": doc["refresh_token"]},
                )
                new = resp.json()
            new_exp = datetime.now(timezone.utc) + timedelta(seconds=new.get("expires_in", 3600))
            await db.oauth_tokens.update_one(
                {"provider": "zoho"},
                {"$set": {"access_token": new["access_token"], "expires_at": new_exp}},
            )
            return new["access_token"]
        return doc["access_token"]

    async def _fetch_all_pages(self, db, module: str, fields: str, max_pages: int = 100) -> list:
        """Robust paginated fetch from any Zoho CRM module.
        
        Handles: token refresh between pages, retries on failure,
        HTTP error checking, and proper timeout configuration.
        Returns ALL records across ALL pages.
        """
        all_records = []
        page = 1
        max_retries = 3

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            while page <= max_pages:
                # Refresh token for each page to avoid expiry mid-pagination
                token = await self._get_token(db)

                for attempt in range(1, max_retries + 1):
                    try:
                        resp = await client.get(
                            f"{ZOHO_API_URL}/{module}",
                            headers={"Authorization": f"Zoho-oauthtoken {token}"},
                            params={"fields": fields, "per_page": 200, "page": page},
                        )

                        if resp.status_code == 204:
                            # 204 = No Content — no more records
                            logger.info(f"[{module}] Page {page}: 204 No Content — done.")
                            return all_records

                        if resp.status_code == 401:
                            # Token expired mid-session, force refresh
                            logger.warning(f"[{module}] Page {page}: 401 Unauthorized — refreshing token (attempt {attempt})")
                            # Invalidate cached token by resetting expiry
                            await db.oauth_tokens.update_one(
                                {"provider": "zoho"},
                                {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(hours=1)}}
                            )
                            token = await self._get_token(db)
                            continue  # retry this page

                        if resp.status_code != 200:
                            logger.warning(f"[{module}] Page {page}: HTTP {resp.status_code} (attempt {attempt})")
                            if attempt < max_retries:
                                import asyncio
                                await asyncio.sleep(1 * attempt)  # backoff
                                continue
                            else:
                                logger.error(f"[{module}] Page {page}: Failed after {max_retries} attempts, returning {len(all_records)} records so far")
                                return all_records

                        data = resp.json()
                        records = data.get("data", [])
                        if not records:
                            logger.info(f"[{module}] Page {page}: Empty data array — done. Total: {len(all_records)}")
                            return all_records

                        all_records.extend(records)
                        info = data.get("info", {})
                        logger.info(f"[{module}] Page {page}: Got {len(records)} records (running total: {len(all_records)}, more: {info.get('more_records', False)})")

                        if not info.get("more_records", False):
                            logger.info(f"[{module}] Pagination complete. Total: {len(all_records)} records across {page} pages.")
                            return all_records

                        page += 1
                        break  # success, move to next page

                    except httpx.TimeoutException:
                        logger.warning(f"[{module}] Page {page}: Timeout (attempt {attempt}/{max_retries})")
                        if attempt < max_retries:
                            import asyncio
                            await asyncio.sleep(2 * attempt)
                            continue
                        else:
                            logger.error(f"[{module}] Page {page}: Timeout after {max_retries} attempts, returning {len(all_records)} records so far")
                            return all_records
                    except Exception as e:
                        logger.warning(f"[{module}] Page {page}: Error {e} (attempt {attempt}/{max_retries})")
                        if attempt < max_retries:
                            import asyncio
                            await asyncio.sleep(1 * attempt)
                            continue
                        else:
                            logger.error(f"[{module}] Page {page}: Failed after {max_retries} attempts: {e}")
                            return all_records

        logger.warning(f"[{module}] Hit max page limit ({max_pages}). Total: {len(all_records)} records.")
        return all_records

    async def fetch_accounts(self, db) -> list:
        try:
            records = await self._fetch_all_pages(
                db, "Accounts",
                "Account_Name,Email,Phone,Website,Industry,Owner,Account_Manager",
                max_pages=200  # Up to 200 pages × 200 records = 40,000 accounts
            )
            logger.info(f"Fetched {len(records)} accounts from Zoho")
            return records
        except Exception as e:
            logger.error(f"Zoho fetch accounts: {e}")
            return []

    async def fetch_deals(self, db) -> list:
        try:
            records = await self._fetch_all_pages(
                db, "Deals",
                "Deal_Name,Stage,Amount,Account_Name,Close_Date,Owner,Referral_Partner,Description,Probability,Field_Services,Professional_Services1,Referral_Solution,Wholesale_Solution_MRR",
                max_pages=200
            )
            logger.info(f"Fetched {len(records)} deals from Zoho")
            return records
        except Exception as e:
            logger.error(f"Zoho fetch deals: {e}")
            return []

    async def sync_accounts_from_zoho(self, db) -> int:
        accounts = await self.fetch_accounts(db)
        # Build Zoho user map for resolving userlookup emails
        try:
            token = await self._get_token(db)
            users_map = await self._get_zoho_users_map(token)
        except Exception:
            users_map = {}

        synced = updated = 0
        for acc in accounts:
            zoho_id = acc["id"]

            # Resolve Account Owner (ownerlookup)
            owner = acc.get("Owner") or {}
            owner_name = owner.get("name", "") if isinstance(owner, dict) else ""
            owner_id = owner.get("id", "") if isinstance(owner, dict) else ""
            owner_email = users_map.get(owner_id, "") if owner_id else ""

            # Resolve Account Manager (userlookup)
            manager = acc.get("Account_Manager") or {}
            manager_name = manager.get("name", "") if isinstance(manager, dict) else ""
            manager_id = manager.get("id", "") if isinstance(manager, dict) else ""
            manager_email = users_map.get(manager_id, "") if manager_id else ""

            doc = {
                "name": acc.get("Account_Name", ""),
                "account_type": "company",
                "email": acc.get("Email"),
                "phone": acc.get("Phone"),
                "website": acc.get("Website"),
                "industry": acc.get("Industry"),
                "zoho_account_id": zoho_id,
                "account_owner_name": owner_name,
                "account_owner_email": owner_email,
                "account_owner_zoho_id": owner_id,
                "account_manager_name": manager_name,
                "account_manager_email": manager_email,
                "account_manager_zoho_id": manager_id,
            }
            existing = await db.accounts.find_one({"zoho_account_id": zoho_id}, {"_id": 0})
            if existing:
                await db.accounts.update_one(
                    {"zoho_account_id": zoho_id},
                    {"$set": {k: v for k, v in doc.items() if v is not None}},
                )
                updated += 1
            else:
                await db.accounts.insert_one({
                    "account_id": f"acc_{uuid.uuid4().hex[:12]}",
                    **doc,
                    "qbo_customer_id": None,
                    "source": "zoho",  # Mark as synced from Zoho
                    "created_at": datetime.now(timezone.utc),
                })
                synced += 1
        logger.info(f"Accounts sync: {synced} new, {updated} updated (total from Zoho: {len(accounts)})")
        return synced, updated

    async def sync_deals_from_zoho(self, db) -> int:
        deals = await self.fetch_deals(db)
        # Build Zoho user map for resolving owner emails
        try:
            token = await self._get_token(db)
            users_map = await self._get_zoho_users_map(token)
        except Exception:
            users_map = {}

        synced, updated = 0, 0
        accounts_created = 0
        closed_won_ids = []  # Track Closed Won deals for enrichment

        for deal in deals:
            zoho_deal_id = deal["id"]
            # Find matching account in local DB
            account_name_raw = deal.get("Account_Name", {})
            if isinstance(account_name_raw, dict):
                account_name = account_name_raw.get("name", "")
                account_zoho_id = account_name_raw.get("id", "")
            else:
                account_name = str(account_name_raw) if account_name_raw else ""
                account_zoho_id = ""

            # Look up account by zoho_account_id first, then by name
            account = None
            if account_zoho_id:
                account = await db.accounts.find_one({"zoho_account_id": account_zoho_id}, {"_id": 0, "account_id": 1})
            if not account and account_name:
                account = await db.accounts.find_one({"name": account_name}, {"_id": 0, "account_id": 1})

            # Auto-create account if it doesn't exist in AEGISAXIOM
            if not account and account_name:
                new_account_id = f"acc_{uuid.uuid4().hex[:12]}"
                await db.accounts.insert_one({
                    "account_id": new_account_id,
                    "name": account_name,
                    "account_type": "company",
                    "email": None,
                    "phone": None,
                    "website": None,
                    "industry": None,
                    "zoho_account_id": account_zoho_id or None,
                    "account_owner_name": "",
                    "account_owner_email": "",
                    "account_manager_name": "",
                    "account_manager_email": "",
                    "qbo_customer_id": None,
                    "source": "zoho",
                    "created_at": datetime.now(timezone.utc),
                })
                account_id = new_account_id
                accounts_created += 1
                logger.info(f"Auto-created account '{account_name}' during deal sync")
            else:
                account_id = account["account_id"] if account else None

            stage_map = {
                "Qualification": "Qualification",
                "Needs Analysis": "Qualification",
                "Value Proposition": "Proposal",
                "Id. Decision Makers": "Proposal",
                "Perception Analysis": "Proposal",
                "Proposal/Price Quote": "Proposal",
                "Negotiation/Review": "Negotiation",
                "Closed Won": "Closed Won",
                "Closed Lost": "Closed Lost",
            }
            stage = stage_map.get(deal.get("Stage", ""), deal.get("Stage", "Prospecting"))

            # Resolve Deal Owner
            owner = deal.get("Owner") or {}
            owner_name = owner.get("name", "") if isinstance(owner, dict) else ""
            owner_id = owner.get("id", "") if isinstance(owner, dict) else ""
            owner_email = users_map.get(owner_id, "") if owner_id else ""

            # Resolve Agents of Record (Referral_Partner lookup)
            agent_ref = deal.get("Referral_Partner") or {}
            agent_of_record_name = agent_ref.get("name", "") if isinstance(agent_ref, dict) else ""
            agent_of_record_id = agent_ref.get("id", "") if isinstance(agent_ref, dict) else ""

            existing = await db.deals.find_one({"zoho_deal_id": zoho_deal_id}, {"_id": 0})
            
            # Extract deal type checkboxes from Zoho
            deal_types = []
            if deal.get("Field_Services"):
                deal_types.append("field_services")
            if deal.get("Professional_Services1"):
                deal_types.append("professional_services")
            if deal.get("Referral_Solution"):
                deal_types.append("referral_mrr")
            if deal.get("Wholesale_Solution_MRR"):
                deal_types.append("wholesale_mrr")
            
            update_doc = {
                "stage": stage,
                "amount": deal.get("Amount") or 0,
                "account_name": account_name,
                "account_id": account_id or "",
                "deal_owner_name": owner_name,
                "deal_owner_email": owner_email,
                "deal_owner_zoho_id": owner_id,
                "agent_of_record_name": agent_of_record_name,
                "agent_of_record_id": agent_of_record_id,
                "deal_types": deal_types,  # Line of Business: checkbox fields from Zoho
                "description": deal.get("Description") or "",
            }

            if existing:
                await db.deals.update_one(
                    {"zoho_deal_id": zoho_deal_id},
                    {"$set": update_doc},
                )
                updated += 1
                if stage == "Closed Won":
                    closed_won_ids.append((existing.get("deal_id", ""), zoho_deal_id))
            else:
                deal_id = f"deal_{uuid.uuid4().hex[:12]}"
                await db.deals.insert_one({
                    "deal_id": deal_id,
                    "name": deal.get("Deal_Name", "Untitled Deal"),
                    **update_doc,
                    "close_date": str(deal.get("Close_Date", "")) if deal.get("Close_Date") else None,
                    "deal_owner_id": owner_id or "zoho_sync",
                    "assigned_sales_rep_id": None,
                    "assigned_agent_id": None,
                    "probability": int(deal.get("Probability", 0) or 0),
                    "zoho_deal_id": zoho_deal_id,
                    "source": "zoho",
                    "created_at": datetime.now(timezone.utc),
                })
                synced += 1
                if stage == "Closed Won":
                    closed_won_ids.append((deal_id, zoho_deal_id))

        # Enrich Closed Won deals with Notes, Attachments, PandaDoc links
        if closed_won_ids:
            logger.info(f"Enriching {len(closed_won_ids)} Closed Won deals with notes/attachments/PandaDoc...")
            await self._enrich_closed_won_deals(db, closed_won_ids)

        if accounts_created:
            logger.info(f"Deal sync auto-created {accounts_created} missing accounts")
        return synced, updated, accounts_created

    async def _enrich_closed_won_deals(self, db, deal_pairs: list):
        """Fetch Notes, Attachments metadata, and PandaDoc links for Closed Won deals.
        
        deal_pairs: list of (local_deal_id, zoho_deal_id) tuples
        """
        token = await self._get_token(db)
        enriched = 0

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            for local_deal_id, zoho_deal_id in deal_pairs:
                try:
                    enrichment = {}

                    # 1. Fetch Notes (related list) — requires 'fields' param in v3
                    try:
                        resp = await client.get(
                            f"{ZOHO_API_URL}/Deals/{zoho_deal_id}/Notes",
                            headers={"Authorization": f"Zoho-oauthtoken {token}"},
                            params={"fields": "Note_Title,Note_Content,Created_By,Created_Time,Modified_Time", "per_page": 200},
                        )
                        if resp.status_code == 200:
                            notes_data = resp.json().get("data", [])
                            enrichment["zoho_notes"] = [
                                {
                                    "note_id": n.get("id", ""),
                                    "title": n.get("Note_Title", ""),
                                    "content": n.get("Note_Content", ""),
                                    "created_by": n.get("Created_By", {}).get("name", "") if isinstance(n.get("Created_By"), dict) else "",
                                    "created_time": str(n.get("Created_Time", "")),
                                    "modified_time": str(n.get("Modified_Time", "")),
                                }
                                for n in notes_data
                            ]
                        elif resp.status_code == 204:
                            enrichment["zoho_notes"] = []
                    except Exception as e:
                        logger.warning(f"Notes fetch error for deal {zoho_deal_id}: {e}")

                    # 2. Fetch Attachments metadata (related list) — requires 'fields' param in v3
                    try:
                        resp = await client.get(
                            f"{ZOHO_API_URL}/Deals/{zoho_deal_id}/Attachments",
                            headers={"Authorization": f"Zoho-oauthtoken {token}"},
                            params={"fields": "File_Name,Size,Created_By,Created_Time,Modified_Time", "per_page": 200},
                        )
                        if resp.status_code == 200:
                            attach_data = resp.json().get("data", [])
                            enrichment["zoho_attachments"] = [
                                {
                                    "attachment_id": a.get("id", ""),
                                    "file_name": a.get("File_Name", a.get("$file_name", "")),
                                    "size": a.get("Size", a.get("$size", 0)),
                                    "created_by": a.get("Created_By", {}).get("name", "") if isinstance(a.get("Created_By"), dict) else "",
                                    "created_time": str(a.get("Created_Time", "")),
                                    "zoho_download_url": f"{ZOHO_API_URL}/Deals/{zoho_deal_id}/Attachments/{a.get('id', '')}",
                                }
                                for a in attach_data
                            ]
                        elif resp.status_code == 204:
                            enrichment["zoho_attachments"] = []
                    except Exception as e:
                        logger.warning(f"Attachments fetch error for deal {zoho_deal_id}: {e}")

                    # 3. Fetch PandaDoc Documents link (third-party related list)
                    # Note: May require additional OAuth scopes for PandaDoc extension
                    try:
                        resp = await client.get(
                            f"{ZOHO_API_URL}/Deals/{zoho_deal_id}/PandaDoc_Documents",
                            headers={"Authorization": f"Zoho-oauthtoken {token}"},
                            params={"per_page": 50},
                        )
                        if resp.status_code == 200:
                            panda_data = resp.json().get("data", [])
                            enrichment["pandadoc_documents"] = [
                                {
                                    "doc_id": p.get("id", ""),
                                    "name": p.get("Name") or p.get("name") or p.get("Document_Name", ""),
                                    "link": p.get("Link") or p.get("link") or p.get("Document_Link") or p.get("$url") or "",
                                    "status": p.get("Status") or p.get("status") or "",
                                    "raw_fields": {k: v for k, v in p.items() if k not in ("id", "$approval", "$sharing_permission")},
                                }
                                for p in panda_data
                            ]
                        elif resp.status_code == 204:
                            enrichment["pandadoc_documents"] = []
                        elif resp.status_code == 401:
                            # OAuth scope mismatch — PandaDoc extension needs extra scope
                            # Only log once, not for every deal
                            if enriched == 0:
                                logger.warning(
                                    "PandaDoc related list requires additional OAuth scope. "
                                    "Re-authorize Zoho with scope: ZohoCRM.modules.ALL,ZohoCRM.settings.ALL,ZohoCRM.users.READ"
                                )
                    except Exception as e:
                        logger.warning(f"PandaDoc fetch error for deal {zoho_deal_id}: {e}")

                    # Update the deal with enrichment data
                    if enrichment:
                        enrichment["enriched_at"] = datetime.now(timezone.utc)
                        await db.deals.update_one(
                            {"deal_id": local_deal_id},
                            {"$set": enrichment},
                        )
                        enriched += 1

                    # Refresh token periodically (every 50 deals)
                    if enriched % 50 == 0 and enriched > 0:
                        token = await self._get_token(db)

                except Exception as e:
                    logger.warning(f"Enrichment error for deal {zoho_deal_id}: {e}")
                    continue

        logger.info(f"Enriched {enriched}/{len(deal_pairs)} Closed Won deals with notes/attachments/PandaDoc")

    async def fetch_commission_data(self, db, page_limit: int = 200) -> list:
        """Fetch all records from the Commissons_Data custom module in Zoho.

        NOTE: "Commissons_Data" is the actual Zoho module name (typo is on the Zoho side).
        Do NOT rename — it must match the Zoho API exactly.
        """
        try:
            fields = (
                "Agents_of_Record,User,Deal_Name,"
                "Agent_Rep_Commission,Sales_Rep1,Dir_of_sales_Commission,"
                "Agen_Commission_Status,Sales_Rep_Commission_Status,DIR_Commission_Paid,"
                "Invoice_No,Agent_Commission_Payment_Date,Customer_Name,Amount,Stage"
            )
            records = await self._fetch_all_pages(
                db, "Commissons_Data", fields, max_pages=page_limit
            )
            logger.info(f"Fetched {len(records)} commission records from Zoho")
            return records
        except Exception as e:
            logger.error(f"Zoho fetch commission data: {e}")
            return []

    async def _get_zoho_users_map(self, token: str) -> dict:
        """Returns {zoho_user_id: email} mapping from Zoho CRM users.
        Handles pagination for orgs with many users."""
        try:
            all_users = []
            page = 1
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                while True:
                    resp = await client.get(
                        "https://www.zohoapis.com/crm/v3/users",
                        headers={"Authorization": f"Zoho-oauthtoken {token}"},
                        params={"type": "AllUsers", "per_page": 200, "page": page},
                    )
                    if resp.status_code != 200:
                        logger.warning(f"Zoho users page {page}: HTTP {resp.status_code}")
                        break
                    data = resp.json()
                    users = data.get("users", [])
                    all_users.extend(users)
                    if not data.get("info", {}).get("more_records", False):
                        break
                    page += 1
            logger.info(f"Fetched {len(all_users)} Zoho users across {page} pages")
            return {u["id"]: (u.get("email") or "").lower().strip() for u in all_users}
        except Exception as e:
            logger.error(f"Zoho fetch users: {e}")
            return {}

    async def sync_commission_data(self, db) -> int:
        """Sync Commissons_Data module → local zoho_commissions collection."""
        records = await self.fetch_commission_data(db)
        if not records:
            return 0

        # Build Zoho user ID → email map to resolve sales rep emails
        token = await self._get_token(db)
        users_map = await self._get_zoho_users_map(token)

        synced = updated = 0
        for rec in records:
            zoho_id = rec.get("id")
            if not zoho_id:
                continue

            def _lookup(field, sub="id"):
                val = rec.get(field)
                if isinstance(val, dict):
                    return val.get(sub)
                return val

            agent_of_records_id   = _lookup("Agents_of_Record", "id")
            agent_of_records_name = _lookup("Agents_of_Record", "name")

            user_id_zoho = _lookup("User", "id")
            user_name    = _lookup("User", "name")
            # Resolve email from users_map since userlookup doesn't return email
            user_email   = users_map.get(user_id_zoho, "").lower().strip() if user_id_zoho else ""

            deal_id   = _lookup("Deal_Name", "id")
            deal_name = _lookup("Deal_Name", "name")

            doc = {
                "zoho_id":                     zoho_id,
                "deal_id":                     deal_id,
                "deal_name":                   deal_name,
                "agent_of_records_id":         agent_of_records_id,
                "agent_of_records_name":       agent_of_records_name,
                "sales_rep_email":             user_email,
                "sales_rep_zoho_id":           user_id_zoho,
                "sales_rep_name":              user_name,
                "agent_commission":            float(rec.get("Agent_Rep_Commission") or 0),
                "sales_rep_commission":        float(rec.get("Sales_Rep1") or 0),
                "dir_commission":              float(rec.get("Dir_of_sales_Commission") or 0),
                "agent_commission_status":     rec.get("Agen_Commission_Status"),
                "sales_rep_commission_status": rec.get("Sales_Rep_Commission_Status"),
                "dir_commission_status":       rec.get("DIR_Commission_Paid"),
                "invoice_no":                  str(rec.get("Invoice_No") or ""),
                "payment_date":                str(rec.get("Agent_Commission_Payment_Date") or ""),
                "customer_name":               rec.get("Customer_Name"),
                "amount":                      float(rec.get("Amount") or 0),
                "stage":                       rec.get("Stage"),
                "synced_at":                   datetime.now(timezone.utc),
            }

            existing = await db.zoho_commissions.find_one({"zoho_id": zoho_id}, {"_id": 0})
            if existing:
                await db.zoho_commissions.update_one({"zoho_id": zoho_id}, {"$set": doc})
                updated += 1
            else:
                await db.zoho_commissions.insert_one(doc)
                synced += 1

        logger.info(f"Commission sync: {synced} new, {updated} updated")
        return synced + updated

    async def fetch_deals_enriched(self, db, page_limit: int = 200) -> list:
        """Fetch Deals with agent/sales rep fields for the Agent Dashboard."""
        try:
            token = await self._get_token(db)
            # Build owner ID -> email map
            users_map = await self._get_zoho_users_map(token)

            fields = "Deal_Name,Stage,Amount,Account_Name,Close_Date,Owner,Referral_Partner"
            all_records = await self._fetch_all_pages(
                db, "Deals", fields, max_pages=page_limit
            )
            logger.info(f"Fetched {len(all_records)} enriched deals from Zoho")
            return all_records, users_map
        except Exception as e:
            logger.error(f"Zoho fetch enriched deals: {e}")
            return [], {}

    async def sync_deals_enriched(self, db) -> int:
        """Sync Deals with enriched fields → local zoho_deals collection."""
        records, users_map = await self.fetch_deals_enriched(db)
        synced = updated = 0
        for rec in records:
            zoho_id = rec.get("id")
            if not zoho_id:
                continue

            def _lookup(field, sub="id"):
                val = rec.get(field)
                if isinstance(val, dict):
                    return val.get(sub)
                return val

            account_name = rec.get("Account_Name")
            if isinstance(account_name, dict):
                account_name = account_name.get("name", "")

            # Owner → sales rep
            owner_zoho_id = _lookup("Owner", "id")
            owner_name    = _lookup("Owner", "name")
            owner_email   = users_map.get(owner_zoho_id, "").lower().strip() if owner_zoho_id else ""

            # Referral_Partner = "Agents of Record" lookup → account
            agent_id   = _lookup("Referral_Partner", "id")
            agent_name = _lookup("Referral_Partner", "name")

            doc = {
                "zoho_id":              zoho_id,
                "deal_name":            rec.get("Deal_Name", ""),
                "stage":                rec.get("Stage", ""),
                "amount":               float(rec.get("Amount") or 0),
                "account_name":         account_name,
                "close_date":           str(rec.get("Close_Date") or ""),
                "deal_owner_email":     owner_email,
                "deal_owner_name":      owner_name,
                "deal_owner_zoho_id":   owner_zoho_id,
                "agent_of_record_id":   agent_id,
                "agent_of_record_name": agent_name,
                "synced_at":            datetime.now(timezone.utc),
            }

            existing = await db.zoho_deals.find_one({"zoho_id": zoho_id}, {"_id": 0})
            if existing:
                await db.zoho_deals.update_one({"zoho_id": zoho_id}, {"$set": doc})
                updated += 1
            else:
                await db.zoho_deals.insert_one(doc)
                synced += 1

        logger.info(f"Deals enriched sync: {synced} new, {updated} updated")
        return synced + updated

    async def process_crm_webhook(self, payload: dict, db):
        operation = payload.get("operation")
        module = payload.get("module")
        if module == "Deals":
            for deal_id in payload.get("ids", []):
                await db.deals.update_one(
                    {"zoho_deal_id": deal_id},
                    {"$set": {"zoho_synced_at": datetime.now(timezone.utc)}},
                )
        logger.info(f"Processed Zoho webhook: {operation} on {module}")

    # ──────────────────────────────────────────────
    #  WRITE-BACK: Push changes to Zoho CRM
    # ──────────────────────────────────────────────

    # Maps local field names → Zoho API field names for Deals module
    _DEAL_FIELD_MAP = {
        "deal_name": "Deal_Name",
        "stage": "Stage",
        "amount": "Amount",
        "close_date": "Close_Date",
    }

    # Maps local field names → Zoho API field names for Commissons_Data module
    _COMMISSION_FIELD_MAP = {
        "sales_rep_commission_status": "Sales_Rep_Commission_Status",
        "agent_commission_status": "Agen_Commission_Status",
        "dir_commission_status": "DIR_Commission_Paid",
        "sales_rep_commission": "Sales_Rep1",
        "agent_commission": "Agent_Rep_Commission",
        "dir_commission": "Dir_of_sales_Commission",
        "payment_date": "Agent_Commission_Payment_Date",
        "invoice_no": "Invoice_No",
    }

    async def fetch_single_record(self, db, module: str, zoho_id: str) -> dict | None:
        """Fetch a single record from Zoho CRM by module and ID."""
        try:
            token = await self._get_token(db)
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{ZOHO_API_URL}/{module}/{zoho_id}",
                    headers={"Authorization": f"Zoho-oauthtoken {token}"},
                )
            data = resp.json()
            records = data.get("data", [])
            return records[0] if records else None
        except Exception as e:
            logger.error(f"Zoho fetch single {module}/{zoho_id}: {e}")
            return None

    async def update_record_in_zoho(self, db, module: str, zoho_id: str, field_map: dict, updates: dict) -> dict:
        """Push field updates to a Zoho CRM record.

        Returns {"success": True} or {"error": "..."}.
        """
        zoho_payload = {}
        for local_field, value in updates.items():
            zoho_field = field_map.get(local_field)
            if zoho_field:
                zoho_payload[zoho_field] = value

        if not zoho_payload:
            return {"success": True, "message": "No Zoho-mapped fields to update"}

        try:
            token = await self._get_token(db)
            body = {"data": [{**zoho_payload, "id": zoho_id}]}
            async with httpx.AsyncClient() as client:
                resp = await client.put(
                    f"{ZOHO_API_URL}/{module}",
                    headers={
                        "Authorization": f"Zoho-oauthtoken {token}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
            result = resp.json()
            records = result.get("data", [])
            if records and records[0].get("status") == "success":
                logger.info(f"Zoho update success: {module}/{zoho_id}")
                return {"success": True}
            error_msg = records[0].get("message", "Unknown error") if records else str(result)
            logger.error(f"Zoho update failed: {module}/{zoho_id} → {error_msg}")
            return {"error": error_msg}
        except Exception as e:
            logger.error(f"Zoho update exception: {module}/{zoho_id} → {e}")
            return {"error": str(e)}

    async def update_deal_in_zoho(self, db, zoho_id: str, updates: dict) -> dict:
        return await self.update_record_in_zoho(db, "Deals", zoho_id, self._DEAL_FIELD_MAP, updates)

    async def update_commission_in_zoho(self, db, zoho_id: str, updates: dict) -> dict:
        return await self.update_record_in_zoho(db, "Commissons_Data", zoho_id, self._COMMISSION_FIELD_MAP, updates)

    async def detect_conflict(self, db, module: str, zoho_id: str, local_synced_at: datetime) -> dict | None:
        """Check if the Zoho record was modified after our last sync.

        Returns the remote record dict if there's a conflict, or None if clean.
        """
        remote = await self.fetch_single_record(db, module, zoho_id)
        if not remote:
            return None
        remote_modified = remote.get("Modified_Time")
        if not remote_modified:
            return None
        try:
            if isinstance(remote_modified, str):
                remote_dt = datetime.fromisoformat(remote_modified.replace("Z", "+00:00"))
            else:
                remote_dt = remote_modified
            if remote_dt.tzinfo is None:
                remote_dt = remote_dt.replace(tzinfo=timezone.utc)
            if local_synced_at.tzinfo is None:
                local_synced_at = local_synced_at.replace(tzinfo=timezone.utc)
            if remote_dt > local_synced_at + timedelta(seconds=5):
                return remote
        except Exception as e:
            logger.warning(f"Conflict check parse error: {e}")
        return None

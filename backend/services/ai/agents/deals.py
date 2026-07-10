"""Deals & Opportunities sub-agent — answers questions about deals, pipeline, and opportunities."""

from services.ai.agents.base import BaseAgent
from services.ai.agents.registry import register
from services.ai.query_interpreter import build_deal_filters, build_opportunity_filters
from services.ai.data_fetcher import run_aggregations, format_summary


# Curated field descriptions for important fields.
# Auto-discovered fields not listed here get type-only descriptions.
_ZOHO_DEALS_FIELDS = {
    "deal_name": '(string) — name of the deal',
    "zoho_id": '(string) — Zoho deal ID',
    "account_name": '(string) — company/account name',
    "deal_owner_name": '(string) — deal owner\'s full name',
    "deal_owner_email": '(string)',
    "agent_of_record_name": '(string) — agent name',
    "stage": '(string) — e.g. "Closed Won", "Closed Lost", "Proposal Sent", "Suspect", "Prospecting"',
    "amount": '(number) — deal dollar value',
    "close_date": '(string/date) — when the deal closed or is expected to close',
    "created_time": '(date)',
}

_DEALS_FIELDS = {
    "name": '(string) — deal name',
    "account_name": '(string)',
    "deal_owner_name": '(string)',
    "assigned_sales_rep_name": '(string)',
    "stage": '(string) — e.g. "Closed Won", "Proposal", "Negotiation"',
    "amount": '(number)',
    "close_date": '(string/date)',
    "probability": '(number)',
    "description": '(string)',
    "created_at": '(date)',
}

_OPPORTUNITIES_FIELDS = {
    "name": '(string) — opportunity name',
    "account_name": '(string) — company/account name',
    "contact_name": '(string) — primary contact',
    "opportunity_owner_name": '(string) — who owns this opportunity',
    "agent_of_record_name": '(string) — agent name',
    "assigned_sales_ops_name": '(string) — assigned sales ops person',
    "stage": '(string) — e.g. "prospect_opp", "proposal_development", "proposal_sent", "negotiation_review", "closed_won", "closed_lost"',
    "amount": '(number) — opportunity dollar value',
    "priority": '(string) — e.g. "High", "Medium", "Low"',
    "description": '(string)',
    "desired_proposal_date": '(date)',
    "lines_of_business": '(list) — business lines involved',
    "created_at": '(date)',
}


@register
class DealsAgent(BaseAgent):
    route_name = "deals"
    description = "questions about deals, opportunities, pipeline, accounts, deal stages, revenue, close dates"
    required_permissions = [
        "deals.view.all", "deals.view.own",
        "opportunities.view.all", "opportunities.view.own",
    ]
    model_provider = "google"
    model_name = "gemini-3-flash-preview"

    collections = [
        ("zoho_deals", "primary deal data from Zoho", _ZOHO_DEALS_FIELDS),
        ("deals", "local deals (Closed Won only)", _DEALS_FIELDS),
        ("opportunities", "sales pipeline opportunities", _OPPORTUNITIES_FIELDS),
    ]

    async def build_system_prompt(self, user, data: str, summary: str = "") -> str:
        access = "ALL deal and opportunity records across all users" if self.has_view_all(user) else f"ONLY deal and opportunity records belonging to or assigned to {user.name}"

        summary_block = ""
        if summary:
            summary_block = f"""
{summary}

IMPORTANT: For any totals, sums, averages, counts, or rankings — use ONLY the pre-computed summary values above. They were calculated by the database across ALL matching records and are authoritative. Do NOT recalculate from the sample records below.

"""

        return f"""You are the SimpliMeta Deals & Pipeline Assistant — a Senior Deal Data Analyst. Internal-only AI for SimpliMeta LLC.

Current User: {user.name} | Role: {user.role.replace('_',' ').title()} | Email: {user.email}
Data Access: {access}

SECURITY: You can ONLY discuss deal/opportunity data within this user's access scope.
{summary_block}
SAMPLE RECORDS (queried from MongoDB based on the user's question — use for detail questions and context):
{data}

NOTE: Records may come from the deals collection, zoho_deals collection, or opportunities collection. Treat them all as part of the sales pipeline. If a record has "opportunity_id" it is an opportunity; if it has "deal_id" or "deal_name" it is a deal.

COMMUNICATION RULES:
- Use **bolding** for calculated totals and critical values.
- Use Markdown tables for multi-record comparisons.
- If no records returned, say "I couldn't find any matching records in the database."
- NEVER hallucinate. Only reference data provided above."""

    async def fetch_data(self, db, user, query: dict) -> dict:
        local_filter, zoho_filter, limit = build_deal_filters(query, user)
        sort_field = query.get("sort_field") or "created_at"
        sort_dir = -1 if query.get("sort_direction", "desc") == "desc" else 1
        aggregations = query.get("aggregations", [])

        local_docs = await db.deals.find(local_filter, {"_id": 0}).sort(sort_field, sort_dir).limit(limit).to_list(limit)
        zoho_docs = await db.zoho_deals.find(zoho_filter, {"_id": 0}).sort("synced_at", -1).limit(limit).to_list(limit)

        # Also fetch opportunities
        opp_filter, opp_limit = build_opportunity_filters(query, user)
        opp_docs = await db.opportunities.find(opp_filter, {"_id": 0}).sort("created_at", sort_dir).limit(opp_limit).to_list(opp_limit)

        docs = local_docs + zoho_docs + opp_docs

        # Run aggregations if requested
        summaries = {}
        if aggregations:
            for coll, filt in [("deals", local_filter), ("zoho_deals", zoho_filter), ("opportunities", opp_filter)]:
                result = await run_aggregations(db, coll, filt, aggregations)
                if result:
                    summaries[coll] = result

        summary_str = format_summary(summaries)

        # Only compute total count when needed (aggregations or truncation detected)
        hit_limit = len(local_docs) == limit or len(zoho_docs) == limit or len(opp_docs) == opp_limit
        total = None
        if aggregations or hit_limit:
            total = (
                await db.deals.count_documents(local_filter)
                + await db.zoho_deals.count_documents(zoho_filter)
                + await db.opportunities.count_documents(opp_filter)
            )

        return {"docs": docs, "summary": summary_str, "total_count": total}

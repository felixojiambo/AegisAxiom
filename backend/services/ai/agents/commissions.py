"""Commissions sub-agent — answers questions about commissions and payments."""

from services.ai.agents.base import BaseAgent
from services.ai.agents.registry import register
from services.ai.query_interpreter import build_commission_filters
from services.ai.data_fetcher import run_aggregations, format_summary


_ZOHO_COMMISSIONS_FIELDS = {
    "deal_name": '(string) — name of the deal',
    "deal_id": '(string) — Zoho deal ID',
    "customer_name": '(string) — customer/company on the deal',
    "sales_rep_name": '(string) — the sales representative\'s full name',
    "sales_rep_email": '(string) — sales rep email',
    "agent_of_records_name": '(string) — the external agent\'s name',
    "sales_rep_commission": '(number) — dollar amount of the sales rep\'s commission',
    "sales_rep_commission_status": '(string) — one of: "Paid", "Owed", "Potential"',
    "agent_commission": '(number) — dollar amount of the agent\'s commission',
    "agent_commission_status": '(string) — one of: "Paid", "Owed", "Potential"',
    "dir_commission": '(number) — director commission amount',
    "payment_date": '(string/date) — when the commission was paid (ISO date)',
    "invoice_no": '(string) — invoice number',
}

_COMMISSIONS_FIELDS = {
    "deal_name": '(string)',
    "user_name": '(string)',
    "amount_owed": '(number)',
    "amount_paid": '(number)',
    "potential_amount": '(number)',
    "status": '(string) — one of: "pending", "approved", "paid", "rejected"',
    "commission_rate": '(number)',
    "created_at": '(date)',
}


@register
class CommissionsAgent(BaseAgent):
    route_name = "commissions"
    description = "questions about commissions, payments, owed amounts, paid amounts, commission status"
    required_permissions = ["commissions.view.all", "commissions.view.own"]
    model_provider = "google"
    model_name = "gemini-3-flash-preview"

    collections = [
        ("zoho_commissions", "primary commission data from Zoho", _ZOHO_COMMISSIONS_FIELDS),
        ("commissions", "local/manual commissions", _COMMISSIONS_FIELDS),
    ]

    async def build_system_prompt(self, user, data: str, summary: str = "") -> str:
        access = "ALL commission records across all users" if self.has_view_all(user) else f"ONLY commission records belonging to {user.name}"

        summary_block = ""
        if summary:
            summary_block = f"""
{summary}

IMPORTANT: For any totals, sums, averages, counts, or rankings — use ONLY the pre-computed summary values above. They were calculated by the database across ALL matching records and are authoritative. Do NOT recalculate from the sample records below.

"""

        return f"""You are the SimpliMeta Commissions Assistant — a Senior Commissions Analyst. Internal-only tool for SimpliMeta LLC.

Current User: {user.name} | Role: {user.role.replace('_',' ').title()} | Email: {user.email}
Data Access: {access}

SECURITY: You can ONLY discuss commission data within this user's access scope.
{summary_block}
SAMPLE RECORDS (queried from MongoDB based on the user's question — use for detail questions and context):
{data}

PROTOCOLS:
1. If asked about a person, check ALL name fields (sales_rep_name, agent_of_records_name, customer_name).
2. Missing Data: If no records were returned, say "I couldn't find any matching records in the database."

COMMUNICATION RULES:
- Executive summary first (bottom-line numbers).
- Use Markdown tables for multi-record comparisons.
- Keep it scannable — ONE data point per message.
- NEVER hallucinate data not present above."""

    async def fetch_data(self, db, user, query: dict) -> dict:
        local_filter, zoho_filter, limit = build_commission_filters(query, user)
        sort_field = query.get("sort_field") or "created_at"
        sort_dir = -1 if query.get("sort_direction", "desc") == "desc" else 1
        aggregations = query.get("aggregations", [])

        local_docs = await db.commissions.find(local_filter, {"_id": 0}).sort(sort_field, sort_dir).limit(limit).to_list(limit)
        zoho_docs = await db.zoho_commissions.find(zoho_filter, {"_id": 0}).sort("synced_at", -1).limit(limit).to_list(limit)

        docs = local_docs + zoho_docs

        # Run aggregations if requested
        summaries = {}
        if aggregations:
            for coll, filt in [("commissions", local_filter), ("zoho_commissions", zoho_filter)]:
                result = await run_aggregations(db, coll, filt, aggregations)
                if result:
                    summaries[coll] = result

        summary_str = format_summary(summaries)

        # Only compute total count when needed (aggregations or truncation detected)
        hit_limit = len(local_docs) == limit or len(zoho_docs) == limit
        total = None
        if aggregations or hit_limit:
            total = (
                await db.commissions.count_documents(local_filter)
                + await db.zoho_commissions.count_documents(zoho_filter)
            )

        return {"docs": docs, "summary": summary_str, "total_count": total}

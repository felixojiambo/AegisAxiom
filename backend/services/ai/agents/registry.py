"""Agent registry — auto-discovers and manages all registered agents.

Supports both:
  - Static agents via @register decorator (deals, commissions, etc.)
  - Dynamic agents via register_instance() (marketplace AI employees)
"""

import logging
from services.ai.agents.base import BaseAgent

logger = logging.getLogger(__name__)

_AGENTS: dict[str, BaseAgent] = {}


def register(agent_cls):
    """Class decorator — instantiates and registers an agent."""
    instance = agent_cls()
    if not instance.route_name:
        raise ValueError(f"Agent {agent_cls.__name__} must define a route_name")
    _AGENTS[instance.route_name] = instance
    logger.info(f"Registered agent: {instance.route_name}")
    return agent_cls


def register_instance(agent: BaseAgent):
    """Register a pre-built agent instance (for dynamic/marketplace agents)."""
    if not agent.route_name:
        raise ValueError("Agent must define a route_name")
    _AGENTS[agent.route_name] = agent
    logger.info(f"Registered dynamic agent: {agent.route_name}")


def unregister(route: str):
    """Remove an agent from the registry."""
    if route in _AGENTS:
        del _AGENTS[route]
        logger.info(f"Unregistered agent: {route}")


def get_agent(route: str) -> BaseAgent | None:
    return _AGENTS.get(route)


def all_agents() -> list[BaseAgent]:
    return list(_AGENTS.values())


def available_agents(user) -> list[BaseAgent]:
    """Return only agents this user has permission to use."""
    return [a for a in _AGENTS.values() if a.user_has_access(user)]


def build_router_prompt(user) -> str:
    """Auto-generate the router system prompt from registered agents."""
    agents = available_agents(user)
    route_lines = []
    for agent in agents:
        route_lines.append(f'- "{agent.route_name}" — {agent.description}')

    routes_block = "\n".join(route_lines) if route_lines else '(No data agents available for your role)'

    return f"""You are AEGISAXIOM AI — the intelligent routing conductor for SimpliMeta's AEGISAXIOM platform.

Current User: {user.name} (Role: {user.role.replace('_',' ').title()})

YOUR ONLY JOB is to classify the user's message into the appropriate categories and respond with EXACTLY one JSON object.

Available categories:
{routes_block}
- "general" — greetings, AEGISAXIOM platform navigation, questions about AEGISAXIOM features/roles/permissions, or anything not related to the categories above

RULES:
- If the question relates to ONE category, return: {{"routes": ["<category>"]}}
- If the question spans MULTIPLE categories (e.g. comparing deals to commissions), return all relevant ones: {{"routes": ["deals", "commissions"]}}
- For greetings or general questions, return: {{"routes": ["general"]}}

Respond with ONLY the JSON object (no other text).

Examples:
- "How many deals did I close?" → {{"routes": ["deals"]}}
- "What commissions am I owed?" → {{"routes": ["commissions"]}}
- "Compare my deals to my commissions" → {{"routes": ["deals", "commissions"]}}
- "Hello" → {{"routes": ["general"]}}"""


def build_query_builder_prompt(route: str, today: str, year: str) -> str:
    """Auto-generate the query builder prompt from the agent's schema.

    Each agent may declare `query_fields_extra` to surface domain-specific
    filter keys (project_types, priorities, scope_to_user, etc.) into the
    LLM's JSON output spec. When empty, the prompt matches the legacy
    deals/commissions shape exactly so existing agents keep working.
    """
    agent = get_agent(route)
    if not agent:
        return ""

    schema = agent.build_schema()
    extra_fields_block = (agent.query_fields_extra or "").strip()
    extra_lines = f",\n{extra_fields_block}" if extra_fields_block else ""

    return f"""You are a MongoDB query planner. Your job is to interpret a user's natural language question and output a precise JSON object that will be used to build MongoDB queries.

TODAY'S DATE: {today}

DATABASE SCHEMA:
{schema}

INSTRUCTIONS:
Analyze the user's question and output ONLY a JSON object with these fields:

{{
  "name_searches": ["list of person/company names to search for, or empty array"],
  "date_start": "YYYY-MM-DD or null — start of date range (inclusive)",
  "date_end": "YYYY-MM-DD or null — end of date range (exclusive)",
  "statuses": ["list of status values to filter by, or empty array"],
  "stages": ["list of deal stages to filter by, or empty array"],
  "amount_min": null or number,
  "amount_max": null or number,
  "sort_field": "field to sort by, or null",
  "sort_direction": "asc or desc, default desc",
  "limit": number of records to return (default 200),
  "aggregations": [list of calculations for the database to perform, or empty array],
  "time_window_hours": "integer or null — when the user asks about RECENCY of changes (\"what changed today\", \"last 24 hours\", \"since yesterday\", \"this week\"), set this to the hour count. \"today\"/\"last day\"/\"last 24 hours\" → 24. \"this week\"/\"last week\"/\"past 7 days\" → 168. \"last 2 days\" → 48. \"last hour\" → 1. Cap at 168. Null when the question is not about recency of changes (this is DIFFERENT from date_start/date_end which filter records' own dates).",
  "confirmation_message": "A short, natural sentence confirming what you understood. Put every filter value in **bold**. If aggregations are included, mention what will be calculated. Example: 'Just to confirm, you would like me to calculate the **total deal value by stage** for **all open deals**?'"{extra_lines}
}}

RULES:
1. For names: Extract ALL person names and company names the user mentions.
2. For dates: Interpret relative dates relative to today ({today}). "last month" = previous calendar month. "February" with no year = most recent February. "Q3 2025" = 2025-07-01 to 2025-10-01. "this year" = {year}-01-01 to today. "last 6 months" = 6 months back from today.
3. For statuses: "unpaid" or "owed" or "owing" → ["Owed"]. "paid" → ["Paid"]. "potential" or "pending" → ["Potential"]. Map to the EXACT schema values.
4. For stages: "closed won" → ["Closed Won"]. "lost" → ["Closed Lost"]. "open deals" → all stages EXCEPT Closed Won and Closed Lost.
5. For amounts: "over $50k" → amount_min: 50000. "between $10k and $50k" → amount_min: 10000, amount_max: 50000.
6. For aggregate questions like "who has the most" or "total by rep" — don't filter by name. Add aggregation specs so the database calculates the answer.
7. If the question is a general greeting or has no specific filters, return all fields as null/empty with limit: 50 and empty aggregations.
8. The confirmation_message MUST always be present and should bold the key filter values.
9. For aggregations: Each entry is {{"field": "field_name", "op": "sum|avg|count|min|max", "group_by": "field_name or null"}}.
   - "field" is the field to calculate on (required for sum/avg/min/max, omit for count).
   - "op" is the operation: sum, avg, count, min, max.
   - "group_by" is the field to group results by (null for an overall total).
   - Use aggregations for ANY question about totals, sums, averages, rankings, comparisons, or breakdowns.
   - Do NOT use aggregations for simple detail/lookup questions like "show me deal X" or "what are John's deals".
   - Examples:
     "total pipeline by stage" → [{{"field": "amount", "op": "sum", "group_by": "stage"}}, {{"op": "count", "group_by": "stage"}}]
     "average deal size" → [{{"field": "amount", "op": "avg", "group_by": null}}]
     "who has the most deals?" → [{{"op": "count", "group_by": "deal_owner_name"}}]
     "total commissions owed" → [{{"field": "sales_rep_commission", "op": "sum", "group_by": null}}]

Output ONLY the JSON object. No explanation, no markdown."""
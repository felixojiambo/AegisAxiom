"""Marketplace agents — dynamically registers AI employees from the database.

When an AI employee is imported through the AEGISAXIOM UI, this module creates
a lightweight agent wrapper and registers it in the conductor's agent registry.
The conductor can then route chat messages to these marketplace AI employees.

Usage:
    # At server startup:
    await sync_marketplace_agents(db)

    # When a new AI employee is created via API:
    await register_ai_employee(db, employee_doc)

    # When an AI employee is deleted:
    unregister_ai_employee(employee_id)
"""

import logging
from datetime import datetime, timezone

from services.ai.agents.base import BaseAgent
from services.ai.agents.registry import register_instance, unregister, get_agent
from seeds.seed_tenants import SIMPLIMETA_ROOT_TENANT_ID

logger = logging.getLogger(__name__)

# Prefix for marketplace agent route names to avoid collisions with built-in agents
_ROUTE_PREFIX = "emp_"

# Execution log fields that marketplace agents can query
_EXECUTION_LOG_FIELDS = {
    "timestamp": "(date) — when the execution occurred",
    "status": "(string) — success or error",
    "label": "(string) — output label or category applied",
    "subject": "(string) — input subject or description",
    "execution_time_ms": "(number) — processing time in milliseconds",
    "workflow_id": "(string) — N8N workflow identifier",
}


def derive_employee_use_perms(employee_doc: dict) -> list[str]:
    """Return the employee-specific *.use permission keys for an AI employee.

    Single source of truth for what `<employee>.use` permission(s) grant
    access to a given AI employee. Two paths:

    1. Explicit ``required_permissions`` on the doc — takes precedence and
       is returned verbatim (filtered to non-empty strings).
    2. Convention from ``employee_id`` — ``ai_vega`` → ``["vega.use"]``,
       ``ai_arc`` → ``["arc.use"]``, etc.

    Does NOT include the broad ``ai_employees.view`` perm — callers that
    want it (BaseAgent.required_permissions, which lets *any* viewer reach
    *any* agent) prepend it themselves. The AI employee listing endpoint
    deliberately does not, so granting ``foo.use`` to a role surfaces only
    that specific employee in the user's sidebar.
    """
    explicit_req = employee_doc.get("required_permissions") or []
    if isinstance(explicit_req, list) and explicit_req:
        return [p for p in explicit_req if isinstance(p, str) and p]
    employee_id = employee_doc.get("employee_id", "") or ""
    if employee_id.startswith("ai_"):
        short = employee_id[3:]
        if short:
            return [f"{short}.use"]
    return []


class MarketplaceAgent(BaseAgent):
    """A dynamic agent backed by an AI employee record in MongoDB.

    Unlike static agents (deals, commissions), marketplace agents:
    - Are created at runtime from database records
    - Query the ai_employees collection for execution data
    - Can represent either conversational AIs or N8N workflows
    """

    def __init__(self, employee_doc: dict):
        self._employee = employee_doc
        self._employee_id = employee_doc.get("employee_id", "")

        # Phase 1B.3 — Tenant scoping for marketplace agents.
        #
        # Each AI Employee instance belongs to exactly one tenant. The
        # `user_has_access` override below uses this to gate routing and
        # dispatch so users only ever see/invoke agents from their own
        # tenant.
        #
        # Pre-1B.4 migration rows have no `tenant_id` on the document —
        # they fall back to the SimpliMeta root tenant, which is where
        # all current users live. So today's UX is unchanged: root-tenant
        # users still see every existing agent. Once partner / account
        # tenants start onboarding, isolation kicks in automatically.
        # After 1B.4 backfills every row with an explicit `tenant_id`,
        # this `or` fallback never fires in practice but stays as a
        # safety net.
        self.tenant_id = (
                self._employee.get("tenant_id") or SIMPLIMETA_ROOT_TENANT_ID
        )

        # Identity — used by the conductor's router
        self.route_name = f"{_ROUTE_PREFIX}{self._employee_id}"
        self.description = self._build_description()

        # Permissions — fallback for employees without allowed_roles/allowed_user_ids.
        # In addition to the generic ai_employees.view, also accept the
        # employee-specific *.use permission. Convention lives in
        # derive_employee_use_perms; BaseAgent keeps ai_employees.view as a
        # broad-access escape hatch for the chat path.
        derived_req = derive_employee_use_perms(employee_doc)
        self.required_permissions = list(dict.fromkeys(["ai_employees.view"] + derived_req))

        # Model — use the same default as other agents
        self.model_provider = "google"
        self.model_name = "gemini-3-flash-preview"

        # Collections — marketplace agents query ai_employees
        self.collections = [
            ("ai_employees", f"AI Employee: {self._employee.get('name', 'Unknown')}", _EXECUTION_LOG_FIELDS),
        ]

    def _build_description(self) -> str:
        """Build a routing description from the AI employee's metadata."""
        name = self._employee.get("name", "Unknown")
        role = self._employee.get("role", "AI Employee")
        specialties = self._employee.get("specialties", [])

        desc = f"questions about {name} ({role})"
        if specialties:
            desc += f" — specializes in: {', '.join(specialties[:4])}"
        return desc

    def user_has_access(self, user) -> bool:
        """Phase 1B.3 — Tenant gate + existing RBAC check.

        Two layers of access control:
          1. Tenant gate: user must belong to the same tenant as this
             AI Employee instance. Short-circuits on mismatch so
             out-of-tenant requests never reach the RBAC stage.
          2. Existing RBAC: delegated to BaseAgent — checks
             `allowed_user_ids`, `allowed_roles`, and
             `required_permissions` per the data on the employee doc.

        Tenant filtering also flows transparently to `available_agents`
        in the registry — since that helper iterates `_AGENTS.values()`
        and calls `user_has_access` per agent, out-of-tenant agents
        automatically disappear from the router prompt and from any
        admin listing that respects RBAC visibility.

        Note: this intentionally does NOT honor the wildcard (`*`)
        permission for cross-tenant access. A SimpliMeta admin in the
        root tenant still cannot see an Acme tenant's agents through
        this surface; cross-tenant admin views are a SimpliMeta-root
        capability that lands with the catalog APIs (1B.5+) and uses
        an explicit cross-tenant query rather than the agent registry.
        """
        if getattr(user, "tenant_id", None) != self.tenant_id:
            return False
        return super().user_has_access(user)

    async def build_system_prompt(self, user, data: str, summary: str = "") -> str:
        """Build the answer-generation prompt with the AI employee's context."""
        emp = self._employee
        name = emp.get("name", "AI Employee")
        role = emp.get("role", "")
        description = emp.get("description", "")
        specialties = emp.get("specialties", [])
        emp_type = emp.get("type", "n8n")

        metrics_config = emp.get("metrics_config", {})
        metric_labels = [
            metrics_config.get("metric1_label", "Executions"),
            metrics_config.get("metric2_label", "Success Rate"),
            metrics_config.get("metric3_label", "Avg Time"),
        ]

        type_context = ""
        if emp_type == "n8n":
            connection = emp.get("connection", {})
            type_context = f"""
This is an N8N automated workflow employee.
Workflow ID: {connection.get('workflow_id', 'N/A')}
N8N Instance: {connection.get('n8n_instance_url', 'N/A')}
It processes tasks automatically and reports execution results via webhooks."""
        elif emp_type == "conversational":
            type_context = """
This is a conversational AI employee that interacts with users through a chat interface."""

        return f"""You are the {name} Assistant — providing insights about the {name} AI employee in AEGISAXIOM.

Current User: {user.name} | Role: {user.role.replace('_', ' ').title()}

ABOUT THIS AI EMPLOYEE:
- Name: {name}
- Role: {role}
- Description: {description}
- Type: {emp_type}
- Specialties: {', '.join(specialties) if specialties else 'General'}
- Tracked Metrics: {', '.join(metric_labels)}
{type_context}

EXECUTION DATA (from webhook reports — this is the ONLY data you may reference):
{data}

CAPABILITIES:
1. Report on execution history — how many times this AI employee has run, success/error rates
2. Analyze execution patterns — when it's most active, common labels/outputs
3. Summarize recent activity — what was processed recently

COMMUNICATION RULES:
- Use **bolding** for key numbers and metrics
- If no execution data exists, say "This AI employee hasn't reported any executions yet."
- NEVER hallucinate — only reference the data provided above
- Be concise and actionable"""

    async def fetch_data(self, db, user, query: dict) -> list:
        """Fetch execution data for this AI employee from MongoDB."""
        employee = await db.ai_employees.find_one(
            {"employee_id": self._employee_id},
            {"_id": 0, "execution_log": 1, "execution_count": 1,
             "success_count": 1, "error_count": 1, "metrics": 1,
             "last_execution_at": 1, "name": 1, "role": 1}
        )

        if not employee:
            return []

        # Build a summary document for the LLM
        summary = {
            "employee_name": employee.get("name", self._employee.get("name", "")),
            "total_executions": employee.get("execution_count", 0),
            "successful": employee.get("success_count", 0),
            "errors": employee.get("error_count", 0),
            "last_active": employee.get("last_execution_at", "Never"),
            "metrics": employee.get("metrics", {}),
        }

        # Include recent execution entries
        log = employee.get("execution_log", [])
        limit = query.get("limit", 50)
        recent = log[:limit]

        # Return summary + recent executions as a list the conductor can serialize
        return [summary] + recent


class ConversationalMarketplaceAgent(BaseAgent):
    """A conversational AI employee with its own system prompt, knowledge base,
    and per-user state. Sibling of MarketplaceAgent — that one handles n8n
    employees that report execution data. This one handles native conversations."""

    is_conversational = True  # Checked by conductor to skip query interpretation

    def __init__(self, employee_doc: dict):
        self._employee = employee_doc
        self._employee_id = employee_doc.get("employee_id", "")

        # Phase 1B.3 — Tenant scoping (see MarketplaceAgent.__init__
        # for the full rationale). Same fallback semantics: pre-1B.4
        # rows without `tenant_id` resolve to SIMPLIMETA_ROOT_TENANT_ID
        # so localhost / dev demos continue to work unchanged until
        # 1B.4 backfills explicit tenant_ids onto every row.
        self.tenant_id = (
                self._employee.get("tenant_id") or SIMPLIMETA_ROOT_TENANT_ID
        )

        self.route_name = f"{_ROUTE_PREFIX}{self._employee_id}"
        self.description = employee_doc.get(
            "router_description",
            f"questions about {employee_doc.get('name', 'this AI employee')}",
        )
        self.required_permissions = ["ai_employees.view"]
        self.model_provider = employee_doc.get("model_provider", "google")
        self.model_name = employee_doc.get("model_name", "gemini-3-flash-preview")
        self.collections = []

    def user_has_access(self, user) -> bool:
        """Phase 1B.3 — Tenant gate + existing RBAC check.

        Mirrors MarketplaceAgent.user_has_access. See that docstring for
        full rationale on why the wildcard is intentionally not honored
        and how this flows through to registry.available_agents().
        """
        if getattr(user, "tenant_id", None) != self.tenant_id:
            return False
        return super().user_has_access(user)

    async def fetch_data(self, db, user, query: dict) -> dict:
        """Load user state + narrowed knowledge base for the current module."""
        state_doc = await db.ai_employee_user_state.find_one(
            {"user_id": user.user_id, "employee_id": self._employee_id},
            {"_id": 0},
        )
        user_state = (state_doc or {}).get("state", {})
        if not user_state:
            user_state = dict(self._employee.get("state_schema", {}))

        current_module = user_state.get("current_module", 1)
        kb_filter = {
            "employee_id": self._employee_id,
            "$or": [
                {"module_number": {"$in": [current_module, current_module + 1]}},
                {"module_number": None},
                {"tags": {"$in": [f"module_{current_module}", "global"]}},
            ],
        }
        if current_module == 7 and 7 in user_state.get("modules_completed", []):
            kb_filter["$or"].append({"tags": "final_test"})

        # Only load metadata — NOT full content or quiz payloads.
        # The render_artifact tool fetches content directly when needed.
        # This keeps the prompt small (~500 chars vs ~28,000 chars).
        kb_docs = await db.ai_employee_knowledge.find(
            kb_filter,
            {"_id": 0, "content": 0, "quiz_payload": 0},
        ).to_list(500)

        return {
            "docs": kb_docs,
            "user_state": user_state,
            "summary": "",
            "total_count": len(kb_docs),
        }

    async def build_system_prompt(self, user, data: str, summary: str = "") -> str:
        base_prompt = self._employee.get("system_prompt", "")
        return f"""{base_prompt}

CURRENT USER: {user.name} (Role: {user.role})

<knowledge_base>
{data}
</knowledge_base>
"""


# ──────────────────────────────────────────────
#  SYNC / REGISTER / UNREGISTER
# ──────────────────────────────────────────────

def _build_agent(employee_doc: dict) -> BaseAgent:
    """Pick the right agent class for an employee document."""
    if employee_doc.get("type") == "conversational" and employee_doc.get("system_prompt"):
        return ConversationalMarketplaceAgent(employee_doc)
    return MarketplaceAgent(employee_doc)


async def sync_marketplace_agents(db):
    """Load all active AI employees from MongoDB and register them as agents.

    Call this at server startup. Safe to call multiple times —
    it will update existing registrations and remove stale ones.
    """
    try:
        employees = await db.ai_employees.find(
            {"status": {"$in": ["active", "training"]}},
            {"_id": 0}
        ).to_list(500)

        registered_count = 0
        current_routes = set()
        # Phase 1B.3 — per-tenant breakdown for the startup log.
        # Useful during the multi-tenancy rollout to spot misconfigurations
        # (e.g. a tenant unexpectedly missing all instances, or every
        # instance falling back to root because tenant_id is unset).
        tenant_counts: dict[str, int] = {}

        for emp in employees:
            employee_id = emp.get("employee_id")
            if not employee_id:
                continue

            # Ensure access control defaults exist on the document
            if "allowed_roles" not in emp:
                emp["allowed_roles"] = ["admin"]  # Restrictive default
            if "allowed_user_ids" not in emp:
                emp["allowed_user_ids"] = []

            route = f"{_ROUTE_PREFIX}{employee_id}"
            current_routes.add(route)

            agent = _build_agent(emp)
            register_instance(agent)
            registered_count += 1
            tenant_counts[agent.tenant_id] = tenant_counts.get(agent.tenant_id, 0) + 1

        # Clean up stale marketplace agents (deleted or deactivated employees)
        from services.ai.agents.registry import all_agents
        stale = [
            a.route_name for a in all_agents()
            if a.route_name.startswith(_ROUTE_PREFIX) and a.route_name not in current_routes
        ]
        for route in stale:
            unregister(route)
            logger.info(f"Removed stale marketplace agent: {route}")

        # Format: "5 in ten_simplimeta_root, 2 in ten_acme"
        breakdown = ", ".join(
            f"{count} in {tid}" for tid, count in sorted(tenant_counts.items())
        ) or "no tenants"
        logger.info(
            f"Marketplace sync complete: {registered_count} AI employees "
            f"registered as agents ({breakdown})"
        )

    except Exception as e:
        logger.error(f"Failed to sync marketplace agents: {e}")


async def register_ai_employee(db, employee_doc: dict):
    """Register a single AI employee as a conductor agent.

    Call this when a new AI employee is created via the API.
    """
    employee_id = employee_doc.get("employee_id")
    if not employee_id:
        return

    status = employee_doc.get("status", "active")
    if status not in ("active", "training"):
        logger.info(f"Skipping registration for {employee_id} — status is {status}")
        return

    # Ensure access control defaults exist on the document
    if "allowed_roles" not in employee_doc:
        employee_doc["allowed_roles"] = ["admin"]  # Restrictive default
    if "allowed_user_ids" not in employee_doc:
        employee_doc["allowed_user_ids"] = []

    agent = _build_agent(employee_doc)
    register_instance(agent)
    logger.info(
        f"Registered AI employee as agent: {employee_doc.get('name')} "
        f"→ {agent.route_name} (tenant={agent.tenant_id})"
    )


def unregister_ai_employee(employee_id: str):
    """Remove an AI employee from the conductor's agent registry.

    Call this when an AI employee is deleted or deactivated.
    """
    route = f"{_ROUTE_PREFIX}{employee_id}"
    existing = get_agent(route)
    if existing:
        unregister(route)
        logger.info(f"Unregistered AI employee agent: {route}")
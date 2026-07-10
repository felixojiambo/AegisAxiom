"""Base class for all AEGISAXIOM AI agents."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from services.ai.schema import build_collection_schema


@dataclass
class AgentResult:
    """Returned by an agent after processing a user message."""
    answer: str
    trace: list = field(default_factory=list)
    pending_confirmation: bool = False
    pending_data: dict = field(default_factory=dict)


class BaseAgent(ABC):
    """Abstract base for all AEGISAXIOM AI sub-agents.

    Subclasses define their domain (deals, commissions, etc.) and the
    conductor dispatches to them via the registry.
    """

    # --- Identity ---
    route_name: str = ""
    description: str = ""

    # --- Permissions: user needs at least one to access this agent ---
    required_permissions: list[str] = []

    # --- LLM model preference (overrides global default) ---
    model_provider: str = "google"
    model_name: str = "gemini-3-flash-preview"

    # --- Schema: collections this agent queries + curated field descriptions ---
    # Each entry: (collection_name, human_label, {field: description})
    collections: list[tuple[str, str, dict[str, str]]] = []

    # --- Optional domain-specific filter keys exposed to the LLM query builder ---
    # When non-empty, this string is interpolated into the query-builder prompt
    # so the LLM can produce these extra keys in its JSON output. The prompt
    # already understands name_searches, dates, statuses, stages, amounts;
    # subclasses use this hook to surface domain-specific keys (project_types,
    # priorities, scope_to_user, etc.) and the matching extraction rules.
    query_fields_extra: str = ""

    def user_has_access(self, user) -> bool:
        """Check if user can access this AI employee.

        Access granted if:
        1. User has wildcard (*) permission (admin)
        2. User's ID is in the employee's allowed_user_ids (per-user override)
        3. User's role is in the employee's allowed_roles (role-level access)
        4. Fallback: user has any of the agent's required_permissions (vega.use, orion.use, etc.)
        """
        perms = getattr(user, "permissions", [])
        if "*" in perms:
            return True

        # Data-driven access from AI employee document
        emp = getattr(self, '_employee', {})
        if isinstance(emp, dict):
            allowed_users = emp.get("allowed_user_ids", [])
            if getattr(user, "user_id", None) in allowed_users:
                return True
            allowed_roles = emp.get("allowed_roles", [])
            if getattr(user, "role", None) in allowed_roles:
                return True

        # Fallback to permission-based (vega.use, orion.use, bpmn.use)
        return any(p in perms for p in self.required_permissions)

    def has_view_all(self, user) -> bool:
        perms = getattr(user, "permissions", [])
        if "*" in perms:
            return True
        return any(p for p in self.required_permissions if p.endswith(".view.all") and p in perms)

    def build_schema(self) -> str:
        """Build the full schema string from discovered fields + curated descriptions."""
        parts = []
        for collection_name, label, field_descs in self.collections:
            schema_str = build_collection_schema(collection_name, label, field_descs)
            if schema_str:
                parts.append(schema_str)
        return "\n\n".join(parts)

    @abstractmethod
    async def build_system_prompt(self, user, data: str, summary: str = "") -> str:
        """Return the system prompt for the answer-generation LLM call."""

    @abstractmethod
    async def fetch_data(self, db, user, query: dict) -> dict | list:
        """Fetch data from the database using RBAC-scoped filters.

        Returns either:
          - list of docs (legacy)
          - dict with {"docs": list, "summary": str, "total_count": int}
        """

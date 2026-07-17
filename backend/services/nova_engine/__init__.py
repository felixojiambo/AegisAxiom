"""Nova engine package — finance survivors only.

The externally-fed projection engine, GL loaders, and report tools were removed
in Phase 1 (accounting-integration removal + Nova/FCC park). What remains is the
internal-billing-fed
Cashflow Planner (``cashflow_planner``, ``cashflow_planner_adapter``) and the
finance-snapshot aggregator (``finance_snapshots``), plus their app-DB and
numeric helpers (``db``, ``_gl_helpers``). These migrate to Postgres in the
Finance-survivors phase (Phase 10).
"""
__version__ = "0.1.0"

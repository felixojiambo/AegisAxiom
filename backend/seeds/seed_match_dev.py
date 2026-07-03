"""Dev-only seeder for MATCH Settings + a canonical Provider Template.

Pre-fills the MATCH settings singleton with the local mock-appdirect URL +
bearer, the n8n cloud webhook URLs + shared auth token, and a canonical
``Nextiva (dev)`` provider template so operators don't have to paste tokens
and webhook URLs on every fresh stack.

Gated by ``MATCH_DEV_SEED=true`` in ``server.py`` startup. NEVER run in
production — the bundled defaults include a local-only AppDirect bearer.

Idempotency contract:

  - Settings: only fields that are empty/missing get filled. If the operator
    has already typed in a non-empty value (URL, bearer ciphertext, etc.),
    we leave it alone. Existing manual edits always win.

  - Template: keyed on ``template_id="m_tmpl_dev_nextiva"``. If a template
    with this id already exists, we log "skipped, already present" and
    return — the operator may have customized it.

Token rotation: every secret + URL is env-var-driven. When Aidan rotates
the n8n bearer, set ``MATCH_DEV_N8N_TOKEN`` in ``.env.ingestion`` and
re-boot; no code edit required.

Encryption: bearer tokens are encrypted at rest via
``services.match.crypto.encrypt_value`` before any DB write. Plaintext
NEVER lands in storage. Encryption is performed lazily inside the seeder
so importing this module never raises if MATCH_FERNET_KEY is unset.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict

from services.match.crypto import encrypt_value

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults — overridable via env vars so token rotation does not require
# code edits. These are local-dev / shared-test secrets; production deploys
# MUST NOT set MATCH_DEV_SEED=true.
# ---------------------------------------------------------------------------
_DEFAULT_APPDIRECT_URL = "http://host.docker.internal:8001"
_DEFAULT_APPDIRECT_BEARER = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJtb2NrLWFwcGRpcmVjdC1kZW1vIiwidGVuYW50IjoiYnJ5YW4tdGVzdCIs"
    "ImlhdCI6MTcwMDAwMDAwMH0."
    "RGVtbzFvY2FsT25seVNpZ25hdHVyZURvTm90VXNlSW5Qcm9k"
)
_DEFAULT_N8N_TRIGGER_URL = "https://n8n.simplimeta.io/webhook/process-provider-report"
_DEFAULT_N8N_MAGIC_URL = "https://n8n.simplimeta.io/webhook/suggest-mappings"
_DEFAULT_N8N_TOKEN = "cOMoIwEz-6IKeMGFOAfIXTqD6IiniI8efLkWMMp6OZQ"

_SINGLETON_FILTER: Dict[str, Any] = {"_singleton": True}
_DEV_TEMPLATE_ID = "m_tmpl_dev_nextiva"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_blank(value: Any) -> bool:
    """An existing setting is considered blank/missing iff it is None, "",
    or absent. Anything else — including a ciphertext string — counts as
    a real operator value that must be preserved.
    """
    return value is None or value == ""


def _fill_if_blank(
    target: Dict[str, Any],
    key: str,
    new_value: Any,
    filled: list,
    label: str,
) -> None:
    """Set ``target[key] = new_value`` only when the current slot is blank.

    Appends the human-readable ``label`` to ``filled`` when a fill occurs,
    so the caller can log exactly which fields were touched.
    """
    if _is_blank(target.get(key)):
        target[key] = new_value
        filled.append(label)


async def _seed_settings_singleton(db) -> list:
    """Upsert the MATCH settings singleton, filling only blank fields.

    Returns the list of human-readable field labels that were filled this
    run. An empty list means the doc was already fully populated and this
    call was a true no-op.
    """
    appdirect_url = os.getenv("MATCH_DEV_APPDIRECT_URL", _DEFAULT_APPDIRECT_URL)
    appdirect_bearer_plaintext = os.getenv(
        "MATCH_DEV_APPDIRECT_BEARER", _DEFAULT_APPDIRECT_BEARER
    )
    n8n_trigger_url = os.getenv("MATCH_DEV_N8N_TRIGGER_URL", _DEFAULT_N8N_TRIGGER_URL)
    n8n_magic_url = os.getenv("MATCH_DEV_N8N_MAGIC_URL", _DEFAULT_N8N_MAGIC_URL)
    n8n_token_plaintext = os.getenv("MATCH_DEV_N8N_TOKEN", _DEFAULT_N8N_TOKEN)

    existing = await db["match_settings"].find_one(_SINGLETON_FILTER) or {}
    # Mongo internals must not survive into the replace doc — we re-add
    # `_singleton` ourselves below.
    doc: Dict[str, Any] = {
        k: v for k, v in existing.items() if k not in ("_id",)
    }
    doc.setdefault("_singleton", True)

    n8n = doc.setdefault("n8n", {})
    appdirect = doc.setdefault("appdirect", {})
    defaults = appdirect.setdefault("defaults", {})
    crm = doc.setdefault("crm", {})

    filled: list = []

    # n8n block. URLs are plaintext; the shared_auth_token is encrypted at
    # rest. Only encrypt + fill when the slot is blank — we must not re-encrypt
    # an existing ciphertext.
    _fill_if_blank(n8n, "trigger_webhook_url", n8n_trigger_url, filled, "n8n.trigger_webhook_url")
    _fill_if_blank(n8n, "magic_mapping_webhook_url", n8n_magic_url, filled, "n8n.magic_mapping_webhook_url")
    if _is_blank(n8n.get("shared_auth_token")):
        n8n["shared_auth_token"] = encrypt_value(n8n_token_plaintext)
        filled.append("n8n.shared_auth_token (encrypted)")

    # AppDirect block.
    _fill_if_blank(appdirect, "base_url", appdirect_url, filled, "appdirect.base_url")
    if _is_blank(appdirect.get("bearer_token")):
        appdirect["bearer_token"] = encrypt_value(appdirect_bearer_plaintext)
        filled.append("appdirect.bearer_token (encrypted)")

    # Safety switches — only fill when actually missing (a deliberate False
    # set by the operator must be preserved).
    if "live_post_enabled" not in appdirect:
        appdirect["live_post_enabled"] = False
        filled.append("appdirect.live_post_enabled=False")
    if "multipass_enabled" not in appdirect:
        appdirect["multipass_enabled"] = False
        filled.append("appdirect.multipass_enabled=False")

    # Seeded mock test user (Bryan Reynolds in mock seed data). Operator may
    # override; we only fill when blank/zero (the model default is 0 which
    # means "unset" per AppDirectEnumDefaults — fill it).
    if not defaults.get("sales_agent_id"):
        defaults["sales_agent_id"] = 8003
        filled.append("appdirect.defaults.sales_agent_id=8003")

    # CRM connection mode — operator can change in UI.
    if _is_blank(crm.get("connection_mode")):
        crm["connection_mode"] = "snapshot"
        filled.append("crm.connection_mode=snapshot")

    if filled:
        doc["updated_at"] = _iso_now()
        await db["match_settings"].replace_one(
            _SINGLETON_FILTER, doc, upsert=True
        )
        logger.info(
            "MATCH dev seed: settings singleton — filled %d field(s): %s",
            len(filled),
            ", ".join(filled),
        )
    else:
        logger.info(
            "MATCH dev seed: settings singleton already fully populated; no-op"
        )
    return filled


# Canonical Nextiva column mapping — source columns match the CSV at
# ``test-data/match/provider_report.csv``; target paths are Contract H
# (``shared/contract_h.json`` in the source POC), which is what n8n writes
# and what ``services/match/appdirect_client.py`` reads back for the push
# chain (per the OpenAPI spec at provider-sales-api.openapi.yaml).
#
# Close Date intentionally appears twice — n8n fans the same source value
# into ``output.order_details.contract_start`` AND
# ``output.milestones.order_completion_date`` (matches source POC § 18.2).
#
# Unused_D / Unused_H / Unused_J / Unused_Q columns in the test CSV are
# deliberately omitted — they're "no payload" filler columns simulating
# the gaps in the real Nextiva sheet.
_NEXTIVA_COLUMN_MAPPINGS = [
    {"source_column": "Partner Account",         "target_path": "output.top_level.advisor_company",                "transforms": [],                "required": True},
    {"source_column": "Account Name",            "target_path": "output.top_level.customer.name",                  "transforms": [],                "required": True},
    {"source_column": "Opportunity Name",        "target_path": "output.order_attributes.order_description",       "transforms": [],                "required": False},
    {"source_column": "MRR",                     "target_path": "output.order_details.mrgmv",                      "transforms": ["strip_usd_prefix"], "required": False},
    {"source_column": "Order Type",              "target_path": "output.order_attributes.provider_revenue_category", "transforms": ["normalize_prc"], "required": True},
    {"source_column": "Close Date",              "target_path": "output.order_details.contract_start",             "transforms": ["parse_date"],    "required": True},
    {"source_column": "Close Date",              "target_path": "output.milestones.order_completion_date",         "transforms": ["parse_date"],    "required": False},
    {"source_column": "Created Date",            "target_path": "output.order_details.contract_signed",            "transforms": ["parse_date"],    "required": True},
    {"source_column": "Account Number",          "target_path": "output.order_details.commission_ban",             "transforms": [],                "required": True},
    {"source_column": "Term",                    "target_path": "output.order_details.contract_term",              "transforms": [],                "required": True},
    {"source_column": "Street",                  "target_path": "output.top_level.location.street",                "transforms": [],                "required": False},
    {"source_column": "City",                    "target_path": "output.top_level.location.city",                  "transforms": [],                "required": False},
    {"source_column": "State",                   "target_path": "output.top_level.location.state",                 "transforms": [],                "required": False},
    {"source_column": "Zip",                     "target_path": "output.top_level.location.zip",                   "transforms": [],                "required": False},
    {"source_column": "Provider Sales Order ID", "target_path": "output.order_details.provider_order_id",          "transforms": [],                "required": False},
]


async def _seed_provider_template(db) -> bool:
    """Seed (or migrate in place) the canonical ``Nextiva (dev)`` template.

    Fill-if-blank semantics — same as the settings singleton. If the
    template already exists with a customized ``column_mappings`` /
    ``default_product`` / etc., we leave those alone. Only fields that
    are still at their empty/default state get the canonical Nextiva
    values folded in.

    Returns True if any change was written, False if the doc was already
    fully populated and this call was a true no-op.
    """
    existing = await db["match_provider_templates"].find_one(
        {"template_id": _DEV_TEMPLATE_ID}
    )

    if existing is None:
        # Fresh insert — write the full canonical doc.
        now = _iso_now()
        template = {
            "template_id": _DEV_TEMPLATE_ID,
            "name": "Nextiva (dev)",
            "default_product": "Other (UCaaS)",
            "sales_rep_priority": "Commission Sales > Principal",
            "default_order_status": "Provisioned",
            "appdirect_product_id": 4201,
            "appdirect_provider_id": 901,
            "appdirect_advisor_id": 7001,
            "appdirect_account_manager_id": 8003,
            "column_mappings": list(_NEXTIVA_COLUMN_MAPPINGS),
            "business_rules": [],
            "created_at": now,
            "updated_at": now,
            "created_by": "system",
        }
        await db["match_provider_templates"].insert_one(template)
        logger.info(
            "MATCH dev seed: inserted provider template %s with %d column mappings",
            _DEV_TEMPLATE_ID,
            len(_NEXTIVA_COLUMN_MAPPINGS),
        )
        return True

    # Existing doc — fill blank fields only.
    updates: Dict[str, Any] = {}
    filled: list = []

    # column_mappings: only fill if currently absent OR empty list. A
    # non-empty list means the operator has customized — leave alone.
    if not existing.get("column_mappings"):
        updates["column_mappings"] = list(_NEXTIVA_COLUMN_MAPPINGS)
        filled.append(f"column_mappings ({len(_NEXTIVA_COLUMN_MAPPINGS)} entries)")

    # Scalar defaults — fill-if-blank using the same _is_blank rule.
    for field, canonical in (
        ("default_product", "Other (UCaaS)"),
        ("sales_rep_priority", "Commission Sales > Principal"),
        ("default_order_status", "Provisioned"),
    ):
        if _is_blank(existing.get(field)):
            updates[field] = canonical
            filled.append(field)

    if updates:
        updates["updated_at"] = _iso_now()
        await db["match_provider_templates"].update_one(
            {"template_id": _DEV_TEMPLATE_ID},
            {"$set": updates},
        )
        logger.info(
            "MATCH dev seed: provider template %s migrated — filled: %s",
            _DEV_TEMPLATE_ID,
            ", ".join(filled),
        )
        return True

    logger.info(
        "MATCH dev seed: provider template %s already fully populated; no-op",
        _DEV_TEMPLATE_ID,
    )
    return False


async def seed_match_dev(db) -> None:
    """Idempotent dev-environment seeder for MATCH.

    Pre-fills the MATCH settings singleton with the local mock-appdirect
    URL + bearer, the n8n cloud webhook URLs + shared auth token, and a
    canonical 'Nextiva (dev)' provider template.

    Safe to re-run on every boot — upserts by ``_singleton:true`` for
    settings and by ``template_id`` for the template. Existing operator
    edits are preserved: only fields that are empty/missing get filled;
    non-empty values are left alone.

    Env-var driven for token rotation:
      - MATCH_DEV_APPDIRECT_BEARER
      - MATCH_DEV_N8N_TOKEN
      - MATCH_DEV_APPDIRECT_URL
      - MATCH_DEV_N8N_TRIGGER_URL
      - MATCH_DEV_N8N_MAGIC_URL

    Encrypts bearer tokens at rest using ``services.match.crypto.encrypt_value``
    BEFORE writing them. NEVER stores plaintext.
    """
    await _seed_settings_singleton(db)
    await _seed_provider_template(db)

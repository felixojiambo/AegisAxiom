"""Source-level tests for the procurement vendor discovery package.

The discovery package is documentation plus templates. It does not
add backend or frontend code. These tests assert the doc and
templates exist, name the required sections and fields, contain
no credential-looking values, and do not silently introduce
supplier automation.

See docs/sales-ops/PROCUREMENT_VENDOR_DISCOVERY_PACKAGE.md.
"""

import json
import pathlib

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC_PATH = REPO_ROOT / "docs" / "sales-ops" / "PROCUREMENT_VENDOR_DISCOVERY_PACKAGE.md"
CSV_PATH = REPO_ROOT / "docs" / "sales-ops" / "templates" / "procurement_vendor_matrix.template.csv"
SCHEMA_PATH = REPO_ROOT / "docs" / "sales-ops" / "templates" / "procurement_vendor_matrix.schema.json"


def _skip_if_docs_not_mounted():
    if not DOC_PATH.exists():
        pytest.skip("docs tree not mounted alongside backend tests")


# ---------------------------------------------------------------------------
# Discovery doc
# ---------------------------------------------------------------------------


def test_discovery_doc_exists():
    """The discovery package lives at the canonical path. Skips
    cleanly when the docs tree is not mounted alongside the backend
    tests."""
    _skip_if_docs_not_mounted()
    assert DOC_PATH.exists()


def test_discovery_doc_names_required_vendor_matrix_fields():
    """The vendor matrix columns are the contract Ticket 9 reads.
    Every required column from the discovery doc spec must appear
    so a reviewer can grep the doc for the field they care about."""
    _skip_if_docs_not_mounted()
    text = DOC_PATH.read_text(encoding="utf-8")
    required_columns = (
        "category",
        "subcategory",
        "vendor_name",
        "vendor_rank",
        "preferred_vendor",
        "net_terms_available",
        "net_terms_notes",
        "portal_url",
        "login_required",
        "api_available",
        "api_docs_url",
        "search_method",
        "sku_mapping_required",
        "price_fields_available",
        "availability_fields_available",
        "delivery_eta_fields_available",
        "source_url_required",
        "confidence_notes",
        "manual_review_required",
        "owner_role",
        "status",
    )
    for col in required_columns:
        assert col in text, f"discovery doc missing vendor matrix column {col!r}"


def test_discovery_doc_names_category_mapping_template():
    _skip_if_docs_not_mounted()
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "Category mapping template" in text or "category mapping template" in text.lower()
    for col in (
        "aegisaxiom_material_category",
        "normalized_procurement_category",
        "preferred_vendor_rank_list_needed",
        "fallback_category",
        "requires_manual_mapping",
    ):
        assert col in text, f"category mapping column {col!r} missing"


def test_discovery_doc_names_quote_fields_contract():
    _skip_if_docs_not_mounted()
    text = DOC_PATH.read_text(encoding="utf-8")
    for field in (
        "quote_id",
        "sourcing_request_id",
        "material_line_id",
        "vendor_name",
        "vendor_rank",
        "price",
        "currency",
        "availability",
        "delivery_eta",
        "shipping_cost",
        "tax_estimate",
        "total_cost",
        "confidence",
        "source_url",
        "source_captured_at",
        "manual_review_reason",
        "selected",
        "ordered_at",
        "delivered_at",
        "tenant_id",
    ):
        assert field in text, f"quote field {field!r} missing from discovery doc"


def test_discovery_doc_names_ranking_rules_and_thresholds():
    _skip_if_docs_not_mounted()
    text = DOC_PATH.read_text(encoding="utf-8")
    # Net terms preference rule.
    assert "net-terms" in text.lower() or "net terms" in text.lower()
    # Cheaper / faster override rules.
    assert "significantly_cheaper_percent" in text
    assert "significantly_cheaper_absolute_dollars" in text
    assert "significantly_faster_days" in text
    # Auto-approve guardrails.
    assert "max_auto_approve_amount" in text
    assert "min_auto_approve_confidence" in text
    assert "require_human_review_above_amount" in text
    # Manual review triggers.
    assert "manual review" in text.lower()


def test_discovery_doc_states_supplier_automation_not_built():
    """The spec rule: this ticket must NOT build supplier
    automation. The doc must explicitly say so."""
    _skip_if_docs_not_mounted()
    text = DOC_PATH.read_text(encoding="utf-8")
    lower_text = text.lower()
    assert "does not build" in lower_text or "must not be built" in lower_text
    # The specific things the spec forbids must be named. Each
    # term is compared against the lowercased doc body so doc
    # casing variations do not cause false negatives.
    for term in (
        "scraping",
        "portal automation",
        "quote approval ui",
    ):
        assert term in lower_text, f"forbidden-scope item {term!r} missing"


def test_discovery_doc_lists_fallback_policy():
    _skip_if_docs_not_mounted()
    text = DOC_PATH.read_text(encoding="utf-8")
    # Amazon fallback explicitly addressed.
    assert "Amazon" in text
    # Auth-failure and API/rate-limit fallback addressed.
    assert "authentication failure" in text.lower() or "auth failure" in text.lower()
    assert "rate-limit" in text.lower() or "rate limit" in text.lower()


def test_discovery_doc_lists_approval_checklist():
    _skip_if_docs_not_mounted()
    text = DOC_PATH.read_text(encoding="utf-8")
    # Section heading present.
    assert "Approval checklist" in text
    # Required approval items present (at least the high-impact ones).
    for item in (
        "Categories list approved",
        "Vendor list approved",
        "vendor rankings approved",
        "Net-terms metadata",
        "Threshold values approved",
        "Fallback policy approved",
        "Manual review rules approved",
    ):
        assert item in text, f"approval checklist item {item!r} missing"


def test_discovery_doc_lists_ticket_9_gate():
    _skip_if_docs_not_mounted()
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "Ticket 9" in text
    # Both modes named so a future implementer cannot blend them.
    assert "Mode A" in text
    assert "Mode B" in text


# ---------------------------------------------------------------------------
# CSV template
# ---------------------------------------------------------------------------


def test_vendor_matrix_template_csv_exists():
    if not CSV_PATH.exists():
        pytest.skip("docs tree not mounted alongside backend tests")
    assert CSV_PATH.exists()


def test_vendor_matrix_template_csv_has_required_headers():
    if not CSV_PATH.exists():
        pytest.skip("docs tree not mounted alongside backend tests")
    text = CSV_PATH.read_text(encoding="utf-8")
    header_line = text.splitlines()[0]
    for col in (
        "category",
        "vendor_name",
        "vendor_rank",
        "preferred_vendor",
        "net_terms_available",
        "portal_url",
        "owner_role",
        "status",
    ):
        assert col in header_line, f"template CSV missing column {col!r}"


def test_vendor_matrix_template_csv_carries_no_credentials():
    """The template must not contain any credential-looking keys
    or values. Acceptable: PLACEHOLDER markers, public test URLs.
    The substring list is intentionally broad; if a future column
    name legitimately contains one of these words the test will
    catch it and force a deliberate review."""
    if not CSV_PATH.exists():
        pytest.skip("docs tree not mounted alongside backend tests")
    text = CSV_PATH.read_text(encoding="utf-8").lower()
    forbidden_substrings = (
        "password",
        "secret",
        "api_key",
        "token",
        "authorization",
        "bearer",
        "client_secret",
        "private_key",
    )
    for needle in forbidden_substrings:
        assert needle not in text, (
            f"template CSV must not contain credential-looking token {needle!r}"
        )


def test_vendor_matrix_template_csv_only_uses_example_test_urls():
    """Real vendor URLs would imply approved rankings. The template
    is documentation-only; any URL in it must use a synthetic test
    domain like example.test. The portal_url column index is
    resolved from the header so a future column reordering does
    not silently check the wrong cell."""
    if not CSV_PATH.exists():
        pytest.skip("docs tree not mounted alongside backend tests")
    text = CSV_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    header_cells = [c.strip() for c in lines[0].split(",")]
    assert "portal_url" in header_cells, "template CSV missing portal_url column"
    portal_idx = header_cells.index("portal_url")
    # Only the data rows are checked; the header line is column names.
    for line in lines[1:]:
        if not line.strip():
            continue
        cells = line.split(",")
        if len(cells) <= portal_idx:
            continue
        portal_url = cells[portal_idx].strip()
        if not portal_url:
            continue
        assert "example.test" in portal_url or portal_url.startswith(
            "PLACEHOLDER"
        ), "template CSV portal_url must be synthetic; got " + repr(portal_url)


# ---------------------------------------------------------------------------
# JSON schema
# ---------------------------------------------------------------------------


def test_vendor_matrix_schema_exists():
    if not SCHEMA_PATH.exists():
        pytest.skip("docs tree not mounted alongside backend tests")
    assert SCHEMA_PATH.exists()


def test_vendor_matrix_schema_is_valid_json_with_required_fields():
    if not SCHEMA_PATH.exists():
        pytest.skip("docs tree not mounted alongside backend tests")
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert data.get("type") == "object"
    assert isinstance(data.get("properties"), dict)
    required = set(data.get("required", []))
    for field in (
        "category",
        "vendor_name",
        "vendor_rank",
        "preferred_vendor",
        "net_terms_available",
        "portal_url",
        "login_required",
        "api_available",
        "search_method",
        "sku_mapping_required",
        "price_fields_available",
        "availability_fields_available",
        "delivery_eta_fields_available",
        "source_url_required",
        "manual_review_required",
        "owner_role",
        "status",
    ):
        assert field in required, f"schema missing required field {field!r}"
        assert field in data["properties"], (
            f"schema properties missing definition for {field!r}"
        )


def test_vendor_matrix_schema_owner_role_uses_capability_enum():
    """owner_role must be a closed enum of role names, never a free
    string and never a list of named individuals."""
    if not SCHEMA_PATH.exists():
        pytest.skip("docs tree not mounted alongside backend tests")
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    owner_role = data["properties"].get("owner_role", {})
    enum = owner_role.get("enum")
    assert isinstance(enum, list)
    assert set(enum) == {"procurement", "sales_ops", "pmo", "validator"}


def test_vendor_matrix_schema_status_uses_closed_enum():
    """Status is closed so a row cannot land in an undefined state."""
    if not SCHEMA_PATH.exists():
        pytest.skip("docs tree not mounted alongside backend tests")
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    status = data["properties"].get("status", {})
    enum = status.get("enum")
    assert isinstance(enum, list)
    assert set(enum) == {"draft", "approved", "deprecated"}


def test_vendor_matrix_schema_carries_no_credentials():
    """No credential-looking keys anywhere in the schema. Mirrors
    the CSV forbidden-substring list so both artifacts catch the
    same vocabulary."""
    if not SCHEMA_PATH.exists():
        pytest.skip("docs tree not mounted alongside backend tests")
    text = SCHEMA_PATH.read_text(encoding="utf-8").lower()
    forbidden_substrings = (
        "password",
        "secret",
        "api_key",
        "token",
        "authorization",
        "bearer",
        "client_secret",
        "private_key",
    )
    for needle in forbidden_substrings:
        assert needle not in text, (
            f"schema must not contain credential-looking token {needle!r}"
        )


# ---------------------------------------------------------------------------
# Anti-scope-creep: confirm no supplier automation introduced
# ---------------------------------------------------------------------------


def test_no_procurement_bot_scaffold_introduced_by_this_ticket():
    """The ticket spec says this discovery package must not
    introduce supplier scraping, vendor adapters, procurement bot
    client code, quote webhook processing, or quote UI. None of
    those files exist today. If a future ticket legitimately adds
    any of these, this test MUST fail loudly so the maintainer
    explicitly removes the path from this list rather than letting
    the scope-creep guard silently degrade into a no-op skip.
    """
    forbidden_paths = (
        REPO_ROOT / "backend" / "services" / "procurement_bot.py",
        REPO_ROOT / "backend" / "services" / "vendor_scraping.py",
        REPO_ROOT / "backend" / "services" / "supplier_adapter.py",
        REPO_ROOT / "backend" / "routes" / "procurement_bot.py",
        REPO_ROOT / "backend" / "routes" / "quote_webhook.py",
        REPO_ROOT
        / "frontend"
        / "src"
        / "components"
        / "sales-ops"
        / "QuoteApprovalPanel.js",
    )
    existing = [str(p) for p in forbidden_paths if p.exists()]
    assert existing == [], (
        "Scope-creep guard. The following procurement automation "
        "files exist but Ticket 8 (discovery package) must not "
        "introduce them. If a later ticket legitimately added one, "
        "remove that path from forbidden_paths and add a separate "
        "regression test for the new file:\n"
        + "\n".join(existing)
    )

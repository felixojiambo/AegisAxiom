// Contract H — JS port of shared/contract_h.json.
// Single source of truth for Contract H field paths, labels, and types for the
// AEGISAXIOM MATCH module frontend. To rename or add a field, edit the JSON in
// backend/.ingestion/staging/match-main/shared/contract_h.json and re-sync
// this file. Both backend and frontend derive from the same schema.

// ── Dropdown option lists (spec §6.2.1 and §6.3.1) ─────────────────────────

export const ORDER_STATUS_OPTIONS = [
  "New",
  "Provisioned",
  "Pending Network Install",
  "Pending Order Acceptance",
  "Pending Permits",
  "Pending ROE",
  "Pending Site Survey",
  "Project Hold",
];

export const PRC_OPTIONS = ["New", "MACD", "Renewal", "Upgrade", "MAC"];

export const CONTRACT_TYPE_OPTIONS = ["New", "MAC", "MACD", "Renewal", "Upgrade"];
export const SERVICE_STATUS_OPTIONS = ["Active", "Pending Activation", "Suspended", "Cancelled"];

export const CONTRACT_TERM_OPTIONS = [
  "Month-to-Month",
  "12 months",
  "24 months",
  "36 months",
  "60 months",
];
export const RENEWAL_ACTION_OPTIONS = [
  "Auto-Renew",
  "Manual Review",
  "Notify Customer",
  "Do Not Renew",
];
export const RENEWAL_NOTICE_PERIOD_OPTIONS = ["30 days", "60 days", "90 days", "120 days"];
export const ORDER_SOURCE_OPTIONS = ["Direct", "Partner", "Channel", "Inbound", "Referral"];
export const ORDER_COMMISSION_STATUS_OPTIONS = [
  "Pending",
  "Calculated",
  "Paid",
  "Disputed",
  "Reconciled",
];

// ── Tab definitions (consumed by RowDetailDrawer and FieldGrid) ─────────────

export const TABS = [
  { key: "top_level",          label: "Top Level Setup" },
  { key: "order_details",      label: "Order Details" },
  { key: "order_attributes",   label: "Order Attributes" },
  { key: "milestones",         label: "Milestones" },
  { key: "service_attributes", label: "Service Attributes" },
];

// ── Field shape ──────────────────────────────────────────────────────────────
//
// Each entry:
//   path          — dot-path RELATIVE to row.output (e.g. "top_level.customer.name")
//   label         — human-readable label
//   type          — "string" | "number" | "date" | "select" | "boolean" | "money" |
//                   "percentage" | "textarea"
//   options?      — string[] for type="select" (with allowCustom flag)
//   allowCustom?  — true when the dropdown allows free-form typing
//   sectionHeader? — renders a section header row before this field
//   derivedOnly?  — true when this field has no source in the provider CSV
//   required?     — true for AppDirect-required fields

// Map dropdown_options string keys from the JSON → real arrays.
const DROPDOWN_OPTION_LISTS = {
  ORDER_STATUS_OPTIONS,
  PRC_OPTIONS,
  CONTRACT_TYPE_OPTIONS,
  SERVICE_STATUS_OPTIONS,
  CONTRACT_TERM_OPTIONS,
  RENEWAL_ACTION_OPTIONS,
  RENEWAL_NOTICE_PERIOD_OPTIONS,
  ORDER_SOURCE_OPTIONS,
  ORDER_COMMISSION_STATUS_OPTIONS,
};

// Raw Contract H field definitions — mirrors contract_h.json "fields" array.
// Path convention: paths here are RELATIVE to row.output (the "output." prefix
// from the JSON is stripped). Consumers reference e.g. "top_level.customer.name".
const RAW_FIELDS = [
  // ── top_level ──────────────────────────────────────────────────────────────
  { path: "top_level.advisor_company",              tab: "top_level",      label: "Advisor Company",                    required: true,  type: "string" },
  { path: "top_level.sales_rep",                    tab: "top_level",      label: "Sales Rep",                          derivedOnly: true, type: "string" },
  { path: "top_level.customer.name",                tab: "top_level",      label: "Customer",                           required: true,  type: "string" },
  { path: "top_level.customer.appdirect_customer_id", tab: "top_level",   label: "AppDirect Customer ID",               derivedOnly: true, type: "string" },
  { path: "top_level.location.street",              tab: "top_level",      label: "Street",                             type: "string" },
  { path: "top_level.location.city",                tab: "top_level",      label: "City",                               type: "string" },
  { path: "top_level.location.state",               tab: "top_level",      label: "State",                              type: "string" },
  { path: "top_level.location.zip",                 tab: "top_level",      label: "ZIP",                                type: "string" },

  // ── order_details ──────────────────────────────────────────────────────────
  { path: "order_details.product",                  tab: "order_details",  label: "Product",                            derivedOnly: true, type: "string" },
  { path: "order_details.provider",                 tab: "order_details",  label: "Provider",                           derivedOnly: true, type: "string" },
  { path: "order_details.order_status",             tab: "order_details",  label: "Order Status",                       derivedOnly: true, type: "select",
    options: ORDER_STATUS_OPTIONS, sectionHeader: "Order Details" },
  { path: "order_details.commission_ban",           tab: "order_details",  label: "Commission BAN",                     required: true,  type: "string" },
  { path: "order_details.provider_order_id",        tab: "order_details",  label: "Provider Order ID",                  type: "string" },
  { path: "order_details.requested_install",        tab: "order_details",  label: "Requested Install",                  derivedOnly: true, type: "date" },
  { path: "order_details.advisor_order_id",         tab: "order_details",  label: "Advisor Order ID",                   derivedOnly: true, type: "string" },
  { path: "order_details.contract_type",            tab: "order_details",  label: "Contract Type",                      derivedOnly: true, type: "select",
    options: CONTRACT_TYPE_OPTIONS, sectionHeader: "Contract and Pricing" },
  { path: "order_details.contract_term",            tab: "order_details",  label: "Contract Term",                      type: "select",
    options: CONTRACT_TERM_OPTIONS, allowCustom: true },
  { path: "order_details.contract_signed",          tab: "order_details",  label: "Contract Signed",                    type: "string" },
  { path: "order_details.net_new_revenue",          tab: "order_details",  label: "Net New Revenue",                    derivedOnly: true, type: "money" },
  { path: "order_details.contract_start",           tab: "order_details",  label: "Contract Start",                     required: true,  type: "string" },
  { path: "order_details.contract_end",             tab: "order_details",  label: "Contract End",                       derivedOnly: true, type: "string" },
  { path: "order_details.mrgmv",                    tab: "order_details",  label: "MRGMV",                              type: "money" },
  { path: "order_details.nrc",                      tab: "order_details",  label: "NRC",                                derivedOnly: true, type: "money" },

  // ── order_attributes ──────────────────────────────────────────────────────
  { path: "order_attributes.teamed_order",          tab: "order_attributes", label: "Teamed Order",                     derivedOnly: true, type: "boolean" },
  { path: "order_attributes.order_commission_status", tab: "order_attributes", label: "Order Commission Status",        derivedOnly: true, type: "select",
    options: ORDER_COMMISSION_STATUS_OPTIONS, allowCustom: true },
  { path: "order_attributes.expected_spiff_info",   tab: "order_attributes", label: "Expected SPIFF Info",              derivedOnly: true, type: "string" },
  { path: "order_attributes.order_description",     tab: "order_attributes", label: "Order Description",                type: "string" },
  { path: "order_attributes.renewal_revisit_date",  tab: "order_attributes", label: "Renewal Revisit Date",             derivedOnly: true, type: "date" },
  { path: "order_attributes.renewal_action",        tab: "order_attributes", label: "Renewal Action",                   derivedOnly: true, type: "select",
    options: RENEWAL_ACTION_OPTIONS, allowCustom: true },
  { path: "order_attributes.renewal_notice_period", tab: "order_attributes", label: "Renewal Notice Period",            derivedOnly: true, type: "select",
    options: RENEWAL_NOTICE_PERIOD_OPTIONS, allowCustom: true },
  { path: "order_attributes.renewal_language",      tab: "order_attributes", label: "Renewal Language",                 derivedOnly: true, type: "textarea" },
  { path: "order_attributes.counter_signed_contract_received", tab: "order_attributes", label: "Counter-Signed Contract Received", derivedOnly: true, type: "date" },
  { path: "order_attributes.order_source",          tab: "order_attributes", label: "Order Source",                     derivedOnly: true, type: "select",
    options: ORDER_SOURCE_OPTIONS, allowCustom: true },
  { path: "order_attributes.expected_1x_commission", tab: "order_attributes", label: "Expected 1X Commission",          derivedOnly: true, type: "money" },
  { path: "order_attributes.appdirect_billing_completed", tab: "order_attributes", label: "AppDirect Billing Completed", derivedOnly: true, type: "boolean" },
  { path: "order_attributes.appdirect_billing_entity", tab: "order_attributes", label: "AppDirect Billing Entity",      derivedOnly: true, type: "string" },
  { path: "order_attributes.billing_account_number", tab: "order_attributes", label: "Billing Account Number",          derivedOnly: true, type: "string" },
  { path: "order_attributes.level",                 tab: "order_attributes", label: "Level",                            derivedOnly: true, type: "string" },
  { path: "order_attributes.provider_revenue_category", tab: "order_attributes", label: "Provider Revenue Category",    type: "select",
    options: PRC_OPTIONS },
  { path: "order_attributes.commissionable_nrc",    tab: "order_attributes", label: "Commissionable NRC",               derivedOnly: true, type: "money" },
  { path: "order_attributes.original_order",        tab: "order_attributes", label: "Original Order",                   derivedOnly: true, type: "string" },
  { path: "order_attributes.renewed_revenue",       tab: "order_attributes", label: "Renewed Revenue",                  derivedOnly: true, type: "money" },
  { path: "order_attributes.hubspot_id",            tab: "order_attributes", label: "Hubspot ID",                       derivedOnly: true, type: "string" },
  { path: "order_attributes.provider_gross_pct_rate", tab: "order_attributes", label: "Provider Gross % Rate",          derivedOnly: true, type: "percentage" },
  { path: "order_attributes.special_comp",          tab: "order_attributes", label: "Special Comp",                     derivedOnly: true, type: "money" },
  { path: "order_attributes.public_note",           tab: "order_attributes", label: "Public Note (reviewer)",           derivedOnly: true, type: "textarea",
    sectionHeader: "Reviewer Notes" },

  // ── milestones ─────────────────────────────────────────────────────────────
  { path: "milestones.order_received",              tab: "milestones",     label: "Order Received",                     derivedOnly: true, type: "date" },
  { path: "milestones.sent_to_provider",            tab: "milestones",     label: "Sent to Provider",                   derivedOnly: true, type: "date" },
  { path: "milestones.test_turn_up_date",           tab: "milestones",     label: "Test & Turn Up Date",                derivedOnly: true, type: "date" },
  { path: "milestones.order_completion_date",       tab: "milestones",     label: "Order Completion Date",              type: "string" },
  { path: "milestones.commission_date",             tab: "milestones",     label: "Commission Date",                    derivedOnly: true, type: "date" },

  // ── service_attributes ────────────────────────────────────────────────────
  { path: "service_attributes.service_status",      tab: "service_attributes", label: "Service Status",                 derivedOnly: true, type: "select",
    options: SERVICE_STATUS_OPTIONS },
];

// Build FIELDS_BY_TAB from the raw list.
export const FIELDS_BY_TAB = RAW_FIELDS.reduce((acc, f) => {
  if (!acc[f.tab]) acc[f.tab] = [];
  acc[f.tab].push(f);
  return acc;
}, { top_level: [], order_details: [], order_attributes: [], milestones: [], service_attributes: [] });

// Flat list of all fields (for consumers that need the full set).
export const ALL_FIELDS = RAW_FIELDS;

// Convenience lookup by path.
export function findFieldByPath(path) {
  return RAW_FIELDS.find((f) => f.path === path) || null;
}

"""
Field registry registrations for QBO data lake virtual entities consumed
by the Financial Command Center.

These entities have no CRUD routes — they're read-only views of the QBO
data lake (env QBO_DB, default `aegisaxiom`) populated by an upstream sync
pipeline. Registering their fields here lets:
  - The AEGISAXIOM policy engine reason over QBO data (e.g., "block emails to
    customers in 91+ aging bucket").
  - The workflow builder offer their `synced` events as triggers.
  - Nova tools reference field names without hardcoding strings.

Import this module from `backend/server.py` startup so the registrations
fire on import.

Module-naming convention (N5 fix): all QBO virtual entities are registered
under module key "finance" for BOTH register_module_config() and
register_fields(). This ensures the policy-builder UI, workflow builder, and
Nova all see QBO entities under the Finance category. The entity_key arg
("qbo_chart_of_accounts", etc.) distinguishes each entity within the module.
Previously register_module_config used "qbo_<entity>" as the module key
while register_fields used "finance" — this mismatch caused the policy
builder to list 19 separate top-level modules and get_fields() to return
empty lists for QBO entities.
"""

from services.field_registry import register_module_config, register_module_events, register_fields

_QBO_LAKE_CATEGORY = "Finance (QBO Lake)"


# ---------------------------------------------------------------------------
# qbo_chart_of_accounts
# ---------------------------------------------------------------------------

def _register_qbo_chart_of_accounts():
    register_module_config("finance.qbo_chart_of_accounts", "chart_of_accounts", "qbo_account_id", _QBO_LAKE_CATEGORY)
    register_module_events("finance", "qbo_chart_of_accounts", ["synced"])
    register_fields("finance", "qbo_chart_of_accounts", [
        {"name": "qbo_account_id", "type": "string", "label": "QBO Account ID", "description": "[QBO LAKE] Primary key from QBO."},
        {"name": "name", "type": "string", "label": "Account Name", "description": "[QBO LAKE] Account name as shown in QBO."},
        {"name": "fully_qualified_name", "type": "string", "label": "Fully Qualified Name", "description": "[QBO LAKE] Full hierarchical name including parent accounts (e.g. 'Income:Services:Consulting')."},
        {"name": "account_type", "type": "string", "label": "Account Type", "description": "[QBO LAKE] QBO account type (e.g. Income, Expense, Bank, AccountsReceivable)."},
        {"name": "classification", "type": "string", "label": "Classification", "description": "[QBO LAKE] High-level classification (Asset, Liability, Equity, Revenue, Expense)."},
        {"name": "active", "type": "boolean", "label": "Active", "description": "[QBO LAKE] Whether the account is currently active in QBO."},
        {"name": "currency_ref", "type": "object", "label": "Currency", "description": "[QBO LAKE] Currency reference object from QBO ({value, name})."},
        {"name": "parent_ref", "type": "object", "label": "Parent Account", "description": "[QBO LAKE] Reference to parent account ({value, name}) for sub-accounts; null for top-level."},
    ])


# ---------------------------------------------------------------------------
# qbo_general_ledger
# ---------------------------------------------------------------------------

def _register_qbo_general_ledger():
    register_module_config("finance.qbo_general_ledger", "general_ledger", "_id", _QBO_LAKE_CATEGORY)
    register_module_events("finance", "qbo_general_ledger", ["synced"])
    register_fields("finance", "qbo_general_ledger", [
        {"name": "tx_date", "type": "date", "label": "Transaction Date", "description": "[QBO LAKE] Date the transaction was posted (YYYY-MM-DD)."},
        {"name": "account_name", "type": "string", "label": "Account Name", "description": "[QBO LAKE] Name of the GL account this line is posted to."},
        {"name": "account_id", "type": "string", "label": "Account ID", "description": "[QBO LAKE] QBO internal account ID for the posted account."},
        {"name": "txn_type", "type": "string", "label": "Transaction Type", "description": "[QBO LAKE] QBO transaction type (e.g. Invoice, Bill, JournalEntry, Payment)."},
        {"name": "doc_num", "type": "string", "label": "Document Number", "description": "[QBO LAKE] Transaction document/reference number (e.g. invoice number, bill number)."},
        {"name": "name", "type": "string", "label": "Name", "description": "[QBO LAKE] Customer or vendor name associated with the transaction."},
        {"name": "memo", "type": "string", "label": "Memo", "description": "[QBO LAKE] Transaction memo or description."},
        {"name": "basis", "type": "string", "label": "Accounting Basis", "description": "[QBO LAKE] Accounting basis for this entry: 'accrual' or 'cash'."},
        {"name": "amount", "type": "number", "label": "Amount", "description": "[QBO LAKE] Net signed amount of this GL line (positive = credit side, negative = debit side depending on account type)."},
        {"name": "debit", "type": "number", "label": "Debit", "description": "[QBO LAKE] Debit amount for this line (0 if credit entry)."},
        {"name": "credit", "type": "number", "label": "Credit", "description": "[QBO LAKE] Credit amount for this line (0 if debit entry)."},
        # Computed join fields — derived at query time from ar_invoices / ap_bills raw_data
        {
            "name": "deal_id",
            "type": "string",
            "label": "Deal ID",
            "computed": True,
            "description": "[QBO LAKE] Computed — joined from ar_invoices.raw_data.CustomField[Name startswith 'sales'].StringValue. ~19-digit numeric string identifying the associated deal.",
        },
        {
            "name": "class_name",
            "type": "string",
            "label": "Class",
            "computed": True,
            "description": "[QBO LAKE] Computed — joined from ar_invoices.raw_data.Line[].SalesItemLineDetail.ClassRef.name (invoices) or ap_bills.raw_data.Line[].AccountBasedExpenseLineDetail.ClassRef.name (bills); first non-null line wins.",
        },
        {
            "name": "location",
            "type": "string",
            "label": "Location",
            "computed": True,
            "description": "[QBO LAKE] Computed — joined from ar_invoices.raw_data.DepartmentRef.name or ap_bills.raw_data.DepartmentRef.name (QBO Location/Department field). Sparse: most rows are null until the upstream sync pipeline captures DepartmentRef.",
        },
    ])


# ---------------------------------------------------------------------------
# qbo_ar_invoice
# ---------------------------------------------------------------------------

def _register_qbo_ar_invoice():
    register_module_config("finance.qbo_ar_invoice", "ar_invoices", "qbo_invoice_id", _QBO_LAKE_CATEGORY)
    register_module_events("finance", "qbo_ar_invoice", ["synced"])
    register_fields("finance", "qbo_ar_invoice", [
        {"name": "qbo_invoice_id", "type": "string", "label": "QBO Invoice ID", "description": "[QBO LAKE] Primary key from QBO (numeric string, e.g. '10156')."},
        {"name": "doc_number", "type": "string", "label": "Document Number", "description": "[QBO LAKE] Invoice document number as shown in QBO."},
        {"name": "txn_date", "type": "date", "label": "Invoice Date", "description": "[QBO LAKE] Date the invoice was issued (YYYY-MM-DD)."},
        {"name": "due_date", "type": "date", "label": "Due Date", "description": "[QBO LAKE] Payment due date (YYYY-MM-DD)."},
        {"name": "balance", "type": "number", "label": "Outstanding Balance", "description": "[QBO LAKE] Remaining unpaid balance on the invoice."},
        {"name": "total_amount", "type": "number", "label": "Total Amount", "description": "[QBO LAKE] Total invoice amount before any payments applied."},
        {"name": "customer_ref", "type": "object", "label": "Customer", "description": "[QBO LAKE] QBO customer reference ({value: id, name: display_name})."},
        {"name": "raw_data", "type": "object", "label": "Raw QBO Payload", "description": "[QBO LAKE] Full QBO Invoice payload from the sync pipeline. Source of truth for line items, custom fields (Deal ID), Class, and Location joins."},
    ])


# ---------------------------------------------------------------------------
# qbo_ap_bill
# ---------------------------------------------------------------------------

def _register_qbo_ap_bill():
    register_module_config("finance.qbo_ap_bill", "ap_bills", "qbo_bill_id", _QBO_LAKE_CATEGORY)
    register_module_events("finance", "qbo_ap_bill", ["synced"])
    register_fields("finance", "qbo_ap_bill", [
        {"name": "qbo_bill_id", "type": "string", "label": "QBO Bill ID", "description": "[QBO LAKE] Primary key from QBO."},
        {"name": "doc_number", "type": "string", "label": "Document Number", "description": "[QBO LAKE] Bill reference/document number."},
        {"name": "txn_date", "type": "date", "label": "Bill Date", "description": "[QBO LAKE] Date the bill was created (YYYY-MM-DD)."},
        {"name": "due_date", "type": "date", "label": "Due Date", "description": "[QBO LAKE] Payment due date (YYYY-MM-DD)."},
        {"name": "balance", "type": "number", "label": "Outstanding Balance", "description": "[QBO LAKE] Remaining unpaid balance on the bill."},
        {"name": "total_amount", "type": "number", "label": "Total Amount", "description": "[QBO LAKE] Total bill amount."},
        {"name": "vendor_ref", "type": "object", "label": "Vendor", "description": "[QBO LAKE] QBO vendor reference ({value: id, name: display_name})."},
        {"name": "raw_data", "type": "object", "label": "Raw QBO Payload", "description": "[QBO LAKE] Full QBO Bill payload. Source for line-level Class and Location joins."},
    ])


# ---------------------------------------------------------------------------
# qbo_account  (bank / credit-card accounts)
# ---------------------------------------------------------------------------

def _register_qbo_account():
    register_module_config("finance.qbo_account", "accounts", "account_id", _QBO_LAKE_CATEGORY)
    register_module_events("finance", "qbo_account", ["synced"])
    register_fields("finance", "qbo_account", [
        {"name": "account_id", "type": "string", "label": "Account ID", "description": "[QBO LAKE] Unique identifier for the bank or credit-card account."},
        {"name": "name", "type": "string", "label": "Account Name", "description": "[QBO LAKE] Human-readable account name (e.g. 'Chase Checking')."},
        {"name": "type", "type": "string", "label": "Account Type", "description": "[QBO LAKE] Account type (e.g. Bank, CreditCard)."},
        {"name": "currency", "type": "string", "label": "Currency", "description": "[QBO LAKE] ISO 4217 currency code (e.g. 'USD')."},
    ])


# ---------------------------------------------------------------------------
# qbo_contact  (customer + vendor contacts)
# ---------------------------------------------------------------------------

def _register_qbo_contact():
    register_module_config("finance.qbo_contact", "contacts", "id", _QBO_LAKE_CATEGORY)
    register_module_events("finance", "qbo_contact", ["synced"])
    register_fields("finance", "qbo_contact", [
        {"name": "id", "type": "string", "label": "Contact ID", "description": "[QBO LAKE] Unique QBO contact identifier."},
        {"name": "display_name", "type": "string", "label": "Display Name", "description": "[QBO LAKE] Full display name of the customer or vendor."},
        {"name": "email", "type": "string", "label": "Email", "description": "[QBO LAKE] Primary email address."},
        {"name": "phone", "type": "string", "label": "Phone", "description": "[QBO LAKE] Primary phone number."},
        {"name": "type", "type": "string", "label": "Contact Type", "description": "[QBO LAKE] Contact classification: 'customer' or 'vendor'."},
    ])


# ---------------------------------------------------------------------------
# qbo_report_pnl  (current-period Profit & Loss)
# ---------------------------------------------------------------------------

def _register_qbo_report_pnl():
    register_module_config("finance.qbo_report_pnl", "reports_pnl", "period", _QBO_LAKE_CATEGORY)
    register_module_events("finance", "qbo_report_pnl", ["synced"])
    register_fields("finance", "qbo_report_pnl", [
        {"name": "period", "type": "string", "label": "Period", "description": "[QBO LAKE] Reporting period identifier (e.g. 'current', 'YYYY-MM')."},
        {"name": "line_items", "type": "array", "label": "Line Items", "description": "[QBO LAKE] Hierarchical list of P&L rows — each row has account, amount, and indent level."},
        {"name": "summary", "type": "object", "label": "Summary", "description": "[QBO LAKE] Pre-aggregated P&L summary totals (total_income, total_expenses, net_income, etc.)."},
    ])


# ---------------------------------------------------------------------------
# qbo_report_pnl_prior  (prior-year Profit & Loss)
# ---------------------------------------------------------------------------

def _register_qbo_report_pnl_prior():
    register_module_config("finance.qbo_report_pnl_prior", "reports_pnl_prior_year", "period", _QBO_LAKE_CATEGORY)
    register_module_events("finance", "qbo_report_pnl_prior", ["synced"])
    register_fields("finance", "qbo_report_pnl_prior", [
        {"name": "period", "type": "string", "label": "Period", "description": "[QBO LAKE] Prior-year reporting period identifier."},
        {"name": "line_items", "type": "array", "label": "Line Items", "description": "[QBO LAKE] Hierarchical list of prior-year P&L rows (same shape as qbo_report_pnl)."},
        {"name": "summary", "type": "object", "label": "Summary", "description": "[QBO LAKE] Pre-aggregated prior-year P&L summary totals for YoY comparison."},
    ])


# ---------------------------------------------------------------------------
# qbo_report_balance_sheet
# ---------------------------------------------------------------------------

def _register_qbo_report_balance_sheet():
    register_module_config("finance.qbo_report_balance_sheet", "reports_balance_sheet", "_id", _QBO_LAKE_CATEGORY)
    register_module_events("finance", "qbo_report_balance_sheet", ["synced"])
    register_fields("finance", "qbo_report_balance_sheet", [
        {"name": "as_of_date", "type": "date", "label": "As-of Date", "description": "[QBO LAKE] Balance sheet date (YYYY-MM-DD)."},
        {"name": "line_items", "type": "array", "label": "Line Items", "description": "[QBO LAKE] Hierarchical list of balance sheet rows (assets, liabilities, equity) with account name, amount, and indent level."},
        {"name": "total_assets", "type": "number", "label": "Total Assets", "description": "[QBO LAKE] Aggregate total assets from the balance sheet summary."},
        {"name": "total_liabilities", "type": "number", "label": "Total Liabilities", "description": "[QBO LAKE] Aggregate total liabilities."},
        {"name": "total_equity", "type": "number", "label": "Total Equity", "description": "[QBO LAKE] Aggregate total equity (assets minus liabilities)."},
    ])


# ---------------------------------------------------------------------------
# qbo_report_cash_flow
# ---------------------------------------------------------------------------

def _register_qbo_report_cash_flow():
    register_module_config("finance.qbo_report_cash_flow", "reports_cash_flow", "_id", _QBO_LAKE_CATEGORY)
    register_module_events("finance", "qbo_report_cash_flow", ["synced"])
    register_fields("finance", "qbo_report_cash_flow", [
        {"name": "period", "type": "string", "label": "Period", "description": "[QBO LAKE] Reporting period for the cash flow statement."},
        {"name": "line_items", "type": "array", "label": "Line Items", "description": "[QBO LAKE] Cash flow line items grouped by operating, investing, and financing activities."},
        {"name": "net_cash_operating", "type": "number", "label": "Net Cash from Operations", "description": "[QBO LAKE] Net cash generated by operating activities."},
        {"name": "net_cash_investing", "type": "number", "label": "Net Cash from Investing", "description": "[QBO LAKE] Net cash used in investing activities."},
        {"name": "net_cash_financing", "type": "number", "label": "Net Cash from Financing", "description": "[QBO LAKE] Net cash from financing activities."},
        {"name": "net_change", "type": "number", "label": "Net Change in Cash", "description": "[QBO LAKE] Total net change in cash for the period."},
    ])


# ---------------------------------------------------------------------------
# qbo_report_trial_balance
# ---------------------------------------------------------------------------

def _register_qbo_report_trial_balance():
    register_module_config("finance.qbo_report_trial_balance", "reports_trial_balance", "_id", _QBO_LAKE_CATEGORY)
    register_module_events("finance", "qbo_report_trial_balance", ["synced"])
    register_fields("finance", "qbo_report_trial_balance", [
        {"name": "as_of_date", "type": "date", "label": "As-of Date", "description": "[QBO LAKE] Trial balance date."},
        {"name": "line_items", "type": "array", "label": "Line Items", "description": "[QBO LAKE] List of trial balance rows — each has account_name, debit, and credit columns."},
        {"name": "total_debits", "type": "number", "label": "Total Debits", "description": "[QBO LAKE] Sum of all debit balances."},
        {"name": "total_credits", "type": "number", "label": "Total Credits", "description": "[QBO LAKE] Sum of all credit balances."},
    ])


# ---------------------------------------------------------------------------
# qbo_report_ar_aging  (summary aging — per customer buckets)
# ---------------------------------------------------------------------------

def _register_qbo_report_ar_aging():
    register_module_config("finance.qbo_report_ar_aging", "reports_ar_aging", "_id", _QBO_LAKE_CATEGORY)
    register_module_events("finance", "qbo_report_ar_aging", ["synced"])
    register_fields("finance", "qbo_report_ar_aging", [
        {"name": "as_of_date", "type": "date", "label": "As-of Date", "description": "[QBO LAKE] Date the AR aging report was generated."},
        {"name": "rows", "type": "array", "label": "Customer Aging Rows", "description": "[QBO LAKE] List of per-customer aging rows, each with customer_name, current, bucket_1_30, bucket_31_60, bucket_61_90, bucket_91_plus, total."},
        {"name": "total_current", "type": "number", "label": "Total Current", "description": "[QBO LAKE] Sum of all current (0-30 day) AR balances."},
        {"name": "total_1_30", "type": "number", "label": "Total 1-30 Days", "description": "[QBO LAKE] Sum of all 1-30 day overdue balances."},
        {"name": "total_31_60", "type": "number", "label": "Total 31-60 Days", "description": "[QBO LAKE] Sum of all 31-60 day overdue balances."},
        {"name": "total_61_90", "type": "number", "label": "Total 61-90 Days", "description": "[QBO LAKE] Sum of all 61-90 day overdue balances."},
        {"name": "total_91_plus", "type": "number", "label": "Total 91+ Days", "description": "[QBO LAKE] Sum of all 91+ day overdue balances. Key policy signal for escalations."},
        {"name": "grand_total", "type": "number", "label": "Grand Total AR", "description": "[QBO LAKE] Total outstanding AR across all customers and buckets."},
    ])


# ---------------------------------------------------------------------------
# qbo_report_ap_aging  (summary aging — per vendor buckets)
# ---------------------------------------------------------------------------

def _register_qbo_report_ap_aging():
    register_module_config("finance.qbo_report_ap_aging", "reports_ap_aging", "_id", _QBO_LAKE_CATEGORY)
    register_module_events("finance", "qbo_report_ap_aging", ["synced"])
    register_fields("finance", "qbo_report_ap_aging", [
        {"name": "as_of_date", "type": "date", "label": "As-of Date", "description": "[QBO LAKE] Date the AP aging report was generated."},
        {"name": "rows", "type": "array", "label": "Vendor Aging Rows", "description": "[QBO LAKE] List of per-vendor aging rows with the same bucket structure as AR aging."},
        {"name": "total_current", "type": "number", "label": "Total Current", "description": "[QBO LAKE] Sum of current AP balances."},
        {"name": "total_1_30", "type": "number", "label": "Total 1-30 Days", "description": "[QBO LAKE] Sum of 1-30 day overdue AP balances."},
        {"name": "total_31_60", "type": "number", "label": "Total 31-60 Days", "description": "[QBO LAKE] Sum of 31-60 day overdue AP balances."},
        {"name": "total_61_90", "type": "number", "label": "Total 61-90 Days", "description": "[QBO LAKE] Sum of 61-90 day overdue AP balances."},
        {"name": "total_91_plus", "type": "number", "label": "Total 91+ Days", "description": "[QBO LAKE] Sum of 91+ day overdue AP balances."},
        {"name": "grand_total", "type": "number", "label": "Grand Total AP", "description": "[QBO LAKE] Total outstanding AP across all vendors and buckets."},
    ])


# ---------------------------------------------------------------------------
# qbo_report_ar_aging_detail  (per-invoice open balances)
# ---------------------------------------------------------------------------

def _register_qbo_report_ar_aging_detail():
    register_module_config("finance.qbo_report_ar_aging_detail", "reports_ar_aging_detail", "_id", _QBO_LAKE_CATEGORY)
    register_module_events("finance", "qbo_report_ar_aging_detail", ["synced"])
    register_fields("finance", "qbo_report_ar_aging_detail", [
        {"name": "as_of_date", "type": "date", "label": "As-of Date", "description": "[QBO LAKE] Date the detail aging snapshot was taken."},
        {"name": "rows", "type": "array", "label": "Invoice Rows", "description": "[QBO LAKE] List of open invoice rows — each has customer_name, invoice_num, txn_date, due_date, amount, open_balance, aging_bucket."},
        {"name": "customer_id", "type": "string", "label": "Customer ID", "description": "[QBO LAKE] QBO customer ID filter key used when loading per-customer detail."},
        {"name": "total_open", "type": "number", "label": "Total Open Balance", "description": "[QBO LAKE] Sum of all open invoice balances in the snapshot."},
    ])


# ---------------------------------------------------------------------------
# qbo_report_ap_aging_detail  (per-bill open balances)
# ---------------------------------------------------------------------------

def _register_qbo_report_ap_aging_detail():
    register_module_config("finance.qbo_report_ap_aging_detail", "reports_ap_aging_detail", "_id", _QBO_LAKE_CATEGORY)
    register_module_events("finance", "qbo_report_ap_aging_detail", ["synced"])
    register_fields("finance", "qbo_report_ap_aging_detail", [
        {"name": "as_of_date", "type": "date", "label": "As-of Date", "description": "[QBO LAKE] Date the detail aging snapshot was taken."},
        {"name": "rows", "type": "array", "label": "Bill Rows", "description": "[QBO LAKE] List of open bill rows — each has vendor_name, bill_num, txn_date, due_date, amount, open_balance, aging_bucket."},
        {"name": "vendor_id", "type": "string", "label": "Vendor ID", "description": "[QBO LAKE] QBO vendor ID filter key used when loading per-vendor detail."},
        {"name": "total_open", "type": "number", "label": "Total Open Balance", "description": "[QBO LAKE] Sum of all open bill balances in the snapshot."},
    ])


# ---------------------------------------------------------------------------
# qbo_report_customer_balance  (open balance per customer)
# ---------------------------------------------------------------------------

def _register_qbo_report_customer_balance():
    register_module_config("finance.qbo_report_customer_balance", "reports_customer_balance", "_id", _QBO_LAKE_CATEGORY)
    register_module_events("finance", "qbo_report_customer_balance", ["synced"])
    register_fields("finance", "qbo_report_customer_balance", [
        {"name": "as_of_date", "type": "date", "label": "As-of Date", "description": "[QBO LAKE] Date the customer balance snapshot was taken."},
        {"name": "rows", "type": "array", "label": "Customer Balance Rows", "description": "[QBO LAKE] List of rows with customer_name, customer_id, and open_balance."},
        {"name": "total_balance", "type": "number", "label": "Total Balance", "description": "[QBO LAKE] Aggregate open balance across all customers."},
    ])


# ---------------------------------------------------------------------------
# qbo_report_vendor_balance  (open balance per vendor)
# ---------------------------------------------------------------------------

def _register_qbo_report_vendor_balance():
    register_module_config("finance.qbo_report_vendor_balance", "reports_vendor_balance", "_id", _QBO_LAKE_CATEGORY)
    register_module_events("finance", "qbo_report_vendor_balance", ["synced"])
    register_fields("finance", "qbo_report_vendor_balance", [
        {"name": "as_of_date", "type": "date", "label": "As-of Date", "description": "[QBO LAKE] Date the vendor balance snapshot was taken."},
        {"name": "rows", "type": "array", "label": "Vendor Balance Rows", "description": "[QBO LAKE] List of rows with vendor_name, vendor_id, and open_balance."},
        {"name": "total_balance", "type": "number", "label": "Total Balance", "description": "[QBO LAKE] Aggregate open balance across all vendors."},
    ])


# ---------------------------------------------------------------------------
# qbo_report_customer_income  (per-customer YTD income)
# ---------------------------------------------------------------------------

def _register_qbo_report_customer_income():
    register_module_config("finance.qbo_report_customer_income", "reports_customer_income", "_id", _QBO_LAKE_CATEGORY)
    register_module_events("finance", "qbo_report_customer_income", ["synced"])
    register_fields("finance", "qbo_report_customer_income", [
        {"name": "period", "type": "string", "label": "Period", "description": "[QBO LAKE] YTD period identifier for the report."},
        {"name": "rows", "type": "array", "label": "Customer Income Rows", "description": "[QBO LAKE] List of rows with customer_name, customer_id, income, and expenses columns."},
        {"name": "total_income", "type": "number", "label": "Total Income", "description": "[QBO LAKE] Aggregate YTD income across all customers."},
    ])


# ---------------------------------------------------------------------------
# qbo_report_vendor_expenses  (per-vendor YTD spend)
# ---------------------------------------------------------------------------

def _register_qbo_report_vendor_expenses():
    register_module_config("finance.qbo_report_vendor_expenses", "reports_vendor_expenses", "_id", _QBO_LAKE_CATEGORY)
    register_module_events("finance", "qbo_report_vendor_expenses", ["synced"])
    register_fields("finance", "qbo_report_vendor_expenses", [
        {"name": "period", "type": "string", "label": "Period", "description": "[QBO LAKE] YTD period identifier for the report."},
        {"name": "rows", "type": "array", "label": "Vendor Expense Rows", "description": "[QBO LAKE] List of rows with vendor_name, vendor_id, and total_spend columns."},
        {"name": "total_spend", "type": "number", "label": "Total Spend", "description": "[QBO LAKE] Aggregate YTD spend across all vendors."},
    ])


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def register_all_qbo_virtual_entities():
    """Register all 18 QBO virtual entities in the field registry."""
    _register_qbo_chart_of_accounts()
    _register_qbo_general_ledger()
    _register_qbo_ar_invoice()
    _register_qbo_ap_bill()
    _register_qbo_account()
    _register_qbo_contact()
    _register_qbo_report_pnl()
    _register_qbo_report_pnl_prior()
    _register_qbo_report_balance_sheet()
    _register_qbo_report_cash_flow()
    _register_qbo_report_trial_balance()
    _register_qbo_report_ar_aging()
    _register_qbo_report_ap_aging()
    _register_qbo_report_ar_aging_detail()
    _register_qbo_report_ap_aging_detail()
    _register_qbo_report_customer_balance()
    _register_qbo_report_vendor_balance()
    _register_qbo_report_customer_income()
    _register_qbo_report_vendor_expenses()


# Import-time registration so it runs on server startup
register_all_qbo_virtual_entities()

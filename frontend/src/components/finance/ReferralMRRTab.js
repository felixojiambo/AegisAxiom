import React, { useState, useEffect } from 'react';
import {
  Upload,
  Save,
  X,
  Check,
  AlertCircle,
  Edit2,
  Trash2,
  Calendar,
} from 'lucide-react';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import API_URL from '@/lib/apiConfig';
import ConfirmModal from '@/components/shared/ConfirmModal';

const API = API_URL;
const REFERRAL_BASE = `${API_URL}/api/finance/referral-mrr`;

export default function ReferralMRRTab() {
  const [dealMappings, setDealMappings] = useState([]);
  const [headerTemplates, setHeaderTemplates] = useState([]);
  const [selectedTemplate, setSelectedTemplate] = useState(null);
  const [uploadedFile, setUploadedFile] = useState(null);
  const [parsedData, setParsedData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [deals, setDeals] = useState([]);

  const [showMappingModal, setShowMappingModal] = useState(false);
  const [csvHeaders, setCsvHeaders] = useState([]);
  const [headerMappings, setHeaderMappings] = useState({});
  const [linkingColumn, setLinkingColumn] = useState('');
  const [amountColumn, setAmountColumn] = useState('');
  const [templateName, setTemplateName] = useState('');

  const currentYear = new Date().getFullYear();
  const currentMonth = new Date().getMonth() + 1;
  const [selectedMonth, setSelectedMonth] = useState(currentMonth);
  const [selectedYear, setSelectedYear] = useState(currentYear);
  const [commissionPeriod, setCommissionPeriod] = useState(
    `${currentYear}-${String(currentMonth).padStart(2, '0')}`,
  );

  const [showDealMappingModal, setShowDealMappingModal] = useState(false);
  const [selectedMapping, setSelectedMapping] = useState(null);
  const [mappingForm, setMappingForm] = useState({ deal_id: '', account_number: '' });
  const [dealSearchQuery, setDealSearchQuery] = useState('');
  const [showDealDropdown, setShowDealDropdown] = useState(true);
  const [editMode, setEditMode] = useState(false);

  // ConfirmModal for delete
  const [confirmState, setConfirmState] = useState({ open: false, mapping: null });

  useEffect(() => {
    setCommissionPeriod(`${selectedYear}-${String(selectedMonth).padStart(2, '0')}`);
  }, [selectedMonth, selectedYear]);

  useEffect(() => {
    loadDealMappings();
    loadHeaderTemplates();
    loadDeals();
  }, []);

  const loadDealMappings = async () => {
    try {
      const res = await api.get(`${REFERRAL_BASE}/account-mappings`);
      if (res.ok) {
        const data = await res.json();
        setDealMappings(data.mappings || []);
      }
    } catch (err) {
      console.error('Failed to load deal mappings:', err);
    }
  };

  const loadHeaderTemplates = async () => {
    try {
      const res = await api.get(`${REFERRAL_BASE}/header-templates`);
      if (res.ok) {
        const data = await res.json();
        setHeaderTemplates(data.templates || []);
      }
    } catch (err) {
      console.error('Failed to load templates:', err);
    }
  };

  const loadDeals = async () => {
    try {
      const res = await api.get(`${API}/api/deals`);
      if (res.ok) {
        const data = await res.json();
        setDeals(Array.isArray(data) ? data : data.items || []);
      }
    } catch (err) {
      console.error('Failed to load deals:', err);
    }
  };

  const handleFileUpload = async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    setUploadedFile(null);
    setCsvHeaders([]);
    setHeaderMappings({});
    setLinkingColumn('');
    setAmountColumn('');
    setTemplateName('');
    setSelectedTemplate(null);
    setUploadedFile(file);

    const reader = new FileReader();
    reader.onload = (event) => {
      const text = event.target.result;
      const firstLine = text.split('\n')[0];
      const headers = firstLine.split(',').map((h) => h.trim().replace(/"/g, ''));
      setCsvHeaders(headers);
      setShowMappingModal(true);
    };
    reader.readAsText(file);
    e.target.value = '';
  };

  const handleParseCSV = async () => {
    if (!uploadedFile) { toast.error('Please upload a file first'); return; }
    if (!linkingColumn) { toast.error('Please select the account identifier column'); return; }
    if (!amountColumn) { toast.error('Please select the commission amount column'); return; }

    setLoading(true);
    try {
      const formData = new FormData();
      formData.append('file', uploadedFile);
      formData.append('mappings', JSON.stringify(headerMappings));
      formData.append('linking_column', linkingColumn);
      formData.append('amount_column', amountColumn);
      formData.append('period', commissionPeriod);

      const res = await fetch(`${REFERRAL_BASE}/parse-csv`, {
        method: 'POST',
        credentials: 'include',
        body: formData,
      });

      if (res.ok) {
        const data = await res.json();
        setParsedData(data);
        setShowMappingModal(false);
        if (data.total_rows === 0) {
          toast.error('No data rows found in CSV. Check your file format.');
        } else {
          toast.success(`Parsed ${data.total_rows} rows: ${data.matched.length} matched, ${data.unmatched.length} need mapping`);
        }
      } else {
        const err = await res.json();
        toast.error(err.detail || 'Failed to parse CSV');
      }
    } catch (err) {
      toast.error('Failed to parse CSV: ' + err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleSaveTemplate = async () => {
    if (!templateName.trim()) { toast.error('Please enter a template name'); return; }
    if (!linkingColumn) { toast.error('Please select the linking column first'); return; }
    if (!amountColumn) { toast.error('Please select the amount column first'); return; }

    try {
      const res = await api.post(`${REFERRAL_BASE}/header-templates`, {
        template_name: templateName,
        mappings: headerMappings,
        linking_column: linkingColumn,
        amount_column: amountColumn,
      });
      if (res.ok) {
        toast.success('Template saved!');
        loadHeaderTemplates();
        setTemplateName('');
      } else {
        const err = await res.json();
        toast.error(err.detail || 'Failed to save template');
      }
    } catch (err) {
      toast.error('Failed to save template');
    }
  };

  const handleLoadTemplate = (template) => {
    setHeaderMappings(template.mappings);
    setLinkingColumn(template.linking_column || '');
    setAmountColumn(template.amount_column || '');
    setSelectedTemplate(template);
    toast.success(`Loaded template: ${template.template_name}`);
  };

  const handleSaveAccountMapping = async () => {
    if (!mappingForm.deal_id) { toast.error('Please select a deal'); return; }
    try {
      const deal = deals.find((d) => d.deal_id === mappingForm.deal_id);
      const res = await api.post(`${REFERRAL_BASE}/account-mappings`, {
        account_number: selectedMapping.account_number,
        deal_id: mappingForm.deal_id,
        deal_name: deal?.name || '',
        partner_name: selectedMapping.partner_name,
        commission_plan: null,
        linking_field_name: selectedMapping.linking_field_name || 'Account Number',
      });
      if (res.ok) {
        toast.success("Account mapped to deal!");
        loadDealMappings();
        setShowDealMappingModal(false);
        setDealSearchQuery('');
        setShowDealDropdown(true);
        if (parsedData && uploadedFile) await handleParseCSV();
      } else {
        const err = await res.json();
        toast.error(err.detail || 'Failed to save mapping');
      }
    } catch (err) {
      toast.error('Failed to save deal mapping');
    }
  };

  const handleEditMapping = (mapping) => {
    setSelectedMapping(mapping);
    setEditMode(true);
    setMappingForm({ account_number: mapping.account_number, deal_id: mapping.deal_id });
    setDealSearchQuery(mapping.deal_name || '');
    setShowDealMappingModal(true);
  };

  const handleDeleteMapping = (mapping) => {
    setConfirmState({ open: true, mapping });
  };

  const confirmDeleteMapping = async () => {
    const mapping = confirmState.mapping;
    setConfirmState({ open: false, mapping: null });
    try {
      const res = await api.delete(
        `${REFERRAL_BASE}/account-mappings/${encodeURIComponent(mapping.account_number)}`,
      );
      if (res.ok) {
        toast.success('Deal mapping deleted');
        loadDealMappings();
        if (parsedData && uploadedFile) await handleParseCSV();
      } else {
        const err = await res.json();
        toast.error(err.detail || 'Failed to delete mapping');
      }
    } catch (err) {
      toast.error('Failed to delete deal mapping');
    }
  };

  const handleSaveEditedMapping = async () => {
    if (!mappingForm.deal_id) { toast.error('Please select a deal'); return; }
    try {
      const deal = deals.find((d) => d.deal_id === mappingForm.deal_id);
      const res = await api.post(`${REFERRAL_BASE}/account-mappings`, {
        account_number: mappingForm.account_number,
        deal_id: mappingForm.deal_id,
        deal_name: deal?.name || '',
        partner_name: selectedMapping?.partner_name || '',
        commission_plan: null,
        linking_field_name: selectedMapping?.linking_field_name || 'Account Number',
      });
      if (res.ok) {
        toast.success('Deal mapping updated');
        loadDealMappings();
        setShowDealMappingModal(false);
        setEditMode(false);
        setDealSearchQuery('');
        setShowDealDropdown(true);
        if (parsedData && uploadedFile) await handleParseCSV();
      } else {
        const err = await res.json();
        toast.error(err.detail || 'Failed to update mapping');
      }
    } catch (err) {
      toast.error('Failed to update deal mapping');
    }
  };

  const closeMappingModal = () => {
    setShowMappingModal(false);
    setUploadedFile(null);
    setCsvHeaders([]);
    setHeaderMappings({});
    setLinkingColumn('');
    setAmountColumn('');
  };

  const closeDealModal = () => {
    setShowDealMappingModal(false);
    setDealSearchQuery('');
    setShowDealDropdown(true);
    setEditMode(false);
  };

  const filteredDeals = deals.filter((deal) => {
    if (!dealSearchQuery) return true;
    const q = dealSearchQuery.toLowerCase();
    return deal.name.toLowerCase().includes(q) || deal.account_name?.toLowerCase().includes(q);
  });

  return (
    <div className="space-y-6" data-testid="page-referral-mrr">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <p className="text-[11px] font-semibold tracking-wider uppercase text-aegisaxiom-text-muted mb-0.5">LEGACY</p>
          <h2 className="page-title">Referral MRR</h2>
          <p className="text-sm text-aegisaxiom-text-secondary mt-1">
            Import partner commission data and map account numbers to deals
          </p>
        </div>
        <label className="btn-primary flex items-center gap-2 text-[13px] cursor-pointer">
          <Upload className="h-4 w-4" />
          Upload CSV
          <input type="file" accept=".csv,.xlsx" className="hidden" onChange={handleFileUpload} />
        </label>
      </div>

      {/* Unmatched Accounts */}
      {parsedData && parsedData.unmatched.length > 0 && (
        <div className="glass-card p-4 border-l-4 border-amber-500">
          <div className="flex items-start gap-3">
            <AlertCircle className="h-5 w-5 text-amber-400 mt-0.5 shrink-0" />
            <div className="flex-1">
              <h3 className="text-sm font-semibold text-aegisaxiom-text mb-1">Action Items</h3>
              <p className="text-[13px] text-aegisaxiom-text-secondary mb-3">
                {parsedData.unmatched.length} account(s) need to be mapped to deals
              </p>
              <div className="space-y-2">
                {parsedData.unmatched.map((row, idx) => (
                  <div key={idx} className="bg-aegisaxiom-elevated rounded-lg p-3 flex items-center justify-between gap-3">
                    <div>
                      <p className="text-[13px] font-medium text-aegisaxiom-text">
                        {row.linking_field_name || 'Account'}: {row.account_number}
                      </p>
                      <p className="text-[12px] text-aegisaxiom-text-secondary">
                        Partner: {row.partner_name || 'Unknown'} · Amount: ${row.amount.toFixed(2)}
                      </p>
                    </div>
                    <button
                      onClick={() => {
                        setSelectedMapping(row);
                        setMappingForm({ account_number: row.account_number, deal_id: '' });
                        setDealSearchQuery('');
                        setShowDealDropdown(true);
                        setShowDealMappingModal(true);
                      }}
                      className="btn-secondary text-[12px] shrink-0"
                    >
                      Map to Deal
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Matched Accounts */}
      {parsedData && parsedData.matched.length > 0 && (
        <div className="glass-card p-4 border-l-4 border-emerald-500">
          <div className="flex items-start gap-3">
            <Check className="h-5 w-5 text-emerald-400 mt-0.5 shrink-0" />
            <div className="flex-1">
              <h3 className="text-sm font-semibold text-aegisaxiom-text mb-1">Ready to Commit</h3>
              <p className="text-[13px] text-aegisaxiom-text-secondary mb-3">
                {parsedData.matched.length} matched commission(s) ready
              </p>
              <div className="max-h-64 overflow-y-auto space-y-2">
                {parsedData.matched.map((row, idx) => (
                  <div key={idx} className="bg-aegisaxiom-elevated rounded-lg p-3 flex items-center justify-between gap-3">
                    <div>
                      <p className="text-[13px] font-medium text-aegisaxiom-text">
                        {row.deal_name} · {row.linking_field_name || 'Account'}: {row.account_number}
                      </p>
                      <p className="text-[12px] text-aegisaxiom-text-secondary">
                        Partner: {row.partner_name} · Plan: {row.commission_plan || 'Default'}
                      </p>
                    </div>
                    <p className="text-sm font-bold text-emerald-400 shrink-0">${row.amount.toFixed(2)}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Deals Without Commission Plans */}
      {parsedData?.deals_without_plans?.length > 0 && (
        <div className="glass-card p-4 border-l-4 border-amber-500/60">
          <div className="flex items-start gap-3">
            <AlertCircle className="h-5 w-5 text-amber-400 mt-0.5 shrink-0" />
            <div className="flex-1">
              <h3 className="text-sm font-semibold text-aegisaxiom-text mb-1">
                Deals Missing Commission Plans ({parsedData.deals_without_plans.length})
              </h3>
              <p className="text-[13px] text-aegisaxiom-text-secondary mb-3">
                These deals have no commission plan. Records cannot be created until plans are configured.
              </p>
              <div className="space-y-2">
                {parsedData.deals_without_plans.map((deal, idx) => (
                  <div key={idx} className="bg-aegisaxiom-elevated rounded-lg p-3 flex items-center justify-between gap-3">
                    <div>
                      <p className="text-[13px] font-medium text-aegisaxiom-text">{deal.deal_name}</p>
                      <p className="text-[12px] text-aegisaxiom-text-secondary">
                        Account: {deal.account_number} · Amount: ${deal.amount.toFixed(2)}
                      </p>
                    </div>
                    <button
                      onClick={() => window.open(`/contracts?search=${encodeURIComponent(deal.deal_name)}`, '_blank')}
                      className="btn-secondary text-[12px] shrink-0"
                    >
                      Set Up Plan
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Deal Mappings */}
      <div className="glass-card p-4">
        <h3 className="text-[13px] font-semibold text-aegisaxiom-text mb-3">
          Deal Mappings ({dealMappings.length})
        </h3>
        {dealMappings.length === 0 ? (
          <p className="text-[13px] text-aegisaxiom-text-muted">No deal mappings yet. Import a CSV to get started.</p>
        ) : (
          <div className="max-h-96 overflow-y-auto space-y-2">
            {dealMappings.map((mapping, idx) => (
              <div key={idx} className="bg-aegisaxiom-elevated rounded-lg p-3 flex items-center justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <p className="text-[13px] font-medium text-aegisaxiom-text truncate">
                    {mapping.linking_field_name || 'Account'}: {mapping.account_number}
                  </p>
                  <p className="text-[12px] text-aegisaxiom-text-secondary">
                    Deal: {mapping.deal_name} · Partner: {mapping.partner_name || 'N/A'}
                  </p>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <span className="text-[12px] text-emerald-400 mr-1">Mapped</span>
                  <button
                    onClick={() => handleEditMapping(mapping)}
                    className="h-7 w-7 grid place-items-center rounded-md hover:bg-aegisaxiom-elevated text-aegisaxiom-text-muted hover:text-aegisaxiom-text transition-colors"
                    title="Edit mapping"
                  >
                    <Edit2 className="h-3.5 w-3.5" />
                  </button>
                  <button
                    onClick={() => handleDeleteMapping(mapping)}
                    className="h-7 w-7 grid place-items-center rounded-md hover:bg-red-500/10 text-aegisaxiom-text-muted hover:text-red-400 transition-colors"
                    title="Delete mapping"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Header Mapping Modal */}
      {showMappingModal && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-aegisaxiom-surface rounded-xl border border-aegisaxiom-border w-full max-w-2xl max-h-[80vh] overflow-hidden flex flex-col shadow-2xl">
            <div className="px-5 py-4 border-b border-aegisaxiom-border flex items-center justify-between">
              <h3 className="text-base font-semibold text-aegisaxiom-text">Map CSV Headers</h3>
              <button onClick={closeMappingModal} className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text">
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="p-5 overflow-y-auto flex-1 space-y-4">
              <p className="text-[13px] text-aegisaxiom-text-secondary">
                Select the required columns, then optionally map additional fields.
              </p>

              {/* Step 1 */}
              <div className="p-3 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-elevated">
                <label className="text-[12px] font-semibold text-aegisaxiom-text-secondary block mb-2">
                  Step 1: Account Identifier Column *
                </label>
                <select
                  value={linkingColumn}
                  onChange={(e) => setLinkingColumn(e.target.value)}
                  className="w-full h-9 px-3 rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface text-[13px] text-aegisaxiom-text"
                >
                  <option value="">-- Select Column --</option>
                  {csvHeaders.filter((h) => h?.trim()).map((h) => (
                    <option key={h} value={h}>{h}</option>
                  ))}
                </select>
                <p className="text-[12px] text-aegisaxiom-text-muted mt-1">For AppDirect: "Account Number" or "BAN"</p>
              </div>

              {/* Step 2 */}
              <div className="p-3 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-elevated">
                <label className="text-[12px] font-semibold text-aegisaxiom-text-secondary block mb-2">
                  Step 2: Commission Amount Column *
                </label>
                <select
                  value={amountColumn}
                  onChange={(e) => setAmountColumn(e.target.value)}
                  className="w-full h-9 px-3 rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface text-[13px] text-aegisaxiom-text"
                >
                  <option value="">-- Select Column --</option>
                  {csvHeaders.filter((h) => h?.trim()).map((h) => (
                    <option key={h} value={h}>{h}</option>
                  ))}
                </select>
                <p className="text-[12px] text-aegisaxiom-text-muted mt-1">For AppDirect: "Comp Paid" or "Commission Amount"</p>
              </div>

              {/* Step 3 — Period */}
              <div className="p-3 rounded-lg border border-aegisaxiom-border bg-aegisaxiom-elevated">
                <div className="flex items-center gap-2 mb-3">
                  <Calendar className="h-4 w-4 text-aegisaxiom-text-muted" />
                  <label className="text-[12px] font-semibold text-aegisaxiom-text-secondary">
                    Step 3: Commission Period *
                  </label>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-[12px] text-aegisaxiom-text-muted block mb-1">Month</label>
                    <select
                      value={selectedMonth}
                      onChange={(e) => setSelectedMonth(Number(e.target.value))}
                      className="w-full h-9 px-3 rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface text-[13px] text-aegisaxiom-text"
                    >
                      {['January','February','March','April','May','June','July','August','September','October','November','December'].map((m, i) => (
                        <option key={m} value={i + 1}>{m}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="text-[12px] text-aegisaxiom-text-muted block mb-1">Year</label>
                    <select
                      value={selectedYear}
                      onChange={(e) => setSelectedYear(Number(e.target.value))}
                      className="w-full h-9 px-3 rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface text-[13px] text-aegisaxiom-text"
                    >
                      {[2024, 2025, 2026, 2027, 2028, 2029, 2030].map((y) => (
                        <option key={y} value={y}>{y}</option>
                      ))}
                    </select>
                  </div>
                </div>
                <p className="text-[12px] text-aegisaxiom-text-muted mt-2">
                  Period: <span className="text-aegisaxiom-text font-medium">{commissionPeriod}</span>
                </p>
              </div>

              {/* Template Selection */}
              {headerTemplates.length > 0 && (
                <div>
                  <label className="text-[12px] text-aegisaxiom-text-muted block mb-1">Load Saved Template</label>
                  <select
                    className="w-full h-9 px-3 rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface text-[13px] text-aegisaxiom-text"
                    onChange={(e) => {
                      const tmpl = headerTemplates.find((t) => t.template_id === e.target.value);
                      if (tmpl) handleLoadTemplate(tmpl);
                    }}
                  >
                    <option value="">Select a template...</option>
                    {headerTemplates.map((tmpl) => (
                      <option key={tmpl.template_id} value={tmpl.template_id}>{tmpl.template_name}</option>
                    ))}
                  </select>
                </div>
              )}

              {/* Step 4 — Optional Mappings */}
              <div>
                <label className="text-[12px] font-semibold text-aegisaxiom-text-secondary block mb-2">
                  Step 4: Map Other Fields (Optional)
                </label>
                <div className="space-y-2">
                  {csvHeaders.filter((h) => h?.trim()).map((header, idx) => (
                    <div key={idx} className="flex items-center gap-3">
                      <p className="text-[13px] text-aegisaxiom-text flex-1">{header}</p>
                      <select
                        value={headerMappings[header] || ''}
                        onChange={(e) => setHeaderMappings({ ...headerMappings, [header]: e.target.value })}
                        className="flex-1 h-8 px-2 rounded-md border border-aegisaxiom-border bg-aegisaxiom-surface text-[12px] text-aegisaxiom-text"
                      >
                        <option value="">-- Skip --</option>
                        <option value="partner_name">Partner Name</option>
                        <option value="period">Period</option>
                      </select>
                    </div>
                  ))}
                </div>
              </div>

              {/* Save Template */}
              <div className="pt-4 border-t border-aegisaxiom-border">
                <label className="text-[12px] text-aegisaxiom-text-muted block mb-1">Save as Template (optional)</label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    placeholder="Template name..."
                    value={templateName}
                    onChange={(e) => setTemplateName(e.target.value)}
                    className="flex-1 h-9 px-3 rounded-md border border-aegisaxiom-border bg-aegisaxiom-elevated text-[13px] text-aegisaxiom-text"
                  />
                  <button onClick={handleSaveTemplate} className="btn-secondary px-3">
                    <Save className="h-4 w-4" />
                  </button>
                </div>
              </div>
            </div>

            <div className="px-5 py-4 border-t border-aegisaxiom-border flex justify-end gap-2">
              <button onClick={closeMappingModal} className="btn-secondary text-[13px]">Cancel</button>
              <button onClick={handleParseCSV} disabled={loading} className="btn-primary text-[13px]">
                {loading ? 'Parsing...' : 'Parse CSV'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Deal Mapping Modal */}
      {showDealMappingModal && selectedMapping && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-aegisaxiom-surface rounded-xl border border-aegisaxiom-border w-full max-w-lg shadow-2xl">
            <div className="px-5 py-4 border-b border-aegisaxiom-border flex items-center justify-between">
              <h3 className="text-base font-semibold text-aegisaxiom-text">
                {editMode ? 'Edit Deal Mapping' : 'Map to Deal'}
              </h3>
              <button onClick={closeDealModal} className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text">
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="p-5 space-y-4">
              <div className="bg-aegisaxiom-elevated rounded-lg p-3">
                <p className="text-[12px] text-aegisaxiom-text-muted">{selectedMapping.linking_field_name || 'Account Number'}</p>
                <p className="text-[13px] font-medium text-aegisaxiom-text">{selectedMapping.account_number}</p>
                {selectedMapping.amount != null && (
                  <p className="text-[12px] text-aegisaxiom-text-secondary mt-1">
                    Partner: {selectedMapping.partner_name || 'Unknown'} · Amount: ${selectedMapping.amount.toFixed(2)}
                  </p>
                )}
              </div>

              <div>
                <label className="text-[12px] text-aegisaxiom-text-muted block mb-1">Select Deal *</label>
                <div className="relative mb-2">
                  <input
                    type="text"
                    placeholder="Search deals..."
                    value={dealSearchQuery}
                    onChange={(e) => setDealSearchQuery(e.target.value)}
                    className="w-full h-9 px-3 rounded-md border border-aegisaxiom-border bg-aegisaxiom-elevated text-[13px] text-aegisaxiom-text pr-8"
                  />
                  {dealSearchQuery && (
                    <button
                      onClick={() => setDealSearchQuery('')}
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  )}
                </div>

                {showDealDropdown && (
                  <div className="max-h-56 overflow-y-auto bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg">
                    {filteredDeals.map((deal) => (
                      <button
                        key={deal.deal_id}
                        onClick={() => {
                          setMappingForm({ ...mappingForm, deal_id: deal.deal_id });
                          setShowDealDropdown(false);
                        }}
                        className={`w-full text-left px-3 py-2 text-[13px] hover:bg-aegisaxiom-surface transition-colors ${
                          mappingForm.deal_id === deal.deal_id ? 'text-violet-400 bg-violet-500/10' : 'text-aegisaxiom-text'
                        }`}
                      >
                        {deal.name} {deal.account_name ? `— ${deal.account_name}` : ''}
                      </button>
                    ))}
                    {filteredDeals.length === 0 && (
                      <div className="px-3 py-2 text-[13px] text-aegisaxiom-text-muted">No deals found</div>
                    )}
                  </div>
                )}

                {mappingForm.deal_id && !showDealDropdown && (
                  <div className="bg-violet-500/10 border border-violet-500/20 rounded-lg p-3 flex items-center justify-between">
                    <div>
                      <p className="text-[12px] text-violet-400 mb-0.5">Selected Deal:</p>
                      <p className="text-[13px] font-medium text-aegisaxiom-text">
                        {deals.find((d) => d.deal_id === mappingForm.deal_id)?.name}
                      </p>
                    </div>
                    <button
                      onClick={() => { setMappingForm({ ...mappingForm, deal_id: '' }); setShowDealDropdown(true); }}
                      className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  </div>
                )}

                {mappingForm.deal_id && (
                  <p className="text-[12px] text-aegisaxiom-text-muted mt-2">
                    Commission will be split according to this deal's commission plan
                  </p>
                )}
              </div>
            </div>

            <div className="px-5 py-4 border-t border-aegisaxiom-border flex justify-end gap-2">
              <button onClick={closeDealModal} className="btn-secondary text-[13px]">Cancel</button>
              <button
                onClick={editMode ? handleSaveEditedMapping : handleSaveAccountMapping}
                className="btn-primary text-[13px]"
              >
                {editMode ? 'Update Mapping' : 'Save Mapping'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Confirm Delete */}
      <ConfirmModal
        open={confirmState.open}
        title="Delete Mapping"
        message={`Delete the mapping for account "${confirmState.mapping?.account_number}"? This cannot be undone.`}
        confirmText="Delete"
        variant="danger"
        onConfirm={confirmDeleteMapping}
        onCancel={() => setConfirmState({ open: false, mapping: null })}
      />
    </div>
  );
}

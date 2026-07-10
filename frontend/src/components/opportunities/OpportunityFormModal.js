import React, { useState, useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { api, apiDownload } from '@/lib/api';
import API_URL from '../../lib/apiConfig';
import { useAuth } from '@/contexts/AuthContext';
import AutoGrowTextarea from '../shared/AutoGrowTextarea';
import {
  X,
  Building2,
  User,
  Calendar,
  DollarSign,
  Flag,
  Briefcase,
  FileText,
  Plus,
  Check,
  Loader2,
  MessageSquare,
  Paperclip,
  Trash2,
  Upload,
} from 'lucide-react';

const API = API_URL;

const PRIORITIES = ['high', 'medium', 'low'];

const LINES_OF_BUSINESS = [
  { value: 'professional_services', label: 'Professional Services' },
  { value: 'field_services', label: 'Field Services' },
  { value: 'referral_mrr', label: 'Referral MRR' },
  { value: 'wholesale_mrr', label: 'Wholesale MRR' },
];

const COMMISSION_METHODS = [
  { value: 'telarus', label: 'Telarus' },
  { value: 'appdirect', label: 'AppDirect' },
  { value: 'direct', label: 'Direct' },
  { value: 'custom', label: 'Custom' },
];

const STAGES = [
  { value: 'prospect_opp', label: 'Prospect Opportunity' },
  { value: 'prospect_lead', label: 'Prospect Lead' },
  { value: 'proposal_development', label: 'Proposal Development' },
  { value: 'scoping_complete', label: 'Scoping Complete' },
  { value: 'proposal_sent', label: 'Proposal Sent' },
  { value: 'negotiation_review', label: 'Negotiation/Review' },
  { value: 'verbal_agreement', label: 'Verbal Agreement' },
  { value: 'closed_won', label: 'Closed Won' },
  { value: 'closed_lost', label: 'Closed Lost' },
];

const INDUSTRIES = [
  'Technology',
  'Healthcare',
  'Finance',
  'Energy',
  'Manufacturing',
  'Retail',
  'Education',
  'Government',
  'Other',
];

const EMPTY_INLINE_ACCOUNT = {
  name: '',
  account_type: 'company',
  industry: '',
  email: '',
  phone: '',
  website: '',
};

const EMPTY_INLINE_CONTACT = {
  first_name: '',
  last_name: '',
  email: '',
  phone: '',
  mobile_phone: '',
  title: '',
  department: '',
  linkedin_url: '',
  preferred_contact_method: '',
  notes: '',
};

const CONTACT_METHODS = [
  { value: '', label: 'Not specified' },
  { value: 'email', label: 'Email' },
  { value: 'phone', label: 'Phone' },
  { value: 'mobile', label: 'Mobile' },
  { value: 'sms', label: 'SMS' },
];

export default function OpportunityFormModal({
  opportunity,
  editMode,
  testOnlyMode = false,
  onClose,
  onSave,
}) {
  const { user } = useAuth();
  const [loading, setLoading] = useState(false);

  // Escape to close + body scroll lock.
  // Ref avoids re-running effect when parent passes a new onClose ref each render
  // (which would otherwise corrupt the prev-overflow snapshot).
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key === 'Escape') onCloseRef.current?.();
    };
    window.addEventListener('keydown', onKeyDown);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = prev;
    };
  }, []);

  const [dropdownData, setDropdownData] = useState({
    accounts: [],
    contacts: [],
    users: [],
    partners: [],
  });

  // Inline account creation state
  const [showInlineAccount, setShowInlineAccount] = useState(false);
  const [inlineAccountForm, setInlineAccountForm] = useState(EMPTY_INLINE_ACCOUNT);
  const [creatingAccount, setCreatingAccount] = useState(false);

  // Inline contact creation state
  const [showInlineContact, setShowInlineContact] = useState(false);
  const [inlineContactForm, setInlineContactForm] = useState(EMPTY_INLINE_CONTACT);
  const [creatingContact, setCreatingContact] = useState(false);

  // Notes state (edit mode only)
  const [noteText, setNoteText] = useState('');
  const [addingNote, setAddingNote] = useState(false);
  const [localNotes, setLocalNotes] = useState([]);

  // Attachments state (edit mode only)
  const [uploading, setUploading] = useState(false);
  const [localAttachments, setLocalAttachments] = useState([]);

  const [formData, setFormData] = useState({
    name: '',
    account_id: '',
    contact_id: '',
    agent_of_record_id: '',
    description: '',
    desired_proposal_date: '',
    // Expected close date. Separate concept from desired_proposal_date
    // and never copied into it — the close-date alert in
    // OpportunityManagement reads close_date directly.
    close_date: '',
    priority: 'medium',
    assigned_sales_ops_id: '',
    amount: 0,
    stage: 'prospect_opp',
    commission_payment_method: '',
    lines_of_business: [],
    is_test_data: testOnlyMode,
  });

  useEffect(() => {
    loadDropdownData();

    if (opportunity && editMode) {
      setFormData({
        name: opportunity.name || '',
        account_id: opportunity.account_id || '',
        contact_id: opportunity.contact_id || '',
        agent_of_record_id: opportunity.agent_of_record_id || '',
        description: opportunity.description || '',
        desired_proposal_date: opportunity.desired_proposal_date || '',
        close_date: opportunity.close_date || '',
        priority: opportunity.priority || 'medium',
        assigned_sales_ops_id: opportunity.assigned_sales_ops_id || '',
        amount: opportunity.amount || 0,
        stage: opportunity.stage || 'prospect_opp',
        commission_payment_method: opportunity.commission_payment_method || '',
        lines_of_business: opportunity.lines_of_business || [],
        is_test_data: opportunity.is_test_data || false,
      });
      setLocalNotes(opportunity.notes || []);
      setLocalAttachments(opportunity.attachments || []);
    }
  }, [opportunity, editMode, testOnlyMode]);

  const loadDropdownData = async () => {
    try {
      const response = await api.get(`${API}/api/opportunities/dropdown-data/all`);
      if (response.ok) {
        const data = await response.json();

        if (testOnlyMode) {
          setDropdownData({
            accounts: data.accounts?.filter((a) => a.is_test_data) || [],
            contacts: data.contacts?.filter((c) => c.is_test_data) || [],
            users: data.users?.filter((u) => u.is_test_data) || [],
            partners: data.partners?.filter((p) => p.is_test_data) || [],
          });
        } else {
          setDropdownData(data);
        }
      } else {
        toast.error('Failed to load form data');
      }
    } catch (error) {
      console.error('Error loading dropdown data:', error);
      toast.error('Failed to load form data');
    }
  };

  const handleAccountChange = async (accountId) => {
    setFormData((prev) => ({ ...prev, account_id: accountId, contact_id: '' }));

    if (accountId) {
      try {
        const response = await api.get(
          `${API}/api/opportunities/dropdown-data/all?account_id=${accountId}`,
        );
        if (response.ok) {
          const data = await response.json();
          const filteredContacts = testOnlyMode
            ? data.contacts?.filter((c) => c.is_test_data) || []
            : data.contacts || [];
          setDropdownData((prev) => ({ ...prev, contacts: filteredContacts }));
        }
      } catch (error) {
        console.error('Error loading contacts:', error);
      }
    }
  };

  // ---- Inline Account Creation ----
  const handleCreateInlineAccount = async () => {
    if (!inlineAccountForm.name.trim()) {
      toast.error('Account name is required');
      return;
    }

    setCreatingAccount(true);
    try {
      const payload = {
        name: inlineAccountForm.name.trim(),
        account_type: inlineAccountForm.account_type || 'company',
        industry: inlineAccountForm.industry || null,
        email: inlineAccountForm.email || null,
        phone: inlineAccountForm.phone || null,
        website: inlineAccountForm.website || null,
        is_test_data: testOnlyMode || formData.is_test_data,
        // Auto-assign sales_rep from frontend (backend also does this as safety net)
        assigned_sales_rep_id: user?.role === 'sales_rep' ? user.user_id : '',
      };

      const res = await api.post(`${API}/api/accounts`, payload);
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to create account');
      }

      const newAccount = await res.json();
      toast.success(`Account "${newAccount.name}" created`);

      setDropdownData((prev) => ({
        ...prev,
        accounts: [
          ...prev.accounts,
          {
            account_id: newAccount.account_id,
            name: newAccount.name,
            is_test_data: newAccount.is_test_data,
          },
        ].sort((a, b) => a.name.localeCompare(b.name)),
      }));
      setFormData((prev) => ({ ...prev, account_id: newAccount.account_id, contact_id: '' }));

      setInlineAccountForm(EMPTY_INLINE_ACCOUNT);
      setShowInlineAccount(false);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setCreatingAccount(false);
    }
  };

  // ---- Inline Contact Creation ----
  const handleCreateInlineContact = async () => {
    if (!inlineContactForm.first_name.trim() || !inlineContactForm.last_name.trim()) {
      toast.error('First and last name are required');
      return;
    }
    if (!formData.account_id) {
      toast.error('Select an account first');
      return;
    }

    setCreatingContact(true);
    try {
      const payload = {
        first_name: inlineContactForm.first_name.trim(),
        last_name: inlineContactForm.last_name.trim(),
        account_id: formData.account_id,
        email: inlineContactForm.email || null,
        phone: inlineContactForm.phone || null,
        mobile_phone: inlineContactForm.mobile_phone || null,
        title: inlineContactForm.title || null,
        department: inlineContactForm.department || null,
        linkedin_url: inlineContactForm.linkedin_url || null,
        preferred_contact_method: inlineContactForm.preferred_contact_method || null,
        notes: inlineContactForm.notes || null,
        is_test_data: testOnlyMode || formData.is_test_data,
      };

      const res = await api.post(`${API}/api/contacts`, payload);
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to create contact');
      }

      const newContact = await res.json();
      toast.success(`Contact "${newContact.name}" created`);

      setDropdownData((prev) => ({
        ...prev,
        contacts: [
          ...prev.contacts,
          {
            contact_id: newContact.contact_id,
            name: newContact.name,
            account_id: newContact.account_id,
            is_test_data: newContact.is_test_data,
          },
        ].sort((a, b) => a.name.localeCompare(b.name)),
      }));
      setFormData((prev) => ({ ...prev, contact_id: newContact.contact_id }));

      setInlineContactForm(EMPTY_INLINE_CONTACT);
      setShowInlineContact(false);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setCreatingContact(false);
    }
  };

  // ---- Notes ----
  const handleAddNote = async () => {
    if (!noteText.trim()) {
      toast.error('Please enter a note');
      return;
    }
    setAddingNote(true);
    try {
      const response = await api.post(
        `${API}/api/opportunities/${opportunity.opportunity_id}/notes?note_text=${encodeURIComponent(noteText)}`,
      );
      if (response.ok) {
        const data = await response.json();
        setLocalNotes((prev) => [...prev, data.note]);
        setNoteText('');
        toast.success('Note added');
      } else {
        const err = await response.json();
        toast.error(err.detail || 'Failed to add note');
      }
    } catch (error) {
      console.error('Error adding note:', error);
      toast.error('Failed to add note');
    } finally {
      setAddingNote(false);
    }
  };

  const handleDeleteNote = async (noteId) => {
    try {
      const response = await api.delete(
        `${API}/api/opportunities/${opportunity.opportunity_id}/notes/${noteId}`,
      );
      if (response.ok) {
        setLocalNotes((prev) => prev.filter((n) => n.note_id !== noteId));
        toast.success('Note deleted');
      } else {
        toast.error('Failed to delete note');
      }
    } catch (error) {
      console.error('Error deleting note:', error);
      toast.error('Failed to delete note');
    }
  };

  // ---- Attachments ----
  const refreshAttachments = async () => {
    try {
      const res = await api.get(`${API}/api/opportunities/${opportunity.opportunity_id}`);
      if (res.ok) {
        const opp = await res.json();
        setLocalAttachments(opp.attachments || []);
      }
    } catch (err) {
      console.error('Error refreshing attachments:', err);
    }
  };

  const handleViewAttachment = async (att) => {
    // Claim the new tab synchronously, inside the click handler, so the
    // browser treats it as user-initiated. Opening it after the `await`
    // below would otherwise be caught by the popup blocker.
    const win = window.open('', '_blank');
    try {
      const blob = await apiDownload(
        `${API}/api/opportunities/${opportunity.opportunity_id}/attachments/${att.attachment_id}/download`,
      );
      if (!blob) {
        win?.close();
        toast.error('Failed to open attachment');
        return;
      }
      const url = URL.createObjectURL(blob);
      if (win) {
        win.location = url;
      } else {
        toast.error('Allow pop-ups to view attachments');
      }
      // Release the object URL once the tab has had time to load it.
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (err) {
      win?.close();
      console.error('Error opening attachment:', err);
      toast.error('Failed to open attachment');
    }
  };

  const handleFileUpload = async (e) => {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;

    setUploading(true);
    let uploaded = 0;
    try {
      for (const file of files) {
        if (file.size > 50 * 1024 * 1024) {
          toast.error(`${file.name} exceeds 50MB — skipped`);
          continue;
        }

        const formDataUpload = new FormData();
        formDataUpload.append('file', file);

        const res = await api.postForm(
          `${API}/api/opportunities/${opportunity.opportunity_id}/attachments`,
          formDataUpload,
        );

        if (res.ok) {
          uploaded += 1;
        } else {
          const err = await res.json().catch(() => ({}));
          toast.error(err.detail || `Failed to upload ${file.name}`);
        }
      }
      if (uploaded > 0) {
        toast.success(uploaded > 1 ? `${uploaded} files uploaded` : 'File uploaded');
        // Refetch once after the batch so localAttachments reflects authoritative
        // server state. Parsing per-file response shapes inside the loop risked a
        // later file's `attachments` array overwriting earlier appended uploads.
        await refreshAttachments();
      }
    } catch (error) {
      console.error('Error uploading file:', error);
      toast.error('Failed to upload file');
    } finally {
      setUploading(false);
      e.target.value = '';
    }
  };

  const handleDeleteAttachment = async (attachmentId) => {
    try {
      const response = await api.delete(
        `${API}/api/opportunities/${opportunity.opportunity_id}/attachments/${attachmentId}`,
      );
      if (response.ok) {
        setLocalAttachments((prev) => prev.filter((a) => a.attachment_id !== attachmentId));
        toast.success('Attachment deleted');
      } else {
        toast.error('Failed to delete attachment');
      }
    } catch (error) {
      console.error('Error deleting attachment:', error);
      toast.error('Failed to delete attachment');
    }
  };

  const formatFileSize = (bytes) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const formatDateTime = (dateString) => {
    if (!dateString) return '';
    return new Date(dateString).toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    });
  };

  const handleLOBToggle = (lob) => {
    setFormData((prev) => ({
      ...prev,
      lines_of_business: prev.lines_of_business.includes(lob)
        ? prev.lines_of_business.filter((l) => l !== lob)
        : [...prev.lines_of_business, lob],
    }));
  };

  // Track which element the mouse went down on so a text selection
  // started inside the form that releases on the dim overlay does NOT
  // count as an outside-modal click. Without this, highlighting text
  // in the name input can release outside the panel and close the
  // modal mid-edit. The standard fix is: only close on click when the
  // mousedown ALSO happened on the overlay itself.
  const overlayMouseDownTargetRef = useRef(null);
  const handleOverlayMouseDown = (e) => {
    overlayMouseDownTargetRef.current = e.target;
  };
  const handleOverlayClick = (e) => {
    if (overlayMouseDownTargetRef.current === e.currentTarget && e.target === e.currentTarget) {
      onClose();
    }
    overlayMouseDownTargetRef.current = null;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();

    // Trim the opportunity name at the submit boundary so any leading
    // or trailing whitespace the user, the browser autofill, or a
    // paste from another field introduced does not land in storage
    // and prepend to the visible card title. The backend stores the
    // value verbatim per OpportunityCreate; the UI is the right place
    // to normalize the input.
    const trimmedName = formData.name.trim();
    if (!trimmedName) {
      toast.error('Opportunity name is required');
      return;
    }
    if (!formData.account_id) {
      toast.error('Account is required');
      return;
    }
    if (formData.amount < 0) {
      toast.error('Amount cannot be negative');
      return;
    }

    setLoading(true);
    try {
      let response;
      // Send the trimmed name in the payload so what the user typed
      // is what gets stored — nothing prepended, nothing tacked on.
      const payload = { ...formData, name: trimmedName };
      if (editMode && opportunity) {
        response = await api.put(
          `${API}/api/opportunities/${opportunity.opportunity_id}`,
          payload,
        );
      } else {
        response = await api.post(`${API}/api/opportunities`, payload);
      }

      if (response.ok) {
        toast.success(
          editMode ? 'Opportunity updated successfully' : 'Opportunity created successfully',
        );
        onSave();
      } else {
        const errorData = await response.json();
        toast.error(errorData.detail || 'Failed to save opportunity');
      }
    } catch (error) {
      console.error('Error saving opportunity:', error);
      toast.error('Failed to save opportunity');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-label={editMode ? 'Edit Opportunity' : 'New Opportunity'}
      onMouseDown={handleOverlayMouseDown}
      onClick={handleOverlayClick}
    >
      <div
        className="bg-aegisaxiom-surface border border-aegisaxiom-border w-full h-full sm:rounded-lg sm:max-w-3xl sm:max-h-[90vh] sm:h-auto overflow-hidden flex flex-col"
        onMouseDown={(e) => e.stopPropagation()}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-4 sm:p-6 border-b border-aegisaxiom-border shrink-0">
          <h2 className="text-lg sm:text-xl font-bold text-aegisaxiom-text">
            {editMode ? 'Edit Opportunity' : 'New Opportunity'}
          </h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="w-9 h-9 flex items-center justify-center hover:bg-aegisaxiom-elevated rounded-lg transition-colors"
          >
            <X className="w-5 h-5 text-aegisaxiom-text-secondary" />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto p-4 sm:p-6 min-h-0">
          <div className="space-y-6">
            {/* Basic Info */}
            <div>
              <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-4">
                Basic Information
              </h3>
              <div className="space-y-4">
                {/* Name */}
                <div>
                  <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                    Opportunity Name <span className="text-red-600 dark:text-red-400">*</span>
                  </label>
                  <input
                    type="text"
                    name="opportunity_name"
                    value={formData.name}
                    onChange={(e) => setFormData((prev) => ({ ...prev, name: e.target.value }))}
                    className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
                    placeholder="Enter opportunity name"
                    required
                    autoComplete="off"
                    data-testid="opportunity-form-name-input"
                  />
                </div>

                {/* Account and Contact */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                      <Building2 className="w-4 h-4 inline mr-1" />
                      Account <span className="text-red-600 dark:text-red-400">*</span>
                    </label>
                    <div className="flex gap-2">
                      <select
                        value={formData.account_id}
                        onChange={(e) => handleAccountChange(e.target.value)}
                        className="flex-1 px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
                        required
                      >
                        <option value="">Select account</option>
                        {dropdownData.accounts.map((acc) => (
                          <option key={acc.account_id} value={acc.account_id}>
                            {acc.name}
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                        onClick={() => setShowInlineAccount(!showInlineAccount)}
                        className={`flex-shrink-0 p-2 rounded-lg border transition-colors ${
                          showInlineAccount
                            ? 'bg-violet-600 border-violet-500 text-white'
                            : 'bg-aegisaxiom-base border-aegisaxiom-border text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:border-aegisaxiom-border-hover'
                        }`}
                        title="Create new account"
                      >
                        <Plus className="w-5 h-5" />
                      </button>
                    </div>

                    {/* Inline Account Creation Form */}
                    {showInlineAccount && (
                      <div className="mt-3 p-4 bg-aegisaxiom-base border border-violet-500/40 rounded-lg space-y-3">
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-xs font-semibold text-violet-700 dark:text-violet-300 uppercase tracking-wide">
                            Quick Create Account
                          </span>
                          <button
                            type="button"
                            onClick={() => {
                              setShowInlineAccount(false);
                              setInlineAccountForm(EMPTY_INLINE_ACCOUNT);
                            }}
                            className="p-0.5 hover:bg-aegisaxiom-elevated rounded"
                          >
                            <X className="w-3.5 h-3.5 text-aegisaxiom-text-secondary" />
                          </button>
                        </div>

                        <div>
                          <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                            Company Name *
                          </label>
                          <input
                            type="text"
                            value={inlineAccountForm.name}
                            onChange={(e) =>
                              setInlineAccountForm((prev) => ({ ...prev, name: e.target.value }))
                            }
                            className="w-full px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                            placeholder="e.g. Acme Corp"
                            autoFocus
                          />
                        </div>

                        <div className="grid grid-cols-2 gap-2">
                          <div>
                            <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                              Type
                            </label>
                            <select
                              value={inlineAccountForm.account_type}
                              onChange={(e) =>
                                setInlineAccountForm((prev) => ({
                                  ...prev,
                                  account_type: e.target.value,
                                }))
                              }
                              className="w-full px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                            >
                              <option value="company">Company</option>
                              <option value="partner_agency">Partner Agency</option>
                            </select>
                          </div>
                          <div>
                            <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                              Industry
                            </label>
                            <select
                              value={inlineAccountForm.industry}
                              onChange={(e) =>
                                setInlineAccountForm((prev) => ({
                                  ...prev,
                                  industry: e.target.value,
                                }))
                              }
                              className="w-full px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                            >
                              <option value="">Select...</option>
                              {INDUSTRIES.map((i) => (
                                <option key={i} value={i}>
                                  {i}
                                </option>
                              ))}
                            </select>
                          </div>
                        </div>

                        <div className="grid grid-cols-2 gap-2">
                          <div>
                            <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                              Email
                            </label>
                            <input
                              type="email"
                              value={inlineAccountForm.email}
                              onChange={(e) =>
                                setInlineAccountForm((prev) => ({ ...prev, email: e.target.value }))
                              }
                              className="w-full px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                              placeholder="contact@..."
                            />
                          </div>
                          <div>
                            <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                              Phone
                            </label>
                            <input
                              type="text"
                              value={inlineAccountForm.phone}
                              onChange={(e) =>
                                setInlineAccountForm((prev) => ({ ...prev, phone: e.target.value }))
                              }
                              className="w-full px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                              placeholder="555-123-4567"
                            />
                          </div>
                        </div>

                        <div>
                          <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                            Website
                          </label>
                          <input
                            type="text"
                            value={inlineAccountForm.website}
                            onChange={(e) =>
                              setInlineAccountForm((prev) => ({ ...prev, website: e.target.value }))
                            }
                            className="w-full px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                            placeholder="https://..."
                          />
                        </div>

                        <div className="flex justify-end gap-2 pt-1">
                          <button
                            type="button"
                            onClick={() => {
                              setShowInlineAccount(false);
                              setInlineAccountForm(EMPTY_INLINE_ACCOUNT);
                            }}
                            className="px-3 py-1.5 text-xs text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition-colors"
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            onClick={handleCreateInlineAccount}
                            disabled={creatingAccount || !inlineAccountForm.name.trim()}
                            className="flex items-center gap-1.5 px-3 py-1.5 bg-violet-600 hover:bg-violet-700 rounded-lg text-xs text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {creatingAccount ? (
                              <Loader2 className="w-3 h-3 animate-spin" />
                            ) : (
                              <Check className="w-3 h-3" />
                            )}
                            {creatingAccount ? 'Creating...' : 'Create & Select'}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                      <User className="w-4 h-4 inline mr-1" />
                      Contact
                    </label>
                    <div className="flex gap-2">
                      <select
                        value={formData.contact_id}
                        onChange={(e) =>
                          setFormData((prev) => ({ ...prev, contact_id: e.target.value }))
                        }
                        className="flex-1 px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
                        disabled={!formData.account_id}
                      >
                        <option value="">Select contact</option>
                        {dropdownData.contacts
                          .filter((c) => c.account_id === formData.account_id)
                          .map((contact) => (
                            <option key={contact.contact_id} value={contact.contact_id}>
                              {contact.name}
                            </option>
                          ))}
                      </select>
                      <button
                        type="button"
                        onClick={() => setShowInlineContact(!showInlineContact)}
                        disabled={!formData.account_id}
                        className={`flex-shrink-0 p-2 rounded-lg border transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                          showInlineContact
                            ? 'bg-violet-600 border-violet-500 text-white'
                            : 'bg-aegisaxiom-base border-aegisaxiom-border text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:border-aegisaxiom-border-hover'
                        }`}
                        title={
                          formData.account_id ? 'Create new contact' : 'Select an account first'
                        }
                      >
                        <Plus className="w-5 h-5" />
                      </button>
                    </div>

                    {/* Inline Contact Creation Form */}
                    {showInlineContact && (
                      <div className="mt-3 p-4 bg-aegisaxiom-base border border-violet-500/40 rounded-lg space-y-3">
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-xs font-semibold text-violet-700 dark:text-violet-300 uppercase tracking-wide">
                            Quick Create Contact
                          </span>
                          <button
                            type="button"
                            onClick={() => {
                              setShowInlineContact(false);
                              setInlineContactForm(EMPTY_INLINE_CONTACT);
                            }}
                            className="p-0.5 hover:bg-aegisaxiom-elevated rounded"
                          >
                            <X className="w-3.5 h-3.5 text-aegisaxiom-text-secondary" />
                          </button>
                        </div>

                        <div className="grid grid-cols-2 gap-2">
                          <div>
                            <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                              First Name *
                            </label>
                            <input
                              type="text"
                              value={inlineContactForm.first_name}
                              onChange={(e) =>
                                setInlineContactForm((prev) => ({
                                  ...prev,
                                  first_name: e.target.value,
                                }))
                              }
                              className="w-full px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                              placeholder="Jane"
                              autoFocus
                            />
                          </div>
                          <div>
                            <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                              Last Name *
                            </label>
                            <input
                              type="text"
                              value={inlineContactForm.last_name}
                              onChange={(e) =>
                                setInlineContactForm((prev) => ({
                                  ...prev,
                                  last_name: e.target.value,
                                }))
                              }
                              className="w-full px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                              placeholder="Smith"
                            />
                          </div>
                        </div>

                        <div className="grid grid-cols-2 gap-2">
                          <div>
                            <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                              Title
                            </label>
                            <input
                              type="text"
                              value={inlineContactForm.title}
                              onChange={(e) =>
                                setInlineContactForm((prev) => ({ ...prev, title: e.target.value }))
                              }
                              className="w-full px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                              placeholder="IT Director"
                            />
                          </div>
                          <div>
                            <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                              Department
                            </label>
                            <input
                              type="text"
                              value={inlineContactForm.department}
                              onChange={(e) =>
                                setInlineContactForm((prev) => ({
                                  ...prev,
                                  department: e.target.value,
                                }))
                              }
                              className="w-full px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                              placeholder="Information Technology"
                            />
                          </div>
                        </div>

                        <div className="grid grid-cols-2 gap-2">
                          <div>
                            <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                              Email
                            </label>
                            <input
                              type="email"
                              value={inlineContactForm.email}
                              onChange={(e) =>
                                setInlineContactForm((prev) => ({ ...prev, email: e.target.value }))
                              }
                              className="w-full px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                              placeholder="jane@..."
                            />
                          </div>
                          <div>
                            <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                              Phone
                            </label>
                            <input
                              type="text"
                              value={inlineContactForm.phone}
                              onChange={(e) =>
                                setInlineContactForm((prev) => ({ ...prev, phone: e.target.value }))
                              }
                              className="w-full px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                              placeholder="555-123-4567"
                            />
                          </div>
                        </div>

                        <div className="grid grid-cols-2 gap-2">
                          <div>
                            <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                              Mobile Phone
                            </label>
                            <input
                              type="text"
                              value={inlineContactForm.mobile_phone}
                              onChange={(e) =>
                                setInlineContactForm((prev) => ({
                                  ...prev,
                                  mobile_phone: e.target.value,
                                }))
                              }
                              className="w-full px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                              placeholder="555-123-4568"
                            />
                          </div>
                          <div>
                            <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                              Preferred Contact Method
                            </label>
                            <select
                              value={inlineContactForm.preferred_contact_method}
                              onChange={(e) =>
                                setInlineContactForm((prev) => ({
                                  ...prev,
                                  preferred_contact_method: e.target.value,
                                }))
                              }
                              className="w-full px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                            >
                              {CONTACT_METHODS.map((m) => (
                                <option key={m.value} value={m.value}>
                                  {m.label}
                                </option>
                              ))}
                            </select>
                          </div>
                        </div>

                        <div>
                          <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                            LinkedIn URL
                          </label>
                          <input
                            type="text"
                            value={inlineContactForm.linkedin_url}
                            onChange={(e) =>
                              setInlineContactForm((prev) => ({
                                ...prev,
                                linkedin_url: e.target.value,
                              }))
                            }
                            className="w-full px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                            placeholder="https://linkedin.com/in/username"
                          />
                        </div>

                        <div>
                          <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">
                            Notes
                          </label>
                          <textarea
                            value={inlineContactForm.notes}
                            onChange={(e) =>
                              setInlineContactForm((prev) => ({ ...prev, notes: e.target.value }))
                            }
                            rows={2}
                            className="w-full px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500 resize-none"
                            placeholder="Additional notes about this contact"
                          />
                        </div>

                        <div className="flex justify-end gap-2 pt-1">
                          <button
                            type="button"
                            onClick={() => {
                              setShowInlineContact(false);
                              setInlineContactForm(EMPTY_INLINE_CONTACT);
                            }}
                            className="px-3 py-1.5 text-xs text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition-colors"
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            onClick={handleCreateInlineContact}
                            disabled={
                              creatingContact ||
                              !inlineContactForm.first_name.trim() ||
                              !inlineContactForm.last_name.trim()
                            }
                            className="flex items-center gap-1.5 px-3 py-1.5 bg-violet-600 hover:bg-violet-700 rounded-lg text-xs text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {creatingContact ? (
                              <Loader2 className="w-3 h-3 animate-spin" />
                            ) : (
                              <Check className="w-3 h-3" />
                            )}
                            {creatingContact ? 'Creating...' : 'Create & Select'}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                </div>

                {/* Description */}
                <div>
                  <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                    Description
                  </label>
                  <AutoGrowTextarea
                    value={formData.description}
                    onChange={(e) =>
                      setFormData((prev) => ({ ...prev, description: e.target.value }))
                    }
                    rows={3}
                    className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500 resize-none"
                    placeholder="Brief description of the opportunity"
                  />
                </div>
              </div>
            </div>

            {/* Financial Info */}
            <div>
              <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-4">
                Financial Details
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                    <DollarSign className="w-4 h-4 inline mr-1" />
                    Amount
                  </label>
                  <input
                    type="number"
                    value={formData.amount}
                    onChange={(e) =>
                      setFormData((prev) => ({ ...prev, amount: parseFloat(e.target.value) || 0 }))
                    }
                    className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
                    min="0"
                    step="1000"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                    Commission Payment Method
                  </label>
                  <select
                    value={formData.commission_payment_method}
                    onChange={(e) =>
                      setFormData((prev) => ({
                        ...prev,
                        commission_payment_method: e.target.value,
                      }))
                    }
                    className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
                  >
                    <option value="">Select method</option>
                    {COMMISSION_METHODS.map((method) => (
                      <option key={method.value} value={method.value}>
                        {method.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </div>

            {/* Lines of Business */}
            <div>
              <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                <Briefcase className="w-4 h-4 inline mr-1" />
                Lines of Business
              </label>
              <div className="flex flex-wrap gap-2">
                {LINES_OF_BUSINESS.map((lob) => (
                  <button
                    key={lob.value}
                    type="button"
                    onClick={() => handleLOBToggle(lob.value)}
                    className={`px-3 py-1.5 rounded-lg border text-sm transition-colors ${
                      formData.lines_of_business.includes(lob.value)
                        ? 'bg-violet-600 border-violet-500 text-white'
                        : 'bg-aegisaxiom-base border-aegisaxiom-border text-aegisaxiom-text-secondary hover:border-aegisaxiom-border-hover'
                    }`}
                  >
                    {lob.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Stage and Priority */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                  <FileText className="w-4 h-4 inline mr-1" />
                  Stage
                </label>
                <select
                  value={formData.stage}
                  onChange={(e) => setFormData((prev) => ({ ...prev, stage: e.target.value }))}
                  className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
                >
                  {STAGES.map((stage) => (
                    <option key={stage.value} value={stage.value}>
                      {stage.label}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                  <Flag className="w-4 h-4 inline mr-1" />
                  Priority
                </label>
                <select
                  value={formData.priority}
                  onChange={(e) => setFormData((prev) => ({ ...prev, priority: e.target.value }))}
                  className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
                >
                  {PRIORITIES.map((priority) => (
                    <option key={priority} value={priority}>
                      {priority.charAt(0).toUpperCase() + priority.slice(1)}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Assignment */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                  Agent of Record (Partner)
                </label>
                <select
                  value={formData.agent_of_record_id}
                  onChange={(e) =>
                    setFormData((prev) => ({ ...prev, agent_of_record_id: e.target.value }))
                  }
                  className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
                >
                  <option value="">None</option>
                  {dropdownData.partners.length === 0 ? (
                    <option value="" disabled>
                      (No partners yet — add one in Accounts)
                    </option>
                  ) : (
                    dropdownData.partners.map((partner) => (
                      <option key={partner.partner_id} value={partner.partner_id}>
                        {partner.name}
                      </option>
                    ))
                  )}
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                  Assigned Sales Ops
                </label>
                <select
                  value={formData.assigned_sales_ops_id}
                  onChange={(e) =>
                    setFormData((prev) => ({ ...prev, assigned_sales_ops_id: e.target.value }))
                  }
                  className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
                >
                  <option value="">None</option>
                  {dropdownData.users.map((u) => (
                    <option key={u.user_id} value={u.user_id}>
                      {u.name} ({u.role})
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Dates */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                  <Calendar className="w-4 h-4 inline mr-1" />
                  Desired Proposal Date
                </label>
                <input
                  type="date"
                  value={formData.desired_proposal_date}
                  onChange={(e) =>
                    setFormData((prev) => ({ ...prev, desired_proposal_date: e.target.value }))
                  }
                  className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500 [&::-webkit-calendar-picker-indicator]:invert [&::-webkit-calendar-picker-indicator]:brightness-200 [&::-webkit-calendar-picker-indicator]:cursor-pointer"
                />
              </div>
              <div>
                <label
                  htmlFor="opportunity-close-date"
                  className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2"
                >
                  <Calendar className="w-4 h-4 inline mr-1" />
                  Close Date
                </label>
                <input
                  id="opportunity-close-date"
                  type="date"
                  value={formData.close_date}
                  onChange={(e) =>
                    setFormData((prev) => ({ ...prev, close_date: e.target.value }))
                  }
                  className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500 [&::-webkit-calendar-picker-indicator]:invert [&::-webkit-calendar-picker-indicator]:brightness-200 [&::-webkit-calendar-picker-indicator]:cursor-pointer"
                />
                <p className="mt-1 text-xs text-aegisaxiom-text-muted">
                  Drives the close-date alert. Independent of Desired Proposal
                  Date.
                </p>
              </div>
            </div>

            {/* Test Data Flag */}
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="is_test_data"
                checked={formData.is_test_data}
                onChange={(e) =>
                  setFormData((prev) => ({ ...prev, is_test_data: e.target.checked }))
                }
                className="w-4 h-4 rounded border-aegisaxiom-border bg-aegisaxiom-base text-violet-600 focus:ring-violet-500"
              />
              <label htmlFor="is_test_data" className="text-sm text-aegisaxiom-text-secondary">
                Mark as test data
              </label>
            </div>

            {/* ========== NOTES SECTION (edit mode only) ========== */}
            {editMode && opportunity && (
              <div className="pt-4 border-t border-aegisaxiom-border">
                <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3 flex items-center gap-2">
                  <MessageSquare className="w-4 h-4" />
                  Notes ({localNotes.length})
                </h3>

                {/* Add Note */}
                <div className="flex gap-2 mb-3">
                  <input
                    type="text"
                    value={noteText}
                    onChange={(e) => setNoteText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        handleAddNote();
                      }
                    }}
                    placeholder="Add a note or update..."
                    className="flex-1 px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500"
                  />
                  <button
                    type="button"
                    onClick={handleAddNote}
                    disabled={addingNote || !noteText.trim()}
                    className="flex items-center gap-1.5 px-4 py-2 bg-violet-600 hover:bg-violet-700 rounded-lg text-sm text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {addingNote ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <Plus className="w-3.5 h-3.5" />
                    )}
                    Add
                  </button>
                </div>

                {/* Notes List */}
                {localNotes.length > 0 ? (
                  <div className="space-y-2 max-h-48 overflow-y-auto">
                    {[...localNotes].reverse().map((note) => (
                      <div
                        key={note.note_id}
                        className="flex items-start justify-between gap-3 p-3 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg group"
                      >
                        <div className="flex-1 min-w-0">
                          <p className="text-sm text-aegisaxiom-text">{note.text}</p>
                          <p className="text-xs text-aegisaxiom-text-muted mt-1">
                            {note.created_by_name} • {formatDateTime(note.created_at)}
                          </p>
                        </div>
                        <button
                          type="button"
                          onClick={() => handleDeleteNote(note.note_id)}
                          className="p-1 opacity-0 group-hover:opacity-100 hover:bg-aegisaxiom-elevated rounded transition-all flex-shrink-0"
                          title="Delete note"
                        >
                          <Trash2 className="w-3 h-3 text-red-600 dark:text-red-400" />
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-aegisaxiom-text-muted italic">
                    No notes yet. Add one above.
                  </p>
                )}
              </div>
            )}

            {/* ========== ATTACHMENTS SECTION (edit mode only) ========== */}
            {editMode && opportunity && (
              <div className="pt-4 border-t border-aegisaxiom-border">
                <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3 flex items-center gap-2">
                  <Paperclip className="w-4 h-4" />
                  Attachments ({localAttachments.length})
                </h3>

                {/* Upload */}
                <div className="mb-3">
                  <label className="inline-flex items-center gap-2 px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-sm text-aegisaxiom-text-secondary hover:border-aegisaxiom-border-hover hover:text-aegisaxiom-text cursor-pointer transition-colors">
                    <input
                      type="file"
                      multiple
                      onChange={handleFileUpload}
                      className="hidden"
                      disabled={uploading}
                    />
                    {uploading ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Upload className="w-4 h-4" />
                    )}
                    {uploading ? 'Uploading...' : 'Upload File'}
                  </label>
                  <span className="text-xs text-aegisaxiom-text-muted ml-3">
                    Floor plans, photos, documents (max 50MB)
                  </span>
                </div>

                {/* Attachments List */}
                {localAttachments.length > 0 ? (
                  <div className="space-y-2 max-h-48 overflow-y-auto">
                    {localAttachments.map((att) => (
                      <div
                        key={att.attachment_id}
                        className="flex items-center justify-between gap-3 p-3 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg group"
                      >
                        <button
                          type="button"
                          onClick={() => handleViewAttachment(att)}
                          className="flex items-center gap-3 flex-1 min-w-0 text-left"
                          title="Click to view"
                        >
                          <Paperclip className="w-4 h-4 text-aegisaxiom-text-secondary flex-shrink-0" />
                          <div className="flex-1 min-w-0">
                            <p className="text-sm text-aegisaxiom-text truncate hover:underline">{att.filename}</p>
                            <p className="text-xs text-aegisaxiom-text-muted">
                              {formatFileSize(att.file_size)} • {att.uploaded_by_name} •{' '}
                              {formatDateTime(att.uploaded_at)}
                            </p>
                          </div>
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDeleteAttachment(att.attachment_id)}
                          className="p-1 opacity-0 group-hover:opacity-100 hover:bg-aegisaxiom-elevated rounded transition-all flex-shrink-0"
                          title="Delete attachment"
                        >
                          <Trash2 className="w-3 h-3 text-red-600 dark:text-red-400" />
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-aegisaxiom-text-muted italic">
                    No attachments yet. Upload floor plans, photos, or documents above.
                  </p>
                )}
              </div>
            )}
          </div>
        </form>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 p-4 sm:p-6 border-t border-aegisaxiom-border shrink-0">
          <button
            type="button"
            onClick={onClose}
            className="flex-1 sm:flex-none px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-base text-aegisaxiom-text rounded-lg transition-colors text-sm font-medium"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading}
            className="flex-1 sm:flex-none px-4 py-2 bg-violet-600 hover:bg-violet-700 text-white rounded-lg transition-colors disabled:opacity-50 text-sm font-medium whitespace-nowrap"
          >
            {loading ? 'Saving...' : editMode ? 'Update' : 'Create'}
            <span className="hidden sm:inline"> Opportunity</span>
          </button>
        </div>
      </div>
    </div>
  );
}

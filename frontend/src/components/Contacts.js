import React, { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '../lib/apiConfig';
import { useAuth } from '@/contexts/AuthContext';
import { usePermissions } from '../hooks/usePermissions';
import {
  Plus,
  Search,
  X,
  Edit,
  Trash2,
  User,
  Users,
  Mail,
  Phone,
  Smartphone,
  Briefcase,
  Building2,
  Linkedin,
  TestTube,
  UserPlus,
  Star,
  CheckCircle2,
} from 'lucide-react';
import ContactFormModal from './contacts/ContactFormModal';
import ConfirmModal from './shared/ConfirmModal';

const API = API_URL;

// Derive up-to-2-letter initials from a name ("John Doe" → "JD", "acme" → "A")
function getInitials(name) {
  if (!name || typeof name !== 'string') return '?';
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

// Circle with initials. Size: "sm" (36px) or "md" (40px).
function Avatar({ name, size = 'md' }) {
  const sizeCls = size === 'sm' ? 'w-9 h-9 text-xs' : 'w-10 h-10 text-sm';
  return (
    <div
      className={`${sizeCls} rounded-full bg-violet-100 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 flex items-center justify-center font-semibold shrink-0 ring-1 ring-violet-200/60 dark:ring-violet-500/20`}
      aria-hidden="true"
    >
      {getInitials(name)}
    </div>
  );
}

export default function Contacts() {
  const { user } = useAuth();
  const { isAdmin, hasPermission, hasAnyPermission } = usePermissions();
  const canEdit = hasAnyPermission(['contacts.edit', 'contacts.edit.own', 'contacts.create']);
  const canDelete = hasPermission('contacts.delete');
  const canManageUsers = isAdmin() || hasPermission('users.manage');
  const [contacts, setContacts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [initialLoad, setInitialLoad] = useState(true); // FIX 1: track first load
  const [showTestOnly, setShowTestOnly] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState(''); // FIX 1: debounced value
  const [selectedAccount, setSelectedAccount] = useState('');
  const [accounts, setAccounts] = useState([]);

  // Pagination
  const PAGE_SIZE = 50;
  const [page, setPage] = useState(1);

  // Modals
  const [showFormModal, setShowFormModal] = useState(false);
  const [selectedContact, setSelectedContact] = useState(null);
  const [editMode, setEditMode] = useState(false);
  const [confirmState, setConfirmState] = useState({
    open: false,
    action: null,
    title: '',
    message: '',
  });

  // Create User modal state
  const [createUserContact, setCreateUserContact] = useState(null);
  const [createUserPassword, setCreateUserPassword] = useState('');
  const [createUserRole, setCreateUserRole] = useState('customer');
  const [createUserSubmitting, setCreateUserSubmitting] = useState(false);

  // FIX 2: Debounce search — wait 300ms after user stops typing
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(searchQuery), 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  useEffect(() => {
    loadAccounts();
  }, []);

  // FIX 3: Depend on debouncedSearch, not searchQuery
  useEffect(() => {
    setPage(1);
    loadContacts();
  }, [showTestOnly, debouncedSearch, selectedAccount]);

  const loadAccounts = async () => {
    try {
      const response = await api.get(`${API}/api/accounts?limit=1000`);
      const data = await response.json();
      setAccounts(data.items || []);
    } catch (error) {
      console.error('Error loading accounts:', error);
    }
  };

  const loadContacts = async () => {
    try {
      // FIX 4a: Only show full-page spinner on first load
      if (initialLoad) setLoading(true);

      const params = new URLSearchParams();
      if (showTestOnly) params.append('test_only', 'true');
      if (debouncedSearch) params.append('search', debouncedSearch);
      if (selectedAccount) params.append('account_id', selectedAccount);
      params.append('limit', '1000');

      const response = await api.get(`${API}/api/contacts?${params.toString()}`);
      const data = await response.json();
      setContacts(data.items || []);
    } catch (error) {
      console.error('Error loading contacts:', error);
      toast.error('Failed to load contacts');
    } finally {
      setLoading(false);
      setInitialLoad(false);
    }
  };

  const handleCreate = () => {
    setSelectedContact(null);
    setEditMode(false);
    setShowFormModal(true);
  };

  const handleEdit = (contact) => {
    setSelectedContact(contact);
    setEditMode(true);
    setShowFormModal(true);
  };

  const handleDelete = (contactId) => {
    setConfirmState({
      open: true,
      title: 'Delete Contact',
      message: 'Are you sure you want to delete this contact? This action cannot be undone.',
      action: async () => {
        try {
          const response = await api.delete(`${API}/api/contacts/${contactId}`);
          await response.json();
          toast.success('Contact deleted');
          loadContacts();
        } catch (error) {
          console.error('Error deleting contact:', error);
          const errorData = await error.response?.json();
          toast.error(errorData?.detail || 'Failed to delete contact');
        }
      },
    });
  };

  const handleCreateUser = (contact) => {
    setCreateUserContact(contact);
    setCreateUserPassword('');
    setCreateUserRole('customer');
  };

  const submitCreateUser = async () => {
    if (!createUserPassword || createUserPassword.length < 6) {
      toast.error('Password must be at least 6 characters');
      return;
    }
    setCreateUserSubmitting(true);
    try {
      const response = await api.post(
        `${API}/api/contacts/${createUserContact.contact_id}/create-user?password=${encodeURIComponent(createUserPassword)}&role=${createUserRole}`,
      );
      await response.json();
      toast.success('User account created successfully');
      setCreateUserContact(null);
      loadContacts();
    } catch (error) {
      console.error('Error creating user:', error);
      const errorData = await error.response?.json();
      toast.error(errorData?.detail || 'Failed to create user');
    } finally {
      setCreateUserSubmitting(false);
    }
  };

  const totalPages = Math.max(1, Math.ceil(contacts.length / PAGE_SIZE));
  const pageStart = (page - 1) * PAGE_SIZE;
  const pageEnd = Math.min(pageStart + PAGE_SIZE, contacts.length);
  const pagedContacts = contacts.slice(pageStart, pageEnd);

  // FIX 4b: Only unmount page for spinner on initial load
  if (loading && initialLoad) {
    return (
      <div className="min-h-screen bg-aegisaxiom-base flex items-center justify-center">
        <div className="w-10 h-10 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-aegisaxiom-base text-aegisaxiom-text p-4 sm:p-6">
      {/* Header */}
      <div className="mb-5 sm:mb-6">
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3 mb-4">
          <div className="min-w-0">
            <h1 className="text-xl sm:text-2xl font-bold text-aegisaxiom-text">Contacts</h1>
            <p className="text-aegisaxiom-text-secondary text-xs sm:text-sm mt-1">
              Manage contacts associated with customer accounts
            </p>
          </div>
          {canEdit && (
            <button
              onClick={handleCreate}
              className="flex items-center justify-center gap-2 px-4 py-2 bg-violet-600 hover:bg-violet-700 text-white rounded-lg transition-colors shrink-0 w-full sm:w-auto"
            >
              <Plus className="w-4 h-4" />
              <span>New Contact</span>
            </button>
          )}
        </div>

        {/* Toolbar */}
        <div className="flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-4">
          {/* Search */}
          <div className="relative w-full sm:flex-1 sm:max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-aegisaxiom-text-secondary" />
            <input
              type="text"
              placeholder="Search contacts..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-10 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 text-sm"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                className="absolute right-3 top-1/2 -translate-y-1/2"
                aria-label="Clear search"
              >
                <X className="w-4 h-4 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text" />
              </button>
            )}
          </div>

          {/* Account Filter */}
          <select
            value={selectedAccount}
            onChange={(e) => setSelectedAccount(e.target.value)}
            className="px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500 text-sm w-full sm:w-auto"
          >
            <option value="">All Accounts</option>
            {accounts.map((acc) => (
              <option key={acc.account_id} value={acc.account_id}>
                {acc.name}
              </option>
            ))}
          </select>

          {/* Test Data Filter */}
          <button
            onClick={() => setShowTestOnly(!showTestOnly)}
            aria-pressed={showTestOnly}
            className={`flex items-center justify-center gap-2 px-4 py-2 rounded-lg border transition-colors text-sm shrink-0 w-full sm:w-auto ${
              showTestOnly
                ? 'bg-amber-50 dark:bg-amber-600/20 border-amber-600 text-amber-700 dark:text-amber-300'
                : 'bg-aegisaxiom-base border-aegisaxiom-border text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'
            }`}
          >
            <TestTube className="w-4 h-4" />
            <span>Test Data Only</span>
          </button>
        </div>
      </div>

      {/* Stats */}
      <div className="mb-5 sm:mb-6 grid grid-cols-3 gap-3 sm:gap-4">
        <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3 sm:p-4 flex items-center gap-3">
          <div className="w-9 h-9 sm:w-10 sm:h-10 rounded-lg bg-violet-100 dark:bg-violet-600/15 flex items-center justify-center shrink-0">
            <Users className="w-4 h-4 sm:w-5 sm:h-5 text-violet-600 dark:text-violet-300" />
          </div>
          <div className="min-w-0">
            <div className="text-aegisaxiom-text-secondary text-[11px] sm:text-xs font-medium">Total</div>
            <div className="text-xl sm:text-2xl font-bold text-aegisaxiom-text leading-tight">
              {contacts.length}
            </div>
          </div>
        </div>
        <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3 sm:p-4 flex items-center gap-3">
          <div className="w-9 h-9 sm:w-10 sm:h-10 rounded-lg bg-blue-100 dark:bg-blue-600/15 flex items-center justify-center shrink-0">
            <Mail className="w-4 h-4 sm:w-5 sm:h-5 text-blue-600 dark:text-blue-300" />
          </div>
          <div className="min-w-0">
            <div className="text-aegisaxiom-text-secondary text-[11px] sm:text-xs font-medium">
              With Email
            </div>
            <div className="text-xl sm:text-2xl font-bold text-aegisaxiom-text leading-tight">
              {contacts.filter((c) => c.email).length}
            </div>
          </div>
        </div>
        <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3 sm:p-4 flex items-center gap-3">
          <div className="w-9 h-9 sm:w-10 sm:h-10 rounded-lg bg-green-100 dark:bg-green-600/15 flex items-center justify-center shrink-0">
            <CheckCircle2 className="w-4 h-4 sm:w-5 sm:h-5 text-green-600 dark:text-green-300" />
          </div>
          <div className="min-w-0">
            <div className="text-aegisaxiom-text-secondary text-[11px] sm:text-xs font-medium">
              Active
            </div>
            <div className="text-xl sm:text-2xl font-bold text-aegisaxiom-text leading-tight">
              {contacts.filter((c) => c.is_active).length}
            </div>
          </div>
        </div>
      </div>

      {/* Contacts — Desktop table */}
      <div className="hidden md:block bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-aegisaxiom-surface border-b border-aegisaxiom-border">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                  Contact
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                  Account
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                  Sales Rep
                </th>
                <th className="hidden lg:table-cell px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                  Title
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                  Email
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                  Phone
                </th>
                <th className="hidden xl:table-cell px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary">
                  Mobile
                </th>
                <th className="px-4 py-3 text-center text-xs font-semibold text-aegisaxiom-text-secondary">
                  Status
                </th>
                <th className="px-4 py-3 text-right text-xs font-semibold text-aegisaxiom-text-secondary">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-aegisaxiom-border">
              {contacts.length === 0 ? (
                <tr>
                  <td colSpan="9" className="px-4 py-14 text-center">
                    <div className="flex flex-col items-center">
                      <div className="w-14 h-14 rounded-full bg-violet-100 dark:bg-violet-600/15 flex items-center justify-center mb-3">
                        <Users className="w-6 h-6 text-violet-600 dark:text-violet-300" />
                      </div>
                      <p className="text-aegisaxiom-text font-semibold text-sm mb-1">
                        {searchQuery || selectedAccount || showTestOnly
                          ? 'No contacts match your filters'
                          : 'No contacts yet'}
                      </p>
                      <p className="text-aegisaxiom-text-muted text-xs mb-4 max-w-xs">
                        {searchQuery || selectedAccount || showTestOnly
                          ? 'Try adjusting your search or filters.'
                          : 'Add your first contact to start tracking people at your customer accounts.'}
                      </p>
                      {!searchQuery && !selectedAccount && !showTestOnly && (
                        <button
                          onClick={handleCreate}
                          className="inline-flex items-center gap-2 px-4 py-2 bg-violet-600 hover:bg-violet-700 text-white rounded-lg text-sm transition-colors"
                        >
                          <Plus className="w-4 h-4" />
                          New Contact
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ) : (
                pagedContacts.map((contact) => (
                  <tr key={contact.contact_id} className="hover:bg-aegisaxiom-surface transition-colors">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-3">
                        <div className="relative">
                          <Avatar name={contact.name} size="md" />
                          {contact.is_primary && (
                            <Star
                              className="absolute -top-0.5 -right-0.5 w-3.5 h-3.5 text-yellow-500 fill-yellow-500 drop-shadow-sm"
                              title="Primary Contact"
                            />
                          )}
                        </div>
                        <div className="min-w-0">
                          <div className="font-medium text-aegisaxiom-text text-sm truncate">
                            {contact.name}
                          </div>
                          {contact.department && (
                            <div className="text-xs text-aegisaxiom-text-secondary truncate">
                              {contact.department}
                            </div>
                          )}
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-sm text-aegisaxiom-text-secondary">
                      {contact.account_name}
                    </td>
                    <td className="px-4 py-3 text-sm text-aegisaxiom-text-secondary">
                      {contact.assigned_sales_rep_name || '-'}
                    </td>
                    <td className="hidden lg:table-cell px-4 py-3 text-sm text-aegisaxiom-text-secondary">
                      {contact.title || '-'}
                    </td>
                    <td className="px-4 py-3">
                      {contact.email ? (
                        <a
                          href={`mailto:${contact.email}`}
                          className="text-sm text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:hover:text-violet-300"
                        >
                          {contact.email}
                        </a>
                      ) : (
                        <span className="text-aegisaxiom-text-muted">-</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm text-aegisaxiom-text-secondary">
                      {contact.phone || '-'}
                    </td>
                    <td className="hidden xl:table-cell px-4 py-3 text-sm text-aegisaxiom-text-secondary">
                      {contact.mobile_phone || '-'}
                    </td>
                    <td className="px-4 py-3 text-center">
                      {contact.is_active ? (
                        <span
                          className="inline-block w-2 h-2 bg-green-500 rounded-full"
                          title="Active"
                        />
                      ) : (
                        <span
                          className="inline-block w-2 h-2 bg-aegisaxiom-elevated rounded-full"
                          title="Inactive"
                        />
                      )}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex justify-end gap-2">
                        {contact.linkedin_url && (
                          <a
                            href={contact.linkedin_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="p-1 hover:bg-aegisaxiom-elevated rounded"
                            title="LinkedIn"
                            aria-label={`${contact.name} LinkedIn profile`}
                          >
                            <Linkedin className="w-4 h-4 text-blue-600 dark:text-blue-400" />
                          </a>
                        )}
                        {canManageUsers && !contact.linked_user_id && (
                          <button
                            onClick={() => handleCreateUser(contact)}
                            className="p-1 hover:bg-aegisaxiom-elevated rounded"
                            title="Create User Account"
                            aria-label={`Create user account for ${contact.name}`}
                          >
                            <UserPlus className="w-4 h-4 text-green-600 dark:text-green-400" />
                          </button>
                        )}
                        {canEdit && (
                          <button
                            onClick={() => handleEdit(contact)}
                            className="p-1 hover:bg-aegisaxiom-elevated rounded"
                            title="Edit"
                            aria-label={`Edit ${contact.name}`}
                          >
                            <Edit className="w-4 h-4 text-aegisaxiom-text-secondary" />
                          </button>
                        )}
                        {canDelete && (
                          <button
                            onClick={() => handleDelete(contact.contact_id)}
                            className="p-1 hover:bg-aegisaxiom-elevated rounded"
                            title="Delete"
                            aria-label={`Delete ${contact.name}`}
                          >
                            <Trash2 className="w-4 h-4 text-red-600 dark:text-red-400" />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Contacts — Mobile card list */}
      <div className="md:hidden">
        {contacts.length === 0 ? (
          <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg px-6 py-10 text-center">
            <div className="w-12 h-12 rounded-full bg-violet-100 dark:bg-violet-600/15 flex items-center justify-center mx-auto mb-3">
              <Users className="w-5 h-5 text-violet-600 dark:text-violet-300" />
            </div>
            <p className="text-aegisaxiom-text font-semibold text-sm mb-1">
              {searchQuery || selectedAccount || showTestOnly
                ? 'No contacts match your filters'
                : 'No contacts yet'}
            </p>
            <p className="text-aegisaxiom-text-muted text-xs mb-4 leading-relaxed">
              {searchQuery || selectedAccount || showTestOnly
                ? 'Try adjusting your search or filters.'
                : 'Add your first contact to start tracking people at your customer accounts.'}
            </p>
            {!searchQuery && !selectedAccount && !showTestOnly && (
              <button
                onClick={handleCreate}
                className="inline-flex items-center gap-2 px-4 py-2 bg-violet-600 hover:bg-violet-700 text-white rounded-lg text-sm transition-colors"
              >
                <Plus className="w-4 h-4" />
                New Contact
              </button>
            )}
          </div>
        ) : (
          pagedContacts.map((contact) => (
            <div
              key={contact.contact_id}
              className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3.5 mb-3"
            >
              {/* Header: avatar + name + status pill */}
              <div className="flex items-start justify-between gap-3 mb-3">
                <div className="flex items-start gap-3 min-w-0 flex-1">
                  <div className="relative shrink-0">
                    <Avatar name={contact.name} size="sm" />
                    {contact.is_primary && (
                      <Star
                        className="absolute -top-0.5 -right-0.5 w-3.5 h-3.5 text-yellow-500 fill-yellow-500 drop-shadow-sm"
                        aria-label="Primary contact"
                      />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="font-semibold text-aegisaxiom-text text-sm truncate leading-tight">
                      {contact.name}
                    </div>
                    {contact.title && (
                      <div className="text-xs text-aegisaxiom-text-secondary truncate mt-0.5">
                        {contact.title}
                      </div>
                    )}
                    {contact.department && (
                      <div className="text-[11px] text-aegisaxiom-text-muted truncate">
                        {contact.department}
                      </div>
                    )}
                  </div>
                </div>
                <span
                  className={`shrink-0 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium border ${
                    contact.is_active
                      ? 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300 border-green-500/40'
                      : 'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary border-aegisaxiom-border'
                  }`}
                >
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${
                      contact.is_active ? 'bg-green-500' : 'bg-aegisaxiom-text-muted'
                    }`}
                  />
                  {contact.is_active ? 'Active' : 'Inactive'}
                </span>
              </div>

              {/* Account + sales rep */}
              <div className="flex items-center gap-1.5 text-xs text-aegisaxiom-text-secondary mb-2.5 min-w-0">
                <Building2 className="w-3.5 h-3.5 shrink-0" />
                <span className="truncate">{contact.account_name}</span>
                {contact.assigned_sales_rep_name && (
                  <>
                    <span className="text-aegisaxiom-text-muted">•</span>
                    <span className="truncate">{contact.assigned_sales_rep_name}</span>
                  </>
                )}
              </div>

              {/* Contact details */}
              <div className="mb-3 text-xs">
                {contact.email && (
                  <div className="flex items-center gap-2 min-w-0 mb-1.5">
                    <Mail className="w-3.5 h-3.5 text-aegisaxiom-text-muted shrink-0" />
                    <a
                      href={`mailto:${contact.email}`}
                      className="text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:hover:text-violet-300 truncate"
                    >
                      {contact.email}
                    </a>
                  </div>
                )}
                {contact.phone && (
                  <div className="flex items-center gap-2 mb-1.5">
                    <Phone className="w-3.5 h-3.5 text-aegisaxiom-text-muted shrink-0" />
                    <a
                      href={`tel:${contact.phone}`}
                      className="text-aegisaxiom-text hover:text-violet-600 dark:hover:text-violet-400"
                    >
                      {contact.phone}
                    </a>
                  </div>
                )}
                {contact.mobile_phone && (
                  <div className="flex items-center gap-2 mb-1.5">
                    <Smartphone className="w-3.5 h-3.5 text-aegisaxiom-text-muted shrink-0" />
                    <a
                      href={`tel:${contact.mobile_phone}`}
                      className="text-aegisaxiom-text hover:text-violet-600 dark:hover:text-violet-400"
                    >
                      {contact.mobile_phone}
                    </a>
                  </div>
                )}
                {!contact.email && !contact.phone && !contact.mobile_phone && (
                  <div className="text-aegisaxiom-text-muted">No contact details</div>
                )}
              </div>

              {/* Actions */}
              <div className="flex items-center gap-2 pt-3 border-t border-aegisaxiom-border">
                {contact.linkedin_url && (
                  <a
                    href={contact.linkedin_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="p-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface rounded-md"
                    title="LinkedIn"
                    aria-label={`${contact.name} LinkedIn profile`}
                  >
                    <Linkedin className="w-4 h-4 text-blue-600 dark:text-blue-400" />
                  </a>
                )}
                {canManageUsers && !contact.linked_user_id && (
                  <button
                    onClick={() => handleCreateUser(contact)}
                    className="p-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface rounded-md"
                    title="Create user account"
                    aria-label={`Create user account for ${contact.name}`}
                  >
                    <UserPlus className="w-4 h-4 text-green-600 dark:text-green-400" />
                  </button>
                )}
                {canEdit && (
                  <button
                    onClick={() => handleEdit(contact)}
                    className="flex-1 flex items-center justify-center gap-1.5 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface rounded-md text-xs text-aegisaxiom-text font-medium"
                  >
                    <Edit className="w-3.5 h-3.5" />
                    Edit
                  </button>
                )}
                {canDelete && (
                  <button
                    onClick={() => handleDelete(contact.contact_id)}
                    className="p-2 bg-aegisaxiom-elevated hover:bg-red-600 hover:text-white text-red-600 dark:text-red-400 rounded-md transition-colors"
                    title="Delete"
                    aria-label={`Delete ${contact.name}`}
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                )}
              </div>
            </div>
          ))
        )}
      </div>

      {/* Pagination */}
      {contacts.length > PAGE_SIZE && (
        <div className="mt-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 text-sm">
          <span className="text-aegisaxiom-text-secondary text-center sm:text-left">
            Showing {pageStart + 1}–{pageEnd} of {contacts.length}
          </span>
          <div className="flex items-center justify-center gap-2">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="px-3 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text-secondary hover:bg-aegisaxiom-surface hover:text-aegisaxiom-text transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Previous
            </button>
            <span className="text-aegisaxiom-text-secondary whitespace-nowrap">
              Page {page} of {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className="px-3 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text-secondary hover:bg-aegisaxiom-surface hover:text-aegisaxiom-text transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Next
            </button>
          </div>
        </div>
      )}

      {/* Modals */}
      {showFormModal && (
        <ContactFormModal
          contact={selectedContact}
          editMode={editMode}
          onClose={() => {
            setShowFormModal(false);
            setSelectedContact(null);
          }}
          onSave={() => {
            setShowFormModal(false);
            setSelectedContact(null);
            loadContacts();
          }}
        />
      )}
      <ConfirmModal
        isOpen={confirmState.open}
        title={confirmState.title}
        message={confirmState.message}
        confirmText="Delete"
        onConfirm={() => confirmState.action?.()}
        onCancel={() => setConfirmState({ open: false, action: null, title: '', message: '' })}
      />

      {/* Create User modal */}
      {createUserContact && (
        <div
          className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
          onClick={() => !createUserSubmitting && setCreateUserContact(null)}
          role="dialog"
          aria-modal="true"
          aria-label="Create user account"
        >
          <div
            className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-md overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="p-5 sm:p-6 border-b border-aegisaxiom-border">
              <h3 className="text-lg font-semibold text-aegisaxiom-text mb-1">Create User Account</h3>
              <p className="text-sm text-aegisaxiom-text-secondary">
                Creating login for{' '}
                <span className="text-aegisaxiom-text font-medium">{createUserContact.name}</span>
              </p>
            </div>

            <div className="p-5 sm:p-6 space-y-4">
              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text mb-1.5">
                  Initial password
                </label>
                <input
                  type="password"
                  value={createUserPassword}
                  onChange={(e) => setCreateUserPassword(e.target.value)}
                  placeholder="At least 6 characters"
                  className="w-full px-3 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 text-sm"
                  autoFocus
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text mb-1.5">Role</label>
                <select
                  value={createUserRole}
                  onChange={(e) => setCreateUserRole(e.target.value)}
                  className="w-full px-3 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500 text-sm"
                >
                  <option value="customer">Customer</option>
                  <option value="agent">Agent</option>
                  <option value="sales_rep">Sales Rep</option>
                  <option value="contractor">Contractor</option>
                </select>
              </div>
            </div>

            <div className="p-5 sm:p-6 border-t border-aegisaxiom-border flex flex-col-reverse sm:flex-row sm:justify-end gap-2">
              <button
                onClick={() => setCreateUserContact(null)}
                disabled={createUserSubmitting}
                className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text text-sm font-medium rounded-lg transition disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={submitCreateUser}
                disabled={createUserSubmitting || !createUserPassword}
                className="px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium rounded-lg transition disabled:opacity-50 flex items-center justify-center gap-2"
              >
                {createUserSubmitting ? (
                  <>
                    <div className="w-4 h-4 border-2 border-white/40 border-t-white rounded-full animate-spin" />
                    Creating...
                  </>
                ) : (
                  <>
                    <UserPlus className="w-4 h-4" />
                    Create User
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

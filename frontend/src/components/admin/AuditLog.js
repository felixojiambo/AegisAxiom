import React, { useEffect, useState, useCallback, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, unwrap } from '@/lib/api';
import {
  ScrollText,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  Filter,
  Download,
  Clock,
  User,
  Shield,
  Trash2,
  Edit,
  LogIn,
  Key,
  UserPlus,
  Search,
  X,
  MessageCircle,
  Bot,
  BarChart3,
  DollarSign,
  Cpu,
  Settings2,
  Save,
  Check,
  Plus,
  Pencil,
} from 'lucide-react';
import { toast } from 'sonner';
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  PieChart,
  Pie,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  Cell,
  Legend,
} from 'recharts';
import ChatMarkdown from '../ChatMarkdown';
import API_URL from '../../lib/apiConfig';

const API = API_URL;

// Security event_type → human label + icon + color. Security events live
// in the same audit_logs collection as activity events, distinguished by
// event_category. The UI surfaces them through this lookup when an entry
// carries an event_type value.
const EVENT_TYPE_META = {
  authz_failed: {
    label: 'Permission Denied',
    icon: Shield,
    color:
      'bg-red-50 dark:bg-red-500/15 text-red-700 dark:text-red-300 border-red-500/20',
  },
  auth_failed: {
    label: 'Auth Failed',
    icon: Shield,
    color:
      'bg-red-50 dark:bg-red-500/15 text-red-700 dark:text-red-300 border-red-500/20',
  },
  role_changed: {
    label: 'Role Changed',
    icon: Shield,
    color:
      'bg-violet-50 dark:bg-violet-500/15 text-violet-700 dark:text-violet-300 border-violet-500/20',
  },
  user_created: {
    label: 'User Created',
    icon: UserPlus,
    color:
      'bg-emerald-50 dark:bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-emerald-500/20',
  },
  user_deleted: {
    label: 'User Deleted',
    icon: Trash2,
    color: 'bg-red-50 dark:bg-red-500/15 text-red-700 dark:text-red-300 border-red-500/20',
  },
  user_deactivated: {
    label: 'User Deactivated',
    icon: User,
    color: 'bg-amber-50 dark:bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/20',
  },
  ai_employee_created: {
    label: 'AI Employee Created',
    icon: Bot,
    color:
      'bg-emerald-50 dark:bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-emerald-500/20',
  },
  ai_employee_deleted: {
    label: 'AI Employee Deleted',
    icon: Bot,
    color: 'bg-red-50 dark:bg-red-500/15 text-red-700 dark:text-red-300 border-red-500/20',
  },
  ai_access_changed: {
    label: 'AI Access Changed',
    icon: Bot,
    color: 'bg-amber-50 dark:bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/20',
  },
  session_approved: {
    label: 'Session Approved',
    icon: Check,
    color:
      'bg-emerald-50 dark:bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-emerald-500/20',
  },
  data_exported: {
    label: 'Data Exported',
    icon: Download,
    color: 'bg-cyan-50 dark:bg-cyan-500/15 text-cyan-700 dark:text-cyan-300 border-cyan-500/20',
  },
  view_as_used: {
    label: 'View-As Used',
    icon: User,
    color: 'bg-amber-50 dark:bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/20',
  },
  password_reset: {
    label: 'Password Reset',
    icon: Key,
    color:
      'bg-amber-50 dark:bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/20',
  },
  invite_sent: {
    label: 'Invite Sent',
    icon: UserPlus,
    color:
      'bg-blue-50 dark:bg-blue-500/15 text-blue-700 dark:text-blue-300 border-blue-500/20',
  },
};

// Severity → small badge color. info entries get no badge to avoid noise.
const SEVERITY_BADGE = {
  warning: 'bg-amber-500/20 text-amber-700 dark:text-amber-300',
  critical: 'bg-red-500/20 text-red-700 dark:text-red-300',
};

const ACTION_META = {
  login: {
    label: 'Login',
    icon: LogIn,
    color: 'bg-blue-50 dark:bg-blue-500/15 text-blue-700 dark:text-blue-300 border-blue-500/20',
  },
  password_reset: {
    label: 'Password Reset',
    icon: Key,
    color:
      'bg-amber-50 dark:bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/20',
  },
  user_invite: {
    label: 'User Invited',
    icon: UserPlus,
    color:
      'bg-emerald-50 dark:bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-emerald-500/20',
  },
  user_update: {
    label: 'User Updated',
    icon: Edit,
    color: 'bg-cyan-500/15 text-cyan-700 dark:text-cyan-300 border-cyan-500/20',
  },
  user_delete: {
    label: 'User Deleted',
    icon: Trash2,
    color: 'bg-red-50 dark:bg-red-500/15 text-red-700 dark:text-red-300 border-red-500/20',
  },
  account_delete: {
    label: 'Account Deleted',
    icon: Trash2,
    color: 'bg-red-50 dark:bg-red-500/15 text-red-700 dark:text-red-300 border-red-500/20',
  },
  deal_delete: {
    label: 'Deal Deleted',
    icon: Trash2,
    color: 'bg-red-50 dark:bg-red-500/15 text-red-700 dark:text-red-300 border-red-500/20',
  },
  role_create: {
    label: 'Role Created',
    icon: Shield,
    color:
      'bg-violet-50 dark:bg-violet-500/15 text-violet-700 dark:text-violet-300 border-violet-500/20',
  },
  role_update: {
    label: 'Role Updated',
    icon: Shield,
    color:
      'bg-violet-50 dark:bg-violet-500/15 text-violet-700 dark:text-violet-300 border-violet-500/20',
  },
};

const RESOURCE_BADGE = {
  auth: 'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300',
  user: 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300',
  account: 'bg-orange-50 dark:bg-orange-600/20 text-orange-700 dark:text-orange-300',
  deal: 'bg-cyan-600/20 text-cyan-700 dark:text-cyan-300',
  role: 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300',
  ai_employee: 'bg-purple-50 dark:bg-purple-600/20 text-purple-700 dark:text-purple-300',
  ai_tool: 'bg-pink-50 dark:bg-pink-600/20 text-pink-700 dark:text-pink-300',
};

const PAGE_SIZE = 25;

export default function AuditLog() {
  const [activeTab, setActiveTab] = useState('audit');
  return (
    <div className="space-y-5" data-testid="audit-log-page">
      {/* Tab Header */}
      <div
        className="flex flex-wrap items-center gap-1 bg-aegisaxiom-surface rounded-xl p-1 w-fit max-w-full border border-aegisaxiom-border"
        style={{ marginBottom: '1rem' }}
      >
        <button
          onClick={() => setActiveTab('audit')}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${activeTab === 'audit' ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border border-violet-200 dark:border-violet-600/20' : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'}`}
          data-testid="tab-audit-log"
        >
          <ScrollText size={14} className="inline mr-1.5 -mt-0.5" />
          Audit Log
        </button>
        <button
          onClick={() => setActiveTab('chatlogs')}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${activeTab === 'chatlogs' ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border border-violet-200 dark:border-violet-600/20' : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'}`}
          data-testid="tab-chat-logs"
        >
          <MessageCircle size={14} className="inline mr-1.5 -mt-0.5" />
          AI Chat Logs
        </button>
        <button
          onClick={() => setActiveTab('usage')}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${activeTab === 'usage' ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border border-violet-200 dark:border-violet-600/20' : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'}`}
          data-testid="tab-ai-usage"
        >
          <BarChart3 size={14} className="inline mr-1.5 -mt-0.5" />
          AI Usage & Costs
        </button>
      </div>

      {activeTab === 'audit' && <AuditLogTab />}
      {activeTab === 'chatlogs' && <ChatLogsTab />}
      {activeTab === 'usage' && <AIUsageTab />}
    </div>
  );
}

// ══════════════════════════════════════════════
//  CHAT LOGS TAB
// ══════════════════════════════════════════════

function ChatLogsTab() {
  const [sessions, setSessions] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(false);
  const [userFilter, setUserFilter] = useState('');
  const [chatNumberSearch, setChatNumberSearch] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [expandedSession, setExpandedSession] = useState(null);
  const [expandedMessages, setExpandedMessages] = useState([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [users, setUsers] = useState([]);

  const PAGE_SIZE = 20;

  const loadUsers = useCallback(async () => {
    try {
      const res = await api.get(`${API}/api/admin/users`);
      if (res.ok) setUsers(await res.json());
    } catch {}
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    let url = `${API}/api/chat/admin/logs?skip=${page * PAGE_SIZE}&limit=${PAGE_SIZE}`;
    if (userFilter) url += `&user_id=${userFilter}`;
    if (chatNumberSearch) url += `&chat_number=${encodeURIComponent(chatNumberSearch)}`;
    if (dateFrom) url += `&date_from=${dateFrom}`;
    if (dateTo) url += `&date_to=${dateTo}`;
    try {
      const res = await api.get(url);
      if (res.ok) {
        const data = await res.json();
        setSessions(data.items || []);
        setTotal(data.total || 0);
      }
    } catch {
      toast.error('Failed to load chat logs');
    }
    setLoading(false);
  }, [page, userFilter, chatNumberSearch, dateFrom, dateTo]);

  useEffect(() => {
    loadUsers();
  }, [loadUsers]);
  useEffect(() => {
    load();
  }, [load]);

  const loadMessages = async (sessionId) => {
    if (expandedSession === sessionId) {
      setExpandedSession(null);
      return;
    }
    setMessagesLoading(true);
    setExpandedSession(sessionId);
    try {
      const res = await api.get(`${API}/api/chat/admin/logs/${sessionId}/messages`);
      if (res.ok) {
        const data = await res.json();
        setExpandedMessages(data.messages || []);
      }
    } catch {
      toast.error('Failed to load messages');
    }
    setMessagesLoading(false);
  };

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const hasFilters = userFilter || chatNumberSearch || dateFrom || dateTo;

  const clearFilters = () => {
    setUserFilter('');
    setChatNumberSearch('');
    setDateFrom('');
    setDateTo('');
    setPage(0);
  };

  const fmtTime = (ts) => {
    if (!ts) return '—';
    const d = new Date(ts);
    return d.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="page-title" data-testid="chat-logs-title">
            AI Chat Logs
          </h1>
          <p className="text-aegisaxiom-text-muted text-xs mt-0.5">{total} total chat sessions</p>
        </div>
        <button
          onClick={load}
          className="btn-secondary flex items-center gap-2 text-xs"
          data-testid="refresh-chat-logs-btn"
        >
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {/* Filters */}
      <div className="glass-card p-4" data-testid="chat-log-filters">
        <div className="flex items-center gap-2 mb-3">
          <Filter size={14} className="text-aegisaxiom-text-muted" />
          <span className="text-xs font-medium text-aegisaxiom-text-secondary uppercase tracking-wider">
            Filters
          </span>
          {hasFilters && (
            <button
              onClick={clearFilters}
              className="ml-auto text-xs text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary flex items-center gap-1"
              data-testid="clear-chat-filters"
            >
              <X size={12} />
              Clear all
            </button>
          )}
        </div>
        <div className="flex flex-wrap gap-3">
          {/* Chat number search */}
          <div className="flex-1 min-w-[180px]">
            <label className="text-[11px] text-aegisaxiom-text-muted block mb-1.5">Chat #</label>
            <div className="relative">
              <Search
                size={13}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted"
              />
              <input
                type="text"
                placeholder="e.g. CHAT-00001"
                value={chatNumberSearch}
                onChange={(e) => {
                  setChatNumberSearch(e.target.value);
                  setPage(0);
                }}
                className="input-field text-xs w-full pl-8"
                data-testid="chat-number-filter"
              />
            </div>
          </div>
          {/* User filter */}
          <div className="flex-1 min-w-[180px]">
            <label className="text-[11px] text-aegisaxiom-text-muted block mb-1.5">User</label>
            <select
              value={userFilter}
              onChange={(e) => {
                setUserFilter(e.target.value);
                setPage(0);
              }}
              className="input-field text-xs w-full"
              data-testid="chat-user-filter"
            >
              <option value="">All users</option>
              {users.map((u) => (
                <option key={u.user_id} value={u.user_id}>
                  {u.name} ({u.email})
                </option>
              ))}
            </select>
          </div>
          {/* Date from */}
          <div className="flex-1 min-w-[140px]">
            <label className="text-[11px] text-aegisaxiom-text-muted block mb-1.5">From</label>
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => {
                setDateFrom(e.target.value);
                setPage(0);
              }}
              className="input-field text-xs w-full"
              data-testid="chat-date-from"
            />
          </div>
          {/* Date to */}
          <div className="flex-1 min-w-[140px]">
            <label className="text-[11px] text-aegisaxiom-text-muted block mb-1.5">To</label>
            <input
              type="date"
              value={dateTo}
              onChange={(e) => {
                setDateTo(e.target.value);
                setPage(0);
              }}
              className="input-field text-xs w-full"
              data-testid="chat-date-to"
            />
          </div>
        </div>
      </div>

      {/* Sessions Table */}
      {sessions.length === 0 ? (
        <div className="glass-card p-16 text-center" data-testid="chat-logs-empty">
          <MessageCircle size={32} className="text-aegisaxiom-text-muted mx-auto mb-3" />
          <p className="text-aegisaxiom-text-muted">
            {hasFilters ? 'No chat sessions match your filters.' : 'No chat sessions found.'}
          </p>
        </div>
      ) : (
        <div className="glass-card overflow-hidden" data-testid="chat-logs-table-card">
          {/* Desktop table */}
          <div className="hidden md:block overflow-x-auto">
            <table className="w-full text-sm" data-testid="chat-logs-table">
              <thead>
                <tr className="border-b border-aegisaxiom-border">
                  {['Chat #', 'User', 'Title', 'Messages', 'Created', 'Last Active'].map((h) => (
                    <th
                      key={h}
                      className="text-left py-3 px-4 text-[11px] font-medium text-aegisaxiom-text-muted uppercase tracking-wider"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <React.Fragment key={s.session_id}>
                    <tr
                      className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-accent-muted cursor-pointer transition-colors"
                      onClick={() => loadMessages(s.session_id)}
                      data-testid={`chat-log-row-${s.session_id}`}
                    >
                      <td className="py-3 px-4">
                        <span
                          className="font-mono text-xs bg-violet-50 dark:bg-violet-600/15 text-violet-700 dark:text-violet-300 px-2 py-0.5 rounded"
                          data-testid={`chat-num-${s.session_id}`}
                        >
                          {s.chat_number || '—'}
                        </span>
                      </td>
                      <td className="py-3 px-4">
                        <div>
                          <p className="text-xs text-aegisaxiom-text">{s.user_name || '—'}</p>
                          <p className="text-[11px] text-aegisaxiom-text-muted">
                            {s.user_email || s.user_id}
                          </p>
                        </div>
                      </td>
                      <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs max-w-[200px] truncate">
                        {s.title || 'New Chat'}
                      </td>
                      <td className="py-3 px-4 text-center">
                        <span className="text-xs bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary px-2 py-0.5 rounded-full">
                          {s.message_count || 0}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs whitespace-nowrap">
                        {fmtTime(s.created_at)}
                      </td>
                      <td className="py-3 px-4 text-aegisaxiom-text-secondary text-xs whitespace-nowrap">
                        {fmtTime(s.updated_at)}
                      </td>
                    </tr>
                    {expandedSession === s.session_id && (
                      <tr className="bg-aegisaxiom-surface">
                        <td colSpan={6} className="p-4">
                          {messagesLoading ? (
                            <div className="text-center py-6">
                              <p className="text-aegisaxiom-text-muted text-xs">Loading messages...</p>
                            </div>
                          ) : expandedMessages.length === 0 ? (
                            <div className="text-center py-6">
                              <p className="text-aegisaxiom-text-muted text-xs">
                                No messages in this session.
                              </p>
                            </div>
                          ) : (
                            <div
                              className="max-h-96 overflow-y-auto space-y-3 px-2"
                              data-testid={`chat-log-messages-${s.session_id}`}
                            >
                              {expandedMessages.map((m, i) => (
                                <div
                                  key={m.message_id || i}
                                  className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
                                >
                                  <div
                                    className={`flex gap-2 max-w-xl ${m.role === 'user' ? 'flex-row-reverse' : ''}`}
                                  >
                                    {m.role === 'assistant' && (
                                      <div className="w-6 h-6 rounded-md bg-gradient-to-br from-violet-600 to-fuchsia-600 flex items-center justify-center shrink-0 mt-0.5">
                                        <Bot size={12} className="text-aegisaxiom-text" />
                                      </div>
                                    )}
                                    {m.role === 'user' && (
                                      <div className="w-6 h-6 rounded-md bg-aegisaxiom-elevated flex items-center justify-center shrink-0 mt-0.5">
                                        <User size={12} className="text-aegisaxiom-text-secondary" />
                                      </div>
                                    )}
                                    <div
                                      className={`rounded-xl px-3 py-2 text-xs leading-relaxed ${
                                        m.role === 'user'
                                          ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-200 border border-violet-200 dark:border-violet-600/20'
                                          : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary border border-aegisaxiom-border'
                                      }`}
                                    >
                                      {m.role === 'user' ? (
                                        <p className="whitespace-pre-wrap">{m.content}</p>
                                      ) : (
                                        <ChatMarkdown content={m.content} />
                                      )}
                                    </div>
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile card list */}
          <div className="md:hidden p-3 space-y-2.5">
            {sessions.map((s) => {
              const isExpanded = expandedSession === s.session_id;
              return (
                <div
                  key={s.session_id}
                  className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg overflow-hidden"
                  data-testid={`chat-log-card-${s.session_id}`}
                >
                  <div
                    className="p-3.5 cursor-pointer hover:bg-aegisaxiom-accent-muted transition-colors"
                    onClick={() => loadMessages(s.session_id)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        loadMessages(s.session_id);
                      }
                    }}
                  >
                    {/* Row 1: chat # + msg count + chevron */}
                    <div className="flex items-center justify-between gap-2 mb-2">
                      <span className="font-mono text-xs bg-violet-50 dark:bg-violet-600/15 text-violet-700 dark:text-violet-300 px-2 py-0.5 rounded shrink-0">
                        {s.chat_number || '—'}
                      </span>
                      <div className="flex items-center gap-2 shrink-0">
                        <span className="inline-flex items-center gap-1 text-[11px] bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary px-2 py-0.5 rounded-full">
                          <MessageCircle size={10} className="opacity-70" />
                          {s.message_count || 0}
                        </span>
                        <ChevronRight
                          size={14}
                          className={`text-aegisaxiom-text-muted transition-transform ${isExpanded ? 'rotate-90' : ''}`}
                        />
                      </div>
                    </div>

                    {/* Row 2: title */}
                    <p className="text-sm font-medium text-aegisaxiom-text truncate mb-1">
                      {s.title || 'New Chat'}
                    </p>

                    {/* Row 3: user */}
                    <div className="flex items-center gap-1.5 text-xs text-aegisaxiom-text-secondary mb-2 min-w-0">
                      <User size={12} className="text-aegisaxiom-text-muted shrink-0" />
                      <span className="truncate">{s.user_name || '—'}</span>
                      <span className="text-aegisaxiom-text-muted">·</span>
                      <span className="truncate text-aegisaxiom-text-muted">
                        {s.user_email || s.user_id}
                      </span>
                    </div>

                    {/* Row 4: timestamps */}
                    <div className="flex items-center gap-3 text-[11px] text-aegisaxiom-text-muted">
                      <span className="inline-flex items-center gap-1">
                        <Clock size={11} className="opacity-70" />
                        <span className="truncate">Created {fmtTime(s.created_at)}</span>
                      </span>
                    </div>
                    <div className="text-[11px] text-aegisaxiom-text-muted mt-1">
                      Last active {fmtTime(s.updated_at)}
                    </div>
                  </div>

                  {/* Expanded messages panel */}
                  {isExpanded && (
                    <div
                      className="bg-aegisaxiom-surface border-t border-aegisaxiom-border p-3"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {messagesLoading ? (
                        <div className="text-center py-4">
                          <p className="text-aegisaxiom-text-muted text-xs">Loading messages...</p>
                        </div>
                      ) : expandedMessages.length === 0 ? (
                        <div className="text-center py-4">
                          <p className="text-aegisaxiom-text-muted text-xs">
                            No messages in this session.
                          </p>
                        </div>
                      ) : (
                        <div
                          className="max-h-80 overflow-y-auto px-1"
                          data-testid={`chat-log-messages-mobile-${s.session_id}`}
                        >
                          {expandedMessages.map((m, i) => (
                            <div
                              key={m.message_id || i}
                              className={`flex mb-3 last:mb-0 ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
                            >
                              <div
                                className={`flex gap-2 max-w-[88%] ${m.role === 'user' ? 'flex-row-reverse' : ''}`}
                              >
                                {m.role === 'assistant' && (
                                  <div className="w-6 h-6 rounded-md bg-gradient-to-br from-violet-600 to-fuchsia-600 flex items-center justify-center shrink-0 mt-0.5">
                                    <Bot size={12} className="text-white" />
                                  </div>
                                )}
                                {m.role === 'user' && (
                                  <div className="w-6 h-6 rounded-md bg-aegisaxiom-elevated flex items-center justify-center shrink-0 mt-0.5">
                                    <User size={12} className="text-aegisaxiom-text-secondary" />
                                  </div>
                                )}
                                <div
                                  className={`rounded-xl px-3 py-2 text-xs leading-relaxed min-w-0 ${
                                    m.role === 'user'
                                      ? 'bg-violet-600 text-white'
                                      : 'bg-aegisaxiom-elevated text-aegisaxiom-text border border-aegisaxiom-border'
                                  }`}
                                >
                                  {m.role === 'user' ? (
                                    <p className="whitespace-pre-wrap break-words">{m.content}</p>
                                  ) : (
                                    <ChatMarkdown content={m.content} />
                                  )}
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div
              className="flex items-center justify-between px-4 py-3 border-t border-aegisaxiom-border"
              data-testid="chat-logs-pagination"
            >
              <span className="text-xs text-aegisaxiom-text-muted">
                Showing {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
              </span>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                  disabled={page === 0}
                  className="p-1.5 rounded-lg bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:text-aegisaxiom-text disabled:opacity-30 transition-colors"
                >
                  <ChevronLeft size={14} />
                </button>
                <span className="text-xs text-aegisaxiom-text-secondary px-2">
                  {page + 1} / {totalPages}
                </span>
                <button
                  onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                  disabled={page >= totalPages - 1}
                  className="p-1.5 rounded-lg bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:text-aegisaxiom-text disabled:opacity-30 transition-colors"
                >
                  <ChevronRight size={14} />
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ══════════════════════════════════════════════
//  AUDIT LOG TAB  (existing code)
// ══════════════════════════════════════════════

// Detect the ingested-app resource_type pattern: two non-empty words joined by a dot
// (e.g. "davies_pmo.project"). Native types are single words ("user", "deal").
const INGESTED_RT_RE = /^\w+\.\w+$/;

function AuditLogTab() {
  const [searchParams, setSearchParams] = useSearchParams();

  const [logs, setLogs] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(false);
  const [actionFilter, setActionFilter] = useState('');
  const [resourceFilter, setResourceFilter] = useState('');
  const [searchActor, setSearchActor] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [eventTypeFilter, setEventTypeFilter] = useState('');
  const [severityFilter, setSeverityFilter] = useState('');
  const [outcomeFilter, setOutcomeFilter] = useState('');
  const [expanded, setExpanded] = useState(null);

  // Module / entity filter state — persisted in URL params
  const [moduleFilter, setModuleFilter] = useState(() => searchParams.get('module') || '');
  const [entityFilter, setEntityFilter] = useState(() => searchParams.get('entity') || '');

  // "Ingested Apps only" chip state
  const [ingestedOnly, setIngestedOnly] = useState(false);

  // Known-modules data from the /api/audit/known-modules endpoint
  const [knownModules, setKnownModules] = useState(null); // null = not yet attempted
  const knownModulesFetched = useRef(false);

  // Fetch known modules once on mount. If the endpoint 404s or errors we
  // set to [] so the module dropdown is simply hidden.
  useEffect(() => {
    if (knownModulesFetched.current) return;
    knownModulesFetched.current = true;

    (async () => {
      try {
        const res = await api.get(`${API}/api/audit/known-modules`);
        if (res.status === 404) {
          setKnownModules([]);
          return;
        }
        if (res.ok) {
          const data = await res.json();
          setKnownModules(Array.isArray(data) ? data : []);
        } else {
          setKnownModules([]);
        }
      } catch {
        setKnownModules([]);
      }
    })();
  }, []);

  // Sync module/entity into URL params when they change
  useEffect(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (moduleFilter) {
        next.set('module', moduleFilter);
      } else {
        next.delete('module');
      }
      if (entityFilter && moduleFilter) {
        next.set('entity', entityFilter);
      } else {
        next.delete('entity');
      }
      return next;
    }, { replace: true });
  }, [moduleFilter, entityFilter, setSearchParams]);

  // Compute the effective resource_type value to send to the backend.
  // Module+entity takes priority over the legacy resource dropdown.
  // When module-only is selected we send "<module>." (trailing dot) — the
  // backend treats that as a prefix match across all entities of the module.
  const effectiveResourceType = (() => {
    if (moduleFilter && entityFilter) return `${moduleFilter}.${entityFilter}`;
    if (moduleFilter) return `${moduleFilter}.`;
    return resourceFilter;
  })();

  const load = useCallback(async () => {
    setLoading(true);
    let url = `${API}/api/audit?skip=${page * PAGE_SIZE}&limit=${PAGE_SIZE}`;
    if (actionFilter) url += `&action=${encodeURIComponent(actionFilter)}`;
    if (effectiveResourceType) url += `&resource_type=${encodeURIComponent(effectiveResourceType)}`;
    if (searchActor) url += `&actor_id=${encodeURIComponent(searchActor)}`;
    if (categoryFilter) url += `&event_category=${encodeURIComponent(categoryFilter)}`;
    if (eventTypeFilter) url += `&event_type=${encodeURIComponent(eventTypeFilter)}`;
    if (severityFilter) url += `&severity=${encodeURIComponent(severityFilter)}`;
    if (outcomeFilter) url += `&outcome=${encodeURIComponent(outcomeFilter)}`;
    try {
      const res = await api.get(url);
      if (res.ok) {
        const data = await res.json();
        setLogs(data.items || []);
        setTotal(data.total || 0);
      }
    } catch {
      toast.error('Failed to load audit logs');
    }
    setLoading(false);
  }, [
    page,
    actionFilter,
    effectiveResourceType,
    searchActor,
    categoryFilter,
    eventTypeFilter,
    severityFilter,
    outcomeFilter,
  ]);

  useEffect(() => {
    load();
  }, [load]);

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const actions = Object.keys(ACTION_META);
  const resources = ['auth', 'user', 'account', 'deal', 'role', 'ai_employee', 'ai_tool'];
  const eventTypes = Object.keys(EVENT_TYPE_META);

  // Entities available for the selected module
  const selectedModuleData = knownModules?.find((m) => m?.module === moduleFilter) ?? null;
  const availableEntities = selectedModuleData?.entities ?? [];

  const clearFilters = () => {
    setActionFilter('');
    setResourceFilter('');
    setSearchActor('');
    setCategoryFilter('');
    setEventTypeFilter('');
    setSeverityFilter('');
    setOutcomeFilter('');
    setModuleFilter('');
    setEntityFilter('');
    setIngestedOnly(false);
    setPage(0);
  };
  const hasFilters =
    actionFilter ||
    resourceFilter ||
    searchActor ||
    categoryFilter ||
    eventTypeFilter ||
    severityFilter ||
    outcomeFilter ||
    moduleFilter ||
    entityFilter ||
    ingestedOnly;

  // Post-filter logs for the "Ingested Apps only" chip (client-side only)
  const displayLogs = ingestedOnly
    ? logs.filter((l) => INGESTED_RT_RE.test(l.resource_type || ''))
    : logs;

  // Quick filter chips for common security queries. Each chip sets a
  // composite filter state; clicking the same chip again clears it.
  // Unrecognized target keys return false (fail-closed) so a typo or
  // future-added key never silently registers as "always active".
  const buildQuickFilter = (key, label, target) => {
    const isActive = Object.entries(target).every(
      ([k, v]) => {
        if (k === 'category') return categoryFilter === v;
        if (k === 'eventType') return eventTypeFilter === v;
        if (k === 'severity') return severityFilter === v;
        if (k === 'outcome') return outcomeFilter === v;
        return false;
      }
    );
    return {
      key,
      label,
      isActive,
      apply: () => {
        if (isActive) {
          setCategoryFilter('');
          setEventTypeFilter('');
          setSeverityFilter('');
          setOutcomeFilter('');
        } else {
          setCategoryFilter(target.category || '');
          setEventTypeFilter(target.eventType || '');
          setSeverityFilter(target.severity || '');
          setOutcomeFilter(target.outcome || '');
        }
        setPage(0);
      },
    };
  };

  // "Ingested Apps only" chip — client-side filter that shows only rows
  // where resource_type matches the <module>.<entity> shape (two dot-separated
  // words). Native types are single words. We don't send anything extra to the
  // backend; we just post-filter the current page's rows.
  const ingestedChip = {
    key: 'ingested_apps',
    label: 'Ingested Apps',
    isActive: ingestedOnly,
    apply: () => {
      setIngestedOnly((v) => !v);
      setPage(0);
    },
  };

  const quickFilters = [
    buildQuickFilter('authz_failed', 'Permission Denials', { category: 'security', eventType: 'authz_failed' }),
    buildQuickFilter('auth_failed', 'Auth Failures', { category: 'security', eventType: 'auth_failed' }),
    buildQuickFilter('role_changed', 'Role Changes', { category: 'security', eventType: 'role_changed' }),
    buildQuickFilter('critical', 'Critical Severity', { category: 'security', severity: 'critical' }),
    ingestedChip,
  ];

  const exportCSV = () => {
    if (!logs.length) return;
    const headers = [
      'Timestamp',
      'Category',
      'Action',
      'Event Type',
      'Severity',
      'Outcome',
      'Actor',
      'Resource Type',
      'Resource ID',
      'Details',
    ];
    const rows = logs.map((l) => [
      new Date(l.timestamp).toISOString(),
      l.event_category || '',
      l.action || '',
      l.event_type || '',
      l.severity || '',
      l.outcome || '',
      l.actor_email || '',
      l.resource_type || '',
      l.resource_id || '',
      JSON.stringify(l.details || {}),
    ]);
    const csv = [headers, ...rows]
      .map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(','))
      .join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `audit-log-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    toast.success('Audit log exported');
  };

  const fmtTime = (ts) => {
    const d = new Date(ts);
    const now = new Date();
    const diffMs = now - d;
    if (diffMs < 60000) return 'Just now';
    if (diffMs < 3600000) return `${Math.floor(diffMs / 60000)}m ago`;
    if (diffMs < 86400000) return `${Math.floor(diffMs / 3600000)}h ago`;
    return d.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const fmtDetails = (details) => {
    if (!details || Object.keys(details).length === 0) return null;
    return Object.entries(details).map(([k, v]) => (
      <span
        key={k}
        className="inline-flex items-center gap-1 text-[11px] bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary px-2 py-0.5 rounded mr-1.5 mb-1"
      >
        <span className="text-aegisaxiom-text-muted">{k.replace(/_/g, ' ')}:</span>
        <span className="text-aegisaxiom-text-secondary">
          {typeof v === 'object' ? JSON.stringify(v) : String(v)}
        </span>
      </span>
    ));
  };

  return (
    <div className="space-y-5" data-testid="audit-log-tab">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="page-title" data-testid="audit-log-title">
            Audit Log
          </h1>
          <p className="text-aegisaxiom-text-muted text-xs mt-0.5">{total} total events tracked</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={exportCSV}
            disabled={!logs.length}
            className="btn-secondary flex items-center gap-2 text-xs disabled:opacity-40"
            data-testid="export-csv-btn"
          >
            <Download size={13} />
            Export CSV
          </button>
          <button
            onClick={load}
            className="btn-secondary flex items-center gap-2 text-xs"
            data-testid="refresh-audit-btn"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="glass-card p-4" data-testid="audit-filters">
        <div className="flex items-center gap-2 mb-3">
          <Filter size={14} className="text-aegisaxiom-text-muted" />
          <span className="text-xs font-medium text-aegisaxiom-text-secondary uppercase tracking-wider">
            Filters
          </span>
          {hasFilters && (
            <button
              onClick={clearFilters}
              className="ml-auto text-xs text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary flex items-center gap-1"
              data-testid="clear-filters-btn"
            >
              <X size={12} />
              Clear all
            </button>
          )}
        </div>
        <div className="flex flex-wrap gap-3">
          {/* Action filter */}
          <div className="flex-1 min-w-[160px]">
            <label className="text-[11px] text-aegisaxiom-text-muted block mb-1.5">Action</label>
            <select
              value={actionFilter}
              onChange={(e) => {
                setActionFilter(e.target.value);
                setPage(0);
              }}
              className="input-field text-xs w-full"
              data-testid="action-filter"
            >
              <option value="">All actions</option>
              {actions.map((a) => (
                <option key={a} value={a}>
                  {ACTION_META[a]?.label || a}
                </option>
              ))}
            </select>
          </div>
          {/* Resource filter */}
          <div className="flex-1 min-w-[160px]">
            <label className="text-[11px] text-aegisaxiom-text-muted block mb-1.5">Resource</label>
            <select
              value={resourceFilter}
              onChange={(e) => {
                setResourceFilter(e.target.value);
                setPage(0);
              }}
              className="input-field text-xs w-full"
              data-testid="resource-filter"
            >
              <option value="">All resources</option>
              {resources.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </div>
          {/* Category filter (activity vs security) */}
          <div className="flex-1 min-w-[140px]">
            <label className="text-[11px] text-aegisaxiom-text-muted block mb-1.5">Category</label>
            <select
              value={categoryFilter}
              onChange={(e) => {
                setCategoryFilter(e.target.value);
                setPage(0);
              }}
              className="input-field text-xs w-full"
              data-testid="category-filter"
            >
              <option value="">All categories</option>
              <option value="activity">Activity</option>
              <option value="security">Security</option>
            </select>
          </div>
          {/* Event type filter (security only) */}
          <div className="flex-1 min-w-[160px]">
            <label className="text-[11px] text-aegisaxiom-text-muted block mb-1.5">Event Type</label>
            <select
              value={eventTypeFilter}
              onChange={(e) => {
                setEventTypeFilter(e.target.value);
                setPage(0);
              }}
              className="input-field text-xs w-full"
              data-testid="event-type-filter"
            >
              <option value="">All events</option>
              {eventTypes.map((t) => (
                <option key={t} value={t}>
                  {EVENT_TYPE_META[t]?.label || t}
                </option>
              ))}
            </select>
          </div>
          {/* Severity filter */}
          <div className="flex-1 min-w-[120px]">
            <label className="text-[11px] text-aegisaxiom-text-muted block mb-1.5">Severity</label>
            <select
              value={severityFilter}
              onChange={(e) => {
                setSeverityFilter(e.target.value);
                setPage(0);
              }}
              className="input-field text-xs w-full"
              data-testid="severity-filter"
            >
              <option value="">All severities</option>
              <option value="info">Info</option>
              <option value="warning">Warning</option>
              <option value="critical">Critical</option>
            </select>
          </div>
          {/* Outcome filter */}
          <div className="flex-1 min-w-[120px]">
            <label className="text-[11px] text-aegisaxiom-text-muted block mb-1.5">Outcome</label>
            <select
              value={outcomeFilter}
              onChange={(e) => {
                setOutcomeFilter(e.target.value);
                setPage(0);
              }}
              className="input-field text-xs w-full"
              data-testid="outcome-filter"
            >
              <option value="">All outcomes</option>
              <option value="success">Success</option>
              <option value="denied">Denied</option>
              <option value="error">Error</option>
            </select>
          </div>
          {/* Actor search */}
          <div className="flex-1 min-w-[200px]">
            <label className="text-[11px] text-aegisaxiom-text-muted block mb-1.5">Actor ID</label>
            <div className="relative">
              <Search
                size={13}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted"
              />
              <input
                type="text"
                placeholder="Search by actor ID..."
                value={searchActor}
                onChange={(e) => {
                  setSearchActor(e.target.value);
                  setPage(0);
                }}
                className="input-field text-xs w-full pl-8"
                data-testid="actor-search"
              />
            </div>
          </div>
          {/* Module filter — only shown when the known-modules endpoint responded */}
          {knownModules !== null && knownModules.length > 0 && (
            <div className="flex-1 min-w-[200px]">
              <label className="text-[11px] text-aegisaxiom-text-muted block mb-1.5">Module</label>
              <select
                value={moduleFilter}
                onChange={(e) => {
                  setModuleFilter(e.target.value);
                  setEntityFilter('');
                  setPage(0);
                }}
                className="input-field text-xs w-full"
                data-testid="module-filter"
              >
                <option value="">All modules</option>
                {/* Native modules first */}
                {knownModules.filter((m) => m?.source === 'native').length > 0 && (
                  <optgroup label="Native modules">
                    {knownModules
                      .filter((m) => m?.source === 'native')
                      .map((m) => (
                        <option key={m.module} value={m.module}>
                          {m.module}
                          {m.event_count != null ? ` (${m.event_count})` : ''}
                        </option>
                      ))}
                  </optgroup>
                )}
                {/* Ingested apps second */}
                {knownModules.filter((m) => m?.source === 'ingestion').length > 0 && (
                  <optgroup label="Ingested apps">
                    {knownModules
                      .filter((m) => m?.source === 'ingestion')
                      .map((m) => (
                        <option key={m.module} value={m.module}>
                          {m.module}
                          {m.event_count != null ? ` (${m.event_count})` : ''}
                        </option>
                      ))}
                  </optgroup>
                )}
                {/* Fallback: modules without a source field (future-proofing) */}
                {knownModules.filter((m) => !m?.source).length > 0 && (
                  <>
                    {knownModules
                      .filter((m) => !m?.source)
                      .map((m) => (
                        <option key={m.module} value={m.module}>
                          {m.module}
                          {m.event_count != null ? ` (${m.event_count})` : ''}
                        </option>
                      ))}
                  </>
                )}
              </select>
            </div>
          )}
          {/* Entity filter — only shown when a module is selected and has entities */}
          {moduleFilter && availableEntities.length > 0 && (
            <div className="flex-1 min-w-[180px]">
              <label className="text-[11px] text-aegisaxiom-text-muted block mb-1.5">Entity</label>
              <select
                value={entityFilter}
                onChange={(e) => {
                  setEntityFilter(e.target.value);
                  setPage(0);
                }}
                className="input-field text-xs w-full"
                data-testid="entity-filter"
              >
                <option value="">(any entity)</option>
                {availableEntities.map((entity) => (
                  <option key={entity} value={entity}>
                    {entity}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
        {/* Quick-filter chips for common security queries */}
        <div className="flex flex-wrap gap-2 mt-3">
          {quickFilters.map((qf) => (
            <button
              key={qf.key}
              onClick={qf.apply}
              data-testid={`quick-filter-${qf.key}`}
              className={`text-[11px] px-2.5 py-1 rounded-full border transition-colors ${
                qf.isActive
                  ? 'bg-aegisaxiom-primary/20 text-aegisaxiom-primary border-aegisaxiom-primary/40'
                  : 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary border-aegisaxiom-border hover:border-aegisaxiom-border-hover'
              }`}
            >
              {qf.label}
            </button>
          ))}
        </div>
      </div>

      {/* Stats Row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-5" style={{ marginBottom: '1.5rem' }}>
        {[
          { label: 'Total', value: total, color: 'text-aegisaxiom-text' },
          {
            label: 'Logins',
            value: displayLogs.filter((l) => l.action === 'login').length,
            color: 'text-blue-600 dark:text-blue-400',
          },
          {
            label: 'Changes',
            value: displayLogs.filter((l) =>
              ['user_update', 'role_update', 'role_create'].includes(l.action),
            ).length,
            color: 'text-cyan-600 dark:text-cyan-400',
          },
          {
            label: 'Deletions',
            value: displayLogs.filter((l) => l.action?.includes('delete')).length,
            color: 'text-red-600 dark:text-red-400',
          },
        ].map((s) => (
          <div key={s.label} className="glass-card p-5 text-center">
            <p
              className={`text-2xl font-bold ${s.color}`}
              style={{ fontFamily: 'Manrope, sans-serif' }}
            >
              {s.value}
            </p>
            <p className="text-aegisaxiom-text-secondary text-sm mt-2">{s.label}</p>
          </div>
        ))}
      </div>

      {/* Table */}
      {displayLogs.length === 0 ? (
        <div className="glass-card p-16 text-center" data-testid="audit-empty">
          <ScrollText size={32} className="text-aegisaxiom-text-muted mx-auto mb-3" />
          <p className="text-aegisaxiom-text-muted">
            {hasFilters ? 'No events match your filters.' : 'No audit events recorded yet.'}
          </p>
          <p className="text-aegisaxiom-text-muted text-xs mt-1">
            Sensitive actions (login, user changes, deletions) are automatically logged.
          </p>
        </div>
      ) : (
        <div className="glass-card overflow-hidden" data-testid="audit-table-card">
          {/* Desktop table */}
          <div className="hidden md:block overflow-x-auto">
            <table className="w-full text-sm" data-testid="audit-logs-table">
              <thead>
                <tr className="border-b border-aegisaxiom-border">
                  {['Time', 'Action', 'Actor', 'Resource', 'Details'].map((h) => (
                    <th
                      key={h}
                      className="text-left py-3 px-4 text-[11px] font-medium text-aegisaxiom-text-muted uppercase tracking-wider"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {displayLogs.map((log, i) => {
                  // Security entries use event_type for the chip; activity
                  // entries fall back to action. Either way the chip is the
                  // primary "what happened" cue in the row.
                  const meta =
                    EVENT_TYPE_META[log.event_type] ||
                    ACTION_META[log.action] || {
                      label: log.event_type || log.action,
                      icon: ScrollText,
                      color:
                        'bg-aegisaxiom-elevated/15 text-aegisaxiom-text-secondary border-aegisaxiom-border-hover/20',
                    };
                  const Icon = meta.icon;
                  const isExpanded = expanded === i;
                  return (
                    <React.Fragment key={i}>
                      <tr
                        className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-accent-muted transition-colors cursor-pointer"
                        onClick={() => setExpanded(isExpanded ? null : i)}
                        data-testid={`audit-row-${i}`}
                      >
                        <td className="py-3 px-4">
                          <div className="flex items-center gap-1.5 text-aegisaxiom-text-secondary">
                            <Clock size={12} className="text-aegisaxiom-text-muted" />
                            <span className="text-xs whitespace-nowrap">
                              {fmtTime(log.timestamp)}
                            </span>
                          </div>
                        </td>
                        <td className="py-3 px-4">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span
                              className={`inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-lg border ${meta.color}`}
                            >
                              <Icon size={12} />
                              {meta.label}
                            </span>
                            {SEVERITY_BADGE[log.severity] && (
                              <span
                                className={`text-[10px] uppercase tracking-wider font-semibold px-1.5 py-0.5 rounded ${SEVERITY_BADGE[log.severity]}`}
                                data-testid={`severity-badge-${log.severity}`}
                              >
                                {log.severity}
                              </span>
                            )}
                            {log.outcome === 'denied' && (
                              <span
                                className="text-[10px] uppercase tracking-wider font-semibold px-1.5 py-0.5 rounded bg-red-500/15 text-red-700 dark:text-red-300"
                                data-testid="outcome-denied"
                              >
                                denied
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="py-3 px-4">
                          <div className="flex items-center gap-1.5">
                            <User size={12} className="text-aegisaxiom-text-muted" />
                            <span className="text-xs text-aegisaxiom-text-secondary truncate max-w-[180px]">
                              {log.actor_email}
                            </span>
                          </div>
                        </td>
                        <td className="py-3 px-4">
                          <div className="flex items-center gap-2">
                            <span
                              className={`text-[11px] px-2 py-0.5 rounded-full ${RESOURCE_BADGE[log.resource_type] || 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary'}`}
                            >
                              {log.resource_type}
                            </span>
                            {log.resource_id && (
                              <span className="text-[11px] text-aegisaxiom-text-muted font-mono truncate max-w-[120px]">
                                {log.resource_id}
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="py-3 px-4">
                          <div className="flex flex-wrap max-w-xs">
                            {fmtDetails(log.details) || (
                              <span className="text-aegisaxiom-text-muted text-xs">—</span>
                            )}
                          </div>
                        </td>
                      </tr>
                      {isExpanded && (
                        <tr className="bg-aegisaxiom-surface">
                          <td colSpan={5} className="px-4 py-3">
                            <div className="grid grid-cols-2 gap-x-8 gap-y-2 text-xs max-w-2xl">
                              <div>
                                <span className="text-aegisaxiom-text-muted">Full Timestamp:</span>{' '}
                                <span className="text-aegisaxiom-text-secondary ml-1">
                                  {new Date(log.timestamp).toLocaleString()}
                                </span>
                              </div>
                              <div>
                                <span className="text-aegisaxiom-text-muted">Actor ID:</span>{' '}
                                <span className="text-aegisaxiom-text-secondary ml-1 font-mono">
                                  {log.actor_id}
                                </span>
                              </div>
                              <div>
                                <span className="text-aegisaxiom-text-muted">Actor Email:</span>{' '}
                                <span className="text-aegisaxiom-text-secondary ml-1">
                                  {log.actor_email}
                                </span>
                              </div>
                              <div>
                                <span className="text-aegisaxiom-text-muted">Resource ID:</span>{' '}
                                <span className="text-aegisaxiom-text-secondary ml-1 font-mono">
                                  {log.resource_id || '—'}
                                </span>
                              </div>
                              {log.event_category && (
                                <div>
                                  <span className="text-aegisaxiom-text-muted">Category:</span>{' '}
                                  <span className="text-aegisaxiom-text-secondary ml-1">
                                    {log.event_category}
                                  </span>
                                </div>
                              )}
                              {log.event_type && (
                                <div>
                                  <span className="text-aegisaxiom-text-muted">Event Type:</span>{' '}
                                  <span className="text-aegisaxiom-text-secondary ml-1 font-mono">
                                    {log.event_type}
                                  </span>
                                </div>
                              )}
                              {log.severity && (
                                <div>
                                  <span className="text-aegisaxiom-text-muted">Severity:</span>{' '}
                                  <span className="text-aegisaxiom-text-secondary ml-1">
                                    {log.severity}
                                  </span>
                                </div>
                              )}
                              {log.outcome && (
                                <div>
                                  <span className="text-aegisaxiom-text-muted">Outcome:</span>{' '}
                                  <span className="text-aegisaxiom-text-secondary ml-1">
                                    {log.outcome}
                                  </span>
                                </div>
                              )}
                              {log.details && Object.keys(log.details).length > 0 && (
                                <div className="col-span-2">
                                  <span className="text-aegisaxiom-text-muted">Details:</span>
                                  <pre className="text-aegisaxiom-text-secondary mt-1 bg-aegisaxiom-elevated p-2 rounded text-[11px] overflow-x-auto">
                                    {JSON.stringify(log.details, null, 2)}
                                  </pre>
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Mobile card list */}
          <div className="md:hidden divide-y divide-aegisaxiom-border">
            {displayLogs.map((log, i) => {
              const meta =
                EVENT_TYPE_META[log.event_type] ||
                ACTION_META[log.action] || {
                  label: log.event_type || log.action,
                  icon: ScrollText,
                  color:
                    'bg-aegisaxiom-elevated/15 text-aegisaxiom-text-secondary border-aegisaxiom-border-hover/20',
                };
              const Icon = meta.icon;
              const isExpanded = expanded === i;
              const hasDetails = log.details && Object.keys(log.details).length > 0;
              return (
                <div
                  key={i}
                  className="p-3.5 cursor-pointer hover:bg-aegisaxiom-accent-muted transition-colors"
                  onClick={() => setExpanded(isExpanded ? null : i)}
                  data-testid={`audit-card-${i}`}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      setExpanded(isExpanded ? null : i);
                    }
                  }}
                >
                  {/* Row 1: action chip + time + chevron */}
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <span
                      className={`inline-flex items-center gap-1.5 text-[11px] px-2 py-1 rounded-lg border shrink-0 ${meta.color}`}
                    >
                      <Icon size={11} />
                      {meta.label}
                    </span>
                    <div className="flex items-center gap-1.5 text-aegisaxiom-text-muted shrink-0">
                      <Clock size={11} />
                      <span className="text-[11px] whitespace-nowrap">
                        {fmtTime(log.timestamp)}
                      </span>
                      <ChevronRight
                        size={14}
                        className={`transition-transform ${isExpanded ? 'rotate-90' : ''}`}
                      />
                    </div>
                  </div>

                  {/* Row 2: actor */}
                  <div className="flex items-center gap-1.5 mb-2 min-w-0">
                    <User size={12} className="text-aegisaxiom-text-muted shrink-0" />
                    <span className="text-xs text-aegisaxiom-text-secondary truncate">
                      {log.actor_email}
                    </span>
                  </div>

                  {/* Row 3: resource + resource id */}
                  <div className="flex items-center gap-2 flex-wrap">
                    <span
                      className={`text-[11px] px-2 py-0.5 rounded-full ${RESOURCE_BADGE[log.resource_type] || 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary'}`}
                    >
                      {log.resource_type}
                    </span>
                    {log.resource_id && (
                      <span className="text-[11px] text-aegisaxiom-text-muted font-mono truncate max-w-[200px]">
                        {log.resource_id}
                      </span>
                    )}
                  </div>

                  {/* Row 4: detail pills when not expanded */}
                  {!isExpanded && hasDetails && (
                    <div className="flex flex-wrap mt-2">{fmtDetails(log.details)}</div>
                  )}

                  {/* Expanded detail block */}
                  {isExpanded && (
                    <div
                      className="mt-3 pt-3 border-t border-aegisaxiom-border space-y-2 text-xs"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <div>
                        <span className="text-aegisaxiom-text-muted">Full Timestamp:</span>{' '}
                        <span className="text-aegisaxiom-text-secondary">
                          {new Date(log.timestamp).toLocaleString()}
                        </span>
                      </div>
                      <div className="min-w-0">
                        <span className="text-aegisaxiom-text-muted">Actor ID:</span>{' '}
                        <span className="text-aegisaxiom-text-secondary font-mono break-all">
                          {log.actor_id}
                        </span>
                      </div>
                      {log.resource_id && (
                        <div className="min-w-0">
                          <span className="text-aegisaxiom-text-muted">Resource ID:</span>{' '}
                          <span className="text-aegisaxiom-text-secondary font-mono break-all">
                            {log.resource_id}
                          </span>
                        </div>
                      )}
                      {hasDetails && (
                        <div>
                          <span className="text-aegisaxiom-text-muted block mb-1">Details:</span>
                          <pre className="text-aegisaxiom-text-secondary bg-aegisaxiom-elevated p-2 rounded text-[10px] overflow-x-auto leading-snug">
                            {JSON.stringify(log.details, null, 2)}
                          </pre>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div
              className="flex items-center justify-between px-4 py-3 border-t border-aegisaxiom-border"
              data-testid="audit-pagination"
            >
              <span className="text-xs text-aegisaxiom-text-muted">
                Showing {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
              </span>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                  disabled={page === 0}
                  className="p-1.5 rounded-lg bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:text-aegisaxiom-text disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                  data-testid="audit-prev-page"
                >
                  <ChevronLeft size={14} />
                </button>
                <span className="text-xs text-aegisaxiom-text-secondary px-2">
                  {page + 1} / {totalPages}
                </span>
                <button
                  onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                  disabled={page >= totalPages - 1}
                  className="p-1.5 rounded-lg bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:text-aegisaxiom-text disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                  data-testid="audit-next-page"
                >
                  <ChevronRight size={14} />
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ══════════════════════════════════════════════
//  AI USAGE & COSTS TAB
// ══════════════════════════════════════════════

const AGENT_COLORS = {
  router: '#7C3AED',
  deals: '#10B981',
  commissions: '#F59E0B',
  merge: '#3B82F6',
  general: '#6366F1',
};
const MODEL_COLORS = [
  '#7C3AED',
  '#10B981',
  '#F59E0B',
  '#EF4444',
  '#3B82F6',
  '#C026D3',
  '#14B8A6',
  '#F97316',
];
const TOOLTIP_STYLE = {
  background: 'var(--aegisaxiom-bg-elevated)',
  border: '1px solid #334155',
  borderRadius: '8px',
  color: 'var(--aegisaxiom-text-secondary)',
  fontSize: '12px',
};

function fmtTokens(n) {
  if (n == null) return '\u2014';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function fmtCost(c) {
  if (c == null) return '\u2014';
  return `$${c.toFixed(4)}`;
}

function AIUsageTab() {
  const [subTab, setSubTab] = useState('dashboard');
  return (
    <div className="space-y-5">
      {/* Subtab nav */}
      <div className="flex flex-wrap items-center gap-1 bg-aegisaxiom-surface rounded-xl p-1 w-fit max-w-full border border-aegisaxiom-border">
        {[
          { key: 'dashboard', label: 'Usage Dashboard', icon: BarChart3 },
          { key: 'models', label: 'Model Configuration', icon: Cpu },
          { key: 'pricing', label: 'Pricing Configuration', icon: DollarSign },
        ].map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setSubTab(key)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${subTab === key ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border border-violet-200 dark:border-violet-600/20' : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'}`}
          >
            <Icon size={12} className="inline mr-1.5 -mt-0.5" />
            {label}
          </button>
        ))}
      </div>
      {subTab === 'dashboard' && <UsageDashboard />}
      {subTab === 'models' && <ModelConfigTab />}
      {subTab === 'pricing' && <PricingConfigTab />}
    </div>
  );
}

// ── Usage Dashboard Subtab ──

function UsageDashboard() {
  const [summary, setSummary] = useState(null);
  const [daily, setDaily] = useState([]);
  const [byUser, setByUser] = useState([]);
  const [breakdown, setBreakdown] = useState({ by_agent: [], by_model: [] });
  const [loading, setLoading] = useState(false);
  const [datePreset, setDatePreset] = useState('30d');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');

  const getDateParams = useCallback(() => {
    if (datePreset === 'custom') {
      const params = new URLSearchParams();
      if (dateFrom) params.set('date_from', dateFrom);
      if (dateTo) params.set('date_to', dateTo);
      return params.toString() ? `?${params}` : '';
    }
    const days = datePreset === '7d' ? 7 : datePreset === '90d' ? 90 : 30;
    const from = new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);
    return `?date_from=${from}`;
  }, [datePreset, dateFrom, dateTo]);

  const load = useCallback(async () => {
    setLoading(true);
    const qs = getDateParams();
    try {
      const [sRes, dRes, uRes, bRes] = await Promise.all([
        api.get(`${API}/api/admin/ai-usage/summary${qs}`),
        api.get(`${API}/api/admin/ai-usage/daily${qs}`),
        api.get(`${API}/api/admin/ai-usage/by-user${qs}`),
        api.get(`${API}/api/admin/ai-usage/by-breakdown${qs}`),
      ]);
      if (sRes.ok) setSummary(await sRes.json());
      if (dRes.ok) setDaily(await dRes.json());
      if (uRes.ok) setByUser(await uRes.json());
      if (bRes.ok) setBreakdown(await bRes.json());
    } catch {
      toast.error('Failed to load usage data');
    }
    setLoading(false);
  }, [getDateParams]);

  useEffect(() => {
    load();
  }, [load]);

  const isEmpty = !summary || summary.total_requests === 0;

  return (
    <div className="space-y-6 sm:space-y-5">
      {/* Header + date presets */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between flex-wrap gap-3">
        <div>
          <h1 className="page-title">AI Usage Dashboard</h1>
          <p className="text-aegisaxiom-text-muted text-xs mt-0.5">
            Gemini API costs only. Estimates based on configured pricing.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {['7d', '30d', '90d', 'custom'].map((p) => (
            <button
              key={p}
              onClick={() => setDatePreset(p)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${datePreset === p ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border border-violet-200 dark:border-violet-600/20' : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text bg-aegisaxiom-elevated'}`}
            >
              {p === 'custom' ? 'Custom' : p}
            </button>
          ))}
          <button onClick={load} className="btn-secondary flex items-center gap-2 text-xs">
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      {/* Custom date pickers */}
      {datePreset === 'custom' && (
        <div className="glass-card p-4 flex gap-3 items-end">
          <div>
            <label className="text-[11px] text-aegisaxiom-text-muted block mb-1.5">From</label>
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              className="input-field text-xs"
            />
          </div>
          <div>
            <label className="text-[11px] text-aegisaxiom-text-muted block mb-1.5">To</label>
            <input
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              className="input-field text-xs"
            />
          </div>
        </div>
      )}

      {isEmpty && !loading ? (
        <div className="glass-card p-16 text-center">
          <BarChart3 size={32} className="text-aegisaxiom-text-muted mx-auto mb-3" />
          <p className="text-aegisaxiom-text-muted">No AI usage data yet.</p>
          <p className="text-aegisaxiom-text-muted text-xs mt-1">
            Usage will appear here once AI features are active.
          </p>
        </div>
      ) : (
        <>
          {/* Summary cards */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              {
                label: 'Total Tokens',
                value: fmtTokens(summary?.total_tokens),
                color: 'text-aegisaxiom-text',
              },
              {
                label: 'Total Requests',
                value: summary?.total_requests ?? '\u2014',
                color: 'text-violet-600 dark:text-violet-400',
              },
              {
                label: 'Unique Users',
                value: summary?.unique_users ?? '\u2014',
                color: 'text-emerald-600 dark:text-emerald-400',
              },
              {
                label: 'Est. Cost',
                value: fmtCost(summary?.estimated_cost),
                color: 'text-amber-600 dark:text-amber-400',
              },
            ].map((s) => (
              <div key={s.label} className="glass-card p-3 text-center">
                <p
                  className={`text-xl font-bold ${s.color}`}
                  style={{ fontFamily: 'Manrope, sans-serif' }}
                >
                  {s.value}
                </p>
                <p className="text-aegisaxiom-text-muted text-[11px]">{s.label}</p>
              </div>
            ))}
          </div>

          {/* Daily usage chart */}
          {daily.length > 0 && (
            <div className="glass-card p-5">
              <h3 className="text-sm font-medium text-aegisaxiom-text-secondary mb-4">
                Daily Token Usage
              </h3>
              <ResponsiveContainer width="100%" height={280}>
                <AreaChart data={daily}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                  <XAxis
                    dataKey="date"
                    tick={{ fill: '#94A3B8', fontSize: 11 }}
                    tickFormatter={(d) => d.slice(5)}
                  />
                  <YAxis tick={{ fill: '#94A3B8', fontSize: 11 }} tickFormatter={fmtTokens} />
                  <Tooltip
                    contentStyle={TOOLTIP_STYLE}
                    formatter={(v, name) => [fmtTokens(v), name]}
                  />
                  <Legend
                    wrapperStyle={{ fontSize: '12px', color: 'var(--aegisaxiom-text-secondary)' }}
                  />
                  <Area
                    type="monotone"
                    dataKey="prompt_tokens"
                    name="Prompt Tokens"
                    stackId="tokens"
                    fill="#7C3AED"
                    fillOpacity={0.6}
                    stroke="#7C3AED"
                  />
                  <Area
                    type="monotone"
                    dataKey="completion_tokens"
                    name="Completion Tokens"
                    stackId="tokens"
                    fill="#C026D3"
                    fillOpacity={0.6}
                    stroke="#C026D3"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Agent + Model charts */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 md:gap-5">
            {/* Per-agent bar chart */}
            {breakdown.by_agent.length > 0 && (
              <div className="glass-card p-5">
                <h3 className="text-sm font-medium text-aegisaxiom-text-secondary mb-4">
                  Usage by Agent
                </h3>
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={breakdown.by_agent} layout="vertical">
                    <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                    <XAxis
                      type="number"
                      tick={{ fill: '#94A3B8', fontSize: 11 }}
                      tickFormatter={fmtTokens}
                    />
                    <YAxis
                      dataKey="name"
                      type="category"
                      tick={{ fill: '#94A3B8', fontSize: 11 }}
                      width={90}
                    />
                    <Tooltip
                      contentStyle={TOOLTIP_STYLE}
                      formatter={(v) => [fmtTokens(v), 'Tokens']}
                    />
                    <Bar dataKey="total_tokens" radius={[0, 4, 4, 0]}>
                      {breakdown.by_agent.map((entry, i) => (
                        <Cell
                          key={i}
                          fill={AGENT_COLORS[entry.name] || MODEL_COLORS[i % MODEL_COLORS.length]}
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* Per-model pie chart */}
            {breakdown.by_model.length > 0 && (
              <div className="glass-card p-5">
                <h3 className="text-sm font-medium text-aegisaxiom-text-secondary mb-4">
                  Usage by Model
                </h3>
                <ResponsiveContainer width="100%" height={240}>
                  <PieChart>
                    <Pie
                      data={breakdown.by_model}
                      dataKey="total_tokens"
                      nameKey="name"
                      cx="50%"
                      cy="50%"
                      outerRadius={85}
                      label={({ name, percent }) => `${name} (${(percent * 100).toFixed(0)}%)`}
                    >
                      {breakdown.by_model.map((_, i) => (
                        <Cell key={i} fill={MODEL_COLORS[i % MODEL_COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={TOOLTIP_STYLE}
                      formatter={(v) => [fmtTokens(v), 'Tokens']}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>

          {/* Per-user table */}
          {byUser.length > 0 && (
            <div className="glass-card overflow-hidden">
              <div className="px-5 py-3 border-b border-aegisaxiom-border">
                <h3 className="text-sm font-medium text-aegisaxiom-text-secondary">Usage by User</h3>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-aegisaxiom-border">
                      {[
                        'User',
                        'Requests',
                        'Prompt Tokens',
                        'Completion Tokens',
                        'Total Tokens',
                        'Est. Cost',
                      ].map((h) => (
                        <th
                          key={h}
                          className="text-left py-3 px-4 text-[11px] font-medium text-aegisaxiom-text-muted uppercase tracking-wider"
                        >
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {byUser.map((u) => (
                      <tr
                        key={u.user_id}
                        className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-accent-muted transition-colors"
                      >
                        <td className="py-3 px-4">
                          <p className="text-xs text-aegisaxiom-text">{u.user_name}</p>
                          <p className="text-[11px] text-aegisaxiom-text-muted">{u.user_email}</p>
                        </td>
                        <td className="py-3 px-4 text-xs text-aegisaxiom-text-secondary">{u.requests}</td>
                        <td className="py-3 px-4 text-xs text-aegisaxiom-text-secondary">
                          {fmtTokens(u.prompt_tokens)}
                        </td>
                        <td className="py-3 px-4 text-xs text-aegisaxiom-text-secondary">
                          {fmtTokens(u.completion_tokens)}
                        </td>
                        <td className="py-3 px-4 text-xs text-aegisaxiom-text font-medium">
                          {fmtTokens(u.total_tokens)}
                        </td>
                        <td className="py-3 px-4 text-xs text-amber-600 dark:text-amber-400">
                          {fmtCost(u.estimated_cost)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Model Configuration Subtab ──

function ModelConfigTab() {
  const [models, setModels] = useState([]);
  const [available, setAvailable] = useState([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState({});

  // Model management state
  const [editingModel, setEditingModel] = useState(null); // { model, label, provider } being edited
  const [newModel, setNewModel] = useState({ model: '', label: '', provider: 'google' });
  const [showAddForm, setShowAddForm] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [mRes, aRes] = await Promise.all([
        api.get(`${API}/api/admin/ai-usage/models`),
        api.get(`${API}/api/admin/ai-usage/models/available`),
      ]);
      if (mRes.ok) setModels(await mRes.json());
      if (aRes.ok) setAvailable(await aRes.json());
    } catch {
      toast.error('Failed to load model config');
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleChange = async (key, modelName) => {
    const selected = available.find((a) => a.model === modelName);
    if (!selected) return;
    setSaving((s) => ({ ...s, [key]: true }));
    try {
      const res = await api.put(`${API}/api/admin/ai-usage/models/${key}`, {
        model_name: selected.model,
        model_provider: selected.provider,
      });
      if (res.ok) {
        toast.success(`Model for "${key}" updated to ${selected.label}`);
        load();
      } else {
        const data = await res.json();
        toast.error(data.detail || 'Failed to update');
      }
    } catch {
      toast.error('Failed to update model');
    }
    setSaving((s) => ({ ...s, [key]: false }));
  };

  const handleAddModel = async () => {
    if (!newModel.model.trim() || !newModel.label.trim()) return;
    try {
      const res = await api.post(`${API}/api/admin/ai-usage/models/available`, newModel);
      if (res.ok) {
        toast.success(`Model "${newModel.label}" added`);
        setNewModel({ model: '', label: '', provider: 'google' });
        setShowAddForm(false);
        load();
      } else {
        const data = await res.json();
        toast.error(data.detail || 'Failed to add model');
      }
    } catch {
      toast.error('Failed to add model');
    }
  };

  const handleUpdateModel = async (originalModel) => {
    if (!editingModel.model.trim() || !editingModel.label.trim()) return;
    try {
      const res = await api.put(
        `${API}/api/admin/ai-usage/models/available/${encodeURIComponent(originalModel)}`,
        editingModel,
      );
      if (res.ok) {
        toast.success(`Model updated to "${editingModel.label}"`);
        setEditingModel(null);
        load();
      } else {
        const data = await res.json();
        toast.error(data.detail || 'Failed to update model');
      }
    } catch {
      toast.error('Failed to update model');
    }
  };

  const handleDeleteModel = async (modelId, label) => {
    if (!window.confirm(`Remove "${label}" from available models?`)) return;
    try {
      const res = await api.delete(
        `${API}/api/admin/ai-usage/models/available/${encodeURIComponent(modelId)}`,
      );
      if (res.ok) {
        toast.success(`Model "${label}" removed`);
        load();
      } else {
        const data = await res.json();
        toast.error(data.detail || 'Failed to remove model');
      }
    } catch {
      toast.error('Failed to remove model');
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-title">Model Configuration</h1>
          <p className="text-aegisaxiom-text-muted text-xs mt-0.5">
            Change the LLM model used by each agent and system role. New agents appear
            automatically.
          </p>
        </div>
        <button onClick={load} className="btn-secondary flex items-center gap-2 text-xs">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {/* Agent Model Assignment */}
      <div className="glass-card overflow-hidden">
        {/* Desktop table */}
        <div className="hidden md:block overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-aegisaxiom-border">
                {['Role / Agent', 'Current Model', 'Status', 'Change Model'].map((h) => (
                  <th
                    key={h}
                    className="text-left py-3 px-4 text-[11px] font-medium text-aegisaxiom-text-muted uppercase tracking-wider"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <tr
                  key={m.key}
                  className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-accent-muted transition-colors"
                >
                  <td className="py-3 px-4">
                    <p className="text-xs text-aegisaxiom-text font-medium">{m.label}</p>
                    <p className="text-[11px] text-aegisaxiom-text-muted font-mono">{m.key}</p>
                  </td>
                  <td className="py-3 px-4">
                    <span className="text-xs bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary px-2 py-0.5 rounded">
                      {m.model_name}
                    </span>
                  </td>
                  <td className="py-3 px-4">
                    {m.is_overridden ? (
                      <span className="text-[11px] bg-amber-50 dark:bg-amber-600/15 text-amber-700 dark:text-amber-300 px-2 py-0.5 rounded">
                        Overridden
                      </span>
                    ) : (
                      <span className="text-[11px] bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary px-2 py-0.5 rounded">
                        Default
                      </span>
                    )}
                  </td>
                  <td className="py-3 px-4">
                    <select
                      value={m.model_name}
                      onChange={(e) => handleChange(m.key, e.target.value)}
                      disabled={saving[m.key]}
                      className="input-field text-xs w-48"
                    >
                      {available.map((a) => (
                        <option key={a.model} value={a.model}>
                          {a.label}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Mobile card list */}
        <div className="md:hidden p-3 space-y-2.5">
          {models.map((m) => (
            <div key={m.key} className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3.5">
              <div className="flex items-start justify-between gap-2 mb-2">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-aegisaxiom-text leading-tight">{m.label}</p>
                  <p className="text-[11px] text-aegisaxiom-text-muted font-mono truncate mt-0.5">
                    {m.key}
                  </p>
                </div>
                {m.is_overridden ? (
                  <span className="text-[10px] bg-amber-50 dark:bg-amber-600/15 text-amber-700 dark:text-amber-300 px-2 py-0.5 rounded shrink-0">
                    Overridden
                  </span>
                ) : (
                  <span className="text-[10px] bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary px-2 py-0.5 rounded shrink-0">
                    Default
                  </span>
                )}
              </div>
              <div className="mb-2">
                <span className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider block mb-1">
                  Current
                </span>
                <span className="text-xs bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary px-2 py-0.5 rounded font-mono">
                  {m.model_name}
                </span>
              </div>
              <div>
                <label className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider block mb-1">
                  Change Model
                </label>
                <select
                  value={m.model_name}
                  onChange={(e) => handleChange(m.key, e.target.value)}
                  disabled={saving[m.key]}
                  className="input-field text-xs w-full"
                >
                  {available.map((a) => (
                    <option key={a.model} value={a.model}>
                      {a.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Available Models Management */}
      <div className="glass-card overflow-hidden">
        <div className="px-5 py-3 border-b border-aegisaxiom-border flex items-center justify-between">
          <div>
            <h3 className="text-sm font-medium text-aegisaxiom-text-secondary">Available Models</h3>
            <p className="text-[11px] text-aegisaxiom-text-muted mt-0.5">
              Manage the models that appear in the dropdowns above. Edit a model name here when a
              new version is released.
            </p>
          </div>
          <button
            onClick={() => setShowAddForm(!showAddForm)}
            className="btn-secondary flex items-center gap-1.5 text-xs"
          >
            <Plus size={13} />
            Add Model
          </button>
        </div>

        {showAddForm && (
          <div className="px-5 py-3 border-b border-aegisaxiom-border bg-aegisaxiom-accent-muted flex items-end gap-3">
            <div className="flex-1">
              <label className="text-[11px] text-aegisaxiom-text-muted block mb-1">
                Model ID (API name)
              </label>
              <input
                type="text"
                value={newModel.model}
                onChange={(e) => setNewModel((p) => ({ ...p, model: e.target.value }))}
                placeholder="e.g. gemini-3-pro"
                className="input-field text-xs w-full"
              />
            </div>
            <div className="flex-1">
              <label className="text-[11px] text-aegisaxiom-text-muted block mb-1">Display Label</label>
              <input
                type="text"
                value={newModel.label}
                onChange={(e) => setNewModel((p) => ({ ...p, label: e.target.value }))}
                placeholder="e.g. Gemini 3 Pro"
                className="input-field text-xs w-full"
              />
            </div>
            <div className="w-32">
              <label className="text-[11px] text-aegisaxiom-text-muted block mb-1">Provider</label>
              <input
                type="text"
                value={newModel.provider}
                onChange={(e) => setNewModel((p) => ({ ...p, provider: e.target.value }))}
                className="input-field text-xs w-full"
              />
            </div>
            <button
              onClick={handleAddModel}
              className="btn-primary flex items-center gap-1.5 text-xs whitespace-nowrap"
            >
              <Check size={13} />
              Save
            </button>
            <button onClick={() => setShowAddForm(false)} className="btn-secondary text-xs">
              <X size={13} />
            </button>
          </div>
        )}

        <div className="hidden md:block overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-aegisaxiom-border">
                {['Model ID', 'Label', 'Provider', ''].map((h) => (
                  <th
                    key={h}
                    className="text-left py-3 px-4 text-[11px] font-medium text-aegisaxiom-text-muted uppercase tracking-wider"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {available.map((a) => (
                <tr
                  key={a.model}
                  className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-accent-muted transition-colors"
                >
                  {editingModel && editingModel._original === a.model ? (
                    <>
                      <td className="py-2 px-4">
                        <input
                          type="text"
                          value={editingModel.model}
                          onChange={(e) =>
                            setEditingModel((p) => ({ ...p, model: e.target.value }))
                          }
                          className="input-field text-xs w-full"
                        />
                      </td>
                      <td className="py-2 px-4">
                        <input
                          type="text"
                          value={editingModel.label}
                          onChange={(e) =>
                            setEditingModel((p) => ({ ...p, label: e.target.value }))
                          }
                          className="input-field text-xs w-full"
                        />
                      </td>
                      <td className="py-2 px-4">
                        <input
                          type="text"
                          value={editingModel.provider}
                          onChange={(e) =>
                            setEditingModel((p) => ({ ...p, provider: e.target.value }))
                          }
                          className="input-field text-xs w-full"
                        />
                      </td>
                      <td className="py-2 px-4 text-right">
                        <div className="flex items-center gap-1.5 justify-end">
                          <button
                            onClick={() => handleUpdateModel(a.model)}
                            className="p-1.5 rounded hover:bg-emerald-50 dark:bg-emerald-600/20 text-emerald-600 dark:text-emerald-400"
                            title="Save"
                          >
                            <Check size={13} />
                          </button>
                          <button
                            onClick={() => setEditingModel(null)}
                            className="p-1.5 rounded hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary"
                            title="Cancel"
                          >
                            <X size={13} />
                          </button>
                        </div>
                      </td>
                    </>
                  ) : (
                    <>
                      <td className="py-3 px-4">
                        <span className="text-xs font-mono text-aegisaxiom-text-secondary">
                          {a.model}
                        </span>
                      </td>
                      <td className="py-3 px-4">
                        <span className="text-xs text-aegisaxiom-text">{a.label}</span>
                      </td>
                      <td className="py-3 px-4">
                        <span className="text-xs text-aegisaxiom-text-secondary">{a.provider}</span>
                      </td>
                      <td className="py-3 px-4 text-right">
                        <div className="flex items-center gap-1.5 justify-end">
                          <button
                            onClick={() => setEditingModel({ ...a, _original: a.model })}
                            className="p-1.5 rounded hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary"
                            title="Edit"
                          >
                            <Pencil size={13} />
                          </button>
                          <button
                            onClick={() => handleDeleteModel(a.model, a.label)}
                            className="p-1.5 rounded hover:bg-red-50 dark:bg-red-600/20 text-red-600 dark:text-red-400"
                            title="Remove"
                          >
                            <Trash2 size={13} />
                          </button>
                        </div>
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Mobile card list */}
        <div className="md:hidden p-3 space-y-2.5">
          {available.map((a) => {
            const isEditing = editingModel && editingModel._original === a.model;
            return (
              <div
                key={a.model}
                className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-3.5"
              >
                {isEditing ? (
                  <div className="space-y-2.5">
                    <div>
                      <label className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider block mb-1">
                        Model ID
                      </label>
                      <input
                        type="text"
                        value={editingModel.model}
                        onChange={(e) => setEditingModel((p) => ({ ...p, model: e.target.value }))}
                        className="input-field text-xs w-full"
                      />
                    </div>
                    <div>
                      <label className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider block mb-1">
                        Label
                      </label>
                      <input
                        type="text"
                        value={editingModel.label}
                        onChange={(e) => setEditingModel((p) => ({ ...p, label: e.target.value }))}
                        className="input-field text-xs w-full"
                      />
                    </div>
                    <div>
                      <label className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider block mb-1">
                        Provider
                      </label>
                      <input
                        type="text"
                        value={editingModel.provider}
                        onChange={(e) =>
                          setEditingModel((p) => ({ ...p, provider: e.target.value }))
                        }
                        className="input-field text-xs w-full"
                      />
                    </div>
                    <div className="flex items-center justify-end gap-1.5 pt-1">
                      <button
                        onClick={() => setEditingModel(null)}
                        className="inline-flex items-center gap-1 px-3 py-1.5 rounded-md text-xs font-medium text-aegisaxiom-text-secondary hover:bg-aegisaxiom-surface transition-colors"
                      >
                        <X size={12} />
                        Cancel
                      </button>
                      <button
                        onClick={() => handleUpdateModel(a.model)}
                        className="inline-flex items-center gap-1 px-3 py-1.5 rounded-md text-xs font-medium bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-600/20"
                      >
                        <Check size={12} />
                        Save
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="min-w-0 mb-2">
                      <p className="text-sm font-medium text-aegisaxiom-text truncate">{a.label}</p>
                      <p className="text-[11px] text-aegisaxiom-text-muted font-mono truncate mt-0.5">
                        {a.model}
                      </p>
                    </div>
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[11px] text-aegisaxiom-text-secondary bg-aegisaxiom-surface px-2 py-0.5 rounded">
                        {a.provider}
                      </span>
                      <div className="flex items-center gap-1.5">
                        <button
                          onClick={() => setEditingModel({ ...a, _original: a.model })}
                          className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-medium text-aegisaxiom-text-secondary hover:bg-aegisaxiom-surface transition-colors"
                          aria-label="Edit model"
                        >
                          <Pencil size={12} />
                          Edit
                        </button>
                        <button
                          onClick={() => handleDeleteModel(a.model, a.label)}
                          className="inline-flex items-center justify-center w-8 h-8 rounded-md text-aegisaxiom-text-muted hover:text-red-600 dark:hover:text-red-400 hover:bg-aegisaxiom-surface transition-colors"
                          aria-label="Remove model"
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ── Pricing Configuration Subtab ──

function PricingConfigTab() {
  const [pricing, setPricing] = useState([]);
  const [loading, setLoading] = useState(false);
  const [edits, setEdits] = useState({});
  const [saving, setSaving] = useState({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get(`${API}/api/admin/ai-usage/pricing`);
      if (res.ok) {
        setPricing(await res.json());
        setEdits({});
      }
    } catch {
      toast.error('Failed to load pricing');
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleEdit = (model, field, value) => {
    setEdits((prev) => ({
      ...prev,
      [model]: { ...(prev[model] || {}), [field]: value },
    }));
  };

  const handleSave = async (model) => {
    const original = pricing.find((p) => p.model === model);
    const edit = edits[model] || {};
    const input = parseFloat(edit.input_per_million ?? original.input_per_million);
    const output = parseFloat(edit.output_per_million ?? original.output_per_million);

    if (isNaN(input) || isNaN(output) || input < 0 || output < 0) {
      toast.error('Pricing must be non-negative numbers');
      return;
    }

    setSaving((s) => ({ ...s, [model]: true }));
    try {
      const res = await api.put(`${API}/api/admin/ai-usage/pricing/${model}`, {
        input_per_million: input,
        output_per_million: output,
      });
      if (res.ok) {
        toast.success(`Pricing for ${model} updated`);
        load();
      } else {
        const data = await res.json();
        toast.error(data.detail || 'Failed to update');
      }
    } catch {
      toast.error('Failed to update pricing');
    }
    setSaving((s) => ({ ...s, [model]: false }));
  };

  const isEdited = (model) => {
    if (!edits[model]) return false;
    const original = pricing.find((p) => p.model === model);
    const edit = edits[model];
    return (
      (edit.input_per_million !== undefined &&
        String(edit.input_per_million) !== String(original.input_per_million)) ||
      (edit.output_per_million !== undefined &&
        String(edit.output_per_million) !== String(original.output_per_million))
    );
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="page-title">Pricing Configuration</h1>
          <p className="text-aegisaxiom-text-muted text-xs mt-0.5">
            Edit estimated costs per 1M tokens for each model. Changes apply to cost calculations on
            the dashboard.
          </p>
        </div>
        <button
          onClick={load}
          className="btn-secondary flex items-center justify-center gap-2 text-xs w-full sm:w-auto shrink-0"
        >
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      <div className="glass-card overflow-hidden">
        {/* Desktop table */}
        <div className="hidden md:block overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-aegisaxiom-border">
                {['Model', 'Label', 'Input ($ / 1M tokens)', 'Output ($ / 1M tokens)', ''].map(
                  (h) => (
                    <th
                      key={h}
                      className="text-left py-3 px-4 text-[11px] font-medium text-aegisaxiom-text-muted uppercase tracking-wider"
                    >
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {pricing.map((p) => (
                <tr
                  key={p.model}
                  className={`border-b border-aegisaxiom-border hover:bg-aegisaxiom-accent-muted transition-colors ${p.orphaned ? 'opacity-50' : ''} ${!p.has_pricing && !p.orphaned ? 'bg-amber-50 dark:bg-amber-900/10' : ''}`}
                >
                  <td className="py-3 px-4">
                    <span className="text-xs text-aegisaxiom-text font-medium">{p.model}</span>
                    {p.orphaned && (
                      <span className="ml-2 text-[10px] text-aegisaxiom-text-muted">(removed)</span>
                    )}
                  </td>
                  <td className="py-3 px-4">
                    <span className="text-xs text-aegisaxiom-text-secondary">{p.label || p.model}</span>
                    {!p.has_pricing && !p.orphaned && (
                      <span className="ml-2 text-[10px] text-amber-600 dark:text-amber-400">
                        (not configured)
                      </span>
                    )}
                  </td>
                  <td className="py-3 px-4">
                    <div className="flex items-center gap-1">
                      <span className="text-aegisaxiom-text-muted text-xs">$</span>
                      <input
                        type="number"
                        step="0.01"
                        min="0"
                        value={edits[p.model]?.input_per_million ?? p.input_per_million}
                        onChange={(e) => handleEdit(p.model, 'input_per_million', e.target.value)}
                        className="input-field text-xs w-28"
                      />
                    </div>
                  </td>
                  <td className="py-3 px-4">
                    <div className="flex items-center gap-1">
                      <span className="text-aegisaxiom-text-muted text-xs">$</span>
                      <input
                        type="number"
                        step="0.01"
                        min="0"
                        value={edits[p.model]?.output_per_million ?? p.output_per_million}
                        onChange={(e) => handleEdit(p.model, 'output_per_million', e.target.value)}
                        className="input-field text-xs w-28"
                      />
                    </div>
                  </td>
                  <td className="py-3 px-4">
                    {isEdited(p.model) && (
                      <button
                        onClick={() => handleSave(p.model)}
                        disabled={saving[p.model]}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border border-violet-200 dark:border-violet-600/20 hover:bg-violet-600/30 transition-all disabled:opacity-50"
                      >
                        {saving[p.model] ? (
                          <RefreshCw size={12} className="animate-spin" />
                        ) : (
                          <Save size={12} />
                        )}
                        Save
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Mobile card list */}
        <div className="md:hidden p-3 space-y-2.5">
          {pricing.map((p) => {
            const edited = isEdited(p.model);
            const baseCard = `bg-aegisaxiom-base border rounded-lg p-3.5 ${p.orphaned ? 'opacity-50 border-aegisaxiom-border' : !p.has_pricing ? 'border-amber-300 dark:border-amber-600/40 bg-amber-50 dark:bg-amber-900/10' : 'border-aegisaxiom-border'}`;
            return (
              <div key={p.model} className={baseCard}>
                {/* Model name + label */}
                <div className="mb-3">
                  <div className="flex items-start gap-2 flex-wrap">
                    <span className="text-sm text-aegisaxiom-text font-medium font-mono break-all">
                      {p.model}
                    </span>
                    {p.orphaned && (
                      <span className="text-[10px] text-aegisaxiom-text-muted shrink-0">(removed)</span>
                    )}
                    {!p.has_pricing && !p.orphaned && (
                      <span className="text-[10px] text-amber-600 dark:text-amber-400 shrink-0">
                        (not configured)
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-aegisaxiom-text-secondary mt-0.5">{p.label || p.model}</p>
                </div>

                {/* Input + Output inputs */}
                <div className="grid grid-cols-2 gap-2.5 mb-3">
                  <div>
                    <label className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider block mb-1">
                      Input / 1M
                    </label>
                    <div className="flex items-center gap-1">
                      <span className="text-aegisaxiom-text-muted text-xs">$</span>
                      <input
                        type="number"
                        step="0.01"
                        min="0"
                        value={edits[p.model]?.input_per_million ?? p.input_per_million}
                        onChange={(e) => handleEdit(p.model, 'input_per_million', e.target.value)}
                        className="input-field text-xs w-full"
                      />
                    </div>
                  </div>
                  <div>
                    <label className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider block mb-1">
                      Output / 1M
                    </label>
                    <div className="flex items-center gap-1">
                      <span className="text-aegisaxiom-text-muted text-xs">$</span>
                      <input
                        type="number"
                        step="0.01"
                        min="0"
                        value={edits[p.model]?.output_per_million ?? p.output_per_million}
                        onChange={(e) => handleEdit(p.model, 'output_per_million', e.target.value)}
                        className="input-field text-xs w-full"
                      />
                    </div>
                  </div>
                </div>

                {/* Save button — only when edited */}
                {edited && (
                  <button
                    onClick={() => handleSave(p.model)}
                    disabled={saving[p.model]}
                    className="flex items-center justify-center gap-1.5 w-full px-3 py-2 rounded-lg text-xs font-medium bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border border-violet-200 dark:border-violet-600/20 hover:bg-violet-600/30 transition-all disabled:opacity-50"
                  >
                    {saving[p.model] ? (
                      <RefreshCw size={12} className="animate-spin" />
                    ) : (
                      <Save size={12} />
                    )}
                    Save changes
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

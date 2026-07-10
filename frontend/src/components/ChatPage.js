import React, { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useChat } from '@/contexts/ChatContext';
import { useAuth } from '@/contexts/AuthContext';
import {
  Send,
  Plus,
  Trash2,
  Bot,
  MessageCircle,
  Search,
  Sparkles,
  ChevronRight,
  X,
  PanelLeft,
  TrendingUp,
  DollarSign,
  Target,
  FileText,
} from 'lucide-react';
import ChatMarkdown from './ChatMarkdown';
import ChatInputAttachButton from './chat/ChatInputAttachButton';
import PMOQuickActions from './chat/PMOQuickActions';
import Tier2ConfirmCard from './chat/Tier2ConfirmCard';

// ---------------------------------------------------------------------------
// Tier-2 block detection — parses the structured blocked payload out of an
// assistant message. Returns { tool, intendedParams, detail } or null.
// ---------------------------------------------------------------------------
function parseTier2Block(message) {
  if (!message || message.role !== 'assistant') return null;
  // The backend may embed the JSON payload directly on the message object
  // or stringify it inside message.content.
  if (message._blocked === true) {
    return {
      tool: message.tool ?? null,
      intendedParams: message.intended_params ?? null,
      detail: message.detail ?? null,
    };
  }
  // Fallback: try to parse content as JSON if it looks like a blocked payload.
  if (typeof message.content === 'string') {
    try {
      const parsed = JSON.parse(message.content);
      if (parsed?._blocked === true) {
        return {
          tool: parsed.tool ?? null,
          intendedParams: parsed.intended_params ?? null,
          detail: parsed.detail ?? null,
        };
      }
    } catch {
      // Not JSON — treat as normal prose
    }
  }
  return null;
}

const STEP_ICONS = {
  Routing: '\u2192',
  Confirmed: '\u2713',
  Understanding: '\u2734',
  Searching: '\u25CE',
  Synthesizing: '\u2726',
  Limit: '\u26A0',
  Timeout: '\u231B',
};

// Relative time — "just now", "5m", "2h", "3d", else MMM d
function relativeTime(iso) {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const diff = Date.now() - then;
  if (diff < 45_000) return 'just now';
  const m = Math.round(diff / 60_000);
  if (m < 60) return `${m}m`;
  const h = Math.round(diff / 3_600_000);
  if (h < 24) return `${h}h`;
  const d = Math.round(diff / 86_400_000);
  if (d < 7) return `${d}d`;
  return new Date(then).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

const SUGGESTIONS = [
  { text: 'What are my top deals this month?', Icon: TrendingUp },
  { text: 'Show my commission summary', Icon: DollarSign },
  { text: 'Help me understand my pipeline', Icon: Target },
  { text: 'What contracts are expiring soon?', Icon: FileText },
];

function agentDisplayName(pinnedAgent) {
  if (pinnedAgent === 'nova') return 'Nova';
  return 'AegisAxiom AI';
}

function sessionDisplayTitle(session) {
  const pinnedDisplayName = agentDisplayName(session?.pinned_agent);
  if (session?.pinned_agent && pinnedDisplayName !== 'AegisAxiom AI') {
    return `${pinnedDisplayName}: ${session.title || 'New Chat'}`;
  }
  return session?.title || 'New Chat';
}

function ThinkingBubble({ thinking }) {
  const [expanded, setExpanded] = useState(false);
  if (!thinking || thinking.length === 0) return null;

  return (
    <div className="mb-2">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1.5 text-[11px] text-aegisaxiom-text-muted hover:text-violet-600 dark:text-violet-400 transition-colors group"
      >
        <Sparkles size={12} className="text-violet-500/70" />
        <span className="font-medium">Thinking</span>
        <ChevronRight size={11} className={`transition-transform ${expanded ? 'rotate-90' : ''}`} />
        {!expanded && (
          <span className="text-aegisaxiom-text-muted">
            {thinking.length} step{thinking.length !== 1 ? 's' : ''}
          </span>
        )}
      </button>
      {expanded && (
        <div
          className="mt-2 pl-1 border-l-2 border-gradient-to-b from-violet-600/40 to-fuchsia-600/40 space-y-1.5"
          style={{
            borderImage: 'linear-gradient(to bottom, rgba(124,58,237,0.4), rgba(192,38,211,0.4)) 1',
          }}
        >
          {thinking.map((t, i) => (
            <div key={i} className="flex items-start gap-2 pl-3 py-0.5">
              <span className="text-[11px] text-violet-600 dark:text-violet-400/80 shrink-0 mt-px">
                {STEP_ICONS[t.step] || '\u2022'}
              </span>
              <div className="text-[11px] leading-relaxed">
                <span className="text-aegisaxiom-text-muted font-medium mr-1.5">{t.step}</span>
                <span className="text-aegisaxiom-text-secondary">
                  <ChatMarkdown content={t.detail} />
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Module-level so React keeps the component type stable across parent renders
// (inline definitions cause unmount/remount on every ChatPage render, which
// destroys the search input's focus after each keystroke).
// testIdSuffix disambiguates the desktop ("") and mobile-drawer ("-mobile")
// instances, which are both in the DOM simultaneously.
function SidebarBody({
  sessions,
  filtered,
  activeSession,
  search,
  setSearch,
  onSelectSession,
  onNewSession,
  onDelete,
  onClose,
  testIdSuffix = '',
}) {
  return (
    <>
      <div className="p-4 border-b border-aegisaxiom-border">
        {onClose && (
          <div className="flex items-center justify-between mb-3">
            <p
              className="text-sm font-semibold text-aegisaxiom-text"
              style={{ fontFamily: 'Manrope, sans-serif' }}
            >
              Chats
            </p>
            <button
              onClick={onClose}
              aria-label="Close chats"
              className="w-8 h-8 rounded-lg hover:bg-aegisaxiom-elevated text-aegisaxiom-text-secondary hover:text-aegisaxiom-text flex items-center justify-center transition-colors"
            >
              <X size={16} />
            </button>
          </div>
        )}
        <button
          onClick={onNewSession}
          data-testid={`chat-new-session-btn${testIdSuffix}`}
          className="w-full flex items-center justify-center gap-2 text-sm font-medium py-2.5 rounded-xl bg-gradient-to-r from-violet-600 to-fuchsia-600 text-white shadow-sm hover:shadow-md hover:opacity-95 active:scale-[0.99] transition-all"
        >
          <Plus size={16} />
          New Chat
        </button>
        {sessions.length > 3 && (
          <div className="relative mt-3">
            <Search
              size={14}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted"
            />
            <input
              type="text"
              placeholder="Search chats..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-xs text-aegisaxiom-text-secondary placeholder:text-aegisaxiom-text-muted pl-9 pr-3 py-2 outline-none focus:border-violet-500/40 focus:ring-2 focus:ring-violet-500/10 transition-all"
              data-testid={`chat-search${testIdSuffix}`}
            />
          </div>
        )}
      </div>
      <div className="flex-1 overflow-y-auto py-2 px-2">
        {filtered.length === 0 && (
          <div className="text-center py-10 px-4">
            <div className="w-10 h-10 rounded-full bg-aegisaxiom-elevated mx-auto mb-3 flex items-center justify-center">
              <MessageCircle size={18} className="text-aegisaxiom-text-muted" />
            </div>
            <p className="text-aegisaxiom-text-secondary text-xs font-medium mb-1">
              {search ? 'No matching chats' : 'No chats yet'}
            </p>
            {!search && (
              <p className="text-aegisaxiom-text-muted text-[11px] leading-relaxed">
                Start a new conversation to get help with your AEGISAXIOM data.
              </p>
            )}
          </div>
        )}
        {filtered.map((s) => {
          const active = activeSession?.session_id === s.session_id;
          const ts = relativeTime(s.updated_at || s.created_at);
          return (
            <div
              key={s.session_id}
              onClick={() => onSelectSession(s)}
              data-testid={`chat-session-item-${s.session_id}${testIdSuffix}`}
              className={`group relative flex items-start gap-2.5 px-3 py-2.5 rounded-lg cursor-pointer transition-all mb-0.5 ${
                active
                  ? 'bg-violet-50 dark:bg-violet-600/15 text-violet-700 dark:text-violet-300 ring-1 ring-violet-200 dark:ring-violet-600/20'
                  : 'text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated hover:text-aegisaxiom-text'
              }`}
            >
              <MessageCircle
                size={14}
                className={`shrink-0 mt-0.5 ${active ? 'opacity-90' : 'opacity-50'}`}
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-baseline gap-2">
                  <span className="text-sm truncate flex-1">{sessionDisplayTitle(s)}</span>
                  {ts && (
                    <span
                      className={`text-[10px] shrink-0 ${
                        active
                          ? 'text-violet-600/70 dark:text-violet-300/70'
                          : 'text-aegisaxiom-text-muted'
                      }`}
                    >
                      {ts}
                    </span>
                  )}
                </div>
                {s.chat_number && (
                  <span className="text-[10px] text-aegisaxiom-text-muted font-mono">
                    {s.chat_number}
                  </span>
                )}
              </div>
              <button
                onClick={(e) => onDelete(s.session_id, e)}
                className="opacity-0 group-hover:opacity-100 focus:opacity-100 text-aegisaxiom-text-muted hover:text-red-600 dark:hover:text-red-400 transition-all shrink-0 p-1 -mr-1 rounded"
                data-testid={`delete-session-${s.session_id}${testIdSuffix}`}
                aria-label="Delete chat"
              >
                <Trash2 size={13} />
              </button>
            </div>
          );
        })}
      </div>
      <div className="p-3 border-t border-aegisaxiom-border text-center">
        <p className="text-[11px] text-aegisaxiom-text-muted">
          {sessions.length} conversation{sessions.length !== 1 ? 's' : ''}
        </p>
      </div>
    </>
  );
}

export default function ChatPage() {
  const { user } = useAuth();
  const {
    sessions,
    activeSession,
    messages,
    loading,
    sessionsLoaded,
    loadSessions,
    selectSession,
    newSession,
    deleteSession,
    sendMessage,
  } = useChat();
  const [searchParams] = useSearchParams();
  // The URL-pinned agent is the source of truth for header, placeholder, and
  // every session created from this page while the param is present. Do NOT
  // strip ?agent=… from the URL — keeping it sticky is what makes "New Chat",
  // suggestion buttons, and "Open in new tab" inherit the pin.
  const urlPinnedAgent = searchParams.get('agent') || null;
  const [input, setInput] = useState('');
  const [search, setSearch] = useState('');
  const [attachments, setAttachments] = useState([]);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  // eslint-disable-next-line no-unused-vars -- bump forces a re-render so relativeTime() recomputes
  const [_tick, setTick] = useState(0);
  const bottomRef = useRef(null);
  const agentLaunchRef = useRef(null);

  // Refresh relative timestamps every 60s
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 60_000);
    return () => clearInterval(id);
  }, []);

  // Close mobile drawer on Escape
  useEffect(() => {
    if (!mobileSidebarOpen) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') setMobileSidebarOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [mobileSidebarOpen]);

  useEffect(() => {
    if (!sessionsLoaded) loadSessions();
  }, [sessionsLoaded, loadSessions]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Pre-fill the chat input from a `?q=…` URL param. Used by the command
  // palette's "Ask PMO Pilot" action so the user can edit the seeded
  // question before hitting send instead of having it auto-fired.
  // Runs once on mount; subsequent URL changes intentionally do not
  // overwrite the user's draft.
  const prefilledRef = useRef(false);
  useEffect(() => {
    if (prefilledRef.current) return;
    const q = searchParams.get('q');
    if (q) {
      setInput(q);
      prefilledRef.current = true;
    }
  }, [searchParams]);

  // Latest activeSession in a ref so the artifact-submitted handler always
  // re-fetches against the *current* session — not whichever session was
  // active when the handler was bound. Switching sessions during the 500ms
  // delay below could otherwise overwrite the new session's messages with
  // the old one's.
  const activeSessionRef = useRef(activeSession);
  useEffect(() => {
    activeSessionRef.current = activeSession;
  }, [activeSession]);

  useEffect(() => {
    const handler = async () => {
      // Small delay to let backend finish saving the AI response
      await new Promise((r) => setTimeout(r, 500));
      const current = activeSessionRef.current;
      if (current) selectSession(current);
    };
    window.addEventListener('artifact-submitted', handler);
    window.addEventListener('arc-quiz-graded', handler);
    return () => {
      window.removeEventListener('artifact-submitted', handler);
      window.removeEventListener('arc-quiz-graded', handler);
    };
  }, [selectSession]);

  useEffect(() => {
    if (!urlPinnedAgent) {
      agentLaunchRef.current = null;
      return;
    }
    if (!sessionsLoaded) return;

    if (activeSession?.pinned_agent === urlPinnedAgent) {
      agentLaunchRef.current = null;
      return;
    }

    if (agentLaunchRef.current === urlPinnedAgent) return;
    agentLaunchRef.current = urlPinnedAgent;

    const existingSession = sessions.find((s) => s.pinned_agent === urlPinnedAgent);

    const initAgentSession = async () => {
      if (existingSession) {
        await selectSession(existingSession);
      } else {
        await newSession(urlPinnedAgent);
      }
      agentLaunchRef.current = null;
    };
    initAgentSession();
  }, [
    urlPinnedAgent,
    sessionsLoaded,
    sessions,
    activeSession?.pinned_agent,
    selectSession,
    newSession,
  ]);

  const handleSend = (e) => {
    e.preventDefault();
    if (!input.trim() && attachments.length === 0) return;
    let msg = input.trim();
    if (attachments.length > 0) {
      const markers = attachments
        .map((a) => `[ATTACHED: ${a.filename} (upload_id=${a.upload_id})]`)
        .join('\n');
      msg = `${markers}\n\n${msg}`;
      setAttachments([]);
    }
    sendMessage(msg);
    setInput('');
  };

  const handleDelete = (id, e) => {
    e.stopPropagation();
    deleteSession(id);
  };

  const filtered = search
    ? sessions.filter((s) => sessionDisplayTitle(s).toLowerCase().includes(search.toLowerCase()))
    : sessions;

  // URL pin wins over the active session's pin so the header doesn't flicker
  // to "AegisAxiom AI" between clicking New Chat and the new pinned session arriving.
  const effectivePinnedAgent = urlPinnedAgent || activeSession?.pinned_agent || null;
  const activeAgentName = agentDisplayName(effectivePinnedAgent);

  // Wrap session selection so mobile drawer closes on pick
  const handleSelectSession = (s) => {
    selectSession(s);
    setMobileSidebarOpen(false);
  };

  // Every session created while the URL pins an agent must inherit that pin —
  // covers the New Chat button, the empty-state suggestion buttons, and any
  // retry/recreate path. Generic /chat (no agent param) keeps current behavior.
  const handleNewSession = async () => {
    const s = await newSession(urlPinnedAgent);
    setMobileSidebarOpen(false);
    return s;
  };

  return (
    <div className="flex h-[calc(100vh-3.5rem)] -m-6 overflow-hidden" data-testid="chat-page">
      {/* Persistent sidebar — desktop only */}
      <aside
        className="hidden md:flex w-72 bg-aegisaxiom-base border-r border-aegisaxiom-border flex-col shrink-0"
        data-testid="chat-sidebar"
      >
        <SidebarBody
          sessions={sessions}
          filtered={filtered}
          activeSession={activeSession}
          search={search}
          setSearch={setSearch}
          onSelectSession={handleSelectSession}
          onNewSession={handleNewSession}
          onDelete={handleDelete}
        />
      </aside>

      {/* Mobile drawer */}
      <div
        className={`md:hidden fixed inset-0 z-40 flex pointer-events-none transition-opacity duration-200 ${
          mobileSidebarOpen ? 'opacity-100 pointer-events-auto' : 'opacity-0'
        }`}
        aria-hidden={!mobileSidebarOpen}
        {...(!mobileSidebarOpen && { inert: '' })}
      >
        <div
          className={`absolute inset-0 bg-black/50 backdrop-blur-sm transition-opacity duration-200 ${
            mobileSidebarOpen ? 'opacity-100' : 'opacity-0'
          }`}
          onClick={() => setMobileSidebarOpen(false)}
        />
        <aside
          className={`relative w-[85%] max-w-[300px] bg-aegisaxiom-base border-r border-aegisaxiom-border flex flex-col shadow-2xl transform transition-transform duration-300 ease-out ${
            mobileSidebarOpen ? 'translate-x-0' : '-translate-x-full'
          }`}
          role="dialog"
          aria-modal="true"
          aria-label="Chat history"
        >
          <SidebarBody
            sessions={sessions}
            filtered={filtered}
            activeSession={activeSession}
            search={search}
            setSearch={setSearch}
            onSelectSession={handleSelectSession}
            onNewSession={handleNewSession}
            onDelete={handleDelete}
            onClose={() => setMobileSidebarOpen(false)}
            testIdSuffix="-mobile"
          />
        </aside>
      </div>

      {/* Main area */}
      <div className="flex-1 flex flex-col bg-aegisaxiom-base min-w-0">
        {!activeSession ? (
          <div className="flex-1 flex flex-col min-w-0" data-testid="chat-empty-state">
            {/* Mobile top bar — Chats toggle */}
            <div className="md:hidden flex items-center justify-between px-4 py-3 border-b border-aegisaxiom-border shrink-0">
              <button
                onClick={() => setMobileSidebarOpen(true)}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-aegisaxiom-surface border border-aegisaxiom-border text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated transition-all"
                data-testid="chat-mobile-open-sidebar-empty"
                aria-label="Open chats"
              >
                <PanelLeft size={16} />
                <span className="text-xs font-medium">Chats</span>
                {sessions.length > 0 && (
                  <span className="text-[10px] bg-violet-100 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 px-1.5 py-0.5 rounded-full font-semibold">
                    {sessions.length}
                  </span>
                )}
              </button>
              <button
                onClick={handleNewSession}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gradient-to-r from-violet-600 to-fuchsia-600 text-white text-xs font-medium shadow-sm active:scale-95 transition-all"
                aria-label="New chat"
              >
                <Plus size={14} />
                New
              </button>
            </div>

            {/* Empty-state hero */}
            <div className="flex-1 flex flex-col items-center justify-center text-center px-5 py-8 relative overflow-hidden">
              {/* Soft radial glow backdrop */}
              <div
                aria-hidden="true"
                className="absolute inset-0 pointer-events-none"
                style={{
                  background:
                    'radial-gradient(circle at 50% 38%, rgba(139, 92, 246, 0.12), transparent 55%)',
                }}
              />
              <div className="relative mb-6">
                <div
                  aria-hidden="true"
                  className="absolute inset-0 -m-4 rounded-full blur-2xl opacity-70"
                  style={{
                    background:
                      'radial-gradient(circle, rgba(139, 92, 246, 0.35), transparent 70%)',
                  }}
                />
                <div className="relative w-20 h-20 rounded-2xl bg-gradient-to-br from-violet-600 to-fuchsia-600 flex items-center justify-center shadow-lg shadow-violet-600/20">
                  <Bot size={36} className="text-white" />
                </div>
              </div>
              <h2
                className="relative text-2xl font-bold text-aegisaxiom-text mb-2 tracking-tight"
                style={{ fontFamily: 'Manrope, sans-serif' }}
              >
                {activeAgentName}
              </h2>
              <p className="relative text-aegisaxiom-text-muted text-sm max-w-md mb-8 leading-relaxed px-2">
                Your intelligent business assistant. Ask about deals, commissions, contracts, or
                anything about the AEGISAXIOM system.
              </p>
              <div className="relative grid grid-cols-1 sm:grid-cols-2 gap-2.5 max-w-lg w-full">
                {SUGGESTIONS.map(({ text, Icon }, i) => (
                  <button
                    key={i}
                    onClick={async () => {
                      const s = await handleNewSession();
                      if (s) sendMessage(text);
                    }}
                    data-testid={`suggestion-${i}`}
                    className="group flex items-center gap-3 text-left text-xs text-aegisaxiom-text-secondary hover:text-violet-700 dark:hover:text-violet-300 bg-aegisaxiom-surface hover:bg-aegisaxiom-elevated border border-aegisaxiom-border hover:border-violet-300 dark:hover:border-violet-600/40 rounded-xl px-3.5 py-3 transition-all active:scale-[0.98]"
                  >
                    <span className="w-7 h-7 rounded-lg bg-violet-50 dark:bg-violet-600/15 text-violet-600 dark:text-violet-300 flex items-center justify-center shrink-0 group-hover:bg-violet-100 dark:group-hover:bg-violet-600/25 transition-colors">
                      <Icon size={14} />
                    </span>
                    <span className="flex-1 leading-snug">{text}</span>
                  </button>
                ))}
              </div>
              <p className="relative text-[11px] text-aegisaxiom-text-muted mt-6">
                or start typing your own question
              </p>
            </div>
          </div>
        ) : (
          <>
            <div
              className="px-4 md:px-6 py-3 border-b border-aegisaxiom-border flex items-center gap-3 shrink-0"
              data-testid="chat-header"
            >
              <button
                onClick={() => setMobileSidebarOpen(true)}
                className="md:hidden w-9 h-9 rounded-lg bg-aegisaxiom-surface border border-aegisaxiom-border text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated flex items-center justify-center shrink-0 transition-all"
                aria-label="Open chats"
                data-testid="chat-mobile-open-sidebar-header"
              >
                <PanelLeft size={16} />
              </button>
              <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-violet-600 to-fuchsia-600 flex items-center justify-center shrink-0">
                <Bot size={18} className="text-white" />
              </div>
              <div className="min-w-0 flex-1">
                <p
                  className="text-sm font-semibold text-aegisaxiom-text"
                  style={{ fontFamily: 'Manrope, sans-serif' }}
                >
                  {activeAgentName}
                </p>
                <div className="flex items-center gap-2 min-w-0">
                  <p className="text-xs text-aegisaxiom-text-muted truncate">
                    {activeSession.title || 'New Chat'}
                  </p>
                  {activeSession.chat_number && (
                    <span
                      className="text-[10px] font-mono bg-aegisaxiom-surface text-aegisaxiom-text-secondary border border-aegisaxiom-border px-1.5 py-0.5 rounded shrink-0"
                      data-testid="chat-number-badge"
                    >
                      {activeSession.chat_number}
                    </span>
                  )}
                </div>
              </div>
            </div>

            <PMOQuickActions
              effectivePinnedAgent={effectivePinnedAgent}
              messageCount={messages.length}
              onSend={(text) => {
                sendMessage(text);
              }}
            />

            <div className="flex-1 overflow-y-auto px-4 md:px-6 py-4" data-testid="chat-messages">
              {messages.length === 0 && (
                <div className="flex flex-col items-center justify-center py-12 text-center">
                  <Sparkles size={20} className="text-violet-400 mb-2" />
                  <p className="text-aegisaxiom-text-muted text-sm">
                    Ask me anything about your business data...
                  </p>
                </div>
              )}

              {messages
                .filter((m) => {
                  if (m.role === 'user' && typeof m.content === 'string') {
                    if (m.content.startsWith('[INTAKE SELECTION]')) return false;
                    if (m.content.startsWith('[UPLOADS SUBMITTED]')) return false;
                    if (m.content.startsWith('[QUIZ GRADED]')) return false;
                  }
                  return true;
                })
                .map((m) => {
                  const tier2 = parseTier2Block(m);

                  // Tier-2 blocked tool call — render inline confirm card instead of a bubble.
                  if (tier2) {
                    return (
                      <div
                        key={m.message_id}
                        className="flex mb-4 justify-start"
                        data-testid="fullchat-msg-tier2-block"
                      >
                        <div className="flex gap-2.5 md:gap-3 max-w-[88%] md:max-w-2xl min-w-0">
                          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-600 to-fuchsia-600 flex items-center justify-center shrink-0 mt-0.5 shadow-sm">
                            <Bot size={14} className="text-white" />
                          </div>
                          <Tier2ConfirmCard
                            tool={tier2.tool}
                            intendedParams={tier2.intendedParams}
                            detail={tier2.detail}
                            onSend={sendMessage}
                          />
                        </div>
                      </div>
                    );
                  }

                  return (
                    <div
                      key={m.message_id}
                      className={`flex mb-4 ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
                      data-testid={`fullchat-msg-${m.role}`}
                    >
                      <div
                        className={`flex gap-2.5 md:gap-3 max-w-[88%] md:max-w-2xl min-w-0 ${
                          m.role === 'user' ? 'flex-row-reverse' : ''
                        }`}
                      >
                        {m.role === 'assistant' && (
                          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-600 to-fuchsia-600 flex items-center justify-center shrink-0 mt-0.5 shadow-sm">
                            <Bot size={14} className="text-white" />
                          </div>
                        )}
                        <div
                          className={`rounded-2xl px-4 py-3 text-sm leading-relaxed min-w-0 max-w-full ${
                            m.role === 'user'
                              ? 'bg-violet-600 text-white rounded-br-md shadow-sm'
                              : 'bg-aegisaxiom-surface text-aegisaxiom-text rounded-bl-md border border-aegisaxiom-border shadow-sm'
                          }`}
                        >
                          {m.role === 'assistant' && <ThinkingBubble thinking={m.thinking} />}
                          {m.role === 'user' ? (
                            <p className="whitespace-pre-wrap break-words">{m.content}</p>
                          ) : (
                            <ChatMarkdown content={m.content} />
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}

              {loading && (
                <div className="flex justify-start mb-4">
                  <div className="flex gap-2.5 md:gap-3">
                    <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-600 to-fuchsia-600 flex items-center justify-center shrink-0 shadow-sm">
                      <Bot size={14} className="text-white" />
                    </div>
                    <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-2xl rounded-bl-md px-4 py-3 flex items-center gap-2.5">
                      <div className="flex gap-1.5">
                        {[0, 1, 2].map((i) => (
                          <div
                            key={i}
                            className="w-2 h-2 bg-violet-400 rounded-full animate-bounce"
                            style={{ animationDelay: `${i * 0.15}s` }}
                          />
                        ))}
                      </div>
                      <span className="text-[11px] font-medium text-aegisaxiom-text-muted">Thinking</span>
                    </div>
                  </div>
                </div>
              )}
              <div ref={bottomRef} />
            </div>

            <div
              className="px-4 md:px-6 py-3 md:py-4 border-t border-aegisaxiom-border shrink-0 bg-aegisaxiom-base"
              data-testid="chat-input-area"
            >
              <form
                onSubmit={handleSend}
                className="flex gap-2 md:gap-3 items-end max-w-3xl mx-auto"
              >
                {activeSession && (
                  <ChatInputAttachButton
                    sessionId={activeSession.session_id}
                    attachments={attachments}
                    onChange={setAttachments}
                    disabled={loading}
                  />
                )}
                <textarea
                  rows={1}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      handleSend(e);
                    }
                  }}
                  placeholder={`Message ${activeAgentName}...`}
                  disabled={loading}
                  className="flex-1 bg-aegisaxiom-surface border border-aegisaxiom-border focus:border-violet-500 focus:ring-2 focus:ring-violet-500/10 rounded-xl px-4 py-3 text-sm text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted outline-none transition-all resize-none"
                  data-testid="chat-page-input"
                />
                <button
                  type="submit"
                  disabled={(!input.trim() && attachments.length === 0) || loading}
                  className="w-11 h-11 rounded-xl bg-gradient-to-r from-violet-600 to-fuchsia-600 hover:shadow-md hover:shadow-violet-600/20 disabled:opacity-40 disabled:cursor-not-allowed disabled:shadow-none flex items-center justify-center transition-all shrink-0 active:scale-95"
                  data-testid="chat-page-send"
                  aria-label="Send message"
                >
                  <Send size={16} className="text-white" />
                </button>
              </form>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
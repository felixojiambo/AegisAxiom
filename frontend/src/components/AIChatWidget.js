import React, { useState, useEffect, useRef } from 'react';
import { useChat } from '@/contexts/ChatContext';
import { useAuth } from '@/contexts/AuthContext';
import { useNavigate, useLocation } from 'react-router-dom';
import { X, Send, Plus, Trash2, Bot, Maximize2, Sparkles } from 'lucide-react';
import ChatMarkdown from './ChatMarkdown';

export default function AIChatWidget() {
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
  const navigate = useNavigate();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');
  const bottomRef = useRef(null);

  useEffect(() => {
    if (user && open && !sessionsLoaded) loadSessions();
  }, [user, open, sessionsLoaded, loadSessions]);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Auto-select first session when sessions load and none is active
  useEffect(() => {
    if (open && sessionsLoaded && sessions.length > 0 && !activeSession) {
      selectSession(sessions[0]);
    }
  }, [open, sessionsLoaded, sessions, activeSession, selectSession]);

  const handleSend = (e) => {
    e.preventDefault();
    if (!input.trim()) return;
    sendMessage(input.trim());
    setInput('');
  };

  const handleDelete = (id, e) => {
    e.stopPropagation();
    deleteSession(id);
  };

  const openFullPage = () => {
    setOpen(false);
    navigate('/chat');
  };

  // Hide widget on the full chat page
  if (!user || location.pathname === '/chat') return null;

  return (
    <>
      <button
        onClick={() => setOpen((p) => !p)}
        data-testid="ai-chat-toggle"
        className="fixed bottom-6 right-6 z-50 w-14 h-14 rounded-full bg-gradient-to-br from-violet-600 to-fuchsia-600 flex items-center justify-center shadow-lg shadow-violet-900/50 hover:scale-105 active:scale-95 transition-transform animate-pulse-glow"
        aria-label={open ? 'Close AegisAxiom AI' : 'Open AegisAxiom AI'}
      >
        {open ? <X size={22} className="text-white" /> : <Bot size={22} className="text-white" />}
      </button>
      {open && (
        <div
          className="fixed z-40 bg-aegisaxiom-base border border-violet-500/30 rounded-2xl shadow-2xl shadow-violet-900/30 flex overflow-hidden animate-fadeInUp
            bottom-[calc(5rem+env(safe-area-inset-bottom))] right-3 left-3 max-h-[calc(100vh-6rem)] h-[min(560px,calc(100vh-6rem))]
            md:bottom-24 md:right-6 md:left-auto md:w-[380px] md:h-[560px]"
          data-testid="ai-chat-window"
          style={{ boxShadow: '0 0 40px -8px rgba(124,58,237,0.4)' }}
          role="dialog"
          aria-label="AegisAxiom AI chat"
        >
          <div className="w-32 sm:w-40 bg-aegisaxiom-surface border-r border-aegisaxiom-border flex flex-col shrink-0">
            <div className="p-3 border-b border-aegisaxiom-border">
              <button
                onClick={() => newSession()}
                className="w-full flex items-center gap-1.5 text-xs font-medium text-violet-600 dark:text-violet-300 hover:text-violet-700 dark:hover:text-violet-200 transition-colors"
                data-testid="new-chat-btn"
              >
                <Plus size={13} />
                New Chat
              </button>
            </div>
            <div className="flex-1 overflow-y-auto py-1">
              {sessions.length === 0 && (
                <p className="text-[10px] text-aegisaxiom-text-muted text-center px-3 py-4 leading-snug">
                  No chats yet
                </p>
              )}
              {sessions.map((s) => (
                <div
                  key={s.session_id}
                  onClick={() => selectSession(s)}
                  className={`group flex items-center justify-between px-3 py-2 cursor-pointer transition-colors ${activeSession?.session_id === s.session_id ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300' : 'text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated hover:text-aegisaxiom-text'}`}
                  data-testid={`chat-session-${s.session_id}`}
                >
                  <span className="text-xs truncate flex-1">{s.title || 'New Chat'}</span>
                  <button
                    onClick={(e) => handleDelete(s.session_id, e)}
                    className="opacity-0 group-hover:opacity-100 focus:opacity-100 text-aegisaxiom-text-muted hover:text-red-600 dark:hover:text-red-400 ml-1 transition-all shrink-0"
                    aria-label="Delete chat"
                  >
                    <Trash2 size={11} />
                  </button>
                </div>
              ))}
            </div>
          </div>
          <div className="flex-1 flex flex-col min-w-0">
            <div className="p-3 border-b border-aegisaxiom-border flex items-center justify-between shrink-0">
              <div className="flex items-center gap-2 min-w-0">
                <div className="w-7 h-7 rounded-full bg-gradient-to-br from-violet-600 to-fuchsia-600 flex items-center justify-center shrink-0 shadow-sm">
                  <Bot size={14} className="text-white" />
                </div>
                <div className="min-w-0">
                  <p className="text-xs font-semibold text-aegisaxiom-text truncate">AegisAxiom AI</p>
                  <p className="text-[10px] text-aegisaxiom-text-muted truncate">
                    {user.role?.replace('_', ' ')} access
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-1 shrink-0">
                <button
                  onClick={openFullPage}
                  title="Open full chat"
                  className="text-aegisaxiom-text-muted hover:text-violet-600 dark:hover:text-violet-400 transition-colors p-1.5 rounded-md hover:bg-aegisaxiom-elevated"
                  data-testid="expand-chat-btn"
                  aria-label="Open full chat page"
                >
                  <Maximize2 size={14} />
                </button>
                <button
                  onClick={() => setOpen(false)}
                  title="Close"
                  className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text transition-colors p-1.5 rounded-md hover:bg-aegisaxiom-elevated md:hidden"
                  aria-label="Close chat"
                >
                  <X size={14} />
                </button>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto p-3" data-testid="widget-messages">
              {!activeSession ? (
                <div className="flex flex-col items-center justify-center h-full text-center px-2 py-4">
                  <div className="relative mb-3">
                    <div
                      aria-hidden="true"
                      className="absolute inset-0 -m-2 rounded-full blur-xl opacity-60"
                      style={{
                        background:
                          'radial-gradient(circle, rgba(139, 92, 246, 0.4), transparent 70%)',
                      }}
                    />
                    <div className="relative w-11 h-11 rounded-xl bg-gradient-to-br from-violet-600 to-fuchsia-600 flex items-center justify-center shadow-md shadow-violet-600/20">
                      <Bot size={22} className="text-white" />
                    </div>
                  </div>
                  <p className="text-xs font-semibold text-aegisaxiom-text mb-1">AegisAxiom AI</p>
                  <p className="text-[11px] text-aegisaxiom-text-muted mb-3 leading-snug">
                    Ask about your deals, commissions, or contracts
                  </p>
                  <button
                    onClick={() => newSession()}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gradient-to-r from-violet-600 to-fuchsia-600 text-white text-[11px] font-medium shadow-sm hover:shadow-md hover:opacity-95 active:scale-95 transition-all"
                  >
                    <Plus size={12} />
                    Start chat
                  </button>
                </div>
              ) : messages.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full text-center px-2">
                  <Sparkles size={16} className="text-violet-400 mb-1.5" />
                  <p className="text-aegisaxiom-text-muted text-[11px] leading-snug">
                    Ask me anything about your commissions and deals
                  </p>
                </div>
              ) : (
                messages.map((m) => (
                  <div
                    key={m.message_id}
                    className={`flex mb-3 ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
                    data-testid={`chat-message-${m.role}`}
                  >
                    <div
                      className={`max-w-[85%] rounded-xl px-3 py-2 text-xs leading-relaxed ${
                        m.role === 'user'
                          ? 'bg-violet-600 text-white rounded-br-sm shadow-sm'
                          : 'bg-aegisaxiom-elevated text-aegisaxiom-text rounded-bl-sm border border-aegisaxiom-border'
                      }`}
                    >
                      {m.role === 'user' ? m.content : <ChatMarkdown content={m.content} />}
                    </div>
                  </div>
                ))
              )}
              {loading && (
                <div className="flex justify-start mb-3">
                  <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-xl rounded-bl-sm px-3 py-2 flex items-center gap-2">
                    <div className="flex gap-1">
                      {[0, 1, 2].map((i) => (
                        <div
                          key={i}
                          className="w-1.5 h-1.5 bg-violet-400 rounded-full animate-bounce"
                          style={{ animationDelay: `${i * 0.15}s` }}
                        />
                      ))}
                    </div>
                    <span className="text-[10px] font-medium text-aegisaxiom-text-muted">Thinking</span>
                  </div>
                </div>
              )}
              <div ref={bottomRef} />
            </div>
            <form
              onSubmit={handleSend}
              className="p-3 border-t border-aegisaxiom-border flex gap-2 shrink-0"
            >
              <input
                className="flex-1 min-w-0 bg-aegisaxiom-surface border border-aegisaxiom-border focus:border-violet-500 focus:ring-2 focus:ring-violet-500/10 rounded-lg px-3 py-2 text-xs text-aegisaxiom-text placeholder:text-aegisaxiom-text-muted outline-none transition-all"
                placeholder={activeSession ? 'Ask AegisAxiom AI...' : 'Create a chat first'}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={!activeSession || loading}
                data-testid="chat-input"
              />
              <button
                type="submit"
                disabled={!input.trim() || loading || !activeSession}
                className="w-8 h-8 rounded-lg bg-gradient-to-r from-violet-600 to-fuchsia-600 hover:shadow-md hover:shadow-violet-600/20 disabled:opacity-40 disabled:cursor-not-allowed disabled:shadow-none flex items-center justify-center transition-all shrink-0 active:scale-95"
                data-testid="chat-send-btn"
                aria-label="Send message"
              >
                <Send size={13} className="text-white" />
              </button>
            </form>
          </div>
        </div>
      )}
    </>
  );
}

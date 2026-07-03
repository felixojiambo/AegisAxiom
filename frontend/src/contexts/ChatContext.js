import React, { createContext, useContext, useState, useCallback, useRef, useEffect } from 'react';
import { api } from '../lib/api';
import { useAuth } from './AuthContext';
import API_URL from '../lib/apiConfig';

const ChatContext = createContext(null);
const API = API_URL;

export function ChatProvider({ children }) {
  const { user } = useAuth();
  const [sessions, setSessions] = useState([]);
  const [activeSession, setActiveSessionState] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [sessionsLoaded, setSessionsLoaded] = useState(false);

  const loadSessions = useCallback(async () => {
    if (!user) return;
    const res = await api.get(`${API}/api/chat/sessions`);
    if (res.ok) {
      const data = await res.json();
      setSessions(data);
      setSessionsLoaded(true);
      return data;
    }
    return [];
  }, [user]);

  const selectSession = useCallback(async (session) => {
    setActiveSessionState(session);
    const res = await api.get(`${API}/api/chat/sessions/${session.session_id}/messages`);
    if (res.ok) setMessages(await res.json());
  }, []);

  const newSession = useCallback(async (agent = null) => {
    const body = agent ? { agent } : {};
    const res = await api.post(`${API}/api/chat/sessions`, body);
    if (res.ok) {
      const session = await res.json();
      setSessions((p) => [session, ...p]);
      setActiveSessionState(session);
      setMessages([]);
      return session;
    }
    return null;
  }, []);

  const deleteSession = useCallback(
    async (id) => {
      await api.delete(`${API}/api/chat/sessions/${id}`);
      setSessions((prev) => {
        const updated = prev.filter((s) => s.session_id !== id);
        if (activeSession?.session_id === id) {
          if (updated.length > 0) {
            selectSession(updated[0]);
          } else {
            setActiveSessionState(null);
            setMessages([]);
          }
        }
        return updated;
      });
    },
    [activeSession, selectSession],
  );

  const sendMessage = useCallback(
    async (content) => {
      if (!content.trim() || loading || !activeSession) return;
      setMessages((p) => [
        ...p,
        {
          message_id: `tmp_${Date.now()}`,
          role: 'user',
          content,
          created_at: new Date().toISOString(),
        },
      ]);
      setLoading(true);
      try {
        const res = await api.post(
          `${API}/api/chat/sessions/${activeSession.session_id}/messages`,
          { content },
        );
        if (res.ok) {
          const aiMsg = await res.json();
          setMessages((p) => [...p, aiMsg]);
          setSessions((prev) =>
            prev.map((s) =>
              s.session_id === activeSession.session_id
                ? { ...s, title: s.title === 'New Chat' ? content.slice(0, 40) : s.title }
                : s,
            ),
          );
        } else {
          let errorText = 'Something went wrong. Please try again.';
          try {
            const errData = await res.json();
            errorText = errData.detail || errData.message || errorText;
          } catch {
            // Response body not JSON — use status text as fallback
            errorText = res.statusText || errorText;
          }
          setMessages((p) => [
            ...p,
            {
              message_id: `err_${Date.now()}`,
              role: 'assistant',
              content: `Error: ${errorText}`,
              created_at: new Date().toISOString(),
            },
          ]);
        }
      } catch {
        setMessages((p) => [
          ...p,
          {
            message_id: `err_${Date.now()}`,
            role: 'assistant',
            content: 'Connection error. Please try again.',
            created_at: new Date().toISOString(),
          },
        ]);
      } finally {
        setLoading(false);
      }
    },
    [loading, activeSession],
  );

  // Reset on logout
  useEffect(() => {
    if (!user) {
      setSessions([]);
      setActiveSessionState(null);
      setMessages([]);
      setSessionsLoaded(false);
    }
  }, [user]);

  const value = {
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
  };

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

export const useChat = () => useContext(ChatContext);

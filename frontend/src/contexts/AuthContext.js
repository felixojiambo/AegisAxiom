import React, { createContext, useContext, useState, useEffect } from 'react';
import { safeXhr } from '../lib/safeXhr';
import API_URL from '../lib/apiConfig';

const AuthContext = createContext(null);
const API = API_URL;
const TOKEN_KEY = 'aegisaxiom_session_token';

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    checkAuth();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const checkAuth = async () => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      const { data } = await safeXhr('GET', `${API}/api/auth/me`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      setUser(data || null);
    } catch {
      localStorage.removeItem(TOKEN_KEY);
      setUser(null);
    } finally {
      setLoading(false);
    }
  };

  const login = async (email, password) => {
    const { data } = await safeXhr('POST', `${API}/api/auth/login`, {
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    if (data.token) {
      localStorage.setItem(TOKEN_KEY, data.token);
    }
    setUser(data.user);
    return data;
  };

  const logout = async () => {
    const token = localStorage.getItem(TOKEN_KEY);
    try {
      await safeXhr('POST', `${API}/api/auth/logout`, {
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: '{}',
      });
    } catch {
      /* ignore */
    }
    localStorage.removeItem(TOKEN_KEY);
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, setUser, checkAuth }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);

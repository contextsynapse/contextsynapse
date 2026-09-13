import React, { createContext, useContext, useState, useCallback, useEffect } from 'react';
import api from '../lib/api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => {
    try {
      const stored = localStorage.getItem('contextsynapse_user');
      return stored ? JSON.parse(stored) : null;
    } catch {
      return null;
    }
  });
  const [token, setToken] = useState(() => localStorage.getItem('contextsynapse_token'));
  const [loading, setLoading] = useState(false);

  // Persist changes
  useEffect(() => {
    if (token) localStorage.setItem('contextsynapse_token', token);
    else localStorage.removeItem('contextsynapse_token');
  }, [token]);

  useEffect(() => {
    if (user) localStorage.setItem('contextsynapse_user', JSON.stringify(user));
    else localStorage.removeItem('contextsynapse_user');
  }, [user]);

  const signup = useCallback(async (email, password, displayName) => {
    setLoading(true);
    try {
      const res = await api.post('/auth/signup', {
        email,
        password,
        display_name: displayName,
      });
      localStorage.setItem('contextsynapse_token', res.data.token);
      localStorage.setItem('contextsynapse_user', JSON.stringify(res.data.user));
      setToken(res.data.token);
      setUser(res.data.user);
      return res.data;
    } finally {
      setLoading(false);
    }
  }, []);

  const login = useCallback(async (email, password) => {
    setLoading(true);
    try {
      const res = await api.post('/auth/login', { email, password });
      // Write to localStorage synchronously before React re-renders,
      // so subsequent API calls in child components pick up the token immediately.
      localStorage.setItem('contextsynapse_token', res.data.token);
      localStorage.setItem('contextsynapse_user', JSON.stringify(res.data.user));
      setToken(res.data.token);
      setUser(res.data.user);
      return res.data;
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    localStorage.removeItem('contextsynapse_token');
    localStorage.removeItem('contextsynapse_user');
  }, []);

  const refreshProfile = useCallback(async () => {
    if (!token) return;
    try {
      const res = await api.get('/auth/me');
      setUser(res.data);
    } catch {
      // token invalid — force logout
      logout();
    }
  }, [token, logout]);

  const value = {
    user,
    token,
    loading,
    isAuthenticated: !!token && !!user,
    signup,
    login,
    logout,
    refreshProfile,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within <AuthProvider>');
  return ctx;
}

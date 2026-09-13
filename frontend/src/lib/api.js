/**
 * Centralized API client with JWT auth interceptor.
 */
import axios from 'axios';
import toast from 'react-hot-toast';

// In development, requests go through setupProxy.js (same origin, no CORS).
// In production, requests go to the same host (API serves the frontend build).
const BASE_URL = '';

const api = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
  timeout: 60000, // 60s default timeout — allows time for graph loading
});

// ── Request interceptor: attach JWT ──────────────────────────────
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('contextsynapse_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  // Let browser set Content-Type for FormData (multipart/form-data with boundary)
  if (config.data instanceof FormData) {
    delete config.headers['Content-Type'];
  }
  return config;
});

// ── Response interceptor: handle 401 + toast errors ──────────────
api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response?.status;
    const detail = error.response?.data?.detail;

    if (status === 401) {
      localStorage.removeItem('contextsynapse_token');
      localStorage.removeItem('contextsynapse_user');
      // Redirect to login unless already there
      if (!window.location.pathname.startsWith('/login')) {
        window.location.href = '/login';
      }
    }

    // Normalize detail to a string (Pydantic v2 returns array of {type,loc,msg,input})
    const userMessage = typeof detail === 'string'
      ? detail
      : Array.isArray(detail)
        ? detail.map((d) => d.msg || JSON.stringify(d)).join('; ')
        : error.message || 'Something went wrong';
    error.userMessage = userMessage;

    // Toast non-401 errors (callers can opt out with { suppressErrorToast: true })
    if (status !== 401 && !error.config?.suppressErrorToast) {
      toast.error(userMessage);
    }

    return Promise.reject(error);
  },
);

export default api;

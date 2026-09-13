import React, { useState } from 'react';
import { KeyRound, AlertCircle, Loader2 } from 'lucide-react';
import axios from 'axios';

export default function LoginPage({ onLogin }) {
  const [adminKey, setAdminKey] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const res = await axios.post('/admin/login', { admin_key: adminKey });
      onLogin(res.data.token);
    } catch (err) {
      const msg =
        err.response?.status === 403
          ? 'Invalid admin key'
          : err.response?.data?.detail || 'Login failed — is the server running?';
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-neo-bg flex items-center justify-center">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm bg-neo-surface rounded-lg border border-neo-border p-8 shadow-xl"
      >
        <div className="flex flex-col items-center mb-6">
          <div className="w-12 h-12 rounded-full bg-neo-blue/20 flex items-center justify-center mb-3">
            <KeyRound size={24} className="text-neo-blue" />
          </div>
          <h1 className="text-xl font-semibold text-neo-text">ContextSynapse Admin</h1>
          <p className="text-sm text-neo-text-muted mt-1">Enter your admin key to continue</p>
        </div>

        {error && (
          <div className="flex items-center gap-2 text-neo-red text-sm bg-neo-red/10 rounded px-3 py-2 mb-4">
            <AlertCircle size={14} />
            {error}
          </div>
        )}

        <label className="block text-sm text-neo-text-muted mb-1.5">Admin Key</label>
        <input
          type="password"
          value={adminKey}
          onChange={(e) => setAdminKey(e.target.value)}
          placeholder="AICONTEXTDB_ADMIN_KEY"
          required
          autoFocus
          className="w-full px-3 py-2 rounded bg-neo-bg border border-neo-border text-neo-text placeholder-neo-text-muted/50 text-sm focus:outline-none focus:border-neo-blue transition-colors mb-4"
        />

        <button
          type="submit"
          disabled={loading || !adminKey}
          className="w-full py-2.5 rounded bg-neo-blue hover:bg-neo-blue/90 text-white font-medium text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2 border-none cursor-pointer"
        >
          {loading && <Loader2 size={16} className="animate-spin" />}
          {loading ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}

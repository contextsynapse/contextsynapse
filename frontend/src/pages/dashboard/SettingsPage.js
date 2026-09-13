import React, { useState } from 'react';
import { useForm } from 'react-hook-form';
import { useAuth } from '../../context/AuthContext';
import {
  Settings, Loader2, Check, Eye, EyeOff, Database, Download, UploadCloud, RefreshCw,
  Key, RotateCw, Copy, AlertTriangle,
} from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../../lib/api';

export default function SettingsPage() {
  const { user, refreshProfile } = useAuth();
  const [saving, setSaving] = useState(false);
  const [showPw, setShowPw] = useState(false);
  const [backups, setBackups] = useState([]);
  const [backupsLoading, setBackupsLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [graphs, setGraphs] = useState([]);

  // Tenant API key state
  const [tenantKeys, setTenantKeys] = useState([]);
  const [rotating, setRotating] = useState(false);
  const [newTenantKey, setNewTenantKey] = useState(null);
  const [keyCopied, setKeyCopied] = useState(false);

  const fetchBackups = () => {
    setBackupsLoading(true);
    api.get('/dashboard/backups')
      .then((res) => setBackups(res.data.backups || []))
      .catch(() => {})
      .finally(() => setBackupsLoading(false));
  };

  const fetchGraphs = () => {
    api.get('/dashboard/graphs')
      .then((res) => setGraphs(res.data.graphs || []))
      .catch(() => {});
  };

  const fetchTenantKeys = () => {
    api.get('/dashboard/api-keys')
      .then((res) => setTenantKeys(res.data.keys || []))
      .catch(() => {});
  };

  const handleRotateKey = async () => {
    if (!window.confirm('Rotate tenant API key? The current key will stop working immediately.')) return;
    setRotating(true);
    try {
      const res = await api.post('/dashboard/api-keys/rotate');
      setNewTenantKey(res.data.api_key);
      toast.success('Tenant API key rotated');
      fetchTenantKeys();
    } catch {} finally {
      setRotating(false);
    }
  };

  const copyTenantKey = () => {
    if (newTenantKey) {
      navigator.clipboard.writeText(newTenantKey);
      setKeyCopied(true);
      toast.success('Copied to clipboard');
      setTimeout(() => setKeyCopied(false), 2000);
    }
  };

  React.useEffect(() => { fetchBackups(); fetchGraphs(); fetchTenantKeys(); }, []);

  const createBackup = async (graphName) => {
    setCreating(true);
    try {
      await api.post('/dashboard/backups', { graph: graphName });
      toast.success(`Backup created for "${graphName}"`);
      fetchBackups();
    } catch {} finally { setCreating(false); }
  };

  const restoreBackup = async (cpId, graphName) => {
    if (!window.confirm(`Restore "${graphName}" from this backup? Current data will be overwritten.`)) return;
    try {
      await api.post(`/dashboard/backups/${cpId}/restore`, { graph: graphName });
      toast.success('Backup restored');
    } catch {}
  };

  const {
    register,
    handleSubmit,
    formState: { errors, isDirty },
    reset,
  } = useForm({
    defaultValues: {
      display_name: user?.display_name || '',
      password: '',
    },
  });

  const onSubmit = async (data) => {
    const payload = {};
    if (data.display_name && data.display_name !== user?.display_name) {
      payload.display_name = data.display_name;
    }
    if (data.password) {
      payload.password = data.password;
    }
    if (Object.keys(payload).length === 0) return;

    setSaving(true);
    try {
      await api.patch('/dashboard/settings', payload);
      toast.success('Settings updated');
      await refreshProfile();
      reset({ display_name: data.display_name, password: '' });
    } catch {
      // handled by interceptor
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="max-w-lg">
      <h1 className="text-xl font-bold mb-1" style={{ color: 'var(--neo-text)' }}>Settings</h1>
      <p className="text-sm mb-6" style={{ color: 'var(--neo-text-muted)' }}>
        Manage your account
      </p>

      <div
        className="rounded-xl p-6"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
      >
        <div className="flex items-center gap-2 mb-5">
          <Settings size={16} style={{ color: 'var(--neo-cyan)' }} />
          <h2 className="text-sm font-semibold" style={{ color: 'var(--neo-text)' }}>Profile</h2>
        </div>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: 'var(--neo-text-muted)' }}>
              Email
            </label>
            <input
              value={user?.email || ''}
              disabled
              className="w-full px-3 py-2 rounded-lg text-sm opacity-60"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            />
          </div>

          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: 'var(--neo-text-muted)' }}>
              Display Name
            </label>
            <input
              {...register('display_name', { required: 'Name is required' })}
              className="w-full px-3 py-2 rounded-lg text-sm outline-none"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            />
            {errors.display_name && (
              <p className="mt-1 text-xs" style={{ color: 'var(--neo-red)' }}>{errors.display_name.message}</p>
            )}
          </div>

          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: 'var(--neo-text-muted)' }}>
              New Password <span className="opacity-50">(leave blank to keep current)</span>
            </label>
            <div className="relative">
              <input
                {...register('password', {
                  minLength: { value: 6, message: 'At least 6 characters' },
                })}
                type={showPw ? 'text' : 'password'}
                className="w-full px-3 py-2 pr-10 rounded-lg text-sm outline-none"
                style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                placeholder="Min 6 characters"
              />
              <button
                type="button"
                onClick={() => setShowPw(!showPw)}
                className="absolute right-3 top-1/2 -translate-y-1/2 opacity-50 hover:opacity-100"
                style={{ color: 'var(--neo-text-muted)' }}
              >
                {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
            {errors.password && (
              <p className="mt-1 text-xs" style={{ color: 'var(--neo-red)' }}>{errors.password.message}</p>
            )}
          </div>

          <button
            type="submit"
            disabled={saving || !isDirty}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            {saving ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />}
            {saving ? 'Saving...' : 'Save Changes'}
          </button>
        </form>
      </div>

      {/* Backup & Restore */}
      <div
        className="rounded-xl p-6 mt-6"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
      >
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Database size={16} style={{ color: 'var(--neo-cyan)' }} />
            <h2 className="text-sm font-semibold" style={{ color: 'var(--neo-text)' }}>
              Backup & Restore
            </h2>
          </div>
          <button
            onClick={fetchBackups}
            className="flex items-center gap-1 px-2 py-1 rounded text-xs"
            style={{ color: 'var(--neo-text-muted)' }}
          >
            <RefreshCw size={12} /> Refresh
          </button>
        </div>

        {/* Create backup */}
        <div className="mb-4">
          <label className="block text-xs mb-1.5" style={{ color: 'var(--neo-text-muted)' }}>
            Create a backup checkpoint
          </label>
          <div className="flex gap-2 flex-wrap">
            {graphs.length > 0 ? graphs.map((g) => (
              <button
                key={g.name}
                onClick={() => createBackup(g.name)}
                disabled={creating}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition hover:opacity-80 disabled:opacity-50"
                style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
              >
                <Download size={12} />
                Backup "{g.name}"
              </button>
            )) : (
              <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                No graphs available
              </span>
            )}
          </div>
        </div>

        {/* Backup list */}
        {backupsLoading ? (
          <div className="flex justify-center py-4">
            <Loader2 size={16} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
          </div>
        ) : backups.length === 0 ? (
          <p className="text-xs py-4 text-center" style={{ color: 'var(--neo-text-muted)' }}>
            No backups yet. Create one above.
          </p>
        ) : (
          <div className="space-y-1.5">
            {backups.slice(0, 10).map((bp, i) => (
              <div
                key={bp.checkpoint_id || i}
                className="flex items-center justify-between px-3 py-2 rounded-lg"
                style={{ background: 'var(--neo-bg)' }}
              >
                <div className="flex items-center gap-2 text-xs min-w-0">
                  <Database size={12} style={{ color: 'var(--neo-cyan)' }} />
                  <span style={{ color: 'var(--neo-text)' }}>{bp.graph || 'unknown'}</span>
                  <span style={{ color: 'var(--neo-text-muted)' }}>
                    {bp.checkpoint_id ? bp.checkpoint_id.slice(0, 12) : ''}
                  </span>
                  {bp.timestamp && (
                    <span style={{ color: 'var(--neo-text-muted)' }}>
                      {new Date(bp.timestamp * 1000).toLocaleString()}
                    </span>
                  )}
                </div>
                <button
                  onClick={() => restoreBackup(bp.checkpoint_id, bp.graph)}
                  className="flex items-center gap-1 px-2 py-1 rounded text-xs transition hover:opacity-80"
                  style={{ color: 'var(--neo-cyan)', border: '1px solid var(--neo-border)' }}
                >
                  <UploadCloud size={10} /> Restore
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
      {/* Tenant API Key */}
      <div
        className="rounded-xl p-6 mt-6"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
      >
        <div className="flex items-center gap-2 mb-4">
          <Key size={16} style={{ color: 'var(--neo-cyan)' }} />
          <h2 className="text-sm font-semibold" style={{ color: 'var(--neo-text)' }}>
            Tenant API Key
          </h2>
        </div>
        <p className="text-xs mb-4" style={{ color: 'var(--neo-text-muted)' }}>
          Management key for your organization. For agent-level access, register agents on the{' '}
          <a href="/dashboard/agents" style={{ color: 'var(--neo-cyan)' }}>Agents page</a>.
        </p>

        {newTenantKey && (
          <div
            className="flex items-start gap-3 p-3 rounded-lg mb-4"
            style={{ background: 'rgba(76,217,100,0.1)', border: '1px solid var(--neo-green)' }}
          >
            <AlertTriangle size={14} style={{ color: 'var(--neo-yellow)' }} className="mt-0.5 shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium mb-2" style={{ color: 'var(--neo-text)' }}>
                New key generated — copy it now. It won't be shown again.
              </p>
              <div className="flex items-center gap-2">
                <code
                  className="flex-1 px-2 py-1.5 rounded text-xs break-all"
                  style={{ background: 'var(--neo-bg)', color: 'var(--neo-green)', fontFamily: "'JetBrains Mono', monospace" }}
                >
                  {newTenantKey}
                </code>
                <button
                  onClick={copyTenantKey}
                  className="shrink-0 p-1.5 rounded transition hover:opacity-80"
                  style={{ color: keyCopied ? 'var(--neo-green)' : 'var(--neo-text-muted)' }}
                >
                  {keyCopied ? <Check size={14} /> : <Copy size={14} />}
                </button>
              </div>
            </div>
          </div>
        )}

        <div className="space-y-2">
          {tenantKeys.map((k) => (
            <div
              key={k.tenant_id}
              className="flex items-center justify-between px-3 py-2.5 rounded-lg"
              style={{ background: 'var(--neo-bg)' }}
            >
              <div className="flex items-center gap-2 text-xs">
                <Key size={12} style={{ color: 'var(--neo-cyan)' }} />
                <span style={{ color: 'var(--neo-text)' }}>{k.tenant_name}</span>
                <span style={{ color: 'var(--neo-text-muted)' }}>{k.key_hint || '***'}</span>
                <span
                  className="px-1.5 py-0.5 rounded"
                  style={{
                    background: k.status === 'active' ? 'rgba(76,217,100,0.15)' : 'rgba(242,87,87,0.15)',
                    color: k.status === 'active' ? 'var(--neo-green)' : '#ef4444',
                  }}
                >
                  {k.status}
                </span>
              </div>
              {k.role === 'owner' && (
                <button
                  onClick={handleRotateKey}
                  disabled={rotating}
                  className="flex items-center gap-1 px-2 py-1 rounded text-xs transition hover:opacity-80 disabled:opacity-50"
                  style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
                >
                  {rotating ? <Loader2 size={12} className="animate-spin" /> : <RotateCw size={12} />}
                  Rotate
                </button>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

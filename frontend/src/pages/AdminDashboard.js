import React, { useEffect, useState, useCallback } from 'react';
import { Routes, Route } from 'react-router-dom';
import axios from 'axios';
import {
  Users, Activity, Server, Plus, Trash2, RotateCw, Ban,
  CheckCircle, RefreshCw, Loader2,
} from 'lucide-react';

import Sidebar from '../components/Sidebar';

// ── Axios instance with JWT ──────────────────────────────────────────

function useApi(token) {
  return useCallback(
    () => {
      const instance = axios.create();
      instance.interceptors.request.use((cfg) => {
        cfg.headers.Authorization = `Bearer ${token}`;
        return cfg;
      });
      return instance;
    },
    [token],
  );
}

// ── Main layout ─────────────────────────────────────────────────────

export default function AdminDashboard({ token }) {
  const createApi = useApi(token);

  return (
    <div className="flex flex-1 overflow-hidden">
      <Sidebar />
      <main className="flex-1 overflow-y-auto p-6 bg-neo-bg">
        <Routes>
          <Route index element={<OverviewPage createApi={createApi} />} />
          <Route path="tenants" element={<TenantsPage createApi={createApi} />} />
          <Route path="health" element={<HealthPage createApi={createApi} />} />
          <Route path="settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  );
}

// ── Shared UI ───────────────────────────────────────────────────────

function Card({ children, className = '' }) {
  return (
    <div className={`bg-neo-surface rounded-lg border border-neo-border p-5 ${className}`}>
      {children}
    </div>
  );
}

function StatCard({ icon: Icon, label, value, color = 'text-neo-blue' }) {
  return (
    <Card>
      <div className="flex items-center gap-3">
        <div className={`w-10 h-10 rounded-lg ${color.replace('text-', 'bg-')}/20 flex items-center justify-center`}>
          <Icon size={20} className={color} />
        </div>
        <div>
          <div className="text-2xl font-bold text-neo-text">{value}</div>
          <div className="text-xs text-neo-text-muted">{label}</div>
        </div>
      </div>
    </Card>
  );
}

function Badge({ status }) {
  const colors = {
    active: 'bg-neo-green/20 text-neo-green',
    suspended: 'bg-neo-yellow/20 text-neo-yellow',
    deleted: 'bg-neo-red/20 text-neo-red',
  };
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${colors[status] || 'bg-neo-surface-light text-neo-text-muted'}`}>
      {status}
    </span>
  );
}

function PageTitle({ children }) {
  return <h2 className="text-lg font-semibold text-neo-text mb-5">{children}</h2>;
}

// ── Overview page ───────────────────────────────────────────────────

function OverviewPage({ createApi }) {
  const [system, setSystem] = useState(null);
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    const api = createApi();
    try {
      const [sysRes, healthRes] = await Promise.all([
        api.get('/admin/system'),
        api.get('/health'),
      ]);
      setSystem(sysRes.data);
      setHealth(healthRes.data);
    } catch {
      // ignore — cards will show "—"
    } finally {
      setLoading(false);
    }
  }, [createApi]);

  useEffect(() => { load(); }, [load]);

  if (loading) return <Spinner />;

  return (
    <>
      <PageTitle>Dashboard Overview</PageTitle>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard icon={Users} label="Total Tenants" value={system?.total_tenants ?? '—'} color="text-neo-blue" />
        <StatCard icon={CheckCircle} label="Active" value={system?.active_tenants ?? '—'} color="text-neo-green" />
        <StatCard icon={Ban} label="Suspended" value={system?.suspended_tenants ?? '—'} color="text-neo-yellow" />
        <StatCard icon={Server} label="Server Status" value={health?.status === 'ok' ? 'Healthy' : 'Down'} color="text-neo-cyan" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <h3 className="text-sm font-medium text-neo-text-muted mb-3">Context Service</h3>
          <dl className="space-y-2 text-sm">
            <Row label="Agents" value={health?.agents ?? '—'} />
            <Row label="Sessions" value={health?.sessions ?? '—'} />
            <Row label="Vector Backend" value={health?.vector_backend || 'none'} />
            <Row label="Embeddings" value={health?.embedding_available ? 'available' : 'unavailable'} />
          </dl>
        </Card>
        <Card>
          <h3 className="text-sm font-medium text-neo-text-muted mb-3">Quick Actions</h3>
          <div className="flex flex-wrap gap-2">
            <button onClick={load} className="admin-btn">
              <RefreshCw size={14} /> Refresh
            </button>
          </div>
        </Card>
      </div>
    </>
  );
}

function Row({ label, value }) {
  return (
    <div className="flex justify-between">
      <dt className="text-neo-text-muted">{label}</dt>
      <dd className="text-neo-text font-medium">{value}</dd>
    </div>
  );
}

// ── Tenants page ────────────────────────────────────────────────────

function TenantsPage({ createApi }) {
  const [tenants, setTenants] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);
  const [newKey, setNewKey] = useState(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await createApi().get('/admin/tenants?include_inactive=true');
      setTenants(res.data);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [createApi]);

  useEffect(() => { load(); }, [load]);

  const handleCreate = async (e) => {
    e.preventDefault();
    setCreating(true);
    setError('');
    try {
      const res = await createApi().post('/admin/tenants', { name: newName });
      setNewKey(res.data.api_key);
      setNewName('');
      load();
    } catch (err) {
      setError(err.userMessage || 'Creation failed');
    } finally {
      setCreating(false);
    }
  };

  const handleSuspend = async (id) => {
    await createApi().patch(`/admin/tenants/${id}`, { status: 'suspended' });
    load();
  };

  const handleActivate = async (id) => {
    await createApi().patch(`/admin/tenants/${id}`, { status: 'active' });
    load();
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this tenant? This action is soft — it can be reversed.')) return;
    await createApi().delete(`/admin/tenants/${id}`);
    load();
  };

  const handleRotateKey = async (id) => {
    if (!window.confirm('Rotate API key? The old key will stop working immediately.')) return;
    const res = await createApi().post(`/admin/tenants/${id}/rotate-key`);
    alert(`New API key: ${res.data.api_key}\n\nCopy this now — it won't be shown again.`);
  };

  if (loading) return <Spinner />;

  return (
    <>
      <div className="flex items-center justify-between mb-5">
        <PageTitle>Tenants</PageTitle>
        <button onClick={() => setShowCreate(!showCreate)} className="admin-btn-primary">
          <Plus size={14} /> New Tenant
        </button>
      </div>

      {showCreate && (
        <Card className="mb-4">
          <form onSubmit={handleCreate} className="flex items-end gap-3">
            <div className="flex-1">
              <label className="block text-xs text-neo-text-muted mb-1">Tenant Name</label>
              <input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                required
                placeholder="my-org"
                className="w-full px-3 py-2 rounded bg-neo-bg border border-neo-border text-neo-text text-sm focus:outline-none focus:border-neo-blue"
              />
            </div>
            <button type="submit" disabled={creating} className="admin-btn-primary">
              {creating ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
              Create
            </button>
          </form>
          {error && <p className="text-neo-red text-sm mt-2">{error}</p>}
          {newKey && (
            <div className="mt-3 p-3 bg-neo-green/10 border border-neo-green/30 rounded text-sm">
              <span className="font-medium text-neo-green">API Key:</span>{' '}
              <code className="text-neo-text break-all">{newKey}</code>
              <p className="text-neo-text-muted text-xs mt-1">Copy this now — it won't be shown again.</p>
            </div>
          )}
        </Card>
      )}

      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-neo-text-muted border-b border-neo-border">
                <th className="pb-2 font-medium">Name</th>
                <th className="pb-2 font-medium">Status</th>
                <th className="pb-2 font-medium">Created</th>
                <th className="pb-2 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {tenants.map((t) => (
                <tr key={t.tenant_id} className="border-b border-neo-border/50 last:border-0">
                  <td className="py-3 text-neo-text font-medium">{t.name}</td>
                  <td className="py-3"><Badge status={t.status} /></td>
                  <td className="py-3 text-neo-text-muted">{new Date(t.created_at).toLocaleDateString()}</td>
                  <td className="py-3 text-right">
                    <div className="flex items-center justify-end gap-1">
                      {t.status === 'active' && (
                        <ActionBtn icon={Ban} title="Suspend" onClick={() => handleSuspend(t.tenant_id)} />
                      )}
                      {t.status === 'suspended' && (
                        <ActionBtn icon={CheckCircle} title="Activate" color="text-neo-green" onClick={() => handleActivate(t.tenant_id)} />
                      )}
                      <ActionBtn icon={RotateCw} title="Rotate Key" onClick={() => handleRotateKey(t.tenant_id)} />
                      <ActionBtn icon={Trash2} title="Delete" color="text-neo-red" onClick={() => handleDelete(t.tenant_id)} />
                    </div>
                  </td>
                </tr>
              ))}
              {tenants.length === 0 && (
                <tr>
                  <td colSpan={4} className="py-8 text-center text-neo-text-muted">
                    No tenants yet. Create one to get started.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}

function ActionBtn({ icon: Icon, title, color = 'text-neo-text-muted', onClick }) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`p-1.5 rounded hover:bg-neo-surface-light ${color} transition-colors bg-transparent border-none cursor-pointer`}
    >
      <Icon size={14} />
    </button>
  );
}

// ── Health page ─────────────────────────────────────────────────────

function HealthPage({ createApi }) {
  const [health, setHealth] = useState(null);
  const [ctxHealth, setCtxHealth] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    const api = createApi();
    try {
      const [hRes, cRes] = await Promise.all([
        api.get('/health'),
        api.get('/context/health').catch(() => ({ data: null })),
      ]);
      setHealth(hRes.data);
      setCtxHealth(cRes.data);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [createApi]);

  useEffect(() => { load(); }, [load]);

  if (loading) return <Spinner />;

  return (
    <>
      <div className="flex items-center justify-between mb-5">
        <PageTitle>System Health</PageTitle>
        <button onClick={load} className="admin-btn">
          <RefreshCw size={14} /> Refresh
        </button>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <h3 className="text-sm font-medium text-neo-text-muted mb-3 flex items-center gap-2">
            <Activity size={14} /> API Server
          </h3>
          <StatusDot ok={health?.status === 'ok'} label={health?.status === 'ok' ? 'Healthy' : 'Unhealthy'} />
        </Card>

        <Card>
          <h3 className="text-sm font-medium text-neo-text-muted mb-3 flex items-center gap-2">
            <Server size={14} /> Context Service
          </h3>
          {ctxHealth ? (
            <dl className="space-y-2 text-sm">
              <Row label="Status" value={ctxHealth.status} />
              <Row label="Agents" value={ctxHealth.agents} />
              <Row label="Sessions" value={ctxHealth.sessions} />
              <Row label="Vector" value={ctxHealth.vector_available ? 'available' : 'unavailable'} />
              <Row label="Backend" value={ctxHealth.vector_backend || 'none'} />
            </dl>
          ) : (
            <StatusDot ok={false} label="Unavailable" />
          )}
        </Card>
      </div>
    </>
  );
}

function StatusDot({ ok, label }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <div className={`w-2.5 h-2.5 rounded-full ${ok ? 'bg-neo-green' : 'bg-neo-red'}`} />
      <span className="text-neo-text">{label}</span>
    </div>
  );
}

// ── Settings page (placeholder) ─────────────────────────────────────

function SettingsPage() {
  return (
    <>
      <PageTitle>Settings</PageTitle>
      <Card>
        <p className="text-neo-text-muted text-sm">
          System settings will be configurable here in a future release.
        </p>
      </Card>
    </>
  );
}

// ── Spinner ─────────────────────────────────────────────────────────

function Spinner() {
  return (
    <div className="flex items-center justify-center py-20">
      <Loader2 size={28} className="animate-spin text-neo-blue" />
    </div>
  );
}

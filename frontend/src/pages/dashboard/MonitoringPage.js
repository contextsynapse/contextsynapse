import React, { useState, useEffect, useCallback } from 'react';
import {
  Activity, Database, Clock, Cpu, HardDrive, RefreshCw, Loader2, AlertTriangle,
} from 'lucide-react';
import api from '../../lib/api';

function StatCard({ icon: Icon, label, value, sub, color }) {
  return (
    <div
      className="p-4 rounded-xl"
      style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
    >
      <div className="flex items-center gap-2 mb-2">
        <Icon size={16} style={{ color: color || 'var(--neo-blue)' }} />
        <span className="text-xs font-medium" style={{ color: 'var(--neo-text-muted)' }}>{label}</span>
      </div>
      <div className="text-xl font-bold" style={{ color: 'var(--neo-text)' }}>{value}</div>
      {sub && <div className="text-xs mt-0.5" style={{ color: 'var(--neo-text-muted)' }}>{sub}</div>}
    </div>
  );
}

export default function MonitoringPage() {
  const [health, setHealth] = useState(null);
  const [slowQueries, setSlowQueries] = useState([]);
  const [agentActivity, setAgentActivity] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      const [healthRes, queriesRes, agentsRes] = await Promise.allSettled([
        api.get('/dashboard/monitoring/health'),
        api.get('/dashboard/monitoring/queries'),
        api.get('/dashboard/monitoring/agents'),
      ]);

      if (healthRes.status === 'fulfilled') setHealth(healthRes.value.data);
      if (queriesRes.status === 'fulfilled') setSlowQueries(queriesRes.value.data.queries || []);
      if (agentsRes.status === 'fulfilled') setAgentActivity(agentsRes.value.data.agents || []);
    } catch {} finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const handleRefresh = () => {
    setRefreshing(true);
    fetchAll();
  };

  if (!health && !loading) {
    return (
      <div className="max-w-6xl mx-auto">
        <h1 className="text-xl font-bold mb-4" style={{ color: 'var(--neo-text)' }}>System Monitoring</h1>
        <div className="flex items-center justify-center h-48 rounded-xl" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
          <div className="text-center">
            <Activity size={28} className="mx-auto mb-2" style={{ color: 'var(--neo-text-muted)', opacity: 0.4 }} />
            <p className="text-sm mb-3" style={{ color: 'var(--neo-text-muted)' }}>Monitoring service unavailable</p>
            <button onClick={fetchAll} className="px-4 py-2 rounded-lg text-sm font-medium" style={{ background: 'var(--neo-blue)', color: '#fff' }}>
              Retry
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      </div>
    );
  }

  return (
    <div className="max-w-4xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold" style={{ color: 'var(--neo-text)' }}>Monitoring</h1>
          <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
            System health, performance, and agent activity
          </p>
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition hover:opacity-80"
          style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
        >
          <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} /> Refresh
        </button>
      </div>

      {/* Health cards */}
      <div className="grid grid-cols-4 gap-3 mb-8">
        <StatCard
          icon={Activity}
          label="Status"
          value={health?.status || 'Unknown'}
          color={health?.status === 'healthy' ? 'var(--neo-green)' : '#ef4444'}
        />
        <StatCard
          icon={Database}
          label="Graphs"
          value={health?.graphs_count ?? '—'}
          sub={`${health?.total_nodes ?? 0} nodes`}
        />
        <StatCard
          icon={Clock}
          label="Uptime"
          value={health?.uptime || '—'}
        />
        <StatCard
          icon={HardDrive}
          label="Storage"
          value={health?.storage_used || '—'}
          sub={health?.storage_available || ''}
        />
      </div>

      {/* Slow queries */}
      <div className="mb-8">
        <h2 className="text-sm font-semibold mb-3" style={{ color: 'var(--neo-text-muted)' }}>
          <Clock size={14} className="inline mr-1" /> Slow Queries
        </h2>
        {slowQueries.length === 0 ? (
          <div
            className="text-center py-8 rounded-xl"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
          >
            <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>No slow queries detected</p>
          </div>
        ) : (
          <div className="space-y-2">
            {slowQueries.map((q, i) => (
              <div
                key={i}
                className="flex items-center justify-between p-3 rounded-lg"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
              >
                <div className="flex-1 mr-3">
                  <code
                    className="text-xs block truncate"
                    style={{ color: 'var(--neo-text)', fontFamily: "'JetBrains Mono', monospace" }}
                  >
                    {q.query}
                  </code>
                  <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                    {new Date(q.timestamp).toLocaleString()}
                  </span>
                </div>
                <span
                  className="text-xs font-mono px-2 py-1 rounded shrink-0"
                  style={{
                    background: q.duration_ms > 1000 ? 'rgba(239,68,68,0.1)' : 'rgba(234,179,8,0.1)',
                    color: q.duration_ms > 1000 ? '#ef4444' : 'var(--neo-yellow)',
                  }}
                >
                  {q.duration_ms}ms
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Agent activity */}
      <div>
        <h2 className="text-sm font-semibold mb-3" style={{ color: 'var(--neo-text-muted)' }}>
          <Cpu size={14} className="inline mr-1" /> Agent Activity
        </h2>
        {agentActivity.length === 0 ? (
          <div
            className="text-center py-8 rounded-xl"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
          >
            <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>No agent activity recorded</p>
          </div>
        ) : (
          <div className="space-y-2">
            {agentActivity.map((a, i) => (
              <div
                key={a.agent_id || i}
                className="flex items-center justify-between p-3 rounded-lg"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
              >
                <div className="flex items-center gap-3">
                  <div
                    className="w-2 h-2 rounded-full"
                    style={{ background: a.status === 'active' ? 'var(--neo-green)' : 'var(--neo-text-muted)' }}
                  />
                  <div>
                    <div className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>
                      {a.name || a.agent_id}
                    </div>
                    <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                      {a.last_action || 'No recent activity'}
                      {a.last_seen && ` \u00b7 ${new Date(a.last_seen).toLocaleString()}`}
                    </div>
                  </div>
                </div>
                <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                  {a.request_count ?? 0} requests
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

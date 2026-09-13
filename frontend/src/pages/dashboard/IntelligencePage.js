import React, { useState, useEffect, useCallback } from 'react';
import { Brain, Shield, RefreshCw, MessageSquare, Radar, AlertTriangle, TrendingUp, Activity, Loader2 } from 'lucide-react';
import { StatCard, ConflictCard, SuggestionCard } from '../../components/IntelligenceWidgets';
import api from '../../lib/api';
import { toast } from 'react-hot-toast';

export default function IntelligencePage() {
  const [stats, setStats] = useState(null);
  const [conflicts, setConflicts] = useState([]);
  const [config, setConfig] = useState(null);
  const [activeTab, setActiveTab] = useState('overview');
  const [conflictFilter, setConflictFilter] = useState('open');
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const [statsRes, conflictsRes, configRes] = await Promise.all([
        api.get('/dashboard/intelligence/stats'),
        api.get(`/dashboard/intelligence/conflicts?status=${conflictFilter}`),
        api.get('/dashboard/intelligence/config'),
      ]);
      setStats(statsRes.data.stats || {});
      setConflicts(conflictsRes.data.conflicts || []);
      setConfig(configRes.data);
    } catch (e) {
      console.error('Intelligence fetch error:', e);
    } finally {
      setLoading(false);
    }
  }, [conflictFilter]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleResolve = async (conflictId) => {
    const resolution = prompt('Resolution summary:');
    if (!resolution) return;
    try {
      await api.post(`/dashboard/intelligence/conflicts/${conflictId}/resolve`, { resolution });
      toast.success('Conflict resolved');
      fetchData();
    } catch (e) { toast.error('Failed to resolve conflict'); }
  };

  const handleDismiss = async (conflictId) => {
    try {
      await api.post(`/dashboard/intelligence/conflicts/${conflictId}/dismiss`);
      toast.success('Conflict dismissed');
      fetchData();
    } catch (e) { toast.error('Failed to dismiss conflict'); }
  };

  const watcherStats = stats?.source_watcher || {};
  const conflictStats = stats?.conflict_detector || {};
  const radarStats = stats?.context_radar || {};
  const feedbackStats = stats?.feedback_loop || {};

  const tabs = [
    { key: 'overview', icon: Brain, label: 'Overview' },
    { key: 'freshness', icon: RefreshCw, label: 'Freshness' },
    { key: 'quality', icon: TrendingUp, label: 'Quality' },
    { key: 'conflicts', icon: AlertTriangle, label: `Conflicts${conflictStats.open ? ` (${conflictStats.open})` : ''}` },
    { key: 'radar', icon: Radar, label: 'Radar' },
  ];

  return (
    <div className="p-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Brain size={24} style={{ color: 'var(--neo-blue)' }} />
          <div>
            <h1 className="text-xl font-bold" style={{ color: 'var(--neo-text)' }}>Context Intelligence</h1>
            <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Active curation, conflict detection, and proactive suggestions</p>
          </div>
        </div>
        <button onClick={fetchData} disabled={loading} className="flex items-center gap-1 px-3 py-1.5 rounded text-xs" style={{ background: 'var(--neo-blue)', color: '#fff' }}>
          {loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          {stats ? 'Refresh' : 'Load Data'}
        </button>
      </div>

      {!stats && !loading && (
        <div className="flex items-center justify-center h-48 rounded-xl" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
          <div className="text-center">
            <Brain size={28} className="mx-auto mb-2" style={{ color: 'var(--neo-text-muted)', opacity: 0.4 }} />
            <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>No intelligence data yet.</p>
            <p className="text-xs mt-1" style={{ color: 'var(--neo-text-muted)', opacity: 0.7 }}>Ingest content and run pipelines to generate insights.</p>
          </div>
        </div>
      )}
      {!stats && loading && (
        <div className="flex items-center justify-center h-48">
          <Loader2 size={24} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
        </div>
      )}

      {/* Tab Nav */}
      <div className="flex gap-1 mb-4" style={{ borderBottom: '1px solid var(--neo-border)' }}>
        {tabs.map(t => (
          <button key={t.key} onClick={() => setActiveTab(t.key)}
            className="flex items-center gap-1.5 px-3 py-2 text-xs font-medium"
            style={{
              color: activeTab === t.key ? 'var(--neo-blue)' : 'var(--neo-text-muted)',
              borderBottom: activeTab === t.key ? '2px solid var(--neo-blue)' : '2px solid transparent',
            }}>
            <t.icon size={12} /> {t.label}
          </button>
        ))}
      </div>

      {/* Overview Tab */}
      {activeTab === 'overview' && (
        <div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
            <StatCard label="Sources Watched" value={watcherStats.running ? 'Active' : 'Idle'} icon={RefreshCw} />
            <StatCard label="Open Conflicts" value={conflictStats.open || 0} icon={AlertTriangle} color={conflictStats.open > 0 ? 'var(--neo-red)' : 'var(--neo-green)'} />
            <StatCard label="Active Agents" value={radarStats.active_agents || 0} icon={Radar} />
            <StatCard label="Suggestions Sent" value={radarStats.total_suggestions || 0} icon={Activity} />
          </div>

          <h2 className="text-sm font-semibold mb-3" style={{ color: 'var(--neo-text)' }}>Module Status</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
            {config && ['source_watcher', 'feedback_loop', 'conflict_detector', 'context_radar'].map(mod => {
              const level = config[mod] || 'off';
              const colors = { auto: 'var(--neo-green)', suggest: 'var(--neo-blue)', off: 'var(--neo-text-muted)' };
              return (
                <div key={mod} className="p-3 rounded-lg" style={{ background: 'var(--neo-bg-secondary)', border: '1px solid var(--neo-border)' }}>
                  <div className="text-xs font-medium mb-1" style={{ color: 'var(--neo-text)' }}>{mod.replace(/_/g, ' ')}</div>
                  <span className="text-xs px-2 py-0.5 rounded-full" style={{ background: `${colors[level]}20`, color: colors[level] }}>
                    {level.toUpperCase()}
                  </span>
                </div>
              );
            })}
          </div>

          {conflictStats.by_severity && (
            <div>
              <h2 className="text-sm font-semibold mb-3" style={{ color: 'var(--neo-text)' }}>Conflict Severity</h2>
              <div className="flex gap-4 mb-6">
                {Object.entries(conflictStats.by_severity).map(([sev, count]) => {
                  const colors = { high: 'var(--neo-red)', medium: '#f59e0b', low: 'var(--neo-text-muted)' };
                  return (
                    <div key={sev} className="flex items-center gap-2">
                      <div className="w-3 h-3 rounded-full" style={{ background: colors[sev] }} />
                      <span className="text-xs" style={{ color: 'var(--neo-text)' }}>{sev}: {count}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <div className="p-4 rounded-lg" style={{ background: 'var(--neo-bg-secondary)', border: '1px solid var(--neo-border)' }}>
            <h2 className="text-sm font-semibold mb-2 flex items-center gap-2" style={{ color: 'var(--neo-text)' }}>
              <TrendingUp size={14} style={{ color: 'var(--neo-green)' }} /> Intelligence Benefits
            </h2>
            <div className="grid grid-cols-2 gap-3 text-xs" style={{ color: 'var(--neo-text-muted)' }}>
              <div>Conflicts detected: <strong style={{ color: 'var(--neo-text)' }}>{conflictStats.total || 0}</strong></div>
              <div>Conflicts resolved: <strong style={{ color: 'var(--neo-green)' }}>{conflictStats.resolved || 0}</strong></div>
              <div>Suggestions delivered: <strong style={{ color: 'var(--neo-text)' }}>{radarStats.delivered || 0}</strong></div>
              <div>Suggestions accepted: <strong style={{ color: 'var(--neo-green)' }}>{radarStats.accepted || 0}</strong></div>
            </div>
          </div>
        </div>
      )}

      {/* Freshness Tab */}
      {activeTab === 'freshness' && (
        <div>
          <p className="text-sm mb-4" style={{ color: 'var(--neo-text-muted)' }}>
            Source Watcher monitors ingested content for changes. Stale sources are flagged or auto-refreshed based on autonomy settings.
          </p>
          <StatCard label="Watcher Status" value={watcherStats.running ? 'Running' : 'Idle'} icon={RefreshCw} />
          <p className="text-xs mt-4" style={{ color: 'var(--neo-text-muted)' }}>
            Source tracking is added to new ingestions automatically. Use <code>check_freshness</code> tool for on-demand checks.
          </p>
        </div>
      )}

      {/* Quality Tab */}
      {activeTab === 'quality' && (
        <div>
          <p className="text-sm mb-4" style={{ color: 'var(--neo-text-muted)' }}>
            Feedback from agents and humans improves context quality over time. Nodes rated as misleading or outdated are ranked lower.
          </p>
          <div className="grid grid-cols-3 gap-3">
            <StatCard label="Feedback Signals" value={feedbackStats.total_signals || 0} icon={MessageSquare} />
            <StatCard label="Avg Usefulness" value={feedbackStats.avg_score ? feedbackStats.avg_score.toFixed(2) : 'N/A'} icon={TrendingUp} />
            <StatCard label="Auto-Archived" value={feedbackStats.auto_archived || 0} icon={Shield} />
          </div>
        </div>
      )}

      {/* Conflicts Tab */}
      {activeTab === 'conflicts' && (
        <div>
          <div className="flex gap-2 mb-4">
            {['open', 'resolved', 'dismissed'].map(f => (
              <button key={f} onClick={() => setConflictFilter(f)}
                className="px-2.5 py-1 rounded text-xs font-medium"
                style={{
                  background: conflictFilter === f ? 'var(--neo-blue)' : 'transparent',
                  color: conflictFilter === f ? '#fff' : 'var(--neo-text-muted)',
                }}>
                {f.charAt(0).toUpperCase() + f.slice(1)}
              </button>
            ))}
          </div>
          {conflicts.length === 0 ? (
            <p className="text-sm py-8 text-center" style={{ color: 'var(--neo-text-muted)' }}>No {conflictFilter} conflicts</p>
          ) : (
            conflicts.map(c => <ConflictCard key={c.conflict_id} conflict={c} onResolve={handleResolve} onDismiss={handleDismiss} />)
          )}
        </div>
      )}

      {/* Radar Tab */}
      {activeTab === 'radar' && (
        <div>
          <p className="text-sm mb-4" style={{ color: 'var(--neo-text-muted)' }}>
            Context Radar proactively surfaces relevant context to agents based on their current work.
          </p>
          <div className="grid grid-cols-3 gap-3 mb-4">
            <StatCard label="Active Agents" value={radarStats.active_agents || 0} icon={Radar} />
            <StatCard label="Pending" value={radarStats.pending || 0} icon={Activity} color="#f59e0b" />
            <StatCard label="Accepted" value={radarStats.accepted || 0} icon={TrendingUp} color="var(--neo-green)" />
          </div>
        </div>
      )}
    </div>
  );
}

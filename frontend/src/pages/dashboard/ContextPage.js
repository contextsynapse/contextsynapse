/**
 * ContextPage — Cross-session context overview.
 *
 * Read-only summary of all context across all sessions.
 * Links to SessionsPage for editing.
 */

import React, { useState, useEffect, useCallback } from 'react';
import {
  Layers, Loader2, Lock, Eye, Unlock, AlertTriangle,
  Database, GitMerge, FileText, ArrowRight, RefreshCw, Plus, X,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import api from '../../lib/api';

const SENSITIVITY_LEVELS = [
  { value: 'public', label: 'Public', color: '#22c55e', icon: Unlock },
  { value: 'internal', label: 'Internal', color: '#3b82f6', icon: Eye },
  { value: 'confidential', label: 'Confidential', color: '#f59e0b', icon: Lock },
  { value: 'restricted', label: 'Restricted', color: '#ef4444', icon: AlertTriangle },
];

export default function ContextPage() {
  const navigate = useNavigate();
  const [sessions, setSessions] = useState([]);
  const [contextSummaries, setContextSummaries] = useState({});
  const [lineageSummaries, setLineageSummaries] = useState({});
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/dashboard/sessions');
      const sessionList = res.data.sessions || [];
      setSessions(sessionList);

      // Fetch context + lineage for each session in parallel
      const contextPromises = sessionList.map((s) =>
        api.get(`/dashboard/sessions/${s.session_id}/context`)
          .then((r) => ({ id: s.session_id, data: r.data }))
          .catch(() => ({ id: s.session_id, data: { count: 0, estimated_tokens: 0, items: [] } }))
      );
      const lineagePromises = sessionList.map((s) =>
        api.get(`/dashboard/sessions/${s.session_id}/context/lineage`)
          .then((r) => ({ id: s.session_id, data: r.data }))
          .catch(() => ({ id: s.session_id, data: { lineage: [], breadcrumb: '' } }))
      );

      const [ctxResults, lineageResults] = await Promise.all([
        Promise.all(contextPromises),
        Promise.all(lineagePromises),
      ]);

      const ctxMap = {};
      ctxResults.forEach((r) => { ctxMap[r.id] = r.data; });
      setContextSummaries(ctxMap);

      const linMap = {};
      lineageResults.forEach((r) => { linMap[r.id] = r.data; });
      setLineageSummaries(linMap);
    } catch {} finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      await api.post('/dashboard/sessions', { name: newName.trim(), agent_id: null });
      toast.success(`Context session "${newName}" created`);
      setNewName('');
      setShowCreate(false);
      fetchAll();
    } catch {
      toast.error('Failed to create session');
    } finally {
      setCreating(false);
    }
  };

  // Aggregate stats
  const totalItems = Object.values(contextSummaries).reduce((sum, c) => sum + (c.count || 0), 0);
  const totalTokens = Object.values(contextSummaries).reduce((sum, c) => sum + (c.estimated_tokens || 0), 0);
  const totalSources = Object.values(lineageSummaries).reduce((sum, l) => sum + ((l.lineage || []).length), 0);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      </div>
    );
  }

  return (
    <div className="max-w-4xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold" style={{ color: 'var(--neo-text)' }}>Context Overview</h1>
          <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
            Cross-session view of all context in your workspace
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition hover:opacity-90"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            <Plus size={14} /> New Context
          </button>
          <button
            onClick={fetchAll}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition hover:opacity-80"
            style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
          >
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      {/* Inline create form */}
      {showCreate && (
        <div
          className="mb-4 p-4 rounded-xl flex items-center gap-3"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-blue)' }}
        >
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
            placeholder="Context session name..."
            autoFocus
            className="flex-1 px-3 py-1.5 rounded-lg text-sm outline-none"
            style={{
              background: 'var(--neo-bg)',
              border: '1px solid var(--neo-border)',
              color: 'var(--neo-text)',
            }}
          />
          <button
            onClick={handleCreate}
            disabled={creating || !newName.trim()}
            className="px-3 py-1.5 rounded-lg text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            {creating ? 'Creating...' : 'Create'}
          </button>
          <button
            onClick={() => { setShowCreate(false); setNewName(''); }}
            className="p-1.5 rounded-lg transition hover:opacity-80"
            style={{ color: 'var(--neo-text-muted)' }}
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* Summary cards */}
      <div className="grid grid-cols-3 gap-3 mb-6">
        <div
          className="p-4 rounded-xl"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        >
          <div className="flex items-center gap-2 mb-1">
            <Layers size={14} style={{ color: 'var(--neo-blue)' }} />
            <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Total Items</span>
          </div>
          <div className="text-2xl font-bold" style={{ color: 'var(--neo-text)' }}>{totalItems}</div>
          <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
            across {sessions.length} session{sessions.length !== 1 ? 's' : ''}
          </div>
        </div>
        <div
          className="p-4 rounded-xl"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        >
          <div className="flex items-center gap-2 mb-1">
            <FileText size={14} style={{ color: 'var(--neo-cyan)' }} />
            <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Total Tokens</span>
          </div>
          <div className="text-2xl font-bold" style={{ color: 'var(--neo-text)' }}>
            {totalTokens > 1000 ? `${(totalTokens / 1000).toFixed(1)}k` : totalTokens}
          </div>
        </div>
        <div
          className="p-4 rounded-xl"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        >
          <div className="flex items-center gap-2 mb-1">
            <GitMerge size={14} style={{ color: 'var(--neo-green)' }} />
            <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Data Sources</span>
          </div>
          <div className="text-2xl font-bold" style={{ color: 'var(--neo-text)' }}>{totalSources}</div>
          <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
            graphs, sessions, overlays
          </div>
        </div>
      </div>

      {/* Session context list */}
      {sessions.length === 0 ? (
        <div
          className="text-center py-16 rounded-xl"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        >
          <Layers size={32} className="mx-auto mb-3" style={{ color: 'var(--neo-text-muted)' }} />
          <p className="text-sm mb-4" style={{ color: 'var(--neo-text-muted)' }}>
            No context sessions yet. Create one to start building context.
          </p>
          <button
            onClick={() => setShowCreate(true)}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            <Plus size={14} /> New Context
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          {sessions.map((s) => {
            const ctx = contextSummaries[s.session_id] || {};
            const lin = lineageSummaries[s.session_id] || {};
            const lineage = lin.lineage || [];
            const itemCount = ctx.count || 0;
            const tokens = ctx.estimated_tokens || 0;

            // Count sensitivity distribution from items
            const items = ctx.items || ctx.context || [];
            const sensCounts = {};
            items.forEach((item) => {
              const sens = item.sensitivity || 'public';
              sensCounts[sens] = (sensCounts[sens] || 0) + 1;
            });

            return (
              <button
                key={s.session_id}
                onClick={() => navigate('/dashboard/sessions')}
                className="w-full text-left p-4 rounded-xl transition hover:scale-[1.01]"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
              >
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <Database size={14} style={{ color: '#af52de' }} />
                    <span className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>
                      {s.name}
                    </span>
                    <span
                      className="px-1.5 py-0.5 rounded text-xs"
                      style={{
                        background: s.status === 'active' ? 'rgba(76,217,100,0.15)' : 'rgba(242,87,87,0.15)',
                        color: s.status === 'active' ? 'var(--neo-green)' : '#ef4444',
                      }}
                    >
                      {s.status}
                    </span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                      {itemCount} items
                    </span>
                    <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                      ~{tokens > 1000 ? `${(tokens / 1000).toFixed(1)}k` : tokens} tokens
                    </span>
                    <span
                      onClick={(e) => {
                        e.stopPropagation();
                        navigate('/dashboard/sessions', { state: { openSession: s.session_id, tab: 'context' } });
                      }}
                      className="px-2 py-0.5 rounded text-xs font-medium transition hover:opacity-80"
                      style={{ background: 'rgba(0,122,255,0.15)', color: 'var(--neo-blue)' }}
                    >
                      + Add Context
                    </span>
                    <ArrowRight size={14} style={{ color: 'var(--neo-text-muted)' }} />
                  </div>
                </div>

                {/* Sensitivity distribution bar */}
                {itemCount > 0 && (
                  <div className="flex items-center gap-2 mb-2">
                    {SENSITIVITY_LEVELS.map((sl) => {
                      const count = sensCounts[sl.value] || 0;
                      if (count === 0) return null;
                      return (
                        <span
                          key={sl.value}
                          className="text-xs px-1.5 py-0.5 rounded"
                          style={{ background: `${sl.color}15`, color: sl.color }}
                        >
                          {sl.label}: {count}
                        </span>
                      );
                    })}
                  </div>
                )}

                {/* Lineage breadcrumb */}
                {lin.breadcrumb && (
                  <div className="flex items-center gap-1.5">
                    <GitMerge size={10} style={{ color: 'var(--neo-cyan)' }} />
                    <span
                      className="text-xs truncate"
                      style={{ color: 'var(--neo-text-muted)', maxWidth: '100%' }}
                    >
                      {lin.breadcrumb}
                    </span>
                  </div>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

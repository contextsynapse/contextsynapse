import React, { useState, useEffect, useCallback } from 'react';
import {
  Shield, Loader2, Download, RefreshCw, ChevronLeft, ChevronRight,
  Filter, User, Clock,
} from 'lucide-react';
import api from '../../lib/api';

const ACTION_COLORS = {
  backup_created: 'var(--neo-green)',
  backup_restored: 'var(--neo-blue)',
  session_created: 'var(--neo-cyan)',
  session_deleted: '#ef4444',
  agent_registered: 'var(--neo-cyan)',
  agent_deregistered: '#ef4444',
  access_granted: 'var(--neo-green)',
  access_revoked: '#ef4444',
  key_rotated: 'var(--neo-yellow, #f59e0b)',
  settings_changed: 'var(--neo-text-muted)',
  member_invited: 'var(--neo-blue)',
  member_removed: '#ef4444',
  role_changed: 'var(--neo-cyan)',
};

const PAGE_SIZE = 25;

export default function AuditPage() {
  const [entries, setEntries] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const [filterAction, setFilterAction] = useState('');
  const [showFilters, setShowFilters] = useState(false);

  const fetchAudit = useCallback(() => {
    setLoading(true);
    const params = new URLSearchParams({
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    });
    if (filterAction) params.set('action', filterAction);

    api.get(`/dashboard/audit?${params}`)
      .then((res) => {
        setEntries(res.data.entries || []);
        setTotal(res.data.total || 0);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [page, filterAction]);

  // Don't auto-fetch — user clicks Load when ready
  // useEffect(() => { fetchAudit(); }, [fetchAudit]);

  const exportCsv = () => {
    api.get('/dashboard/audit/export', { responseType: 'blob' })
      .then((res) => {
        const url = window.URL.createObjectURL(new Blob([res.data]));
        const a = document.createElement('a');
        a.href = url;
        a.download = 'audit_log.csv';
        a.click();
        window.URL.revokeObjectURL(url);
      })
      .catch(() => {});
  };

  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="max-w-5xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold" style={{ color: 'var(--neo-text)' }}>Audit Trail</h1>
          <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
            Immutable log of all admin and security actions
          </p>
        </div>
        <div className="flex gap-2">
          {!entries.length && !loading && (
            <button onClick={fetchAudit} className="px-3 py-1.5 rounded-lg text-xs font-medium"
              style={{ background: 'var(--neo-blue)', color: '#fff' }}>
              Load Audit Log
            </button>
          )}
          <button
            onClick={() => setShowFilters(!showFilters)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition hover:opacity-80"
            style={{
              border: '1px solid var(--neo-border)',
              color: showFilters ? 'var(--neo-cyan)' : 'var(--neo-text-muted)',
            }}
          >
            <Filter size={14} /> Filter
          </button>
          <button
            onClick={fetchAudit}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition hover:opacity-80"
            style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
          >
            <RefreshCw size={14} /> Refresh
          </button>
          <button
            onClick={exportCsv}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition hover:opacity-90"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            <Download size={14} /> Export CSV
          </button>
        </div>
      </div>

      {/* Filters */}
      {showFilters && (
        <div
          className="mb-4 p-4 rounded-xl flex items-center gap-4"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        >
          <label className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Action:</label>
          <select
            value={filterAction}
            onChange={(e) => { setFilterAction(e.target.value); setPage(0); }}
            className="px-2 py-1 rounded-lg text-sm outline-none"
            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          >
            <option value="">All Actions</option>
            {Object.keys(ACTION_COLORS).map((a) => (
              <option key={a} value={a}>{a.replace(/_/g, ' ')}</option>
            ))}
          </select>
          {filterAction && (
            <button
              onClick={() => { setFilterAction(''); setPage(0); }}
              className="text-xs px-2 py-1 rounded"
              style={{ color: 'var(--neo-text-muted)' }}
            >
              Clear
            </button>
          )}
        </div>
      )}

      {/* Table */}
      {loading ? (
        <div className="flex items-center justify-center h-64">
          <Loader2 size={24} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
        </div>
      ) : entries.length === 0 ? (
        <div
          className="text-center py-16 rounded-xl"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        >
          <Shield size={32} className="mx-auto mb-3" style={{ color: 'var(--neo-text-muted)' }} />
          <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
            No audit entries yet. Actions will be logged as you use the platform.
          </p>
        </div>
      ) : (
        <>
          <div
            className="rounded-xl overflow-hidden"
            style={{ border: '1px solid var(--neo-border)' }}
          >
            {/* Header */}
            <div
              className="grid gap-3 px-4 py-2 text-xs font-semibold"
              style={{
                gridTemplateColumns: '160px 1fr 120px 120px 1fr',
                background: 'var(--neo-surface)',
                color: 'var(--neo-text-muted)',
                borderBottom: '1px solid var(--neo-border)',
              }}
            >
              <span>Time</span>
              <span>Action</span>
              <span>Resource</span>
              <span>User</span>
              <span>Details</span>
            </div>

            {/* Rows */}
            {entries.map((e) => (
              <div
                key={e.entry_id}
                className="grid gap-3 px-4 py-2.5 text-xs items-center transition hover:opacity-90"
                style={{
                  gridTemplateColumns: '160px 1fr 120px 120px 1fr',
                  background: 'var(--neo-bg)',
                  borderBottom: '1px solid var(--neo-border)',
                }}
              >
                <span className="flex items-center gap-1" style={{ color: 'var(--neo-text-muted)' }}>
                  <Clock size={10} />
                  {new Date(e.timestamp * 1000).toLocaleString()}
                </span>
                <span
                  className="font-medium px-2 py-0.5 rounded w-fit"
                  style={{
                    color: ACTION_COLORS[e.action] || 'var(--neo-text)',
                    background: `${ACTION_COLORS[e.action] || 'var(--neo-text-muted)'}15`,
                  }}
                >
                  {e.action.replace(/_/g, ' ')}
                </span>
                <span style={{ color: 'var(--neo-text)' }}>
                  {e.resource_type && (
                    <span className="text-xs">
                      {e.resource_type}
                      {e.resource_id ? `: ${e.resource_id.slice(0, 12)}` : ''}
                    </span>
                  )}
                </span>
                <span className="flex items-center gap-1" style={{ color: 'var(--neo-text-muted)' }}>
                  <User size={10} />
                  {(e.user_id || '').slice(0, 12)}
                </span>
                <span className="truncate" style={{ color: 'var(--neo-text-muted)' }}>
                  {e.details && Object.keys(e.details).length > 0
                    ? Object.entries(e.details).map(([k, v]) => `${k}: ${v}`).join(', ')
                    : '—'}
                </span>
              </div>
            ))}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between mt-4">
              <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                {total} entries · Page {page + 1} of {totalPages}
              </span>
              <div className="flex gap-1">
                <button
                  onClick={() => setPage(Math.max(0, page - 1))}
                  disabled={page === 0}
                  className="p-1.5 rounded-lg transition disabled:opacity-30"
                  style={{ color: 'var(--neo-text-muted)', border: '1px solid var(--neo-border)' }}
                >
                  <ChevronLeft size={14} />
                </button>
                <button
                  onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
                  disabled={page >= totalPages - 1}
                  className="p-1.5 rounded-lg transition disabled:opacity-30"
                  style={{ color: 'var(--neo-text-muted)', border: '1px solid var(--neo-border)' }}
                >
                  <ChevronRight size={14} />
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

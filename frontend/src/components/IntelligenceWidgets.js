import React from 'react';
import { AlertTriangle, CheckCircle, XCircle, ThumbsUp, ThumbsDown, RefreshCw, Zap, Eye } from 'lucide-react';

// Stat Card
export function StatCard({ label, value, icon: Icon, color = 'var(--neo-blue)' }) {
  return (
    <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg-secondary)', border: '1px solid var(--neo-border)' }}>
      <div className="flex items-center gap-2 mb-1">
        {Icon && <Icon size={14} style={{ color }} />}
        <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>{label}</span>
      </div>
      <div className="text-xl font-bold" style={{ color }}>{value}</div>
    </div>
  );
}

// Conflict Card
export function ConflictCard({ conflict, onResolve, onDismiss }) {
  const severityColors = { high: 'var(--neo-red)', medium: '#f59e0b', low: 'var(--neo-text-muted)' };
  const color = severityColors[conflict.severity] || 'var(--neo-text-muted)';

  return (
    <div className="p-3 rounded-lg mb-2" style={{ background: 'var(--neo-bg-secondary)', border: `1px solid ${color}`, borderLeft: `3px solid ${color}` }}>
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <AlertTriangle size={14} style={{ color }} />
          <span className="text-xs font-medium" style={{ color }}>{conflict.severity.toUpperCase()}</span>
          <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'var(--neo-bg)', color: 'var(--neo-text-muted)' }}>
            {conflict.conflict_type.replace('_', ' ')}
          </span>
        </div>
        <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
          {new Date(conflict.detected_at * 1000).toLocaleString()}
        </span>
      </div>
      <p className="text-sm mb-2" style={{ color: 'var(--neo-text)' }}>{conflict.summary}</p>
      <div className="text-xs mb-2" style={{ color: 'var(--neo-text-muted)' }}>
        Nodes: {conflict.node_a_id?.slice(0, 12)}... vs {conflict.node_b_id?.slice(0, 12)}...
      </div>
      {conflict.status === 'open' && (
        <div className="flex gap-2">
          <button onClick={() => onResolve(conflict.conflict_id)} className="flex items-center gap-1 px-2 py-1 rounded text-xs" style={{ background: 'var(--neo-green)', color: '#fff' }}>
            <CheckCircle size={10} /> Resolve
          </button>
          <button onClick={() => onDismiss(conflict.conflict_id)} className="flex items-center gap-1 px-2 py-1 rounded text-xs" style={{ background: 'var(--neo-bg)', color: 'var(--neo-text-muted)', border: '1px solid var(--neo-border)' }}>
            <XCircle size={10} /> Dismiss
          </button>
        </div>
      )}
      {conflict.status === 'resolved' && (
        <span className="text-xs" style={{ color: 'var(--neo-green)' }}>Resolved: {conflict.resolution}</span>
      )}
    </div>
  );
}

// Feedback Toolbar
export function FeedbackToolbar({ nodeId, onFeedback }) {
  const signals = [
    { key: 'helpful', icon: ThumbsUp, label: 'Helpful', color: 'var(--neo-green)' },
    { key: 'misleading', icon: ThumbsDown, label: 'Wrong', color: 'var(--neo-red)' },
    { key: 'outdated', icon: RefreshCw, label: 'Outdated', color: '#f59e0b' },
    { key: 'irrelevant', icon: XCircle, label: 'Irrelevant', color: 'var(--neo-text-muted)' },
  ];

  return (
    <div className="flex gap-1">
      {signals.map(s => (
        <button key={s.key} onClick={() => onFeedback(nodeId, s.key)}
          className="flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs transition hover:opacity-80"
          style={{ color: s.color, border: `1px solid ${s.color}20` }}
          title={s.label}>
          <s.icon size={10} /> {s.label}
        </button>
      ))}
    </div>
  );
}

// Suggestion Card
export function SuggestionCard({ suggestion }) {
  const triggerIcons = {
    source_changed: RefreshCw,
    conflict_found: AlertTriangle,
    high_usefulness: Zap,
    related_decision: Eye,
  };
  const Icon = triggerIcons[suggestion.trigger] || Zap;

  return (
    <div className="p-2 rounded-lg mb-1.5 flex items-start gap-2" style={{ background: 'var(--neo-bg-secondary)', border: '1px solid var(--neo-border)' }}>
      <Icon size={14} style={{ color: 'var(--neo-blue)', marginTop: 2 }} />
      <div>
        <p className="text-xs" style={{ color: 'var(--neo-text)' }}>{suggestion.message}</p>
        <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
          {new Date(suggestion.created_at * 1000).toLocaleString()}
        </span>
      </div>
    </div>
  );
}

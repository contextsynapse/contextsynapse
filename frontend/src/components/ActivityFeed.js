import React from 'react';
import { Loader2, Shield } from 'lucide-react';

const OP_COLORS = {
  ingest: 'var(--neo-green)',
  write: 'var(--neo-blue)',
  create: 'var(--neo-blue)',
  update: 'var(--neo-cyan)',
  delete: '#ef4444',
  read: 'var(--neo-text-muted)',
};

function opColor(operation) {
  const op = (operation || '').toLowerCase();
  for (const [key, color] of Object.entries(OP_COLORS)) {
    if (op.includes(key)) return color;
  }
  return 'var(--neo-cyan)';
}

export default function ActivityFeed({
  activity = [],
  loading = false,
  maxItems = 20,
  emptyMessage = 'No activity recorded yet',
}) {
  if (loading) {
    return (
      <div className="flex items-center justify-center py-4">
        <Loader2 size={16} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      </div>
    );
  }

  if (activity.length === 0) {
    return (
      <p className="text-xs py-3 text-center" style={{ color: 'var(--neo-text-muted)' }}>
        {emptyMessage}
      </p>
    );
  }

  return (
    <div className="space-y-1.5">
      {activity.slice(0, maxItems).map((act, i) => (
        <div
          key={act.id || i}
          className="flex items-center justify-between px-3 py-2 rounded-lg"
          style={{ background: 'var(--neo-surface)' }}
        >
          <div className="flex items-center gap-2 min-w-0">
            <Shield size={12} style={{ color: opColor(act.operation) }} />
            {act.agent_name && (
              <span
                className="px-1.5 py-0.5 rounded text-xs shrink-0"
                style={{ background: 'rgba(0,210,255,0.1)', color: 'var(--neo-cyan)' }}
              >
                {act.agent_name}
              </span>
            )}
            <span className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>
              {act.operation}
            </span>
            {act.target_type && (
              <span className="text-xs truncate" style={{ color: 'var(--neo-text-muted)' }}>
                on {act.target_type}
                {act.target_id ? ` ${act.target_id.slice(0, 8)}…` : ''}
              </span>
            )}
          </div>
          <span className="text-xs shrink-0 ml-2" style={{ color: 'var(--neo-text-muted)' }}>
            {new Date(act.timestamp).toLocaleString()}
          </span>
        </div>
      ))}
    </div>
  );
}

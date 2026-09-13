import React from 'react';
import { CheckCircle, XCircle, Clock } from 'lucide-react';

const STATUS_CONFIG = {
  success: { icon: CheckCircle, color: 'var(--neo-green)', label: 'Success' },
  partial: { icon: Clock, color: 'var(--neo-yellow)', label: 'Partial' },
  failed: { icon: XCircle, color: '#ef4444', label: 'Failed' },
};

export default function SyncHistory({ history }) {
  if (!history || history.length === 0) {
    return (
      <div className="text-center py-6 text-sm" style={{ color: 'var(--neo-text-muted)' }}>
        No sync history yet
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {history.map((run) => {
        const cfg = STATUS_CONFIG[run.status] || STATUS_CONFIG.failed;
        const Icon = cfg.icon;
        return (
          <div
            key={run.sync_id}
            className="flex items-center justify-between p-3 rounded-lg"
            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)' }}
          >
            <div className="flex items-center gap-3">
              <Icon size={16} style={{ color: cfg.color }} />
              <div>
                <div className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>
                  {cfg.label}
                  <span className="font-normal ml-2" style={{ color: 'var(--neo-text-muted)' }}>
                    +{run.nodes_created} nodes, +{run.edges_created} edges
                  </span>
                </div>
                <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                  {new Date(run.started_at).toLocaleString()}
                  {run.duration_ms > 0 && ` \u00b7 ${(run.duration_ms / 1000).toFixed(1)}s`}
                </div>
              </div>
            </div>
            {run.error_log && (
              <div className="text-xs max-w-xs truncate" style={{ color: '#ef4444' }}>
                {typeof run.error_log === 'string' ? run.error_log : JSON.stringify(run.error_log)}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

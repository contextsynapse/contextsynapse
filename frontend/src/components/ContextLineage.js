import React, { useState, useEffect } from 'react';
import {
  Database, Layers, FileText, Upload, GitMerge,
  ChevronRight, ChevronDown, Clock, Hash,
} from 'lucide-react';
import api from '../lib/api';

const TYPE_CONFIG = {
  graph:   { icon: Database,  color: 'var(--neo-blue)',  label: 'Graph' },
  session: { icon: Layers,    color: '#af52de',          label: 'Session' },
  compose: { icon: GitMerge,  color: 'var(--neo-cyan)',  label: 'Composed' },
  overlay: { icon: FileText,  color: 'var(--neo-green)', label: 'Overlay' },
  file:    { icon: Upload,    color: '#f59e0b',          label: 'File' },
};

function formatTime(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch { return ts; }
}

function LineageEntry({ entry }) {
  const cfg = TYPE_CONFIG[entry.type] || TYPE_CONFIG.overlay;
  const Icon = cfg.icon;

  let description = '';
  let detail = '';
  const nodeCount = entry.nodes_copied || entry.nodes_merged || entry.nodes_created || 0;

  switch (entry.type) {
    case 'graph':
      description = entry.source || 'unknown graph';
      detail = `${nodeCount} nodes`;
      if (entry.node_type_filter) detail += ` (type: ${entry.node_type_filter})`;
      break;
    case 'session':
      description = entry.source_session_name || entry.source_session_id || 'unknown session';
      detail = `${nodeCount} nodes`;
      break;
    case 'compose':
      description = (entry.source_names || []).join(' + ') || `${(entry.sources || []).length} sessions`;
      detail = `${nodeCount} merged`;
      if (entry.duplicates_removed) detail += `, ${entry.duplicates_removed} deduped`;
      break;
    case 'overlay':
      description = entry.label || 'text';
      detail = entry.nodes_created ? `${entry.nodes_created} nodes` : entry.role || '';
      break;
    case 'file':
      description = entry.filename || 'uploaded file';
      detail = entry.chunks ? `${entry.chunks} chunks` : '';
      break;
    default:
      description = entry.type;
  }

  return (
    <div className="flex items-start gap-2 py-1.5">
      <div
        className="w-5 h-5 rounded flex items-center justify-center shrink-0 mt-0.5"
        style={{ background: `${cfg.color}20` }}
      >
        <Icon size={12} style={{ color: cfg.color }} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>
            {cfg.label}:
          </span>
          <span className="text-xs" style={{ color: 'var(--neo-text)' }}>
            {description}
          </span>
          {detail && (
            <span
              className="px-1.5 py-0.5 rounded text-xs"
              style={{ background: `${cfg.color}15`, color: cfg.color }}
            >
              {typeof detail === 'string' ? detail : JSON.stringify(detail)}
            </span>
          )}
        </div>
        {entry.timestamp && (
          <div className="flex items-center gap-1 mt-0.5">
            <Clock size={9} style={{ color: 'var(--neo-text-muted)' }} />
            <span className="text-xs" style={{ color: 'var(--neo-text-muted)', fontSize: '10px' }}>
              {formatTime(entry.timestamp)}
            </span>
          </div>
        )}
        {/* Node type breakdown */}
        {entry.node_types && Object.keys(entry.node_types).length > 0 && (
          <div className="flex items-center gap-1.5 mt-1 flex-wrap">
            {Object.entries(entry.node_types).map(([type, count]) => (
              <span
                key={type}
                className="px-1 py-0.5 rounded text-xs"
                style={{ background: 'var(--neo-surface)', color: 'var(--neo-text-muted)', fontSize: '10px' }}
              >
                {type}: {count}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function ContextLineage({ sessionId }) {
  const [lineage, setLineage] = useState([]);
  const [breadcrumb, setBreadcrumb] = useState('');
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!sessionId) return;
    setLoading(true);
    api.get(`/dashboard/sessions/${sessionId}/context/lineage`)
      .then((res) => {
        setLineage(res.data.lineage || []);
        setBreadcrumb(res.data.breadcrumb || '');
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [sessionId]);

  if (loading) return null;
  if (lineage.length === 0) {
    return (
      <div
        className="flex items-center gap-2 px-3 py-2 rounded-lg"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
      >
        <Hash size={12} style={{ color: 'var(--neo-text-muted)' }} />
        <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
          No context sources yet — seed from a graph, compose, or add text to begin
        </span>
      </div>
    );
  }

  return (
    <div
      className="rounded-lg overflow-hidden"
      style={{ border: '1px solid var(--neo-border)' }}
    >
      {/* Breadcrumb bar — always visible */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left transition hover:opacity-90"
        style={{ background: 'var(--neo-surface)' }}
      >
        {expanded
          ? <ChevronDown size={12} style={{ color: 'var(--neo-text-muted)' }} />
          : <ChevronRight size={12} style={{ color: 'var(--neo-text-muted)' }} />
        }
        <GitMerge size={12} style={{ color: 'var(--neo-cyan)' }} />
        <span
          className="text-xs flex-1 truncate"
          style={{ color: 'var(--neo-text-muted)' }}
        >
          {breadcrumb}
        </span>
        <span
          className="px-1.5 py-0.5 rounded text-xs shrink-0"
          style={{ background: 'rgba(0,210,255,0.1)', color: 'var(--neo-cyan)' }}
        >
          {lineage.length} source{lineage.length !== 1 ? 's' : ''}
        </span>
      </button>

      {/* Expanded tree view */}
      {expanded && (
        <div
          className="px-3 py-2 space-y-0.5"
          style={{ background: 'var(--neo-bg)', borderTop: '1px solid var(--neo-border)' }}
        >
          {lineage.map((entry, i) => (
            <LineageEntry key={i} entry={entry} />
          ))}
        </div>
      )}
    </div>
  );
}

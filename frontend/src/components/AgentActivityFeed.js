/**
 * AgentActivityFeed — shows what each agent wrote to the shared graph.
 *
 * Displays a timeline of Findings, Decisions, Tasks by agent, with color-coded
 * agent badges. Used in Sessions page and Contexts page.
 */
import React, { useState, useCallback } from 'react';
import { Loader2, RefreshCw, User, FileText, CheckCircle, Cpu } from 'lucide-react';
import api from '../lib/api';

const AGENT_COLORS = {
  'chatgpt': '#10a37f',
  'gpt': '#10a37f',
  'claude': '#d97706',
  'claude-desktop': '#d97706',
  'dispatcher': '#8b5cf6',
  'task_worker': '#3b82f6',
  'pipeline': '#6b7280',
};

function getAgentColor(name) {
  const lower = (name || '').toLowerCase();
  for (const [key, color] of Object.entries(AGENT_COLORS)) {
    if (lower.includes(key)) return color;
  }
  return '#6b7280';
}

const LABEL_ICONS = {
  Finding: FileText,
  Insight: Cpu,
  Decision: CheckCircle,
  Task: CheckCircle,
};

export default function AgentActivityFeed({ graphName, sessionId }) {
  const [activity, setActivity] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const loadActivity = useCallback(async () => {
    setLoading(true);
    try {
      // Try graph-based activity
      const res = await api.get(`/dashboard/graphs/${encodeURIComponent(graphName)}/nodes`, {
        params: { label: 'Finding', limit: 50 },
      });
      const findings = (res.data.nodes || []).map(n => ({
        ...n, label: 'Finding',
      }));

      // Also get Decisions and Tasks
      const [decRes, taskRes] = await Promise.all([
        api.get(`/dashboard/graphs/${encodeURIComponent(graphName)}/nodes`, { params: { label: 'Decision', limit: 20 } }).catch(() => ({ data: { nodes: [] } })),
        api.get(`/dashboard/graphs/${encodeURIComponent(graphName)}/nodes`, { params: { label: 'Task', limit: 20 } }).catch(() => ({ data: { nodes: [] } })),
      ]);

      const all = [
        ...findings,
        ...(decRes.data.nodes || []).map(n => ({ ...n, label: 'Decision' })),
        ...(taskRes.data.nodes || []).map(n => ({ ...n, label: 'Task' })),
      ];

      // Categorize: agent-written vs pipeline-extracted
      const agentActivity = all.map(item => {
        const props = item.properties || {};
        const agentName = props._agent_name || props.created_by || '';
        const isPipeline = agentName.startsWith('pipeline') || agentName === 'system'
          || agentName.startsWith('worker-') || (!agentName && props._extraction_method);
        return { ...item, _isPipeline: isPipeline, _agentDisplay: isPipeline ? 'pipeline' : (agentName || 'unknown') };
      }).filter(item => !item._isPipeline);  // Only show real agent activity

      // Sort by created_at
      agentActivity.sort((a, b) => (b.properties?.created_at || '').localeCompare(a.properties?.created_at || ''));
      setActivity(agentActivity);
      setLoaded(true);
    } catch {
      setActivity([]);
      setLoaded(true);
    } finally {
      setLoading(false);
    }
  }, [graphName]);

  if (!loaded) {
    return (
      <div className="flex flex-col items-center justify-center py-8">
        <User size={28} className="mb-3" style={{ color: 'var(--neo-text-dim)' }} />
        <p className="text-xs mb-1" style={{ color: 'var(--neo-text-muted)' }}>
          See what each agent contributed to the shared graph.
        </p>
        <button onClick={loadActivity}
          className="mt-2 px-3 py-1.5 rounded-lg text-xs font-medium"
          style={{ background: 'var(--neo-blue)', color: '#fff' }}>
          {loading ? 'Loading...' : 'Load Activity'}
        </button>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>
          {activity.length} agent action{activity.length !== 1 ? 's' : ''}
        </span>
        <button onClick={loadActivity} className="p-1 rounded" style={{ color: 'var(--neo-text-muted)' }}>
          {loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
        </button>
      </div>

      {activity.length === 0 ? (
        <p className="text-xs py-4 text-center" style={{ color: 'var(--neo-text-muted)' }}>
          No agent activity yet. Agents write Findings, Decisions, and Tasks here.
        </p>
      ) : (
        <div className="space-y-2">
          {activity.map((item, i) => {
            const props = item.properties || {};
            const agent = item._agentDisplay || props._agent_name || props.created_by || 'unknown';
            const color = getAgentColor(agent);
            const Icon = LABEL_ICONS[item.label] || FileText;
            const time = props.created_at ? new Date(props.created_at).toLocaleString() : '';

            return (
              <div key={item.id || i} className="p-2.5 rounded-lg"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', borderLeft: `3px solid ${color}` }}>
                <div className="flex items-center gap-2 mb-1">
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-bold"
                    style={{ background: `${color}15`, color }}>
                    {agent}
                  </span>
                  <span className="flex items-center gap-1 text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
                    <Icon size={10} /> {item.label}
                  </span>
                  {time && <span className="text-[9px] ml-auto" style={{ color: 'var(--neo-text-dim)' }}>{time}</span>}
                </div>
                <p className="text-[11px] font-medium" style={{ color: 'var(--neo-text)' }}>
                  {props.name || props.title || 'Untitled'}
                </p>
                {props.content && (
                  <p className="text-[10px] mt-0.5 line-clamp-2" style={{ color: 'var(--neo-text-muted)' }}>
                    {props.content.slice(0, 200)}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

import React, { useState, useEffect } from 'react';
import { Bot, Loader2, CheckCircle, XCircle, Clock, Zap, BarChart3 } from 'lucide-react';
import api from '../../lib/api';

function fmtTime(isoStr) {
  if (!isoStr) return 'never';
  const d = new Date(isoStr);
  const mo = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getUTCMonth()];
  return `${mo} ${d.getUTCDate()}, ${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')} UTC`;
}

export default function AgentAnalyticsPage() {
  const [agents, setAgents] = useState([]);
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.allSettled([
      api.get('/dashboard/agents'),
      api.get('/dashboard/schedules'),
    ]).then(([agentsRes, runsRes]) => {
      if (agentsRes.status === 'fulfilled') setAgents(agentsRes.value.data.agents || []);
      if (runsRes.status === 'fulfilled') setRuns(runsRes.value.data.schedules || []);
    }).finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      </div>
    );
  }

  // Compute agent stats
  const agentStats = agents.map(a => {
    const stats = a.stats || {};
    const completed = stats.tasks_completed || 0;
    const failed = stats.tasks_failed || 0;
    const total = completed + failed;
    const successRate = total > 0 ? Math.round((completed / total) * 100) : null;

    return {
      ...a,
      tasks_completed: completed,
      tasks_failed: failed,
      total_tasks: total,
      success_rate: successRate,
      total_turns: stats.total_turns || 0,
      total_actions: stats.total_actions || 0,
      last_error: stats.last_error,
    };
  }).sort((a, b) => b.tasks_completed - a.tasks_completed);

  const totalCompleted = agentStats.reduce((s, a) => s + a.tasks_completed, 0);
  const totalFailed = agentStats.reduce((s, a) => s + a.tasks_failed, 0);
  const activeCount = agentStats.filter(a => {
    if (!a.last_seen) return false;
    return (Date.now() - new Date(a.last_seen).getTime()) < 3600000;
  }).length;

  return (
    <div className="max-w-5xl">
      <h1 className="text-xl font-bold mb-1" style={{ color: 'var(--neo-text)' }}>Agent Analytics</h1>
      <p className="text-sm mb-6" style={{ color: 'var(--neo-text-muted)' }}>
        Performance metrics for all registered agents.
      </p>

      {/* Summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
        <div className="p-4 rounded-xl" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
          <Bot size={16} className="mb-1" style={{ color: 'var(--neo-blue)' }} />
          <div className="text-2xl font-bold" style={{ color: 'var(--neo-text)' }}>{agents.length}</div>
          <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Total Agents</div>
        </div>
        <div className="p-4 rounded-xl" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
          <Zap size={16} className="mb-1" style={{ color: '#22c55e' }} />
          <div className="text-2xl font-bold" style={{ color: 'var(--neo-text)' }}>{activeCount}</div>
          <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Active (last hour)</div>
        </div>
        <div className="p-4 rounded-xl" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
          <CheckCircle size={16} className="mb-1" style={{ color: '#22c55e' }} />
          <div className="text-2xl font-bold" style={{ color: 'var(--neo-text)' }}>{totalCompleted}</div>
          <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Tasks Completed</div>
        </div>
        <div className="p-4 rounded-xl" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
          <XCircle size={16} className="mb-1" style={{ color: '#ef4444' }} />
          <div className="text-2xl font-bold" style={{ color: 'var(--neo-text)' }}>{totalFailed}</div>
          <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Tasks Failed</div>
        </div>
      </div>

      {/* Agent performance table */}
      <div className="rounded-xl overflow-hidden" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
        <div className="px-4 py-3 flex items-center gap-2" style={{ borderBottom: '1px solid var(--neo-border)' }}>
          <BarChart3 size={14} style={{ color: 'var(--neo-blue)' }} />
          <span className="text-sm font-semibold" style={{ color: 'var(--neo-text)' }}>Agent Performance</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr style={{ background: 'var(--neo-bg)' }}>
                <th className="text-left px-4 py-2 font-medium" style={{ color: 'var(--neo-text-muted)' }}>Agent</th>
                <th className="text-left px-3 py-2 font-medium" style={{ color: 'var(--neo-text-muted)' }}>Platform</th>
                <th className="text-center px-3 py-2 font-medium" style={{ color: 'var(--neo-text-muted)' }}>Completed</th>
                <th className="text-center px-3 py-2 font-medium" style={{ color: 'var(--neo-text-muted)' }}>Failed</th>
                <th className="text-center px-3 py-2 font-medium" style={{ color: 'var(--neo-text-muted)' }}>Success %</th>
                <th className="text-center px-3 py-2 font-medium" style={{ color: 'var(--neo-text-muted)' }}>Actions</th>
                <th className="text-left px-3 py-2 font-medium" style={{ color: 'var(--neo-text-muted)' }}>Last Seen</th>
                <th className="text-left px-3 py-2 font-medium" style={{ color: 'var(--neo-text-muted)' }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {agentStats.map((a) => {
                const isActive = a.last_seen && (Date.now() - new Date(a.last_seen).getTime()) < 3600000;
                return (
                  <tr key={a.agent_id} style={{ borderBottom: '1px solid var(--neo-border)' }}>
                    <td className="px-4 py-2.5">
                      <div className="font-medium" style={{ color: 'var(--neo-text)' }}>{a.name}</div>
                      <div className="text-[10px] font-mono" style={{ color: 'var(--neo-text-dim)' }}>{a.agent_id?.slice(0, 12)}</div>
                    </td>
                    <td className="px-3 py-2.5">
                      <span className="px-1.5 py-0.5 rounded" style={{ background: 'rgba(0,210,255,0.1)', color: 'var(--neo-cyan)', fontSize: 10 }}>
                        {a.platform || 'app'}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-center font-medium" style={{ color: '#22c55e' }}>{a.tasks_completed}</td>
                    <td className="px-3 py-2.5 text-center font-medium" style={{ color: a.tasks_failed > 0 ? '#ef4444' : 'var(--neo-text-dim)' }}>{a.tasks_failed}</td>
                    <td className="px-3 py-2.5 text-center">
                      {a.success_rate !== null ? (
                        <div className="flex items-center justify-center gap-1">
                          <div className="w-12 h-1.5 rounded-full" style={{ background: 'var(--neo-bg)' }}>
                            <div className="h-full rounded-full" style={{
                              width: `${a.success_rate}%`,
                              background: a.success_rate >= 80 ? '#22c55e' : a.success_rate >= 50 ? '#f59e0b' : '#ef4444',
                            }} />
                          </div>
                          <span style={{ color: 'var(--neo-text)' }}>{a.success_rate}%</span>
                        </div>
                      ) : (
                        <span style={{ color: 'var(--neo-text-dim)' }}>—</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-center" style={{ color: 'var(--neo-text-muted)' }}>{a.total_actions}</td>
                    <td className="px-3 py-2.5" style={{ color: 'var(--neo-text-dim)' }}>{fmtTime(a.last_seen)}</td>
                    <td className="px-3 py-2.5">
                      <span className="flex items-center gap-1">
                        <span className="w-2 h-2 rounded-full" style={{ background: isActive ? '#22c55e' : 'var(--neo-text-dim)' }} />
                        <span style={{ color: isActive ? '#22c55e' : 'var(--neo-text-dim)' }}>{isActive ? 'active' : 'idle'}</span>
                      </span>
                    </td>
                  </tr>
                );
              })}
              {agentStats.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center" style={{ color: 'var(--neo-text-muted)' }}>
                    No agents registered yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

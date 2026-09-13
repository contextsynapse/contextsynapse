import { useState, useEffect } from 'react';
import { Activity, Search, AlertTriangle, RefreshCw, GitBranch, Users, MessageSquare, Award, Database } from 'lucide-react';
import api from '../../lib/api';

export default function CognitionPage() {
  const [namespace, setNamespace] = useState('default');
  const [events, setEvents] = useState([]);
  const [eventFilter, setEventFilter] = useState('');
  const [loading, setLoading] = useState(false);

  // Node inspector
  const [inspectId, setInspectId] = useState('');
  const [lineage, setLineage] = useState(null);
  const [consumers, setConsumers] = useState(null);
  const [feedback, setFeedback] = useState(null);

  // Report Card
  const [reportCard, setReportCard] = useState(null);

  // Context Summary
  const [contextSummary, setContextSummary] = useState(null);

  // Invalidation
  const [invalidateId, setInvalidateId] = useState('');
  const [invalidateReason, setInvalidateReason] = useState('');
  const [invalidateResult, setInvalidateResult] = useState(null);

  // Review Queue
  const [reviewQueue, setReviewQueue] = useState([]);

  const fetchReportCard = async () => {
    try {
      const res = await api.get(`/shield/report-card`, { params: { namespace } });
      setReportCard(res.data);
    } catch {}
  };

  const fetchContextSummary = async () => {
    try {
      const res = await api.get(`/shield/context/${namespace}/summary`);
      setContextSummary(res.data);
    } catch {}
  };

  const fetchReviewQueue = async () => {
    try {
      const res = await api.get('/shield/review-queue');
      setReviewQueue(res.data.items || []);
    } catch {}
  };

  const fetchEvents = async () => {
    setLoading(true);
    try {
      const params = { limit: 50 };
      if (eventFilter) params.event_type = eventFilter;
      const res = await api.get(`/cognition/${namespace}/events`, { params });
      setEvents(res.data.events || []);
    } catch {} finally { setLoading(false); }
  };

  useEffect(() => { fetchEvents(); fetchReportCard(); fetchContextSummary(); fetchReviewQueue(); }, [namespace, eventFilter]); // eslint-disable-line react-hooks/exhaustive-deps

  const inspectNode = async () => {
    if (!inspectId.trim()) return;
    try {
      const [lin, con, fb] = await Promise.all([
        api.get(`/cognition/${namespace}/node/${inspectId}/lineage`),
        api.get(`/cognition/${namespace}/node/${inspectId}/consumers`),
        api.get(`/cognition/${namespace}/node/${inspectId}/feedback`),
      ]);
      setLineage(lin.data);
      setConsumers(con.data);
      setFeedback(fb.data);
    } catch {}
  };

  const handleInvalidate = async () => {
    if (!invalidateId.trim() || !invalidateReason.trim()) return;
    if (!window.confirm(`Invalidate "${invalidateId}" and cascade? This cannot be undone.`)) return;
    try {
      const res = await api.post(`/cognition/${namespace}/node/${invalidateId}/invalidate`, {
        reason: invalidateReason,
        agent_id: 'dashboard_user',
      });
      setInvalidateResult(res.data);
      fetchEvents(); // refresh
    } catch {}
  };

  const eventTypeColors = {
    read: 'var(--neo-blue)',
    derive: 'var(--neo-green)',
    feedback: '#f59e0b',
    invalidate: '#ef4444',
    write: '#8b5cf6',
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Activity size={20} style={{ color: 'var(--neo-green)' }} />
          <h1 className="text-lg font-bold" style={{ color: 'var(--neo-text)' }}>Cognitive Reliability</h1>
        </div>
        <div className="flex items-center gap-2">
          <input
            value={namespace}
            onChange={(e) => setNamespace(e.target.value)}
            placeholder="namespace"
            className="px-2 py-1 rounded text-xs"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)', width: 120 }}
          />
          <button onClick={fetchEvents} className="p-1.5 rounded hover:opacity-80" style={{ color: 'var(--neo-text-muted)' }}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Report Card */}
      {reportCard && (
        <div className="rounded-lg p-4" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
          <h2 className="text-sm font-semibold flex items-center gap-1.5 mb-3" style={{ color: 'var(--neo-text)' }}>
            <Award size={14} /> Context Report Card
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {/* Quality Grade */}
            <div className="p-3 rounded-lg text-center" style={{ background: 'var(--neo-bg)' }}>
              <div className="text-3xl font-bold" style={{
                color: {'A': '#22c55e', 'B': 'var(--neo-blue)', 'C': '#f59e0b', 'D': '#f97316', 'F': '#ef4444'}[reportCard.quality?.grade] || 'var(--neo-text)'
              }}>
                {reportCard.quality?.grade || '\u2014'}
              </div>
              <div className="text-[10px] mt-1" style={{ color: 'var(--neo-text-muted)' }}>
                Quality ({Math.round((reportCard.quality?.ratio || 0) * 100)}% useful)
              </div>
            </div>

            {/* Activity */}
            <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg)' }}>
              <div className="text-lg font-bold" style={{ color: 'var(--neo-text)' }}>
                {reportCard.activity?.total_events || 0}
              </div>
              <div className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>Events</div>
              <div className="flex gap-2 mt-1 text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                <span>{reportCard.activity?.reads || 0} reads</span>
                <span>{reportCard.activity?.writes || 0} writes</span>
              </div>
            </div>

            {/* Risk */}
            <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg)' }}>
              <div className="text-lg font-bold" style={{
                color: {'low': '#22c55e', 'medium': '#f59e0b', 'high': '#ef4444'}[reportCard.risk?.level] || 'var(--neo-text)'
              }}>
                {(reportCard.risk?.level || 'unknown').toUpperCase()}
              </div>
              <div className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>Risk Level</div>
              <div className="text-[10px] mt-1" style={{ color: 'var(--neo-text-muted)' }}>
                {Math.round((reportCard.risk?.invalidation_rate || 0) * 100)}% invalidation rate
              </div>
            </div>

            {/* Agents */}
            <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg)' }}>
              <div className="text-lg font-bold" style={{ color: 'var(--neo-text)' }}>
                {reportCard.agents?.total || 0}
              </div>
              <div className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>Agents</div>
              <div className="flex gap-1 mt-1 flex-wrap">
                {Object.entries(reportCard.agents?.trust_distribution || {}).map(([level, count]) => (
                  count > 0 && <span key={level} className="px-1.5 py-0.5 rounded text-[9px] font-medium" style={{
                    background: {'trusted': '#22c55e', 'verified': 'var(--neo-blue)', 'provisional': '#f59e0b', 'untrusted': '#ef4444'}[level],
                    color: '#fff'
                  }}>{count} {level}</span>
                ))}
              </div>
            </div>
          </div>

          {/* Leaderboard */}
          {reportCard.agents?.leaderboard?.length > 0 && (
            <div className="mt-3">
              <div className="text-[10px] font-semibold uppercase mb-1" style={{ color: 'var(--neo-text-muted)' }}>
                Agent Leaderboard
              </div>
              <div className="space-y-1">
                {reportCard.agents.leaderboard.slice(0, 5).map((a, i) => (
                  <div key={i} className="flex items-center gap-2 text-[11px] px-2 py-1 rounded" style={{ background: 'var(--neo-bg)' }}>
                    <span className="font-mono" style={{ color: 'var(--neo-text)' }}>{a.agent_id?.slice(0, 12)}</span>
                    <span className="px-1 rounded text-[9px] font-medium" style={{
                      background: {'trusted': '#22c55e', 'verified': 'var(--neo-blue)', 'provisional': '#f59e0b', 'untrusted': '#ef4444'}[a.trust_level],
                      color: '#fff'
                    }}>{a.trust_level}</span>
                    <span style={{ color: 'var(--neo-text-muted)' }}>{a.reads}r / {a.writes}w</span>
                    {a.quality !== null && <span style={{ color: a.quality >= 0.8 ? '#22c55e' : '#f59e0b' }}>{Math.round(a.quality * 100)}% quality</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Context Summary */}
      {contextSummary && (
        <div className="rounded-lg p-4" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
          <h2 className="text-sm font-semibold flex items-center gap-1.5 mb-3" style={{ color: 'var(--neo-text)' }}>
            <Database size={14} /> Context Health
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            {/* Health Grade */}
            <div className="p-3 rounded-lg text-center" style={{ background: 'var(--neo-bg)' }}>
              <div className="text-3xl font-bold" style={{
                color: {'A': '#22c55e', 'B': 'var(--neo-blue)', 'C': '#f59e0b', 'D': '#f97316', 'F': '#ef4444'}[contextSummary.health?.grade] || 'var(--neo-text)'
              }}>
                {contextSummary.health?.grade || '—'}
              </div>
              <div className="text-[10px] mt-1" style={{ color: 'var(--neo-text-muted)' }}>
                Health ({contextSummary.health?.score || 0}/100)
              </div>
            </div>

            {/* Size */}
            <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg)' }}>
              <div className="text-lg font-bold" style={{ color: 'var(--neo-text)' }}>
                {contextSummary.size?.total_nodes || 0}
              </div>
              <div className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>Nodes</div>
              <div className="text-[10px] mt-1" style={{ color: 'var(--neo-text-muted)' }}>
                {contextSummary.size?.total_edges || 0} edges
              </div>
            </div>

            {/* Freshness */}
            <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg)' }}>
              <div className="text-lg font-bold" style={{
                color: (contextSummary.freshness?.fresh_percent || 0) >= 50 ? '#22c55e' : '#f59e0b'
              }}>
                {contextSummary.freshness?.fresh_percent || 0}%
              </div>
              <div className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>Fresh (7d)</div>
            </div>

            {/* Validation */}
            <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg)' }}>
              <div className="text-lg font-bold" style={{ color: 'var(--neo-text)' }}>
                {contextSummary.quality?.validation_rate || 0}%
              </div>
              <div className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>Validated</div>
              <div className="text-[10px] mt-1" style={{ color: '#ef4444' }}>
                {contextSummary.quality?.invalidation_rate || 0}% invalidated
              </div>
            </div>

            {/* Sources */}
            <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg)' }}>
              <div className="text-lg font-bold" style={{ color: 'var(--neo-text)' }}>
                {contextSummary.sources?.unique_sources || 0}
              </div>
              <div className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>Sources</div>
              <div className="text-[10px] mt-1" style={{ color: 'var(--neo-text-muted)' }}>
                {contextSummary.structure?.avg_edges_per_node || 0} edges/node
              </div>
            </div>
          </div>

          {/* Node type distribution */}
          {contextSummary.size?.label_distribution && Object.keys(contextSummary.size.label_distribution).length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {Object.entries(contextSummary.size.label_distribution).slice(0, 10).map(([label, count]) => (
                <span key={label} className="px-2 py-0.5 rounded-full text-[10px] font-medium"
                  style={{ background: 'var(--neo-bg)', color: 'var(--neo-text)', border: '1px solid var(--neo-border)' }}>
                  {label}: {count}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Section 1: Event Stream */}
      <div className="rounded-lg p-4" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold flex items-center gap-1.5" style={{ color: 'var(--neo-text)' }}>
            <Activity size={14} /> Event Stream
          </h2>
          <select value={eventFilter} onChange={(e) => setEventFilter(e.target.value)}
            className="text-xs px-2 py-1 rounded"
            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}>
            <option value="">All events</option>
            <option value="read">Read</option>
            <option value="derive">Derive</option>
            <option value="feedback">Feedback</option>
            <option value="invalidate">Invalidate</option>
          </select>
        </div>
        <div className="space-y-1 max-h-64 overflow-y-auto">
          {events.length === 0 && (
            <p className="text-xs py-4 text-center" style={{ color: 'var(--neo-text-muted)' }}>No events yet</p>
          )}
          {events.map((ev, i) => (
            <div key={i} className="flex items-center gap-2 px-2 py-1.5 rounded text-xs"
              style={{ background: 'var(--neo-bg)' }}>
              <span className="px-1.5 py-0.5 rounded font-medium text-[10px]"
                style={{ background: eventTypeColors[ev.event_type] || 'var(--neo-border)', color: '#fff' }}>
                {ev.event_type}
              </span>
              <span style={{ color: 'var(--neo-text-muted)' }}>{ev.agent_id}</span>
              <span style={{ color: 'var(--neo-text)' }} className="truncate flex-1">
                {(ev.node_ids || []).slice(0, 2).join(', ')}
                {(ev.node_ids || []).length > 2 && ` +${ev.node_ids.length - 2}`}
              </span>
              <span style={{ color: 'var(--neo-text-muted)' }} className="text-[10px] whitespace-nowrap">
                {ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString() : ''}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Section 2: Node Inspector */}
      <div className="rounded-lg p-4" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
        <h2 className="text-sm font-semibold flex items-center gap-1.5 mb-3" style={{ color: 'var(--neo-text)' }}>
          <Search size={14} /> Node Inspector
        </h2>
        <div className="flex gap-2 mb-3">
          <input
            value={inspectId}
            onChange={(e) => setInspectId(e.target.value)}
            placeholder="Enter node ID..."
            className="flex-1 px-2 py-1.5 rounded text-xs"
            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            onKeyDown={(e) => e.key === 'Enter' && inspectNode()}
          />
          <button onClick={inspectNode}
            className="px-3 py-1.5 rounded text-xs font-medium"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}>
            Inspect
          </button>
        </div>

        {lineage && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {/* Lineage */}
            <div className="p-2 rounded" style={{ background: 'var(--neo-bg)' }}>
              <h3 className="text-[10px] font-semibold uppercase mb-1.5 flex items-center gap-1" style={{ color: 'var(--neo-text-muted)' }}>
                <GitBranch size={10} /> Lineage
              </h3>
              {lineage.sources.length > 0 ? (
                <div className="space-y-0.5">
                  {lineage.sources.map((s, i) => (
                    <div key={i} className="text-[11px] truncate" style={{ color: 'var(--neo-text)' }}>← {s}</div>
                  ))}
                </div>
              ) : <p className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>No sources</p>}
              {lineage.derived_to.length > 0 && (
                <div className="mt-2 space-y-0.5">
                  {lineage.derived_to.map((d, i) => (
                    <div key={i} className="text-[11px] truncate" style={{ color: 'var(--neo-green)' }}>→ {d}</div>
                  ))}
                </div>
              )}
            </div>

            {/* Consumers */}
            <div className="p-2 rounded" style={{ background: 'var(--neo-bg)' }}>
              <h3 className="text-[10px] font-semibold uppercase mb-1.5 flex items-center gap-1" style={{ color: 'var(--neo-text-muted)' }}>
                <Users size={10} /> Consumers
              </h3>
              {consumers && consumers.consumers.length > 0 ? (
                <div className="space-y-0.5">
                  {consumers.consumers.map((c, i) => (
                    <div key={i} className="text-[11px]" style={{ color: 'var(--neo-text)' }}>{c}</div>
                  ))}
                </div>
              ) : <p className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>No consumers</p>}
            </div>

            {/* Feedback */}
            <div className="p-2 rounded" style={{ background: 'var(--neo-bg)' }}>
              <h3 className="text-[10px] font-semibold uppercase mb-1.5 flex items-center gap-1" style={{ color: 'var(--neo-text-muted)' }}>
                <MessageSquare size={10} /> Feedback
              </h3>
              {feedback && feedback.feedback.length > 0 ? (
                <div className="space-y-0.5">
                  {feedback.feedback.map((f, i) => (
                    <div key={i} className="text-[11px] flex items-center gap-1">
                      <span className="px-1 rounded text-[9px] font-medium"
                        style={{ background: f.signal === 'useful' ? 'var(--neo-green)' : '#ef4444', color: '#fff' }}>
                        {f.signal}
                      </span>
                      <span style={{ color: 'var(--neo-text-muted)' }}>{f.agent_id}</span>
                    </div>
                  ))}
                </div>
              ) : <p className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>No feedback</p>}
            </div>
          </div>
        )}
      </div>

      {/* Section 3: Invalidation */}
      <div className="rounded-lg p-4" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
        <h2 className="text-sm font-semibold flex items-center gap-1.5 mb-3" style={{ color: 'var(--neo-text)' }}>
          <AlertTriangle size={14} /> Invalidation
        </h2>
        <div className="flex gap-2 mb-2">
          <input
            value={invalidateId}
            onChange={(e) => setInvalidateId(e.target.value)}
            placeholder="Node ID to invalidate..."
            className="flex-1 px-2 py-1.5 rounded text-xs"
            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          />
          <input
            value={invalidateReason}
            onChange={(e) => setInvalidateReason(e.target.value)}
            placeholder="Reason..."
            className="flex-1 px-2 py-1.5 rounded text-xs"
            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          />
          <button onClick={handleInvalidate}
            className="px-3 py-1.5 rounded text-xs font-medium"
            style={{ background: '#ef4444', color: '#fff' }}>
            Invalidate
          </button>
        </div>
        {invalidateResult && (
          <div className="p-2 rounded text-xs" style={{ background: 'var(--neo-bg)' }}>
            <span className="font-medium" style={{ color: '#ef4444' }}>
              Invalidated {invalidateResult.count} node(s):
            </span>
            <span style={{ color: 'var(--neo-text)' }} className="ml-1">
              {(invalidateResult.invalidated || []).slice(0, 5).join(', ')}
              {(invalidateResult.invalidated || []).length > 5 && ` +${invalidateResult.invalidated.length - 5} more`}
            </span>
          </div>
        )}
      </div>

      {/* Review Queue */}
      <div className="rounded-lg p-4" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold flex items-center gap-1.5" style={{ color: 'var(--neo-text)' }}>
            <AlertTriangle size={14} /> Review Queue
          </h2>
          <span className="text-[10px] px-2 py-0.5 rounded-full font-medium"
            style={{ background: reviewQueue.length > 0 ? '#f59e0b' : 'var(--neo-bg)', color: reviewQueue.length > 0 ? '#fff' : 'var(--neo-text-muted)' }}>
            {reviewQueue.length} pending
          </span>
        </div>
        {reviewQueue.length === 0 ? (
          <p className="text-xs py-3 text-center" style={{ color: 'var(--neo-text-muted)' }}>No items pending review</p>
        ) : (
          <div className="space-y-2 max-h-64 overflow-y-auto">
            {reviewQueue.map((item, i) => (
              <div key={i} className="flex items-start gap-2 p-2 rounded" style={{ background: 'var(--neo-bg)' }}>
                <div className="flex-1">
                  <div className="text-[11px]" style={{ color: 'var(--neo-text)' }}>
                    {item.content_preview || item.node_id?.slice(0, 20)}
                  </div>
                  <div className="flex items-center gap-2 mt-1 text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                    <span>Score: {(item.score || 0).toFixed(2)}</span>
                    <span>by {item.submitted_by || '?'}</span>
                    {item.session_name && <span>session: {item.session_name}</span>}
                  </div>
                </div>
                <div className="flex gap-1">
                  <button
                    onClick={async () => {
                      try {
                        await api.post(`/shield/review/${item.node_id}/approve`, { namespace: item.namespace });
                        fetchReviewQueue();
                      } catch {}
                    }}
                    className="text-[10px] px-2 py-1 rounded font-medium"
                    style={{ background: 'var(--neo-green)', color: '#fff' }}
                  >
                    Approve
                  </button>
                  <button
                    onClick={async () => {
                      try {
                        await api.post(`/shield/review/${item.node_id}/reject`, { namespace: item.namespace });
                        fetchReviewQueue();
                      } catch {}
                    }}
                    className="text-[10px] px-2 py-1 rounded"
                    style={{ color: '#ef4444', border: '1px solid #ef4444' }}
                  >
                    Reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

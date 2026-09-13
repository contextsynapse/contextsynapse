import React, { useState, useEffect, useCallback } from 'react';
import { Radar, Plus, Play, Pause, RefreshCw, AlertTriangle, TrendingUp, TrendingDown,
         Activity, Globe, Zap, Shield, Clock, ArrowUpRight, ArrowDownRight, Minus,
         Eye, Search, Bell, Loader2, ChevronDown, ChevronRight, Trash2, Settings } from 'lucide-react';
import api from '../../lib/api';
import { toast } from 'react-hot-toast';
// FusionGraphExplorer and FusionTimeline are provided by the finance vertical plugin.
// Lazy-load them if available, otherwise render nothing.
const FusionGraphExplorer = ({ contexts, fusionContext }) => null;
const FusionTimeline = ({ contexts, focusEntity }) => null;

// ── Tab Components ──

function TrackingTab({ plans, onRefresh, loading }) {
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    entity: '', template: 'indian_listed_company',
    full_name: '', bse_code: '', ticker: '', topics: '',
  });

  const handleTrack = async () => {
    if (!form.entity) return toast.error('Entity name required');
    try {
      const extra = {};
      if (form.full_name) extra.full_name = form.full_name;
      if (form.bse_code) extra.bse_code = form.bse_code;
      if (form.ticker) extra.ticker = form.ticker;
      if (form.topics) extra.topics = form.topics.split(',').map(t => t.trim());

      await api.post('/intelligence/track', { entity: form.entity, template: form.template, extra });
      toast.success(`Now tracking ${form.entity}`);
      setShowForm(false);
      setForm({ entity: '', template: 'indian_listed_company', full_name: '', bse_code: '', ticker: '', topics: '' });
      onRefresh();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to track'); }
  };

  const handleRun = async (name) => {
    try {
      toast.loading(`Running ${name}...`, { id: 'run' });
      const res = await api.post(`/intelligence/plans/${encodeURIComponent(name)}/run`);
      toast.success(`${name}: ${res.data.total_ingested || 0} ingested, ${res.data.total_deduped || 0} deduped`, { id: 'run' });
      onRefresh();
    } catch (e) { toast.error('Run failed', { id: 'run' }); }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold" style={{ color: 'var(--neo-text)' }}>Tracked Entities</h2>
        <button onClick={() => setShowForm(!showForm)} className="flex items-center gap-1 px-3 py-1.5 rounded text-xs font-medium"
          style={{ background: 'var(--neo-blue)', color: '#fff' }}>
          <Plus size={14} /> Track Entity
        </button>
      </div>

      {showForm && (
        <div className="mb-4 p-4 rounded-lg border" style={{ background: 'var(--neo-bg-secondary)', borderColor: 'var(--neo-border)' }}>
          <div className="grid grid-cols-2 gap-3 mb-3">
            <input placeholder="Entity name (e.g., TCS)" value={form.entity}
              onChange={e => setForm({...form, entity: e.target.value})}
              className="px-3 py-2 rounded text-sm" style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
            <select value={form.template} onChange={e => setForm({...form, template: e.target.value})}
              className="px-3 py-2 rounded text-sm" style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}>
              <option value="indian_listed_company">Indian Listed Company</option>
              <option value="us_listed_company">US Listed Company</option>
              <option value="generic_topic">Generic Topic</option>
            </select>
            <input placeholder="Full name (optional)" value={form.full_name}
              onChange={e => setForm({...form, full_name: e.target.value})}
              className="px-3 py-2 rounded text-sm" style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
            <input placeholder="BSE code / Ticker (optional)" value={form.bse_code || form.ticker}
              onChange={e => setForm({...form, bse_code: e.target.value, ticker: e.target.value})}
              className="px-3 py-2 rounded text-sm" style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
            <input placeholder="Topics (comma-separated)" value={form.topics} className="col-span-2 px-3 py-2 rounded text-sm"
              onChange={e => setForm({...form, topics: e.target.value})}
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
          </div>
          <div className="flex gap-2">
            <button onClick={handleTrack} className="px-4 py-1.5 rounded text-xs font-medium" style={{ background: 'var(--neo-blue)', color: '#fff' }}>Start Tracking</button>
            <button onClick={() => setShowForm(false)} className="px-4 py-1.5 rounded text-xs" style={{ color: 'var(--neo-text-muted)' }}>Cancel</button>
          </div>
        </div>
      )}

      <div className="grid gap-3">
        {(plans || []).map(plan => (
          <div key={plan.name} className="p-4 rounded-lg border" style={{ background: 'var(--neo-bg-secondary)', borderColor: 'var(--neo-border)' }}>
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <Globe size={16} style={{ color: 'var(--neo-blue)' }} />
                <span className="font-semibold text-sm" style={{ color: 'var(--neo-text)' }}>{plan.name}</span>
                {plan.tags?.map(tag => (
                  <span key={tag} className="px-1.5 py-0.5 rounded text-[10px]" style={{ background: 'var(--neo-blue-bg)', color: 'var(--neo-blue)' }}>{tag}</span>
                ))}
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>{plan.pipeline_count} pipelines</span>
                <button onClick={() => handleRun(plan.name)} className="p-1 rounded hover:opacity-80" style={{ color: 'var(--neo-green)' }}><Play size={14} /></button>
              </div>
            </div>
            {plan.description && <p className="text-xs mb-2" style={{ color: 'var(--neo-text-muted)' }}>{plan.description}</p>}
            {plan.status?.pipelines && (
              <div className="flex gap-2 flex-wrap">
                {Object.entries(plan.status.pipelines).map(([name, status]) => (
                  <span key={name} className="text-[10px] px-2 py-0.5 rounded" style={{
                    background: status.last_result === 'success' ? 'var(--neo-green-bg)' : status.last_result === 'error' ? 'var(--neo-red-bg)' : 'var(--neo-bg)',
                    color: status.last_result === 'success' ? 'var(--neo-green)' : status.last_result === 'error' ? 'var(--neo-red)' : 'var(--neo-text-muted)',
                  }}>{name}: {status.items_ingested || 0} ingested</span>
                ))}
              </div>
            )}
          </div>
        ))}
        {(!plans || plans.length === 0) && !loading && (
          <p className="text-center py-8 text-sm" style={{ color: 'var(--neo-text-muted)' }}>No entities tracked yet. Click "Track Entity" to start.</p>
        )}
      </div>
    </div>
  );
}

function SignalsTab({ signals }) {
  return (
    <div>
      <h2 className="text-lg font-semibold mb-4" style={{ color: 'var(--neo-text)' }}>Signals & Reactions</h2>

      {(signals?.active_escalations || []).length > 0 && (
        <div className="mb-4">
          <h3 className="text-xs font-semibold uppercase mb-2" style={{ color: 'var(--neo-red)' }}>Active Escalations</h3>
          {signals.active_escalations.map((esc, i) => (
            <div key={i} className="p-3 rounded-lg border mb-2" style={{ background: 'var(--neo-red-bg)', borderColor: 'var(--neo-red)' }}>
              <div className="flex items-center gap-2">
                <Zap size={14} style={{ color: 'var(--neo-red)' }} />
                <span className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>{esc.entity_name} — {esc.signal_type}</span>
                <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>deescalates {new Date(esc.deescalate_at).toLocaleTimeString()}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      <h3 className="text-xs font-semibold uppercase mb-2" style={{ color: 'var(--neo-text-muted)' }}>Recent Reactions</h3>
      <div className="space-y-2">
        {(signals?.reaction_log || []).slice(0, 20).map((reaction, i) => (
          <div key={i} className="flex items-center gap-3 p-2 rounded text-xs" style={{ background: 'var(--neo-bg-secondary)' }}>
            <span className="px-1.5 py-0.5 rounded font-medium" style={{
              background: reaction.action === 'alert' ? 'var(--neo-red-bg)' : reaction.action === 'accelerate' ? 'var(--neo-yellow-bg)' : 'var(--neo-blue-bg)',
              color: reaction.action === 'alert' ? 'var(--neo-red)' : reaction.action === 'accelerate' ? 'var(--neo-yellow)' : 'var(--neo-blue)',
            }}>{reaction.action}</span>
            <span style={{ color: 'var(--neo-text)' }}>{reaction.entity_name}</span>
            <span style={{ color: 'var(--neo-text-muted)' }}>{reaction.context_name}</span>
            <span className="ml-auto" style={{ color: 'var(--neo-text-muted)' }}>{new Date(reaction.timestamp).toLocaleTimeString()}</span>
          </div>
        ))}
        {(!signals?.reaction_log || signals.reaction_log.length === 0) && (
          <p className="text-center py-4 text-xs" style={{ color: 'var(--neo-text-muted)' }}>No signals fired yet. Signals trigger when sentiment reverses, thresholds breach, or breaking news detected.</p>
        )}
      </div>
    </div>
  );
}

function SentimentTab() {
  const [entity, setEntity] = useState('');
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [comparison, setComparison] = useState(null);
  const [compareEntities, setCompareEntities] = useState('');
  const [loading, setLoading] = useState(false);

  const fetchSentiment = async () => {
    if (!entity) return;
    setLoading(true);
    try {
      const res = await api.get(`/intelligence/timeseries/sentiment/${encodeURIComponent(entity)}?days=${days}`);
      setData(res.data);
    } catch (e) { toast.error('Failed to fetch sentiment'); }
    setLoading(false);
  };

  const fetchComparison = async () => {
    if (!compareEntities) return;
    setLoading(true);
    try {
      const res = await api.get(`/intelligence/timeseries/compare?entities=${encodeURIComponent(compareEntities)}&days=${days}`);
      setComparison(res.data);
    } catch (e) { toast.error('Failed to compare'); }
    setLoading(false);
  };

  return (
    <div>
      <h2 className="text-lg font-semibold mb-4" style={{ color: 'var(--neo-text)' }}>Sentiment Analysis</h2>

      <div className="grid grid-cols-2 gap-4 mb-6">
        <div className="p-4 rounded-lg border" style={{ background: 'var(--neo-bg-secondary)', borderColor: 'var(--neo-border)' }}>
          <h3 className="text-xs font-semibold uppercase mb-3" style={{ color: 'var(--neo-text-muted)' }}>Entity Sentiment</h3>
          <div className="flex gap-2 mb-3">
            <input placeholder="Entity name (e.g., TCS)" value={entity} onChange={e => setEntity(e.target.value)}
              className="flex-1 px-3 py-2 rounded text-sm" style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
            <select value={days} onChange={e => setDays(Number(e.target.value))} className="px-2 py-2 rounded text-sm"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}>
              <option value={7}>7 days</option><option value={30}>30 days</option><option value={90}>90 days</option>
            </select>
            <button onClick={fetchSentiment} disabled={loading} className="px-3 py-2 rounded text-xs" style={{ background: 'var(--neo-blue)', color: '#fff' }}>
              {loading ? <Loader2 size={12} className="animate-spin" /> : <Search size={12} />}
            </button>
          </div>
          {data?.data?.map((d, i) => (
            <div key={i} className="flex items-center gap-2 py-1 text-xs">
              <span className="w-20" style={{ color: 'var(--neo-text-muted)' }}>{d.date}</span>
              <span style={{ color: 'var(--neo-green)' }}>+{d.positive}</span>
              <span style={{ color: 'var(--neo-red)' }}>-{d.negative}</span>
              <span style={{ color: 'var(--neo-text-muted)' }}>{d.neutral}n</span>
              <span className="ml-auto font-medium" style={{ color: d.net_sentiment > 0 ? 'var(--neo-green)' : d.net_sentiment < 0 ? 'var(--neo-red)' : 'var(--neo-text-muted)' }}>
                {d.net_sentiment > 0 ? '+' : ''}{d.net_sentiment}
              </span>
            </div>
          ))}
        </div>

        <div className="p-4 rounded-lg border" style={{ background: 'var(--neo-bg-secondary)', borderColor: 'var(--neo-border)' }}>
          <h3 className="text-xs font-semibold uppercase mb-3" style={{ color: 'var(--neo-text-muted)' }}>Compare Entities</h3>
          <div className="flex gap-2 mb-3">
            <input placeholder="TCS, Infosys, HDFC Bank" value={compareEntities} onChange={e => setCompareEntities(e.target.value)}
              className="flex-1 px-3 py-2 rounded text-sm" style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
            <button onClick={fetchComparison} disabled={loading} className="px-3 py-2 rounded text-xs" style={{ background: 'var(--neo-blue)', color: '#fff' }}>Go</button>
          </div>
          {comparison && Object.entries(comparison).map(([name, stats]) => (
            <div key={name} className="flex items-center justify-between py-2 border-b" style={{ borderColor: 'var(--neo-border)' }}>
              <span className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>{name}</span>
              <div className="flex items-center gap-3 text-xs">
                <span style={{ color: 'var(--neo-green)' }}>+{stats.positive}</span>
                <span style={{ color: 'var(--neo-red)' }}>-{stats.negative}</span>
                <span className="font-semibold" style={{ color: stats.net_sentiment > 0 ? 'var(--neo-green)' : stats.net_sentiment < 0 ? 'var(--neo-red)' : 'var(--neo-text-muted)' }}>
                  {stats.net_sentiment > 0 ? <ArrowUpRight size={12} /> : stats.net_sentiment < 0 ? <ArrowDownRight size={12} /> : <Minus size={12} />}
                  {stats.net_sentiment}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function WatchdogTab({ watchdog, onRefresh }) {
  const handleStart = async () => {
    try {
      await api.post('/intelligence/watchdog/start?interval=60');
      toast.success('Watchdog started');
      onRefresh();
    } catch (e) { toast.error('Failed to start watchdog'); }
  };

  const handleStop = async () => {
    try {
      await api.post('/intelligence/watchdog/stop');
      toast.success('Watchdog stopped');
      onRefresh();
    } catch (e) { toast.error('Failed to stop watchdog'); }
  };

  const handleCheck = async () => {
    try {
      toast.loading('Checking...', { id: 'check' });
      const res = await api.post('/intelligence/watchdog/check');
      const data = res.data;
      if (data.alerts?.length > 0) {
        toast.error(`${data.alerts.length} breaking alerts detected!`, { id: 'check' });
      } else {
        toast.success(`Checked ${data.sources_checked} sources — no alerts`, { id: 'check' });
      }
      onRefresh();
    } catch (e) { toast.error('Check failed', { id: 'check' }); }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold" style={{ color: 'var(--neo-text)' }}>Breaking News Watchdog</h2>
        <div className="flex gap-2">
          <button onClick={handleCheck} className="px-3 py-1.5 rounded text-xs" style={{ background: 'var(--neo-blue)', color: '#fff' }}>Check Now</button>
          {watchdog?.running ? (
            <button onClick={handleStop} className="flex items-center gap-1 px-3 py-1.5 rounded text-xs" style={{ background: 'var(--neo-red)', color: '#fff' }}><Pause size={12} /> Stop</button>
          ) : (
            <button onClick={handleStart} className="flex items-center gap-1 px-3 py-1.5 rounded text-xs" style={{ background: 'var(--neo-green)', color: '#fff' }}><Play size={12} /> Start</button>
          )}
        </div>
      </div>

      <div className="flex items-center gap-2 mb-4">
        <div className={`w-2 h-2 rounded-full ${watchdog?.running ? 'bg-green-400 animate-pulse' : 'bg-gray-400'}`} />
        <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>{watchdog?.running ? 'Running (polling every 60s)' : 'Stopped'}</span>
      </div>

      <h3 className="text-xs font-semibold uppercase mb-2" style={{ color: 'var(--neo-text-muted)' }}>Sources</h3>
      <div className="space-y-1 mb-4">
        {(watchdog?.sources || []).map((src, i) => (
          <div key={i} className="flex items-center gap-2 text-xs p-2 rounded" style={{ background: 'var(--neo-bg-secondary)' }}>
            <Globe size={12} style={{ color: 'var(--neo-blue)' }} />
            <span style={{ color: 'var(--neo-text)' }}>{src.name}</span>
            <span style={{ color: 'var(--neo-text-muted)' }}>{src.url}</span>
          </div>
        ))}
      </div>

      <h3 className="text-xs font-semibold uppercase mb-2" style={{ color: 'var(--neo-red)' }}>Recent Alerts</h3>
      <div className="space-y-2">
        {(watchdog?.recent_alerts || []).slice(0, 10).map((alert, i) => (
          <div key={i} className="p-3 rounded-lg border" style={{
            background: alert.impact_level === 'critical' ? 'var(--neo-red-bg)' : 'var(--neo-yellow-bg)',
            borderColor: alert.impact_level === 'critical' ? 'var(--neo-red)' : 'var(--neo-yellow)',
          }}>
            <div className="flex items-center gap-2 mb-1">
              <AlertTriangle size={14} style={{ color: alert.impact_level === 'critical' ? 'var(--neo-red)' : 'var(--neo-yellow)' }} />
              <span className="text-xs font-semibold uppercase" style={{ color: alert.impact_level === 'critical' ? 'var(--neo-red)' : 'var(--neo-yellow)' }}>{alert.impact_level}</span>
              <span className="text-[10px] ml-auto" style={{ color: 'var(--neo-text-muted)' }}>{new Date(alert.detected_at).toLocaleString()}</span>
            </div>
            <p className="text-sm" style={{ color: 'var(--neo-text)' }}>{alert.headline}</p>
            <p className="text-[10px] mt-1" style={{ color: 'var(--neo-text-muted)' }}>
              Keywords: {alert.matched_keywords?.join(', ')} | Source: {alert.source_url
                ? <a href={alert.source_url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--neo-blue)', textDecoration: 'underline' }}>{alert.source_name}</a>
                : alert.source_name}
            </p>
          </div>
        ))}
        {(!watchdog?.recent_alerts || watchdog.recent_alerts.length === 0) && (
          <p className="text-center py-4 text-xs" style={{ color: 'var(--neo-text-muted)' }}>No breaking alerts detected.</p>
        )}
      </div>
    </div>
  );
}

// ── Runtime Session Tab ──

function RuntimeTab({ plans }) {
  const [selected, setSelected] = useState({});
  const [focusEntity, setFocusEntity] = useState('');
  const [view, setView] = useState(null);
  const [fusionInsights, setFusionInsights] = useState([]);
  const [loading, setLoading] = useState(false);

  const allContexts = (plans || []).map(p => p.name);

  const toggleContext = (name) => {
    setSelected(prev => ({ ...prev, [name]: !prev[name] }));
  };

  const assemble = async () => {
    const contexts = Object.entries(selected).filter(([, v]) => v).map(([k]) => k.toLowerCase().replace(/ /g, '_'));
    if (contexts.length === 0) return toast.error('Select at least one context');
    setLoading(true);
    try {
      // Assemble runtime view
      const res = await api.post('/intelligence/runtime/assemble', {
        contexts, focus_entity: focusEntity, days: 30,
      });
      setView(res.data);

      // Try to load fusion insights from any fusion graphs
      const fusionNames = contexts.length === 2
        ? [`${contexts[0]}_${contexts[1]}_fusion`, `${contexts[1]}_${contexts[0]}_fusion`]
        : contexts.length > 2 ? ['it_sector_fusion'] : [];

      const insights = [];
      for (const fn of fusionNames) {
        try {
          const fRes = await api.get('/graph/nodes', { params: { graph: fn, limit: 50 } });
          const nodes = (fRes.data.nodes || []).filter(n => n.label === 'FusedInsight');
          insights.push(...nodes.map(n => ({ ...n.properties, _graph: fn })));
        } catch (_) {}
      }
      setFusionInsights(insights);
    } catch (e) { toast.error('Assembly failed'); }
    setLoading(false);
  };

  return (
    <div>
      <h2 className="text-lg font-semibold mb-3" style={{ color: 'var(--neo-text)' }}>Runtime Context Assembly</h2>
      <p className="text-xs mb-4" style={{ color: 'var(--neo-text-muted)' }}>
        Select multiple atomic contexts to combine into a unified analytical view. Data stays in its own context — assembly happens on-the-fly.
      </p>

      {/* Context picker */}
      <div className="p-3 rounded-lg mb-4" style={{ background: 'var(--neo-bg-secondary)', border: '1px solid var(--neo-border)' }}>
        <div className="flex items-center gap-2 mb-2">
          <span className="text-xs font-semibold" style={{ color: 'var(--neo-text-muted)' }}>SELECT CONTEXTS:</span>
          <button onClick={() => { const all = {}; allContexts.forEach(c => all[c] = true); setSelected(all); }}
            className="text-[10px] px-2 py-0.5 rounded" style={{ background: 'var(--neo-blue)', color: '#fff' }}>All</button>
          <button onClick={() => setSelected({})}
            className="text-[10px] px-2 py-0.5 rounded" style={{ color: 'var(--neo-text-muted)' }}>Clear</button>
        </div>
        <div className="flex gap-1.5 flex-wrap mb-3">
          {allContexts.map(name => (
            <button key={name} onClick={() => toggleContext(name)}
              className="text-[10px] px-2.5 py-1 rounded-full font-medium transition-all"
              style={{
                background: selected[name] ? 'var(--neo-blue)' : 'var(--neo-bg)',
                color: selected[name] ? '#fff' : 'var(--neo-text-muted)',
                border: `1px solid ${selected[name] ? 'var(--neo-blue)' : 'var(--neo-border)'}`,
              }}>
              {name}
            </button>
          ))}
        </div>
        <div className="flex gap-2">
          <input placeholder="Focus entity (optional, e.g., TCS)" value={focusEntity}
            onChange={e => setFocusEntity(e.target.value)}
            className="flex-1 px-3 py-1.5 rounded text-xs" style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
          <button onClick={assemble} disabled={loading}
            className="px-4 py-1.5 rounded text-xs font-medium" style={{ background: 'var(--neo-blue)', color: '#fff' }}>
            {loading ? <Loader2 size={12} className="animate-spin" /> : 'Assemble View'}
          </button>
        </div>
      </div>

      {/* Fusion Timeline + Graph */}
      {Object.values(selected).some(v => v) && (
        <div className="mb-3 space-y-3">
          {/* Time-series view */}
          <FusionTimeline
            contexts={Object.entries(selected).filter(([, v]) => v).map(([k]) => k.toLowerCase().replace(/ /g, '_'))}
            focusEntity={focusEntity}
          />
          {/* Graph view (collapsible) */}
          <details className="group">
            <summary className="text-xs font-semibold cursor-pointer flex items-center gap-2 mb-2" style={{ color: 'var(--neo-text-muted)' }}>
              <Eye size={12} /> Graph View (click to expand)
            </summary>
            <FusionGraphExplorer
              contexts={Object.entries(selected).filter(([, v]) => v).map(([k]) => k.toLowerCase().replace(/ /g, '_'))}
              fusionContext=""
            />
          </details>
        </div>
      )}

      {/* Assembled view */}
      {view && (
        <div className="space-y-3">
          {/* Stats */}
          <div className="grid grid-cols-5 gap-2">
            {[
              { label: 'Contexts', value: view.stats?.contexts_queried || 0, color: '#3b82f6' },
              { label: 'Entities', value: view.stats?.total_entities || 0, color: '#8b5cf6' },
              { label: 'Facts', value: view.stats?.total_facts || 0, color: '#ec4899' },
              { label: 'Indicators', value: view.stats?.total_indicators || 0, color: '#f59e0b' },
              { label: 'Cross-Context', value: view.stats?.cross_context_entities || 0, color: '#10b981' },
            ].map((s, i) => (
              <div key={i} className="p-2.5 rounded-lg text-center" style={{ background: `${s.color}10`, border: `1px solid ${s.color}30` }}>
                <p className="text-[9px] uppercase font-semibold" style={{ color: `${s.color}aa` }}>{s.label}</p>
                <p className="text-lg font-bold" style={{ color: s.color }}>{s.value}</p>
              </div>
            ))}
          </div>

          {/* Sentiment comparison */}
          {view.sentiment_comparison && Object.keys(view.sentiment_comparison).length > 0 && (
            <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg-secondary)' }}>
              <h3 className="text-[10px] font-bold uppercase mb-2" style={{ color: 'var(--neo-text-muted)' }}>Sentiment Across Contexts</h3>
              <div className="space-y-1.5">
                {Object.entries(view.sentiment_comparison)
                  .sort((a, b) => (b[1].total || 0) - (a[1].total || 0))
                  .slice(0, 12)
                  .map(([entity, counts]) => {
                    const net = (counts.positive || 0) - (counts.negative || 0);
                    const ctxs = counts.contexts || [];
                    return (
                      <div key={entity} className="flex items-center gap-2 text-xs">
                        <span className="w-32 truncate font-medium" style={{ color: 'var(--neo-text)' }}>{entity}</span>
                        <div className="flex-1 flex h-2 rounded-full overflow-hidden" style={{ background: 'var(--neo-bg)' }}>
                          {counts.positive > 0 && <div style={{ width: `${(counts.positive / Math.max(1, counts.total)) * 100}%`, background: '#10b981' }} />}
                          {counts.neutral > 0 && <div style={{ width: `${(counts.neutral / Math.max(1, counts.total)) * 100}%`, background: '#6b7280' }} />}
                          {counts.negative > 0 && <div style={{ width: `${(counts.negative / Math.max(1, counts.total)) * 100}%`, background: '#ef4444' }} />}
                        </div>
                        <span className="w-8 text-right font-mono" style={{ color: net > 0 ? '#10b981' : net < 0 ? '#ef4444' : '#6b7280' }}>
                          {net > 0 ? '+' : ''}{net}
                        </span>
                        <span className="text-[9px] w-20 truncate" style={{ color: 'var(--neo-text-muted)' }}>{ctxs.join(', ')}</span>
                      </div>
                    );
                  })}
              </div>
            </div>
          )}

          {/* Cross-context entities */}
          {view.cross_entity_pairs && view.cross_entity_pairs.length > 0 && (
            <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg-secondary)' }}>
              <h3 className="text-[10px] font-bold uppercase mb-2" style={{ color: 'var(--neo-text-muted)' }}>Cross-Context Entities (appear in multiple contexts)</h3>
              <div className="space-y-1">
                {view.cross_entity_pairs.map((pair, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs p-1.5 rounded" style={{ background: 'var(--neo-bg)' }}>
                    <Zap size={10} style={{ color: '#f59e0b' }} />
                    <span className="font-medium" style={{ color: 'var(--neo-text)' }}>{pair.entity}</span>
                    <span style={{ color: 'var(--neo-text-muted)' }}>in</span>
                    {(pair.contexts || []).map(c => (
                      <span key={c} className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: 'var(--neo-blue-bg)', color: 'var(--neo-blue)' }}>{c}</span>
                    ))}
                    <span className="ml-auto text-[10px]" style={{ color: pair.consistent ? '#10b981' : '#ef4444' }}>
                      {pair.consistent ? 'consistent' : 'conflicting'}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Facts from all contexts */}
          {view.facts && view.facts.length > 0 && (
            <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg-secondary)' }}>
              <h3 className="text-[10px] font-bold uppercase mb-2" style={{ color: 'var(--neo-text-muted)' }}>Facts Across Contexts ({view.facts.length})</h3>
              <div className="space-y-1 max-h-60 overflow-y-auto">
                {view.facts.slice(0, 20).map((f, i) => (
                  <div key={i} className="flex items-start gap-2 text-xs py-1 border-b" style={{ borderColor: 'var(--neo-border)' }}>
                    <span className="text-[9px] px-1.5 py-0.5 rounded flex-shrink-0 mt-0.5" style={{ background: 'var(--neo-blue-bg)', color: 'var(--neo-blue)' }}>
                      {f.source_context}
                    </span>
                    <span style={{ color: 'var(--neo-text)' }}>{f.statement}</span>
                    {f.sentiment && (
                      <span className="flex-shrink-0 text-[10px]" style={{ color: f.sentiment === 'positive' ? '#10b981' : f.sentiment === 'negative' ? '#ef4444' : '#6b7280' }}>
                        {f.sentiment}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Fusion Insights — discovered correlations */}
          {fusionInsights.length > 0 && (
            <div className="p-3 rounded-lg" style={{ background: '#ec489910', border: '1px solid #ec489930' }}>
              <h3 className="text-[10px] font-bold uppercase mb-2 flex items-center gap-2" style={{ color: '#ec4899' }}>
                <Zap size={12} /> Discovered Correlations ({fusionInsights.length})
              </h3>
              <div className="space-y-2 max-h-60 overflow-y-auto">
                {fusionInsights.slice(0, 15).map((insight, i) => {
                  const edgeColors = {
                    COINCIDES_WITH: '#f59e0b', FOLLOWS: '#3b82f6',
                    CAUSES: '#ef4444', CORRELATES_WITH: '#8b5cf6', BRIDGE_ENTITY: '#10b981',
                  };
                  const color = edgeColors[insight.edge_type] || '#6b7280';
                  return (
                    <div key={i} className="p-2 rounded text-xs" style={{ background: 'var(--neo-bg)', border: `1px solid ${color}30` }}>
                      <div className="flex items-center gap-2 mb-1">
                        <span className="px-1.5 py-0.5 rounded text-[9px] font-medium" style={{ background: `${color}20`, color }}>
                          {(insight.edge_type || '').replace(/_/g, ' ')}
                        </span>
                        <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                          conf: {((insight.confidence || 0) * 100).toFixed(0)}% | occ: {insight.occurrences || 0}
                        </span>
                      </div>
                      <div className="flex items-center gap-1 flex-wrap">
                        <span className="font-medium" style={{ color: 'var(--neo-text)' }}>{(insight.source_entity || '').substring(0, 25)}</span>
                        <span style={{ color: 'var(--neo-text-muted)' }}>({insight.source_context})</span>
                        <span style={{ color }}>→</span>
                        <span className="font-medium" style={{ color: 'var(--neo-text)' }}>{(insight.target_entity || '').substring(0, 25)}</span>
                        <span style={{ color: 'var(--neo-text-muted)' }}>({insight.target_context})</span>
                      </div>
                      {insight.pattern && (
                        <p className="text-[10px] mt-1" style={{ color: 'var(--neo-text-muted)' }}>{insight.pattern.substring(0, 80)}</p>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {!view && !loading && (
        <p className="text-center py-8 text-sm" style={{ color: 'var(--neo-text-muted)' }}>
          Select contexts above and click "Assemble View" to see cross-context analysis.
        </p>
      )}
    </div>
  );
}

// ── Main Page ──

export default function ContextLakePage() {
  const [activeTab, setActiveTab] = useState('tracking');
  const [plans, setPlans] = useState([]);
  const [signals, setSignals] = useState(null);
  const [watchdog, setWatchdog] = useState(null);
  const [health, setHealth] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const [plansRes, signalsRes, watchdogRes, healthRes] = await Promise.allSettled([
        api.get('/intelligence/plans'),
        api.get('/intelligence/signals?limit=20'),
        api.get('/intelligence/watchdog/status'),
        api.get('/intelligence/health/failing'),
      ]);
      if (plansRes.status === 'fulfilled') setPlans(plansRes.value.data.plans || []);
      if (signalsRes.status === 'fulfilled') setSignals(signalsRes.value.data);
      if (watchdogRes.status === 'fulfilled') setWatchdog(watchdogRes.value.data);
      if (healthRes.status === 'fulfilled') setHealth(healthRes.value.data);
    } catch (e) { console.error('Context Lake fetch error:', e); }
    setLoading(false);
  }, []);

  // Auto-refresh every 30 seconds
  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, [fetchData]);

  // Toast notifications for new breaking alerts
  const prevAlertCount = React.useRef(0);
  useEffect(() => {
    const alertCount = watchdog?.recent_alerts?.length || 0;
    if (alertCount > prevAlertCount.current && prevAlertCount.current > 0) {
      const latest = watchdog.recent_alerts[0];
      toast(`⚡ ${latest.headline}`, {
        icon: '🔴',
        duration: 8000,
        style: { background: '#1a1a2e', color: '#e0e0e0', border: '1px solid #e74c3c' },
      });
    }
    prevAlertCount.current = alertCount;
  }, [watchdog?.recent_alerts?.length]);

  // Toast for new escalations
  const prevEscCount = React.useRef(0);
  useEffect(() => {
    const escCount = signals?.active_escalations?.length || 0;
    if (escCount > prevEscCount.current && prevEscCount.current > 0) {
      const latest = signals.active_escalations[0];
      toast(`🚨 Escalation: ${latest.entity_name} — ${latest.signal_type}`, {
        duration: 6000,
        style: { background: '#1a1a2e', color: '#e0e0e0', border: '1px solid #f39c12' },
      });
    }
    prevEscCount.current = escCount;
  }, [signals?.active_escalations?.length]);

  const tabs = [
    { key: 'tracking', icon: Globe, label: 'Tracking' },
    { key: 'runtime', icon: Eye, label: 'Runtime View' },
    { key: 'signals', icon: Zap, label: `Signals${signals?.active_escalations?.length ? ` (${signals.active_escalations.length})` : ''}` },
    { key: 'sentiment', icon: TrendingUp, label: 'Sentiment' },
    { key: 'watchdog', icon: Shield, label: `Watchdog${watchdog?.running ? ' (ON)' : ''}` },
  ];

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Radar size={24} style={{ color: 'var(--neo-blue)' }} />
          <div>
            <h1 className="text-xl font-bold" style={{ color: 'var(--neo-text)' }}>Context Lake</h1>
            <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
              Track entities, collect signals, monitor sentiment — no code required
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-1 text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
            <div className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
            Live (30s)
          </span>
          <button onClick={fetchData} disabled={loading} className="flex items-center gap-1 px-3 py-1.5 rounded text-xs"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}>
            {loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            Refresh
          </button>
        </div>
      </div>

      {/* Stats bar */}
      <div className="grid grid-cols-4 gap-3 mb-6">
        <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg-secondary)' }}>
          <p className="text-[10px] uppercase" style={{ color: 'var(--neo-text-muted)' }}>Tracked</p>
          <p className="text-2xl font-bold" style={{ color: 'var(--neo-text)' }}>{plans.length}</p>
        </div>
        <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg-secondary)' }}>
          <p className="text-[10px] uppercase" style={{ color: 'var(--neo-text-muted)' }}>Pipelines</p>
          <p className="text-2xl font-bold" style={{ color: 'var(--neo-text)' }}>{plans.reduce((s, p) => s + (p.pipeline_count || 0), 0)}</p>
        </div>
        <div className="p-3 rounded-lg" style={{ background: signals?.active_escalations?.length ? 'var(--neo-red-bg)' : 'var(--neo-bg-secondary)' }}>
          <p className="text-[10px] uppercase" style={{ color: 'var(--neo-text-muted)' }}>Escalations</p>
          <p className="text-2xl font-bold" style={{ color: signals?.active_escalations?.length ? 'var(--neo-red)' : 'var(--neo-text)' }}>
            {signals?.active_escalations?.length || 0}
          </p>
        </div>
        <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg-secondary)' }}>
          <p className="text-[10px] uppercase" style={{ color: 'var(--neo-text-muted)' }}>Watchdog</p>
          <p className="text-2xl font-bold" style={{ color: watchdog?.running ? 'var(--neo-green)' : 'var(--neo-text-muted)' }}>
            {watchdog?.running ? 'ON' : 'OFF'}
          </p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-4 border-b" style={{ borderColor: 'var(--neo-border)' }}>
        {tabs.map(tab => (
          <button key={tab.key} onClick={() => setActiveTab(tab.key)}
            className="flex items-center gap-1.5 px-4 py-2 text-xs font-medium border-b-2 transition-colors"
            style={{
              borderColor: activeTab === tab.key ? 'var(--neo-blue)' : 'transparent',
              color: activeTab === tab.key ? 'var(--neo-blue)' : 'var(--neo-text-muted)',
            }}>
            <tab.icon size={14} /> {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === 'tracking' && <TrackingTab plans={plans} onRefresh={fetchData} loading={loading} />}
      {activeTab === 'runtime' && <RuntimeTab plans={plans} />}
      {activeTab === 'signals' && <SignalsTab signals={signals} />}
      {activeTab === 'sentiment' && <SentimentTab />}
      {activeTab === 'watchdog' && <WatchdogTab watchdog={watchdog} onRefresh={fetchData} />}
    </div>
  );
}

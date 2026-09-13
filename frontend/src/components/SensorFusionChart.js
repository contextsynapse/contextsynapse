import React, { useState, useEffect, useCallback } from 'react';
import { Loader2, RefreshCw, ChevronDown, ChevronRight, Zap } from 'lucide-react';
import { ComposedChart, Line, Bar, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, ReferenceLine, Cell } from 'recharts';
import api from '../lib/api';

const SENSOR_COLORS = {
  price: '#3b82f6',
  fx: '#eab308',
  index: '#8b5cf6',
  macro: '#06b6d4',
  macro_event: '#06b6d4',
  company_news: '#22c55e',
  company_fact: '#84cc16',
  fund_flow: '#f97316',
  fund_holding: '#ec4899',
  calendar: '#6b7280',
};

function DecayBar({ label, original, current, decayPct, evidence, category }) {
  const isPos = original > 0;
  const color = isPos ? '#22c55e' : '#dc2626';
  const width = Math.min(Math.abs(current) * 200, 100);
  return (
    <div className="flex items-center gap-2 py-1">
      <span className="text-[10px] w-28 truncate" style={{ color: 'var(--neo-text)' }} title={label}>{label}</span>
      <div className="flex-1 flex items-center gap-1">
        <div className="h-3 rounded relative" style={{ width: `${width}%`, background: color, minWidth: 4, opacity: 0.3 + (1 - decayPct / 100) * 0.7 }}>
          {decayPct > 5 && <div className="absolute right-0 top-0 h-full rounded-r" style={{ width: `${decayPct}%`, background: 'var(--neo-bg)', opacity: 0.5 }} />}
        </div>
        <span className="text-[9px]" style={{ color: 'var(--neo-text-muted)' }}>{decayPct > 0 ? `-${decayPct.toFixed(0)}%` : 'fresh'}</span>
      </div>
      <span className="text-[9px] w-8 text-right font-bold" style={{ color }}>{current > 0 ? '+' : ''}{current.toFixed(2)}</span>
    </div>
  );
}

export default function SensorFusionChart({ entity, onClose }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showDecaying, setShowDecaying] = useState(true);
  const [showEvents, setShowEvents] = useState(true);

  const fetchFusion = useCallback(async () => {
    setLoading(true);
    try {
      // Try dedicated fusion endpoint first
      const res = await api.get(`/graph/fusion/${encodeURIComponent(entity)}`, { timeout: 15000, suppressErrorToast: true });
      if (res.data && !res.data.error && res.data.direction) {
        setData(res.data);
        setLoading(false);
        return;
      }
    } catch (e) {
      // Fusion endpoint not available — build from graph nodes directly
    }

    // Fallback: gather sensor data from individual graphs
    try {
      const ns = entity.toLowerCase().replace(/ /g, '_');
      const [priceRes, newsRes, fundRes, fxRes, indexRes] = await Promise.allSettled([
        api.get('/graph/nodes', { params: { graph: `${ns}_price`, limit: 50 } }),
        api.get('/graph/nodes', { params: { graph: ns, limit: 100 } }),
        api.get('/graph/nodes', { params: { graph: 'fund_positioning', limit: 100 } }),
        api.get('/graph/nodes', { params: { graph: 'usd_inr', limit: 20 } }),
        api.get('/graph/nodes', { params: { graph: 'nifty_it_index', limit: 20 } }),
      ]);

      const priceNodes = (priceRes.status === 'fulfilled' ? priceRes.value.data.nodes || [] : []).filter(n => n.label === 'Indicator');
      const newsNodes = newsRes.status === 'fulfilled' ? newsRes.value.data.nodes || [] : [];
      const fundNodes = (fundRes.status === 'fulfilled' ? fundRes.value.data.nodes || [] : []).filter(n => n.label === 'Indicator');
      const fxNodes = (fxRes.status === 'fulfilled' ? fxRes.value.data.nodes || [] : []).filter(n => n.label === 'Indicator');
      const idxNodes = (indexRes.status === 'fulfilled' ? indexRes.value.data.nodes || [] : []).filter(n => n.label === 'Indicator');

      // Build moving signals
      const moving = [];
      priceNodes.sort((a, b) => (a.properties?._published_at || '').localeCompare(b.properties?._published_at || ''));
      priceNodes.forEach(n => moving.push({ sensor: 'price', sensor_type: 'moving', value: (n.properties?._price_change_pct || 0) / 100, raw_value: n.properties?.value || 0, timestamp: n.properties?._published_at || '', label: `${entity} ${n.properties?.value?.toFixed(2)}`, category: 'price' }));
      fxNodes.forEach(n => moving.push({ sensor: 'usd_inr', sensor_type: 'moving', value: 0, raw_value: n.properties?.value || 0, timestamp: n.properties?._published_at || '', label: `USD/INR ${n.properties?.value?.toFixed(2)}`, category: 'fx' }));
      idxNodes.forEach(n => moving.push({ sensor: 'nifty_it', sensor_type: 'moving', value: 0, raw_value: n.properties?.value || 0, timestamp: n.properties?._published_at || '', label: `Nifty IT ${n.properties?.value?.toFixed(0)}`, category: 'index' }));

      // Build event signals from news
      const events = [];
      const entities = newsNodes.filter(n => n.label === 'Entity');
      const facts = newsNodes.filter(n => n.label === 'Fact');
      entities.forEach(n => {
        const sent = n.properties?._sentiment;
        if (sent === 'positive' || sent === 'negative') {
          events.push({ sensor: `${ns}_news`, sensor_type: 'event', value: sent === 'positive' ? 0.5 : -0.5, raw_value: 0, timestamp: n.properties?._published_at || '', label: n.properties?.name || '', category: 'company_news' });
        }
      });
      facts.slice(0, 10).forEach(n => {
        events.push({ sensor: `${ns}_fact`, sensor_type: 'event', value: 0.1, raw_value: 0, timestamp: n.properties?._published_at || '', label: (n.properties?.statement || n.properties?.name || '').substring(0, 80), category: 'company_fact' });
      });

      // Fund events
      fundNodes.filter(n => n.properties?.source_type === 'fii_flow').forEach(n => {
        const val = n.properties?.value || 0;
        events.push({ sensor: 'fii_flow', sensor_type: 'event', value: val > 0 ? 0.3 : -0.3, raw_value: val, timestamp: n.properties?._published_at || '', label: `FII ${val > 0 ? '+' : ''}${val.toFixed(0)} Cr`, category: 'fund_flow' });
      });
      fundNodes.filter(n => n.properties?.source_type === 'fund_positioning' && n.properties?.entity === entity).forEach(n => {
        const change = n.properties?._change || 0;
        events.push({ sensor: 'mf_holding', sensor_type: 'event', value: change > 0 ? 0.4 : -0.4, raw_value: change, timestamp: n.properties?._published_at || '', label: `${n.properties?._fund_name || 'Fund'} ${change > 0 ? '+' : ''}${change.toFixed(1)}%`, category: 'fund_holding' });
      });

      // Compute sensor summary
      const summary = {};
      [...moving, ...events].forEach(s => {
        summary[s.sensor] = (summary[s.sensor] || 0) + (s.value || 0) * 0.1;
      });

      const totalScore = Object.values(summary).reduce((s, v) => s + v, 0);
      const direction = totalScore > 0.1 ? 'bullish' : totalScore < -0.1 ? 'bearish' : 'neutral';

      setData({
        direction, confidence: Math.min(Math.abs(totalScore) * 2, 1), fused_score: totalScore,
        moving_signals: moving, decaying_signals: [], event_signals: events.sort((a, b) => (b.timestamp || '').localeCompare(a.timestamp || '')).slice(0, 20),
        sensor_summary: summary, timeline: [],
      });
    } catch (e) {
      console.error('Fusion fallback error:', e);
    }
    setLoading(false);
  }, [entity]);

  useEffect(() => { fetchFusion(); }, [fetchFusion]);

  if (loading) return <div className="p-8 text-center"><Loader2 size={20} className="animate-spin inline" style={{ color: 'var(--neo-blue)' }} /></div>;
  if (!data || data.error) return <div className="p-4 text-center text-xs" style={{ color: 'var(--neo-text-muted)' }}>{data?.error || 'No fusion data'}</div>;

  const dirColor = data.direction === 'bullish' ? '#22c55e' : data.direction === 'bearish' ? '#dc2626' : '#eab308';

  // Build chart data from timeline
  const chartData = [];
  const pricePoints = (data.moving_signals || []).filter(s => s.sensor === 'price');
  const fxPoints = (data.moving_signals || []).filter(s => s.sensor === 'usd_inr');
  const indexPoints = (data.moving_signals || []).filter(s => s.sensor === 'nifty_it_index');

  // Merge all moving signals onto timeline
  const timeMap = {};
  pricePoints.forEach(p => {
    const t = (p.timestamp || '').substring(0, 16);
    if (!t) return;
    timeMap[t] = { ...timeMap[t], t: t.substring(5, 16), price: p.raw_value };
  });
  fxPoints.forEach(p => {
    const t = (p.timestamp || '').substring(0, 16);
    if (!t) return;
    timeMap[t] = { ...timeMap[t], t: t.substring(5, 16), fx: p.raw_value };
  });
  indexPoints.forEach(p => {
    const t = (p.timestamp || '').substring(0, 16);
    if (!t) return;
    timeMap[t] = { ...timeMap[t], t: t.substring(5, 16), index: p.raw_value };
  });

  // Add event markers to timeline
  (data.event_signals || []).forEach(e => {
    const t = (e.timestamp || '').substring(0, 16);
    if (!t) return;
    if (!timeMap[t]) timeMap[t] = { t: t.substring(5, 16) };
    timeMap[t].event = e.value;
    timeMap[t].eventLabel = e.label;
    timeMap[t].eventCategory = e.category;
  });

  const sortedData = Object.values(timeMap).sort((a, b) => (a.t || '').localeCompare(b.t || ''));

  // Sensor contribution chart data
  const contribData = Object.entries(data.sensor_summary || {})
    .filter(([, v]) => Math.abs(v) > 0.001)
    .map(([sensor, value]) => ({
      sensor: sensor.replace(/_/g, ' ').replace(/fact$/, '').replace(/news$/, '').trim(),
      value: Math.round(value * 100) / 100,
      fill: value > 0 ? '#22c55e' : '#dc2626',
    }));

  return (
    <div className="rounded-lg border p-4" style={{ background: 'var(--neo-bg-secondary)', borderColor: dirColor }}>
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <Zap size={18} style={{ color: dirColor }} />
          <div>
            <h3 className="text-sm font-bold" style={{ color: 'var(--neo-text)' }}>{entity} — Sensor Fusion</h3>
            <p className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
              {(data.moving_signals || []).length} moving + {(data.decaying_signals || []).length} decaying + {(data.event_signals || []).length} event sensors
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-lg font-bold px-3 py-1 rounded" style={{ background: `${dirColor}22`, color: dirColor }}>
            {data.direction?.toUpperCase()} {(data.confidence * 100).toFixed(0)}%
          </span>
          <span className="text-sm font-bold" style={{ color: dirColor }}>
            Score: {data.fused_score > 0 ? '+' : ''}{data.fused_score?.toFixed(3)}
          </span>
          <button onClick={fetchFusion} className="p-1 rounded" style={{ color: 'var(--neo-text-muted)' }}>
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {/* Main fused chart — price + FX + index + events */}
      <div className="mb-4">
        <h4 className="text-[10px] uppercase font-semibold mb-1" style={{ color: 'var(--neo-text-muted)' }}>
          Fused Timeline — Price + FX + Index + Events
        </h4>
        {sortedData.length > 0 ? (
          <ResponsiveContainer width="100%" height={200}>
            <ComposedChart data={sortedData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#333" />
              <XAxis dataKey="t" tick={{ fontSize: 8, fill: '#666' }} />
              <YAxis yAxisId="price" domain={['dataMin', 'dataMax']} tick={{ fontSize: 8, fill: '#3b82f6' }} width={55} />
              <YAxis yAxisId="fx" orientation="right" domain={['dataMin', 'dataMax']} tick={{ fontSize: 8, fill: '#eab308' }} width={45} />
              <Tooltip contentStyle={{ background: '#1a1a2e', border: '1px solid #333', fontSize: 10 }}
                formatter={(value, name) => {
                  if (name === 'price') return [value?.toFixed(2), 'Price'];
                  if (name === 'fx') return [value?.toFixed(2), 'USD/INR'];
                  if (name === 'event') return [value?.toFixed(2), 'Event Signal'];
                  return [value, name];
                }} />
              <Line yAxisId="price" type="monotone" dataKey="price" stroke="#3b82f6" strokeWidth={2} dot={false} connectNulls />
              <Line yAxisId="fx" type="monotone" dataKey="fx" stroke="#eab308" strokeWidth={1.5} dot={false} connectNulls strokeDasharray="5 3" />
              {/* Event markers as bars */}
              <Bar yAxisId="price" dataKey="event" barSize={6}>
                {sortedData.map((entry, i) => (
                  <Cell key={i} fill={entry.event > 0 ? '#22c55e55' : entry.event < 0 ? '#dc262655' : 'transparent'} />
                ))}
              </Bar>
              <ReferenceLine yAxisId="price" y={0} stroke="#666" strokeDasharray="3 3" />
            </ComposedChart>
          </ResponsiveContainer>
        ) : <p className="text-[10px] text-center py-8" style={{ color: 'var(--neo-text-muted)' }}>No timeline data</p>}
        <div className="flex gap-4 mt-1 text-[9px]" style={{ color: 'var(--neo-text-muted)' }}>
          <span><span style={{ color: '#3b82f6' }}>---</span> Price</span>
          <span><span style={{ color: '#eab308' }}>- -</span> USD/INR</span>
          <span><span style={{ color: '#22c55e' }}>|</span> Bullish event</span>
          <span><span style={{ color: '#dc2626' }}>|</span> Bearish event</span>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-4">
        {/* Sensor contributions */}
        <div>
          <h4 className="text-[10px] uppercase font-semibold mb-2" style={{ color: 'var(--neo-text-muted)' }}>Sensor Contributions</h4>
          {contribData.length > 0 ? (
            <ResponsiveContainer width="100%" height={contribData.length * 22 + 10}>
              <ComposedChart data={contribData} layout="vertical">
                <XAxis type="number" tick={{ fontSize: 8, fill: '#666' }} />
                <YAxis dataKey="sensor" type="category" tick={{ fontSize: 8, fill: '#aaa' }} width={80} />
                <Tooltip contentStyle={{ background: '#1a1a2e', border: '1px solid #333', fontSize: 10 }} />
                <Bar dataKey="value" barSize={12}>
                  {contribData.map((e, i) => <Cell key={i} fill={e.fill} />)}
                </Bar>
                <ReferenceLine x={0} stroke="#666" />
              </ComposedChart>
            </ResponsiveContainer>
          ) : <p className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>No contributions</p>}
        </div>

        {/* Decaying signals */}
        <div>
          <div className="flex items-center gap-1 cursor-pointer mb-2" onClick={() => setShowDecaying(!showDecaying)}>
            {showDecaying ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            <h4 className="text-[10px] uppercase font-semibold" style={{ color: 'var(--neo-text-muted)' }}>
              Decaying Signals ({(data.decaying_signals || []).length})
            </h4>
          </div>
          {showDecaying && (
            <div>
              {(data.decaying_signals || []).slice(0, 10).map((s, i) => (
                <DecayBar key={i} label={s.label} original={s.original} current={s.current}
                  decayPct={s.decay_pct} evidence={s.evidence} category={s.category} />
              ))}
            </div>
          )}
        </div>

        {/* Recent events */}
        <div>
          <div className="flex items-center gap-1 cursor-pointer mb-2" onClick={() => setShowEvents(!showEvents)}>
            {showEvents ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            <h4 className="text-[10px] uppercase font-semibold" style={{ color: 'var(--neo-text-muted)' }}>
              Events ({(data.event_signals || []).length})
            </h4>
          </div>
          {showEvents && (
            <div className="space-y-1 max-h-[200px] overflow-y-auto">
              {(data.event_signals || []).map((e, i) => {
                const rel = e.relevance_score ?? 1;
                const relColor = rel >= 0.75 ? '#22c55e' : rel >= 0.5 ? '#eab308' : '#94a3b8';
                return (
                  <div key={i} className="flex items-start gap-1 py-0.5 rounded px-1"
                    style={{ opacity: 0.5 + rel * 0.5, background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.02)' }}>
                    <span className="text-[9px] font-bold w-5 flex-shrink-0" style={{ color: e.value > 0 ? '#22c55e' : e.value < 0 ? '#dc2626' : '#888' }}>
                      {e.value > 0 ? '+' : e.value < 0 ? '−' : '='}
                    </span>
                    <div className="flex-1 min-w-0">
                      <p className="text-[10px] truncate" style={{ color: 'var(--neo-text)' }} title={e.label}>{e.label}</p>
                      <div className="flex items-center gap-1 mt-0.5">
                        <span className="text-[8px] px-1 rounded" style={{ background: (SENSOR_COLORS[e.category] || '#888') + '22', color: SENSOR_COLORS[e.category] || '#888' }}>
                          {e.category?.replace(/_/g, ' ')}
                        </span>
                        <span className="text-[7px] px-1 rounded font-medium" style={{ background: relColor + '22', color: relColor }}>
                          {Math.round(rel * 100)}% rel
                        </span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

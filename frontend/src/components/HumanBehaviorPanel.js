/**
 * HumanBehaviorPanel.js
 *
 * Visualizes the 8 human-behavior sensor sub-signals for a focused stock.
 * Reads directly from the fusion endpoint (event_signals + sensor_summary filtered
 * to the `human_behavior` category) so no extra API call is needed beyond what
 * the intelligence HUD already fetches.
 *
 * Props:
 *   entity      — stock symbol (e.g. "SBIN", "TCS")
 *   fusionData  — optional pre-fetched fusion result to avoid re-fetching
 *   refreshKey  — increment to trigger re-fetch
 */
import React, { useState, useEffect, useCallback } from 'react';
import {
  RadarChart, Radar, PolarGrid, PolarAngleAxis, ResponsiveContainer,
  BarChart, Bar, XAxis, YAxis, Tooltip, Cell, ReferenceLine,
} from 'recharts';
import { RefreshCw, Users, TrendingUp, TrendingDown, Minus, AlertTriangle, Eye } from 'lucide-react';
import axios from 'axios';

const api = axios.create({ baseURL: '', timeout: 15000 });

// ── Sub-signal metadata ─────────────────────────────────────────────────────
const SIGNAL_META = {
  retail_volume:    { label: 'Retail Volume',    icon: '📊', desc: 'Volume anomaly vs 20-day avg — spike = herding' },
  social_buzz:      { label: 'Social Buzz',      icon: '📰', desc: 'News density per day — surge = retail attention' },
  analyst_action:   { label: 'Analyst Action',   icon: '🏦', desc: 'Broker upgrades / downgrades / target changes' },
  insider_activity: { label: 'Insider',          icon: '🔑', desc: 'Promoter buying / selling / pledging' },
  panic_fomo:       { label: 'Panic/FOMO',       icon: '⚡', desc: 'Extreme move with volume — emotion-driven' },
  options_pcr:      { label: 'Options PCR',      icon: '📈', desc: 'Put/call sentiment proxy via VIX + expiry' },
  retail_flow:      { label: 'Retail Flow',      icon: '💰', desc: 'Calendar retail patterns (SIP / salary day)' },
  herd_behavior:    { label: 'Herd',             icon: '🐄', desc: 'Cross-portfolio directional correlation burst' },
};

const ORDER = [
  'analyst_action', 'insider_activity', 'retail_volume', 'social_buzz',
  'panic_fomo', 'options_pcr', 'retail_flow', 'herd_behavior',
];

function signalColor(v) {
  if (v > 0.25) return '#22c55e';
  if (v > 0.05) return '#86efac';
  if (v < -0.25) return '#ef4444';
  if (v < -0.05) return '#fca5a5';
  return '#6b7280';
}

function fmt(v, d = 2) { return Number(v || 0).toFixed(d); }

// ── Mini signal row ─────────────────────────────────────────────────────────
function SignalRow({ sensorKey, value, events }) {
  const meta = SIGNAL_META[sensorKey] || { label: sensorKey, icon: '•', desc: '' };
  const color = signalColor(value);
  const barW = Math.min(100, Math.abs(value) * 130);
  const relatedEvents = (events || []).filter(e => e.sensor === sensorKey).slice(0, 2);

  return (
    <div style={{
      padding: '5px 8px', borderRadius: 6,
      background: `${color}08`, border: `1px solid ${color}22`,
      marginBottom: 4,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: '0.85em', minWidth: 18 }}>{meta.icon}</span>
        <span style={{ fontSize: '0.68em', fontWeight: 600, color: 'var(--neo-text)', flex: 1 }}>
          {meta.label}
        </span>
        {/* Bar */}
        <div style={{ width: 80, height: 6, background: 'rgba(255,255,255,0.06)', borderRadius: 3, position: 'relative', overflow: 'hidden' }}>
          <div style={{
            position: 'absolute',
            [value >= 0 ? 'left' : 'right']: value >= 0 ? '50%' : '50%',
            top: 0, height: '100%',
            width: `${barW / 2}%`,
            background: color, borderRadius: 3,
            // center the bar at 50%, extend left or right
            transform: value >= 0 ? 'none' : 'scaleX(-1)',
          }} />
          {/* Center line */}
          <div style={{ position: 'absolute', left: '50%', top: 0, width: 1, height: '100%', background: 'rgba(255,255,255,0.2)' }} />
        </div>
        <span style={{ fontSize: '0.7em', fontWeight: 700, color, minWidth: 38, textAlign: 'right' }}>
          {value >= 0 ? '+' : ''}{fmt(value, 3)}
        </span>
      </div>
      {relatedEvents.length > 0 && (
        <div style={{ marginTop: 3, paddingLeft: 24 }}>
          {relatedEvents.map((ev, i) => (
            <p key={i} style={{ fontSize: '0.6em', color: 'var(--neo-text-muted)', margin: 0, lineHeight: 1.4 }}>
              {ev.label?.slice(0, 80)}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Radar chart data builder ────────────────────────────────────────────────
function buildRadarData(summary) {
  return ORDER.map(key => {
    const meta = SIGNAL_META[key] || { label: key };
    const raw = summary[key] || 0;
    return {
      key,
      subject: meta.label.slice(0, 12),
      // Radar needs 0–100 range; map -1..+1 → 0..100 with 50=neutral
      value: Math.max(0, Math.min(100, (raw + 1) * 50)),
      raw,
    };
  });
}

// ── Sentiment verdict ───────────────────────────────────────────────────────
function HumanSentiment({ scores }) {
  const vals = Object.values(scores);
  if (!vals.length) return null;
  const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
  const color = avg > 0.1 ? '#22c55e' : avg < -0.1 ? '#ef4444' : '#6b7280';
  const label = avg > 0.1 ? 'Bullish' : avg < -0.1 ? 'Bearish' : 'Neutral';
  const Icon = avg > 0.1 ? TrendingUp : avg < -0.1 ? TrendingDown : Minus;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 6,
      padding: '4px 10px', borderRadius: 8,
      background: `${color}12`, border: `1px solid ${color}33`,
    }}>
      <Icon size={12} style={{ color }} />
      <span style={{ fontSize: '0.75em', fontWeight: 700, color }}>
        Human Sentiment: {label}
      </span>
      <span style={{ fontSize: '0.65em', color: 'var(--neo-text-muted)', marginLeft: 'auto' }}>
        avg {avg >= 0 ? '+' : ''}{fmt(avg, 3)}
      </span>
    </div>
  );
}

// ── Main component ──────────────────────────────────────────────────────────
export default function HumanBehaviorPanel({ entity, fusionData: propData, refreshKey }) {
  const [data, setData]     = useState(propData || null);
  const [loading, setLoading] = useState(!propData);
  const [tab, setTab]       = useState('signals');  // signals | radar | history

  const fetch = useCallback(async () => {
    if (!entity) return;
    setLoading(true);
    try {
      const res = await api.get(`/graph/fusion/${encodeURIComponent(entity)}`);
      setData(res.data);
    } catch (e) {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [entity]);

  useEffect(() => {
    if (propData) { setData(propData); setLoading(false); }
    else fetch();
  }, [propData, fetch, refreshKey]);

  if (!entity) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
        <p style={{ fontSize: '0.75em', color: 'var(--neo-text-muted)' }}>Select a stock</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 6 }}>
        <RefreshCw size={12} className="animate-spin" style={{ color: '#8b5cf6' }} />
        <span style={{ fontSize: '0.72em', color: 'var(--neo-text-muted)' }}>Loading…</span>
      </div>
    );
  }

  if (!data) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 8 }}>
        <AlertTriangle size={14} style={{ color: '#f59e0b' }} />
        <p style={{ fontSize: '0.72em', color: 'var(--neo-text-muted)' }}>No data</p>
        <button onClick={fetch} style={{ fontSize: '0.65em', color: '#8b5cf6', background: 'none', border: '1px solid rgba(139,92,246,0.3)', borderRadius: 4, padding: '2px 8px', cursor: 'pointer' }}>
          Retry
        </button>
      </div>
    );
  }

  // Extract human_behavior sub-signals from sensor_summary + events
  const sensorSummary = data.sensor_summary || {};
  const allEvents     = [...(data.event_signals || []), ...(data.decaying_signals || [])];

  // Filter to human_behavior category keys
  const HB_KEYS = new Set(ORDER);
  const hbScores = Object.fromEntries(
    Object.entries(sensorSummary).filter(([k]) => HB_KEYS.has(k))
  );
  const hbEvents = allEvents.filter(e => HB_KEYS.has(e.sensor));

  const radarData   = buildRadarData(hbScores);
  const barData     = ORDER.map(k => ({
    name: (SIGNAL_META[k]?.label || k).slice(0, 12),
    val: parseFloat(fmt(hbScores[k] || 0, 3)),
    key: k,
  }));

  const hasSignals = Object.keys(hbScores).length > 0;

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

      {/* Header */}
      <div style={{
        padding: '5px 10px', flexShrink: 0,
        borderBottom: '1px solid var(--neo-border)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Users size={12} style={{ color: '#8b5cf6' }} />
          <span style={{ fontSize: '0.75em', fontWeight: 700, color: 'var(--neo-text)' }}>
            {entity} · Human Behavior
          </span>
        </div>
        <button onClick={fetch} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#8b5cf6', padding: 2 }}>
          <RefreshCw size={10} />
        </button>
      </div>

      {/* Sentiment verdict */}
      <div style={{ padding: '6px 8px', flexShrink: 0 }}>
        <HumanSentiment scores={hbScores} />
      </div>

      {/* Tab bar */}
      <div style={{ display: 'flex', flexShrink: 0, borderBottom: '1px solid var(--neo-border)' }}>
        {[
          { id: 'signals', label: 'Signals' },
          { id: 'radar',   label: 'Radar' },
          { id: 'bar',     label: 'Bars' },
        ].map(t => (
          <button key={t.id} onClick={() => setTab(t.id)} style={{
            flex: 1, padding: '4px 0', fontSize: '0.63em', fontWeight: 600,
            background: tab === t.id ? 'rgba(139,92,246,0.10)' : 'transparent',
            color: tab === t.id ? '#8b5cf6' : 'var(--neo-text-muted)',
            border: 'none', borderBottom: tab === t.id ? '2px solid #8b5cf6' : '2px solid transparent',
            cursor: 'pointer',
          }}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div style={{ flex: 1, overflow: 'auto', padding: '6px 8px' }}>

        {/* Signals list */}
        {tab === 'signals' && (
          hasSignals
            ? ORDER.filter(k => hbScores[k] !== undefined).map(k => (
                <SignalRow key={k} sensorKey={k} value={hbScores[k]} events={hbEvents} />
              ))
            : (
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: 120, gap: 8 }}>
                <Eye size={16} style={{ color: '#475569' }} />
                <p style={{ fontSize: '0.7em', color: 'var(--neo-text-muted)', textAlign: 'center' }}>
                  Human behavior sensors are warming up.<br />
                  They populate as news, volume, and analyst data is ingested.
                </p>
              </div>
            )
        )}

        {/* Radar chart */}
        {tab === 'radar' && (
          <ResponsiveContainer width="100%" height={220}>
            <RadarChart data={radarData} margin={{ top: 8, right: 20, bottom: 8, left: 20 }}>
              <PolarGrid stroke="rgba(255,255,255,0.08)" />
              <PolarAngleAxis
                dataKey="subject"
                tick={{ fontSize: 8, fill: '#64748b' }}
              />
              <Radar
                name="Human Behavior"
                dataKey="value"
                stroke="#8b5cf6"
                fill="#8b5cf6"
                fillOpacity={0.25}
                strokeWidth={1.5}
              />
              <Tooltip
                formatter={(v, _, { payload }) => [
                  `${payload.raw >= 0 ? '+' : ''}${fmt(payload.raw, 3)}`,
                  payload.key,
                ]}
                contentStyle={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', fontSize: 10 }}
              />
            </RadarChart>
          </ResponsiveContainer>
        )}

        {/* Bar chart */}
        {tab === 'bar' && (
          <>
            <p style={{ fontSize: '0.6em', color: 'var(--neo-text-muted)', marginBottom: 6 }}>
              Each bar = weighted contribution to price prediction. Green = bullish, red = bearish.
            </p>
            <ResponsiveContainer width="100%" height={190}>
              <BarChart data={barData} layout="vertical" margin={{ top: 0, right: 8, left: 64, bottom: 0 }}>
                <XAxis type="number" tick={{ fontSize: 8, fill: '#64748b' }} domain={[-0.5, 0.5]} />
                <YAxis dataKey="name" type="category" tick={{ fontSize: 7.5, fill: '#94a3b8' }} width={64} />
                <ReferenceLine x={0} stroke="#475569" strokeWidth={1.5} />
                <Tooltip
                  formatter={(v) => [`${v >= 0 ? '+' : ''}${v}`, 'Score']}
                  contentStyle={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', fontSize: 10 }}
                />
                <Bar dataKey="val" name="Score" radius={[0, 3, 3, 0]}>
                  {barData.map((d, i) => (
                    <Cell key={i} fill={signalColor(d.val)} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </>
        )}

      </div>

      {/* Footer — event ticker */}
      {hbEvents.length > 0 && (
        <div style={{
          padding: '4px 10px', flexShrink: 0,
          borderTop: '1px solid var(--neo-border)',
          background: 'rgba(139,92,246,0.04)',
        }}>
          <p style={{ fontSize: '0.58em', color: 'var(--neo-text-muted)', margin: 0 }}>
            <span style={{ color: '#8b5cf6', fontWeight: 600 }}>{hbEvents.length} signals</span>
            {' · '}
            {hbEvents[0]?.label?.slice(0, 70)}
          </p>
        </div>
      )}
    </div>
  );
}

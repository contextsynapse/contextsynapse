/**
 * PredictionPanel.js
 *
 * Shows online-learning price predictions for a focused stock:
 *   - Current active prediction with countdown
 *   - Predicted vs actual line chart
 *   - Neural-network weight graph (SVG, sensors as input nodes)
 *   - Loss history spark-line
 *   - Direction accuracy badge
 *   - Sensor weight evolution bar chart
 */
import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, ReferenceLine, Cell,
} from 'recharts';
import { RefreshCw, Target, Brain, TrendingUp, TrendingDown, Minus, Zap } from 'lucide-react';
import axios from 'axios';

const api = axios.create({ baseURL: '', timeout: 15000 });

// ── colour helpers ─────────────────────────────────────────────
const signalColor = (v) =>
  v > 0.15 ? '#22c55e' : v > 0.02 ? '#86efac' :
  v < -0.15 ? '#ef4444' : v < -0.02 ? '#fca5a5' : '#6b7280';

const dirColor = (dir) =>
  dir === 'bullish' ? '#22c55e' : dir === 'bearish' ? '#ef4444' : '#6b7280';

function fmt(v, d = 2) { return Number(v || 0).toFixed(d); }
function fmtPrice(v) {
  return v > 0 ? `₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 })}` : '—';
}

// ── Countdown clock ────────────────────────────────────────────
function Countdown({ targetIso }) {
  const [secsLeft, setSecsLeft] = useState(0);
  useEffect(() => {
    const calc = () => {
      const diff = Math.floor((new Date(targetIso) - Date.now()) / 1000);
      setSecsLeft(diff);
    };
    calc();
    const id = setInterval(calc, 1000);
    return () => clearInterval(id);
  }, [targetIso]);

  if (secsLeft <= 0) return <span style={{ color: '#f59e0b', fontSize: '0.7em' }}>resolving…</span>;
  const m = Math.floor(secsLeft / 60);
  const s = secsLeft % 60;
  return (
    <span style={{ color: '#06b6d4', fontSize: '0.7em', fontVariantNumeric: 'tabular-nums' }}>
      {m}:{String(s).padStart(2, '0')} left
    </span>
  );
}

// ── Neural-network weight SVG ──────────────────────────────────
function NeuralNetSVG({ weights, predicted_delta_pct }) {
  const entries = Object.entries(weights || {})
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
    .slice(0, 10);  // show top 10 by magnitude

  const W = 260, H = Math.max(180, entries.length * 22 + 40);
  const inputX = 30, outputX = W - 30;
  const outputY = H / 2;
  const step = (H - 40) / Math.max(entries.length - 1, 1);
  const outColor = (predicted_delta_pct || 0) >= 0 ? '#22c55e' : '#ef4444';

  return (
    <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ overflow: 'visible' }}>
      {/* Output node */}
      <circle cx={outputX} cy={outputY} r={14} fill={`${outColor}22`} stroke={outColor} strokeWidth={2} />
      <text x={outputX} y={outputY + 4} textAnchor="middle" fill={outColor} fontSize={9} fontWeight="bold">
        {(predicted_delta_pct || 0) >= 0 ? '+' : ''}{fmt(predicted_delta_pct || 0, 2)}%
      </text>
      <text x={outputX} y={outputY + 24} textAnchor="middle" fill="#94a3b8" fontSize={7}>price Δ</text>

      {entries.map(([sensor, w], i) => {
        const y = 20 + i * step;
        const wc = signalColor(w);
        const strokeW = Math.max(0.5, Math.min(3.5, Math.abs(w) * 8));
        const label = sensor.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        return (
          <g key={sensor}>
            {/* Sensor node */}
            <circle cx={inputX} cy={y} r={8} fill={`${wc}22`} stroke={wc} strokeWidth={1.5} />
            <text x={inputX - 14} y={y + 4} textAnchor="end" fill="#94a3b8" fontSize={7}>{label}</text>
            <text x={inputX} y={y + 4} textAnchor="middle" fill={wc} fontSize={6} fontWeight="bold">
              {w >= 0 ? '+' : ''}{fmt(w, 2)}
            </text>
            {/* Connection to output */}
            <line
              x1={inputX + 8} y1={y}
              x2={outputX - 14} y2={outputY}
              stroke={wc} strokeWidth={strokeW} opacity={0.45}
              strokeDasharray={w < 0 ? '4 3' : 'none'}
            />
          </g>
        );
      })}
    </svg>
  );
}

// ── Custom tooltip for recharts ────────────────────────────────
function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{
      background: 'var(--neo-bg, #1a1a2e)', border: '1px solid var(--neo-border, #334155)',
      borderRadius: 6, padding: '4px 8px', fontSize: 10,
    }}>
      <p style={{ color: 'var(--neo-text-muted, #94a3b8)', marginBottom: 2 }}>{label}</p>
      {payload.map(p => (
        <p key={p.dataKey} style={{ color: p.color, margin: 0 }}>
          {p.name}: {fmtPrice(p.value)}
        </p>
      ))}
    </div>
  );
}

// ── Main PredictionPanel ───────────────────────────────────────
export default function PredictionPanel({ entity, horizon = 30, refreshKey }) {
  const [data, setData]       = useState(null);   // { prediction, learning_state, auto_resolved }
  const [history, setHistory] = useState(null);   // { history, learning_state }
  const [loading, setLoading] = useState(false);
  const [tab, setTab]         = useState('chart'); // chart | weights | network | loss
  const latestFetchRef        = useRef(0);

  const fetchAll = useCallback(async (ent) => {
    if (!ent) return;
    const fetchId = ++latestFetchRef.current;
    setLoading(true);
    try {
      const [predRes, histRes] = await Promise.allSettled([
        api.get(`/graph/fusion/${encodeURIComponent(ent)}/predict`, { params: { horizon } }),
        api.get(`/graph/fusion/${encodeURIComponent(ent)}/predictions`, { params: { limit: 60 } }),
      ]);
      if (fetchId !== latestFetchRef.current) return; // stale
      setData(predRes.status === 'fulfilled' ? predRes.value.data : null);
      setHistory(histRes.status === 'fulfilled' ? histRes.value.data : null);
    } finally {
      if (fetchId === latestFetchRef.current) setLoading(false);
    }
  }, [horizon]);

  useEffect(() => { fetchAll(entity); }, [fetchAll, entity, refreshKey]);

  if (!entity) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
        <p style={{ fontSize: '0.75em', color: 'var(--neo-text-muted)' }}>Select a stock to see predictions</p>
      </div>
    );
  }

  const pred   = data?.prediction;
  const state  = data?.learning_state || history?.learning_state || {};
  const hist   = history?.history || [];

  // Build chart data: resolved predictions only (have actual_price)
  const chartData = hist
    .filter(p => p.resolved && p.actual_price > 0)
    .slice(0, 20)
    .reverse()
    .map(p => ({
      label: new Date(p.made_at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }),
      predicted: p.predicted_price,
      actual:    p.actual_price,
      error:     parseFloat(fmt(p.error, 3)),
    }));

  const resolvedCount = hist.filter(p => p.resolved).length;
  const pendingCount  = hist.filter(p => !p.resolved).length;
  const accPct        = state.direction_accuracy || 0;
  const accColor      = accPct >= 65 ? '#22c55e' : accPct >= 50 ? '#f59e0b' : '#94a3b8';

  // Weight bar chart data
  const weightData = Object.entries(state.weights || {})
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
    .slice(0, 12)
    .map(([name, val]) => ({
      name: name.replace(/_/g, ' ').slice(0, 14),
      val: parseFloat(fmt(val, 3)),
    }));

  // Loss sparkline
  const lossData = (state.loss_history || []).map((v, i) => ({ i, v }));

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden', gap: 0 }}>

      {/* ── Header ── */}
      <div style={{
        padding: '6px 10px', flexShrink: 0,
        borderBottom: '1px solid var(--neo-border)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Brain size={13} style={{ color: '#8b5cf6' }} />
          <span style={{ fontSize: '0.78em', fontWeight: 700, color: 'var(--neo-text)' }}>
            {entity} · Prediction
          </span>
          <span style={{
            fontSize: '0.65em', padding: '1px 6px', borderRadius: 8,
            background: `${accColor}18`, color: accColor,
            border: `1px solid ${accColor}44`, fontWeight: 600,
          }}>
            {fmt(accPct, 1)}% dir acc
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {loading && <RefreshCw size={10} className="animate-spin" style={{ color: '#06b6d4' }} />}
          <button
            onClick={() => fetchAll(entity)}
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#06b6d4', padding: 2 }}
          >
            <RefreshCw size={11} />
          </button>
        </div>
      </div>

      {/* ── Active Prediction Card ── */}
      {pred && (
        <div style={{
          margin: '8px 10px 4px', borderRadius: 8, flexShrink: 0,
          background: 'var(--neo-card, rgba(139,92,246,0.06))',
          border: '1px solid rgba(139,92,246,0.25)', padding: '8px 12px',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div>
              <p style={{ fontSize: '0.65em', color: 'var(--neo-text-muted)', marginBottom: 3 }}>
                Current prediction · horizon {pred.horizon_minutes}min
              </p>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                <span style={{ fontSize: '1.4em', fontWeight: 800, color: 'var(--neo-text)' }}>
                  {fmtPrice(pred.predicted_price)}
                </span>
                <span style={{
                  fontSize: '0.85em', fontWeight: 700,
                  color: pred.predicted_change_pct >= 0 ? '#22c55e' : '#ef4444',
                }}>
                  {pred.predicted_change_pct >= 0 ? '+' : ''}{fmt(pred.predicted_change_pct, 3)}%
                </span>
              </div>
              <p style={{ fontSize: '0.65em', color: 'var(--neo-text-muted)', marginTop: 2 }}>
                from {fmtPrice(pred.current_price)} · <Countdown targetIso={pred.predict_at} />
              </p>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div style={{
                width: 48, height: 48, borderRadius: '50%',
                display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                border: `2px solid rgba(139,92,246,0.5)`,
                background: 'rgba(139,92,246,0.08)',
              }}>
                <span style={{ fontSize: '0.9em', fontWeight: 800, color: '#8b5cf6' }}>
                  {fmt(pred.confidence * 100, 0)}%
                </span>
                <span style={{ fontSize: '0.5em', color: '#8b5cf6' }}>conf</span>
              </div>
            </div>
          </div>

          {/* Mini sensor bar */}
          {pred.sensor_inputs && (
            <div style={{ marginTop: 6, display: 'flex', gap: 2, flexWrap: 'wrap' }}>
              {Object.entries(pred.sensor_inputs).slice(0, 8).map(([k, v]) => (
                <span key={k} style={{
                  fontSize: '0.55em', padding: '1px 4px', borderRadius: 4,
                  background: `${signalColor(v)}18`, color: signalColor(v),
                  border: `1px solid ${signalColor(v)}33`,
                }}>
                  {k.replace(/_/g, ' ').slice(0, 10)} {v >= 0 ? '+' : ''}{fmt(v, 2)}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Stats strip ── */}
      <div style={{
        padding: '4px 10px', flexShrink: 0,
        display: 'flex', gap: 16, alignItems: 'center',
        borderBottom: '1px solid var(--neo-border)',
      }}>
        {[
          { label: 'Updates', val: state.total_updates || 0, color: 'var(--neo-text)' },
          { label: 'Resolved', val: resolvedCount, color: '#22c55e' },
          { label: 'Pending',  val: pendingCount,  color: '#f59e0b' },
          { label: 'Avg Loss', val: state.loss_history?.length
            ? fmt(state.loss_history.slice(-10).reduce((a, b) => a + b, 0) / Math.min(10, state.loss_history.length), 4)
            : '—', color: '#94a3b8' },
        ].map(({ label, val, color }) => (
          <div key={label} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
            <span style={{ fontSize: '0.75em', fontWeight: 700, color }}>{val}</span>
            <span style={{ fontSize: '0.55em', color: 'var(--neo-text-muted)' }}>{label}</span>
          </div>
        ))}
      </div>

      {/* ── Tab bar ── */}
      <div style={{
        display: 'flex', gap: 0, flexShrink: 0,
        borderBottom: '1px solid var(--neo-border)',
      }}>
        {[
          { id: 'chart',   label: 'Pred vs Actual' },
          { id: 'weights', label: 'Weights' },
          { id: 'network', label: 'Network' },
          { id: 'loss',    label: 'Loss' },
        ].map(t => (
          <button key={t.id} onClick={() => setTab(t.id)} style={{
            flex: 1, padding: '5px 4px', fontSize: '0.65em', fontWeight: 600,
            background: tab === t.id ? 'rgba(139,92,246,0.12)' : 'transparent',
            color: tab === t.id ? '#8b5cf6' : 'var(--neo-text-muted)',
            border: 'none', borderBottom: tab === t.id ? '2px solid #8b5cf6' : '2px solid transparent',
            cursor: 'pointer', transition: 'all 0.15s',
          }}>
            {t.label}
          </button>
        ))}
      </div>

      {/* ── Tab content ── */}
      <div style={{ flex: 1, overflow: 'auto', padding: '8px 10px' }}>

        {/* Predicted vs Actual */}
        {tab === 'chart' && (
          <div style={{ height: '100%', minHeight: 160 }}>
            {chartData.length < 2 ? (
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 8 }}>
                <Zap size={18} style={{ color: '#f59e0b' }} />
                <p style={{ fontSize: '0.72em', color: 'var(--neo-text-muted)', textAlign: 'center' }}>
                  Predictions are learning…<br />Chart populates after {2 - chartData.length} more resolved prediction{chartData.length === 0 ? 's' : ''}.
                </p>
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                  <XAxis dataKey="label" tick={{ fontSize: 8, fill: '#64748b' }} />
                  <YAxis tick={{ fontSize: 8, fill: '#64748b' }} domain={['auto', 'auto']} />
                  <Tooltip content={<ChartTooltip />} />
                  <Line
                    type="monotone" dataKey="actual" name="Actual"
                    stroke="#22c55e" strokeWidth={2} dot={{ r: 3, fill: '#22c55e' }}
                    activeDot={{ r: 5 }}
                  />
                  <Line
                    type="monotone" dataKey="predicted" name="Predicted"
                    stroke="#8b5cf6" strokeWidth={1.5} strokeDasharray="5 3"
                    dot={{ r: 2, fill: '#8b5cf6' }} activeDot={{ r: 4 }}
                  />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        )}

        {/* Sensor Weight Bars */}
        {tab === 'weights' && (
          <div style={{ height: '100%', minHeight: 160 }}>
            {weightData.length === 0 ? (
              <p style={{ fontSize: '0.72em', color: 'var(--neo-text-muted)' }}>No weights yet.</p>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={weightData} layout="vertical" margin={{ top: 0, right: 10, left: 60, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" horizontal={false} />
                  <XAxis type="number" tick={{ fontSize: 8, fill: '#64748b' }} domain={[-0.5, 0.5]} />
                  <YAxis dataKey="name" type="category" tick={{ fontSize: 7.5, fill: '#94a3b8' }} width={60} />
                  <ReferenceLine x={0} stroke="#475569" strokeWidth={1.5} />
                  <Tooltip
                    formatter={(v) => [`${v >= 0 ? '+' : ''}${v}`, 'Weight']}
                    contentStyle={{ background: 'var(--neo-bg, #1a1a2e)', border: '1px solid var(--neo-border)', fontSize: 10 }}
                  />
                  <Bar dataKey="val" name="Weight" radius={[0, 3, 3, 0]}>
                    {weightData.map((entry, i) => (
                      <Cell key={i} fill={signalColor(entry.val)} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        )}

        {/* Neural Network SVG */}
        {tab === 'network' && (
          <div style={{ overflowY: 'auto', paddingBottom: 8 }}>
            {pred ? (
              <>
                <p style={{ fontSize: '0.62em', color: 'var(--neo-text-muted)', marginBottom: 6 }}>
                  Top 10 sensors by weight magnitude. Dashed = inverse signal. Line thickness = |weight|.
                </p>
                <NeuralNetSVG weights={pred.weights} predicted_delta_pct={pred.predicted_change_pct} />
              </>
            ) : (
              <p style={{ fontSize: '0.72em', color: 'var(--neo-text-muted)' }}>Generate a prediction first.</p>
            )}
          </div>
        )}

        {/* Loss history */}
        {tab === 'loss' && (
          <div style={{ height: '100%', minHeight: 160 }}>
            {lossData.length < 2 ? (
              <p style={{ fontSize: '0.72em', color: 'var(--neo-text-muted)' }}>Loss data populates after a few updates.</p>
            ) : (
              <>
                <p style={{ fontSize: '0.62em', color: 'var(--neo-text-muted)', marginBottom: 6 }}>
                  MSE loss per update — lower = better. Trend should decrease as weights converge.
                </p>
                <ResponsiveContainer width="100%" height={140}>
                  <LineChart data={lossData} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                    <XAxis dataKey="i" tick={{ fontSize: 7, fill: '#64748b' }} label={{ value: 'update #', position: 'insideBottomRight', offset: 0, fontSize: 7 }} />
                    <YAxis tick={{ fontSize: 7, fill: '#64748b' }} />
                    <Tooltip
                      formatter={(v) => [fmt(v, 5), 'Loss']}
                      contentStyle={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', fontSize: 10 }}
                    />
                    <Line type="monotone" dataKey="v" name="Loss" stroke="#f59e0b" strokeWidth={1.5} dot={false} />
                  </LineChart>
                </ResponsiveContainer>

                {/* Accuracy history sparkline */}
                {(state.accuracy_history || []).length >= 5 && (
                  <>
                    <p style={{ fontSize: '0.62em', color: 'var(--neo-text-muted)', margin: '10px 0 4px' }}>
                      Direction accuracy (last {state.accuracy_history.length} updates) — 1 = correct, 0 = wrong
                    </p>
                    <ResponsiveContainer width="100%" height={70}>
                      <BarChart data={(state.accuracy_history || []).map((v, i) => ({ i, v }))} margin={{ top: 0, right: 4, left: -20, bottom: 0 }}>
                        <XAxis dataKey="i" hide />
                        <YAxis hide domain={[0, 1]} />
                        <Bar dataKey="v" name="Correct">
                          {(state.accuracy_history || []).map((v, i) => (
                            <Cell key={i} fill={v ? '#22c55e' : '#ef4444'} />
                          ))}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  </>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

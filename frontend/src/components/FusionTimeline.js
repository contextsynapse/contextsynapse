/**
 * FusionTimeline — Time-series chart with price line + news events + sentiment overlay.
 *
 * Shows multiple data layers on a single timeline:
 * - Price line (from price context)
 * - News event markers (from news context)
 * - Sentiment color bands
 * - Volume bars
 * - Macro event annotations
 */

import React, { useState, useEffect, useCallback } from 'react';
import {
  ComposedChart, Line, Bar, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, ReferenceDot, Legend,
} from 'recharts';
import { Loader2, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import api from '../lib/api';
import { toast } from 'react-hot-toast';

const SENTIMENT_COLORS = {
  positive: '#10b981',
  negative: '#ef4444',
  mixed: '#f59e0b',
  neutral: '#6b7280',
};

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null;

  return (
    <div className="p-2 rounded-lg text-xs" style={{ background: '#1f2937', border: '1px solid #374151', color: '#d1d5db' }}>
      <p className="font-semibold mb-1" style={{ color: '#ec4899' }}>{label}</p>
      {payload.map((p, i) => (
        <p key={i} style={{ color: p.color }}>
          {p.name}: {typeof p.value === 'number' ? p.value.toLocaleString() : p.value}
        </p>
      ))}
    </div>
  );
}

function NewsMarker({ cx, cy, event }) {
  if (!cx || !cy) return null;
  const color = SENTIMENT_COLORS[event?.sentiment] || '#6b7280';
  return (
    <g>
      <circle cx={cx} cy={cy - 15} r={6} fill={color} stroke="#fff" strokeWidth={1.5} style={{ cursor: 'pointer' }} />
      <text x={cx} y={cy - 15} textAnchor="middle" fill="#fff" fontSize={8} dy={3}>!</text>
    </g>
  );
}

export default function FusionTimeline({ contexts = [], focusEntity = '' }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [selectedEvent, setSelectedEvent] = useState(null);

  const loadData = useCallback(async () => {
    if (!contexts.length) return;
    setLoading(true);

    try {
      // Fetch data from all selected contexts
      const allNodes = {};
      for (const ctx of contexts) {
        try {
          const res = await api.get('/graph/nodes', { params: { graph: ctx, limit: 500 } });
          allNodes[ctx] = res.data.nodes || [];
        } catch (_) {}
      }

      // Extract price data (Indicator nodes with timestamps)
      const pricePoints = [];
      const newsEvents = [];
      const macroEvents = [];

      for (const [ctx, nodes] of Object.entries(allNodes)) {
        for (const n of nodes) {
          const p = n.properties || {};
          const label = n.label;

          if (label === 'Indicator' && p.value) {
            pricePoints.push({
              timestamp: p._published_at || p._event_date || p._created_at || '',
              price: parseFloat(p.value) || 0,
              volume: parseInt(p._price_volume) || 0,
              change_pct: parseFloat(p._price_change_pct) || 0,
              entity: p.entity || ctx,
              context: ctx,
              open: parseFloat(p._price_open) || 0,
              high: parseFloat(p._price_high) || 0,
              low: parseFloat(p._price_low) || 0,
            });
          } else if (label === 'Fact') {
            const isNews = ctx.includes('price') ? false : true;
            if (isNews) {
              newsEvents.push({
                timestamp: p._event_date || p._published_at || p._created_at || '',
                statement: p.statement || p.name || '',
                sentiment: p._sentiment || '',
                context: ctx,
                confidence: p.confidence || 0,
              });
            }
          } else if (label === 'Entity' && p._sentiment) {
            // Entities from macro/sector contexts are macro events
            if (ctx.includes('economy') || ctx.includes('macro') || ctx.includes('sector')) {
              macroEvents.push({
                timestamp: p._event_date || p._created_at || '',
                name: p.name || '',
                sentiment: p._sentiment || '',
                context: ctx,
              });
            }
          }
        }
      }

      // Sort by timestamp
      pricePoints.sort((a, b) => a.timestamp.localeCompare(b.timestamp));
      newsEvents.sort((a, b) => a.timestamp.localeCompare(b.timestamp));

      // Build chart data — merge price + news on timeline
      const chartData = pricePoints.map(pp => ({
        time: pp.timestamp ? formatTime(pp.timestamp) : '',
        fullTime: pp.timestamp,
        price: pp.price,
        volume: pp.volume,
        change: pp.change_pct,
        entity: pp.entity,
        open: pp.open,
        high: pp.high,
        low: pp.low,
        // Sentiment band: find closest news event
        sentiment: findClosestSentiment(pp.timestamp, newsEvents),
        sentimentValue: sentimentToValue(findClosestSentiment(pp.timestamp, newsEvents)),
      }));

      setData({
        chartData,
        pricePoints,
        newsEvents,
        macroEvents,
        entity: focusEntity || (pricePoints[0]?.entity || ''),
        minPrice: Math.min(...pricePoints.map(p => p.low || p.price).filter(p => p > 0)) * 0.99,
        maxPrice: Math.max(...pricePoints.map(p => p.high || p.price).filter(p => p > 0)) * 1.01,
        maxVolume: Math.max(...pricePoints.map(p => p.volume).filter(v => v > 0)),
      });

    } catch (e) {
      console.error('Timeline load error:', e);
    }
    setLoading(false);
  }, [contexts, focusEntity]);

  useEffect(() => { loadData(); }, [loadData]);

  if (loading) return <div className="flex items-center justify-center py-8"><Loader2 size={20} className="animate-spin" style={{ color: '#ec4899' }} /></div>;
  if (!data || (!data.chartData.length && !data.newsEvents.length && !data.macroEvents.length)) {
    return <div className="text-center py-8 text-xs" style={{ color: 'var(--neo-text-muted)' }}>No data available. Select contexts with data to see the timeline.</div>;
  }

  const lastPrice = data.chartData[data.chartData.length - 1]?.price || 0;
  const firstPrice = data.chartData[0]?.price || 0;
  const totalChange = firstPrice > 0 ? ((lastPrice - firstPrice) / firstPrice * 100) : 0;

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-sm font-bold" style={{ color: 'var(--neo-text)' }}>{data.entity || contexts.join(' + ')}</span>
          {lastPrice > 0 && (
            <>
              <span className="text-lg font-bold font-mono" style={{ color: 'var(--neo-text)' }}>
                {lastPrice.toLocaleString(undefined, { minimumFractionDigits: 2 })}
              </span>
              <span className="text-xs font-medium flex items-center gap-0.5" style={{ color: totalChange >= 0 ? '#10b981' : '#ef4444' }}>
                {totalChange >= 0 ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                {totalChange >= 0 ? '+' : ''}{totalChange.toFixed(2)}%
              </span>
            </>
          )}
        </div>
        <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
          {data.pricePoints.length > 0 ? `${data.pricePoints.length} prices | ` : ''}{data.newsEvents.length} news | {data.macroEvents.length} macro
        </span>
      </div>

      {/* Price chart — only if price data exists */}
      {data.chartData.length > 0 && (
      <div style={{ background: '#0a0a1a', borderRadius: 8, padding: '12px 8px', border: '1px solid var(--neo-border)' }}>
        <ResponsiveContainer width="100%" height={300}>
          <ComposedChart data={data.chartData} margin={{ top: 20, right: 20, bottom: 5, left: 10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis dataKey="time" tick={{ fill: '#6b7280', fontSize: 10 }} stroke="#374151" />
            <YAxis yAxisId="price" domain={[data.minPrice, data.maxPrice]} tick={{ fill: '#6b7280', fontSize: 10 }} stroke="#374151"
              tickFormatter={(v) => v > 1000 ? `${(v/1000).toFixed(1)}K` : v.toFixed(0)} />
            <YAxis yAxisId="volume" orientation="right" domain={[0, data.maxVolume * 3]} tick={false} stroke="transparent" />
            <Tooltip content={<CustomTooltip />} />

            {/* Volume bars */}
            <Bar yAxisId="volume" dataKey="volume" fill="#3b82f620" barSize={8} name="Volume" />

            {/* Sentiment band */}
            <Area yAxisId="price" dataKey="price" fill="transparent" stroke="transparent"
              dot={(props) => {
                const { cx, cy, payload } = props;
                if (!payload?.sentiment || payload.sentiment === 'neutral') return null;
                const color = SENTIMENT_COLORS[payload.sentiment] || '#6b7280';
                return <circle cx={cx} cy={cy} r={3} fill={color} fillOpacity={0.4} stroke="none" />;
              }}
            />

            {/* Price line */}
            <Line yAxisId="price" type="monotone" dataKey="price" stroke="#ec4899" strokeWidth={2}
              dot={false} activeDot={{ r: 4, fill: '#ec4899', stroke: '#fff', strokeWidth: 2 }}
              name="Price" />

            {/* News event markers */}
            {data.newsEvents.slice(0, 10).map((event, i) => {
              const matchingPoint = data.chartData.find(d => d.fullTime && event.timestamp && d.fullTime.substring(0, 10) === event.timestamp.substring(0, 10));
              if (!matchingPoint) return null;
              return (
                <ReferenceDot key={i} yAxisId="price" x={matchingPoint.time} y={matchingPoint.price}
                  r={0} label="" >
                </ReferenceDot>
              );
            })}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      )}

      {/* News events timeline */}
      {data.newsEvents.length > 0 && (
        <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg-secondary)' }}>
          <h4 className="text-[10px] font-bold uppercase mb-2" style={{ color: 'var(--neo-text-muted)' }}>
            News Events ({data.newsEvents.length})
          </h4>
          <div className="space-y-1 max-h-40 overflow-y-auto">
            {data.newsEvents.map((event, i) => (
              <div key={i} className="flex items-start gap-2 text-xs py-1" style={{ borderLeft: `3px solid ${SENTIMENT_COLORS[event.sentiment] || '#6b7280'}`, paddingLeft: 8 }}>
                <span className="text-[9px] flex-shrink-0 w-16" style={{ color: 'var(--neo-text-muted)' }}>
                  {event.timestamp ? formatTime(event.timestamp) : '?'}
                </span>
                <span style={{ color: 'var(--neo-text)' }}>{event.statement.substring(0, 70)}</span>
                <span className="text-[9px] flex-shrink-0 px-1 rounded" style={{
                  background: `${SENTIMENT_COLORS[event.sentiment]}20`,
                  color: SENTIMENT_COLORS[event.sentiment]
                }}>{event.sentiment}</span>
                <span className="text-[9px] flex-shrink-0" style={{ color: 'var(--neo-text-muted)' }}>{event.context}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Macro events */}
      {data.macroEvents.length > 0 && (
        <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg-secondary)' }}>
          <h4 className="text-[10px] font-bold uppercase mb-2" style={{ color: 'var(--neo-text-muted)' }}>
            Macro Signals ({data.macroEvents.length})
          </h4>
          <div className="flex gap-1.5 flex-wrap">
            {data.macroEvents.slice(0, 15).map((event, i) => (
              <span key={i} className="text-[9px] px-2 py-1 rounded" style={{
                background: `${SENTIMENT_COLORS[event.sentiment]}15`,
                color: SENTIMENT_COLORS[event.sentiment],
                border: `1px solid ${SENTIMENT_COLORS[event.sentiment]}30`,
              }}>
                {event.name.substring(0, 25)} ({event.context.replace(/_/g, ' ')})
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// Helpers

function formatTime(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts.substring(0, 16).replace('T', ' ');
    const mon = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getMonth()];
    const day = d.getDate();
    const hr = d.getHours();
    const min = String(d.getMinutes()).padStart(2, '0');
    const ampm = hr >= 12 ? 'PM' : 'AM';
    const hr12 = hr % 12 || 12;
    return `${mon} ${day}, ${hr12}:${min} ${ampm}`;
  } catch { return ts.substring(0, 16).replace('T', ' '); }
}

function findClosestSentiment(timestamp, newsEvents) {
  if (!timestamp || !newsEvents.length) return 'neutral';
  const ts = timestamp.substring(0, 10);
  const match = newsEvents.find(e => e.timestamp?.substring(0, 10) === ts);
  return match?.sentiment || 'neutral';
}

function sentimentToValue(sentiment) {
  return { positive: 1, negative: -1, mixed: 0, neutral: 0 }[sentiment] || 0;
}

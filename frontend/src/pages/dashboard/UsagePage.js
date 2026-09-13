import React, { useState, useEffect } from 'react';
import { BarChart3, Loader2 } from 'lucide-react';
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts';
import api from '../../lib/api';

export default function UsagePage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get('/dashboard/usage')
      .then(res => setData(res.data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      </div>
    );
  }

  const summary = data?.summary || {};
  const daily = data?.daily || [];
  const totalCalls = Object.values(summary).reduce((a, b) => a + b, 0);

  return (
    <div className="max-w-5xl">
      <h1 className="text-xl font-bold mb-1" style={{ color: 'var(--neo-text)' }}>Usage</h1>
      <p className="text-sm mb-6" style={{ color: 'var(--neo-text-muted)' }}>
        Current billing period
      </p>

      {/* Summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
        <SummaryCard label="Total Events" value={totalCalls} color="var(--neo-blue)" />
        <SummaryCard label="API Calls" value={summary.api_call || 0} color="var(--neo-cyan)" />
        <SummaryCard label="Queries" value={summary.query || 0} color="var(--neo-green)" />
        <SummaryCard label="Nodes Created" value={summary.node_create || 0} color="var(--neo-yellow)" />
      </div>

      {/* Chart */}
      <div
        className="rounded-xl p-5 mb-8"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
      >
        <div className="flex items-center gap-2 mb-4">
          <BarChart3 size={16} style={{ color: 'var(--neo-cyan)' }} />
          <h2 className="text-sm font-semibold" style={{ color: 'var(--neo-text)' }}>
            Daily Activity
          </h2>
        </div>

        {daily.length > 0 ? (
          <ResponsiveContainer width="100%" height={260}>
            <AreaChart data={daily}>
              <defs>
                <linearGradient id="colorCalls" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--neo-blue)" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="var(--neo-blue)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--neo-border)" />
              <XAxis
                dataKey="date"
                tick={{ fill: 'var(--neo-text-muted)', fontSize: 11 }}
                tickFormatter={d => d.slice(5)}
              />
              <YAxis tick={{ fill: 'var(--neo-text-muted)', fontSize: 11 }} />
              <Tooltip
                contentStyle={{
                  background: 'var(--neo-surface)',
                  border: '1px solid var(--neo-border)',
                  borderRadius: 8,
                  color: 'var(--neo-text)',
                  fontSize: 12,
                }}
              />
              <Area
                type="monotone"
                dataKey="api_call"
                name="API Calls"
                stroke="var(--neo-blue)"
                fill="url(#colorCalls)"
                strokeWidth={2}
              />
              <Area
                type="monotone"
                dataKey="query"
                name="Queries"
                stroke="var(--neo-green)"
                fill="none"
                strokeWidth={2}
              />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex items-center justify-center h-40 text-sm" style={{ color: 'var(--neo-text-muted)' }}>
            No usage data yet
          </div>
        )}
      </div>

      {/* Breakdown table */}
      {Object.keys(summary).length > 0 && (
        <div
          className="rounded-xl overflow-hidden"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        >
          <table className="w-full text-sm">
            <thead>
              <tr style={{ borderBottom: '1px solid var(--neo-border)' }}>
                <th className="text-left px-5 py-3 text-xs font-semibold" style={{ color: 'var(--neo-text-muted)' }}>
                  Event Type
                </th>
                <th className="text-right px-5 py-3 text-xs font-semibold" style={{ color: 'var(--neo-text-muted)' }}>
                  Count
                </th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(summary)
                .sort(([, a], [, b]) => b - a)
                .map(([type, count]) => (
                  <tr key={type} style={{ borderBottom: '1px solid var(--neo-border)' }}>
                    <td className="px-5 py-3" style={{ color: 'var(--neo-text)' }}>
                      {type.replace(/_/g, ' ')}
                    </td>
                    <td className="px-5 py-3 text-right font-mono" style={{ color: 'var(--neo-text)' }}>
                      {count.toLocaleString()}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SummaryCard({ label, value, color }) {
  return (
    <div
      className="rounded-xl p-4"
      style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
    >
      <div className="text-2xl font-bold" style={{ color }}>{value.toLocaleString()}</div>
      <div className="text-xs mt-1" style={{ color: 'var(--neo-text-muted)' }}>{label}</div>
    </div>
  );
}

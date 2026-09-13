import React from 'react';

export default function UsageBar({ label, used, limit, color = 'var(--neo-blue)' }) {
  const isUnlimited = limit === -1;
  const pct = isUnlimited ? 0 : limit > 0 ? Math.min((used / limit) * 100, 100) : 0;
  const isOver = !isUnlimited && pct >= 90;

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-medium" style={{ color: 'var(--neo-text-muted)' }}>{label}</span>
        <span className="text-xs font-mono" style={{ color: isOver ? 'var(--neo-red)' : 'var(--neo-text-muted)' }}>
          {used.toLocaleString()} / {isUnlimited ? 'Unlimited' : limit.toLocaleString()}
        </span>
      </div>
      <div className="h-2 rounded-full overflow-hidden" style={{ background: 'var(--neo-bg)' }}>
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{
            width: isUnlimited ? '0%' : `${pct}%`,
            background: isOver ? 'var(--neo-red)' : color,
          }}
        />
      </div>
    </div>
  );
}

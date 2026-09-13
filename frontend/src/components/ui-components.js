/**
 * PMS Design System — Shared UI Components
 * Import: import { PageHeader, MetricCard, GlassCard, ... } from '../components/ui-components';
 */
import React, { useState, useRef, useEffect } from 'react';
import { X, ChevronDown, ChevronUp, Search, Download, ArrowUpRight, ArrowDownRight } from 'lucide-react';

// ── Page Header ──────────────────────────────────────────────
export function PageHeader({ icon: Icon, title, subtitle, children }) {
  return (
    <div className="pms-page-header">
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {Icon && <Icon size={20} style={{ color: 'var(--neo-blue)' }} />}
          <h1 className="pms-page-title">{title}</h1>
        </div>
        {subtitle && <p className="pms-page-subtitle">{subtitle}</p>}
      </div>
      {children && <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>{children}</div>}
    </div>
  );
}

// ── Glass Card ───────────────────────────────────────────────
export function GlassCard({ children, className = '', style = {}, hover = true, glow, padding = '20px', onClick }) {
  const glowClass = glow === 'green' ? 'pms-glow-green' : glow === 'red' ? 'pms-glow-red' : glow === 'blue' ? 'pms-glow-blue' : '';
  return (
    <div
      className={`${hover ? 'pms-glass' : 'pms-glass-static'} ${glowClass} ${className}`}
      style={{ padding, ...style }}
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
    >
      {children}
    </div>
  );
}

// ── Metric Card ──────────────────────────────────────────────
export function MetricCard({ icon: Icon, label, value, change, prefix = '', suffix = '', color, loading }) {
  const isPositive = typeof change === 'number' ? change >= 0 : null;
  const changeColor = isPositive === null ? 'var(--neo-text-muted)' : isPositive ? 'var(--neo-green)' : 'var(--neo-red)';

  if (loading) {
    return (
      <GlassCard hover={false}>
        <div className="pms-skeleton" style={{ width: 80, height: 12, marginBottom: 12 }} />
        <div className="pms-skeleton" style={{ width: 120, height: 28, marginBottom: 8 }} />
        <div className="pms-skeleton" style={{ width: 60, height: 12 }} />
      </GlassCard>
    );
  }

  return (
    <GlassCard hover={false} glow={isPositive === true ? 'green' : isPositive === false ? 'red' : undefined}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--neo-text-muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
          {label}
        </span>
        {Icon && <Icon size={16} style={{ color: color || 'var(--neo-text-dim)' }} />}
      </div>
      <div style={{ fontSize: 26, fontWeight: 700, color: color || 'var(--neo-text)', letterSpacing: '-0.02em' }} className="pms-number">
        {prefix}{typeof value === 'number' ? value.toLocaleString('en-IN') : value}{suffix}
      </div>
      {change != null && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 6 }}>
          {isPositive ? <ArrowUpRight size={13} style={{ color: changeColor }} /> : <ArrowDownRight size={13} style={{ color: changeColor }} />}
          <span style={{ fontSize: 12, fontWeight: 600, color: changeColor }} className="pms-number">
            {isPositive ? '+' : ''}{typeof change === 'number' ? change.toFixed(2) : change}%
          </span>
        </div>
      )}
    </GlassCard>
  );
}

// ── Status Badge ─────────────────────────────────────────────
const BADGE_VARIANTS = {
  success: 'pms-badge-green', buy: 'pms-badge-green', approved: 'pms-badge-green', active: 'pms-badge-green', bullish: 'pms-badge-green',
  danger: 'pms-badge-red', sell: 'pms-badge-red', rejected: 'pms-badge-red', critical: 'pms-badge-red', bearish: 'pms-badge-red', block: 'pms-badge-red',
  warning: 'pms-badge-yellow', proposed: 'pms-badge-yellow', pending: 'pms-badge-yellow', warn: 'pms-badge-yellow', medium: 'pms-badge-yellow',
  info: 'pms-badge-blue', executed: 'pms-badge-blue', low: 'pms-badge-blue',
  purple: 'pms-badge-purple', neutral: 'pms-badge-gray', default: 'pms-badge-gray',
};

export function StatusBadge({ status, variant, dot, children }) {
  const v = variant || status?.toLowerCase() || 'default';
  const cls = BADGE_VARIANTS[v] || BADGE_VARIANTS.default;
  return (
    <span className={`pms-badge ${cls}`}>
      {dot && <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'currentColor' }} />}
      {children || status}
    </span>
  );
}

// ── Data Table ───────────────────────────────────────────────
export function DataTable({ columns, data, onRowClick, emptyMessage = 'No data', loading, sortable = true }) {
  const [sortKey, setSortKey] = useState(null);
  const [sortDir, setSortDir] = useState('asc');
  const [filter, setFilter] = useState('');

  const handleSort = (key) => {
    if (!sortable) return;
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir('asc');
    }
  };

  let rows = data || [];

  // Filter
  if (filter) {
    const q = filter.toLowerCase();
    rows = rows.filter(row =>
      columns.some(col => {
        const val = typeof col.accessor === 'function' ? col.accessor(row) : row[col.accessor];
        return String(val || '').toLowerCase().includes(q);
      })
    );
  }

  // Sort
  if (sortKey && sortable) {
    const col = columns.find(c => (typeof c.accessor === 'string' ? c.accessor : c.key) === sortKey);
    if (col) {
      rows = [...rows].sort((a, b) => {
        const av = typeof col.accessor === 'function' ? col.accessor(a) : a[col.accessor];
        const bv = typeof col.accessor === 'function' ? col.accessor(b) : b[col.accessor];
        if (av == null) return 1;
        if (bv == null) return -1;
        const cmp = typeof av === 'number' ? av - bv : String(av).localeCompare(String(bv));
        return sortDir === 'asc' ? cmp : -cmp;
      });
    }
  }

  if (loading) {
    return (
      <div className="pms-glass-static" style={{ padding: 0, overflow: 'hidden' }}>
        {[...Array(5)].map((_, i) => (
          <div key={i} style={{ display: 'flex', gap: 16, padding: '12px 14px', borderBottom: '1px solid rgba(255,255,255,0.03)' }}>
            {columns.map((_, j) => (
              <div key={j} className="pms-skeleton" style={{ height: 14, flex: 1 }} />
            ))}
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="pms-glass-static" style={{ padding: 0, overflow: 'hidden' }}>
      {/* Search bar */}
      {data && data.length > 5 && (
        <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--neo-border)', display: 'flex', alignItems: 'center', gap: 8 }}>
          <Search size={14} style={{ color: 'var(--neo-text-dim)' }} />
          <input
            className="pms-input"
            placeholder="Filter..."
            value={filter}
            onChange={e => setFilter(e.target.value)}
            style={{ border: 'none', background: 'transparent', padding: 0, fontSize: 12 }}
          />
          <span className="pms-hint">{rows.length} rows</span>
        </div>
      )}

      <div style={{ overflowX: 'auto' }}>
        <table className="pms-table">
          <thead>
            <tr>
              {columns.map((col) => {
                const key = typeof col.accessor === 'string' ? col.accessor : col.key;
                const isActive = sortKey === key;
                return (
                  <th
                    key={key}
                    onClick={() => handleSort(key)}
                    style={{
                      cursor: sortable ? 'pointer' : 'default',
                      userSelect: 'none',
                      width: col.width,
                      textAlign: col.align || 'left',
                    }}
                  >
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                      {col.header}
                      {sortable && isActive && (sortDir === 'asc' ? <ChevronUp size={11} /> : <ChevronDown size={11} />)}
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={columns.length} style={{ textAlign: 'center', padding: 32, color: 'var(--neo-text-dim)' }}>
                  {emptyMessage}
                </td>
              </tr>
            ) : (
              rows.map((row, i) => (
                <tr
                  key={row.id || row._id || i}
                  onClick={() => onRowClick?.(row)}
                  style={{ cursor: onRowClick ? 'pointer' : 'default' }}
                >
                  {columns.map((col) => {
                    const key = typeof col.accessor === 'string' ? col.accessor : col.key;
                    const val = typeof col.accessor === 'function' ? col.accessor(row) : row[col.accessor];
                    return (
                      <td key={key} style={{ textAlign: col.align || 'left' }}>
                        {col.render ? col.render(val, row) : val}
                      </td>
                    );
                  })}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Skeleton ─────────────────────────────────────────────────
export function Skeleton({ width, height = 16, rounded = false, style = {} }) {
  return (
    <div
      className="pms-skeleton"
      style={{
        width: width || '100%',
        height,
        borderRadius: rounded ? '50%' : 'var(--pms-radius-sm)',
        ...style,
      }}
    />
  );
}

// ── SlideOver Panel ──────────────────────────────────────────
export function SlideOver({ open, onClose, title, subtitle, width = 480, children }) {
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      <div className="pms-slide-overlay" onClick={onClose} />
      <div className="pms-slide-over" ref={ref} style={{ width }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 20px', borderBottom: '1px solid var(--neo-border)' }}>
          <div>
            <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--neo-text)' }}>{title}</div>
            {subtitle && <div style={{ fontSize: 12, color: 'var(--neo-text-muted)', marginTop: 2 }}>{subtitle}</div>}
          </div>
          <button onClick={onClose} className="pms-btn pms-btn-ghost pms-btn-sm" style={{ padding: 6 }}>
            <X size={16} />
          </button>
        </div>
        {/* Body */}
        <div style={{ padding: 20 }}>{children}</div>
      </div>
    </>
  );
}

// ── Empty State ──────────────────────────────────────────────
export function EmptyState({ icon: Icon, title, description, action, onAction }) {
  return (
    <div style={{ textAlign: 'center', padding: '48px 24px' }}>
      {Icon && (
        <div style={{
          width: 56, height: 56, borderRadius: 'var(--pms-radius-lg)',
          background: 'var(--neo-surface)', display: 'flex', alignItems: 'center', justifyContent: 'center',
          margin: '0 auto 16px',
        }}>
          <Icon size={24} style={{ color: 'var(--neo-text-dim)' }} />
        </div>
      )}
      <h3 style={{ fontSize: 16, fontWeight: 600, color: 'var(--neo-text)', margin: '0 0 6px' }}>{title}</h3>
      {description && <p style={{ fontSize: 13, color: 'var(--neo-text-muted)', margin: '0 0 20px' }}>{description}</p>}
      {action && (
        <button className="pms-btn pms-btn-primary" onClick={onAction}>
          {action}
        </button>
      )}
    </div>
  );
}

// ── Tabs ─────────────────────────────────────────────────────
export function Tabs({ tabs, active, onChange }) {
  return (
    <div className="pms-tabs">
      {tabs.map(tab => (
        <button
          key={tab.key}
          className={`pms-tab ${active === tab.key ? 'pms-tab-active' : ''}`}
          onClick={() => onChange(tab.key)}
        >
          {tab.icon && <tab.icon size={14} style={{ marginRight: 5, verticalAlign: 'middle' }} />}
          {tab.label}
          {tab.count != null && (
            <span style={{ marginLeft: 6, fontSize: 10, fontWeight: 700, padding: '1px 6px', borderRadius: 100, background: 'rgba(255,255,255,0.06)' }}>
              {tab.count}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}

// ── KPI Row (horizontal metrics strip) ───────────────────────
export function KPIStrip({ items, loading }) {
  if (loading) {
    return (
      <div style={{ display: 'flex', gap: 16 }}>
        {[1, 2, 3, 4].map(i => (
          <div key={i} className="pms-glass-static" style={{ flex: 1, padding: 16 }}>
            <Skeleton width={60} height={10} style={{ marginBottom: 8 }} />
            <Skeleton width={100} height={24} />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="pms-grid-4 pms-stagger">
      {items.map((item, i) => (
        <MetricCard key={item.label || i} {...item} />
      ))}
    </div>
  );
}

// ── Section Header ───────────────────────────────────────────
export function SectionHeader({ title, action, onAction, children }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12, marginTop: 24 }}>
      <h2 style={{ fontSize: 15, fontWeight: 600, color: 'var(--neo-text)', margin: 0 }}>{title}</h2>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {children}
        {action && (
          <button className="pms-btn pms-btn-ghost pms-btn-sm" onClick={onAction}>
            {action}
          </button>
        )}
      </div>
    </div>
  );
}

// ── Progress Bar ─────────────────────────────────────────────
export function ProgressBar({ value, max = 100, color = 'var(--neo-blue)', height = 6, label }) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100));
  return (
    <div>
      {label && (
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
          <span style={{ fontSize: 11, color: 'var(--neo-text-muted)' }}>{label}</span>
          <span style={{ fontSize: 11, color: 'var(--neo-text-dim)' }} className="pms-number">{pct.toFixed(0)}%</span>
        </div>
      )}
      <div style={{ height, background: 'var(--neo-surface)', borderRadius: height, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: height, transition: 'width 0.5s ease' }} />
      </div>
    </div>
  );
}

// ── Form Field ───────────────────────────────────────────────
export function FormField({ label, required, error, hint, children }) {
  return (
    <div style={{ marginBottom: 16 }}>
      {label && (
        <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--neo-text-muted)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
          {label} {required && <span style={{ color: 'var(--neo-red)' }}>*</span>}
        </label>
      )}
      {children}
      {error && <div style={{ fontSize: 11, color: 'var(--neo-red)', marginTop: 4 }}>{error}</div>}
      {hint && !error && <div style={{ fontSize: 11, color: 'var(--neo-text-dim)', marginTop: 4 }}>{hint}</div>}
    </div>
  );
}

// ── Confirmation Dialog ──────────────────────────────────────
export function ConfirmDialog({ open, title, message, confirmLabel = 'Confirm', danger = false, onConfirm, onCancel }) {
  if (!open) return null;
  return (
    <>
      <div className="pms-slide-overlay" onClick={onCancel} style={{ zIndex: 60 }} />
      <div style={{
        position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
        zIndex: 61, background: 'var(--neo-bg-secondary)', border: '1px solid var(--neo-border)',
        borderRadius: 'var(--pms-radius-lg)', padding: 24, width: 400, maxWidth: '90vw',
        boxShadow: 'var(--pms-shadow-lg)',
      }}>
        <h3 style={{ fontSize: 16, fontWeight: 700, color: 'var(--neo-text)', margin: '0 0 8px' }}>{title}</h3>
        <p style={{ fontSize: 13, color: 'var(--neo-text-muted)', margin: '0 0 20px' }}>{message}</p>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button className="pms-btn pms-btn-ghost" onClick={onCancel}>Cancel</button>
          <button className={`pms-btn ${danger ? 'pms-btn-danger' : 'pms-btn-primary'}`} onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </div>
    </>
  );
}

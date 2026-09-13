// frontend/src/pages/dashboard/CanvasPage.js
// Adaptive PMS Canvas — one living view that changes based on context
import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Sun, Moon, TrendingUp, TrendingDown, AlertTriangle, Target,
  ArrowRight, RefreshCw, Clock, ChevronRight, Zap, Shield,
  BarChart2, Activity,
} from 'lucide-react';
import { ensurePmsToken } from '../../lib/pmsApi';
import axios from 'axios';

const PMS_TOKEN_KEY = 'pms_jwt_token';
const api = axios.create({ baseURL: '', timeout: 30000 });
api.interceptors.request.use((config) => {
  const token = localStorage.getItem(PMS_TOKEN_KEY);
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// ── Time-of-day detection ────────────────────────────────────
const getMarketPhase = () => {
  const h = new Date().getHours();
  const m = new Date().getMinutes();
  const t = h * 60 + m;
  if (t < 9 * 60) return 'pre_market';      // before 9:00
  if (t < 9 * 60 + 15) return 'opening';     // 9:00-9:15
  if (t < 15 * 60 + 30) return 'market';     // 9:15-15:30
  if (t < 16 * 60) return 'closing';         // 15:30-16:00
  return 'post_market';                      // after 16:00
};

const phaseLabel = {
  pre_market: 'Pre-Market',
  opening: 'Market Opening',
  market: 'Market Hours',
  closing: 'Market Closing',
  post_market: 'After Hours',
};

const phaseGreeting = () => {
  const h = new Date().getHours();
  if (h < 12) return 'Good morning';
  if (h < 17) return 'Good afternoon';
  return 'Good evening';
};

// ── Helpers ──────────────────────────────────────────────────
const fmt = (n) => n != null ? n.toLocaleString('en-IN') : '—';
const fmtPct = (n) => n != null ? `${n >= 0 ? '+' : ''}${n.toFixed(1)}%` : '—';
const pnlColor = (n) => n > 0 ? '#22c55e' : n < 0 ? '#ef4444' : '#6b7280';

// ── Action Card ──────────────────────────────────────────────
const ActionCard = ({ icon: Icon, color, title, detail, actions, urgency }) => (
  <div style={{
    background: '#0f172a', border: `1px solid ${urgency === 'high' ? '#ef444440' : urgency === 'medium' ? '#f59e0b40' : '#1e293b'}`,
    borderRadius: 10, padding: '14px 16px', display: 'flex', gap: 12, alignItems: 'flex-start',
  }}>
    <div style={{
      width: 36, height: 36, borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: color + '15', flexShrink: 0,
    }}>
      <Icon size={18} style={{ color }} />
    </div>
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--neo-text)', marginBottom: 4 }}>{title}</div>
      <div style={{ fontSize: 11, color: '#9ca3af', lineHeight: 1.5 }}>{detail}</div>
      {actions && (
        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          {actions.map((a, i) => (
            <button key={i} onClick={a.onClick} style={{
              fontSize: 11, padding: '4px 14px', borderRadius: 6, cursor: 'pointer',
              background: i === 0 ? color : 'transparent',
              color: i === 0 ? '#fff' : color,
              border: `1px solid ${i === 0 ? color : color + '60'}`,
              fontWeight: i === 0 ? 600 : 400,
            }}>{a.label}</button>
          ))}
        </div>
      )}
    </div>
  </div>
);

// ── Portfolio Summary Strip ──────────────────────────────────
const PortfolioStrip = ({ portfolio, onClick }) => {
  const pnl = (portfolio.total_value || 0) - (portfolio.total_cost || 0);
  const pnlPct = portfolio.total_cost ? (pnl / portfolio.total_cost) * 100 : 0;
  return (
    <button onClick={onClick} style={{
      width: '100%', textAlign: 'left', cursor: 'pointer',
      background: '#0f172a', border: '1px solid #1e293b', borderRadius: 10,
      padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 12,
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--neo-text)' }}>{portfolio.name}</div>
        <div style={{ fontSize: 11, color: '#6b7280', marginTop: 2 }}>
          {portfolio.holdings_count || 0} holdings
        </div>
      </div>
      <div style={{ textAlign: 'right' }}>
        <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'monospace', color: 'var(--neo-text)' }}>
          {fmt(portfolio.total_value)}
        </div>
        <div style={{ fontSize: 11, fontFamily: 'monospace', color: pnlColor(pnl) }}>
          {fmtPct(pnlPct)}
        </div>
      </div>
      <ChevronRight size={14} style={{ color: '#374151' }} />
    </button>
  );
};

// ── Holding Cell (heat map) ──────────────────────────────────
const HoldingCell = ({ holding, onClick }) => {
  const pnl = ((holding.current_price || holding.avg_cost) - holding.avg_cost) * holding.quantity;
  const pnlPct = holding.avg_cost ? ((holding.current_price || holding.avg_cost) / holding.avg_cost - 1) * 100 : 0;
  const color = pnlColor(pnlPct);
  const sym = (holding.stock_symbol || '').replace('.NS', '').replace('.BO', '');
  const weight = holding.weight || 0;
  // Size relative to weight
  const minW = 70;
  const maxW = 160;
  const w = Math.max(minW, Math.min(maxW, minW + weight * (maxW - minW)));

  return (
    <button onClick={onClick} style={{
      width: w, padding: '10px 8px', borderRadius: 8, cursor: 'pointer',
      background: color + '08', border: `1px solid ${color}30`,
      display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2,
      textAlign: 'center',
    }}>
      <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--neo-text)' }}>{sym}</span>
      <span style={{ fontSize: 13, fontWeight: 700, fontFamily: 'monospace', color }}>
        {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%
      </span>
      <span style={{ fontSize: 9, color: '#4b5563' }}>{(weight * 100).toFixed(0)}% wt</span>
    </button>
  );
};

// ── Market Strip ─────────────────────────────────────────────
const MarketStrip = ({ data }) => (
  <div style={{
    display: 'flex', gap: 16, padding: '8px 16px', background: '#0d1117',
    borderRadius: 8, border: '1px solid #1e293b', flexWrap: 'wrap',
  }}>
    {(data || []).map((item, i) => (
      <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 10, color: '#6b7280' }}>{item.label}</span>
        <span style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--neo-text)', fontWeight: 600 }}>
          {item.value}
        </span>
        {item.change != null && (
          <span style={{ fontSize: 10, fontFamily: 'monospace', color: pnlColor(item.change) }}>
            {item.change >= 0 ? '+' : ''}{item.change.toFixed(1)}%
          </span>
        )}
      </div>
    ))}
  </div>
);

// ── Main Canvas ──────────────────────────────────────────────
export default function CanvasPage() {
  const navigate = useNavigate();
  const [phase] = useState(getMarketPhase);
  const [portfolios, setPortfolios] = useState([]);
  const [allHoldings, setAllHoldings] = useState([]);
  const [actions, setActions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefresh, setLastRefresh] = useState(null);

  const loadData = useCallback(async (isRefresh = false) => {
    if (!isRefresh) setLoading(true);
    else setRefreshing(true);

    // Ensure JWT token exists
    await ensurePmsToken();

    try {
      // Fetch all portfolios
      const pfRes = await api.get('/pms/portfolios');
      const pfList = pfRes.data || [];

      // Fetch holdings for each
      const enriched = [];
      const allH = [];
      for (const pf of pfList) {
        const pid = pf.portfolio_id || pf.id;
        try {
          const hRes = await api.get(`/pms/portfolios/${pid}/holdings`);
          const holdings = Array.isArray(hRes.data) ? hRes.data : (hRes.data?.holdings || []);
          const totalValue = holdings.reduce((s, h) => s + (h.current_price || h.avg_cost) * h.quantity, 0);
          const totalCost = holdings.reduce((s, h) => s + h.avg_cost * h.quantity, 0);
          enriched.push({
            ...pf,
            total_value: totalValue,
            total_cost: totalCost,
            holdings_count: holdings.length,
            holdings,
          });
          allH.push(...holdings.map(h => ({ ...h, portfolio_name: pf.name, portfolio_id: pid })));
        } catch {
          enriched.push({ ...pf, total_value: 0, total_cost: 0, holdings_count: 0, holdings: [] });
        }
      }
      setPortfolios(enriched);
      setAllHoldings(allH);

      // Build action cards
      const actionCards = [];

      // Check drift
      for (const pf of enriched) {
        const pid = pf.portfolio_id || pf.id;
        try {
          const dRes = await api.get(`/pms/portfolios/${pid}/drift?threshold=3`);
          const drift = dRes.data;
          if (drift?.needs_rebalance || drift?.max_drift > 3) {
            actionCards.push({
              icon: AlertTriangle, color: '#f59e0b', urgency: 'medium',
              title: `${pf.name}: drift ${drift.max_drift?.toFixed(1)}%`,
              detail: `Portfolio drifted from model allocation. ${drift.stocks_above_threshold || 0} stocks need rebalancing.`,
              actions: [
                { label: 'Rebalance', onClick: () => navigate('/dashboard/cockpit') },
                { label: 'Ignore', onClick: () => {} },
              ],
            });
          }
        } catch {}
      }

      // Check compliance
      for (const pf of enriched) {
        const pid = pf.portfolio_id || pf.id;
        try {
          const cRes = await api.post('/pms/compliance/check', { portfolio_id: pid });
          const comp = cRes.data;
          if (comp?.has_blocks) {
            actionCards.push({
              icon: Shield, color: '#ef4444', urgency: 'high',
              title: `Compliance violation: ${pf.name}`,
              detail: `${(comp.violations || []).length} rule violations found. Immediate attention needed.`,
              actions: [
                { label: 'Review', onClick: () => navigate('/dashboard/compliance') },
              ],
            });
          }
        } catch {}
      }

      // Check fusion for action-ready stocks
      const uniqueStocks = [...new Set(allH.map(h => h.stock_symbol?.replace('.NS', '').replace('.BO', '').toLowerCase().replace(/\s/g, '_')))].filter(Boolean);
      for (const stock of uniqueStocks.slice(0, 5)) {
        try {
          const fRes = await api.get(`/graph/fusion/${stock}`);
          const f = fRes.data;
          if (f && !f.error) {
            const conv = f.crosshair?.convergence ?? 0;
            const ready = f.crosshair?.action_ready;
            const action = f.crosshair?.recommended_action;
            if (ready || conv > 0.7) {
              actionCards.push({
                icon: Target, color: action === 'BUY' ? '#22c55e' : action === 'SELL' ? '#ef4444' : '#8b5cf6',
                urgency: conv > 0.8 ? 'high' : 'medium',
                title: `${stock.toUpperCase()}: ${action || 'HOLD'} signal (${(conv * 100).toFixed(0)}% convergence)`,
                detail: `Sensor fusion score: ${(f.fused_score * 100).toFixed(0)}%, ${f.direction}. Multiple sensors aligned.`,
                actions: [
                  { label: 'View HUD', onClick: () => navigate(`/dashboard/hud/${stock}`) },
                  ...(action === 'BUY' || action === 'SELL' ? [{ label: 'Propose Trade', onClick: () => navigate('/dashboard/command') }] : []),
                ],
              });
            }
          }
        } catch {}
      }

      // If no actions, add a calm message
      if (actionCards.length === 0) {
        actionCards.push({
          icon: Sun, color: '#22c55e', urgency: 'low',
          title: 'All clear',
          detail: 'No drift, no compliance issues, no urgent signals. Portfolios are tracking well.',
          actions: [],
        });
      }

      setActions(actionCards);
      setLastRefresh(new Date());
    } catch (e) {
      console.error('Canvas load error:', e);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [navigate]);

  useEffect(() => { loadData(); }, [loadData]);

  // Auto-refresh every 2 minutes during market hours
  useEffect(() => {
    if (phase === 'market' || phase === 'opening') {
      const iv = setInterval(() => loadData(true), 120000);
      return () => clearInterval(iv);
    }
  }, [phase, loadData]);

  // Totals
  const totalAUM = portfolios.reduce((s, p) => s + (p.total_value || 0), 0);
  const totalCost = portfolios.reduce((s, p) => s + (p.total_cost || 0), 0);
  const totalPnl = totalAUM - totalCost;
  const totalPnlPct = totalCost ? (totalPnl / totalCost) * 100 : 0;

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '60vh', flexDirection: 'column', gap: 12 }}>
        <Activity size={24} style={{ color: '#6b7280', opacity: 0.5 }} />
        <p style={{ color: '#6b7280', fontSize: 13 }}>Loading your portfolios...</p>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* ── Header ──────────────────────────────────────── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: 'var(--neo-text)', margin: 0 }}>
            {phaseGreeting()}
          </h1>
          <p style={{ fontSize: 12, color: '#6b7280', marginTop: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
            <Clock size={12} />
            {phaseLabel[phase]} &middot; {portfolios.length} portfolios &middot; {allHoldings.length} holdings
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {lastRefresh && (
            <span style={{ fontSize: 9, color: '#4b5563' }}>
              Updated {lastRefresh.toLocaleTimeString()}
            </span>
          )}
          <button onClick={() => loadData(true)} style={{
            background: 'none', border: '1px solid var(--neo-border)', borderRadius: 6,
            padding: '4px 8px', cursor: 'pointer',
          }}>
            <RefreshCw size={12} style={{ color: '#6b7280', animation: refreshing ? 'spin 1s linear infinite' : 'none' }} />
          </button>
        </div>
      </div>

      {/* ── AUM Strip ───────────────────────────────────── */}
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12,
      }}>
        <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 10, padding: '16px 20px' }}>
          <div style={{ fontSize: 10, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 4 }}>Total AUM</div>
          <div style={{ fontSize: 22, fontWeight: 700, fontFamily: 'monospace', color: 'var(--neo-text)' }}>
            {fmt(Math.round(totalAUM))}
          </div>
        </div>
        <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 10, padding: '16px 20px' }}>
          <div style={{ fontSize: 10, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 4 }}>Total P&L</div>
          <div style={{ fontSize: 22, fontWeight: 700, fontFamily: 'monospace', color: pnlColor(totalPnl) }}>
            {totalPnl >= 0 ? '+' : ''}{fmt(Math.round(totalPnl))}
          </div>
        </div>
        <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 10, padding: '16px 20px' }}>
          <div style={{ fontSize: 10, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 4 }}>Return</div>
          <div style={{ fontSize: 22, fontWeight: 700, fontFamily: 'monospace', color: pnlColor(totalPnlPct) }}>
            {fmtPct(totalPnlPct)}
          </div>
        </div>
      </div>

      {/* ── Actions Needed ──────────────────────────────── */}
      <div>
        <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--neo-text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6 }}>
          <Zap size={13} />
          {actions.length > 1 ? `${actions.length} things need attention` : 'Status'}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {actions.map((a, i) => (
            <ActionCard key={i} {...a} />
          ))}
        </div>
      </div>

      {/* ── Holdings Heat Map ───────────────────────────── */}
      {allHoldings.length > 0 && (
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--neo-text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6 }}>
            <BarChart2 size={13} />
            Holdings ({allHoldings.length})
            <span style={{ fontSize: 9, color: '#374151', fontWeight: 400, marginLeft: 4 }}>click to inspect</span>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {/* Deduplicate holdings across portfolios, keep highest weight */}
            {Object.values(
              allHoldings.reduce((acc, h) => {
                const sym = (h.stock_symbol || '').replace('.NS', '').replace('.BO', '');
                if (!acc[sym] || (h.weight || 0) > (acc[sym].weight || 0)) acc[sym] = h;
                return acc;
              }, {})
            )
              .sort((a, b) => (b.weight || 0) - (a.weight || 0))
              .map((h, i) => {
                const sym = (h.stock_symbol || '').replace('.NS', '').replace('.BO', '').toLowerCase().replace(/\s/g, '_');
                return (
                  <HoldingCell key={i} holding={h}
                    onClick={() => navigate(`/dashboard/hud/${sym}`)} />
                );
              })}
          </div>
        </div>
      )}

      {/* ── Portfolios ──────────────────────────────────── */}
      <div>
        <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--neo-text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 10 }}>
          Portfolios ({portfolios.length})
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {portfolios.map((pf, i) => (
            <PortfolioStrip key={i} portfolio={pf}
              onClick={() => navigate('/dashboard/portfolio')} />
          ))}
        </div>
      </div>
    </div>
  );
}

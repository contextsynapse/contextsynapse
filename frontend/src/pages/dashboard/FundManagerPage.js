/**
 * FundManagerPage — Comprehensive fund manager research with 3 tabs:
 * Managers list, Manager Detail (deep-dive), Compare (side-by-side).
 */
import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Users, Award, TrendingUp, BarChart3, Target, Eye, RefreshCw,
  ChevronRight, Star, Zap, Shield, Brain, Layers, ArrowUpRight,
  ArrowDownRight, Search, Download, Briefcase,
} from 'lucide-react';
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, RadarChart, PolarGrid,
  PolarAngleAxis, PolarRadiusAxis, Radar as RechartsRadar, Legend,
} from 'recharts';
import toast from 'react-hot-toast';
import {
  PageHeader, GlassCard, DataTable, StatusBadge, Tabs, KPIStrip,
  Skeleton, SlideOver, ProgressBar,
} from '../../components/ui-components';
import axios from 'axios';
import { ensurePmsToken } from '../../lib/pmsApi';

// Authenticated PMS axios instance (reuses token from pmsApi)
const pms = axios.create({ baseURL: '', timeout: 15000 });
pms.interceptors.request.use((config) => {
  const token = localStorage.getItem('pms_jwt_token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// ── Style colors ──────────────────────────────────────────────
const STYLE_COLORS = {
  value: '#34d399',
  growth: '#5b8af0',
  blend: '#a78bfa',
  GARP: '#22d3ee',
  contrarian: '#f59e0b',
};

const styleColor = (s) => STYLE_COLORS[s] || '#6b7280';

// ── 12 Sample Indian Fund Managers (fallback) ─────────────────
const SAMPLE_MANAGERS = [
  {
    name: 'Prashant Jain', fund_house: 'HDFC AMC', designation: 'CIO',
    style: 'value', aum_cr: 142000, tenure_years: 28, alpha_5y: 3.2, hit_rate: 0.68,
    fusion_score: 82,
    philosophy: 'Long-term value investing with a focus on margin of safety and compounding.',
    principles: ['Margin of Safety', 'Low Churn', 'Contrarian Bets', 'Sector Rotation'],
    behavioral_dna: { concentration: 72, churn: 18, contrarian_score: 78, crash_behavior: 'Hold & Average', rally_behavior: 'Trim Winners', sector_biases: { Financials: 32, IT: 18, Pharma: 12, Energy: 15, Auto: 8 } },
    track_record: { cagr_10y: 14.2, cagr_5y: 12.8, cagr_3y: 16.1, best_year: 52.3, worst_year: -18.4, max_drawdown: -28.1, sharpe: 1.12, sortino: 1.45, yearly_returns: { 2020: -5.2, 2021: 32.1, 2022: 8.4, 2023: 18.7, 2024: 22.3, 2025: 14.1 } },
    conviction_stocks: [
      { stock: 'ICICI Bank', weight: 9.8, holding_years: 12 },
      { stock: 'SBI', weight: 7.2, holding_years: 8 },
      { stock: 'Infosys', weight: 6.5, holding_years: 15 },
      { stock: 'L&T', weight: 5.8, holding_years: 10 },
      { stock: 'NTPC', weight: 5.1, holding_years: 6 },
    ],
    sensors: { fundamental: 85, momentum: 62, sentiment: 71, macro: 78, technical: 55 },
  },
  {
    name: 'Rajeev Thakkar', fund_house: 'PPFAS MF', designation: 'CIO & Director',
    style: 'value', aum_cr: 58000, tenure_years: 12, alpha_5y: 4.8, hit_rate: 0.72,
    fusion_score: 88,
    philosophy: 'Buying great businesses at reasonable prices with a global perspective.',
    principles: ['Quality at Fair Price', 'Global Diversification', 'Low Turnover', 'Cash as Asset'],
    behavioral_dna: { concentration: 65, churn: 12, contrarian_score: 82, crash_behavior: 'Deploy Cash', rally_behavior: 'Build Cash', sector_biases: { IT: 28, Financials: 22, Consumer: 15, Auto: 12, Pharma: 8 } },
    track_record: { cagr_10y: 16.8, cagr_5y: 18.2, cagr_3y: 19.4, best_year: 48.6, worst_year: -12.1, max_drawdown: -22.3, sharpe: 1.34, sortino: 1.72, yearly_returns: { 2020: 8.4, 2021: 38.2, 2022: 4.1, 2023: 22.6, 2024: 28.1, 2025: 16.8 } },
    conviction_stocks: [
      { stock: 'Alphabet', weight: 8.2, holding_years: 8 },
      { stock: 'ICICI Bank', weight: 7.8, holding_years: 10 },
      { stock: 'ITC', weight: 6.4, holding_years: 7 },
      { stock: 'Bajaj Finance', weight: 5.9, holding_years: 6 },
      { stock: 'Amazon', weight: 4.8, holding_years: 5 },
    ],
    sensors: { fundamental: 92, momentum: 58, sentiment: 74, macro: 82, technical: 48 },
  },
  {
    name: 'Sankaran Naren', fund_house: 'ICICI Pru AMC', designation: 'ED & CIO',
    style: 'contrarian', aum_cr: 185000, tenure_years: 22, alpha_5y: 2.9, hit_rate: 0.65,
    fusion_score: 79,
    philosophy: 'Contrarian investing with deep value and cyclical awareness.',
    principles: ['Buy Fear', 'Sell Greed', 'Cyclical Timing', 'Sector Contrarian'],
    behavioral_dna: { concentration: 55, churn: 28, contrarian_score: 92, crash_behavior: 'Aggressive Buy', rally_behavior: 'Reduce Equity', sector_biases: { Financials: 28, Energy: 20, Metals: 15, Infra: 12, Pharma: 10 } },
    track_record: { cagr_10y: 13.1, cagr_5y: 11.9, cagr_3y: 14.8, best_year: 58.2, worst_year: -22.6, max_drawdown: -35.2, sharpe: 0.98, sortino: 1.18, yearly_returns: { 2020: -8.1, 2021: 42.5, 2022: 12.3, 2023: 15.2, 2024: 18.9, 2025: 11.4 } },
    conviction_stocks: [
      { stock: 'SBI', weight: 8.1, holding_years: 14 },
      { stock: 'ONGC', weight: 6.8, holding_years: 8 },
      { stock: 'Tata Steel', weight: 5.5, holding_years: 5 },
      { stock: 'NTPC', weight: 5.2, holding_years: 7 },
      { stock: 'Coal India', weight: 4.9, holding_years: 6 },
    ],
    sensors: { fundamental: 78, momentum: 72, sentiment: 65, macro: 88, technical: 62 },
  },
  {
    name: 'Neelesh Surana', fund_house: 'Mirae Asset MF', designation: 'CIO',
    style: 'GARP', aum_cr: 95000, tenure_years: 18, alpha_5y: 3.8, hit_rate: 0.71,
    fusion_score: 85,
    philosophy: 'Growth at reasonable price with bottom-up stock selection across market caps.',
    principles: ['Earnings Growth', 'Reasonable Valuations', 'Quality Management', 'Scalable Business'],
    behavioral_dna: { concentration: 60, churn: 22, contrarian_score: 55, crash_behavior: 'Selective Buying', rally_behavior: 'Stay Invested', sector_biases: { Financials: 30, IT: 20, Consumer: 18, Pharma: 10, Auto: 8 } },
    track_record: { cagr_10y: 15.4, cagr_5y: 16.1, cagr_3y: 17.8, best_year: 44.2, worst_year: -14.8, max_drawdown: -25.1, sharpe: 1.22, sortino: 1.55, yearly_returns: { 2020: 2.1, 2021: 35.4, 2022: 6.8, 2023: 20.1, 2024: 25.6, 2025: 15.2 } },
    conviction_stocks: [
      { stock: 'HDFC Bank', weight: 9.2, holding_years: 12 },
      { stock: 'TCS', weight: 7.1, holding_years: 10 },
      { stock: 'Bajaj Finance', weight: 6.3, holding_years: 8 },
      { stock: 'Asian Paints', weight: 5.4, holding_years: 7 },
      { stock: 'Divi\'s Labs', weight: 4.8, holding_years: 5 },
    ],
    sensors: { fundamental: 88, momentum: 68, sentiment: 72, macro: 75, technical: 58 },
  },
  {
    name: 'Shreyash Devalkar', fund_house: 'Axis AMC', designation: 'Head of Equity',
    style: 'growth', aum_cr: 72000, tenure_years: 10, alpha_5y: 4.1, hit_rate: 0.69,
    fusion_score: 83,
    philosophy: 'Quality growth investing with a focus on earnings visibility and competitive moats.',
    principles: ['Earnings Visibility', 'Competitive Moats', 'Capital Efficiency', 'Management Quality'],
    behavioral_dna: { concentration: 68, churn: 20, contrarian_score: 42, crash_behavior: 'Hold Quality', rally_behavior: 'Add to Winners', sector_biases: { Financials: 25, IT: 22, Consumer: 20, Pharma: 12, Chemicals: 8 } },
    track_record: { cagr_10y: 14.8, cagr_5y: 15.6, cagr_3y: 18.2, best_year: 42.8, worst_year: -16.2, max_drawdown: -26.4, sharpe: 1.18, sortino: 1.48, yearly_returns: { 2020: -2.4, 2021: 36.8, 2022: 2.1, 2023: 21.4, 2024: 26.8, 2025: 14.8 } },
    conviction_stocks: [
      { stock: 'Bajaj Finance', weight: 8.8, holding_years: 8 },
      { stock: 'Avenue Supermarts', weight: 7.2, holding_years: 6 },
      { stock: 'HDFC Bank', weight: 6.8, holding_years: 9 },
      { stock: 'Infosys', weight: 5.9, holding_years: 7 },
      { stock: 'Pidilite', weight: 5.1, holding_years: 5 },
    ],
    sensors: { fundamental: 86, momentum: 72, sentiment: 68, macro: 70, technical: 52 },
  },
  {
    name: 'R. Srinivasan', fund_house: 'SBI MF', designation: 'Head - Equity',
    style: 'blend', aum_cr: 210000, tenure_years: 20, alpha_5y: 2.6, hit_rate: 0.64,
    fusion_score: 76,
    philosophy: 'Blended approach combining growth and value with benchmark-aware positioning.',
    principles: ['Benchmark Awareness', 'Risk Management', 'Diversification', 'Process Driven'],
    behavioral_dna: { concentration: 48, churn: 25, contrarian_score: 45, crash_behavior: 'Rebalance', rally_behavior: 'Benchmark Align', sector_biases: { Financials: 28, IT: 15, Energy: 14, Consumer: 12, Pharma: 10 } },
    track_record: { cagr_10y: 12.8, cagr_5y: 11.4, cagr_3y: 13.9, best_year: 38.4, worst_year: -19.8, max_drawdown: -30.2, sharpe: 0.92, sortino: 1.12, yearly_returns: { 2020: -6.8, 2021: 28.4, 2022: 7.2, 2023: 16.8, 2024: 19.2, 2025: 12.4 } },
    conviction_stocks: [
      { stock: 'Reliance', weight: 10.2, holding_years: 15 },
      { stock: 'HDFC Bank', weight: 8.8, holding_years: 12 },
      { stock: 'Infosys', weight: 7.1, holding_years: 14 },
      { stock: 'TCS', weight: 6.2, holding_years: 10 },
      { stock: 'ITC', weight: 5.4, holding_years: 8 },
    ],
    sensors: { fundamental: 75, momentum: 65, sentiment: 62, macro: 72, technical: 60 },
  },
  {
    name: 'Jinesh Gopani', fund_house: 'Axis AMC', designation: 'Sr. Fund Manager',
    style: 'growth', aum_cr: 48000, tenure_years: 14, alpha_5y: 3.5, hit_rate: 0.67,
    fusion_score: 80,
    philosophy: 'Concentrated growth investing with high conviction in structural winners.',
    principles: ['Structural Growth', 'High Conviction', 'Concentrated Bets', 'Long Holding'],
    behavioral_dna: { concentration: 78, churn: 15, contrarian_score: 38, crash_behavior: 'Hold Tight', rally_behavior: 'Ride Momentum', sector_biases: { IT: 25, Financials: 22, Consumer: 18, Pharma: 15, Auto: 8 } },
    track_record: { cagr_10y: 15.1, cagr_5y: 14.2, cagr_3y: 16.8, best_year: 46.2, worst_year: -20.1, max_drawdown: -28.8, sharpe: 1.08, sortino: 1.35, yearly_returns: { 2020: -4.8, 2021: 38.6, 2022: 1.2, 2023: 19.8, 2024: 24.1, 2025: 13.6 } },
    conviction_stocks: [
      { stock: 'TCS', weight: 9.4, holding_years: 11 },
      { stock: 'Bajaj Finance', weight: 8.1, holding_years: 9 },
      { stock: 'HDFC Bank', weight: 7.2, holding_years: 12 },
      { stock: 'Titan', weight: 6.8, holding_years: 7 },
      { stock: 'Kotak Bank', weight: 5.5, holding_years: 6 },
    ],
    sensors: { fundamental: 82, momentum: 75, sentiment: 70, macro: 68, technical: 58 },
  },
  {
    name: 'Mahesh Patil', fund_house: 'Aditya Birla SL AMC', designation: 'CIO',
    style: 'blend', aum_cr: 125000, tenure_years: 24, alpha_5y: 2.4, hit_rate: 0.63,
    fusion_score: 74,
    philosophy: 'Diversified blend of growth and value across market capitalizations.',
    principles: ['Multi-Cap', 'Sector Balance', 'Risk Adjusted Returns', 'Top-Down + Bottom-Up'],
    behavioral_dna: { concentration: 45, churn: 30, contrarian_score: 50, crash_behavior: 'Defensive Shift', rally_behavior: 'Tactical Allocation', sector_biases: { Financials: 26, IT: 16, Consumer: 14, Pharma: 12, Auto: 10 } },
    track_record: { cagr_10y: 12.2, cagr_5y: 10.8, cagr_3y: 13.2, best_year: 36.8, worst_year: -21.4, max_drawdown: -32.1, sharpe: 0.88, sortino: 1.05, yearly_returns: { 2020: -8.4, 2021: 26.2, 2022: 6.4, 2023: 15.6, 2024: 17.8, 2025: 11.2 } },
    conviction_stocks: [
      { stock: 'ICICI Bank', weight: 8.4, holding_years: 14 },
      { stock: 'Reliance', weight: 7.8, holding_years: 12 },
      { stock: 'Infosys', weight: 6.2, holding_years: 10 },
      { stock: 'Axis Bank', weight: 5.6, holding_years: 8 },
      { stock: 'Maruti', weight: 4.8, holding_years: 6 },
    ],
    sensors: { fundamental: 72, momentum: 62, sentiment: 58, macro: 70, technical: 55 },
  },
  {
    name: 'Sohini Andani', fund_house: 'SBI MF', designation: 'Fund Manager',
    style: 'GARP', aum_cr: 68000, tenure_years: 16, alpha_5y: 3.4, hit_rate: 0.70,
    fusion_score: 84,
    philosophy: 'Disciplined GARP approach with focus on capital allocation efficiency.',
    principles: ['Capital Allocation', 'Earnings Growth', 'Valuation Discipline', 'Bottom-Up Research'],
    behavioral_dna: { concentration: 62, churn: 19, contrarian_score: 58, crash_behavior: 'Selective Deploy', rally_behavior: 'Profit Booking', sector_biases: { Financials: 30, Consumer: 20, IT: 16, Pharma: 12, Auto: 8 } },
    track_record: { cagr_10y: 14.6, cagr_5y: 15.8, cagr_3y: 17.2, best_year: 41.8, worst_year: -13.6, max_drawdown: -24.2, sharpe: 1.25, sortino: 1.58, yearly_returns: { 2020: 1.2, 2021: 34.2, 2022: 5.8, 2023: 19.4, 2024: 24.8, 2025: 15.6 } },
    conviction_stocks: [
      { stock: 'HDFC Bank', weight: 9.6, holding_years: 14 },
      { stock: 'Bajaj Finance', weight: 7.4, holding_years: 10 },
      { stock: 'HUL', weight: 6.1, holding_years: 8 },
      { stock: 'Asian Paints', weight: 5.8, holding_years: 7 },
      { stock: 'Kotak Bank', weight: 5.2, holding_years: 9 },
    ],
    sensors: { fundamental: 90, momentum: 64, sentiment: 72, macro: 76, technical: 52 },
  },
  {
    name: 'Manish Gunwani', fund_house: 'Bandhan AMC', designation: 'Head - Equity',
    style: 'growth', aum_cr: 32000, tenure_years: 15, alpha_5y: 3.9, hit_rate: 0.68,
    fusion_score: 81,
    philosophy: 'Aggressive growth investing in emerging structural themes.',
    principles: ['Thematic Investing', 'Emerging Leaders', 'Earnings Momentum', 'Sector Rotation'],
    behavioral_dna: { concentration: 58, churn: 32, contrarian_score: 48, crash_behavior: 'Rotate to Safety', rally_behavior: 'Ride Themes', sector_biases: { IT: 24, Financials: 20, Consumer: 16, Pharma: 14, Chemicals: 10 } },
    track_record: { cagr_10y: 14.1, cagr_5y: 15.2, cagr_3y: 18.8, best_year: 52.1, worst_year: -18.8, max_drawdown: -29.4, sharpe: 1.05, sortino: 1.32, yearly_returns: { 2020: -3.6, 2021: 42.8, 2022: 3.2, 2023: 22.1, 2024: 28.4, 2025: 14.2 } },
    conviction_stocks: [
      { stock: 'Info Edge', weight: 7.8, holding_years: 6 },
      { stock: 'Bajaj Finance', weight: 7.2, holding_years: 8 },
      { stock: 'TCS', weight: 6.4, holding_years: 9 },
      { stock: 'Divi\'s Labs', weight: 5.8, holding_years: 5 },
      { stock: 'SBI Cards', weight: 4.6, holding_years: 4 },
    ],
    sensors: { fundamental: 80, momentum: 78, sentiment: 72, macro: 68, technical: 62 },
  },
  {
    name: 'Vetri Subramaniam', fund_house: 'UTI AMC', designation: 'CIO',
    style: 'value', aum_cr: 88000, tenure_years: 26, alpha_5y: 2.8, hit_rate: 0.66,
    fusion_score: 78,
    philosophy: 'Deep value investing with emphasis on mean reversion and cyclical opportunities.',
    principles: ['Mean Reversion', 'Margin of Safety', 'Patient Capital', 'Contrarian Value'],
    behavioral_dna: { concentration: 52, churn: 20, contrarian_score: 75, crash_behavior: 'Accumulate Value', rally_behavior: 'Trim & Wait', sector_biases: { Financials: 28, Energy: 18, IT: 14, Pharma: 12, Metals: 10 } },
    track_record: { cagr_10y: 13.4, cagr_5y: 12.2, cagr_3y: 15.1, best_year: 45.6, worst_year: -17.2, max_drawdown: -27.8, sharpe: 1.02, sortino: 1.28, yearly_returns: { 2020: -4.2, 2021: 30.8, 2022: 8.8, 2023: 17.2, 2024: 20.6, 2025: 12.8 } },
    conviction_stocks: [
      { stock: 'SBI', weight: 8.8, holding_years: 16 },
      { stock: 'ICICI Bank', weight: 7.6, holding_years: 12 },
      { stock: 'L&T', weight: 6.2, holding_years: 10 },
      { stock: 'Bharti Airtel', weight: 5.4, holding_years: 8 },
      { stock: 'NTPC', weight: 4.8, holding_years: 7 },
    ],
    sensors: { fundamental: 82, momentum: 58, sentiment: 65, macro: 80, technical: 48 },
  },
  {
    name: 'Anand Radhakrishnan', fund_house: 'Franklin Templeton', designation: 'CIO - Equity',
    style: 'GARP', aum_cr: 42000, tenure_years: 20, alpha_5y: 3.1, hit_rate: 0.67,
    fusion_score: 80,
    philosophy: 'Research-driven GARP investing with global analytical frameworks.',
    principles: ['Research Depth', 'Global Frameworks', 'Valuation Discipline', 'Quality Earnings'],
    behavioral_dna: { concentration: 58, churn: 24, contrarian_score: 52, crash_behavior: 'Methodical Buying', rally_behavior: 'Systematic Trimming', sector_biases: { Financials: 26, IT: 20, Consumer: 16, Auto: 14, Pharma: 10 } },
    track_record: { cagr_10y: 13.8, cagr_5y: 14.6, cagr_3y: 16.4, best_year: 40.2, worst_year: -15.8, max_drawdown: -26.1, sharpe: 1.15, sortino: 1.42, yearly_returns: { 2020: -1.8, 2021: 32.6, 2022: 5.4, 2023: 18.8, 2024: 23.2, 2025: 14.4 } },
    conviction_stocks: [
      { stock: 'ICICI Bank', weight: 9.1, holding_years: 14 },
      { stock: 'Infosys', weight: 7.4, holding_years: 12 },
      { stock: 'Maruti', weight: 6.2, holding_years: 8 },
      { stock: 'Bajaj Finance', weight: 5.6, holding_years: 7 },
      { stock: 'Sun Pharma', weight: 4.8, holding_years: 6 },
    ],
    sensors: { fundamental: 84, momentum: 66, sentiment: 70, macro: 74, technical: 56 },
  },
];

// ── Helpers ──────────────────────────────────────────────────
const fmt = (n) => {
  if (n == null) return '-';
  if (n >= 100000) return `${(n / 100000).toFixed(1)}L Cr`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K Cr`;
  return `${n.toLocaleString('en-IN')} Cr`;
};

const pct = (n) => (n != null ? `${n > 0 ? '+' : ''}${n.toFixed(1)}%` : '-');
const initials = (name) => name.split(' ').map(w => w[0]).join('').slice(0, 2);
const scoreGrade = (s) => s >= 85 ? 'A+' : s >= 75 ? 'A' : s >= 65 ? 'B+' : s >= 55 ? 'B' : 'C';
const scoreColor = (s) => s >= 80 ? '#34d399' : s >= 65 ? '#5b8af0' : s >= 50 ? '#f59e0b' : '#ef4444';

// ── Main Component ──────────────────────────────────────────
export default function FundManagerPage() {
  const [tab, setTab] = useState('managers');
  const [managers, setManagers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [compare, setCompare] = useState([null, null, null]);
  const [populating, setPopulating] = useState(false);

  const fetchManagers = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await pms.get('/pms/fund-managers');
      setManagers(data.managers || data || []);
    } catch {
      setManagers(SAMPLE_MANAGERS);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchManagers(); }, [fetchManagers]);

  const handlePopulate = async () => {
    setPopulating(true);
    try {
      await pms.post('/pms/fund-managers/populate');
      toast.success('Fund manager data populated');
      fetchManagers();
    } catch {
      toast('Using sample data', { icon: 'i' });
      setManagers(SAMPLE_MANAGERS);
    } finally {
      setPopulating(false);
    }
  };

  const handleRowClick = (mgr) => {
    setSelected(mgr);
    setTab('detail');
  };

  const handleSelectDetail = async (name) => {
    try {
      const { data } = await pms.get(`/pms/fund-managers/${encodeURIComponent(name)}`);
      setSelected(data);
      setTab('detail');
    } catch {
      const fallback = (managers.length ? managers : SAMPLE_MANAGERS).find(m => m.name === name);
      if (fallback) { setSelected(fallback); setTab('detail'); }
      else toast.error('Manager not found');
    }
  };

  const data = managers.length ? managers : SAMPLE_MANAGERS;

  const tabList = [
    { key: 'managers', label: 'Managers', icon: Users, count: data.length },
    { key: 'detail', label: 'Detail', icon: Eye },
    { key: 'compare', label: 'Compare', icon: BarChart3 },
  ];

  return (
    <div style={{ maxWidth: 1400, margin: '0 auto' }}>
      <PageHeader icon={Users} title="Fund Managers" subtitle="Research, analyze and compare fund manager personas">
        <button className="pms-btn pms-btn-ghost pms-btn-sm" onClick={handlePopulate} disabled={populating}>
          <RefreshCw size={14} className={populating ? 'animate-spin' : ''} style={{ marginRight: 6 }} />
          {populating ? 'Populating...' : 'Populate'}
        </button>
      </PageHeader>

      <Tabs tabs={tabList} active={tab} onChange={setTab} />

      <div style={{ marginTop: 20 }}>
        {tab === 'managers' && <ManagersTab data={data} loading={loading} onRowClick={handleRowClick} />}
        {tab === 'detail' && <DetailTab manager={selected} managers={data} onSelect={handleSelectDetail} />}
        {tab === 'compare' && <CompareTab managers={data} compare={compare} setCompare={setCompare} />}
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════
// TAB 1: MANAGERS LIST
// ══════════════════════════════════════════════════════════════
function ManagersTab({ data, loading, onRowClick }) {
  const kpis = useMemo(() => {
    if (!data.length) return [];
    const avgAlpha = data.reduce((s, m) => s + (m.alpha_5y || 0), 0) / data.length;
    const avgHit = data.reduce((s, m) => s + (m.hit_rate || 0), 0) / data.length;
    const avgFusion = data.reduce((s, m) => s + (m.fusion_score || 0), 0) / data.length;
    const totalAum = data.reduce((s, m) => s + (m.aum_cr || 0), 0);
    return [
      { label: 'Total Managers', value: data.length, icon: Users },
      { label: 'Total AUM', value: fmt(totalAum), icon: Briefcase },
      { label: 'Avg Alpha 5Y', value: pct(avgAlpha), icon: TrendingUp, color: avgAlpha > 0 ? 'var(--neo-green)' : undefined },
      { label: 'Avg Fusion Score', value: avgFusion.toFixed(0), icon: Brain },
    ];
  }, [data]);

  const columns = [
    {
      header: 'Name', accessor: 'name', key: 'name',
      render: (v, row) => (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 32, height: 32, borderRadius: '50%',
            background: `linear-gradient(135deg, ${styleColor(row.style)}, rgba(255,255,255,0.1))`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 12, fontWeight: 700, color: '#fff', flexShrink: 0,
          }}>{initials(v)}</div>
          <div>
            <div style={{ fontWeight: 600, color: 'var(--neo-text)' }}>{v}</div>
            <div style={{ fontSize: 11, color: 'var(--neo-text-dim)' }}>{row.designation || ''}</div>
          </div>
        </div>
      ),
    },
    { header: 'Fund House', accessor: 'fund_house', key: 'fund_house' },
    {
      header: 'Style', accessor: 'style', key: 'style',
      render: (v) => (
        <span style={{
          fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 4,
          background: `${styleColor(v)}18`, color: styleColor(v),
          border: `1px solid ${styleColor(v)}30`,
        }}>{v}</span>
      ),
    },
    { header: 'AUM', accessor: 'aum_cr', key: 'aum_cr', align: 'right', render: (v) => <span className="pms-number">{fmt(v)}</span> },
    { header: 'Tenure', accessor: 'tenure_years', key: 'tenure_years', align: 'right', render: (v) => `${v}Y` },
    {
      header: 'Alpha 5Y', accessor: 'alpha_5y', key: 'alpha_5y', align: 'right',
      render: (v) => (
        <span style={{ color: v > 0 ? 'var(--neo-green)' : 'var(--neo-red)', fontWeight: 600 }} className="pms-number">
          {pct(v)}
        </span>
      ),
    },
    {
      header: 'Hit Rate', accessor: 'hit_rate', key: 'hit_rate', align: 'right',
      render: (v) => <span className="pms-number">{(v * 100).toFixed(0)}%</span>,
    },
    {
      header: 'Fusion', accessor: 'fusion_score', key: 'fusion_score', align: 'center',
      render: (v) => (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'center' }}>
          <div style={{
            width: 28, height: 28, borderRadius: '50%',
            background: `${scoreColor(v)}18`, border: `2px solid ${scoreColor(v)}`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 11, fontWeight: 700, color: scoreColor(v),
          }}>{v}</div>
        </div>
      ),
    },
  ];

  return (
    <>
      <KPIStrip items={kpis} loading={loading} />
      <div style={{ marginTop: 20 }}>
        <DataTable columns={columns} data={data} loading={loading} onRowClick={onRowClick} emptyMessage="No fund managers found. Click Populate to load sample data." />
      </div>
    </>
  );
}

// ══════════════════════════════════════════════════════════════
// TAB 2: DETAIL — Deep-dive persona
// ══════════════════════════════════════════════════════════════
function DetailTab({ manager, managers, onSelect }) {
  const mgr = manager || (managers && managers[0]) || SAMPLE_MANAGERS[0];
  const dna = mgr.behavioral_dna || {};
  const track = mgr.track_record || {};
  const sensors = mgr.sensors || {};
  const yearly = track.yearly_returns || {};
  const yearlyData = Object.entries(yearly).map(([yr, ret]) => ({ year: yr, return: ret }));

  return (
    <div>
      {/* Manager selector */}
      <div style={{ marginBottom: 20, display: 'flex', alignItems: 'center', gap: 12 }}>
        <label style={{ fontSize: 12, color: 'var(--neo-text-muted)', fontWeight: 600 }}>SELECT MANAGER</label>
        <select
          className="pms-input"
          value={mgr.name}
          onChange={(e) => onSelect(e.target.value)}
          style={{ width: 260, fontSize: 13 }}
        >
          {(managers.length ? managers : SAMPLE_MANAGERS).map(m => (
            <option key={m.name} value={m.name}>{m.name} - {m.fund_house}</option>
          ))}
        </select>
      </div>

      {/* Top: Profile + Behavioral DNA */}
      <div style={{ display: 'grid', gridTemplateColumns: '340px 1fr', gap: 20, marginBottom: 20 }}>
        {/* Left: Profile card */}
        <GlassCard>
          <div style={{ textAlign: 'center', marginBottom: 16 }}>
            <div style={{
              width: 72, height: 72, borderRadius: '50%', margin: '0 auto 12px',
              background: `linear-gradient(135deg, ${styleColor(mgr.style)}, rgba(255,255,255,0.15))`,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 24, fontWeight: 700, color: '#fff',
              boxShadow: `0 0 24px ${styleColor(mgr.style)}40`,
            }}>{initials(mgr.name)}</div>
            <h2 style={{ fontSize: 20, fontWeight: 700, color: 'var(--neo-text)', margin: 0 }}>{mgr.name}</h2>
            <div style={{ fontSize: 13, color: 'var(--neo-text-muted)', marginTop: 2 }}>{mgr.designation} | {mgr.fund_house}</div>
            <div style={{ marginTop: 8, display: 'flex', justifyContent: 'center', gap: 8 }}>
              <span style={{
                fontSize: 11, fontWeight: 600, padding: '3px 10px', borderRadius: 6,
                background: `${styleColor(mgr.style)}18`, color: styleColor(mgr.style),
                border: `1px solid ${styleColor(mgr.style)}30`,
              }}>{mgr.style}</span>
              <span style={{
                fontSize: 11, fontWeight: 600, padding: '3px 10px', borderRadius: 6,
                background: `${scoreColor(mgr.fusion_score)}18`, color: scoreColor(mgr.fusion_score),
                border: `1px solid ${scoreColor(mgr.fusion_score)}30`,
              }}>Fusion: {mgr.fusion_score}</span>
            </div>
          </div>

          {/* Philosophy */}
          <div style={{
            padding: '12px 14px', borderRadius: 8, marginBottom: 14,
            background: 'rgba(255,255,255,0.02)', borderLeft: `3px solid ${styleColor(mgr.style)}`,
          }}>
            <div style={{ fontSize: 11, color: 'var(--neo-text-dim)', fontWeight: 600, marginBottom: 4 }}>PHILOSOPHY</div>
            <div style={{ fontSize: 12, color: 'var(--neo-text-muted)', fontStyle: 'italic', lineHeight: 1.5 }}>
              "{mgr.philosophy}"
            </div>
          </div>

          {/* Principles */}
          <div style={{ fontSize: 11, color: 'var(--neo-text-dim)', fontWeight: 600, marginBottom: 6 }}>PRINCIPLES</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {(mgr.principles || []).map(p => (
              <span key={p} style={{
                fontSize: 11, padding: '3px 8px', borderRadius: 4,
                background: 'rgba(255,255,255,0.04)', color: 'var(--neo-text-muted)',
                border: '1px solid var(--neo-border)',
              }}>{p}</span>
            ))}
          </div>
        </GlassCard>

        {/* Right: Behavioral DNA */}
        <div>
          <GlassCard style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--neo-text)', marginBottom: 16 }}>
              <Brain size={14} style={{ marginRight: 6, verticalAlign: 'middle', color: 'var(--neo-blue)' }} />
              Behavioral DNA
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16, marginBottom: 20 }}>
              <DNAMetric label="Concentration" value={dna.concentration} suffix="%" />
              <DNAMetric label="Churn Rate" value={dna.churn} suffix="%" low />
              <DNAMetric label="Contrarian Score" value={dna.contrarian_score} suffix="%" />
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 20 }}>
              <div>
                <div style={{ fontSize: 11, color: 'var(--neo-text-dim)', fontWeight: 600, marginBottom: 4 }}>CRASH BEHAVIOR</div>
                <StatusBadge variant="warning">{dna.crash_behavior || '-'}</StatusBadge>
              </div>
              <div>
                <div style={{ fontSize: 11, color: 'var(--neo-text-dim)', fontWeight: 600, marginBottom: 4 }}>RALLY BEHAVIOR</div>
                <StatusBadge variant="success">{dna.rally_behavior || '-'}</StatusBadge>
              </div>
            </div>
            {/* Sector biases as bars */}
            <div style={{ fontSize: 11, color: 'var(--neo-text-dim)', fontWeight: 600, marginBottom: 8 }}>SECTOR BIASES</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {Object.entries(dna.sector_biases || {}).map(([sector, weight]) => (
                <ProgressBar key={sector} label={sector} value={weight} max={40} color={styleColor(mgr.style)} />
              ))}
            </div>
          </GlassCard>
        </div>
      </div>

      {/* Track Record */}
      <GlassCard style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--neo-text)', marginBottom: 16 }}>
          <TrendingUp size={14} style={{ marginRight: 6, verticalAlign: 'middle', color: 'var(--neo-green)' }} />
          Track Record
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 12, marginBottom: 20 }}>
          <StatBox label="CAGR 10Y" value={pct(track.cagr_10y)} positive={track.cagr_10y > 0} />
          <StatBox label="CAGR 5Y" value={pct(track.cagr_5y)} positive={track.cagr_5y > 0} />
          <StatBox label="CAGR 3Y" value={pct(track.cagr_3y)} positive={track.cagr_3y > 0} />
          <StatBox label="Best Year" value={pct(track.best_year)} positive />
          <StatBox label="Worst Year" value={pct(track.worst_year)} positive={false} />
          <StatBox label="Max Drawdown" value={pct(track.max_drawdown)} positive={false} />
          <StatBox label="Sharpe" value={track.sharpe?.toFixed(2)} />
          <StatBox label="Sortino" value={track.sortino?.toFixed(2)} />
        </div>
        {/* Yearly Returns BarChart */}
        {yearlyData.length > 0 && (
          <div style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={yearlyData}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="year" tick={{ fill: 'var(--neo-text-muted)', fontSize: 11 }} />
                <YAxis tick={{ fill: 'var(--neo-text-muted)', fontSize: 11 }} tickFormatter={v => `${v}%`} />
                <Tooltip
                  contentStyle={{ background: 'var(--neo-bg-secondary)', border: '1px solid var(--neo-border)', borderRadius: 8, fontSize: 12 }}
                  formatter={(v) => [`${v.toFixed(1)}%`, 'Return']}
                />
                <Bar dataKey="return" radius={[4, 4, 0, 0]} fill={styleColor(mgr.style)}>
                  {yearlyData.map((entry, i) => (
                    <rect key={i} fill={entry.return >= 0 ? '#34d399' : '#ef4444'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </GlassCard>

      {/* Bottom: Conviction Stocks + Sensors */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
        {/* Conviction Stocks */}
        <GlassCard>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--neo-text)', marginBottom: 12 }}>
            <Star size={14} style={{ marginRight: 6, verticalAlign: 'middle', color: '#f59e0b' }} />
            Conviction Holdings
          </div>
          <table className="pms-table" style={{ margin: 0 }}>
            <thead>
              <tr>
                <th>Stock</th>
                <th style={{ textAlign: 'right' }}>Weight</th>
                <th style={{ textAlign: 'right' }}>Holding</th>
              </tr>
            </thead>
            <tbody>
              {(mgr.conviction_stocks || []).map((s, i) => (
                <tr key={i}>
                  <td style={{ fontWeight: 600 }}>{s.stock}</td>
                  <td style={{ textAlign: 'right' }} className="pms-number">{s.weight}%</td>
                  <td style={{ textAlign: 'right', color: 'var(--neo-text-dim)' }}>{s.holding_years}Y</td>
                </tr>
              ))}
            </tbody>
          </table>
        </GlassCard>

        {/* Sensor Bars + Fusion */}
        <GlassCard>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--neo-text)', marginBottom: 12 }}>
            <Layers size={14} style={{ marginRight: 6, verticalAlign: 'middle', color: 'var(--neo-blue)' }} />
            Context Sensors
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 16 }}>
            {['fundamental', 'momentum', 'sentiment', 'macro', 'technical'].map(s => (
              <ProgressBar key={s} label={s.charAt(0).toUpperCase() + s.slice(1)} value={sensors[s] || 0} max={100}
                color={sensors[s] >= 80 ? '#34d399' : sensors[s] >= 60 ? '#5b8af0' : sensors[s] >= 40 ? '#f59e0b' : '#ef4444'} />
            ))}
          </div>
          <div style={{
            padding: '12px 16px', borderRadius: 8,
            background: `${scoreColor(mgr.fusion_score)}10`, border: `1px solid ${scoreColor(mgr.fusion_score)}30`,
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--neo-text)' }}>Fusion Score</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 22, fontWeight: 700, color: scoreColor(mgr.fusion_score) }} className="pms-number">
                {mgr.fusion_score}
              </span>
              <span style={{
                fontSize: 12, fontWeight: 700, padding: '2px 8px', borderRadius: 4,
                background: `${scoreColor(mgr.fusion_score)}18`, color: scoreColor(mgr.fusion_score),
              }}>{scoreGrade(mgr.fusion_score)}</span>
            </div>
          </div>
        </GlassCard>
      </div>
    </div>
  );
}

function DNAMetric({ label, value, suffix = '', low = false }) {
  const color = low
    ? (value <= 20 ? '#34d399' : value <= 35 ? '#f59e0b' : '#ef4444')
    : (value >= 70 ? '#34d399' : value >= 50 ? '#5b8af0' : '#f59e0b');
  return (
    <div style={{ padding: '10px 12px', borderRadius: 8, background: 'rgba(255,255,255,0.02)', border: '1px solid var(--neo-border)' }}>
      <div style={{ fontSize: 10, color: 'var(--neo-text-dim)', fontWeight: 600, marginBottom: 4, textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, color }} className="pms-number">{value}{suffix}</div>
    </div>
  );
}

function StatBox({ label, value, positive }) {
  const color = positive === true ? 'var(--neo-green)' : positive === false ? 'var(--neo-red)' : 'var(--neo-text)';
  return (
    <div style={{ padding: '10px 12px', borderRadius: 8, background: 'rgba(255,255,255,0.02)', border: '1px solid var(--neo-border)' }}>
      <div style={{ fontSize: 10, color: 'var(--neo-text-dim)', fontWeight: 600, marginBottom: 4, textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 700, color }} className="pms-number">{value}</div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════
// TAB 3: COMPARE — Side-by-side
// ══════════════════════════════════════════════════════════════
function CompareTab({ managers, compare, setCompare }) {
  const data = managers.length ? managers : SAMPLE_MANAGERS;

  const handleSelect = (idx, name) => {
    const mgr = data.find(m => m.name === name) || null;
    setCompare(prev => {
      const next = [...prev];
      next[idx] = mgr;
      return next;
    });
  };

  const selected = compare.filter(Boolean);
  const colors = ['#5b8af0', '#34d399', '#a78bfa'];

  // Radar data
  const radarData = useMemo(() => {
    const metrics = [
      { key: 'conviction', label: 'Conviction' },
      { key: 'contrarian', label: 'Contrarian' },
      { key: 'churn', label: 'Low Churn' },
      { key: 'alpha', label: 'Alpha' },
      { key: 'hit_rate', label: 'Hit Rate' },
      { key: 'cash', label: 'Cash Mgmt' },
    ];
    return metrics.map(m => {
      const row = { metric: m.label };
      selected.forEach((mgr, i) => {
        const dna = mgr.behavioral_dna || {};
        switch (m.key) {
          case 'conviction': row[`m${i}`] = dna.concentration || 50; break;
          case 'contrarian': row[`m${i}`] = dna.contrarian_score || 50; break;
          case 'churn': row[`m${i}`] = 100 - (dna.churn || 25); break;
          case 'alpha': row[`m${i}`] = Math.min(100, (mgr.alpha_5y || 0) * 15 + 30); break;
          case 'hit_rate': row[`m${i}`] = (mgr.hit_rate || 0.5) * 100; break;
          case 'cash': row[`m${i}`] = mgr.fusion_score || 50; break;
          default: row[`m${i}`] = 50;
        }
      });
      return row;
    });
  }, [selected]);

  // Yearly returns multi-line
  const yearlyLineData = useMemo(() => {
    if (selected.length < 2) return [];
    const allYears = new Set();
    selected.forEach(m => {
      Object.keys(m.track_record?.yearly_returns || {}).forEach(y => allYears.add(y));
    });
    return [...allYears].sort().map(yr => {
      const row = { year: yr };
      selected.forEach((m, i) => {
        row[`m${i}`] = m.track_record?.yearly_returns?.[yr] ?? null;
      });
      return row;
    });
  }, [selected]);

  // Conviction overlap
  const overlapData = useMemo(() => {
    if (selected.length < 2) return [];
    const allStocks = new Set();
    selected.forEach(m => (m.conviction_stocks || []).forEach(s => allStocks.add(s.stock)));
    return [...allStocks].map(stock => {
      const row = { stock };
      selected.forEach((m, i) => {
        const found = (m.conviction_stocks || []).find(s => s.stock === stock);
        row[`m${i}`] = found ? `${found.weight}%` : '-';
      });
      return row;
    });
  }, [selected]);

  return (
    <div>
      {/* Selectors */}
      <GlassCard style={{ marginBottom: 20 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          {[0, 1, 2].map(idx => (
            <div key={idx}>
              <label style={{ fontSize: 11, color: 'var(--neo-text-dim)', fontWeight: 600, display: 'block', marginBottom: 6 }}>
                MANAGER {idx + 1}
              </label>
              <select
                className="pms-input"
                value={compare[idx]?.name || ''}
                onChange={(e) => handleSelect(idx, e.target.value)}
                style={{ width: '100%', fontSize: 13, borderColor: compare[idx] ? colors[idx] : undefined }}
              >
                <option value="">-- Select --</option>
                {data.map(m => (
                  <option key={m.name} value={m.name}>{m.name} ({m.fund_house})</option>
                ))}
              </select>
              {compare[idx] && (
                <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div style={{
                    width: 8, height: 8, borderRadius: '50%', background: colors[idx], flexShrink: 0,
                  }} />
                  <span style={{
                    fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 4,
                    background: `${styleColor(compare[idx].style)}18`, color: styleColor(compare[idx].style),
                  }}>{compare[idx].style}</span>
                  <span style={{ fontSize: 11, color: 'var(--neo-text-dim)' }}>Fusion: {compare[idx].fusion_score}</span>
                </div>
              )}
            </div>
          ))}
        </div>
      </GlassCard>

      {selected.length < 2 && (
        <GlassCard>
          <div style={{ textAlign: 'center', padding: 40, color: 'var(--neo-text-dim)' }}>
            <Users size={32} style={{ margin: '0 auto 12px', opacity: 0.4 }} />
            <div style={{ fontSize: 14, fontWeight: 600 }}>Select at least 2 managers to compare</div>
          </div>
        </GlassCard>
      )}

      {selected.length >= 2 && (
        <>
          {/* Style Comparison Table */}
          <GlassCard style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--neo-text)', marginBottom: 12 }}>Style Comparison</div>
            <div style={{ overflowX: 'auto' }}>
              <table className="pms-table">
                <thead>
                  <tr>
                    <th>Metric</th>
                    {selected.map((m, i) => (
                      <th key={i} style={{ color: colors[i] }}>{m.name}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[
                    { label: 'Style', fn: m => m.style },
                    { label: 'Fund House', fn: m => m.fund_house },
                    { label: 'AUM', fn: m => fmt(m.aum_cr) },
                    { label: 'Tenure', fn: m => `${m.tenure_years}Y` },
                    { label: 'Concentration', fn: m => `${m.behavioral_dna?.concentration || 0}%` },
                    { label: 'Churn', fn: m => `${m.behavioral_dna?.churn || 0}%` },
                    { label: 'Contrarian Score', fn: m => `${m.behavioral_dna?.contrarian_score || 0}%` },
                    { label: 'Crash Behavior', fn: m => m.behavioral_dna?.crash_behavior || '-' },
                    { label: 'Rally Behavior', fn: m => m.behavioral_dna?.rally_behavior || '-' },
                  ].map(row => (
                    <tr key={row.label}>
                      <td style={{ fontWeight: 600 }}>{row.label}</td>
                      {selected.map((m, i) => <td key={i}>{row.fn(m)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </GlassCard>

          {/* Track Record Table (best in green) */}
          <GlassCard style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--neo-text)', marginBottom: 12 }}>Track Record</div>
            <div style={{ overflowX: 'auto' }}>
              <table className="pms-table">
                <thead>
                  <tr>
                    <th>Metric</th>
                    {selected.map((m, i) => (
                      <th key={i} style={{ color: colors[i] }}>{m.name}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[
                    { label: 'CAGR 10Y', fn: m => m.track_record?.cagr_10y, higher: true },
                    { label: 'CAGR 5Y', fn: m => m.track_record?.cagr_5y, higher: true },
                    { label: 'CAGR 3Y', fn: m => m.track_record?.cagr_3y, higher: true },
                    { label: 'Alpha 5Y', fn: m => m.alpha_5y, higher: true },
                    { label: 'Hit Rate', fn: m => m.hit_rate, higher: true },
                    { label: 'Best Year', fn: m => m.track_record?.best_year, higher: true },
                    { label: 'Worst Year', fn: m => m.track_record?.worst_year, higher: true },
                    { label: 'Max Drawdown', fn: m => m.track_record?.max_drawdown, higher: true },
                    { label: 'Sharpe', fn: m => m.track_record?.sharpe, higher: true },
                    { label: 'Sortino', fn: m => m.track_record?.sortino, higher: true },
                    { label: 'Fusion Score', fn: m => m.fusion_score, higher: true },
                  ].map(row => {
                    const vals = selected.map(m => row.fn(m));
                    const best = row.higher ? Math.max(...vals.filter(v => v != null)) : Math.min(...vals.filter(v => v != null));
                    return (
                      <tr key={row.label}>
                        <td style={{ fontWeight: 600 }}>{row.label}</td>
                        {selected.map((m, i) => {
                          const v = row.fn(m);
                          const isBest = v === best && vals.filter(x => x === best).length === 1;
                          const formatted = row.label === 'Hit Rate' ? `${((v || 0) * 100).toFixed(0)}%`
                            : row.label === 'Fusion Score' ? v
                            : typeof v === 'number' ? (row.label.includes('Sharpe') || row.label.includes('Sortino') ? v.toFixed(2) : pct(v))
                            : '-';
                          return (
                            <td key={i} className="pms-number" style={{
                              color: isBest ? '#34d399' : 'var(--neo-text)',
                              fontWeight: isBest ? 700 : 400,
                            }}>{formatted}</td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </GlassCard>

          {/* Charts row */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, marginBottom: 20 }}>
            {/* Yearly returns multi-line */}
            <GlassCard>
              <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--neo-text)', marginBottom: 12 }}>Yearly Returns</div>
              <div style={{ height: 260 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={yearlyLineData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                    <XAxis dataKey="year" tick={{ fill: 'var(--neo-text-muted)', fontSize: 11 }} />
                    <YAxis tick={{ fill: 'var(--neo-text-muted)', fontSize: 11 }} tickFormatter={v => `${v}%`} />
                    <Tooltip
                      contentStyle={{ background: 'var(--neo-bg-secondary)', border: '1px solid var(--neo-border)', borderRadius: 8, fontSize: 12 }}
                      formatter={(v, name) => {
                        const idx = parseInt(name.replace('m', ''));
                        return [`${v?.toFixed(1)}%`, selected[idx]?.name || name];
                      }}
                    />
                    <Legend formatter={(value) => {
                      const idx = parseInt(value.replace('m', ''));
                      return selected[idx]?.name || value;
                    }} />
                    {selected.map((_, i) => (
                      <Line key={i} type="monotone" dataKey={`m${i}`} stroke={colors[i]}
                        strokeWidth={2} dot={{ r: 3 }} connectNulls />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </GlassCard>

            {/* Radar chart */}
            <GlassCard>
              <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--neo-text)', marginBottom: 12 }}>Manager DNA</div>
              <div style={{ height: 260 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <RadarChart data={radarData}>
                    <PolarGrid stroke="rgba(255,255,255,0.08)" />
                    <PolarAngleAxis dataKey="metric" tick={{ fill: 'var(--neo-text-muted)', fontSize: 10 }} />
                    <PolarRadiusAxis angle={30} domain={[0, 100]} tick={false} />
                    {selected.map((_, i) => (
                      <RechartsRadar key={i} name={selected[i]?.name || `M${i + 1}`}
                        dataKey={`m${i}`} stroke={colors[i]} fill={colors[i]} fillOpacity={0.15} />
                    ))}
                    <Legend formatter={(value) => value} />
                    <Tooltip
                      contentStyle={{ background: 'var(--neo-bg-secondary)', border: '1px solid var(--neo-border)', borderRadius: 8, fontSize: 12 }}
                    />
                  </RadarChart>
                </ResponsiveContainer>
              </div>
            </GlassCard>
          </div>

          {/* Conviction Overlap */}
          <GlassCard>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--neo-text)', marginBottom: 12 }}>Conviction Overlap</div>
            <div style={{ overflowX: 'auto' }}>
              <table className="pms-table">
                <thead>
                  <tr>
                    <th>Stock</th>
                    {selected.map((m, i) => (
                      <th key={i} style={{ color: colors[i] }}>{m.name}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {overlapData.map((row, ri) => {
                    const present = selected.map((_, i) => row[`m${i}`] !== '-');
                    const isOverlap = present.filter(Boolean).length >= 2;
                    return (
                      <tr key={ri} style={{ background: isOverlap ? 'rgba(52, 211, 153, 0.04)' : undefined }}>
                        <td style={{ fontWeight: 600 }}>
                          {row.stock}
                          {isOverlap && <span style={{ marginLeft: 6, fontSize: 10, color: '#34d399' }}>OVERLAP</span>}
                        </td>
                        {selected.map((_, i) => (
                          <td key={i} className="pms-number" style={{ color: row[`m${i}`] !== '-' ? 'var(--neo-text)' : 'var(--neo-text-dim)' }}>
                            {row[`m${i}`]}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </GlassCard>
        </>
      )}
    </div>
  );
}

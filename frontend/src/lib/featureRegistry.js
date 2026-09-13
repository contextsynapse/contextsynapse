/**
 * Feature Plugin Registry — reusable features that any vertical can enable.
 *
 * A feature plugin is a page/capability that isn't tied to one vertical.
 * Each vertical declares which features it wants in its config.
 *
 * Usage:
 *   import { getEnabledFeatures, isFeatureEnabled } from '../lib/featureRegistry';
 *
 *   const features = getEnabledFeatures('pms');
 *   // → [{id: 'mf_lab', label: 'MF Lab', path: '/pms/mf-lab', icon: 'FlaskConical'}, ...]
 *
 *   if (isFeatureEnabled('pms', 'mf_lab')) {
 *     // render MF Lab in PMS sidebar
 *   }
 */

// ── All available feature plugins ──────────────────────────────
export const FEATURE_PLUGINS = {
  mf_lab: {
    id: 'mf_lab',
    label: 'MF Lab',
    description: 'Mutual Fund research — explore schemes, compare, track flows',
    icon: 'FlaskConical',
    component: 'MFLabPage',
    category: 'research',
    path_suffix: 'mf-lab',
  },
  stock_hud: {
    id: 'stock_hud',
    label: 'Stock HUD',
    description: 'Sensor fusion HUD for individual stocks',
    icon: 'Crosshair',
    component: 'StockHUDPage',
    category: 'analysis',
    path_suffix: 'stock/:stockId',
  },
  screener: {
    id: 'screener',
    label: 'Screener',
    description: 'Fundamental stock screener with filters',
    icon: 'Target',
    component: 'ScreenerPage',
    category: 'research',
    path_suffix: 'screener',
  },
  risk_dashboard: {
    id: 'risk_dashboard',
    label: 'Risk Dashboard',
    description: 'Portfolio risk analytics — VaR, drawdown, stress tests',
    icon: 'AlertTriangle',
    component: 'RiskDashboardPage',
    category: 'risk',
    path_suffix: 'risk',
  },
  trade_book: {
    id: 'trade_book',
    label: 'Trade Book',
    description: 'Full trade lifecycle management',
    icon: 'ClipboardList',
    component: 'TradeBookPage',
    category: 'trading',
    path_suffix: 'trades',
  },
  compliance: {
    id: 'compliance',
    label: 'Compliance',
    description: 'SEBI regulatory compliance monitoring',
    icon: 'Shield',
    component: 'ComplianceCenterPage',
    category: 'compliance',
    path_suffix: 'compliance',
  },
  sensor_studio: {
    id: 'sensor_studio',
    label: 'Sensor Studio',
    description: 'Configure and monitor data pipelines',
    icon: 'Radar',
    component: 'SensorStudioPage',
    category: 'sensors',
    path_suffix: 'sensors',
  },
  strategy_lab: {
    id: 'strategy_lab',
    label: 'Strategy Lab',
    description: 'Backtesting and simulation',
    icon: 'FlaskConical',
    component: 'StrategyLabPage',
    category: 'trading',
    path_suffix: 'backtest',
  },
  operations: {
    id: 'operations',
    label: 'Operations Center',
    description: 'Settlements, corporate actions, invoices',
    icon: 'Settings',
    component: 'OperationsCenterPage',
    category: 'operations',
    path_suffix: 'operations',
  },
  cash_management: {
    id: 'cash_management',
    label: 'Cash Ledger',
    description: 'Deposits, withdrawals, fees, dividends',
    icon: 'IndianRupee',
    component: 'CashManagementPage',
    category: 'operations',
    path_suffix: 'cash',
  },
  client_management: {
    id: 'client_management',
    label: 'Client Management',
    description: 'Onboard and manage clients',
    icon: 'Users',
    component: 'ClientManagementPage',
    category: 'clients',
    path_suffix: 'clients',
  },
  research_desk: {
    id: 'research_desk',
    label: 'Research Desk',
    description: 'Ingest and analyze research content',
    icon: 'BookOpen',
    component: 'ResearchDeskPage',
    category: 'research',
    path_suffix: 'research',
  },
};

// ── Vertical feature configurations ────────────────────────────
// Each vertical declares which features it enables.
// This can be overridden by user/tenant settings.

const VERTICAL_FEATURES = {
  pms: [
    'stock_hud', 'screener', 'risk_dashboard', 'trade_book',
    'compliance', 'sensor_studio', 'strategy_lab', 'operations',
    'cash_management', 'client_management', 'research_desk',
    'mf_lab',  // MF research available in PMS too
  ],
  mf: [
    'mf_lab', 'compliance', 'operations', 'risk_dashboard',
    'sensor_studio',
  ],
  wealth: [
    'stock_hud', 'mf_lab', 'risk_dashboard', 'trade_book',
    'compliance', 'screener', 'client_management',
  ],
};

// ── User-level overrides (stored in localStorage) ──────────────
const STORAGE_KEY = 'contextsynapse_feature_overrides';

function _getUserOverrides(vertical) {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const overrides = JSON.parse(raw);
    return overrides[vertical] || null;
  } catch { return null; }
}

function _setUserOverrides(vertical, features) {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const overrides = raw ? JSON.parse(raw) : {};
    overrides[vertical] = features;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(overrides));
  } catch {}
}

// ── Public API ─────────────────────────────────────────────────

/**
 * Get all enabled features for a vertical.
 * Returns feature objects with path resolved for the vertical.
 */
export function getEnabledFeatures(vertical) {
  const userOverrides = _getUserOverrides(vertical);
  const enabledIds = userOverrides || VERTICAL_FEATURES[vertical] || [];

  return enabledIds
    .filter(id => FEATURE_PLUGINS[id])
    .map(id => {
      const plugin = FEATURE_PLUGINS[id];
      return {
        ...plugin,
        path: `/${vertical}/${plugin.path_suffix}`,
      };
    });
}

/**
 * Check if a specific feature is enabled for a vertical.
 */
export function isFeatureEnabled(vertical, featureId) {
  const userOverrides = _getUserOverrides(vertical);
  const enabledIds = userOverrides || VERTICAL_FEATURES[vertical] || [];
  return enabledIds.includes(featureId);
}

/**
 * Get all available features (for the feature picker UI).
 */
export function getAllFeatures() {
  return Object.values(FEATURE_PLUGINS);
}

/**
 * Enable/disable a feature for a vertical (user preference).
 */
export function toggleFeature(vertical, featureId, enabled) {
  const current = _getUserOverrides(vertical) || [...(VERTICAL_FEATURES[vertical] || [])];
  if (enabled && !current.includes(featureId)) {
    current.push(featureId);
  } else if (!enabled) {
    const idx = current.indexOf(featureId);
    if (idx >= 0) current.splice(idx, 1);
  }
  _setUserOverrides(vertical, current);
}

/**
 * Reset to default features for a vertical.
 */
export function resetFeatures(vertical) {
  _setUserOverrides(vertical, null);
}

/**
 * Get features grouped by category.
 */
export function getFeaturesByCategory(vertical) {
  const features = getEnabledFeatures(vertical);
  const groups = {};
  for (const f of features) {
    const cat = f.category || 'other';
    if (!groups[cat]) groups[cat] = [];
    groups[cat].push(f);
  }
  return groups;
}

// frontend/src/pages/dashboard/AutomationsPage.js
// Fund managers see "Automations" — not "Agents"
// Each automation maps to a skill + trigger + threshold behind the scenes
import React, { useState } from 'react';
import {
  Bell, Clock, Shield, TrendingDown, BarChart3, FileText,
  Sun, AlertTriangle, Zap, Power, ChevronDown, ChevronUp
} from 'lucide-react';

const AUTOMATIONS = [
  {
    id: 'morning_briefing',
    name: 'Morning Briefing',
    description: 'Daily summary of overnight markets, FII flows, and portfolio impact — ready when you arrive',
    icon: Sun,
    color: '#f59e0b',
    skill: '/morning-briefing',
    trigger: 'schedule',
    defaultSettings: { enabled: true, time: '07:30', days: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'] },
    configFields: [
      { key: 'time', label: 'Delivery Time', type: 'time' },
    ],
  },
  {
    id: 'compliance_guard',
    name: 'Compliance Guard',
    description: 'Automatically checks every trade against SEBI rules before execution — blocks non-compliant trades',
    icon: Shield,
    color: '#22c55e',
    skill: '/pre-trade-check',
    trigger: 'pre_trade',
    defaultSettings: { enabled: true },
    configFields: [],
    alwaysOn: true,
  },
  {
    id: 'drift_alert',
    name: 'Portfolio Drift Alert',
    description: 'Alerts when any portfolio drifts beyond threshold from target allocation',
    icon: BarChart3,
    color: '#3b82f6',
    skill: '/portfolio-health',
    trigger: 'schedule',
    defaultSettings: { enabled: true, threshold: 5, check_interval: 'daily' },
    configFields: [
      { key: 'threshold', label: 'Drift Threshold (%)', type: 'number', min: 1, max: 20 },
      { key: 'check_interval', label: 'Check Frequency', type: 'select',
        options: ['hourly', 'daily', 'weekly'] },
    ],
  },
  {
    id: 'fii_flow_alert',
    name: 'FII Flow Alert',
    description: 'Alerts when FII selling continues for consecutive days — early warning for market pressure',
    icon: TrendingDown,
    color: '#ef4444',
    skill: '/signal-detect',
    trigger: 'on_ingest',
    defaultSettings: { enabled: true, consecutive_days: 3, min_amount: 1000 },
    configFields: [
      { key: 'consecutive_days', label: 'Consecutive Selling Days', type: 'number', min: 2, max: 10 },
      { key: 'min_amount', label: 'Min Amount (₹ Cr)', type: 'number', min: 100, max: 5000 },
    ],
  },
  {
    id: 'concentration_alert',
    name: 'Concentration Alert',
    description: 'Warns when single stock or sector exceeds safe concentration limits',
    icon: AlertTriangle,
    color: '#f59e0b',
    skill: '/compliance-check',
    trigger: 'on_change',
    defaultSettings: { enabled: true, stock_limit: 10, sector_limit: 25, top5_limit: 40 },
    configFields: [
      { key: 'stock_limit', label: 'Single Stock Limit (%)', type: 'number', min: 5, max: 15 },
      { key: 'sector_limit', label: 'Sector Limit (%)', type: 'number', min: 15, max: 35 },
      { key: 'top5_limit', label: 'Top 5 Limit (%)', type: 'number', min: 30, max: 50 },
    ],
  },
  {
    id: 'monthly_report',
    name: 'Monthly Client Report',
    description: 'Auto-generates portfolio performance report for all clients at month end',
    icon: FileText,
    color: '#8b5cf6',
    skill: '/client-report',
    trigger: 'schedule',
    defaultSettings: { enabled: false, day_of_month: 1, include_tax: true },
    configFields: [
      { key: 'day_of_month', label: 'Generate On Day', type: 'number', min: 1, max: 28 },
      { key: 'include_tax', label: 'Include Tax Impact', type: 'toggle' },
    ],
  },
  {
    id: 'earnings_alert',
    name: 'Earnings Alert',
    description: 'Notifies before and after earnings of stocks in your portfolio',
    icon: Zap,
    color: '#06b6d4',
    skill: '/earnings-digest',
    trigger: 'schedule',
    defaultSettings: { enabled: true, days_before: 2, notify_results: true },
    configFields: [
      { key: 'days_before', label: 'Alert Days Before Results', type: 'number', min: 1, max: 7 },
      { key: 'notify_results', label: 'Notify When Results Published', type: 'toggle' },
    ],
  },
  {
    id: 'price_alert',
    name: 'Price Movement Alert',
    description: 'Alerts when any holding moves more than threshold in a single day',
    icon: Bell,
    color: '#ec4899',
    skill: '/signal-detect',
    trigger: 'on_change',
    defaultSettings: { enabled: true, move_threshold: 3 },
    configFields: [
      { key: 'move_threshold', label: 'Price Move Threshold (%)', type: 'number', min: 1, max: 10 },
    ],
  },
];

const AutomationCard = ({ automation, settings, onUpdate }) => {
  const [expanded, setExpanded] = useState(false);
  const isEnabled = settings?.enabled ?? automation.defaultSettings.enabled;
  const Icon = automation.icon;

  const handleToggle = () => {
    if (automation.alwaysOn) return;
    onUpdate(automation.id, { ...settings, enabled: !isEnabled });
  };

  const handleFieldChange = (key, value) => {
    onUpdate(automation.id, { ...settings, [key]: value });
  };

  return (
    <div className="rounded-lg overflow-hidden" style={{
      background: 'var(--neo-card-bg, #111827)',
      border: `1px solid ${isEnabled ? automation.color + '40' : 'var(--neo-border, #1f2937)'}`,
    }}>
      {/* Header */}
      <div className="p-4">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg" style={{ background: automation.color + '15' }}>
              <Icon size={18} style={{ color: automation.color }} />
            </div>
            <div>
              <h3 className="text-sm font-semibold" style={{ color: 'var(--neo-text)' }}>
                {automation.name}
                {automation.alwaysOn && (
                  <span className="text-xs ml-2 px-1.5 py-0.5 rounded bg-green-900 text-green-300">Always On</span>
                )}
              </h3>
              <p className="text-xs mt-0.5" style={{ color: 'var(--neo-text-muted)' }}>
                {automation.description}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {!automation.alwaysOn && (
              <button onClick={handleToggle} className="p-1.5 rounded" style={{
                background: isEnabled ? 'rgba(34, 197, 94, 0.1)' : 'rgba(239, 68, 68, 0.1)',
                border: `1px solid ${isEnabled ? '#22c55e' : '#ef4444'}`,
              }}>
                <Power size={14} style={{ color: isEnabled ? '#22c55e' : '#ef4444' }} />
              </button>
            )}
            {automation.configFields.length > 0 && isEnabled && (
              <button onClick={() => setExpanded(!expanded)} className="p-1.5 rounded"
                style={{ border: '1px solid var(--neo-border)' }}>
                {expanded ? <ChevronUp size={14} style={{ color: 'var(--neo-text-muted)' }} />
                          : <ChevronDown size={14} style={{ color: 'var(--neo-text-muted)' }} />}
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Settings (expandable) */}
      {expanded && isEnabled && automation.configFields.length > 0 && (
        <div className="px-4 pb-4 pt-0 border-t" style={{ borderColor: 'var(--neo-border)' }}>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
            {automation.configFields.map(f => (
              <div key={f.key}>
                <label className="text-xs block mb-1" style={{ color: 'var(--neo-text-muted)' }}>{f.label}</label>
                {f.type === 'number' && (
                  <input type="number" min={f.min} max={f.max}
                    value={settings?.[f.key] ?? automation.defaultSettings[f.key]}
                    onChange={e => handleFieldChange(f.key, parseInt(e.target.value))}
                    className="w-full px-2 py-1 rounded text-sm"
                    style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
                )}
                {f.type === 'time' && (
                  <input type="time"
                    value={settings?.[f.key] ?? automation.defaultSettings[f.key]}
                    onChange={e => handleFieldChange(f.key, e.target.value)}
                    className="w-full px-2 py-1 rounded text-sm"
                    style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
                )}
                {f.type === 'select' && (
                  <select value={settings?.[f.key] ?? automation.defaultSettings[f.key]}
                    onChange={e => handleFieldChange(f.key, e.target.value)}
                    className="w-full px-2 py-1 rounded text-sm"
                    style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}>
                    {f.options.map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                )}
                {f.type === 'toggle' && (
                  <button
                    onClick={() => handleFieldChange(f.key, !(settings?.[f.key] ?? automation.defaultSettings[f.key]))}
                    className="px-3 py-1 rounded text-xs"
                    style={{
                      background: (settings?.[f.key] ?? automation.defaultSettings[f.key])
                        ? 'rgba(34, 197, 94, 0.2)' : 'rgba(239, 68, 68, 0.1)',
                      border: `1px solid ${(settings?.[f.key] ?? automation.defaultSettings[f.key]) ? '#22c55e' : '#ef4444'}`,
                      color: (settings?.[f.key] ?? automation.defaultSettings[f.key]) ? '#22c55e' : '#ef4444',
                    }}>
                    {(settings?.[f.key] ?? automation.defaultSettings[f.key]) ? 'Yes' : 'No'}
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export default function AutomationsPage() {
  const [settings, setSettings] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem('pms_automations') || '{}');
    } catch { return {}; }
  });

  const handleUpdate = (id, newSettings) => {
    const updated = { ...settings, [id]: newSettings };
    setSettings(updated);
    localStorage.setItem('pms_automations', JSON.stringify(updated));
  };

  const activeCount = AUTOMATIONS.filter(a =>
    a.alwaysOn || (settings[a.id]?.enabled ?? a.defaultSettings.enabled)
  ).length;

  return (
    <div className="max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold" style={{ color: 'var(--neo-text)' }}>Automations</h1>
          <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
            {activeCount} of {AUTOMATIONS.length} active — the system monitors your portfolio and alerts you automatically
          </p>
        </div>
      </div>

      <div className="space-y-3">
        {AUTOMATIONS.map(a => (
          <AutomationCard key={a.id} automation={a}
            settings={settings[a.id] || a.defaultSettings}
            onUpdate={handleUpdate} />
        ))}
      </div>

      <div className="mt-6 rounded-lg p-4" style={{ background: 'var(--neo-card-bg)', border: '1px solid var(--neo-border)' }}>
        <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
          Automations run in the background. When triggered, alerts appear in your Command Center.
          Compliance Guard is always on — it cannot be disabled (SEBI requirement).
        </p>
      </div>
    </div>
  );
}

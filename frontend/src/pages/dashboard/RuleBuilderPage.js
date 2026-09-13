// frontend/src/pages/dashboard/RuleBuilderPage.js
// InRule-style visual Business Rules Management System for PMS
import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import {
  Shield, Plus, Trash2, Lock, Play, CheckCircle, XCircle,
  ChevronDown, Save, ToggleLeft, ToggleRight, AlertTriangle,
  Bell, Ban, RefreshCw, FileText, Loader, Info,
} from 'lucide-react';

const pms = axios.create({ baseURL: '', timeout: 30000 });

// ── Constants (style only — all data comes from API) ─────────────────────────

const SEVERITY_STYLE = {
  critical: { dot: '#dc2626', badge: 'rgba(220,38,38,0.15)', text: '#f87171', border: 'rgba(220,38,38,0.3)' },
  high:     { dot: '#f59e0b', badge: 'rgba(245,158,11,0.15)', text: '#fbbf24', border: 'rgba(245,158,11,0.3)' },
  medium:   { dot: '#3b82f6', badge: 'rgba(59,130,246,0.15)', text: '#93c5fd', border: 'rgba(59,130,246,0.3)' },
  low:      { dot: '#22c55e', badge: 'rgba(34,197,94,0.15)',  text: '#4ade80', border: 'rgba(34,197,94,0.3)' },
  info:     { dot: '#6b7280', badge: 'rgba(107,114,128,0.15)', text: '#9ca3af', border: 'rgba(107,114,128,0.3)' },
};

const ACTION_COLOR = {
  block:     '#dc2626',
  warn:      '#f59e0b',
  alert:     '#3b82f6',
  rebalance: '#22c55e',
  report:    '#8b5cf6',
};

// ── Helpers ──────────────────────────────────────────────────────────────────

function emptyRule(meta) {
  const firstField = meta?.fields?.[0]?.id || 'stock_weight';
  const firstOp = meta?.operators?.[0]?.id || 'gt';
  const firstAction = meta?.action_types?.[0]?.id || 'alert';
  const defaultCategory = meta?.categories?.[0]?.id || 'custom';
  return {
    rule_id: null,
    name: '',
    description: '',
    category: defaultCategory,
    severity: 'medium',
    conditions: [{ field: firstField, operator: firstOp, value: 10, unit: 'percent', connector: 'AND' }],
    conditions_operator: 'AND',
    actions: [{ type: firstAction, message: '' }],
    scope: 'all',
    triggers: ['on_demand'],
    enabled: true,
    readonly: false,
  };
}

function isBuiltin(rule) {
  return rule && rule.readonly === true;
}

// ── Sub-components ───────────────────────────────────────────────────────────

function SeverityDot({ severity }) {
  const s = SEVERITY_STYLE[severity] || SEVERITY_STYLE.medium;
  return (
    <span
      style={{
        display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
        background: s.dot, flexShrink: 0,
      }}
    />
  );
}

function CategoryBadge({ category }) {
  const colors = {
    sebi:      { bg: 'rgba(220,38,38,0.12)', text: '#f87171' },
    custom:    { bg: 'rgba(139,92,246,0.12)', text: '#a78bfa' },
    compliance:{ bg: 'rgba(245,158,11,0.12)', text: '#fbbf24' },
    risk:      { bg: 'rgba(251,146,60,0.12)', text: '#fb923c' },
    portfolio: { bg: 'rgba(34,197,94,0.12)', text: '#4ade80' },
  };
  const c = colors[category] || colors.custom;
  return (
    <span
      style={{
        fontSize: 10, padding: '1px 6px', borderRadius: 4,
        background: c.bg, color: c.text, fontWeight: 600, textTransform: 'uppercase',
        letterSpacing: '0.04em',
      }}
    >
      {category}
    </span>
  );
}

function RuleCard({ rule, selected, onClick, onToggle, onDelete }) {
  const builtin = isBuiltin(rule);
  const sev = SEVERITY_STYLE[rule.severity] || SEVERITY_STYLE.medium;
  return (
    <div
      onClick={() => onClick(rule)}
      style={{
        padding: '10px 12px', borderRadius: 8, cursor: 'pointer',
        background: selected ? 'rgba(59,130,246,0.08)' : 'transparent',
        border: `1px solid ${selected ? 'rgba(59,130,246,0.35)' : 'var(--neo-border, #1f2937)'}`,
        transition: 'all 0.15s',
        marginBottom: 6,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
        <SeverityDot severity={rule.severity} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 3 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--neo-text, #f9fafb)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {rule.name || 'Untitled Rule'}
            </span>
            {builtin && <Lock size={10} style={{ color: 'var(--neo-text-dim, #6b7280)', flexShrink: 0 }} />}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <CategoryBadge category={rule.category} />
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
          {!builtin && (
            <button
              onClick={(e) => { e.stopPropagation(); onDelete(rule.rule_id); }}
              title="Delete rule"
              style={{
                background: 'transparent', border: 'none', cursor: 'pointer',
                color: 'var(--neo-text-dim, #6b7280)', padding: 2, borderRadius: 4,
                display: 'flex', alignItems: 'center',
              }}
            >
              <Trash2 size={13} />
            </button>
          )}
          <button
            onClick={(e) => { e.stopPropagation(); !builtin && onToggle(rule); }}
            disabled={builtin}
            title={builtin ? 'SEBI rules cannot be disabled' : rule.enabled ? 'Disable' : 'Enable'}
            style={{
              background: 'transparent', border: 'none',
              cursor: builtin ? 'not-allowed' : 'pointer',
              color: rule.enabled ? sev.dot : 'var(--neo-text-dim, #6b7280)',
              padding: 2, display: 'flex', alignItems: 'center',
              opacity: builtin ? 0.5 : 1,
            }}
          >
            {rule.enabled ? <ToggleRight size={18} /> : <ToggleLeft size={18} />}
          </button>
        </div>
      </div>
    </div>
  );
}

function ConditionRow({ cond, index, isLast, onChange, onDelete, fields, operators, readonly }) {
  const fieldOpt = fields.find(f => f.id === cond.field) || fields[0] || {};
  const connector = cond.connector || 'AND';
  return (
    <div>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '8px 10px', borderRadius: 6,
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid var(--neo-border, #1f2937)',
      }}>
        <span style={{ fontSize: 11, color: 'var(--neo-text-dim, #6b7280)', minWidth: 14, textAlign: 'center' }}>
          {index + 1}
        </span>
        <select
          value={cond.field}
          onChange={e => onChange(index, 'field', e.target.value)}
          disabled={readonly}
          style={selectStyle}
        >
          {fields.map(f => (
            <option key={f.id} value={f.id}>{f.label}</option>
          ))}
        </select>
        <select
          value={cond.operator}
          onChange={e => onChange(index, 'operator', e.target.value)}
          disabled={readonly}
          style={{ ...selectStyle, width: 60, textAlign: 'center' }}
        >
          {operators.map(o => (
            <option key={o.id} value={o.id}>{o.label}</option>
          ))}
        </select>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, flex: 1 }}>
          <input
            type="number"
            value={cond.value}
            onChange={e => onChange(index, 'value', parseFloat(e.target.value) || 0)}
            disabled={readonly}
            style={{ ...inputStyle, width: 80 }}
            min={0}
            step={0.5}
          />
          <span style={{ fontSize: 12, color: 'var(--neo-text-dim, #6b7280)', minWidth: 20 }}>
            {fieldOpt.unit || ''}
          </span>
        </div>
        {!readonly && (
          <button
            onClick={() => onDelete(index)}
            style={{
              background: 'transparent', border: 'none', cursor: 'pointer',
              color: 'var(--neo-text-dim, #6b7280)', padding: 3, borderRadius: 4,
              display: 'flex', alignItems: 'center',
            }}
          >
            <XCircle size={14} />
          </button>
        )}
      </div>

      {/* Per-row connector — shown between rows, not after the last */}
      {!isLast && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 0 4px 22px' }}>
          <div style={{ height: 1, width: 12, background: 'var(--neo-border, #1f2937)' }} />
          {['AND', 'OR'].map(op => (
            <button
              key={op}
              onClick={() => !readonly && onChange(index, 'connector', op)}
              disabled={readonly}
              style={{
                padding: '2px 8px', borderRadius: 4, fontSize: 10, fontWeight: 700,
                cursor: readonly ? 'not-allowed' : 'pointer',
                border: '1px solid',
                borderColor: connector === op ? (op === 'AND' ? 'rgba(59,130,246,0.5)' : 'rgba(245,158,11,0.5)') : 'var(--neo-border, #1f2937)',
                background: connector === op ? (op === 'AND' ? 'rgba(59,130,246,0.15)' : 'rgba(245,158,11,0.12)') : 'transparent',
                color: connector === op ? (op === 'AND' ? '#93c5fd' : '#fbbf24') : 'var(--neo-text-dim, #6b7280)',
                transition: 'all 0.15s',
                letterSpacing: '0.05em',
              }}
            >
              {op}
            </button>
          ))}
          <div style={{ height: 1, flex: 1, background: 'var(--neo-border, #1f2937)' }} />
        </div>
      )}
    </div>
  );
}

function ActionRow({ action, index, onChange, onDelete, actionTypes }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8,
      padding: '8px 10px', borderRadius: 6,
      background: 'rgba(255,255,255,0.03)',
      border: '1px solid var(--neo-border, #1f2937)',
      marginBottom: 6,
    }}>
      <select
        value={action.type}
        onChange={e => onChange(index, 'type', e.target.value)}
        style={{ ...selectStyle, width: 130 }}
      >
        {actionTypes.map(a => (
          <option key={a.id} value={a.id}>{a.label}</option>
        ))}
      </select>
      <input
        type="text"
        value={action.message}
        onChange={e => onChange(index, 'message', e.target.value)}
        placeholder="Message template (use {max_stock_weight}, {top5_concentration}...)"
        style={{ ...inputStyle, flex: 1, fontSize: 12 }}
      />
      <button
        onClick={() => onDelete(index)}
        style={{
          background: 'transparent', border: 'none', cursor: 'pointer',
          color: 'var(--neo-text-dim, #6b7280)', padding: 3, borderRadius: 4,
          display: 'flex', alignItems: 'center',
        }}
      >
        <XCircle size={14} />
      </button>
    </div>
  );
}

// ── Shared Styles ─────────────────────────────────────────────────────────────

const selectStyle = {
  background: 'var(--neo-bg, #0d1117)',
  border: '1px solid var(--neo-border, #1f2937)',
  color: 'var(--neo-text, #f9fafb)',
  borderRadius: 6,
  padding: '5px 8px',
  fontSize: 13,
  outline: 'none',
};

const inputStyle = {
  background: 'var(--neo-bg, #0d1117)',
  border: '1px solid var(--neo-border, #1f2937)',
  color: 'var(--neo-text, #f9fafb)',
  borderRadius: 6,
  padding: '5px 10px',
  fontSize: 13,
  outline: 'none',
};

const labelStyle = {
  fontSize: 11,
  fontWeight: 600,
  color: 'var(--neo-text-muted, #9ca3af)',
  textTransform: 'uppercase',
  letterSpacing: '0.06em',
  marginBottom: 5,
  display: 'block',
};

const sectionStyle = {
  borderRadius: 8,
  border: '1px solid var(--neo-border, #1f2937)',
  background: 'rgba(255,255,255,0.02)',
  padding: 14,
  marginBottom: 12,
};

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function RuleBuilderPage() {
  // Schema metadata loaded from API
  const [meta, setMeta] = useState({ fields: [], operators: [], action_types: [], categories: [], severities: [], triggers: [] });
  const [builtinRules, setBuiltinRules] = useState([]);
  const [customRules, setCustomRules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');

  const [activeTab, setActiveTab] = useState('all');
  const [selectedRule, setSelectedRule] = useState(null);
  const [editor, setEditor] = useState(emptyRule(null));
  const [editorDirty, setEditorDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState('');

  // Test panel
  const [portfolios, setPortfolios] = useState([]);
  const [testPortfolioId, setTestPortfolioId] = useState('');
  const [testRunning, setTestRunning] = useState(false);
  const [testResult, setTestResult] = useState(null);

  useEffect(() => {
    loadSchema();
    loadPortfolios();
  }, []);

  // Phase 1: load schema + rules (unblocks editor immediately)
  async function loadSchema() {
    setLoading(true);
    setLoadError('');
    try {
      const [metaRes, builtinRes, customRes] = await Promise.all([
        pms.get('/pms/rules/meta'),
        pms.get('/pms/rules/builtin'),
        pms.get('/pms/rules'),
      ]);
      const fetchedMeta = metaRes.data || {};
      setMeta(fetchedMeta);
      setBuiltinRules(builtinRes.data || []);
      setCustomRules(customRes.data || []);
      setEditor(emptyRule(fetchedMeta));
    } catch (e) {
      setLoadError('Failed to load rules schema. ' + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  }

  // Phase 2: load portfolios independently (doesn't block editor)
  async function loadPortfolios() {
    try {
      const res = await pms.get('/pms/portfolios');
      const ports = res.data || [];
      setPortfolios(ports);
      if (ports.length > 0) setTestPortfolioId(ports[0].portfolio_id || ports[0].id || '');
    } catch (e) {
      // non-blocking — test panel will show empty state
    }
  }

  async function loadRules() {
    try {
      const res = await pms.get('/pms/rules');
      setCustomRules(res.data || []);
    } catch (e) {
      // silently ignore reload errors
    }
  }

  // Category tabs: 'all' + unique categories from builtin + custom + meta
  const allCategories = ['all', ...meta.categories.map(c => c.id), 'sebi'];
  const categoryTabs = [...new Set(allCategories)];

  // Filtered library rules
  const allLibraryRules = [...builtinRules, ...customRules];
  const filteredRules = activeTab === 'all'
    ? allLibraryRules
    : allLibraryRules.filter(r => r.category === activeTab);

  // Select a rule for editing
  function selectRule(rule) {
    setSelectedRule(rule);
    // Migrate legacy conditions (no per-row connector) using global conditions_operator as default
    const defaultConn = rule.conditions_operator || 'AND';
    const conditions = (rule.conditions || []).map(c => ({
      ...c,
      connector: c.connector || defaultConn,
    }));
    setEditor({ ...rule, conditions });
    setEditorDirty(false);
    setTestResult(null);
    setSaveMsg('');
  }

  // Start a new rule
  function newRule() {
    setSelectedRule(null);
    setEditor(emptyRule(meta));
    setEditorDirty(false);
    setTestResult(null);
    setSaveMsg('');
  }

  // Update editor field
  function setEditorField(field, value) {
    setEditor(prev => ({ ...prev, [field]: value }));
    setEditorDirty(true);
  }

  // Condition helpers
  function addCondition() {
    const firstField = meta?.fields?.[0]?.id || 'stock_weight';
    const firstOp = meta?.operators?.[0]?.id || 'gt';
    setEditorField('conditions', [
      ...(editor.conditions || []),
      { field: firstField, operator: firstOp, value: 0, unit: 'percent', connector: 'AND' },
    ]);
  }

  function updateCondition(index, key, value) {
    const updated = (editor.conditions || []).map((c, i) =>
      i === index ? { ...c, [key]: value } : c
    );
    setEditorField('conditions', updated);
  }

  function deleteCondition(index) {
    setEditorField('conditions', (editor.conditions || []).filter((_, i) => i !== index));
  }

  // Action helpers
  function addAction() {
    const firstAction = meta?.action_types?.[0]?.id || 'alert';
    setEditorField('actions', [
      ...(editor.actions || []),
      { type: firstAction, message: '' },
    ]);
  }

  function updateAction(index, key, value) {
    const updated = (editor.actions || []).map((a, i) =>
      i === index ? { ...a, [key]: value } : a
    );
    setEditorField('actions', updated);
  }

  function deleteAction(index) {
    setEditorField('actions', (editor.actions || []).filter((_, i) => i !== index));
  }

  // Save rule
  async function saveRule() {
    if (!editor.name.trim()) {
      setSaveMsg('Rule name is required.');
      return;
    }
    if (!editor.conditions || editor.conditions.length === 0) {
      setSaveMsg('At least one condition is required.');
      return;
    }
    if (!editor.actions || editor.actions.length === 0) {
      setSaveMsg('At least one action is required.');
      return;
    }
    setSaving(true);
    setSaveMsg('');
    try {
      const payload = {
        name: editor.name,
        description: editor.description || '',
        category: editor.category || 'custom',
        severity: editor.severity || 'medium',
        conditions: editor.conditions,
        conditions_operator: editor.conditions_operator || 'AND',
        actions: editor.actions,
        scope: editor.scope || 'all',
        triggers: editor.triggers || ['on_demand'],
        enabled: editor.enabled !== false,
      };
      let saved;
      if (selectedRule && selectedRule.rule_id && !isBuiltin(selectedRule)) {
        // Update existing
        const res = await pms.put(`/pms/rules/${selectedRule.rule_id}`, payload);
        saved = res.data;
        setCustomRules(prev => prev.map(r => r.rule_id === saved.rule_id ? saved : r));
      } else if (!selectedRule || isBuiltin(selectedRule)) {
        // Create new
        const res = await pms.post('/pms/rules', payload);
        saved = res.data;
        setCustomRules(prev => [...prev, saved]);
      }
      setSelectedRule(saved);
      setEditor({ ...saved });
      setEditorDirty(false);
      setSaveMsg('Saved!');
      setTimeout(() => setSaveMsg(''), 2500);
    } catch (e) {
      setSaveMsg('Save failed: ' + (e.response?.data?.detail || e.message));
    } finally {
      setSaving(false);
    }
  }

  // Toggle rule enabled
  async function toggleRule(rule) {
    if (isBuiltin(rule)) return;
    const updated = { enabled: !rule.enabled };
    try {
      const res = await pms.put(`/pms/rules/${rule.rule_id}`, updated);
      setCustomRules(prev => prev.map(r => r.rule_id === rule.rule_id ? res.data : r));
      if (selectedRule && selectedRule.rule_id === rule.rule_id) {
        setEditor(prev => ({ ...prev, enabled: res.data.enabled }));
      }
    } catch (e) {
      // silently ignore
    }
  }

  // Delete rule
  async function deleteRule(ruleId) {
    if (!window.confirm('Delete this rule?')) return;
    try {
      await pms.delete(`/pms/rules/${ruleId}`);
      setCustomRules(prev => prev.filter(r => r.rule_id !== ruleId));
      if (selectedRule && selectedRule.rule_id === ruleId) {
        newRule();
      }
    } catch (e) {
      alert('Delete failed: ' + (e.response?.data?.detail || e.message));
    }
  }

  // Test rule
  async function runTest() {
    if (!testPortfolioId) { alert('Select a portfolio to test against.'); return; }
    setTestRunning(true);
    setTestResult(null);
    try {
      const payload = {
        portfolio_id: testPortfolioId,
        name: editor.name || 'Test Rule',
        severity: editor.severity || 'medium',
        conditions: editor.conditions || [],
        conditions_operator: editor.conditions_operator || 'AND',
        actions: editor.actions || [],
      };
      const res = await pms.post('/pms/rules/test/preview', payload);
      setTestResult(res.data);
    } catch (e) {
      setTestResult({ error: e.response?.data?.detail || e.message });
    } finally {
      setTestRunning(false);
    }
  }

  const readonlyMode = isBuiltin(editor);

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 64px)', overflow: 'hidden', gap: 0 }}>

      {/* ── LEFT: Rule Library ──────────────────────────────────────── */}
      <div style={{
        width: 280, flexShrink: 0,
        borderRight: '1px solid var(--neo-border, #1f2937)',
        display: 'flex', flexDirection: 'column',
        background: 'var(--neo-surface, #111827)',
        overflow: 'hidden',
      }}>
        {/* Library Header */}
        <div style={{ padding: '14px 14px 10px', borderBottom: '1px solid var(--neo-border, #1f2937)' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
              <div style={{
                width: 28, height: 28, borderRadius: 7,
                background: 'rgba(139,92,246,0.15)',
                border: '1px solid rgba(139,92,246,0.3)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <Shield size={14} style={{ color: '#a78bfa' }} />
              </div>
              <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--neo-text, #f9fafb)' }}>
                Rule Library
              </span>
            </div>
            <button
              onClick={newRule}
              style={{
                background: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.25)',
                borderRadius: 6, padding: '4px 8px', cursor: 'pointer',
                display: 'flex', alignItems: 'center', gap: 4,
                color: '#93c5fd', fontSize: 12, fontWeight: 600,
              }}
            >
              <Plus size={12} /> New
            </button>
          </div>

          {/* Category Tabs */}
          <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
            {categoryTabs.map(tab => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                style={{
                  padding: '3px 7px', borderRadius: 5, fontSize: 10, fontWeight: 600,
                  textTransform: 'uppercase', letterSpacing: '0.04em', cursor: 'pointer',
                  border: 'none',
                  background: activeTab === tab ? 'rgba(59,130,246,0.2)' : 'transparent',
                  color: activeTab === tab ? '#93c5fd' : 'var(--neo-text-dim, #6b7280)',
                  transition: 'all 0.15s',
                }}
              >
                {tab}
              </button>
            ))}
          </div>
        </div>

        {/* Rule List */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '10px 10px' }}>
          {loading ? (
            <div style={{ textAlign: 'center', paddingTop: 40 }}>
              <Loader size={18} style={{ color: 'var(--neo-text-dim, #6b7280)', animation: 'spin 1s linear infinite' }} />
              <p style={{ fontSize: 12, color: 'var(--neo-text-dim, #6b7280)', marginTop: 8 }}>Loading rules...</p>
            </div>
          ) : loadError ? (
            <div style={{
              margin: 8, padding: 10, borderRadius: 6, fontSize: 12,
              background: 'rgba(220,38,38,0.08)', border: '1px solid rgba(220,38,38,0.2)',
              color: '#f87171',
            }}>
              {loadError}
              <button onClick={loadRules} style={{ marginLeft: 6, color: '#93c5fd', background: 'none', border: 'none', cursor: 'pointer', fontSize: 12 }}>Retry</button>
            </div>
          ) : filteredRules.length === 0 ? (
            <div style={{ textAlign: 'center', paddingTop: 40 }}>
              <p style={{ fontSize: 12, color: 'var(--neo-text-dim, #6b7280)' }}>
                {activeTab === 'all' ? 'No rules yet.' : `No ${activeTab} rules.`}
              </p>
            </div>
          ) : (
            filteredRules.map(rule => (
              <RuleCard
                key={rule.rule_id}
                rule={rule}
                selected={selectedRule && selectedRule.rule_id === rule.rule_id}
                onClick={selectRule}
                onToggle={toggleRule}
                onDelete={deleteRule}
              />
            ))
          )}
        </div>

        {/* Library Footer */}
        <div style={{
          padding: '8px 12px',
          borderTop: '1px solid var(--neo-border, #1f2937)',
          fontSize: 11, color: 'var(--neo-text-dim, #6b7280)',
          display: 'flex', justifyContent: 'space-between',
        }}>
          <span>{allLibraryRules.length} rules total</span>
          <span>{allLibraryRules.filter(r => r.enabled !== false).length} active</span>
        </div>
      </div>

      {/* ── CENTER: Visual Rule Editor ──────────────────────────────── */}
      <div style={{
        flex: 1, display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
        background: 'var(--neo-bg, #0d1117)',
      }}>
        {/* Editor Top Bar */}
        <div style={{
          padding: '10px 16px', borderBottom: '1px solid var(--neo-border, #1f2937)',
          display: 'flex', alignItems: 'center', gap: 10,
          background: 'var(--neo-surface, #111827)',
        }}>
          <input
            type="text"
            value={editor.name}
            onChange={e => setEditorField('name', e.target.value)}
            placeholder="Rule Name..."
            disabled={readonlyMode}
            style={{
              ...inputStyle, flex: 1, fontSize: 15, fontWeight: 600,
              padding: '6px 12px',
              opacity: readonlyMode ? 0.6 : 1,
            }}
          />
          <select
            value={editor.category}
            onChange={e => setEditorField('category', e.target.value)}
            disabled={readonlyMode}
            style={{ ...selectStyle, opacity: readonlyMode ? 0.6 : 1 }}
          >
            {meta.categories.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
          </select>
          <select
            value={editor.severity}
            onChange={e => setEditorField('severity', e.target.value)}
            disabled={readonlyMode}
            style={{
              ...selectStyle,
              color: (SEVERITY_STYLE[editor.severity] || SEVERITY_STYLE.medium).text,
              opacity: readonlyMode ? 0.6 : 1,
            }}
          >
            {meta.severities.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
          {readonlyMode ? (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 5,
              padding: '6px 12px', borderRadius: 6,
              background: 'rgba(107,114,128,0.1)', border: '1px solid rgba(107,114,128,0.2)',
              fontSize: 12, color: 'var(--neo-text-muted, #9ca3af)',
            }}>
              <Lock size={13} /> Built-in (read-only)
            </div>
          ) : (
            <button
              onClick={saveRule}
              disabled={saving || !editorDirty}
              style={{
                background: 'rgba(59,130,246,0.15)', border: '1px solid rgba(59,130,246,0.35)',
                borderRadius: 6, padding: '6px 14px', cursor: saving ? 'wait' : !editorDirty ? 'default' : 'pointer',
                display: 'flex', alignItems: 'center', gap: 5,
                color: saving || !editorDirty ? 'var(--neo-text-dim, #6b7280)' : '#93c5fd',
                fontSize: 13, fontWeight: 600,
                opacity: saving || !editorDirty ? 0.6 : 1,
                transition: 'all 0.15s',
              }}
            >
              {saving ? <Loader size={13} /> : <Save size={13} />}
              {saving ? 'Saving...' : selectedRule && !isBuiltin(selectedRule) ? 'Update' : 'Save Rule'}
            </button>
          )}
        </div>

        {saveMsg && (
          <div style={{
            margin: '8px 16px 0', padding: '6px 12px', borderRadius: 6, fontSize: 12,
            background: saveMsg.startsWith('Save') || saveMsg.includes('required')
              ? 'rgba(220,38,38,0.08)' : 'rgba(34,197,94,0.08)',
            border: saveMsg.startsWith('Save') || saveMsg.includes('required')
              ? '1px solid rgba(220,38,38,0.2)' : '1px solid rgba(34,197,94,0.2)',
            color: saveMsg.startsWith('Save') || saveMsg.includes('required') ? '#f87171' : '#4ade80',
          }}>
            {saveMsg}
          </div>
        )}

        {/* Editor Body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '16px' }}>

          {/* Description */}
          <div style={{ marginBottom: 14 }}>
            <label style={labelStyle}>Description</label>
            <textarea
              value={editor.description || ''}
              onChange={e => setEditorField('description', e.target.value)}
              disabled={readonlyMode}
              placeholder="Describe what this rule checks..."
              rows={2}
              style={{
                ...inputStyle, width: '100%', resize: 'vertical',
                fontFamily: 'inherit', lineHeight: 1.5,
                opacity: readonlyMode ? 0.6 : 1,
              }}
            />
          </div>

          {/* IF Block — Conditions */}
          <div style={sectionStyle}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
              <span style={{
                fontSize: 11, fontWeight: 700, color: '#3b82f6',
                background: 'rgba(59,130,246,0.12)', padding: '2px 8px', borderRadius: 4,
                letterSpacing: '0.06em',
              }}>
                IF
              </span>
              <span style={{ fontSize: 12, color: 'var(--neo-text-muted, #9ca3af)' }}>Conditions</span>
              <span style={{ fontSize: 11, color: 'var(--neo-text-dim, #6b7280)', marginLeft: 4 }}>
                — use AND / OR between rows
              </span>
            </div>

            {(editor.conditions || []).length === 0 ? (
              <div style={{ textAlign: 'center', padding: '16px 0', color: 'var(--neo-text-dim, #6b7280)', fontSize: 13 }}>
                No conditions yet. Add one below.
              </div>
            ) : (
              (editor.conditions || []).map((cond, i) => (
                <ConditionRow
                  key={i}
                  cond={cond}
                  index={i}
                  onChange={readonlyMode ? () => {} : updateCondition}
                  onDelete={readonlyMode ? () => {} : deleteCondition}
                  isLast={i === editor.conditions.length - 1}
                  fields={meta.fields}
                  operators={meta.operators}
                  readonly={readonlyMode}
                />
              ))
            )}

            {!readonlyMode && (
              <button
                onClick={addCondition}
                style={{
                  marginTop: 4, background: 'transparent',
                  border: '1px dashed var(--neo-border, #1f2937)',
                  borderRadius: 6, padding: '6px 12px',
                  color: 'var(--neo-text-muted, #9ca3af)', fontSize: 12,
                  cursor: 'pointer', width: '100%',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5,
                  transition: 'border-color 0.15s',
                }}
              >
                <Plus size={13} /> Add Condition
              </button>
            )}
          </div>

          {/* THEN Block — Actions */}
          <div style={sectionStyle}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
              <span style={{
                fontSize: 11, fontWeight: 700, color: '#22c55e',
                background: 'rgba(34,197,94,0.12)', padding: '2px 8px', borderRadius: 4,
                letterSpacing: '0.06em',
              }}>
                THEN
              </span>
              <span style={{ fontSize: 12, color: 'var(--neo-text-muted, #9ca3af)' }}>Actions</span>
            </div>

            {(editor.actions || []).length === 0 ? (
              <div style={{ textAlign: 'center', padding: '16px 0', color: 'var(--neo-text-dim, #6b7280)', fontSize: 13 }}>
                No actions yet. Add one below.
              </div>
            ) : (
              (editor.actions || []).map((action, i) => (
                <ActionRow
                  key={i}
                  action={action}
                  index={i}
                  onChange={readonlyMode ? () => {} : updateAction}
                  onDelete={readonlyMode ? () => {} : deleteAction}
                  actionTypes={meta.action_types}
                />
              ))
            )}

            {!readonlyMode && (
              <button
                onClick={addAction}
                style={{
                  marginTop: 4, background: 'transparent',
                  border: '1px dashed var(--neo-border, #1f2937)',
                  borderRadius: 6, padding: '6px 12px',
                  color: 'var(--neo-text-muted, #9ca3af)', fontSize: 12,
                  cursor: 'pointer', width: '100%',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5,
                }}
              >
                <Plus size={13} /> Add Action
              </button>
            )}
          </div>

          {/* Scope & Triggers */}
          <div style={{ display: 'flex', gap: 12 }}>
            <div style={{ flex: 1, ...sectionStyle }}>
              <label style={labelStyle}>Scope</label>
              <select
                value={editor.scope || 'all'}
                onChange={e => setEditorField('scope', e.target.value)}
                disabled={readonlyMode}
                style={{ ...selectStyle, width: '100%', opacity: readonlyMode ? 0.6 : 1 }}
              >
                <option value="all">All Portfolios</option>
                {portfolios.map(p => (
                  <option key={p.portfolio_id || p.id} value={p.portfolio_id || p.id}>
                    {p.name || p.portfolio_id}
                  </option>
                ))}
              </select>
            </div>
            <div style={{ flex: 1, ...sectionStyle }}>
              <label style={labelStyle}>Trigger</label>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {['on_demand', 'on_trade', 'on_schedule'].map(t => {
                  const active = (editor.triggers || []).includes(t);
                  return (
                    <button
                      key={t}
                      disabled={readonlyMode}
                      onClick={() => {
                        if (readonlyMode) return;
                        const cur = editor.triggers || [];
                        setEditorField('triggers', active ? cur.filter(x => x !== t) : [...cur, t]);
                      }}
                      style={{
                        padding: '3px 8px', borderRadius: 5, fontSize: 11, fontWeight: 600,
                        cursor: readonlyMode ? 'not-allowed' : 'pointer',
                        border: '1px solid',
                        borderColor: active ? 'rgba(139,92,246,0.4)' : 'var(--neo-border, #1f2937)',
                        background: active ? 'rgba(139,92,246,0.12)' : 'transparent',
                        color: active ? '#a78bfa' : 'var(--neo-text-dim, #6b7280)',
                        opacity: readonlyMode ? 0.6 : 1,
                      }}
                    >
                      {t.replace('on_', '')}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          {/* SEBI Notice */}
          {readonlyMode && (
            <div style={{
              padding: '10px 14px', borderRadius: 8, fontSize: 12,
              background: 'rgba(107,114,128,0.08)', border: '1px solid rgba(107,114,128,0.2)',
              color: 'var(--neo-text-muted, #9ca3af)',
              display: 'flex', alignItems: 'center', gap: 8,
            }}>
              <Shield size={14} style={{ flexShrink: 0, color: '#a78bfa' }} />
              This is a built-in rule and cannot be modified. SEBI-mandated rules are always active.
            </div>
          )}
        </div>
      </div>

      {/* ── RIGHT: Test Panel ───────────────────────────────────────── */}
      <div style={{
        width: 340, flexShrink: 0,
        borderLeft: '1px solid var(--neo-border, #1f2937)',
        display: 'flex', flexDirection: 'column',
        background: 'var(--neo-surface, #111827)',
        overflow: 'hidden',
      }}>
        {/* Test Header */}
        <div style={{ padding: '14px 14px 10px', borderBottom: '1px solid var(--neo-border, #1f2937)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12 }}>
            <Play size={14} style={{ color: '#4ade80' }} />
            <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--neo-text, #f9fafb)' }}>
              Test Rule
            </span>
          </div>

          {/* Portfolio selector */}
          <label style={labelStyle}>Portfolio</label>
          {portfolios.length === 0 ? (
            <div style={{ fontSize: 12, color: 'var(--neo-text-dim, #6b7280)', padding: '6px 0' }}>
              No portfolios found. Create one first.
            </div>
          ) : (
            <select
              value={testPortfolioId}
              onChange={e => setTestPortfolioId(e.target.value)}
              style={{ ...selectStyle, width: '100%', marginBottom: 10 }}
            >
              {portfolios.map(p => (
                <option key={p.portfolio_id || p.id} value={p.portfolio_id || p.id}>
                  {p.name || p.portfolio_id}
                </option>
              ))}
            </select>
          )}

          {/* Run button */}
          <button
            onClick={runTest}
            disabled={testRunning || !testPortfolioId}
            style={{
              width: '100%', padding: '8px 0', borderRadius: 7,
              background: testRunning ? 'rgba(34,197,94,0.08)' : 'rgba(34,197,94,0.15)',
              border: '1px solid rgba(34,197,94,0.3)',
              color: testRunning ? 'var(--neo-text-dim, #6b7280)' : '#4ade80',
              fontSize: 13, fontWeight: 700, cursor: testRunning ? 'wait' : 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
              transition: 'all 0.15s',
            }}
          >
            {testRunning ? <Loader size={14} style={{ animation: 'spin 1s linear infinite' }} /> : <Play size={14} />}
            {testRunning ? 'Running...' : 'Run Test'}
          </button>
        </div>

        {/* Test Results */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '12px 14px' }}>
          {!testResult && !testRunning && (
            <div style={{ textAlign: 'center', paddingTop: 40 }}>
              <Info size={24} style={{ color: 'var(--neo-text-dim, #6b7280)', marginBottom: 8 }} />
              <p style={{ fontSize: 12, color: 'var(--neo-text-dim, #6b7280)' }}>
                Configure the rule and click Run Test to see results.
              </p>
            </div>
          )}

          {testResult && testResult.error && (
            <div style={{
              padding: 12, borderRadius: 8,
              background: 'rgba(220,38,38,0.08)', border: '1px solid rgba(220,38,38,0.2)',
              color: '#f87171', fontSize: 12,
            }}>
              <strong>Error:</strong> {testResult.error}
            </div>
          )}

          {testResult && !testResult.error && (
            <>
              {/* PASS / FAIL Badge */}
              <div style={{
                padding: '12px 16px', borderRadius: 8, marginBottom: 12,
                background: testResult.fired
                  ? 'rgba(220,38,38,0.08)' : 'rgba(34,197,94,0.08)',
                border: `1px solid ${testResult.fired ? 'rgba(220,38,38,0.25)' : 'rgba(34,197,94,0.25)'}`,
                display: 'flex', alignItems: 'center', gap: 10,
              }}>
                {testResult.fired
                  ? <XCircle size={20} style={{ color: '#f87171', flexShrink: 0 }} />
                  : <CheckCircle size={20} style={{ color: '#4ade80', flexShrink: 0 }} />
                }
                <div>
                  <div style={{
                    fontSize: 15, fontWeight: 800,
                    color: testResult.fired ? '#f87171' : '#4ade80',
                  }}>
                    {testResult.fired ? 'RULE FIRED' : 'PASS — No Violation'}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--neo-text-muted, #9ca3af)', marginTop: 2 }}>
                    {testResult.fired ? 'Rule conditions triggered' : 'Portfolio is within limits'}
                  </div>
                </div>
              </div>

              {/* Result Details */}
              {testResult.fired && testResult.result && (
                <>
                  <div style={{ marginBottom: 10 }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--neo-text-muted, #9ca3af)', marginBottom: 5, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                      Action
                    </div>
                    <div style={{
                      padding: '6px 10px', borderRadius: 6, fontSize: 12,
                      background: `rgba(${testResult.result.action === 'block' ? '220,38,38' : testResult.result.action === 'warn' ? '245,158,11' : '59,130,246'},0.1)`,
                      border: `1px solid rgba(${testResult.result.action === 'block' ? '220,38,38' : testResult.result.action === 'warn' ? '245,158,11' : '59,130,246'},0.25)`,
                      color: ACTION_COLOR[testResult.result.action] || '#93c5fd',
                      fontWeight: 600,
                    }}>
                      {testResult.result.action?.toUpperCase()} — {testResult.result.message}
                    </div>
                  </div>

                  {/* Fired Conditions */}
                  {testResult.result.details?.fired_conditions?.length > 0 && (
                    <div style={{ marginBottom: 10 }}>
                      <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--neo-text-muted, #9ca3af)', marginBottom: 5, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                        Triggered By
                      </div>
                      {testResult.result.details.fired_conditions.map((c, i) => {
                        const f = meta.fields.find(x => x.id === c.field);
                        const o = meta.operators.find(x => x.id === c.operator);
                        return (
                          <div key={i} style={{
                            fontSize: 12, padding: '5px 8px', borderRadius: 5, marginBottom: 4,
                            background: 'rgba(220,38,38,0.06)', border: '1px solid rgba(220,38,38,0.2)',
                            color: '#f87171',
                          }}>
                            {f?.label || c.field} {o?.label || c.operator} {c.value}{f?.unit}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </>
              )}

              {/* Portfolio Metrics Table */}
              {testResult.result?.details?.metrics && (
                <div>
                  <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--neo-text-muted, #9ca3af)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                    Portfolio Metrics
                  </div>
                  <div style={{
                    borderRadius: 8, overflow: 'hidden',
                    border: '1px solid var(--neo-border, #1f2937)',
                  }}>
                    {Object.entries(testResult.result.details.metrics).map(([k, v], i) => {
                      const fieldOpt = meta.fields.find(f => f.id === k);
                      if (!fieldOpt) return null;
                      return (
                        <div key={k} style={{
                          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                          padding: '7px 10px', fontSize: 12,
                          background: i % 2 === 0 ? 'rgba(255,255,255,0.02)' : 'transparent',
                          borderBottom: i < Object.keys(testResult.result.details.metrics).length - 1
                            ? '1px solid var(--neo-border, #1f2937)' : 'none',
                        }}>
                          <span style={{ color: 'var(--neo-text-muted, #9ca3af)' }}>
                            {fieldOpt.label}
                          </span>
                          <span style={{ color: 'var(--neo-text, #f9fafb)', fontWeight: 600 }}>
                            {typeof v === 'number' ? v.toFixed(2) : v}{fieldOpt.unit}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Total portfolio value */}
              {testResult.result?.details?.metrics?.total_value !== undefined && (
                <div style={{
                  marginTop: 10, padding: '8px 12px', borderRadius: 7,
                  background: 'rgba(255,255,255,0.03)', border: '1px solid var(--neo-border, #1f2937)',
                  display: 'flex', justifyContent: 'space-between', fontSize: 12,
                }}>
                  <span style={{ color: 'var(--neo-text-muted, #9ca3af)' }}>Portfolio Total Value</span>
                  <span style={{ color: 'var(--neo-text, #f9fafb)', fontWeight: 600 }}>
                    ₹{Number(testResult.result.details.metrics.total_value || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                  </span>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        select option { background: #111827; color: #f9fafb; }
      `}</style>
    </div>
  );
}

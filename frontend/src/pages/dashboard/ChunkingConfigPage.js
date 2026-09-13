import React, { useState, useEffect, useCallback } from 'react';
import {
  SlidersHorizontal, Save, RotateCcw, Loader2, Plus, Trash2,
  ChevronDown, ChevronRight, Check, AlertCircle,
} from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../../lib/api';

const PARAM_DESCRIPTIONS = {
  chunk_size: 'Maximum characters per chunk',
  overlap: 'Character overlap between adjacent chunks',
  preserve_sentences: 'Avoid splitting mid-sentence',
  max_sentences: 'Max sentences per chunk',
  min_sentences: 'Min sentences per chunk',
  overlap_sentences: 'Sentence overlap between chunks',
  max_paragraphs: 'Max paragraphs per chunk',
  min_paragraphs: 'Min paragraphs per chunk',
  overlap_paragraphs: 'Paragraph overlap between chunks',
  section_markers: 'Heading markers to split on',
  min_chunk_size: 'Minimum chunk size (chars)',
  max_chunk_size: 'Maximum chunk size (chars)',
  preserve_hierarchy: 'Maintain document hierarchy',
  extract_tables: 'Extract and preserve table structures',
  preserve_table_context: 'Include surrounding text with tables',
  table_chunk_size: 'Max chars for table chunks',
  context_before: 'Context chars before table',
  context_after: 'Context chars after table',
  window_size: 'Sliding window size (chars)',
  step_size: 'Step size between windows',
  overlap_ratio: 'Overlap as fraction of window',
  document_type_detection: 'Auto-detect document type',
  fallback_strategy: 'Strategy when auto-detect fails',
  confidence_threshold: 'Min confidence for auto-detection',
  max_tokens: 'Max tokens per chunk (LLM)',
  overlap_tokens: 'Token overlap between chunks',
  tokenizer: 'Tokenizer to use',
  model: 'LLM model for token counting',
  similarity_threshold: 'Min similarity to group sentences',
  preserve_code_blocks: 'Keep code blocks intact',
  code_context: 'Context chars around code blocks',
  include_captions: 'Include image captions in chunks',
  caption_context: 'Context chars around captions',
};

function ParamEditor({ name, value, onChange, onRemove }) {
  const desc = PARAM_DESCRIPTIONS[name] || '';
  const isBoolean = typeof value === 'boolean';
  const isNumber = typeof value === 'number';
  const isArray = Array.isArray(value);

  return (
    <div className="flex items-start gap-3 py-2">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <code className="text-xs font-mono" style={{ color: 'var(--neo-text)' }}>{name}</code>
          {desc && (
            <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>{desc}</span>
          )}
        </div>
        <div className="mt-1">
          {isBoolean ? (
            <button
              onClick={() => onChange(!value)}
              className="flex items-center gap-1.5 px-2 py-1 rounded text-xs transition"
              style={{
                background: value ? 'rgba(16,185,129,0.15)' : 'var(--neo-surface)',
                color: value ? '#10b981' : 'var(--neo-text-muted)',
                border: `1px solid ${value ? '#10b981' : 'var(--neo-border)'}`,
              }}
            >
              {value ? <Check size={12} /> : null}
              {value ? 'true' : 'false'}
            </button>
          ) : isArray ? (
            <input
              value={value.join(', ')}
              onChange={(e) => onChange(e.target.value.split(',').map((s) => s.trim()).filter(Boolean))}
              className="w-full px-2 py-1 rounded text-sm outline-none font-mono"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            />
          ) : isNumber ? (
            <input
              type="number"
              value={value}
              onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
              className="w-32 px-2 py-1 rounded text-sm outline-none font-mono"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            />
          ) : (
            <input
              value={value}
              onChange={(e) => onChange(e.target.value)}
              className="w-full px-2 py-1 rounded text-sm outline-none font-mono"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            />
          )}
        </div>
      </div>
      {onRemove && (
        <button onClick={onRemove} className="mt-1 p-1 rounded hover:bg-red-500/10 transition" title="Remove parameter">
          <Trash2 size={14} style={{ color: '#ef4444' }} />
        </button>
      )}
    </div>
  );
}

function StrategyCard({ name, strategy, onChange, onDelete }) {
  const [expanded, setExpanded] = useState(false);
  const [newParamKey, setNewParamKey] = useState('');

  const handleParamChange = (paramName, newValue) => {
    onChange({
      ...strategy,
      parameters: { ...strategy.parameters, [paramName]: newValue },
    });
  };

  const handleParamRemove = (paramName) => {
    const params = { ...strategy.parameters };
    delete params[paramName];
    onChange({ ...strategy, parameters: params });
  };

  const handleAddParam = () => {
    if (!newParamKey.trim()) return;
    onChange({
      ...strategy,
      parameters: { ...strategy.parameters, [newParamKey.trim()]: '' },
    });
    setNewParamKey('');
  };

  const handleFieldChange = (field, value) => {
    onChange({ ...strategy, [field]: value });
  };

  const handleValidationChange = (field, value) => {
    onChange({
      ...strategy,
      output_validation: { ...strategy.output_validation, [field]: value },
    });
  };

  return (
    <div
      className="rounded-lg overflow-hidden"
      style={{ border: '1px solid var(--neo-border)', background: 'var(--neo-surface)' }}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-4 py-3 text-left transition hover:opacity-90"
        style={{ background: 'var(--neo-surface)' }}
      >
        <div className="flex items-center gap-3">
          {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          <div>
            <div className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>
              {strategy.name || name}
            </div>
            <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
              {strategy.description || `Strategy: ${name}`}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <code className="text-xs px-2 py-0.5 rounded" style={{ background: 'var(--neo-bg)', color: 'var(--neo-text-muted)' }}>
            {name}
          </code>
          {onDelete && (
            <button
              onClick={(e) => { e.stopPropagation(); onDelete(); }}
              className="p-1 rounded hover:bg-red-500/10 transition"
              title="Delete strategy"
            >
              <Trash2 size={14} style={{ color: '#ef4444' }} />
            </button>
          )}
        </div>
      </button>

      {expanded && (
        <div className="px-4 pb-4" style={{ borderTop: '1px solid var(--neo-border)' }}>
          {/* Basic fields */}
          <div className="grid grid-cols-2 gap-3 mt-3">
            <div>
              <label className="text-xs font-medium" style={{ color: 'var(--neo-text-muted)' }}>Display Name</label>
              <input
                value={strategy.name || ''}
                onChange={(e) => handleFieldChange('name', e.target.value)}
                className="w-full px-2 py-1.5 rounded text-sm outline-none mt-1"
                style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
              />
            </div>
            <div>
              <label className="text-xs font-medium" style={{ color: 'var(--neo-text-muted)' }}>Description</label>
              <input
                value={strategy.description || ''}
                onChange={(e) => handleFieldChange('description', e.target.value)}
                className="w-full px-2 py-1.5 rounded text-sm outline-none mt-1"
                style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
              />
            </div>
          </div>

          {/* Parameters */}
          <div className="mt-4">
            <h4 className="text-xs font-semibold uppercase tracking-wider mb-2" style={{ color: 'var(--neo-text-muted)' }}>
              Parameters
            </h4>
            <div className="divide-y" style={{ borderColor: 'var(--neo-border)' }}>
              {Object.entries(strategy.parameters || {}).map(([key, val]) => (
                <ParamEditor
                  key={key}
                  name={key}
                  value={val}
                  onChange={(v) => handleParamChange(key, v)}
                  onRemove={() => handleParamRemove(key)}
                />
              ))}
            </div>
            <div className="flex items-center gap-2 mt-2">
              <input
                value={newParamKey}
                onChange={(e) => setNewParamKey(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleAddParam()}
                placeholder="New parameter name"
                className="px-2 py-1 rounded text-xs outline-none font-mono"
                style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)', width: 180 }}
              />
              <button
                onClick={handleAddParam}
                disabled={!newParamKey.trim()}
                className="flex items-center gap-1 px-2 py-1 rounded text-xs transition hover:opacity-80 disabled:opacity-40"
                style={{ background: 'var(--neo-blue)', color: '#fff' }}
              >
                <Plus size={12} /> Add
              </button>
            </div>
          </div>

          {/* Validation */}
          {strategy.output_validation && (
            <div className="mt-4">
              <h4 className="text-xs font-semibold uppercase tracking-wider mb-2" style={{ color: 'var(--neo-text-muted)' }}>
                Output Validation
              </h4>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Max Chunk Size</label>
                  <input
                    type="number"
                    value={strategy.output_validation.max_chunk_size || 0}
                    onChange={(e) => handleValidationChange('max_chunk_size', parseInt(e.target.value) || 0)}
                    className="w-full px-2 py-1 rounded text-sm outline-none mt-1 font-mono"
                    style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                  />
                </div>
                <div>
                  <label className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Min Chunk Size</label>
                  <input
                    type="number"
                    value={strategy.output_validation.min_chunk_size || 0}
                    onChange={(e) => handleValidationChange('min_chunk_size', parseInt(e.target.value) || 0)}
                    className="w-full px-2 py-1 rounded text-sm outline-none mt-1 font-mono"
                    style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                  />
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function ChunkingConfigPage() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [newStrategyKey, setNewStrategyKey] = useState('');
  const [showAddStrategy, setShowAddStrategy] = useState(false);

  const fetchConfig = useCallback(() => {
    setLoading(true);
    api.get('/dashboard/config/chunking')
      .then((res) => {
        setConfig(res.data.strategies || {});
        setDirty(false);
      })
      .catch(() => toast.error('Failed to load chunking config'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetchConfig(); }, [fetchConfig]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.put('/dashboard/config/chunking', { strategies: config });
      toast.success('Chunking config saved');
      setDirty(false);
    } catch {
      // toast handled by interceptor
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    if (!window.confirm('Reset to saved config? Unsaved changes will be lost.')) return;
    fetchConfig();
  };

  const handleStrategyChange = (key, updated) => {
    setConfig((prev) => ({ ...prev, [key]: updated }));
    setDirty(true);
  };

  const handleDeleteStrategy = (key) => {
    if (!window.confirm(`Delete strategy "${key}"?`)) return;
    setConfig((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
    setDirty(true);
  };

  const handleAddStrategy = () => {
    const key = newStrategyKey.trim().toLowerCase().replace(/\s+/g, '_');
    if (!key) return;
    if (config[key]) {
      toast.error(`Strategy "${key}" already exists`);
      return;
    }
    setConfig((prev) => ({
      ...prev,
      [key]: {
        name: newStrategyKey.trim(),
        description: '',
        parameters: { chunk_size: 1000, overlap: 200 },
        output_validation: { required_fields: ['id', 'content', 'chunk_type', 'chunk_index'], chunk_types: ['text'], max_chunk_size: 2000, min_chunk_size: 100 },
      },
    }));
    setNewStrategyKey('');
    setShowAddStrategy(false);
    setDirty(true);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 size={24} className="animate-spin" style={{ color: 'var(--neo-text-muted)' }} />
      </div>
    );
  }

  const strategyKeys = config ? Object.keys(config) : [];

  return (
    <div className="max-w-3xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold flex items-center gap-2" style={{ color: 'var(--neo-text)' }}>
            <SlidersHorizontal size={20} />
            Chunking Configuration
          </h1>
          <p className="text-sm mt-1" style={{ color: 'var(--neo-text-muted)' }}>
            Configure chunking strategies used during data ingestion. Changes are saved to the server and take effect immediately.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {dirty && (
            <span className="flex items-center gap-1 text-xs px-2 py-1 rounded" style={{ background: 'rgba(245,158,11,0.15)', color: '#f59e0b' }}>
              <AlertCircle size={12} /> Unsaved
            </span>
          )}
          <button
            onClick={handleReset}
            disabled={!dirty}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition hover:opacity-80 disabled:opacity-40"
            style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
          >
            <RotateCcw size={14} /> Reset
          </button>
          <button
            onClick={handleSave}
            disabled={!dirty || saving}
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
            style={{ background: 'var(--neo-green)', color: '#fff' }}
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            Save
          </button>
        </div>
      </div>

      {/* Strategy count */}
      <div className="flex items-center justify-between mb-4">
        <span className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
          {strategyKeys.length} strateg{strategyKeys.length === 1 ? 'y' : 'ies'} configured
        </span>
        <button
          onClick={() => setShowAddStrategy(!showAddStrategy)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition hover:opacity-80"
          style={{ background: 'var(--neo-blue)', color: '#fff' }}
        >
          <Plus size={14} /> Add Strategy
        </button>
      </div>

      {/* Add strategy form */}
      {showAddStrategy && (
        <div
          className="flex items-center gap-2 mb-4 p-3 rounded-lg"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-blue)' }}
        >
          <input
            value={newStrategyKey}
            onChange={(e) => setNewStrategyKey(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleAddStrategy()}
            placeholder="Strategy name (e.g. token_based)"
            className="flex-1 px-3 py-1.5 rounded text-sm outline-none font-mono"
            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            autoFocus
          />
          <button
            onClick={handleAddStrategy}
            disabled={!newStrategyKey.trim()}
            className="px-3 py-1.5 rounded text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
            style={{ background: 'var(--neo-green)', color: '#fff' }}
          >
            Create
          </button>
          <button
            onClick={() => { setShowAddStrategy(false); setNewStrategyKey(''); }}
            className="px-3 py-1.5 rounded text-sm"
            style={{ color: 'var(--neo-text-muted)' }}
          >
            Cancel
          </button>
        </div>
      )}

      {/* Strategy cards */}
      <div className="space-y-3">
        {strategyKeys.map((key) => (
          <StrategyCard
            key={key}
            name={key}
            strategy={config[key]}
            onChange={(updated) => handleStrategyChange(key, updated)}
            onDelete={() => handleDeleteStrategy(key)}
          />
        ))}
      </div>

      {strategyKeys.length === 0 && (
        <div
          className="text-center py-12 rounded-lg"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        >
          <SlidersHorizontal size={32} className="mx-auto mb-3" style={{ color: 'var(--neo-text-muted)' }} />
          <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
            No chunking strategies configured. Click "Add Strategy" to create one.
          </p>
        </div>
      )}
    </div>
  );
}

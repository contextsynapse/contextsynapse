import React, { useState, useEffect, useCallback } from 'react';
import {
  Workflow, Play, Clock, CheckCircle, XCircle, Loader2, Shield,
  BarChart3, ChevronDown, ChevronRight, Plus, Trash2, Tag,
  FileText, Globe, Upload, Sparkles, Database, ArrowRight,
  Pencil, X, Save, GripVertical, Eye, Settings, Server,
  Rss, Pause, RefreshCw, Link, Hash, Filter, Layers,
} from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import StepWisePipelinePanel from '../../components/StepWisePipelinePanel';

const STAGE_ICONS = {
  CHUNK: '📄',
  EMBED: '🧮',
  EXTRACT_ENTITIES: '🔍',
  EXTRACT_ENTITIES_AND_RELATIONSHIPS: '🔗',
  ENHANCE_GRAPH: '🌐',
  PERSIST: '💾',
};

const STAGE_LABELS = {
  CHUNK: 'Chunk',
  EMBED: 'Embed',
  EXTRACT_ENTITIES: 'Extract Entities',
  EXTRACT_ENTITIES_AND_RELATIONSHIPS: 'Extract Entities & Relationships',
  ENHANCE_GRAPH: 'Enhance Graph',
  PERSIST: 'Persist',
};

const CLASSIFICATION_COLORS = {
  public: { bg: '#10b98120', text: '#10b981', label: 'Public' },
  internal: { bg: '#3b82f620', text: '#3b82f6', label: 'Internal' },
  confidential: { bg: '#f59e0b20', text: '#f59e0b', label: 'Confidential' },
  restricted: { bg: '#ef444420', text: '#ef4444', label: 'Restricted' },
};

// ── Stage flow visualization ──────────────────────────────────────────

function StageFlow({ stages, size = 'md' }) {
  const textSize = size === 'sm' ? 'text-[10px]' : 'text-xs';
  const gap = size === 'sm' ? 'gap-1' : 'gap-1.5';
  return (
    <div className={`flex items-center flex-wrap ${gap}`}>
      {stages.map((s, i) => (
        <React.Fragment key={s.name || i}>
          {i > 0 && (
            <ArrowRight size={size === 'sm' ? 10 : 12} style={{ color: 'var(--neo-text-muted)', opacity: 0.5 }} />
          )}
          <span
            className={`${textSize} px-1.5 py-0.5 rounded font-medium`}
            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          >
            {STAGE_ICONS[s.type] || '⚙️'} {STAGE_LABELS[s.type] || s.name}
          </span>
        </React.Fragment>
      ))}
    </div>
  );
}

// ── Run stage progress ────────────────────────────────────────────────

function RunStages({ stages }) {
  if (!stages || !stages.length) return null;
  return (
    <div className="flex items-center gap-1 flex-wrap">
      {stages.map((s, i) => {
        const isCompleted = s.status === 'completed';
        const isRunning = s.status === 'running';
        const isFailed = s.status === 'failed';
        const isSkipped = s.status === 'skipped';

        let color = 'var(--neo-text-muted)';
        let icon = '·';
        if (isCompleted) { color = 'var(--neo-green)'; icon = '✓'; }
        else if (isRunning) { color = 'var(--neo-blue)'; icon = '◉'; }
        else if (isFailed) { color = '#ef4444'; icon = '✗'; }
        else if (isSkipped) { color = 'var(--neo-text-muted)'; icon = '–'; }

        return (
          <React.Fragment key={s.name}>
            {i > 0 && <span style={{ color: 'var(--neo-text-muted)', fontSize: 8 }}>→</span>}
            <span
              className="text-[10px] px-1 py-0.5 rounded"
              style={{ color, fontWeight: isRunning ? 600 : 400, opacity: isSkipped ? 0.4 : 1 }}
              title={`${s.name}: ${s.status}${s.items_created ? ` (+${s.items_created})` : ''}${s.duration_ms ? ` ${s.duration_ms}ms` : ''}`}
            >
              {icon} {STAGE_LABELS[s.stage_type] || s.name}
            </span>
          </React.Fragment>
        );
      })}
    </div>
  );
}

// ── Accuracy badge ────────────────────────────────────────────────────

function AccuracyBadge({ score, breakdown }) {
  if (!score && score !== 0) return null;
  const pct = Math.round(score * 100);
  const color = pct >= 80 ? 'var(--neo-green)' : pct >= 50 ? '#f59e0b' : '#ef4444';
  return (
    <span
      className="text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded"
      style={{ color, background: `${color}15` }}
      title={breakdown ? JSON.stringify(breakdown, null, 2) : `${pct}% accuracy`}
    >
      {pct}%
    </span>
  );
}

// ── Classification badge ──────────────────────────────────────────────

function ClassificationBadge({ classification }) {
  const c = CLASSIFICATION_COLORS[classification] || CLASSIFICATION_COLORS.internal;
  return (
    <span
      className="text-[10px] font-medium px-1.5 py-0.5 rounded"
      style={{ background: c.bg, color: c.text }}
    >
      {c.label}
    </span>
  );
}

// ── Available stage types ─────────────────────────────────────────────

const AVAILABLE_STAGES = [
  { type: 'CHUNK', name: 'Chunk', description: 'Split content into chunks' },
  { type: 'EMBED', name: 'Embed', description: 'Generate embeddings' },
  { type: 'EXTRACT_ENTITIES', name: 'Extract Entities', description: 'Extract entities from text' },
  { type: 'EXTRACT_ENTITIES_AND_RELATIONSHIPS', name: 'Extract Entities & Relationships', description: 'Extract entities and relationships' },
  { type: 'ENHANCE_GRAPH', name: 'Enhance Graph', description: 'Enhance graph with inferred relationships' },
  { type: 'PERSIST', name: 'Persist', description: 'Persist to graph storage' },
];

// ── Pipeline Create/Edit Modal ───────────────────────────────────────

function PipelineFormModal({ pipeline, onClose, onSaved }) {
  const isEdit = !!pipeline;
  const [name, setName] = useState(pipeline?.name || '');
  const [description, setDescription] = useState(pipeline?.description || '');
  const [stages, setStages] = useState(
    pipeline?.stages?.length
      ? pipeline.stages.map(s => ({ type: s.type, name: s.name }))
      : [{ type: 'CHUNK', name: 'Chunk' }, { type: 'PERSIST', name: 'Persist' }]
  );
  const [aiqlTemplate, setAiqlTemplate] = useState(pipeline?.aiql_template || '');
  const [tags, setTags] = useState((pipeline?.tags || []).join(', '));
  const [category, setCategory] = useState(pipeline?.category || 'ingestion');
  const [defaultParams, setDefaultParams] = useState(
    pipeline?.default_params ? JSON.stringify(pipeline.default_params, null, 2) : '{}'
  );
  const [saving, setSaving] = useState(false);

  const addStage = (stageType) => {
    const def = AVAILABLE_STAGES.find(s => s.type === stageType);
    if (def) setStages(prev => [...prev, { type: def.type, name: def.name }]);
  };

  const removeStage = (idx) => {
    setStages(prev => prev.filter((_, i) => i !== idx));
  };

  const moveStage = (idx, dir) => {
    setStages(prev => {
      const next = [...prev];
      const target = idx + dir;
      if (target < 0 || target >= next.length) return next;
      [next[idx], next[target]] = [next[target], next[idx]];
      return next;
    });
  };

  const handleSave = async () => {
    if (!name.trim() || stages.length === 0) {
      toast.error('Name and at least one stage are required');
      return;
    }
    let parsedParams = {};
    try {
      parsedParams = JSON.parse(defaultParams || '{}');
    } catch {
      toast.error('Invalid JSON in default parameters');
      return;
    }

    setSaving(true);
    try {
      const body = {
        name: name.trim(),
        description: description.trim(),
        stages,
        aiql_template: aiqlTemplate.trim() || `INGEST WITH PIPELINE "${name.trim()}"`,
        default_params: parsedParams,
        tags: tags.split(',').map(t => t.trim()).filter(Boolean),
        category,
      };

      if (isEdit) {
        await api.put(`/dashboard/ingest/pipeline-templates/${pipeline.id}`, body);
        toast.success('Pipeline updated');
      } else {
        await api.post('/dashboard/ingest/pipeline-templates', body);
        toast.success('Pipeline created');
      }
      onSaved();
      onClose();
    } catch {
      // toast handled by interceptor
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: 'rgba(0,0,0,0.5)' }}>
      <div
        className="rounded-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto shadow-2xl"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4" style={{ borderBottom: '1px solid var(--neo-border)' }}>
          <h2 className="text-base font-bold" style={{ color: 'var(--neo-text)' }}>
            {isEdit ? 'Edit Pipeline' : 'New Pipeline'}
          </h2>
          <button onClick={onClose} className="p-1 rounded hover:opacity-70 transition">
            <X size={18} style={{ color: 'var(--neo-text-muted)' }} />
          </button>
        </div>

        <div className="px-6 py-5 space-y-5">
          {/* Name + Category */}
          <div className="grid grid-cols-3 gap-3">
            <div className="col-span-2">
              <label className="text-xs font-medium block mb-1" style={{ color: 'var(--neo-text)' }}>Name</label>
              <input
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="My Ingestion Pipeline"
                className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                autoFocus
              />
            </div>
            <div>
              <label className="text-xs font-medium block mb-1" style={{ color: 'var(--neo-text)' }}>Category</label>
              <select
                value={category}
                onChange={e => setCategory(e.target.value)}
                className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
              >
                <option value="ingestion">Ingestion</option>
                <option value="extraction">Extraction</option>
                <option value="enrichment">Enrichment</option>
                <option value="custom">Custom</option>
              </select>
            </div>
          </div>

          {/* Description */}
          <div>
            <label className="text-xs font-medium block mb-1" style={{ color: 'var(--neo-text)' }}>Description</label>
            <input
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="What does this pipeline do?"
              className="w-full px-3 py-2 rounded-lg text-sm outline-none"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            />
          </div>

          {/* Stages */}
          <div>
            <label className="text-xs font-medium block mb-2" style={{ color: 'var(--neo-text)' }}>
              Pipeline Stages
            </label>
            <div className="space-y-1.5 mb-3">
              {stages.map((s, idx) => (
                <div
                  key={idx}
                  className="flex items-center gap-2 px-3 py-2 rounded-lg"
                  style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)' }}
                >
                  <GripVertical size={14} style={{ color: 'var(--neo-text-muted)', cursor: 'grab' }} />
                  <span className="text-sm" style={{ color: 'var(--neo-text)' }}>
                    {STAGE_ICONS[s.type] || ''} {STAGE_LABELS[s.type] || s.name}
                  </span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded font-mono" style={{ background: 'var(--neo-surface)', color: 'var(--neo-text-muted)' }}>
                    {s.type}
                  </span>
                  <div className="ml-auto flex items-center gap-1">
                    <button
                      onClick={() => moveStage(idx, -1)}
                      disabled={idx === 0}
                      className="text-[10px] px-1 py-0.5 rounded disabled:opacity-30 hover:opacity-70 transition"
                      style={{ color: 'var(--neo-text-muted)' }}
                    >
                      Up
                    </button>
                    <button
                      onClick={() => moveStage(idx, 1)}
                      disabled={idx === stages.length - 1}
                      className="text-[10px] px-1 py-0.5 rounded disabled:opacity-30 hover:opacity-70 transition"
                      style={{ color: 'var(--neo-text-muted)' }}
                    >
                      Dn
                    </button>
                    <button
                      onClick={() => removeStage(idx)}
                      className="p-0.5 rounded hover:bg-red-500/10 transition"
                    >
                      <Trash2 size={12} style={{ color: '#ef4444' }} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {AVAILABLE_STAGES.filter(as => !stages.some(s => s.type === as.type)).map(as => (
                <button
                  key={as.type}
                  onClick={() => addStage(as.type)}
                  className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium transition hover:opacity-80"
                  style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
                  title={as.description}
                >
                  <Plus size={10} /> {STAGE_ICONS[as.type]} {as.name}
                </button>
              ))}
              {stages.length === AVAILABLE_STAGES.length && (
                <span className="text-[10px] py-1" style={{ color: 'var(--neo-text-muted)' }}>
                  All stages added
                </span>
              )}
            </div>
          </div>

          {/* AIQL Template */}
          <div>
            <label className="text-xs font-medium block mb-1" style={{ color: 'var(--neo-text)' }}>
              AIQL Template
            </label>
            <textarea
              value={aiqlTemplate}
              onChange={e => setAiqlTemplate(e.target.value)}
              placeholder={`INGEST WITH PIPELINE "${name || 'my_pipeline'}"\nSOURCE FROM $input\nINTO GRAPH $graph`}
              rows={4}
              className="w-full px-3 py-2 rounded-lg text-sm outline-none resize-none font-mono"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            />
          </div>

          {/* Default Parameters */}
          <div>
            <label className="text-xs font-medium block mb-1" style={{ color: 'var(--neo-text)' }}>
              Default Parameters (JSON)
            </label>
            <textarea
              value={defaultParams}
              onChange={e => setDefaultParams(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 rounded-lg text-sm outline-none resize-none font-mono"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            />
          </div>

          {/* Tags */}
          <div>
            <label className="text-xs font-medium block mb-1" style={{ color: 'var(--neo-text)' }}>
              Tags (comma-separated)
            </label>
            <input
              value={tags}
              onChange={e => setTags(e.target.value)}
              placeholder="rag, documents, pdf"
              className="w-full px-3 py-2 rounded-lg text-sm outline-none"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            />
          </div>
        </div>

        {/* Footer */}
        <div
          className="flex items-center justify-end gap-2 px-6 py-4"
          style={{ borderTop: '1px solid var(--neo-border)' }}
        >
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg text-sm transition"
            style={{ color: 'var(--neo-text-muted)' }}
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !name.trim() || stages.length === 0}
            className="flex items-center gap-1.5 px-5 py-2 rounded-lg text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
            style={{ background: 'var(--neo-green)', color: '#fff' }}
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            {isEdit ? 'Update Pipeline' : 'Create Pipeline'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Webhooks Panel ────────────────────────────────────────────────────

function WebhooksPanel() {
  const [hooks, setHooks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [url, setUrl] = useState('');
  const [events, setEvents] = useState('');
  const [creating, setCreating] = useState(false);

  const fetch_ = useCallback(() => {
    setLoading(true);
    api.get('/dashboard/webhooks')
      .then(r => setHooks(r.data.webhooks || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetch_(); }, [fetch_]);

  const handleCreate = async () => {
    if (!url) return;
    setCreating(true);
    try {
      const eventTypes = events ? events.split(',').map(e => e.trim()).filter(Boolean) : [];
      const res = await api.post('/dashboard/webhooks', { url, event_types: eventTypes });
      toast.success(`Webhook created. Secret: ${res.data.secret}`);
      setUrl(''); setEvents('');
      fetch_();
    } catch {} finally { setCreating(false); }
  };

  const handleDelete = async (id) => {
    await api.delete(`/dashboard/webhooks/${id}`);
    toast.success('Webhook deleted');
    fetch_();
  };

  if (loading) return <div className="flex justify-center py-8"><Loader2 className="animate-spin" size={20} style={{ color: 'var(--neo-blue)' }} /></div>;

  return (
    <div className="space-y-4">
      <div className="p-4 rounded-xl" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
        <h3 className="text-sm font-semibold mb-3" style={{ color: 'var(--neo-text)' }}>Register Webhook</h3>
        <div className="flex gap-2 items-end flex-wrap">
          <div className="flex-1 min-w-[200px]">
            <label className="block text-xs mb-1" style={{ color: 'var(--neo-text-muted)' }}>URL</label>
            <input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://your-server.com/webhook"
              className="w-full px-3 py-1.5 rounded-lg text-xs"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
          </div>
          <div className="w-48">
            <label className="block text-xs mb-1" style={{ color: 'var(--neo-text-muted)' }}>Events (comma-sep, optional)</label>
            <input value={events} onChange={e => setEvents(e.target.value)} placeholder="task_completed, agent_joined"
              className="w-full px-3 py-1.5 rounded-lg text-xs"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
          </div>
          <button onClick={handleCreate} disabled={creating || !url}
            className="px-3 py-1.5 rounded-lg text-xs font-medium"
            style={{ background: 'var(--neo-blue)', color: '#fff', opacity: creating || !url ? 0.5 : 1 }}>
            {creating ? <Loader2 size={12} className="animate-spin" /> : <><Plus size={12} className="inline mr-1" />Add</>}
          </button>
        </div>
      </div>

      {hooks.length === 0 ? (
        <p className="text-sm text-center py-6" style={{ color: 'var(--neo-text-muted)' }}>No webhooks registered.</p>
      ) : (
        <div className="space-y-2">
          {hooks.map(h => (
            <div key={h.webhook_id} className="flex items-center justify-between p-3 rounded-lg"
              style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
              <div>
                <div className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>{h.url}</div>
                <div className="text-[10px] mt-0.5" style={{ color: 'var(--neo-text-muted)' }}>
                  Events: {(h.event_types || []).join(', ') || 'all'} · Status: {h.status}
                </div>
              </div>
              <button onClick={() => handleDelete(h.webhook_id)}
                className="p-1.5 rounded-lg hover:opacity-80" style={{ color: '#ef4444' }}>
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="text-xs p-3 rounded-lg" style={{ background: 'rgba(99,102,241,0.05)', color: 'var(--neo-text-muted)' }}>
        <strong>Supported events:</strong> task_completed, agent_joined, agent_left, pipeline_completed, pipeline_submitted,
        nodes_archived, archive_restored, session_created, session_deleted, context_attached, graph_created, graph_deleted
      </div>
    </div>
  );
}


// ── Agent Consumption Panel ──────────────────────────────────────────

function ConsumptionPanel() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get('/dashboard/savings')
      .then(r => setData(r.data))
      .catch(() => setData({ message: 'No usage data yet. Run queries or pipelines to start tracking token consumption.' }))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="flex justify-center py-8"><Loader2 className="animate-spin" size={20} style={{ color: 'var(--neo-blue)' }} /></div>;
  if (!data || data.data_source === 'unavailable' || (!data.actual_tokens && !data.per_agent?.length)) {
    return (
      <div className="text-center py-12">
        <BarChart3 size={32} className="mx-auto mb-3" style={{ color: 'var(--neo-text-muted)', opacity: 0.4 }} />
        <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>{data?.message || 'No usage data yet.'}</p>
        <p className="text-xs mt-1" style={{ color: 'var(--neo-text-muted)', opacity: 0.7 }}>
          Run queries or pipelines to start tracking token consumption and savings.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { label: 'Tokens Used', value: data.actual_tokens?.toLocaleString() || '0', color: 'var(--neo-blue)' },
          { label: 'Tokens Saved', value: data.tokens_saved?.toLocaleString() || '0', color: 'var(--neo-green)' },
          { label: 'Savings', value: `${data.savings_pct || 0}%`, color: '#8b5cf6' },
          { label: 'Cost', value: `$${(data.actual_cost_usd || 0).toFixed(4)}`, color: '#f59e0b' },
        ].map(({ label, value, color }) => (
          <div key={label} className="p-3 rounded-lg" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
            <div className="text-[10px] mb-1" style={{ color: 'var(--neo-text-muted)' }}>{label}</div>
            <div className="text-lg font-bold" style={{ color }}>{value}</div>
          </div>
        ))}
      </div>

      {/* Data source badge */}
      <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
        Data: {data.data_source === 'measured' ? '✅ Real measurements' : '⚠️ Estimated (run queries for real data)'}
      </div>

      {/* Per-agent breakdown */}
      {data.per_agent?.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold mb-2" style={{ color: 'var(--neo-text)' }}>Per Agent</h4>
          <div className="space-y-1.5">
            {data.per_agent.map((a, i) => {
              const total = (a.total_input_tokens || 0) + (a.total_output_tokens || 0);
              const maxTotal = Math.max(...data.per_agent.map(x => (x.total_input_tokens || 0) + (x.total_output_tokens || 0)));
              const pct = maxTotal > 0 ? (total / maxTotal * 100) : 0;
              return (
                <div key={a.agent_id || i} className="flex items-center gap-2">
                  <span className="text-xs w-24 truncate" style={{ color: 'var(--neo-text-muted)' }}>{a.agent_id?.slice(0, 12) || '?'}</span>
                  <div className="flex-1 h-2 rounded-full" style={{ background: 'var(--neo-bg)' }}>
                    <div className="h-full rounded-full" style={{ width: `${pct}%`, background: 'var(--neo-blue)' }} />
                  </div>
                  <span className="text-[10px] w-16 text-right" style={{ color: 'var(--neo-text-muted)' }}>{total.toLocaleString()}</span>
                  <span className="text-[10px] w-14 text-right" style={{ color: '#f59e0b' }}>${(a.total_cost_usd || 0).toFixed(4)}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Per-tool breakdown */}
      {data.per_tool?.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold mb-2" style={{ color: 'var(--neo-text)' }}>Per Tool</h4>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {data.per_tool.slice(0, 9).map((t, i) => (
              <div key={t.tool || i} className="flex items-center justify-between px-2 py-1.5 rounded-lg text-xs"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
                <span style={{ color: 'var(--neo-text)' }}>{t.tool}</span>
                <span style={{ color: 'var(--neo-text-muted)' }}>{t.calls}× {t.avg_ms > 0 ? `${Math.round(t.avg_ms)}ms` : ''}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}


// ── Trust Panel ──────────────────────────────────────────────────────

function TrustPanel() {
  const [scores, setScores] = useState([]);
  const [message, setMessage] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get('/dashboard/trust')
      .then(r => {
        setScores(r.data.scores || []);
        setMessage(r.data.message || null);
      })
      .catch(() => setMessage('No agents registered yet. Agents appear here once they connect and interact with the system.'))
      .finally(() => setLoading(false));
  }, []);

  const levelColors = {
    trusted: 'var(--neo-green)',
    verified: 'var(--neo-blue)',
    provisional: '#f59e0b',
    untrusted: '#ef4444',
  };

  if (loading) return <div className="flex justify-center py-8"><Loader2 className="animate-spin" size={20} style={{ color: 'var(--neo-blue)' }} /></div>;
  if (scores.length === 0) {
    return (
      <div className="text-center py-12">
        <Shield size={32} className="mx-auto mb-3" style={{ color: 'var(--neo-text-muted)', opacity: 0.4 }} />
        <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>{message || 'No agents registered yet.'}</p>
        <p className="text-xs mt-1" style={{ color: 'var(--neo-text-muted)', opacity: 0.7 }}>
          Connect an agent via the SDK, MCP, or API to see trust scores here.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="text-xs mb-2" style={{ color: 'var(--neo-text-muted)' }}>
        Agent trust levels — earned through verified claims. False claims are penalized 5x.
      </div>

      {scores.length === 0 ? (
        <p className="text-sm text-center py-6" style={{ color: 'var(--neo-text-muted)' }}>
          No trust scores yet. Agents earn trust through verified claims.
        </p>
      ) : (
        <div className="space-y-2">
          {scores.map(s => (
            <div key={s.agent_id} className="flex items-center justify-between p-3 rounded-lg"
              style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center"
                  style={{ background: `${levelColors[s.trust_level] || '#888'}20` }}>
                  <Shield size={14} style={{ color: levelColors[s.trust_level] || '#888' }} />
                </div>
                <div>
                  <div className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>{s.agent_id}</div>
                  <div className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                    {s.verified_claims} verified · {s.false_claims} false · {s.total_actions} actions
                  </div>
                </div>
              </div>
              <div className="text-right">
                <span className="text-xs font-medium px-2 py-0.5 rounded"
                  style={{ background: `${levelColors[s.trust_level] || '#888'}20`, color: levelColors[s.trust_level] || '#888' }}>
                  {s.trust_level}
                </span>
                <div className="text-[10px] mt-1" style={{ color: 'var(--neo-text-muted)' }}>
                  Score: {(s.trust_score * 100).toFixed(0)}%
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


// ── Server Config Panel ───────────────────────────────────────────────

function ServerConfigPanel() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [llmDefault, setLlmDefault] = useState('');
  const [embDefault, setEmbDefault] = useState('');
  const [fallbackEnabled, setFallbackEnabled] = useState(true);
  const [models, setModels] = useState({ llm_models: [], embedding_models: [] });

  useEffect(() => {
    Promise.all([
      api.get('/dashboard/config/server').catch(() => ({ data: {} })),
      api.get('/dashboard/ingest/models').catch(() => ({ data: { llm_models: [], embedding_models: [] } })),
    ]).then(([cfgRes, modRes]) => {
      const cfg = cfgRes.data;
      setConfig(cfg);
      setLlmDefault(cfg?.llm?.default_model || '');
      setEmbDefault(cfg?.embeddings?.default_model || '');
      setFallbackEnabled(cfg?.llm?.fallback_enabled !== false);
      setModels(modRes.data);
      setLoading(false);
    });
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.put('/dashboard/config/server', {
        llm: { default_model: llmDefault, fallback_enabled: fallbackEnabled },
        embeddings: { default_model: embDefault },
      });
      toast.success('Server configuration saved');
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="flex justify-center py-8"><Loader2 size={20} className="animate-spin" style={{ color: 'var(--neo-blue)' }} /></div>;

  const providers = config?.providers || {};

  return (
    <div className="space-y-4">
      {/* Provider Status */}
      <div className="p-4 rounded-xl" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
        <h3 className="text-sm font-medium mb-3" style={{ color: 'var(--neo-text)' }}>
          <Server size={14} className="inline mr-1.5" /> Provider Status
        </h3>
        <div className="grid grid-cols-3 gap-2">
          {Object.entries(providers).map(([name, active]) => (
            <div key={name} className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)' }}>
              {active
                ? <CheckCircle size={12} style={{ color: '#22c55e' }} />
                : <XCircle size={12} style={{ color: '#ef4444' }} />}
              <span style={{ color: active ? 'var(--neo-text)' : 'var(--neo-text-muted)' }}>
                {name.charAt(0).toUpperCase() + name.slice(1)}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Default Models */}
      <div className="p-4 rounded-xl" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
        <h3 className="text-sm font-medium mb-3" style={{ color: 'var(--neo-text)' }}>
          <Sparkles size={14} className="inline mr-1.5" /> Default Models
        </h3>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs mb-1 block" style={{ color: 'var(--neo-text-muted)' }}>Default LLM (entity extraction)</label>
            <select value={llmDefault} onChange={(e) => setLlmDefault(e.target.value)}
              className="w-full px-2.5 py-1.5 rounded text-sm outline-none"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}>
              <option value="">Auto-detect</option>
              {models.llm_models.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs mb-1 block" style={{ color: 'var(--neo-text-muted)' }}>Default Embedding Model</label>
            <select value={embDefault} onChange={(e) => setEmbDefault(e.target.value)}
              className="w-full px-2.5 py-1.5 rounded text-sm outline-none"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}>
              <option value="">None</option>
              {models.embedding_models.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
        </div>
        <div className="mt-3 flex items-center gap-2">
          <input type="checkbox" id="fallback" checked={fallbackEnabled}
            onChange={(e) => setFallbackEnabled(e.target.checked)} />
          <label htmlFor="fallback" className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
            Enable LLM fallback (auto-retry with local Ollama model if primary returns empty)
          </label>
        </div>
      </div>

      {/* Environment Info */}
      <div className="p-4 rounded-xl" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
        <h3 className="text-sm font-medium mb-3" style={{ color: 'var(--neo-text)' }}>
          <Database size={14} className="inline mr-1.5" /> Environment
        </h3>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className="flex justify-between px-3 py-1.5 rounded" style={{ background: 'var(--neo-bg)' }}>
            <span style={{ color: 'var(--neo-text-muted)' }}>Environment</span>
            <span style={{ color: 'var(--neo-text)' }}>{config?.env || 'development'}</span>
          </div>
          <div className="flex justify-between px-3 py-1.5 rounded" style={{ background: 'var(--neo-bg)' }}>
            <span style={{ color: 'var(--neo-text-muted)' }}>Rate Limit</span>
            <span style={{ color: 'var(--neo-text)' }}>{config?.rate_limit_rpm || 60} req/min</span>
          </div>
          <div className="flex justify-between px-3 py-1.5 rounded" style={{ background: 'var(--neo-bg)' }}>
            <span style={{ color: 'var(--neo-text-muted)' }}>Redis</span>
            <span style={{ color: config?.redis_url ? 'var(--neo-text)' : '#ef4444' }}>
              {config?.redis_url ? 'Connected' : 'Not configured'}
            </span>
          </div>
          <div className="flex justify-between px-3 py-1.5 rounded" style={{ background: 'var(--neo-bg)' }}>
            <span style={{ color: 'var(--neo-text-muted)' }}>Ollama</span>
            <span style={{ color: providers.ollama ? 'var(--neo-text)' : '#ef4444' }}>
              {providers.ollama ? config?.ollama_url : 'Offline'}
            </span>
          </div>
        </div>
      </div>

      {/* Save */}
      <button onClick={handleSave} disabled={saving}
        className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
        style={{ background: 'var(--neo-blue)', color: '#fff' }}>
        {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
        Save Configuration
      </button>
    </div>
  );
}

// ── Scheduled / Feed Pipelines Panel ─────────────────────────────────

const STATUS_COLORS = {
  active:    { bg: '#10b98120', text: '#10b981', label: 'Active' },
  draft:     { bg: '#64748b20', text: '#64748b', label: 'Draft' },
  paused:    { bg: '#f59e0b20', text: '#f59e0b', label: 'Paused' },
  error:     { bg: '#ef444420', text: '#ef4444', label: 'Error' },
  completed: { bg: '#3b82f620', text: '#3b82f6', label: 'Completed' },
  pending:   { bg: '#8b5cf620', text: '#8b5cf6', label: 'Pending' },
};

const TRIGGER_LABELS = {
  one_time: 'One-time',
  scheduled: 'Scheduled',
  webhook: 'Webhook',
};

function StatusBadge({ status }) {
  const s = STATUS_COLORS[status] || STATUS_COLORS.pending;
  return (
    <span className="text-[10px] font-medium px-1.5 py-0.5 rounded"
      style={{ background: s.bg, color: s.text }}>
      {s.label}
    </span>
  );
}

function FeedFormModal({ pipeline, onClose, onSaved }) {
  const isEdit = !!pipeline;
  const [name, setName] = useState(pipeline?.name || '');
  const [sourceUrl, setSourceUrl] = useState(pipeline?.source_url || '');
  const [maxPages, setMaxPages] = useState(pipeline?.max_pages ?? 5);
  const [triggerType, setTriggerType] = useState(pipeline?.trigger_type || 'one_time');
  const [intervalMinutes, setIntervalMinutes] = useState(pipeline?.interval_minutes ?? 60);
  const [includeKeywords, setIncludeKeywords] = useState((pipeline?.include_keywords || []).join(', '));
  const [excludeKeywords, setExcludeKeywords] = useState((pipeline?.exclude_keywords || []).join(', '));
  const [semanticFilter, setSemanticFilter] = useState(pipeline?.semantic_filter || '');
  const [llmModel, setLlmModel] = useState(pipeline?.llm_model || '');
  const [embeddingModel, setEmbeddingModel] = useState(pipeline?.embedding_model || '');
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    if (!name.trim()) { toast.error('Name is required'); return; }
    if (!sourceUrl.trim()) { toast.error('Source URL is required'); return; }
    setSaving(true);
    try {
      const body = {
        name: name.trim(),
        source_url: sourceUrl.trim(),
        max_pages: Number(maxPages) || 5,
        trigger_type: triggerType,
        interval_minutes: Number(intervalMinutes) || 60,
        include_keywords: includeKeywords.split(',').map(k => k.trim()).filter(Boolean),
        exclude_keywords: excludeKeywords.split(',').map(k => k.trim()).filter(Boolean),
        semantic_filter: semanticFilter.trim(),
        llm_model: llmModel.trim(),
        embedding_model: embeddingModel.trim(),
      };
      if (isEdit) {
        await api.put(`/dashboard/pipelines/${pipeline.id}`, body);
        toast.success('Pipeline updated');
      } else {
        await api.post('/dashboard/pipelines', body);
        toast.success('Pipeline created');
      }
      onSaved();
      onClose();
    } catch {
      // handled by interceptor
    } finally {
      setSaving(false);
    }
  };

  const inputCls = 'w-full px-3 py-2 rounded-lg text-sm outline-none';
  const inputStyle = { background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' };
  const labelCls = 'text-xs font-medium block mb-1';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: 'rgba(0,0,0,0.5)' }}>
      <div className="rounded-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto shadow-2xl"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4" style={{ borderBottom: '1px solid var(--neo-border)' }}>
          <h2 className="text-base font-bold" style={{ color: 'var(--neo-text)' }}>
            {isEdit ? 'Edit Pipeline' : 'New Pipeline'}
          </h2>
          <button onClick={onClose} className="p-1 rounded hover:opacity-70">
            <X size={18} style={{ color: 'var(--neo-text-muted)' }} />
          </button>
        </div>

        <div className="px-6 py-5 space-y-4">
          {/* Name + Trigger */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelCls} style={{ color: 'var(--neo-text)' }}>Name *</label>
              <input value={name} onChange={e => setName(e.target.value)} placeholder="My Pipeline"
                className={inputCls} style={inputStyle} autoFocus />
            </div>
            <div>
              <label className={labelCls} style={{ color: 'var(--neo-text)' }}>Trigger Type</label>
              <select value={triggerType} onChange={e => setTriggerType(e.target.value)}
                className={inputCls} style={inputStyle}>
                <option value="one_time">One-time</option>
                <option value="scheduled">Scheduled</option>
                <option value="webhook">Webhook</option>
              </select>
            </div>
          </div>

          {/* Source URL + Max Pages */}
          <div className="grid grid-cols-3 gap-3">
            <div className="col-span-2">
              <label className={labelCls} style={{ color: 'var(--neo-text)' }}>Source URL *</label>
              <input value={sourceUrl} onChange={e => setSourceUrl(e.target.value)} placeholder="https://example.com/feed"
                className={inputCls} style={inputStyle} />
            </div>
            <div>
              <label className={labelCls} style={{ color: 'var(--neo-text)' }}>Max Pages</label>
              <input type="number" value={maxPages} onChange={e => setMaxPages(e.target.value)} min={1} max={100}
                className={inputCls} style={inputStyle} />
            </div>
          </div>

          {/* Interval (only for scheduled) */}
          {triggerType === 'scheduled' && (
            <div>
              <label className={labelCls} style={{ color: 'var(--neo-text)' }}>Interval (minutes)</label>
              <input type="number" value={intervalMinutes} onChange={e => setIntervalMinutes(e.target.value)} min={1}
                className={inputCls} style={inputStyle} />
            </div>
          )}

          {/* Keywords */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelCls} style={{ color: 'var(--neo-text)' }}>Include Keywords (comma-separated)</label>
              <input value={includeKeywords} onChange={e => setIncludeKeywords(e.target.value)} placeholder="AI, machine learning"
                className={inputCls} style={inputStyle} />
            </div>
            <div>
              <label className={labelCls} style={{ color: 'var(--neo-text)' }}>Exclude Keywords (comma-separated)</label>
              <input value={excludeKeywords} onChange={e => setExcludeKeywords(e.target.value)} placeholder="spam, ads"
                className={inputCls} style={inputStyle} />
            </div>
          </div>

          {/* Semantic Filter */}
          <div>
            <label className={labelCls} style={{ color: 'var(--neo-text)' }}>Semantic Filter (topic/intent description)</label>
            <textarea value={semanticFilter} onChange={e => setSemanticFilter(e.target.value)}
              placeholder="Only ingest content about AI safety and alignment research"
              rows={2} className={`${inputCls} resize-none`} style={inputStyle} />
          </div>

          {/* Model overrides */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelCls} style={{ color: 'var(--neo-text)' }}>LLM Model override (optional)</label>
              <input value={llmModel} onChange={e => setLlmModel(e.target.value)} placeholder="groq/llama3-70b-8192"
                className={inputCls} style={inputStyle} />
            </div>
            <div>
              <label className={labelCls} style={{ color: 'var(--neo-text)' }}>Embedding Model override (optional)</label>
              <input value={embeddingModel} onChange={e => setEmbeddingModel(e.target.value)} placeholder="text-embedding-3-small"
                className={inputCls} style={inputStyle} />
            </div>
          </div>

          {/* Note about context */}
          <p className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
            Pipelines start as drafts. Attach a context from the Contexts page to activate.
          </p>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-6 py-4" style={{ borderTop: '1px solid var(--neo-border)' }}>
          <button onClick={onClose} className="px-4 py-2 rounded-lg text-sm" style={{ color: 'var(--neo-text-muted)' }}>
            Cancel
          </button>
          <button onClick={handleSave} disabled={saving || !name.trim() || !sourceUrl.trim()}
            className="flex items-center gap-1.5 px-5 py-2 rounded-lg text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
            style={{ background: 'var(--neo-green)', color: '#fff' }}>
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            {isEdit ? 'Update' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  );
}

function PipelineMonitorPanel() {
  const [contexts, setContexts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedRuns, setExpandedRuns] = useState({}); // contextId -> runs[]
  const [loadingRuns, setLoadingRuns] = useState({}); // contextId -> bool

  const fetchContexts = useCallback(() => {
    setLoading(true);
    api.get('/dashboard/contexts')
      .then(res => {
        const all = res.data.contexts || [];
        setContexts(all.filter(c => c.config?.pipeline));
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  useEffect(() => { fetchContexts(); }, [fetchContexts]);

  const handleRunNow = async (ctx) => {
    try {
      await api.post(`/dashboard/contexts/${ctx.context_id}/pipeline-runs`);
      toast.success(`Pipeline for "${ctx.name}" queued`);
      fetchContexts();
    } catch { /* handled by interceptor */ }
  };

  const handlePause = async (ctx) => {
    try {
      const newStatus = ctx.config?.schedule?.status === 'paused' ? 'active' : 'paused';
      await api.patch(`/dashboard/contexts/${ctx.context_id}`, {
        config: { ...ctx.config, schedule: { ...(ctx.config?.schedule || {}), status: newStatus } },
      });
      toast.success(`Pipeline ${newStatus === 'paused' ? 'paused' : 'resumed'}`);
      fetchContexts();
    } catch { /* handled */ }
  };

  const toggleRuns = async (ctx) => {
    const id = ctx.context_id;
    if (expandedRuns[id]) {
      setExpandedRuns(prev => { const n = { ...prev }; delete n[id]; return n; });
      return;
    }
    setLoadingRuns(prev => ({ ...prev, [id]: true }));
    try {
      const res = await api.get(`/dashboard/contexts/${id}/pipeline-runs`);
      setExpandedRuns(prev => ({ ...prev, [id]: res.data.runs || [] }));
    } catch {
      setExpandedRuns(prev => ({ ...prev, [id]: [] }));
    } finally {
      setLoadingRuns(prev => ({ ...prev, [id]: false }));
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 size={22} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      </div>
    );
  }

  if (contexts.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 rounded-xl"
        style={{ border: '2px dashed var(--neo-border)' }}>
        <Rss size={32} className="mb-3" style={{ color: 'var(--neo-text-muted)' }} />
        <p className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>No pipeline-enabled contexts</p>
        <p className="text-xs mt-1 text-center max-w-xs" style={{ color: 'var(--neo-text-muted)' }}>
          Configure a Data Source when creating a context on the Contexts page to enable auto-ingestion.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
        Contexts with configured pipeline data sources. Create pipelines from the Contexts page.
      </p>
      <div className="rounded-xl overflow-hidden" style={{ border: '1px solid var(--neo-border)' }}>
        <table className="w-full text-xs">
          <thead>
            <tr style={{ background: 'var(--neo-surface)', borderBottom: '1px solid var(--neo-border)' }}>
              {['Context', 'Template', 'Source URL', 'Schedule', 'Last Run', 'Status', 'Total Docs', 'Actions'].map(h => (
                <th key={h} className="text-left px-3 py-2.5 font-semibold"
                  style={{ color: 'var(--neo-text-muted)' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {contexts.map((ctx, i) => {
              const pl = ctx.config?.pipeline || {};
              const sched = ctx.config?.schedule || {};
              const isPaused = sched.status === 'paused';
              const runs = expandedRuns[ctx.context_id];
              const loadingRunsForCtx = loadingRuns[ctx.context_id];
              return (
                <React.Fragment key={ctx.context_id}>
                  <tr style={{
                    borderBottom: '1px solid var(--neo-border)',
                    background: 'var(--neo-bg)',
                  }}>
                    {/* Context Name */}
                    <td className="px-3 py-2.5">
                      <span className="font-medium" style={{ color: 'var(--neo-text)' }}>{ctx.name}</span>
                    </td>
                    {/* Template */}
                    <td className="px-3 py-2.5" style={{ color: 'var(--neo-text-muted)' }}>
                      {pl.template_id || '—'}
                    </td>
                    {/* Source URL */}
                    <td className="px-3 py-2.5 max-w-[160px]">
                      {pl.source_url ? (
                        <a href={pl.source_url} target="_blank" rel="noreferrer"
                          className="flex items-center gap-1 hover:opacity-80 truncate"
                          style={{ color: 'var(--neo-blue)' }} title={pl.source_url}>
                          <Link size={10} className="shrink-0" />
                          <span className="truncate">{pl.source_url}</span>
                        </a>
                      ) : <span style={{ opacity: 0.4 }}>—</span>}
                    </td>
                    {/* Schedule */}
                    <td className="px-3 py-2.5" style={{ color: 'var(--neo-text-muted)' }}>
                      {TRIGGER_LABELS[pl.trigger_type] || pl.trigger_type || '—'}
                      {pl.trigger_type === 'scheduled' && pl.interval_minutes ? ` · ${pl.interval_minutes}m` : ''}
                    </td>
                    {/* Last Run */}
                    <td className="px-3 py-2.5" style={{ color: 'var(--neo-text-muted)' }}>
                      {sched.last_run_at ? new Date(sched.last_run_at).toLocaleString() : '—'}
                    </td>
                    {/* Status */}
                    <td className="px-3 py-2.5">
                      <StatusBadge status={isPaused ? 'paused' : sched.status || 'active'} />
                    </td>
                    {/* Total Docs */}
                    <td className="px-3 py-2.5" style={{ color: 'var(--neo-text-muted)' }}>
                      {sched.total_documents ?? '—'}
                    </td>
                    {/* Actions */}
                    <td className="px-3 py-2.5">
                      <div className="flex items-center gap-1">
                        <button onClick={() => handleRunNow(ctx)} title="Run Now"
                          className="p-1 rounded hover:opacity-80 transition"
                          style={{ color: 'var(--neo-green)' }}>
                          <Play size={13} />
                        </button>
                        <button onClick={() => handlePause(ctx)} title={isPaused ? 'Resume' : 'Pause'}
                          className="p-1 rounded hover:opacity-80 transition"
                          style={{ color: isPaused ? 'var(--neo-blue)' : '#f59e0b' }}>
                          {isPaused ? <RefreshCw size={13} /> : <Pause size={13} />}
                        </button>
                        <button onClick={() => toggleRuns(ctx)} title="View Runs"
                          className="p-1 rounded hover:opacity-80 transition"
                          style={{ color: 'var(--neo-text-muted)' }}>
                          {loadingRunsForCtx
                            ? <Loader2 size={13} className="animate-spin" />
                            : <Eye size={13} />}
                        </button>
                      </div>
                    </td>
                  </tr>
                  {runs !== undefined && (
                    <tr style={{ background: 'var(--neo-surface)' }}>
                      <td colSpan={8} className="px-4 py-3">
                        {runs.length === 0 ? (
                          <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>No runs yet.</p>
                        ) : (
                          <div className="space-y-1.5">
                            {runs.map((run, ri) => (
                              <div key={run.run_id || ri} className="flex items-center gap-3 text-xs px-2 py-1.5 rounded"
                                style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)' }}>
                                <StatusBadge status={run.status || 'pending'} />
                                <span style={{ color: 'var(--neo-text-muted)' }}>
                                  {run.started_at ? new Date(run.started_at).toLocaleString() : '—'}
                                </span>
                                {run.total_documents != null && (
                                  <span style={{ color: 'var(--neo-text-muted)' }}>{run.total_documents} docs</span>
                                )}
                                {run.error && (
                                  <span className="truncate max-w-xs" style={{ color: '#ef4444' }}>{run.error}</span>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────

export default function PipelinesPage() {
  const [pipelines, setPipelines] = useState([]);
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selectedPipeline, setSelectedPipeline] = useState(null);
  const [graphs, setGraphs] = useState([]);
  const [view, setView] = useState('templates'); // templates | feeds | runs | config

  // Run form state
  const [showRunForm, setShowRunForm] = useState(false);
  const [runGraph, setRunGraph] = useState('');
  const [runSource, setRunSource] = useState('text'); // text | url | file
  const [runText, setRunText] = useState('');
  const [runUrl, setRunUrl] = useState('');
  const [runFile, setRunFile] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const [runClassification, setRunClassification] = useState('internal');
  const [runParams, setRunParams] = useState({});
  const [submitting, setSubmitting] = useState(false);

  // View a specific run via StepWisePipelinePanel
  const [viewingRun, setViewingRun] = useState(null); // { run_id, graph }

  // Create/Edit modal state
  const [showFormModal, setShowFormModal] = useState(false);
  const [editingPipeline, setEditingPipeline] = useState(null); // null = create, object = edit

  // Fetch pipelines + graphs
  const fetchData = useCallback(() => {
    const safe = (p) => p.catch((err) => {
      // Let 401 propagate to interceptor (redirects to login)
      if (err.response?.status === 401) throw err;
      return { data: {} };
    });
    Promise.all([
      safe(api.get('/dashboard/ingest/pipeline-templates')),
      safe(api.get('/dashboard/graphs')),
      safe(api.get('/dashboard/pipelines/runs?limit=50')),
    ]).then(([pRes, gRes, rRes]) => {
      setPipelines(pRes.data.pipelines || []);
      setGraphs(gRes.data.graphs || []);
      setRuns(rRes.data.runs || []);
      setLoading(false);
    }).catch((err) => {
      console.error('[PipelinesPage] fetch error:', err);
      setLoading(false);
    });
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Poll runs that are still running
  useEffect(() => {
    const hasRunning = runs.some(r => r.status === 'running' || r.status === 'pending');
    if (!hasRunning) return;
    const interval = setInterval(() => {
      api.get('/dashboard/pipelines/runs?limit=50')
        .then(r => setRuns(r.data.runs || []))
        .catch(() => {});
    }, 3000);
    return () => clearInterval(interval);
  }, [runs]);

  const handleRun = async () => {
    if (!selectedPipeline || !runGraph) return;
    setSubmitting(true);
    try {
      let res;
      if (runSource === 'file' && runFile) {
        const formData = new FormData();
        formData.append('file', runFile);
        formData.append('graph', runGraph);
        formData.append('data_classification', runClassification);
        if (Object.keys(runParams).length > 0) {
          formData.append('params', JSON.stringify(runParams));
        }
        res = await api.post(
          `/dashboard/pipelines/${selectedPipeline.id}/run/file`,
          formData,
          { headers: { 'Content-Type': 'multipart/form-data' } },
        );
      } else {
        const body = {
          graph: runGraph,
          params: runParams,
          data_classification: runClassification,
        };
        if (runSource === 'text') body.text = runText;
        else body.url = runUrl;
        res = await api.post(`/dashboard/pipelines/${selectedPipeline.id}/run`, body);
      }

      toast.success(`Pipeline "${selectedPipeline.name}" started`);
      setShowRunForm(false);
      setRunText('');
      setRunUrl('');
      setRunFile(null);
      const newRun = {
        run_id: res.data.run_id,
        pipeline_id: selectedPipeline.id,
        pipeline_name: selectedPipeline.name,
        graph: runGraph,
        status: 'running',
        started_at: new Date().toISOString(),
        stages: selectedPipeline.stages.map(s => ({ name: s.name, stage_type: s.type, status: 'pending' })),
      };
      setRuns(prev => [newRun, ...prev]);
      setView('runs');
    } catch (err) {
      const detail = err.response?.data?.detail;
      toast.error(typeof detail === 'string' ? detail : 'Failed to start pipeline');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (p, e) => {
    e.stopPropagation();
    if (!window.confirm(`Delete pipeline "${p.name}"? This cannot be undone.`)) return;
    try {
      await api.delete(`/dashboard/ingest/pipeline-templates/${p.id}`);
      toast.success(`Pipeline "${p.name}" deleted`);
      if (selectedPipeline?.id === p.id) setSelectedPipeline(null);
      fetchData();
    } catch {
      // toast handled by interceptor
    }
  };

  const openEdit = (p, e) => {
    e.stopPropagation();
    setEditingPipeline(p);
    setShowFormModal(true);
  };

  const openCreate = () => {
    setEditingPipeline(null);
    setShowFormModal(true);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold" style={{ color: 'var(--neo-text)' }}>Pipelines</h1>
          <p className="text-xs mt-0.5" style={{ color: 'var(--neo-text-muted)' }}>
            Stored procedures for ingestion, extraction, and graph building
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setView('templates')}
            className="px-3 py-1.5 rounded-lg text-xs font-medium transition"
            style={{
              background: view === 'templates' ? 'var(--neo-bg)' : 'transparent',
              color: view === 'templates' ? 'var(--neo-text)' : 'var(--neo-text-muted)',
              border: view === 'templates' ? '1px solid var(--neo-border)' : '1px solid transparent',
            }}
          >
            <Workflow size={13} className="inline mr-1" /> Pipeline Setup
          </button>
          <button
            onClick={() => setView('feeds')}
            className="px-3 py-1.5 rounded-lg text-xs font-medium transition"
            style={{
              background: view === 'feeds' ? 'var(--neo-bg)' : 'transparent',
              color: view === 'feeds' ? 'var(--neo-text)' : 'var(--neo-text-muted)',
              border: view === 'feeds' ? '1px solid var(--neo-border)' : '1px solid transparent',
            }}
          >
            <Rss size={13} className="inline mr-1" /> Monitor
          </button>
          <button
            onClick={() => setView('runs')}
            className="px-3 py-1.5 rounded-lg text-xs font-medium transition"
            style={{
              background: view === 'runs' ? 'var(--neo-bg)' : 'transparent',
              color: view === 'runs' ? 'var(--neo-text)' : 'var(--neo-text-muted)',
              border: view === 'runs' ? '1px solid var(--neo-border)' : '1px solid transparent',
            }}
          >
            <Clock size={13} className="inline mr-1" /> Run History ({runs.length})
          </button>
          <button
            onClick={() => setView('config')}
            className="px-3 py-1.5 rounded-lg text-xs font-medium transition"
            style={{
              background: view === 'config' ? 'var(--neo-bg)' : 'transparent',
              color: view === 'config' ? 'var(--neo-text)' : 'var(--neo-text-muted)',
              border: view === 'config' ? '1px solid var(--neo-border)' : '1px solid transparent',
            }}
          >
            <Settings size={13} className="inline mr-1" /> Server Config
          </button>
          <button
            onClick={() => setView('webhooks')}
            className="px-3 py-1.5 rounded-lg text-xs font-medium transition"
            style={{
              background: view === 'webhooks' ? 'var(--neo-bg)' : 'transparent',
              color: view === 'webhooks' ? 'var(--neo-text)' : 'var(--neo-text-muted)',
              border: view === 'webhooks' ? '1px solid var(--neo-border)' : '1px solid transparent',
            }}
          >
            <Globe size={13} className="inline mr-1" /> Webhooks
          </button>
          <button
            onClick={() => setView('trust')}
            className="px-3 py-1.5 rounded-lg text-xs font-medium transition"
            style={{
              background: view === 'trust' ? 'var(--neo-bg)' : 'transparent',
              color: view === 'trust' ? 'var(--neo-text)' : 'var(--neo-text-muted)',
              border: view === 'trust' ? '1px solid var(--neo-border)' : '1px solid transparent',
            }}
          >
            <Shield size={13} className="inline mr-1" /> Trust
          </button>
          <button
            onClick={() => setView('consumption')}
            className="px-3 py-1.5 rounded-lg text-xs font-medium transition"
            style={{
              background: view === 'consumption' ? 'var(--neo-bg)' : 'transparent',
              color: view === 'consumption' ? 'var(--neo-text)' : 'var(--neo-text-muted)',
              border: view === 'consumption' ? '1px solid var(--neo-border)' : '1px solid transparent',
            }}
          >
            <BarChart3 size={13} className="inline mr-1" /> Usage
          </button>
        </div>
      </div>

      {view === 'templates' && (
        <>
          {/* Toolbar */}
          <div className="flex items-center justify-between mb-4">
            <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
              Pipeline templates define the processing chain for ingestion. Select one when creating a context.
            </p>
            <button onClick={openCreate}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium hover:opacity-90 transition"
              style={{ background: 'var(--neo-blue)', color: '#fff' }}>
              <Plus size={13} /> New Pipeline
            </button>
          </div>

          {/* Pipeline Cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
            {pipelines.map(p => {
              const isSelected = selectedPipeline?.id === p.id;
              const stats = p.stats || {};
              return (
                <div
                  key={p.id}
                  className="rounded-xl p-4 cursor-pointer transition hover:shadow-md group"
                  style={{
                    background: 'var(--neo-surface)',
                    border: isSelected ? '2px solid var(--neo-blue)' : '1px solid var(--neo-border)',
                  }}
                  onClick={() => {
                    setSelectedPipeline(isSelected ? null : p);
                    setShowRunForm(false);
                    setRunParams(p.default_params || {});
                  }}
                >
                  {/* Name + tags + actions */}
                  <div className="flex items-start justify-between mb-2">
                    <div>
                      <h3 className="text-sm font-semibold" style={{ color: 'var(--neo-text)' }}>{p.name}</h3>
                      {p.builtin && (
                        <span className="text-[10px] font-medium px-1.5 py-0.5 rounded mt-0.5 inline-block"
                              style={{ background: 'var(--neo-blue)/15', color: 'var(--neo-blue)' }}>
                          Built-in
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-1">
                      <ClassificationBadge classification={p.governance?.classification || 'internal'} />
                      <button
                        onClick={(e) => { e.stopPropagation(); setSelectedPipeline(isSelected ? null : p); }}
                        className="p-1 rounded transition hover:opacity-80"
                        style={{ color: 'var(--neo-blue)' }}
                        title="View details"
                      >
                        <Eye size={13} />
                      </button>
                      <button
                        onClick={(e) => openEdit(p, e)}
                        className="p-1 rounded transition hover:opacity-80"
                        style={{ color: 'var(--neo-text-muted)' }}
                        title="Edit pipeline"
                      >
                        <Pencil size={13} />
                      </button>
                      <button
                        onClick={(e) => handleDelete(p, e)}
                        className="p-1 rounded transition hover:opacity-80"
                        style={{ color: 'var(--neo-text-muted)' }}
                        title="Delete pipeline"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>

                  {/* Description */}
                  <p className="text-xs mb-3" style={{ color: 'var(--neo-text-muted)' }}>{p.description}</p>

                  {/* Stage flow */}
                  <StageFlow stages={p.stages || []} size="sm" />

                  {/* Stats row */}
                  {(stats.total_runs > 0) && (
                    <div className="flex items-center gap-3 mt-3 pt-2" style={{ borderTop: '1px solid var(--neo-border)' }}>
                      <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                        {stats.total_runs} runs
                      </span>
                      {stats.avg_accuracy > 0 && (
                        <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                          Avg accuracy: <AccuracyBadge score={stats.avg_accuracy} />
                        </span>
                      )}
                      {stats.avg_duration_ms > 0 && (
                        <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                          ~{Math.round(stats.avg_duration_ms / 1000)}s avg
                        </span>
                      )}
                    </div>
                  )}

                  {/* Tags */}
                  {p.tags && p.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {p.tags.map(tag => (
                        <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded"
                              style={{ background: 'var(--neo-bg)', color: 'var(--neo-text-muted)' }}>
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Run Form (appears when pipeline selected) */}
          {selectedPipeline && (
            <div className="rounded-xl p-5 mb-6"
                 style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h3 className="text-sm font-semibold" style={{ color: 'var(--neo-text)' }}>
                    Run: {selectedPipeline.name}
                  </h3>
                  <StageFlow stages={selectedPipeline.stages || []} />
                </div>
                <button
                  onClick={() => setShowRunForm(!showRunForm)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90"
                  style={{ background: 'var(--neo-blue)', color: '#fff' }}
                >
                  <Play size={13} /> Run Pipeline
                </button>
              </div>

              {showRunForm && (
                <div className="space-y-4 pt-3" style={{ borderTop: '1px solid var(--neo-border)' }}>
                  {/* Graph selector */}
                  <div>
                    <label className="text-xs font-medium block mb-1" style={{ color: 'var(--neo-text)' }}>
                      Target Graph
                    </label>
                    <select
                      value={runGraph}
                      onChange={e => setRunGraph(e.target.value)}
                      className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                      style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                    >
                      <option value="">Select graph...</option>
                      {graphs.map(g => (
                        <option key={g.name || g.display_name} value={g.display_name || g.name}>
                          {g.display_name || g.name}
                        </option>
                      ))}
                    </select>
                  </div>

                  {/* Source type tabs */}
                  <div>
                    <label className="text-xs font-medium block mb-1" style={{ color: 'var(--neo-text)' }}>
                      Source
                    </label>
                    <div className="flex gap-2 mb-2">
                      {[
                        { id: 'text', label: 'Text', icon: FileText },
                        { id: 'file', label: 'File', icon: Upload },
                        { id: 'url', label: 'URL', icon: Globe },
                      ].map(s => (
                        <button
                          key={s.id}
                          onClick={() => setRunSource(s.id)}
                          className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium transition"
                          style={{
                            background: runSource === s.id ? 'var(--neo-bg)' : 'transparent',
                            color: runSource === s.id ? 'var(--neo-text)' : 'var(--neo-text-muted)',
                            border: runSource === s.id ? '1px solid var(--neo-border)' : '1px solid transparent',
                          }}
                        >
                          <s.icon size={12} /> {s.label}
                        </button>
                      ))}
                    </div>
                    {runSource === 'text' && (
                      <textarea
                        value={runText}
                        onChange={e => setRunText(e.target.value)}
                        placeholder="Paste text to ingest..."
                        rows={6}
                        className="w-full px-3 py-2 rounded-lg text-sm outline-none resize-none"
                        style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                      />
                    )}
                    {runSource === 'file' && (
                      <div
                        className="rounded-xl p-6 text-center transition cursor-pointer"
                        style={{
                          background: dragOver ? 'var(--neo-blue)/8' : 'var(--neo-bg)',
                          border: dragOver ? '2px dashed var(--neo-blue)' : '2px dashed var(--neo-border)',
                        }}
                        onDragOver={e => { e.preventDefault(); setDragOver(true); }}
                        onDragLeave={() => setDragOver(false)}
                        onDrop={e => {
                          e.preventDefault();
                          setDragOver(false);
                          const f = e.dataTransfer.files[0];
                          if (f) setRunFile(f);
                        }}
                        onClick={() => {
                          const input = document.createElement('input');
                          input.type = 'file';
                          input.accept = '.pdf,.docx,.csv,.json,.txt,.html,.xml,.md';
                          input.onchange = e => { if (e.target.files[0]) setRunFile(e.target.files[0]); };
                          input.click();
                        }}
                      >
                        {runFile ? (
                          <div className="flex items-center justify-center gap-2">
                            <FileText size={16} style={{ color: 'var(--neo-blue)' }} />
                            <span className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>
                              {runFile.name}
                            </span>
                            <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                              ({(runFile.size / 1024).toFixed(1)} KB)
                            </span>
                            <button
                              onClick={e => { e.stopPropagation(); setRunFile(null); }}
                              className="text-xs ml-2 px-1.5 py-0.5 rounded hover:opacity-80"
                              style={{ color: 'var(--neo-text-muted)' }}
                            >
                              ✕
                            </button>
                          </div>
                        ) : (
                          <>
                            <Upload size={24} className="mx-auto mb-2" style={{ color: 'var(--neo-text-muted)' }} />
                            <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                              Drop a file or click to browse
                            </p>
                            <p className="text-[10px] mt-1" style={{ color: 'var(--neo-text-muted)', opacity: 0.6 }}>
                              PDF, DOCX, CSV, JSON, TXT, HTML, XML, MD
                            </p>
                          </>
                        )}
                      </div>
                    )}
                    {runSource === 'url' && (
                      <input
                        value={runUrl}
                        onChange={e => setRunUrl(e.target.value)}
                        placeholder="https://example.com/article"
                        className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                        style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                      />
                    )}
                  </div>

                  {/* Governance */}
                  <div className="flex items-center gap-4">
                    <div>
                      <label className="text-xs font-medium block mb-1" style={{ color: 'var(--neo-text)' }}>
                        <Shield size={11} className="inline mr-1" /> Data Classification
                      </label>
                      <select
                        value={runClassification}
                        onChange={e => setRunClassification(e.target.value)}
                        className="px-3 py-1.5 rounded-lg text-xs outline-none"
                        style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                      >
                        <option value="public">Public</option>
                        <option value="internal">Internal</option>
                        <option value="confidential">Confidential</option>
                        <option value="restricted">Restricted</option>
                      </select>
                    </div>
                  </div>

                  {/* Pipeline params (editable JSON) */}
                  {selectedPipeline.default_params && Object.keys(selectedPipeline.default_params).length > 0 && (
                    <div>
                      <label className="text-xs font-medium block mb-1" style={{ color: 'var(--neo-text)' }}>
                        Parameters
                      </label>
                      <div className="grid grid-cols-2 gap-2">
                        {Object.entries(selectedPipeline.default_params).map(([key, defaultVal]) => (
                          <div key={key}>
                            <label className="text-[10px] block mb-0.5" style={{ color: 'var(--neo-text-muted)' }}>
                              {key}
                            </label>
                            <input
                              value={runParams[key] ?? defaultVal ?? ''}
                              onChange={e => setRunParams(prev => ({ ...prev, [key]: e.target.value }))}
                              className="w-full px-2 py-1 rounded text-xs outline-none"
                              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                            />
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Submit */}
                  <div className="flex justify-end gap-2 pt-2">
                    <button
                      onClick={() => setShowRunForm(false)}
                      className="px-3 py-1.5 rounded-lg text-xs transition"
                      style={{ color: 'var(--neo-text-muted)' }}
                    >
                      Cancel
                    </button>
                    <button
                      onClick={handleRun}
                      disabled={submitting || !runGraph || (runSource === 'text' ? !runText.trim() : runSource === 'file' ? !runFile : !runUrl.trim())}
                      className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90 disabled:opacity-50"
                      style={{ background: 'var(--neo-green)', color: '#fff' }}
                    >
                      {submitting ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
                      Execute
                    </button>
                  </div>
                </div>
              )}

              {/* Governance info */}
              {selectedPipeline.governance && (
                <div className="mt-3 pt-3 flex items-center gap-4 text-[10px]"
                     style={{ borderTop: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}>
                  <span><Shield size={10} className="inline mr-0.5" /> Owner: {selectedPipeline.governance.owner}</span>
                  {selectedPipeline.governance.approved_by && (
                    <span>Approved by: {selectedPipeline.governance.approved_by}</span>
                  )}
                  <span>Version: {selectedPipeline.version}</span>
                </div>
              )}
            </div>
          )}
        </>
      )}

      {/* Viewing a specific run */}
      {viewingRun && (
        <div className="rounded-xl p-4 mb-4" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
          <StepWisePipelinePanel
            graphName={viewingRun.graph}
            resumeRunId={viewingRun.run_id}
            onComplete={() => { setViewingRun(null); fetchData(); }}
            onCancel={() => setViewingRun(null)}
          />
        </div>
      )}

      {/* Run History View */}
      {view === 'runs' && (
        <div className="space-y-2">
          {runs.length === 0 ? (
            <div className="text-center py-16">
              <Clock size={32} className="mx-auto mb-3" style={{ color: 'var(--neo-text-muted)', opacity: 0.4 }} />
              <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>No pipeline runs yet</p>
              <p className="text-xs mt-1" style={{ color: 'var(--neo-text-muted)', opacity: 0.7 }}>
                Select a pipeline template and run it
              </p>
            </div>
          ) : (
            runs.map(run => (
              <RunCard key={run.run_id} run={run} onView={() => setViewingRun({ run_id: run.run_id, graph: run.graph })} />
            ))
          )}
        </div>
      )}

      {view === 'config' && <ServerConfigPanel />}

      {view === 'webhooks' && <WebhooksPanel />}

      {view === 'trust' && <TrustPanel />}

      {view === 'consumption' && <ConsumptionPanel />}

      {view === 'feeds' && <PipelineMonitorPanel />}

      {/* Create/Edit Pipeline Modal */}
      {showFormModal && (
        <PipelineFormModal
          pipeline={editingPipeline}
          onClose={() => { setShowFormModal(false); setEditingPipeline(null); }}
          onSaved={fetchData}
        />
      )}
    </div>
  );
}

// ── Run Card ──────────────────────────────────────────────────────────

function RunCard({ run, onView }) {
  const [expanded, setExpanded] = useState(false);
  const isRunning = run.status === 'running' || run.status === 'pending';
  const isCompleted = run.status === 'completed';
  const isFailed = run.status === 'failed';

  const statusColor = isCompleted ? 'var(--neo-green)' : isFailed ? '#ef4444' : 'var(--neo-blue)';
  const StatusIcon = isCompleted ? CheckCircle : isFailed ? XCircle : Loader2;

  return (
    <div
      className="rounded-xl p-4 transition"
      style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
    >
      <div className="flex items-center justify-between cursor-pointer" onClick={() => setExpanded(!expanded)}>
        <div className="flex items-center gap-3 min-w-0">
          <StatusIcon
            size={16}
            className={isRunning ? 'animate-spin' : ''}
            style={{ color: statusColor, flexShrink: 0 }}
          />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>
                {run.pipeline_name}
              </span>
              <span className="text-[10px] px-1.5 py-0.5 rounded font-medium"
                    style={{ color: statusColor, background: `${statusColor}15` }}>
                {run.status}
              </span>
              {run.accuracy_score > 0 && <AccuracyBadge score={run.accuracy_score} breakdown={run.accuracy_breakdown} />}
              {run.data_classification && <ClassificationBadge classification={run.data_classification} />}
            </div>
            <div className="flex items-center gap-3 mt-0.5">
              <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                <Database size={10} className="inline mr-0.5" /> {run.graph}
              </span>
              {run.duration_ms > 0 && (
                <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                  {run.duration_ms > 1000 ? `${(run.duration_ms / 1000).toFixed(1)}s` : `${run.duration_ms}ms`}
                </span>
              )}
              {(run.nodes_created > 0 || run.edges_created > 0) && (
                <span className="text-[10px] font-mono" style={{ color: 'var(--neo-text-muted)' }}>
                  +{run.nodes_created}n +{run.edges_created}e
                  {run.entities_extracted > 0 && ` ${run.entities_extracted} entities`}
                </span>
              )}
              <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                {run.started_at && new Date(run.started_at).toLocaleString()}
              </span>
            </div>
          </div>
        </div>
        {expanded ? <ChevronDown size={14} style={{ color: 'var(--neo-text-muted)' }} /> : <ChevronRight size={14} style={{ color: 'var(--neo-text-muted)' }} />}
      </div>

      {expanded && (
        <div className="mt-3 pt-3 space-y-3" style={{ borderTop: '1px solid var(--neo-border)' }}>
          {/* Stage progress */}
          <div>
            <span className="text-[10px] font-medium uppercase tracking-wider" style={{ color: 'var(--neo-text-muted)' }}>
              Stages
            </span>
            <div className="mt-1.5">
              <RunStages stages={run.stages} />
            </div>
            {/* Stage detail table */}
            <div className="mt-2 space-y-1">
              {(run.stages || []).map(s => (
                <div key={s.name} className="flex items-center justify-between text-[10px] px-2 py-1 rounded"
                     style={{ background: 'var(--neo-bg)' }}>
                  <span style={{ color: 'var(--neo-text)' }}>{STAGE_LABELS[s.stage_type] || s.name}</span>
                  <div className="flex items-center gap-3">
                    {s.items_created > 0 && (
                      <span style={{ color: 'var(--neo-text-muted)' }}>+{s.items_created} items</span>
                    )}
                    {s.duration_ms > 0 && (
                      <span className="font-mono" style={{ color: 'var(--neo-text-muted)' }}>{s.duration_ms}ms</span>
                    )}
                    <span style={{
                      color: s.status === 'completed' ? 'var(--neo-green)' : s.status === 'failed' ? '#ef4444' : 'var(--neo-text-muted)',
                    }}>{s.status}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Accuracy breakdown */}
          {run.accuracy_breakdown && Object.keys(run.accuracy_breakdown).length > 0 && (
            <div>
              <span className="text-[10px] font-medium uppercase tracking-wider" style={{ color: 'var(--neo-text-muted)' }}>
                <BarChart3 size={10} className="inline mr-1" /> Accuracy
              </span>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-2 mt-1.5">
                {[
                  { key: 'entity_confidence_avg', label: 'Entity Confidence' },
                  { key: 'relationship_coverage', label: 'Relationship Coverage' },
                  { key: 'chunk_quality', label: 'Chunk Quality' },
                  { key: 'embedding_coverage', label: 'Embedding Coverage' },
                  { key: 'schema_compliance', label: 'Schema Compliance' },
                ].map(({ key, label }) => {
                  const val = run.accuracy_breakdown[key];
                  if (!val && val !== 0) return null;
                  const pct = Math.round(val * 100);
                  return (
                    <div key={key} className="px-2 py-1.5 rounded" style={{ background: 'var(--neo-bg)' }}>
                      <div className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>{label}</div>
                      <div className="text-xs font-semibold mt-0.5" style={{ color: 'var(--neo-text)' }}>{pct}%</div>
                      <div className="w-full h-1 rounded-full mt-1" style={{ background: 'var(--neo-border)' }}>
                        <div className="h-1 rounded-full" style={{
                          width: `${pct}%`,
                          background: pct >= 80 ? 'var(--neo-green)' : pct >= 50 ? '#f59e0b' : '#ef4444',
                        }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Governance */}
          {run.governance_tags && run.governance_tags.length > 0 && (
            <div className="flex items-center gap-2">
              <Tag size={10} style={{ color: 'var(--neo-text-muted)' }} />
              {run.governance_tags.map(tag => (
                <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded"
                      style={{ background: 'var(--neo-bg)', color: 'var(--neo-text-muted)' }}>
                  {tag}
                </span>
              ))}
            </div>
          )}

          {/* Error */}
          {run.error && (
            <div className="text-xs px-3 py-2 rounded" style={{ background: '#ef444415', color: '#ef4444' }}>
              {typeof run.error === 'string' ? run.error : JSON.stringify(run.error)}
            </div>
          )}

          {/* View / Run ID */}
          <div className="flex items-center justify-between">
            <div className="text-[10px] font-mono" style={{ color: 'var(--neo-text-muted)', opacity: 0.5 }}>
              {run.run_id}
            </div>
            {(isRunning || isCompleted) && onView && (
              <button
                onClick={(e) => { e.stopPropagation(); onView(); }}
                className="flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium transition hover:opacity-80"
                style={{ background: 'var(--neo-blue)', color: '#fff' }}
              >
                <Eye size={10} /> {isRunning ? 'Monitor' : 'View'}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * ContextsPage — First-class Context management with built-in ingestion.
 *
 * List, create, and manage independent Context entities.
 * Each context is a typed knowledge store backed by a graph namespace.
 * Supports ingestion via text, file upload, and URL fetch.
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useEvent } from '../../context/EventContext';
import {
  Database, Loader2, Plus, X, RefreshCw, Trash2,
  User, Settings, BookOpen, FileText, Globe, Sparkles, Code2,
  Lock, Eye, Unlock, AlertTriangle, ChevronDown, ChevronUp, ChevronRight,
  Layers, Send, Upload, Link, CheckCircle, XCircle, Clock,
  Search, Tag, Hash, MessageSquare, Download, ListOrdered,
  Rss, Play, Pause, ArrowRight, Cpu, Brain, TrendingUp, TrendingDown, Minus, Zap,
} from 'lucide-react';
import toast from 'react-hot-toast';
import {
  ComposedChart, Line, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Area,
} from 'recharts';
import api from '../../lib/api';
import StepWisePipelinePanel from '../../components/StepWisePipelinePanel';
import DashboardGraphExplorer from '../../components/DashboardGraphExplorer';
import SchemaBuilder from '../../components/SchemaBuilder';
import AgentActivityFeed from '../../components/AgentActivityFeed';
import { useJobs } from '../../context/JobsContext';

const CONTEXT_TYPES = [
  { value: 'knowledge_base', label: 'Knowledge Base', icon: BookOpen, color: '#3b82f6',
    hint: 'Requirements, specs, PRDs, research, meeting notes, domain knowledge.',
    accepts: '.pdf,.docx,.doc,.txt,.md,.csv,.xlsx,.rtf',
    ingestModes: ['file', 'text', 'url', 'connector'] },
  { value: 'software_dev', label: 'Software Dev', icon: Code2, color: '#10b981',
    hint: 'Source code, configs, schemas, project files. Upload or connect a git repo.',
    accepts: '.py,.js,.ts,.tsx,.jsx,.go,.rs,.java,.json,.yaml,.yml,.toml,.sql,.sh,.css,.html,.xml,.pdf,.docx,.txt,.md',
    ingestModes: ['file', 'text', 'url', 'connector'] },
  { value: 'rules', label: 'Rules & Standards', icon: Settings, color: '#8b5cf6',
    hint: 'Coding standards, design guidelines, constraints, compliance rules.',
    accepts: '.txt,.md,.yaml,.yml,.pdf,.docx',
    ingestModes: ['text', 'file', 'url'] },
  { value: 'database', label: 'Database', icon: Database, color: '#f59e0b',
    hint: 'Database metadata, schemas, table definitions. Connect via connection string.',
    accepts: '.sql,.json,.csv,.xlsx',
    ingestModes: ['connector', 'file', 'text'] },
  { value: 'decision', label: 'Decisions', icon: Sparkles, color: '#ec4899',
    hint: 'Architecture decisions, trade-offs, rationale, ADRs.',
    accepts: '.txt,.md,.pdf,.docx',
    ingestModes: ['text', 'file'] },
  { value: 'web', label: 'Web Content', icon: Globe, color: '#06b6d4',
    hint: 'Web pages, API docs, external references. Paste URL to crawl.',
    accepts: '',
    ingestModes: ['url', 'text', 'connector'] },
];

// Backward compat: map old types to new
const _OLD_TYPE_MAP = {
  'user': 'knowledge_base', 'system': 'rules',
  'generated': 'knowledge_base', 'document': 'knowledge_base', 'code': 'software_dev',
  'knowledge': 'knowledge_base',
};

const SENSITIVITY_LEVELS = [
  { value: 'public', label: 'Public', color: '#22c55e', icon: Unlock },
  { value: 'internal', label: 'Internal', color: '#3b82f6', icon: Eye },
  { value: 'confidential', label: 'Confidential', color: '#f59e0b', icon: Lock },
  { value: 'restricted', label: 'Restricted', color: '#ef4444', icon: AlertTriangle },
];

const STAGE_ICONS = {
  pending: <Clock size={11} style={{ color: 'var(--neo-text-muted)' }} />,
  running: <Loader2 size={11} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />,
  completed: <CheckCircle size={11} style={{ color: '#22c55e' }} />,
  failed: <XCircle size={11} style={{ color: '#ef4444' }} />,
};

const STAGE_LABELS = {
  PARSE_FILE: 'Parse', CLASSIFY: 'Classify', CHUNK: 'Chunk',
  MAP_TABULAR: 'Map', EXTRACT: 'Extract', EXTRACT_FACTS: 'Facts',
  EMBED: 'Embed', INDEX_BM25: 'BM25', STORE_VECTORS: 'Vectors',
  CANONICALIZE: 'Canon.', ENHANCE_GRAPH: 'Enhance', PERSIST: 'Persist',
  IMPORT_GRAPH: 'Import', VALIDATE_SCHEMA: 'Validate',
};

const STAGE_TOOLTIPS = {
  PARSE_FILE: 'Parse the uploaded file (PDF, DOCX, CSV, etc.) into raw text and tables',
  CLASSIFY: 'Classify the document type to determine which pipeline stages to run',
  CHUNK: 'Split text into semantic chunks for retrieval (paragraph or sentence boundaries)',
  MAP_TABULAR: 'Map tabular data (CSV/Excel) into structured graph nodes',
  EXTRACT: 'Extract entities and relationships from text using LLM (Groq/OpenAI)',
  EXTRACT_FACTS: 'Extract verifiable facts (claims, metrics, dates, decisions) using LLM',
  EMBED: 'Generate vector embeddings for semantic search',
  INDEX_BM25: 'Build BM25 full-text search index for keyword search',
  STORE_VECTORS: 'Store embeddings in the vector database for similarity search',
  CANONICALIZE: 'Deduplicate and normalize entities (merge "IBM" and "IBM Corp")',
  ENHANCE_GRAPH: 'Add inferred relationships and enrich the knowledge graph',
  PERSIST: 'Save all nodes, edges, and metadata to the graph database',
  IMPORT_GRAPH: 'Import an existing graph structure from file',
  VALIDATE_SCHEMA: 'Validate extracted data against the graph schema',
};

function TypeBadge({ type }) {
  const mapped = _OLD_TYPE_MAP[type] || type;
  const t = CONTEXT_TYPES.find((ct) => ct.value === mapped) || CONTEXT_TYPES.find((ct) => ct.value === type) || CONTEXT_TYPES[0];
  const Icon = t.icon;
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium"
      style={{ background: `${t.color}15`, color: t.color }}
    >
      <Icon size={11} /> {t.label}
    </span>
  );
}

function SensitivityBadge({ sensitivity }) {
  const s = SENSITIVITY_LEVELS.find((sl) => sl.value === sensitivity) || SENSITIVITY_LEVELS[0];
  const Icon = s.icon;
  return (
    <span
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs"
      style={{ background: `${s.color}15`, color: s.color }}
    >
      <Icon size={10} /> {s.label}
    </span>
  );
}

/* ─── Pipeline Config Panel (inside expanded context card) ─── */

function PipelineConfigPanel({ context, onUpdate }) {
  const [editing, setEditing] = useState(false);
  const [loading, setLoading] = useState(false);
  const [templates, setTemplates] = useState([]);
  const [schemas, setSchemas] = useState([]);
  const [showRuns, setShowRuns] = useState(false);
  const [runs, setRuns] = useState([]);
  const [runsLoading, setRunsLoading] = useState(false);

  // Pipeline config state
  const pipeline = context.config?.pipeline || {};
  const filters = context.config?.filters || {};
  const schedule = context.config?.schedule || {};

  const [templateId, setTemplateId] = useState(pipeline.template_id || '');
  const [sourceType, setSourceType] = useState(pipeline.source_url ? 'url' : 'file');
  const [sourceUrl, setSourceUrl] = useState(pipeline.source_url || '');
  const [sourceText, setSourceText] = useState('');
  const [pipelineFiles, setPipelineFiles] = useState([]);
  const [maxPages, setMaxPages] = useState(pipeline.max_pages || 5);
  const [schemaId, setSchemaId] = useState(pipeline.schema_id || '');
  const [schemaMode, setSchemaMode] = useState(pipeline.schema_mode || 'schema_plus');
  const [triggerType, setTriggerType] = useState(schedule.trigger_type || 'one_time');
  const [intervalMinutes, setIntervalMinutes] = useState(schedule.interval_minutes || 60);
  const [kwInclude, setKwInclude] = useState((filters.keyword_include || []).join(', '));
  const [kwExclude, setKwExclude] = useState((filters.keyword_exclude || []).join(', '));
  const [semanticRules, setSemanticRules] = useState(filters.semantic_rules || []);
  const [semanticMode, setSemanticMode] = useState(filters.semantic_mode || 'or');

  useEffect(() => {
    if (editing) {
      api.get('/dashboard/ingest/pipeline-templates')
        .then(r => setTemplates(r.data.pipelines || []))
        .catch(() => {});
      api.get('/dashboard/contexts/schemas/defaults')
        .then(r => setSchemas(Array.isArray(r.data) ? r.data : r.data.schemas || []))
        .catch(() => {});
    }
  }, [editing]);


  const handleSave = async () => {
    setLoading(true);
    try {
      const updatedConfig = {
        ...context.config,
        pipeline: {
          template_id: templateId,
          source_type: sourceType,
          source_url: sourceType === 'url' ? sourceUrl.trim() : '',
          max_pages: Number(maxPages) || 5,
          schema_id: schemaId,
          schema_mode: schemaMode,
        },
        filters: {
          keyword_include: kwInclude.split(',').map(k => k.trim()).filter(Boolean),
          keyword_exclude: kwExclude.split(',').map(k => k.trim()).filter(Boolean),
          semantic_rules: semanticRules.filter(r => r.description.trim()),
          semantic_mode: semanticMode,
        },
        schedule: {
          trigger_type: triggerType,
          interval_minutes: Number(intervalMinutes) || 60,
          status: (templateId || sourceUrl.trim()) ? 'active' : 'draft',
          last_run_at: schedule.last_run_at || '',
          next_run_at: schedule.next_run_at || '',
          total_runs: schedule.total_runs || 0,
          total_documents: schedule.total_documents || 0,
          last_error: schedule.last_error || '',
        },
      };
      await api.patch(`/dashboard/contexts/${context.context_id}`, { config: updatedConfig });

      // If source is file and a file is selected, upload it immediately
      if (sourceType === 'file' && pipelineFiles.length > 0) {
        for (const file of pipelineFiles) {
          const fd = new FormData();
          fd.append('file', file);
          fd.append('pipeline_id', templateId);
          if (schemaId) fd.append('graph_schema', schemaId);
          await api.post(`/dashboard/contexts/${context.context_id}/ingest/file`, fd, {
            headers: { 'Content-Type': undefined },
            timeout: 300000,
          });
        }
        toast.success(`Pipeline saved + ${pipelineFiles.length} file(s) ingestion started`);
        setPipelineFiles([]);
      } else if (sourceType === 'text' && sourceText.trim()) {
        await api.post(`/dashboard/contexts/${context.context_id}/ingest`, {
          text: sourceText.trim(),
          pipeline: templateId,
          schema: schemaId,
        });
        toast.success('Pipeline saved + text ingestion started');
        setSourceText('');
      } else {
        toast.success('Pipeline configuration saved');
      }

      setEditing(false);
      if (onUpdate) onUpdate();
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to save pipeline config');
    } finally {
      setLoading(false);
    }
  };

  const inputCls = 'w-full px-2 py-1.5 rounded-lg text-xs outline-none';
  const inputStyle = { background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' };

  // Display mode — show summary
  if (!editing) {
    const hasPipeline = pipeline.template_id || pipeline.source_url;
    return (
      <div className="mt-3 pt-3" style={{ borderTop: '1px solid var(--neo-border)' }}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Rss size={12} style={{ color: 'var(--neo-blue)' }} />
            <span className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>
              {hasPipeline ? 'Pipeline' : 'No Pipeline Configured'}
            </span>
          </div>
          <button onClick={() => setEditing(true)}
            className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium"
            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}>
            <Settings size={10} /> {hasPipeline ? 'Edit' : 'Configure'}
          </button>
        </div>
        {hasPipeline && (
          <>
            {/* Source + template */}
            <div className="mt-2 flex items-center gap-2 text-xs" style={{ color: 'var(--neo-text)' }}>
              <Globe size={11} style={{ color: 'var(--neo-text-muted)', flexShrink: 0 }} />
              <a href={pipeline.source_url} target="_blank" rel="noopener noreferrer"
                className="hover:underline truncate" style={{ color: 'var(--neo-blue)' }}>
                {pipeline.source_url}
              </a>
              {pipeline.template_id && (
                <span className="px-1.5 py-0.5 rounded text-[10px] font-medium whitespace-nowrap"
                  style={{ background: 'rgba(59,130,246,0.1)', color: 'var(--neo-blue)' }}>
                  {pipeline.template_id.replace('builtin:', '')}
                </span>
              )}
            </div>

            {/* Status + stats */}
            <div className="flex items-center gap-2 mt-2 flex-wrap">
              <span className="text-[10px] px-1.5 py-0.5 rounded font-medium" style={{
                background: schedule.status === 'active' ? 'rgba(34,197,94,0.15)' :
                             schedule.status === 'completed' ? 'rgba(59,130,246,0.15)' :
                             schedule.status === 'paused' ? 'rgba(234,179,8,0.15)' : 'rgba(156,163,175,0.15)',
                color: schedule.status === 'active' ? '#22c55e' :
                       schedule.status === 'completed' ? 'var(--neo-blue)' :
                       schedule.status === 'paused' ? '#eab308' : '#9ca3af',
              }}>
                {schedule.status || 'draft'}
              </span>
              {schedule.trigger_type === 'scheduled' && (
                <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                  every {schedule.interval_minutes || 60}min
                </span>
              )}
              {schedule.total_runs > 0 && (
                <span className="text-[10px] font-medium" style={{ color: 'var(--neo-text-muted)' }}>
                  {schedule.total_runs} runs · {schedule.total_documents} docs
                </span>
              )}
              {pipeline.schema_id && (
                <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                  schema: {pipeline.schema_id}
                </span>
              )}
            </div>

            {/* Last run */}
            {schedule.last_run_at && (
              <div className="text-[10px] mt-1" style={{ color: 'var(--neo-text-muted)' }}>
                Last run: {new Date(schedule.last_run_at).toLocaleString()}
                {schedule.last_error && (
                  <span style={{ color: '#ef4444' }}> — {schedule.last_error.slice(0, 80)}</span>
                )}
              </div>
            )}

            {/* Actions */}
            <div className="flex items-center gap-1.5 mt-2">
              <button onClick={() => api.post(`/dashboard/contexts/${context.context_id}/pipeline/run`).then(() => { toast.success('Pipeline run started'); if (onUpdate) onUpdate(); }).catch(() => toast.error('Failed to start'))}
                className="flex items-center gap-1 px-2.5 py-1 rounded text-[10px] font-medium transition hover:opacity-90"
                style={{ background: 'var(--neo-blue)', color: '#fff' }}>
                <Play size={10} /> Run Now
              </button>
              {schedule.status === 'active' ? (
                <button onClick={() => api.post(`/dashboard/contexts/${context.context_id}/pipeline/pause`).then(() => { toast.success('Paused'); if (onUpdate) onUpdate(); })}
                  className="flex items-center gap-1 px-2 py-1 rounded text-[10px] transition hover:opacity-70"
                  style={{ color: 'var(--neo-text-muted)', border: '1px solid var(--neo-border)' }}>
                  <Pause size={10} /> Pause
                </button>
              ) : schedule.status === 'paused' && (
                <button onClick={() => api.post(`/dashboard/contexts/${context.context_id}/pipeline/resume`).then(() => { toast.success('Resumed'); if (onUpdate) onUpdate(); })}
                  className="flex items-center gap-1 px-2 py-1 rounded text-[10px] transition hover:opacity-70"
                  style={{ color: '#22c55e', border: '1px solid rgba(34,197,94,0.3)' }}>
                  <Play size={10} /> Resume
                </button>
              )}
              <button onClick={() => {
                  if (!showRuns) {
                    setRunsLoading(true);
                    api.get(`/dashboard/contexts/${context.context_id}/pipeline-runs`)
                      .then(r => setRuns(r.data.runs || []))
                      .catch(() => setRuns([]))
                      .finally(() => setRunsLoading(false));
                  }
                  setShowRuns(!showRuns);
                }}
                className="flex items-center gap-1 px-2 py-1 rounded text-[10px] transition hover:opacity-70"
                style={{ color: 'var(--neo-text-muted)', border: '1px solid var(--neo-border)' }}>
                <Clock size={10} /> {showRuns ? 'Hide' : 'History'}
              </button>
            </div>

            {/* Run history */}
            {showRuns && (
              <div className="mt-2 space-y-1">
                {runsLoading ? (
                  <div className="text-[10px] py-2 text-center" style={{ color: 'var(--neo-text-muted)' }}>Loading...</div>
                ) : runs.length === 0 ? (
                  <div className="text-[10px] py-2 text-center" style={{ color: 'var(--neo-text-muted)' }}>No runs yet</div>
                ) : runs.map((run, i) => (
                  <div key={i} className="flex items-center justify-between px-2 py-1.5 rounded"
                    style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)' }}>
                    <div className="flex items-center gap-3 text-[10px]">
                      <span style={{ color: 'var(--neo-text-muted)' }}>
                        {new Date(run.run_at).toLocaleString()}
                      </span>
                      <span style={{ color: 'var(--neo-text)' }}>
                        {run.pages_ingested || 0} pages
                      </span>
                      <span style={{ color: 'var(--neo-text)' }}>
                        {run.nodes_created || 0} nodes
                      </span>
                      <span style={{ color: 'var(--neo-text)' }}>
                        {run.edges_created || 0} edges
                      </span>
                      {run.entities > 0 && (
                        <span style={{ color: '#22c55e' }}>{run.entities} entities</span>
                      )}
                      {run.facts > 0 && (
                        <span style={{ color: 'var(--neo-blue)' }}>{run.facts} facts</span>
                      )}
                      {run.duration_ms > 0 && (
                        <span style={{ color: 'var(--neo-text-muted)' }}>
                          {run.duration_ms > 1000 ? `${(run.duration_ms/1000).toFixed(1)}s` : `${run.duration_ms}ms`}
                        </span>
                      )}
                    </div>
                    {run.run_id && (
                      <button onClick={() => {
                        if (!window.confirm(`Rollback this run? This will delete all ${run.nodes_created || 0} nodes created by this run.`)) return;
                        api.post(`/dashboard/contexts/${context.context_id}/pipeline-runs/${run.run_id}/rollback`)
                          .then(r => {
                            toast.success(`Rolled back: ${r.data.deleted || 0} nodes deleted`);
                            if (onUpdate) onUpdate();
                            // Refresh runs
                            api.get(`/dashboard/contexts/${context.context_id}/pipeline-runs`)
                              .then(r2 => setRuns(r2.data.runs || []));
                          })
                          .catch(() => toast.error('Rollback failed'));
                      }}
                        className="flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] transition hover:opacity-70"
                        style={{ color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)' }}
                        title="Rollback this run — delete all nodes it created">
                        <Trash2 size={9} /> Rollback
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    );
  }

  // Edit mode — full form
  return (
    <div className="mt-3 pt-3 space-y-3" style={{ borderTop: '1px solid var(--neo-blue)' }}>
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium" style={{ color: 'var(--neo-blue)' }}>
          <Rss size={12} className="inline mr-1" /> Configure Pipeline
        </span>
        <div className="flex items-center gap-1">
          <button onClick={() => setEditing(false)}
            className="px-2 py-1 rounded text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>Cancel</button>
          <button onClick={handleSave} disabled={loading}
            className="px-2 py-1 rounded text-[10px] font-medium"
            style={{ background: 'var(--neo-green)', color: '#fff' }}>
            {loading ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>

      {/* Pipeline Template */}
      <div>
        <label className="text-[10px] font-medium block mb-1" style={{ color: 'var(--neo-text-muted)' }}>Pipeline Template</label>
        <select value={templateId} onChange={e => setTemplateId(e.target.value)} className={inputCls} style={inputStyle}>
          <option value="">— Select —</option>
          {templates.map(t => <option key={t.id} value={t.id}>{t.name} — {t.description?.slice(0, 60)}</option>)}
        </select>
        {/* Show stages for selected template */}
        {templateId && (() => {
          const tpl = templates.find(t => t.id === templateId);
          if (!tpl?.stages?.length) return null;
          return (
            <div className="mt-2">
              <div className="flex items-center gap-1 flex-wrap">
                {tpl.stages.map((s, i) => (
                  <React.Fragment key={s.name}>
                    <span className="px-2 py-0.5 rounded text-[10px] font-medium"
                      style={{ background: 'rgba(59,130,246,0.1)', color: 'var(--neo-blue)', border: '1px solid rgba(59,130,246,0.2)' }}>
                      {s.type}
                    </span>
                    {i < tpl.stages.length - 1 && (
                      <ArrowRight size={10} style={{ color: 'var(--neo-text-muted)' }} />
                    )}
                  </React.Fragment>
                ))}
              </div>
              {tpl.explanation && (
                <p className="text-[10px] mt-1.5 leading-relaxed" style={{ color: 'var(--neo-text-muted)' }}>
                  {tpl.explanation.split('\n')[0].replace(/\*\*/g, '').slice(0, 150)}
                </p>
              )}
            </div>
          );
        })()}
      </div>

      {/* Source Type + Input */}
      <div>
        <label className="text-[10px] font-medium block mb-1" style={{ color: 'var(--neo-text-muted)' }}>Source</label>
        <div className="flex gap-1 mb-2">
          {[
            { key: 'url', label: 'URL', icon: '🔗' },
            { key: 'file', label: 'Upload File', icon: '📄' },
            { key: 'text', label: 'Paste Text', icon: '📝' },
          ].map(s => (
            <button key={s.key}
              onClick={() => setSourceType(s.key)}
              className="flex items-center gap-1 px-2.5 py-1 rounded text-[10px] font-medium transition"
              style={{
                background: sourceType === s.key ? 'var(--neo-blue)' : 'var(--neo-bg)',
                color: sourceType === s.key ? '#fff' : 'var(--neo-text-muted)',
                border: `1px solid ${sourceType === s.key ? 'var(--neo-blue)' : 'var(--neo-border)'}`,
              }}>
              {s.icon} {s.label}
            </button>
          ))}
        </div>

        {sourceType === 'url' && (
          <div className="flex gap-2">
            <div className="flex-1">
              <input value={sourceUrl} onChange={e => setSourceUrl(e.target.value)} placeholder="https://..."
                className={inputCls} style={inputStyle} />
            </div>
            <div className="w-20">
              <label className="text-[10px] font-medium block mb-1" style={{ color: 'var(--neo-text-muted)' }}>Pages</label>
              <input type="number" value={maxPages} onChange={e => setMaxPages(e.target.value)} min={1} max={50}
                className={inputCls} style={inputStyle} />
            </div>
          </div>
        )}

        {sourceType === 'file' && (
          <div className="border-2 border-dashed rounded-lg p-4 text-center cursor-pointer transition hover:border-blue-400"
            style={{ borderColor: 'var(--neo-border)', background: 'var(--neo-bg)' }}
            onClick={() => document.getElementById('pipeline-file-input')?.click()}
            onDragOver={e => { e.preventDefault(); e.currentTarget.style.borderColor = '#3b82f6'; }}
            onDragLeave={e => { e.currentTarget.style.borderColor = ''; }}
            onDrop={e => {
              e.preventDefault();
              e.currentTarget.style.borderColor = '';
              const files = Array.from(e.dataTransfer.files || []);
              if (files.length) setPipelineFiles(prev => [...prev, ...files]);
            }}>
            {pipelineFiles.length > 0 ? (
              <div className="space-y-1">
                {pipelineFiles.map((f, i) => (
                  <div key={i} className="flex items-center justify-center gap-2">
                    <span className="text-sm" style={{ color: 'var(--neo-text)' }}>{f.name}</span>
                    <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                      ({(f.size / 1024).toFixed(1)} KB)
                    </span>
                    <button onClick={e => { e.stopPropagation(); setPipelineFiles(prev => prev.filter((_, j) => j !== i)); }}
                      style={{ color: 'var(--neo-text-muted)' }}>✕</button>
                  </div>
                ))}
                <p className="text-[10px] mt-1" style={{ color: 'var(--neo-text-dim)' }}>
                  Click to add more files
                </p>
              </div>
            ) : (
              <div>
                <p className="text-xs mb-1" style={{ color: 'var(--neo-text-muted)' }}>
                  Drop files here or click to browse
                </p>
                <p className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
                  PDF, DOCX, CSV, JSON, TXT, ZIP (ChatGPT/Claude exports) — multiple files supported
                </p>
              </div>
            )}
            <input id="pipeline-file-input" type="file" className="hidden" multiple
              accept=".pdf,.docx,.doc,.txt,.md,.csv,.xlsx,.json,.zip,.html,.xml"
              onChange={e => { const files = Array.from(e.target.files || []); if (files.length) setPipelineFiles(prev => [...prev, ...files]); e.target.value = ''; }} />
          </div>
        )}

        {sourceType === 'text' && (
          <textarea value={sourceText} onChange={e => setSourceText(e.target.value)}
            placeholder="Paste content here..."
            rows={4} className={inputCls} style={{ ...inputStyle, resize: 'vertical' }} />
        )}
      </div>

      {/* Schedule */}
      <div className="flex gap-2">
        <div className="flex-1">
          <label className="text-[10px] font-medium block mb-1" style={{ color: 'var(--neo-text-muted)' }}>Trigger</label>
          <select value={triggerType} onChange={e => setTriggerType(e.target.value)} className={inputCls} style={inputStyle}>
            <option value="one_time">One-time</option>
            <option value="scheduled">Scheduled</option>
            <option value="webhook">Webhook</option>
            <option value="manual">Manual</option>
          </select>
        </div>
        {triggerType === 'scheduled' && (
          <div className="w-24">
            <label className="text-[10px] font-medium block mb-1" style={{ color: 'var(--neo-text-muted)' }}>Interval (min)</label>
            <input type="number" value={intervalMinutes} onChange={e => setIntervalMinutes(e.target.value)} min={5}
              className={inputCls} style={inputStyle} />
          </div>
        )}
      </div>

      {/* Keywords */}
      <div>
        <label className="text-[10px] font-medium block mb-1" style={{ color: 'var(--neo-text-muted)' }}>Include Keywords (comma-separated)</label>
        <input value={kwInclude} onChange={e => setKwInclude(e.target.value)} placeholder="politics, economy, policy"
          className={inputCls} style={inputStyle} />
      </div>
      <div>
        <label className="text-[10px] font-medium block mb-1" style={{ color: 'var(--neo-text-muted)' }}>Exclude Keywords (comma-separated)</label>
        <input value={kwExclude} onChange={e => setKwExclude(e.target.value)} placeholder="cricket, bollywood"
          className={inputCls} style={inputStyle} />
      </div>

      {/* Semantic Rules */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-[10px] font-medium" style={{ color: 'var(--neo-text-muted)' }}>
            Semantic Filters ({semanticRules.length})
          </label>
          <div className="flex items-center gap-1">
            <select value={semanticMode} onChange={e => setSemanticMode(e.target.value)}
              className="px-1.5 py-0.5 rounded text-[10px]" style={inputStyle}>
              <option value="or">ANY (OR)</option>
              <option value="and">ALL (AND)</option>
            </select>
            <button onClick={() => setSemanticRules([...semanticRules, {description: '', threshold: 0.6}])}
              className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: 'var(--neo-blue)', color: '#fff' }}>
              + Add
            </button>
          </div>
        </div>
        {semanticRules.map((rule, i) => (
          <div key={i} className="flex gap-1 mt-1">
            <input value={rule.description}
              onChange={e => { const u = [...semanticRules]; u[i] = {...u[i], description: e.target.value}; setSemanticRules(u); }}
              placeholder="Describe topic to include..."
              className="flex-1 px-2 py-1 rounded-lg text-[11px] outline-none" style={inputStyle} />
            <input type="number" value={rule.threshold} min={0} max={1} step={0.05}
              onChange={e => { const u = [...semanticRules]; u[i] = {...u[i], threshold: parseFloat(e.target.value) || 0.6}; setSemanticRules(u); }}
              className="w-14 px-1 py-1 rounded-lg text-[11px] outline-none text-center" style={inputStyle} title="Threshold" />
            <button onClick={() => setSemanticRules(semanticRules.filter((_, j) => j !== i))}
              className="p-1 rounded hover:opacity-70" style={{ color: 'var(--neo-text-muted)' }}>
              <X size={11} />
            </button>
          </div>
        ))}
      </div>

      {/* Schema */}
      <div className="flex gap-2">
        <div className="flex-1">
          <label className="text-[10px] font-medium block mb-1" style={{ color: 'var(--neo-text-muted)' }}>Extraction Schema</label>
          <select value={schemaId} onChange={e => setSchemaId(e.target.value)} className={inputCls} style={inputStyle}>
            <option value="">Auto-detect from tags</option>
            {schemas.map(s => (
              <option key={s.name || s} value={s.name || s}>
                {s.name || s}{s.node_count ? ` (${s.node_count} types)` : ''}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-[10px] font-medium block mb-1" style={{ color: 'var(--neo-text-muted)' }}>Mode</label>
          <select value={schemaMode} onChange={e => setSchemaMode(e.target.value)} className={inputCls} style={inputStyle}>
            <option value="schema_plus">Schema+</option>
            <option value="strict">Strict</option>
          </select>
        </div>
      </div>
    </div>
  );
}

/* ─── Ingest Panel (inside expanded context) ─── */

/* ─── Timeline Panel (Time Travel) ─── */

function TimelinePanel({ entities, facts }) {
  const [asOfDate, setAsOfDate] = useState('');
  const [compareDate, setCompareDate] = useState('');
  const [mode, setMode] = useState('chart'); // chart | timeline | snapshot | diff

  // Build unified timeline from entities + facts
  const allItems = [
    ...entities.map(e => ({
      type: 'entity',
      name: (e.properties || {}).name || '',
      sentiment: (e.properties || {})._sentiment || '',
      date: (e.properties || {})._event_date || (e.properties || {})._created_at?.substring(0, 10) || '',
      timestamp: (e.properties || {})._created_at || '',
      freshness: (e.properties || {})._freshness || '',
      source: (e.properties || {})._source_title || '',
      confidence: (e.properties || {}).confidence || 0,
      props: e.properties || {},
    })),
    ...facts.map(f => ({
      type: 'fact',
      name: (f.properties || {}).statement || (f.properties || {}).name || '',
      sentiment: (f.properties || {})._sentiment || '',
      date: (f.properties || {})._event_date || (f.properties || {})._created_at?.substring(0, 10) || '',
      timestamp: (f.properties || {})._created_at || '',
      freshness: (f.properties || {})._freshness || '',
      source: (f.properties || {})._source_title || '',
      confidence: (f.properties || {}).confidence || 0,
      props: f.properties || {},
    })),
  ].filter(item => item.date).sort((a, b) => (b.date || '').localeCompare(a.date || ''));

  // Group by date
  const byDate = {};
  allItems.forEach(item => {
    const d = item.date || 'unknown';
    if (!byDate[d]) byDate[d] = [];
    byDate[d].push(item);
  });

  // Snapshot: filter to items as of a specific date
  const snapshotItems = asOfDate
    ? allItems.filter(item => item.date <= asOfDate)
    : allItems;

  // Diff: items between two dates
  const diffBefore = compareDate ? allItems.filter(item => item.date <= compareDate) : [];
  const diffAfter = asOfDate ? allItems.filter(item => item.date <= asOfDate) : allItems;
  const diffNew = diffAfter.filter(a => !diffBefore.some(b => b.name === a.name && b.type === a.type));
  const diffRemoved = diffBefore.filter(b => !diffAfter.some(a => a.name === b.name && a.type === b.type));

  // Sentiment shift detection
  const sentimentShifts = [];
  if (compareDate && asOfDate) {
    const beforeSent = {};
    diffBefore.forEach(i => { if (i.type === 'entity' && i.sentiment) beforeSent[i.name] = i.sentiment; });
    diffAfter.forEach(i => {
      if (i.type === 'entity' && i.sentiment && beforeSent[i.name] && beforeSent[i.name] !== i.sentiment) {
        sentimentShifts.push({ name: i.name, from: beforeSent[i.name], to: i.sentiment });
      }
    });
  }

  const sentColors = { positive: '#10b981', negative: '#ef4444', mixed: '#f59e0b', neutral: '#6b7280' };

  return (
    <div className="space-y-3">
      {/* Mode selector + date pickers */}
      <div className="flex items-center gap-3 p-3 rounded-lg" style={{ background: 'var(--neo-bg-secondary)' }}>
        <div className="flex gap-1">
          {[
            { id: 'chart', label: 'Time Series' },
            { id: 'timeline', label: 'Timeline' },
            { id: 'snapshot', label: 'Snapshot' },
            { id: 'diff', label: 'Before / After' },
          ].map(m => (
            <button key={m.id} onClick={() => setMode(m.id)} className="px-2.5 py-1 rounded text-[10px] font-medium"
              style={{ background: mode === m.id ? '#ec4899' : 'var(--neo-bg)', color: mode === m.id ? '#fff' : 'var(--neo-text-muted)' }}>
              {m.label}
            </button>
          ))}
        </div>
        {(mode === 'snapshot' || mode === 'diff') && (
          <div className="flex items-center gap-2 ml-auto">
            {mode === 'diff' && (
              <>
                <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>Before:</span>
                <input type="date" value={compareDate} onChange={e => setCompareDate(e.target.value)}
                  className="px-2 py-1 rounded text-[10px]" style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
              </>
            )}
            <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>{mode === 'diff' ? 'After:' : 'As of:'}</span>
            <input type="date" value={asOfDate} onChange={e => setAsOfDate(e.target.value)}
              className="px-2 py-1 rounded text-[10px]" style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
          </div>
        )}
      </div>

      {/* Timeline mode */}
      {/* Time Series Chart mode */}
      {mode === 'chart' && (() => {
        // Build time-series data: group by date+hour, count entities/facts, average sentiment
        const tsMap = {};
        allItems.forEach(item => {
          const ts = item.timestamp || item.date;
          if (!ts) return;
          // Group by hour
          const key = ts.length >= 13 ? ts.substring(0, 13) : ts.substring(0, 10);
          if (!tsMap[key]) tsMap[key] = { time: key, entities: 0, facts: 0, positive: 0, negative: 0, neutral: 0, mixed: 0, total: 0, sentScore: 0 };
          tsMap[key].total++;
          if (item.type === 'entity') tsMap[key].entities++;
          if (item.type === 'fact') tsMap[key].facts++;
          if (item.sentiment === 'positive') { tsMap[key].positive++; tsMap[key].sentScore += 1; }
          else if (item.sentiment === 'negative') { tsMap[key].negative++; tsMap[key].sentScore -= 1; }
          else if (item.sentiment === 'mixed') { tsMap[key].mixed++; }
          else { tsMap[key].neutral++; }
        });

        const tsData = Object.values(tsMap).sort((a, b) => a.time.localeCompare(b.time)).map(d => ({
          ...d,
          displayTime: (() => {
            try {
              const dt = new Date(d.time.length === 10 ? d.time + 'T12:00:00' : d.time + ':00:00');
              if (isNaN(dt.getTime())) return d.time;
              const mon = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][dt.getMonth()];
              return `${mon} ${dt.getDate()}, ${dt.getHours()}:${String(dt.getMinutes()).padStart(2,'0')}`;
            } catch { return d.time; }
          })(),
          sentimentAvg: d.total > 0 ? d.sentScore / d.total : 0,
        }));

        if (!tsData.length) return <p className="text-xs text-center py-8" style={{ color: 'var(--neo-text-muted)' }}>No timestamped data to chart.</p>;

        return (
          <div className="space-y-3">
            {/* Sentiment + Activity chart */}
            <div style={{ background: '#0a0a1a', borderRadius: 8, padding: '12px 8px', border: '1px solid var(--neo-border)' }}>
              <p className="text-[10px] font-semibold uppercase mb-2 ml-2" style={{ color: '#9ca3af' }}>Sentiment & Activity Over Time</p>
              <ResponsiveContainer width="100%" height={200}>
                <ComposedChart data={tsData} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                  <XAxis dataKey="displayTime" tick={{ fill: '#6b7280', fontSize: 9 }} stroke="#374151" angle={-30} textAnchor="end" height={50} />
                  <YAxis yAxisId="count" tick={{ fill: '#6b7280', fontSize: 10 }} stroke="#374151" />
                  <YAxis yAxisId="sent" orientation="right" domain={[-1, 1]} tick={{ fill: '#6b7280', fontSize: 10 }} stroke="#374151" />
                  <Tooltip contentStyle={{ background: '#1f2937', border: '1px solid #374151', color: '#d1d5db', fontSize: 11 }} />
                  <Bar yAxisId="count" dataKey="positive" stackId="sent" fill="#10b981" name="Positive" barSize={12} />
                  <Bar yAxisId="count" dataKey="neutral" stackId="sent" fill="#6b7280" name="Neutral" barSize={12} />
                  <Bar yAxisId="count" dataKey="mixed" stackId="sent" fill="#f59e0b" name="Mixed" barSize={12} />
                  <Bar yAxisId="count" dataKey="negative" stackId="sent" fill="#ef4444" name="Negative" barSize={12} />
                  <Line yAxisId="sent" type="monotone" dataKey="sentimentAvg" stroke="#ec4899" strokeWidth={2} dot={{ fill: '#ec4899', r: 3 }} name="Sentiment Score" />
                </ComposedChart>
              </ResponsiveContainer>
            </div>

            {/* Entity + Fact count chart */}
            <div style={{ background: '#0a0a1a', borderRadius: 8, padding: '12px 8px', border: '1px solid var(--neo-border)' }}>
              <p className="text-[10px] font-semibold uppercase mb-2 ml-2" style={{ color: '#9ca3af' }}>Entities & Facts Over Time</p>
              <ResponsiveContainer width="100%" height={150}>
                <ComposedChart data={tsData} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                  <XAxis dataKey="displayTime" tick={{ fill: '#6b7280', fontSize: 9 }} stroke="#374151" angle={-30} textAnchor="end" height={50} />
                  <YAxis tick={{ fill: '#6b7280', fontSize: 10 }} stroke="#374151" />
                  <Tooltip contentStyle={{ background: '#1f2937', border: '1px solid #374151', color: '#d1d5db', fontSize: 11 }} />
                  <Area type="monotone" dataKey="entities" fill="#3b82f620" stroke="#3b82f6" name="Entities" />
                  <Area type="monotone" dataKey="facts" fill="#8b5cf620" stroke="#8b5cf6" name="Facts" />
                </ComposedChart>
              </ResponsiveContainer>
            </div>

            {/* Summary */}
            <div className="flex gap-3 text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
              <span>{tsData.length} time points</span>
              <span>{allItems.filter(i => i.type === 'entity').length} entities</span>
              <span>{allItems.filter(i => i.type === 'fact').length} facts</span>
              <span>Range: {tsData[0]?.displayTime} → {tsData[tsData.length-1]?.displayTime}</span>
            </div>
          </div>
        );
      })()}

      {mode === 'timeline' && (
        <div className="relative pl-6">
          {/* Vertical line */}
          <div className="absolute left-3 top-0 bottom-0 w-0.5 rounded" style={{ background: 'var(--neo-border)' }} />

          {Object.entries(byDate).slice(0, 15).map(([date, items]) => (
            <div key={date} className="mb-4 relative">
              {/* Date dot */}
              <div className="absolute -left-3 w-3 h-3 rounded-full border-2" style={{ background: 'var(--neo-bg)', borderColor: '#ec4899', top: '2px' }} />
              <div className="text-[10px] font-semibold mb-1" style={{ color: '#ec4899' }}>{date}</div>
              <div className="space-y-1">
                {items.map((item, j) => (
                  <div key={j} className="flex items-center gap-2 text-xs p-1.5 rounded" style={{ background: 'var(--neo-bg)' }}>
                    <span className="text-[9px] px-1 rounded" style={{
                      background: item.type === 'entity' ? '#3b82f620' : '#8b5cf620',
                      color: item.type === 'entity' ? '#3b82f6' : '#8b5cf6',
                    }}>{item.type === 'entity' ? 'ENT' : 'FACT'}</span>
                    <span className="flex-1 truncate" style={{ color: 'var(--neo-text)' }}>{item.name}</span>
                    {item.sentiment && <span className="w-2 h-2 rounded-full" style={{ background: sentColors[item.sentiment] }} title={item.sentiment} />}
                    {item.source && <span className="text-[9px] truncate max-w-[80px]" style={{ color: 'var(--neo-text-muted)' }}>{item.source}</span>}
                  </div>
                ))}
              </div>
            </div>
          ))}
          {Object.keys(byDate).length === 0 && <p className="text-xs py-4" style={{ color: 'var(--neo-text-muted)' }}>No dated items found. Ingest content with dates to see the timeline.</p>}
        </div>
      )}

      {/* Snapshot mode */}
      {mode === 'snapshot' && (
        <div>
          <div className="flex items-center gap-3 mb-3">
            <span className="text-xs font-semibold" style={{ color: 'var(--neo-text)' }}>
              {asOfDate ? `Context as of ${asOfDate}` : 'Select a date to see a snapshot'}
            </span>
            <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
              {snapshotItems.length} items | {snapshotItems.filter(i => i.type === 'entity').length} entities, {snapshotItems.filter(i => i.type === 'fact').length} facts
            </span>
          </div>
          {asOfDate && (
            <div className="space-y-1 max-h-80 overflow-y-auto">
              {snapshotItems.slice(0, 30).map((item, i) => (
                <div key={i} className="flex items-center gap-2 text-xs p-1.5 rounded" style={{ background: 'var(--neo-bg)' }}>
                  <span className="text-[9px] px-1 rounded" style={{
                    background: item.type === 'entity' ? '#3b82f620' : '#8b5cf620',
                    color: item.type === 'entity' ? '#3b82f6' : '#8b5cf6',
                  }}>{item.type === 'entity' ? 'ENT' : 'FACT'}</span>
                  <span className="flex-1" style={{ color: 'var(--neo-text)' }}>{item.name}</span>
                  {item.sentiment && <span className="px-1 rounded text-[9px]" style={{ background: `${sentColors[item.sentiment]}20`, color: sentColors[item.sentiment] }}>{item.sentiment}</span>}
                  <span className="text-[9px]" style={{ color: 'var(--neo-text-muted)' }}>{item.date}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Diff mode — Before / After */}
      {mode === 'diff' && (
        <div>
          {compareDate && asOfDate ? (
            <div className="space-y-3">
              {/* Summary */}
              <div className="grid grid-cols-4 gap-2">
                <div className="p-2.5 rounded-lg text-center" style={{ background: '#10b98110', border: '1px solid #10b98130' }}>
                  <p className="text-[9px] uppercase" style={{ color: '#10b981' }}>New</p>
                  <p className="text-lg font-bold" style={{ color: '#10b981' }}>+{diffNew.length}</p>
                </div>
                <div className="p-2.5 rounded-lg text-center" style={{ background: '#ef444410', border: '1px solid #ef444430' }}>
                  <p className="text-[9px] uppercase" style={{ color: '#ef4444' }}>Removed</p>
                  <p className="text-lg font-bold" style={{ color: '#ef4444' }}>-{diffRemoved.length}</p>
                </div>
                <div className="p-2.5 rounded-lg text-center" style={{ background: '#f59e0b10', border: '1px solid #f59e0b30' }}>
                  <p className="text-[9px] uppercase" style={{ color: '#f59e0b' }}>Sentiment Shifts</p>
                  <p className="text-lg font-bold" style={{ color: '#f59e0b' }}>{sentimentShifts.length}</p>
                </div>
                <div className="p-2.5 rounded-lg text-center" style={{ background: '#3b82f610', border: '1px solid #3b82f630' }}>
                  <p className="text-[9px] uppercase" style={{ color: '#3b82f6' }}>After Total</p>
                  <p className="text-lg font-bold" style={{ color: '#3b82f6' }}>{diffAfter.length}</p>
                </div>
              </div>

              {/* Sentiment shifts */}
              {sentimentShifts.length > 0 && (
                <div className="p-3 rounded-lg" style={{ background: '#f59e0b10', border: '1px solid #f59e0b30' }}>
                  <h3 className="text-[10px] font-bold uppercase mb-2" style={{ color: '#f59e0b' }}>Sentiment Shifts</h3>
                  {sentimentShifts.map((shift, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs py-1">
                      <span style={{ color: 'var(--neo-text)' }}>{shift.name}</span>
                      <span className="px-1 rounded" style={{ background: `${sentColors[shift.from]}20`, color: sentColors[shift.from] }}>{shift.from}</span>
                      <span style={{ color: 'var(--neo-text-muted)' }}>→</span>
                      <span className="px-1 rounded" style={{ background: `${sentColors[shift.to]}20`, color: sentColors[shift.to] }}>{shift.to}</span>
                    </div>
                  ))}
                </div>
              )}

              {/* New items */}
              {diffNew.length > 0 && (
                <div className="p-3 rounded-lg" style={{ background: '#10b98110', border: '1px solid #10b98130' }}>
                  <h3 className="text-[10px] font-bold uppercase mb-2" style={{ color: '#10b981' }}>New (appeared after {compareDate})</h3>
                  <div className="space-y-1 max-h-40 overflow-y-auto">
                    {diffNew.slice(0, 15).map((item, i) => (
                      <div key={i} className="flex items-center gap-2 text-xs">
                        <span className="text-[9px] px-1 rounded" style={{ background: item.type === 'entity' ? '#3b82f620' : '#8b5cf620', color: item.type === 'entity' ? '#3b82f6' : '#8b5cf6' }}>
                          {item.type === 'entity' ? 'ENT' : 'FACT'}
                        </span>
                        <span style={{ color: 'var(--neo-text)' }}>{item.name.substring(0, 60)}</span>
                        {item.sentiment && <span className="w-2 h-2 rounded-full" style={{ background: sentColors[item.sentiment] }} />}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <p className="text-xs text-center py-4" style={{ color: 'var(--neo-text-muted)' }}>
              Select "Before" and "After" dates above to see what changed.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/* ─── Expandable Entity List ─── */

function ExpandableEntityList({ entities, graphName }) {
  const [expanded, setExpanded] = useState(null);

  const sentColors = { positive: '#10b981', negative: '#ef4444', mixed: '#f59e0b', neutral: '#6b7280' };
  const freshColors = { fresh: '#10b981', recent: '#3b82f6', aging: '#f59e0b', stale: '#ef4444', historical: '#6b7280' };
  const impColors = { high: '#ef4444', medium: '#f59e0b', low: '#10b981' };

  // Properties to hide in expanded view (shown in main row already or internal)
  const hideProps = new Set(['name', '_sentiment', '_sentiment_confidence', '_impact', '_freshness',
    '_staleness_days', '_event_date', '_amplifier_relevance', 'confidence', '_source_url', '_source_title',
    '_published_at', '_source_name', '_date_source', '_ingested_at', '_created_at', '_updated_at',
    '_origin', '_region', '_verified', '_embedded', 'valid_from', 'valid_to', 'version', 'status']);

  return (
    <div className="space-y-1">
      {entities.slice(0, 30).map((e, i) => {
        const p = e.properties || {};
        const isExpanded = expanded === i;
        const sent = p._sentiment;
        const conf = p.confidence || 0;
        const geo = p._geography;
        const domain = p._domain;

        return (
          <div key={i}>
            {/* Main row — clickable */}
            <div className="flex items-center gap-2 px-3 py-2 rounded-t cursor-pointer hover:opacity-90 transition-opacity"
              onClick={() => setExpanded(isExpanded ? null : i)}
              style={{ background: isExpanded ? 'var(--neo-bg)' : 'var(--neo-bg-secondary)', border: `1px solid ${isExpanded ? 'var(--neo-blue)' : 'var(--neo-border)'}` }}>
              <ChevronRight size={12} style={{ color: 'var(--neo-text-muted)', transform: isExpanded ? 'rotate(90deg)' : '', transition: 'transform 0.15s' }} />
              <span className="font-medium text-xs flex-1" style={{ color: 'var(--neo-text)' }}>{p.name || e.id?.substring(0, 15)}</span>
              {sent && <span className="px-1.5 py-0.5 rounded text-[10px] font-medium" style={{ background: `${sentColors[sent]}20`, color: sentColors[sent] }}>{sent}</span>}
              {p._impact && <span className="px-1.5 py-0.5 rounded text-[10px]" style={{ background: `${impColors[p._impact]}20`, color: impColors[p._impact] }}>{p._impact}</span>}
              {p._freshness && p._freshness !== 'unknown' && <span className="text-[10px]" style={{ color: freshColors[p._freshness] }}>{p._freshness}</span>}
              <div className="flex items-center gap-1">
                <div className="w-10 h-1.5 rounded-full" style={{ background: 'var(--neo-bg)' }}>
                  <div className="h-full rounded-full" style={{ width: `${conf * 100}%`, background: conf > 0.7 ? '#10b981' : '#f59e0b' }} />
                </div>
                <span className="text-[9px] font-mono" style={{ color: 'var(--neo-text-muted)' }}>{(conf * 100).toFixed(0)}%</span>
              </div>
            </div>

            {/* Expanded detail — lazy loaded on click */}
            {isExpanded && (
              <div className="px-4 py-3 rounded-b text-xs space-y-3" style={{ background: 'var(--neo-bg)', borderLeft: `1px solid var(--neo-blue)`, borderRight: `1px solid var(--neo-blue)`, borderBottom: `1px solid var(--neo-blue)` }}>
                {/* Key metrics row */}
                <div className="grid grid-cols-4 gap-2">
                  <div>
                    <span className="text-[9px] uppercase block" style={{ color: 'var(--neo-text-muted)' }}>Sentiment</span>
                    <span style={{ color: sentColors[sent] || '#6b7280' }}>{sent || 'unknown'} ({((p._sentiment_confidence || 0) * 100).toFixed(0)}%)</span>
                  </div>
                  <div>
                    <span className="text-[9px] uppercase block" style={{ color: 'var(--neo-text-muted)' }}>Relevance</span>
                    <span style={{ color: (p._amplifier_relevance || 0) > 0.6 ? '#10b981' : '#f59e0b' }}>{((p._amplifier_relevance || 0) * 100).toFixed(0)}%</span>
                  </div>
                  <div>
                    <span className="text-[9px] uppercase block" style={{ color: 'var(--neo-text-muted)' }}>Event Date</span>
                    <span style={{ color: 'var(--neo-text)' }}>{p._event_date || 'unknown'}</span>
                  </div>
                  <div>
                    <span className="text-[9px] uppercase block" style={{ color: 'var(--neo-text-muted)' }}>Staleness</span>
                    <span style={{ color: 'var(--neo-text)' }}>{p._staleness_days != null ? `${p._staleness_days} days` : 'unknown'}</span>
                  </div>
                </div>

                {/* Source */}
                <div className="p-2 rounded" style={{ background: 'var(--neo-bg-secondary)' }}>
                  <span className="text-[9px] uppercase font-semibold block mb-1" style={{ color: 'var(--neo-text-muted)' }}>Source</span>
                  {p._source_url ? (
                    <a href={p._source_url} target="_blank" rel="noopener noreferrer" className="text-[11px] underline" style={{ color: 'var(--neo-blue)' }}>
                      {p._source_title || p._source_url}
                    </a>
                  ) : (
                    <span style={{ color: 'var(--neo-text)' }}>{p._source_title || p.created_by || 'Direct ingestion'}</span>
                  )}
                  {p._published_at && <span className="block text-[10px] mt-0.5" style={{ color: 'var(--neo-text-muted)' }}>Published: {p._published_at}</span>}
                  {p._source_name && <span className="block text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>Source: {p._source_name}</span>}
                  {p._ingested_at && <span className="block text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>Ingested: {new Date(p._ingested_at).toLocaleString()}</span>}
                </div>

                {/* Tags */}
                <div className="flex gap-2 flex-wrap">
                  {geo && Array.isArray(geo) && geo.map(g => (
                    <span key={g} className="text-[10px] px-2 py-0.5 rounded-full" style={{ background: '#8b5cf620', color: '#8b5cf6' }}>{g}</span>
                  ))}
                  {domain && Array.isArray(domain) && domain.map(d => (
                    <span key={d} className="text-[10px] px-2 py-0.5 rounded-full" style={{ background: '#3b82f620', color: '#3b82f6' }}>{d}</span>
                  ))}
                  {p.entity_type && <span className="text-[10px] px-2 py-0.5 rounded-full" style={{ background: '#f59e0b20', color: '#f59e0b' }}>{p.entity_type}</span>}
                </div>

                {/* All other properties */}
                <div>
                  <span className="text-[9px] uppercase font-semibold block mb-1" style={{ color: 'var(--neo-text-muted)' }}>All Properties</span>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
                    {Object.entries(p).filter(([k]) => !hideProps.has(k) && !k.startsWith('_')).map(([k, v]) => (
                      <div key={k} className="flex gap-1">
                        <span className="font-mono text-[10px]" style={{ color: '#ec4899' }}>{k}:</span>
                        <span className="text-[10px] truncate" style={{ color: 'var(--neo-text)' }}>{typeof v === 'object' ? JSON.stringify(v) : String(v).substring(0, 50)}</span>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="text-[9px] font-mono" style={{ color: 'var(--neo-text-muted)' }}>ID: {e.id} | Label: {e.label}</div>
              </div>
            )}
          </div>
        );
      })}
      {entities.length === 0 && <p className="text-xs text-center py-4" style={{ color: 'var(--neo-text-muted)' }}>No entities extracted yet</p>}
    </div>
  );
}

/* ─── Expandable Fact List ─── */

function ExpandableFactList({ facts, graphName }) {
  const [expanded, setExpanded] = useState(null);
  const sentColors = { positive: '#10b981', negative: '#ef4444', mixed: '#f59e0b' };

  return (
    <div className="space-y-1">
      {facts.map((f, i) => {
        const p = f.properties || {};
        const sent = p._sentiment;
        const isExpanded = expanded === i;

        return (
          <div key={i}>
            <div className="flex items-start gap-2 px-3 py-2 rounded-t cursor-pointer hover:opacity-90 transition-opacity"
              onClick={() => setExpanded(isExpanded ? null : i)}
              style={{ background: isExpanded ? 'var(--neo-bg)' : 'var(--neo-bg-secondary)',
                       borderLeft: sent ? `3px solid ${sentColors[sent] || '#6b7280'}` : '3px solid var(--neo-border)',
                       border: `1px solid ${isExpanded ? 'var(--neo-blue)' : 'var(--neo-border)'}` }}>
              <ChevronRight size={12} className="mt-0.5 flex-shrink-0" style={{ color: 'var(--neo-text-muted)', transform: isExpanded ? 'rotate(90deg)' : '', transition: 'transform 0.15s' }} />
              <p className="text-xs flex-1" style={{ color: 'var(--neo-text)' }}>{p.statement || p.name}</p>
              {sent && <span className="text-[10px] flex-shrink-0 px-1.5 py-0.5 rounded" style={{ background: `${sentColors[sent]}20`, color: sentColors[sent] }}>{sent}</span>}
            </div>

            {isExpanded && (
              <div className="px-4 py-3 rounded-b text-xs space-y-2" style={{ background: 'var(--neo-bg)', borderLeft: `1px solid var(--neo-blue)`, borderRight: `1px solid var(--neo-blue)`, borderBottom: `1px solid var(--neo-blue)` }}>
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <span className="text-[9px] uppercase block" style={{ color: 'var(--neo-text-muted)' }}>Confidence</span>
                    <span style={{ color: 'var(--neo-text)' }}>{((p.confidence || 0) * 100).toFixed(0)}%</span>
                  </div>
                  <div>
                    <span className="text-[9px] uppercase block" style={{ color: 'var(--neo-text-muted)' }}>Event Date</span>
                    <span style={{ color: 'var(--neo-text)' }}>{p._event_date || 'unknown'}</span>
                  </div>
                  <div>
                    <span className="text-[9px] uppercase block" style={{ color: 'var(--neo-text-muted)' }}>Freshness</span>
                    <span style={{ color: 'var(--neo-text)' }}>{p._freshness || 'unknown'}</span>
                  </div>
                </div>

                <div className="p-2 rounded" style={{ background: 'var(--neo-bg-secondary)' }}>
                  <span className="text-[9px] uppercase font-semibold block mb-1" style={{ color: 'var(--neo-text-muted)' }}>Source</span>
                  {p._source_url ? (
                    <a href={p._source_url} target="_blank" rel="noopener noreferrer" className="text-[11px] underline" style={{ color: 'var(--neo-blue)' }}>
                      {p._source_title || p._source_url}
                    </a>
                  ) : (
                    <span style={{ color: 'var(--neo-text)' }}>{p._source_title || p.created_by || 'Direct ingestion'}</span>
                  )}
                  {p._published_at && <span className="block text-[10px] mt-0.5" style={{ color: 'var(--neo-text-muted)' }}>Published: {p._published_at}</span>}
                </div>

                {p.subject && (
                  <div>
                    <span className="text-[9px] uppercase" style={{ color: 'var(--neo-text-muted)' }}>Subject: </span>
                    <span style={{ color: 'var(--neo-text)' }}>{p.subject}</span>
                  </div>
                )}

                <div className="text-[9px] font-mono" style={{ color: 'var(--neo-text-muted)' }}>ID: {f.id} | Created: {p._created_at ? new Date(p._created_at).toLocaleString() : 'unknown'}</div>
              </div>
            )}
          </div>
        );
      })}
      {facts.length === 0 && <p className="text-xs text-center py-4" style={{ color: 'var(--neo-text-muted)' }}>No facts extracted yet</p>}
    </div>
  );
}

/* ─── Intelligence Panel ─── */

function IntelligencePanel({ graphName, contextName }) {
  const [data, setData] = useState(null);
  const [runtime, setRuntime] = useState(null);
  const [loading, setLoading] = useState(false);
  const [subTab, setSubTab] = useState('overview');

  const encodedGraph = encodeURIComponent(graphName);

  const loadData = async () => {
    setLoading(true);
    try {
      const [nodesRes, runtimeRes, edgesRes] = await Promise.allSettled([
        api.get(`/graph/nodes`, { params: { graph: graphName, limit: 500 } }),
        api.post('/intelligence/runtime/assemble', { contexts: [graphName], focus_entity: contextName || graphName, days: 30 }).catch(() => null),
        api.get(`/graph/edges`, { params: { graph: graphName, limit: 500 } }).catch(() => ({ data: { edges: [] } })),
      ]);

      const nodes = nodesRes.status === 'fulfilled' ? (nodesRes.value.data.nodes || []) : [];
      const edges = edgesRes.status === 'fulfilled' ? (edgesRes.value?.data?.edges || []) : [];
      if (runtimeRes.status === 'fulfilled' && runtimeRes.value?.data) setRuntime(runtimeRes.value.data);

      const entities = nodes.filter(n => n.label === 'Entity');
      const facts = nodes.filter(n => n.label === 'Fact');
      const indicators = nodes.filter(n => n.label === 'Indicator');
      const documents = nodes.filter(n => n.label === 'Document');

      // Sentiment
      const sentimentCounts = { positive: 0, negative: 0, neutral: 0, mixed: 0 };
      entities.forEach(e => { const s = e.properties?._sentiment; if (s && sentimentCounts[s] !== undefined) sentimentCounts[s]++; });

      // Freshness
      const freshnessCounts = {};
      entities.forEach(e => { const f = e.properties?._freshness || 'unknown'; freshnessCounts[f] = (freshnessCounts[f] || 0) + 1; });

      // Geography
      const geoCounts = {};
      entities.forEach(e => {
        const geo = e.properties?._geography || [];
        (Array.isArray(geo) ? geo : []).forEach(g => { geoCounts[g] = (geoCounts[g] || 0) + 1; });
      });

      // Domain
      const domainCounts = {};
      entities.forEach(e => {
        const dom = e.properties?._domain || [];
        (Array.isArray(dom) ? dom : []).forEach(d => { domainCounts[d] = (domainCounts[d] || 0) + 1; });
      });

      // Impact
      const impactCounts = {};
      entities.forEach(e => { const imp = e.properties?._impact; if (imp) impactCounts[imp] = (impactCounts[imp] || 0) + 1; });

      // Confidence distribution
      const confidences = entities.map(e => e.properties?.confidence || 0).filter(c => c > 0);
      const avgConfidence = confidences.length ? (confidences.reduce((a, b) => a + b, 0) / confidences.length) : 0;

      // Relevance distribution
      const relevances = entities.map(e => e.properties?._amplifier_relevance || 0).filter(r => r > 0);
      const avgRelevance = relevances.length ? (relevances.reduce((a, b) => a + b, 0) / relevances.length) : 0;

      // Edge types
      const edgeTypes = {};
      edges.forEach(e => { edgeTypes[e.label] = (edgeTypes[e.label] || 0) + 1; });

      // Sort entities by confidence
      entities.sort((a, b) => (b.properties?.confidence || 0) - (a.properties?.confidence || 0));

      setData({
        entities, facts, indicators, documents, edges,
        sentimentCounts, freshnessCounts, geoCounts, domainCounts, impactCounts, edgeTypes,
        avgConfidence, avgRelevance,
        totalNodes: nodes.length, totalEdges: edges.length,
      });
    } catch (e) { console.error('Intelligence load error:', e); }
    setLoading(false);
  };

  // Auto-refresh every 30 seconds
  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 30000);
    return () => clearInterval(interval);
  }, [graphName]);

  if (loading && !data) return <div className="p-4 text-center text-xs" style={{ color: 'var(--neo-text-muted)' }}><Loader2 size={16} className="animate-spin inline mr-2" />Analyzing context...</div>;
  if (!data) return <div className="p-4 text-center text-xs" style={{ color: 'var(--neo-text-muted)' }}>No data available</div>;

  const net = data.sentimentCounts.positive - data.sentimentCounts.negative;
  const sentTotal = Object.values(data.sentimentCounts).reduce((a, b) => a + b, 0);

  const subTabs = [
    { id: 'overview', label: 'Overview' },
    { id: 'entities', label: `Entities (${data.entities.length})` },
    { id: 'facts', label: `Facts (${data.facts.length})` },
    { id: 'timeline', label: 'Timeline' },
    { id: 'connections', label: 'Connections' },
  ];

  return (
    <div className="space-y-3 p-1">
      {/* Context identifier */}
      <div className="flex items-center gap-2 px-1 mb-1">
        <Brain size={14} style={{ color: '#ec4899' }} />
        <span className="text-xs font-bold" style={{ color: 'var(--neo-text)' }}>{contextName}</span>
        <span className="text-[9px] font-mono px-1.5 py-0.5 rounded" style={{ background: 'var(--neo-bg)', color: 'var(--neo-text-muted)' }}>{graphName}</span>
      </div>

      {/* Hero Stats */}
      <div className="grid grid-cols-6 gap-2">
        {[
          { label: 'Entities', value: data.entities.length, color: '#3b82f6' },
          { label: 'Facts', value: data.facts.length, color: '#8b5cf6' },
          { label: 'Documents', value: data.documents.length, color: '#6b7280' },
          { label: 'Relationships', value: data.totalEdges, color: '#f59e0b' },
          { label: 'Net Sentiment', value: `${net > 0 ? '+' : ''}${net}`, color: net > 0 ? '#10b981' : net < 0 ? '#ef4444' : '#6b7280', icon: net > 0 ? TrendingUp : net < 0 ? TrendingDown : Minus },
          { label: 'Avg Confidence', value: `${(data.avgConfidence * 100).toFixed(0)}%`, color: data.avgConfidence > 0.7 ? '#10b981' : '#f59e0b' },
        ].map((s, i) => (
          <div key={i} className="p-2.5 rounded-lg text-center" style={{ background: `${s.color}10`, border: `1px solid ${s.color}30` }}>
            <p className="text-[9px] uppercase font-semibold" style={{ color: `${s.color}aa` }}>{s.label}</p>
            <p className="text-lg font-bold flex items-center justify-center gap-1" style={{ color: s.color }}>
              {s.icon && <s.icon size={14} />}{s.value}
            </p>
          </div>
        ))}
      </div>

      {/* Sub-tabs */}
      <div className="flex gap-1" style={{ borderBottom: '1px solid var(--neo-border)' }}>
        {subTabs.map(t => (
          <button key={t.id} onClick={() => setSubTab(t.id)} className="px-3 py-1.5 text-[11px] font-medium"
            style={{ color: subTab === t.id ? '#ec4899' : 'var(--neo-text-muted)', borderBottom: subTab === t.id ? '2px solid #ec4899' : '2px solid transparent' }}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Overview sub-tab */}
      {subTab === 'overview' && (
        <div className="grid grid-cols-2 gap-3">
          {/* Sentiment */}
          <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg)' }}>
            <h3 className="text-[10px] font-bold uppercase mb-2" style={{ color: 'var(--neo-text-muted)' }}>Sentiment Analysis</h3>
            <div className="flex h-3 rounded-full overflow-hidden mb-2" style={{ background: 'var(--neo-bg-secondary)' }}>
              {sentTotal > 0 && <>
                {data.sentimentCounts.positive > 0 && <div title={`Positive: ${data.sentimentCounts.positive}`} style={{ width: `${(data.sentimentCounts.positive / sentTotal) * 100}%`, background: '#10b981' }} />}
                {data.sentimentCounts.mixed > 0 && <div title={`Mixed: ${data.sentimentCounts.mixed}`} style={{ width: `${(data.sentimentCounts.mixed / sentTotal) * 100}%`, background: '#f59e0b' }} />}
                {data.sentimentCounts.neutral > 0 && <div title={`Neutral: ${data.sentimentCounts.neutral}`} style={{ width: `${(data.sentimentCounts.neutral / sentTotal) * 100}%`, background: '#6b7280' }} />}
                {data.sentimentCounts.negative > 0 && <div title={`Negative: ${data.sentimentCounts.negative}`} style={{ width: `${(data.sentimentCounts.negative / sentTotal) * 100}%`, background: '#ef4444' }} />}
              </>}
            </div>
            <div className="grid grid-cols-2 gap-1 text-[10px]">
              <span style={{ color: '#10b981' }}>&#9679; Positive: {data.sentimentCounts.positive}</span>
              <span style={{ color: '#ef4444' }}>&#9679; Negative: {data.sentimentCounts.negative}</span>
              <span style={{ color: '#f59e0b' }}>&#9679; Mixed: {data.sentimentCounts.mixed}</span>
              <span style={{ color: '#6b7280' }}>&#9679; Neutral: {data.sentimentCounts.neutral}</span>
            </div>
          </div>

          {/* Freshness */}
          <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg)' }}>
            <h3 className="text-[10px] font-bold uppercase mb-2" style={{ color: 'var(--neo-text-muted)' }}>Data Freshness</h3>
            <div className="space-y-1.5">
              {Object.entries(data.freshnessCounts).sort((a, b) => b[1] - a[1]).map(([label, count]) => {
                const colors = { fresh: '#10b981', recent: '#3b82f6', aging: '#f59e0b', stale: '#ef4444', historical: '#6b7280', unknown: '#4b5563' };
                const pct = data.entities.length ? ((count / data.entities.length) * 100) : 0;
                return (
                  <div key={label} className="flex items-center gap-2">
                    <span className="text-[10px] w-16" style={{ color: colors[label] || '#6b7280' }}>{label}</span>
                    <div className="flex-1 h-1.5 rounded-full" style={{ background: 'var(--neo-bg-secondary)' }}>
                      <div className="h-full rounded-full" style={{ width: `${pct}%`, background: colors[label] || '#6b7280' }} />
                    </div>
                    <span className="text-[10px] w-6 text-right" style={{ color: 'var(--neo-text-muted)' }}>{count}</span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Geography */}
          <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg)' }}>
            <h3 className="text-[10px] font-bold uppercase mb-2" style={{ color: 'var(--neo-text-muted)' }}>Geographic Exposure</h3>
            {Object.keys(data.geoCounts).length > 0 ? (
              <div className="flex gap-1.5 flex-wrap">
                {Object.entries(data.geoCounts).sort((a, b) => b[1] - a[1]).map(([geo, count]) => (
                  <span key={geo} className="text-[10px] px-2 py-1 rounded-full flex items-center gap-1" style={{ background: '#8b5cf620', color: '#8b5cf6', border: '1px solid #8b5cf630' }}>
                    <Globe size={10} /> {geo} <span style={{ opacity: 0.6 }}>({count})</span>
                  </span>
                ))}
              </div>
            ) : <p className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>No geographic tags detected</p>}
          </div>

          {/* Domains & Impact */}
          <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg)' }}>
            <h3 className="text-[10px] font-bold uppercase mb-2" style={{ color: 'var(--neo-text-muted)' }}>Domain & Impact</h3>
            {Object.keys(data.domainCounts).length > 0 && (
              <div className="mb-2">
                <span className="text-[9px] uppercase" style={{ color: 'var(--neo-text-muted)' }}>Domains: </span>
                {Object.entries(data.domainCounts).sort((a, b) => b[1] - a[1]).map(([d, c]) => (
                  <span key={d} className="text-[10px] px-1.5 py-0.5 rounded mr-1" style={{ background: '#3b82f620', color: '#3b82f6' }}>{d} ({c})</span>
                ))}
              </div>
            )}
            {Object.keys(data.impactCounts).length > 0 && (
              <div>
                <span className="text-[9px] uppercase" style={{ color: 'var(--neo-text-muted)' }}>Impact: </span>
                {Object.entries(data.impactCounts).map(([imp, c]) => (
                  <span key={imp} className="text-[10px] px-1.5 py-0.5 rounded mr-1" style={{
                    background: imp === 'high' ? '#ef444420' : imp === 'medium' ? '#f59e0b20' : '#10b98120',
                    color: imp === 'high' ? '#ef4444' : imp === 'medium' ? '#f59e0b' : '#10b981',
                  }}>{imp}: {c}</span>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Entities sub-tab */}
      {subTab === 'entities' && (
        <ExpandableEntityList entities={data.entities} graphName={graphName} />
      )}

      {/* Facts sub-tab */}
      {subTab === 'facts' && (
        <ExpandableFactList facts={data.facts} graphName={graphName} />
      )}

      {/* Timeline sub-tab — time travel */}
      {subTab === 'timeline' && (
        <TimelinePanel entities={data.entities} facts={data.facts} />
      )}

      {/* Connections sub-tab */}
      {subTab === 'connections' && (
        <div className="grid grid-cols-2 gap-3">
          <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg)' }}>
            <h3 className="text-[10px] font-bold uppercase mb-2" style={{ color: 'var(--neo-text-muted)' }}>Relationship Types</h3>
            {Object.keys(data.edgeTypes).length > 0 ? (
              <div className="space-y-1.5">
                {Object.entries(data.edgeTypes).sort((a, b) => b[1] - a[1]).map(([type, count]) => (
                  <div key={type} className="flex items-center gap-2">
                    <span className="text-[10px] flex-1 font-mono" style={{ color: '#f59e0b' }}>{type}</span>
                    <div className="w-16 h-1.5 rounded-full" style={{ background: 'var(--neo-bg-secondary)' }}>
                      <div className="h-full rounded-full" style={{ width: `${Math.min(100, (count / Math.max(...Object.values(data.edgeTypes))) * 100)}%`, background: '#f59e0b' }} />
                    </div>
                    <span className="text-[10px] w-6 text-right font-mono" style={{ color: 'var(--neo-text-muted)' }}>{count}</span>
                  </div>
                ))}
              </div>
            ) : <p className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>No relationships found</p>}
          </div>

          <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg)' }}>
            <h3 className="text-[10px] font-bold uppercase mb-2" style={{ color: 'var(--neo-text-muted)' }}>Quality Scores</h3>
            <div className="space-y-2">
              <div>
                <div className="flex justify-between text-[10px] mb-1">
                  <span style={{ color: 'var(--neo-text-muted)' }}>Avg Confidence</span>
                  <span style={{ color: data.avgConfidence > 0.7 ? '#10b981' : '#f59e0b' }}>{(data.avgConfidence * 100).toFixed(0)}%</span>
                </div>
                <div className="h-2 rounded-full" style={{ background: 'var(--neo-bg-secondary)' }}>
                  <div className="h-full rounded-full" style={{ width: `${data.avgConfidence * 100}%`, background: data.avgConfidence > 0.7 ? '#10b981' : '#f59e0b' }} />
                </div>
              </div>
              <div>
                <div className="flex justify-between text-[10px] mb-1">
                  <span style={{ color: 'var(--neo-text-muted)' }}>Avg Relevance</span>
                  <span style={{ color: data.avgRelevance > 0.6 ? '#10b981' : '#f59e0b' }}>{(data.avgRelevance * 100).toFixed(0)}%</span>
                </div>
                <div className="h-2 rounded-full" style={{ background: 'var(--neo-bg-secondary)' }}>
                  <div className="h-full rounded-full" style={{ width: `${data.avgRelevance * 100}%`, background: data.avgRelevance > 0.6 ? '#10b981' : '#f59e0b' }} />
                </div>
              </div>
              <div className="pt-2 border-t" style={{ borderColor: 'var(--neo-border)' }}>
                <p className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                  {data.totalNodes} nodes, {data.totalEdges} edges in this context
                </p>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ─── Context Units Panel ─── */

function ContextUnitsPanel({ graphName }) {
  const [cus, setCus] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);

  const loadCUs = async () => {
    setLoading(true);
    try {
      const res = await api.get(`/dashboard/graphs/${encodeURIComponent(graphName)}/nodes?label=ContextUnit&limit=50`);
      const nodes = res.data.nodes || res.data || [];
      setCus(nodes);
      setLoaded(true);
    } catch {
      setCus([]);
      setLoaded(true);
    } finally {
      setLoading(false);
    }
  };

  const rebuildCUs = async () => {
    setRebuilding(true);
    try {
      await api.post(`/dashboard/graphs/${encodeURIComponent(graphName)}/rebuild-cus`);
      toast.success('Context Units rebuilt');
      loadCUs();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Rebuild failed');
    } finally {
      setRebuilding(false);
    }
  };

  if (!loaded) {
    return (
      <div className="flex flex-col items-center justify-center py-8">
        <Cpu size={28} className="mb-3" style={{ color: '#f59e0b' }} />
        <p className="text-xs mb-1" style={{ color: 'var(--neo-text-muted)' }}>
          Context Units are clustered intelligence packets built from your data.
        </p>
        <p className="text-[10px] mb-3" style={{ color: 'var(--neo-text-dim)' }}>
          Each CU groups related facts by shared entities and answers specific questions.
        </p>
        <button onClick={loadCUs}
          className="px-3 py-1.5 rounded-lg text-xs font-medium"
          style={{ background: '#f59e0b', color: '#fff' }}>
          {loading ? 'Loading...' : 'Load Context Units'}
        </button>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Cpu size={13} style={{ color: '#f59e0b' }} />
          <span className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>
            {cus.length} Context Unit{cus.length !== 1 ? 's' : ''}
          </span>
        </div>
        <div className="flex gap-1">
          <button onClick={loadCUs}
            className="px-2 py-1 rounded text-[10px]"
            style={{ color: 'var(--neo-text-muted)', border: '1px solid var(--neo-border)' }}>
            {loading ? '...' : 'Refresh'}
          </button>
          <button onClick={rebuildCUs}
            disabled={rebuilding}
            className="px-2 py-1 rounded text-[10px] font-medium"
            style={{ background: 'rgba(245,158,11,0.15)', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.3)' }}>
            {rebuilding ? 'Rebuilding...' : 'Rebuild CUs'}
          </button>
        </div>
      </div>

      {cus.length === 0 ? (
        <div className="text-center py-6">
          <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
            No Context Units yet. Ingest data first, then click "Rebuild CUs".
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {cus.map((cu, i) => {
            const props = cu.properties || {};
            const confidence = props.confidence || 0;
            const confColor = confidence >= 0.8 ? '#22c55e' : confidence >= 0.5 ? '#f59e0b' : '#ef4444';
            return (
              <div key={cu.id || i} className="p-3 rounded-lg"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)',
                  borderLeft: `3px solid #f59e0b` }}>
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-xs font-semibold" style={{ color: 'var(--neo-text)' }}>
                    {props.topic || 'Untitled'}
                  </span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded font-bold"
                    style={{ background: `${confColor}15`, color: confColor }}>
                    {Math.round(confidence * 100)}%
                  </span>
                </div>
                <p className="text-[11px] leading-relaxed mb-2" style={{ color: 'var(--neo-text-muted)' }}>
                  {props.claim || 'No claim'}
                </p>
                {props.questions_answered && props.questions_answered.length > 0 && (
                  <div className="mb-1.5">
                    <span className="text-[10px] font-medium" style={{ color: '#3b82f6' }}>Answers:</span>
                    {props.questions_answered.map((q, j) => (
                      <p key={j} className="text-[10px] ml-2" style={{ color: 'var(--neo-text-dim)' }}>- {q}</p>
                    ))}
                  </div>
                )}
                {props.next_clues && props.next_clues.length > 0 && (
                  <div>
                    <span className="text-[10px] font-medium" style={{ color: '#8b5cf6' }}>Next clues:</span>
                    {props.next_clues.map((c, j) => (
                      <p key={j} className="text-[10px] ml-2" style={{ color: 'var(--neo-text-dim)' }}>- {c}</p>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}


function IngestPanel({ contextId, contextType, onIngested }) {
  const mapped = _OLD_TYPE_MAP[contextType] || contextType;
  const typeInfo = CONTEXT_TYPES.find(ct => ct.value === mapped) || CONTEXT_TYPES.find(ct => ct.value === contextType) || CONTEXT_TYPES[0];
  // All context types support all ingest modes — Smart Pipeline handles everything
  const ingestModes = ['file', 'text', 'url', 'connector'];
  const defaultTab = mapped === 'software_dev' ? 'repo' : 'file';
  const [ingestTab, setIngestTab] = useState(defaultTab);
  const [pipelines, setPipelines] = useState([]);
  const [selectedPipeline, setSelectedPipeline] = useState('');

  // Text tab
  const [text, setText] = useState('');
  const [title, setTitle] = useState('');

  // File tab
  const [file, setFile] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef(null);

  // URL tab
  const [url, setUrl] = useState('');
  const [crawlMode, setCrawlMode] = useState(false);
  const [maxPages, setMaxPages] = useState(20);
  const [githubToken, setGithubToken] = useState('');
  const isGithubUrl = /^https:\/\/github\.com\/[^/]+\/[^/]+/.test(url.trim());

  // Repo tab (software_dev)
  const [repoPath, setRepoPath] = useState('');

  // Connector tab
  const [connectorType, setConnectorType] = useState(mapped === 'database' ? 'database' : mapped === 'software_dev' ? 'github' : 'custom');
  const [connectorUrl, setConnectorUrl] = useState('');
  const [connectorToken, setConnectorToken] = useState('');
  const [connectorExtra, setConnectorExtra] = useState('');

  // Job tracking — uses global JobsProvider (persists across page navigation)
  const { jobsForContext, refreshJobs } = useJobs();
  const jobs = jobsForContext(contextId);
  const [busy, setBusy] = useState(false);
  const pollRef = useRef(null);

  // Intent for scenario-driven pipelines
  const [intent, setIntent] = useState('graph_rag');
  // Execution mode
  const [mode, setMode] = useState('run_all'); // run_all or step_by_step
  const [showStepWise, setShowStepWise] = useState(false);

  // Ingestion mode: auto or review
  const [ingestMode, setIngestMode] = useState('auto');
  // Background jobs
  const [bgJobs, setBgJobs] = useState([]);
  const [showJobLog, setShowJobLog] = useState(null); // job_id to show log for
  const [debugMode, setDebugMode] = useState(false);  // debug logging for jobs
  const [showDebugLogs, setShowDebugLogs] = useState(false); // show/hide debug entries in log
  // Preview state (review mode)
  const [preview, setPreview] = useState(null);
  const [previewTab, setPreviewTab] = useState('passages');
  // Unified filter tags: [{type, value, direction?, threshold?}]
  const [filterTags, setFilterTags] = useState([]);
  const [showFilters, setShowFilters] = useState(false);
  const [newFilterType, setNewFilterType] = useState('keyword_include');
  const [newFilterValue, setNewFilterValue] = useState('');
  const [newFilterThreshold, setNewFilterThreshold] = useState(0.55);
  const [keywordMode, setKeywordMode] = useState('or'); // "or" | "and"

  const addFilter = () => {
    if (!newFilterValue.trim()) return;
    setFilterTags([...filterTags, {
      type: newFilterType,
      value: newFilterValue.trim(),
      threshold: newFilterType.startsWith('semantic') ? newFilterThreshold : undefined,
    }]);
    setNewFilterValue('');
  };
  const removeFilter = (i) => setFilterTags(filterTags.filter((_, j) => j !== i));

  // Build filters object for API
  const buildFilters = () => {
    if (!filterTags.length) return null;
    const f = { keywords_include: [], keywords_exclude: [], categories_include: [], semantic_rules: [] };
    for (const tag of filterTags) {
      if (tag.type === 'keyword_include') f.keywords_include.push(tag.value);
      else if (tag.type === 'keyword_exclude') f.keywords_exclude.push(tag.value);
      else if (tag.type === 'category') f.categories_include.push(tag.value);
      else if (tag.type === 'semantic_include') f.semantic_rules.push({ query: tag.value, direction: 'include', threshold: tag.threshold || 0.7 });
      else if (tag.type === 'semantic_exclude') f.semantic_rules.push({ query: tag.value, direction: 'exclude', threshold: tag.threshold || 0.5 });
    }
    // Include keyword mode if there are keyword includes
    if (f.keywords_include.length > 0 && keywordMode === 'and') {
      f.keywords_include_mode = 'and';
    }
    // Remove empty arrays
    Object.keys(f).forEach(k => { if (Array.isArray(f[k]) && !f[k].length) delete f[k]; });
    return Object.keys(f).length ? f : null;
  };

  useEffect(() => {
    api.get('/dashboard/pipelines')
      .then((r) => {
        const pl = r.data.pipelines || [];
        setPipelines(pl);
        const auto = pl.find((p) => p.id === 'builtin:auto');
        const basic = pl.find((p) => p.id === 'builtin:basic');
        if (auto) setSelectedPipeline(auto.id);
        else if (basic) setSelectedPipeline(basic.id);
        else if (pl.length > 0) setSelectedPipeline(pl[0].id);
      })
      .catch(() => {});

    // Poll for background jobs
    const pollJobs = () => {
      api.get(`/dashboard/ingest/jobs?context_id=${contextId}`)
        .then(r => {
          const incoming = r.data.jobs || [];
          setBgJobs(prev => {
            // Server is the source of truth — start from server list, merge running-only from prev
            const serverIds = new Set(incoming.map(j => j.job_id));
            const map = {};
            for (const j of incoming) map[j.job_id] = j;
            // Keep locally-known running jobs not yet on server (just submitted)
            for (const j of prev) {
              if (!serverIds.has(j.job_id) && (j.status === 'running' || j.status === 'queued')) {
                map[j.job_id] = j;
              }
            }
            return Object.values(map).sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
          });
        })
        .catch(() => {});
    };
    pollJobs();
    const jobInterval = setInterval(pollJobs, 3000);
    return () => clearInterval(jobInterval);
  }, [contextId]);

  const startPolling = () => {
    // Trigger a refresh of the global jobs state
    refreshJobs();
  };

  const handleIngestRepo = async () => {
    if (!repoPath.trim()) return;
    setBusy(true);
    try {
      const body = {
        text: repoPath.trim(),
        title: repoPath.trim().split('/').pop() || 'repo',
        pipeline_id: selectedPipeline || undefined,
      };
      if (githubToken.trim()) body.github_token = githubToken.trim();
      await api.post(`/dashboard/contexts/${contextId}/ingest/text`, body);
      toast.success('SDLC repo scan started');
      setRepoPath('');
      setGithubToken('');
      startPolling();
    } catch {} finally { setBusy(false); }
  };

  const handleIngestText = async () => {
    if (!text.trim()) return;
    setBusy(true);
    try {
      await api.post(`/dashboard/contexts/${contextId}/ingest/text`, {
        text: text.trim(), title: title.trim() || 'Untitled',
        pipeline_id: selectedPipeline || undefined,
      });
      toast.success('Ingestion started');
      setText(''); setTitle('');
      startPolling();
    } catch {} finally { setBusy(false); }
  };

  const handleIngestFile = async () => {
    if (!file) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      if (selectedPipeline) fd.append('pipeline_id', selectedPipeline);
      await api.post(`/dashboard/contexts/${contextId}/ingest/file`, fd);
      toast.success('File ingestion started');
      setFile(null);
      startPolling();
    } catch {} finally { setBusy(false); }
  };

  const handleIngestUrl = async () => {
    if (!url.trim()) return;
    setBusy(true);
    try {
      // GitHub repo URLs → route to SDLC scan (text handler detects repo URLs)
      const trimmedUrl = url.trim();
      if (trimmedUrl.match(/^https:\/\/github\.com\/[^/]+\/[^/]+\/?$/)) {
        const body = {
          text: trimmedUrl,
          title: trimmedUrl.split('/').pop() || 'repo',
          pipeline_id: selectedPipeline || undefined,
        };
        if (githubToken.trim()) body.github_token = githubToken.trim();
        await api.post(`/dashboard/contexts/${contextId}/ingest/text`, body);
        toast.success('SDLC repo scan started');
        setUrl('');
        setGithubToken('');
        startPolling();
        setBusy(false);
        return;
      }
      // Check if a smart pipeline is selected
      const pl = pipelines.find(p => p.id === selectedPipeline);
      const smartName = pl?._smart_pipeline;

      const filters = buildFilters();
      const hasFilters = !!filters;

      if (smartName && crawlMode) {
        // Crawl mode with smart pipeline — submit as background job
        const body = {
          url: url.trim(), graph: contextId, pipeline: smartName,
          mode: 'auto', crawl: true, max_pages: maxPages,
        };
        if (hasFilters) body.filters = filters;
        if (debugMode) body.debug = true;
        const res = await api.post('/dashboard/ingest/submit', body);
        toast.success(`Crawl job submitted (up to ${maxPages} pages). Job: ${res.data.job_id?.slice(0, 8)}...`);
        setUrl('');
        setCrawlMode(false);
        startPolling();
      } else if (smartName && ingestMode === 'review') {
        // Review mode: preview before commit
        const body = { url: url.trim(), graph: contextId, pipeline: smartName };
        if (hasFilters) body.filters = filters;
        if (debugMode) body.debug = true;
        const res = await api.post('/dashboard/ingest/preview', body);
        if (res.data.status === 'staged') {
          setPreview(res.data);
          toast.success('Preview ready — review before committing');
        } else {
          toast.error(res.data.result?.errors?.[0] || 'Preview failed');
        }
      } else if (smartName) {
        // Auto mode with smart pipeline
        const body = { url: url.trim(), graph: contextId, pipeline: smartName };
        if (hasFilters) body.filters = filters;
        if (debugMode) body.debug = true;
        await api.post('/dashboard/ingest/url', body);
        toast.success('Smart ingestion started');
        setUrl('');
        startPolling();
      } else {
        // Legacy pipeline
        await api.post(`/dashboard/contexts/${contextId}/ingest/url`, {
          url: url.trim(),
          pipeline_id: selectedPipeline || undefined,
          crawl: crawlMode,
          max_pages: crawlMode ? maxPages : 1,
        });
        toast.success(crawlMode ? `Crawling website (up to ${maxPages} pages)...` : 'URL ingestion started');
        setUrl('');
        setCrawlMode(false);
        startPolling();
      }
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Ingestion failed');
    } finally { setBusy(false); }
  };

  const handleCommitPreview = async () => {
    if (!preview?.staging_id) return;
    setBusy(true);
    try {
      const res = await api.post('/dashboard/ingest/commit', {
        staging_id: preview.staging_id,
        graph: contextId,
      });
      toast.success(`Committed: ${res.data.result?.passages || 0} passages, ${res.data.result?.entities || 0} entities`);
      setPreview(null);
      setUrl('');
      onIngested?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Commit failed');
    } finally { setBusy(false); }
  };

  const handleDiscardPreview = async () => {
    if (preview?.staging_id) {
      await api.post('/dashboard/ingest/discard', { staging_id: preview.staging_id }).catch(() => {});
    }
    setPreview(null);
    toast('Preview discarded');
  };

  const handleIngestConnector = async () => {
    if (!connectorUrl.trim()) return;
    setBusy(true);
    try {
      const config = {};
      const credentials = {};

      if (connectorType === 'database') {
        config.connection_string = connectorUrl.trim();
        // Auto-detect db_type from connection string
        const cs = connectorUrl.toLowerCase();
        if (cs.startsWith('postgresql') || cs.startsWith('postgres')) config.db_type = 'postgresql';
        else if (cs.startsWith('mysql')) config.db_type = 'mysql';
        else config.db_type = 'sqlite';
      } else if (connectorType === 'github') {
        config.repo_url = connectorUrl.trim();
        if (connectorToken) credentials.token = connectorToken;
      } else if (connectorType === 'jira') {
        config.domain = connectorUrl.trim();
        if (connectorExtra) credentials.email = connectorExtra.trim();
        if (connectorToken) credentials.api_token = connectorToken;
      } else if (connectorType === 'sitemap') {
        config.base_url = connectorUrl.trim();
        config.endpoints = ['/sitemap.xml'];
      } else if (connectorType === 'api') {
        config.base_url = connectorUrl.trim();
        if (connectorExtra) {
          config.endpoints = connectorExtra.split(',').map(e => e.trim()).filter(Boolean);
        }
        if (connectorToken) {
          credentials.auth_type = 'bearer';
          credentials.auth_value = connectorToken;
        }
      } else if (connectorType === 'chatgpt') {
        config.export_file = connectorUrl.trim();
        config.max_conversations = 100;
        if (connectorToken) credentials.api_key = connectorToken;
      } else if (connectorType === 'claude') {
        // Support both export file and project dir
        const path = connectorUrl.trim();
        if (path.endsWith('.json')) {
          config.export_file = path;
        } else {
          config.project_dir = path;
        }
        if (connectorToken) credentials.api_key = connectorToken;
      } else if (connectorType === 'gemini') {
        config.export_file = connectorUrl.trim();
        if (connectorToken) credentials.api_key = connectorToken;
      } else {
        // ServiceNow / Salesforce / SAP / Custom
        config.instance_url = connectorUrl.trim();
        config.domain = connectorUrl.trim();
        config.host = connectorUrl.trim();
        if (connectorExtra) credentials.username = connectorExtra.trim();
        if (connectorToken) {
          credentials.password = connectorToken;
          credentials.api_token = connectorToken;
        }
      }

      // Map sitemap/api to rest_api connector_type for backend
      const backendType = connectorType === 'sitemap' || connectorType === 'api' ? 'rest_api' : connectorType;

      await api.post(`/dashboard/contexts/${contextId}/ingest/connector`, {
        connector_type: backendType,
        config,
        credentials,
        phase: 'both',
        pipeline_id: selectedPipeline || undefined,
      });
      toast.success(`${connectorType} connector ingestion started`);
      setConnectorUrl('');
      setConnectorToken('');
      setConnectorExtra('');
      startPolling();
    } catch {} finally { setBusy(false); }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) setFile(f);
  };

  const tabStyle = (t) => ({
    background: ingestTab === t ? 'var(--neo-blue)' : 'transparent',
    color: ingestTab === t ? '#fff' : 'var(--neo-text-muted)',
  });

  return (
    <div className="space-y-3">
      <span className="text-xs font-medium" style={{ color: 'var(--neo-text-muted)' }}>Ingest Data</span>

      {/* Tab switcher — only show modes supported by this context type */}
      <div className="flex gap-1">
        {[
          ...(mapped === 'software_dev' ? [{ key: 'repo', icon: Code2, label: 'Repo' }] : []),
          { key: 'text', icon: FileText, label: 'Text' },
          { key: 'file', icon: Upload, label: 'Upload' },
          { key: 'url', icon: Link, label: 'URL' },
          { key: 'connector', icon: Database, label: 'Connector' },
        ].filter(t => t.key === 'repo' || ingestModes.includes(t.key)).map((t) => (
          <button
            key={t.key}
            onClick={() => setIngestTab(t.key)}
            className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium transition"
            style={tabStyle(t.key)}
          >
            <t.icon size={11} /> {t.label}
          </button>
        ))}
      </div>

      {/* Repo tab (software_dev only) */}
      {ingestTab === 'repo' && (
        <div className="space-y-2">
          <input
            value={repoPath}
            onChange={(e) => setRepoPath(e.target.value)}
            placeholder="https://github.com/owner/repo  or  /local/path/to/repo"
            className="w-full px-2.5 py-1.5 rounded-lg text-xs outline-none"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          />
          {/* Token field for GitHub repos */}
          {/^https:\/\/github\.com\//.test(repoPath.trim()) && (
            <div className="space-y-1.5">
              <div className="flex items-center gap-2 px-2 py-1 rounded-lg" style={{ background: 'rgba(16,185,129,0.08)', border: '1px solid rgba(16,185,129,0.25)' }}>
                <span style={{ color: '#10b981', fontSize: 11 }}>⬡ GitHub repo — will scan issues, PRs, README, contributors via API</span>
              </div>
              <div className="flex items-center gap-2">
                <input
                  type="password"
                  value={githubToken}
                  onChange={(e) => setGithubToken(e.target.value)}
                  placeholder="GitHub token (optional — required for private repos)"
                  className="flex-1 px-3 py-1.5 rounded-lg text-sm outline-none"
                  style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                />
                {githubToken && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: 'rgba(16,185,129,0.15)', color: '#10b981' }}>
                    token set
                  </span>
                )}
              </div>
              <p className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
                Without a token: public repos work, 60 API req/hour. With token: private repos + 5000 req/hour.
              </p>
            </div>
          )}
          <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
            Scans repo into SDLC graph: README → Requirements, issues → KnownIssues, PRs → ChangeRecords, source → CodeModules
          </div>
        </div>
      )}

      {/* Text tab */}
      {ingestTab === 'text' && (
        <div className="space-y-2">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Title (optional)"
            className="w-full px-2.5 py-1 rounded-lg text-xs outline-none"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          />
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Paste text content here..."
            rows={4}
            className="w-full px-3 py-1.5 rounded-lg text-sm outline-none resize-none"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          />
        </div>
      )}

      {/* File tab */}
      {ingestTab === 'file' && (
        <div
          className="rounded-lg p-6 text-center transition"
          style={{
            background: dragOver ? 'rgba(0,122,255,0.08)' : 'var(--neo-surface)',
            border: `2px dashed ${dragOver ? 'var(--neo-blue)' : 'var(--neo-border)'}`,
          }}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
        >
          {file ? (
            <div className="flex items-center justify-center gap-2">
              <FileText size={16} style={{ color: 'var(--neo-blue)' }} />
              <span className="text-sm" style={{ color: 'var(--neo-text)' }}>{file.name}</span>
              <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                ({(file.size / 1024).toFixed(1)} KB)
              </span>
              <button onClick={() => setFile(null)} style={{ color: 'var(--neo-text-muted)' }}><X size={14} /></button>
            </div>
          ) : (
            <>
              <Upload size={24} className="mx-auto mb-2" style={{ color: 'var(--neo-text-muted)' }} />
              <p className="text-xs mb-1" style={{ color: 'var(--neo-text-muted)' }}>
                Drop file here or{' '}
                <button
                  onClick={() => fileRef.current?.click()}
                  className="underline"
                  style={{ color: 'var(--neo-blue)', background: 'none', border: 'none', cursor: 'pointer' }}
                >
                  browse
                </button>
              </p>
              <p className="text-xs" style={{ color: typeInfo?.color || 'var(--neo-text-dim)' }}>
                {typeInfo?.hint || 'Excel, PDF, DOCX, CSV, JSON, TXT, HTML, XML, MD'}
              </p>
              <input
                ref={fileRef}
                type="file"
                accept={typeInfo?.accepts ? `${typeInfo.accepts},.zip,.json` : ".xlsx,.xls,.pdf,.docx,.csv,.json,.txt,.html,.xml,.md,.doc,.rtf,.zip"}
                className="hidden"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
              />
            </>
          )}
        </div>
      )}

      {/* URL tab */}
      {ingestTab === 'url' && (
        <div className="space-y-2">
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder={isGithubUrl ? "https://github.com/owner/repo" : mapped === 'software_dev' ? "https://github.com/user/repo  or  https://docs.example.com" : mapped === 'web' ? "https://example.com/page" : "https://..."}
            className="w-full px-3 py-1.5 rounded-lg text-sm outline-none"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          />
          {/* GitHub repo detected — show token field, hide crawl */}
          {isGithubUrl ? (
            <div className="space-y-1.5">
              <div className="flex items-center gap-2 px-2 py-1 rounded-lg" style={{ background: 'rgba(16,185,129,0.08)', border: '1px solid rgba(16,185,129,0.25)' }}>
                <span style={{ color: '#10b981', fontSize: 11 }}>⬡ GitHub repo detected — will scan issues, PRs, README, contributors</span>
              </div>
              <div className="flex items-center gap-2">
                <input
                  type="password"
                  value={githubToken}
                  onChange={(e) => setGithubToken(e.target.value)}
                  placeholder="GitHub token (optional — required for private repos)"
                  className="flex-1 px-3 py-1.5 rounded-lg text-sm outline-none"
                  style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                />
                {githubToken && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: 'rgba(16,185,129,0.15)', color: '#10b981' }}>
                    token set
                  </span>
                )}
              </div>
              <p className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
                Without a token: public repos work, 60 API req/hour limit. With token: private repos + 5000 req/hour.
              </p>
            </div>
          ) : (
            <>
              {/* Crawl toggle — only for regular URLs */}
              <div className="flex items-center gap-3">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={crawlMode} onChange={(e) => setCrawlMode(e.target.checked)}
                    className="rounded" style={{ accentColor: 'var(--neo-blue)' }} />
                  <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Crawl website (follow links)</span>
                </label>
                {crawlMode && (
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Max pages:</span>
                    <input type="number" value={maxPages} onChange={(e) => setMaxPages(Math.min(50, Math.max(1, parseInt(e.target.value) || 1)))}
                      min={1} max={50} className="w-14 px-2 py-0.5 rounded text-xs text-center"
                      style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
                  </div>
                )}
              </div>
              {crawlMode && (
                <p className="text-xs" style={{ color: 'var(--neo-cyan)' }}>
                  Will crawl up to {maxPages} pages from this domain, following internal links. Respects robots.txt.
                </p>
              )}
              {mapped === 'software_dev' && !crawlMode && (
                <p className="text-xs" style={{ color: '#10b981' }}>Git repo URL, raw file URL, or documentation URL</p>
              )}
            </>
          )}

          {/* Unified content filters (tag-style) */}
          {pipelines.find(p => p.id === selectedPipeline)?._smart_pipeline && (
            <div>
              <button onClick={() => setShowFilters(!showFilters)}
                className="flex items-center gap-1 text-xs font-medium transition"
                style={{ color: 'var(--neo-cyan)' }}>
                {showFilters ? '\u25BE' : '\u25B8'} Content Filters
                {filterTags.length > 0 && (
                  <span className="px-1.5 py-0.5 rounded-full text-[10px]" style={{ background: 'var(--neo-cyan)', color: '#000' }}>
                    {filterTags.length}
                  </span>
                )}
              </button>
              {showFilters && (
                <div className="mt-2 p-3 rounded-lg space-y-2" style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)' }}>
                  {/* Active filter tags */}
                  {filterTags.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {filterTags.map((tag, i) => {
                        const colors = {
                          keyword_include: { bg: 'var(--neo-green)', label: '+ keyword' },
                          keyword_exclude: { bg: '#ef4444', label: '- keyword' },
                          category: { bg: 'var(--neo-blue)', label: 'category' },
                          semantic_include: { bg: '#8b5cf6', label: '+ semantic' },
                          semantic_exclude: { bg: '#f97316', label: '- semantic' },
                        };
                        const c = colors[tag.type] || { bg: 'var(--neo-border)', label: tag.type };
                        return (
                          <span key={i} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium"
                            style={{ background: c.bg, color: '#fff' }}>
                            {c.label}: {tag.value}
                            {tag.threshold !== undefined && ` (${tag.threshold})`}
                            <button onClick={() => removeFilter(i)} className="ml-0.5 hover:opacity-70">&times;</button>
                          </span>
                        );
                      })}
                      <button onClick={() => setFilterTags([])}
                        className="text-[10px] px-1.5 py-0.5 rounded"
                        style={{ color: 'var(--neo-text-muted)', border: '1px solid var(--neo-border)' }}>
                        Clear all
                      </button>
                    </div>
                  )}

                  {/* Keyword AND/OR toggle — shown when keyword includes exist */}
                  {filterTags.some(t => t.type === 'keyword_include') && (
                    <div className="flex items-center gap-1.5">
                      <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>Keyword match:</span>
                      {['or', 'and'].map(m => (
                        <button key={m} onClick={() => setKeywordMode(m)}
                          className="text-[10px] px-2 py-0.5 rounded font-medium"
                          style={{
                            background: keywordMode === m ? 'var(--neo-blue)' : 'transparent',
                            color: keywordMode === m ? '#fff' : 'var(--neo-text-muted)',
                            border: `1px solid ${keywordMode === m ? 'var(--neo-blue)' : 'var(--neo-border)'}`,
                          }}>
                          {m.toUpperCase()}
                        </button>
                      ))}
                      <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                        {keywordMode === 'or' ? '(any keyword matches)' : '(all keywords must match)'}
                      </span>
                    </div>
                  )}

                  {/* Add new filter */}
                  <div className="flex items-center gap-1.5">
                    <select value={newFilterType} onChange={(e) => setNewFilterType(e.target.value)}
                      className="px-2 py-1 rounded text-[10px] outline-none"
                      style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}>
                      <option value="keyword_include">+ Keyword (include)</option>
                      <option value="keyword_exclude">- Keyword (exclude)</option>
                      <option value="category">Category</option>
                      <option value="semantic_include">+ Semantic (include)</option>
                      <option value="semantic_exclude">- Semantic (exclude)</option>
                    </select>
                    <input value={newFilterValue} onChange={(e) => setNewFilterValue(e.target.value)}
                      placeholder={newFilterType.startsWith('semantic') ? 'e.g. AI startups in Bengaluru' : newFilterType === 'category' ? 'e.g. technology' : 'e.g. Bengaluru'}
                      onKeyDown={(e) => e.key === 'Enter' && addFilter()}
                      className="flex-1 px-2 py-1 rounded text-xs outline-none"
                      style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
                    {newFilterType.startsWith('semantic') && (
                      <input type="number" value={newFilterThreshold} onChange={(e) => setNewFilterThreshold(parseFloat(e.target.value) || 0.5)}
                        min={0} max={1} step={0.1}
                        className="w-14 px-1 py-1 rounded text-[10px] text-center outline-none"
                        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                        title="Similarity threshold (0-1)" />
                    )}
                    <button onClick={addFilter}
                      className="px-2.5 py-1 rounded text-[10px] font-medium"
                      style={{ background: 'var(--neo-blue)', color: '#fff' }}>
                      Add
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Ingest mode selector */}
          {pipelines.find(p => p.id === selectedPipeline)?._smart_pipeline && (
            <div className="flex items-center gap-2 mt-1">
              <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Mode:</span>
              {['auto', 'review'].map(m => (
                <button
                  key={m}
                  onClick={() => setIngestMode(m)}
                  className="px-2.5 py-1 rounded text-xs font-medium transition"
                  style={{
                    background: ingestMode === m ? 'var(--neo-blue)' : 'var(--neo-surface)',
                    color: ingestMode === m ? '#fff' : 'var(--neo-text-muted)',
                    border: `1px solid ${ingestMode === m ? 'var(--neo-blue)' : 'var(--neo-border)'}`,
                  }}
                >
                  {m === 'auto' ? 'Auto (commit immediately)' : 'Review (preview first)'}
                </button>
              ))}
            </div>
          )}

          {/* Debug mode toggle */}
          <label className="flex items-center gap-1.5 mt-1 cursor-pointer">
            <input type="checkbox" checked={debugMode} onChange={(e) => setDebugMode(e.target.checked)}
              className="w-3 h-3 rounded" />
            <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
              Debug mode (detailed filter &amp; stage logs)
            </span>
          </label>
        </div>
      )}

      {/* Preview panel (review mode result) */}
      {preview && (
        <div className="mt-3 p-4 rounded-xl" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-cyan)', borderWidth: 2 }}>
          <div className="flex items-center justify-between mb-3">
            <h4 className="text-sm font-semibold" style={{ color: 'var(--neo-text)' }}>
              Preview — Quality: {preview.preview?.quality_score?.toFixed(0) || '?'}%
            </h4>
            <div className="flex gap-2">
              <button onClick={handleCommitPreview} disabled={busy}
                className="px-3 py-1 rounded-lg text-xs font-medium"
                style={{ background: 'var(--neo-green)', color: '#fff' }}>
                Approve & Ingest
              </button>
              <button onClick={handleDiscardPreview}
                className="px-3 py-1 rounded-lg text-xs font-medium"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}>
                Discard
              </button>
            </div>
          </div>

          {/* Preview tabs */}
          <div className="flex gap-1 mb-2">
            {['passages', 'entities', 'facts', 'edges', 'filtered'].map(tab => {
              const counts = {
                passages: preview.preview?.passages?.length || 0,
                entities: preview.preview?.entities?.length || 0,
                facts: preview.preview?.facts?.length || 0,
                edges: preview.preview?.edges?.length || 0,
                filtered: preview.preview?.filter_log?.length || 0,
              };
              return (
                <button key={tab} onClick={() => setPreviewTab(tab)}
                  className="px-2 py-1 rounded text-xs transition"
                  style={{
                    background: previewTab === tab ? 'var(--neo-blue)' : 'transparent',
                    color: previewTab === tab ? '#fff' : 'var(--neo-text-muted)',
                  }}>
                  {tab} ({counts[tab]})
                </button>
              );
            })}
          </div>

          {/* Preview content */}
          <div className="max-h-60 overflow-y-auto text-xs space-y-1" style={{ color: 'var(--neo-text)' }}>
            {previewTab === 'passages' && (preview.preview?.passages || []).map((p, i) => (
              <div key={i} className="p-2 rounded" style={{ background: 'var(--neo-bg)' }}>
                <span style={{ color: 'var(--neo-text-muted)' }}>[{p.chunk_index}] {p.token_count} tokens:</span> {p.content}
              </div>
            ))}
            {previewTab === 'entities' && (preview.preview?.entities || []).map((e, i) => (
              <div key={i} className="p-2 rounded flex items-center gap-2" style={{ background: 'var(--neo-bg)' }}>
                <span className="px-1.5 py-0.5 rounded text-[10px] font-medium" style={{ background: 'var(--neo-blue)', color: '#fff' }}>{e.label}</span>
                <span>{e.name}</span>
              </div>
            ))}
            {previewTab === 'facts' && (preview.preview?.facts || []).map((f, i) => (
              <div key={i} className="p-2 rounded" style={{ background: 'var(--neo-bg)' }}>
                {f.statement}
              </div>
            ))}
            {previewTab === 'edges' && (preview.preview?.edges || []).map((e, i) => (
              <div key={i} className="p-2 rounded" style={{ background: 'var(--neo-bg)' }}>
                {e.source} <span style={{ color: 'var(--neo-cyan)' }}>--{e.label}--&gt;</span> {e.target}
              </div>
            ))}
            {previewTab === 'filtered' && (preview.preview?.filter_log || []).map((f, i) => (
              <div key={i} className="p-2 rounded" style={{ background: 'var(--neo-bg)', color: 'var(--neo-text-muted)' }}>
                {f.title || f.url}: <span style={{ color: '#ef4444' }}>{f.reason}</span>
              </div>
            ))}
            {previewTab === 'filtered' && (!preview.preview?.filter_log || preview.preview.filter_log.length === 0) && (
              <div className="p-2" style={{ color: 'var(--neo-text-muted)' }}>No content was filtered out.</div>
            )}
          </div>
        </div>
      )}

      {/* Background jobs panel */}
      {bgJobs.length > 0 && (
        <div className="mt-3 space-y-2">
          <div className="flex items-center justify-between">
            <h4 className="text-xs font-semibold" style={{ color: 'var(--neo-text-muted)' }}>
              Ingestion Jobs ({bgJobs.length})
            </h4>
            {bgJobs.some(j => j.status === 'completed' || j.status === 'failed' || j.status === 'completed_empty') && (
              <button onClick={() => {
                api.delete('/dashboard/ingest/jobs')
                  .then(() => { setBgJobs(prev => prev.filter(j => j.status === 'running' || j.status === 'queued')); refreshJobs(); toast.success('Finished jobs cleared'); })
                  .catch(() => {});
              }}
                className="text-[10px] px-1.5 py-0.5 rounded"
                style={{ color: 'var(--neo-text-muted)', border: '1px solid var(--neo-border)' }}>
                Clear finished
              </button>
            )}
          </div>
          {bgJobs.slice(0, 10).map(job => {
            const statusColors = {
              queued: 'var(--neo-text-muted)',
              running: 'var(--neo-blue)',
              review_pending: '#f59e0b',
              completed: 'var(--neo-green)',
              failed: '#ef4444',
            };
            return (
              <div key={job.job_id} className="p-2.5 rounded-lg text-xs"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full" style={{ background: statusColors[job.status] || 'var(--neo-border)' }} />
                    <span style={{ color: 'var(--neo-text)' }}>{job.input_summary?.slice(0, 50)}</span>
                    <span className="px-1.5 py-0.5 rounded text-[10px]"
                      style={{ background: statusColors[job.status] + '22', color: statusColors[job.status] }}>
                      {job.status}
                    </span>
                  </div>
                  <div className="flex items-center gap-1">
                    {(job.status === 'failed' || job.status === 'completed_empty') && (
                      <button onClick={() => {
                        api.post(`/dashboard/ingest/jobs/${job.job_id}/retry`)
                          .then(() => { toast.success('Job retrying'); refreshJobs(); })
                          .catch(e => toast.error(e?.response?.data?.detail || 'Retry failed'));
                      }}
                        className="text-[10px] px-1.5 py-0.5 rounded"
                        style={{ color: '#f59e0b', border: '1px solid #f59e0b' }}>
                        Retry
                      </button>
                    )}
                    {job.status !== 'running' && job.status !== 'queued' && (
                      <button onClick={() => {
                        api.delete(`/dashboard/ingest/jobs/${job.job_id}`)
                          .then(() => { setBgJobs(prev => prev.filter(j => j.job_id !== job.job_id)); refreshJobs(); })
                          .catch(e => toast.error(e?.response?.data?.detail || 'Delete failed'));
                      }}
                        className="text-[10px] px-1.5 py-0.5 rounded"
                        style={{ color: 'var(--neo-text-muted)', border: '1px solid var(--neo-border)' }}>
                        Clear
                      </button>
                    )}
                    <button onClick={() => setShowJobLog(showJobLog === job.job_id ? null : job.job_id)}
                      className="text-[10px] px-1.5 py-0.5 rounded"
                      style={{ color: 'var(--neo-cyan)', border: '1px solid var(--neo-border)' }}>
                      {showJobLog === job.job_id ? 'Hide log' : 'Show log'}
                    </button>
                  </div>
                </div>
                {job.progress && job.status === 'running' && (
                  <div className="mt-1 text-[10px]" style={{ color: 'var(--neo-blue)' }}>
                    Stage: {job.progress}
                  </div>
                )}
                {job.result && job.status === 'completed' && (
                  <div className="mt-1 text-[10px]" style={{ color: 'var(--neo-green)' }}>
                    {job.result.passages !== undefined && `${job.result.passages} passages, ${job.result.entities} entities, ${job.result.edges} edges`}
                    {job.result.ingested && ` | ${job.result.ingested.length} ingested, ${job.result.filtered?.length || 0} filtered`}
                  </div>
                )}
                {job.error && (
                  <div className="mt-1 text-[10px]" style={{ color: '#ef4444' }}>{job.error}</div>
                )}
                {/* Job log */}
                {showJobLog === job.job_id && job.log && (() => {
                  const hasDebug = job.log.some(e => e.level === 'debug');
                  const visibleLogs = showDebugLogs ? job.log : job.log.filter(e => e.level !== 'debug');
                  return (
                    <div className="mt-2 p-2 rounded space-y-0.5 max-h-48 overflow-y-auto"
                      style={{ background: 'var(--neo-bg)', fontFamily: 'monospace' }}>
                      {hasDebug && (
                        <div className="flex items-center gap-1.5 mb-1">
                          <button onClick={() => setShowDebugLogs(!showDebugLogs)}
                            className="text-[9px] px-1.5 py-0.5 rounded"
                            style={{ color: showDebugLogs ? '#f59e0b' : 'var(--neo-text-muted)',
                                     border: `1px solid ${showDebugLogs ? '#f59e0b' : 'var(--neo-border)'}` }}>
                            {showDebugLogs ? 'Hide debug' : 'Show debug'}
                          </button>
                        </div>
                      )}
                      {visibleLogs.map((entry, i) => (
                        <div key={i} className="text-[10px]"
                          style={{ color: entry.level === 'debug' ? '#f59e0b' : 'var(--neo-text-muted)',
                                   opacity: entry.level === 'debug' ? 0.85 : 1 }}>
                          <span style={{ color: 'var(--neo-cyan)' }}>
                            {new Date(entry.timestamp).toLocaleTimeString()}
                          </span>
                          {' '}
                          <span style={{ color: entry.level === 'debug' ? '#f59e0b' : 'var(--neo-text)' }}>[{entry.stage}]</span>
                          {entry.duration_ms > 0 && <span style={{ color: 'var(--neo-text-muted)' }}> ({entry.duration_ms}ms)</span>}
                          {' '}
                          {entry.details?.message || entry.message}
                          {entry.level === 'debug' && entry.details?.filter_decisions && (
                            <div className="ml-4 mt-0.5 space-y-0.5">
                              {entry.details.filter_decisions.map((d, j) => (
                                <div key={j} className="text-[9px]" style={{ color: '#f59e0b' }}>
                                  {d.step}: {JSON.stringify(d, null, 0).slice(0, 120)}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  );
                })()}
                {/* Schema suggestions */}
                {job.result?.schema_suggestions?.length > 0 && (
                  <div className="mt-1.5 p-1.5 rounded" style={{ background: 'rgba(139,92,246,0.1)', border: '1px solid #8b5cf6' }}>
                    <span className="text-[10px] font-medium" style={{ color: '#8b5cf6' }}>
                      Schema suggestions ({job.result.schema_suggestions.length}):
                    </span>
                    {job.result.schema_suggestions.map((s, i) => (
                      <div key={i} className="text-[10px] mt-0.5" style={{ color: 'var(--neo-text)' }}>
                        {s.type === 'new_node_type' ? '+ Node' : '+ Edge'}: <strong>{s.name}</strong>
                        {' '}({s.count}x)
                        {s.examples?.length > 0 && <span style={{ color: 'var(--neo-text-muted)' }}> e.g. {s.examples.slice(0, 2).join(', ')}</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Connector tab */}
      {ingestTab === 'connector' && (
        <div className="space-y-2">
          {/* Connector type selector — all types available */}
          <select
            value={connectorType}
            onChange={(e) => { setConnectorType(e.target.value); setConnectorUrl(''); setConnectorToken(''); setConnectorExtra(''); }}
            className="w-full px-3 py-1.5 rounded-lg text-sm outline-none"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          >
            <optgroup label="Data Sources">
              <option value="database">Database (PostgreSQL, MySQL, SQLite)</option>
              <option value="api">REST API</option>
            </optgroup>
            <optgroup label="Dev Tools">
              <option value="github">GitHub Repository</option>
              <option value="jira">Jira Project</option>
            </optgroup>
            <optgroup label="Enterprise">
              <option value="servicenow">ServiceNow</option>
              <option value="salesforce">Salesforce</option>
              <option value="sap">SAP</option>
            </optgroup>
            <optgroup label="AI Chat History">
              <option value="chatgpt">ChatGPT Conversations</option>
              <option value="claude">Claude Conversations</option>
              <option value="gemini">Gemini Conversations</option>
            </optgroup>
            <optgroup label="Other">
              <option value="sitemap">Sitemap Crawler</option>
              <option value="custom">Custom Connector</option>
            </optgroup>
          </select>

          {/* Connection URL / string */}
          <input
            value={connectorUrl}
            onChange={(e) => setConnectorUrl(e.target.value)}
            placeholder={
              connectorType === 'database' ? "postgresql://user:pass@host:5432/dbname or path/to/file.db" :
              connectorType === 'github' ? "https://github.com/owner/repo" :
              connectorType === 'jira' ? "https://yourcompany.atlassian.net" :
              connectorType === 'servicenow' ? "https://yourorg.service-now.com" :
              connectorType === 'salesforce' ? "https://yourorg.salesforce.com" :
              connectorType === 'sap' ? "SAP host (e.g., 10.0.0.1)" :
              connectorType === 'sitemap' ? "https://example.com" :
              connectorType === 'api' ? "https://api.example.com" :
              connectorType === 'chatgpt' ? "Path to conversations.json (from ChatGPT export)" :
              connectorType === 'claude' ? "Path to Claude project dir or export file" :
              connectorType === 'gemini' ? "Path to Gemini export file" :
              "Connection URL..."
            }
            className="w-full px-3 py-1.5 rounded-lg text-sm outline-none"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          />

          {/* Connector-specific fields */}
          {connectorType === 'github' && (
            <input value={connectorToken} onChange={(e) => setConnectorToken(e.target.value)}
              placeholder="GitHub token (ghp_... — optional for public repos)" type="password"
              className="w-full px-3 py-1.5 rounded-lg text-sm outline-none"
              style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
          )}
          {connectorType === 'jira' && (
            <>
              <div className="flex gap-2">
                <input value={connectorExtra} onChange={(e) => setConnectorExtra(e.target.value)}
                  placeholder="Email (for auth)" className="flex-1 px-3 py-1.5 rounded-lg text-sm outline-none"
                  style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
                <input value={connectorToken} onChange={(e) => setConnectorToken(e.target.value)}
                  placeholder="API Token" type="password" className="flex-1 px-3 py-1.5 rounded-lg text-sm outline-none"
                  style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
              </div>
            </>
          )}
          {(connectorType === 'servicenow' || connectorType === 'salesforce') && (
            <div className="flex gap-2">
              <input value={connectorExtra} onChange={(e) => setConnectorExtra(e.target.value)}
                placeholder="Username / Email" className="flex-1 px-3 py-1.5 rounded-lg text-sm outline-none"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
              <input value={connectorToken} onChange={(e) => setConnectorToken(e.target.value)}
                placeholder="Password / Token" type="password" className="flex-1 px-3 py-1.5 rounded-lg text-sm outline-none"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
            </div>
          )}
          {connectorType === 'api' && (
            <>
              <input value={connectorToken} onChange={(e) => setConnectorToken(e.target.value)}
                placeholder="Bearer token (optional)" type="password"
                className="w-full px-3 py-1.5 rounded-lg text-sm outline-none"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
              <input value={connectorExtra} onChange={(e) => setConnectorExtra(e.target.value)}
                placeholder="Endpoints (comma-separated, e.g. /users,/orders)"
                className="w-full px-3 py-1.5 rounded-lg text-sm outline-none"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
            </>
          )}

          {/* Description + Test Connection */}
          <div className="flex items-center justify-between">
            <p className="text-xs" style={{ color: 'var(--neo-text-dim)' }}>
              {connectorType === 'database' ? 'Extracts table schemas, columns, relationships, and sample data' :
               connectorType === 'github' ? 'Pulls issues, PRs, contributors, and file structure' :
               connectorType === 'jira' ? 'Pulls projects, issues, sprints, and components' :
               connectorType === 'servicenow' ? 'Pulls incidents, changes, and CMDB items' :
               connectorType === 'salesforce' ? 'Pulls objects, fields, and relationships (requires simple-salesforce)' :
               connectorType === 'sap' ? 'Pulls modules, tables, and fields (requires pyrfc)' :
               connectorType === 'api' ? 'Discovers endpoints, fields, and pulls records' :
               connectorType === 'chatgpt' ? 'Imports conversations, messages, and memories from ChatGPT export (Settings → Export data)' :
               connectorType === 'claude' ? 'Imports conversations from Claude export or Claude Code project (CLAUDE.md + memory files)' :
               connectorType === 'gemini' ? 'Imports conversations from Google Gemini export' :
               'Connect to an external data source'}
            </p>
            {connectorUrl.trim() && (
              <button
                onClick={async () => {
                  try {
                    const body = { connector_type: connectorType === 'sitemap' || connectorType === 'api' ? 'rest_api' : connectorType,
                      config: connectorType === 'database' ? { connection_string: connectorUrl, db_type: 'sqlite' } :
                        connectorType === 'github' ? { repo_url: connectorUrl } :
                        { domain: connectorUrl, instance_url: connectorUrl, base_url: connectorUrl },
                      credentials: connectorToken ? { token: connectorToken, api_token: connectorToken, username: connectorExtra, password: connectorToken } : {} };
                    const res = await api.post(`/dashboard/contexts/${contextId}/ingest/connector/test`, body);
                    if (res.data.success) toast.success('Connection successful');
                    else toast.error(res.data.message || 'Connection failed');
                  } catch { toast.error('Connection test failed'); }
                }}
                className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium transition hover:opacity-80"
                style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
              >
                <CheckCircle size={11} /> Test
              </button>
            )}
          </div>
        </div>
      )}

      {/* Pipeline selector + intent + submit */}
      <div className="space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          <select
            value={selectedPipeline}
            onChange={(e) => setSelectedPipeline(e.target.value)}
            className="px-2 py-1 rounded-lg text-xs outline-none flex-1"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)', minWidth: 180 }}
          >
            {/* Group: Recommended */}
            {pipelines.filter((p) => p.tags?.includes('recommended') || p.tags?.includes('auto')).length > 0 && (
              <optgroup label="Recommended">
                {pipelines.filter((p) => p.tags?.includes('recommended') || p.tags?.includes('auto')).map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </optgroup>
            )}
            {/* Group: Structured Data */}
            {pipelines.filter((p) => p.tags?.includes('tabular') || p.tags?.includes('excel') || p.tags?.includes('records')).length > 0 && (
              <optgroup label="Structured Data">
                {pipelines.filter((p) => (p.tags?.includes('tabular') || p.tags?.includes('excel') || p.tags?.includes('records')) && !p.tags?.includes('auto')).map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </optgroup>
            )}
            {/* Group: Documents */}
            {pipelines.filter((p) => !p.tags?.includes('tabular') && !p.tags?.includes('excel') && !p.tags?.includes('records') && !p.tags?.includes('auto') && !p.tags?.includes('recommended')).length > 0 && (
              <optgroup label="Documents">
                {pipelines.filter((p) => !p.tags?.includes('tabular') && !p.tags?.includes('excel') && !p.tags?.includes('records') && !p.tags?.includes('auto') && !p.tags?.includes('recommended')).map((p) => (
                  <option key={p.id} value={p.id}>{p.name}{p.tags?.includes('llm') ? ' (LLM)' : ''}{p.tags?.includes('embeddings') || p.tags?.includes('no-llm') ? '' : ''}</option>
                ))}
              </optgroup>
            )}
          </select>
          <select
            value={intent}
            onChange={(e) => setIntent(e.target.value)}
            className="px-2 py-1 rounded-lg text-xs outline-none"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)', minWidth: 110 }}
          >
            <option value="build_graph">Build Graph</option>
            <option value="graph_rag">Graph RAG</option>
            <option value="search_only">Search Only</option>
          </select>
          <button
            onClick={() => setMode(mode === 'run_all' ? 'step_by_step' : 'run_all')}
            className="px-2 py-1 rounded-lg text-xs font-medium transition"
            style={{
              background: mode === 'step_by_step' ? 'rgba(88,86,214,0.15)' : 'var(--neo-surface)',
              color: mode === 'step_by_step' ? '#5856d6' : 'var(--neo-text-muted)',
              border: `1px solid ${mode === 'step_by_step' ? '#5856d6' : 'var(--neo-border)'}`,
            }}
            title="Toggle step-by-step execution"
          >
            {mode === 'step_by_step' ? 'Step' : 'Auto'}
          </button>
        </div>
        {/* Pipeline info + schema toggle */}
        <div className="flex items-center gap-2 flex-wrap">
          {(() => {
            const sel = pipelines.find((p) => p.id === selectedPipeline);
            if (!sel) return null;
            const params = sel.default_params || {};
            const badges = [];
            if (params.llm_model) badges.push(params.llm_model);
            if (params.embedding_model) badges.push(params.embedding_model);
            if (sel.tags?.includes('no-llm')) badges.push('No LLM');
            if (sel.tags?.includes('fast')) badges.push('Fast');
            if (badges.length === 0) return null;
            return (
              <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'rgba(88,86,214,0.1)', color: '#5856d6' }}>
                {badges.join(' + ')}
              </span>
            );
          })()}
          <button
            onClick={() => {
              if (mode === 'step_by_step') {
                setShowStepWise(true);
              } else {
                (ingestTab === 'repo' ? handleIngestRepo : ingestTab === 'text' ? handleIngestText : ingestTab === 'file' ? handleIngestFile : ingestTab === 'url' ? handleIngestUrl : ingestTab === 'connector' ? handleIngestConnector : handleIngestUrl)();
              }
            }}
            disabled={busy || !selectedPipeline || (ingestTab === 'repo' && !repoPath.trim()) || (ingestTab === 'text' && !text.trim()) || (ingestTab === 'file' && !file) || (ingestTab === 'url' && !url.trim()) || (ingestTab === 'connector' && !connectorUrl.trim())}
            className="ml-auto flex items-center gap-1 px-3 py-1 rounded-lg text-xs font-medium transition hover:opacity-90 disabled:opacity-50"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            <Send size={11} /> {busy ? 'Processing...' : mode === 'step_by_step' ? 'Start Step-by-Step' : 'Ingest'}
          </button>
        </div>
      </div>


      {/* Active job indicator — full details in Jobs tab */}
      {jobs.some(j => j.status === 'processing') && (
        <div className="flex items-center gap-2 p-2 rounded-lg text-xs"
          style={{ background: 'var(--neo-blue)' + '10', border: '1px solid var(--neo-blue)' + '30' }}>
          <Loader2 size={12} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
          <span style={{ color: 'var(--neo-blue)' }}>
            {jobs.filter(j => j.status === 'processing').length} job(s) running
          </span>
          {jobs.filter(j => j.status === 'processing').map(j => (
            <span key={j.job_id} className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
              {j.source}{j.current_stage ? ` — ${STAGE_LABELS[j.current_stage] || j.current_stage}` : ''}
            </span>
          ))}
        </div>
      )}

      {/* Step-wise pipeline panel */}
      {showStepWise && (
        <div
          className="rounded-lg p-3"
          style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)' }}
        >
          <StepWisePipelinePanel
            graphName={contextId}
            pipelineId={selectedPipeline}
            intent={intent}
            file={ingestTab === 'file' ? file : null}
            text={ingestTab === 'text' ? text : null}
            url={ingestTab === 'url' ? url : null}
            onComplete={() => {
              setShowStepWise(false);
              onIngested();
            }}
            onCancel={() => setShowStepWise(false)}
          />
        </div>
      )}
    </div>
  );
}

/* ─── Chunk / Item Card ─── */

const HIDDEN_KEYS = new Set([
  'node_id', 'label_type', 'name', 'content', 'statement', 'snippet',
  'score', 'category', 'role', 'label', 'node_type', 'graph',
  // Infrastructure metadata — not useful for users
  '_origin', '_verified', '_region', 'source_type', 'confidence',
  '_created_at', '_agent_id', '_agent_name', 'created_by',
  '_extraction_method', '_source', 'validation_status', 'validation_errors',
  '_missing_fields', 'content_hash', 'normalised_hash', 'simhash',
]);

function ChunkCard({ item, contextId }) {
  const [expanded, setExpanded] = useState(false);
  const [provenance, setProvenance] = useState(null);
  const [provLoading, setProvLoading] = useState(false);
  const [showProv, setShowProv] = useState(false);
  // Items from get_items() are flat property dicts; search results may have .properties nested
  const props = item.properties || item;
  const content = item.snippet || props.content || props.statement || '';
  const name = props.name || item.label || item.name || '';
  const source = props.source || props.filename || props.url || '';
  const charCount = props.char_count || content.length;
  const chunkIdx = props.chunk_index;
  const nodeId = item.node_id || props.node_id;
  const nodeLabel = item.label_type || props.label_type || item.node_type || '';

  const loadProvenance = async () => {
    if (provenance) { setShowProv(p => !p); return; }
    if (!contextId || !nodeId) return;
    setProvLoading(true);
    try {
      const res = await api.get(`/dashboard/contexts/${contextId}/items/${encodeURIComponent(nodeId)}/provenance`);
      setProvenance(res.data);
      setShowProv(true);
    } catch { setProvenance({ passages: [], facts: [], entities: [] }); setShowProv(true); }
    finally { setProvLoading(false); }
  };

  // Collect extra metadata keys for display
  const metaEntries = Object.entries(props).filter(
    ([k, v]) => !HIDDEN_KEYS.has(k) && v != null && v !== ''
  );

  const PREVIEW_LEN = 250;
  const isLong = content.length > PREVIEW_LEN;
  const displayText = expanded ? content : content.substring(0, PREVIEW_LEN);

  return (
    <div
      className="p-3 rounded-lg"
      style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
    >
      {/* Header: name + badges */}
      <div className="flex items-center gap-2 mb-1.5 flex-wrap">
        {name && (
          <span className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>{name}</span>
        )}
        {(item.label_type || props.label_type || item.node_type) && (
          <span className="px-1.5 py-0.5 rounded text-xs" style={{ background: 'rgba(139,92,246,0.1)', color: '#8b5cf6' }}>
            {item.label_type || props.label_type || item.node_type}
          </span>
        )}
        {(item.role || props.role) && (
          <span className="px-1.5 py-0.5 rounded text-xs" style={{ background: 'rgba(59,130,246,0.1)', color: '#3b82f6' }}>
            {item.role || props.role}
          </span>
        )}
        {item.score != null && (
          <span className="px-1.5 py-0.5 rounded text-xs font-medium" style={{ background: 'rgba(34,197,94,0.1)', color: '#22c55e' }}>
            {typeof item.score === 'number' ? `${(item.score * 100).toFixed(0)}%` : item.score}
          </span>
        )}
        {(item.category || props.category) && (
          <span className="px-1.5 py-0.5 rounded text-xs" style={{ background: 'rgba(59,130,246,0.05)', color: 'var(--neo-text-dim)' }}>
            {item.category || props.category}
          </span>
        )}
      </div>

      {/* Content — expandable */}
      {content && (
        <div className="mb-1.5">
          <p className="text-sm whitespace-pre-wrap break-words leading-relaxed" style={{ color: 'var(--neo-text)' }}>
            {displayText}
            {isLong && !expanded && '...'}
          </p>
          {isLong && (
            <button
              onClick={() => setExpanded(e => !e)}
              className="mt-1 flex items-center gap-1 text-xs bg-transparent border-none cursor-pointer"
              style={{ color: 'var(--neo-blue)' }}
            >
              {expanded ? <><ChevronUp size={12} /> Show less</> : <><ChevronDown size={12} /> Show full content ({charCount.toLocaleString()} chars)</>}
            </button>
          )}
        </div>
      )}

      {/* Source & chunk info */}
      <div className="flex items-center gap-3 text-xs flex-wrap mb-1" style={{ color: 'var(--neo-text-dim)' }}>
        {source && <span title="Source"><FileText size={10} className="inline mr-0.5" />{source}</span>}
        {chunkIdx != null && <span><Hash size={10} className="inline mr-0.5" />Chunk {chunkIdx}</span>}
        {charCount > 0 && <span>{charCount.toLocaleString()} chars</span>}
        {(item.node_id || props.node_id) && <span className="font-mono">{(item.node_id || props.node_id).substring(0, 8)}</span>}
      </div>

      {/* Extra metadata */}
      {metaEntries.length > 0 && (
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs mt-1 pt-1" style={{ borderTop: '1px solid var(--neo-border)' }}>
          {metaEntries.slice(0, 8).map(([k, v]) => (
            <span key={k} style={{ color: 'var(--neo-text-dim)' }}>
              <span className="font-medium" style={{ color: 'var(--neo-text-muted)' }}>{k}:</span>{' '}
              {typeof v === 'object' ? JSON.stringify(v) : String(v).substring(0, 80)}
            </span>
          ))}
          {metaEntries.length > 8 && (
            <span style={{ color: 'var(--neo-text-dim)' }}>+{metaEntries.length - 8} more</span>
          )}
        </div>
      )}

      {/* Provenance: show source passage/facts/entities */}
      {contextId && nodeId && (
        <div className="mt-1.5 pt-1" style={{ borderTop: '1px solid var(--neo-border)' }}>
          <button
            onClick={loadProvenance}
            disabled={provLoading}
            className="flex items-center gap-1 text-xs bg-transparent border-none cursor-pointer"
            style={{ color: 'var(--neo-blue)' }}
          >
            {provLoading ? <Loader2 size={11} className="animate-spin" /> : <Layers size={11} />}
            {showProv ? 'Hide connections' : 'Show connections'}
          </button>
          {showProv && provenance && (
            <div className="mt-2 space-y-2">
              {provenance.passages?.length > 0 && (
                <div>
                  <span className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: '#10b981' }}>
                    Source Passages ({provenance.passages.length})
                  </span>
                  {provenance.passages.map((p, i) => (
                    <div key={p.node_id || i} className="mt-1 p-2 rounded text-xs" style={{ background: 'var(--neo-bg)', border: '1px solid rgba(16,185,129,0.2)' }}>
                      <div className="font-medium mb-1" style={{ color: 'var(--neo-text)' }}>{p.name || 'Passage'}</div>
                      <p className="whitespace-pre-wrap break-words leading-relaxed" style={{ color: 'var(--neo-text-muted)' }}>
                        {p.content || p.content_preview || p.statement || '(content stored in vector DB)'}
                      </p>
                      {p.source && <div className="mt-1 text-[10px]" style={{ color: 'var(--neo-text-dim)' }}><FileText size={9} className="inline mr-0.5" />{p.source}</div>}
                    </div>
                  ))}
                </div>
              )}
              {provenance.facts?.length > 0 && (
                <div>
                  <span className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: '#f59e0b' }}>
                    Related Facts ({provenance.facts.length})
                  </span>
                  {provenance.facts.map((f, i) => (
                    <div key={f.node_id || i} className="mt-1 p-2 rounded text-xs" style={{ background: 'var(--neo-bg)', border: '1px solid rgba(245,158,11,0.2)' }}>
                      <span style={{ color: 'var(--neo-text)' }}>{f.statement || f.name}</span>
                      {f.confidence != null && <span className="ml-2 text-[10px]" style={{ color: '#f59e0b' }}>{(f.confidence * 100).toFixed(0)}%</span>}
                    </div>
                  ))}
                </div>
              )}
              {provenance.entities?.length > 0 && (
                <div>
                  <span className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: '#8b5cf6' }}>
                    Mentioned Entities ({provenance.entities.length})
                  </span>
                  <div className="flex flex-wrap gap-1 mt-1">
                    {provenance.entities.map((e, i) => (
                      <span key={e.node_id || i} className="px-1.5 py-0.5 rounded text-xs"
                        style={{ background: 'rgba(139,92,246,0.1)', color: '#8b5cf6' }}>
                        {e.name} <span className="opacity-60">({e.label})</span>
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {!provenance.passages?.length && !provenance.facts?.length && !provenance.entities?.length && (
                <p className="text-xs py-1" style={{ color: 'var(--neo-text-dim)' }}>No connected nodes found.</p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ─── Schema Editor (with Library Picker + SchemaBuilder) ─── */

function SchemaEditor({ contextId, contextType }) {
  const [yamlText, setYamlText] = useState('');
  const [source, setSource] = useState(''); // 'default', 'custom', 'none'
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [librarySchemas, setLibrarySchemas] = useState([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerSearch, setPickerSearch] = useState('');
  const [customizing, setCustomizing] = useState(false);

  const loadSchema = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get(`/dashboard/contexts/${contextId}/extraction-schema`);
      setYamlText(res.data.yaml || '');
      setSource(res.data.source);
    } catch {
      toast.error('Failed to load schema');
    } finally {
      setLoading(false);
    }
  }, [contextId]);

  const loadLibrary = useCallback(async () => {
    try {
      const res = await api.get('/dashboard/schemas');
      setLibrarySchemas(res.data.schemas || []);
    } catch { /* silent */ }
  }, []);

  useEffect(() => { loadSchema(); loadLibrary(); }, [loadSchema, loadLibrary]);

  const handlePickSchema = async (schema) => {
    try {
      const res = await api.get(`/dashboard/schemas/${schema.name}`);
      const yaml = res.data.yaml || '';
      setSaving(true);
      await api.put(`/dashboard/contexts/${contextId}/extraction-schema`, { yaml });
      setYamlText(yaml);
      setSource('custom');
      setPickerOpen(false);
      setCustomizing(false);
      toast.success(`Applied "${schema.name}" schema`);
    } catch {
      toast.error('Failed to apply schema');
    } finally {
      setSaving(false);
    }
  };

  const handleSaveCustom = async (yaml) => {
    setSaving(true);
    try {
      await api.put(`/dashboard/contexts/${contextId}/extraction-schema`, { yaml });
      setYamlText(yaml);
      setSource('custom');
      setCustomizing(false);
      toast.success('Schema saved');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to save schema');
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async () => {
    if (!window.confirm('Reset to default schema for this context type?')) return;
    try {
      await api.delete(`/dashboard/contexts/${contextId}/extraction-schema`);
      toast.success('Reset to default schema');
      setCustomizing(false);
      loadSchema();
    } catch {
      toast.error('Failed to reset');
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-6">
        <Loader2 size={16} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      </div>
    );
  }

  // Customizing mode — show full SchemaBuilder
  if (customizing) {
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>Customize Schema</span>
          <button
            onClick={() => setCustomizing(false)}
            className="px-2 py-1 rounded text-xs transition"
            style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
          >
            Cancel
          </button>
        </div>
        <SchemaBuilder
          initialYaml={yamlText}
          onSave={(yaml) => handleSaveCustom(yaml)}
          showSaveBar={true}
        />
      </div>
    );
  }

  const filteredLibrary = librarySchemas.filter(s => {
    if (!pickerSearch) return true;
    const q = pickerSearch.toLowerCase();
    return s.name.toLowerCase().includes(q) || (s.tags || []).some(t => t.includes(q));
  });

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>Extraction Schema</span>
          <span
            className="px-1.5 py-0.5 rounded text-xs font-medium"
            style={{
              background: source === 'custom' ? 'rgba(139,92,246,0.15)' : 'rgba(34,197,94,0.15)',
              color: source === 'custom' ? '#8b5cf6' : '#22c55e',
            }}
          >
            {source === 'custom' ? 'Custom' : source === 'default' ? 'Default' : 'None'}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setPickerOpen(!pickerOpen)}
            className="px-2 py-1 rounded text-xs transition"
            style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
          >
            Pick from Library
          </button>
          <button
            onClick={() => setCustomizing(true)}
            className="px-2 py-1 rounded text-xs font-medium transition"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            Customize
          </button>
          {source === 'custom' && (
            <button
              onClick={handleReset}
              className="px-2 py-1 rounded text-xs transition"
              style={{ border: '1px solid #ef4444', color: '#ef4444' }}
            >
              Reset
            </button>
          )}
        </div>
      </div>

      {/* Info */}
      <p className="text-xs" style={{ color: 'var(--neo-text-dim)' }}>
        Defines what entities, relationships, and facts the LLM extracts during ingestion.
        Pick a schema from the library or customize inline.
      </p>

      {/* Library picker dropdown */}
      {pickerOpen && (
        <div className="rounded-lg p-3 space-y-2" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
          <div className="flex items-center gap-2">
            <Search size={12} style={{ color: 'var(--neo-text-dim)' }} />
            <input
              value={pickerSearch}
              onChange={e => setPickerSearch(e.target.value)}
              placeholder="Search schemas..."
              className="flex-1 text-xs outline-none"
              style={{ background: 'transparent', color: 'var(--neo-text)' }}
              autoFocus
            />
            <button onClick={() => setPickerOpen(false)}>
              <X size={12} style={{ color: 'var(--neo-text-dim)' }} />
            </button>
          </div>
          <div className="max-h-48 overflow-y-auto space-y-1">
            {filteredLibrary.map(s => (
              <button
                key={`${s.source}-${s.name}`}
                onClick={() => handlePickSchema(s)}
                disabled={saving}
                className="w-full text-left px-2.5 py-2 rounded-lg text-xs transition hover:bg-blue-500/5 flex items-center justify-between"
                style={{ color: 'var(--neo-text)' }}
              >
                <div>
                  <span className="font-medium">{s.name}</span>
                  <span className="ml-2" style={{ color: 'var(--neo-text-dim)' }}>
                    {s.node_count} nodes · {s.edge_count} edges
                  </span>
                  {s.tags?.length > 0 && (
                    <span className="ml-2" style={{ color: '#3b82f6' }}>{s.tags.join(', ')}</span>
                  )}
                </div>
                <span
                  className="px-1.5 py-0.5 rounded text-xs"
                  style={{
                    background: s.source === 'builtin' ? 'rgba(34,197,94,0.1)' : 'rgba(139,92,246,0.1)',
                    color: s.source === 'builtin' ? '#22c55e' : '#8b5cf6',
                  }}
                >
                  {s.source}
                </span>
              </button>
            ))}
            {filteredLibrary.length === 0 && (
              <p className="text-xs py-2 text-center" style={{ color: 'var(--neo-text-dim)' }}>No schemas found</p>
            )}
          </div>
        </div>
      )}

      {/* Current schema preview (read-only) */}
      {yamlText && !pickerOpen && (
        <SchemaBuilder
          initialYaml={yamlText}
          readOnly={true}
          showSaveBar={false}
        />
      )}
    </div>
  );
}

/* ─── Main Page ─── */

export default function ContextsPage() {
  const [contexts, setContexts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filterType, setFilterType] = useState('');
  const [filterTag, setFilterTag] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [expandedId, setExpandedId] = useState(null);
  const [expandedItems, setExpandedItems] = useState([]);
  const [loadingItems, setLoadingItems] = useState(false);
  const [itemSearch, setItemSearch] = useState('');
  const [searchResults, setSearchResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [showAllItems, setShowAllItems] = useState(false);

  // Briefing state
  const [briefing, setBriefing] = useState(null);
  const [briefingLoading, setBriefingLoading] = useState(false);

  // RAG state
  const [detailTab, setDetailTab] = useState('data'); // 'data' | 'explore' | 'ingest' | 'query' | 'config'
  const [ragQuery, setRagQuery] = useState('');
  const [ragMessages, setRagMessages] = useState([]);
  const [ragLoading, setRagLoading] = useState(false);

  // Global search across all contexts
  const [showGlobalSearch, setShowGlobalSearch] = useState(false);
  const [globalQuery, setGlobalQuery] = useState('');
  const [globalResults, setGlobalResults] = useState(null);
  const [globalSearching, setGlobalSearching] = useState(false);

  // Create form state
  const [newName, setNewName] = useState('');
  const [newType, setNewType] = useState('knowledge_base');
  const [newDesc, setNewDesc] = useState('');
  const [newSensitivity, setNewSensitivity] = useState('public');
  const [newTags, setNewTags] = useState([]);
  const [tagInput, setTagInput] = useState('');
  const [newSchema, setNewSchema] = useState('');
  const [availableSchemas, setAvailableSchemas] = useState([]);
  const [creating, setCreating] = useState(false);

  // Software Dev context fields
  // Software dev fields removed — all config comes from uploaded documents now

  // Link existing graph state
  const [linkGraph, setLinkGraph] = useState(false);
  const [unlinkedGraphs, setUnlinkedGraphs] = useState([]);
  const [graphSearch, setGraphSearch] = useState('');
  const [selectedGraph, setSelectedGraph] = useState(null);


  const fetchContexts = useCallback(async () => {
    setLoading(true);
    try {
      const params = {};
      if (filterType) params.context_type = filterType;
      if (searchQuery.trim()) params.search = searchQuery.trim();
      if (filterTag) params.tag = filterTag;
      const res = await api.get('/dashboard/contexts', { params });
      setContexts(res.data.contexts || []);
    } catch {} finally {
      setLoading(false);
    }
  }, [filterType, searchQuery, filterTag]);

  const loadItems = useCallback(async (contextId, all = false) => {
    setLoadingItems(true);
    setExpandedItems([]);
    try {
      const params = all ? { show_all: true } : {};
      const res = await api.get(`/dashboard/contexts/${contextId}/items`, { params });
      setExpandedItems(res.data.items || []);
    } catch {} finally {
      setLoadingItems(false);
    }
  }, []);

  useEffect(() => { fetchContexts(); }, []); // eslint-disable-line react-hooks/exhaustive-deps -- fire once on mount

  // Fetch available extraction schemas
  useEffect(() => {
    api.get('/dashboard/schemas').then(r => setAvailableSchemas(r.data.schemas || [])).catch(() => {});
  }, []);


  // Debounced refresh — prevents rapid re-fetches from multiple events
  const refreshTimer = useRef(null);
  const debouncedRefresh = useCallback(() => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(() => {
      fetchContexts();
      // Also refresh items if a context is expanded (so new ingested data shows up)
      if (expandedId) loadItems(expandedId);
    }, 2000); // At most once per 2 seconds
  }, [fetchContexts, expandedId, loadItems]);

  // Real-time: refresh when contexts/graphs change (debounced)
  useEvent('context_created', debouncedRefresh);
  useEvent('context_deleted', debouncedRefresh);
  useEvent('graph_deleted', debouncedRefresh);
  useEvent('context_updated', debouncedRefresh);
  useEvent('ingest_job_complete', debouncedRefresh);

  const loadBriefing = useCallback(async (contextId) => {
    setBriefingLoading(true);
    setBriefing(null);
    try {
      const res = await api.get(`/dashboard/contexts/${contextId}/briefing`);
      setBriefing(res.data);
    } catch {} finally {
      setBriefingLoading(false);
    }
  }, []);

  // Job context — used by ingest panel only, no auto-refresh of context list
  const { jobs: allJobs, jobsForContext } = useJobs();

  // Fetch unlinked graphs when link toggle is enabled
  useEffect(() => {
    if (!linkGraph) return;
    api.get('/dashboard/graphs/unlinked')
      .then((r) => setUnlinkedGraphs(r.data.graphs || []))
      .catch(() => setUnlinkedGraphs([]));
  }, [linkGraph]);


  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      const payload = {
        name: newName.trim(),
        context_type: newType,
        description: newDesc.trim(),
        sensitivity: newSensitivity,
      };
      if (newTags.length > 0) payload.tags = newTags;
      if (linkGraph && selectedGraph) payload.graph_namespace = selectedGraph.name;
      if (newSchema) payload.schema_name = newSchema;

      await api.post('/dashboard/contexts', payload);
      toast.success(`Context "${newName}" created`);
      setNewName(''); setNewDesc(''); setNewType('knowledge_base'); setNewSensitivity('public'); setNewSchema('');
      setNewTags([]); setTagInput('');
      setLinkGraph(false); setSelectedGraph(null); setGraphSearch('');
      setShowCreate(false);
      fetchContexts();
    } catch {} finally {
      setCreating(false);
    }
  };

  const handleDelete = async (contextId, name) => {
    if (!window.confirm(`Delete context "${name}"? This will also delete its graph data.`)) return;
    try {
      await api.delete(`/dashboard/contexts/${contextId}`);
      setContexts(prev => prev.filter(c => c.context_id !== contextId));
      if (expandedId === contextId) setExpandedId(null);
      toast.success('Context deleted');
    } catch (err) {
      const detail = err?.response?.data?.detail || 'Failed to delete context';
      const status = err?.response?.status;
      if (status === 409) {
        toast.error(detail);  // "Cannot delete context with N active ingestion jobs"
      } else {
        toast.error(detail);
      }
    }
  };

  const handleExpand = async (contextId) => {
    if (expandedId === contextId) {
      setExpandedId(null);
      return;
    }
    setExpandedId(contextId);
    setDetailTab('explore');
    setRagMessages([]);
    setRagQuery('');
    setShowAllItems(false);
    clearItemSearch();
    setBriefing(null);
    // Nothing auto-loads — user clicks Load when ready
  };

  const handleRefresh = async (contextId) => {
    try {
      await api.post(`/dashboard/contexts/${contextId}/refresh`);
      toast.success('Stats refreshed');
      fetchContexts();
    } catch {}
  };

  const handleIngested = useCallback(() => {
    fetchContexts();
  }, [fetchContexts]);

  const handleItemSearch = async (contextId) => {
    if (!itemSearch.trim()) {
      setSearchResults(null);
      return;
    }
    setSearching(true);
    try {
      const res = await api.post(`/dashboard/contexts/${contextId}/search`, {
        query: itemSearch.trim(), limit: 50,
      });
      setSearchResults(res.data.results || []);
    } catch { setSearchResults([]); }
    finally { setSearching(false); }
  };

  const clearItemSearch = () => {
    setItemSearch('');
    setSearchResults(null);
  };

  const handleGlobalSearch = async () => {
    if (!globalQuery.trim()) return;
    setGlobalSearching(true);
    try {
      // Search across all contexts
      const allResults = [];
      for (const ctx of contexts.slice(0, 10)) {
        try {
          const res = await api.post(`/dashboard/contexts/${ctx.context_id}/search`, {
            query: globalQuery.trim(), limit: 5,
          });
          const results = (res.data.results || []).map(r => ({
            ...r,
            _context_name: ctx.name,
            _context_type: ctx.context_type,
          }));
          allResults.push(...results);
        } catch {}
      }
      // Sort by score if available
      allResults.sort((a, b) => (b.score || 0) - (a.score || 0));
      setGlobalResults(allResults.slice(0, 20));
    } catch {
      setGlobalResults([]);
    } finally {
      setGlobalSearching(false);
    }
  };

  const handleRagAsk = async (contextId) => {
    const q = ragQuery.trim();
    if (!q) return;
    setRagMessages((prev) => [...prev, { role: 'user', content: q }]);
    setRagQuery('');
    setRagLoading(true);
    try {
      const res = await api.post(`/dashboard/contexts/${contextId}/rag`, {
        question: q, limit: 5,
      });
      setRagMessages((prev) => [...prev, {
        role: 'assistant',
        content: res.data.answer || 'No answer generated.',
        sources: res.data.sources || [],
      }]);
    } catch {
      setRagMessages((prev) => [...prev, {
        role: 'assistant',
        content: 'Failed to get answer. Check if an LLM provider is configured.',
        sources: [],
      }]);
    } finally { setRagLoading(false); }
  };

  // Aggregate stats
  const totalItems = contexts.reduce((sum, c) => sum + (c.item_count || 0), 0);
  const totalTokens = contexts.reduce((sum, c) => sum + (c.estimated_tokens || 0), 0);

  // Collect all unique tags across contexts
  const allTags = [...new Set(contexts.flatMap((c) => c.tags || []))].sort();

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      </div>
    );
  }

  return (
    <div className="max-w-4xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold" style={{ color: 'var(--neo-text)' }}>Contexts</h1>
          <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
            Atomic knowledge stores — each context holds one type of data. Attach to boundaries for agent work.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition hover:opacity-90"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            <Plus size={14} /> New Context
          </button>
          <button
            onClick={fetchContexts}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition hover:opacity-80"
            style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
          >
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-3 gap-3 mb-6">
        <div className="p-4 rounded-xl" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
          <div className="flex items-center gap-2 mb-1">
            <Database size={14} style={{ color: 'var(--neo-blue)' }} />
            <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Contexts</span>
          </div>
          <div className="text-2xl font-bold" style={{ color: 'var(--neo-text)' }}>{contexts.length}</div>
        </div>
        <div className="p-4 rounded-xl" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
          <div className="flex items-center gap-2 mb-1">
            <Layers size={14} style={{ color: 'var(--neo-cyan)' }} />
            <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Total Items</span>
          </div>
          <div className="text-2xl font-bold" style={{ color: 'var(--neo-text)' }}>{totalItems}</div>
        </div>
        <div className="p-4 rounded-xl" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
          <div className="flex items-center gap-2 mb-1">
            <FileText size={14} style={{ color: 'var(--neo-green)' }} />
            <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Est. Tokens</span>
          </div>
          <div className="text-2xl font-bold" style={{ color: 'var(--neo-text)' }}>
            {totalTokens > 1000 ? `${(totalTokens / 1000).toFixed(1)}k` : totalTokens}
          </div>
        </div>
      </div>

      {/* Search bar — filter contexts + search across all */}
      <div className="flex gap-2 mb-4">
        <div className="relative flex-1">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: 'var(--neo-text-muted)' }} />
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Filter contexts by name..."
            className="w-full pl-9 pr-3 py-2 rounded-lg text-sm outline-none"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery('')}
              className="absolute right-3 top-1/2 -translate-y-1/2"
              style={{ color: 'var(--neo-text-muted)' }}
            >
              <X size={14} />
            </button>
        )}
        </div>
        <button
          onClick={() => setShowGlobalSearch(!showGlobalSearch)}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm transition hover:opacity-80"
          style={{
            background: showGlobalSearch ? 'var(--neo-blue)' : 'var(--neo-surface)',
            color: showGlobalSearch ? '#fff' : 'var(--neo-text-muted)',
            border: `1px solid ${showGlobalSearch ? 'var(--neo-blue)' : 'var(--neo-border)'}`,
          }}
        >
          <MessageSquare size={14} /> Ask All
        </button>
      </div>

      {/* Global search across all contexts */}
      {showGlobalSearch && (
        <div className="mb-4 p-4 rounded-xl space-y-3"
             style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-blue)' }}>
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>Search across all contexts</span>
            <button onClick={() => { setShowGlobalSearch(false); setGlobalResults(null); }} style={{ color: 'var(--neo-text-muted)' }}><X size={14} /></button>
          </div>
          <div className="flex gap-2">
            <input
              value={globalQuery}
              onChange={(e) => setGlobalQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleGlobalSearch()}
              placeholder="Ask in plain English... e.g. 'What are the requirements about auth?'"
              className="flex-1 px-3 py-1.5 rounded-lg text-sm outline-none"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            />
            <button
              onClick={handleGlobalSearch}
              disabled={!globalQuery.trim() || globalSearching}
              className="px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50"
              style={{ background: 'var(--neo-blue)', color: '#fff' }}
            >
              {globalSearching ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
            </button>
          </div>
          {globalResults && (
            <div className="space-y-1 max-h-60 overflow-y-auto">
              {globalResults.length === 0 ? (
                <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>No results found.</p>
              ) : (
                globalResults.map((r, i) => (
                  <div key={i} className="px-2 py-1.5 rounded text-xs" style={{ background: 'var(--neo-bg)' }}>
                    <span className="font-medium" style={{ color: 'var(--neo-text)' }}>
                      {r.title || r.name || r.path || r.content?.slice(0, 60) || JSON.stringify(r).slice(0, 60)}
                    </span>
                    {r.label && <span className="ml-2 px-1 py-0.5 rounded" style={{ background: 'rgba(0,122,255,0.1)', color: 'var(--neo-blue)', fontSize: 10 }}>{r.label}</span>}
                    {r._context_name && <span className="ml-1 px-1 py-0.5 rounded" style={{ background: 'rgba(139,92,246,0.1)', color: '#8b5cf6', fontSize: 10 }}>{r._context_name}</span>}
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      )}

      {/* Filter bar */}
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <button
          onClick={() => setFilterType('')}
          className="px-2.5 py-1 rounded-lg text-xs font-medium transition"
          style={{
            background: !filterType ? 'var(--neo-blue)' : 'var(--neo-surface)',
            color: !filterType ? '#fff' : 'var(--neo-text-muted)',
            border: `1px solid ${!filterType ? 'var(--neo-blue)' : 'var(--neo-border)'}`,
          }}
        >
          All
        </button>
        {CONTEXT_TYPES.map((ct) => (
          <button
            key={ct.value}
            onClick={() => setFilterType(ct.value)}
            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium transition"
            style={{
              background: filterType === ct.value ? `${ct.color}20` : 'var(--neo-surface)',
              color: filterType === ct.value ? ct.color : 'var(--neo-text-muted)',
              border: `1px solid ${filterType === ct.value ? ct.color : 'var(--neo-border)'}`,
            }}
          >
            <ct.icon size={11} /> {ct.label}
          </button>
        ))}
      </div>

      {/* Tag filter */}
      {allTags.length > 0 && (
        <div className="flex items-center gap-1.5 mb-4 flex-wrap">
          <Tag size={12} style={{ color: 'var(--neo-text-muted)' }} />
          {filterTag && (
            <button
              onClick={() => setFilterTag('')}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium transition"
              style={{ background: 'var(--neo-blue)', color: '#fff' }}
            >
              {filterTag} <X size={10} />
            </button>
          )}
          {allTags.filter((t) => t !== filterTag).map((t) => (
            <button
              key={t}
              onClick={() => setFilterTag(t)}
              className="px-2 py-0.5 rounded-full text-xs transition hover:opacity-80"
              style={{ background: 'var(--neo-surface)', color: 'var(--neo-text-muted)', border: '1px solid var(--neo-border)' }}
            >
              {t}
            </button>
          ))}
        </div>
      )}

      {/* Create form */}
      {showCreate && (
        <div
          className="mb-4 p-4 rounded-xl space-y-3"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-blue)' }}
        >
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>New Context</span>
            <button onClick={() => setShowCreate(false)} style={{ color: 'var(--neo-text-muted)' }}><X size={14} /></button>
          </div>
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Context name..."
            autoFocus
            className="w-full px-3 py-1.5 rounded-lg text-sm outline-none"
            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          />
          <input
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
            placeholder="Description (optional)..."
            className="w-full px-3 py-1.5 rounded-lg text-sm outline-none"
            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          />
          {(() => {
            const typeInfo = CONTEXT_TYPES.find(ct => ct.value === newType);
            return typeInfo?.hint ? (
              <div className="text-xs px-1" style={{ color: typeInfo.color }}>
                {typeInfo.hint}
              </div>
            ) : null;
          })()}
          <div className="flex items-center gap-3">
            <select
              value={newType}
              onChange={(e) => setNewType(e.target.value)}
              className="px-3 py-1.5 rounded-lg text-sm outline-none"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            >
              {CONTEXT_TYPES.map((ct) => (
                <option key={ct.value} value={ct.value}>{ct.label}</option>
              ))}
            </select>
            <select
              value={newSensitivity}
              onChange={(e) => setNewSensitivity(e.target.value)}
              className="px-3 py-1.5 rounded-lg text-sm outline-none"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            >
              {SENSITIVITY_LEVELS.map((sl) => (
                <option key={sl.value} value={sl.value}>{sl.label}</option>
              ))}
            </select>
            <select
              value={newSchema}
              onChange={(e) => setNewSchema(e.target.value)}
              className="px-3 py-1.5 rounded-lg text-sm outline-none"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            >
              <option value="">No schema (open)</option>
              {availableSchemas.map((s) => (
                <option key={s.name || s} value={s.name || s}>{s.name || s}</option>
              ))}
            </select>
          </div>
          {newSchema && (
            <div className="text-xs px-1" style={{ color: '#8b5cf6' }}>
              Schema "{newSchema}" will validate node and edge types for this context.
            </div>
          )}
          {/* Tag input with industry presets */}
          <div>
            <div className="flex items-center gap-1 mb-2 flex-wrap">
              {['technology', 'healthcare', 'finance', 'education', 'legal', 'retail', 'manufacturing', 'media', 'research', 'government'].map(tag => (
                <button key={tag} onClick={() => { if (!newTags.includes(tag)) setNewTags([...newTags, tag]); }}
                  className="px-2 py-0.5 rounded-full text-[10px] transition hover:opacity-80"
                  style={{
                    background: newTags.includes(tag) ? 'rgba(88,86,214,0.2)' : 'var(--neo-bg)',
                    color: newTags.includes(tag) ? '#5856d6' : 'var(--neo-text-muted)',
                    border: `1px solid ${newTags.includes(tag) ? '#5856d6' : 'var(--neo-border)'}`,
                  }}>
                  {tag}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-1.5 flex-wrap">
              {newTags.map((t) => (
                <span
                  key={t}
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium"
                  style={{ background: 'rgba(88,86,214,0.12)', color: '#5856d6' }}
                >
                  <Hash size={10} /> {t}
                  <button onClick={() => setNewTags(newTags.filter((x) => x !== t))} style={{ color: '#5856d6' }}>
                    <X size={10} />
                  </button>
                </span>
              ))}
              <input
                value={tagInput}
                onChange={(e) => setTagInput(e.target.value)}
                onKeyDown={(e) => {
                  if ((e.key === 'Enter' || e.key === ',') && tagInput.trim()) {
                    e.preventDefault();
                    const val = tagInput.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '');
                    if (val && !newTags.includes(val)) setNewTags([...newTags, val]);
                    setTagInput('');
                  }
                }}
                placeholder={newTags.length > 0 ? 'Add more tags...' : 'Tags (press Enter to add)'}
                className="flex-1 px-2 py-1 rounded-lg text-xs outline-none min-w-[140px]"
                style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
              />
            </div>
          </div>
          {/* Extraction schema configured when adding pipeline — not here */}
          {/* Link existing graph toggle */}
          <div>
            <label className="flex items-center gap-2 cursor-pointer text-xs" style={{ color: 'var(--neo-text-muted)' }}>
              <input
                type="checkbox"
                checked={linkGraph}
                onChange={(e) => { setLinkGraph(e.target.checked); if (!e.target.checked) { setSelectedGraph(null); setGraphSearch(''); } }}
                className="rounded"
              />
              <Link size={12} /> Link to an existing graph
            </label>
            {linkGraph && (
              <div className="mt-2 space-y-2">
                <input
                  value={graphSearch}
                  onChange={(e) => setGraphSearch(e.target.value)}
                  placeholder="Search graphs..."
                  className="w-full px-3 py-1.5 rounded-lg text-xs outline-none"
                  style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                />
                <div
                  className="max-h-36 overflow-y-auto rounded-lg"
                  style={{ border: '1px solid var(--neo-border)' }}
                >
                  {unlinkedGraphs
                    .filter((g) => !graphSearch || g.display_name.toLowerCase().includes(graphSearch.toLowerCase()))
                    .map((g) => (
                      <button
                        key={g.name}
                        onClick={() => setSelectedGraph(selectedGraph?.name === g.name ? null : g)}
                        className="w-full flex items-center justify-between px-3 py-1.5 text-xs text-left transition-colors"
                        style={{
                          background: selectedGraph?.name === g.name ? 'var(--neo-blue-bg, rgba(59,130,246,0.1))' : 'transparent',
                          color: selectedGraph?.name === g.name ? 'var(--neo-blue)' : 'var(--neo-text)',
                          borderBottom: '1px solid var(--neo-border)',
                        }}
                      >
                        <span className="flex items-center gap-1.5">
                          <Database size={11} />
                          {g.display_name}
                        </span>
                        <span style={{ color: 'var(--neo-text-muted)' }}>
                          {g.node_count} nodes, {g.edge_count} edges
                        </span>
                      </button>
                    ))}
                  {unlinkedGraphs.filter((g) => !graphSearch || g.display_name.toLowerCase().includes(graphSearch.toLowerCase())).length === 0 && (
                    <div className="px-3 py-3 text-xs text-center" style={{ color: 'var(--neo-text-muted)' }}>
                      {unlinkedGraphs.length === 0 ? 'No unlinked graphs available' : 'No graphs match search'}
                    </div>
                  )}
                </div>
                {selectedGraph && (
                  <div className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--neo-blue)' }}>
                    <CheckCircle size={11} /> Linking to "{selectedGraph.display_name}" ({selectedGraph.node_count} nodes)
                  </div>
                )}
              </div>
            )}
          </div>
          <div className="flex justify-end">
            <button
              onClick={handleCreate}
              disabled={creating || !newName.trim()}
              className="px-4 py-1.5 rounded-lg text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
              style={{ background: 'var(--neo-blue)', color: '#fff' }}
            >
              {creating ? 'Creating...' : linkGraph && selectedGraph ? 'Create & Link' : 'Create'}
            </button>
          </div>
        </div>
      )}

      {/* Context list */}
      {contexts.length === 0 ? (
        <div
          className="text-center py-16 rounded-xl"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        >
          <Database size={32} className="mx-auto mb-3" style={{ color: 'var(--neo-text-muted)' }} />
          <p className="text-sm mb-2" style={{ color: 'var(--neo-text-muted)' }}>
            {filterType ? `No ${filterType.replace('_', ' ')} contexts yet.` : 'No contexts yet.'}
          </p>
          <p className="text-xs mb-4" style={{ color: 'var(--neo-text-muted)' }}>
            Create a context to start ingesting documents, URLs, and text.
          </p>
          <button
            onClick={() => setShowCreate(true)}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            <Plus size={14} /> New Context
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          {contexts.map((ctx) => {
            const isExpanded = expandedId === ctx.context_id;
            return (
              <div key={ctx.context_id}>
                <button
                  onClick={() => handleExpand(ctx.context_id)}
                  className="w-full text-left p-4 rounded-xl transition hover:scale-[1.005]"
                  style={{ background: 'var(--neo-surface)', border: `1px solid ${isExpanded ? 'var(--neo-blue)' : 'var(--neo-border)'}` }}
                >
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <Database size={14} style={{ color: '#af52de' }} />
                      <span className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>{ctx.name}</span>
                      <TypeBadge type={ctx.context_type} />
                      <SensitivityBadge sensitivity={ctx.sensitivity} />
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                        {ctx.item_count} items
                      </span>
                      <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                        ~{ctx.estimated_tokens > 1000 ? `${(ctx.estimated_tokens / 1000).toFixed(1)}k` : ctx.estimated_tokens} tokens
                      </span>
                      {ctx.embedding_model && (
                        <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'rgba(88,86,214,0.1)', color: '#5856d6' }}>
                          {ctx.embedding_model}{ctx.embedding_dimension ? ` (${ctx.embedding_dimension}d)` : ''}
                        </span>
                      )}
                      {ctx.updated_at && (() => {
                        const d = new Date(ctx.updated_at);
                        const mo = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getUTCMonth()];
                        return (
                          <span className="text-xs flex items-center gap-1" style={{ color: 'var(--neo-text-dim)' }}>
                            <Clock size={10} /> {`${mo} ${d.getUTCDate()}, ${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')} UTC`}
                          </span>
                        );
                      })()}
                      {ctx.session_count > 0 && (
                        <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'rgba(0,122,255,0.1)', color: 'var(--neo-blue)' }}>
                          {ctx.session_count} session{ctx.session_count !== 1 ? 's' : ''}
                        </span>
                      )}
                      {isExpanded ? <ChevronUp size={14} style={{ color: 'var(--neo-text-muted)' }} /> : <ChevronDown size={14} style={{ color: 'var(--neo-text-muted)' }} />}
                    </div>
                  </div>
                  {ctx.description && (
                    <p className="text-xs ml-5" style={{ color: 'var(--neo-text-muted)' }}>{ctx.description}</p>
                  )}
                  {(ctx.tags?.length > 0 || ctx.config?.source_policy || ctx.config?.extraction_schema || ctx.source?.startsWith('web:')) && (
                    <div className="flex items-center gap-1 ml-5 mt-1 flex-wrap">
                      {ctx.tags?.map((t) => (
                        <span
                          key={t}
                          onClick={(e) => { e.stopPropagation(); setFilterTag(t); }}
                          className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-xs cursor-pointer hover:opacity-80"
                          style={{ background: 'rgba(88,86,214,0.1)', color: '#5856d6' }}
                        >
                          <Hash size={9} /> {t}
                        </span>
                      ))}
                      {ctx.config?.extraction_schema && (
                        <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[10px]"
                          style={{ background: 'rgba(16,185,129,0.1)', color: '#10b981' }}>
                          📋 {ctx.config.extraction_schema}
                        </span>
                      )}
                      {(ctx.config?.source_policy?.allowed_domains?.length > 0 || ctx.source?.startsWith('web:')) && (
                        <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[10px]"
                          style={{ background: 'rgba(239,68,68,0.1)', color: '#ef4444' }}>
                          🔒 {ctx.config?.source_policy?.allowed_domains?.[0] || ctx.source?.replace('web:', '')}
                        </span>
                      )}
                    </div>
                  )}
                  {ctx.source && ctx.source !== 'manual' && (
                    <div className="flex items-center gap-1.5 ml-5 mt-1">
                      {ctx.source.startsWith('connector:') ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px]"
                          style={{ background: 'rgba(139,92,246,0.1)', color: '#8b5cf6' }}>
                          <Database size={9} />
                          {ctx.source.split(':')[1] || 'connector'}
                        </span>
                      ) : ctx.source.startsWith('web:') ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px]"
                          style={{ background: 'rgba(6,182,212,0.1)', color: '#06b6d4' }}>
                          <Globe size={9} />
                          {ctx.source.replace('web:', '').split('/')[2] || 'web'}
                        </span>
                      ) : ctx.source.startsWith('document:') ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px]"
                          style={{ background: 'rgba(59,130,246,0.1)', color: '#3b82f6' }}>
                          <FileText size={9} />
                          {ctx.source.replace('document:', '')}
                        </span>
                      ) : (
                        <span className="text-xs" style={{ color: 'var(--neo-text-dim)' }}>
                          {ctx.source}
                        </span>
                      )}
                    </div>
                  )}
                </button>

                {/* Expanded detail view */}
                {isExpanded && (
                  <div
                    className="mx-2 p-4 rounded-b-xl space-y-4"
                    style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', borderTop: 'none' }}
                  >
                    {/* Action bar */}
                    <div className="flex items-center gap-2">
                      <button
                        onClick={(e) => { e.stopPropagation(); handleRefresh(ctx.context_id); }}
                        className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs transition hover:opacity-80"
                        style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
                      >
                        <RefreshCw size={11} /> Refresh Stats
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); handleDelete(ctx.context_id, ctx.name); }}
                        className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs transition hover:opacity-80"
                        style={{ border: '1px solid #ef4444', color: '#ef4444' }}
                      >
                        <Trash2 size={11} /> Delete
                      </button>
                      <span className="ml-auto text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                        Graph: {ctx.graph_namespace}
                      </span>
                    </div>

                    {/* Tab switcher */}
                    <div className="flex gap-1 mb-2" style={{ borderBottom: '1px solid var(--neo-border)' }}>
                      {[
                        { id: 'explore', label: 'Explore', icon: Layers, color: '#10b981' },
                        { id: 'data', label: 'Data', icon: Search, color: 'var(--neo-blue)' },
                        { id: 'intelligence', label: 'Intelligence', icon: Brain, color: '#ec4899' },
                        { id: 'context_units', label: 'Context Units', icon: Cpu, color: '#f59e0b' },
                        { id: 'ingest', label: 'Ingest', icon: Upload, color: 'var(--neo-blue)' },
                        { id: 'query', label: 'Query', icon: MessageSquare, color: 'var(--neo-blue)' },
                        { id: 'config', label: 'Config', icon: Settings, color: '#8b5cf6' },
                      ].map((t) => {
                        const Icon = t.icon;
                        return (
                          <button
                            key={t.id}
                            onClick={() => {
                              setDetailTab(t.id);
                            }}
                            className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-t"
                            style={{
                              color: detailTab === t.id ? t.color : 'var(--neo-text-muted)',
                              borderBottom: detailTab === t.id ? `2px solid ${t.color}` : '2px solid transparent',
                            }}
                          >
                            <Icon size={12} />
                            {t.label}
                          </button>
                        );
                      })}
                    </div>

                    {/* Explore tab — Graph briefing + visualization */}
                    {detailTab === 'explore' && (
                      <div>
                        {/* Graph briefing summary */}
                        {briefing && briefing.node_count > 0 && (
                          <div className="p-3 rounded-lg mb-3" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
                            <div className="flex items-center gap-2 mb-2">
                              <Layers size={13} style={{ color: 'var(--neo-blue)' }} />
                              <span className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>
                                Knowledge Graph — {briefing.node_count} nodes, {briefing.edge_count} edges
                              </span>
                            </div>
                            <div className="flex flex-wrap gap-1.5 mb-2">
                              {Object.entries(briefing.node_types || {})
                                .filter(([t]) => !['Context','KnowledgeBase','CodeBase','SystemStore','UserStore','WebStore','GeneratedStore','MemoryStore','ArtifactStore','ToolStore','PipelineRun'].includes(t))
                                .map(([type, count]) => (
                                  <span key={type} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs"
                                    style={{ background: 'rgba(59,130,246,0.1)', color: 'var(--neo-blue)' }}>
                                    {type} <strong>{count}</strong>
                                  </span>
                                ))}
                            </div>
                            {briefing.key_entities?.length > 0 && (
                              <div className="flex flex-wrap gap-1">
                                {briefing.key_entities.filter(e => e.type !== 'Fact').slice(0, 12).map((e, i) => (
                                  <span key={i} className="px-1.5 py-0.5 rounded text-xs"
                                    style={{ background: 'rgba(139,92,246,0.1)', color: '#8b5cf6' }}>
                                    {e.name}
                                  </span>
                                ))}
                              </div>
                            )}
                          </div>
                        )}
                        {briefingLoading && (
                          <div className="flex items-center gap-2 py-2">
                            <Loader2 size={14} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
                            <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Loading graph summary...</span>
                          </div>
                        )}
                        {!briefing && !briefingLoading && (
                          <div className="flex flex-col items-center justify-center py-6">
                            <p className="text-xs mb-2" style={{ color: 'var(--neo-text-muted)' }}>
                              Graph explorer loads on demand.
                            </p>
                            <button onClick={() => loadBriefing(ctx.context_id)}
                              className="px-3 py-1.5 rounded-lg text-xs font-medium"
                              style={{ background: 'var(--neo-blue)', color: '#fff' }}>
                              Load Explorer
                            </button>
                          </div>
                        )}
                        {/* Graph visualization — only after briefing is loaded */}
                        {briefing && (
                          <div style={{ height: 500 }}>
                            <DashboardGraphExplorer graphName={ctx.graph_namespace} embedded />
                          </div>
                        )}
                      </div>
                    )}

                    {/* Data tab — Items list with search + provenance */}
                    {detailTab === 'data' && (
                      <div>
                        <div className="flex items-center gap-1 mb-2">
                          <input
                            type="text"
                            value={itemSearch}
                            onChange={(e) => setItemSearch(e.target.value)}
                            onKeyDown={(e) => e.key === 'Enter' && handleItemSearch(ctx.context_id)}
                            placeholder={`Search ${CONTEXT_TYPES.find(ct => ct.value === (_OLD_TYPE_MAP[ctx.context_type] || ctx.context_type))?.label || 'items'}...`}
                            className="flex-1 px-2 py-1 rounded text-xs outline-none"
                            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                          />
                          <button
                            onClick={() => handleItemSearch(ctx.context_id)}
                            disabled={searching}
                            className="p-1 rounded"
                            style={{ color: 'var(--neo-blue)' }}
                          >
                            {searching ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
                          </button>
                          {searchResults && (
                            <button onClick={clearItemSearch} className="p-1 rounded" style={{ color: 'var(--neo-text-muted)' }}>
                              <X size={14} />
                            </button>
                          )}
                          <button
                            onClick={() => {
                              const next = !showAllItems;
                              setShowAllItems(next);
                              loadItems(ctx.context_id, next);
                            }}
                            className="px-2 py-1 rounded text-xs font-medium transition"
                            style={{
                              background: showAllItems ? 'rgba(139,92,246,0.15)' : 'transparent',
                              color: showAllItems ? '#8b5cf6' : 'var(--neo-text-muted)',
                              border: `1px solid ${showAllItems ? '#8b5cf6' : 'var(--neo-border)'}`,
                            }}
                            title={showAllItems ? 'Showing all node types' : 'Showing only relevant node types — click to show all'}
                          >
                            {showAllItems ? 'All Types' : 'Filtered'}
                          </button>
                        </div>
                        {loadingItems ? (
                          <div className="flex items-center justify-center py-4">
                            <Loader2 size={16} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
                          </div>
                        ) : !expandedItems.length && !searchResults ? (
                          <div className="flex flex-col items-center justify-center py-6">
                            <p className="text-xs mb-2" style={{ color: 'var(--neo-text-muted)' }}>
                              Items are loaded on demand to keep the page fast.
                            </p>
                            <button onClick={() => loadItems(ctx.context_id, false)}
                              className="px-3 py-1.5 rounded-lg text-xs font-medium"
                              style={{ background: 'var(--neo-blue)', color: '#fff' }}>
                              Load Items
                            </button>
                          </div>
                        ) : (searchResults || expandedItems).length === 0 ? (
                          <p className="text-xs py-3" style={{ color: 'var(--neo-text-muted)' }}>
                            {searchResults ? 'No results found.' : 'No items yet. Use the ingest panel above to add data.'}
                          </p>
                        ) : (
                          <div className="space-y-2 max-h-[600px] overflow-y-auto">
                            {(searchResults || expandedItems).map((item, i) => (
                              <ChunkCard key={item.node_id || i} item={item} contextId={ctx.context_id} />
                            ))}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Ingest tab — pipeline configuration */}
                    {detailTab === 'ingest' && (
                      <div className="space-y-4">
                        {/* Active Collection Pipelines */}
                        {ctx.config?.pipelines && ctx.config.pipelines.length > 0 && (
                          <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)' }}>
                            <h4 className="text-xs font-semibold mb-3 flex items-center gap-2" style={{ color: 'var(--neo-text)' }}>
                              <Rss size={14} style={{ color: '#10b981' }} /> Active Collection Pipelines
                              <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: '#10b98120', color: '#10b981' }}>
                                {ctx.config.pipelines.filter(p => p.source_type !== 'upload').length} auto-collecting
                              </span>
                            </h4>
                            <div className="space-y-2">
                              {ctx.config.pipelines.map((pipe, pi) => (
                                <div key={pi} className="p-2.5 rounded flex items-start gap-3" style={{ background: 'var(--neo-bg-secondary)', border: '1px solid var(--neo-border)' }}>
                                  <div className="flex-shrink-0 mt-0.5">
                                    {pipe.source_type === 'rss' ? <Rss size={14} style={{ color: '#3b82f6' }} /> :
                                     pipe.source_type === 'upload' ? <Upload size={14} style={{ color: '#6b7280' }} /> :
                                     <Globe size={14} style={{ color: '#8b5cf6' }} />}
                                  </div>
                                  <div className="flex-1 min-w-0">
                                    <div className="flex items-center gap-2 mb-1">
                                      <span className="text-xs font-semibold" style={{ color: 'var(--neo-text)' }}>{pipe.name}</span>
                                      <span className="text-[9px] px-1.5 py-0.5 rounded font-medium" style={{
                                        background: pipe.source_type === 'rss' ? '#3b82f620' : pipe.source_type === 'upload' ? '#6b728020' : '#8b5cf620',
                                        color: pipe.source_type === 'rss' ? '#3b82f6' : pipe.source_type === 'upload' ? '#6b7280' : '#8b5cf6',
                                      }}>{pipe.source_type.toUpperCase()}</span>
                                      <span className="text-[9px] px-1.5 py-0.5 rounded" style={{ background: '#10b98120', color: '#10b981' }}>
                                        every {pipe.interval}
                                      </span>
                                      {pipe.strategy && <span className="text-[9px]" style={{ color: 'var(--neo-text-muted)' }}>{pipe.strategy}</span>}
                                    </div>
                                    {pipe.sources && pipe.sources.length > 0 && (
                                      <div className="mb-1.5">
                                        {pipe.sources.map((src, si) => (
                                          <div key={si} className="text-[10px] truncate flex items-center gap-1" style={{ color: 'var(--neo-text-muted)' }}>
                                            <Link size={8} /> {src}
                                          </div>
                                        ))}
                                      </div>
                                    )}
                                    {pipe.filter && (
                                      <div className="flex gap-1 flex-wrap">
                                        {(pipe.filter.topics || []).map((t, ti) => (
                                          <span key={ti} className="text-[9px] px-1.5 py-0.5 rounded" style={{ background: '#ec489920', color: '#ec4899' }}>
                                            {t}
                                          </span>
                                        ))}
                                        {(pipe.filter.exclude || []).map((t, ti) => (
                                          <span key={ti} className="text-[9px] px-1.5 py-0.5 rounded" style={{ background: '#ef444420', color: '#ef4444' }}>
                                            -{t}
                                          </span>
                                        ))}
                                      </div>
                                    )}
                                  </div>
                                </div>
                              ))}
                            </div>
                            {ctx.config.schedule && (
                              <div className="mt-2 pt-2 border-t flex items-center gap-2 text-[10px]" style={{ borderColor: 'var(--neo-border)', color: 'var(--neo-text-muted)' }}>
                                <Clock size={10} />
                                <span>Scheduler: {ctx.config.schedule.status === 'active' ? '🟢 Active' : '⚪ Inactive'}</span>
                                <span>| Check every {ctx.config.schedule.interval_minutes || 30} min</span>
                              </div>
                            )}
                          </div>
                        )}

                        {/* Manual ingestion form */}
                        <PipelineConfigPanel context={ctx} onUpdate={fetchContexts} />
                      </div>
                    )}

                    {/* Query tab — AIQL + RAG chat */}
                    {detailTab === 'query' && (
                      <div>
                        {/* Chat messages */}
                        <div className="space-y-3 max-h-[500px] overflow-y-auto mb-3 min-h-[80px]">
                          {ragMessages.length === 0 && (
                            <p className="text-xs py-6 text-center" style={{ color: 'var(--neo-text-muted)' }}>
                              Ask questions about the data in this context.
                            </p>
                          )}
                          {ragMessages.map((msg, i) => (
                            <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                              <div
                                className="rounded-lg px-3 py-2 text-xs max-w-[85%]"
                                style={{
                                  background: msg.role === 'user' ? 'var(--neo-blue)' : 'var(--neo-surface)',
                                  color: msg.role === 'user' ? '#fff' : 'var(--neo-text)',
                                  border: msg.role === 'user' ? 'none' : '1px solid var(--neo-border)',
                                }}
                              >
                                <div style={{ whiteSpace: 'pre-wrap' }}>{typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content)}</div>
                                {msg.sources && msg.sources.length > 0 && (
                                  <div className="mt-2 pt-2" style={{ borderTop: '1px solid var(--neo-border)' }}>
                                    <span className="text-[10px] font-medium" style={{ color: 'var(--neo-text-muted)' }}>
                                      Sources ({msg.sources.length}):
                                    </span>
                                    {msg.sources.map((s, j) => (
                                      <div key={j} className="text-[10px] mt-0.5 truncate" style={{ color: 'var(--neo-text-muted)' }}>
                                        {s.label || 'Node'}: {s.snippet || s.name || s.node_id}
                                        {s.score != null && <span className="ml-1 opacity-60">({Math.round(s.score * 100)}%)</span>}
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>
                            </div>
                          ))}
                          {ragLoading && (
                            <div className="flex justify-start">
                              <div className="rounded-lg px-3 py-2" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
                                <Loader2 size={14} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
                              </div>
                            </div>
                          )}
                        </div>
                        {/* Input */}
                        <div className="flex items-center gap-1">
                          <input
                            type="text"
                            value={ragQuery}
                            onChange={(e) => setRagQuery(e.target.value)}
                            onKeyDown={(e) => e.key === 'Enter' && !ragLoading && handleRagAsk(ctx.context_id)}
                            placeholder="Ask a question about this context..."
                            className="flex-1 px-2 py-1.5 rounded text-xs outline-none"
                            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                          />
                          <button
                            onClick={() => handleRagAsk(ctx.context_id)}
                            disabled={ragLoading || !ragQuery.trim()}
                            className="p-1.5 rounded"
                            style={{ color: ragQuery.trim() ? 'var(--neo-blue)' : 'var(--neo-text-muted)' }}
                          >
                            <Send size={14} />
                          </button>
                        </div>
                      </div>
                    )}

                    {/* Intelligence tab */}
                    {detailTab === 'intelligence' && (
                      <IntelligencePanel graphName={ctx.graph_namespace} contextName={ctx.name} />
                    )}

                    {/* Context Units tab */}
                    {detailTab === 'context_units' && (
                      <ContextUnitsPanel graphName={ctx.graph_namespace} />
                    )}

                    {/* Config tab — Schema + Jobs */}
                    {detailTab === 'config' && (
                      <div className="space-y-4">
                        {/* Pipeline Configuration */}
                        {ctx.config?.pipelines && ctx.config.pipelines.length > 0 && (
                          <div className="p-3 rounded-lg" style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)' }}>
                            <h4 className="text-xs font-semibold mb-3 flex items-center gap-2" style={{ color: 'var(--neo-text)' }}>
                              <Rss size={14} style={{ color: 'var(--neo-blue)' }} /> Collection Pipelines ({ctx.config.pipelines.length})
                            </h4>
                            <div className="space-y-2">
                              {ctx.config.pipelines.map((pipe, pi) => (
                                <div key={pi} className="p-2.5 rounded" style={{ background: 'var(--neo-bg-secondary)', border: '1px solid var(--neo-border)' }}>
                                  <div className="flex items-center gap-2 mb-1">
                                    <span className="text-xs font-semibold" style={{ color: 'var(--neo-text)' }}>{pipe.name}</span>
                                    <span className="text-[10px] px-1.5 py-0.5 rounded" style={{
                                      background: pipe.source_type === 'rss' ? '#3b82f620' : pipe.source_type === 'crawl' ? '#8b5cf620' : pipe.source_type === 'api' ? '#f59e0b20' : '#6b728020',
                                      color: pipe.source_type === 'rss' ? '#3b82f6' : pipe.source_type === 'crawl' ? '#8b5cf6' : pipe.source_type === 'api' ? '#f59e0b' : '#6b7280',
                                    }}>{pipe.source_type}</span>
                                    <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: '#10b98120', color: '#10b981' }}>{pipe.interval}</span>
                                    {pipe.strategy && <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>{pipe.strategy}</span>}
                                  </div>
                                  {pipe.sources && pipe.sources.length > 0 && (
                                    <div className="mb-1">
                                      {pipe.sources.map((src, si) => (
                                        <div key={si} className="text-[10px] truncate" style={{ color: 'var(--neo-text-muted)' }}>
                                          {src}
                                        </div>
                                      ))}
                                    </div>
                                  )}
                                  {pipe.filter && (
                                    <div className="flex gap-1 flex-wrap">
                                      {(pipe.filter.topics || []).map((t, ti) => (
                                        <span key={ti} className="text-[9px] px-1.5 py-0.5 rounded" style={{ background: '#ec489920', color: '#ec4899' }}>{t}</span>
                                      ))}
                                      {(pipe.filter.exclude || []).map((t, ti) => (
                                        <span key={ti} className="text-[9px] px-1.5 py-0.5 rounded" style={{ background: '#ef444420', color: '#ef4444' }}>-{t}</span>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              ))}
                            </div>
                            {ctx.config.schedule && (
                              <div className="mt-2 text-[10px] flex items-center gap-2" style={{ color: 'var(--neo-text-muted)' }}>
                                <Clock size={10} />
                                Schedule: {ctx.config.schedule.trigger_type} | Status: {ctx.config.schedule.status} | Interval: {ctx.config.schedule.interval_minutes}min
                              </div>
                            )}
                          </div>
                        )}

                        <SchemaEditor contextId={ctx.context_id} contextType={ctx.context_type} />
                        <div style={{ borderTop: '1px solid var(--neo-border)', paddingTop: 16 }}>
                          <h4 className="text-xs font-medium mb-2" style={{ color: 'var(--neo-text-muted)' }}>Ingestion Jobs</h4>
                          {(jobsForContext(ctx.context_id) || []).length === 0 ? (
                            <p className="text-xs py-4 text-center" style={{ color: 'var(--neo-text-muted)' }}>
                              No ingestion jobs yet. Use the Ingest tab to add data.
                            </p>
                          ) : (
                            <div className="space-y-2">
                              {(jobsForContext(ctx.context_id) || []).map((job) => (
                                <div
                                  key={job.job_id}
                                  className="rounded-lg p-3"
                                  style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
                                >
                                  {/* Job header */}
                                  <div className="flex items-center justify-between mb-2">
                                    <div className="flex items-center gap-2">
                                      {job.status === 'processing' ? (
                                        <Loader2 size={14} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
                                      ) : job.status === 'completed' ? (
                                        <CheckCircle size={14} style={{ color: '#22c55e' }} />
                                      ) : job.status === 'completed_empty' ? (
                                        <CheckCircle size={14} style={{ color: '#f59e0b' }} />
                                      ) : (
                                        <XCircle size={14} style={{ color: '#ef4444' }} />
                                      )}
                                      <span className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>
                                        {job.source}
                                      </span>
                                      <span
                                        className="text-[10px] px-1.5 py-0.5 rounded"
                                        style={{
                                          background: job.status === 'processing' ? 'var(--neo-blue)' + '20'
                                            : job.status === 'completed' ? '#22c55e20'
                                            : job.status === 'completed_empty' ? '#f59e0b20'
                                            : '#ef444420',
                                          color: job.status === 'processing' ? 'var(--neo-blue)'
                                            : job.status === 'completed' ? '#22c55e'
                                            : job.status === 'completed_empty' ? '#f59e0b'
                                            : '#ef4444',
                                        }}
                                      >
                                        {job.status === 'completed_empty' ? 'empty' : job.status}
                                      </span>
                                    </div>
                                    <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                                      {job.job_id} &middot; {new Date(job.created_at).toLocaleString()}
                                    </span>
                                  </div>

                                  {/* Models */}
                                  {(job.llm_model || job.embedding_model) && (
                                    <div className="flex gap-3 mb-2 text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                                      {job.llm_model && <span>LLM: <strong style={{ color: 'var(--neo-text)' }}>{job.llm_model}</strong></span>}
                                      {job.embedding_model && <span>Embedding: <strong style={{ color: 'var(--neo-text)' }}>{job.embedding_model}</strong></span>}
                                    </div>
                                  )}

                                  {/* Stage pipeline progress */}
                                  {job.stages && Object.keys(job.stages).length > 0 && (
                                    <div className="flex items-center gap-1 flex-wrap mb-2">
                                      {Object.entries(job.stages).map(([stage, info], idx, arr) => (
                                        <React.Fragment key={stage}>
                                          <span
                                            className="flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px]"
                                            title={`${STAGE_TOOLTIPS[stage] || stage}\nStatus: ${info.status}${info.count ? `\nItems: ${info.count}` : ''}`}
                                            style={{
                                              background: info.status === 'completed' ? '#22c55e15'
                                                : info.status === 'running' ? 'var(--neo-blue)' + '15'
                                                : info.status === 'failed' ? '#ef444415'
                                                : 'var(--neo-bg)',
                                              color: info.status === 'completed' ? '#22c55e'
                                                : info.status === 'running' ? 'var(--neo-blue)'
                                                : info.status === 'failed' ? '#ef4444'
                                                : 'var(--neo-text-muted)',
                                              border: `1px solid ${info.status === 'completed' ? '#22c55e30'
                                                : info.status === 'running' ? 'var(--neo-blue)' + '30'
                                                : info.status === 'failed' ? '#ef444430'
                                                : 'var(--neo-border)'}`,
                                            }}
                                          >
                                            {STAGE_ICONS[info.status] || STAGE_ICONS.pending}
                                            {STAGE_LABELS[stage] || stage}
                                            {info.count > 0 && <span className="ml-0.5 opacity-70">({info.count})</span>}
                                          </span>
                                          {idx < arr.length - 1 && (
                                            <span className="text-[10px]" style={{ color: 'var(--neo-border)' }}>&rarr;</span>
                                          )}
                                        </React.Fragment>
                                      ))}
                                    </div>
                                  )}

                                  {/* Results summary */}
                                  {job.status !== 'processing' && (
                                    <div className="flex gap-4 text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                                      {job.nodes_created > 0 && <span>Nodes: <strong style={{ color: 'var(--neo-text)' }}>{job.nodes_created}</strong></span>}
                                      {job.edges_created > 0 && <span>Edges: <strong style={{ color: 'var(--neo-text)' }}>{job.edges_created}</strong></span>}
                                      {job.entities_extracted > 0 && <span>Entities: <strong style={{ color: 'var(--neo-text)' }}>{job.entities_extracted}</strong></span>}
                                    </div>
                                  )}

                                  {/* Error */}
                                  {job.error && (
                                    <div className="mt-1 text-[10px] p-1.5 rounded" style={{ background: '#ef444410', color: '#ef4444', border: '1px solid #ef444420' }}>
                                      {typeof job.error === 'string' ? job.error : JSON.stringify(job.error)}
                                    </div>
                                  )}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                    )}

                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

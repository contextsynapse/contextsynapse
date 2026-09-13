/**
 * StepWisePipelinePanel — Interactive step-by-step pipeline execution UI.
 *
 * Shows a stage timeline, context viewer, data editor, and control buttons.
 * Used in both ContextsPage and GraphsPage for step-wise ingestion.
 *
 * Props:
 *   graphName   — target graph name
 *   pipelineId  — selected pipeline template ID
 *   intent      — "build_graph" | "graph_rag" | "search_only"
 *   file        — File object (optional)
 *   text        — text string (optional)
 *   url         — URL string (optional)
 *   onComplete  — callback when pipeline finishes
 *   onCancel    — callback when user cancels
 */

import React, { useState, useCallback, useEffect, useRef } from 'react';
import {
  Loader2, CheckCircle, XCircle, Clock, Play, SkipForward,
  FastForward, X, Edit3, Eye, ChevronDown, ChevronUp,
  FileText, Layers, Database, Zap, Filter, Save, AlertTriangle,
} from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../lib/api';

const STAGE_META = {
  PARSE_FILE:      { label: 'Parse File',       icon: FileText,   color: '#3b82f6' },
  CLASSIFY:        { label: 'Classify',          icon: Filter,     color: '#8b5cf6' },
  CHUNK:           { label: 'Chunk Text',        icon: Layers,     color: '#06b6d4' },
  DEDUP:           { label: 'Deduplicate',       icon: Filter,     color: '#0d9488' },
  MAP_TABULAR:     { label: 'Map Tabular',       icon: Database,   color: '#22c55e' },
  EXTRACT:         { label: 'LLM Extract',       icon: Zap,        color: '#f59e0b' },
  EMBED:           { label: 'Embed',             icon: Zap,        color: '#ec4899' },
  CANONICALIZE:    { label: 'Deduplicate',       icon: Filter,     color: '#14b8a6' },
  ENHANCE_GRAPH:   { label: 'Enhance',           icon: Zap,        color: '#a855f7' },
  VALIDATE_SCHEMA: { label: 'Validate Schema',   icon: CheckCircle,color: '#0ea5e9' },
  EXTRACT_FACTS:   { label: 'Extract Facts',     icon: Zap,        color: '#f97316' },
  INDEX_BM25:      { label: 'BM25 Index',        icon: Database,   color: '#0d9488' },
  STORE_VECTORS:   { label: 'Store Vectors',     icon: Database,   color: '#7c3aed' },
  PERSIST:         { label: 'Save to Graph',     icon: Save,       color: '#22c55e' },
  IMPORT_GRAPH:    { label: 'Import Graph',      icon: Database,   color: '#6366f1' },
};

function StageBadge({ stage, status }) {
  const meta = STAGE_META[stage] || { label: stage, icon: Layers, color: '#6b7280' };
  const Icon = meta.icon;
  const statusIcons = {
    pending:   <Clock size={10} style={{ color: 'var(--neo-text-dim)' }} />,
    running:   <Loader2 size={10} className="animate-spin" style={{ color: meta.color }} />,
    completed: <CheckCircle size={10} style={{ color: '#22c55e' }} />,
    failed:    <XCircle size={10} style={{ color: '#ef4444' }} />,
    skipped:   <SkipForward size={10} style={{ color: 'var(--neo-text-muted)' }} />,
  };

  return (
    <div
      className="flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs"
      style={{
        background: status === 'running' ? `${meta.color}15` : 'var(--neo-surface)',
        border: `1px solid ${status === 'running' ? meta.color : 'var(--neo-border)'}`,
        opacity: status === 'skipped' ? 0.5 : 1,
      }}
    >
      <Icon size={11} style={{ color: meta.color }} />
      <span style={{ color: 'var(--neo-text)' }}>{meta.label}</span>
      {statusIcons[status] || statusIcons.pending}
    </div>
  );
}

function DataPreview({ label, data, maxItems = 10 }) {
  const [expanded, setExpanded] = useState(false);
  if (!data || data.length === 0) return null;

  return (
    <div className="space-y-1">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1 text-xs font-medium"
        style={{ color: 'var(--neo-text-muted)', background: 'none', border: 'none', cursor: 'pointer' }}
      >
        {expanded ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
        {label} ({data.length})
      </button>
      {expanded && (
        <div
          className="rounded-lg p-2 space-y-1 text-xs overflow-auto"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', maxHeight: 200 }}
        >
          {data.slice(0, maxItems).map((item, i) => (
            <div key={i} className="flex items-center gap-2 py-0.5" style={{ borderBottom: '1px solid var(--neo-border)' }}>
              <span className="font-mono" style={{ color: 'var(--neo-blue)', minWidth: 60 }}>
                {item.id ? item.id.slice(0, 10) : `#${i}`}
              </span>
              <span
                className="px-1.5 py-0.5 rounded text-xs"
                style={{ background: 'rgba(88,86,214,0.1)', color: '#5856d6' }}
              >
                {item.label || item.type || ''}
              </span>
              <span className="truncate flex-1" style={{ color: 'var(--neo-text-muted)' }}>
                {item.properties?.name || item.properties?.content?.slice(0, 60) || item.source || item.content?.slice(0, 60) || JSON.stringify(item.properties || {}).slice(0, 60)}
              </span>
            </div>
          ))}
          {data.length > maxItems && (
            <div className="text-center text-xs py-1" style={{ color: 'var(--neo-text-dim)' }}>
              +{data.length - maxItems} more
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const STORAGE_KEY = 'qgraph_active_pipeline_run';

export default function StepWisePipelinePanel({ graphName, pipelineId, intent, file, text, url, resumeRunId, onComplete, onCancel }) {
  const [runId, setRunId] = useState(resumeRunId || null);
  const [status, setStatus] = useState(resumeRunId ? 'running' : 'idle');  // idle, starting, running, completed, failed, cancelled
  const [stages, setStages] = useState([]);
  const [stageResults, setStageResults] = useState([]);
  const [currentStage, setCurrentStage] = useState(null);
  const [remainingStages, setRemainingStages] = useState([]);
  const [classification, setClassification] = useState(null);
  const [nodesPreview, setNodesPreview] = useState([]);
  const [edgesPreview, setEdgesPreview] = useState([]);
  const [nodesCount, setNodesCount] = useState(0);
  const [edgesCount, setEdgesCount] = useState(0);
  const [chunksCount, setChunksCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [lastStageOutput, setLastStageOutput] = useState(null);
  const [editMode, setEditMode] = useState(false);
  const [subSteps, setSubSteps] = useState([]);
  const [showSubStepLog, setShowSubStepLog] = useState(false);
  const wsRef = useRef(null);
  const completeFiredRef = useRef(false);

  // Guard: fire onComplete/onFail only once per run
  const fireComplete = useCallback((finalStatus) => {
    if (completeFiredRef.current) return;
    completeFiredRef.current = true;
    try { localStorage.removeItem(STORAGE_KEY); } catch {}
    if (finalStatus === 'completed') {
      toast.success('Pipeline completed!');
      onComplete?.();
    } else if (finalStatus === 'failed') {
      toast.error('Pipeline failed');
    }
  }, [onComplete]);

  // Persist active runId to localStorage
  useEffect(() => {
    if (runId && status !== 'completed' && status !== 'failed' && status !== 'cancelled') {
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ runId, graphName })); } catch {}
    } else if (status === 'completed' || status === 'failed' || status === 'cancelled') {
      try { localStorage.removeItem(STORAGE_KEY); } catch {}
    }
  }, [runId, status, graphName]);

  // On mount with resumeRunId, fetch current context immediately
  useEffect(() => {
    if (!resumeRunId) return;
    const fetchInitial = async () => {
      try {
        const res = await api.get(`/dashboard/pipelines/runs/${resumeRunId}/context`);
        updateFromResponse(res.data);
      } catch {
        // Run may have been cleaned up
        setStatus('idle');
        try { localStorage.removeItem(STORAGE_KEY); } catch {}
      }
    };
    fetchInitial();
  }, [resumeRunId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Subscribe to real-time pipeline events via WebSocket
  useEffect(() => {
    if (!runId) return;
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${protocol}://${window.location.host}/ws/events`);
    ws.onmessage = (msg) => {
      try {
        const evt = JSON.parse(msg.data);
        if (evt.data?.run_id !== runId) return;

        if (evt.type === 'pipeline_sub_step') {
          setSubSteps(prev => [...prev, evt.data]);
        } else if (evt.type === 'pipeline_stage_update') {
          // Stage completed/failed/paused — update all state from the event
          updateFromResponse(evt.data);
          if (evt.data.status === 'completed' || evt.data.status === 'failed') {
            fireComplete(evt.data.status);
          }
        }
      } catch {}
    };
    ws.onerror = () => {};
    ws.onclose = () => {};
    wsRef.current = ws;
    return () => { ws.close(); wsRef.current = null; };
  }, [runId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Polling fallback — keeps UI in sync when WebSocket is unavailable
  useEffect(() => {
    if (!runId || status !== 'running') return;
    let intervalId = null;
    let stopped = false;
    const poll = async () => {
      if (stopped) return;
      try {
        const res = await api.get(`/dashboard/pipelines/runs/${runId}/context`);
        if (stopped) return;
        updateFromResponse(res.data);
        const s = res.data.status;
        if (s === 'completed' || s === 'failed') {
          // Stop polling immediately — don't wait for status state update
          stopped = true;
          if (intervalId) clearInterval(intervalId);
          fireComplete(s);
        }
      } catch (err) {
        // Transient network errors — log but keep polling
        console.debug('Pipeline poll error (will retry):', err?.message);
      }
    };
    intervalId = setInterval(poll, 2500);
    return () => { stopped = true; clearInterval(intervalId); };
  }, [runId, status]); // eslint-disable-line react-hooks/exhaustive-deps

  const updateFromResponse = (data) => {
    if (data.status) setStatus(data.status);
    if (data.stages) setStages(data.stages);
    if (data.stage_results) setStageResults(data.stage_results);
    if (data.current_stage !== undefined) setCurrentStage(data.current_stage);
    if (data.remaining_stages) setRemainingStages(data.remaining_stages);
    if (data.classification) setClassification(data.classification);
    if (data.nodes_preview) setNodesPreview(data.nodes_preview);
    if (data.edges_preview) setEdgesPreview(data.edges_preview);
    if (data.nodes_count !== undefined) setNodesCount(data.nodes_count);
    if (data.edges_count !== undefined) setEdgesCount(data.edges_count);
    if (data.chunks_count !== undefined) setChunksCount(data.chunks_count);
    if (data.stage_output) setLastStageOutput(data.stage_output);
    if (data.sub_steps) setSubSteps(data.sub_steps);
    // For run-all mode
    if (data.nodes !== undefined) setNodesCount(data.nodes);
    if (data.edges !== undefined) setEdgesCount(data.edges);
  };

  const startRun = useCallback(async () => {
    setLoading(false);
    setStatus('running');
    setSubSteps([]);
    try {
      const fd = new FormData();
      fd.append('graph', graphName);
      fd.append('intent', intent || 'graph_rag');
      fd.append('mode', 'step_by_step');
      if (pipelineId) fd.append('pipeline_id', pipelineId);
      if (file) fd.append('file', file);
      if (text) fd.append('text', text);
      if (url) fd.append('url', url);

      // Returns immediately — execution runs in background
      const res = await api.post('/dashboard/pipelines/runs/start', fd);
      const newRunId = res.data.run_id;
      completeFiredRef.current = false;
      setRunId(newRunId);
      if (res.data.stages) setStages(res.data.stages);
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ runId: newRunId, graphName })); } catch {}
      toast.success('Pipeline started');
    } catch (err) {
      const detail = err.response?.data?.detail;
      const msg = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(d => d.msg || JSON.stringify(d)).join('; ') : err.message;
      toast.error('Failed to start pipeline: ' + msg);
      // 409 = duplicate file — reset to idle so user can pick a different file
      setStatus(err.response?.status === 409 ? 'idle' : 'failed');
    }
  }, [graphName, pipelineId, intent, file, text, url]);

  const refreshContext = useCallback(async () => {
    if (!runId) return;
    try {
      const res = await api.get(`/dashboard/pipelines/runs/${runId}/context`);
      updateFromResponse(res.data);
    } catch {}
  }, [runId]);

  const continueRun = useCallback(async () => {
    if (!runId) return;
    setStatus('running');
    try {
      await api.post(`/dashboard/pipelines/runs/${runId}/continue`);
      // Updates arrive via WebSocket pipeline_stage_update events
    } catch (err) {
      toast.error('Stage failed: ' + (err.response?.data?.detail || err.message));
      setStatus('failed');
    }
  }, [runId]);

  const runAllRemaining = useCallback(async () => {
    if (!runId) return;
    setStatus('running');
    try {
      await api.post(`/dashboard/pipelines/runs/${runId}/run-all`);
      // Updates arrive via WebSocket pipeline_stage_update events
    } catch (err) {
      toast.error('Pipeline failed: ' + (err.response?.data?.detail || err.message));
      setStatus('failed');
    }
  }, [runId]);

  const skipStage = useCallback(async () => {
    if (!runId) return;
    setLoading(true);
    try {
      const res = await api.post(`/dashboard/pipelines/runs/${runId}/skip`);
      updateFromResponse(res.data);
    } catch (err) {
      toast.error('Skip failed');
    } finally {
      setLoading(false);
    }
  }, [runId]);

  const cancelRun = useCallback(async () => {
    if (!runId) return;
    try {
      await api.delete(`/dashboard/pipelines/runs/${runId}/cancel`);
      setStatus('cancelled');
      try { localStorage.removeItem(STORAGE_KEY); } catch {}
      toast('Pipeline cancelled');
      onCancel?.();
    } catch {}
  }, [runId, onCancel]);

  // Compute stage statuses
  const stageStatusMap = {};
  stageResults.forEach((sr) => { stageStatusMap[sr.stage_name] = sr.status; });
  if (currentStage && !stageStatusMap[currentStage]) {
    stageStatusMap[currentStage] = 'pending';
  }

  // Before run started
  if (status === 'idle') {
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <Layers size={14} style={{ color: 'var(--neo-blue)' }} />
          <span className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>Step-by-Step Pipeline</span>
        </div>
        <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
          Run the pipeline one stage at a time. Review and edit data between stages.
        </div>
        <div className="flex gap-2">
          <button
            onClick={startRun}
            disabled={loading}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            <Play size={11} /> Start Pipeline
          </button>
          <button
            onClick={onCancel}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90"
            style={{ background: 'var(--neo-surface)', color: 'var(--neo-text-muted)', border: '1px solid var(--neo-border)' }}
          >
            <X size={11} /> Cancel
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Layers size={14} style={{ color: 'var(--neo-blue)' }} />
          <span className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>
            Pipeline Run
          </span>
          <span
            className="px-1.5 py-0.5 rounded text-xs font-medium"
            style={{
              background: status === 'completed' ? 'rgba(34,197,94,0.1)' : status === 'failed' ? 'rgba(239,68,68,0.1)' : 'rgba(59,130,246,0.1)',
              color: status === 'completed' ? '#22c55e' : status === 'failed' ? '#ef4444' : '#3b82f6',
            }}
          >
            {status}
          </span>
        </div>
        <span className="text-xs font-mono" style={{ color: 'var(--neo-text-dim)' }}>
          {runId?.slice(0, 8)}
        </span>
      </div>

      {/* Stage Timeline */}
      <div className="flex flex-wrap gap-1.5">
        {stages.map((stage, i) => (
          <React.Fragment key={stage + i}>
            <StageBadge stage={stage} status={stageStatusMap[stage] || 'pending'} />
            {i < stages.length - 1 && (
              <span className="self-center text-xs" style={{ color: 'var(--neo-text-dim)' }}>&rarr;</span>
            )}
          </React.Fragment>
        ))}
      </div>

      {/* Classification result */}
      {classification && (
        <div
          className="rounded-lg p-2 text-xs space-y-1"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        >
          <div className="flex items-center gap-2">
            <Filter size={11} style={{ color: '#8b5cf6' }} />
            <span style={{ color: 'var(--neo-text)' }}>
              <strong>Type:</strong> {classification.file_type}
            </span>
            <span style={{ color: 'var(--neo-text)' }}>
              <strong>Structure:</strong> {classification.structure_type}
            </span>
            <span
              className="px-1.5 py-0.5 rounded"
              style={{ background: 'rgba(88,86,214,0.1)', color: '#5856d6' }}
            >
              {Math.round((classification.confidence || 0) * 100)}% confidence
            </span>
          </div>
        </div>
      )}

      {/* Data previews */}
      <div className="space-y-2">
        <div className="flex items-center gap-3 text-xs" style={{ color: 'var(--neo-text-muted)' }}>
          <span><strong>{nodesCount}</strong> nodes</span>
          <span><strong>{edgesCount}</strong> edges</span>
          {chunksCount > 0 && <span><strong>{chunksCount}</strong> chunks</span>}
        </div>
        <DataPreview label="Nodes" data={nodesPreview} />
        <DataPreview label="Edges" data={edgesPreview} />
      </div>

      {/* Last stage output */}
      {lastStageOutput && (
        <div
          className="rounded-lg p-2 text-xs"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        >
          <div className="flex items-center gap-1 mb-1">
            {lastStageOutput.status === 'completed' ? (
              <CheckCircle size={10} style={{ color: '#22c55e' }} />
            ) : (
              <XCircle size={10} style={{ color: '#ef4444' }} />
            )}
            <span style={{ color: 'var(--neo-text)' }}>{lastStageOutput.stage_name}</span>
          </div>
          {lastStageOutput.summary && (
            <pre
              className="text-xs overflow-auto rounded p-1"
              style={{ background: 'var(--neo-bg)', color: 'var(--neo-text-muted)', maxHeight: 100 }}
            >
              {JSON.stringify(lastStageOutput.summary, null, 2)}
            </pre>
          )}
          {lastStageOutput.error && (
            <div className="flex items-center gap-1 mt-1" style={{ color: '#ef4444' }}>
              <AlertTriangle size={10} /> {typeof lastStageOutput.error === 'string' ? lastStageOutput.error : JSON.stringify(lastStageOutput.error)}
            </div>
          )}
        </div>
      )}

      {/* Live sub-steps */}
      {subSteps.length > 0 && (
        <div
          className="rounded-lg p-2 text-xs"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        >
          <div className="flex items-center justify-between mb-1">
            <div className="flex items-center gap-1.5" style={{ color: 'var(--neo-text-muted)' }}>
              {status === 'running' && <Loader2 size={10} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />}
              <span>{subSteps[subSteps.length - 1].message}</span>
              {subSteps[subSteps.length - 1].progress != null && (
                <span style={{ color: 'var(--neo-blue)' }}>
                  {Math.round(subSteps[subSteps.length - 1].progress * 100)}%
                </span>
              )}
            </div>
            <button
              onClick={() => setShowSubStepLog(!showSubStepLog)}
              className="text-xs flex items-center gap-0.5"
              style={{ color: 'var(--neo-text-dim)' }}
            >
              {subSteps.length} steps {showSubStepLog ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
            </button>
          </div>
          {subSteps[subSteps.length - 1].progress != null && (
            <div className="w-full h-1 rounded-full mt-1" style={{ background: 'var(--neo-border)' }}>
              <div
                className="h-full rounded-full transition-all"
                style={{ width: `${Math.round(subSteps[subSteps.length - 1].progress * 100)}%`, background: 'var(--neo-blue)' }}
              />
            </div>
          )}
          {showSubStepLog && (
            <div className="mt-2 max-h-32 overflow-auto space-y-0.5">
              {subSteps.map((s, i) => (
                <div key={i} className="flex items-center gap-2 text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
                  <span className="font-mono opacity-60">{s.stage}</span>
                  <span>{s.message}</span>
                  {s.progress != null && <span style={{ color: 'var(--neo-blue)' }}>{Math.round(s.progress * 100)}%</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Controls — show when paused (stage done, waiting for user) or idle */}
      {(status === 'paused' || status === 'idle') && (
        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={continueRun}
            disabled={!currentStage}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90 disabled:opacity-50"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            <Play size={11} /> Continue
          </button>
          <button
            onClick={skipStage}
            disabled={!currentStage}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90 disabled:opacity-50"
            style={{ background: 'var(--neo-surface)', color: 'var(--neo-text)', border: '1px solid var(--neo-border)' }}
          >
            <SkipForward size={11} /> Skip
          </button>
          <button
            onClick={runAllRemaining}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90 disabled:opacity-50"
            style={{ background: '#22c55e', color: '#fff' }}
          >
            <FastForward size={11} /> Run All
          </button>
          <button
            onClick={() => { setEditMode(!editMode); refreshContext(); }}
            className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90"
            style={{ background: 'var(--neo-surface)', color: 'var(--neo-text-muted)', border: '1px solid var(--neo-border)' }}
          >
            {editMode ? <Eye size={11} /> : <Edit3 size={11} />}
            {editMode ? 'View' : 'Edit'}
          </button>
          <button
            onClick={cancelRun}
            className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90 ml-auto"
            style={{ color: '#ef4444', background: 'none', border: 'none', cursor: 'pointer' }}
          >
            <X size={11} /> Cancel
          </button>
        </div>
      )}

      {/* Running indicator with cancel */}
      {status === 'running' && (
        <div className="flex items-center gap-2">
          <Loader2 size={14} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
          <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
            {currentStage ? `Running ${currentStage}...` : 'Processing...'}
          </span>
          <button
            onClick={cancelRun}
            className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90 ml-auto"
            style={{ color: '#ef4444' }}
          >
            <X size={11} /> Cancel
          </button>
        </div>
      )}

      {/* Completed summary */}
      {status === 'completed' && (
        <div
          className="rounded-lg p-3 text-center"
          style={{ background: 'rgba(34,197,94,0.05)', border: '1px solid rgba(34,197,94,0.2)' }}
        >
          <CheckCircle size={20} className="mx-auto mb-1" style={{ color: '#22c55e' }} />
          <div className="text-sm font-medium" style={{ color: '#22c55e' }}>Pipeline Complete</div>
          <div className="text-xs mt-1" style={{ color: 'var(--neo-text-muted)' }}>
            {nodesCount} nodes, {edgesCount} edges created
          </div>
          <button
            onClick={onComplete}
            className="mt-2 px-3 py-1 rounded-lg text-xs font-medium"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            Done
          </button>
        </div>
      )}
    </div>
  );
}

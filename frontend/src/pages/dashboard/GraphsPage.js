import React, { useState, useEffect, useCallback } from 'react';
import { useEvent } from '../../context/EventContext';
import {
  Database, Plus, Trash2, Loader2, GitBranch, Eye,
  Terminal, Search, MessageSquare, Upload,
  FileText, Globe, Check, X, RefreshCw,
} from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import StepWisePipelinePanel from '../../components/StepWisePipelinePanel';
import DashboardGraphExplorer from '../../components/DashboardGraphExplorer';
import QueryTab from '../../components/graph-tabs/QueryTab';
import SearchTab from '../../components/graph-tabs/SearchTab';
import RAGTab from '../../components/graph-tabs/RAGTab';

const TABS = [
  { id: 'explorer', label: 'Explorer', icon: GitBranch },
  { id: 'ingest',   label: 'Ingest',   icon: Upload },
  { id: 'query',    label: 'Query',    icon: Terminal },
  { id: 'search',   label: 'Search',   icon: Search },
  { id: 'rag',      label: 'RAG',      icon: MessageSquare },
];

export default function GraphsPage() {
  const [graphs, setGraphs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [selectedGraph, setSelectedGraph] = useState(null);
  const [activeTab, setActiveTab] = useState('explorer');

  const fetchGraphs = useCallback(() => {
    api.get('/dashboard/graphs')
      .then(res => setGraphs(res.data.graphs || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetchGraphs(); }, [fetchGraphs]);

  // Real-time: refresh when graphs are created/deleted (from any page)
  useEvent('graph_created', fetchGraphs);
  useEvent('graph_deleted', fetchGraphs);
  useEvent('context_created', fetchGraphs);
  useEvent('context_deleted', fetchGraphs);

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!newName.trim()) return;
    setCreating(true);
    try {
      await api.post('/dashboard/graphs', { name: newName.trim() });
      toast.success(`Graph "${newName}" created`);
      setNewName('');
      setShowCreate(false);
      fetchGraphs();
    } catch {
      // toast handled by interceptor
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (name, e) => {
    e.stopPropagation();
    if (!window.confirm(`Delete graph "${name}"? This cannot be undone.`)) return;
    // Optimistic: remove from UI immediately
    setGraphs(prev => prev.filter(g => g.name !== name));
    if (selectedGraph === name) setSelectedGraph(null);
    try {
      await api.delete(`/dashboard/graphs/${encodeURIComponent(name)}`);
      toast.success(`Graph deleted`);
    } catch {
      fetchGraphs(); // revert on error
    }
  };

  const selectGraph = (name) => {
    if (selectedGraph === name) {
      setSelectedGraph(null);
    } else {
      setSelectedGraph(name);
      setActiveTab('explorer');
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      </div>
    );
  }

  return (
    <div className="flex h-full gap-0" style={{ minHeight: 'calc(100vh - 160px)' }}>
      {/* ── Left panel: Graph list ─────────────────────────────────── */}
      <div
        className="w-64 shrink-0 flex flex-col overflow-hidden"
        style={{ borderRight: '1px solid var(--neo-border)' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3">
          <div>
            <h1 className="text-base font-bold" style={{ color: 'var(--neo-text)' }}>Graphs</h1>
            <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
              {graphs.length} graph{graphs.length !== 1 ? 's' : ''}
            </p>
          </div>
          <button
            onClick={() => setShowCreate(!showCreate)}
            className="p-1.5 rounded-lg transition hover:opacity-90"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
            title="New Graph"
          >
            <Plus size={16} />
          </button>
        </div>

        {/* Create form */}
        {showCreate && (
          <form
            onSubmit={handleCreate}
            className="flex items-center gap-2 px-3 pb-3"
          >
            <input
              value={newName}
              onChange={e => setNewName(e.target.value)}
              placeholder="Graph name"
              className="flex-1 px-2 py-1.5 rounded-lg text-xs outline-none"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
              autoFocus
            />
            <button
              type="submit"
              disabled={creating || !newName.trim()}
              className="px-2.5 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90 disabled:opacity-50"
              style={{ background: 'var(--neo-green)', color: '#fff' }}
            >
              {creating ? <Loader2 size={12} className="animate-spin" /> : 'Create'}
            </button>
          </form>
        )}

        {/* Graph list */}
        <div className="flex-1 overflow-y-auto px-2 pb-2">
          {graphs.length === 0 ? (
            <div className="text-center py-12">
              <Database size={28} className="mx-auto mb-2" style={{ color: 'var(--neo-text-muted)' }} />
              <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                No graphs yet.
              </p>
            </div>
          ) : (
            <div className="space-y-1">
              {graphs.map((g) => {
                const name = g.display_name || g.name;
                const isActive = selectedGraph === name;
                return (
                  <div
                    key={name}
                    className="flex items-center justify-between px-3 py-2.5 rounded-lg transition cursor-pointer group"
                    style={{
                      background: isActive ? 'var(--neo-bg)' : 'transparent',
                      border: isActive ? '1px solid var(--neo-blue)' : '1px solid transparent',
                    }}
                    onClick={() => selectGraph(name)}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <Database size={14} className="shrink-0" style={{ color: isActive ? 'var(--neo-blue)' : 'var(--neo-text-muted)' }} />
                      <div className="min-w-0">
                        <div className="text-sm font-medium truncate" style={{ color: 'var(--neo-text)' }}>
                          {name}
                        </div>
                        <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                          {g.node_count ?? 0}n / {g.edge_count ?? 0}e
                          {g.updated_at && (() => {
                            const d = new Date(g.updated_at);
                            const mo = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getUTCMonth()];
                            return (
                              <span className="ml-1" style={{ color: 'var(--neo-text-dim)' }}>
                                · {`${mo} ${d.getUTCDate()}, ${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')} UTC`}
                              </span>
                            );
                          })()}
                        </div>
                      </div>
                    </div>
                    <button
                      onClick={(e) => handleDelete(g.name || name, e)}
                      className="p-1 rounded opacity-0 group-hover:opacity-100 transition hover:opacity-80"
                      style={{ color: 'var(--neo-text-muted)' }}
                      title="Delete"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* ── Right panel: Tabbed content ────────────────────────────── */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {selectedGraph ? (
          <>
            {/* Tab bar */}
            <div
              className="flex items-center gap-1 px-4 py-2 shrink-0"
              style={{ borderBottom: '1px solid var(--neo-border)', background: 'var(--neo-surface)' }}
            >
              <span className="text-sm font-semibold mr-3" style={{ color: 'var(--neo-text)' }}>
                {selectedGraph}
              </span>
              {TABS.map((tab) => {
                const Icon = tab.icon;
                const active = activeTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    onClick={() => setActiveTab(tab.id)}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition"
                    style={{
                      background: active ? 'var(--neo-bg)' : 'transparent',
                      color: active ? 'var(--neo-text)' : 'var(--neo-text-muted)',
                      border: active ? '1px solid var(--neo-border)' : '1px solid transparent',
                    }}
                  >
                    <Icon size={13} />
                    {tab.label}
                  </button>
                );
              })}
            </div>

            {/* Tab content */}
            <div className="flex-1 overflow-auto p-4">
              {activeTab === 'explorer' && (
                <DashboardGraphExplorer graphName={selectedGraph} embedded />
              )}
              {activeTab === 'ingest' && (
                <GraphIngestTab graphName={selectedGraph} onIngestComplete={fetchGraphs} />
              )}
              {activeTab === 'query' && (
                <QueryTab graphName={selectedGraph} />
              )}
              {activeTab === 'search' && (
                <SearchTab graphName={selectedGraph} />
              )}
              {activeTab === 'rag' && (
                <RAGTab graphName={selectedGraph} />
              )}
            </div>
          </>
        ) : (
          /* Empty state */
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center">
              <Eye size={40} className="mx-auto mb-3" style={{ color: 'var(--neo-text-muted)', opacity: 0.4 }} />
              <p className="text-sm font-medium" style={{ color: 'var(--neo-text-muted)' }}>
                Select a graph to explore
              </p>
              <p className="text-xs mt-1" style={{ color: 'var(--neo-text-muted)', opacity: 0.7 }}>
                Choose a graph from the list or create a new one
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ─── Graph Ingest Tab ─── */

function GraphIngestTab({ graphName, onIngestComplete }) {
  const [mode, setMode] = useState('file'); // file | text | url
  const [ingesting, setIngesting] = useState(false);

  // Text mode
  const [text, setText] = useState('');
  const [textTitle, setTextTitle] = useState('');

  // File mode
  const [file, setFile] = useState(null);
  const [dragOver, setDragOver] = useState(false);

  // URL mode
  const [url, setUrl] = useState('');

  // Pipeline options
  const [pipelines, setPipelines] = useState([]);
  const [selectedPipeline, setSelectedPipeline] = useState('');

  // Step-wise options
  const [intent, setIntent] = useState('graph_rag');
  const [execMode, setExecMode] = useState('run_all');
  const [showStepWise, setShowStepWise] = useState(false);

  useEffect(() => {
    api.get('/dashboard/pipelines')
      .then((res) => {
        const pl = res.data.pipelines || [];
        setPipelines(pl);
        const auto = pl.find((p) => p.id === 'builtin:auto');
        const basic = pl.find((p) => p.id === 'builtin:basic');
        if (auto) setSelectedPipeline(auto.id);
        else if (basic) setSelectedPipeline(basic.id);
        else if (pl.length > 0) setSelectedPipeline(pl[0].id);
      })
      .catch(() => {});
  }, []);

  const handleIngestText = async () => {
    if (!text.trim()) return;
    setIngesting(true);
    try {
      await api.post('/dashboard/ingest/text', {
        graph: graphName,
        text: text.trim(),
        title: textTitle.trim() || undefined,
        pipeline_id: selectedPipeline || undefined,
      });
      toast.success('Text ingested');
      setText('');
      setTextTitle('');
      onIngestComplete?.();
    } catch {} finally {
      setIngesting(false);
    }
  };

  const handleIngestFile = async () => {
    if (!file) return;
    setIngesting(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('graph', graphName);
      if (selectedPipeline) formData.append('pipeline_id', selectedPipeline);
      await api.post('/dashboard/ingest/file', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      toast.success(`"${file.name}" ingested`);
      setFile(null);
      onIngestComplete?.();
    } catch {} finally {
      setIngesting(false);
    }
  };

  const handleIngestUrl = async () => {
    if (!url.trim()) return;
    setIngesting(true);
    try {
      await api.post('/dashboard/ingest/url', {
        graph: graphName,
        url: url.trim(),
        pipeline_id: selectedPipeline || undefined,
      });
      toast.success('URL content ingested');
      setUrl('');
      onIngestComplete?.();
    } catch {} finally {
      setIngesting(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files[0];
    if (f) setFile(f);
  };

  return (
    <div className="max-w-2xl">
      <h3 className="text-sm font-semibold mb-1" style={{ color: 'var(--neo-text)' }}>
        Ingest into "{graphName}"
      </h3>
      <p className="text-xs mb-4" style={{ color: 'var(--neo-text-muted)' }}>
        Upload files, paste text, or fetch a URL to add data directly to this graph.
      </p>

      {/* Mode tabs */}
      <div className="flex gap-1 mb-4">
        {[
          { id: 'file', label: 'Upload', icon: FileText },
          { id: 'text', label: 'Text', icon: Terminal },
          { id: 'url', label: 'URL', icon: Globe },
        ].map((m) => {
          const Icon = m.icon;
          return (
            <button
              key={m.id}
              onClick={() => setMode(m.id)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition"
              style={{
                background: mode === m.id ? 'var(--neo-blue)' : 'transparent',
                color: mode === m.id ? '#fff' : 'var(--neo-text-muted)',
                border: `1px solid ${mode === m.id ? 'var(--neo-blue)' : 'var(--neo-border)'}`,
              }}
            >
              <Icon size={12} />
              {m.label}
            </button>
          );
        })}
      </div>

      {/* File upload */}
      {mode === 'file' && (
        <div className="space-y-3">
          <div
            className="border-2 border-dashed rounded-xl p-8 text-center transition cursor-pointer"
            style={{
              borderColor: dragOver ? 'var(--neo-blue)' : 'var(--neo-border)',
              background: dragOver ? 'rgba(0,122,255,0.05)' : 'transparent',
            }}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            onClick={() => document.getElementById('graph-file-input').click()}
          >
            <Upload size={24} className="mx-auto mb-2" style={{ color: 'var(--neo-text-muted)' }} />
            {file ? (
              <div>
                <p className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>{file.name}</p>
                <p className="text-xs mt-1" style={{ color: 'var(--neo-text-muted)' }}>
                  {(file.size / 1024).toFixed(1)} KB
                </p>
              </div>
            ) : (
              <div>
                <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                  Drop a file here or click to browse
                </p>
                <p className="text-xs mt-1" style={{ color: 'var(--neo-text-dim)' }}>
                  Excel, PDF, DOCX, CSV, JSON, GraphML, TTL, TXT
                </p>
              </div>
            )}
          </div>
          <input
            id="graph-file-input"
            type="file"
            className="hidden"
            accept=".xlsx,.xls,.pdf,.docx,.csv,.txt,.json,.md,.html,.xml,.graphml,.ttl,.nt,.rdf,.jsonld"
            onChange={(e) => setFile(e.target.files[0] || null)}
          />
          <button
            onClick={handleIngestFile}
            disabled={!file || ingesting}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
            style={{ background: 'var(--neo-green)', color: '#fff' }}
          >
            {ingesting ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
            Upload & Ingest
          </button>
        </div>
      )}

      {/* Text paste */}
      {mode === 'text' && (
        <div className="space-y-3">
          <input
            value={textTitle}
            onChange={(e) => setTextTitle(e.target.value)}
            placeholder="Title (optional)"
            className="w-full px-3 py-2 rounded-lg text-sm outline-none"
            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          />
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Paste text content here..."
            rows={8}
            className="w-full px-3 py-2 rounded-lg text-sm outline-none resize-y"
            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          />
          <button
            onClick={handleIngestText}
            disabled={!text.trim() || ingesting}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
            style={{ background: 'var(--neo-green)', color: '#fff' }}
          >
            {ingesting ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
            Ingest Text
          </button>
        </div>
      )}

      {/* URL fetch */}
      {mode === 'url' && (
        <div className="space-y-3">
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com/article"
            className="w-full px-3 py-2 rounded-lg text-sm outline-none"
            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          />
          <button
            onClick={handleIngestUrl}
            disabled={!url.trim() || ingesting}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
            style={{ background: 'var(--neo-green)', color: '#fff' }}
          >
            {ingesting ? <Loader2 size={14} className="animate-spin" /> : <Globe size={14} />}
            Fetch & Ingest
          </button>
        </div>
      )}

      {/* Pipeline selector */}
      <div className="mt-4 pt-4" style={{ borderTop: '1px solid var(--neo-border)' }}>
        <label className="block text-xs mb-1" style={{ color: 'var(--neo-text-muted)' }}>Pipeline</label>
        <div className="flex items-center gap-2">
          <select
            value={selectedPipeline}
            onChange={(e) => setSelectedPipeline(e.target.value)}
            className="flex-1 px-2 py-1.5 rounded-lg text-xs outline-none"
            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          >
            {pipelines.filter((p) => p.tags?.includes('auto')).length > 0 && (
              <optgroup label="Recommended">
                {pipelines.filter((p) => p.tags?.includes('auto')).map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </optgroup>
            )}
            {pipelines.filter((p) => p.tags?.includes('tabular') || p.tags?.includes('excel')).length > 0 && (
              <optgroup label="Structured Data">
                {pipelines.filter((p) => (p.tags?.includes('tabular') || p.tags?.includes('excel')) && !p.tags?.includes('auto')).map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </optgroup>
            )}
            <optgroup label="Documents">
              {pipelines.filter((p) => !p.tags?.includes('tabular') && !p.tags?.includes('excel') && !p.tags?.includes('auto')).map((p) => (
                <option key={p.id} value={p.id}>{p.name}{p.tags?.includes('llm') ? ' (LLM)' : ''}</option>
              ))}
            </optgroup>
          </select>
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
              <span className="text-xs px-1.5 py-0.5 rounded whitespace-nowrap" style={{ background: 'rgba(88,86,214,0.1)', color: '#5856d6' }}>
                {badges.join(' + ')}
              </span>
            );
          })()}
        </div>
        <div className="flex items-center gap-2 mt-2">
          <select
            value={intent}
            onChange={(e) => setIntent(e.target.value)}
            className="px-2 py-1 rounded-lg text-xs outline-none"
            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          >
            <option value="build_graph">Build Graph</option>
            <option value="graph_rag">Graph RAG</option>
            <option value="search_only">Search Only</option>
          </select>
          <button
            onClick={() => setExecMode(execMode === 'run_all' ? 'step_by_step' : 'run_all')}
            className="px-2 py-1 rounded-lg text-xs font-medium transition"
            style={{
              background: execMode === 'step_by_step' ? 'rgba(88,86,214,0.15)' : 'var(--neo-bg)',
              color: execMode === 'step_by_step' ? '#5856d6' : 'var(--neo-text-muted)',
              border: `1px solid ${execMode === 'step_by_step' ? '#5856d6' : 'var(--neo-border)'}`,
            }}
            title="Toggle step-by-step execution"
          >
            {execMode === 'step_by_step' ? 'Step' : 'Auto'}
          </button>
          {execMode === 'step_by_step' && (
            <button
              onClick={() => setShowStepWise(true)}
              disabled={mode === 'file' ? !file : mode === 'text' ? !text.trim() : !url.trim()}
              className="px-3 py-1 rounded-lg text-xs font-medium transition hover:opacity-90 disabled:opacity-50"
              style={{ background: '#5856d6', color: '#fff' }}
            >
              Start Step-by-Step
            </button>
          )}
        </div>
      </div>

      {/* Step-wise pipeline panel */}
      {showStepWise && (
        <div className="mt-4 pt-4" style={{ borderTop: '1px solid var(--neo-border)' }}>
          <StepWisePipelinePanel
            graphName={graphName}
            pipelineId={selectedPipeline}
            intent={intent}
            file={mode === 'file' ? file : null}
            text={mode === 'text' ? text : null}
            url={mode === 'url' ? url : null}
            onComplete={() => {
              setShowStepWise(false);
              onIngestComplete?.();
            }}
            onCancel={() => setShowStepWise(false)}
          />
        </div>
      )}
    </div>
  );
}


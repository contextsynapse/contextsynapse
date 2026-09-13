import React, { useState, useEffect, useCallback } from 'react';
import {
  Upload, FileText, Globe, Loader2, CheckCircle, XCircle, File,
  ChevronDown, ChevronRight, Cpu, Sparkles, FileCode, AlertTriangle,
} from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../../lib/api';

const TABS = [
  { id: 'upload', label: 'File Upload', icon: Upload },
  { id: 'text', label: 'Paste Text', icon: FileText },
  { id: 'url', label: 'From URL', icon: Globe },
];

const STAGE_LABELS = {
  chunking: 'Chunking',
  extracting: 'Extracting',
  embedding: 'Embedding',
  persisting: 'Persisting',
};

const STAGE_ORDER = ['chunking', 'extracting', 'embedding', 'persisting'];

function StageIndicator({ stages }) {
  if (!stages) return null;
  return (
    <div className="flex items-center gap-1 mt-1.5">
      {STAGE_ORDER.map((key, i) => {
        const s = stages[key];
        if (!s) return null;
        const status = s.status;
        const isCompleted = status === 'completed';
        const isRunning = status === 'running';
        const isFailed = status === 'failed';
        const isSkipped = status === 'skipped';

        let color = 'var(--neo-text-muted)';
        if (isCompleted) color = 'var(--neo-green)';
        else if (isRunning) color = 'var(--neo-blue)';
        else if (isFailed) color = '#ef4444';

        return (
          <React.Fragment key={key}>
            {i > 0 && <span style={{ color: 'var(--neo-text-muted)', fontSize: 8 }}>&rarr;</span>}
            <span
              className="text-xs px-1.5 py-0.5 rounded"
              style={{
                color,
                background: isRunning ? 'var(--neo-blue)/10' : 'transparent',
                opacity: isSkipped ? 0.4 : 1,
                fontWeight: isRunning ? 600 : 400,
              }}
              title={`${STAGE_LABELS[key]}: ${status}${s.count ? ` (${s.count})` : ''}`}
            >
              {isCompleted && '\u2713'}
              {isRunning && '\u25CF'}
              {isFailed && '\u2717'}
              {isSkipped && '\u2013'}
              {status === 'pending' && '\u00B7'}
              {' '}{STAGE_LABELS[key]}
            </span>
          </React.Fragment>
        );
      })}
    </div>
  );
}

export default function IngestPage() {
  const [tab, setTab] = useState('upload');
  const [graphs, setGraphs] = useState([]);
  const [selectedGraph, setSelectedGraph] = useState('');
  const [loading, setLoading] = useState(false);
  const [jobs, setJobs] = useState([]);

  // Text tab
  const [textContent, setTextContent] = useState('');
  const [textTitle, setTextTitle] = useState('');

  // URL tab
  const [url, setUrl] = useState('');

  // File upload
  const [dragOver, setDragOver] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);

  // Pipeline selection
  const [selectedPipeline, setSelectedPipeline] = useState('smart_pipeline');
  const [pipelines, setPipelines] = useState({});

  // Extraction settings
  const [showSettings, setShowSettings] = useState(false);
  const [llmModel, setLlmModel] = useState('');
  const [embeddingModel, setEmbeddingModel] = useState('');
  const [schemaContent, setSchemaContent] = useState('');
  const [schemaFileName, setSchemaFileName] = useState('');
  const [availableModels, setAvailableModels] = useState({
    llm_models: [], embedding_models: [], llm_available: false, embedding_available: false,
  });
  const [modelsLoaded, setModelsLoaded] = useState(false);

  const fetchGraphs = useCallback(() => {
    api.get('/dashboard/graphs')
      .then((res) => {
        const g = res.data.graphs || [];
        setGraphs(g);
        if (g.length > 0 && !selectedGraph) setSelectedGraph(g[0].display_name || g[0].name);
      })
      .catch(() => {});
  }, [selectedGraph]);

  const fetchJobs = useCallback(() => {
    api.get('/dashboard/ingest/jobs')
      .then((res) => setJobs(res.data.jobs || []))
      .catch(() => {});
  }, []);

  const fetchModels = useCallback(() => {
    api.get('/dashboard/ingest/models')
      .then((res) => {
        setAvailableModels(res.data);
        // Auto-select first available LLM model for entity extraction
        if (!modelsLoaded) {
          const models = res.data.llm_models || [];
          if (models.length > 0 && !llmModel) setLlmModel(models[0]);
          setModelsLoaded(true);
        }
      })
      .catch(() => {});
  }, [modelsLoaded, llmModel]);

  useEffect(() => {
    fetchGraphs();
    fetchJobs();
    fetchModels();
    // Fetch available pipelines
    api.get('/dashboard/ingest/pipelines')
      .then((res) => setPipelines(res.data.pipelines || {}))
      .catch(() => {});
  }, [fetchGraphs, fetchJobs, fetchModels]);

  const handleFileSelect = (e) => {
    const file = e.target.files?.[0];
    if (file) setSelectedFile(file);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) setSelectedFile(file);
  };

  const handleSchemaUpload = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setSchemaFileName(file.name);
    const reader = new FileReader();
    reader.onload = (ev) => setSchemaContent(ev.target.result);
    reader.readAsText(file);
  };

  // Poll jobs until target finishes
  const pollJobs = useCallback((jobId) => {
    const poll = setInterval(() => {
      api.get('/dashboard/ingest/jobs')
        .then((res) => {
          const allJobs = res.data.jobs || [];
          setJobs(allJobs);
          const target = allJobs.find((j) => j.job_id === jobId);
          if (!target || target.status !== 'processing') {
            clearInterval(poll);
            if (target?.status === 'completed') {
              const parts = [`${target.nodes_created} node(s)`];
              if (target.edges_created) parts.push(`${target.edges_created} edge(s)`);
              if (target.entities_extracted) parts.push(`${target.entities_extracted} entities`);
              toast.success(`Ingestion complete: ${parts.join(', ')}`);
            } else if (target?.status === 'failed') {
              toast.error(`Ingestion failed: ${target.error || 'Unknown error'}`);
            }
          }
        })
        .catch(() => clearInterval(poll));
    }, 1500);
    setTimeout(() => clearInterval(poll), 120000);
  }, []);

  const handleUpload = async () => {
    if (!selectedFile || !selectedGraph) return;
    setLoading(true);
    try {
      const formData = new FormData();
      formData.append('file', selectedFile);
      formData.append('graph', selectedGraph);
      if (llmModel) formData.append('llm_model', llmModel);
      if (embeddingModel) formData.append('embedding_model', embeddingModel);
      if (schemaContent) formData.append('schema', schemaContent);
      const res = await api.post('/dashboard/ingest/file', formData);
      toast.success(`File "${selectedFile.name}" submitted for ingestion`);
      setSelectedFile(null);
      fetchJobs();
      if (res.data.job_id) pollJobs(res.data.job_id);
    } catch {} finally {
      setLoading(false);
    }
  };

  const handleTextIngest = async () => {
    if (!textContent.trim() || !selectedGraph) return;
    setLoading(true);
    try {
      const body = {
        text: textContent.trim(),
        title: textTitle.trim() || 'Untitled',
        graph: selectedGraph,
      };
      if (llmModel) body.llm_model = llmModel;
      if (embeddingModel) body.embedding_model = embeddingModel;
      if (schemaContent) body.schema = schemaContent;
      const res = await api.post('/dashboard/ingest/text', body);
      toast.success('Text submitted for ingestion');
      setTextContent('');
      setTextTitle('');
      fetchJobs();
      if (res.data.job_id) pollJobs(res.data.job_id);
    } catch {} finally {
      setLoading(false);
    }
  };

  const handleUrlIngest = async () => {
    if (!url.trim() || !selectedGraph) return;
    setLoading(true);
    try {
      const body = {
        url: url.trim(),
        graph: selectedGraph,
        pipeline: selectedPipeline,
      };
      if (selectedPipeline === '') {
        // Legacy mode: use LLM/embedding model settings
        if (llmModel) body.llm_model = llmModel;
        if (embeddingModel) body.embedding_model = embeddingModel;
      }
      if (schemaContent) body.schema = schemaContent;
      const res = await api.post('/dashboard/ingest/url', body);
      toast.success('URL submitted for ingestion');
      setUrl('');
      fetchJobs();
      if (res.data.job_id) pollJobs(res.data.job_id);
    } catch {} finally {
      setLoading(false);
    }
  };

  const hasLLM = llmModel || embeddingModel;

  return (
    <div className="max-w-3xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold" style={{ color: 'var(--neo-text)' }}>Data Ingestion</h1>
          <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
            Import data into your graph from files, text, or URLs
          </p>
        </div>
        <select
          value={selectedGraph}
          onChange={(e) => setSelectedGraph(e.target.value)}
          className="px-3 py-1.5 rounded-lg text-sm outline-none"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
        >
          <option value="">Select graph</option>
          {graphs.map((g) => (
            <option key={g.name} value={g.display_name || g.name}>{g.display_name || g.name}</option>
          ))}
        </select>
      </div>

      {/* Extraction Settings */}
      <div
        className="mb-4 rounded-lg overflow-hidden"
        style={{ border: `1px solid ${hasLLM ? 'var(--neo-blue)' : 'var(--neo-border)'}` }}
      >
        <button
          onClick={() => setShowSettings(!showSettings)}
          className="w-full flex items-center justify-between px-4 py-2.5 text-sm font-medium transition"
          style={{
            background: hasLLM ? 'rgba(59,130,246,0.08)' : 'var(--neo-surface)',
            color: hasLLM ? 'var(--neo-blue)' : 'var(--neo-text-muted)',
          }}
        >
          <div className="flex items-center gap-2">
            <Sparkles size={15} />
            <span>Extraction Settings</span>
            {hasLLM && (
              <span className="text-xs px-1.5 py-0.5 rounded-full" style={{ background: 'var(--neo-blue)', color: '#fff' }}>
                Pipeline Active
              </span>
            )}
          </div>
          {showSettings ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        </button>

        {showSettings && (
          <div className="px-4 py-3 space-y-3" style={{ background: 'var(--neo-surface)' }}>
            <div className="grid grid-cols-2 gap-3">
              {/* LLM Model */}
              <div>
                <label className="text-xs font-medium mb-1 flex items-center gap-1" style={{ color: 'var(--neo-text-muted)' }}>
                  <Cpu size={12} /> LLM Model (entity extraction)
                </label>
                <select
                  value={llmModel}
                  onChange={(e) => setLlmModel(e.target.value)}
                  className="w-full px-2.5 py-1.5 rounded text-sm outline-none"
                  style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                >
                  <option value="">None (basic chunking)</option>
                  {availableModels.llm_models.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
                {!availableModels.llm_available && availableModels.llm_models.length === 0 && (
                  <p className="text-xs mt-1" style={{ color: '#ef4444' }}>No API key configured</p>
                )}
              </div>

              {/* Embedding Model */}
              <div>
                <label className="text-xs font-medium mb-1 flex items-center gap-1" style={{ color: 'var(--neo-text-muted)' }}>
                  <Sparkles size={12} /> Embedding Model
                </label>
                <select
                  value={embeddingModel}
                  onChange={(e) => setEmbeddingModel(e.target.value)}
                  className="w-full px-2.5 py-1.5 rounded text-sm outline-none"
                  style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                >
                  <option value="">None (skip embeddings)</option>
                  {availableModels.embedding_models.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              </div>
            </div>

            {/* Schema Upload */}
            <div>
              <label className="text-xs font-medium mb-1 flex items-center gap-1" style={{ color: 'var(--neo-text-muted)' }}>
                <FileCode size={12} /> Schema (optional YAML — constrains extraction)
              </label>
              <div className="flex items-center gap-2">
                <label
                  className="px-3 py-1.5 rounded text-xs cursor-pointer transition hover:opacity-80"
                  style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
                >
                  {schemaFileName || 'Choose .yaml file'}
                  <input type="file" accept=".yaml,.yml" onChange={handleSchemaUpload} className="hidden" />
                </label>
                {schemaContent && (
                  <button
                    onClick={() => { setSchemaContent(''); setSchemaFileName(''); }}
                    className="text-xs px-2 py-1 rounded"
                    style={{ color: '#ef4444' }}
                  >
                    Clear
                  </button>
                )}
              </div>
            </div>

            {!llmModel && (
              <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                Without an LLM model, text is split into chunks only. Select a model to extract entities, relationships, and build a knowledge graph.
              </p>
            )}
          </div>
        )}
      </div>

      {/* Warning when no LLM selected */}
      {!llmModel && !showSettings && (
        <div
          className="flex items-center gap-2 px-4 py-2.5 mb-3 rounded-lg text-sm"
          style={{ background: '#fef3c7', border: '1px solid #f59e0b', color: '#92400e' }}
        >
          <Cpu size={14} />
          <span>No LLM model selected — uploads will only create text chunks, not a knowledge graph.</span>
          <button
            onClick={() => setShowSettings(true)}
            className="ml-auto text-xs font-medium underline"
            style={{ color: '#92400e' }}
          >
            Configure
          </button>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 mb-4">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition"
            style={{
              background: tab === t.id ? 'var(--neo-blue)' : 'var(--neo-surface)',
              color: tab === t.id ? '#fff' : 'var(--neo-text-muted)',
              border: `1px solid ${tab === t.id ? 'var(--neo-blue)' : 'var(--neo-border)'}`,
            }}
          >
            <t.icon size={15} /> {t.label}
          </button>
        ))}
      </div>

      {/* Upload tab */}
      {tab === 'upload' && (
        <div className="space-y-4">
          <div
            className={`relative border-2 border-dashed rounded-xl p-10 text-center transition ${
              dragOver ? 'border-blue-400 bg-blue-50/5' : ''
            }`}
            style={{ borderColor: dragOver ? 'var(--neo-blue)' : 'var(--neo-border)' }}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
          >
            {selectedFile ? (
              <div className="flex flex-col items-center gap-2">
                <File size={32} style={{ color: 'var(--neo-blue)' }} />
                <span className="font-medium text-sm" style={{ color: 'var(--neo-text)' }}>
                  {selectedFile.name}
                </span>
                <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                  {(selectedFile.size / 1024).toFixed(1)} KB
                </span>
                <div className="flex gap-2 mt-2">
                  <button
                    onClick={handleUpload}
                    disabled={loading || !selectedGraph}
                    className="px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
                    style={{ background: 'var(--neo-green)', color: '#fff' }}
                  >
                    {loading ? <Loader2 size={16} className="animate-spin" /> : 'Ingest File'}
                  </button>
                  <button
                    onClick={() => setSelectedFile(null)}
                    className="px-4 py-2 rounded-lg text-sm"
                    style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
                  >
                    Clear
                  </button>
                </div>
              </div>
            ) : (
              <>
                <Upload size={32} className="mx-auto mb-3" style={{ color: 'var(--neo-text-muted)' }} />
                <p className="font-medium" style={{ color: 'var(--neo-text)' }}>
                  Drop a file here or click to browse
                </p>
                <p className="text-xs mt-1" style={{ color: 'var(--neo-text-muted)' }}>
                  Supports PDF, DOCX, CSV, JSON, TXT, HTML, XML
                </p>
                <input
                  type="file"
                  onChange={handleFileSelect}
                  className="absolute inset-0 opacity-0 cursor-pointer"
                  accept=".pdf,.docx,.csv,.json,.txt,.html,.xml,.md"
                />
              </>
            )}
          </div>
        </div>
      )}

      {/* Text tab */}
      {tab === 'text' && (
        <div className="space-y-3">
          <input
            value={textTitle}
            onChange={(e) => setTextTitle(e.target.value)}
            placeholder="Title (optional)"
            className="w-full px-3 py-2 rounded-lg text-sm outline-none"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          />
          <textarea
            value={textContent}
            onChange={(e) => setTextContent(e.target.value)}
            placeholder="Paste your text here... Entities and relationships will be extracted automatically."
            rows={10}
            className="w-full px-3 py-3 rounded-lg text-sm outline-none resize-none"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          />
          <button
            onClick={handleTextIngest}
            disabled={loading || !textContent.trim() || !selectedGraph}
            className="px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
            style={{ background: 'var(--neo-green)', color: '#fff' }}
          >
            {loading ? <Loader2 size={16} className="animate-spin" /> : 'Extract & Ingest'}
          </button>
        </div>
      )}

      {/* URL tab */}
      {tab === 'url' && (
        <div className="space-y-3">
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com/article"
            type="url"
            className="w-full px-3 py-2 rounded-lg text-sm outline-none"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          />

          {/* Pipeline selector */}
          <div>
            <label className="text-xs font-medium mb-1 block" style={{ color: 'var(--neo-text-muted)' }}>
              Ingestion Pipeline
            </label>
            <div className="flex gap-2 flex-wrap">
              {Object.entries(pipelines).length > 0 ? (
                Object.entries(pipelines).map(([key, info]) => (
                  <button
                    key={key}
                    onClick={() => setSelectedPipeline(key)}
                    className="px-3 py-1.5 rounded-lg text-xs font-medium transition"
                    style={{
                      background: selectedPipeline === key ? 'var(--neo-green)' : 'var(--neo-surface)',
                      color: selectedPipeline === key ? '#fff' : 'var(--neo-text)',
                      border: `1px solid ${selectedPipeline === key ? 'var(--neo-green)' : 'var(--neo-border)'}`,
                    }}
                    title={info.description}
                  >
                    {info.name || key}
                    {info.llm_required && ' (LLM)'}
                  </button>
                ))
              ) : (
                <>
                  <button
                    onClick={() => setSelectedPipeline('smart_pipeline')}
                    className="px-3 py-1.5 rounded-lg text-xs font-medium transition"
                    style={{
                      background: selectedPipeline === 'smart_pipeline' ? 'var(--neo-green)' : 'var(--neo-surface)',
                      color: selectedPipeline === 'smart_pipeline' ? '#fff' : 'var(--neo-text)',
                      border: `1px solid ${selectedPipeline === 'smart_pipeline' ? 'var(--neo-green)' : 'var(--neo-border)'}`,
                    }}
                  >
                    Smart Article (LLM)
                  </button>
                  <button
                    onClick={() => setSelectedPipeline('fast_ingest')}
                    className="px-3 py-1.5 rounded-lg text-xs font-medium transition"
                    style={{
                      background: selectedPipeline === 'fast_ingest' ? 'var(--neo-green)' : 'var(--neo-surface)',
                      color: selectedPipeline === 'fast_ingest' ? '#fff' : 'var(--neo-text)',
                      border: `1px solid ${selectedPipeline === 'fast_ingest' ? 'var(--neo-green)' : 'var(--neo-border)'}`,
                    }}
                  >
                    Fast (No LLM)
                  </button>
                  <button
                    onClick={() => setSelectedPipeline('')}
                    className="px-3 py-1.5 rounded-lg text-xs font-medium transition"
                    style={{
                      background: selectedPipeline === '' ? 'var(--neo-green)' : 'var(--neo-surface)',
                      color: selectedPipeline === '' ? '#fff' : 'var(--neo-text)',
                      border: `1px solid ${selectedPipeline === '' ? 'var(--neo-green)' : 'var(--neo-border)'}`,
                    }}
                  >
                    Legacy
                  </button>
                </>
              )}
            </div>
            {selectedPipeline && pipelines[selectedPipeline] && (
              <p className="text-xs mt-1" style={{ color: 'var(--neo-text-muted)' }}>
                {pipelines[selectedPipeline].description}
              </p>
            )}
            {!selectedPipeline && (
              <p className="text-xs mt-1" style={{ color: 'var(--neo-text-muted)' }}>
                Legacy pipeline: uses configured LLM/embedding models with old extraction stages.
              </p>
            )}
          </div>

          <button
            onClick={handleUrlIngest}
            disabled={loading || !url.trim() || !selectedGraph}
            className="px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
            style={{ background: 'var(--neo-green)', color: '#fff' }}
          >
            {loading ? <Loader2 size={16} className="animate-spin" /> : 'Fetch & Ingest'}
          </button>
        </div>
      )}

      {/* Recent jobs */}
      {jobs.length > 0 && (
        <div className="mt-8">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold" style={{ color: 'var(--neo-text-muted)' }}>
              Recent Ingestion Jobs
            </h2>
            <div className="flex gap-2">
              {jobs.some(j => j.status === 'processing' || j.status === 'running' || j.status === 'queued') && (
                <button
                  onClick={() => {
                    if (window.confirm('Force cancel all stuck/running jobs?')) {
                      api.post('/dashboard/ingest/jobs/clear-stuck').then(() => fetchJobs()).catch(() => {});
                    }
                  }}
                  className="text-[10px] px-2 py-1 rounded font-medium"
                  style={{ background: '#ef4444', color: '#fff' }}
                >
                  Cancel Stuck
                </button>
              )}
              <button
                onClick={() => {
                  api.delete('/dashboard/ingest/jobs').then(() => fetchJobs()).catch(() => {});
                }}
                className="text-[10px] px-2 py-1 rounded"
                style={{ color: 'var(--neo-text-muted)', border: '1px solid var(--neo-border)' }}
              >
                Clear Finished
              </button>
            </div>
          </div>
          <div className="space-y-2">
            {jobs.map((job, i) => (
              <div
                key={job.job_id || i}
                className="flex items-center justify-between p-3 rounded-lg"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
              >
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    {(job.status === 'success' || job.status === 'completed') ? (
                      <CheckCircle size={16} style={{ color: 'var(--neo-green)' }} />
                    ) : job.status === 'completed_empty' ? (
                      <AlertTriangle size={16} style={{ color: '#f59e0b' }} />
                    ) : job.status === 'failed' ? (
                      <XCircle size={16} style={{ color: '#ef4444' }} />
                    ) : (
                      <Loader2 size={16} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
                    )}
                    <div>
                      <div className="text-sm" style={{ color: 'var(--neo-text)' }}>
                        {job.source || 'Unknown'}
                        {job.llm_model && (
                          <span className="text-xs ml-2 px-1.5 py-0.5 rounded" style={{ background: 'var(--neo-bg)', color: 'var(--neo-text-muted)' }}>
                            {job.llm_model}
                          </span>
                        )}
                      </div>
                      <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                        {job.graph} &middot; {new Date(job.created_at).toLocaleString()}
                      </div>
                    </div>
                  </div>
                  {/* Stage progress indicator */}
                  {job.stages && job.status === 'processing' && (
                    <StageIndicator stages={job.stages} />
                  )}
                  {/* Warning for empty results */}
                  {job.status === 'completed_empty' && (
                    <div className="mt-1 flex items-center gap-1.5 text-xs px-2 py-1 rounded"
                      style={{ background: 'rgba(245,158,11,0.1)', color: '#f59e0b' }}>
                      <AlertTriangle size={12} />
                      {job.warning || 'Pipeline completed but nothing was extracted. Check LLM model.'}
                    </div>
                  )}
                  {job.status === 'failed' && job.error && (
                    <div className="mt-1 text-xs px-2 py-1 rounded"
                      style={{ background: 'rgba(239,68,68,0.1)', color: '#ef4444' }}>
                      {job.error}
                    </div>
                  )}
                </div>
                <div className="text-right flex items-center gap-2">
                  <div>
                    {job.nodes_created !== undefined && (
                      <span className="text-xs block" style={{ color: 'var(--neo-text-muted)' }}>
                        +{job.nodes_created} nodes
                      </span>
                    )}
                    {job.edges_created > 0 && (
                      <span className="text-xs block" style={{ color: 'var(--neo-text-muted)' }}>
                        +{job.edges_created} edges
                      </span>
                    )}
                    {job.entities_extracted > 0 && (
                      <span className="text-xs block" style={{ color: 'var(--neo-blue)' }}>
                        {job.entities_extracted} entities
                      </span>
                    )}
                  </div>
                  {(job.status === 'processing' || job.status === 'running' || job.status === 'queued') && (
                    <button
                      onClick={() => {
                        api.delete(`/dashboard/ingest/jobs/${job.job_id}?force=true`).then(() => fetchJobs()).catch(() => {});
                      }}
                      className="text-[10px] px-2 py-1 rounded"
                      style={{ color: '#ef4444', border: '1px solid #ef4444' }}
                      title="Force cancel this job"
                    >
                      Cancel
                    </button>
                  )}
                  {(job.status === 'failed' || job.status === 'completed' || job.status === 'completed_empty') && (
                    <button
                      onClick={() => {
                        api.post(`/dashboard/ingest/jobs/${job.job_id}/retry`).then((r) => {
                          fetchJobs();
                          toast.success(`Retrying: ${r.data.new_job_id || 'started'}`);
                        }).catch(() => {});
                      }}
                      className="text-[10px] px-2 py-1 rounded"
                      style={{ color: 'var(--neo-blue)', border: '1px solid var(--neo-blue)' }}
                      title="Retry this job from the beginning"
                    >
                      Retry
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

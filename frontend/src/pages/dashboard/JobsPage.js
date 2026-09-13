import React, { useState } from 'react';
import {
  Loader2, CheckCircle, XCircle, Clock, RefreshCw, Filter,
  ChevronDown, ChevronRight, FileText, Globe, Type,
} from 'lucide-react';
import { useJobs } from '../../context/JobsContext';

/* ── human-readable stage labels ── */
const STAGE_LABELS = {
  PARSE_FILE: 'Parse',
  CLASSIFY: 'Classify',
  CHUNK: 'Chunk',
  MAP_TABULAR: 'Map Tabular',
  EXTRACT: 'Extract Entities',
  EXTRACT_FACTS: 'Extract Facts',
  EMBED: 'Embed',
  INDEX_BM25: 'Index BM25',
  STORE_VECTORS: 'Store Vectors',
  CANONICALIZE: 'Canonicalize',
  ENHANCE_GRAPH: 'Enhance Graph',
  PERSIST: 'Persist',
  IMPORT_GRAPH: 'Import Graph',
  VALIDATE_SCHEMA: 'Validate Schema',
};

const STATUS_COLORS = {
  processing: 'var(--neo-blue)',
  completed: '#22c55e',
  failed: '#ef4444',
  pending: 'var(--neo-text-muted)',
};

function StageProgress({ stages }) {
  if (!stages || Object.keys(stages).length === 0) return null;
  const entries = Object.entries(stages);
  return (
    <div className="flex items-center gap-0.5 flex-wrap mt-1.5">
      {entries.map(([name, info], i) => {
        const isRunning = info.status === 'running';
        const isDone = info.status === 'completed';
        const isFailed = info.status === 'failed';
        return (
          <React.Fragment key={name}>
            <div
              className="flex items-center gap-1 px-1.5 py-0.5 rounded text-xs"
              style={{
                background: isDone ? 'rgba(34,197,94,0.1)' : isRunning ? 'rgba(0,122,255,0.1)' : isFailed ? 'rgba(239,68,68,0.1)' : 'var(--neo-surface)',
                border: `1px solid ${isDone ? 'rgba(34,197,94,0.3)' : isRunning ? 'rgba(0,122,255,0.3)' : isFailed ? 'rgba(239,68,68,0.3)' : 'var(--neo-border)'}`,
                color: isDone ? '#22c55e' : isRunning ? 'var(--neo-blue)' : isFailed ? '#ef4444' : 'var(--neo-text-muted)',
              }}
            >
              {isRunning && <Loader2 size={10} className="animate-spin" />}
              {isDone && <CheckCircle size={10} />}
              {isFailed && <XCircle size={10} />}
              {!isRunning && !isDone && !isFailed && <Clock size={10} />}
              <span>{STAGE_LABELS[name] || name}</span>
            </div>
            {i < entries.length - 1 && (
              <ChevronRight size={10} style={{ color: 'var(--neo-text-dim)' }} />
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
}

function SourceIcon({ source }) {
  if (!source) return <FileText size={14} />;
  const s = source.toLowerCase();
  if (s.includes('http') || s.includes('url')) return <Globe size={14} />;
  if (s.endsWith('.pdf') || s.endsWith('.docx') || s.endsWith('.xlsx')) return <FileText size={14} />;
  return <Type size={14} />;
}

function timeAgo(iso) {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export default function JobsPage() {
  const { jobs, activeCount, refreshJobs } = useJobs();
  const [filter, setFilter] = useState('all'); // all | processing | completed | failed
  const [expanded, setExpanded] = useState({});

  // Smart pipeline jobs (from JobManager)
  const [smartJobs, setSmartJobs] = useState([]);
  const [showLog, setShowLog] = useState(null);

  React.useEffect(() => {
    const fetchSmartJobs = () => {
      import('../../lib/api').then(({ default: api }) => {
        api.get('/dashboard/ingest/jobs')
          .then(r => {
            const incoming = r.data.jobs || [];
            if (incoming.length > 0) {
              setSmartJobs(prev => {
                const map = {};
                for (const j of prev) map[j.job_id] = j;
                for (const j of incoming) map[j.job_id] = j;
                return Object.values(map).sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
              });
            }
          })
          .catch(() => {});
      });
    };
    fetchSmartJobs();
    const interval = setInterval(fetchSmartJobs, 5000);
    return () => clearInterval(interval);
  }, []);

  const filtered = filter === 'all' ? jobs : jobs.filter(j => j.status === filter);

  const toggle = (id) => setExpanded(prev => ({ ...prev, [id]: !prev[id] }));

  return (
    <div className="max-w-4xl mx-auto space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold" style={{ color: 'var(--neo-text)' }}>Jobs</h1>
          <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
            {activeCount > 0 ? `${activeCount} running` : 'No active jobs'}
            {' · '}{jobs.length} total
          </p>
        </div>
        <button
          onClick={refreshJobs}
          className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs transition hover:opacity-80"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
        >
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {/* Filter tabs */}
      <div className="flex gap-1">
        {[
          { key: 'all', label: 'All' },
          { key: 'processing', label: 'Running' },
          { key: 'completed', label: 'Completed' },
          { key: 'failed', label: 'Failed' },
        ].map(f => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium transition"
            style={{
              background: filter === f.key ? 'var(--neo-blue)' : 'transparent',
              color: filter === f.key ? '#fff' : 'var(--neo-text-muted)',
              border: filter === f.key ? 'none' : '1px solid var(--neo-border)',
            }}
          >
            {f.key === 'processing' && <Loader2 size={10} className={filter === f.key ? 'animate-spin' : ''} />}
            {f.key === 'completed' && <CheckCircle size={10} />}
            {f.key === 'failed' && <XCircle size={10} />}
            {f.key === 'all' && <Filter size={10} />}
            {f.label}
          </button>
        ))}
      </div>

      {/* Job list */}
      {filtered.length === 0 ? (
        <div className="text-center py-12">
          <Clock size={32} className="mx-auto mb-2" style={{ color: 'var(--neo-text-dim)' }} />
          <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
            {filter === 'all' ? 'No jobs yet. Start an ingestion from Contexts.' : `No ${filter} jobs.`}
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {filtered.map(job => {
            const isExpanded = expanded[job.job_id];
            return (
              <div
                key={job.job_id}
                className="rounded-lg overflow-hidden"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
              >
                {/* Job header row */}
                <button
                  className="w-full flex items-center gap-3 p-3 text-left hover:opacity-90 transition"
                  style={{ background: 'transparent', border: 'none', cursor: 'pointer' }}
                  onClick={() => toggle(job.job_id)}
                >
                  {/* Status icon */}
                  {job.status === 'processing' ? (
                    <Loader2 size={16} className="animate-spin" style={{ color: STATUS_COLORS.processing }} />
                  ) : job.status === 'completed' ? (
                    <CheckCircle size={16} style={{ color: STATUS_COLORS.completed }} />
                  ) : (
                    <XCircle size={16} style={{ color: STATUS_COLORS.failed }} />
                  )}

                  {/* Source info */}
                  <div className="flex items-center gap-1.5 flex-1 min-w-0">
                    <SourceIcon source={job.source} />
                    <span className="text-sm font-medium truncate" style={{ color: 'var(--neo-text)' }}>
                      {job.source}
                    </span>
                    {job.context_id && (
                      <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'rgba(0,122,255,0.1)', color: 'var(--neo-blue)' }}>
                        context
                      </span>
                    )}
                  </div>

                  {/* Current stage label */}
                  {job.status === 'processing' && job.current_stage && (
                    <span className="text-xs px-2 py-0.5 rounded-full" style={{ background: 'rgba(0,122,255,0.1)', color: 'var(--neo-blue)' }}>
                      {STAGE_LABELS[job.current_stage] || job.current_stage}
                    </span>
                  )}

                  {/* Result counts */}
                  {job.status === 'completed' && (
                    <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                      {job.nodes_created} nodes, {job.edges_created || 0} edges
                    </span>
                  )}

                  {/* Error preview */}
                  {job.status === 'failed' && (
                    <span className="text-xs truncate" style={{ color: '#ef4444', maxWidth: 200 }}>
                      {typeof job.error === 'string' ? job.error.slice(0, 60) : 'Error'}
                    </span>
                  )}

                  {/* Time + expand */}
                  <span className="text-xs whitespace-nowrap" style={{ color: 'var(--neo-text-dim)' }}>
                    {timeAgo(job.created_at)}
                  </span>
                  {isExpanded ? <ChevronDown size={14} style={{ color: 'var(--neo-text-muted)' }} />
                             : <ChevronRight size={14} style={{ color: 'var(--neo-text-muted)' }} />}
                </button>

                {/* Expanded detail */}
                {isExpanded && (
                  <div className="px-3 pb-3 space-y-2" style={{ borderTop: '1px solid var(--neo-border)' }}>
                    {/* Stage pipeline */}
                    <StageProgress stages={job.stages} />

                    {/* Metadata grid */}
                    <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs mt-2" style={{ color: 'var(--neo-text-muted)' }}>
                      <span>Job ID</span><span style={{ color: 'var(--neo-text)' }}>{job.job_id}</span>
                      <span>Graph</span><span style={{ color: 'var(--neo-text)' }}>{job.graph || '—'}</span>
                      {job.llm_model && <><span>LLM</span><span style={{ color: 'var(--neo-text)' }}>{job.llm_model}</span></>}
                      {job.embedding_model && <><span>Embeddings</span><span style={{ color: 'var(--neo-text)' }}>{job.embedding_model}</span></>}
                      <span>Started</span><span style={{ color: 'var(--neo-text)' }}>{job.created_at ? new Date(job.created_at).toLocaleString() : '—'}</span>
                      {job.status === 'completed' && <>
                        <span>Nodes</span><span style={{ color: 'var(--neo-text)' }}>{job.nodes_created}</span>
                        <span>Edges</span><span style={{ color: 'var(--neo-text)' }}>{job.edges_created || 0}</span>
                      </>}
                    </div>

                    {/* Error detail */}
                    {job.error && (
                      <div className="text-xs p-2 rounded" style={{ background: 'rgba(239,68,68,0.08)', color: '#ef4444', whiteSpace: 'pre-wrap' }}>
                        {typeof job.error === 'string' ? job.error : JSON.stringify(job.error, null, 2)}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Smart Pipeline Jobs */}
      {smartJobs.length > 0 && (
        <div className="space-y-2 mt-6">
          <h2 className="text-sm font-semibold" style={{ color: 'var(--neo-text)' }}>
            Smart Pipeline Jobs ({smartJobs.length})
          </h2>
          {smartJobs.map(job => {
            const statusColors = {
              queued: 'var(--neo-text-muted)', running: 'var(--neo-blue)',
              review_pending: '#f59e0b', completed: '#22c55e', failed: '#ef4444',
            };
            const color = statusColors[job.status] || 'var(--neo-border)';
            return (
              <div key={job.job_id} className="p-3 rounded-xl"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full" style={{ background: color }} />
                    <span className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>
                      {job.input_summary?.slice(0, 60) || job.job_type}
                    </span>
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-medium"
                      style={{ background: color + '22', color }}>
                      {job.status}
                    </span>
                    <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                      {job.pipeline}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    {job.created_at && (
                      <span className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
                        {timeAgo(job.created_at)}
                      </span>
                    )}
                    <button onClick={() => setShowLog(showLog === job.job_id ? null : job.job_id)}
                      className="text-[10px] px-2 py-0.5 rounded transition hover:opacity-80"
                      style={{ color: 'var(--neo-cyan)', border: '1px solid var(--neo-border)' }}>
                      {showLog === job.job_id ? 'Hide log' : 'Show log'}
                    </button>
                  </div>
                </div>

                {/* Progress */}
                {job.progress && job.status === 'running' && (
                  <div className="mt-1 text-xs" style={{ color: 'var(--neo-blue)' }}>
                    Stage: {job.progress}
                  </div>
                )}

                {/* Result summary */}
                {job.result && job.status === 'completed' && (
                  <div className="mt-1 text-xs" style={{ color: '#22c55e' }}>
                    {job.result.passages !== undefined && (
                      <span>{job.result.passages} passages, {job.result.entities} entities, {job.result.facts || 0} facts, {job.result.edges} edges</span>
                    )}
                    {job.result.ingested && (
                      <div>
                        <span>Crawl: {job.result.ingested.length} ingested, {job.result.filtered?.length || 0} filtered</span>
                        {job.result.ingested.map((p, i) => (
                          <div key={i} className="ml-2 mt-0.5" style={{ color: 'var(--neo-text)' }}>
                            {p.title?.slice(0, 50)} — {p.passages || 0}p, {p.entities || 0}e, {p.facts || 0}f, {p.edges || 0} edges
                            {p.entity_names?.length > 0 && (
                              <span style={{ color: 'var(--neo-text-muted)' }}> [{p.entity_names.slice(0, 3).join(', ')}]</span>
                            )}
                          </div>
                        ))}
                        {job.result.filtered?.length > 0 && (
                          <div className="mt-1" style={{ color: 'var(--neo-text-muted)' }}>
                            Filtered: {job.result.filtered.slice(0, 5).map(f => f.title?.slice(0, 30) || f.url?.slice(0, 30)).join(', ')}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}

                {/* Error */}
                {job.error && (
                  <div className="mt-1 text-xs" style={{ color: '#ef4444' }}>{job.error}</div>
                )}

                {/* Log viewer */}
                {showLog === job.job_id && job.log && (
                  <div className="mt-2 p-2.5 rounded-lg space-y-0.5 max-h-60 overflow-y-auto"
                    style={{ background: 'var(--neo-bg)', fontFamily: 'monospace', fontSize: 11 }}>
                    {job.log.map((entry, i) => (
                      <div key={i} style={{ color: 'var(--neo-text-muted)' }}>
                        <span style={{ color: 'var(--neo-cyan)' }}>
                          {new Date(entry.timestamp).toLocaleTimeString()}
                        </span>
                        {' '}
                        <span style={{ color: entry.stage === 'error' ? '#ef4444' : entry.stage === 'complete' ? '#22c55e' : 'var(--neo-text)' }}>
                          [{entry.stage}]
                        </span>
                        {' '}
                        {entry.details?.message || entry.message}
                        {/* Chunk / embed stats inline */}
                        {(entry.details?.chunk_count != null || entry.details?.total_chunks != null ||
                          entry.details?.embedded != null || entry.details?.nodes_to_embed != null) && (
                          <span style={{ color: 'var(--neo-cyan)', marginLeft: 8, fontSize: 10 }}>
                            {entry.details.chunk_count != null && `chunks:${entry.details.chunk_count} `}
                            {entry.details.avg_chars != null && `avg:${entry.details.avg_chars}ch `}
                            {entry.details.total_chunks != null && `total_chunks:${entry.details.total_chunks} `}
                            {entry.details.pages_skipped > 0 && `skipped_pages:${entry.details.pages_skipped} `}
                            {entry.details.nodes_to_embed != null && `to_embed:${entry.details.nodes_to_embed} `}
                            {entry.details.embedded != null && `embedded:${entry.details.embedded} `}
                            {entry.details.skipped > 0 && `no_text:${entry.details.skipped}`}
                          </span>
                        )}
                        {entry.duration_ms > 0 && (
                          <span style={{ color: 'var(--neo-text-dim)', marginLeft: 8, fontSize: 10 }}>
                            {entry.duration_ms < 1000 ? `${entry.duration_ms}ms` : `${(entry.duration_ms/1000).toFixed(1)}s`}
                          </span>
                        )}
                        {entry.details?.entities?.length > 0 && (
                          <div style={{ color: 'var(--neo-text-dim)', marginLeft: 16 }}>
                            {entry.details.entities.slice(0, 8).map((e, j) => (
                              <div key={j}>{e}</div>
                            ))}
                            {entry.details.entities.length > 8 && <div>... +{entry.details.entities.length - 8} more</div>}
                          </div>
                        )}
                        {entry.details?.relationships?.length > 0 && (
                          <div style={{ color: 'var(--neo-cyan)', marginLeft: 16 }}>
                            {entry.details.relationships.slice(0, 5).map((r, j) => (
                              <div key={j}>{r}</div>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {/* Sub-jobs (review mode) */}
                {job.sub_jobs && job.sub_jobs.length > 0 && (
                  <div className="mt-2 text-xs">
                    <span style={{ color: 'var(--neo-text-muted)' }}>
                      Sub-jobs: {job.sub_jobs_accepted || 0} accepted, {job.sub_jobs_rejected || 0} rejected, {job.sub_jobs_pending || 0} pending
                    </span>
                  </div>
                )}

                {/* Schema suggestions */}
                {job.result?.schema_suggestions?.length > 0 && (
                  <div className="mt-2 p-2 rounded" style={{ background: 'rgba(139,92,246,0.08)', border: '1px solid rgba(139,92,246,0.3)' }}>
                    <span className="text-[10px] font-medium" style={{ color: '#8b5cf6' }}>
                      Schema suggestions:
                    </span>
                    {job.result.schema_suggestions.map((s, i) => (
                      <div key={i} className="text-[10px] mt-0.5" style={{ color: 'var(--neo-text)' }}>
                        + {s.type === 'new_node_type' ? 'Node' : 'Edge'}: <strong>{s.name}</strong> ({s.count}x)
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

      {/* Empty state for smart jobs */}
      {smartJobs.length === 0 && jobs.length === 0 && (
        <div className="text-center py-12">
          <Clock size={32} className="mx-auto mb-3" style={{ color: 'var(--neo-text-dim)', opacity: 0.3 }} />
          <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>No jobs yet</p>
          <p className="text-xs mt-1" style={{ color: 'var(--neo-text-dim)' }}>
            Submit an ingestion job from a Context to see it here
          </p>
        </div>
      )}
    </div>
  );
}

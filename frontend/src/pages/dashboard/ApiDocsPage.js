import React, { useState, useEffect } from 'react';
import {
  BookOpen, ChevronDown, ChevronRight, Copy, Check,
  Globe, FileText, Loader2,
} from 'lucide-react';
import api from '../../lib/api';

/* ── Static fallback endpoints ─────────────────────────────────── */
const STATIC_ENDPOINTS = [
  {
    tag: 'Ingestion',
    endpoints: [
      { method: 'POST', path: '/dashboard/ingest/text', summary: 'Ingest text content into a context' },
      { method: 'POST', path: '/dashboard/ingest/file', summary: 'Upload and ingest a file (PDF, TXT, MD, JSON, CSV)' },
      { method: 'POST', path: '/dashboard/ingest/url', summary: 'Crawl and ingest content from a URL' },
    ],
  },
  {
    tag: 'Graphs',
    endpoints: [
      { method: 'GET', path: '/dashboard/graphs', summary: 'List all graphs with node/edge counts' },
    ],
  },
  {
    tag: 'Contexts',
    endpoints: [
      { method: 'GET', path: '/dashboard/contexts', summary: 'List all contexts' },
      { method: 'POST', path: '/dashboard/contexts', summary: 'Create a new context' },
    ],
  },
  {
    tag: 'Agents',
    endpoints: [
      { method: 'POST', path: '/dashboard/agents', summary: 'Register a new agent and get an API key' },
      { method: 'GET', path: '/agent/sessions/{sid}/orient', summary: 'Get context briefing for a connected agent' },
      { method: 'POST', path: '/agent/sessions/{sid}/tasks/{tid}/claim', summary: 'Claim a task for an agent' },
    ],
  },
  {
    tag: 'Shield',
    endpoints: [
      { method: 'GET', path: '/shield/report-card', summary: 'Context quality report card' },
      { method: 'GET', path: '/shield/agents', summary: 'Agent trust scores' },
    ],
  },
  {
    tag: 'Cognition',
    endpoints: [
      { method: 'POST', path: '/dashboard/rag', summary: 'Ask a question using graph-enhanced RAG' },
    ],
  },
];

/* ── Method badge colors ───────────────────────────────────────── */
const METHOD_COLORS = {
  GET: { bg: 'rgba(76,217,100,0.15)', color: '#4cd964', border: 'rgba(76,217,100,0.3)' },
  POST: { bg: 'rgba(76,142,218,0.15)', color: '#4c8eda', border: 'rgba(76,142,218,0.3)' },
  PUT: { bg: 'rgba(255,179,64,0.15)', color: '#ffb340', border: 'rgba(255,179,64,0.3)' },
  PATCH: { bg: 'rgba(255,179,64,0.15)', color: '#ffb340', border: 'rgba(255,179,64,0.3)' },
  DELETE: { bg: 'rgba(239,68,68,0.15)', color: '#ef4444', border: 'rgba(239,68,68,0.3)' },
};

function MethodBadge({ method }) {
  const style = METHOD_COLORS[method] || METHOD_COLORS.GET;
  return (
    <span
      className="inline-block px-2 py-0.5 rounded text-xs font-mono font-bold uppercase"
      style={{ background: style.bg, color: style.color, border: `1px solid ${style.border}`, minWidth: 52, textAlign: 'center' }}
    >
      {method}
    </span>
  );
}

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <button
      onClick={handleCopy}
      className="p-1 rounded transition-colors"
      style={{ color: 'var(--neo-text-muted)' }}
      title="Copy"
    >
      {copied ? <Check size={13} style={{ color: 'var(--neo-green)' }} /> : <Copy size={13} />}
    </button>
  );
}

function buildCurl(method, path) {
  const base = window.location.origin;
  const url = `${base}${path}`;
  if (method === 'GET') {
    return `curl -X GET "${url}" \\\n  -H "Authorization: Bearer <TOKEN>"`;
  }
  return `curl -X ${method} "${url}" \\\n  -H "Authorization: Bearer <TOKEN>" \\\n  -H "Content-Type: application/json" \\\n  -d '{}'`;
}

/* ── Parse OpenAPI spec into grouped endpoints ─────────────────── */
function parseOpenApi(spec) {
  const groups = {};
  const paths = spec.paths || {};

  for (const [path, methods] of Object.entries(paths)) {
    for (const [method, detail] of Object.entries(methods)) {
      if (['get', 'post', 'put', 'patch', 'delete'].indexOf(method) === -1) continue;
      const tags = detail.tags || ['Other'];
      const tag = tags[0];
      if (!groups[tag]) groups[tag] = [];

      const params = [];
      if (detail.parameters) {
        for (const p of detail.parameters) {
          params.push({
            name: p.name,
            in: p.in,
            required: p.required || false,
            type: p.schema?.type || 'string',
            description: p.description || '',
          });
        }
      }

      groups[tag].push({
        method: method.toUpperCase(),
        path,
        summary: detail.summary || detail.description || '',
        description: detail.description || '',
        parameters: params,
        requestBody: detail.requestBody || null,
      });
    }
  }

  return Object.entries(groups)
    .map(([tag, endpoints]) => ({ tag, endpoints }))
    .sort((a, b) => a.tag.localeCompare(b.tag));
}

/* ── Endpoint row ──────────────────────────────────────────────── */
function EndpointRow({ ep }) {
  const [expanded, setExpanded] = useState(false);
  const curl = buildCurl(ep.method, ep.path);

  return (
    <div
      className="rounded-lg mb-2 overflow-hidden"
      style={{ border: '1px solid var(--neo-border)' }}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left transition-colors"
        style={{ background: expanded ? 'var(--neo-bg)' : 'transparent' }}
      >
        <MethodBadge method={ep.method} />
        <span className="font-mono text-sm flex-1" style={{ color: 'var(--neo-text)' }}>
          {ep.path}
        </span>
        <span className="text-xs hidden md:inline mr-2" style={{ color: 'var(--neo-text-muted)', maxWidth: 300 }}>
          {ep.summary}
        </span>
        {expanded ? <ChevronDown size={14} style={{ color: 'var(--neo-text-muted)' }} /> : <ChevronRight size={14} style={{ color: 'var(--neo-text-muted)' }} />}
      </button>

      {expanded && (
        <div className="px-4 pb-4 pt-2" style={{ background: 'var(--neo-bg)' }}>
          {ep.summary && (
            <p className="text-sm mb-3" style={{ color: 'var(--neo-text-muted)' }}>
              {ep.description || ep.summary}
            </p>
          )}

          {ep.parameters && ep.parameters.length > 0 && (
            <div className="mb-3">
              <h4 className="text-xs font-semibold uppercase mb-2" style={{ color: 'var(--neo-text-muted)' }}>
                Parameters
              </h4>
              <div className="space-y-1">
                {ep.parameters.map((p) => (
                  <div key={p.name} className="flex items-center gap-2 text-xs">
                    <code
                      className="px-1.5 py-0.5 rounded font-mono"
                      style={{ background: 'var(--neo-surface)', color: 'var(--neo-text)' }}
                    >
                      {p.name}
                    </code>
                    <span style={{ color: 'var(--neo-text-dim)' }}>{p.in}</span>
                    <span style={{ color: 'var(--neo-text-dim)' }}>{p.type}</span>
                    {p.required && (
                      <span className="text-xs" style={{ color: '#ef4444' }}>required</span>
                    )}
                    {p.description && (
                      <span style={{ color: 'var(--neo-text-muted)' }}>{p.description}</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          <div>
            <div className="flex items-center justify-between mb-1">
              <h4 className="text-xs font-semibold uppercase" style={{ color: 'var(--neo-text-muted)' }}>
                Example
              </h4>
              <CopyButton text={curl} />
            </div>
            <pre
              className="text-xs p-3 rounded-lg overflow-x-auto"
              style={{
                background: 'var(--neo-surface)',
                color: 'var(--neo-text)',
                border: '1px solid var(--neo-border)',
              }}
            >
              {curl}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Endpoint group ────────────────────────────────────────────── */
function EndpointGroup({ group }) {
  const [open, setOpen] = useState(true);

  return (
    <div className="mb-6">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 mb-3 w-full text-left"
      >
        {open ? (
          <ChevronDown size={16} style={{ color: 'var(--neo-text-muted)' }} />
        ) : (
          <ChevronRight size={16} style={{ color: 'var(--neo-text-muted)' }} />
        )}
        <h3 className="text-sm font-bold uppercase tracking-wider" style={{ color: 'var(--neo-text)' }}>
          {group.tag}
        </h3>
        <span className="text-xs" style={{ color: 'var(--neo-text-dim)' }}>
          ({group.endpoints.length})
        </span>
      </button>

      {open && (
        <div>
          {group.endpoints.map((ep, i) => (
            <EndpointRow key={`${ep.method}-${ep.path}-${i}`} ep={ep} />
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Main page ─────────────────────────────────────────────────── */
export default function ApiDocsPage() {
  const [groups, setGroups] = useState(null);
  const [source, setSource] = useState(null); // 'openapi' | 'static'
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.get('/openapi.json', { timeout: 5000 });
        if (!cancelled && res.data?.paths) {
          setGroups(parseOpenApi(res.data));
          setSource('openapi');
        } else {
          throw new Error('no paths');
        }
      } catch {
        if (!cancelled) {
          setGroups(STATIC_ENDPOINTS);
          setSource('static');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const filtered = groups
    ? groups
        .map((g) => ({
          ...g,
          endpoints: g.endpoints.filter(
            (ep) =>
              !filter ||
              ep.path.toLowerCase().includes(filter.toLowerCase()) ||
              (ep.summary || '').toLowerCase().includes(filter.toLowerCase()) ||
              g.tag.toLowerCase().includes(filter.toLowerCase())
          ),
        }))
        .filter((g) => g.endpoints.length > 0)
    : [];

  const totalEndpoints = groups ? groups.reduce((s, g) => s + g.endpoints.length, 0) : 0;

  return (
    <div className="max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <Globe size={24} style={{ color: 'var(--neo-blue)' }} />
        <div>
          <h1 className="text-2xl font-bold" style={{ color: 'var(--neo-text)' }}>
            API Reference
          </h1>
          <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
            {source === 'openapi'
              ? `${totalEndpoints} endpoints from OpenAPI spec`
              : source === 'static'
              ? 'Key endpoints (OpenAPI spec unavailable)'
              : 'Loading...'}
          </p>
        </div>
      </div>

      {/* Search filter */}
      <div className="mb-6">
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter endpoints... (e.g. ingest, agents, shield)"
          className="w-full px-3 py-2 rounded-lg text-sm outline-none"
          style={{
            background: 'var(--neo-surface)',
            border: '1px solid var(--neo-border)',
            color: 'var(--neo-text)',
          }}
        />
      </div>

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center py-12">
          <Loader2 size={24} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
        </div>
      )}

      {/* Endpoint groups */}
      {!loading && filtered.length === 0 && (
        <div className="text-center py-12">
          <FileText size={32} className="mx-auto mb-2" style={{ color: 'var(--neo-text-dim)' }} />
          <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
            No endpoints match your filter.
          </p>
        </div>
      )}

      {!loading &&
        filtered.map((group) => (
          <EndpointGroup key={group.tag} group={group} />
        ))}

      {/* Auth note */}
      {!loading && (
        <div
          className="mt-6 p-4 rounded-xl"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        >
          <h3 className="text-sm font-bold mb-2" style={{ color: 'var(--neo-text)' }}>
            <BookOpen size={14} className="inline mr-1" />
            Authentication
          </h3>
          <p className="text-xs mb-2" style={{ color: 'var(--neo-text-muted)' }}>
            All endpoints require a Bearer token. Dashboard endpoints use JWT tokens obtained at login.
            Agent endpoints use the API key returned when registering an agent.
          </p>
          <pre
            className="text-xs p-2 rounded"
            style={{ background: 'var(--neo-bg)', color: 'var(--neo-text)', border: '1px solid var(--neo-border)' }}
          >
            Authorization: Bearer {'<your-token>'}
          </pre>
        </div>
      )}
    </div>
  );
}

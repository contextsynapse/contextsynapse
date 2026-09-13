import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  Play, Save, Trash2, Loader2, Download, BookOpen, Clock, Copy, Check,
  GitBranch, ZoomIn, ZoomOut, Maximize2, LayoutGrid,
} from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../../lib/api';

let cytoscape, dagre;
try {
  cytoscape = require('cytoscape');
  dagre = require('cytoscape-dagre');
  try { cytoscape.use(dagre); } catch (_) {}
} catch (_) {}

const AIQL_CHEATSHEET = [
  { cmd: 'CREATE GRAPH mydb', desc: 'Create a new graph' },
  { cmd: 'USE GRAPH mydb', desc: 'Switch active graph' },
  { cmd: 'SHOW GRAPHS', desc: 'List all graphs' },
  { cmd: 'CREATE NODE Person {name: "Alice", age: 30}', desc: 'Create a node' },
  { cmd: 'SELECT * FROM Person', desc: 'Get all nodes of type' },
  { cmd: 'MATCH NODE Person WHERE name = "Alice"', desc: 'Filter nodes' },
  { cmd: 'CREATE EDGE KNOWS FROM alice TO bob {since: 2020}', desc: 'Create edge' },
  { cmd: 'TRAVERSE FROM alice DEPTH 2', desc: 'Graph traversal' },
  { cmd: 'COUNT Person', desc: 'Count nodes' },
];

const CY_STYLE = [
  { selector: 'node', style: {
    label: 'data(displayLabel)', 'background-color': 'data(color)', color: '#e8e8e8',
    'text-valign': 'bottom', 'text-halign': 'center', 'font-size': '11px', 'text-margin-y': 6,
    width: 34, height: 34, 'border-width': 2, 'border-color': 'data(color)', 'border-opacity': 0.5,
    'text-outline-color': '#0a0a0f', 'text-outline-width': 2, 'text-max-width': '100px', 'text-wrap': 'ellipsis',
  }},
  { selector: 'node:selected', style: { 'border-width': 3, 'border-color': '#60a5fa', width: 40, height: 40 }},
  { selector: 'edge', style: {
    label: 'data(displayLabel)', 'line-color': 'data(color)', 'target-arrow-color': 'data(color)',
    'target-arrow-shape': 'triangle', 'curve-style': 'bezier', width: 2.5, 'font-size': '9px',
    color: '#94a3b8', 'text-rotation': 'autorotate', 'text-outline-color': '#0a0a0f', 'text-outline-width': 2,
    'arrow-scale': 0.9, 'line-opacity': 0.8,
  }},
  { selector: 'edge:selected', style: { 'line-color': '#60a5fa', 'target-arrow-color': '#60a5fa', width: 3 }},
];

const LAYOUTS = [
  { id: 'dagre', label: 'Hierarchical' },
  { id: 'cose', label: 'Force' },
  { id: 'circle', label: 'Circle' },
  { id: 'grid', label: 'Grid' },
  { id: 'breadthfirst', label: 'Tree' },
];

function layoutConfig(name) {
  const base = { name, animate: true, animationDuration: 300 };
  switch (name) {
    case 'dagre': return { ...base, rankDir: 'TB', nodeSep: 60, rankSep: 80 };
    case 'cose': return { ...base, nodeRepulsion: 8000, idealEdgeLength: 120, animate: false };
    case 'circle': return { ...base, spacingFactor: 1.5 };
    case 'grid': return { ...base, spacingFactor: 1.2 };
    case 'breadthfirst': return { ...base, spacingFactor: 1.3 };
    default: return base;
  }
}

function goldenColor(index, sat, light) {
  const hue = (index * 137.508) % 360;
  return `hsl(${hue.toFixed(1)}, ${sat}%, ${light}%)`;
}

// ── Graph Visualization Panel ────────────────────────────────────────

function GraphPanel({ nodes, edges }) {
  const containerRef = useRef(null);
  const cyRef = useRef(null);
  const colorMapRef = useRef({ nodes: {}, edges: {} });
  const [layout, setLayout] = useState('dagre');
  const [selectedItem, setSelectedItem] = useState(null);

  const assignColor = useCallback((kind, type) => {
    if (colorMapRef.current[kind][type]) return colorMapRef.current[kind][type];
    const count = Object.keys(colorMapRef.current[kind]).length;
    const sat = kind === 'nodes' ? 65 : 80;
    const light = kind === 'nodes' ? 55 : 65;
    const c = goldenColor(count + (kind === 'edges' ? 0.43 : 0), sat, light);
    colorMapRef.current[kind][type] = c;
    return c;
  }, []);

  const cyElements = useMemo(() => {
    const elements = [];
    (nodes || []).forEach((node) => {
      const type = node.label || 'default';
      const displayLabel = node.name || node.properties?.name || type;
      elements.push({ data: { ...node, ...node.properties, displayLabel, color: assignColor('nodes', type) } });
    });
    const nodeIds = new Set(elements.map(el => el.data.id));
    (edges || []).forEach((edge) => {
      const type = edge.label || 'EDGE';
      if (edge.source && edge.target && nodeIds.has(edge.source) && nodeIds.has(edge.target)) {
        elements.push({ data: {
          ...edge, ...edge.properties,
          id: edge.id || `${edge.source}-${edge.target}-${type}`,
          displayLabel: type, color: assignColor('edges', type),
        }});
      }
    });
    return elements;
  }, [nodes, edges, assignColor]);

  useEffect(() => {
    if (!containerRef.current || !cytoscape) return;
    const cy = cytoscape({ container: containerRef.current, elements: [], style: CY_STYLE, wheelSensitivity: 0.8, minZoom: 0.15, maxZoom: 4 });
    cy.on('tap', 'node', (evt) => setSelectedItem(evt.target.data()));
    cy.on('tap', 'edge', (evt) => setSelectedItem(evt.target.data()));
    cy.on('tap', (evt) => { if (evt.target === cy) setSelectedItem(null); });
    cyRef.current = cy;
    return () => cy.destroy();
  }, []);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const newIds = new Set(cyElements.map(el => el.data.id));
    cy.elements().forEach(ele => { if (!newIds.has(ele.id())) ele.remove(); });
    cyElements.forEach(el => {
      const existing = cy.getElementById(el.data.id);
      if (existing.length === 0) cy.add(el); else existing.data(el.data);
    });
    if (cy.elements().length > 0) {
      try { cy.layout(layoutConfig(layout)).run(); } catch (_) { cy.layout(layoutConfig('cose')).run(); }
      cy.resize(); cy.fit(undefined, 40);
    }
  }, [cyElements, layout]);

  const nodeTypes = useMemo(() => { const t = {}; (nodes || []).forEach(n => t[n.label || 'default'] = true); return t; }, [nodes]);
  const edgeTypes = useMemo(() => { const t = {}; (edges || []).forEach(e => t[e.label || 'EDGE'] = true); return t; }, [edges]);

  const SKIP = new Set(['id','uuid','label','type','source','target','displayLabel','color','from_id','to_id','properties']);

  return (
    <div className="flex" style={{ height: 400 }}>
      {/* Canvas */}
      <div className="flex-1 relative">
        <div ref={containerRef} style={{ position: 'absolute', inset: 0, background: '#0a0a0f' }} />
        <div className="absolute top-2 left-2 z-10 text-xs" style={{ color: 'var(--neo-text-muted)' }}>
          {(nodes || []).length} nodes, {(edges || []).length} edges
        </div>
        <div className="absolute top-2 right-2 flex flex-col gap-1 z-10">
          <button onClick={() => cyRef.current?.zoom(cyRef.current.zoom() * 1.3)} className="p-1.5 rounded" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}><ZoomIn size={13} /></button>
          <button onClick={() => cyRef.current?.zoom(cyRef.current.zoom() / 1.3)} className="p-1.5 rounded" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}><ZoomOut size={13} /></button>
          <button onClick={() => cyRef.current?.fit(undefined, 40)} className="p-1.5 rounded" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}><Maximize2 size={13} /></button>
        </div>
        {(nodes || []).length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center">
            <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>No graph data to visualize</p>
          </div>
        )}
      </div>
      {/* Sidebar */}
      <div className="w-48 overflow-y-auto p-3" style={{ borderLeft: '1px solid var(--neo-border)', background: 'var(--neo-surface)' }}>
        <div className="mb-2">
          <div className="flex items-center gap-1 mb-1 text-xs font-medium" style={{ color: 'var(--neo-text-muted)' }}><LayoutGrid size={11} /> Layout</div>
          <select value={layout} onChange={e => setLayout(e.target.value)} className="w-full px-2 py-1 rounded text-xs outline-none" style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}>
            {LAYOUTS.map(l => <option key={l.id} value={l.id}>{l.label}</option>)}
          </select>
        </div>
        {Object.keys(nodeTypes).length > 0 && (
          <div className="mb-2">
            <div className="text-xs font-medium mb-1" style={{ color: 'var(--neo-text-muted)' }}>Nodes</div>
            {Object.keys(nodeTypes).map(t => (
              <div key={t} className="flex items-center gap-1.5 text-xs py-0.5">
                <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: colorMapRef.current.nodes[t] || '#3498db' }} />
                <span style={{ color: 'var(--neo-text)' }}>{t}</span>
              </div>
            ))}
          </div>
        )}
        {Object.keys(edgeTypes).length > 0 && (
          <div className="mb-2">
            <div className="text-xs font-medium mb-1" style={{ color: 'var(--neo-text-muted)' }}>Edges</div>
            {Object.keys(edgeTypes).map(t => (
              <div key={t} className="flex items-center gap-1.5 text-xs py-0.5">
                <span className="w-2.5 h-1 rounded" style={{ backgroundColor: colorMapRef.current.edges[t] || '#ff6b6b' }} />
                <span style={{ color: 'var(--neo-text)' }}>{t}</span>
              </div>
            ))}
          </div>
        )}
        {selectedItem && (
          <>
            <div className="my-2" style={{ borderTop: '1px solid var(--neo-border)' }} />
            <div className="text-xs font-bold mb-1" style={{ color: 'var(--neo-blue)' }}>
              {selectedItem.source ? 'Edge' : 'Node'}: {selectedItem.label || 'unknown'}
            </div>
            <div className="space-y-0.5">
              {Object.entries(selectedItem).filter(([k]) => !SKIP.has(k) && !k.startsWith('_')).map(([k, v]) => (
                <div key={k} className="flex justify-between text-xs gap-1">
                  <span style={{ color: 'var(--neo-text-muted)' }}>{k}</span>
                  <span className="text-right truncate" style={{ color: 'var(--neo-text)', maxWidth: 100 }}>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ── Main QueryPage ───────────────────────────────────────────────────

export default function QueryPage() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState(null);
  const [running, setRunning] = useState(false);
  const [viewMode, setViewMode] = useState('table'); // table | json | graph
  const [graphs, setGraphs] = useState([]);
  const [selectedGraph, setSelectedGraph] = useState('');
  const [savedQueries, setSavedQueries] = useState([]);
  const [history, setHistory] = useState([]);
  const [showCheatsheet, setShowCheatsheet] = useState(false);
  const [showSaved, setShowSaved] = useState(false);
  const [saveName, setSaveName] = useState('');
  const [showSaveInput, setShowSaveInput] = useState(false);
  const [copied, setCopied] = useState(false);

  const fetchGraphs = useCallback(() => {
    api.get('/dashboard/graphs')
      .then((res) => {
        const g = res.data.graphs || [];
        setGraphs(g);
        if (g.length > 0 && !selectedGraph) setSelectedGraph(g[0].name);
      })
      .catch(() => {});
  }, [selectedGraph]);

  const fetchSaved = useCallback(() => {
    api.get('/dashboard/queries/saved')
      .then((res) => setSavedQueries(res.data.queries || []))
      .catch(() => {});
  }, []);

  const fetchHistory = useCallback(() => {
    api.get('/dashboard/queries/history')
      .then((res) => setHistory(res.data.history || []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetchGraphs();
    fetchSaved();
    fetchHistory();
  }, [fetchGraphs, fetchSaved, fetchHistory]);

  const handleRun = async () => {
    if (!query.trim()) return;
    setRunning(true);
    setResults(null);
    try {
      const res = await api.post('/dashboard/query/execute', {
        query: query.trim(),
        graph: selectedGraph,
      });
      setResults(res.data);
      setHistory((prev) => [
        { query: query.trim(), timestamp: new Date().toISOString(), status: 'success' },
        ...prev.slice(0, 19),
      ]);
      // Auto-switch to graph view if nodes are present
      if ((res.data.nodes?.length || 0) > 0) {
        setViewMode('graph');
      }
    } catch (err) {
      setResults({ error: err.response?.data?.detail || 'Query execution failed' });
    } finally {
      setRunning(false);
    }
  };

  const handleSave = async () => {
    if (!saveName.trim() || !query.trim()) return;
    try {
      await api.post('/dashboard/queries/saved', { name: saveName.trim(), query: query.trim() });
      toast.success('Query saved');
      setSaveName('');
      setShowSaveInput(false);
      fetchSaved();
    } catch {}
  };

  const handleDeleteSaved = async (id) => {
    try {
      await api.delete(`/dashboard/queries/saved/${id}`);
      toast.success('Query deleted');
      fetchSaved();
    } catch {}
  };

  const handleExport = (format) => {
    if (!results?.data) return;
    let content, filename, type;
    if (format === 'json') {
      content = JSON.stringify(results.data, null, 2);
      filename = 'query_results.json';
      type = 'application/json';
    } else {
      const data = results.data;
      if (!Array.isArray(data) || data.length === 0) return;
      const headers = Object.keys(data[0]);
      const rows = data.map((row) => headers.map((h) => JSON.stringify(row[h] ?? '')).join(','));
      content = [headers.join(','), ...rows].join('\n');
      filename = 'query_results.csv';
      type = 'text/csv';
    }
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleCopyResults = () => {
    if (!results?.data) return;
    navigator.clipboard.writeText(JSON.stringify(results.data, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleKeyDown = (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); handleRun(); }
  };

  const hasGraphData = (results?.nodes?.length || 0) > 0 || (results?.edges?.length || 0) > 0;

  return (
    <div className="max-w-5xl">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-bold" style={{ color: 'var(--neo-text)' }}>Query Workspace</h1>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowSaved(!showSaved)} className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-80" style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}>
            <BookOpen size={14} /> Saved ({savedQueries.length})
          </button>
          <button onClick={() => setShowCheatsheet(!showCheatsheet)} className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-80" style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}>
            <BookOpen size={14} /> AIQL Reference
          </button>
        </div>
      </div>

      <div className="flex gap-4">
        {/* Main query area */}
        <div className="flex-1 space-y-4">
          {/* Graph selector + editor */}
          <div className="rounded-xl overflow-hidden" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
            <div className="flex items-center justify-between px-4 py-2 border-b" style={{ borderColor: 'var(--neo-border)' }}>
              <div className="flex items-center gap-2">
                <select value={selectedGraph} onChange={(e) => setSelectedGraph(e.target.value)} className="px-2 py-1 rounded text-xs outline-none" style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}>
                  <option value="">All graphs</option>
                  {graphs.map((g) => <option key={g.name} value={g.display_name || g.name}>{g.display_name || g.name}</option>)}
                </select>
              </div>
              <div className="flex items-center gap-1.5">
                <button onClick={() => { if (query.trim()) setShowSaveInput(true); }} className="p-1.5 rounded transition hover:opacity-70" style={{ color: 'var(--neo-text-muted)' }} title="Save query"><Save size={15} /></button>
                <button onClick={handleRun} disabled={running || !query.trim()} className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90 disabled:opacity-50" style={{ background: 'var(--neo-green)', color: '#fff' }}>
                  {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />} Run
                </button>
              </div>
            </div>

            {showSaveInput && (
              <div className="flex items-center gap-2 px-4 py-2 border-b" style={{ borderColor: 'var(--neo-border)' }}>
                <input value={saveName} onChange={(e) => setSaveName(e.target.value)} placeholder="Query name..." className="flex-1 px-2 py-1 rounded text-xs outline-none" style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} autoFocus onKeyDown={(e) => e.key === 'Enter' && handleSave()} />
                <button onClick={handleSave} className="text-xs px-2 py-1 rounded" style={{ background: 'var(--neo-blue)', color: '#fff' }}>Save</button>
                <button onClick={() => setShowSaveInput(false)} className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Cancel</button>
              </div>
            )}

            <textarea value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={handleKeyDown} placeholder="Enter AIQL query... (Ctrl+Enter to run)" rows={6} className="w-full px-4 py-3 text-sm outline-none resize-none" style={{ background: 'var(--neo-bg)', color: 'var(--neo-text)', fontFamily: "'JetBrains Mono', 'Fira Code', monospace" }} />
          </div>

          {/* Results */}
          {results && (
            <div className="rounded-xl overflow-hidden" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
              <div className="flex items-center justify-between px-4 py-2 border-b" style={{ borderColor: 'var(--neo-border)' }}>
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium" style={{ color: 'var(--neo-text-muted)' }}>Results</span>
                  {results.duration_ms != null && (
                    <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>({results.duration_ms}ms)</span>
                  )}
                  {results.data && Array.isArray(results.data) && (
                    <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>({results.data.length} rows)</span>
                  )}
                </div>
                <div className="flex items-center gap-1">
                  {/* View mode tabs */}
                  {['table', 'json', ...(hasGraphData ? ['graph'] : [])].map((mode) => (
                    <button
                      key={mode}
                      onClick={() => setViewMode(mode)}
                      className="px-2 py-1 rounded text-xs font-medium"
                      style={{
                        background: viewMode === mode ? 'var(--neo-bg)' : 'transparent',
                        color: viewMode === mode ? 'var(--neo-text)' : 'var(--neo-text-muted)',
                        border: viewMode === mode ? '1px solid var(--neo-border)' : '1px solid transparent',
                      }}
                    >
                      {mode === 'graph' ? <GitBranch size={13} className="inline mr-0.5" /> : null}
                      {mode.charAt(0).toUpperCase() + mode.slice(1)}
                    </button>
                  ))}
                  <span className="mx-1" style={{ borderLeft: '1px solid var(--neo-border)', height: 16 }} />
                  <button onClick={handleCopyResults} className="p-1 rounded" style={{ color: 'var(--neo-text-muted)' }}>
                    {copied ? <Check size={14} /> : <Copy size={14} />}
                  </button>
                  <button onClick={() => handleExport('csv')} className="p-1 rounded" style={{ color: 'var(--neo-text-muted)' }} title="Export CSV">
                    <Download size={14} />
                  </button>
                </div>
              </div>

              {/* Graph view */}
              {viewMode === 'graph' && hasGraphData && (
                <GraphPanel nodes={results.nodes} edges={results.edges} />
              )}

              {/* Table / JSON view */}
              {viewMode !== 'graph' && (
                <div className="max-h-96 overflow-auto">
                  {results.error ? (
                    <div className="p-4 text-sm" style={{ color: '#ef4444' }}>
                      {typeof results.error === 'string' ? results.error : JSON.stringify(results.error)}
                    </div>
                  ) : viewMode === 'json' ? (
                    <pre className="p-4 text-xs whitespace-pre-wrap" style={{ color: 'var(--neo-text)', fontFamily: "'JetBrains Mono', monospace" }}>
                      {JSON.stringify(results.data || results, null, 2)}
                    </pre>
                  ) : Array.isArray(results.data) && results.data.length > 0 ? (
                    <table className="w-full text-xs">
                      <thead>
                        <tr style={{ borderBottom: '1px solid var(--neo-border)' }}>
                          {Object.keys(results.data[0]).map((key) => (
                            <th key={key} className="px-3 py-2 text-left font-medium" style={{ color: 'var(--neo-text-muted)' }}>{key}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {results.data.map((row, i) => (
                          <tr key={i} style={{ borderBottom: '1px solid var(--neo-border)' }}>
                            {Object.values(row).map((val, j) => (
                              <td key={j} className="px-3 py-2" style={{ color: 'var(--neo-text)' }}>
                                {typeof val === 'object' ? JSON.stringify(val) : String(val ?? '')}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <pre className="p-4 text-xs whitespace-pre-wrap" style={{ color: 'var(--neo-text)', fontFamily: "'JetBrains Mono', monospace" }}>
                      {JSON.stringify(results.data || results, null, 2)}
                    </pre>
                  )}
                </div>
              )}
            </div>
          )}

          {/* History */}
          {history.length > 0 && (
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wider mb-2" style={{ color: 'var(--neo-text-muted)' }}>
                <Clock size={12} className="inline mr-1" /> Recent Queries
              </h3>
              <div className="space-y-1">
                {history.slice(0, 10).map((h, i) => (
                  <button key={i} onClick={() => setQuery(h.query)} className="block w-full text-left px-3 py-2 rounded-lg text-xs truncate transition hover:opacity-80" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)', fontFamily: "'JetBrains Mono', monospace" }}>
                    {h.query}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Sidebar panels */}
        {(showSaved || showCheatsheet) && (
          <div className="w-72 shrink-0 space-y-4">
            {showSaved && (
              <div className="rounded-xl p-4" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
                <h3 className="text-sm font-semibold mb-3" style={{ color: 'var(--neo-text)' }}>Saved Queries</h3>
                {savedQueries.length === 0 ? (
                  <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>No saved queries</p>
                ) : (
                  <div className="space-y-2">
                    {savedQueries.map((sq) => (
                      <div key={sq.query_id} className="flex items-center justify-between">
                        <button onClick={() => setQuery(sq.query)} className="text-xs text-left truncate flex-1 mr-2" style={{ color: 'var(--neo-blue)' }}>{sq.name}</button>
                        <button onClick={() => handleDeleteSaved(sq.query_id)} className="p-1 shrink-0" style={{ color: 'var(--neo-text-muted)' }}><Trash2 size={12} /></button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {showCheatsheet && (
              <div className="rounded-xl p-4" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
                <h3 className="text-sm font-semibold mb-3" style={{ color: 'var(--neo-text)' }}>AIQL Reference</h3>
                <div className="space-y-2">
                  {AIQL_CHEATSHEET.map((item, i) => (
                    <button key={i} onClick={() => setQuery(item.cmd)} className="block w-full text-left">
                      <code className="text-xs block truncate" style={{ color: 'var(--neo-blue)', fontFamily: "'JetBrains Mono', monospace" }}>{item.cmd}</code>
                      <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>{item.desc}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

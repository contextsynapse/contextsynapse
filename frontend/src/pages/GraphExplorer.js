/**
 * GraphExplorer — Cytoscape.js-powered graph visualization.
 *
 * Replaces the legacy canvas renderer with Cytoscape for proper layouts,
 * pan/zoom, and click-to-inspect.  The query interface and example queries
 * are preserved from the original App.js.
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import dagre from 'cytoscape-dagre';
import cytoscape from 'cytoscape';
import axios from 'axios';

import GraphControls from '../components/GraphControls';
import PropertyPanel from '../components/PropertyPanel';

import '../App.css';  // existing dark theme styles

// Register dagre layout once
try { cytoscape.use(dagre); } catch (_) { /* already registered */ }

// ── Color helpers ────────────────────────────────────────────────────

function goldenColor(index, sat, light) {
  const hue = (index * 137.508) % 360;
  return `hsl(${hue.toFixed(1)}, ${sat}%, ${light}%)`;
}

// ── Cytoscape stylesheet ─────────────────────────────────────────────

const CY_STYLE = [
  {
    selector: 'node',
    style: {
      label: 'data(displayLabel)',
      'background-color': 'data(color)',
      color: '#e8e8e8',
      'text-valign': 'bottom',
      'text-halign': 'center',
      'font-size': '11px',
      'text-margin-y': 6,
      width: 32,
      height: 32,
      'border-width': 2,
      'border-color': 'data(color)',
      'border-opacity': 0.5,
      'text-outline-color': '#1a1b1e',
      'text-outline-width': 2,
      'text-max-width': '100px',
      'text-wrap': 'ellipsis',
    },
  },
  {
    selector: 'node:selected',
    style: {
      'border-width': 3,
      'border-color': '#4c8eda',
      'border-opacity': 1,
      width: 38,
      height: 38,
    },
  },
  {
    selector: 'edge',
    style: {
      label: 'data(displayLabel)',
      'line-color': 'data(color)',
      'target-arrow-color': 'data(color)',
      'target-arrow-shape': 'triangle',
      'curve-style': 'bezier',
      width: 3,
      'font-size': '10px',
      color: '#c0c3cb',
      'text-rotation': 'autorotate',
      'text-outline-color': '#1a1b1e',
      'text-outline-width': 2,
      'arrow-scale': 1,
      'line-opacity': 0.85,
    },
  },
  {
    selector: 'edge:selected',
    style: {
      'line-color': '#4c8eda',
      'target-arrow-color': '#4c8eda',
      width: 3,
    },
  },
];

// ── Layout configs ───────────────────────────────────────────────────

function layoutConfig(name) {
  const base = { name, animate: true, animationDuration: 300 };
  switch (name) {
    case 'dagre':
      return { ...base, rankDir: 'TB', nodeSep: 60, rankSep: 80 };
    case 'cose':
      return { ...base, nodeRepulsion: 8000, idealEdgeLength: 120, animate: false };
    case 'circle':
      return { ...base, spacingFactor: 1.5 };
    case 'grid':
      return { ...base, spacingFactor: 1.2 };
    case 'concentric':
      return { ...base, minNodeSpacing: 60 };
    case 'breadthfirst':
      return { ...base, spacingFactor: 1.3 };
    default:
      return base;
  }
}

// ── Main component ───────────────────────────────────────────────────

export default function GraphExplorer() {
  // State
  const [query, setQuery] = useState('FIND nodes');
  const [results, setResults] = useState({ nodes: [], edges: [] });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [selectedItem, setSelectedItem] = useState(null);
  const [graphName, setGraphName] = useState('default');
  const [availableGraphs, setAvailableGraphs] = useState(['default', 'test', 'demo', 'production']);
  const [showCreateGraphModal, setShowCreateGraphModal] = useState(false);
  const [newGraphName, setNewGraphName] = useState('');
  const [queryResult, setQueryResult] = useState(null);
  const [, setExecutionTime] = useState(null);
  const [layout, setLayout] = useState('dagre');

  // Color maps
  const colorMapRef = useRef({ nodes: {}, edges: {} });
  const [colorMap, setColorMap] = useState({ nodes: {}, edges: {} });
  const [nodeTypeFilters, setNodeTypeFilters] = useState({});
  const [edgeTypeFilters, setEdgeTypeFilters] = useState({});

  const cyRef = useRef(null);
  const containerRef = useRef(null);

  // ── Color assignment ──────────────────────────────────────────────

  const assignColor = useCallback((kind, type) => {
    if (colorMapRef.current[kind][type]) return colorMapRef.current[kind][type];
    const count = Object.keys(colorMapRef.current[kind]).length;
    const sat = kind === 'nodes' ? 65 : 80;
    const light = kind === 'nodes' ? 55 : 65;
    const offset = kind === 'edges' ? 0.43 : 0;
    const c = goldenColor(count + offset, sat, light);
    colorMapRef.current[kind][type] = c;
    setColorMap({ ...colorMapRef.current });
    return c;
  }, []);

  // ── Convert results to Cytoscape elements ─────────────────────────

  const cyElements = React.useMemo(() => {
    const elements = [];

    (results.nodes || []).forEach((node) => {
      const type = node.label || node.type || 'default';
      const displayLabel = node.name || node.properties?.name || node.title || type;
      const color = colorMapRef.current.nodes[type] || assignColor('nodes', type);
      elements.push({
        data: {
          ...node,
          id: node.id || node.uuid,
          displayLabel,
          color,
        },
      });
    });

    const nodeIds = new Set(elements.map((el) => el.data.id));

    (results.edges || []).forEach((edge) => {
      const type = edge.label || edge.type || 'EDGE';
      const color = colorMapRef.current.edges[type] || assignColor('edges', type);
      const src = edge.source_id || edge.from_id || edge.source;
      const tgt = edge.target_id || edge.to_id || edge.target;
      if (src && tgt && nodeIds.has(src) && nodeIds.has(tgt)) {
        elements.push({
          data: {
            ...edge,
            id: edge.id || `${src}-${tgt}-${type}`,
            source: src,
            target: tgt,
            displayLabel: type,
            color,
          },
        });
      }
    });

    return elements;
  }, [results, assignColor]);

  // ── Update type filters + sync color map when data changes ────────

  useEffect(() => {
    const nt = {};
    (results.nodes || []).forEach((n) => {
      const t = n.label || n.type || 'default';
      if (!colorMapRef.current.nodes[t]) assignColor('nodes', t);
      nt[t] = true;
    });
    setNodeTypeFilters(nt);

    const et = {};
    (results.edges || []).forEach((e) => {
      const t = e.label || e.type || 'default';
      if (!colorMapRef.current.edges[t]) assignColor('edges', t);
      et[t] = true;
    });
    setEdgeTypeFilters(et);

    // Sync color map state for legend
    setColorMap({ ...colorMapRef.current });
  }, [results, assignColor]);

  // ── Initialize Cytoscape once on mount ──────────────────────────

  useEffect(() => {
    if (!containerRef.current) return;

    const cy = cytoscape({
      container: containerRef.current,
      elements: [],
      style: CY_STYLE,
      wheelSensitivity: 1,
      boxSelectionEnabled: false,
      minZoom: 0.2,
      maxZoom: 3,
    });

    cy.on('tap', 'node', (evt) => setSelectedItem(evt.target.data()));
    cy.on('tap', 'edge', (evt) => setSelectedItem(evt.target.data()));
    cy.on('tap', (evt) => { if (evt.target === cy) setSelectedItem(null); });

    cyRef.current = cy;

    return () => { cy.destroy(); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Sync elements into Cytoscape when data changes ──────────────

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;

    // Build a set of new element IDs
    const newIds = new Set(cyElements.map((el) => el.data.id));

    // Remove elements no longer in the list
    cy.elements().forEach((ele) => {
      if (!newIds.has(ele.id())) ele.remove();
    });

    // Add or update elements
    cyElements.forEach((el) => {
      const existing = cy.getElementById(el.data.id);
      if (existing.length === 0) {
        cy.add(el);
      } else {
        existing.data(el.data);
      }
    });

    // Run layout + fit
    if (cy.elements().length > 0) {
      try {
        cy.layout(layoutConfig(layout)).run();
      } catch (_) {
        cy.layout(layoutConfig('cose')).run();
      }
      cy.resize();
      cy.fit(undefined, 40);
    }

  }, [cyElements, layout]);

  // ── Zoom helpers ──────────────────────────────────────────────────

  const handleZoomIn = () => cyRef.current?.zoom(cyRef.current.zoom() * 1.3);
  const handleZoomOut = () => cyRef.current?.zoom(cyRef.current.zoom() / 1.3);
  const handleFit = () => cyRef.current?.fit(undefined, 40);

  // ── Query execution ───────────────────────────────────────────────

  const formatErrorMessage = (rawError) => {
    if (rawError.includes('already exists')) return `Node already exists. Use UPDATE to modify.`;
    if (rawError.length > 200) return rawError.substring(0, 200) + '…';
    return rawError;
  };

  const executeQuery = async (queryToExecute = null) => {
    const actualQuery =
      typeof queryToExecute === 'string' ? queryToExecute : query;
    if (!actualQuery.trim()) return;

    setLoading(true);
    setError('');
    setSelectedItem(null);
    setQueryResult(null);
    setExecutionTime(null);

    const t0 = performance.now();
    try {
      const response = await axios.post('/query/grql', {
        query: actualQuery,
        graph_name: graphName,
      });

      const ms = Math.round(performance.now() - t0);
      setExecutionTime(ms);

      if (response.data?.result) {
        const r = response.data.result;
        if (r.success === false) {
          setError(formatErrorMessage(r.message || r.data?.message || 'Query failed'));
        } else {
          setResults({ nodes: r.nodes || [], edges: r.edges || [] });
          setQueryResult({
            success: true,
            message: `Executed in ${ms}ms`,
            metadata: {
              nodesCount: r.nodes?.length || 0,
              edgesCount: r.edges?.length || 0,
              executionTime: ms,
            },
          });
        }
      } else {
        setError('No results returned');
      }
    } catch (err) {
      setError(formatErrorMessage(
        err.response?.data?.error || err.message || 'Query failed',
      ));
    } finally {
      setLoading(false);
    }
  };

  const refreshGraphData = async () => {
    try {
      const [nodeRes, edgeRes] = await Promise.all([
        axios.post('/query/grql', { query: 'FIND nodes', graph_name: graphName }),
        axios.post('/query/grql', { query: 'FIND edges', graph_name: graphName }),
      ]);
      setResults({
        nodes: nodeRes.data.result?.nodes || [],
        edges: edgeRes.data.result?.edges || [],
      });
    } catch (_) {
      /* ignore */
    }
  };

  const createNewGraph = async () => {
    if (!newGraphName.trim()) return;
    setLoading(true);
    try {
      const res = await axios.post('/graphs', { name: newGraphName });
      if (res.data?.success) {
        setAvailableGraphs((prev) => [...prev, newGraphName]);
        setGraphName(newGraphName);
        setShowCreateGraphModal(false);
        setNewGraphName('');
      }
    } catch (err) {
      setError(formatErrorMessage(
        err.response?.data?.error || 'Failed to create graph',
      ));
    } finally {
      setLoading(false);
    }
  };

  // ── Load initial data ─────────────────────────────────────────────

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch('/graph');
        if (res.ok) {
          const data = await res.json();
          setResults({ nodes: data.nodes || [], edges: data.edges || [] });
        }
      } catch (_) {
        /* server may not be running */
      }
    })();
  }, []);

  // ── Render ────────────────────────────────────────────────────────

  return (
    <div className="graph-explorer">
      {/* Query Window */}
      <div className="query-window">
        <div className="query-controls">
          <div className="query-input-group">
            <div className="graph-selector">
              <select
                value={graphName}
                onChange={(e) => setGraphName(e.target.value)}
                className="graph-name-select"
              >
                {availableGraphs.map((g) => (
                  <option key={g} value={g}>{g}</option>
                ))}
              </select>
              <button
                onClick={() => setShowCreateGraphModal(true)}
                className="create-graph-button"
                title="Create New Graph"
              >
                +
              </button>
            </div>
            <textarea
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); executeQuery(); } }}
              placeholder="Enter AIQL query…"
              className="query-textarea"
            />
            <button onClick={() => executeQuery()} disabled={loading} className="execute-button">
              {loading ? 'Executing…' : 'Execute'}
            </button>
            <button onClick={refreshGraphData} disabled={loading} className="refresh-button" title="Refresh">
              &#x1f504;
            </button>
          </div>
        </div>

        {/* Status messages */}
        {queryResult && (
          <div className="query-message success">
            <span className="message-text">{queryResult.message}</span>
            <span className="metadata">
              {' '}| Nodes: {queryResult.metadata.nodesCount} | Edges: {queryResult.metadata.edgesCount} | Time: {queryResult.metadata.executionTime}ms
            </span>
          </div>
        )}
        {error && (
          <div className="query-message error">
            <span className="message-text">{error}</span>
          </div>
        )}
      </div>

      {/* Three-Pane Layout */}
      <div className="three-pane-layout">
        {/* Left Pane — Example Queries */}
        <div className="left-pane">
          <div className="pane-header"><h3>Example Queries</h3></div>
          <div className="sample-queries">
            <QueryCategory title="Quick Demo">
              <QueryBtn label="Create Alice" q='CREATE NODE Person {name: "Alice_Johnson", age: 30, occupation: "Data_Scientist"}' onClick={setQuery} />
              <QueryBtn label="Create Bob" q='CREATE NODE Person {name: "Bob_Smith", age: 28, occupation: "Software_Engineer"}' onClick={setQuery} />
              <QueryBtn label="Create TechCorp" q='CREATE NODE Company {name: "TechCorp_Inc", industry: "Technology", founded: 2010}' onClick={setQuery} />
              <QueryBtn label="Connect Alice & Bob" q='CREATE EDGE WORKS_WITH {department: "Engineering"} SRC Person WHERE name = "Alice_Johnson" DEST Person WHERE name = "Bob_Smith"' onClick={setQuery} />
              <QueryBtn label="Alice → TechCorp" q='CREATE EDGE WORKS_FOR {position: "Senior_Data_Scientist"} SRC Person WHERE name = "Alice_Johnson" DEST Company WHERE name = "TechCorp_Inc"' onClick={setQuery} />
            </QueryCategory>

            <QueryCategory title="Exploration">
              <QueryBtn label="View Entire Graph" q="FIND nodes, edges" onClick={setQuery} />
              <QueryBtn label="All Nodes" q="FIND nodes" onClick={setQuery} />
              <QueryBtn label="All Edges" q="FIND edges" onClick={setQuery} />
              <QueryBtn label="All People" q='FIND nodes WHERE label="Person"' onClick={setQuery} />
              <QueryBtn label="All Companies" q='FIND nodes WHERE label="Company"' onClick={setQuery} />
            </QueryCategory>

            <QueryCategory title="Advanced">
              <QueryBtn label="Order by Age" q='SELECT * FROM Person ORDER BY age DESC' onClick={setQuery} />
              <QueryBtn label="Match by Name" q='MATCH NODE Person WHERE name = "Alice_Johnson"' onClick={setQuery} />
              <QueryBtn label="Count by Type" q='AGGREGATE COUNT(*) AS total FROM Person' onClick={setQuery} />
              <QueryBtn label="Show Graphs" q='SHOW GRAPHS' onClick={setQuery} />
            </QueryCategory>

            <QueryCategory title="Cleanup">
              <QueryBtn label="Delete All Nodes" q="DELETE ALL nodes" onClick={setQuery} />
              <QueryBtn label="Delete All Edges" q="DELETE ALL edges" onClick={setQuery} />
            </QueryCategory>
          </div>
        </div>

        {/* Center Pane — Cytoscape Graph */}
        <div className="center-pane" style={{ position: 'relative' }}>
          <div style={{ position: 'absolute', top: 8, left: 8, zIndex: 10 }}>
            <span style={{ color: '#a0a3ab', fontSize: 12 }}>
              Nodes: {results.nodes?.length || 0} | Edges: {results.edges?.length || 0}
            </span>
          </div>
          <div
            ref={containerRef}
            style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, background: '#1a1b1e' }}
          />
        </div>

        {/* Right Pane — Controls + Properties */}
        <div className="right-pane">
          <GraphControls
            layout={layout}
            onLayoutChange={setLayout}
            onZoomIn={handleZoomIn}
            onZoomOut={handleZoomOut}
            onFit={handleFit}
            nodeTypes={nodeTypeFilters}
            edgeTypes={edgeTypeFilters}
            colorMap={colorMap}
          />
          <PropertyPanel item={selectedItem} onClose={() => setSelectedItem(null)} />
        </div>
      </div>

      {/* Create Graph Modal */}
      {showCreateGraphModal && (
        <div className="modal-overlay" onClick={() => setShowCreateGraphModal(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Create New Graph</h3>
              <button className="modal-close" onClick={() => setShowCreateGraphModal(false)}>&times;</button>
            </div>
            <div className="modal-body">
              <div className="form-group">
                <label className="form-label">Graph Name</label>
                <input
                  type="text"
                  value={newGraphName}
                  onChange={(e) => setNewGraphName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') createNewGraph(); }}
                  placeholder="e.g. my-project"
                  className="form-input"
                  autoFocus
                />
              </div>
            </div>
            <div className="modal-footer">
              <button onClick={() => setShowCreateGraphModal(false)} className="btn-secondary">Cancel</button>
              <button onClick={createNewGraph} disabled={!newGraphName.trim()} className="execute-button">Create</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Small helpers ────────────────────────────────────────────────────

function QueryCategory({ title, children }) {
  return (
    <div className="query-category">
      <h4>{title}</h4>
      {children}
    </div>
  );
}

function QueryBtn({ label, q, onClick }) {
  return <button onClick={() => onClick(q)}>{label}</button>;
}

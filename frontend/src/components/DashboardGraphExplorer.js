/**
 * DashboardGraphExplorer — Cytoscape.js graph visualizer for the dashboard.
 *
 * Embedded in GraphsPage when a graph is selected. Shows nodes/edges with
 * interactive layout controls, property inspector, and AIQL query bar.
 */

import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import cytoscape from 'cytoscape';
import dagre from 'cytoscape-dagre';
import {
  X, Play, RotateCcw, ZoomIn, ZoomOut, Maximize2, Download,
  LayoutGrid, Circle, GitBranch, Loader2,
  PanelLeftClose, PanelLeftOpen, Expand, Search, Copy,
} from 'lucide-react';
import api from '../lib/api';
import CommentThread from './CommentThread';

// Register dagre layout once
try { cytoscape.use(dagre); } catch (_) { /* already registered */ }

// ── Color helpers ────────────────────────────────────────────────────

function goldenColor(index, sat, light) {
  const hue = (index * 137.508) % 360;
  return `hsl(${hue.toFixed(1)}, ${sat}%, ${light}%)`;
}

// Fixed colors and sizes for GraphRAG hierarchy types
const GRAPHRAG_TYPES = {
  // Structure nodes (large, distinct shapes)
  Context:          { color: '#ef4444', size: 60, shape: 'round-rectangle' },
  KnowledgeBase:    { color: '#3b82f6', size: 50, shape: 'round-rectangle' },
  CodeBase:         { color: '#8b5cf6', size: 50, shape: 'round-rectangle' },
  SystemStore:      { color: '#64748b', size: 50, shape: 'round-rectangle' },
  UserStore:        { color: '#f59e0b', size: 50, shape: 'round-rectangle' },
  WebStore:         { color: '#06b6d4', size: 50, shape: 'round-rectangle' },
  GeneratedStore:   { color: '#10b981', size: 50, shape: 'round-rectangle' },
  MemoryStore:      { color: '#ec4899', size: 50, shape: 'round-rectangle' },
  ArtifactStore:    { color: '#f97316', size: 50, shape: 'round-rectangle' },
  ToolStore:        { color: '#78716c', size: 50, shape: 'round-rectangle' },
  // Content nodes
  Document:         { color: '#3b82f6', size: 42, shape: 'round-rectangle' },
  Passage:          { color: '#8b5cf6', size: 32, shape: 'ellipse' },
  TextChunk:        { color: '#8b5cf6', size: 32, shape: 'ellipse' },
  Chunk:            { color: '#8b5cf6', size: 32, shape: 'ellipse' },
  Fact:             { color: '#f59e0b', size: 26, shape: 'diamond' },
  Entity:           { color: '#22c55e', size: 30, shape: 'ellipse' },
  PipelineRun:      { color: '#64748b', size: 36, shape: 'hexagon' },
  Boundary:         { color: '#ef4444', size: 55, shape: 'round-rectangle' },
  ContextRef:       { color: '#3b82f6', size: 40, shape: 'round-rectangle' },
  AgentRef:         { color: '#22c55e', size: 32, shape: 'ellipse' },
  Turn:             { color: '#f59e0b', size: 30, shape: 'ellipse' },
  Decision:         { color: '#ef4444', size: 30, shape: 'diamond' },
  Action:           { color: '#10b981', size: 30, shape: 'diamond' },
  Topic:            { color: '#06b6d4', size: 28, shape: 'ellipse' },
  ContextBoundary:  { color: '#ef4444', size: 55, shape: 'round-rectangle' },
  CategoryBoundary: { color: '#f97316', size: 42, shape: 'round-rectangle' },
  // SDLC / Software Dev nodes
  Feature:          { color: '#22c55e', size: 38, shape: 'round-rectangle' },
  UserStory:        { color: '#10b981', size: 34, shape: 'round-rectangle' },
  Requirement:      { color: '#0ea5e9', size: 34, shape: 'round-rectangle' },
  Constraint:       { color: '#f97316', size: 28, shape: 'diamond' },
  Milestone:        { color: '#a855f7', size: 36, shape: 'hexagon' },
  Component:        { color: '#6366f1', size: 36, shape: 'round-rectangle' },
  APIEndpoint:      { color: '#14b8a6', size: 30, shape: 'round-rectangle' },
  DataModel:        { color: '#f59e0b', size: 32, shape: 'round-rectangle' },
  Risk:             { color: '#ef4444', size: 28, shape: 'diamond' },
  Stakeholder:      { color: '#ec4899', size: 32, shape: 'ellipse' },
  // Knowledge Base nodes
  Person:           { color: '#ec4899', size: 32, shape: 'ellipse' },
  Organization:     { color: '#f97316', size: 34, shape: 'round-rectangle' },
  Concept:          { color: '#06b6d4', size: 30, shape: 'ellipse' },
  Technology:       { color: '#8b5cf6', size: 30, shape: 'hexagon' },
  // Rules nodes
  Rule:             { color: '#ef4444', size: 30, shape: 'round-rectangle' },
  Standard:         { color: '#3b82f6', size: 30, shape: 'round-rectangle' },
  Policy:           { color: '#f59e0b', size: 32, shape: 'round-rectangle' },
  // Database nodes
  Table:            { color: '#3b82f6', size: 34, shape: 'round-rectangle' },
  Column:           { color: '#64748b', size: 24, shape: 'ellipse' },
  Schema:           { color: '#8b5cf6', size: 36, shape: 'round-rectangle' },
  // Decision nodes
  ADR:              { color: '#ef4444', size: 34, shape: 'round-rectangle' },
  TradeOff:         { color: '#f97316', size: 30, shape: 'diamond' },
  Alternative:      { color: '#06b6d4', size: 28, shape: 'ellipse' },
  // Web nodes
  WebPage:          { color: '#06b6d4', size: 34, shape: 'round-rectangle' },
  Article:          { color: '#3b82f6', size: 32, shape: 'round-rectangle' },
  Author:           { color: '#ec4899', size: 28, shape: 'ellipse' },
};

// Category colors for content node border tinting
const CATEGORY_COLORS = {
  knowledge_base:    '#3b82f6',
  software_dev:      '#22c55e',
  connectors:        '#8b5cf6',
  user_message:      '#f59e0b',
  system_message:    '#ef4444',
  generated_message: '#10b981',
};

// Fixed colors for GraphRAG edge types
const GRAPHRAG_EDGES = {
  // Hierarchy edges
  HAS_KNOWLEDGE_BASE:   '#3b82f6',
  HAS_CODE_BASE:        '#8b5cf6',
  HAS_SYSTEM_STORE:     '#64748b',
  HAS_USER_STORE:       '#f59e0b',
  HAS_WEB_STORE:        '#06b6d4',
  HAS_GENERATED_STORE:  '#10b981',
  HAS_MEMORY_STORE:     '#ec4899',
  HAS_ARTIFACT_STORE:   '#f97316',
  HAS_TOOL_STORE:       '#78716c',
  HAS_DOCUMENT:         '#3b82f6',
  HAS_CONTENT:          '#6366f1',
  HAS_RUN:              '#64748b',
  HAS_CONTEXT:          '#3b82f6',
  EXECUTED_BY:          '#22c55e',
  HAS_PIPELINE_RUN:     '#64748b',
  // Content edges
  HAS_PASSAGE:          '#6366f1',
  CONTAINS:             '#6366f1',
  STATES:               '#f97316',
  MENTIONS:             '#10b981',
  CO_OCCURS_WITH:       '#64748b',
  BELONGS_TO:           '#ef4444',
  IN_CATEGORY:          '#94a3b8',
  // Interaction edges
  SPOKEN_BY:            '#f59e0b',
  NEXT_TURN:            '#94a3b8',
  DECIDES:              '#ef4444',
  ASSIGNS:              '#10b981',
  RAISES:               '#06b6d4',
  REFERENCES:           '#8b5cf6',
  // SDLC edges
  IMPLEMENTS:           '#22c55e',
  DEPENDS_ON:           '#f97316',
  CONSTRAINED_BY:       '#ef4444',
  DELIVERS:             '#a855f7',
  USES:                 '#8b5cf6',
  EXPOSES:              '#14b8a6',
  STORES:               '#f59e0b',
  OWNS:                 '#ec4899',
  MITIGATES:            '#ef4444',
  REQUIRES:             '#0ea5e9',
  // Knowledge edges
  RELATED_TO:           '#06b6d4',
  DISCUSSES:            '#3b82f6',
  AUTHORED_BY:          '#ec4899',
  EXPERT_IN:            '#8b5cf6',
  WORKS_AT:             '#f97316',
  // Rules edges
  ENFORCES:             '#ef4444',
  APPLIES_TO:           '#f59e0b',
  SUPERSEDES:           '#64748b',
  DERIVED_FROM:         '#3b82f6',
  // Decision edges
  DECIDED_BY:           '#ec4899',
  CONSIDERS:            '#06b6d4',
  TRADES_OFF:           '#f97316',
  DOCUMENTED_IN:        '#3b82f6',
  IMPACTS:              '#ef4444',
  // Web edges
  LINKS_TO:             '#06b6d4',
  CITES:                '#3b82f6',
  PUBLISHED_BY:         '#f97316',
};

// ── Cytoscape stylesheet (dashboard themed) ──────────────────────────

const CY_STYLE = [
  {
    selector: 'node',
    style: {
      label: 'data(displayLabel)',
      'background-color': 'data(color)',
      shape: 'data(nodeShape)',
      color: '#e8e8e8',
      'text-valign': 'bottom',
      'text-halign': 'center',
      'font-size': '11px',
      'text-margin-y': 6,
      width: 'data(nodeSize)',
      height: 'data(nodeSize)',
      'border-width': 2,
      'border-color': 'data(color)',
      'border-opacity': 0.5,
      'text-outline-color': '#0a0a0f',
      'text-outline-width': 2,
      'text-max-width': '120px',
      'text-wrap': 'ellipsis',
    },
  },
  // Storage indicator: nodes with vector_ref get a subtle glow
  {
    selector: 'node[?hasVectorRef]',
    style: {
      'border-width': 3,
      'border-color': '#7c3aed',
      'border-opacity': 0.8,
    },
  },
  // Storage indicator: nodes with bm25_ref get a warm border
  {
    selector: 'node[?hasBm25Ref]',
    style: {
      'border-width': 3,
      'border-color': '#f97316',
      'border-opacity': 0.8,
    },
  },
  // Category border tint for content nodes
  {
    selector: 'node[catBorderColor]',
    style: {
      'border-width': 2,
      'border-color': 'data(catBorderColor)',
      'border-opacity': 0.7,
    },
  },
  {
    selector: 'node:selected',
    style: {
      'border-width': 4,
      'border-color': '#60a5fa',
      'border-opacity': 1,
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
      width: 2.5,
      'font-size': '9px',
      color: '#94a3b8',
      'text-rotation': 'autorotate',
      'text-outline-color': '#0a0a0f',
      'text-outline-width': 2,
      'arrow-scale': 0.9,
      'line-opacity': 0.8,
    },
  },
  {
    selector: 'edge:selected',
    style: {
      'line-color': '#60a5fa',
      'target-arrow-color': '#60a5fa',
      width: 3,
    },
  },
];

// ── Layout configs ───────────────────────────────────────────────────

const LAYOUTS = [
  { id: 'dagre', label: 'Hierarchical' },
  { id: 'cose', label: 'Force' },
  { id: 'circle', label: 'Circle' },
  { id: 'grid', label: 'Grid' },
  { id: 'concentric', label: 'Concentric' },
  { id: 'breadthfirst', label: 'Tree' },
];

function layoutConfig(name) {
  const base = { name, animate: true, animationDuration: 300 };
  switch (name) {
    case 'dagre': return { ...base, rankDir: 'TB', nodeSep: 60, rankSep: 80 };
    case 'cose': return { ...base, nodeRepulsion: 8000, idealEdgeLength: 120, animate: false };
    case 'circle': return { ...base, spacingFactor: 1.5 };
    case 'grid': return { ...base, spacingFactor: 1.2 };
    case 'concentric': return { ...base, minNodeSpacing: 60 };
    case 'breadthfirst': return { ...base, spacingFactor: 1.3 };
    default: return base;
  }
}

// ── Main component ───────────────────────────────────────────────────

export default function DashboardGraphExplorer({ graphName, onClose, embedded = false }) {
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [loading, setLoading] = useState(false);
  const [layout, setLayout] = useState('dagre');
  const [categoryFilter, setCategoryFilter] = useState('all');
  const [selectedItem, setSelectedItem] = useState(null);
  const [query, setQuery] = useState('');
  const [queryRunning, setQueryRunning] = useState(false);
  const [queryMessage, setQueryMessage] = useState(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [hiddenNodeTypes, setHiddenNodeTypes] = useState(new Set());
  const [hiddenEdgeTypes, setHiddenEdgeTypes] = useState(new Set());
  const [searchTerm, setSearchTerm] = useState('');

  const colorMapRef = useRef({ nodes: {}, edges: {} });
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
    return c;
  }, []);

  // ── Convert to Cytoscape elements ─────────────────────────────────

  const cyElements = useMemo(() => {
    const elements = [];
    const boundaryLabels = new Set(['ContextBoundary', 'CategoryBoundary']);

    nodes.forEach((node) => {
      const type = node.label || 'default';
      const props = node.properties || {};
      const graphRag = GRAPHRAG_TYPES[type];
      const isBoundary = boundaryLabels.has(type);

      // Category filter: boundary nodes always visible, content nodes filtered
      if (!isBoundary && categoryFilter !== 'all') {
        const nodeCat = props.category;
        if (nodeCat && nodeCat !== categoryFilter) return;
      }

      // Node type filter via checkboxes
      if (hiddenNodeTypes.has(type)) return;

      // Display label: shorter for Passage/Fact, descriptive for boundaries
      let displayLabel = node.name || props.name || type;
      if (type === 'Fact') {
        displayLabel = (props.statement || displayLabel).slice(0, 40);
      } else if (type === 'Passage' || type === 'TextChunk') {
        displayLabel = `P${(props.chunk_index ?? 0) + 1}`;
      } else if (type === 'ContextBoundary') {
        displayLabel = props.name || 'Context';
      } else if (type === 'CategoryBoundary') {
        displayLabel = props.name || props.category || 'Category';
      }

      const color = graphRag ? graphRag.color : assignColor('nodes', type);
      const nodeSize = graphRag ? graphRag.size : 34;
      const nodeShape = graphRag ? graphRag.shape : 'ellipse';

      // Category border tint for content nodes
      const catColor = !isBoundary && props.category ? CATEGORY_COLORS[props.category] : null;

      // Storage reference flags for Cytoscape selectors
      const hasVectorRef = !!props.vector_ref;
      const hasBm25Ref = !!props.bm25_ref;

      elements.push({
        data: {
          ...node,
          ...props,
          displayLabel,
          color,
          nodeSize,
          nodeShape,
          hasVectorRef,
          hasBm25Ref,
          ...(catColor ? { catBorderColor: catColor } : {}),
        },
      });
    });

    const nodeIds = new Set(elements.map((el) => el.data.id));

    edges.forEach((edge) => {
      const type = edge.label || 'EDGE';
      if (hiddenEdgeTypes.has(type)) return;
      const color = GRAPHRAG_EDGES[type] || assignColor('edges', type);
      if (edge.source && edge.target && nodeIds.has(edge.source) && nodeIds.has(edge.target)) {
        elements.push({
          data: {
            ...edge,
            ...edge.properties,
            id: edge.id || `${edge.source}-${edge.target}-${type}`,
            displayLabel: type,
            color,
          },
        });
      }
    });

    return elements;
  }, [nodes, edges, assignColor, categoryFilter, hiddenNodeTypes, hiddenEdgeTypes]);

  // ── Type legends ──────────────────────────────────────────────────

  const nodeTypes = useMemo(() => {
    const types = {};
    nodes.forEach(n => { types[n.label || 'default'] = true; });
    return types;
  }, [nodes]);

  const edgeTypes = useMemo(() => {
    const types = {};
    edges.forEach(e => { types[e.label || 'EDGE'] = true; });
    return types;
  }, [edges]);

  // ── Initialize Cytoscape ──────────────────────────────────────────

  useEffect(() => {
    if (!containerRef.current) return;

    const cy = cytoscape({
      container: containerRef.current,
      elements: [],
      style: CY_STYLE,
      wheelSensitivity: 1,
      boxSelectionEnabled: false,
      minZoom: 0.15,
      maxZoom: 4,
    });

    cy.on('tap', 'node', (evt) => setSelectedItem(evt.target.data()));
    cy.on('tap', 'edge', (evt) => setSelectedItem(evt.target.data()));
    cy.on('tap', (evt) => { if (evt.target === cy) setSelectedItem(null); });

    cyRef.current = cy;
    return () => { cy.destroy(); };
  }, []);

  // ── Sync elements into Cytoscape ──────────────────────────────────

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;

    const newIds = new Set(cyElements.map((el) => el.data.id));
    cy.elements().forEach((ele) => {
      if (!newIds.has(ele.id())) ele.remove();
    });

    cyElements.forEach((el) => {
      const existing = cy.getElementById(el.data.id);
      if (existing.length === 0) {
        cy.add(el);
      } else {
        existing.data(el.data);
      }
    });

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

  // ── Search highlight ────────────────────────────────────────────
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    if (!searchTerm.trim()) {
      cy.nodes().style('opacity', 1);
      cy.edges().style('opacity', 1);
      return;
    }
    const term = searchTerm.toLowerCase();
    cy.nodes().forEach(node => {
      const data = node.data();
      const searchable = [data.name, data.label, data.displayLabel, data.statement, data.description, data.content]
        .filter(Boolean).join(' ').toLowerCase();
      if (searchable.includes(term)) {
        node.style('opacity', 1);
      } else {
        node.style('opacity', 0.12);
      }
    });
    cy.edges().forEach(edge => {
      const src = edge.source();
      const tgt = edge.target();
      edge.style('opacity', (src.style('opacity') === '1' || tgt.style('opacity') === '1') ? 0.6 : 0.08);
    });
  }, [searchTerm]);

  // ── Fetch graph data ──────────────────────────────────────────────

  const [dataLoaded, setDataLoaded] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get(`/dashboard/graphs/${encodeURIComponent(graphName)}/data`);
      setNodes(res.data.nodes || []);
      setEdges(res.data.edges || []);
      setDataLoaded(true);
    } catch {
      setNodes([]);
      setEdges([]);
    } finally {
      setLoading(false);
    }
  }, [graphName]);

  // Auto-fetch on mount so graph data is always fresh
  useEffect(() => { if (graphName) fetchData(); }, [graphName, fetchData]);

  // ── Execute query ─────────────────────────────────────────────────

  const executeQuery = async () => {
    if (!query.trim()) return;
    setQueryRunning(true);
    setQueryMessage(null);
    setSelectedItem(null);
    try {
      const res = await api.post(`/dashboard/graphs/${encodeURIComponent(graphName)}/query`, {
        query: query.trim(),
      });
      setNodes(res.data.nodes || []);
      setEdges(res.data.edges || []);
      const n = res.data.nodes?.length || 0;
      const e = res.data.edges?.length || 0;
      setQueryMessage({ type: 'success', text: `OK — ${n} nodes, ${e} edges` });
    } catch (err) {
      const detail = err.response?.data?.detail;
      const msg = typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : (err.message || 'Query failed');
      setQueryMessage({ type: 'error', text: msg });
    } finally {
      setQueryRunning(false);
    }
  };

  // ── Zoom helpers ──────────────────────────────────────────────────

  const zoomIn = () => cyRef.current?.zoom(cyRef.current.zoom() * 1.3);
  const zoomOut = () => cyRef.current?.zoom(cyRef.current.zoom() / 1.3);
  const fitView = () => cyRef.current?.fit(undefined, 40);

  // ── Property inspector ────────────────────────────────────────────

  const SKIP_KEYS = new Set([
    'id', 'uuid', 'label', 'type', 'source', 'target', 'displayLabel', 'color',
    'from_id', 'to_id', 'properties', 'nodeSize', 'nodeShape', 'hasVectorRef', 'hasBm25Ref',
    'embedding', 'embeddings', 'vector', 'vectors',
  ]);

  // Storage badge component
  const StorageBadge = ({ label, value, color }) => (
    <div className="flex items-center gap-1.5 px-2 py-1 rounded text-[10px] font-mono" style={{ background: `${color}15`, border: `1px solid ${color}30` }}>
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: color }} />
      <span style={{ color }}>{label}</span>
      <span className="truncate" style={{ color: 'var(--neo-text-muted)', maxWidth: 100 }}>{value}</span>
    </div>
  );

  const renderPropertyPanel = () => {
    if (!selectedItem) return (
      <div className="text-xs text-center py-6" style={{ color: 'var(--neo-text-muted)' }}>
        Click a node or edge to inspect
      </div>
    );

    const isEdge = !!(selectedItem.source && selectedItem.target);
    const displayType = selectedItem.label || (isEdge ? 'EDGE' : 'Node');
    const typeStyle = GRAPHRAG_TYPES[displayType];
    const props = Object.entries(selectedItem).filter(([k]) => !SKIP_KEYS.has(k) && !k.startsWith('_'));

    return (
      <div>
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-bold" style={{ color: typeStyle?.color || 'var(--neo-blue)' }}>
            {isEdge ? 'Edge' : 'Node'}: {displayType}
          </span>
          <button onClick={() => setSelectedItem(null)} className="p-0.5" style={{ color: 'var(--neo-text-muted)' }}>
            <X size={12} />
          </button>
        </div>
        <div className="space-y-1">
          <div className="flex justify-between text-xs">
            <span style={{ color: 'var(--neo-text-muted)' }}>ID</span>
            <div className="flex items-center gap-1">
              <span className="font-mono" style={{ color: 'var(--neo-text)' }}>{(selectedItem.id || '').substring(0, 16)}</span>
              <button
                onClick={() => { navigator.clipboard.writeText(selectedItem.id); }}
                className="p-0.5 rounded hover:opacity-80"
                style={{ color: 'var(--neo-text-dim)', background: 'none', border: 'none', cursor: 'pointer' }}
                title="Copy full ID"
              >
                <Copy size={10} />
              </button>
            </div>
          </div>
          {isEdge && (
            <>
              <div className="flex justify-between text-xs">
                <span style={{ color: 'var(--neo-text-muted)' }}>Source</span>
                <span className="font-mono" style={{ color: 'var(--neo-text)' }}>{(selectedItem.source || '').substring(0, 12)}</span>
              </div>
              <div className="flex justify-between text-xs">
                <span style={{ color: 'var(--neo-text-muted)' }}>Target</span>
                <span className="font-mono" style={{ color: 'var(--neo-text)' }}>{(selectedItem.target || '').substring(0, 12)}</span>
              </div>
            </>
          )}

          {/* Connections section for nodes */}
          {!isEdge && (() => {
            const cy = cyRef.current;
            if (!cy) return null;
            const node = cy.getElementById(selectedItem.id);
            if (!node || node.length === 0) return null;
            const connEdges = node.connectedEdges();
            if (connEdges.length === 0) return null;
            return (
              <>
                <div className="my-1" style={{ borderTop: '1px solid var(--neo-border)' }} />
                <div className="text-[10px] font-medium uppercase tracking-wider mb-1" style={{ color: 'var(--neo-text-dim)' }}>
                  Connections ({connEdges.length})
                </div>
                <div className="space-y-0.5 max-h-24 overflow-y-auto">
                  {connEdges.toArray().slice(0, 10).map(edge => {
                    const other = edge.source().id() === selectedItem.id ? edge.target() : edge.source();
                    const dir = edge.source().id() === selectedItem.id ? '\u2192' : '\u2190';
                    return (
                      <button
                        key={edge.id()}
                        onClick={() => setSelectedItem(other.data())}
                        className="w-full text-left flex items-center gap-1 text-[10px] py-0.5 px-1 rounded hover:opacity-80"
                        style={{ color: 'var(--neo-text-muted)', background: 'transparent', border: 'none', cursor: 'pointer' }}
                      >
                        <span>{dir}</span>
                        <span style={{ color: edge.data().color || 'var(--neo-text-dim)' }}>{edge.data().label}</span>
                        <span style={{ color: 'var(--neo-text)' }}>{other.data().displayLabel || other.data().name || other.id().substring(0, 8)}</span>
                      </button>
                    );
                  })}
                  {connEdges.length > 10 && <span className="text-[10px] px-1" style={{ color: 'var(--neo-text-dim)' }}>+{connEdges.length - 10} more</span>}
                </div>
              </>
            );
          })()}

          {/* Storage references — prominent display */}
          {(selectedItem.vector_ref || selectedItem.bm25_ref || selectedItem.has_embedding) && (
            <>
              <div className="my-1" style={{ borderTop: '1px solid var(--neo-border)' }} />
              <div className="text-[10px] font-medium uppercase tracking-wider mb-1" style={{ color: 'var(--neo-text-dim)' }}>Storage</div>
              <div className="space-y-1">
                {selectedItem.vector_ref && <StorageBadge label="Vector" value={selectedItem.vector_ref} color="#7c3aed" />}
                {selectedItem.bm25_ref && <StorageBadge label="BM25" value={selectedItem.bm25_ref} color="#f97316" />}
                {selectedItem.has_embedding && !selectedItem.vector_ref && (
                  <StorageBadge label="Embedded" value={`${selectedItem.embedding_dim || '?'}d`} color="#8b5cf6" />
                )}
              </div>
            </>
          )}

          {props.length > 0 && (
            <>
              <div className="my-1" style={{ borderTop: '1px solid var(--neo-border)' }} />
              {props.map(([key, value]) => {
                // Skip storage refs (already shown above) and large values
                if (['vector_ref', 'bm25_ref', 'has_embedding', 'embedding_dim', 'embedding_model'].includes(key)) return null;
                const display = typeof value === 'object' ? JSON.stringify(value) : String(value);
                // Truncate long content
                const truncated = display.length > 200 ? display.slice(0, 200) + '...' : display;
                return (
                  <div key={key} className="flex justify-between text-xs gap-2">
                    <span style={{ color: 'var(--neo-text-muted)', flexShrink: 0 }}>{key}</span>
                    <span className="text-right" style={{ color: 'var(--neo-text)', maxWidth: '140px', overflow: 'hidden', textOverflow: 'ellipsis', wordBreak: 'break-all' }}>
                      {truncated}
                    </span>
                  </div>
                );
              })}
            </>
          )}

          {/* Comments section */}
          {!isEdge && selectedItem.id && (
            <>
              <div className="my-2" style={{ borderTop: '1px solid var(--neo-border)' }} />
              <CommentThread nodeId={selectedItem.id} compact />
            </>
          )}
        </div>
      </div>
    );
  };

  // ── Render ────────────────────────────────────────────────────────

  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
    >
      {/* Header — hidden in embedded mode (parent provides graph name + tabs) */}
      {!embedded && (
        <div
          className="flex items-center justify-between px-4 py-3"
          style={{ borderBottom: '1px solid var(--neo-border)' }}
        >
          <div className="flex items-center gap-2">
            <GitBranch size={16} style={{ color: 'var(--neo-blue)' }} />
            <span className="font-semibold text-sm" style={{ color: 'var(--neo-text)' }}>{graphName}</span>
            <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
              {nodes.length} nodes, {edges.length} edges
            </span>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:opacity-80" style={{ color: 'var(--neo-text-muted)' }}>
            <X size={16} />
          </button>
        </div>
      )}

      {/* Query bar */}
      <div
        className="flex items-center gap-2 px-4 py-2"
        style={{ borderBottom: '1px solid var(--neo-border)', background: 'var(--neo-bg)' }}
      >
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') executeQuery(); }}
          placeholder='AIQL query (e.g. CREATE NODE Person {name: "Alice"})'
          className="flex-1 px-3 py-1.5 rounded-lg text-xs outline-none"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
        />
        <button
          onClick={executeQuery}
          disabled={queryRunning || !query.trim()}
          className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium disabled:opacity-40"
          style={{ background: 'var(--neo-green)', color: '#fff' }}
        >
          {queryRunning ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
          Run
        </button>
        <button
          onClick={fetchData}
          disabled={loading}
          className="p-1.5 rounded-lg hover:opacity-80"
          style={{ color: 'var(--neo-text-muted)' }}
          title="Refresh"
        >
          <RotateCcw size={14} />
        </button>
      </div>

      {/* Query message */}
      {queryMessage && (
        <div
          className="px-4 py-1.5 text-xs"
          style={{
            background: queryMessage.type === 'error' ? 'rgba(239,68,68,0.1)' : 'rgba(34,197,94,0.1)',
            color: queryMessage.type === 'error' ? '#ef4444' : '#22c55e',
            borderBottom: '1px solid var(--neo-border)',
          }}
        >
          {queryMessage.text}
        </div>
      )}

      {/* Search bar */}
      <div className="flex items-center gap-2 px-4 py-1.5" style={{ borderBottom: '1px solid var(--neo-border)', background: 'var(--neo-bg)' }}>
        <Search size={13} style={{ color: 'var(--neo-text-muted)' }} />
        <input
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          placeholder="Search nodes..."
          className="flex-1 px-2 py-1 rounded text-xs outline-none"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
        />
        {searchTerm && (
          <span className="text-[10px] whitespace-nowrap" style={{ color: 'var(--neo-text-muted)' }}>
            {(() => {
              const cy = cyRef.current;
              if (!cy) return '';
              const term = searchTerm.toLowerCase();
              const matched = cy.nodes().filter(node => {
                const data = node.data();
                const searchable = [data.name, data.label, data.displayLabel, data.statement, data.description, data.content]
                  .filter(Boolean).join(' ').toLowerCase();
                return searchable.includes(term);
              }).length;
              return `${matched} of ${nodes.length} nodes`;
            })()}
          </span>
        )}
        {searchTerm && (
          <button
            onClick={() => setSearchTerm('')}
            className="p-0.5 rounded hover:opacity-80"
            style={{ color: 'var(--neo-text-muted)', background: 'none', border: 'none', cursor: 'pointer' }}
          >
            <X size={12} />
          </button>
        )}
        {embedded && (
          <span className="text-xs px-2" style={{ color: 'var(--neo-text-dim)' }}>
            {nodes.length} nodes, {edges.length} edges
          </span>
        )}
      </div>

      {/* Main area: canvas + sidebar */}
      <div className="flex" style={{ height: embedded ? '100%' : expanded ? 'calc(100vh - 200px)' : 480, minHeight: embedded ? 500 : undefined, transition: 'height 0.3s ease' }}>
        {/* Cytoscape canvas */}
        <div className="flex-1 relative">
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center z-10" style={{ background: 'rgba(10,10,15,0.6)' }}>
              <Loader2 size={24} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
            </div>
          )}
          <div
            ref={containerRef}
            style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, background: '#0a0a0f' }}
          />
          {/* Zoom controls overlay */}
          <div className="absolute top-2 right-2 flex flex-col gap-1 z-10">
            <button onClick={zoomIn} className="p-1.5 rounded" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }} title="Zoom in"><ZoomIn size={14} /></button>
            <button onClick={zoomOut} className="p-1.5 rounded" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }} title="Zoom out"><ZoomOut size={14} /></button>
            <button onClick={fitView} className="p-1.5 rounded" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }} title="Fit to view"><Maximize2 size={14} /></button>
            <select
              onChange={(e) => {
                if (!e.target.value) return;
                const baseUrl = api?.defaults?.baseURL || '';
                window.open(`${baseUrl}/dashboard/graphs/${encodeURIComponent(graphName)}/export?format=${e.target.value}`, '_blank');
                e.target.value = '';
              }}
              className="px-1.5 py-1 rounded text-[10px]"
              style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
              title="Export graph"
            >
              <option value="">Export</option>
              <option value="json">JSON</option>
              <option value="csv">CSV</option>
              <option value="cypher">Cypher</option>
            </select>
            <button
              onClick={() => {
                const cy = cyRef.current;
                if (!cy) return;
                const png = cy.png({ scale: 2, bg: '#0a0a0f' });
                const link = document.createElement('a');
                link.href = png;
                link.download = `${graphName || 'graph'}.png`;
                link.click();
              }}
              className="p-1.5 rounded"
              style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
              title="Download as PNG"
            >
              <Download size={14} />
            </button>
            <button onClick={() => setExpanded(e => !e)} className="p-1.5 rounded" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }} title={expanded ? 'Collapse canvas' : 'Expand canvas'}><Expand size={14} /></button>
            <button onClick={() => setSidebarOpen(s => !s)} className="p-1.5 rounded" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }} title={sidebarOpen ? 'Hide sidebar' : 'Show sidebar'}>{sidebarOpen ? <PanelLeftClose size={14} /> : <PanelLeftOpen size={14} />}</button>
          </div>
          {/* Empty state — prompt to load or query */}
          {!loading && nodes.length === 0 && (
            <div className="absolute inset-0 flex flex-col items-center justify-center z-5">
              {!dataLoaded ? (
                <>
                  <GitBranch size={32} style={{ color: 'var(--neo-text-muted)', opacity: 0.3 }} />
                  <p className="text-xs mt-2 mb-3" style={{ color: 'var(--neo-text-muted)' }}>
                    Graph ready. Load data or run a query.
                  </p>
                  <button
                    onClick={fetchData}
                    className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium transition hover:opacity-90"
                    style={{ background: 'var(--neo-blue)', color: '#fff' }}
                  >
                    <Play size={12} /> Load Graph
                  </button>
                </>
              ) : (
                <>
                  <Circle size={32} style={{ color: 'var(--neo-text-muted)', opacity: 0.3 }} />
                  <p className="text-xs mt-2" style={{ color: 'var(--neo-text-muted)' }}>
                    No nodes in this graph. Use the query bar or ingest data first.
                  </p>
                </>
              )}
            </div>
          )}
        </div>

        {/* Right sidebar */}
        {sidebarOpen && <div
          className="w-52 overflow-y-auto p-3 flex-shrink-0"
          style={{ borderLeft: '1px solid var(--neo-border)', background: 'var(--neo-surface)' }}
        >
          {/* Layout selector */}
          <div className="mb-3">
            <div className="flex items-center gap-1 mb-1.5 text-xs font-medium" style={{ color: 'var(--neo-text-muted)' }}>
              <LayoutGrid size={11} /> Layout
            </div>
            <div className="flex gap-1 flex-wrap">
              {LAYOUTS.map((l) => (
                <button
                  key={l.id}
                  onClick={() => setLayout(l.id)}
                  className="px-2 py-1 rounded text-[10px] font-medium transition"
                  style={{
                    background: layout === l.id ? 'var(--neo-blue)' : 'transparent',
                    color: layout === l.id ? '#fff' : 'var(--neo-text-muted)',
                    border: `1px solid ${layout === l.id ? 'var(--neo-blue)' : 'var(--neo-border)'}`,
                    cursor: 'pointer',
                  }}
                  title={l.label}
                >
                  {l.label}
                </button>
              ))}
            </div>
          </div>

          {/* Category filter */}
          <div className="mb-3">
            <div className="flex items-center gap-1 mb-1.5 text-xs font-medium" style={{ color: 'var(--neo-text-muted)' }}>
              Category
            </div>
            <select
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value)}
              className="w-full px-2 py-1 rounded text-xs outline-none"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            >
              <option value="all">All Categories</option>
              <option value="knowledge_base">Knowledge Base</option>
              <option value="software_dev">Software Dev</option>
              <option value="connectors">Connectors</option>
              <option value="user_message">User Messages</option>
              <option value="system_message">System Messages</option>
              <option value="generated_message">Generated Messages</option>
            </select>
          </div>

          {/* Node type legend — multi-select checkboxes */}
          {Object.keys(nodeTypes).length > 0 && (
            <div className="mb-3">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-medium" style={{ color: 'var(--neo-text-muted)' }}>Node Types</span>
                <div className="flex gap-1">
                  <button
                    onClick={() => setHiddenNodeTypes(new Set())}
                    className="text-[9px] px-1 rounded hover:opacity-80"
                    style={{ color: 'var(--neo-blue)', background: 'none', border: 'none', cursor: 'pointer' }}
                  >All</button>
                  <button
                    onClick={() => setHiddenNodeTypes(new Set(Object.keys(nodeTypes)))}
                    className="text-[9px] px-1 rounded hover:opacity-80"
                    style={{ color: 'var(--neo-text-muted)', background: 'none', border: 'none', cursor: 'pointer' }}
                  >None</button>
                </div>
              </div>
              <div className="max-h-48 overflow-y-auto space-y-0.5">
                {Object.keys(nodeTypes).map((type) => {
                  const count = nodes.filter(n => (n.label || 'default') === type).length;
                  const graphRag = GRAPHRAG_TYPES[type];
                  const visible = !hiddenNodeTypes.has(type);
                  return (
                    <label key={type} className="flex items-center gap-1.5 text-xs py-0.5 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={visible}
                        onChange={() => {
                          setHiddenNodeTypes(prev => {
                            const next = new Set(prev);
                            if (next.has(type)) next.delete(type);
                            else next.add(type);
                            return next;
                          });
                        }}
                        className="w-3 h-3 rounded accent-blue-500"
                        style={{ accentColor: graphRag?.color || colorMapRef.current.nodes[type] || '#3498db' }}
                      />
                      <span
                        className="w-2.5 h-2.5 inline-block shrink-0"
                        style={{
                          backgroundColor: graphRag?.color || colorMapRef.current.nodes[type] || '#3498db',
                          borderRadius: graphRag?.shape === 'diamond' ? '2px' : '50%',
                          transform: graphRag?.shape === 'diamond' ? 'rotate(45deg) scale(0.8)' : 'none',
                          opacity: visible ? 1 : 0.3,
                        }}
                      />
                      <span style={{ color: visible ? 'var(--neo-text)' : 'var(--neo-text-dim)' }}>{type}</span>
                      <span className="text-[10px] ml-auto" style={{ color: 'var(--neo-text-dim)' }}>({count})</span>
                    </label>
                  );
                })}
              </div>
            </div>
          )}

          {/* Edge type legend — multi-select checkboxes */}
          {Object.keys(edgeTypes).length > 0 && (
            <div className="mb-3">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-medium" style={{ color: 'var(--neo-text-muted)' }}>Edge Types</span>
                <div className="flex gap-1">
                  <button
                    onClick={() => setHiddenEdgeTypes(new Set())}
                    className="text-[9px] px-1 rounded hover:opacity-80"
                    style={{ color: 'var(--neo-blue)', background: 'none', border: 'none', cursor: 'pointer' }}
                  >All</button>
                  <button
                    onClick={() => setHiddenEdgeTypes(new Set(Object.keys(edgeTypes)))}
                    className="text-[9px] px-1 rounded hover:opacity-80"
                    style={{ color: 'var(--neo-text-muted)', background: 'none', border: 'none', cursor: 'pointer' }}
                  >None</button>
                </div>
              </div>
              <div className="max-h-48 overflow-y-auto space-y-0.5">
                {Object.keys(edgeTypes).map((type) => {
                  const edgeColor = GRAPHRAG_EDGES[type] || colorMapRef.current.edges[type] || '#ff6b6b';
                  const visible = !hiddenEdgeTypes.has(type);
                  const count = edges.filter(e => (e.label || 'EDGE') === type).length;
                  return (
                    <label key={type} className="flex items-center gap-1.5 text-xs py-0.5 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={visible}
                        onChange={() => {
                          setHiddenEdgeTypes(prev => {
                            const next = new Set(prev);
                            if (next.has(type)) next.delete(type);
                            else next.add(type);
                            return next;
                          });
                        }}
                        className="w-3 h-3 rounded"
                        style={{ accentColor: edgeColor }}
                      />
                      <span
                        className="w-3 h-0.5 rounded inline-block shrink-0"
                        style={{ backgroundColor: edgeColor, opacity: visible ? 1 : 0.3 }}
                      />
                      <span style={{ color: visible ? 'var(--neo-text)' : 'var(--neo-text-dim)' }}>{type}</span>
                      <span className="text-[10px] ml-auto" style={{ color: 'var(--neo-text-dim)' }}>({count})</span>
                    </label>
                  );
                })}
              </div>
            </div>
          )}

          {/* GraphRAG storage summary */}
          {nodes.length > 0 && (() => {
            const counts = {};
            let vectorCount = 0, bm25Count = 0, embeddedCount = 0;
            nodes.forEach(n => {
              const t = n.label || 'other';
              counts[t] = (counts[t] || 0) + 1;
              const p = n.properties || {};
              if (p.vector_ref) vectorCount++;
              if (p.bm25_ref) bm25Count++;
              if (p.has_embedding) embeddedCount++;
            });
            const hasStorage = vectorCount > 0 || bm25Count > 0 || embeddedCount > 0;
            return hasStorage ? (
              <div className="mb-3 p-2 rounded-lg" style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)' }}>
                <div className="text-[10px] font-medium uppercase tracking-wider mb-1.5" style={{ color: 'var(--neo-text-dim)' }}>Storage</div>
                {vectorCount > 0 && (
                  <div className="flex items-center gap-1.5 text-[10px] py-0.5">
                    <span className="w-2 h-2 rounded-full" style={{ background: '#7c3aed' }} />
                    <span style={{ color: 'var(--neo-text)' }}>{vectorCount} vectors</span>
                  </div>
                )}
                {bm25Count > 0 && (
                  <div className="flex items-center gap-1.5 text-[10px] py-0.5">
                    <span className="w-2 h-2 rounded-full" style={{ background: '#f97316' }} />
                    <span style={{ color: 'var(--neo-text)' }}>{bm25Count} BM25 indexed</span>
                  </div>
                )}
                {embeddedCount > 0 && (
                  <div className="flex items-center gap-1.5 text-[10px] py-0.5">
                    <span className="w-2 h-2 rounded-full" style={{ background: '#8b5cf6' }} />
                    <span style={{ color: 'var(--neo-text)' }}>{embeddedCount} embedded</span>
                  </div>
                )}
              </div>
            ) : null;
          })()}

          {/* Divider */}
          <div className="my-2" style={{ borderTop: '1px solid var(--neo-border)' }} />

          {/* Property inspector */}
          {renderPropertyPanel()}

          {/* Quick queries */}
          <div className="mt-3" style={{ borderTop: '1px solid var(--neo-border)', paddingTop: '8px' }}>
            <div className="text-xs font-medium mb-1.5" style={{ color: 'var(--neo-text-muted)' }}>Quick Queries</div>
            {[
              { label: 'All Nodes', q: 'FIND nodes' },
              { label: 'All Edges', q: 'FIND edges' },
              { label: 'Show All', q: 'FIND nodes, edges' },
            ].map(({ label, q }) => (
              <button
                key={q}
                onClick={() => { setQuery(q); }}
                className="block w-full text-left text-xs px-2 py-1 rounded mb-0.5 hover:opacity-80"
                style={{ color: 'var(--neo-blue)', background: 'transparent' }}
              >
                {label}
              </button>
            ))}
          </div>
        </div>}
      </div>
    </div>
  );
}

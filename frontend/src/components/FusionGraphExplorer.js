/**
 * FusionGraphExplorer — Cytoscape.js visualizer for cross-context graph fusion.
 *
 * Shows atomic contexts as clusters, entities as nodes within clusters,
 * and fused edges crossing between clusters.
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import cytoscape from 'cytoscape';
import dagre from 'cytoscape-dagre';
import {
  Play, RotateCcw, ZoomIn, ZoomOut, Maximize2, Loader2,
  LayoutGrid, Circle, GitBranch, Eye,
} from 'lucide-react';
import api from '../lib/api';
import { toast } from 'react-hot-toast';

try { cytoscape.use(dagre); } catch (_) {}

// Context colors
const CONTEXT_COLORS = {
  tcs: '#3b82f6',
  infosys: '#8b5cf6',
  wipro: '#ec4899',
  hcltech: '#f59e0b',
  tech_mahindra: '#10b981',
  india_economy: '#ef4444',
  us_economy: '#06b6d4',
  it_services_sector: '#f97316',
  global_macro: '#6b7280',
  tcs_price: '#2563eb',
  infosys_price: '#7c3aed',
};

const SENTIMENT_COLORS = {
  positive: '#10b981',
  negative: '#ef4444',
  mixed: '#f59e0b',
  neutral: '#6b7280',
};

const EDGE_COLORS = {
  COINCIDES_WITH: '#f59e0b',
  FOLLOWS: '#3b82f6',
  CAUSES: '#ef4444',
  CORRELATES_WITH: '#8b5cf6',
  BRIDGE_ENTITY: '#10b981',
};

export default function FusionGraphExplorer({ contexts = [], fusionContext = '' }) {
  const containerRef = useRef(null);
  const cyRef = useRef(null);
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState(null);
  const [selectedNode, setSelectedNode] = useState(null);
  const [layout, setLayout] = useState('dagre');

  const loadFusion = useCallback(async () => {
    if (!contexts.length) return;
    setLoading(true);

    try {
      // Fetch fusion data via runtime assembly
      const res = await api.post('/intelligence/runtime/assemble', {
        contexts,
        days: 30,
      });

      const view = res.data;

      // Also try to load fusion graph if it exists
      let fusionEdges = [];
      if (fusionContext) {
        try {
          const fusionRes = await api.get(`/graph/nodes`, { params: { graph: fusionContext, limit: 500 } });
          fusionEdges = (fusionRes.data.nodes || []).filter(n => n.label === 'FusedInsight');
        } catch (_) {}
      }

      buildGraph(view, fusionEdges);
      setStats(view.stats);
    } catch (e) {
      toast.error('Failed to load fusion data');
      console.error(e);
    }
    setLoading(false);
  }, [contexts, fusionContext]);

  const buildGraph = (view, fusionEdges) => {
    if (!containerRef.current) return;

    // Destroy existing
    if (cyRef.current) cyRef.current.destroy();

    const elements = [];

    // Create compound nodes for each context (clusters)
    const contextSet = new Set();
    (view.entities || []).forEach(e => contextSet.add(e.source_context));
    (view.facts || []).forEach(f => contextSet.add(f.source_context));

    contextSet.forEach(ctx => {
      elements.push({
        data: {
          id: `ctx_${ctx}`,
          label: ctx.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
          type: 'context',
        },
        classes: 'context-node',
      });
    });

    // Add entity nodes
    const entityIds = new Set();
    (view.entities || []).forEach((e, i) => {
      const id = `ent_${i}_${e.name.replace(/\s+/g, '_').substring(0, 20)}`;
      if (entityIds.has(id)) return;
      entityIds.add(id);

      const color = CONTEXT_COLORS[e.source_context] || '#6b7280';
      const sentColor = SENTIMENT_COLORS[e.sentiment] || '#6b7280';

      elements.push({
        data: {
          id,
          label: e.name.substring(0, 25),
          parent: `ctx_${e.source_context}`,
          type: 'entity',
          sentiment: e.sentiment,
          confidence: e.confidence,
          context: e.source_context,
          color,
          sentColor,
          fullData: e,
        },
        classes: `entity-node sentiment-${e.sentiment || 'unknown'}`,
      });
    });

    // Add fact nodes (smaller)
    (view.facts || []).slice(0, 30).forEach((f, i) => {
      const id = `fact_${i}`;
      const color = CONTEXT_COLORS[f.source_context] || '#6b7280';

      elements.push({
        data: {
          id,
          label: (f.statement || '').substring(0, 30) + '...',
          parent: `ctx_${f.source_context}`,
          type: 'fact',
          sentiment: f.sentiment,
          context: f.source_context,
          color,
          fullData: f,
        },
        classes: 'fact-node',
      });
    });

    // Add fusion edges (cross-context)
    fusionEdges.forEach((fe, i) => {
      const props = fe.properties || {};
      const srcEntity = props.source_entity || '';
      const tgtEntity = props.target_entity || '';

      // Find matching source and target nodes
      const srcNode = [...entityIds].find(id => {
        const name = id.replace('ent_', '').split('_').slice(1).join('_');
        return srcEntity.toLowerCase().includes(name.toLowerCase().substring(0, 5));
      });
      const tgtNode = [...entityIds].find(id => {
        const name = id.replace('ent_', '').split('_').slice(1).join('_');
        return tgtEntity.toLowerCase().includes(name.toLowerCase().substring(0, 5));
      });

      if (srcNode && tgtNode && srcNode !== tgtNode) {
        const edgeType = props.edge_type || 'COINCIDES_WITH';
        elements.push({
          data: {
            id: `fusion_${i}`,
            source: srcNode,
            target: tgtNode,
            label: edgeType.replace(/_/g, ' '),
            edgeType,
            confidence: props.confidence || 0,
            color: EDGE_COLORS[edgeType] || '#f59e0b',
            fullData: props,
          },
          classes: 'fusion-edge',
        });
      }
    });

    // Add cross-context entity pairs as edges
    (view.cross_entity_pairs || []).forEach((pair, i) => {
      if (pair.contexts && pair.contexts.length >= 2) {
        const srcCtx = pair.contexts[0];
        const tgtCtx = pair.contexts[1];

        // Find entity nodes in each context
        const srcNodes = [...entityIds].filter(id => id.includes(pair.entity?.substring(0, 8)?.replace(/\s/g, '_')));
        if (srcNodes.length >= 2) {
          elements.push({
            data: {
              id: `bridge_${i}`,
              source: srcNodes[0],
              target: srcNodes[1],
              label: 'bridge',
              edgeType: 'BRIDGE_ENTITY',
              color: '#10b981',
            },
            classes: 'bridge-edge',
          });
        }
      }
    });

    // Create Cytoscape instance
    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: [
        // Context cluster
        {
          selector: '.context-node',
          style: {
            'background-color': '#1a1a2e',
            'background-opacity': 0.3,
            'border-width': 2,
            'border-color': '#374151',
            'border-style': 'dashed',
            'label': 'data(label)',
            'text-valign': 'top',
            'text-halign': 'center',
            'font-size': '14px',
            'color': '#9ca3af',
            'font-weight': 'bold',
            'padding': '30px',
            'text-margin-y': -10,
          },
        },
        // Entity node
        {
          selector: '.entity-node',
          style: {
            'background-color': 'data(color)',
            'label': 'data(label)',
            'text-valign': 'bottom',
            'text-halign': 'center',
            'font-size': '10px',
            'color': '#d1d5db',
            'width': 30,
            'height': 30,
            'border-width': 3,
            'border-color': 'data(sentColor)',
            'text-margin-y': 5,
          },
        },
        // Fact node (smaller, diamond)
        {
          selector: '.fact-node',
          style: {
            'background-color': 'data(color)',
            'background-opacity': 0.6,
            'shape': 'diamond',
            'width': 15,
            'height': 15,
            'label': '',
          },
        },
        // Fusion edge
        {
          selector: '.fusion-edge',
          style: {
            'line-color': 'data(color)',
            'target-arrow-color': 'data(color)',
            'target-arrow-shape': 'triangle',
            'width': 2,
            'curve-style': 'bezier',
            'label': 'data(label)',
            'font-size': '8px',
            'color': '#9ca3af',
            'text-rotation': 'autorotate',
            'text-margin-y': -10,
            'line-style': 'dashed',
            'line-dash-pattern': [6, 3],
          },
        },
        // Bridge edge
        {
          selector: '.bridge-edge',
          style: {
            'line-color': '#10b981',
            'width': 3,
            'curve-style': 'bezier',
            'line-style': 'dotted',
            'line-dash-pattern': [2, 4],
          },
        },
        // Sentiment colors
        {
          selector: '.sentiment-positive',
          style: { 'border-color': '#10b981' },
        },
        {
          selector: '.sentiment-negative',
          style: { 'border-color': '#ef4444' },
        },
        {
          selector: '.sentiment-mixed',
          style: { 'border-color': '#f59e0b' },
        },
        // Selected
        {
          selector: ':selected',
          style: {
            'border-color': '#ec4899',
            'border-width': 4,
            'background-color': '#ec4899',
          },
        },
      ],
      layout: { name: layout === 'dagre' ? 'dagre' : layout === 'circle' ? 'circle' : 'cose', padding: 40 },
      minZoom: 0.2,
      maxZoom: 3,
    });

    // Click handler
    cy.on('tap', 'node', (evt) => {
      const data = evt.target.data();
      if (data.type !== 'context') {
        setSelectedNode(data);
      }
    });

    cy.on('tap', 'edge', (evt) => {
      setSelectedNode({ ...evt.target.data(), _isEdge: true });
    });

    cy.on('tap', (evt) => {
      if (evt.target === cy) setSelectedNode(null);
    });

    cyRef.current = cy;
  };

  useEffect(() => {
    if (contexts.length > 0) loadFusion();
    return () => { if (cyRef.current) cyRef.current.destroy(); };
  }, [contexts, fusionContext]);

  const relayout = (name) => {
    setLayout(name);
    if (cyRef.current) {
      cyRef.current.layout({ name: name === 'dagre' ? 'dagre' : name === 'circle' ? 'circle' : 'cose', padding: 40, animate: true }).run();
    }
  };

  return (
    <div className="relative" style={{ height: '500px', background: '#0a0a1a', borderRadius: 8, overflow: 'hidden', border: '1px solid var(--neo-border)' }}>
      {/* Toolbar */}
      <div className="absolute top-2 left-2 z-10 flex gap-1">
        <button onClick={() => relayout('dagre')} className="p-1.5 rounded" style={{ background: layout === 'dagre' ? '#3b82f6' : '#1f2937', color: '#fff' }} title="Tree layout">
          <GitBranch size={14} />
        </button>
        <button onClick={() => relayout('circle')} className="p-1.5 rounded" style={{ background: layout === 'circle' ? '#3b82f6' : '#1f2937', color: '#fff' }} title="Circle layout">
          <Circle size={14} />
        </button>
        <button onClick={() => relayout('cose')} className="p-1.5 rounded" style={{ background: layout === 'cose' ? '#3b82f6' : '#1f2937', color: '#fff' }} title="Force layout">
          <LayoutGrid size={14} />
        </button>
        <div className="w-px mx-1" style={{ background: '#374151' }} />
        <button onClick={() => cyRef.current?.fit()} className="p-1.5 rounded" style={{ background: '#1f2937', color: '#fff' }} title="Fit to view">
          <Maximize2 size={14} />
        </button>
        <button onClick={() => cyRef.current?.zoom(cyRef.current.zoom() * 1.3)} className="p-1.5 rounded" style={{ background: '#1f2937', color: '#fff' }}>
          <ZoomIn size={14} />
        </button>
        <button onClick={() => cyRef.current?.zoom(cyRef.current.zoom() * 0.7)} className="p-1.5 rounded" style={{ background: '#1f2937', color: '#fff' }}>
          <ZoomOut size={14} />
        </button>
        <button onClick={loadFusion} disabled={loading} className="p-1.5 rounded" style={{ background: '#1f2937', color: '#fff' }}>
          {loading ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />}
        </button>
      </div>

      {/* Stats */}
      {stats && (
        <div className="absolute top-2 right-2 z-10 text-[10px] px-2 py-1 rounded" style={{ background: '#1f293790', color: '#9ca3af' }}>
          {stats.contexts_queried} contexts | {stats.total_entities} entities | {stats.total_facts} facts
        </div>
      )}

      {/* Legend */}
      <div className="absolute bottom-2 left-2 z-10 flex gap-3 text-[9px] px-2 py-1 rounded" style={{ background: '#1f293790' }}>
        <span style={{ color: '#10b981' }}>● positive</span>
        <span style={{ color: '#ef4444' }}>● negative</span>
        <span style={{ color: '#f59e0b' }}>● mixed</span>
        <span style={{ color: '#f59e0b' }}>--- fusion edge</span>
        <span style={{ color: '#10b981' }}>··· bridge entity</span>
      </div>

      {/* Selected node info */}
      {selectedNode && (
        <div className="absolute bottom-2 right-2 z-10 max-w-xs p-3 rounded-lg text-xs" style={{ background: '#1f2937', border: '1px solid #374151', color: '#d1d5db' }}>
          <div className="flex items-center justify-between mb-1">
            <span className="font-bold" style={{ color: '#ec4899' }}>
              {selectedNode._isEdge ? 'Edge' : selectedNode.type === 'entity' ? 'Entity' : 'Fact'}
            </span>
            <button onClick={() => setSelectedNode(null)} className="text-gray-500 hover:text-white">&times;</button>
          </div>
          <p className="font-medium mb-1">{selectedNode.label}</p>
          {selectedNode.context && <p className="text-[10px]" style={{ color: '#6b7280' }}>Context: {selectedNode.context}</p>}
          {selectedNode.sentiment && <p className="text-[10px]" style={{ color: SENTIMENT_COLORS[selectedNode.sentiment] }}>Sentiment: {selectedNode.sentiment}</p>}
          {selectedNode.confidence > 0 && <p className="text-[10px]">Confidence: {(selectedNode.confidence * 100).toFixed(0)}%</p>}
          {selectedNode.edgeType && <p className="text-[10px]" style={{ color: EDGE_COLORS[selectedNode.edgeType] }}>{selectedNode.edgeType}</p>}
          {selectedNode.fullData && (
            <div className="mt-1 pt-1 border-t" style={{ borderColor: '#374151' }}>
              {Object.entries(selectedNode.fullData).filter(([k]) => !k.startsWith('_') && k !== 'fullData').slice(0, 5).map(([k, v]) => (
                <p key={k} className="text-[9px]"><span style={{ color: '#ec4899' }}>{k}:</span> {String(v).substring(0, 40)}</p>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Cytoscape container */}
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />

      {loading && (
        <div className="absolute inset-0 flex items-center justify-center" style={{ background: '#0a0a1a90' }}>
          <Loader2 size={24} className="animate-spin" style={{ color: '#ec4899' }} />
        </div>
      )}
    </div>
  );
}

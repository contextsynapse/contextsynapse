import React from 'react';
import { LayoutGrid, ZoomIn, ZoomOut, Maximize } from 'lucide-react';

const LAYOUTS = [
  { id: 'dagre', label: 'Hierarchical' },
  { id: 'cose', label: 'Force-directed' },
  { id: 'circle', label: 'Circle' },
  { id: 'grid', label: 'Grid' },
  { id: 'concentric', label: 'Concentric' },
  { id: 'breadthfirst', label: 'Breadth-first' },
];

export default function GraphControls({
  layout,
  onLayoutChange,
  onZoomIn,
  onZoomOut,
  onFit,
  nodeTypes,
  edgeTypes,
  colorMap,
}) {
  return (
    <div className="graph-controls">
      {/* Layout selector */}
      <div className="gc-section">
        <div className="gc-label"><LayoutGrid size={12} /> Layout</div>
        <select
          value={layout}
          onChange={(e) => onLayoutChange(e.target.value)}
          className="gc-select"
        >
          {LAYOUTS.map((l) => (
            <option key={l.id} value={l.id}>{l.label}</option>
          ))}
        </select>
      </div>

      {/* Zoom controls */}
      <div className="gc-section gc-row">
        <button onClick={onZoomIn} className="gc-btn" title="Zoom in"><ZoomIn size={14} /></button>
        <button onClick={onZoomOut} className="gc-btn" title="Zoom out"><ZoomOut size={14} /></button>
        <button onClick={onFit} className="gc-btn" title="Fit to view"><Maximize size={14} /></button>
      </div>

      {/* Node type legend */}
      {Object.keys(nodeTypes).length > 0 && (
        <div className="gc-section">
          <div className="gc-label">Node Types</div>
          {Object.entries(nodeTypes).map(([type, visible]) => (
            <div key={type} className="gc-legend-item">
              <span
                className="gc-swatch"
                style={{ backgroundColor: colorMap.nodes?.[type] || '#3498db' }}
              />
              <span className="gc-legend-text">{type}</span>
            </div>
          ))}
        </div>
      )}

      {/* Edge type legend */}
      {Object.keys(edgeTypes).length > 0 && (
        <div className="gc-section">
          <div className="gc-label">Edge Types</div>
          {Object.entries(edgeTypes).map(([type, visible]) => (
            <div key={type} className="gc-legend-item">
              <span
                className="gc-swatch gc-swatch-edge"
                style={{ backgroundColor: colorMap.edges?.[type] || '#ff6b6b' }}
              />
              <span className="gc-legend-text">{type}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

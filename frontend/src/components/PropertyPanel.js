import React from 'react';
import { X } from 'lucide-react';

export default function PropertyPanel({ item, onClose }) {
  if (!item) {
    return (
      <div className="property-panel-empty">
        <p>Click a node or edge to inspect.</p>
      </div>
    );
  }

  const isEdge = !!(item.source && item.target);
  const displayType = item.label || item.type || (isEdge ? 'EDGE' : 'Node');

  // Collect properties, excluding structural fields
  const SKIP = new Set([
    'id', 'uuid', 'label', 'type', 'source', 'target',
    'from_id', 'to_id', 'domain', 'edge_type', 'node_type',
    '_color', '_x', '_y',
  ]);
  const props = Object.entries(item).filter(
    ([k]) => !SKIP.has(k) && !k.startsWith('_'),
  );

  return (
    <div className="property-panel">
      <div className="property-panel-header">
        <span className="property-panel-type">{isEdge ? 'Edge' : 'Node'}: {displayType}</span>
        <button onClick={onClose} className="property-panel-close" title="Close">
          <X size={14} />
        </button>
      </div>

      <div className="property-panel-body">
        <div className="property-row">
          <span className="property-key">ID</span>
          <span className="property-value mono">{(item.id || item.uuid || '—').substring(0, 12)}</span>
        </div>

        {isEdge && (
          <>
            <div className="property-row">
              <span className="property-key">Source</span>
              <span className="property-value mono">{(item.source || '').substring(0, 12)}</span>
            </div>
            <div className="property-row">
              <span className="property-key">Target</span>
              <span className="property-value mono">{(item.target || '').substring(0, 12)}</span>
            </div>
          </>
        )}

        {props.length > 0 && (
          <>
            <div className="property-divider" />
            {props.map(([key, value]) => (
              <div className="property-row" key={key}>
                <span className="property-key">{key}</span>
                <span className="property-value">
                  {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                </span>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}

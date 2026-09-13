import React, { useState, useEffect, useCallback } from 'react';
import { GitBranch, Database, Loader2, Eye } from 'lucide-react';
import api from '../../lib/api';
import DashboardGraphExplorer from '../../components/DashboardGraphExplorer';

export default function ExplorerPage() {
  const [graphs, setGraphs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedGraph, setSelectedGraph] = useState('');

  const fetchGraphs = useCallback(() => {
    api.get('/dashboard/graphs')
      .then((res) => {
        const g = res.data.graphs || [];
        setGraphs(g);
        // Don't auto-select — let user choose which graph to explore
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [selectedGraph]);

  useEffect(() => { fetchGraphs(); }, [fetchGraphs]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      </div>
    );
  }

  return (
    <div style={{ minHeight: 'calc(100vh - 160px)' }}>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-xl font-bold flex items-center gap-2" style={{ color: 'var(--neo-text)' }}>
            <GitBranch size={20} />
            Graph Explorer
          </h1>
          <p className="text-sm mt-1" style={{ color: 'var(--neo-text-muted)' }}>
            Visualize and interact with your graph data
          </p>
        </div>
        <select
          value={selectedGraph}
          onChange={(e) => setSelectedGraph(e.target.value)}
          className="px-3 py-1.5 rounded-lg text-sm outline-none"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
        >
          <option value="">Select graph</option>
          {graphs.map((g) => {
            const name = g.display_name || g.name;
            return <option key={g.name} value={name}>{name}</option>;
          })}
        </select>
      </div>

      {/* Explorer */}
      {selectedGraph ? (
        <div
          className="rounded-lg overflow-hidden"
          style={{ border: '1px solid var(--neo-border)', height: 'calc(100vh - 240px)' }}
        >
          <DashboardGraphExplorer graphName={selectedGraph} embedded />
        </div>
      ) : (
        <div
          className="flex items-center justify-center rounded-lg"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', height: 'calc(100vh - 240px)' }}
        >
          <div className="text-center">
            <Eye size={40} className="mx-auto mb-3" style={{ color: 'var(--neo-text-muted)', opacity: 0.4 }} />
            <p className="text-sm font-medium" style={{ color: 'var(--neo-text-muted)' }}>
              Select a graph to explore
            </p>
            <p className="text-xs mt-1" style={{ color: 'var(--neo-text-muted)', opacity: 0.7 }}>
              Choose a graph from the dropdown above
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

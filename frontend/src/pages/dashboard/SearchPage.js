import React, { useState, useEffect, useCallback } from 'react';
import { Search, Loader2, ExternalLink } from 'lucide-react';
import api from '../../lib/api';

const MODES = [
  { id: 'keyword', label: 'Keyword' },
  { id: 'semantic', label: 'Semantic' },
  { id: 'hybrid', label: 'Hybrid' },
];

export default function SearchPage() {
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState('hybrid');
  const [results, setResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [graphs, setGraphs] = useState([]);
  const [selectedGraph, setSelectedGraph] = useState('');

  const fetchGraphs = useCallback(() => {
    api.get('/dashboard/graphs')
      .then((res) => {
        const g = res.data.graphs || [];
        setGraphs(g);
        if (g.length > 0 && !selectedGraph) setSelectedGraph(g[0].name);
      })
      .catch(() => {});
  }, [selectedGraph]);

  useEffect(() => { fetchGraphs(); }, [fetchGraphs]);

  const handleSearch = async (e) => {
    e?.preventDefault();
    if (!query.trim()) return;
    setSearching(true);
    setResults(null);
    try {
      const res = await api.post('/dashboard/search', {
        query: query.trim(),
        mode,
        graph: selectedGraph,
        limit: 20,
      });
      setResults(res.data.results || []);
    } catch (err) {
      setResults([]);
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="max-w-3xl">
      <h1 className="text-xl font-bold mb-1" style={{ color: 'var(--neo-text)' }}>Search</h1>
      <p className="text-sm mb-6" style={{ color: 'var(--neo-text-muted)' }}>
        Search across your graph data using keyword, semantic, or hybrid search
      </p>

      {/* Search bar */}
      <form onSubmit={handleSearch} className="space-y-3 mb-6">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: 'var(--neo-text-muted)' }} />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search your graph..."
              className="w-full pl-9 pr-3 py-2.5 rounded-lg text-sm outline-none"
              style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            />
          </div>
          <button
            type="submit"
            disabled={searching || !query.trim()}
            className="px-5 py-2.5 rounded-lg text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            {searching ? <Loader2 size={16} className="animate-spin" /> : 'Search'}
          </button>
        </div>

        <div className="flex items-center gap-3">
          {/* Mode selector */}
          <div className="flex gap-1">
            {MODES.map((m) => (
              <button
                key={m.id}
                type="button"
                onClick={() => setMode(m.id)}
                className="px-3 py-1 rounded-lg text-xs font-medium transition"
                style={{
                  background: mode === m.id ? 'var(--neo-blue)' : 'transparent',
                  color: mode === m.id ? '#fff' : 'var(--neo-text-muted)',
                  border: `1px solid ${mode === m.id ? 'var(--neo-blue)' : 'var(--neo-border)'}`,
                }}
              >
                {m.label}
              </button>
            ))}
          </div>

          {/* Graph filter */}
          <select
            value={selectedGraph}
            onChange={(e) => setSelectedGraph(e.target.value)}
            className="px-2 py-1 rounded text-xs outline-none"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          >
            <option value="">All graphs</option>
            {graphs.map((g) => (
              <option key={g.name} value={g.display_name || g.name}>{g.display_name || g.name}</option>
            ))}
          </select>
        </div>
      </form>

      {/* Results */}
      {results !== null && (
        <div>
          <div className="text-xs font-medium mb-3" style={{ color: 'var(--neo-text-muted)' }}>
            {results.length} result{results.length !== 1 ? 's' : ''}
          </div>

          {results.length === 0 ? (
            <div
              className="text-center py-12 rounded-xl"
              style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
            >
              <Search size={32} className="mx-auto mb-2" style={{ color: 'var(--neo-text-muted)' }} />
              <p style={{ color: 'var(--neo-text-muted)' }}>No results found</p>
            </div>
          ) : (
            <div className="space-y-2">
              {results.map((r, i) => (
                <div
                  key={r.node_id || i}
                  className="p-4 rounded-xl transition hover:opacity-90"
                  style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
                >
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-sm" style={{ color: 'var(--neo-text)' }}>
                          {r.label || r.name || r.node_id || `Node ${i + 1}`}
                        </span>
                        {r.node_type && (
                          <span
                            className="px-1.5 py-0.5 rounded text-xs"
                            style={{ background: 'var(--neo-bg)', color: 'var(--neo-blue)' }}
                          >
                            {r.node_type}
                          </span>
                        )}
                        {r.graph && (
                          <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                            in {r.graph}
                          </span>
                        )}
                      </div>

                      {r.snippet && (
                        <p className="text-xs mt-1" style={{ color: 'var(--neo-text-muted)' }}>
                          {r.snippet}
                        </p>
                      )}

                      {r.properties && Object.keys(r.properties).length > 0 && (
                        <div className="flex flex-wrap gap-2 mt-2">
                          {Object.entries(r.properties).slice(0, 5).map(([k, v]) => (
                            <span key={k} className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                              <span className="font-medium">{k}:</span> {String(v).substring(0, 50)}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>

                    {r.score !== undefined && (
                      <span
                        className="text-xs font-mono shrink-0 ml-3 px-2 py-1 rounded"
                        style={{ background: 'var(--neo-bg)', color: 'var(--neo-green)' }}
                      >
                        {(r.score * 100).toFixed(0)}%
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

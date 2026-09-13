import React, { useState, useEffect } from 'react';
import { SlidersHorizontal, Loader2, Play, CheckCircle, XCircle, Zap, Clock, DollarSign, BarChart3 } from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../../lib/api';

function fmtTime(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  const mo = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getUTCMonth()];
  return `${mo} ${d.getUTCDate()}, ${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')} UTC`;
}

export default function ComparePage() {
  const [available, setAvailable] = useState([]);
  const [selected, setSelected] = useState([]);
  const [text, setText] = useState('');
  const [schema, setSchema] = useState('');
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState(null);

  useEffect(() => {
    api.get('/dashboard/compare-models/available')
      .then(r => {
        const models = r.data.models || [];
        setAvailable(models);
        // Pre-select first 2 available
        setSelected(models.slice(0, 2).map(m => m.spec));
      })
      .catch(() => {});
  }, []);

  const toggleModel = (spec) => {
    setSelected(prev =>
      prev.includes(spec)
        ? prev.filter(s => s !== spec)
        : prev.length < 5 ? [...prev, spec] : prev
    );
  };

  const handleRun = async () => {
    if (!text.trim() || selected.length < 2) return;
    setRunning(true);
    setResults(null);
    try {
      const res = await api.post('/dashboard/compare-models', {
        text: text.trim(),
        models: selected,
        schema_name: schema || undefined,
      });
      setResults(res.data);
    } catch (e) {
      toast.error('Comparison failed');
    } finally {
      setRunning(false);
    }
  };

  const completed = results?.models?.filter(m => m.status === 'completed') || [];
  const maxEntities = Math.max(...completed.map(m => m.entities || 0), 1);
  const maxRels = Math.max(...completed.map(m => m.relationships || 0), 1);
  const maxTime = Math.max(...completed.map(m => m.time_seconds || 0), 1);

  return (
    <div className="max-w-5xl">
      <h1 className="text-xl font-bold mb-1" style={{ color: 'var(--neo-text)' }}>Model Comparison</h1>
      <p className="text-sm mb-6" style={{ color: 'var(--neo-text-muted)' }}>
        Run the same extraction with multiple LLMs — compare speed, quality, and cost.
      </p>

      {/* Model selector */}
      <div className="mb-4">
        <label className="block text-xs font-medium mb-2" style={{ color: 'var(--neo-text-muted)' }}>
          Select models to compare (2–5)
        </label>
        <div className="flex flex-wrap gap-2">
          {available.map((m) => {
            const isSelected = selected.includes(m.spec);
            return (
              <button
                key={m.spec}
                onClick={() => toggleModel(m.spec)}
                disabled={running}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition disabled:opacity-50"
                style={{
                  background: isSelected ? 'var(--neo-blue)' : 'var(--neo-surface)',
                  color: isSelected ? '#fff' : 'var(--neo-text-muted)',
                  border: `1px solid ${isSelected ? 'var(--neo-blue)' : 'var(--neo-border)'}`,
                }}
              >
                {m.name}
                {m.free && <span className="text-[9px] px-1 rounded" style={{ background: 'rgba(34,197,94,0.2)', color: '#22c55e' }}>free</span>}
              </button>
            );
          })}
        </div>
      </div>

      {/* Text input + schema */}
      <div className="flex gap-3 mb-4">
        <div className="flex-1">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Paste text to extract from... (e.g., project requirements, meeting notes, documentation)"
            rows={5}
            className="w-full px-3 py-2 rounded-lg text-sm outline-none resize-y"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            disabled={running}
          />
        </div>
        <div className="w-48 space-y-2">
          <select
            value={schema}
            onChange={(e) => setSchema(e.target.value)}
            className="w-full px-2 py-1.5 rounded-lg text-xs outline-none"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          >
            <option value="">No schema (generic)</option>
            <option value="software_dev">SDLC Schema</option>
            <option value="knowledge_base">Knowledge Base</option>
            <option value="rules">Rules & Standards</option>
            <option value="database">Database</option>
            <option value="decision">Decisions</option>
            <option value="web">Web Content</option>
          </select>
          <button
            onClick={handleRun}
            disabled={running || !text.trim() || selected.length < 2}
            className="w-full flex items-center justify-center gap-1.5 py-2 rounded-lg text-sm font-medium transition disabled:opacity-50"
            style={{ background: 'var(--neo-green)', color: '#fff' }}
          >
            {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
            {running ? 'Running...' : 'Compare'}
          </button>
          <div className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
            {selected.length} models selected · {text.length} chars
          </div>
        </div>
      </div>

      {/* Results */}
      {results && (
        <div className="space-y-4">
          {/* Winner badges */}
          {results.comparison && (
            <div className="flex flex-wrap gap-2">
              {results.comparison.fastest && (
                <span className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium"
                  style={{ background: 'rgba(34,197,94,0.1)', color: '#22c55e' }}>
                  <Zap size={12} /> Fastest: {results.comparison.fastest}
                </span>
              )}
              {results.comparison.best_quality && (
                <span className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium"
                  style={{ background: 'rgba(99,102,241,0.1)', color: '#6366f1' }}>
                  <BarChart3 size={12} /> Best Quality: {results.comparison.best_quality}
                </span>
              )}
              {results.comparison.cheapest && (
                <span className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium"
                  style={{ background: 'rgba(245,158,11,0.1)', color: '#f59e0b' }}>
                  <DollarSign size={12} /> Cheapest: {results.comparison.cheapest}
                </span>
              )}
              {results.comparison.best_value && (
                <span className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium"
                  style={{ background: 'rgba(236,72,153,0.1)', color: '#ec4899' }}>
                  <SlidersHorizontal size={12} /> Best Value: {results.comparison.best_value}
                </span>
              )}
            </div>
          )}

          {/* Model cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {results.models?.map((m) => {
              const isBest = results.comparison?.best_quality === m.model_spec;
              return (
                <div key={m.model_spec} className="rounded-xl p-4"
                  style={{
                    background: 'var(--neo-surface)',
                    border: isBest ? '2px solid #6366f1' : '1px solid var(--neo-border)',
                  }}>
                  <div className="flex items-center justify-between mb-3">
                    <div>
                      <div className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>{m.model}</div>
                      <div className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>{m.provider}</div>
                    </div>
                    {m.status === 'completed' ? (
                      <CheckCircle size={16} style={{ color: '#22c55e' }} />
                    ) : (
                      <XCircle size={16} style={{ color: '#ef4444' }} />
                    )}
                  </div>

                  {m.status === 'completed' ? (
                    <div className="space-y-2">
                      {/* Speed bar */}
                      <div>
                        <div className="flex justify-between text-[10px] mb-0.5">
                          <span style={{ color: 'var(--neo-text-muted)' }}>Speed</span>
                          <span style={{ color: 'var(--neo-text)' }}>{m.time_seconds}s</span>
                        </div>
                        <div className="h-1.5 rounded-full" style={{ background: 'var(--neo-bg)' }}>
                          <div className="h-full rounded-full transition-all" style={{
                            width: `${Math.max(5, 100 - (m.time_seconds / maxTime * 100))}%`,
                            background: '#22c55e',
                          }} />
                        </div>
                      </div>

                      {/* Entities bar */}
                      <div>
                        <div className="flex justify-between text-[10px] mb-0.5">
                          <span style={{ color: 'var(--neo-text-muted)' }}>Entities</span>
                          <span style={{ color: 'var(--neo-text)' }}>{m.entities}</span>
                        </div>
                        <div className="h-1.5 rounded-full" style={{ background: 'var(--neo-bg)' }}>
                          <div className="h-full rounded-full" style={{
                            width: `${Math.max(5, m.entities / maxEntities * 100)}%`,
                            background: '#6366f1',
                          }} />
                        </div>
                      </div>

                      {/* Relationships bar */}
                      <div>
                        <div className="flex justify-between text-[10px] mb-0.5">
                          <span style={{ color: 'var(--neo-text-muted)' }}>Relationships</span>
                          <span style={{ color: 'var(--neo-text)' }}>{m.relationships}</span>
                        </div>
                        <div className="h-1.5 rounded-full" style={{ background: 'var(--neo-bg)' }}>
                          <div className="h-full rounded-full" style={{
                            width: `${Math.max(5, m.relationships / maxRels * 100)}%`,
                            background: '#f59e0b',
                          }} />
                        </div>
                      </div>

                      {/* Stats */}
                      <div className="flex justify-between text-[10px] pt-1" style={{ borderTop: '1px solid var(--neo-border)' }}>
                        <span style={{ color: 'var(--neo-text-muted)' }}>Facts: {m.facts || 0}</span>
                        <span style={{ color: 'var(--neo-text-muted)' }}>Cost: ${m.estimated_cost_usd?.toFixed(4) || '0'}</span>
                        <span style={{ color: 'var(--neo-text-muted)' }}>Score: {m.quality_score}</span>
                      </div>

                      {/* Sample entities */}
                      {m.sample_entities?.length > 0 && (
                        <div className="pt-1">
                          <div className="text-[10px] font-medium mb-1" style={{ color: 'var(--neo-text-dim)' }}>Sample entities:</div>
                          <div className="flex flex-wrap gap-1">
                            {m.sample_entities.map((e, i) => (
                              <span key={i} className="px-1.5 py-0.5 rounded text-[9px]"
                                style={{ background: 'var(--neo-bg)', color: 'var(--neo-text-muted)' }}>
                                {e.type}: {e.name}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="text-xs" style={{ color: '#ef4444' }}>
                      Failed: {m.error?.slice(0, 100)}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Empty state */}
      {!results && !running && (
        <div className="rounded-xl p-8 text-center" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
          <SlidersHorizontal size={32} className="mx-auto mb-3" style={{ color: 'var(--neo-text-muted)', opacity: 0.5 }} />
          <p className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>Compare LLM Models</p>
          <p className="text-xs mt-1" style={{ color: 'var(--neo-text-muted)' }}>
            Paste text, select 2+ models, and see which extracts more entities faster and cheaper.
          </p>
        </div>
      )}
    </div>
  );
}

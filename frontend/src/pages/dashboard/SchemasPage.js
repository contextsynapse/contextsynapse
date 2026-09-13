/**
 * SchemasPage — Schema Library for browsing, creating, and managing
 * extraction schemas (graph ontologies).
 *
 * Grid of schema cards with search + tag filtering. Click to edit (custom)
 * or view (builtin). "New Schema" opens SchemaBuilder in create mode.
 */

import React, { useState, useEffect, useCallback } from 'react';
import {
  Search, Plus, Trash2, Loader2, Database, Tag, X, Eye,
  Edit3, ChevronRight, Layers, FileText, Lock,
} from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import SchemaBuilder from '../../components/SchemaBuilder';

// ---------------------------------------------------------------------------
// Schema Card
// ---------------------------------------------------------------------------

function SchemaCard({ schema, onSelect }) {
  const isBuiltin = schema.source === 'builtin';
  return (
    <button
      onClick={() => onSelect(schema)}
      className="text-left p-4 rounded-xl transition hover:shadow-md group"
      style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
    >
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2">
          <div
            className="w-8 h-8 rounded-lg flex items-center justify-center"
            style={{ background: isBuiltin ? 'rgba(34,197,94,0.12)' : 'rgba(139,92,246,0.12)' }}
          >
            {isBuiltin ? <Lock size={14} style={{ color: '#22c55e' }} /> : <Edit3 size={14} style={{ color: '#8b5cf6' }} />}
          </div>
          <div>
            <div className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>{schema.name}</div>
            <span
              className="text-xs px-1.5 py-0.5 rounded"
              style={{
                background: isBuiltin ? 'rgba(34,197,94,0.1)' : 'rgba(139,92,246,0.1)',
                color: isBuiltin ? '#22c55e' : '#8b5cf6',
              }}
            >
              {isBuiltin ? 'Built-in' : 'Custom'}
            </span>
          </div>
        </div>
        <ChevronRight size={14} className="opacity-0 group-hover:opacity-100 transition" style={{ color: 'var(--neo-text-dim)' }} />
      </div>

      {schema.description && (
        <p className="text-xs mb-2 line-clamp-2" style={{ color: 'var(--neo-text-muted)' }}>
          {schema.description}
        </p>
      )}

      <div className="flex items-center gap-3 text-xs" style={{ color: 'var(--neo-text-dim)' }}>
        <span className="flex items-center gap-1"><Database size={11} /> {schema.node_count} nodes</span>
        <span className="flex items-center gap-1"><Layers size={11} /> {schema.edge_count} edges</span>
      </div>

      {schema.tags?.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2">
          {schema.tags.map(t => (
            <span key={t} className="px-1.5 py-0.5 rounded-full text-xs" style={{ background: 'rgba(59,130,246,0.1)', color: '#3b82f6' }}>
              {t}
            </span>
          ))}
        </div>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function SchemasPage() {
  const [schemas, setSchemas] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [tagFilter, setTagFilter] = useState('');
  const [selected, setSelected] = useState(null); // schema object or 'new'
  const [schemaYaml, setSchemaYaml] = useState('');
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [saving, setSaving] = useState(false);
  const [schemaName, setSchemaName] = useState('');
  const [schemaDesc, setSchemaDesc] = useState('');

  // Fetch all schemas
  const fetchSchemas = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/dashboard/schemas');
      setSchemas(res.data.schemas || []);
    } catch {
      toast.error('Failed to load schemas');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchSchemas(); }, [fetchSchemas]);

  // Collect all unique tags
  const allTags = [...new Set(schemas.flatMap(s => s.tags || []))].sort();

  // Filter schemas
  const filtered = schemas.filter(s => {
    if (search) {
      const q = search.toLowerCase();
      const nameMatch = s.name.toLowerCase().includes(q);
      const descMatch = (s.description || '').toLowerCase().includes(q);
      const tagMatch = (s.tags || []).some(t => t.toLowerCase().includes(q));
      if (!nameMatch && !descMatch && !tagMatch) return false;
    }
    if (tagFilter) {
      if (!(s.tags || []).some(t => t.toLowerCase() === tagFilter.toLowerCase())) return false;
    }
    return true;
  });

  // Select a schema to view/edit
  const handleSelect = async (schema) => {
    setSelected(schema);
    setSchemaName(schema.name);
    setSchemaDesc(schema.description || '');
    setLoadingDetail(true);
    try {
      const res = await api.get(`/dashboard/schemas/${schema.name}`);
      setSchemaYaml(res.data.yaml || '');
    } catch {
      toast.error('Failed to load schema detail');
      setSchemaYaml('');
    } finally {
      setLoadingDetail(false);
    }
  };

  // New schema
  const handleNew = () => {
    setSelected('new');
    setSchemaName('');
    setSchemaDesc('');
    setSchemaYaml('');
  };

  // Save (create or update custom)
  const handleSave = async (yaml, stateObj, tags) => {
    const name = stateObj?.name || schemaName;
    if (!name) { toast.error('Schema name is required'); return; }
    setSaving(true);
    try {
      await api.post('/dashboard/schemas', {
        name,
        yaml,
        description: schemaDesc,
        tags: tags || [],
      });
      toast.success(`Schema "${name}" saved`);
      setSelected(null);
      fetchSchemas();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to save schema');
    } finally {
      setSaving(false);
    }
  };

  // Delete custom schema
  const handleDelete = async (name) => {
    if (!window.confirm(`Delete schema "${name}"?`)) return;
    try {
      await api.delete(`/dashboard/schemas/${name}`);
      toast.success('Schema deleted');
      if (selected?.name === name) setSelected(null);
      fetchSchemas();
    } catch {
      toast.error('Failed to delete schema');
    }
  };

  // Close detail
  const handleClose = () => { setSelected(null); };

  // -----------------------------------------------------------------------
  // Detail / Editor view
  // -----------------------------------------------------------------------
  if (selected) {
    const isBuiltin = selected !== 'new' && selected.source === 'builtin';
    const title = selected === 'new' ? 'New Schema' : selected.name;

    return (
      <div className="max-w-3xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <button
              onClick={handleClose}
              className="p-1.5 rounded-lg transition"
              style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
            >
              <X size={14} />
            </button>
            <div>
              <h2 className="text-lg font-semibold" style={{ color: 'var(--neo-text)' }}>{title}</h2>
              {isBuiltin && (
                <span className="text-xs" style={{ color: '#22c55e' }}>Built-in (read-only)</span>
              )}
            </div>
          </div>
          {!isBuiltin && selected !== 'new' && (
            <button
              onClick={() => handleDelete(selected.name)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs transition"
              style={{ border: '1px solid #ef4444', color: '#ef4444' }}
            >
              <Trash2 size={12} /> Delete
            </button>
          )}
        </div>

        {/* Description (editable for custom/new) */}
        {!isBuiltin && (
          <input
            value={schemaDesc}
            onChange={e => setSchemaDesc(e.target.value)}
            placeholder="Schema description..."
            className="w-full px-3 py-2 rounded-lg text-xs mb-3 outline-none"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          />
        )}

        {loadingDetail ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 size={20} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
          </div>
        ) : (
          <SchemaBuilder
            initialYaml={schemaYaml}
            onSave={handleSave}
            readOnly={isBuiltin}
            showSaveBar={!isBuiltin}
            tags={selected?.tags || []}
          />
        )}
      </div>
    );
  }

  // -----------------------------------------------------------------------
  // List view
  // -----------------------------------------------------------------------
  return (
    <div className="max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-xl font-bold" style={{ color: 'var(--neo-text)' }}>Schema Library</h1>
          <p className="text-xs mt-0.5" style={{ color: 'var(--neo-text-muted)' }}>
            Graph ontologies that guide LLM entity extraction during ingestion.
          </p>
        </div>
        <button
          onClick={handleNew}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition"
          style={{ background: 'var(--neo-blue)', color: '#fff' }}
        >
          <Plus size={14} /> New Schema
        </button>
      </div>

      {/* Search + tag filter */}
      <div className="flex items-center gap-2 mb-4">
        <div className="relative flex-1">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: 'var(--neo-text-dim)' }} />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search by name, description, or tag..."
            className="w-full pl-8 pr-3 py-2 rounded-lg text-xs outline-none"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          />
        </div>
        {allTags.length > 0 && (
          <select
            value={tagFilter}
            onChange={e => setTagFilter(e.target.value)}
            className="px-2.5 py-2 rounded-lg text-xs outline-none"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          >
            <option value="">All tags</option>
            {allTags.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        )}
      </div>

      {/* Tag chips */}
      {tagFilter && (
        <div className="flex items-center gap-1.5 mb-3">
          <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Filtered by:</span>
          <span className="flex items-center gap-1 px-2 py-0.5 rounded-full text-xs" style={{ background: 'rgba(59,130,246,0.12)', color: '#3b82f6' }}>
            {tagFilter}
            <X size={10} className="cursor-pointer" onClick={() => setTagFilter('')} />
          </span>
        </div>
      )}

      {/* Grid */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 size={20} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-12">
          <FileText size={32} className="mx-auto mb-2" style={{ color: 'var(--neo-text-dim)' }} />
          <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
            {search || tagFilter ? 'No schemas match your filters.' : 'No schemas yet.'}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {filtered.map(s => (
            <SchemaCard key={`${s.source}-${s.name}`} schema={s} onSelect={handleSelect} />
          ))}
        </div>
      )}
    </div>
  );
}

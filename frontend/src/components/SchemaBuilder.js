/**
 * SchemaBuilder — Visual graph schema editor with 3 modes (Builder / YAML / Upload).
 *
 * Builder mode has:
 *  - Node type cards with color badges, collapsible property tables
 *  - Relationship cards with visual From → To and optional properties
 *  - Inline "add" forms with focused inputs
 *  - Mini schema preview showing node/edge summary
 *
 * Props:
 *   initialYaml, onSave, onDirtyChange, readOnly, showSaveBar, tags, onTagsChange
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Plus, Trash2, ChevronDown, ChevronRight, Upload, Download,
  AlertTriangle, XCircle, CheckCircle, X, ArrowRight,
  Circle, GitBranch, Pencil, Copy,
} from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../lib/api';

const PROP_TYPES = ['string', 'number', 'integer', 'boolean', 'date', 'list'];

const MODES = [
  { id: 'builder', label: 'Builder' },
  { id: 'yaml', label: 'YAML' },
  { id: 'upload', label: 'Upload' },
];

const NODE_COLORS = [
  '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
  '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#6366f1',
  '#14b8a6', '#e11d48', '#0ea5e9', '#a855f7', '#d946ef',
];

// Quick-add templates for common node types
const NODE_TEMPLATES = [
  { label: 'Person', props: ['name', 'role', 'email'] },
  { label: 'Organization', props: ['name', 'type', 'industry'] },
  { label: 'Document', props: ['title', 'source', 'content'] },
  { label: 'Concept', props: ['name', 'description', 'category'] },
  { label: 'Event', props: ['name', 'date', 'location'] },
  { label: 'Technology', props: ['name', 'version', 'type'] },
];

// ---------------------------------------------------------------------------
// YAML ↔ state conversion
// ---------------------------------------------------------------------------

function stateToYaml(state) {
  const lines = [];
  if (state.name) lines.push(`name: ${state.name}`);
  if (state.version) lines.push(`version: "${state.version}"`);
  lines.push('');
  lines.push('node_types:');
  for (const nt of state.node_types) {
    if (!nt.label) continue;
    lines.push(`  ${nt.label}:`);
    const fieldNames = nt.properties.map(p => p.name).filter(Boolean);
    if (fieldNames.length) lines.push(`    fields: [${fieldNames.join(', ')}]`);
    const required = nt.properties.filter(p => p.required).map(p => p.name).filter(Boolean);
    if (required.length) lines.push(`    required: [${required.join(', ')}]`);
    if (nt.description) lines.push(`    description: "${nt.description}"`);
  }
  if (state.relationship_types.length) {
    lines.push('');
    lines.push('edge_types:');
    for (const rt of state.relationship_types) {
      if (!rt.type) continue;
      lines.push(`  ${rt.type}:`);
      if (rt.from) lines.push(`    source: ${rt.from}`);
      if (rt.to) lines.push(`    target: ${rt.to}`);
      const fieldNames = (rt.properties || []).map(p => p.name).filter(Boolean);
      if (fieldNames.length) lines.push(`    fields: [${fieldNames.join(', ')}]`);
    }
  }
  return lines.join('\n') + '\n';
}

function yamlToState(yamlStr) {
  try {
    const state = { name: '', version: '1.0', node_types: [], relationship_types: [] };
    const lines = yamlStr.split('\n');
    let section = null;
    let currentItem = null;

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;

      if (/^name:\s*(.+)/.test(trimmed)) {
        state.name = trimmed.match(/^name:\s*(.+)/)[1].replace(/^["']|["']$/g, '');
        continue;
      }
      if (/^version:\s*(.+)/.test(trimmed)) {
        state.version = trimmed.match(/^version:\s*(.+)/)[1].replace(/^["']|["']$/g, '');
        continue;
      }
      if (trimmed === 'node_types:') { section = 'node_types'; currentItem = null; continue; }
      if (trimmed === 'edge_types:') { section = 'edge_types'; currentItem = null; continue; }
      if (trimmed === 'fact_types:') { section = 'fact_types'; currentItem = null; continue; }

      if (section === 'node_types') {
        const ntMatch = line.match(/^  (\w+):$/);
        if (ntMatch) {
          currentItem = { label: ntMatch[1], description: '', properties: [] };
          state.node_types.push(currentItem);
          continue;
        }
        if (currentItem) {
          const fieldsMatch = trimmed.match(/^fields:\s*\[(.+)\]/);
          if (fieldsMatch) {
            const names = fieldsMatch[1].split(',').map(s => s.trim());
            const existing = currentItem.properties.map(p => p.name);
            for (const n of names) {
              if (!existing.includes(n)) {
                currentItem.properties.push({ name: n, type: 'string', required: false, unique: false });
              }
            }
            continue;
          }
          const reqMatch = trimmed.match(/^required:\s*\[(.+)\]/);
          if (reqMatch) {
            const reqNames = reqMatch[1].split(',').map(s => s.trim());
            for (const p of currentItem.properties) {
              if (reqNames.includes(p.name)) p.required = true;
            }
            continue;
          }
          const descMatch = trimmed.match(/^description:\s*"?(.+?)"?$/);
          if (descMatch) { currentItem.description = descMatch[1]; continue; }
        }
      }

      if (section === 'edge_types') {
        const etMatch = line.match(/^  (\w+):$/);
        if (etMatch) {
          currentItem = { type: etMatch[1], from: '', to: '', properties: [] };
          state.relationship_types.push(currentItem);
          continue;
        }
        if (currentItem) {
          const srcMatch = trimmed.match(/^(?:source|from):\s*(\w+)/);
          if (srcMatch) { currentItem.from = srcMatch[1]; continue; }
          const tgtMatch = trimmed.match(/^(?:target|to):\s*(\w+)/);
          if (tgtMatch) { currentItem.to = tgtMatch[1]; continue; }
          const fieldsMatch = trimmed.match(/^fields:\s*\[(.+)\]/);
          if (fieldsMatch) {
            currentItem.properties = fieldsMatch[1].split(',').map(s => ({
              name: s.trim(), type: 'string', required: false, unique: false,
            }));
            continue;
          }
        }
      }
    }
    return state;
  } catch {
    return null;
  }
}

function emptyState() {
  return { name: '', version: '1.0', node_types: [], relationship_types: [] };
}

function getNodeColor(index) {
  return NODE_COLORS[index % NODE_COLORS.length];
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function PropertyRow({ prop, onChange, onRemove, readOnly }) {
  return (
    <tr>
      <td className="py-1 pr-2">
        <input
          value={prop.name}
          onChange={e => onChange({ ...prop, name: e.target.value })}
          placeholder="property_name"
          disabled={readOnly}
          className="w-full px-2 py-1 rounded text-xs outline-none"
          style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
        />
      </td>
      <td className="py-1 pr-2">
        <select
          value={prop.type}
          onChange={e => onChange({ ...prop, type: e.target.value })}
          disabled={readOnly}
          className="w-full px-1.5 py-1 rounded text-xs outline-none"
          style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
        >
          {PROP_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
      </td>
      <td className="py-1 pr-1 text-center">
        <input type="checkbox" checked={prop.required}
          onChange={e => onChange({ ...prop, required: e.target.checked })} disabled={readOnly} />
      </td>
      <td className="py-1 pr-1 text-center">
        <input type="checkbox" checked={prop.unique}
          onChange={e => onChange({ ...prop, unique: e.target.checked })} disabled={readOnly} />
      </td>
      <td className="py-1">
        {!readOnly && (
          <button onClick={onRemove} className="p-0.5 rounded hover:bg-red-500/10" style={{ color: '#ef4444' }}>
            <X size={11} />
          </button>
        )}
      </td>
    </tr>
  );
}

function NodeTypeCard({ nt, index, onChange, onRemove, readOnly, onDuplicate }) {
  const [open, setOpen] = useState(true);
  const color = getNodeColor(index);

  const updateProp = (pi, prop) => {
    const props = [...nt.properties];
    props[pi] = prop;
    onChange({ ...nt, properties: props });
  };

  const addProp = () => {
    onChange({ ...nt, properties: [...nt.properties, { name: '', type: 'string', required: false, unique: false }] });
  };

  const removeProp = (pi) => {
    onChange({ ...nt, properties: nt.properties.filter((_, i) => i !== pi) });
  };

  return (
    <div className="rounded-xl overflow-hidden" style={{ border: '1px solid var(--neo-border)' }}>
      {/* Header bar with color accent */}
      <div
        className="flex items-center gap-2.5 px-3 py-2.5 cursor-pointer"
        style={{ background: 'var(--neo-surface)', borderLeft: `3px solid ${color}` }}
        onClick={() => setOpen(!open)}
      >
        <div className="w-6 h-6 rounded-md flex items-center justify-center flex-shrink-0"
          style={{ background: `${color}20` }}>
          <Circle size={10} fill={color} stroke="none" />
        </div>
        <input
          value={nt.label}
          onChange={e => { e.stopPropagation(); onChange({ ...nt, label: e.target.value }); }}
          onClick={e => e.stopPropagation()}
          placeholder="NodeTypeName"
          disabled={readOnly}
          className="flex-1 bg-transparent text-sm font-semibold outline-none"
          style={{ color: 'var(--neo-text)' }}
        />
        <span className="text-xs px-1.5 py-0.5 rounded-full" style={{ background: `${color}15`, color }}>
          {nt.properties.length} {nt.properties.length === 1 ? 'prop' : 'props'}
        </span>
        {!readOnly && (
          <>
            <button onClick={e => { e.stopPropagation(); onDuplicate(); }}
              className="p-1 rounded hover:bg-blue-500/10" style={{ color: 'var(--neo-text-dim)' }} title="Duplicate">
              <Copy size={12} />
            </button>
            <button onClick={e => { e.stopPropagation(); onRemove(); }}
              className="p-1 rounded hover:bg-red-500/10" style={{ color: '#ef4444' }} title="Remove">
              <Trash2 size={12} />
            </button>
          </>
        )}
        {open ? <ChevronDown size={14} style={{ color: 'var(--neo-text-dim)' }} /> : <ChevronRight size={14} style={{ color: 'var(--neo-text-dim)' }} />}
      </div>

      {/* Body */}
      {open && (
        <div className="px-4 py-3 space-y-3" style={{ background: 'var(--neo-bg)' }}>
          {/* Description */}
          <input
            value={nt.description || ''}
            onChange={e => onChange({ ...nt, description: e.target.value })}
            placeholder="Description — what does this node type represent?"
            disabled={readOnly}
            className="w-full px-2.5 py-1.5 rounded text-xs outline-none"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
          />

          {/* Properties table */}
          {nt.properties.length > 0 && (
            <table className="w-full text-xs" style={{ color: 'var(--neo-text)' }}>
              <thead>
                <tr style={{ color: 'var(--neo-text-dim)' }}>
                  <th className="text-left font-medium py-1 pr-2">Name</th>
                  <th className="text-left font-medium py-1 pr-2" style={{ width: 100 }}>Type</th>
                  <th className="text-center font-medium py-1 pr-1" style={{ width: 32 }}>Req</th>
                  <th className="text-center font-medium py-1 pr-1" style={{ width: 32 }}>Uniq</th>
                  <th style={{ width: 24 }}></th>
                </tr>
              </thead>
              <tbody>
                {nt.properties.map((p, pi) => (
                  <PropertyRow key={pi} prop={p} onChange={v => updateProp(pi, v)}
                    onRemove={() => removeProp(pi)} readOnly={readOnly} />
                ))}
              </tbody>
            </table>
          )}

          {/* Add property */}
          {!readOnly && (
            <button onClick={addProp}
              className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-md transition hover:bg-blue-500/5"
              style={{ color: 'var(--neo-blue)', border: '1px dashed var(--neo-border)' }}>
              <Plus size={12} /> Add Property
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function RelationshipCard({ rt, index, nodeLabels, nodeColors, onChange, onRemove, readOnly }) {
  const [showProps, setShowProps] = useState(false);
  const fromColor = nodeColors[rt.from] || 'var(--neo-text-dim)';
  const toColor = nodeColors[rt.to] || 'var(--neo-text-dim)';

  const addProp = () => {
    onChange({ ...rt, properties: [...(rt.properties || []), { name: '', type: 'string', required: false, unique: false }] });
    setShowProps(true);
  };

  const updateProp = (pi, prop) => {
    const props = [...(rt.properties || [])];
    props[pi] = prop;
    onChange({ ...rt, properties: props });
  };

  const removeProp = (pi) => {
    onChange({ ...rt, properties: (rt.properties || []).filter((_, i) => i !== pi) });
  };

  return (
    <div className="rounded-xl overflow-hidden" style={{ border: '1px solid var(--neo-border)' }}>
      <div className="px-3 py-2.5 flex flex-col gap-2.5" style={{ background: 'var(--neo-surface)' }}>
        {/* Relationship type name */}
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-md flex items-center justify-center flex-shrink-0"
            style={{ background: 'rgba(249,115,22,0.12)' }}>
            <GitBranch size={11} style={{ color: '#f97316' }} />
          </div>
          <input
            value={rt.type}
            onChange={e => onChange({ ...rt, type: e.target.value.toUpperCase().replace(/\s+/g, '_') })}
            placeholder="RELATIONSHIP_NAME"
            disabled={readOnly}
            className="flex-1 bg-transparent text-sm font-semibold font-mono outline-none"
            style={{ color: 'var(--neo-text)', letterSpacing: '0.02em' }}
          />
          {!readOnly && (
            <button onClick={onRemove} className="p-1 rounded hover:bg-red-500/10" style={{ color: '#ef4444' }}>
              <Trash2 size={12} />
            </button>
          )}
        </div>

        {/* From → To visual */}
        <div className="flex items-center gap-2">
          <select
            value={rt.from}
            onChange={e => onChange({ ...rt, from: e.target.value })}
            disabled={readOnly}
            className="flex-1 px-2.5 py-1.5 rounded-lg text-xs font-medium outline-none"
            style={{
              background: rt.from ? `${fromColor}10` : 'var(--neo-bg)',
              border: `1px solid ${rt.from ? fromColor + '40' : 'var(--neo-border)'}`,
              color: rt.from ? fromColor : 'var(--neo-text-muted)',
            }}
          >
            <option value="">Source node...</option>
            {nodeLabels.map(l => <option key={l} value={l}>{l}</option>)}
          </select>

          <div className="flex items-center px-1" style={{ color: '#f97316' }}>
            <ArrowRight size={16} />
          </div>

          <select
            value={rt.to}
            onChange={e => onChange({ ...rt, to: e.target.value })}
            disabled={readOnly}
            className="flex-1 px-2.5 py-1.5 rounded-lg text-xs font-medium outline-none"
            style={{
              background: rt.to ? `${toColor}10` : 'var(--neo-bg)',
              border: `1px solid ${rt.to ? toColor + '40' : 'var(--neo-border)'}`,
              color: rt.to ? toColor : 'var(--neo-text-muted)',
            }}
          >
            <option value="">Target node...</option>
            {nodeLabels.map(l => <option key={l} value={l}>{l}</option>)}
          </select>
        </div>

        {/* Edge properties toggle */}
        <div className="flex items-center gap-2">
          {!readOnly && (
            <button onClick={addProp}
              className="text-xs px-2 py-0.5 rounded transition"
              style={{ color: 'var(--neo-text-dim)', border: '1px dashed var(--neo-border)' }}>
              <Plus size={10} className="inline mr-1" />edge property
            </button>
          )}
          {(rt.properties || []).length > 0 && (
            <button onClick={() => setShowProps(!showProps)}
              className="text-xs" style={{ color: 'var(--neo-text-dim)' }}>
              {showProps ? 'hide' : 'show'} {(rt.properties || []).length} props
            </button>
          )}
        </div>
      </div>

      {/* Edge properties */}
      {showProps && (rt.properties || []).length > 0 && (
        <div className="px-4 py-2" style={{ background: 'var(--neo-bg)', borderTop: '1px solid var(--neo-border)' }}>
          <table className="w-full text-xs" style={{ color: 'var(--neo-text)' }}>
            <tbody>
              {(rt.properties || []).map((p, pi) => (
                <PropertyRow key={pi} prop={p} onChange={v => updateProp(pi, v)}
                  onRemove={() => removeProp(pi)} readOnly={readOnly} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// Mini schema summary preview
function SchemaPreview({ state }) {
  const nodeLabels = state.node_types.filter(n => n.label).map(n => n.label);
  if (nodeLabels.length === 0) return null;

  return (
    <div className="rounded-lg px-3 py-2.5" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
      <div className="flex flex-wrap items-center gap-1.5">
        {state.node_types.filter(n => n.label).map((nt, i) => (
          <span key={i} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium"
            style={{ background: `${getNodeColor(i)}15`, color: getNodeColor(i) }}>
            <Circle size={6} fill={getNodeColor(i)} stroke="none" />
            {nt.label}
            <span style={{ opacity: 0.6 }}>({nt.properties.length})</span>
          </span>
        ))}
        {state.relationship_types.filter(r => r.type).map((rt, i) => (
          <span key={`r${i}`} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs"
            style={{ background: 'rgba(249,115,22,0.08)', color: '#f97316' }}>
            {rt.from || '?'} <ArrowRight size={10} /> {rt.to || '?'}
            <span className="font-mono" style={{ opacity: 0.7 }}>{rt.type}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export default function SchemaBuilder({
  initialYaml = '',
  onSave,
  onDirtyChange,
  readOnly = false,
  showSaveBar = true,
  tags: initialTags = [],
  onTagsChange,
}) {
  const [mode, setMode] = useState('builder');
  const [state, setState] = useState(() => {
    if (initialYaml) {
      const parsed = yamlToState(initialYaml);
      return parsed || emptyState();
    }
    return emptyState();
  });
  const [yamlText, setYamlText] = useState(initialYaml || '');
  const [dirty, setDirty] = useState(false);
  const [validation, setValidation] = useState(null);
  const [tags, setTags] = useState(initialTags);
  const [tagInput, setTagInput] = useState('');
  const [showTemplates, setShowTemplates] = useState(false);
  const fileRef = useRef(null);

  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);

  const switchMode = useCallback((newMode) => {
    if (newMode === mode) return;
    if (mode === 'builder' && newMode === 'yaml') {
      setYamlText(stateToYaml(state));
    } else if (mode === 'yaml' && newMode === 'builder') {
      const parsed = yamlToState(yamlText);
      if (!parsed) { toast.error('Invalid YAML — cannot switch to Builder mode'); return; }
      setState(parsed);
    }
    setMode(newMode);
  }, [mode, state, yamlText]);

  const updateState = (newState) => {
    setState(newState);
    setDirty(true);
    setValidation(null);
  };

  const updateYaml = (text) => {
    setYamlText(text);
    setDirty(true);
    setValidation(null);
  };

  const getCurrentYaml = () => mode === 'yaml' ? yamlText : stateToYaml(state);

  const handleValidate = async () => {
    try {
      const res = await api.post('/dashboard/contexts/schemas/validate', { yaml: getCurrentYaml() });
      setValidation(res.data);
      if (res.data.valid && !res.data.warnings?.length) toast.success('Schema is valid');
    } catch { toast.error('Validation failed'); }
  };

  const handleSave = () => {
    const yaml = getCurrentYaml();
    const currentState = mode === 'builder' ? state : yamlToState(yaml) || state;
    onSave?.(yaml, currentState, tags);
    setDirty(false);
  };

  const handleDownload = () => {
    const yaml = getCurrentYaml();
    const blob = new Blob([yaml], { type: 'text/yaml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${state.name || 'schema'}.yaml`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleUpload = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = ev.target.result;
      const parsed = yamlToState(text);
      if (parsed) {
        setState(parsed);
        setYamlText(text);
        setDirty(true);
        setValidation(null);
        setMode('builder');
        toast.success(`Loaded ${file.name}`);
      } else {
        setYamlText(text);
        setDirty(true);
        setMode('yaml');
        toast.error('Could not parse structure — showing as YAML');
      }
    };
    reader.readAsText(file);
    e.target.value = '';
  };

  // Tags
  const addTag = () => {
    const t = tagInput.trim().toLowerCase();
    if (t && !tags.includes(t)) {
      const next = [...tags, t];
      setTags(next);
      onTagsChange?.(next);
      setDirty(true);
    }
    setTagInput('');
  };

  const removeTag = (t) => {
    const next = tags.filter(x => x !== t);
    setTags(next);
    onTagsChange?.(next);
    setDirty(true);
  };

  // Node type helpers
  const nodeLabels = state.node_types.map(nt => nt.label).filter(Boolean);
  const nodeColorMap = {};
  state.node_types.forEach((nt, i) => { if (nt.label) nodeColorMap[nt.label] = getNodeColor(i); });

  const addNodeType = (template) => {
    const nt = template
      ? {
          label: template.label,
          description: '',
          properties: template.props.map((name, i) => ({
            name, type: 'string', required: i === 0, unique: false,
          })),
        }
      : { label: '', description: '', properties: [{ name: 'name', type: 'string', required: true, unique: false }] };
    updateState({ ...state, node_types: [...state.node_types, nt] });
    setShowTemplates(false);
  };

  const duplicateNodeType = (i) => {
    const orig = state.node_types[i];
    const dup = {
      ...orig,
      label: orig.label + '_copy',
      properties: orig.properties.map(p => ({ ...p })),
    };
    const nts = [...state.node_types];
    nts.splice(i + 1, 0, dup);
    updateState({ ...state, node_types: nts });
  };

  const updateNodeType = (i, nt) => {
    const nts = [...state.node_types];
    nts[i] = nt;
    updateState({ ...state, node_types: nts });
  };

  const removeNodeType = (i) => {
    updateState({ ...state, node_types: state.node_types.filter((_, idx) => idx !== i) });
  };

  const addRelationship = () => {
    updateState({
      ...state,
      relationship_types: [...state.relationship_types, { type: '', from: '', to: '', properties: [] }],
    });
  };

  const updateRelationship = (i, rt) => {
    const rts = [...state.relationship_types];
    rts[i] = rt;
    updateState({ ...state, relationship_types: rts });
  };

  const removeRelationship = (i) => {
    updateState({ ...state, relationship_types: state.relationship_types.filter((_, idx) => idx !== i) });
  };

  // Existing templates that are already in the schema
  const existingLabels = new Set(state.node_types.map(n => n.label));
  const availableTemplates = NODE_TEMPLATES.filter(t => !existingLabels.has(t.label));

  return (
    <div className="space-y-3">
      {/* Mode tabs */}
      <div className="flex items-center gap-1 p-0.5 rounded-lg" style={{ background: 'var(--neo-surface)' }}>
        {MODES.map(m => (
          <button
            key={m.id}
            onClick={() => switchMode(m.id)}
            className="flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition"
            style={{
              background: mode === m.id ? 'var(--neo-blue)' : 'transparent',
              color: mode === m.id ? '#fff' : 'var(--neo-text-muted)',
            }}
          >
            {m.label}
          </button>
        ))}
      </div>

      {/* Builder mode */}
      {mode === 'builder' && (
        <div className="space-y-5">
          {/* Schema name + version */}
          <div className="flex gap-2">
            <div className="flex-1">
              <label className="text-xs font-medium mb-1 block" style={{ color: 'var(--neo-text-muted)' }}>Schema Name</label>
              <input
                value={state.name}
                onChange={e => updateState({ ...state, name: e.target.value })}
                placeholder="my_schema"
                disabled={readOnly}
                className="w-full px-2.5 py-2 rounded-lg text-sm outline-none"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
              />
            </div>
            <div style={{ width: 90 }}>
              <label className="text-xs font-medium mb-1 block" style={{ color: 'var(--neo-text-muted)' }}>Version</label>
              <input
                value={state.version}
                onChange={e => updateState({ ...state, version: e.target.value })}
                disabled={readOnly}
                className="w-full px-2.5 py-2 rounded-lg text-sm outline-none"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
              />
            </div>
          </div>

          {/* Mini preview */}
          <SchemaPreview state={state} />

          {/* ── Node Types ── */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold flex items-center gap-2" style={{ color: 'var(--neo-text)' }}>
                <Circle size={10} fill="#3b82f6" stroke="none" />
                Node Types
                <span className="text-xs font-normal px-1.5 py-0.5 rounded-full"
                  style={{ background: 'rgba(59,130,246,0.1)', color: '#3b82f6' }}>
                  {state.node_types.length}
                </span>
              </h3>
              {!readOnly && (
                <div className="flex items-center gap-1.5">
                  {availableTemplates.length > 0 && (
                    <button onClick={() => setShowTemplates(!showTemplates)}
                      className="flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-lg transition"
                      style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}>
                      Quick Add
                    </button>
                  )}
                  <button onClick={() => addNodeType()}
                    className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg font-medium transition"
                    style={{ background: 'var(--neo-blue)', color: '#fff' }}>
                    <Plus size={12} /> New Node Type
                  </button>
                </div>
              )}
            </div>

            {/* Quick-add template chips */}
            {showTemplates && !readOnly && (
              <div className="flex flex-wrap gap-1.5 mb-3 p-2.5 rounded-lg"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
                <span className="text-xs mr-1" style={{ color: 'var(--neo-text-dim)' }}>Templates:</span>
                {availableTemplates.map(t => (
                  <button key={t.label} onClick={() => addNodeType(t)}
                    className="px-2.5 py-1 rounded-full text-xs font-medium transition hover:shadow-sm"
                    style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}>
                    {t.label}
                    <span className="ml-1" style={{ color: 'var(--neo-text-dim)' }}>({t.props.length})</span>
                  </button>
                ))}
              </div>
            )}

            {/* Node type cards */}
            <div className="space-y-2.5">
              {state.node_types.map((nt, i) => (
                <NodeTypeCard
                  key={i} nt={nt} index={i}
                  onChange={v => updateNodeType(i, v)}
                  onRemove={() => removeNodeType(i)}
                  onDuplicate={() => duplicateNodeType(i)}
                  readOnly={readOnly}
                />
              ))}
            </div>

            {/* Empty state */}
            {state.node_types.length === 0 && !readOnly && (
              <div className="text-center py-8 rounded-xl"
                style={{ border: '2px dashed var(--neo-border)', background: 'var(--neo-surface)' }}>
                <Circle size={28} className="mx-auto mb-2" style={{ color: 'var(--neo-text-dim)' }} />
                <p className="text-sm font-medium mb-1" style={{ color: 'var(--neo-text-muted)' }}>
                  No node types yet
                </p>
                <p className="text-xs mb-3" style={{ color: 'var(--neo-text-dim)' }}>
                  Define what types of entities the LLM should extract from your documents.
                </p>
                <div className="flex items-center justify-center gap-2">
                  <button onClick={() => addNodeType()}
                    className="flex items-center gap-1.5 text-xs px-3 py-2 rounded-lg font-medium"
                    style={{ background: 'var(--neo-blue)', color: '#fff' }}>
                    <Plus size={12} /> Create from scratch
                  </button>
                  {availableTemplates.length > 0 && (
                    <button onClick={() => setShowTemplates(true)}
                      className="flex items-center gap-1.5 text-xs px-3 py-2 rounded-lg font-medium"
                      style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}>
                      Use template
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* ── Relationships ── */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold flex items-center gap-2" style={{ color: 'var(--neo-text)' }}>
                <GitBranch size={12} style={{ color: '#f97316' }} />
                Relationships
                <span className="text-xs font-normal px-1.5 py-0.5 rounded-full"
                  style={{ background: 'rgba(249,115,22,0.1)', color: '#f97316' }}>
                  {state.relationship_types.length}
                </span>
              </h3>
              {!readOnly && nodeLabels.length >= 2 && (
                <button onClick={addRelationship}
                  className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg font-medium transition"
                  style={{ background: '#f97316', color: '#fff' }}>
                  <Plus size={12} /> New Relationship
                </button>
              )}
            </div>

            <div className="space-y-2.5">
              {state.relationship_types.map((rt, i) => (
                <RelationshipCard
                  key={i} rt={rt} index={i}
                  nodeLabels={nodeLabels}
                  nodeColors={nodeColorMap}
                  onChange={v => updateRelationship(i, v)}
                  onRemove={() => removeRelationship(i)}
                  readOnly={readOnly}
                />
              ))}
            </div>

            {/* Empty state */}
            {state.relationship_types.length === 0 && (
              <div className="text-center py-6 rounded-xl"
                style={{ border: '2px dashed var(--neo-border)', background: 'var(--neo-surface)' }}>
                <GitBranch size={24} className="mx-auto mb-2" style={{ color: 'var(--neo-text-dim)' }} />
                {nodeLabels.length < 2 ? (
                  <p className="text-xs" style={{ color: 'var(--neo-text-dim)' }}>
                    Add at least 2 node types to define relationships between them.
                  </p>
                ) : (
                  <>
                    <p className="text-xs mb-2" style={{ color: 'var(--neo-text-dim)' }}>
                      Define how your node types connect to each other.
                    </p>
                    {!readOnly && (
                      <button onClick={addRelationship}
                        className="flex items-center gap-1.5 text-xs px-3 py-2 rounded-lg font-medium mx-auto"
                        style={{ background: '#f97316', color: '#fff' }}>
                        <Plus size={12} /> Add Relationship
                      </button>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* YAML mode */}
      {mode === 'yaml' && (
        <textarea
          value={yamlText}
          onChange={e => updateYaml(e.target.value)}
          rows={20}
          spellCheck={false}
          disabled={readOnly}
          className="w-full px-3 py-2 rounded-lg text-xs outline-none resize-y"
          style={{
            background: 'var(--neo-surface)',
            border: `1px solid ${validation && !validation.valid ? '#ef4444' : 'var(--neo-border)'}`,
            color: 'var(--neo-text)',
            fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
            lineHeight: '1.5',
            tabSize: 2,
          }}
        />
      )}

      {/* Upload mode */}
      {mode === 'upload' && (
        <div
          onClick={() => !readOnly && fileRef.current?.click()}
          className="flex flex-col items-center justify-center gap-2 py-10 rounded-lg cursor-pointer transition"
          style={{ border: '2px dashed var(--neo-border)', background: 'var(--neo-surface)' }}
        >
          <Upload size={24} style={{ color: 'var(--neo-text-dim)' }} />
          <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
            Click to upload a <strong>.yaml</strong> or <strong>.yml</strong> file
          </span>
          <input ref={fileRef} type="file" accept=".yaml,.yml" className="hidden" onChange={handleUpload} />
        </div>
      )}

      {/* Validation feedback */}
      {validation && (
        <div className="space-y-1">
          {validation.errors?.map((err, i) => (
            <div key={`e${i}`} className="flex items-start gap-1.5 text-xs" style={{ color: '#ef4444' }}>
              <XCircle size={12} className="mt-0.5 shrink-0" /> {err}
            </div>
          ))}
          {validation.warnings?.map((w, i) => (
            <div key={`w${i}`} className="flex items-start gap-1.5 text-xs" style={{ color: '#f59e0b' }}>
              <AlertTriangle size={12} className="mt-0.5 shrink-0" /> {w}
            </div>
          ))}
          {validation.valid && !validation.errors?.length && !validation.warnings?.length && (
            <div className="flex items-center gap-1.5 text-xs" style={{ color: '#22c55e' }}>
              <CheckCircle size={12} /> Schema is valid
            </div>
          )}
        </div>
      )}

      {/* Tags */}
      {showSaveBar && (
        <div>
          <label className="text-xs font-medium mb-1 block" style={{ color: 'var(--neo-text-muted)' }}>Tags</label>
          <div className="flex flex-wrap items-center gap-1.5">
            {tags.map(t => (
              <span key={t} className="flex items-center gap-1 px-2 py-0.5 rounded-full text-xs"
                style={{ background: 'rgba(59,130,246,0.12)', color: '#3b82f6' }}>
                {t}
                {!readOnly && <X size={10} className="cursor-pointer" onClick={() => removeTag(t)} />}
              </span>
            ))}
            {!readOnly && (
              <input
                value={tagInput}
                onChange={e => setTagInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addTag(); } }}
                placeholder="add tag..."
                className="px-2 py-0.5 rounded text-xs outline-none"
                style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)', width: 90 }}
              />
            )}
          </div>
        </div>
      )}

      {/* Save bar */}
      {showSaveBar && !readOnly && (
        <div className="flex items-center gap-1.5 pt-1">
          <button
            onClick={handleSave}
            disabled={!dirty}
            className="px-3 py-1.5 rounded text-xs font-medium transition disabled:opacity-40"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            Save
          </button>
          <button
            onClick={handleValidate}
            className="px-3 py-1.5 rounded text-xs transition"
            style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
          >
            Validate
          </button>
          <button
            onClick={handleDownload}
            className="p-1.5 rounded transition"
            style={{ color: 'var(--neo-text-muted)' }}
            title="Download YAML"
          >
            <Download size={14} />
          </button>
        </div>
      )}
    </div>
  );
}

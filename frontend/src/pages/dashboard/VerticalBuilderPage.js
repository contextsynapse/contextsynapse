import React, { useState } from 'react';
import { Plus, Trash2, Download, Eye, ChevronRight, ChevronLeft, Package, Database, Radio, Globe, Code2 } from 'lucide-react';

const STEPS = [
  { id: 'basics', label: 'Basics', icon: Package },
  { id: 'schema', label: 'Schema', icon: Database },
  { id: 'sensors', label: 'Sensors', icon: Radio },
  { id: 'routes', label: 'API Routes', icon: Globe },
  { id: 'preview', label: 'Generate', icon: Code2 },
];

const PROPERTY_TYPES = ['string', 'int', 'float', 'bool', 'datetime', 'list'];
const SOURCE_TYPES = ['api', 'rss', 'database', 'file', 'webhook'];

export default function VerticalBuilderPage() {
  const [step, setStep] = useState(0);
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState(null);

  const [form, setForm] = useState({
    name: '',
    description: '',
    author: 'ContextSynapse Developer',
    node_types: [],
    edge_types: [],
    sensors: [],
    routes: [],
  });

  const API = `${window.location.origin}`;

  const updateForm = (field, value) => setForm(prev => ({ ...prev, [field]: value }));

  // ── Node type helpers ──
  const addNodeType = () => updateForm('node_types', [...form.node_types, { name: '', description: '', properties: [] }]);
  const removeNodeType = (i) => updateForm('node_types', form.node_types.filter((_, j) => j !== i));
  const updateNodeType = (i, field, val) => {
    const updated = [...form.node_types];
    updated[i] = { ...updated[i], [field]: val };
    updateForm('node_types', updated);
  };
  const addProperty = (ntIdx) => {
    const updated = [...form.node_types];
    updated[ntIdx].properties = [...updated[ntIdx].properties, { name: '', type: 'string', required: false }];
    updateForm('node_types', updated);
  };
  const removeProperty = (ntIdx, pIdx) => {
    const updated = [...form.node_types];
    updated[ntIdx].properties = updated[ntIdx].properties.filter((_, j) => j !== pIdx);
    updateForm('node_types', updated);
  };
  const updateProperty = (ntIdx, pIdx, field, val) => {
    const updated = [...form.node_types];
    updated[ntIdx].properties[pIdx] = { ...updated[ntIdx].properties[pIdx], [field]: val };
    updateForm('node_types', updated);
  };

  // ── Edge type helpers ──
  const addEdgeType = () => updateForm('edge_types', [...form.edge_types, { name: '', source: '', target: '', description: '' }]);
  const removeEdgeType = (i) => updateForm('edge_types', form.edge_types.filter((_, j) => j !== i));
  const updateEdgeType = (i, field, val) => {
    const updated = [...form.edge_types];
    updated[i] = { ...updated[i], [field]: val };
    updateForm('edge_types', updated);
  };

  // ── Sensor helpers ──
  const addSensor = () => updateForm('sensors', [...form.sensors, { name: '', interval_seconds: 300, source_type: 'api', source_url: '', description: '' }]);
  const removeSensor = (i) => updateForm('sensors', form.sensors.filter((_, j) => j !== i));
  const updateSensor = (i, field, val) => {
    const updated = [...form.sensors];
    updated[i] = { ...updated[i], [field]: val };
    updateForm('sensors', updated);
  };

  // ── Route helpers ──
  const addRoute = () => updateForm('routes', [...form.routes, { method: 'GET', path: '/', description: '' }]);
  const removeRoute = (i) => updateForm('routes', form.routes.filter((_, j) => j !== i));
  const updateRoute = (i, field, val) => {
    const updated = [...form.routes];
    updated[i] = { ...updated[i], [field]: val };
    updateForm('routes', updated);
  };

  // ── API calls ──
  const doPreview = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/vertical-builder/preview`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      setPreview(await res.json());
    } catch (e) { console.error(e); }
    setLoading(false);
  };

  const doGenerate = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/vertical-builder/generate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `contextsynapse-${form.name.toLowerCase().replace(/[^a-z0-9]/g, '_')}.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) { console.error(e); }
    setLoading(false);
  };

  const inputStyle = {
    background: 'var(--neo-surface)',
    border: '1px solid var(--neo-border)',
    color: 'var(--neo-text)',
    borderRadius: 8, padding: '8px 12px', width: '100%', fontSize: 14,
  };
  const cardStyle = {
    background: 'var(--neo-surface-light)',
    border: '1px solid var(--neo-border)',
    borderRadius: 12, padding: 20, marginBottom: 16,
  };
  const btnPrimary = {
    background: 'var(--neo-blue)', color: '#fff', border: 'none',
    borderRadius: 8, padding: '10px 20px', cursor: 'pointer', fontSize: 14, fontWeight: 600,
  };
  const btnSecondary = {
    background: 'transparent', color: 'var(--neo-text-muted)',
    border: '1px solid var(--neo-border)',
    borderRadius: 8, padding: '8px 16px', cursor: 'pointer', fontSize: 13,
  };

  const nodeTypeNames = form.node_types.map(nt => nt.name).filter(Boolean);

  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <h1 style={{ color: 'var(--neo-text)', fontSize: 24, marginBottom: 8 }}>Vertical Builder</h1>
      <p style={{ color: 'var(--neo-text-muted)', marginBottom: 32 }}>
        Create a domain-specific plugin for ContextSynapse. Define your schemas, data feeds, and API — download as a ready-to-install package.
      </p>

      {/* Step indicator */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 32 }}>
        {STEPS.map((s, i) => (
          <button key={s.id} onClick={() => setStep(i)}
            style={{
              flex: 1, padding: '10px 0', border: 'none', cursor: 'pointer',
              borderRadius: 8, fontSize: 13, fontWeight: i === step ? 700 : 400,
              background: i === step ? 'var(--neo-blue)' : i < step ? 'rgba(76,142,218,0.2)' : 'var(--neo-surface)',
              color: i === step ? '#fff' : 'var(--neo-text-muted)',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
            }}>
            <s.icon size={14} /> {s.label}
          </button>
        ))}
      </div>

      {/* Step 1: Basics */}
      {step === 0 && (
        <div style={cardStyle}>
          <h3 style={{ color: 'var(--neo-text)', marginBottom: 16 }}>Basic Information</h3>
          <div style={{ display: 'grid', gap: 16 }}>
            <div>
              <label style={{ fontSize: 13, color: 'var(--neo-text-muted)', display: 'block', marginBottom: 4 }}>Vertical Name *</label>
              <input style={inputStyle} value={form.name} onChange={e => updateForm('name', e.target.value)}
                placeholder="e.g. legal, healthcare, logistics" />
            </div>
            <div>
              <label style={{ fontSize: 13, color: 'var(--neo-text-muted)', display: 'block', marginBottom: 4 }}>Description</label>
              <input style={inputStyle} value={form.description} onChange={e => updateForm('description', e.target.value)}
                placeholder="What does this vertical do?" />
            </div>
            <div>
              <label style={{ fontSize: 13, color: 'var(--neo-text-muted)', display: 'block', marginBottom: 4 }}>Author</label>
              <input style={inputStyle} value={form.author} onChange={e => updateForm('author', e.target.value)} />
            </div>
          </div>
        </div>
      )}

      {/* Step 2: Schema */}
      {step === 1 && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
            <h3 style={{ color: 'var(--neo-text)' }}>Node Types</h3>
            <button onClick={addNodeType} style={btnSecondary}><Plus size={14} /> Add Node Type</button>
          </div>
          {form.node_types.map((nt, i) => (
            <div key={i} style={cardStyle}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
                <input style={{ ...inputStyle, fontWeight: 600, width: '60%' }} value={nt.name}
                  onChange={e => updateNodeType(i, 'name', e.target.value)} placeholder="Node type name (e.g. Contract)" />
                <button onClick={() => removeNodeType(i)} style={{ ...btnSecondary, color: '#e55' }}><Trash2 size={14} /></button>
              </div>
              <input style={{ ...inputStyle, marginBottom: 12 }} value={nt.description}
                onChange={e => updateNodeType(i, 'description', e.target.value)} placeholder="Description" />
              <div style={{ fontSize: 13, color: 'var(--neo-text-muted)', marginBottom: 8 }}>Properties</div>
              {nt.properties.map((p, j) => (
                <div key={j} style={{ display: 'flex', gap: 8, marginBottom: 8, alignItems: 'center' }}>
                  <input style={{ ...inputStyle, flex: 2 }} value={p.name}
                    onChange={e => updateProperty(i, j, 'name', e.target.value)} placeholder="Property name" />
                  <select style={{ ...inputStyle, flex: 1 }} value={p.type}
                    onChange={e => updateProperty(i, j, 'type', e.target.value)}>
                    {PROPERTY_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: 'var(--neo-text-muted)', whiteSpace: 'nowrap' }}>
                    <input type="checkbox" checked={p.required} onChange={e => updateProperty(i, j, 'required', e.target.checked)} /> Req
                  </label>
                  <button onClick={() => removeProperty(i, j)} style={{ background: 'none', border: 'none', color: '#e55', cursor: 'pointer' }}><Trash2 size={14} /></button>
                </div>
              ))}
              <button onClick={() => addProperty(i)} style={{ ...btnSecondary, fontSize: 12, padding: '4px 12px' }}>+ Property</button>
            </div>
          ))}

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 32, marginBottom: 16 }}>
            <h3 style={{ color: 'var(--neo-text)' }}>Edge Types (Relationships)</h3>
            <button onClick={addEdgeType} style={btnSecondary}><Plus size={14} /> Add Edge Type</button>
          </div>
          {form.edge_types.map((et, i) => (
            <div key={i} style={{ ...cardStyle, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr auto', gap: 8, alignItems: 'center' }}>
              <input style={inputStyle} value={et.name} onChange={e => updateEdgeType(i, 'name', e.target.value)} placeholder="Edge name (e.g. HAS_CLAUSE)" />
              <select style={inputStyle} value={et.source} onChange={e => updateEdgeType(i, 'source', e.target.value)}>
                <option value="">Source type...</option>
                {nodeTypeNames.map(n => <option key={n} value={n}>{n}</option>)}
              </select>
              <select style={inputStyle} value={et.target} onChange={e => updateEdgeType(i, 'target', e.target.value)}>
                <option value="">Target type...</option>
                {nodeTypeNames.map(n => <option key={n} value={n}>{n}</option>)}
              </select>
              <button onClick={() => removeEdgeType(i)} style={{ background: 'none', border: 'none', color: '#e55', cursor: 'pointer' }}><Trash2 size={14} /></button>
            </div>
          ))}
        </div>
      )}

      {/* Step 3: Sensors */}
      {step === 2 && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
            <h3 style={{ color: 'var(--neo-text)' }}>Data Sensors</h3>
            <button onClick={addSensor} style={btnSecondary}><Plus size={14} /> Add Sensor</button>
          </div>
          {form.sensors.length === 0 && (
            <div style={{ ...cardStyle, textAlign: 'center', color: 'var(--neo-text-muted)' }}>
              No sensors yet. Sensors automatically collect data and feed it into the graph on a schedule.
            </div>
          )}
          {form.sensors.map((s, i) => (
            <div key={i} style={cardStyle}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
                <input style={{ ...inputStyle, fontWeight: 600, width: '50%' }} value={s.name}
                  onChange={e => updateSensor(i, 'name', e.target.value)} placeholder="Sensor name" />
                <button onClick={() => removeSensor(i)} style={{ ...btnSecondary, color: '#e55' }}><Trash2 size={14} /></button>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginBottom: 8 }}>
                <select style={inputStyle} value={s.source_type} onChange={e => updateSensor(i, 'source_type', e.target.value)}>
                  {SOURCE_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
                <input style={inputStyle} type="number" value={s.interval_seconds}
                  onChange={e => updateSensor(i, 'interval_seconds', parseInt(e.target.value) || 300)} placeholder="Interval (seconds)" />
                <input style={inputStyle} value={s.source_url}
                  onChange={e => updateSensor(i, 'source_url', e.target.value)} placeholder="Source URL (optional)" />
              </div>
              <input style={inputStyle} value={s.description}
                onChange={e => updateSensor(i, 'description', e.target.value)} placeholder="What does this sensor collect?" />
            </div>
          ))}
        </div>
      )}

      {/* Step 4: Routes */}
      {step === 3 && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
            <h3 style={{ color: 'var(--neo-text)' }}>Custom API Routes</h3>
            <button onClick={addRoute} style={btnSecondary}><Plus size={14} /> Add Route</button>
          </div>
          {form.routes.length === 0 && (
            <div style={{ ...cardStyle, textAlign: 'center', color: 'var(--neo-text-muted)' }}>
              No custom routes. Routes are REST endpoints mounted at /v1/{'{your_vertical}'}/
            </div>
          )}
          {form.routes.map((r, i) => (
            <div key={i} style={{ ...cardStyle, display: 'grid', gridTemplateColumns: '100px 1fr 1fr auto', gap: 8, alignItems: 'center' }}>
              <select style={inputStyle} value={r.method} onChange={e => updateRoute(i, 'method', e.target.value)}>
                {['GET', 'POST', 'PUT', 'DELETE'].map(m => <option key={m}>{m}</option>)}
              </select>
              <input style={inputStyle} value={r.path} onChange={e => updateRoute(i, 'path', e.target.value)} placeholder="/items" />
              <input style={inputStyle} value={r.description} onChange={e => updateRoute(i, 'description', e.target.value)} placeholder="Description" />
              <button onClick={() => removeRoute(i)} style={{ background: 'none', border: 'none', color: '#e55', cursor: 'pointer' }}><Trash2 size={14} /></button>
            </div>
          ))}
        </div>
      )}

      {/* Step 5: Preview & Generate */}
      {step === 4 && (
        <div>
          <div style={{ display: 'flex', gap: 12, marginBottom: 24 }}>
            <button onClick={doPreview} disabled={loading || !form.name} style={btnSecondary}>
              <Eye size={14} /> Preview Files
            </button>
            <button onClick={doGenerate} disabled={loading || !form.name} style={btnPrimary}>
              <Download size={14} /> Download ZIP
            </button>
          </div>

          {/* Summary */}
          <div style={cardStyle}>
            <h3 style={{ color: 'var(--neo-text)', marginBottom: 12 }}>Summary</h3>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, fontSize: 14 }}>
              <div style={{ color: 'var(--neo-text-muted)' }}>Package:</div>
              <div style={{ color: 'var(--neo-text)' }}>contextsynapse-{form.name.toLowerCase().replace(/[^a-z0-9]/g, '_')}</div>
              <div style={{ color: 'var(--neo-text-muted)' }}>Node types:</div>
              <div style={{ color: 'var(--neo-text)' }}>{form.node_types.length}</div>
              <div style={{ color: 'var(--neo-text-muted)' }}>Edge types:</div>
              <div style={{ color: 'var(--neo-text)' }}>{form.edge_types.length}</div>
              <div style={{ color: 'var(--neo-text-muted)' }}>Sensors:</div>
              <div style={{ color: 'var(--neo-text)' }}>{form.sensors.length}</div>
              <div style={{ color: 'var(--neo-text-muted)' }}>API routes:</div>
              <div style={{ color: 'var(--neo-text)' }}>{form.routes.length}</div>
            </div>
          </div>

          {/* File preview */}
          {preview && (
            <div style={cardStyle}>
              <h3 style={{ color: 'var(--neo-text)', marginBottom: 12 }}>Generated Files</h3>
              {Object.entries(preview.files).map(([name, desc]) => (
                <div key={name} style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0',
                  borderBottom: '1px solid var(--neo-border)', fontSize: 13 }}>
                  <code style={{ color: 'var(--neo-blue)' }}>{name}</code>
                  <span style={{ color: 'var(--neo-text-muted)' }}>{desc}</span>
                </div>
              ))}
            </div>
          )}

          {/* Install instructions */}
          <div style={cardStyle}>
            <h3 style={{ color: 'var(--neo-text)', marginBottom: 12 }}>After Download</h3>
            <pre style={{ background: 'var(--neo-bg)', padding: 16, borderRadius: 8, fontSize: 13, color: 'var(--neo-text)', overflowX: 'auto' }}>
{`# Extract and install
unzip contextsynapse-${form.name.toLowerCase().replace(/[^a-z0-9]/g, '_')}.zip
cd contextsynapse-${form.name.toLowerCase().replace(/[^a-z0-9]/g, '_')}
pip install -e .

# Start ContextSynapse — your vertical auto-discovers
contextsynapse serve

# Verify
curl http://localhost:8000/plugins/`}
            </pre>
          </div>
        </div>
      )}

      {/* Navigation */}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 32 }}>
        <button onClick={() => setStep(Math.max(0, step - 1))} disabled={step === 0}
          style={{ ...btnSecondary, opacity: step === 0 ? 0.3 : 1 }}>
          <ChevronLeft size={14} /> Back
        </button>
        <button onClick={() => { if (step === 4) doPreview(); else setStep(step + 1); }}
          disabled={step === 0 && !form.name}
          style={step === 4 ? btnPrimary : { ...btnSecondary, borderColor: 'var(--neo-blue)', color: 'var(--neo-blue)' }}>
          {step === 4 ? <><Download size={14} /> Generate</> : <>Next <ChevronRight size={14} /></>}
        </button>
      </div>
    </div>
  );
}

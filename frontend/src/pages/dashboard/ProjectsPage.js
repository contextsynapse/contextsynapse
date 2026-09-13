import React, { useState, useEffect, useCallback } from 'react';
import {
  Code2, BarChart3, AlertTriangle, Bot, Clock, Plus, RefreshCw,
  ChevronRight, CheckCircle, Circle, Terminal, ShieldCheck, ClipboardList,
} from 'lucide-react';

const API = '/api/dashboard/projects';

function LayerBar({ name, score, optional }) {
  const pct = score == null ? 0 : Math.round(score * 100);
  const color = pct >= 80 ? 'var(--neo-green)' : pct >= 40 ? 'var(--neo-blue)' : 'var(--neo-text-muted)';
  return (
    <div className="mb-2">
      <div className="flex items-center justify-between mb-0.5">
        <span className="text-xs capitalize" style={{ color: 'var(--neo-text-muted)' }}>
          {name}{optional ? ' (optional)' : ''}
        </span>
        <span className="text-xs font-medium" style={{ color }}>
          {score == null ? 'N/A' : `${pct}%`}
        </span>
      </div>
      <div className="rounded-full h-1.5" style={{ background: 'var(--neo-border)' }}>
        <div className="h-1.5 rounded-full transition-all" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  );
}

function CoverageMap({ coverage }) {
  if (!coverage) return null;
  const layers = ['intent', 'design', 'build', 'verify', 'evolution'];
  return (
    <div className="rounded-lg p-4" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
      <div className="flex items-center gap-2 mb-3">
        <BarChart3 size={14} style={{ color: 'var(--neo-blue)' }} />
        <span className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>Coverage</span>
        <span className="ml-auto text-xs px-2 py-0.5 rounded-full" style={{ background: 'var(--neo-surface-light)', color: 'var(--neo-text-muted)' }}>
          {coverage.project_type}
        </span>
      </div>
      {layers.map(l => (
        <LayerBar key={l} name={l} score={coverage[l]} optional={l === 'evolution'} />
      ))}
      <div className="mt-3 pt-3 flex items-center justify-between" style={{ borderTop: '1px solid var(--neo-border)' }}>
        <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Overall</span>
        <span className="text-sm font-bold" style={{ color: 'var(--neo-text)' }}>
          {Math.round((coverage.overall || 0) * 100)}%
        </span>
      </div>
    </div>
  );
}

function StaleBoard({ stale }) {
  const empty = !stale || stale.length === 0;
  return (
    <div className="rounded-lg p-4" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
      <div className="flex items-center gap-2 mb-3">
        {empty
          ? <CheckCircle size={14} style={{ color: 'var(--neo-green)' }} />
          : <AlertTriangle size={14} style={{ color: '#f59e0b' }} />}
        <span className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>Stale Nodes</span>
        {!empty && (
          <span className="ml-auto text-xs px-2 py-0.5 rounded-full font-bold" style={{ background: 'rgba(245,158,11,0.15)', color: '#f59e0b' }}>
            {stale.length}
          </span>
        )}
      </div>
      {empty
        ? <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>No stale nodes — everything is current.</p>
        : (
          <div className="flex flex-col gap-2">
            {stale.map(n => (
              <div key={n.node_id} className="rounded p-2" style={{ background: 'var(--neo-bg)' }}>
                <div className="flex items-center gap-1.5">
                  <Circle size={6} style={{ color: '#f59e0b', fill: 'currentColor' }} />
                  <span className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>{n.label}</span>
                  <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>{n.node_id}</span>
                </div>
                {n.stale_reason && (
                  <p className="text-xs mt-0.5 ml-3" style={{ color: 'var(--neo-text-muted)' }}>{n.stale_reason}</p>
                )}
              </div>
            ))}
          </div>
        )}
    </div>
  );
}

function AgentAccess({ projectName }) {
  const [copied, setCopied] = useState(false);
  const snippet = `mcp__contextsynapse__agent_brief(project_name="${projectName}")\nmcp__contextsynapse__coverage_score(project_name="${projectName}")`;
  const copy = () => {
    navigator.clipboard.writeText(snippet).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000); });
  };
  return (
    <div className="rounded-lg p-4" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
      <div className="flex items-center gap-2 mb-3">
        <Bot size={14} style={{ color: 'var(--neo-blue)' }} />
        <span className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>Agent Access</span>
      </div>
      <p className="text-xs mb-2" style={{ color: 'var(--neo-text-muted)' }}>Connect any MCP-enabled agent to this project namespace.</p>
      <pre className="text-xs rounded p-2 overflow-x-auto mb-2" style={{ background: 'var(--neo-bg)', color: 'var(--neo-text)', fontFamily: 'monospace' }}>
        {snippet}
      </pre>
      <button onClick={copy} className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg" style={{ background: 'var(--neo-blue)', color: '#fff', border: 'none', cursor: 'pointer' }}>
        <Terminal size={11} />
        {copied ? 'Copied!' : 'Copy snippet'}
      </button>
    </div>
  );
}

function Timeline({ events }) {
  return (
    <div className="rounded-lg p-4" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
      <div className="flex items-center gap-2 mb-3">
        <Clock size={14} style={{ color: 'var(--neo-blue)' }} />
        <span className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>Timeline</span>
      </div>
      {!events || events.length === 0
        ? <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>No events yet.</p>
        : (
          <div className="flex flex-col gap-1.5">
            {events.slice(0, 10).map((ev, i) => (
              <div key={i} className="flex items-start gap-2 text-xs">
                <span style={{ color: 'var(--neo-text-muted)', whiteSpace: 'nowrap' }}>
                  {ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString() : '—'}
                </span>
                <span style={{ color: 'var(--neo-text)' }}>
                  {ev.event_type || ev.type || 'update'}{' '}
                  <span style={{ color: 'var(--neo-blue)' }}>{ev.node_id || ev.id || ''}</span>
                </span>
              </div>
            ))}
          </div>
        )}
    </div>
  );
}

// ── Requirements Tab ──────────────────────────────────────────────────

const LABEL_COLORS = {
  Requirement: '#3b82f6',
  Feature:     '#8b5cf6',
  Constraint:  '#f59e0b',
};

const PRIORITY_COLORS = {
  must:   '#ef4444',
  should: '#f59e0b',
  could:  '#10b981',
};

function CoverageBar({ covered, partial, uncovered, total }) {
  if (total === 0) return null;
  const pctCovered  = (covered  / total) * 100;
  const pctPartial  = (partial  / total) * 100;
  const pctUncov    = (uncovered / total) * 100;
  return (
    <div style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', background: 'var(--neo-border)' }}>
      {pctCovered  > 0 && <div style={{ width: `${pctCovered}%`,  background: '#10b981' }} />}
      {pctPartial  > 0 && <div style={{ width: `${pctPartial}%`,  background: '#f59e0b' }} />}
      {pctUncov    > 0 && <div style={{ width: `${pctUncov}%`,   background: '#ef4444' }} />}
    </div>
  );
}

function RequirementNode({ node }) {
  const p     = node.properties || {};
  const label = node.label || 'Requirement';
  const labelColor    = LABEL_COLORS[label]    || 'var(--neo-text-muted)';
  const priorityColor = PRIORITY_COLORS[p.priority] || 'var(--neo-text-muted)';
  return (
    <div
      style={{
        background: 'var(--neo-surface)',
        border: '1px solid var(--neo-border)',
        borderRadius: 8,
        padding: '10px 12px',
        marginBottom: 8,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <span style={{
          fontSize: 10, fontWeight: 600, padding: '1px 6px', borderRadius: 4,
          background: `${labelColor}22`, color: labelColor,
        }}>
          {label.toUpperCase()}
        </span>
        {p.priority && (
          <span style={{
            fontSize: 10, fontWeight: 600, padding: '1px 6px', borderRadius: 4,
            background: `${priorityColor}22`, color: priorityColor,
          }}>
            {p.priority.toUpperCase()}
          </span>
        )}
      </div>
      <p style={{ fontSize: 13, fontWeight: 600, color: 'var(--neo-text)', margin: 0 }}>
        {p.name || node.node_id}
      </p>
      {p.description && (
        <p style={{ fontSize: 11, color: 'var(--neo-text-muted)', margin: '4px 0 0', lineHeight: 1.5 }}>
          {p.description.length > 140 ? p.description.slice(0, 140) + '…' : p.description}
        </p>
      )}
    </div>
  );
}

function RequirementsTab({ projectName }) {
  const [nodes, setNodes]       = useState([]);
  const [coverage, setCoverage] = useState(null);
  const [loading, setLoading]   = useState(true);
  const [ingesting, setIngesting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [reqRes, covRes] = await Promise.all([
        fetch(`${API}/${projectName}/spec/requirements`),
        fetch(`${API}/${projectName}/spec/coverage`),
      ]);
      if (reqRes.ok) {
        const d = await reqRes.json();
        setNodes(d.nodes || []);
      }
      if (covRes.ok) setCoverage(await covRes.json());
    } catch {
      // requirements not yet ingested — empty state is fine
    } finally {
      setLoading(false);
    }
  }, [projectName]);

  useEffect(() => { load(); }, [load]);

  const handleIngest = async () => {
    setIngesting(true);
    try {
      const res = await fetch(`${API}/${projectName}/spec/ingest`, { method: 'POST' });
      if (res.ok) {
        const d = await res.json();
        console.log(`Parsed ${d.count} spec items`);
        load();
      }
    } catch (e) {
      console.error('Spec parsing failed', e);
    } finally {
      setIngesting(false);
    }
  };

  const coveredCount   = coverage ? coverage.covered.length   : 0;
  const partialCount   = coverage ? coverage.partial.length   : 0;
  const uncoveredCount = coverage ? coverage.uncovered.length : 0;
  const totalCount     = coverage ? coverage.total            : 0;
  const pct            = coverage ? coverage.coverage_pct     : 0;

  return (
    <div>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--neo-text)' }}>
          {nodes.length > 0 ? `${nodes.length} spec items` : 'No requirements parsed yet'}
        </span>
        <button
          onClick={handleIngest}
          disabled={ingesting}
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            fontSize: 12, fontWeight: 600, padding: '6px 12px', borderRadius: 6,
            background: '#8b5cf6', color: '#fff',
            border: 'none', cursor: ingesting ? 'not-allowed' : 'pointer',
            opacity: ingesting ? 0.6 : 1,
          }}
        >
          <ShieldCheck size={12} />
          {ingesting ? 'Parsing…' : 'Parse Spec'}
        </button>
      </div>

      {/* Coverage block — only when requirements exist */}
      {totalCount > 0 && coverage && (
        <div
          style={{
            background: 'var(--neo-surface)',
            border: '1px solid var(--neo-border)',
            borderRadius: 8,
            padding: '12px 14px',
            marginBottom: 16,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--neo-text)' }}>
              Traceability Coverage
            </span>
            <span style={{ fontSize: 18, fontWeight: 700, color: pct >= 70 ? '#10b981' : pct >= 40 ? '#f59e0b' : '#ef4444' }}>
              {pct.toFixed(0)}%
            </span>
          </div>
          <CoverageBar
            covered={coveredCount}
            partial={partialCount}
            uncovered={uncoveredCount}
            total={totalCount}
          />
          <div style={{ display: 'flex', gap: 12, marginTop: 8 }}>
            <span style={{ fontSize: 11, color: '#10b981' }}>✓ {coveredCount} covered</span>
            <span style={{ fontSize: 11, color: '#f59e0b' }}>◐ {partialCount} partial</span>
            <span style={{ fontSize: 11, color: '#ef4444' }}>○ {uncoveredCount} uncovered</span>
          </div>
        </div>
      )}

      {/* Requirements list */}
      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '40px 0' }}>
          <RefreshCw size={20} style={{ color: 'var(--neo-text-muted)' }} />
        </div>
      ) : nodes.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '48px 0', color: 'var(--neo-text-muted)' }}>
          <ShieldCheck size={32} style={{ margin: '0 auto 12px', opacity: 0.3 }} />
          <p style={{ fontSize: 14, marginBottom: 4 }}>No spec items yet</p>
          <p style={{ fontSize: 12 }}>
            Add <code style={{ fontSize: 11 }}>## REQ:</code>, <code style={{ fontSize: 11 }}>## FEAT:</code>, or{' '}
            <code style={{ fontSize: 11 }}>## CON:</code> headings to your project spec, then click "Parse Spec".
          </p>
        </div>
      ) : (
        <div>
          {nodes.map(n => (
            <RequirementNode key={n.node_id || n.id} node={n} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Overview Tab ──────────────────────────────────────────────────────

function OverviewTab({ projectName, coverage, stale, timeline }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      <CoverageMap coverage={coverage} />
      <StaleBoard stale={stale} />
      <AgentAccess projectName={projectName} />
      <Timeline events={timeline} />
    </div>
  );
}

function ProjectDetail({ name, onBack }) {
  const [coverage, setCoverage] = useState(null);
  const [stale, setStale] = useState([]);
  const [timeline, setTimeline] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('overview');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [covRes, staleRes, tlRes] = await Promise.all([
        fetch(`${API}/${name}/coverage`),
        fetch(`${API}/${name}/stale`),
        fetch(`${API}/${name}/timeline`),
      ]);
      if (covRes.ok) setCoverage(await covRes.json());
      if (staleRes.ok) setStale(await staleRes.json());
      if (tlRes.ok) setTimeline(await tlRes.json());
    } catch (_) {}
    setLoading(false);
  }, [name]);

  useEffect(() => { load(); }, [load]);

  const tabs = [
    { id: 'overview',      label: 'Overview',      icon: ClipboardList },
    { id: 'requirements',  label: 'Requirements',  icon: ShieldCheck },
  ];

  return (
    <div>
      <div className="flex items-center gap-2 mb-4">
        <button onClick={onBack} className="text-xs px-2 py-1 rounded" style={{ background: 'var(--neo-surface-light)', color: 'var(--neo-text-muted)', border: 'none', cursor: 'pointer' }}>
          ← Back
        </button>
        <h2 className="text-base font-semibold" style={{ color: 'var(--neo-text)' }}>{name}</h2>
        <button onClick={load} className="ml-auto" style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--neo-text-muted)' }}>
          <RefreshCw size={13} />
        </button>
      </div>

      {/* Tab bar */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 16, borderBottom: '1px solid var(--neo-border)', paddingBottom: 0 }}>
        {tabs.map(tab => {
          const Icon = tab.icon;
          const active = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                fontSize: 12, fontWeight: active ? 600 : 400,
                padding: '6px 12px', border: 'none', borderRadius: '6px 6px 0 0',
                background: active ? 'var(--neo-surface)' : 'transparent',
                color: active ? 'var(--neo-text)' : 'var(--neo-text-muted)',
                cursor: 'pointer',
                borderBottom: active ? '2px solid var(--neo-blue)' : '2px solid transparent',
              }}
            >
              <Icon size={13} />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      {loading && activeTab === 'overview'
        ? <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Loading…</p>
        : (
          <>
            {activeTab === 'overview' && (
              <OverviewTab projectName={name} coverage={coverage} stale={stale} timeline={timeline} />
            )}
            {activeTab === 'requirements' && <RequirementsTab projectName={name} />}
          </>
        )}
    </div>
  );
}

function CreateProjectForm({ onCreated }) {
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const submit = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    setLoading(true);
    setError('');
    try {
      const res = await fetch(API, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim() }),
      });
      if (res.ok) { onCreated(name.trim()); setName(''); }
      else setError('Failed to create project.');
    } catch (_) { setError('Network error.'); }
    setLoading(false);
  };

  return (
    <form onSubmit={submit} className="flex items-center gap-2 mb-4">
      <input
        value={name}
        onChange={e => setName(e.target.value)}
        placeholder="project-name"
        className="flex-1 text-sm px-3 py-1.5 rounded-lg"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)', outline: 'none' }}
      />
      <button type="submit" disabled={loading || !name.trim()} className="flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-lg" style={{ background: 'var(--neo-blue)', color: '#fff', border: 'none', cursor: 'pointer', opacity: loading ? 0.6 : 1 }}>
        <Plus size={13} />
        {loading ? 'Creating…' : 'New Project'}
      </button>
      {error && <span className="text-xs" style={{ color: '#ef4444' }}>{error}</span>}
    </form>
  );
}

function ProjectList({ projects, onSelect }) {
  if (projects.length === 0) {
    return <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>No projects yet. Create one above.</p>;
  }
  return (
    <div className="flex flex-col gap-2">
      {projects.map(p => (
        <button key={p.name} onClick={() => onSelect(p.name)} className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-left" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)', cursor: 'pointer' }}>
          <Code2 size={13} style={{ color: 'var(--neo-blue)' }} />
          {p.name}
          <ChevronRight size={13} className="ml-auto" style={{ color: 'var(--neo-text-muted)' }} />
        </button>
      ))}
    </div>
  );
}

export default function ProjectsPage() {
  const [projects, setProjects] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadProjects = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(API);
      if (res.ok) setProjects(await res.json());
    } catch (_) {}
    setLoading(false);
  }, []);

  useEffect(() => { loadProjects(); }, [loadProjects]);

  if (selected) {
    return (
      <div className="p-4 max-w-4xl mx-auto">
        <ProjectDetail name={selected} onBack={() => { setSelected(null); loadProjects(); }} />
      </div>
    );
  }

  return (
    <div className="p-4 max-w-2xl mx-auto">
      <div className="flex items-center gap-2 mb-4">
        <Code2 size={16} style={{ color: 'var(--neo-blue)' }} />
        <h1 className="text-base font-semibold" style={{ color: 'var(--neo-text)' }}>Projects</h1>
        <button onClick={loadProjects} className="ml-auto" style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--neo-text-muted)' }}>
          <RefreshCw size={13} />
        </button>
      </div>
      <CreateProjectForm onCreated={name => { loadProjects(); setSelected(name); }} />
      {loading
        ? <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Loading…</p>
        : <ProjectList projects={projects} onSelect={setSelected} />}
    </div>
  );
}

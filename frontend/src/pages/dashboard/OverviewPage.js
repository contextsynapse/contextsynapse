import React, { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { Database, GitBranch, Bot, Activity, ArrowRight, Loader2, Layers, FileText, Play, Plug, Clock, Plus, Link as LinkIcon } from 'lucide-react';
import api from '../../lib/api';

function StatCard({ icon: Icon, label, value, color, to }) {
  const card = (
    <div
      className="rounded-xl p-5 transition hover:scale-[1.02]"
      style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
    >
      <div className="flex items-center justify-between mb-3">
        <Icon size={20} style={{ color }} />
        {to && <ArrowRight size={14} style={{ color: 'var(--neo-text-muted)' }} />}
      </div>
      <div className="text-2xl font-bold" style={{ color: 'var(--neo-text)' }}>{value}</div>
      <div className="text-xs mt-1" style={{ color: 'var(--neo-text-muted)' }}>{label}</div>
    </div>
  );
  return to ? <Link to={to} className="no-underline">{card}</Link> : card;
}

export default function OverviewPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  const loadData = useCallback(() => {
    setLoading(true);
    api.get('/dashboard/overview')
      .then(res => setData(res.data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      </div>
    );
  }

  const usage = data?.usage_this_month || {};
  const apiCalls = usage.api_call || 0;

  return (
    <div className="max-w-5xl">
      <h1 className="text-xl font-bold mb-1" style={{ color: 'var(--neo-text)' }}>
        Dashboard
      </h1>
      <p className="text-sm mb-6" style={{ color: 'var(--neo-text-muted)' }}>
        {data?.tenant_name || 'Your workspace'}
      </p>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-4">
        <StatCard icon={Database} label="Contexts" value={data?.context_count ?? 0} color="var(--neo-blue)" to="/dashboard/contexts" />
        <StatCard icon={Layers} label="Boundaries" value={data?.session_count ?? 0} color="#af52de" to="/dashboard/sessions" />
        <StatCard icon={Bot} label="Agents" value={`${data?.active_agents ?? 0}/${data?.agent_count ?? 0}`} color="var(--neo-green)" to="/dashboard/agents" />
        <StatCard icon={Play} label="Pipeline Runs" value={data?.pipeline_runs ?? 0} color="var(--neo-cyan)" />
        <StatCard icon={Plug} label="Integrations" value={data?.integration_count ?? 0} color="#f59e0b" to="/dashboard/integrations" />
        <StatCard icon={GitBranch} label="Nodes / Edges" value={`${data?.node_count ?? 0} / ${data?.edge_count ?? 0}`} color="var(--neo-text-muted)" to="/dashboard/graphs" />
      </div>

      <div className="flex gap-3 mb-6">
        <a href="/dashboard/contexts" className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium transition hover:scale-[1.02] no-underline"
           style={{ background: 'var(--neo-blue)', color: '#fff' }}>
          <Plus size={14} /> Create Context
        </a>
        <a href="/dashboard/graphs" className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium transition hover:scale-[1.02] no-underline"
           style={{ background: 'var(--neo-surface)', color: 'var(--neo-text)', border: '1px solid var(--neo-border)' }}>
          <Database size={14} /> Explore Graphs
        </a>
        <a href="/dashboard/integrations" className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium transition hover:scale-[1.02] no-underline"
           style={{ background: 'var(--neo-surface)', color: 'var(--neo-text)', border: '1px solid var(--neo-border)' }}>
          <LinkIcon size={14} /> Connect Data Source
        </a>
      </div>

      {/* Getting Started CTA — shown when no graphs exist */}
      {(data?.graph_count ?? 0) === 0 && (
        <div
          className="rounded-xl p-6 mb-6"
          style={{ background: 'linear-gradient(135deg, rgba(76,142,218,0.15), rgba(78,219,229,0.1))', border: '1px solid var(--neo-blue)' }}
        >
          <h2 className="text-base font-bold mb-2" style={{ color: 'var(--neo-text)' }}>
            Welcome to ContextSynapse
          </h2>
          <p className="text-sm mb-4" style={{ color: 'var(--neo-text-muted)' }}>
            Create your first graph to start building your knowledge base with AIQL.
          </p>
          <div className="flex gap-3">
            <Link
              to="/dashboard/graphs"
              className="px-4 py-2 rounded-lg text-sm font-medium no-underline transition"
              style={{ background: 'var(--neo-blue)', color: '#fff' }}
            >
              Create a Graph
            </Link>
            <Link
              to="/dashboard/docs"
              className="px-4 py-2 rounded-lg text-sm font-medium no-underline transition"
              style={{ background: 'var(--neo-surface)', color: 'var(--neo-text)', border: '1px solid var(--neo-border)' }}
            >
              Read the Docs
            </Link>
          </div>
        </div>
      )}

      {/* Context overview */}
      <div
        className="rounded-xl p-5 mb-8"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
      >
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Layers size={16} style={{ color: '#af52de' }} />
            <h2 className="text-sm font-semibold" style={{ color: 'var(--neo-text)' }}>
              Context
            </h2>
          </div>
          <Link
            to="/dashboard/sessions"
            className="flex items-center gap-1 text-xs no-underline transition hover:opacity-80"
            style={{ color: 'var(--neo-cyan)' }}
          >
            Manage sessions <ArrowRight size={12} />
          </Link>
        </div>
        <div className="grid grid-cols-3 gap-4">
          <div>
            <div className="text-2xl font-bold" style={{ color: 'var(--neo-text)' }}>
              {data?.session_count ?? 0}
            </div>
            <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Sessions</div>
          </div>
          <div>
            <div className="text-2xl font-bold" style={{ color: 'var(--neo-text)' }}>
              {data?.context_items ?? 0}
            </div>
            <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Context items</div>
          </div>
          <div>
            <div className="text-2xl font-bold" style={{ color: 'var(--neo-text)' }}>
              {(data?.context_tokens ?? 0) > 1000
                ? `${((data?.context_tokens ?? 0) / 1000).toFixed(1)}k`
                : data?.context_tokens ?? 0}
            </div>
            <div className="flex items-center gap-1">
              <FileText size={10} style={{ color: 'var(--neo-text-muted)' }} />
              <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Tokens</span>
            </div>
          </div>
        </div>
      </div>

      {/* Usage summary */}
      <div
        className="rounded-xl p-5 mb-8"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
      >
        <div className="flex items-center gap-2 mb-4">
          <Activity size={16} style={{ color: 'var(--neo-cyan)' }} />
          <h2 className="text-sm font-semibold" style={{ color: 'var(--neo-text)' }}>
            This Month
          </h2>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          {Object.entries(usage).length > 0 ? (
            Object.entries(usage).map(([type, count]) => (
              <div key={type}>
                <div className="text-lg font-bold" style={{ color: 'var(--neo-text)' }}>{count}</div>
                <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                  {type.replace(/_/g, ' ')}
                </div>
              </div>
            ))
          ) : (
            <div className="col-span-4 text-sm" style={{ color: 'var(--neo-text-muted)' }}>
              No usage yet this month. <Link to="/dashboard/graphs" className="hover:underline" style={{ color: 'var(--neo-cyan)' }}>Create your first graph</Link> to get started.
            </div>
          )}
        </div>
      </div>

      {/* Quick start */}
      <div
        className="rounded-xl p-5"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
      >
        <h2 className="text-sm font-semibold mb-3" style={{ color: 'var(--neo-text)' }}>
          Quick Start
        </h2>
        <pre
          className="text-xs p-4 rounded-lg overflow-x-auto"
          style={{ background: 'var(--neo-bg)', color: 'var(--neo-text-muted)' }}
        >
{`from contextsynapse import ContextSynapse

# Connect to your ContextSynapse instance
db = ContextSynapse()

# Create a context and add knowledge
db.add_node("Person", {"name": "Alice", "role": "Engineer"})
db.add_node("Project", {"name": "Atlas", "status": "active"})
db.add_relationship("Alice", "Atlas", "WORKS_ON")

# Query your knowledge graph
results = db.search("Who works on Atlas?")`}
        </pre>
      </div>
    </div>
  );
}

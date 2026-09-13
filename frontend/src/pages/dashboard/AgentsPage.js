import React, { useState, useEffect, useCallback } from 'react';
import {
  Bot, Loader2, Plus, Trash2, Key, Clock, Eye, Copy, Check,
  ChevronDown, ChevronUp, RefreshCw, Terminal, Code2,
  Monitor, Smartphone, Cloud, Globe, Cpu,
} from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import ActivityFeed from '../../components/ActivityFeed';

const ROLE_OPTIONS = ['agent', 'analyst', 'collector', 'admin'];
const CAPABILITY_OPTIONS = ['read', 'write', 'admin', 'ingest', 'search', 'query'];

const PLATFORM_OPTIONS = [
  { value: 'desktop', label: 'Desktop', icon: Monitor, color: '#3b82f6' },
  { value: 'mobile', label: 'Mobile', icon: Smartphone, color: '#8b5cf6' },
  { value: 'app', label: 'App', icon: Cloud, color: '#22c55e' },
  { value: 'browser', label: 'Browser', icon: Globe, color: '#f59e0b' },
  { value: 'embedded', label: 'Embedded', icon: Cpu, color: '#ef4444' },
];

export default function AgentsPage() {
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showRegister, setShowRegister] = useState(false);
  const [expandedAgent, setExpandedAgent] = useState(null);
  const [activity, setActivity] = useState({});
  const [activityLoading, setActivityLoading] = useState({});

  // Registration form
  const [newName, setNewName] = useState('');
  const [newRole, setNewRole] = useState('agent');
  const [newPlatform, setNewPlatform] = useState('app');
  const [newCaps, setNewCaps] = useState(['read', 'write']);
  const [newDescription, setNewDescription] = useState('');
  const [newSkills, setNewSkills] = useState('');
  // Adapter removed — we're a context service, not an agent runtime
  const [registering, setRegistering] = useState(false);
  const [newApiKey, setNewApiKey] = useState(null);
  const [copied, setCopied] = useState(false);
  const [platformFilter, setPlatformFilter] = useState('');

  const fetchAgents = useCallback(() => {
    const params = platformFilter ? { platform: platformFilter } : {};
    api.get('/dashboard/agents', { params })
      .then((res) => setAgents(res.data.agents || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [platformFilter]);

  useEffect(() => { fetchAgents(); }, [fetchAgents]);

  const handleRegister = async () => {
    if (!newName.trim()) return;
    setRegistering(true);
    try {
      const body = {
        name: newName.trim(),
        role: newRole,
        platform: newPlatform,
        capabilities: newCaps,
      };
      if (newDescription.trim()) body.description = newDescription.trim();
      if (newSkills.trim()) {
        body.skills = newSkills.split(',').map((s) => s.trim()).filter(Boolean).map((s) => ({
          id: s.toLowerCase().replace(/\s+/g, '_'),
          name: s,
          description: s,
        }));
      }
      const res = await api.post('/dashboard/agents', body);
      setNewApiKey(res.data.api_key);
      toast.success(`Agent "${newName}" registered`);
      fetchAgents();
      setNewName('');
      setNewDescription('');
      setNewSkills('');
    } catch {} finally {
      setRegistering(false);
    }
  };

  const handleDelete = async (agentId, name) => {
    if (!window.confirm(`Deregister agent "${name || agentId}"? This cannot be undone.`)) return;
    try {
      await api.delete(`/dashboard/agents/${agentId}`);
      toast.success('Agent deregistered');
      fetchAgents();
      if (expandedAgent === agentId) setExpandedAgent(null);
    } catch {}
  };

  const toggleActivity = async (agentId) => {
    if (expandedAgent === agentId) {
      setExpandedAgent(null);
      return;
    }
    setExpandedAgent(agentId);
    if (!activity[agentId]) {
      setActivityLoading((p) => ({ ...p, [agentId]: true }));
      try {
        const res = await api.get(`/dashboard/agents/${agentId}/activity`);
        setActivity((p) => ({ ...p, [agentId]: res.data.activity || [] }));
      } catch {
        setActivity((p) => ({ ...p, [agentId]: [] }));
      } finally {
        setActivityLoading((p) => ({ ...p, [agentId]: false }));
      }
    }
  };

  const copyKey = () => {
    navigator.clipboard.writeText(newApiKey);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      </div>
    );
  }

  return (
    <div className="max-w-4xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold" style={{ color: 'var(--neo-text)' }}>Agents</h1>
          <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
            Register and manage AI agents connected to your workspace
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={fetchAgents}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition hover:opacity-80"
            style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
          >
            <RefreshCw size={14} /> Refresh
          </button>
          <button
            onClick={() => { setShowRegister(!showRegister); setNewApiKey(null); }}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition hover:opacity-90"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            <Plus size={14} /> Register Agent
          </button>
        </div>
      </div>

      {/* Platform filter */}
      <div className="flex items-center gap-1.5 mb-4">
        <span className="text-xs mr-1" style={{ color: 'var(--neo-text-muted)' }}>Platform:</span>
        <button
          onClick={() => setPlatformFilter('')}
          className="px-2 py-1 rounded text-xs transition"
          style={{
            background: !platformFilter ? 'var(--neo-blue)' : 'var(--neo-bg)',
            color: !platformFilter ? '#fff' : 'var(--neo-text-muted)',
            border: `1px solid ${!platformFilter ? 'var(--neo-blue)' : 'var(--neo-border)'}`,
          }}
        >
          All
        </button>
        {PLATFORM_OPTIONS.map((p) => {
          const Icon = p.icon;
          const active = platformFilter === p.value;
          return (
            <button
              key={p.value}
              onClick={() => setPlatformFilter(active ? '' : p.value)}
              className="flex items-center gap-1 px-2 py-1 rounded text-xs transition"
              style={{
                background: active ? `${p.color}20` : 'var(--neo-bg)',
                color: active ? p.color : 'var(--neo-text-muted)',
                border: `1px solid ${active ? p.color : 'var(--neo-border)'}`,
              }}
            >
              <Icon size={10} />
              {p.label}
            </button>
          );
        })}
      </div>

      {/* Registration panel */}
      {showRegister && (
        <div
          className="mb-6 p-5 rounded-xl"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        >
          <h3 className="text-sm font-semibold mb-4" style={{ color: 'var(--neo-text)' }}>
            Register New Agent
          </h3>

          {newApiKey ? (
            <div>
              <p className="text-sm mb-2" style={{ color: 'var(--neo-text)' }}>
                Agent registered. Copy the API key below — it won't be shown again.
              </p>
              <div className="flex items-center gap-2">
                <code
                  className="flex-1 px-3 py-2 rounded-lg text-xs break-all"
                  style={{
                    background: 'var(--neo-bg)',
                    border: '1px solid var(--neo-border)',
                    color: 'var(--neo-green)',
                    fontFamily: "'JetBrains Mono', monospace",
                  }}
                >
                  {newApiKey}
                </code>
                <button
                  onClick={copyKey}
                  className="shrink-0 px-3 py-2 rounded-lg text-sm transition hover:opacity-80"
                  style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                >
                  {copied ? <Check size={14} /> : <Copy size={14} />}
                </button>
              </div>
              <p className="text-xs mt-3 mb-3" style={{ color: 'var(--neo-text-muted)' }}>
                Use in the Authorization header: <code>Bearer {'<api_key>'}</code>
              </p>
              <button
                onClick={() => { setNewApiKey(null); setShowRegister(false); }}
                className="px-3 py-1.5 rounded-lg text-sm"
                style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
              >
                Done
              </button>
            </div>
          ) : (
            <div className="space-y-3">
              <div>
                <label className="block text-xs mb-1" style={{ color: 'var(--neo-text-muted)' }}>Name</label>
                <input
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="e.g. sales-analyst, data-collector"
                  className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                  style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                />
              </div>

              <div className="flex gap-4">
                <div className="flex-1">
                  <label className="block text-xs mb-1" style={{ color: 'var(--neo-text-muted)' }}>Role</label>
                  <select
                    value={newRole}
                    onChange={(e) => setNewRole(e.target.value)}
                    className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                    style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                  >
                    {ROLE_OPTIONS.map((r) => (
                      <option key={r} value={r}>{r}</option>
                    ))}
                  </select>
                </div>
                <div className="flex-1">
                  <label className="block text-xs mb-1" style={{ color: 'var(--neo-text-muted)' }}>Platform</label>
                  <div className="flex flex-wrap gap-1.5">
                    {PLATFORM_OPTIONS.map((p) => {
                      const Icon = p.icon;
                      const active = newPlatform === p.value;
                      return (
                        <button
                          key={p.value}
                          onClick={() => setNewPlatform(p.value)}
                          className="flex items-center gap-1 px-2 py-1 rounded text-xs transition"
                          style={{
                            background: active ? `${p.color}20` : 'var(--neo-bg)',
                            color: active ? p.color : 'var(--neo-text-muted)',
                            border: `1px solid ${active ? p.color : 'var(--neo-border)'}`,
                          }}
                        >
                          <Icon size={10} />
                          {p.label}
                        </button>
                      );
                    })}
                  </div>
                </div>
              </div>
              <div className="flex gap-4">
                <div className="flex-1">
                  <label className="block text-xs mb-1" style={{ color: 'var(--neo-text-muted)' }}>Capabilities</label>
                  <div className="flex flex-wrap gap-1.5">
                    {CAPABILITY_OPTIONS.map((cap) => (
                      <button
                        key={cap}
                        onClick={() =>
                          setNewCaps((prev) =>
                            prev.includes(cap) ? prev.filter((c) => c !== cap) : [...prev, cap]
                          )
                        }
                        className="px-2 py-1 rounded text-xs transition"
                        style={{
                          background: newCaps.includes(cap) ? 'var(--neo-blue)' : 'var(--neo-bg)',
                          color: newCaps.includes(cap) ? '#fff' : 'var(--neo-text-muted)',
                          border: `1px solid ${newCaps.includes(cap) ? 'var(--neo-blue)' : 'var(--neo-border)'}`,
                        }}
                      >
                        {cap}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {/* Agent Card fields */}
              <div>
                <label className="block text-xs mb-1" style={{ color: 'var(--neo-text-muted)' }}>Description</label>
                <input
                  value={newDescription}
                  onChange={(e) => setNewDescription(e.target.value)}
                  placeholder="What does this agent do? e.g. 'Code generation and testing agent'"
                  className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                  style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                />
              </div>

              <div>
                <label className="block text-xs mb-1" style={{ color: 'var(--neo-text-muted)' }}>
                  Skills <span style={{ opacity: 0.5 }}>(for agent card discovery, comma-separated)</span>
                </label>
                <input
                  value={newSkills}
                  onChange={(e) => setNewSkills(e.target.value)}
                  placeholder="e.g. code review, data analysis, research, summarization"
                  className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                  style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                />
                <p className="text-xs mt-1" style={{ color: 'var(--neo-text-dim)' }}>
                  Other agents see these skills via A2A discovery to decide who to route tasks to.
                </p>
              </div>

              <div className="flex gap-2 pt-1">
                <button
                  onClick={handleRegister}
                  disabled={registering || !newName.trim()}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
                  style={{ background: 'var(--neo-green)', color: '#fff' }}
                >
                  {registering ? <Loader2 size={14} className="animate-spin" /> : <Key size={14} />}
                  Generate API Key
                </button>
                <button
                  onClick={() => setShowRegister(false)}
                  className="px-3 py-2 rounded-lg text-sm"
                  style={{ color: 'var(--neo-text-muted)' }}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Agent list */}
      {agents.length === 0 ? (
        <div
          className="text-center py-16 rounded-xl"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        >
          <Bot size={32} className="mx-auto mb-3" style={{ color: 'var(--neo-text-muted)' }} />
          <p className="text-sm mb-2" style={{ color: 'var(--neo-text-muted)' }}>
            No agents registered yet.
          </p>
          <p className="text-xs mb-4" style={{ color: 'var(--neo-text-muted)' }}>
            Register an agent to get an API key for the Context API.
          </p>
          <button
            onClick={() => setShowRegister(true)}
            className="px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            Register First Agent
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          {agents.map((a) => (
            <div key={a.agent_id}>
              <div
                className="flex items-center justify-between p-4 rounded-xl transition"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
              >
                <div className="flex items-center gap-3 flex-1 min-w-0">
                  <div
                    className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0"
                    style={{ background: 'rgba(0,210,255,0.1)' }}
                  >
                    <Bot size={18} style={{ color: 'var(--neo-cyan)' }} />
                  </div>
                  <div className="min-w-0">
                    <div className="font-medium text-sm" style={{ color: 'var(--neo-text)' }}>
                      {a.name || a.agent_id}
                    </div>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className="text-xs font-mono" style={{ color: 'var(--neo-text-muted)' }}>
                        {a.agent_id.slice(0, 12)}…
                      </span>
                      <span
                        className="px-1.5 py-0.5 rounded text-xs"
                        style={{ background: 'rgba(0,210,255,0.1)', color: 'var(--neo-cyan)' }}
                      >
                        {a.role || 'agent'}
                      </span>
                      {(() => {
                        const pl = PLATFORM_OPTIONS.find((p) => p.value === (a.platform || 'app'));
                        if (!pl) return null;
                        const PlIcon = pl.icon;
                        return (
                          <span
                            className="flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs"
                            style={{ background: `${pl.color}15`, color: pl.color }}
                          >
                            <PlIcon size={10} />
                            {pl.label}
                          </span>
                        );
                      })()}
                      {a.created_at && (
                        <span className="text-xs flex items-center gap-1" style={{ color: 'var(--neo-text-muted)' }}>
                          Created {new Date(a.created_at).toLocaleDateString()} {new Date(a.created_at).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}
                        </span>
                      )}
                      {a.last_seen ? (() => {
                        const ago = Date.now() - new Date(a.last_seen).getTime();
                        const mins = Math.floor(ago / 60000);
                        const isActive = mins < 5;
                        const timeAgo = mins < 1 ? 'just now' : mins < 60 ? `${mins}m ago` : mins < 1440 ? `${Math.floor(mins/60)}h ago` : `${Math.floor(mins/1440)}d ago`;
                        return (
                          <span className="text-xs flex items-center gap-1" style={{ color: isActive ? 'var(--neo-green)' : 'var(--neo-text-muted)' }}>
                            <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: isActive ? 'var(--neo-green)' : 'var(--neo-text-muted)' }} />
                            {isActive ? 'Active' : `Last seen ${timeAgo}`}
                          </span>
                        );
                      })() : (
                        <span className="text-xs flex items-center gap-1" style={{ color: 'var(--neo-text-muted)' }}>
                          <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: 'var(--neo-text-muted)' }} />
                          Never connected
                        </span>
                      )}
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  <div className="hidden sm:flex gap-1">
                    {(a.capabilities || []).slice(0, 3).map((cap) => (
                      <span
                        key={cap}
                        className="px-1.5 py-0.5 rounded text-xs"
                        style={{ background: 'var(--neo-bg)', color: 'var(--neo-text-muted)' }}
                      >
                        {cap}
                      </span>
                    ))}
                  </div>

                  <span
                    className="px-2 py-0.5 rounded text-xs font-medium"
                    style={{
                      background: a.status === 'active' ? 'rgba(76,217,100,0.15)' : 'rgba(242,87,87,0.15)',
                      color: a.status === 'active' ? 'var(--neo-green)' : '#ef4444',
                    }}
                  >
                    {a.status}
                  </span>

                  <button
                    onClick={() => toggleActivity(a.agent_id)}
                    className="p-1.5 rounded-lg transition hover:opacity-80"
                    style={{ color: 'var(--neo-text-muted)' }}
                    title="View activity"
                  >
                    {expandedAgent === a.agent_id ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                  </button>

                  <button
                    onClick={() => handleDelete(a.agent_id, a.name)}
                    className="p-1.5 rounded-lg transition hover:opacity-80"
                    style={{ color: 'var(--neo-text-muted)' }}
                    title="Deregister agent"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>

              {/* Activity panel */}
              {expandedAgent === a.agent_id && (
                <div
                  className="mx-4 mb-1 p-4 rounded-b-xl -mt-1"
                  style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', borderTop: 'none' }}
                >
                  <div className="flex items-center gap-2 mb-3">
                    <Eye size={14} style={{ color: 'var(--neo-text-muted)' }} />
                    <span className="text-xs font-semibold" style={{ color: 'var(--neo-text-muted)' }}>
                      Recent Activity
                    </span>
                  </div>

                  <ActivityFeed
                    activity={activity[a.agent_id] || []}
                    loading={activityLoading[a.agent_id]}
                    maxItems={10}
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* How agents connect — interactive onboarding */}
      <AgentOnboarding
        sampleAgentId={agents.length > 0 ? agents[0].agent_id : 'your-agent-id'}
      />
    </div>
  );
}

/* ─── Agent Onboarding Walkthrough ─── */

const ONBOARDING_STEPS = [
  {
    title: '1. Register an Agent',
    desc: 'Create an agent from the panel above or via the API. Save the API key — it\'s shown only once.',
    icon: Key,
    lang: 'curl',
  },
  {
    title: '2. Connect via MCP or SDK',
    desc: 'Use MCP (for Claude/Copilot) or the Python SDK to connect your agent to a context boundary.',
    icon: Terminal,
    lang: 'python',
  },
  {
    title: '3. Query & Collaborate',
    desc: 'Search knowledge, claim tasks, write code, and collaborate with other agents through the shared graph.',
    icon: Code2,
    lang: 'python',
  },
];

function AgentOnboarding({ sampleAgentId }) {
  const [activeStep, setActiveStep] = useState(0);
  const [codeCopied, setCodeCopied] = useState(false);
  const [langTab, setLangTab] = useState('python'); // python | curl

  const baseUrl = window.location.origin;
  const agentId = sampleAgentId || 'your-agent-id';

  const codeSnippets = {
    0: {
      curl: `# Register a new agent
curl -X POST ${baseUrl}/dashboard/agents \\
  -H "Content-Type: application/json" \\
  -H "X-Admin-Key: YOUR_ADMIN_KEY" \\
  -d '{
    "name": "my-agent",
    "role": "developer",
    "platform": "app",
    "capabilities": ["read", "write"]
  }'

# Response: { "agent_id": "abc123...", "api_key": "abc123:secret..." }
# Save the api_key — it is shown only once!`,
      python: `# Register via the dashboard UI (recommended)
# Or via the REST API:
import requests

res = requests.post(
    "${baseUrl}/dashboard/agents",
    headers={"X-Admin-Key": "YOUR_ADMIN_KEY"},
    json={
        "name": "my-agent",
        "role": "developer",
        "platform": "app",
        "capabilities": ["read", "write"],
    }
)
print(res.json()["api_key"])  # Save this!`,
    },
    1: {
      curl: `# MCP — add to Claude Code settings.json:
{
  "mcpServers": {
    "contextsynapse": {
      "command": "python",
      "args": [
        "-m", "contextsynapse.mcp.server",
        "--api-key", "${agentId}:YOUR_SECRET",
        "--session", "YOUR_SESSION_ID"
      ],
      "cwd": "/path/to/contextsynapse"
    }
  }
}`,
      python: `from contextsynapse.sdk import Session

# Connect to a context boundary
session = Session(
    server_url="${baseUrl}",
    api_key="${agentId}:YOUR_SECRET",
    session_id="YOUR_SESSION_ID"
)

# Get your briefing (tasks, context, decisions)
print(session.briefing())

# Claim and work on a task
tasks = session.tasks.mine()
session.tasks.claim(tasks[0]["id"])`,
    },
    2: {
      python: `# Search across all attached contexts
result = session.ask("What are the requirements?")
print(result)

# Add knowledge to the shared graph
session.graph.add_knowledge(
    name="API Design Decision",
    content="Using REST + WebSocket for real-time updates",
    node_type="Decision"
)

# Complete a task
session.tasks.complete(
    task_id="abc123",
    summary="Built auth module with OAuth2"
)

# Works with any LLM — OpenAI, Claude, Groq, Ollama
# The context boundary provides the shared brain`,
      curl: `# Search the knowledge graph
curl -X POST ${baseUrl}/agent/search \\
  -H "Authorization: Bearer ${agentId}:YOUR_SECRET" \\
  -d '{"query": "authentication requirements", "limit": 10}'

# Get your tasks
curl ${baseUrl}/agent/tasks/mine \\
  -H "Authorization: Bearer ${agentId}:YOUR_SECRET"

# Complete a task
curl -X POST ${baseUrl}/agent/tasks/TASK_ID/complete \\
  -H "Authorization: Bearer ${agentId}:YOUR_SECRET" \\
  -d '{"summary": "Built the login page"}'`,
    },
  };

  const copyCode = () => {
    navigator.clipboard.writeText(codeSnippets[activeStep][langTab]);
    setCodeCopied(true);
    setTimeout(() => setCodeCopied(false), 2000);
  };

  return (
    <div
      className="mt-8 p-5 rounded-xl"
      style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
    >
      <h3 className="text-sm font-semibold mb-4" style={{ color: 'var(--neo-text)' }}>
        Connect Your Agent
      </h3>

      {/* Step tabs */}
      <div className="flex gap-2 mb-4">
        {ONBOARDING_STEPS.map((step, i) => {
          const Icon = step.icon;
          return (
            <button
              key={i}
              onClick={() => { setActiveStep(i); setCodeCopied(false); }}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition"
              style={{
                background: activeStep === i ? 'rgba(0,210,255,0.15)' : 'transparent',
                color: activeStep === i ? 'var(--neo-cyan)' : 'var(--neo-text-muted)',
                border: `1px solid ${activeStep === i ? 'var(--neo-cyan)' : 'var(--neo-border)'}`,
              }}
            >
              <Icon size={12} />
              {step.title}
            </button>
          );
        })}
      </div>

      {/* Active step */}
      <div>
        <p className="text-xs mb-3" style={{ color: 'var(--neo-text-muted)' }}>
          {ONBOARDING_STEPS[activeStep].desc}
        </p>

        {/* Language toggle + copy */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex gap-1">
            {['python', 'curl'].map((lang) => (
              <button
                key={lang}
                onClick={() => { setLangTab(lang); setCodeCopied(false); }}
                className="px-2 py-0.5 rounded text-xs transition"
                style={{
                  background: langTab === lang ? 'var(--neo-bg)' : 'transparent',
                  color: langTab === lang ? 'var(--neo-text)' : 'var(--neo-text-muted)',
                  border: `1px solid ${langTab === lang ? 'var(--neo-border)' : 'transparent'}`,
                }}
              >
                {lang === 'python' ? 'Python' : 'cURL'}
              </button>
            ))}
          </div>
          <button
            onClick={copyCode}
            className="flex items-center gap-1 px-2 py-0.5 rounded text-xs transition hover:opacity-80"
            style={{ color: 'var(--neo-text-muted)' }}
          >
            {codeCopied ? <Check size={10} /> : <Copy size={10} />}
            {codeCopied ? 'Copied' : 'Copy'}
          </button>
        </div>

        {/* Code block */}
        <pre
          className="p-4 rounded-lg text-xs overflow-x-auto"
          style={{
            background: 'var(--neo-bg)',
            border: '1px solid var(--neo-border)',
            color: 'var(--neo-green)',
            fontFamily: "'JetBrains Mono', monospace",
            lineHeight: 1.6,
            maxHeight: 260,
          }}
        >
          {codeSnippets[activeStep][langTab]}
        </pre>

        {/* Step navigation */}
        <div className="flex justify-between mt-3">
          <button
            onClick={() => setActiveStep(Math.max(0, activeStep - 1))}
            disabled={activeStep === 0}
            className="px-3 py-1 rounded-lg text-xs transition disabled:opacity-30"
            style={{ color: 'var(--neo-text-muted)', border: '1px solid var(--neo-border)' }}
          >
            Previous
          </button>
          <button
            onClick={() => setActiveStep(Math.min(2, activeStep + 1))}
            disabled={activeStep === 2}
            className="px-3 py-1 rounded-lg text-xs transition disabled:opacity-30"
            style={{ color: 'var(--neo-cyan)', border: '1px solid var(--neo-cyan)' }}
          >
            Next Step
          </button>
        </div>
      </div>
    </div>
  );
}

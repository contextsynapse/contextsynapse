import React, { useState, useEffect, useCallback } from 'react';
import {
  Layers, Loader2, Plus, Trash2, RefreshCw, ChevronDown, ChevronUp,
  UserPlus, X, Users, Clock, FileText, Database, Link, Copy, Check,
  GitMerge, Shield, Eye, Lock, Unlock, AlertTriangle, Search, Tag,
  Play, Bot, Square, Download, History,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { useLocation } from 'react-router-dom';
import api from '../../lib/api';
import ActivityFeed from '../../components/ActivityFeed';
import ContextPreview from '../../components/ContextPreview';
import ContextLineage from '../../components/ContextLineage';
import LiveFeed from '../../components/LiveFeed';
import ConversationPanel from '../../components/ConversationPanel';
import AgentActivityFeed from '../../components/AgentActivityFeed';
import AgentTheater from '../../components/AgentTheater';
import DashboardGraphExplorer from '../../components/DashboardGraphExplorer';

// Uniform time formatter — always UTC, consistent format
function fmtTime(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  if (isNaN(d.getTime())) return isoStr;
  const mo = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getUTCMonth()];
  const day = d.getUTCDate();
  const hr = String(d.getUTCHours()).padStart(2, '0');
  const min = String(d.getUTCMinutes()).padStart(2, '0');
  return `${mo} ${day}, ${hr}:${min} UTC`;
}

const SENSITIVITY_LEVELS = [
  { value: 'public', label: 'Public', color: '#22c55e', icon: Unlock },
  { value: 'internal', label: 'Internal', color: '#3b82f6', icon: Eye },
  { value: 'confidential', label: 'Confidential', color: '#f59e0b', icon: Lock },
  { value: 'restricted', label: 'Restricted', color: '#ef4444', icon: AlertTriangle },
];

const DEPARTMENTS = [
  'dept:engineering', 'dept:product', 'dept:sales', 'dept:marketing',
  'dept:finance', 'dept:legal', 'dept:hr', 'dept:operations',
  'dept:security', 'dept:data-science', 'dept:support', 'dept:executive',
];

const LEVEL_STYLES = {
  read: { bg: 'rgba(150,150,150,0.15)', color: 'var(--neo-text-muted)' },
  write: { bg: 'rgba(0,122,255,0.15)', color: 'var(--neo-blue)' },
  admin: { bg: 'rgba(175,82,222,0.15)', color: '#af52de' },
};

export default function SessionsPage() {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [expanded, setExpanded] = useState(null);
  const [activeTab, setActiveTab] = useState({});
  const [intelData, setIntelData] = useState({});
  const [intelLoading, setIntelLoading] = useState({});

  // Agents list for dropdowns
  const [agents, setAgents] = useState([]);

  // Create form
  const [newName, setNewName] = useState('');
  const [newOwner, setNewOwner] = useState('');
  const [newGoal, setNewGoal] = useState('');
  const [newContextId, setNewContextId] = useState('');
  const [contexts, setContexts] = useState([]);
  const [creating, setCreating] = useState(false);
  const [copiedId, setCopiedId] = useState(null);

  // Integrations for dropdowns
  const [liveIntegrations, setLiveIntegrations] = useState([]);
  const [newGitIntId, setNewGitIntId] = useState('');
  const [templates, setTemplates] = useState([]);
  const [selectedTemplate, setSelectedTemplate] = useState(null);
  const [newJiraIntId, setNewJiraIntId] = useState('');

  // Members + activity caches
  const [members, setMembers] = useState({});
  const [activity, setActivity] = useState({});
  const [membersLoading, setMembersLoading] = useState({});
  const [activityLoading, setActivityLoading] = useState({});

  // Assigned agents per session
  const [assignedAgents, setAssignedAgents] = useState({});
  const [assignedLoading, setAssignedLoading] = useState({});
  const [assignAgentId, setAssignAgentId] = useState('');

  // Add member form
  const [addAgentId, setAddAgentId] = useState('');
  const [addLevel, setAddLevel] = useState('read');

  // Agent LLM provider/model config per session
  const [agentConfigs, setAgentConfigs] = useState({}); // {agentId: {provider, model, role}}
  const [runningSession, setRunningSession] = useState(null);
  const [runResult, setRunResult] = useState(null);

  // Time filter
  const [timeFilter, setTimeFilter] = useState('all'); // all, 24h, 7d, 30d
  const [searchFilter, setSearchFilter] = useState('');

  // Context tab
  const [graphs, setGraphs] = useState([]);
  const [contextData, setContextData] = useState({});
  const [contextLoading, setContextLoading] = useState({});
  const [ctxText, setCtxText] = useState('');
  const [ctxLabel, setCtxLabel] = useState('');
  const [ctxBusy, setCtxBusy] = useState(false);

  // Search & Attach state
  const [attachQuery, setAttachQuery] = useState('');
  const [attachMode, setAttachMode] = useState('hybrid');
  const [attachGraph, setAttachGraph] = useState('');
  const [attachResults, setAttachResults] = useState([]);
  const [attachSearching, setAttachSearching] = useState(false);
  const [attachSelected, setAttachSelected] = useState(new Set());
  const [attachBusy, setAttachBusy] = useState(false);
  const [attachTags, setAttachTags] = useState('');

  // Enhanced context builder state
  const [composeSessions, setComposeSessions] = useState([]);
  const [lineageKey, setLineageKey] = useState(0); // force refresh
  const [showSecurity, setShowSecurity] = useState(false);
  const [secSensitivity, setSecSensitivity] = useState('');
  const [secDepts, setSecDepts] = useState([]);
  const [secBusy, setSecBusy] = useState(false);

  const fetchSessions = useCallback(async () => {
    try {
      const res = await api.get('/dashboard/sessions');
      const list = res.data.sessions || [];
      setSessions(list);
    } catch (err) {
      console.error('[Sessions] fetch failed:', err?.response?.status, err?.response?.data || err?.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchAgents = useCallback(() => {
    api.get('/dashboard/agents')
      .then((res) => setAgents(res.data.agents || []))
      .catch(() => {});
  }, []);

  const fetchContexts = useCallback(() => {
    api.get('/dashboard/contexts')
      .then((res) => setContexts(res.data.contexts || []))
      .catch(() => {});
  }, []);

  const fetchGraphs = useCallback(() => {
    api.get('/dashboard/graphs')
      .then((res) => setGraphs(res.data.graphs || []))
      .catch(() => {});
  }, []);

  const location = useLocation();

  const fetchIntegrations = useCallback(() => {
    api.get('/dashboard/integrations')
      .then((res) => setLiveIntegrations(res.data.integrations || []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetchSessions();
    fetchAgents();
    fetchGraphs();
    fetchContexts();
    fetchIntegrations();
    api.get('/dashboard/templates').then(r => setTemplates(r.data.templates || [])).catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- fire once on mount, callbacks are stable

  // Auto-expand session + tab when navigated from ContextPage
  useEffect(() => {
    const state = location.state;
    if (state?.openSession && !loading) {
      setExpanded(state.openSession);
      setActiveTab((p) => ({ ...p, [state.openSession]: state.tab || 'context' }));
      if (state.tab === 'context') loadContext(state.openSession);
      // Clear navigation state so refresh doesn't re-trigger
      window.history.replaceState({}, '');
    }
  }, [location.state, loading]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      let res;
      if (selectedTemplate) {
        // Create from template — auto-creates context + boundary
        res = await api.post(`/dashboard/templates/${selectedTemplate.template_id}/instantiate`, {
          name: newName.trim(),
        });
        toast.success(`"${newName}" created from ${selectedTemplate.name} template`);
      } else {
        const payload = {
          name: newName.trim(),
          agent_id: newOwner || null,
        };
        if (newContextId) payload.context_id = newContextId;
        if (newGoal.trim()) payload.goal = newGoal.trim();
        if (newGitIntId) payload.workspace_integration_id = newGitIntId;
        if (newJiraIntId) payload.jira_integration_id = newJiraIntId;

        res = await api.post('/dashboard/sessions', payload);
        toast.success(`Boundary "${newName}" created`);
      }
      setNewName(''); setNewOwner(''); setNewGoal(''); setNewContextId('');
      setNewGitIntId(''); setNewJiraIntId(''); setSelectedTemplate(null);
      setShowCreate(false);
      await fetchSessions();
    } catch (err) {
      const detail = err?.response?.data?.detail || err?.message || 'Failed to create boundary';
      console.error('[Sessions] create FAILED:', detail);
      toast.error(detail);
    } finally {
      setCreating(false);
    }
  };

  const copySessionId = (sid) => {
    navigator.clipboard.writeText(sid);
    setCopiedId(sid);
    toast.success('Session ID copied');
    setTimeout(() => setCopiedId(null), 2000);
  };

  const rotateSessionId = async (sid) => {
    if (!window.confirm('Rotate session ID? All connected agents will be disconnected and need the new ID.')) return;
    try {
      const res = await api.post(`/dashboard/sessions/${sid}/rotate`);
      const newId = res.data.new_session_id;
      toast.success(`Session ID rotated. New ID: ${newId}`);
      navigator.clipboard.writeText(newId);
      fetchSessions();
    } catch (err) {
      toast.error('Failed to rotate session ID');
    }
  };

  const handleDelete = async (sessionId, name) => {
    if (!window.confirm(`Delete session "${name}"? This cannot be undone.`)) return;
    try {
      await api.delete(`/dashboard/sessions/${sessionId}`);
      toast.success('Session deleted');
      if (expanded === sessionId) setExpanded(null);
      fetchSessions();
    } catch {}
  };

  const toggleExpand = async (sessionId) => {
    if (expanded === sessionId) {
      setExpanded(null);
      return;
    }
    setExpanded(sessionId);
    const tab = activeTab[sessionId] || 'overview';
    if (tab === 'overview') { loadMembers(sessionId); loadActivity(sessionId); loadAssignedAgents(sessionId); }
    else if (tab === 'context') loadContext(sessionId);
  };

  const switchTab = (sessionId, tab) => {
    setActiveTab((p) => ({ ...p, [sessionId]: tab }));
    if (tab === 'overview') {
      if (!members[sessionId]) loadMembers(sessionId);
      if (!activity[sessionId]) loadActivity(sessionId);
      if (!assignedAgents[sessionId]) loadAssignedAgents(sessionId);
    }
    if (tab === 'context' && !contextData[sessionId]) loadContext(sessionId);
    if (tab === 'intelligence' && !intelData[sessionId]) loadIntelligence(sessionId);
  };

  const loadIntelligence = async (sessionId) => {
    setIntelLoading((p) => ({ ...p, [sessionId]: true }));
    try {
      const res = await api.get(`/dashboard/sessions/${sessionId}/context`);
      setIntelData((p) => ({ ...p, [sessionId]: res.data }));
    } catch {
      setIntelData((p) => ({ ...p, [sessionId]: null }));
    } finally {
      setIntelLoading((p) => ({ ...p, [sessionId]: false }));
    }
  };

  const loadMembers = async (sessionId) => {
    if (members[sessionId]) return;
    setMembersLoading((p) => ({ ...p, [sessionId]: true }));
    try {
      const res = await api.get(`/dashboard/sessions/${sessionId}/members`);
      setMembers((p) => ({ ...p, [sessionId]: res.data.members || [] }));
    } catch {
      setMembers((p) => ({ ...p, [sessionId]: [] }));
    } finally {
      setMembersLoading((p) => ({ ...p, [sessionId]: false }));
    }
  };

  const loadActivity = async (sessionId) => {
    if (activity[sessionId]) return;
    setActivityLoading((p) => ({ ...p, [sessionId]: true }));
    try {
      const res = await api.get(`/dashboard/sessions/${sessionId}/activity`);
      setActivity((p) => ({ ...p, [sessionId]: res.data.activity || [] }));
    } catch {
      setActivity((p) => ({ ...p, [sessionId]: [] }));
    } finally {
      setActivityLoading((p) => ({ ...p, [sessionId]: false }));
    }
  };

  const handleGrantAccess = async (sessionId, agentIdOverride, levelOverride) => {
    const agId = agentIdOverride || addAgentId;
    const lvl = levelOverride || addLevel;
    if (!agId) return;
    try {
      await api.post(`/dashboard/sessions/${sessionId}/members`, {
        agent_id: agId,
        level: lvl,
      });
      toast.success('Access granted');
      setAddAgentId('');
      setAddLevel('read');
      // Refresh members
      setMembers((p) => ({ ...p, [sessionId]: null }));
      loadMembers(sessionId);
    } catch {}
  };

  const handleRevoke = async (sessionId, agentId) => {
    try {
      await api.delete(`/dashboard/sessions/${sessionId}/members/${agentId}`);
      toast.success('Access revoked');
      setMembers((p) => ({ ...p, [sessionId]: null }));
      loadMembers(sessionId);
      fetchSessions(); // update member count
    } catch {}
  };

  const loadAssignedAgents = async (sessionId) => {
    setAssignedLoading((p) => ({ ...p, [sessionId]: true }));
    try {
      const res = await api.get(`/dashboard/sessions/${sessionId}/agents`);
      setAssignedAgents((p) => ({ ...p, [sessionId]: res.data.agents || [] }));
    } catch {
      setAssignedAgents((p) => ({ ...p, [sessionId]: [] }));
    } finally {
      setAssignedLoading((p) => ({ ...p, [sessionId]: false }));
    }
  };

  const handleAssignAgent = async (sessionId, agentId) => {
    if (!agentId) return;
    try {
      await api.post(`/dashboard/sessions/${sessionId}/agents/${agentId}`);
      toast.success('Agent assigned to session');
      setAssignAgentId('');
      setAssignedAgents((p) => ({ ...p, [sessionId]: null }));
      loadAssignedAgents(sessionId);
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to assign agent');
    }
  };

  const handleUnassignAgent = async (sessionId, agentId) => {
    try {
      await api.delete(`/dashboard/sessions/${sessionId}/agents/${agentId}`);
      toast.success('Agent removed from session');
      setAssignedAgents((p) => ({ ...p, [sessionId]: null }));
      loadAssignedAgents(sessionId);
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to unassign agent');
    }
  };

  const loadContext = async (sessionId) => {
    setContextLoading((p) => ({ ...p, [sessionId]: true }));
    try {
      const res = await api.get(`/dashboard/sessions/${sessionId}/context`);
      setContextData((p) => ({ ...p, [sessionId]: res.data }));
    } catch {
      setContextData((p) => ({ ...p, [sessionId]: { items: [], count: 0, estimated_tokens: 0 } }));
    } finally {
      setContextLoading((p) => ({ ...p, [sessionId]: false }));
    }
  };

  const handleAddText = async (sessionId) => {
    if (!ctxText.trim()) return;
    setCtxBusy(true);
    try {
      const res = await api.post(`/dashboard/sessions/${sessionId}/context/text`, {
        text: ctxText.trim(),
        label: ctxLabel.trim() || 'Manual context',
      });
      toast.success(`Added ${res.data.nodes_created} node(s) to session`);
      setCtxText('');
      setCtxLabel('');
      setContextData((p) => ({ ...p, [sessionId]: null }));
      loadContext(sessionId);
      setLineageKey((k) => k + 1);
    } catch {} finally {
      setCtxBusy(false);
    }
  };

  const handleAttachSearch = async () => {
    if (!attachQuery.trim()) return;
    setAttachSearching(true);
    setAttachSelected(new Set());
    try {
      const body = { query: attachQuery.trim(), mode: attachMode, limit: 50 };
      if (attachGraph) body.graph = attachGraph;
      const res = await api.post('/dashboard/search', body);
      setAttachResults(res.data.results || []);
    } catch {
      setAttachResults([]);
    } finally {
      setAttachSearching(false);
    }
  };

  const toggleAttachSelect = (nodeId) => {
    setAttachSelected((prev) => {
      const next = new Set(prev);
      if (next.has(nodeId)) next.delete(nodeId);
      else next.add(nodeId);
      return next;
    });
  };

  const handleAttachSelected = async (sessionId) => {
    if (attachSelected.size === 0) return;
    setAttachBusy(true);
    try {
      // Group selected nodes by graph
      const byGraph = {};
      attachResults.forEach((r) => {
        if (attachSelected.has(r.node_id)) {
          const g = r.graph || 'default';
          if (!byGraph[g]) byGraph[g] = [];
          byGraph[g].push(r.node_id);
        }
      });

      let totalCopied = 0;
      for (const [graph, nodeIds] of Object.entries(byGraph)) {
        const res = await api.post(`/dashboard/sessions/${sessionId}/context/from-graph`, {
          graph,
          node_ids: nodeIds,
        });
        totalCopied += res.data.copied || 0;
      }

      toast.success(`Attached ${totalCopied} node(s) to session`);
      setAttachSelected(new Set());
      setAttachResults([]);
      setAttachQuery('');
      setContextData((p) => ({ ...p, [sessionId]: null }));
      loadContext(sessionId);
      setLineageKey((k) => k + 1);
    } catch {} finally {
      setAttachBusy(false);
    }
  };

  const handleCompose = async (sessionId) => {
    if (composeSessions.length === 0) return;
    setCtxBusy(true);
    try {
      const res = await api.post(`/dashboard/sessions/${sessionId}/context/compose`, {
        source_session_ids: composeSessions,
      });
      const msg = `Merged ${res.data.nodes_merged} nodes from ${(res.data.source_sessions || []).join(', ')}. ` +
        `${res.data.duplicates_removed} duplicates removed.` +
        (res.data.sensitivity_escalated ? ` ${res.data.sensitivity_escalated} sensitivity escalated.` : '');
      toast.success(msg);
      setComposeSessions([]);
      setContextData((p) => ({ ...p, [sessionId]: null }));
      loadContext(sessionId);
      setLineageKey((k) => k + 1);
    } catch {} finally {
      setCtxBusy(false);
    }
  };

  const handleApplySecurity = async (sessionId) => {
    setSecBusy(true);
    try {
      const res = await api.post(`/dashboard/sessions/${sessionId}/context/security`, {
        default_sensitivity: secSensitivity || null,
        department_tags: secDepts,
      });
      toast.success(`Updated ${res.data.updated} items`);
      setContextData((p) => ({ ...p, [sessionId]: null }));
      loadContext(sessionId);
    } catch {} finally {
      setSecBusy(false);
    }
  };

  // Apply time + search filters
  const filteredSessions = sessions.filter((s) => {
    // Time filter
    if (timeFilter !== 'all') {
      const created = new Date(s.created_at);
      const now = Date.now();
      const ms = { '24h': 86400000, '7d': 604800000, '30d': 2592000000 }[timeFilter];
      if (ms && now - created.getTime() > ms) return false;
    }
    // Search filter
    if (searchFilter) {
      const q = searchFilter.toLowerCase();
      const name = (s.name || '').toLowerCase();
      const owner = (s.owner_agent_name || '').toLowerCase();
      if (!name.includes(q) && !owner.includes(q)) return false;
    }
    return true;
  });

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
          <h1 className="text-xl font-bold" style={{ color: 'var(--neo-text)' }}>Context Runtimes</h1>
          <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
            Execution scopes where agents work — attach contexts, assign agents, track progress
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => { fetchSessions(); fetchAgents(); }}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition hover:opacity-80"
            style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
          >
            <RefreshCw size={14} /> Refresh
          </button>
          <button
            onClick={() => setShowCreate(!showCreate)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition hover:opacity-90"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            <Plus size={14} /> New Boundary
          </button>
        </div>
      </div>

      {/* Create panel */}
      {showCreate && (
        <div
          className="mb-6 p-5 rounded-xl"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        >
          <h3 className="text-sm font-semibold mb-3" style={{ color: 'var(--neo-text)' }}>
            Create Boundary
          </h3>

          {/* Template quick-start */}
          {templates.length > 0 && !selectedTemplate && (
            <div className="mb-4">
              <label className="block text-xs mb-2 font-medium" style={{ color: 'var(--neo-text-muted)' }}>
                Start from a template (optional)
              </label>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
                {templates.filter(t => t.builtin).map((t) => {
                  const icons = { code: '💻', search: '🔍', users: '👥', globe: '🌐', book: '📚', database: '🗄️', shield: '🛡️' };
                  return (
                    <button
                      key={t.template_id}
                      onClick={() => {
                        setSelectedTemplate(t);
                        setNewName(t.name);
                        setNewGoal(t.config?.goal || '');
                      }}
                      className="p-3 rounded-lg text-left transition hover:scale-[1.02]"
                      style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)' }}
                    >
                      <div className="text-lg mb-1">{icons[t.icon] || '📦'}</div>
                      <div className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>{t.name}</div>
                      <div className="text-[10px] mt-0.5 line-clamp-2" style={{ color: 'var(--neo-text-dim)' }}>
                        {t.description?.slice(0, 60)}
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {selectedTemplate && (
            <div className="mb-3 flex items-center gap-2 px-3 py-2 rounded-lg"
              style={{ background: 'rgba(99,102,241,0.1)', border: '1px solid #6366f1' }}>
              <span className="text-xs font-medium" style={{ color: '#6366f1' }}>
                Template: {selectedTemplate.name}
              </span>
              <button onClick={() => { setSelectedTemplate(null); setNewName(''); setNewGoal(''); }}
                className="ml-auto text-xs" style={{ color: '#6366f1' }}>
                <X size={14} />
              </button>
            </div>
          )}

          <div className="space-y-3">
            <div>
              <label className="block text-xs mb-1" style={{ color: 'var(--neo-text-muted)' }}>
                Session Name
              </label>
              <input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="e.g. research-q4, customer-analysis"
                className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
              />
            </div>
            {/* Context attachment moved to boundary detail view — attach/detach after creation */}
            <div>
              <label className="block text-xs mb-1" style={{ color: 'var(--neo-text-muted)' }}>
                Goal (what agents should work on)
              </label>
              <input
                value={newGoal}
                onChange={(e) => setNewGoal(e.target.value)}
                placeholder="e.g. Build the auth module with OAuth2"
                className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
              />
            </div>
            {/* Agents connect via API key + session ID at runtime — not pre-assigned */}
            {/* Integrations — simple dropdowns from live connections */}
            <div className="flex gap-3">
              <div className="flex-1">
                <label className="block text-xs mb-1" style={{ color: 'var(--neo-text-muted)' }}>
                  Git Workspace (optional)
                </label>
                <select
                  value={newGitIntId}
                  onChange={(e) => setNewGitIntId(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                  style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                >
                  <option value="">Local workspace (default)</option>
                  {liveIntegrations
                    .filter((i) => (i.connector_type === 'git' || i.connector_type === 'github') && i.status === 'active')
                    .map((i) => (
                      <option key={i.integration_id} value={i.integration_id}>
                        {i.name} ({i.config?.repo_url?.split('/').pop()?.replace('.git', '') || i.connector_type})
                      </option>
                    ))}
                </select>
              </div>
              <div className="flex-1">
                <label className="block text-xs mb-1" style={{ color: 'var(--neo-text-muted)' }}>
                  Jira (optional)
                </label>
                <select
                  value={newJiraIntId}
                  onChange={(e) => setNewJiraIntId(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                  style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                >
                  <option value="">No Jira</option>
                  {liveIntegrations
                    .filter((i) => i.connector_type === 'jira' && i.status === 'active')
                    .map((i) => (
                      <option key={i.integration_id} value={i.integration_id}>
                        {i.name} ({i.config?.domain || i.config?.projects || 'configured'})
                      </option>
                    ))}
                </select>
              </div>
            </div>
            {(liveIntegrations.filter(i => ['git','github','jira'].includes(i.connector_type) && i.status === 'active').length === 0) && (
              <p className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
                No active integrations — add Git or Jira connections on the Integrations page first.
              </p>
            )}
            <div className="flex gap-2 pt-1">
              <button
                onClick={handleCreate}
                disabled={creating || !newName.trim()}
                className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
                style={{ background: 'var(--neo-green)', color: '#fff' }}
              >
                {creating ? <Loader2 size={14} className="animate-spin" /> : <Layers size={14} />}
                Create
              </button>
              <button
                onClick={() => setShowCreate(false)}
                className="px-3 py-2 rounded-lg text-sm"
                style={{ color: 'var(--neo-text-muted)' }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Filter bar */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <div className="flex items-center gap-1 p-1 rounded-lg" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
          {[
            { key: 'all', label: 'All' },
            { key: '24h', label: '24h' },
            { key: '7d', label: '7 days' },
            { key: '30d', label: '30 days' },
          ].map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setTimeFilter(key)}
              className="px-3 py-1 rounded-md text-xs font-medium transition"
              style={{
                background: timeFilter === key ? 'var(--neo-blue)' : 'transparent',
                color: timeFilter === key ? '#fff' : 'var(--neo-text-muted)',
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="relative flex-1 max-w-xs">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: 'var(--neo-text-muted)' }} />
          <input
            value={searchFilter}
            onChange={(e) => setSearchFilter(e.target.value)}
            placeholder="Filter by name..."
            className="w-full pl-8 pr-3 py-1.5 rounded-lg text-xs"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          />
        </div>
        <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
          {filteredSessions.length} of {sessions.length}
        </span>
      </div>

      {/* Session list */}
      {filteredSessions.length === 0 ? (
        <div
          className="text-center py-16 rounded-xl"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        >
          <Layers size={32} className="mx-auto mb-3" style={{ color: 'var(--neo-text-muted)' }} />
          <p className="text-sm mb-2" style={{ color: 'var(--neo-text-muted)' }}>
            No boundaries yet.
          </p>
          <p className="text-xs mb-4" style={{ color: 'var(--neo-text-muted)' }}>
            Create a boundary to assemble contexts and let agents execute work.
          </p>
          <button
            onClick={() => setShowCreate(true)}
            className="px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            Create First Session
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          {filteredSessions.map((s) => {
            const isExpanded = expanded === s.session_id;
            const tab = activeTab[s.session_id] || 'overview';

            return (
              <div key={s.session_id}>
                <div
                  className="flex items-center justify-between p-4 rounded-xl transition"
                  style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
                >
                  <div className="flex items-center gap-3 flex-1 min-w-0">
                    <div
                      className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0"
                      style={{ background: 'rgba(175,82,222,0.1)' }}
                    >
                      <Layers size={18} style={{ color: '#af52de' }} />
                    </div>
                    <div className="min-w-0">
                      <div className="font-medium text-sm" style={{ color: 'var(--neo-text)' }}>
                        {s.name}
                      </div>
                      <div className="flex items-center gap-2 mt-0.5">
                        {s.owner_agent_name && (
                          <span
                            className="px-1.5 py-0.5 rounded text-xs"
                            style={{ background: 'rgba(0,210,255,0.1)', color: 'var(--neo-cyan)' }}
                          >
                            {s.owner_agent_name}
                          </span>
                        )}
                        <span className="text-xs flex items-center gap-1" style={{ color: 'var(--neo-text-muted)' }}>
                          <Users size={10} />
                          {s.members && s.members.length > 0
                            ? s.members.map(m => m.name || m.agent_id).join(', ')
                            : `${s.member_count || 0} members`}
                        </span>
                        <span className="text-xs flex items-center gap-1" style={{ color: 'var(--neo-text-muted)' }}>
                          <Clock size={10} />
                          {fmtTime(s.updated_at || s.created_at)}
                        </span>
                        {s.config?.version > 0 && (
                          <span className="text-xs px-1.5 py-0.5 rounded font-medium"
                            style={{ background: 'rgba(99,102,241,0.1)', color: '#6366f1' }}>
                            v{s.config.version}
                          </span>
                        )}
                        {s.config?.goal && (
                          <span className="text-xs" style={{ color: '#10b981' }}>
                            {s.config.goal.length > 40 ? s.config.goal.slice(0, 40) + '...' : s.config.goal}
                          </span>
                        )}
                        {s.config?.workspace && (
                          <span className="text-xs flex items-center gap-1 px-1.5 py-0.5 rounded"
                            style={{ background: 'rgba(139,92,246,0.1)', color: '#8b5cf6' }}
                            title={s.config.workspace.path || s.config.workspace.repo_url || ''}
                          >
                            {s.config.workspace.type === 'git' ? 'Git' : s.config.workspace.type === 'github' ? 'GitHub' : 'Local'}
                            {s.config.workspace.repo_url ? ` ${s.config.workspace.repo_url.split('/').pop()?.replace('.git','')}` : ''}
                            {s.config.workspace.path && !s.config.workspace.repo_url ? ` ${s.config.workspace.path.split(/[/\\]/).pop()}` : ''}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      onClick={(e) => { e.stopPropagation(); copySessionId(s.session_id); }}
                      className="flex items-center gap-1 px-2 py-1 rounded text-xs transition hover:opacity-80"
                      style={{ background: 'rgba(0,122,255,0.1)', color: 'var(--neo-blue)' }}
                      title={`Copy Session ID: ${s.session_id}`}
                    >
                      {copiedId === s.session_id ? <Check size={12} /> : <Copy size={12} />}
                      {s.session_id.slice(0, 8)}
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); rotateSessionId(s.session_id); }}
                      className="flex items-center gap-1 px-2 py-1 rounded text-xs transition hover:opacity-80"
                      style={{ background: 'rgba(255,159,10,0.1)', color: '#f59e0b' }}
                      title="Rotate Session ID (disconnects agents)"
                    >
                      <RefreshCw size={12} />
                    </button>
                    <span
                      className="px-2 py-0.5 rounded text-xs font-medium"
                      style={{
                        background: s.status === 'active' ? 'rgba(76,217,100,0.15)' : 'rgba(242,87,87,0.15)',
                        color: s.status === 'active' ? 'var(--neo-green)' : '#ef4444',
                      }}
                    >
                      {s.status}
                    </span>
                    <button
                      onClick={() => toggleExpand(s.session_id)}
                      className="p-1.5 rounded-lg transition hover:opacity-80"
                      style={{ color: 'var(--neo-text-muted)' }}
                    >
                      {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                    </button>
                    <button
                      onClick={() => handleDelete(s.session_id, s.name)}
                      className="p-1.5 rounded-lg transition hover:opacity-80"
                      style={{ color: 'var(--neo-text-muted)' }}
                      title="Delete session"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>

                {/* Expanded panel */}
                {isExpanded && (
                  <div
                    className="mx-4 mb-1 p-4 rounded-b-xl -mt-1"
                    style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', borderTop: 'none' }}
                  >
                    {/* Tab switcher */}
                    <div className="flex gap-1 mb-4">
                      {['overview', 'context', 'activity', 'workspace', 'security', 'more'].map((t) => {
                        const labels = {
                          overview: 'Overview',
                          intelligence: 'Intelligence',
                          context: 'Context',
                          activity: 'Agent Activity',
                          workspace: 'Workspace',
                          security: 'Security',
                          more: 'More',
                        };
                        return (
                          <button
                            key={t}
                            onClick={() => switchTab(s.session_id, t)}
                            className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-t transition"
                            style={{
                              background: tab === t ? 'var(--neo-blue)' : 'transparent',
                              color: tab === t ? '#fff' : 'var(--neo-text-muted)',
                            }}
                          >
                            {labels[t]}
                          </button>
                        );
                      })}
                    </div>

                    {/* ── Overview tab: Members + Activity ── */}
                    {tab === 'overview' && (
                      <div className="space-y-4">
                        {/* Members section */}
                        <div>
                          <h4 className="text-xs font-semibold mb-2" style={{ color: 'var(--neo-text)' }}>Members</h4>
                          {membersLoading[s.session_id] ? (
                            <div className="flex items-center justify-center py-4">
                              <Loader2 size={16} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
                            </div>
                          ) : (
                            <>
                              {(members[s.session_id] || []).length === 0 ? (
                                <p className="text-xs py-3 text-center" style={{ color: 'var(--neo-text-muted)' }}>
                                  No agents have access yet
                                </p>
                              ) : (
                                <div className="space-y-1.5 mb-4">
                                  {(members[s.session_id] || []).map((m, i) => {
                                    const ls = LEVEL_STYLES[m.access_level] || LEVEL_STYLES.read;
                                    return (
                                      <div key={m.agent_id || i} className="rounded-lg" style={{ background: 'var(--neo-surface)' }}>
                                        <div className="flex items-center justify-between px-3 py-2">
                                          <div className="flex items-center gap-2">
                                            <span className="text-sm" style={{ color: 'var(--neo-text)' }}>
                                              {m.agent_name || m.agent_id}
                                            </span>
                                            {m.agent_role && (
                                              <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                                                ({m.agent_role})
                                              </span>
                                            )}
                                            <span
                                              className="px-1.5 py-0.5 rounded text-xs font-medium"
                                              style={{ background: ls.bg, color: ls.color }}
                                            >
                                              {m.access_level}
                                            </span>
                                          </div>
                                          <button
                                            onClick={() => handleRevoke(s.session_id, m.agent_id)}
                                            className="p-1 rounded transition hover:opacity-80"
                                            style={{ color: 'var(--neo-text-muted)' }}
                                            title="Revoke access"
                                          >
                                            <X size={14} />
                                          </button>
                                        </div>
                                      {m.access_level === 'write' && (
                                        <div className="flex items-center gap-1.5 mt-1.5 px-3">
                                          <select
                                            value={(agentConfigs[m.agent_id] || {}).provider || 'anthropic'}
                                            onChange={(e) => setAgentConfigs(prev => ({
                                              ...prev,
                                              [m.agent_id]: { ...(prev[m.agent_id] || {}), provider: e.target.value }
                                            }))}
                                            className="px-1.5 py-0.5 rounded text-xs outline-none"
                                            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                                          >
                                            <option value="anthropic">Anthropic</option>
                                            <option value="openai">OpenAI</option>
                                            <option value="groq">Groq</option>
                                            <option value="deepseek">DeepSeek</option>
                                            <option value="together">Together</option>
                                            <option value="mistral">Mistral</option>
                                            <option value="ollama">Ollama</option>
                                          </select>
                                          <input
                                            value={(agentConfigs[m.agent_id] || {}).model || ''}
                                            onChange={(e) => setAgentConfigs(prev => ({
                                              ...prev,
                                              [m.agent_id]: { ...(prev[m.agent_id] || {}), model: e.target.value }
                                            }))}
                                            placeholder="model (auto)"
                                            className="flex-1 px-1.5 py-0.5 rounded text-xs outline-none"
                                            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)', maxWidth: 140 }}
                                          />
                                          <select
                                            value={(agentConfigs[m.agent_id] || {}).role || 'developer'}
                                            onChange={(e) => setAgentConfigs(prev => ({
                                              ...prev,
                                              [m.agent_id]: { ...(prev[m.agent_id] || {}), role: e.target.value }
                                            }))}
                                            className="px-1.5 py-0.5 rounded text-xs outline-none"
                                            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                                          >
                                            <option value="architect">Architect</option>
                                            <option value="developer">Developer</option>
                                          </select>
                                        </div>
                                      )}
                                      </div>
                                    );
                                  })}
                                </div>
                              )}

                              {/* Run Session button */}
                              {(members[s.session_id] || []).some(m => m.access_level === 'write') && (
                                <div className="mb-3 flex items-center gap-2">
                                  <button
                                    onClick={async () => {
                                      const writeAgents = (members[s.session_id] || []).filter(m => m.access_level === 'write');
                                      const agentPayload = writeAgents.map(m => ({
                                        id: m.agent_id,
                                        role: (agentConfigs[m.agent_id] || {}).role || 'developer',
                                        provider: (agentConfigs[m.agent_id] || {}).provider || 'anthropic',
                                        model: (agentConfigs[m.agent_id] || {}).model || '',
                                      }));
                                      setRunningSession(s.session_id);
                                      setRunResult(null);
                                      try {
                                        const res = await api.post(`/dashboard/sessions/${s.session_id}/run-agents`, { agents: agentPayload });
                                        toast.success(`Session run started: ${res.data.run_id}`);
                                        // Poll for result
                                        const poll = setInterval(async () => {
                                          try {
                                            const r = await api.get(`/dashboard/sessions/${s.session_id}/runs/${res.data.run_id}`);
                                            if (r.data.status !== 'running') {
                                              clearInterval(poll);
                                              setRunningSession(null);
                                              setRunResult(r.data);
                                              if (r.data.status === 'completed') toast.success('Session run complete!');
                                              else toast.error(`Run failed: ${r.data.error || 'unknown'}`);
                                            }
                                          } catch {}
                                        }, 3000);
                                      } catch (e) {
                                        setRunningSession(null);
                                        toast.error('Failed to start session run');
                                      }
                                    }}
                                    disabled={runningSession === s.session_id}
                                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90 disabled:opacity-50"
                                    style={{ background: 'var(--neo-green)', color: '#000' }}
                                  >
                                    {runningSession === s.session_id ? (
                                      <><Loader2 size={12} className="animate-spin" /> Running...</>
                                    ) : (
                                      <><Play size={12} /> Run Agents</>
                                    )}
                                  </button>
                                  {runResult && runResult.session_id === s.session_id && runResult.status === 'completed' && (
                                    <span className="text-xs" style={{ color: 'var(--neo-green)' }}>
                                      Coverage: {Math.round((runResult.before_coverage?.overall || 0) * 100)}% → {Math.round((runResult.after_coverage?.overall || 0) * 100)}% ({runResult.elapsed_ms}ms)
                                    </span>
                                  )}
                                </div>
                              )}

                              {/* Add member — searchable agent picker */}
                              <AgentSearchGrant
                                agents={agents}
                                existingIds={(members[s.session_id] || []).map((m) => m.agent_id)}
                                onGrant={(agentId, level) => {
                                  setAddAgentId(agentId);
                                  setAddLevel(level);
                                  handleGrantAccess(s.session_id, agentId, level);
                                }}
                              />

                              {/* External agent invites removed — use Assigned Agents instead */}
                            </>
                          )}
                        </div>

                        {/* Agent assignment managed from Agents tab */}

                        {/* Activity section */}
                        <div>
                          <h4 className="text-xs font-semibold mb-2" style={{ color: 'var(--neo-text)' }}>Activity</h4>
                          {activityLoading[s.session_id] ? (
                            <div className="flex items-center justify-center py-4">
                              <Loader2 size={16} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
                            </div>
                          ) : (activity[s.session_id] || []).length === 0 ? (
                            <p className="text-xs py-3 text-center" style={{ color: 'var(--neo-text-muted)' }}>
                              No activity yet
                            </p>
                          ) : (
                            <ActivityFeed items={activity[s.session_id]} />
                          )}
                        </div>
                      </div>
                    )}

                    {/* ── Intelligence tab: What agents see ── */}
                    {tab === 'intelligence' && (
                      <div className="space-y-4">
                        {intelLoading[s.session_id] ? (
                          <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>Loading intelligence...</div>
                        ) : (() => {
                          const intel = intelData[s.session_id];
                          if (!intel) return <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>No intelligence data. Run a pipeline first.</div>;
                          const manifest = intel.manifest || intel;
                          const quality = manifest.context_quality || intel.context_quality || 0;
                          const contentType = manifest.content_type || intel.content_type || 'mixed';
                          const stats = manifest.stats || {};
                          const topEntities = manifest.top_entities || intel.top_entities || {};
                          const themes = manifest.themes || intel.themes || [];
                          const hints = manifest.tool_hints || intel.tool_hints || [];

                          return (
                            <>
                              {/* Quality Score Bar */}
                              <div className="p-3 rounded-lg" style={{ background: 'var(--neo-card-bg)', border: '1px solid var(--neo-border)' }}>
                                <div className="flex items-center justify-between mb-2">
                                  <span className="text-xs font-semibold" style={{ color: 'var(--neo-text)' }}>Context Quality</span>
                                  <span className="text-lg font-bold" style={{ color: quality >= 70 ? '#34d399' : quality >= 40 ? '#fbbf24' : '#f87171' }}>{quality}/100</span>
                                </div>
                                <div className="w-full h-2 rounded-full" style={{ background: 'var(--neo-border)' }}>
                                  <div className="h-full rounded-full transition-all" style={{ width: `${quality}%`, background: quality >= 70 ? '#34d399' : quality >= 40 ? '#fbbf24' : '#f87171' }} />
                                </div>
                                <div className="flex justify-between mt-1">
                                  <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>Type: {contentType}</span>
                                  <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                                    {stats.total_nodes || intel.count || 0} nodes ({stats.high_quality || 0} high, {stats.low_quality || 0} low)
                                  </span>
                                </div>
                              </div>

                              {/* Top Entities */}
                              {Object.keys(topEntities).length > 0 && (
                                <div className="p-3 rounded-lg" style={{ background: 'var(--neo-card-bg)', border: '1px solid var(--neo-border)' }}>
                                  <h4 className="text-xs font-semibold mb-2" style={{ color: 'var(--neo-text)' }}>Top Entities (what agents see)</h4>
                                  <div className="grid grid-cols-2 gap-3">
                                    {Object.entries(topEntities).map(([label, entities]) => (
                                      <div key={label}>
                                        <div className="text-[10px] font-semibold mb-1" style={{ color: 'var(--neo-cyan)' }}>{label}</div>
                                        {(entities || []).slice(0, 5).map((e, i) => (
                                          <div key={i} className="flex justify-between text-[11px] py-0.5" style={{ color: 'var(--neo-text-muted)' }}>
                                            <span style={{ color: 'var(--neo-text)' }}>{e.name}</span>
                                            <span>{e.mentions} mentions</span>
                                          </div>
                                        ))}
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              )}

                              {/* Themes */}
                              {themes.length > 0 && (
                                <div className="p-3 rounded-lg" style={{ background: 'var(--neo-card-bg)', border: '1px solid var(--neo-border)' }}>
                                  <h4 className="text-xs font-semibold mb-2" style={{ color: 'var(--neo-text)' }}>Themes</h4>
                                  {themes.map((t, i) => (
                                    <div key={i} className="mb-2">
                                      <div className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>{t.name}</div>
                                      <div className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                                        {t.fact_count || 0} facts {t.key_actors ? `| ${t.key_actors.join(', ')}` : ''}
                                      </div>
                                      {t.sample_fact && <div className="text-[10px] italic mt-0.5" style={{ color: 'var(--neo-text-muted)' }}>"{t.sample_fact}"</div>}
                                    </div>
                                  ))}
                                </div>
                              )}

                              {/* Tool Hints */}
                              {hints.length > 0 && (
                                <div className="p-3 rounded-lg" style={{ background: 'var(--neo-card-bg)', border: '1px solid var(--neo-border)' }}>
                                  <h4 className="text-xs font-semibold mb-2" style={{ color: 'var(--neo-text)' }}>Available Queries (tool hints for agents)</h4>
                                  {hints.map((h, i) => (
                                    <div key={i} className="flex justify-between text-[11px] py-0.5 font-mono" style={{ color: 'var(--neo-text-muted)' }}>
                                      <span style={{ color: 'var(--neo-cyan)' }}>{h.tool}({h.example})</span>
                                      <span>{h.result_count ? `${h.result_count} results` : h.description || ''}</span>
                                    </div>
                                  ))}
                                </div>
                              )}

                              {/* Raw manifest text (what agent literally sees) */}
                              <details className="text-xs">
                                <summary className="cursor-pointer font-semibold py-1" style={{ color: 'var(--neo-text-muted)' }}>Raw agent briefing (what LLM receives)</summary>
                                <pre className="mt-2 p-3 rounded-lg text-[10px] whitespace-pre-wrap" style={{ background: '#0d1117', color: '#c9d1d9', maxHeight: 300, overflow: 'auto' }}>
                                  {manifest.updated_at ? JSON.stringify(manifest, null, 2) : 'No manifest cached'}
                                </pre>
                              </details>
                            </>
                          );
                        })()}
                      </div>
                    )}

                    {/* ── Context tab: Attached + Search + Graph + Context Builder ── */}
                    {tab === 'context' && (
                      <div className="space-y-4">
                        {/* Attached Contexts */}
                        <div>
                          <h4 className="text-xs font-semibold mb-2" style={{ color: 'var(--neo-text)' }}>Attached Contexts</h4>
                          <AttachedContextsPanel sessionId={s.session_id} />
                        </div>

                        {/* Search & Attach */}
                        <div>
                          <h4 className="text-xs font-semibold mb-2" style={{ color: 'var(--neo-text)' }}>Search & Attach</h4>
                          <BoundarySearchPanel sessionId={s.session_id} graphNamespace={s.graph_namespace} />
                        </div>

                        {/* Graph Explorer */}
                        <div>
                          <h4 className="text-xs font-semibold mb-2" style={{ color: 'var(--neo-text)' }}>Graph Explorer</h4>
                          <div style={{ height: 500 }}>
                            <DashboardGraphExplorer graphName={s.graph_namespace} embedded />
                          </div>
                        </div>

                        {/* Context Builder (lineage, summary, search & attach, compose, overlays, preview) */}
                        <div>
                          <h4 className="text-xs font-semibold mb-2" style={{ color: 'var(--neo-text)' }}>Context Builder</h4>
                          <div className="space-y-3">
                            {/* Lineage breadcrumb */}
                            <ContextLineage sessionId={s.session_id} key={`lineage-${lineageKey}`} />

                            {/* Context summary bar */}
                            {contextLoading[s.session_id] ? (
                              <div className="flex items-center justify-center py-4">
                                <Loader2 size={16} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
                              </div>
                            ) : (
                              <div
                                className="flex items-center justify-between px-3 py-2 rounded-lg"
                                style={{ background: 'var(--neo-surface)' }}
                              >
                                <div className="flex items-center gap-3">
                                  <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                                    {contextData[s.session_id]?.count || 0} items
                                  </span>
                                  <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                                    ~{contextData[s.session_id]?.estimated_tokens || 0} tokens
                                  </span>
                                </div>
                                <button
                                  onClick={() => {
                                    setContextData((p) => ({ ...p, [s.session_id]: null }));
                                    loadContext(s.session_id);
                                    setLineageKey((k) => k + 1);
                                  }}
                                  className="text-xs px-2 py-1 rounded transition hover:opacity-80"
                                  style={{ color: 'var(--neo-text-muted)' }}
                                >
                                  <RefreshCw size={12} />
                                </button>
                              </div>
                            )}

                            {/* Search & Attach (inline) */}
                            <div
                              className="p-3 rounded-lg"
                              style={{ border: '1px solid var(--neo-border)' }}
                            >
                              <div className="flex items-center gap-2 mb-2">
                                <Search size={14} style={{ color: 'var(--neo-blue)' }} />
                                <span className="text-xs font-semibold" style={{ color: 'var(--neo-text)' }}>
                                  Search & Attach
                                </span>
                                <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                                  Find nodes across graphs and attach to this session
                                </span>
                              </div>
                              {/* Search bar */}
                              <div className="flex items-center gap-2 mb-2">
                                <input
                                  value={attachQuery}
                                  onChange={(e) => setAttachQuery(e.target.value)}
                                  onKeyDown={(e) => e.key === 'Enter' && handleAttachSearch()}
                                  placeholder="Search nodes..."
                                  className="flex-1 px-2 py-1.5 rounded-lg text-xs outline-none"
                                  style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                                />
                                <select
                                  value={attachMode}
                                  onChange={(e) => setAttachMode(e.target.value)}
                                  className="px-2 py-1.5 rounded-lg text-xs outline-none"
                                  style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                                >
                                  <option value="keyword">Keyword</option>
                                  <option value="semantic">Semantic</option>
                                  <option value="hybrid">Hybrid</option>
                                </select>
                                {graphs.length > 0 && (
                                  <select
                                    value={attachGraph}
                                    onChange={(e) => setAttachGraph(e.target.value)}
                                    className="px-2 py-1.5 rounded-lg text-xs outline-none"
                                    style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                                  >
                                    <option value="">All graphs</option>
                                    {graphs.map((g) => (
                                      <option key={g.name} value={g.display_name || g.name}>{g.display_name || g.name}</option>
                                    ))}
                                  </select>
                                )}
                                <button
                                  onClick={handleAttachSearch}
                                  disabled={attachSearching || !attachQuery.trim()}
                                  className="px-3 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90 disabled:opacity-50 shrink-0"
                                  style={{ background: 'var(--neo-blue)', color: '#fff' }}
                                >
                                  {attachSearching ? <Loader2 size={12} className="animate-spin" /> : 'Search'}
                                </button>
                              </div>

                              {/* Results */}
                              {attachResults.length > 0 && (
                                <div>
                                  <div
                                    className="max-h-48 overflow-y-auto space-y-1 mb-2 rounded-lg p-1"
                                    style={{ background: 'var(--neo-surface)' }}
                                  >
                                    {attachResults.map((r) => (
                                      <label
                                        key={r.node_id}
                                        className="flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer transition hover:opacity-80"
                                        style={{ background: attachSelected.has(r.node_id) ? 'rgba(0,122,255,0.08)' : 'transparent' }}
                                      >
                                        <input
                                          type="checkbox"
                                          checked={attachSelected.has(r.node_id)}
                                          onChange={() => toggleAttachSelect(r.node_id)}
                                          style={{ accentColor: 'var(--neo-blue)' }}
                                        />
                                        <div className="flex-1 min-w-0">
                                          <div className="flex items-center gap-1.5">
                                            <span className="text-xs font-medium truncate" style={{ color: 'var(--neo-text)' }}>
                                              {r.label || r.node_id}
                                            </span>
                                            <span
                                              className="px-1 py-0.5 rounded text-xs shrink-0"
                                              style={{ background: 'rgba(175,82,222,0.1)', color: '#af52de', fontSize: '10px' }}
                                            >
                                              {r.node_type}
                                            </span>
                                            {r.graph && (
                                              <span
                                                className="px-1 py-0.5 rounded text-xs shrink-0"
                                                style={{ background: 'rgba(0,210,255,0.1)', color: 'var(--neo-cyan)', fontSize: '10px' }}
                                              >
                                                {r.graph}
                                              </span>
                                            )}
                                          </div>
                                          {r.snippet && (
                                            <div className="text-xs truncate mt-0.5" style={{ color: 'var(--neo-text-muted)', fontSize: '10px' }}>
                                              {r.snippet}
                                            </div>
                                          )}
                                        </div>
                                        <span className="text-xs shrink-0" style={{ color: 'var(--neo-text-muted)' }}>
                                          {Math.round(r.score * 100)}%
                                        </span>
                                      </label>
                                    ))}
                                  </div>
                                  {/* Attach bar */}
                                  <div className="flex items-center gap-2">
                                    <button
                                      onClick={() => {
                                        if (attachSelected.size === attachResults.length) setAttachSelected(new Set());
                                        else setAttachSelected(new Set(attachResults.map((r) => r.node_id)));
                                      }}
                                      className="text-xs px-2 py-1 rounded transition hover:opacity-80"
                                      style={{ color: 'var(--neo-text-muted)', border: '1px solid var(--neo-border)' }}
                                    >
                                      {attachSelected.size === attachResults.length ? 'Deselect all' : 'Select all'}
                                    </button>
                                    <div className="flex-1 flex items-center gap-1.5">
                                      <Tag size={10} style={{ color: 'var(--neo-text-muted)' }} />
                                      <input
                                        value={attachTags}
                                        onChange={(e) => setAttachTags(e.target.value)}
                                        placeholder="Tags (comma-separated)"
                                        className="flex-1 px-2 py-1 rounded text-xs outline-none"
                                        style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                                      />
                                    </div>
                                    <button
                                      onClick={() => handleAttachSelected(s.session_id)}
                                      disabled={attachBusy || attachSelected.size === 0}
                                      className="px-3 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90 disabled:opacity-50"
                                      style={{ background: 'var(--neo-green)', color: '#fff' }}
                                    >
                                      {attachBusy
                                        ? <Loader2 size={12} className="animate-spin" />
                                        : `Attach ${attachSelected.size} node(s)`}
                                    </button>
                                  </div>
                                </div>
                              )}
                              {attachResults.length === 0 && attachQuery && !attachSearching && (
                                <div className="text-xs py-2 text-center" style={{ color: 'var(--neo-text-muted)' }}>
                                  No results found. Try different keywords or select a different graph.
                                </div>
                              )}
                            </div>

                            {/* Compose from Sessions */}
                            {sessions.length > 1 && (
                              <div
                                className="p-3 rounded-lg"
                                style={{ border: '1px solid var(--neo-border)' }}
                              >
                                <div className="flex items-center gap-2 mb-2">
                                  <GitMerge size={14} style={{ color: 'var(--neo-cyan)' }} />
                                  <span className="text-xs font-semibold" style={{ color: 'var(--neo-text)' }}>
                                    Compose from Sessions
                                  </span>
                                  <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                                    Merge context (dedup + sensitivity escalation)
                                  </span>
                                </div>
                                <div className="space-y-1.5 mb-2">
                                  {sessions.filter((x) => x.session_id !== s.session_id).map((x) => (
                                    <label
                                      key={x.session_id}
                                      className="flex items-center gap-2 px-2 py-1 rounded cursor-pointer transition hover:opacity-80"
                                      style={{ background: composeSessions.includes(x.session_id) ? 'rgba(0,210,255,0.08)' : 'transparent' }}
                                    >
                                      <input
                                        type="checkbox"
                                        checked={composeSessions.includes(x.session_id)}
                                        onChange={(e) => {
                                          if (e.target.checked) setComposeSessions((p) => [...p, x.session_id]);
                                          else setComposeSessions((p) => p.filter((id) => id !== x.session_id));
                                        }}
                                        style={{ accentColor: 'var(--neo-cyan)' }}
                                      />
                                      <span className="text-xs" style={{ color: 'var(--neo-text)' }}>{x.name}</span>
                                      <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                                        ({x.member_count || 0} members)
                                      </span>
                                    </label>
                                  ))}
                                </div>
                                {composeSessions.length > 0 && (
                                  <button
                                    onClick={() => handleCompose(s.session_id)}
                                    disabled={ctxBusy}
                                    className="px-3 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90 disabled:opacity-50"
                                    style={{ background: 'var(--neo-cyan)', color: '#000' }}
                                  >
                                    {ctxBusy ? <Loader2 size={12} className="animate-spin" /> : `Compose ${composeSessions.length} session(s)`}
                                  </button>
                                )}
                              </div>
                            )}

                            {/* Overlays — text/instructions */}
                            <div
                              className="p-3 rounded-lg"
                              style={{ border: '1px solid var(--neo-border)' }}
                            >
                              <div className="flex items-center gap-2 mb-2">
                                <FileText size={14} style={{ color: 'var(--neo-green)' }} />
                                <span className="text-xs font-semibold" style={{ color: 'var(--neo-text)' }}>
                                  Add Overlay
                                </span>
                                <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                                  Instructions, system prompts, manual context
                                </span>
                              </div>
                              <input
                                value={ctxLabel}
                                onChange={(e) => setCtxLabel(e.target.value)}
                                placeholder="Label (e.g. Q4 Revenue Data)"
                                className="w-full px-2 py-1.5 rounded-lg text-xs outline-none mb-2"
                                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                              />
                              <textarea
                                value={ctxText}
                                onChange={(e) => setCtxText(e.target.value)}
                                placeholder="Paste text, data, or instructions to add as context..."
                                rows={3}
                                className="w-full px-2 py-1.5 rounded-lg text-xs outline-none resize-none mb-2"
                                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                              />
                              <button
                                onClick={() => handleAddText(s.session_id)}
                                disabled={ctxBusy || !ctxText.trim()}
                                className="px-3 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90 disabled:opacity-50"
                                style={{ background: 'var(--neo-green)', color: '#fff' }}
                              >
                                {ctxBusy ? <Loader2 size={12} className="animate-spin" /> : 'Add Overlay'}
                              </button>
                            </div>

                            {/* Context preview — what agents see */}
                            <ContextPreview sessionId={s.session_id} />
                          </div>
                        </div>
                      </div>
                    )}

                    {/* ── Workspace tab: Integrations + Run History ── */}
                    {tab === 'activity' && (
                      <AgentActivityFeed graphName={s.graph_namespace} sessionId={s.session_id} />
                    )}

                    {tab === 'workspace' && (
                      <div className="space-y-4">
                        <div>
                          <h4 className="text-xs font-semibold mb-2" style={{ color: 'var(--neo-text)' }}>Integrations</h4>
                          <IntegrationsPanel sessionId={s.session_id} config={s.config || {}} onUpdate={fetchSessions} />
                        </div>
                        <div>
                          <h4 className="text-xs font-semibold mb-2" style={{ color: 'var(--neo-text)' }}>Run History</h4>
                          <RunHistoryPanel sessionId={s.session_id} />
                        </div>
                      </div>
                    )}

                    {/* ── Security tab: Security settings + Join Requests ── */}
                    {tab === 'security' && (
                      <div className="space-y-4">
                        {/* Security & Tags */}
                        <div
                          className="rounded-lg overflow-hidden"
                          style={{ border: '1px solid var(--neo-border)' }}
                        >
                          <button
                            onClick={() => setShowSecurity(!showSecurity)}
                            className="w-full flex items-center gap-2 px-3 py-2 text-left transition hover:opacity-90"
                            style={{ background: 'var(--neo-surface)' }}
                          >
                            {showSecurity ? <ChevronDown size={12} /> : <ChevronUp size={12} style={{ transform: 'rotate(180deg)' }} />}
                            <Shield size={14} style={{ color: '#f59e0b' }} />
                            <span className="text-xs font-semibold" style={{ color: 'var(--neo-text)' }}>
                              Security & Department Tags
                            </span>
                          </button>
                          {showSecurity && (
                            <div className="p-3 space-y-3" style={{ borderTop: '1px solid var(--neo-border)' }}>
                              {/* Default sensitivity */}
                              <div>
                                <span className="text-xs font-medium block mb-1.5" style={{ color: 'var(--neo-text)' }}>
                                  Default Sensitivity
                                </span>
                                <div className="grid grid-cols-2 gap-1.5">
                                  {SENSITIVITY_LEVELS.map((sl) => {
                                    const Icon = sl.icon;
                                    const active = secSensitivity === sl.value;
                                    return (
                                      <button
                                        key={sl.value}
                                        onClick={() => setSecSensitivity(active ? '' : sl.value)}
                                        className="flex items-center gap-1.5 px-2 py-1.5 rounded-lg text-xs transition"
                                        style={{
                                          border: `1px solid ${active ? sl.color : 'var(--neo-border)'}`,
                                          background: active ? `${sl.color}15` : 'transparent',
                                          color: active ? sl.color : 'var(--neo-text-muted)',
                                        }}
                                      >
                                        <Icon size={12} /> {sl.label}
                                      </button>
                                    );
                                  })}
                                </div>
                              </div>

                              {/* Department tags */}
                              <div>
                                <span className="text-xs font-medium block mb-1.5" style={{ color: 'var(--neo-text)' }}>
                                  Department Tags
                                </span>
                                <div className="flex flex-wrap gap-1">
                                  {DEPARTMENTS.map((dept) => {
                                    const label = dept.replace('dept:', '');
                                    const active = secDepts.includes(dept);
                                    return (
                                      <button
                                        key={dept}
                                        onClick={() => {
                                          if (active) setSecDepts((p) => p.filter((d) => d !== dept));
                                          else setSecDepts((p) => [...p, dept]);
                                        }}
                                        className="px-2 py-0.5 rounded text-xs transition"
                                        style={{
                                          border: '1px solid var(--neo-border)',
                                          background: active ? 'rgba(0,122,255,0.15)' : 'transparent',
                                          color: active ? 'var(--neo-blue)' : 'var(--neo-text-muted)',
                                        }}
                                      >
                                        {label}
                                      </button>
                                    );
                                  })}
                                </div>
                              </div>

                              <button
                                onClick={() => handleApplySecurity(s.session_id)}
                                disabled={secBusy || (!secSensitivity && secDepts.length === 0)}
                                className="px-3 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90 disabled:opacity-50"
                                style={{ background: '#f59e0b', color: '#000' }}
                              >
                                {secBusy ? <Loader2 size={12} className="animate-spin" /> : 'Apply to All Items'}
                              </button>
                            </div>
                          )}
                        </div>

                        {/* Join Requests */}
                        <div>
                          <h4 className="text-xs font-semibold mb-2" style={{ color: 'var(--neo-text)' }}>Join Requests</h4>
                          <JoinRequestsPanel sessionId={s.session_id} />
                        </div>
                      </div>
                    )}

                    {/* ── More tab: Snapshots, Agents, Schedule ── */}
                    {tab === 'more' && (
                      <div className="space-y-4">
                        <details open>
                          <summary className="text-xs font-medium cursor-pointer py-1" style={{ color: 'var(--neo-text-muted)' }}>Snapshots</summary>
                          <div className="mt-2">
                            <SnapshotPanel sessionId={s.session_id} />
                          </div>
                        </details>
                        <details>
                          <summary className="text-xs font-medium cursor-pointer py-1" style={{ color: 'var(--neo-text-muted)' }}>Agents</summary>
                          <div className="mt-2">
                            <AgentRunPanel sessionId={s.session_id} />
                          </div>
                        </details>
                        <details>
                          <summary className="text-xs font-medium cursor-pointer py-1" style={{ color: 'var(--neo-text-muted)' }}>Schedule</summary>
                          <div className="mt-2">
                            <p className="text-xs py-3 text-center" style={{ color: 'var(--neo-text-muted)' }}>
                              No scheduled runs configured
                            </p>
                          </div>
                        </details>
                        <details>
                          <summary className="text-xs font-medium cursor-pointer py-1" style={{ color: 'var(--neo-text-muted)' }}>Experiments</summary>
                          <div className="mt-2">
                            <ExperimentLivePanel sessionId={s.session_id} sessionName={s.name} />
                          </div>
                        </details>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ─── Run History Panel ─── */

function RunEventRow({ ev, verbose = false }) {
  const [expanded, setExpanded] = React.useState(false);
  const showFull = verbose || expanded;

  if (ev.type === 'roster') {
    return (
      <div className="py-1 text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
        <span className="font-bold">Roster:</span> Lead: {ev.lead}
        {ev.workers?.length > 0 && ` | Workers: ${ev.workers.join(', ')}`}
        {ev.agent_ids && verbose && (
          <div className="pl-2 mt-0.5 font-mono" style={{ color: 'var(--neo-text-dim)' }}>
            {Object.entries(ev.agent_ids).map(([name, id]) => (
              <div key={name}>{name} → {id}</div>
            ))}
          </div>
        )}
      </div>
    );
  }
  if (ev.type === 'phase') {
    return (
      <div className="py-1.5 font-bold flex items-center gap-2" style={{ color: '#6366f1' }}>
        [{ev.phase}] {ev.agent}
        {ev.agent_id && <span className="font-normal text-[9px] font-mono" style={{ color: 'var(--neo-text-dim)' }}>{ev.agent_id.slice(0, 12)}</span>}
      </div>
    );
  }
  if (ev.type === 'tool_call') {
    return (
      <div className="py-0.5">
        <div className="flex items-start gap-1">
          <span style={{ color: '#22c55e' }}>{ev.agent}</span>
          {verbose && ev.agent_id && <span className="text-[9px] font-mono" style={{ color: 'var(--neo-text-dim)' }}>({ev.agent_id.slice(0, 12)})</span>}
          <span style={{ color: 'var(--neo-text-muted)' }}> → </span>
          <span className="font-bold" style={{ color: 'var(--neo-blue)' }}>{ev.tool}</span>
          {ev._ts && <span className="ml-auto text-[9px] shrink-0" style={{ color: 'var(--neo-text-dim)' }}>{fmtTime(ev._ts)}</span>}
        </div>
        {ev.args && (
          <pre className="pl-4 mt-0.5 cursor-pointer whitespace-pre-wrap break-all" onClick={() => setExpanded(!expanded)} style={{ color: 'var(--neo-text-dim)' }}>
            {showFull ? ev.args : (ev.args.length > 80 ? ev.args.slice(0, 80) + '... ▸' : ev.args)}
          </pre>
        )}
      </div>
    );
  }
  if (ev.type === 'tool_result') {
    if (!verbose && !expanded && !ev.result) return null;
    return (
      <pre className="py-0.5 pl-4 cursor-pointer whitespace-pre-wrap break-words" onClick={() => setExpanded(!expanded)}
        style={{ color: 'var(--neo-text-muted)', borderLeft: '2px solid var(--neo-border)' }}>
        ← {showFull ? ev.result : (ev.result?.length > 100 ? ev.result.slice(0, 100) + '... ▸' : ev.result)}
      </pre>
    );
  }
  if (ev.type === 'response') {
    return (
      <pre className="py-1 pl-2 cursor-pointer whitespace-pre-wrap" onClick={() => setExpanded(!expanded)}
        style={{ color: 'var(--neo-text)', borderLeft: '2px solid var(--neo-blue)' }}>
        {showFull ? ev.content : (ev.content?.length > 150 ? ev.content.slice(0, 150) + '... ▸' : ev.content)}
      </pre>
    );
  }
  if (ev.type === 'error') {
    return <div className="py-1" style={{ color: '#ef4444' }}>Error: {ev.message}</div>;
  }
  return null;
}

/* ─── Boundary Search Panel ─── */

function BoundarySearchPanel({ sessionId, graphNamespace }) {
  const [query, setQuery] = React.useState('');
  const [results, setResults] = React.useState(null);
  const [searching, setSearching] = React.useState(false);

  const handleSearch = async () => {
    if (!query.trim()) return;
    setSearching(true);
    try {
      // Search the boundary's graph
      const graphRes = await api.post(`/dashboard/graphs/${encodeURIComponent(graphNamespace)}/search`, {
        query: query.trim(), limit: 20,
      }).catch(() => ({ data: { results: [] } }));

      // Also search attached contexts
      const ctxRes = await api.get(`/dashboard/sessions/${sessionId}/members`).catch(() => ({ data: {} }));

      // Get attached context IDs
      let contextResults = [];
      try {
        const attRes = await api.get(`/dashboard/sessions/${sessionId}/contexts`);
        const attachments = attRes.data?.contexts || attRes.data?.attachments || [];
        for (const att of attachments.slice(0, 5)) {
          const ctxId = att.context_id || att;
          try {
            const searchRes = await api.post(`/dashboard/contexts/${ctxId}/search`, {
              query: query.trim(), limit: 5,
            });
            const hits = (searchRes.data?.results || []).map(r => ({
              ...r, _context: att.context_name || att.name || ctxId.slice(0, 12),
            }));
            contextResults.push(...hits);
          } catch {}
        }
      } catch {}

      const allResults = [...(graphRes.data?.results || []), ...contextResults];
      allResults.sort((a, b) => (b.score || 0) - (a.score || 0));
      setResults(allResults.slice(0, 20));
    } catch {
      setResults([]);
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
          placeholder="Search across boundary + attached contexts... e.g. 'authentication requirements'"
          className="flex-1 px-3 py-2 rounded-lg text-sm outline-none"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
        />
        <button
          onClick={handleSearch}
          disabled={searching || !query.trim()}
          className="px-4 py-2 rounded-lg text-sm font-medium transition disabled:opacity-50"
          style={{ background: 'var(--neo-blue)', color: '#fff' }}
        >
          {searching ? <Loader2 size={14} className="animate-spin" /> : 'Search'}
        </button>
      </div>

      {results !== null && (
        <div className="space-y-1">
          {results.length === 0 ? (
            <p className="text-xs py-4 text-center" style={{ color: 'var(--neo-text-muted)' }}>
              No results found. Try different keywords.
            </p>
          ) : (
            <>
              <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                {results.length} results
              </p>
              {results.map((r, i) => (
                <div key={i} className="p-2.5 rounded-lg text-xs" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-medium" style={{ color: 'var(--neo-text)' }}>
                      {r.name || r.title || r.path || (r.content || '').slice(0, 50)}
                    </span>
                    {r.label && (
                      <span className="px-1.5 py-0.5 rounded" style={{ background: 'rgba(139,92,246,0.1)', color: '#8b5cf6', fontSize: 10 }}>
                        {r.label}
                      </span>
                    )}
                    {r._context && (
                      <span className="px-1.5 py-0.5 rounded" style={{ background: 'rgba(59,130,246,0.1)', color: 'var(--neo-blue)', fontSize: 10 }}>
                        {r._context}
                      </span>
                    )}
                    {r.score != null && (
                      <span className="ml-auto" style={{ color: 'var(--neo-text-dim)', fontSize: 10 }}>
                        {typeof r.score === 'number' ? `${(r.score * 100).toFixed(0)}%` : r.score}
                      </span>
                    )}
                  </div>
                  {(r.content || r.snippet || r.description || r.statement) && (
                    <p className="leading-relaxed" style={{ color: 'var(--neo-text-muted)' }}>
                      {(r.content || r.snippet || r.description || r.statement || '').slice(0, 200)}
                    </p>
                  )}
                </div>
              ))}
            </>
          )}
        </div>
      )}

      {results === null && (
        <div className="text-center py-8" style={{ color: 'var(--neo-text-muted)' }}>
          <Search size={24} className="mx-auto mb-2 opacity-50" />
          <p className="text-sm">Search across the execution graph and all attached contexts.</p>
          <p className="text-xs mt-1" style={{ color: 'var(--neo-text-dim)' }}>
            Finds requirements, decisions, tasks, code files, entities, and more.
          </p>
        </div>
      )}
    </div>
  );
}

function RunHistoryPanel({ sessionId }) {
  const [runs, setRuns] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [expandedRun, setExpandedRun] = React.useState(null);
  const [runDetail, setRunDetail] = React.useState(null);
  const [filter, setFilter] = React.useState('');
  const [verbose, setVerbose] = React.useState(false);

  React.useEffect(() => {
    setLoading(true);
    api.get(`/dashboard/sessions/${sessionId}/runs`)
      .then((res) => setRuns(res.data.runs || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [sessionId]);

  const handleExpand = async (runId, newFilter) => {
    const f = newFilter || filter;
    if (expandedRun === runId && !newFilter) {
      setExpandedRun(null);
      setRunDetail(null);
      return;
    }
    setExpandedRun(runId);
    try {
      const params = f ? { filter: f } : {};
      const res = await api.get(`/dashboard/sessions/${sessionId}/runs/${runId}`, { params });
      setRunDetail(res.data);
    } catch {
      setRunDetail(null);
    }
  };

  const handleFilter = (f) => {
    setFilter(f);
    if (expandedRun) handleExpand(expandedRun, f);
  };

  if (loading) {
    return <div className="flex justify-center py-6"><Loader2 size={16} className="animate-spin" style={{ color: 'var(--neo-blue)' }} /></div>;
  }

  if (runs.length === 0) {
    return (
      <div className="text-center py-8" style={{ color: 'var(--neo-text-muted)' }}>
        <History size={24} className="mx-auto mb-2 opacity-50" />
        <p className="text-sm">No pipeline runs yet. Go to the "Run Agent" tab to start one.</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {runs.map((run) => {
        const isExpanded = expandedRun === run.run_id;
        const statusColor = run.status === 'completed' ? '#22c55e' : run.status === 'running' ? 'var(--neo-blue)' : '#ef4444';
        return (
          <div key={run.run_id} className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--neo-border)' }}>
            <button
              onClick={() => handleExpand(run.run_id)}
              className="w-full text-left p-3 flex items-center justify-between transition hover:opacity-90"
              style={{ background: 'var(--neo-surface)' }}
            >
              <div className="flex items-center gap-3">
                <span className="text-xs font-bold px-2 py-0.5 rounded" style={{ background: 'rgba(99,102,241,0.1)', color: '#6366f1' }}>
                  v{run.version}
                </span>
                <div>
                  <div className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>
                    {run.prompt?.length > 60 ? run.prompt.slice(0, 60) + '...' : run.prompt}
                  </div>
                  <div className="text-[10px] flex items-center gap-2 mt-0.5" style={{ color: 'var(--neo-text-dim)' }}>
                    <span>{fmtTime(run.started_at)}</span>
                    <span>Lead: {run.lead}</span>
                    {run.workers?.length > 0 && <span>Workers: {run.workers.join(', ')}</span>}
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className="px-1.5 py-0.5 rounded text-[10px] font-medium" style={{ background: `${statusColor}15`, color: statusColor }}>
                  {run.status}
                </span>
                {run.completed_at && (
                  <span className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
                    {Math.round((new Date(run.completed_at) - new Date(run.started_at)) / 1000)}s
                  </span>
                )}
              </div>
            </button>

            {isExpanded && runDetail && (
              <div style={{ borderTop: '1px solid var(--neo-border)' }}>
                {/* Filter buttons */}
                <div className="flex gap-1 p-2" style={{ background: 'var(--neo-surface)' }}>
                  {[
                    { key: '', label: 'All', color: 'var(--neo-text-muted)' },
                    { key: 'graph', label: 'Graph Queries', color: '#8b5cf6' },
                    { key: 'tasks', label: 'Tasks', color: '#f59e0b' },
                    { key: 'git', label: 'Files & Git', color: '#22c55e' },
                  ].map((f) => (
                    <button
                      key={f.key}
                      onClick={() => handleFilter(f.key)}
                      className="px-2 py-0.5 rounded text-[10px] font-medium transition"
                      style={{
                        background: filter === f.key ? `${f.color}20` : 'transparent',
                        color: filter === f.key ? f.color : 'var(--neo-text-dim)',
                        border: `1px solid ${filter === f.key ? f.color : 'transparent'}`,
                      }}
                    >
                      {f.label}
                    </button>
                  ))}
                  <button
                    onClick={() => setVerbose(!verbose)}
                    className="ml-auto px-2 py-0.5 rounded text-[10px] font-medium transition"
                    style={{
                      background: verbose ? 'rgba(34,197,94,0.15)' : 'transparent',
                      color: verbose ? '#22c55e' : 'var(--neo-text-dim)',
                      border: `1px solid ${verbose ? '#22c55e' : 'transparent'}`,
                    }}
                  >
                    {verbose ? 'Verbose' : 'Compact'}
                  </button>
                  <span className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
                    {runDetail.events?.length || 0} events
                  </span>
                </div>
                {/* Event list */}
                <div className="p-3 space-y-0.5 max-h-[500px] overflow-y-auto" style={{ background: 'var(--neo-bg)', fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace', fontSize: 11 }}>
                  {runDetail.events?.map((ev, i) => <RunEventRow key={i} ev={ev} verbose={verbose} />)}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ─── Integrations Panel ─── */

function IntegrationsPanel({ sessionId, config, onUpdate }) {
  const [saving, setSaving] = React.useState(false);
  const [liveIntegrations, setLiveIntegrations] = React.useState([]);
  const [loadingIntegrations, setLoadingIntegrations] = React.useState(true);
  const [testing, setTesting] = React.useState(null);
  const ws = config.workspace || {};

  // Load live integrations from the central integration registry
  React.useEffect(() => {
    setLoadingIntegrations(true);
    api.get('/dashboard/integrations')
      .then((res) => setLiveIntegrations(res.data.integrations || []))
      .catch(() => {})
      .finally(() => setLoadingIntegrations(false));
  }, []);

  const [wsIntegrationId, setWsIntegrationId] = React.useState(config.workspace_integration_id || '');
  const [jiraIntegrationId, setJiraIntegrationId] = React.useState(config.jira_integration_id || '');
  const [wsType, setWsType] = React.useState(ws.type || 'local');

  const gitIntegrations = liveIntegrations.filter(i => i.connector_type === 'git' || i.connector_type === 'github');
  const jiraIntegrations = liveIntegrations.filter(i => i.connector_type === 'jira');

  const handleTest = async (integrationId) => {
    setTesting(integrationId);
    try {
      const res = await api.post(`/dashboard/integrations/${integrationId}/test`);
      if (res.data.success) {
        toast.success(res.data.message);
      } else {
        toast.error(res.data.message);
      }
    } catch {
      toast.error('Test failed');
    } finally {
      setTesting(null);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const payload = {};
      if (wsIntegrationId) {
        // Use integration reference
        const wsInt = liveIntegrations.find(i => i.integration_id === wsIntegrationId);
        if (wsInt) {
          payload.workspace = { type: wsInt.connector_type, integration_id: wsIntegrationId };
        }
      } else {
        payload.workspace = { type: wsType };
      }
      if (jiraIntegrationId) {
        payload.integrations = { jira: { integration_id: jiraIntegrationId } };
      }
      await api.put(`/dashboard/sessions/${sessionId}/config`, payload);
      toast.success('Integrations saved');
      onUpdate();
    } catch {
      toast.error('Failed to save');
    } finally {
      setSaving(false);
    }
  };

  const wsPath = ws.path || `generated/${sessionId.slice(0, 12)}`;

  return (
    <div className="space-y-4">
      {/* Workspace Source */}
      <div>
        <div className="text-xs font-semibold uppercase tracking-wider mb-2" style={{ color: 'var(--neo-text-dim)' }}>
          Workspace
        </div>

        {/* Pick from live integrations or use local */}
        <div className="space-y-2">
          <label className="flex items-center gap-2 p-2.5 rounded-lg cursor-pointer transition"
            style={{
              background: !wsIntegrationId ? 'rgba(99,102,241,0.1)' : 'var(--neo-surface)',
              border: `1px solid ${!wsIntegrationId ? '#6366f1' : 'var(--neo-border)'}`,
            }}
          >
            <input type="radio" checked={!wsIntegrationId} onChange={() => setWsIntegrationId('')}
              className="w-3 h-3" style={{ accentColor: '#6366f1' }} />
            <div>
              <div className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>Local Workspace</div>
              <div className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>Files at: {wsPath}</div>
            </div>
          </label>

          {gitIntegrations.map((gi) => (
            <label key={gi.integration_id}
              className="flex items-center justify-between gap-2 p-2.5 rounded-lg cursor-pointer transition"
              style={{
                background: wsIntegrationId === gi.integration_id ? 'rgba(99,102,241,0.1)' : 'var(--neo-surface)',
                border: `1px solid ${wsIntegrationId === gi.integration_id ? '#6366f1' : 'var(--neo-border)'}`,
              }}
            >
              <div className="flex items-center gap-2">
                <input type="radio" checked={wsIntegrationId === gi.integration_id}
                  onChange={() => setWsIntegrationId(gi.integration_id)}
                  className="w-3 h-3" style={{ accentColor: '#6366f1' }} />
                <div>
                  <div className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>
                    {gi.name}
                    <span className="ml-1.5 px-1.5 py-0.5 rounded text-[10px]"
                      style={{ background: gi.status === 'active' ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)',
                               color: gi.status === 'active' ? '#22c55e' : '#ef4444' }}>
                      {gi.status}
                    </span>
                  </div>
                  <div className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
                    {gi.connector_type.toUpperCase()} — {gi.config?.repo_url || 'no repo configured'}
                  </div>
                </div>
              </div>
              <button
                onClick={(e) => { e.preventDefault(); handleTest(gi.integration_id); }}
                disabled={testing === gi.integration_id}
                className="px-2 py-1 rounded text-[10px] font-medium transition"
                style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
              >
                {testing === gi.integration_id ? <Loader2 size={10} className="animate-spin" /> : 'Test'}
              </button>
            </label>
          ))}

          {gitIntegrations.length === 0 && !loadingIntegrations && (
            <div className="text-xs px-2 py-1.5" style={{ color: 'var(--neo-text-muted)' }}>
              No Git/GitHub integrations configured. Go to Integrations page to add one.
            </div>
          )}
        </div>
      </div>

      {/* Jira */}
      <div>
        <div className="text-xs font-semibold uppercase tracking-wider mb-2" style={{ color: 'var(--neo-text-dim)' }}>
          Jira
        </div>
        <div className="space-y-2">
          <label className="flex items-center gap-2 p-2.5 rounded-lg cursor-pointer"
            style={{
              background: !jiraIntegrationId ? 'rgba(59,130,246,0.05)' : 'var(--neo-surface)',
              border: `1px solid ${!jiraIntegrationId ? 'var(--neo-blue)' : 'var(--neo-border)'}`,
            }}
          >
            <input type="radio" checked={!jiraIntegrationId} onChange={() => setJiraIntegrationId('')}
              className="w-3 h-3" />
            <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>No Jira</div>
          </label>
          {jiraIntegrations.map((ji) => (
            <label key={ji.integration_id}
              className="flex items-center justify-between gap-2 p-2.5 rounded-lg cursor-pointer transition"
              style={{
                background: jiraIntegrationId === ji.integration_id ? 'rgba(59,130,246,0.1)' : 'var(--neo-surface)',
                border: `1px solid ${jiraIntegrationId === ji.integration_id ? 'var(--neo-blue)' : 'var(--neo-border)'}`,
              }}
            >
              <div className="flex items-center gap-2">
                <input type="radio" checked={jiraIntegrationId === ji.integration_id}
                  onChange={() => setJiraIntegrationId(ji.integration_id)} className="w-3 h-3" />
                <div>
                  <div className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>
                    {ji.name}
                    <span className="ml-1.5 px-1.5 py-0.5 rounded text-[10px]"
                      style={{ background: ji.status === 'active' ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)',
                               color: ji.status === 'active' ? '#22c55e' : '#ef4444' }}>
                      {ji.status}
                    </span>
                  </div>
                  <div className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
                    {ji.config?.domain || ji.config?.projects || 'configured'}
                  </div>
                </div>
              </div>
              <button
                onClick={(e) => { e.preventDefault(); handleTest(ji.integration_id); }}
                disabled={testing === ji.integration_id}
                className="px-2 py-1 rounded text-[10px] font-medium transition"
                style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
              >
                {testing === ji.integration_id ? <Loader2 size={10} className="animate-spin" /> : 'Test'}
              </button>
            </label>
          ))}
          {jiraIntegrations.length === 0 && !loadingIntegrations && (
            <div className="text-xs px-2 py-1.5" style={{ color: 'var(--neo-text-muted)' }}>
              No Jira integrations configured. Go to Integrations page to add one.
            </div>
          )}
        </div>
      </div>

      {/* Save */}
      <div className="flex items-center gap-2 pt-1">
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90 disabled:opacity-50"
          style={{ background: 'var(--neo-blue)', color: '#fff' }}
        >
          {saving ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
          Save
        </button>
        {ws.type && ws.type !== 'local' && (
          <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
            Current: {ws.type.toUpperCase()} — {ws.repo_url || 'configured'}
          </span>
        )}
      </div>
    </div>
  );
}

/* ─── Attached Contexts Panel ─── */

function AttachedContextsPanel({ sessionId }) {
  const [attachments, setAttachments] = React.useState([]);
  const [allContexts, setAllContexts] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [showAttach, setShowAttach] = React.useState(false);
  const [selectedCtx, setSelectedCtx] = React.useState('');
  const [attaching, setAttaching] = React.useState(false);

  const fetch = React.useCallback(async () => {
    setLoading(true);
    try {
      const [attRes, ctxRes] = await Promise.all([
        api.get(`/dashboard/sessions/${sessionId}/contexts`),
        api.get('/dashboard/contexts'),
      ]);
      setAttachments(attRes.data.contexts || []);
      setAllContexts(ctxRes.data.contexts || []);
    } catch {} finally {
      setLoading(false);
    }
  }, [sessionId]);

  React.useEffect(() => { fetch(); }, [fetch]);

  const handleAttach = async () => {
    if (!selectedCtx) return;
    setAttaching(true);
    try {
      await api.post(`/dashboard/sessions/${sessionId}/contexts`, { context_id: selectedCtx, role: 'input' });
      toast.success('Context attached');
      setSelectedCtx('');
      setShowAttach(false);
      fetch();
    } catch {} finally {
      setAttaching(false);
    }
  };

  const handleDetach = async (ctxId) => {
    try {
      await api.delete(`/dashboard/sessions/${sessionId}/contexts/${encodeURIComponent(ctxId)}`);
      toast.success('Context detached — session reverted to base graph');
      fetch();
    } catch (err) {
      const detail = err?.response?.data?.detail || 'Failed to detach context';
      toast.error(detail);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-4">
        <Loader2 size={16} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      </div>
    );
  }

  const attachedIds = new Set(attachments.map((a) => a.context_id));
  const available = allContexts.filter((c) => !attachedIds.has(c.context_id));

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>
          Attached Contexts ({attachments.length})
        </span>
        <button
          onClick={() => setShowAttach(!showAttach)}
          className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium transition hover:opacity-90"
          style={{ background: 'var(--neo-blue)', color: '#fff' }}
        >
          <Plus size={12} /> Attach
        </button>
      </div>

      {showAttach && (
        <div className="flex items-center gap-2 p-2 rounded-lg" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
          <select
            value={selectedCtx}
            onChange={(e) => setSelectedCtx(e.target.value)}
            className="flex-1 px-2.5 py-1 rounded-lg text-xs outline-none"
            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          >
            <option value="">Select a context...</option>
            {available.map((c) => (
              <option key={c.context_id} value={c.context_id}>
                {c.name} ({c.context_type}) — {c.item_count} items
              </option>
            ))}
          </select>
          <button
            onClick={handleAttach}
            disabled={attaching || !selectedCtx}
            className="px-3 py-1 rounded-lg text-xs font-medium transition hover:opacity-90 disabled:opacity-50"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            {attaching ? '...' : 'Attach'}
          </button>
          <button onClick={() => setShowAttach(false)} style={{ color: 'var(--neo-text-muted)' }}>
            <X size={12} />
          </button>
        </div>
      )}

      {attachments.length === 0 ? (
        <p className="text-xs py-3" style={{ color: 'var(--neo-text-muted)' }}>
          No contexts attached yet. Attach contexts to share knowledge with this session.
        </p>
      ) : (
        <div className="space-y-1">
          {attachments.map((att) => {
            const ctx = att.context || {};
            return (
              <div
                key={att.context_id}
                className="flex items-center justify-between p-2.5 rounded-lg"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
              >
                <div className="flex items-center gap-2">
                  <Database size={13} style={{ color: '#af52de' }} />
                  <span className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>
                    {ctx.name || att.context_id}
                  </span>
                  {ctx.context_type && (
                    <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'rgba(139,92,246,0.1)', color: '#8b5cf6' }}>
                      {ctx.context_type}
                    </span>
                  )}
                  <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: att.role === 'input' ? 'rgba(0,122,255,0.1)' : 'rgba(236,72,153,0.1)', color: att.role === 'input' ? 'var(--neo-blue)' : '#ec4899' }}>
                    {att.role}
                  </span>
                  {ctx.item_count !== undefined && (
                    <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>{ctx.item_count} items</span>
                  )}
                </div>
                <button
                  onClick={() => handleDetach(att.context_id)}
                  className="p-1 rounded hover:opacity-80 transition"
                  style={{ color: '#ef4444' }}
                  title="Detach context"
                >
                  <X size={13} />
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ─── Invite Panel (inside Members tab) ─── */

function InvitePanel({ sessionId }) {
  const [invitations, setInvitations] = React.useState([]);
  const [showForm, setShowForm] = React.useState(false);
  const [level, setLevel] = React.useState('read');
  const [creating, setCreating] = React.useState(false);
  const [inviteUrl, setInviteUrl] = React.useState(null);
  const [copied, setCopied] = React.useState(false);

  const fetchInvites = () => {
    api.get(`/dashboard/sessions/${sessionId}/invitations`)
      .then((res) => setInvitations(res.data.invitations || []))
      .catch(() => {});
  };

  React.useEffect(() => { fetchInvites(); }, [sessionId]); // eslint-disable-line

  const create = async () => {
    setCreating(true);
    try {
      const res = await api.post(`/dashboard/sessions/${sessionId}/invite`, { level });
      setInviteUrl(`${window.location.origin}${res.data.invite_url}`);
      toast.success('Invite created');
      fetchInvites();
    } catch {} finally { setCreating(false); }
  };

  const revoke = async (invId) => {
    try {
      await api.delete(`/dashboard/sessions/${sessionId}/invitations/${invId}`);
      toast.success('Invite revoked');
      fetchInvites();
    } catch {}
  };

  const copy = () => {
    if (inviteUrl) {
      navigator.clipboard.writeText(inviteUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="mt-3 pt-3" style={{ borderTop: '1px solid var(--neo-border)' }}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <Link size={12} style={{ color: 'var(--neo-text-muted)' }} />
          <span className="text-xs font-semibold" style={{ color: 'var(--neo-text-muted)' }}>
            External Agent Invites
          </span>
        </div>
        <button
          onClick={() => { setShowForm(!showForm); setInviteUrl(null); }}
          className="text-xs px-2 py-0.5 rounded transition hover:opacity-80"
          style={{ color: 'var(--neo-cyan)', border: '1px solid var(--neo-border)' }}
        >
          {showForm ? 'Cancel' : 'Create Invite'}
        </button>
      </div>

      {showForm && (
        <div className="mb-3 space-y-2">
          {inviteUrl ? (
            <div>
              <p className="text-xs mb-1" style={{ color: 'var(--neo-text-muted)' }}>
                Share this link with the external agent:
              </p>
              <div className="flex items-center gap-2">
                <code
                  className="flex-1 px-2 py-1.5 rounded-lg text-xs break-all"
                  style={{
                    background: 'var(--neo-surface)',
                    border: '1px solid var(--neo-border)',
                    color: 'var(--neo-green)',
                    fontFamily: "'JetBrains Mono', monospace",
                  }}
                >
                  {inviteUrl}
                </code>
                <button onClick={copy} className="shrink-0 p-1.5 rounded" style={{ color: 'var(--neo-text-muted)' }}>
                  {copied ? <Check size={12} /> : <Copy size={12} />}
                </button>
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <select
                value={level}
                onChange={(e) => setLevel(e.target.value)}
                className="px-2 py-1 rounded-lg text-xs outline-none"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
              >
                <option value="read">Read</option>
                <option value="write">Write</option>
              </select>
              <button
                onClick={create}
                disabled={creating}
                className="flex items-center gap-1 px-3 py-1 rounded-lg text-xs font-medium transition hover:opacity-90 disabled:opacity-50"
                style={{ background: 'var(--neo-blue)', color: '#fff' }}
              >
                {creating ? <Loader2 size={10} className="animate-spin" /> : <Link size={10} />}
                Generate Link
              </button>
            </div>
          )}
        </div>
      )}

      {invitations.length > 0 && (
        <div className="space-y-1">
          {invitations.map((inv) => (
            <div
              key={inv.invitation_id}
              className="flex items-center justify-between px-2 py-1.5 rounded-lg"
              style={{ background: 'var(--neo-surface)' }}
            >
              <div className="flex items-center gap-2 text-xs">
                <Link size={10} style={{ color: 'var(--neo-cyan)' }} />
                <span style={{ color: 'var(--neo-text)' }}>{inv.access_level}</span>
                <span style={{ color: 'var(--neo-text-muted)' }}>
                  expires {new Date(inv.expires_at * 1000).toLocaleDateString()}
                </span>
              </div>
              <button
                onClick={() => revoke(inv.invitation_id)}
                className="text-xs px-1.5 py-0.5 rounded"
                style={{ color: '#ef4444' }}
              >
                Revoke
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ─── Snapshot Panel (inside Context tab) ─── */

function SnapshotPanel({ sessionId }) {
  const [snapshots, setSnapshots] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [creating, setCreating] = React.useState(false);

  const fetch = () => {
    api.get(`/dashboard/sessions/${sessionId}/snapshots`)
      .then((res) => setSnapshots(res.data.snapshots || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  React.useEffect(() => { fetch(); }, [sessionId]); // eslint-disable-line

  const create = async () => {
    setCreating(true);
    try {
      await api.post(`/dashboard/sessions/${sessionId}/snapshot`, {});
      toast.success('Snapshot created');
      fetch();
    } catch {} finally { setCreating(false); }
  };

  const restore = async (cpId) => {
    if (!window.confirm('Restore this snapshot? Current context will be overwritten.')) return;
    try {
      await api.post(`/dashboard/sessions/${sessionId}/snapshots/${cpId}/restore`);
      toast.success('Snapshot restored');
    } catch {}
  };

  return (
    <div className="mt-4 pt-4" style={{ borderTop: '1px solid var(--neo-border)' }}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-semibold" style={{ color: 'var(--neo-text-muted)' }}>
          Snapshots
        </span>
        <button
          onClick={create}
          disabled={creating}
          className="flex items-center gap-1 px-2 py-1 rounded text-xs transition hover:opacity-80 disabled:opacity-50"
          style={{ color: 'var(--neo-cyan)', border: '1px solid var(--neo-border)' }}
        >
          {creating ? <Loader2 size={10} className="animate-spin" /> : <Database size={10} />}
          Create Snapshot
        </button>
      </div>

      {loading ? (
        <div className="flex justify-center py-2">
          <Loader2 size={14} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
        </div>
      ) : snapshots.length === 0 ? (
        <p className="text-xs text-center py-2" style={{ color: 'var(--neo-text-muted)' }}>
          No snapshots yet
        </p>
      ) : (
        <div className="space-y-1">
          {snapshots.slice(0, 5).map((sp, i) => (
            <div
              key={sp.checkpoint_id || i}
              className="flex items-center justify-between px-3 py-1.5 rounded-lg"
              style={{ background: 'var(--neo-surface)' }}
            >
              <div className="flex items-center gap-2 text-xs">
                <Database size={10} style={{ color: 'var(--neo-cyan)' }} />
                <span style={{ color: 'var(--neo-text-muted)' }}>
                  {sp.checkpoint_id ? sp.checkpoint_id.slice(0, 12) : `#${i + 1}`}
                </span>
                {sp.timestamp && (
                  <span style={{ color: 'var(--neo-text-muted)' }}>
                    {new Date(sp.timestamp * 1000).toLocaleString()}
                  </span>
                )}
                {sp.message && (
                  <span style={{ color: 'var(--neo-text)' }}>{sp.message}</span>
                )}
              </div>
              <button
                onClick={() => restore(sp.checkpoint_id)}
                className="text-xs px-2 py-0.5 rounded transition hover:opacity-80"
                style={{ color: 'var(--neo-cyan)' }}
              >
                Restore
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ─── Quality & Analytics Panel ─── */

const GRADE_COLORS = {
  A: 'var(--neo-green)',
  B: 'var(--neo-blue)',
  C: '#ff9500',
  D: '#ef4444',
  F: '#ef4444',
  '?': 'var(--neo-text-muted)',
};

function QualityPanel({ sessionId }) {
  const [report, setReport] = React.useState(null);
  const [analytics, setAnalytics] = React.useState(null);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    setLoading(true);
    Promise.all([
      api.get(`/dashboard/sessions/${sessionId}/quality`).catch(() => ({ data: null })),
      api.get(`/dashboard/sessions/${sessionId}/analytics`).catch(() => ({ data: null })),
    ]).then(([qRes, aRes]) => {
      setReport(qRes.data);
      setAnalytics(aRes.data);
    }).finally(() => setLoading(false));
  }, [sessionId]);

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 size={20} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {report && (
        <div>
          <div className="flex items-center gap-4 mb-3">
            <div
              className="w-14 h-14 rounded-xl flex items-center justify-center text-2xl font-bold"
              style={{
                background: `${GRADE_COLORS[report.grade] || 'var(--neo-text-muted)'}15`,
                color: GRADE_COLORS[report.grade] || 'var(--neo-text-muted)',
              }}
            >
              {report.grade}
            </div>
            <div>
              <div className="text-sm font-semibold" style={{ color: 'var(--neo-text)' }}>
                Quality Score: {Math.round((report.overall_score || 0) * 100)}%
              </div>
              <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                {report.total_items} items · {report.fresh_items} fresh · {report.stale_items} stale
              </div>
            </div>
          </div>

          <div className="w-full h-2 rounded-full mb-2" style={{ background: 'var(--neo-border)' }}>
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: `${Math.round((report.overall_score || 0) * 100)}%`,
                background: GRADE_COLORS[report.grade],
              }}
            />
          </div>

          {(report.warnings || []).length > 0 && (
            <div className="space-y-1 mb-3">
              {report.warnings.map((w, i) => (
                <div key={i} className="px-3 py-1.5 rounded-lg text-xs" style={{ background: 'rgba(242,87,87,0.1)', color: '#ef4444' }}>
                  {w}
                </div>
              ))}
            </div>
          )}

          <div className="grid grid-cols-3 gap-2">
            {[
              { label: 'Avg Staleness', value: `${Math.round((report.avg_staleness || 0) * 100)}%` },
              { label: 'Avg Confidence', value: `${Math.round((report.avg_confidence || 0) * 100)}%` },
              { label: 'Fresh Items', value: report.total_items > 0 ? `${report.fresh_items}/${report.total_items}` : '—' },
            ].map((m) => (
              <div key={m.label} className="px-3 py-2 rounded-lg text-center" style={{ background: 'var(--neo-surface)' }}>
                <div className="text-sm font-bold" style={{ color: 'var(--neo-text)' }}>{m.value}</div>
                <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>{m.label}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {analytics && (
        <div className="pt-3" style={{ borderTop: '1px solid var(--neo-border)' }}>
          <span className="text-xs font-semibold" style={{ color: 'var(--neo-text-muted)' }}>Session Analytics</span>
          <div className="grid grid-cols-5 gap-2 mt-2">
            {[
              { label: 'Nodes', value: analytics.node_count },
              { label: 'Files', value: analytics.file_count },
              { label: 'Threads', value: analytics.conversation_count },
              { label: 'Members', value: analytics.member_count },
              { label: 'Events', value: analytics.activity_count },
            ].map((m) => (
              <div key={m.label} className="px-2 py-2 rounded-lg text-center" style={{ background: 'var(--neo-surface)' }}>
                <div className="text-lg font-bold" style={{ color: 'var(--neo-cyan)' }}>{m.value || 0}</div>
                <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>{m.label}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ─── Join Requests Panel ─── */

function JoinRequestsPanel({ sessionId }) {
  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [processing, setProcessing] = useState({});

  const fetchRequests = useCallback(() => {
    setLoading(true);
    api.get(`/dashboard/sessions/${sessionId}/join-requests`)
      .then((res) => setRequests(res.data.requests || []))
      .catch(() => setRequests([]))
      .finally(() => setLoading(false));
  }, [sessionId]);

  useEffect(() => { fetchRequests(); }, [fetchRequests]);

  const handleApprove = async (requestId) => {
    setProcessing((p) => ({ ...p, [requestId]: 'approving' }));
    try {
      await api.post(`/dashboard/sessions/${sessionId}/join-requests/${requestId}/approve`, {});
      toast.success('Join request approved');
      fetchRequests();
    } catch {} finally {
      setProcessing((p) => ({ ...p, [requestId]: null }));
    }
  };

  const handleDeny = async (requestId) => {
    setProcessing((p) => ({ ...p, [requestId]: 'denying' }));
    try {
      await api.post(`/dashboard/sessions/${sessionId}/join-requests/${requestId}/deny`);
      toast.success('Join request denied');
      fetchRequests();
    } catch {} finally {
      setProcessing((p) => ({ ...p, [requestId]: null }));
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-6">
        <Loader2 size={16} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      </div>
    );
  }

  if (requests.length === 0) {
    return (
      <div className="text-center py-8">
        <Shield size={24} className="mx-auto mb-2" style={{ color: 'var(--neo-text-muted)' }} />
        <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
          No pending join requests.
        </p>
        <p className="text-xs mt-1" style={{ color: 'var(--neo-text-dim)' }}>
          When agents request to join this session, they'll appear here for approval.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-semibold" style={{ color: 'var(--neo-text-muted)' }}>
          {requests.length} pending request{requests.length !== 1 ? 's' : ''}
        </span>
        <button
          onClick={fetchRequests}
          className="flex items-center gap-1 px-2 py-1 rounded text-xs"
          style={{ color: 'var(--neo-text-muted)' }}
        >
          <RefreshCw size={10} /> Refresh
        </button>
      </div>
      {requests.map((req) => {
        const agent = req.agent || {};
        const isProcessing = processing[req.request_id];
        return (
          <div
            key={req.request_id}
            className="flex items-center justify-between p-3 rounded-lg"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
          >
            <div className="flex items-center gap-3 min-w-0">
              <div
                className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0"
                style={{ background: 'rgba(245,158,11,0.1)' }}
              >
                <UserPlus size={14} style={{ color: '#f59e0b' }} />
              </div>
              <div className="min-w-0">
                <div className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>
                  {agent.name || req.agent_id}
                </div>
                <div className="flex items-center gap-2 mt-0.5">
                  <span
                    className="text-xs px-1.5 py-0.5 rounded"
                    style={{
                      background: req.requested_level === 'write' ? 'rgba(0,122,255,0.15)' : 'rgba(150,150,150,0.15)',
                      color: req.requested_level === 'write' ? 'var(--neo-blue)' : 'var(--neo-text-muted)',
                    }}
                  >
                    {req.requested_level}
                  </span>
                  {req.reason && (
                    <span className="text-xs truncate" style={{ color: 'var(--neo-text-muted)' }}>
                      {req.reason}
                    </span>
                  )}
                  <span className="text-xs flex items-center gap-1" style={{ color: 'var(--neo-text-dim)' }}>
                    <Clock size={10} />
                    {new Date(req.created_at).toLocaleString()}
                  </span>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2 shrink-0">
              <button
                onClick={() => handleApprove(req.request_id)}
                disabled={!!isProcessing}
                className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90 disabled:opacity-50"
                style={{ background: 'var(--neo-green)', color: '#fff' }}
              >
                {isProcessing === 'approving' ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                Approve
              </button>
              <button
                onClick={() => handleDeny(req.request_id)}
                disabled={!!isProcessing}
                className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90 disabled:opacity-50"
                style={{ background: 'rgba(239,68,68,0.15)', color: '#ef4444' }}
              >
                {isProcessing === 'denying' ? <Loader2 size={12} className="animate-spin" /> : <X size={12} />}
                Deny
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ─── Searchable Agent Grant ─── */

function AgentSearchGrant({ agents, existingIds, onGrant }) {
  const [query, setQuery] = useState('');
  const [level, setLevel] = useState('read');
  const [showResults, setShowResults] = useState(false);

  // Filter out agents already in the session
  const available = agents.filter((a) => !existingIds.includes(a.agent_id));
  const filtered = query.trim()
    ? available.filter((a) =>
        (a.name || '').toLowerCase().includes(query.toLowerCase()) ||
        a.agent_id.toLowerCase().includes(query.toLowerCase()) ||
        (a.role || '').toLowerCase().includes(query.toLowerCase()) ||
        (a.platform || '').toLowerCase().includes(query.toLowerCase())
      )
    : available;

  const handleSelect = (agent) => {
    onGrant(agent.agent_id, level);
    setQuery('');
    setShowResults(false);
  };

  return (
    <div className="pt-3 mt-2" style={{ borderTop: '1px solid var(--neo-border)' }}>
      <div className="flex items-center gap-2 mb-2">
        <Search size={13} style={{ color: 'var(--neo-text-muted)' }} />
        <span className="text-xs font-semibold" style={{ color: 'var(--neo-text-muted)' }}>
          Add Agent to Session
        </span>
      </div>
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <input
            value={query}
            onChange={(e) => { setQuery(e.target.value); setShowResults(true); }}
            onFocus={() => setShowResults(true)}
            placeholder="Search agents by name, role, platform..."
            className="w-full px-3 py-1.5 rounded-lg text-xs outline-none"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          />
          {showResults && filtered.length > 0 && (
            <div
              className="absolute left-0 right-0 top-full mt-1 rounded-lg overflow-hidden z-10 max-h-48 overflow-y-auto"
              style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', boxShadow: '0 4px 12px rgba(0,0,0,0.3)' }}
            >
              {filtered.slice(0, 10).map((a) => (
                <button
                  key={a.agent_id}
                  onClick={() => handleSelect(a)}
                  className="w-full text-left px-3 py-2 text-xs transition flex items-center justify-between"
                  style={{ color: 'var(--neo-text)', borderBottom: '1px solid var(--neo-border)' }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--neo-bg)'; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                >
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{a.name || a.agent_id}</span>
                    <span style={{ color: 'var(--neo-text-muted)' }}>{a.role}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="px-1.5 py-0.5 rounded" style={{ background: 'rgba(0,210,255,0.1)', color: 'var(--neo-cyan)', fontSize: '10px' }}>
                      {a.platform || 'app'}
                    </span>
                    <span
                      className="px-1.5 py-0.5 rounded"
                      style={{
                        background: a.status === 'active' ? 'rgba(76,217,100,0.15)' : 'rgba(242,87,87,0.15)',
                        color: a.status === 'active' ? 'var(--neo-green)' : '#ef4444',
                        fontSize: '10px',
                      }}
                    >
                      {a.status}
                    </span>
                  </div>
                </button>
              ))}
              {filtered.length > 10 && (
                <div className="px-3 py-1.5 text-xs text-center" style={{ color: 'var(--neo-text-dim)' }}>
                  {filtered.length - 10} more — refine search
                </div>
              )}
            </div>
          )}
          {showResults && query && filtered.length === 0 && (
            <div
              className="absolute left-0 right-0 top-full mt-1 px-3 py-3 rounded-lg text-xs text-center"
              style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
            >
              No matching agents found
            </div>
          )}
        </div>
        <select
          value={level}
          onChange={(e) => setLevel(e.target.value)}
          className="px-2 py-1.5 rounded-lg text-xs outline-none"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
        >
          <option value="read">read</option>
          <option value="write">write</option>
          <option value="admin">admin</option>
        </select>
      </div>
      {/* Click-outside handler */}
      {showResults && (
        <div className="fixed inset-0 z-0" onClick={() => setShowResults(false)} />
      )}
    </div>
  );
}


/* ─── Agent Run Panel ─── */

const AGENT_COLOR_POOL = ['#af52de', '#f59e0b', '#3b82f6', '#22c55e', '#ef4444', '#06b6d4'];
const _agentColorCache = { system: 'var(--neo-text-muted)' };
function getAgentColor(name) {
  if (!name) return 'var(--neo-text-muted)';
  if (!_agentColorCache[name]) {
    const idx = Object.keys(_agentColorCache).length - 1; // -1 for 'system'
    _agentColorCache[name] = AGENT_COLOR_POOL[idx % AGENT_COLOR_POOL.length];
  }
  return _agentColorCache[name];
}

const PROMPT_TEMPLATES = [
  {
    id: 'sdlc',
    label: 'SDLC / Software Build',
    icon: '🏗️',
    prompt: `Build a complete application based on the attached requirements/spec.

Steps:
1. Analyze the project requirements from the attached context
2. Create project structure (package.json, configs, folder layout)
3. Build core components and pages
4. Implement business logic and data models
5. Add styling and responsive design
6. Write tests for critical paths

Use the SDLC extraction schema for structured task decomposition.`,
  },
  {
    id: 'feature',
    label: 'Feature Development',
    icon: '✨',
    prompt: `Implement the following feature:

[Describe the feature here]

Steps:
1. Review existing codebase for integration points
2. Create new components/modules needed
3. Wire up routing and data flow
4. Add error handling and validation
5. Write unit tests`,
  },
  {
    id: 'bugfix',
    label: 'Bug Fix',
    icon: '🐛',
    prompt: `Fix the following issue:

[Describe the bug here]

Steps:
1. Read relevant source files to understand the issue
2. Identify the root cause
3. Implement the fix
4. Verify the fix doesn't break existing functionality
5. Add a regression test`,
  },
  {
    id: 'refactor',
    label: 'Refactoring',
    icon: '♻️',
    prompt: `Refactor the codebase:

[Describe what needs refactoring]

Steps:
1. Analyze current code structure
2. Identify patterns and anti-patterns
3. Plan the refactoring approach
4. Execute changes incrementally
5. Verify all existing tests still pass`,
  },
  {
    id: 'research',
    label: 'Research & Analysis',
    icon: '🔍',
    prompt: `Research and analyze:

[Describe the topic]

Steps:
1. Search the knowledge graph for existing information
2. Identify gaps in understanding
3. Add findings as knowledge nodes
4. Create a summary with recommendations
5. Record key decisions and trade-offs`,
  },
  {
    id: 'custom',
    label: 'Custom',
    icon: '✏️',
    prompt: '',
  },
];

function AgentRunPanel({ sessionId }) {
  const [prompt, setPrompt] = React.useState('');
  const [selectedTemplate, setSelectedTemplate] = React.useState('custom');
  const [leadAgentId, setLeadAgentId] = React.useState('');
  const [sessionAgents, setSessionAgents] = React.useState([]);
  const [running, setRunning] = React.useState(false);
  const [events, setEvents] = React.useState([]);
  const [error, setError] = React.useState(null);
  const [currentPhase, setCurrentPhase] = React.useState(null);
  const feedRef = React.useRef(null);
  const abortRef = React.useRef(null);

  // Load agents for this session: prefer assigned agents, fall back to all session members
  React.useEffect(() => {
    // First try assigned agents (explicit scoping)
    api.get(`/dashboard/sessions/${sessionId}/agents`)
      .then((res) => {
        const assigned = res.data.agents || [];
        if (assigned.length > 0) {
          setSessionAgents(assigned);
          if (!leadAgentId) setLeadAgentId(assigned[0].agent_id);
          return;
        }
        // No assigned agents — fall back to session members
        return api.get(`/dashboard/sessions/${sessionId}/members`)
          .then((r) => {
            const members = r.data.members || [];
            return api.get('/dashboard/agents').then((ar) => {
              const allAgents = ar.data.agents || [];
              const valid = members
                .map((m) => allAgents.find((a) => a.agent_id === m.agent_id))
                .filter(Boolean);
              setSessionAgents(valid);
              if (valid.length > 0 && !leadAgentId) setLeadAgentId(valid[0].agent_id);
            });
          });
      })
      .catch(() => {});
  }, [sessionId]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleRun = async () => {
    if (!prompt.trim() || running) return;
    setRunning(true);
    setEvents([]);
    setError(null);
    setCurrentPhase(null);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const token = localStorage.getItem('contextsynapse_token');
      const adminKey = sessionStorage.getItem('contextsynapse_admin_token');

      const headers = { 'Content-Type': 'application/json' };
      if (token) headers['Authorization'] = `Bearer ${token}`;
      if (adminKey) headers['X-Admin-Key'] = adminKey;

      const baseUrl = api.defaults?.baseURL || '';
      const res = await fetch(`${baseUrl}/dashboard/sessions/${sessionId}/run-agent`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ prompt: prompt.trim(), lead_agent_id: leadAgentId || null, max_turns: 10 }),
        signal: controller.signal,
      });

      if (!res.ok) {
        const errText = await res.text();
        throw new Error(errText || `HTTP ${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const event = JSON.parse(line.slice(6));
            if (event.type === 'error') {
              setError(typeof event.message === 'string' ? event.message : JSON.stringify(event.message));
            } else if (event.type === 'done') {
              // handled below
            } else if (event.type === 'phase') {
              setCurrentPhase(event);
              setEvents((prev) => [...prev, { ...event, _ts: Date.now() }]);
            } else {
              setEvents((prev) => [...prev, { ...event, _ts: Date.now() }]);
            }
          } catch {}
        }
      }
    } catch (e) {
      if (e.name !== 'AbortError') {
        setError(e.message);
      }
    } finally {
      setRunning(false);
      setCurrentPhase(null);
      abortRef.current = null;
    }
  };

  const handleStop = () => {
    if (abortRef.current) {
      abortRef.current.abort();
      setRunning(false);
    }
  };

  React.useEffect(() => {
    if (feedRef.current) feedRef.current.scrollTop = feedRef.current.scrollHeight;
  }, [events]);

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleRun(); }
  };

  return (
    <div className="space-y-3">
      {/* Template selector */}
      <div className="flex gap-1.5 flex-wrap">
        {PROMPT_TEMPLATES.map((t) => (
          <button
            key={t.id}
            onClick={() => {
              setSelectedTemplate(t.id);
              if (t.prompt) setPrompt(t.prompt);
            }}
            disabled={running}
            className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium transition disabled:opacity-50"
            style={{
              background: selectedTemplate === t.id ? 'var(--neo-blue)' : 'var(--neo-surface)',
              color: selectedTemplate === t.id ? '#fff' : 'var(--neo-text-muted)',
              border: `1px solid ${selectedTemplate === t.id ? 'var(--neo-blue)' : 'var(--neo-border)'}`,
            }}
          >
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {/* Input area */}
      <div className="flex gap-2">
        <div className="flex-1">
          <textarea
            value={prompt}
            onChange={(e) => { setPrompt(e.target.value); setSelectedTemplate('custom'); }}
            onKeyDown={handleKeyDown}
            placeholder="Describe what you want built... or select a template above"
            rows={4}
            className="w-full px-3 py-2 rounded-lg text-sm outline-none resize-y"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)', fontFamily: 'inherit' }}
            disabled={running}
          />
        </div>
        <div className="flex flex-col gap-1">
          <select
            value={leadAgentId}
            onChange={(e) => setLeadAgentId(e.target.value)}
            className="px-2 py-1 rounded-lg text-xs outline-none"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            disabled={running}
          >
            {sessionAgents.length === 0 && <option value="">Auto (default agent)</option>}
            {sessionAgents.map((a) => (
              <option key={a.agent_id} value={a.agent_id}>{a.name} leads</option>
            ))}
          </select>
          {running ? (
            <button onClick={handleStop}
              className="flex items-center justify-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90"
              style={{ background: '#ef4444', color: '#fff' }}>
              <Square size={12} /> Stop
            </button>
          ) : (
            <button onClick={handleRun} disabled={!prompt.trim()}
              className="flex items-center justify-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90 disabled:opacity-40"
              style={{ background: 'var(--neo-green)', color: '#fff' }}>
              <Play size={12} /> Run
            </button>
          )}
        </div>
      </div>

      {/* Pipeline status bar */}
      {(running || events.length > 0) && (
        <div className="flex items-center gap-3 px-3 py-2 rounded-lg text-xs"
          style={{ background: 'rgba(0,122,255,0.08)', border: '1px solid rgba(0,122,255,0.15)' }}>
          <PipelineStatus events={events} running={running} />
        </div>
      )}

      {/* Theater / Log toggle */}
      {(events.length > 0 || running || error) && (
        <>
          <div className="flex items-center gap-2 mb-2">
            <button
              onClick={() => { const v = localStorage.getItem('agent-theater') !== 'off'; localStorage.setItem('agent-theater', v ? 'off' : 'on'); setEvents([...events]); }}
              className="flex items-center gap-1 px-3 py-1 rounded-md text-xs"
              style={{
                background: localStorage.getItem('agent-theater') !== 'off' ? 'rgba(0,210,255,0.12)' : 'var(--neo-surface)',
                border: '1px solid var(--neo-border)', color: localStorage.getItem('agent-theater') !== 'off' ? 'var(--neo-cyan)' : 'var(--neo-text-muted)',
                cursor: 'pointer',
              }}
            >
              {localStorage.getItem('agent-theater') !== 'off' ? '🎭 Theater' : '📋 Log'}
            </button>
          </div>

          {/* Agent Theater Mode */}
          {localStorage.getItem('agent-theater') !== 'off' ? (
            <AgentTheater
              events={events}
              isRunning={running}
              sessionId={sessionId}
              agents={sessionAgents.map(a => a?.name || a?.agent_id || '')}
              onStop={() => { if (abortRef.current) abortRef.current.abort(); setRunning(false); }}
            />
          ) : (
            /* Classic Log Mode */
            <div ref={feedRef} className="rounded-lg p-3 space-y-1 overflow-y-auto"
              style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', maxHeight: 500, fontFamily: 'monospace', fontSize: 12 }}>
              {events.map((ev, i) => <AgentEvent key={i} event={ev} />)}
              {running && currentPhase && (
                <div className="flex items-center gap-2 py-1" style={{ color: getAgentColor(currentPhase.agent) }}>
                  <Loader2 size={12} className="animate-spin" />
                  <span>{currentPhase.agent} is {currentPhase.phase === 'planning' ? 'planning...' : 'executing tasks...'}</span>
                </div>
              )}
              {error && (
                <div className="mt-2 pt-2" style={{ borderTop: '1px solid var(--neo-border)' }}>
                  <div className="text-sm" style={{ color: '#ef4444' }}>Error: {error}</div>
                </div>
              )}
            </div>
          )}
        </>
      )}

      {/* Workspace viewer — shows generated files after pipeline runs */}
      {!running && events.length > 0 && (
        <WorkspaceViewer sessionId={sessionId} />
      )}

      {/* Schedule section */}
      <SchedulePanel sessionId={sessionId} />

      {/* Empty state */}
      {events.length === 0 && !running && !error && (
        <div className="rounded-lg p-6 text-center"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
          <Bot size={32} className="mx-auto mb-2" style={{ color: 'var(--neo-text-muted)', opacity: 0.5 }} />
          <p className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>
            Multi-Agent Pipeline
          </p>
          <p className="text-xs mt-1" style={{ color: 'var(--neo-text-muted)' }}>
            The lead agent plans and assigns tasks. All agents execute their work through the shared graph.
          </p>
          {sessionAgents.length > 0 && (
            <p className="text-xs mt-2" style={{ color: 'var(--neo-text-muted)' }}>
              Agents: {sessionAgents.map((a) => a.name).join(', ')}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/* ─── Workspace Viewer ─── */

/* ─── Schedule Panel ─── */

function SchedulePanel({ sessionId }) {
  const [schedules, setSchedules] = React.useState([]);
  const [showForm, setShowForm] = React.useState(false);
  const [schedInterval, setSchedInterval] = React.useState('daily');
  const [schedPrompt, setSchedPrompt] = React.useState('Continue the project — check tasks, build remaining features');
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    api.get(`/dashboard/sessions/${sessionId}/schedule`)
      .then(r => setSchedules(r.data.schedules || []))
      .catch(() => {});
  }, [sessionId]);

  const handleCreate = async () => {
    setSaving(true);
    try {
      await api.post(`/dashboard/sessions/${sessionId}/schedule`, {
        prompt: schedPrompt, interval: schedInterval,
      });
      toast.success(`Scheduled: run ${schedInterval}`);
      setShowForm(false);
      const r = await api.get(`/dashboard/sessions/${sessionId}/schedule`);
      setSchedules(r.data.schedules || []);
    } catch (e) {
      toast.error('Failed to create schedule');
    } finally { setSaving(false); }
  };

  const handleDelete = async (scheduleId) => {
    await api.delete(`/dashboard/sessions/${sessionId}/schedule/${scheduleId}`);
    setSchedules(s => s.filter(x => x.schedule_id !== scheduleId));
    toast.success('Schedule removed');
  };

  const activeSchedules = schedules.filter(s => s.status === 'active');

  return (
    <div className="rounded-lg p-3" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-semibold flex items-center gap-1.5" style={{ color: 'var(--neo-text)' }}>
          <Clock size={12} /> Scheduled Runs
        </span>
        {!showForm && (
          <button onClick={() => setShowForm(true)}
            className="text-[10px] px-2 py-0.5 rounded font-medium"
            style={{ background: 'rgba(99,102,241,0.1)', color: '#6366f1' }}>
            + Schedule
          </button>
        )}
      </div>

      {activeSchedules.length > 0 && (
        <div className="space-y-1 mb-2">
          {activeSchedules.map(s => (
            <div key={s.schedule_id} className="flex items-center justify-between text-xs px-2 py-1.5 rounded"
              style={{ background: 'var(--neo-bg)' }}>
              <div>
                <span className="font-medium" style={{ color: 'var(--neo-text)' }}>Every {s.interval}</span>
                <span className="ml-2" style={{ color: 'var(--neo-text-dim)' }}>
                  {s.run_count > 0 ? `${s.run_count} runs` : 'not run yet'}
                  {s.next_run_at && ` · next: ${fmtTime(s.next_run_at)}`}
                </span>
              </div>
              <button onClick={() => handleDelete(s.schedule_id)}
                className="text-[10px] px-1.5 rounded" style={{ color: '#ef4444' }}>
                Remove
              </button>
            </div>
          ))}
        </div>
      )}

      {showForm && (
        <div className="space-y-2 pt-1">
          <select value={schedInterval} onChange={e => setSchedInterval(e.target.value)}
            className="w-full px-2 py-1.5 rounded text-xs outline-none"
            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}>
            <option value="hourly">Every hour</option>
            <option value="every_6h">Every 6 hours</option>
            <option value="every_12h">Every 12 hours</option>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
          </select>
          <input value={schedPrompt} onChange={e => setSchedPrompt(e.target.value)}
            placeholder="What should agents do each run?"
            className="w-full px-2 py-1.5 rounded text-xs outline-none"
            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }} />
          <div className="flex gap-2">
            <button onClick={handleCreate} disabled={saving}
              className="px-3 py-1 rounded text-xs font-medium disabled:opacity-50"
              style={{ background: 'var(--neo-blue)', color: '#fff' }}>
              {saving ? 'Saving...' : 'Create Schedule'}
            </button>
            <button onClick={() => setShowForm(false)}
              className="px-2 py-1 rounded text-xs" style={{ color: 'var(--neo-text-muted)' }}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {activeSchedules.length === 0 && !showForm && (
        <p className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
          No schedules. Set one to auto-run pipelines on a recurring basis.
        </p>
      )}
    </div>
  );
}

function WorkspaceViewer({ sessionId }) {
  const [files, setFiles] = React.useState([]);
  const [wsPath, setWsPath] = React.useState('');
  const [exists, setExists] = React.useState(false);
  const [selectedFile, setSelectedFile] = React.useState(null);
  const [fileContent, setFileContent] = React.useState('');
  const [loadingFile, setLoadingFile] = React.useState(false);

  React.useEffect(() => {
    api.get(`/dashboard/sessions/${sessionId}/workspace/files`)
      .then((res) => {
        setFiles(res.data.files || []);
        setWsPath(res.data.path || '');
        setExists(res.data.exists);
      })
      .catch(() => {});
  }, [sessionId]);

  const handleFileClick = async (filepath) => {
    if (selectedFile === filepath) {
      setSelectedFile(null);
      return;
    }
    setSelectedFile(filepath);
    setLoadingFile(true);
    try {
      const res = await api.get(`/dashboard/sessions/${sessionId}/workspace/read`, {
        params: { filepath },
      });
      setFileContent(res.data.content || '');
    } catch {
      setFileContent('(failed to read file)');
    } finally {
      setLoadingFile(false);
    }
  };

  const handleDownload = () => {
    const token = localStorage.getItem('contextsynapse_token');
    const adminKey = sessionStorage.getItem('contextsynapse_admin_token');
    const baseUrl = api.defaults?.baseURL || '';
    const params = new URLSearchParams();
    if (token) params.set('token', token);
    if (adminKey) params.set('admin_key', adminKey);
    window.open(`${baseUrl}/dashboard/sessions/${sessionId}/workspace/download?${params}`, '_blank');
  };

  if (!exists || files.length === 0) return null;

  // Group files by directory
  const dirs = {};
  files.forEach((f) => {
    const parts = f.path.split('/');
    const dir = parts.length > 1 ? parts.slice(0, -1).join('/') : '.';
    if (!dirs[dir]) dirs[dir] = [];
    dirs[dir].push(f);
  });

  return (
    <div className="rounded-lg p-3" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-semibold" style={{ color: 'var(--neo-text)' }}>
          Generated Files ({files.filter(f => !f.is_dir).length})
        </span>
        <button
          onClick={handleDownload}
          className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium transition hover:opacity-90"
          style={{ background: 'var(--neo-blue)', color: '#fff' }}
        >
          <Download size={11} /> Download ZIP
        </button>
      </div>

      <div className="space-y-0.5">
        {Object.entries(dirs).map(([dir, dirFiles]) => (
          <div key={dir}>
            {dir !== '.' && (
              <div className="text-[10px] font-medium mt-1.5 mb-0.5 px-1" style={{ color: 'var(--neo-text-dim)' }}>
                {dir}/
              </div>
            )}
            {dirFiles.filter(f => !f.is_dir).map((f) => (
              <button
                key={f.path}
                onClick={() => handleFileClick(f.path)}
                className="w-full text-left flex items-center justify-between px-2 py-1 rounded text-xs transition hover:opacity-80"
                style={{
                  background: selectedFile === f.path ? 'rgba(0,122,255,0.1)' : 'transparent',
                  color: selectedFile === f.path ? 'var(--neo-blue)' : 'var(--neo-text)',
                }}
              >
                <span className="flex items-center gap-1.5">
                  <FileText size={11} style={{ color: 'var(--neo-text-muted)' }} />
                  {f.name}
                </span>
                <span className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
                  {f.size > 1024 ? `${(f.size / 1024).toFixed(1)}KB` : `${f.size}B`}
                </span>
              </button>
            ))}
          </div>
        ))}
      </div>

      {/* File content viewer */}
      {selectedFile && (
        <div className="mt-2 rounded-lg overflow-hidden" style={{ border: '1px solid var(--neo-border)' }}>
          <div className="flex items-center justify-between px-2 py-1" style={{ background: 'var(--neo-bg)' }}>
            <span className="text-[10px] font-medium" style={{ color: 'var(--neo-text-muted)' }}>{selectedFile}</span>
            <button onClick={() => setSelectedFile(null)} style={{ color: 'var(--neo-text-muted)' }}><X size={12} /></button>
          </div>
          {loadingFile ? (
            <div className="p-4 flex justify-center"><Loader2 size={14} className="animate-spin" style={{ color: 'var(--neo-blue)' }} /></div>
          ) : (
            <pre className="p-3 text-xs overflow-auto" style={{
              background: 'var(--neo-bg)', color: 'var(--neo-text)',
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
              maxHeight: 400, whiteSpace: 'pre-wrap', lineHeight: 1.5,
            }}>
              {fileContent}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

function PipelineStatus({ events, running }) {
  const phases = [
    { key: 'planning', label: 'Plan' },
    { key: 'executing', label: 'Execute' },
    { key: 'reviewing', label: 'Review' },
    { key: 'complete', label: 'Done' },
  ];
  const phaseEvents = events.filter((e) => e.type === 'phase');
  const currentPhase = phaseEvents.length > 0 ? phaseEvents[phaseEvents.length - 1].phase : null;

  return (
    <>
      {phases.map((p, i) => {
        const isActive = currentPhase === p.key;
        const isDone = phases.findIndex((x) => x.key === currentPhase) > i;
        const agent = phaseEvents.find((e) => e.phase === p.key)?.agent;
        return (
          <React.Fragment key={p.key}>
            {i > 0 && <span style={{ color: 'var(--neo-text-muted)' }}>→</span>}
            <span className="flex items-center gap-1" style={{
              color: isActive ? (getAgentColor(agent)) : isDone ? 'var(--neo-green)' : 'var(--neo-text-muted)',
              fontWeight: isActive ? 600 : 400,
            }}>
              {isActive && running && <Loader2 size={10} className="animate-spin" />}
              {isDone && <Check size={10} />}
              {agent && <span style={{ color: getAgentColor(agent), opacity: 0.7 }}>[{agent}]</span>}
              {p.label}
            </span>
          </React.Fragment>
        );
      })}
    </>
  );
}

function _str(v) {
  if (v == null) return '';
  if (typeof v === 'string') return v;
  return JSON.stringify(v);
}

function AgentEvent({ event }) {
  const agentColor = getAgentColor(event.agent);
  const agentTag = event.agent ? (
    <span className="text-xs font-medium mr-1" style={{ color: agentColor }}>[{_str(event.agent)}]</span>
  ) : null;

  if (event.type === 'roster') {
    return (
      <div className="flex items-center gap-2 py-1" style={{ color: 'var(--neo-text-muted)', fontSize: 11 }}>
        <Bot size={12} />
        <span>Lead: <strong style={{ color: getAgentColor(event.lead) }}>{_str(event.lead)}</strong></span>
        {event.workers && event.workers.length > 0 && (
          <span>Workers: {event.workers.map((w, i) => (
            <strong key={i} style={{ color: getAgentColor(w) }}>{i > 0 ? ', ' : ''}{_str(w)}</strong>
          ))}</span>
        )}
      </div>
    );
  }

  if (event.type === 'phase') {
    const labels = { planning: 'Planning & assigning tasks', executing: 'Executing assigned tasks', reviewing: 'Reviewing & completing own tasks', complete: 'Pipeline complete' };
    return (
      <div className="flex items-center gap-2 py-1.5 mt-1" style={{ borderTop: '1px solid var(--neo-border)' }}>
        <span style={{ color: agentColor, fontSize: 13 }}>
          {event.phase === 'complete' ? '✓' : '●'}
        </span>
        {agentTag}
        <span className="font-medium" style={{ color: agentColor }}>
          {labels[event.phase] || event.phase}
        </span>
      </div>
    );
  }

  if (event.type === 'turn') {
    return (
      <div className="py-0.5" style={{ color: 'var(--neo-text-muted)', fontSize: 11 }}>
        {agentTag} Turn {event.turn}/{event.max_turns}
      </div>
    );
  }

  if (event.type === 'tool_call') {
    const args = _str(event.args);
    return (
      <div className="flex items-start gap-1.5 py-0.5">
        {agentTag}
        <span style={{ color: agentColor }}>▶</span>
        <span style={{ color: agentColor }}>{_str(event.tool)}</span>
        <span style={{ color: 'var(--neo-text-muted)' }}>
          ({args})
        </span>
      </div>
    );
  }

  if (event.type === 'tool_result') {
    const result = _str(event.result);
    return (
      <div className="flex items-start gap-1.5 py-0.5 pl-4">
        <span style={{ color: 'var(--neo-green)' }}>←</span>
        <span style={{ color: 'var(--neo-text)', opacity: 0.8 }}>
          {result}
        </span>
      </div>
    );
  }

  if (event.type === 'response') {
    const content = _str(event.content);
    return (
      <div className="mt-1 pt-1" style={{ borderTop: '1px dashed var(--neo-border)' }}>
        <div className="flex items-center gap-1 mb-1">
          {agentTag}
          <Bot size={12} style={{ color: agentColor }} />
          <span className="font-medium text-xs" style={{ color: agentColor }}>Response</span>
        </div>
        <div className="text-sm whitespace-pre-wrap pl-2" style={{ color: 'var(--neo-text)', lineHeight: 1.5 }}>
          {content}
        </div>
      </div>
    );
  }

  return null;
}

/* ─── Experiment Live Panel (runs experiments against session) ─── */

function ExperimentLivePanel({ sessionId, sessionName }) {
  const [experiments, setExperiments] = React.useState([]);
  const [selectedExp, setSelectedExp] = React.useState('');
  const [running, setRunning] = React.useState(false);
  const [result, setResult] = React.useState(null);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    api.get('/dashboard/experiments')
      .then(r => {
        const exps = (r.data.experiments || []).filter(e => e.session_id === sessionId || !e.session_id);
        setExperiments(exps);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [sessionId]);

  const runLive = async () => {
    if (!selectedExp) { toast.error('Select an experiment'); return; }
    setRunning(true);
    setResult(null);
    try {
      await api.put(`/dashboard/experiments/${selectedExp}`, { session_id: sessionId });
      const res = await api.post(`/dashboard/experiments/${selectedExp}/run`, { mode: 'live' }, { timeout: 300000 });
      setResult(res.data);
      toast.success(`Experiment completed in ${res.data.total_duration_ms}ms`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Run failed');
      setResult({ status: 'failed', error: e.response?.data?.detail || 'Unknown error', agents: [] });
    } finally {
      setRunning(false);
    }
  };

  if (loading) return <p className="text-xs py-2" style={{ color: 'var(--neo-text-dim)' }}>Loading...</p>;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <select value={selectedExp} onChange={e => setSelectedExp(e.target.value)}
          className="flex-1 px-2 py-1.5 rounded text-xs"
          style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}>
          <option value="">Select experiment...</option>
          {experiments.map(exp => (
            <option key={exp.id} value={exp.id}>{exp.name} ({(exp.agents || []).length} agents)</option>
          ))}
        </select>
        <button onClick={runLive} disabled={!selectedExp || running}
          className="flex items-center gap-1 px-3 py-1.5 rounded text-xs font-medium disabled:opacity-50"
          style={{ background: '#ef4444', color: '#fff' }}>
          {running ? 'Running...' : 'Run Live'}
        </button>
      </div>
      {experiments.length === 0 && (
        <p className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
          No experiments available. Create one in the Playground first.
        </p>
      )}
      {result && (
        <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--neo-border)' }}>
          <div className="px-3 py-1.5 flex items-center gap-2 text-xs"
            style={{ background: 'var(--neo-surface)', borderBottom: '1px solid var(--neo-border)' }}>
            <span className="px-1.5 py-0.5 rounded font-bold text-[10px]"
              style={{ background: result.status === 'completed' ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)',
                color: result.status === 'completed' ? '#22c55e' : '#ef4444' }}>
              {(result.status || '').toUpperCase()}
            </span>
            <span className="font-bold text-[10px] px-1.5 py-0.5 rounded" style={{ background: 'rgba(239,68,68,0.1)', color: '#ef4444' }}>LIVE</span>
            <span style={{ color: 'var(--neo-text-dim)' }}>{result.total_duration_ms}ms</span>
            <span style={{ color: 'var(--neo-text-dim)' }}>{(result.agents || []).length} agents</span>
          </div>
          {result.error && <div className="px-3 py-2 text-xs" style={{ color: '#ef4444' }}>{result.error}</div>}
          {(result.agents || []).map((agent, i) => (
            <details key={i}>
              <summary className="px-3 py-1.5 flex items-center justify-between cursor-pointer text-xs"
                style={{ borderTop: '1px solid var(--neo-border)' }}>
                <span style={{ color: 'var(--neo-text)' }}>{agent.name} ({agent.role})</span>
                <span className="px-1.5 py-0.5 rounded text-[10px]"
                  style={{ background: agent.status === 'completed' ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)',
                    color: agent.status === 'completed' ? '#22c55e' : '#ef4444' }}>
                  {agent.status} — {(agent.steps || []).length} steps, {agent.duration_ms}ms
                </span>
              </summary>
              <div className="px-3 py-2 space-y-1" style={{ background: 'var(--neo-bg)' }}>
                {agent.error && <p className="text-xs" style={{ color: '#ef4444' }}>{agent.error}</p>}
                {(agent.steps || []).map((step, si) => (
                  <div key={si} className="text-[10px]">
                    <span className="font-bold" style={{ color: 'var(--neo-blue)' }}>{step.step}.</span>
                    {step.reasoning && <span style={{ color: '#8b5cf6' }}> {step.reasoning.slice(0, 100)}</span>}
                    {step.tool && <span className="font-mono" style={{ color: '#3b82f6' }}> → {step.tool}</span>}
                    {step.success === true && <span style={{ color: '#22c55e' }}> OK</span>}
                    {step.success === false && <span style={{ color: '#ef4444' }}> FAIL</span>}
                    {step.summary && <span style={{ color: '#f59e0b' }}> {step.summary}</span>}
                  </div>
                ))}
              </div>
            </details>
          ))}
        </div>
      )}
    </div>
  );
}

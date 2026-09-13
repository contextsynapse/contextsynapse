/**
 * PlaygroundPage — Agent DevTools with MCP Tool Tester, Agent Simulator,
 * and Context Flow Visualizer.
 *
 * Three-tab layout for testing tools, simulating agent workflows, and
 * watching live context mutations on the graph.
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Play, Square, Plus, X, Trash2, Send, Search, Database,
  Loader2, Clock, CheckCircle, XCircle, RefreshCw,
  Cpu, Eye, Layers, Terminal, ChevronDown, ChevronRight,
  Copy, Zap, MessageSquare, Settings, Check, BarChart3, Filter,
  Swords, Radio, Pencil,
} from 'lucide-react';
import api from '../../lib/api';
import { useEvent } from '../../context/EventContext';
import { useExperimentSSE } from '../../hooks/useExperimentSSE';
import DashboardGraphExplorer from '../../components/DashboardGraphExplorer';
import AgentTheater from '../../components/AgentTheater';
import AgentActivityFeed from '../../components/AgentActivityFeed';
import LiveFeed from '../../components/LiveFeed';
import toast from 'react-hot-toast';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TABS = [
  { id: 'tools', label: 'MCP Tools', icon: Terminal, color: '#3b82f6' },
  { id: 'experiments', label: 'Experiments', icon: Cpu, color: '#10b981' },
  { id: 'runs', label: 'All Runs', icon: BarChart3, color: '#f59e0b' },
  { id: 'flow', label: 'Flow Visualizer', icon: Eye, color: '#8b5cf6' },
  { id: 'sessions', label: 'Live Sessions', icon: Swords, color: '#c084fc' },
];

const PRESETS = [
  { label: 'Search', tool: 'search_nodes', params: { query: '' } },
  { label: 'Add Knowledge', tool: 'add_knowledge', params: { content: '', node_type: 'Knowledge' } },
  { label: 'Briefing', tool: 'briefing', params: {} },
  { label: 'RAG Query', tool: 'rag_query', params: { question: '' } },
  { label: 'Graph Summary', tool: 'graph_summary', params: {} },
];

// ---------------------------------------------------------------------------
// Example recipes — pre-filled, immediately runnable workflows
// ---------------------------------------------------------------------------

const EXAMPLES = [
  {
    group: 'Getting Started',
    items: [
      {
        label: 'Orient the graph',
        description: "See what's in your graph",
        tool: 'orient',
        params: {},
      },
      {
        label: 'Search for anything',
        description: 'Find nodes by keyword',
        tool: 'search_nodes',
        params: { query: 'knowledge' },
      },
    ],
  },
  {
    group: 'Graph',
    items: [
      {
        label: 'Add a fact',
        description: 'Store a piece of knowledge',
        tool: 'add_knowledge',
        params: { content: 'The API uses JWT authentication', node_type: 'Fact' },
      },
      {
        label: 'Create a node',
        description: 'Create a Person node with AIQL',
        tool: 'query_graph',
        params: { query: 'CREATE NODE Person {name: "Alice", role: "Engineer"}' },
      },
      {
        label: 'Query with AIQL',
        description: 'Select all Person nodes',
        tool: 'query_graph',
        params: { query: 'SELECT * FROM Person' },
      },
    ],
  },
  {
    group: 'Search & Reasoning',
    items: [
      {
        label: 'RAG query',
        description: 'Ask a question grounded in graph data',
        tool: 'rag_query',
        params: { question: 'What are the main components of the system?' },
      },
      {
        label: 'Reason about graph',
        description: 'LLM analysis of graph patterns',
        tool: 'reason',
        params: { question: 'What patterns do you see in this graph?' },
      },
    ],
  },
  {
    group: 'Extraction',
    items: [
      {
        label: 'Extract entities',
        description: 'Pull typed entities from text',
        tool: 'extract_entities',
        params: { text: 'Alice, CEO of Acme Corp, met with the engineering team on Monday.' },
      },
      {
        label: 'Extract facts',
        description: 'Decompose text into atomic facts',
        tool: 'extract_facts',
        params: { text: 'The API rate limit is 100 requests per minute. Authentication uses OAuth2.' },
      },
    ],
  },
];

// ---------------------------------------------------------------------------
// Tool documentation — usage info, examples, "when to use" for every tool
// ---------------------------------------------------------------------------

const TOOL_INFO = {
  // ── Graph tools ──
  search_nodes: {
    when: 'Find existing entities, facts, or documents in the graph by label or property filter.',
    example: { label: 'Person', where: '{}' },
    tips: 'Use label to filter by node type (e.g., Person, Fact, Document). Use where as a JSON filter on properties.',
  },
  add_knowledge: {
    when: 'Store a new piece of information — a fact, observation, decision, or insight — as a graph node.',
    example: { content: 'The API uses JWT tokens for authentication', node_type: 'Fact', metadata: '{"source": "code review"}' },
    tips: 'Use node_type to categorize: Fact, Decision, Observation, Note, etc. Metadata is optional JSON.',
  },
  add_relationship: {
    when: 'Connect two existing nodes with a labeled edge (e.g., DEPENDS_ON, AUTHORED_BY, RELATED_TO).',
    example: { source_id: 'entity_abc123', target_id: 'entity_def456', label: 'DEPENDS_ON' },
    tips: 'Find node IDs first with search_nodes. Common edge labels: DEPENDS_ON, USES, AUTHORED_BY, RELATED_TO.',
  },
  query_graph: {
    when: 'Run an AIQL query directly against the graph (SELECT, CREATE, MATCH, etc.).',
    example: { query: 'SELECT * FROM Person' },
    tips: 'AIQL syntax: SELECT * FROM NodeType — use the type name as the collection. CREATE NODE Person {name: "Alice"}. MATCH NODE Person WHERE name = "Alice". Use SHOW GRAPHS to list available graphs.',
  },
  graph_summary: {
    when: 'Get a high-level overview of the graph — node counts, edge counts, types, and structure.',
    example: {},
    tips: 'No parameters needed. Good starting point to understand what data is in the graph.',
  },
  use_graph: {
    when: 'Switch the active graph namespace. All subsequent operations will target this graph.',
    example: { namespace: 'my_project' },
    tips: 'Each graph is an independent namespace. Create with query_graph first: CREATE GRAPH my_project.',
  },
  // ── Context tools ──
  briefing: {
    when: 'Get a context briefing — a structured summary of the current graph state for an LLM prompt.',
    example: {},
    tips: 'Returns formatted text ready to inject into an LLM system prompt. Includes key entities and relationships.',
  },
  get_context: {
    when: 'Build an LLM-ready context object from selected node types, with token budget control.',
    example: { node_types: '["Person", "Decision"]', max_tokens: 2000 },
    tips: 'Use node_types to filter. The result includes system prompt + relevant context within your token budget.',
  },
  get_agent_context: {
    when: 'Get personalized context for a specific agent based on its role and current task.',
    example: {},
    tips: 'Automatically filters context relevant to the agent\'s registered role and recent activity.',
  },
  // ── Search tools ──
  rag_query: {
    when: 'Ask a natural language question and get an answer grounded in graph knowledge (RAG).',
    example: { question: 'What are the main components of the system?' },
    tips: 'Parameter is "question" (not "query"). Combines graph search + LLM synthesis. Best for questions about ingested documents.',
  },
  rag_graph: {
    when: 'Like rag_query but also returns the subgraph of nodes that contributed to the answer.',
    example: { question: 'How does authentication work?' },
    tips: 'Parameter is "question" (not "query"). Returns both the answer text and the supporting graph nodes.',
  },
  search: {
    when: 'Full-text search across all node content and properties.',
    example: { query: 'database migration', limit: 10 },
    tips: 'Broader than search_nodes — searches all text content, not just node names.',
  },
  // ── Task tools ──
  add_task: {
    when: 'Create a task node in the graph that agents can claim and work on.',
    example: { title: 'Review authentication module', description: 'Check for security vulnerabilities', priority: 'high' },
    tips: 'Tasks form a work queue. Other agents can claim unblocked tasks with claim_task.',
  },
  claim_task: {
    when: 'Claim an available task so you can start working on it.',
    example: { task_id: 'task_abc123' },
    tips: 'Only unclaimed tasks can be claimed. Use list_tasks or get_unblocked_tasks to find available work.',
  },
  complete_task: {
    when: 'Mark a claimed task as complete, optionally with a result summary.',
    example: { task_id: 'task_abc123', result: 'Reviewed and approved — no vulnerabilities found.' },
    tips: 'Only the agent who claimed the task can complete it.',
  },
  list_tasks: {
    when: 'List all tasks in the graph, optionally filtered by status.',
    example: { status: 'pending' },
    tips: 'Status values: pending, in_progress, completed, blocked.',
  },
  get_unblocked_tasks: {
    when: 'Find tasks that are ready to work on — no blocking dependencies.',
    example: {},
    tips: 'Returns only tasks whose DEPENDS_ON predecessors are all completed.',
  },
  // ── Extraction tools ──
  extract_entities: {
    when: 'Use LLM to extract structured entities from a text passage.',
    example: { text: 'Alice, CEO of Acme Corp, presented the Q3 results at the board meeting.' },
    tips: 'Returns typed entities: Person, Organization, Event, etc. Works best with factual content.',
  },
  extract_facts: {
    when: 'Decompose a text passage into individual atomic facts using LLM.',
    example: { text: 'The new API uses REST over HTTPS with OAuth2 authentication and rate limiting of 100 req/min.' },
    tips: 'Each fact is a standalone statement. Useful for building fine-grained knowledge graphs.',
  },
  extract_with_schema: {
    when: 'Extract entities constrained by a specific schema — only extract what the schema defines.',
    example: { text: 'Alice manages the payments team.', schema_name: 'sdlc' },
    tips: 'Use list_schemas first to see available schemas. Schema-guided extraction is more precise.',
  },
  // ── Workspace tools ──
  ws_list_files: {
    when: 'List files in the agent workspace (git repo or local directory).',
    example: { path: '.' },
    tips: 'Workspace must be configured on the session. Returns file names and sizes.',
  },
  ws_read_file: {
    when: 'Read the contents of a file from the workspace.',
    example: { path: 'README.md' },
    tips: 'Returns full file content. Use for code review, documentation reading, etc.',
  },
  ws_write_file: {
    when: 'Write or overwrite a file in the workspace.',
    example: { path: 'notes.md', content: '# Meeting Notes\\n\\n- Decided to use PostgreSQL' },
    tips: 'Creates parent directories automatically. Be careful with existing files.',
  },
  ws_commit: {
    when: 'Commit staged changes in the workspace git repository.',
    example: { message: 'Add meeting notes from sprint review' },
    tips: 'Only works with git workspaces. Files must be staged first.',
  },
  ws_git_status: {
    when: 'Check the git status of the workspace — modified, staged, and untracked files.',
    example: {},
    tips: 'Quick way to see what changed before committing.',
  },
  // ── Other tools ──
  log_action: {
    when: 'Log an action to the audit trail — useful for tracking agent decisions and reasoning.',
    example: { action: 'Reviewed PR #42', description: 'Approved with minor comments on error handling' },
    tips: 'Both "action" and "description" are required. Creates an audit node visible in the Audit Trail.',
  },
  add_decision: {
    when: 'Record an architectural or product decision with rationale.',
    example: { title: 'Use PostgreSQL over MongoDB', rationale: 'Need ACID transactions for billing', alternatives: 'MongoDB, DynamoDB' },
    tips: 'Decisions become first-class graph nodes that can be queried and referenced later.',
  },
  reason: {
    when: 'Ask the LLM to reason about a question using the current graph context.',
    example: { question: 'What are the risks of removing the auth middleware?' },
    tips: 'Combines graph knowledge with LLM reasoning. More analytical than rag_query.',
  },
  check_freshness: {
    when: 'Check whether a specific node\'s source data is still current.',
    example: { node_id: '' },
    tips: 'Requires a node_id (UUID). Get node IDs from search_nodes or query_graph first. Returns source type, URI, last-fetched time, and stale/fresh status.',
  },
  detect_conflicts: {
    when: 'Find conflicting facts or contradictions in the graph.',
    example: {},
    tips: 'Scans for nodes that assert contradictory information. Helps maintain knowledge quality.',
  },
};

// ---------------------------------------------------------------------------
// Simulation presets — ready-to-run examples
// ---------------------------------------------------------------------------

const SIMULATION_PRESETS = [
  {
    label: 'Research Agent',
    description: 'Search the graph, summarize findings, and log the research action.',
    mode: 'script',
    agent: { name: 'Researcher', role: 'researcher' },
    steps: [
      { tool: 'graph_summary', params: {} },
      { tool: 'search_nodes', params: { label: '', where: '{}' } },
      { tool: 'briefing', params: {} },
      { tool: 'log_action', params: { action: 'Completed research', description: 'Gathered graph summary and searched nodes' } },
    ],
  },
  {
    label: 'Knowledge Builder',
    description: 'Add knowledge nodes and connect them with relationships.',
    mode: 'script',
    agent: { name: 'Knowledge Worker', role: 'writer' },
    steps: [
      { tool: 'add_knowledge', params: { content: 'The system uses a microservices architecture', node_type: 'Fact' } },
      { tool: 'add_knowledge', params: { content: 'PostgreSQL is the primary database', node_type: 'Fact' } },
      { tool: 'search_nodes', params: { label: '', where: '{}' } },
    ],
  },
  {
    label: 'Task Worker',
    description: 'List available tasks, claim one, and complete it.',
    mode: 'script',
    agent: { name: 'Task Agent', role: 'developer' },
    steps: [
      { tool: 'get_unblocked_tasks', params: {} },
      { tool: 'list_tasks', params: { status: 'pending' } },
    ],
  },
  {
    label: 'LLM Analyst',
    description: 'Use an LLM agent to analyze the graph and answer questions autonomously.',
    mode: 'llm',
    agent: { name: 'Analyst', role: 'analyst', prompt: 'You are a thorough analyst. Search the knowledge graph, find relevant information, and provide a detailed answer.' },
    task: 'What are the key entities and relationships in this graph? Summarize the main topics.',
  },
  {
    label: 'Code Reviewer',
    description: 'LLM agent that reviews code context and identifies issues.',
    mode: 'llm',
    agent: { name: 'Reviewer', role: 'reviewer', prompt: 'You are a senior code reviewer. Use the graph tools to find code-related entities and identify potential issues.' },
    task: 'Review the architecture decisions in the graph and flag any potential risks or conflicts.',
  },
  {
    label: 'Two Agents: Research & Review',
    description: 'A researcher gathers info, then a reviewer validates findings.',
    mode: 'plan',
    goal: 'I need two agents: a Researcher who searches the graph and gathers information about the key topics, and a Reviewer who checks for conflicts and validates the findings. The Researcher should go first, then the Reviewer checks the results and logs a summary.',
  },
  {
    label: 'Three Agents: Gather → Analyze → Report',
    description: 'Data collector, analyst, and reporter work in sequence.',
    mode: 'plan',
    goal: 'Set up three agents: (1) a Data Collector that searches the graph and retrieves key entities, (2) an Analyst that checks freshness, detects conflicts, and reasons about the data quality, and (3) a Reporter that creates a briefing and logs the final summary. They should work in order — collector first, then analyst, then reporter.',
  },
];

const TOOL_CATEGORIES = {
  graph: { label: 'Graph', color: '#3b82f6', icon: Database },
  context: { label: 'Context', color: '#8b5cf6', icon: Layers },
  search: { label: 'Search', color: '#f59e0b', icon: Search },
  tasks: { label: 'Tasks', color: '#10b981', icon: CheckCircle },
  extraction: { label: 'Extraction', color: '#ec4899', icon: Zap },
  workspace: { label: 'Workspace', color: '#06b6d4', icon: Settings },
  other: { label: 'Other', color: 'var(--neo-text-muted)', icon: Terminal },
};

const AGENT_ROLES = [
  'researcher', 'developer', 'reviewer', 'planner', 'analyst',
  'writer', 'tester', 'architect', 'assistant',
];

const FRAMEWORKS = [
  { value: '', label: 'None (raw tool calls)' },
  { value: 'langchain', label: 'LangChain' },
  { value: 'langgraph', label: 'LangGraph' },
  { value: 'crewai', label: 'CrewAI' },
  { value: 'autogen', label: 'AutoGen' },
  { value: 'openai', label: 'OpenAI Agents' },
  { value: 'llamaindex', label: 'LlamaIndex' },
  { value: 'pydantic_ai', label: 'Pydantic AI' },
  { value: 'swarm', label: 'Swarm' },
];

const NODE_COLORS = [
  '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
  '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#6366f1',
];

const AGENT_COLORS = [
  '#c084fc', '#60a5fa', '#34d399', '#fbbf24', '#f87171', '#2dd4bf', '#a78bfa', '#fb923c',
];

const HISTORY_KEY = 'playground_history';

function loadHistory() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]'); }
  catch { return []; }
}
function saveHistory(h) {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(h.slice(0, 30)));
}

// ---------------------------------------------------------------------------
// Shared small components
// ---------------------------------------------------------------------------

function Badge({ children, color = 'var(--neo-text-muted)', bg }) {
  return (
    <span
      className="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide"
      style={{
        color,
        background: bg || (color + '18'),
      }}
    >
      {children}
    </span>
  );
}

function StatusDot({ ok }) {
  return (
    <span
      className="inline-block w-2 h-2 rounded-full"
      style={{ background: ok ? '#22c55e' : '#ef4444' }}
    />
  );
}

function SectionCard({ children, className = '', style = {} }) {
  return (
    <div
      className={`rounded-xl ${className}`}
      style={{
        background: 'var(--neo-surface)',
        border: '1px solid var(--neo-border)',
        ...style,
      }}
    >
      {children}
    </div>
  );
}

function SelectInput({ value, onChange, options, placeholder, className = '', style = {} }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={`px-3 py-2 rounded-lg text-sm outline-none ${className}`}
      style={{
        background: 'var(--neo-bg)',
        border: '1px solid var(--neo-border)',
        color: 'var(--neo-text)',
        ...style,
      }}
    >
      {placeholder && <option key="__placeholder__" value="">{placeholder}</option>}
      {options.map((o, i) =>
        typeof o === 'string'
          ? <option key={o || `_opt_${i}`} value={o}>{o}</option>
          : <option key={o.value || `_opt_${i}`} value={o.value}>{o.label}</option>
      )}
    </select>
  );
}

function TextInput({ value, onChange, placeholder, mono, className = '', style = {}, ...rest }) {
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className={`px-3 py-2 rounded-lg text-sm outline-none ${className}`}
      style={{
        background: 'var(--neo-bg)',
        border: '1px solid var(--neo-border)',
        color: 'var(--neo-text)',
        ...(mono ? { fontFamily: "'JetBrains Mono', monospace" } : {}),
        ...style,
      }}
      {...rest}
    />
  );
}

function TextArea({ value, onChange, placeholder, rows = 4, mono, className = '', style = {} }) {
  return (
    <textarea
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      rows={rows}
      className={`w-full px-3 py-2 rounded-lg text-sm outline-none resize-y ${className}`}
      style={{
        background: 'var(--neo-bg)',
        border: '1px solid var(--neo-border)',
        color: 'var(--neo-text)',
        lineHeight: 1.6,
        ...(mono ? { fontFamily: "'JetBrains Mono', monospace" } : {}),
        ...style,
      }}
    />
  );
}

function PrimaryButton({ onClick, disabled, children, color = 'var(--neo-blue)', className = '' }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90 disabled:opacity-50 ${className}`}
      style={{ background: color, color: '#fff' }}
    >
      {children}
    </button>
  );
}

function GhostButton({ onClick, children, className = '', style = {} }) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs transition hover:opacity-80 ${className}`}
      style={{
        background: 'var(--neo-bg)',
        border: '1px solid var(--neo-border)',
        color: 'var(--neo-text-muted)',
        ...style,
      }}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Tab 1: MCP Tool Tester
// ---------------------------------------------------------------------------

function categorize(tool) {
  const n = (tool.name || '').toLowerCase();
  const cats = tool.categories || [];
  if (cats.length) return cats[0];
  if (/graph|node|relationship|edge/.test(n)) return 'graph';
  if (/context|briefing|scope/.test(n)) return 'context';
  if (/search|rag|query/.test(n)) return 'search';
  if (/task/.test(n)) return 'tasks';
  if (/extract|schema|entity|fact/.test(n)) return 'extraction';
  if (/ws_|workspace|commit|push|branch/.test(n)) return 'workspace';
  return 'other';
}

function buildParamInput(name, schema, value, onChange) {
  const type = schema?.type || 'string';
  if (type === 'boolean') {
    return (
      <label className="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={!!value}
          onChange={(e) => onChange(e.target.checked)}
          className="rounded"
        />
        <span className="text-xs" style={{ color: 'var(--neo-text)' }}>{value ? 'true' : 'false'}</span>
      </label>
    );
  }
  if (type === 'integer' || type === 'number') {
    return (
      <TextInput
        type="number"
        value={value ?? ''}
        onChange={(v) => onChange(v === '' ? '' : Number(v))}
        placeholder={schema?.default != null ? String(schema.default) : '0'}
        mono
        style={{ width: 120 }}
      />
    );
  }
  if (type === 'object' || type === 'array') {
    return (
      <TextArea
        value={typeof value === 'string' ? value : JSON.stringify(value || (type === 'array' ? [] : {}), null, 2)}
        onChange={onChange}
        placeholder={type === 'array' ? '[]' : '{}'}
        rows={3}
        mono
      />
    );
  }
  // default: string
  const isLong = (schema?.description || '').length > 80 || name === 'content' || name === 'prompt' || name === 'text';
  if (isLong) {
    return (
      <TextArea
        value={value ?? ''}
        onChange={onChange}
        placeholder={schema?.description || ''}
        rows={3}
        mono
      />
    );
  }
  return (
    <TextInput
      value={value ?? ''}
      onChange={onChange}
      placeholder={schema?.description || schema?.default || ''}
      mono
      className="w-full"
    />
  );
}

function ToolTesterTab({ graphs }) {
  const [tools, setTools] = useState([]);
  const [selectedTool, setSelectedTool] = useState('');
  const [toolParams, setToolParams] = useState({});
  const [toolResult, setToolResult] = useState(null);
  const [toolRunning, setToolRunning] = useState(false);
  const [toolGraph, setToolGraph] = useState(() => localStorage.getItem('playground_graph') || '');
  const [toolHistory, setToolHistory] = useState(() => loadHistory());
  const [expandedCat, setExpandedCat] = useState(null);
  const [copied, setCopied] = useState(false);
  const [filterText, setFilterText] = useState('');
  const [showExamples, setShowExamples] = useState(false);

  useEffect(() => {
    api.get('/dashboard/playground/tools')
      .then((r) => setTools(r.data.tools || []))
      .catch(() => {});
  }, []);

  const toolObj = tools.find((t) => t.name === selectedTool);

  const grouped = {};
  tools.forEach((t) => {
    const cat = categorize(t);
    if (!grouped[cat]) grouped[cat] = [];
    grouped[cat].push(t);
  });

  const selectTool = useCallback((name) => {
    setSelectedTool(name);
    setToolParams({});
    setToolResult(null);
  }, []);

  const setParam = useCallback((key, val) => {
    setToolParams((prev) => ({ ...prev, [key]: val }));
  }, []);

  const runTool = useCallback(async () => {
    if (!selectedTool) return;
    setToolRunning(true);
    setToolResult(null);
    const start = performance.now();
    try {
      // Resolve object/array params from JSON strings
      const resolved = {};
      const props = toolObj?.params || toolObj?.parameters?.properties || {};
      Object.entries(toolParams).forEach(([k, v]) => {
        if (v === '' || v === undefined || v === null) return;
        const schema = props[k] || {};
        if ((schema.type === 'object' || schema.type === 'array') && typeof v === 'string') {
          try { resolved[k] = JSON.parse(v); } catch { resolved[k] = v; }
        } else {
          resolved[k] = v;
        }
      });
      const payload = { tool_name: selectedTool, params: resolved };
      if (toolGraph) payload.graph = toolGraph;

      const res = await api.post('/dashboard/playground/tool', payload);
      const dur = Math.round(performance.now() - start);
      const entry = {
        tool: selectedTool,
        params: resolved,
        graph: toolGraph,
        status: res.status,
        duration: dur,
        response: res.data,
        ts: new Date().toISOString(),
        ok: true,
      };
      setToolResult(entry);
      setToolHistory((prev) => {
        const next = [entry, ...prev].slice(0, 30);
        saveHistory(next);
        return next;
      });
    } catch (err) {
      const dur = Math.round(performance.now() - start);
      const entry = {
        tool: selectedTool,
        params: toolParams,
        graph: toolGraph,
        status: err.response?.status || 0,
        duration: dur,
        response: err.response?.data || { error: err.message },
        ts: new Date().toISOString(),
        ok: false,
      };
      setToolResult(entry);
      setToolHistory((prev) => {
        const next = [entry, ...prev].slice(0, 30);
        saveHistory(next);
        return next;
      });
    } finally {
      setToolRunning(false);
    }
  }, [selectedTool, toolParams, toolGraph, toolObj]);

  const replayHistoryItem = useCallback((item) => {
    setSelectedTool(item.tool);
    setToolParams(item.params || {});
    setToolGraph(item.graph || '');
    setToolResult(item);
  }, []);

  const runExample = useCallback(async (item) => {
    setSelectedTool(item.tool);
    setToolResult(null);
    setToolParams({ ...item.params });
    setShowExamples(false);
    setToolRunning(true);
    const start = performance.now();
    try {
      const payload = { tool_name: item.tool, params: item.params };
      if (toolGraph) payload.graph = toolGraph;
      const res = await api.post('/dashboard/playground/tool', payload);
      const dur = Math.round(performance.now() - start);
      const entry = {
        tool: item.tool,
        params: item.params,
        graph: toolGraph,
        status: res.status,
        duration: dur,
        response: res.data,
        ts: new Date().toISOString(),
        ok: true,
      };
      setToolResult(entry);
      setToolHistory((prev) => {
        const next = [entry, ...prev].slice(0, 30);
        saveHistory(next);
        return next;
      });
    } catch (err) {
      const dur = Math.round(performance.now() - start);
      const entry = {
        tool: item.tool,
        params: item.params,
        graph: toolGraph,
        status: err.response?.status || 0,
        duration: dur,
        response: err.response?.data || { error: err.message },
        ts: new Date().toISOString(),
        ok: false,
      };
      setToolResult(entry);
      setToolHistory((prev) => {
        const next = [entry, ...prev].slice(0, 30);
        saveHistory(next);
        return next;
      });
    } finally {
      setToolRunning(false);
    }
  }, [selectTool, toolGraph]);

  const applyPreset = useCallback((preset) => {
    selectTool(preset.tool);
    setToolParams({ ...preset.params });
  }, [selectTool]);

  const copyResult = () => {
    if (!toolResult) return;
    navigator.clipboard.writeText(JSON.stringify(toolResult.response, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const filteredGrouped = {};
  const lf = filterText.toLowerCase();
  Object.entries(grouped).forEach(([cat, list]) => {
    const filtered = lf ? list.filter((t) => t.name.toLowerCase().includes(lf) || (t.description || '').toLowerCase().includes(lf)) : list;
    if (filtered.length) filteredGrouped[cat] = filtered;
  });

  const paramEntries = toolObj
    ? Object.entries(toolObj.params || toolObj.parameters?.properties || {})
    : [];
  const requiredParams = toolObj?.required || toolObj?.parameters?.required || [];

  return (
    <div className="flex flex-col gap-4">
      {/* Graph selector + presets */}
      <div className="flex flex-wrap items-center gap-2">
        <SelectInput
          value={toolGraph}
          onChange={(v) => { setToolGraph(v); if (v) localStorage.setItem('playground_graph', v); else localStorage.removeItem('playground_graph'); }}
          options={graphs.map((g) => { const n = typeof g === 'string' ? g : (g.name || g.display_name || ''); return { value: n, label: n }; })}
          placeholder="Select graph..."
          style={{ minWidth: 180 }}
        />
        <div className="h-4 w-px mx-1" style={{ background: 'var(--neo-border)' }} />
        {PRESETS.map((p) => (
          <GhostButton key={p.label} onClick={() => applyPreset(p)}>
            <Zap size={10} />
            {p.label}
          </GhostButton>
        ))}
      </div>

      <div className="flex gap-4" style={{ minHeight: 500 }}>
        {/* Left: Tool selector */}
        <SectionCard className="flex-shrink-0 overflow-hidden" style={{ width: 260 }}>
          {/* Examples panel */}
          <div>
            <button
              onClick={() => setShowExamples((v) => !v)}
              className="w-full flex items-center gap-2 px-3 py-2 text-xs font-semibold hover:opacity-80 transition"
              style={{ color: 'var(--neo-text-muted)' }}
            >
              {showExamples ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
              <Zap size={10} />
              Examples
            </button>
            {showExamples && (
              <div className="pb-2">
                {EXAMPLES.map((group) => (
                  <div key={group.group}>
                    <div
                      className="px-3 py-1 text-[10px] font-semibold uppercase tracking-wide"
                      style={{ color: 'var(--neo-text-dim)' }}
                    >
                      {group.group}
                    </div>
                    {group.items.map((item) => (
                      <button
                        key={item.label}
                        onClick={() => runExample(item)}
                        className="w-full text-left px-4 py-1.5 hover:opacity-80 disabled:opacity-50 disabled:cursor-not-allowed transition"
                        disabled={toolRunning}
                      >
                        <div className="text-xs font-medium truncate" style={{ color: 'var(--neo-text)' }}>
                          {item.label}
                        </div>
                        <div className="text-[10px] truncate" style={{ color: 'var(--neo-text-dim)' }}>
                          {item.description}
                        </div>
                      </button>
                    ))}
                  </div>
                ))}
                <div className="mx-3 mt-1" style={{ borderBottom: '1px solid var(--neo-border)' }} />
              </div>
            )}
          </div>

          <div className="p-2">
            <TextInput
              value={filterText}
              onChange={setFilterText}
              placeholder="Filter tools..."
              className="w-full"
              style={{ fontSize: 12 }}
            />
          </div>
          <div className="overflow-y-auto" style={{ maxHeight: 480 }}>
            {Object.entries(filteredGrouped).map(([cat, list]) => {
              const catMeta = TOOL_CATEGORIES[cat] || TOOL_CATEGORIES.other;
              const CatIcon = catMeta.icon;
              const isOpen = expandedCat === cat || expandedCat === null || !!filterText;
              return (
                <div key={cat}>
                  <button
                    onClick={() => setExpandedCat(expandedCat === cat ? null : cat)}
                    className="w-full flex items-center gap-2 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide hover:opacity-80"
                    style={{ color: catMeta.color }}
                  >
                    {isOpen ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                    <CatIcon size={11} />
                    {catMeta.label}
                    <span className="ml-auto text-[10px] opacity-60">{list.length}</span>
                  </button>
                  {isOpen && list.map((t) => {
                    const info = TOOL_INFO[t.name];
                    return (
                      <button
                        key={t.name}
                        onClick={() => selectTool(t.name)}
                        className="w-full text-left px-4 py-1.5 text-xs transition"
                        style={{
                          color: selectedTool === t.name ? catMeta.color : 'var(--neo-text-muted)',
                          background: selectedTool === t.name ? catMeta.color + '12' : 'transparent',
                          borderLeft: selectedTool === t.name ? `2px solid ${catMeta.color}` : '2px solid transparent',
                        }}
                      >
                        <div className="truncate font-medium">{t.name}</div>
                        {info?.when && (
                          <div className="truncate text-[10px] mt-0.5" style={{ color: 'var(--neo-text-dim)', opacity: 0.7 }}>
                            {info.when.slice(0, 60)}{info.when.length > 60 ? '...' : ''}
                          </div>
                        )}
                      </button>
                    );
                  })}
                </div>
              );
            })}
            {Object.keys(filteredGrouped).length === 0 && (
              <p className="px-4 py-6 text-xs text-center" style={{ color: 'var(--neo-text-muted)' }}>
                {tools.length === 0 ? 'Loading tools...' : 'No tools match filter'}
              </p>
            )}
          </div>
        </SectionCard>

        {/* Center: Param form + result */}
        <div className="flex-1 flex flex-col gap-4 min-w-0">
          {toolObj ? (
            <>
              <SectionCard className="p-4">
                <div className="flex items-center gap-2 mb-1">
                  <Terminal size={14} style={{ color: 'var(--neo-blue)' }} />
                  <span className="text-sm font-semibold" style={{ color: 'var(--neo-text)' }}>
                    {toolObj.name}
                  </span>
                </div>
                {toolObj.description && (
                  <p className="text-xs mb-2" style={{ color: 'var(--neo-text-muted)' }}>
                    {toolObj.description}
                  </p>
                )}

                {/* Tool info panel — when to use, example, tips */}
                {TOOL_INFO[toolObj.name] && (
                  <div className="mb-3 p-2.5 rounded-lg space-y-2" style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)' }}>
                    {TOOL_INFO[toolObj.name].when && (
                      <div>
                        <span className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: '#3b82f6' }}>When to use</span>
                        <p className="text-xs mt-0.5" style={{ color: 'var(--neo-text)' }}>{TOOL_INFO[toolObj.name].when}</p>
                      </div>
                    )}
                    {TOOL_INFO[toolObj.name].tips && (
                      <div>
                        <span className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: '#f59e0b' }}>Tips</span>
                        <p className="text-xs mt-0.5" style={{ color: 'var(--neo-text-muted)' }}>{TOOL_INFO[toolObj.name].tips}</p>
                      </div>
                    )}
                    {TOOL_INFO[toolObj.name].example && (
                      <div>
                        <span className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: '#10b981' }}>Example</span>
                        <button
                          onClick={() => { setToolParams(TOOL_INFO[toolObj.name].example); }}
                          className="flex items-center gap-1 mt-1 text-[10px] px-2 py-1 rounded transition"
                          style={{ background: 'rgba(16,185,129,0.08)', color: '#10b981', border: '1px solid rgba(16,185,129,0.2)' }}
                        >
                          <Play size={9} /> Load example params
                        </button>
                      </div>
                    )}
                  </div>
                )}

                {paramEntries.length > 0 ? (
                  <div className="flex flex-col gap-3">
                    {paramEntries.map(([pName, pSchema]) => {
                      const isRequired = requiredParams.includes(pName);
                      return (
                        <div key={pName}>
                          <div className="flex items-center gap-2 mb-1">
                            <span className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>
                              {pName}
                            </span>
                            {pSchema?.type && (
                              <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                                {pSchema.type}
                              </span>
                            )}
                            {isRequired && <Badge color="#ef4444">required</Badge>}
                          </div>
                          {buildParamInput(pName, pSchema, toolParams[pName], (v) => setParam(pName, v))}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                    No parameters required.
                  </p>
                )}

                <div className="mt-4">
                  <PrimaryButton onClick={runTool} disabled={toolRunning}>
                    {toolRunning ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                    Run Tool
                  </PrimaryButton>
                </div>
              </SectionCard>

              {/* Result */}
              {(toolResult || toolRunning) && (
                <SectionCard className="overflow-hidden">
                  <div
                    className="flex items-center justify-between px-4 py-2"
                    style={{ borderBottom: '1px solid var(--neo-border)' }}
                  >
                    <div className="flex items-center gap-3">
                      <span className="text-xs font-semibold" style={{ color: 'var(--neo-text-muted)' }}>
                        Response
                      </span>
                      {toolResult && (
                        <>
                          <span
                            className="px-2 py-0.5 rounded text-xs font-bold"
                            style={{
                              background: toolResult.ok ? 'rgba(76,217,100,0.15)' : 'rgba(242,87,87,0.15)',
                              color: toolResult.ok ? '#22c55e' : '#ef4444',
                            }}
                          >
                            {toolResult.status}
                          </span>
                          <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                            {toolResult.duration}ms
                          </span>
                        </>
                      )}
                    </div>
                    {toolResult && (
                      <button onClick={copyResult} className="flex items-center gap-1 text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                        {copied ? <Check size={10} /> : <Copy size={10} />}
                        {copied ? 'Copied' : 'Copy'}
                      </button>
                    )}
                  </div>
                  <pre
                    className="p-4 text-xs overflow-auto"
                    style={{
                      background: 'var(--neo-bg)',
                      color: toolResult?.ok ? 'var(--neo-green, #22c55e)' : 'var(--neo-text)',
                      fontFamily: "'JetBrains Mono', monospace",
                      lineHeight: 1.6,
                      maxHeight: 320,
                    }}
                  >
                    {toolRunning ? 'Running...' : JSON.stringify(toolResult?.response, null, 2)}
                  </pre>
                </SectionCard>
              )}
            </>
          ) : (
            <SectionCard className="flex items-center justify-center" style={{ minHeight: 300 }}>
              <div className="text-center">
                <Terminal size={32} style={{ color: 'var(--neo-border)', margin: '0 auto 8px' }} />
                <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
                  Select a tool from the left panel or use a quick preset.
                </p>
              </div>
            </SectionCard>
          )}
        </div>

        {/* Right: History */}
        <SectionCard className="flex-shrink-0 overflow-hidden" style={{ width: 220 }}>
          <div
            className="flex items-center justify-between px-3 py-2 text-xs font-semibold"
            style={{ color: 'var(--neo-text-muted)', borderBottom: '1px solid var(--neo-border)' }}
          >
            <span className="flex items-center gap-1"><Clock size={11} /> History</span>
            {toolHistory.length > 0 && (
              <button
                onClick={() => { setToolHistory([]); saveHistory([]); }}
                className="hover:opacity-80"
                style={{ color: 'var(--neo-text-muted)' }}
              >
                <Trash2 size={10} />
              </button>
            )}
          </div>
          <div className="overflow-y-auto" style={{ maxHeight: 480 }}>
            {toolHistory.slice(0, 10).map((h, i) => (
              <button
                key={i}
                onClick={() => replayHistoryItem(h)}
                className="w-full text-left px-3 py-2 text-xs transition hover:opacity-80"
                style={{ borderBottom: '1px solid var(--neo-border)' }}
              >
                <div className="flex items-center gap-1.5">
                  <StatusDot ok={h.ok} />
                  <span className="truncate font-medium" style={{ color: 'var(--neo-text)' }}>
                    {h.tool}
                  </span>
                </div>
                <div className="flex items-center gap-2 mt-0.5" style={{ color: 'var(--neo-text-muted)' }}>
                  <span>{h.duration}ms</span>
                  <span>{new Date(h.ts).toLocaleTimeString()}</span>
                </div>
              </button>
            ))}
            {toolHistory.length === 0 && (
              <p className="px-3 py-6 text-xs text-center" style={{ color: 'var(--neo-text-muted)' }}>
                No history yet
              </p>
            )}
          </div>
        </SectionCard>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 2: Agent Simulator
// ---------------------------------------------------------------------------

function AgentSimulatorTab({ graphs, tools: allTools }) {
  const [simMode, setSimMode] = useState('script');
  const [agents, setAgents] = useState([
    { name: 'Agent 1', role: 'researcher', prompt: '', context: '' },
  ]);
  const [scriptSteps, setScriptSteps] = useState([]);
  const [simRunning, setSimRunning] = useState(false);
  const [activityLog, setActivityLog] = useState([]);
  const [contexts, setContexts] = useState([]);
  const [simGraph, setSimGraph] = useState('');

  // Connection settings
  const [framework, setFramework] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [showConnection, setShowConnection] = useState(false);
  const [sessions, setSessions] = useState([]);

  // LLM mode
  const [llmTask, setLlmTask] = useState('');
  const [llmModel, setLlmModel] = useState('');
  const [maxSteps, setMaxSteps] = useState(10);

  // Plan mode
  const [planGoal, setPlanGoal] = useState('');
  const [planTasks, setPlanTasks] = useState(null); // the generated plan
  const [planAutoExec, setPlanAutoExec] = useState(true);

  // Step builder
  const [newStepTool, setNewStepTool] = useState('');
  const [newStepParams, setNewStepParams] = useState('{}');

  const logRef = useRef(null);

  useEffect(() => {
    api.get('/dashboard/contexts').then((r) => setContexts(r.data.contexts || [])).catch(() => {});
    api.get('/dashboard/sessions').then((r) => setSessions(r.data.sessions || r.data || [])).catch(() => {});
  }, []);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [activityLog]);

  const agent = agents[0];
  const updateAgent = useCallback((patch) => {
    setAgents((prev) => [{ ...prev[0], ...patch }]);
  }, []);

  const addStep = useCallback(() => {
    if (!newStepTool) return;
    let parsed = {};
    try { parsed = JSON.parse(newStepParams); } catch { /* ignore */ }
    setScriptSteps((prev) => [...prev, { tool: newStepTool, params: parsed }]);
    setNewStepParams('{}');
  }, [newStepTool, newStepParams]);

  const removeStep = useCallback((idx) => {
    setScriptSteps((prev) => prev.filter((_, i) => i !== idx));
  }, []);

  const addLogEntry = useCallback((entry) => {
    setActivityLog((prev) => [...prev, { ...entry, ts: new Date().toISOString() }]);
  }, []);

  const runScript = useCallback(async () => {
    if (scriptSteps.length === 0) { toast.error('Add at least one step'); return; }
    setSimRunning(true);
    setActivityLog([]);
    addLogEntry({ type: 'info', msg: `Starting simulation with ${scriptSteps.length} steps...` });
    try {
      const res = await api.post('/dashboard/playground/simulate', {
        agent: { name: agent.name, role: agent.role, context: agent.context },
        steps: scriptSteps,
        graph: simGraph,
        framework: framework || undefined,
        session_id: sessionId || undefined,
        api_key: apiKey || undefined,
      });
      const results = res.data.results || res.data.steps || [];
      results.forEach((r, i) => {
        addLogEntry({
          type: r.error ? 'error' : 'result',
          tool: scriptSteps[i]?.tool,
          msg: r.error || JSON.stringify(r.result || r, null, 2),
          step: i + 1,
        });
      });
      addLogEntry({ type: 'info', msg: 'Simulation complete.' });
      toast.success('Simulation finished');
    } catch (err) {
      addLogEntry({ type: 'error', msg: err.response?.data?.detail || err.message });
      toast.error('Simulation failed');
    } finally {
      setSimRunning(false);
    }
  }, [scriptSteps, agent, simGraph, addLogEntry]);

  const runLLM = useCallback(async () => {
    if (!llmTask.trim()) { toast.error('Enter a task for the agent'); return; }
    setSimRunning(true);
    setActivityLog([]);
    addLogEntry({ type: 'info', msg: `Running LLM agent: "${llmTask}"` });
    try {
      const res = await api.post('/dashboard/playground/simulate/llm', {
        agent: { name: agent.name, role: agent.role, prompt: agent.prompt, context: agent.context },
        task: llmTask,
        graph: simGraph,
        llm_model: llmModel || undefined,
        max_steps: maxSteps,
        framework: framework || undefined,
        session_id: sessionId || undefined,
        api_key: apiKey || undefined,
      });
      const steps = res.data.steps || res.data.results || [];
      steps.forEach((s, i) => {
        if (s.reasoning) addLogEntry({ type: 'reasoning', msg: s.reasoning, step: i + 1 });
        if (s.tool_call) addLogEntry({ type: 'tool', tool: s.tool_call.tool, msg: JSON.stringify(s.tool_call.params, null, 2), step: i + 1 });
        if (s.result) addLogEntry({ type: 'result', msg: typeof s.result === 'string' ? s.result : JSON.stringify(s.result, null, 2), step: i + 1 });
        if (s.error) addLogEntry({ type: 'error', msg: s.error, step: i + 1 });
      });
      if (res.data.final_answer) addLogEntry({ type: 'answer', msg: res.data.final_answer });
      addLogEntry({ type: 'info', msg: 'Agent run complete.' });
      toast.success('Agent run finished');
    } catch (err) {
      addLogEntry({ type: 'error', msg: err.response?.data?.detail || err.message });
      toast.error('Agent run failed');
    } finally {
      setSimRunning(false);
    }
  }, [llmTask, llmModel, maxSteps, agent, simGraph, addLogEntry]);

  const stopSim = useCallback(() => {
    setSimRunning(false);
    addLogEntry({ type: 'info', msg: 'Stopped by user.' });
  }, [addLogEntry]);

  const runPlan = useCallback(async () => {
    if (!planGoal.trim()) { toast.error('Enter a goal for the planner'); return; }
    setSimRunning(true);
    setActivityLog([]);
    setPlanTasks(null);
    addLogEntry({ type: 'info', msg: `Planner decomposing goal: "${planGoal}"` });
    try {
      const res = await api.post('/dashboard/playground/simulate/plan', {
        goal: planGoal,
        graph: simGraph,
        llm_model: llmModel || undefined,
        max_tasks: maxSteps,
        auto_execute: planAutoExec,
        framework: framework || undefined,
        session_id: sessionId || undefined,
        api_key: apiKey || undefined,
      });

      // Show agents and plan
      const agents = res.data.agents || [];
      const plan = res.data.plan || [];
      setPlanTasks({ agents, tasks: plan });
      if (agents.length > 0) {
        addLogEntry({ type: 'info', msg: `Agents: ${agents.map(a => `${a.name} (${a.role})`).join(', ')}` });
      }
      addLogEntry({ type: 'info', msg: `Plan created: ${plan.length} tasks across ${agents.length} agent(s) (${res.data.plan_duration_ms}ms)` });
      plan.forEach((t) => {
        const deps = (t.depends_on || []).length ? ` [after step ${t.depends_on.join(', ')}]` : '';
        const agentTag = t.agent ? `[${t.agent}] ` : '';
        addLogEntry({ type: 'reasoning', msg: `Step ${t.step}: ${agentTag}${t.description}${deps}`, step: t.step });
      });

      // Show execution results
      if (res.data.execution) {
        addLogEntry({ type: 'info', msg: 'Executing plan...' });
        res.data.execution.forEach((r) => {
          const agentTag = r.agent ? `[${r.agent}] ` : '';
          if (r.skipped) {
            addLogEntry({ type: 'error', msg: `${agentTag}Skipped: ${r.reason}`, step: r.step });
          } else if (r.success) {
            addLogEntry({
              type: 'result',
              tool: r.tool,
              msg: `${agentTag}${r.description}\n${typeof r.result === 'string' ? r.result : JSON.stringify(r.result, null, 2)}`,
              step: r.step,
            });
          } else {
            addLogEntry({ type: 'error', tool: r.tool, msg: `${agentTag}${r.error}`, step: r.step });
          }
        });
        addLogEntry({
          type: 'info',
          msg: `Plan complete. ${res.data.steps_completed}/${plan.length} tasks executed in ${res.data.total_duration_ms}ms.`,
        });
      } else {
        addLogEntry({ type: 'info', msg: 'Plan generated (auto-execute disabled). Review and run manually.' });
      }
      toast.success('Planner finished');
    } catch (err) {
      addLogEntry({ type: 'error', msg: err.response?.data?.detail || err.message });
      toast.error('Planner failed');
    } finally {
      setSimRunning(false);
    }
  }, [planGoal, planAutoExec, llmModel, maxSteps, agent, simGraph, framework, sessionId, apiKey, addLogEntry]);

  const logColors = {
    info: 'var(--neo-text-muted)',
    result: '#22c55e',
    error: '#ef4444',
    tool: '#3b82f6',
    reasoning: '#8b5cf6',
    answer: '#f59e0b',
    plan: '#06b6d4',
  };

  const toolOptions = (allTools || []).map((t) => ({ value: t.name, label: t.name }));

  const applySimPreset = useCallback((preset) => {
    setSimMode(preset.mode);
    if (preset.agent) {
      setAgents([{ ...agents[0], ...preset.agent }]);
    }
    if (preset.steps) {
      setScriptSteps(preset.steps);
    }
    if (preset.task) {
      setLlmTask(preset.task);
    }
    if (preset.goal) {
      setPlanGoal(preset.goal);
      setPlanTasks(null);
    }
    setActivityLog([]);
    toast.success(`Loaded "${preset.label}" preset`);
  }, [agents]);

  return (
    <div className="flex flex-col gap-4">
      {/* Mode toggle + graph */}
      <div className="flex items-center gap-3">
        <div
          className="flex rounded-lg overflow-hidden"
          style={{ border: '1px solid var(--neo-border)' }}
        >
          {[['script', 'Script'], ['llm', 'LLM'], ['plan', 'Planner']].map(([m, label]) => (
            <button
              key={m}
              onClick={() => setSimMode(m)}
              className="px-4 py-2 text-xs font-semibold transition"
              style={{
                background: simMode === m ? 'var(--neo-blue)' : 'var(--neo-surface)',
                color: simMode === m ? '#fff' : 'var(--neo-text-muted)',
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <SelectInput
          value={simGraph}
          onChange={setSimGraph}
          options={graphs.map((g) => { const n = typeof g === 'string' ? g : (g.name || g.display_name || ''); return { value: n, label: n }; })}
          placeholder="Select graph..."
          style={{ minWidth: 180 }}
        />
      </div>

      {/* Simulation presets */}
      <div className="flex flex-wrap items-start gap-2">
        <span className="text-xs font-medium py-1.5" style={{ color: 'var(--neo-text-dim)' }}>Try:</span>
        {SIMULATION_PRESETS.map((p) => (
          <button
            key={p.label}
            onClick={() => applySimPreset(p)}
            className="group flex flex-col text-left px-3 py-2 rounded-lg transition hover:shadow-sm"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', maxWidth: 200 }}
          >
            <div className="flex items-center gap-1.5">
              {p.mode === 'plan' ? <Layers size={10} style={{ color: '#06b6d4' }} /> : p.mode === 'llm' ? <Cpu size={10} style={{ color: '#10b981' }} /> : <Play size={10} style={{ color: '#3b82f6' }} />}
              <span className="text-xs font-medium" style={{ color: 'var(--neo-text)' }}>{p.label}</span>
              <span className="text-[9px] px-1 py-0.5 rounded" style={{
                background: p.mode === 'plan' ? 'rgba(6,182,212,0.1)' : p.mode === 'llm' ? 'rgba(16,185,129,0.1)' : 'rgba(59,130,246,0.1)',
                color: p.mode === 'plan' ? '#06b6d4' : p.mode === 'llm' ? '#10b981' : '#3b82f6',
              }}>
                {p.mode}
              </span>
            </div>
            <span className="text-[10px] mt-0.5 line-clamp-2" style={{ color: 'var(--neo-text-dim)' }}>{p.description}</span>
          </button>
        ))}
      </div>

      <div className="flex gap-4" style={{ minHeight: 500 }}>
        {/* Left: Agent config + steps */}
        <div className="flex flex-col gap-4" style={{ width: 380, flexShrink: 0 }}>
          {/* Agent config */}
          <SectionCard className="p-4">
            <h3 className="text-xs font-semibold uppercase tracking-wide mb-3" style={{ color: 'var(--neo-text-muted)' }}>
              Agent Configuration
            </h3>
            <div className="flex flex-col gap-2">
              <TextInput
                value={agent.name}
                onChange={(v) => updateAgent({ name: v })}
                placeholder="Agent name"
              />
              <SelectInput
                value={agent.role}
                onChange={(v) => updateAgent({ role: v })}
                options={AGENT_ROLES}
              />
              <SelectInput
                value={framework}
                onChange={setFramework}
                options={FRAMEWORKS}
                placeholder="Framework..."
              />
              <SelectInput
                value={agent.context}
                onChange={(v) => updateAgent({ context: v })}
                options={contexts.map((c) => ({ value: c.context_id || c.id || c.name, label: c.name }))}
                placeholder="Select context..."
              />
              {simMode === 'llm' && (
                <TextArea
                  value={agent.prompt}
                  onChange={(v) => updateAgent({ prompt: v })}
                  placeholder="System prompt (optional)"
                  rows={3}
                />
              )}
            </div>

            {/* Connection settings */}
            <button
              onClick={() => setShowConnection(!showConnection)}
              className="flex items-center gap-1.5 mt-3 text-xs"
              style={{ color: 'var(--neo-text-muted)' }}
            >
              {showConnection ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              Connection Settings
              {(sessionId || apiKey) && (
                <span className="w-1.5 h-1.5 rounded-full" style={{ background: '#10b981' }} />
              )}
            </button>
            {showConnection && (
              <div className="flex flex-col gap-2 mt-2 pt-2" style={{ borderTop: '1px solid var(--neo-border)' }}>
                <label className="text-xs" style={{ color: 'var(--neo-text-dim)' }}>Session</label>
                <div className="flex gap-1.5">
                  <SelectInput
                    value={sessionId}
                    onChange={setSessionId}
                    options={[
                      { value: '', label: 'New session' },
                      ...sessions.map((s) => ({
                        value: s.session_id || s.id || s.name,
                        label: `${s.name || s.session_id || s.id}${s.agent_count ? ` (${s.agent_count} agents)` : ''}`,
                      })),
                    ]}
                    placeholder="Session..."
                  />
                </div>
                <TextInput
                  value={sessionId}
                  onChange={setSessionId}
                  placeholder="Or paste session ID..."
                  style={{ fontFamily: 'monospace', fontSize: 11 }}
                />
                <label className="text-xs mt-1" style={{ color: 'var(--neo-text-dim)' }}>API Key</label>
                <TextInput
                  type="password"
                  value={apiKey}
                  onChange={setApiKey}
                  placeholder="Agent API key (optional)"
                  style={{ fontFamily: 'monospace', fontSize: 11 }}
                />
                <p className="text-xs mt-0.5" style={{ color: 'var(--neo-text-dim)' }}>
                  Connect to an existing session to simulate within its boundary. Provide an API key to authenticate as an agent.
                </p>
              </div>
            )}
          </SectionCard>

          {/* Mode-specific panels */}
          {simMode === 'script' && (
            <SectionCard className="p-4 flex-1">
              <h3 className="text-xs font-semibold uppercase tracking-wide mb-3" style={{ color: 'var(--neo-text-muted)' }}>
                Steps
              </h3>
              <div className="flex flex-col gap-1.5 mb-3">
                {scriptSteps.map((s, i) => (
                  <div key={i} className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs"
                    style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)' }}>
                    <span className="font-bold" style={{ color: 'var(--neo-blue)', minWidth: 18 }}>{i + 1}.</span>
                    <span className="flex-1 truncate font-medium" style={{ color: 'var(--neo-text)' }}>{s.tool}</span>
                    <button onClick={() => removeStep(i)} style={{ color: 'var(--neo-text-muted)' }}><X size={12} /></button>
                  </div>
                ))}
                {scriptSteps.length === 0 && (
                  <p className="text-xs py-2" style={{ color: 'var(--neo-text-muted)' }}>No steps yet. Add a tool call below.</p>
                )}
              </div>
              <div className="flex flex-col gap-2">
                <SelectInput value={newStepTool} onChange={setNewStepTool} options={toolOptions} placeholder="Select tool..." />
                <TextArea value={newStepParams} onChange={setNewStepParams} placeholder='{"query": "..."}' rows={2} mono />
                <GhostButton onClick={addStep}><Plus size={12} /> Add Step</GhostButton>
              </div>
              <div className="mt-4 flex gap-2">
                <PrimaryButton onClick={runScript} disabled={simRunning} color="#10b981">
                  {simRunning ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />} Run Simulation
                </PrimaryButton>
                {simRunning && <PrimaryButton onClick={stopSim} color="#ef4444"><Square size={14} /> Stop</PrimaryButton>}
              </div>
            </SectionCard>
          )}

          {simMode === 'llm' && (
            <SectionCard className="p-4 flex-1">
              <h3 className="text-xs font-semibold uppercase tracking-wide mb-3" style={{ color: 'var(--neo-text-muted)' }}>
                LLM Task
              </h3>
              <div className="flex flex-col gap-2">
                <TextArea value={llmTask} onChange={setLlmTask} placeholder="What should this agent do?" rows={4} />
                <div className="flex gap-2">
                  <TextInput value={llmModel} onChange={setLlmModel} placeholder="LLM model (optional)" className="flex-1" />
                  <TextInput type="number" value={maxSteps} onChange={(v) => setMaxSteps(Number(v) || 10)} placeholder="Max steps" style={{ width: 90 }} />
                </div>
              </div>
              <div className="mt-4 flex gap-2">
                <PrimaryButton onClick={runLLM} disabled={simRunning} color="#10b981">
                  {simRunning ? <Loader2 size={14} className="animate-spin" /> : <Cpu size={14} />} Run Agent
                </PrimaryButton>
                {simRunning && <PrimaryButton onClick={stopSim} color="#ef4444"><Square size={14} /> Stop</PrimaryButton>}
              </div>
            </SectionCard>
          )}

          {simMode === 'plan' && (
            <SectionCard className="p-4 flex-1">
              <h3 className="text-xs font-semibold uppercase tracking-wide mb-2" style={{ color: 'var(--neo-text-muted)' }}>
                Planner — Describe Agents & Goal
              </h3>
              <p className="text-xs mb-3" style={{ color: 'var(--neo-text-dim)' }}>
                Describe what you want done and which agents should do it. The LLM will figure out the agents, tasks, and execution order from your prompt.
              </p>
              <div className="flex flex-col gap-2">
                <TextArea
                  value={planGoal}
                  onChange={setPlanGoal}
                  placeholder="e.g. I need a researcher agent to search the graph for architecture docs, and a reviewer agent to check for conflicts and inconsistencies. The researcher goes first, then the reviewer validates the findings."
                  rows={3}
                />
                <div className="flex gap-2">
                  <TextInput value={llmModel} onChange={setLlmModel} placeholder="LLM model (optional)" className="flex-1" />
                  <TextInput type="number" value={maxSteps} onChange={(v) => setMaxSteps(Number(v) || 8)} placeholder="Max tasks" style={{ width: 90 }} />
                </div>
                <label className="flex items-center gap-2 text-xs cursor-pointer" style={{ color: 'var(--neo-text-muted)' }}>
                  <input type="checkbox" checked={planAutoExec} onChange={(e) => setPlanAutoExec(e.target.checked)} />
                  Auto-execute after planning
                </label>
              </div>

              {/* Show generated plan with agents */}
              {planTasks && (
                <div className="mt-3 space-y-2">
                  {/* Agent badges */}
                  {planTasks.agents?.length > 0 && (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-xs font-semibold" style={{ color: 'var(--neo-text-muted)' }}>Agents:</span>
                      {planTasks.agents.map((a, i) => (
                        <span key={i} className="px-2 py-0.5 rounded-full text-[10px] font-medium"
                          style={{ background: `${NODE_COLORS[i % NODE_COLORS.length]}15`, color: NODE_COLORS[i % NODE_COLORS.length] }}>
                          {a.name} — {a.role}
                        </span>
                      ))}
                    </div>
                  )}
                  <span className="text-xs font-semibold" style={{ color: 'var(--neo-text-muted)' }}>
                    Plan ({(planTasks.tasks || []).length} tasks):
                  </span>
                  {(planTasks.tasks || []).map((t, i) => {
                    const agentIdx = (planTasks.agents || []).findIndex(a => a.name === t.agent);
                    const agentColor = agentIdx >= 0 ? NODE_COLORS[agentIdx % NODE_COLORS.length] : 'var(--neo-text-dim)';
                    return (
                      <div key={i} className="flex items-start gap-2 px-3 py-2 rounded-lg text-xs"
                        style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', borderLeft: `3px solid ${agentColor}` }}>
                        <span className="font-bold flex-shrink-0" style={{ color: '#06b6d4', minWidth: 20 }}>{t.step}.</span>
                        <div className="flex-1 min-w-0">
                          <div className="font-medium" style={{ color: 'var(--neo-text)' }}>{t.description}</div>
                          <div className="flex flex-wrap items-center gap-2 mt-0.5">
                            {t.agent && (
                              <span className="px-1.5 py-0.5 rounded" style={{ background: `${agentColor}15`, color: agentColor, fontSize: 10 }}>
                                {t.agent}
                              </span>
                            )}
                            <span className="px-1.5 py-0.5 rounded" style={{ background: 'rgba(59,130,246,0.1)', color: '#3b82f6', fontSize: 10 }}>
                              {t.tool}
                            </span>
                            {(t.depends_on || []).length > 0 && (
                              <span className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
                                after step {t.depends_on.join(', ')}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              <div className="mt-4 flex gap-2">
                <PrimaryButton onClick={runPlan} disabled={simRunning} color="#06b6d4">
                  {simRunning ? <Loader2 size={14} className="animate-spin" /> : <Layers size={14} />}
                  {planAutoExec ? 'Plan & Execute' : 'Generate Plan'}
                </PrimaryButton>
                {simRunning && <PrimaryButton onClick={stopSim} color="#ef4444"><Square size={14} /> Stop</PrimaryButton>}
              </div>
            </SectionCard>
          )}
        </div>

        {/* Right: Activity log */}
        <SectionCard className="flex-1 flex flex-col overflow-hidden">
          <div
            className="flex items-center justify-between px-4 py-2"
            style={{ borderBottom: '1px solid var(--neo-border)' }}
          >
            <span className="text-xs font-semibold" style={{ color: 'var(--neo-text-muted)' }}>
              Activity Log
            </span>
            {activityLog.length > 0 && (
              <button
                onClick={() => setActivityLog([])}
                className="text-xs hover:opacity-80"
                style={{ color: 'var(--neo-text-muted)' }}
              >
                Clear
              </button>
            )}
          </div>
          <div
            ref={logRef}
            className="flex-1 overflow-y-auto p-3"
            style={{ background: 'var(--neo-bg)', maxHeight: 520 }}
          >
            {activityLog.length === 0 ? (
              <div className="flex items-center justify-center h-full">
                <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                  Run a simulation to see activity here.
                </p>
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                {activityLog.map((entry, i) => (
                  <div key={i} className="text-xs" style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                    <div className="flex items-start gap-2">
                      <span className="flex-shrink-0" style={{ color: 'var(--neo-text-muted)', minWidth: 60 }}>
                        {new Date(entry.ts).toLocaleTimeString()}
                      </span>
                      {entry.step != null && (
                        <Badge color="var(--neo-blue)">#{entry.step}</Badge>
                      )}
                      {entry.tool && (
                        <Badge color="#3b82f6">{entry.tool}</Badge>
                      )}
                      {entry.type === 'reasoning' && (
                        <Badge color="#8b5cf6">thinking</Badge>
                      )}
                      {entry.type === 'answer' && (
                        <Badge color="#f59e0b">answer</Badge>
                      )}
                    </div>
                    <pre
                      className="mt-1 ml-16 whitespace-pre-wrap break-words"
                      style={{ color: logColors[entry.type] || 'var(--neo-text)', lineHeight: 1.5 }}
                    >
                      {entry.msg}
                    </pre>
                  </div>
                ))}
              </div>
            )}
          </div>
        </SectionCard>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab: Experiment Builder (multi-agent simulation)
// ---------------------------------------------------------------------------

function ReplayView({ runId, expId }) {
  const [replayData, setReplayData] = React.useState(null);
  const [loading, setLoading] = React.useState(false);

  React.useEffect(() => {
    if (!runId || !expId) return;
    setLoading(true);
    api.get(`/dashboard/experiments/${expId}/runs/${runId}/replay`)
      .then(r => setReplayData(r.data))
      .catch(() => setReplayData(null))
      .finally(() => setLoading(false));
  }, [runId, expId]);

  if (loading) return <div className="p-4 text-xs text-center" style={{ color: 'var(--neo-text-muted)' }}>Loading replay...</div>;
  if (!replayData?.steps?.length) return <div className="p-4 text-xs text-center" style={{ color: 'var(--neo-text-muted)' }}>No replay data. Run an experiment first.</div>;

  return (
    <div className="px-3 py-2 space-y-1">
      <div className="text-[10px] mb-2" style={{ color: 'var(--neo-text-dim)' }}>
        {replayData.total_steps} steps ({replayData.write_steps} writes with checkpoints)
      </div>
      {replayData.steps.map((step, idx) => (
        <div key={idx} className="flex items-start gap-2 py-1.5 px-2 rounded"
          style={{
            background: step.is_write ? 'rgba(34,197,94,0.05)' : 'var(--neo-bg)',
            border: `1px solid ${step.is_write ? 'rgba(34,197,94,0.2)' : 'var(--neo-border)'}`,
          }}>
          <div className="flex-shrink-0 w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold"
            style={{
              background: step.is_write ? 'rgba(34,197,94,0.15)' : 'var(--neo-surface)',
              color: step.is_write ? '#22c55e' : 'var(--neo-text-muted)',
            }}>
            {step.step}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] font-medium" style={{ color: 'var(--neo-text)' }}>{step.agent_name}</span>
              <span className="text-[10px] font-mono px-1 rounded" style={{ background: 'var(--neo-surface)', color: step.is_write ? '#22c55e' : 'var(--neo-blue)' }}>
                {step.action || 'done'}
              </span>
              {step.checkpoint_id && (
                <span className="text-[9px] px-1 rounded" style={{ background: 'rgba(168,85,247,0.1)', color: '#a855f7' }}>
                  checkpoint
                </span>
              )}
            </div>
            <p className="text-[10px] mt-0.5 line-clamp-1" style={{ color: 'var(--neo-text-muted)' }}>{step.reasoning}</p>
            {step.diff && step.diff.nodes_added?.length > 0 && (
              <div className="mt-1 text-[9px]" style={{ color: '#22c55e' }}>
                +{step.diff.nodes_added.length} nodes: {step.diff.nodes_added.map(n => `[${n.label}] ${n.name}`).join(', ')}
              </div>
            )}
            {step.result && (
              <p className="text-[9px] mt-0.5 line-clamp-1 font-mono" style={{ color: 'var(--neo-text-dim)' }}>{step.result}</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------

function ExperimentTab({ graphs }) {
  const navigate = useNavigate();
  const [experiments, setExperiments] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState(null);
  const [pastRuns, setPastRuns] = useState([]);
  const [bootstrapAgent, setBootstrapAgent] = useState(null);
  const [bootstrapCode, setBootstrapCode] = useState(null);
  const [showTheater, setShowTheater] = useState(false);
  const [theaterElapsed, setTheaterElapsed] = useState(0);
  const theaterTimer = useRef(null);
  const [experimentMode, setExperimentMode] = useState('sandbox'); // 'sandbox' | 'live'
  const [theaterView, setTheaterView] = useState('timeline'); // 'timeline' | 'log'
  const [runInputs, setRunInputs] = useState(''); // JSON string of runtime inputs
  const [runGoal, setRunGoal] = useState(''); // runtime goal override
  const [showGoalOverride, setShowGoalOverride] = useState(false);
  const [llmModel, setLlmModel] = useState(''); // LLM model for experiment runs
  const [activeRunId, setActiveRunId] = useState(null); // for polling async runs
  const pollRef = useRef(null);
  const [liveScore, setLiveScore] = useState(null); // {partial_score, scores, scored_agents, total_agents}
  const [liveEvents, setLiveEvents] = useState([]); // SSE events accumulated during run
  const [useSSE, setUseSSE] = useState(true); // whether to use SSE (falls back to polling on failure)
  const [editingGoal, setEditingGoal] = useState(false);
  const [editGoalValue, setEditGoalValue] = useState('');

  // New experiment form
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [newExecMode, setNewExecMode] = useState('parallel');
  const [newSessionId, setNewSessionId] = useState('');
  const [newNameError, setNewNameError] = useState('');
  const [newGoalError, setNewGoalError] = useState('');
  const [newSessionError, setNewSessionError] = useState('');
  const [sessions, setSessions] = useState([]);
  const [contexts, setContexts] = useState([]);

  // Add agent form
  const [showAddAgent, setShowAddAgent] = useState(false);
  const [allAgents, setAllAgents] = useState([]);
  const [selectedAgentId, setSelectedAgentId] = useState('');
  const [agentName, setAgentName] = useState('');
  const [agentRole, setAgentRole] = useState('researcher');
  const [agentFramework, setAgentFramework] = useState('');
  const [agentPrompt, setAgentPrompt] = useState('');
  const [agentDeps, setAgentDeps] = useState('');
  const [agentApiKey, setAgentApiKey] = useState('');
  const [agentLlm, setAgentLlm] = useState('');
  const [registerNew, setRegisterNew] = useState(false);

  // Graph preview key — increments to force DashboardGraphExplorer refresh
  const [graphRefreshKey, setGraphRefreshKey] = useState(0);

  // ── SSE Real-Time Connection ──────────────────────────────────────
  const handleSSEEvent = useCallback((event) => {
    const { type, data } = event;
    setLiveEvents(prev => [...prev, { type, data, ts: Date.now() }]);

    switch (type) {
      case 'score_update':
        setLiveScore(data);
        break;
      case 'agent_done':
        // Refresh run result to get full agent data
        if (selected?.id && activeRunId) {
          api.get(`/dashboard/experiments/${selected.id}/runs/${activeRunId}`)
            .then(res => {
              const d = res.data;
              const liveAgents = [...(d.agents || [])];
              if (d._live_agent) liveAgents.push(d._live_agent);
              setRunResult(prev => ({ ...prev, ...d, agents: liveAgents }));
            })
            .catch(() => {});
        }
        break;
      case 'run_complete':
      case 'complete':
        // Final fetch to get complete result with scores
        if (selected?.id && activeRunId) {
          api.get(`/dashboard/experiments/${selected.id}/runs/${activeRunId}`)
            .then(res => {
              setRunResult(res.data);
              setRunning(false);
              setActiveRunId(null);
              setTheaterView('summary');
              setGraphRefreshKey(k => k + 1);
              if (theaterTimer.current) clearInterval(theaterTimer.current);
              if (res.data.status === 'completed') toast.success(`Experiment completed in ${res.data.total_duration_ms}ms`);
              else toast.error(`Experiment ${res.data.status}`);
            })
            .catch(() => { setRunning(false); });
        }
        break;
      default:
        break;
    }
  }, [selected?.id, activeRunId]);

  const handleSSEComplete = useCallback(() => {
    // SSE stream ended — final state already handled in event handler
  }, []);

  const handleSSEError = useCallback(() => {
    // SSE failed — fall back to polling
    // Polling will start on next render when useSSE becomes false
    setUseSSE(false);
  }, []);

  useExperimentSSE({
    expId: selected?.id,
    runId: activeRunId,
    enabled: running && useSSE && !!activeRunId,
    onEvent: handleSSEEvent,
    onComplete: handleSSEComplete,
    onError: handleSSEError,
  });

  const fetchExperiments = useCallback(async () => {
    try {
      const res = await api.get('/dashboard/experiments');
      setExperiments(res.data.experiments || []);
    } catch { /* silent */ }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchExperiments();
    api.get('/dashboard/sessions').then(r => setSessions(r.data.sessions || r.data || [])).catch(() => {});
    api.get('/dashboard/agents').then(r => setAllAgents(r.data.agents || [])).catch(() => {});
    api.get('/dashboard/contexts').then(r => setContexts(r.data.contexts || [])).catch(() => {});
  }, [fetchExperiments]);

  // Sync execution mode from selected experiment
  useEffect(() => {
    if (selected?.execution_mode) {
      setNewExecMode(selected.execution_mode);
    } else {
      setNewExecMode('parallel');
    }
  }, [selected?.id]);

  // Fetch past runs when experiment is selected
  useEffect(() => {
    if (selected?.id) {
      api.get(`/dashboard/experiments/${selected.id}/runs`)
        .then(r => setPastRuns(r.data.runs || []))
        .catch(() => setPastRuns([]));
    } else {
      setPastRuns([]);
    }
  }, [selected?.id, runResult]);

  // Resolve the graph namespace from the experiment's session
  const resolveGraphName = useCallback((exp) => {
    if (!exp?.session_id) return '';
    const sess = sessions.find(s => (s.session_id || s.id) === exp.session_id);
    return sess?.graph_namespace || sess?.graph || '';
  }, [sessions]);

  const experimentGraphName = resolveGraphName(selected);

  const saveGoal = async () => {
    if (!selected) return;
    try {
      const res = await api.put(`/dashboard/experiments/${selected.id}`, { goal: editGoalValue });
      setSelected(res.data);
      setExperiments(prev => prev.map(e => e.id === res.data.id ? res.data : e));
      setEditingGoal(false);
      toast.success('Goal updated');
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to update goal'); }
  };

  const createExperiment = async () => {
    let hasError = false;
    if (!newName.trim()) { setNewNameError('Name is required'); hasError = true; } else { setNewNameError(''); }
    if (!newDesc.trim()) { setNewGoalError('Goal is required'); hasError = true; } else { setNewGoalError(''); }
    if (!newSessionId) { setNewSessionError('A Context Runtime is required'); hasError = true; } else { setNewSessionError(''); }
    if (hasError) return;
    try {
      const res = await api.post('/dashboard/experiments', {
        name: newName, goal: newDesc,
        session_id: newSessionId, execution_mode: newExecMode,
      });
      setExperiments(prev => [res.data, ...prev]);
      setSelected(res.data);
      setShowCreate(false);
      setNewName(''); setNewDesc(''); setNewSessionId('');
      setNewNameError(''); setNewGoalError(''); setNewSessionError('');
      toast.success('Experiment created');
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
  };

  const updateExperimentSession = async (sessionId) => {
    if (!selected) return;
    try {
      const res = await api.put(`/dashboard/experiments/${selected.id}`, { session_id: sessionId });
      setSelected(res.data);
      setExperiments(prev => prev.map(e => e.id === res.data.id ? res.data : e));
      setGraphRefreshKey(k => k + 1);
      toast.success(sessionId ? 'Runtime context attached' : 'Runtime context removed');
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to update'); }
  };

  const updateExecMode = async (mode) => {
    if (!selected) return;
    setNewExecMode(mode);
    try {
      const res = await api.put(`/dashboard/experiments/${selected.id}`, { execution_mode: mode });
      setSelected(res.data);
      setExperiments(prev => prev.map(e => e.id === res.data.id ? res.data : e));
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to update mode'); }
  };

  const [addingAgent, setAddingAgent] = useState(false);
  const addAgent = async () => {
    if (!selected || addingAgent) return;
    if (!selectedAgentId && !agentName.trim()) { toast.error('Select an agent or enter a name'); return; }
    setAddingAgent(true);
    try {
      const payload = {
        name: agentName, role: agentRole, framework: agentFramework,
        prompt: agentPrompt, llm_model: agentLlm || undefined,
        depends_on: agentDeps ? agentDeps.split(',').map(s => s.trim()) : [],
      };
      if (selectedAgentId) {
        payload.agent_id = selectedAgentId;
        if (agentApiKey) payload.api_key = agentApiKey;
      }
      const res = await api.post(`/dashboard/experiments/${selected.id}/agents`, payload);
      const exp = await api.get(`/dashboard/experiments/${selected.id}`);
      setSelected(exp.data);
      setShowAddAgent(false);
      setSelectedAgentId(''); setAgentName(''); setAgentPrompt(''); setAgentDeps('');
      setAgentApiKey(''); setAgentLlm(''); setRegisterNew(false);
      toast.success(`Agent "${res.data.name}" added`);
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
    finally { setAddingAgent(false); }
  };

  const removeAgent = async (agentId) => {
    if (!selected) return;
    try {
      await api.delete(`/dashboard/experiments/${selected.id}/agents/${encodeURIComponent(agentId)}`);
      const exp = await api.get(`/dashboard/experiments/${selected.id}`);
      setSelected(exp.data);
      toast.success('Agent removed');
    } catch (err) {
      const detail = err?.response?.data?.detail || 'Failed to remove agent';
      toast.error(detail);
    }
  };

  // Poll for async run completion (fallback when SSE unavailable)
  const startPolling = useCallback((expId, runId) => {
    if (pollRef.current) clearInterval(pollRef.current);

    const doPoll = async () => {
      try {
        const res = await api.get(`/dashboard/experiments/${expId}/runs/${runId}`);
        const data = res.data;
        if (data.status && data.status !== 'running') {
          // Run finished
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          setRunResult(data);
          setRunning(false);
          setActiveRunId(null);
          setTheaterView('summary');
          setGraphRefreshKey(k => k + 1);
          if (theaterTimer.current) clearInterval(theaterTimer.current);
          if (data.status === 'completed') toast.success(`Experiment completed in ${data.total_duration_ms}ms`);
          else if (data.status === 'aborted') toast.error('Experiment was stopped');
          else toast.error(`Experiment ${data.status}`);
        } else if (data.agents) {
          // Still running — update live progress (show agents as they complete)
          // Merge _live_agent (current running agent) into agents list for live step rendering
          const liveAgents = [...(data.agents || [])];
          if (data._live_agent) {
            liveAgents.push(data._live_agent);
          }
          setRunResult(prev => ({ ...prev, ...data, agents: liveAgents }));
        }
      } catch {
        // Polling error — keep trying
      }
    };

    // First poll after 500ms
    setTimeout(doPoll, 500);
    // Then every 1s for live step updates
    pollRef.current = setInterval(doPoll, 1000);
  }, []);

  // Start polling as fallback when SSE is disabled during a running experiment
  useEffect(() => {
    if (!useSSE && running && activeRunId && selected?.id) {
      startPolling(selected.id, activeRunId);
    }
  }, [useSSE, running, activeRunId, selected?.id, startPolling]);

  const runExperiment = async () => {
    if (!selected) return;
    if (!selected.session_id) {
      toast.error('No Context Runtime attached.');
      return;
    }

    // Parse inputs
    let parsedInputs = {};
    if (runInputs.trim()) {
      try { parsedInputs = JSON.parse(runInputs); }
      catch { toast.error('Invalid JSON in inputs'); return; }
    }

    // Auto-discard previous sandbox if exists
    if (runResult?.mode === 'sandbox' && runResult?.run_id && !runResult?.promoted && !runResult?.discarded) {
      try {
        await api.post(`/dashboard/experiments/${selected.id}/runs/${runResult.run_id}/discard`);
      } catch { /* silent cleanup */ }
    }

    setShowTheater(true);
    setRunning(true);
    setRunResult(null);
    setLiveScore(null);
    setLiveEvents([]);
    setUseSSE(true); // reset SSE preference for each new run
    setTheaterElapsed(0);
    const startTime = Date.now();
    theaterTimer.current = setInterval(() => {
      setTheaterElapsed(Math.round((Date.now() - startTime) / 1000));
    }, 1000);

    try {
      const payload = {
        mode: experimentMode,
        async_mode: true,
        inputs: parsedInputs,
      };
      if (runGoal.trim()) payload.goal = runGoal;
      if (llmModel.trim()) payload.llm_model = llmModel;

      const res = await api.post(`/dashboard/experiments/${selected.id}/run`, payload);

      if (res.data.async && res.data.run_id) {
        // Async mode — set initial run data + connect SSE (or fall back to polling)
        setRunResult(res.data); // has agents=[], status="running" — theater renders immediately
        setActiveRunId(res.data.run_id);
        // Always start polling as baseline — SSE enhances it with live events
        // Polling detects completion reliably; SSE adds real-time score/findings
        startPolling(selected.id, res.data.run_id);
      } else {
        // Sync mode — result returned directly
        setRunResult(res.data);
        setRunning(false);
        setTheaterView('summary');
        setGraphRefreshKey(k => k + 1);
        if (theaterTimer.current) clearInterval(theaterTimer.current);
        toast.success(`Experiment completed in ${res.data.total_duration_ms}ms`);
      }
    } catch (e) {
      const status = e.response?.status || 0;
      const detail = e.response?.data?.detail || e.message || 'Run failed';
      const fullError = status ? `[${status}] ${detail}` : detail;
      toast.error(fullError);
      setRunResult({ status: 'failed', error: fullError, error_detail: JSON.stringify(e.response?.data || {}, null, 2), agents: [] });
      setRunning(false);
      if (theaterTimer.current) clearInterval(theaterTimer.current);
    }
  };

  const stopExperiment = async () => {
    if (!selected || !activeRunId) return;
    try {
      await api.post(`/dashboard/experiments/${selected.id}/runs/${activeRunId}/stop`);
      toast.success('Stop signal sent — agents will finish current step');
    } catch { toast.error('Failed to stop'); }
  };

  const pauseExperiment = async () => {
    if (!selected || !activeRunId) return;
    try {
      await api.post(`/dashboard/experiments/${selected.id}/runs/${activeRunId}/pause`);
      toast.success('Paused — agents will wait between steps');
    } catch { toast.error('Failed to pause'); }
  };

  const resumeExperiment = async () => {
    if (!selected || !activeRunId) return;
    try {
      await api.post(`/dashboard/experiments/${selected.id}/runs/${activeRunId}/resume`);
      toast.success('Resumed');
    } catch { toast.error('Failed to resume'); }
  };

  const showBootstrap = async (agent) => {
    if (!selected) return;
    try {
      const res = await api.get(`/dashboard/experiments/${selected.id}/bootstrap/${agent.agent_id}`);
      setBootstrapAgent(agent);
      setBootstrapCode(res.data);
    } catch (e) { toast.error('Failed to load bootstrap code'); }
  };

  const copyCode = (text) => {
    navigator.clipboard.writeText(text);
    toast.success('Copied to clipboard');
  };

  const deleteExperiment = async (id) => {
    if (!window.confirm('Delete this experiment?')) return;
    try {
      await api.delete(`/dashboard/experiments/${id}`);
      setExperiments(prev => prev.filter(e => e.id !== id));
      if (selected?.id === id) { setSelected(null); setRunResult(null); setShowTheater(false); }
      toast.success('Deleted');
    } catch { toast.error('Failed'); }
  };

  // Readiness checks — agents are auto-pulled from session if none configured
  const hasAgents = (selected?.agents || []).length > 0;
  const hasSession = !!selected?.session_id;
  const canRun = hasSession && !running;
  const selectedSession = sessions.find(s => (s.session_id || s.id) === selected?.session_id);

  return (
    <div className="flex gap-4" style={{ minHeight: 550 }}>
      {/* Left: Experiment list */}
      <div className="flex-shrink-0" style={{ width: 260 }}>
        <SectionCard className="h-full flex flex-col">
          <div className="flex items-center justify-between p-3" style={{ borderBottom: '1px solid var(--neo-border)' }}>
            <span className="text-xs font-semibold" style={{ color: 'var(--neo-text-muted)' }}>Experiments</span>
            <button onClick={() => setShowCreate(true)}
              className="p-1 rounded" style={{ color: 'var(--neo-blue)' }}>
              <Plus size={14} />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto">
            {experiments.map(exp => (
              <button key={exp.id}
                onClick={() => { setSelected(exp); setRunResult(null); setShowTheater(false); }}
                className="w-full text-left px-3 py-2.5 text-xs transition"
                style={{
                  borderBottom: '1px solid var(--neo-border)',
                  background: selected?.id === exp.id ? 'rgba(59, 130, 246, 0.03)' : 'transparent',
                  borderLeft: selected?.id === exp.id ? '2px solid var(--neo-blue)' : '2px solid transparent',
                }}>
                <div className="font-medium" style={{ color: 'var(--neo-text)' }}>{exp.name}</div>
                <div className="flex items-center gap-2 mt-0.5" style={{ color: 'var(--neo-text-dim)' }}>
                  <span>{(exp.agents || []).length} agents</span>
                  <span>·</span>
                  <span>{exp.run_count || 0} runs</span>
                </div>
                {exp.session_id && (
                  <div className="flex items-center gap-1.5 mt-0.5">
                    <span className="px-1 py-0.5 rounded text-[9px]"
                      style={{ background: 'rgba(16,185,129,0.1)', color: '#10b981' }}>
                      {(() => { const s = sessions.find(ss => (ss.session_id || ss.id) === exp.session_id); return s?.name || exp.session_id.slice(0, 12); })()}
                    </span>
                  </div>
                )}
              </button>
            ))}
            {!loading && experiments.length === 0 && (
              <div className="p-4 text-center">
                <p className="text-xs" style={{ color: 'var(--neo-text-dim)' }}>No experiments yet</p>
                <button onClick={() => setShowCreate(true)}
                  className="mt-2 text-xs px-3 py-1.5 rounded-lg"
                  style={{ background: 'var(--neo-blue)', color: '#fff' }}>
                  Create First Experiment
                </button>
              </div>
            )}
          </div>
        </SectionCard>
      </div>

      {/* Center: Experiment detail */}
      <div className="flex-1 flex flex-col gap-4 min-w-0">
        {/* Create modal */}
        {showCreate && (
          <SectionCard className="p-4">
            <h3 className="text-sm font-semibold mb-3" style={{ color: 'var(--neo-text)' }}>New Experiment</h3>
            <div className="flex flex-col gap-3">

              {/* Name */}
              <div>
                <label className="text-xs font-medium mb-1 block" style={{ color: 'var(--neo-text-muted)' }}>Name</label>
                <TextInput
                  value={newName}
                  onChange={(v) => { setNewName(v); if (v.trim()) setNewNameError(''); }}
                  placeholder="e.g. Daily Research Agent"
                />
                {newNameError && <p className="text-[10px] mt-1" style={{ color: '#ef4444' }}>{newNameError}</p>}
              </div>

              {/* Goal */}
              <div>
                <label className="text-xs font-medium mb-1 block" style={{ color: 'var(--neo-text-muted)' }}>
                  What should the agents accomplish?
                </label>
                <TextArea
                  value={newDesc}
                  onChange={(v) => { setNewDesc(v); if (v.trim()) setNewGoalError(''); }}
                  placeholder="e.g. Search the knowledge graph for recent technology trends and summarize key findings."
                  rows={3}
                />
                {newGoalError && <p className="text-[10px] mt-1" style={{ color: '#ef4444' }}>{newGoalError}</p>}
              </div>

              {/* Context Runtime */}
              <div>
                <label className="text-xs font-medium mb-1 block" style={{ color: 'var(--neo-text-muted)' }}>
                  Context Runtime <span style={{ color: '#ef4444' }}>*</span>
                </label>
                <SelectInput
                  value={newSessionId}
                  onChange={(v) => { setNewSessionId(v); if (v) setNewSessionError(''); }}
                  options={sessions.map(s => ({
                    value: s.session_id || s.id || s.name,
                    label: `${s.name || s.session_id || s.id}${s.graph_namespace ? ` → ${s.graph_namespace}` : ''}`,
                  }))}
                  placeholder="Select a session..."
                  className="w-full"
                />
                {newSessionError && <p className="text-[10px] mt-1" style={{ color: '#ef4444' }}>{newSessionError}</p>}
                {!newSessionId && !newSessionError && (
                  <p className="text-[10px] mt-1" style={{ color: 'var(--neo-text-dim)' }}>
                    Sessions provide the graph and agent scope. Create one on the Sessions page first.
                  </p>
                )}
              </div>

              <div className="flex gap-2 mt-1">
                <PrimaryButton
                  onClick={createExperiment}
                  disabled={!newName.trim() || !newDesc.trim() || !newSessionId}
                >
                  Create
                </PrimaryButton>
                <GhostButton onClick={() => {
                  setShowCreate(false);
                  setNewNameError(''); setNewGoalError(''); setNewSessionError('');
                }}>
                  Cancel
                </GhostButton>
              </div>
            </div>
          </SectionCard>
        )}

        {/* ===== EXECUTION THEATER ===== */}
        {showTheater && selected ? (
          <SectionCard className="overflow-hidden">
            <div className="px-4 py-3 flex items-center justify-between"
              style={{ borderBottom: '1px solid var(--neo-border)', background: running ? 'rgba(139,92,246,0.05)' : 'transparent' }}>
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-2">
                  {running ? (
                    <span className="relative flex h-3 w-3">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style={{ background: '#8b5cf6' }} />
                      <span className="relative inline-flex rounded-full h-3 w-3" style={{ background: '#8b5cf6' }} />
                    </span>
                  ) : (
                    <CheckCircle size={14} style={{ color: runResult?.status === 'completed' ? '#22c55e' : '#ef4444' }} />
                  )}
                  <span className="text-sm font-semibold" style={{ color: 'var(--neo-text)' }}>
                    {running ? 'Executing Experiment...' : runResult?.status === 'completed' ? 'Experiment Complete' : 'Experiment Failed'}
                  </span>
                </div>
                <span className="text-xs font-mono px-2 py-0.5 rounded"
                  style={{ background: 'rgba(139,92,246,0.1)', color: '#8b5cf6' }}>
                  {selected.name}
                </span>
                <span className="text-[10px] font-bold px-1.5 py-0.5 rounded uppercase"
                  style={{
                    background: (runResult?.mode || experimentMode) === 'sandbox' ? 'rgba(245,158,11,0.15)' : 'rgba(239,68,68,0.15)',
                    color: (runResult?.mode || experimentMode) === 'sandbox' ? '#f59e0b' : '#ef4444',
                  }}>
                  {(runResult?.mode || experimentMode) === 'sandbox' ? 'SANDBOX' : 'LIVE'}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs font-mono" style={{ color: 'var(--neo-text-dim)' }}>
                  {running ? `${theaterElapsed}s elapsed` : runResult?.total_duration_ms ? `${runResult.total_duration_ms}ms` : ''}
                </span>
                {/* Stop / Pause / Resume controls */}
                {running && activeRunId && (
                  <>
                    <button onClick={pauseExperiment}
                      className="px-2 py-1 rounded text-[10px] font-medium"
                      style={{ background: 'rgba(245,158,11,0.15)', color: '#f59e0b' }}>
                      Pause
                    </button>
                    <button onClick={resumeExperiment}
                      className="px-2 py-1 rounded text-[10px] font-medium"
                      style={{ background: 'rgba(34,197,94,0.15)', color: '#22c55e' }}>
                      Resume
                    </button>
                    <button onClick={stopExperiment}
                      className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium"
                      style={{ background: 'rgba(239,68,68,0.15)', color: '#ef4444' }}>
                      <Square size={8} /> Stop
                    </button>
                  </>
                )}
                {/* Promote / Discard for sandbox runs */}
                {!running && runResult?.mode === 'sandbox' && runResult?.status === 'completed' && !runResult?.promoted && !runResult?.discarded && (
                  <>
                    <button
                      onClick={async () => {
                        try {
                          const res = await api.post(`/dashboard/experiments/${selected.id}/runs/${runResult.run_id}/promote`);
                          setRunResult(prev => ({ ...prev, promoted: true, promoted_nodes: res.data.promoted_nodes, promoted_edges: res.data.promoted_edges }));
                          setGraphRefreshKey(k => k + 1);
                          toast.success(`Promoted ${res.data.promoted_nodes} nodes, ${res.data.promoted_edges} edges to production graph`);
                        } catch (e) { toast.error(e.response?.data?.detail || 'Promote failed'); }
                      }}
                      className="flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium"
                      style={{ background: '#22c55e', color: '#fff' }}>
                      <CheckCircle size={11} /> Promote to Live
                    </button>
                    <button
                      onClick={async () => {
                        try {
                          await api.post(`/dashboard/experiments/${selected.id}/runs/${runResult.run_id}/discard`);
                          setRunResult(prev => ({ ...prev, discarded: true }));
                          toast.success('Sandbox discarded');
                        } catch (e) { toast.error(e.response?.data?.detail || 'Discard failed'); }
                      }}
                      className="flex items-center gap-1 px-2.5 py-1 rounded text-xs"
                      style={{ color: 'var(--neo-text-muted)', border: '1px solid var(--neo-border)' }}>
                      <Trash2 size={11} /> Discard
                    </button>
                  </>
                )}
                {runResult?.promoted && (
                  <span className="text-[10px] font-bold px-1.5 py-0.5 rounded" style={{ background: 'rgba(34,197,94,0.15)', color: '#22c55e' }}>
                    PROMOTED ({runResult.promoted_nodes}n, {runResult.promoted_edges}e)
                  </span>
                )}
                {runResult?.discarded && (
                  <span className="text-[10px] font-bold px-1.5 py-0.5 rounded" style={{ background: 'rgba(239,68,68,0.1)', color: '#ef4444' }}>
                    DISCARDED
                  </span>
                )}
                <button onClick={() => {
                    setShowTheater(false);
                    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
                  }}
                  className="p-1 rounded" style={{ color: 'var(--neo-text-dim)' }}>
                  <X size={14} />
                </button>
              </div>
            </div>

            {/* Theater view toggle + graph link */}
            <div className="px-4 py-1.5 flex items-center justify-between" style={{ borderBottom: '1px solid var(--neo-border)', background: 'var(--neo-surface)' }}>
              <div className="flex items-center gap-1">
                {['summary', 'timeline', 'replay', 'log'].map(v => (
                  <button key={v} onClick={() => setTheaterView(v)}
                    className="px-2.5 py-1 rounded text-[10px] font-medium transition"
                    style={{
                      background: theaterView === v ? 'rgba(59, 130, 246, 0.08)' : 'transparent',
                      color: theaterView === v ? 'var(--neo-blue)' : 'var(--neo-text-muted)',
                    }}>
                    {v === 'summary' ? 'Summary' : v === 'timeline' ? 'Timeline' : v === 'replay' ? 'Replay' : 'Log'}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-3 text-[10px]">
                {runResult && (
                  <span style={{ color: 'var(--neo-text-dim)' }}>
                    {(runResult.agents || []).length} agents · {runResult.total_duration_ms}ms
                    {runResult.track_id && <span className="font-mono ml-1" style={{ color: '#8b5cf6' }}>{runResult.track_id}</span>}
                  </span>
                )}
                {experimentGraphName && (
                  <button onClick={() => navigate(`/dashboard/graphs?name=${experimentGraphName}`)}
                    className="flex items-center gap-1 px-2 py-0.5 rounded"
                    style={{ color: 'var(--neo-blue)', border: '1px solid var(--neo-border)' }}>
                    <Database size={9} /> Open Graph
                  </button>
                )}
              </div>
            </div>

            {/* Scorecard — shows final scores or live partial scores */}
            {(runResult?.scores || (running && liveScore?.scores)) && (
              <div className="px-4 py-2 flex items-center gap-3 flex-wrap" style={{ borderBottom: '1px solid var(--neo-border)', background: running ? 'rgba(139,92,246,0.03)' : 'var(--neo-bg)' }}>
                {(() => {
                  const s = (running && liveScore?.scores) ? liveScore.scores : runResult.scores;
                  const overall = s.overall || 0;
                  const color = overall >= 70 ? '#22c55e' : overall >= 40 ? '#f59e0b' : '#ef4444';
                  const metrics = [
                    { key: 'coverage', label: 'Coverage', icon: Search },
                    { key: 'discovery', label: 'Discovery', icon: Search },
                    { key: 'enrichment', label: 'Enrichment', icon: Database },
                    { key: 'collaboration', label: 'Collab', icon: MessageSquare },
                    { key: 'efficiency', label: 'Efficiency', icon: Zap },
                    { key: 'completion', label: 'Completion', icon: CheckCircle },
                    { key: 'insight_quality', label: 'Insight', icon: Cpu },
                  ];
                  return (
                    <>
                      <div className="flex items-center gap-2 pr-3 mr-1" style={{ borderRight: '1px solid var(--neo-border)' }}>
                        <span className="text-lg font-bold" style={{ color }}>{overall}</span>
                        <span className="text-[10px] font-medium" style={{ color: 'var(--neo-text-muted)' }}>/100</span>
                      </div>
                      {metrics.map(m => {
                        const val = s[m.key] || 0;
                        const mc = val >= 70 ? '#22c55e' : val >= 40 ? '#f59e0b' : '#ef4444';
                        const Icon = m.icon;
                        return (
                          <div key={m.key} className="flex items-center gap-1" title={`${m.label}: ${val}/100`}>
                            <Icon size={9} style={{ color: mc }} />
                            <span className="text-[9px]" style={{ color: 'var(--neo-text-dim)' }}>{m.label}</span>
                            <span className="text-[10px] font-bold" style={{ color: mc }}>{val}</span>
                          </div>
                        );
                      })}
                    </>
                  );
                })()}
              </div>
            )}

            {/* Theater content */}
            <div className="overflow-y-auto" style={{ maxHeight: 550 }}>

              {/* ===== SUMMARY VIEW ===== */}
              {theaterView === 'summary' && !running && runResult && (
                <div className="p-4 space-y-4">

                  {/* Header row: status badge + duration + mode + timestamp */}
                  <div className="flex items-center gap-3 flex-wrap">
                    <span
                      className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-bold"
                      style={{
                        background: runResult.status === 'completed' ? 'rgba(34,197,94,0.12)' : runResult.status === 'aborted' ? 'rgba(245,158,11,0.12)' : 'rgba(239,68,68,0.12)',
                        color: runResult.status === 'completed' ? '#22c55e' : runResult.status === 'aborted' ? '#f59e0b' : '#ef4444',
                      }}
                    >
                      {runResult.status === 'completed' ? <CheckCircle size={11} /> : <XCircle size={11} />}
                      {runResult.status}
                    </span>
                    {runResult.total_duration_ms && (
                      <span className="text-xs font-mono" style={{ color: 'var(--neo-text-muted)' }}>
                        {runResult.total_duration_ms.toLocaleString()}ms
                      </span>
                    )}
                    <span
                      className="text-[10px] font-bold px-1.5 py-0.5 rounded uppercase"
                      style={{
                        background: runResult.mode === 'sandbox' ? 'rgba(245,158,11,0.12)' : 'rgba(239,68,68,0.12)',
                        color: runResult.mode === 'sandbox' ? '#f59e0b' : '#ef4444',
                      }}
                    >
                      {runResult.mode || 'sandbox'}
                    </span>
                    {runResult.started_at && (
                      <span className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
                        {new Date(runResult.started_at).toLocaleTimeString()}
                      </span>
                    )}
                  </div>

                  {/* Error */}
                  {runResult.error && (
                    <div className="p-3 rounded-lg text-xs" style={{ background: 'rgba(239,68,68,0.05)', border: '1px solid rgba(239,68,68,0.15)', color: '#ef4444' }}>
                      <strong>Error:</strong> {runResult.error}
                      {runResult.error_detail && (
                        <pre className="mt-1 text-[9px] opacity-70 whitespace-pre-wrap">{runResult.error_detail}</pre>
                      )}
                    </div>
                  )}

                  {/* Agent cards */}
                  {(runResult.agents || []).length > 0 && (
                    <div className="space-y-2">
                      <div className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: 'var(--neo-text-dim)' }}>
                        Agents ({(runResult.agents || []).length})
                      </div>
                      {(runResult.agents || []).map((agent, i) => (
                        <details key={agent.name || agent.agent_id || i} className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--neo-border)' }}>
                          <summary
                            className="flex items-center gap-2 px-3 py-2 cursor-pointer text-xs"
                            style={{ background: 'var(--neo-surface)', listStyle: 'none' }}
                          >
                            <span className="w-2 h-2 rounded-full flex-shrink-0"
                              style={{ background: NODE_COLORS[i % NODE_COLORS.length] }} />
                            <span className="font-medium" style={{ color: NODE_COLORS[i % NODE_COLORS.length] }}>
                              {agent.agent_name || agent.name}
                            </span>
                            {agent.role && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: `${NODE_COLORS[i % NODE_COLORS.length]}15`, color: NODE_COLORS[i % NODE_COLORS.length] }}>
                                {agent.role}
                              </span>
                            )}
                            <span className="ml-auto text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
                              {(agent.steps || []).length} steps
                            </span>
                            <span
                              className="text-[9px] font-bold px-1.5 py-0.5 rounded ml-1"
                              style={{
                                background: agent.status === 'completed' ? 'rgba(34,197,94,0.12)' : agent.status === 'failed' ? 'rgba(239,68,68,0.12)' : 'rgba(245,158,11,0.12)',
                                color: agent.status === 'completed' ? '#22c55e' : agent.status === 'failed' ? '#ef4444' : '#f59e0b',
                              }}
                            >
                              {agent.status}
                            </span>
                            {agent.duration_ms && (
                              <span className="text-[9px] font-mono ml-1" style={{ color: 'var(--neo-text-dim)' }}>{agent.duration_ms}ms</span>
                            )}
                          </summary>
                          <div className="px-3 py-2 space-y-1.5" style={{ background: 'var(--neo-bg)' }}>
                            {(agent.steps || []).map((step, si) => (
                              <div key={si} className="flex items-start gap-2 text-[10px]">
                                <span className="font-mono text-[9px] flex-shrink-0 mt-0.5" style={{ color: 'var(--neo-text-dim)', minWidth: 20 }}>{si + 1}.</span>
                                <div className="flex-1 min-w-0">
                                  <span className="font-medium" style={{ color: 'var(--neo-text-muted)' }}>{step.tool || step.action || step.type}</span>
                                  {step.summary && (
                                    <p className="mt-0.5" style={{ color: 'var(--neo-text-dim)' }}>{step.summary}</p>
                                  )}
                                </div>
                                <span
                                  className="flex-shrink-0 text-[9px] font-bold"
                                  style={{ color: step.status === 'success' ? '#22c55e' : step.status === 'error' ? '#ef4444' : 'var(--neo-text-dim)' }}
                                >
                                  {step.status}
                                </span>
                              </div>
                            ))}
                            {agent.score != null && (
                              <div className="pt-1.5 flex items-center gap-2 text-[10px]" style={{ borderTop: '1px solid var(--neo-border)' }}>
                                <span style={{ color: 'var(--neo-text-dim)' }}>Score:</span>
                                <span className="font-bold" style={{ color: agent.score >= 70 ? '#22c55e' : agent.score >= 40 ? '#f59e0b' : '#ef4444' }}>
                                  {agent.score}
                                </span>
                              </div>
                            )}
                            {agent.error && (
                              <p className="text-[10px] mt-1" style={{ color: '#ef4444' }}>Error: {agent.error}</p>
                            )}
                          </div>
                        </details>
                      ))}
                    </div>
                  )}

                  {/* Graph diff summary */}
                  {(runResult.nodes_written > 0 || runResult.edges_written > 0) && (
                    <div className="flex items-center gap-3 p-3 rounded-lg text-xs" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
                      <Database size={11} style={{ color: 'var(--neo-blue)', flexShrink: 0 }} />
                      <span style={{ color: 'var(--neo-text-muted)' }}>
                        <strong style={{ color: 'var(--neo-text)' }}>+{runResult.nodes_written || 0} nodes</strong>
                        {', '}
                        <strong style={{ color: 'var(--neo-text)' }}>+{runResult.edges_written || 0} edges</strong>
                        {' written this run'}
                      </span>
                      {experimentGraphName && (
                        <button
                          onClick={() => { setGraphRefreshKey(k => k + 1); navigate(`/dashboard/graphs?name=${experimentGraphName}`); }}
                          className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] ml-auto"
                          style={{ color: 'var(--neo-blue)', border: '1px solid var(--neo-border)' }}
                        >
                          <Eye size={9} /> View in graph →
                        </button>
                      )}
                    </div>
                  )}

                  {/* Run history list */}
                  {pastRuns.length > 1 && (
                    <div>
                      <div className="text-[10px] font-semibold uppercase tracking-wide mb-2" style={{ color: 'var(--neo-text-dim)' }}>
                        Run History ({pastRuns.length})
                      </div>
                      <div className="space-y-1">
                        {pastRuns.slice(0, 8).map((run, ri) => {
                          const sc = run.scores?.overall || 0;
                          const scoreColor = sc >= 70 ? '#22c55e' : sc >= 40 ? '#f59e0b' : sc > 0 ? '#ef4444' : 'var(--neo-text-dim)';
                          const isActive = run.run_id === runResult?.run_id;
                          return (
                            <button
                              key={run.run_id || ri}
                              onClick={() => { setRunResult(run); setTheaterView('summary'); }}
                              className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-[10px] text-left transition"
                              style={{
                                background: isActive ? 'rgba(59, 130, 246, 0.03)' : 'var(--neo-surface)',
                                border: `1px solid ${isActive ? 'rgba(59, 130, 246, 0.19)' : 'var(--neo-border)'}`,
                              }}
                            >
                              <span className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                                style={{ background: run.status === 'completed' ? '#22c55e' : run.status === 'aborted' ? '#f59e0b' : '#ef4444' }} />
                              <span style={{ color: 'var(--neo-text-muted)' }}>{run.status}</span>
                              <span className="text-[9px] px-1 py-0.5 rounded font-bold"
                                style={{
                                  background: run.mode === 'sandbox' ? 'rgba(245,158,11,0.1)' : 'rgba(239,68,68,0.1)',
                                  color: run.mode === 'sandbox' ? '#f59e0b' : '#ef4444',
                                }}>
                                {run.mode || 'sandbox'}
                              </span>
                              {run.total_duration_ms && (
                                <span className="font-mono" style={{ color: 'var(--neo-text-dim)' }}>{run.total_duration_ms}ms</span>
                              )}
                              {sc > 0 && (
                                <span className="font-bold ml-auto" style={{ color: scoreColor }}>{sc}</span>
                              )}
                              {run.started_at && (
                                <span className="ml-auto" style={{ color: 'var(--neo-text-dim)' }}>{new Date(run.started_at).toLocaleDateString()}</span>
                              )}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}

                </div>
              )}

              {/* Live progress bar when running */}
              {running && (
                <div className="px-4 py-2 flex items-center gap-3" style={{ borderBottom: '1px solid var(--neo-border)', background: 'rgba(139,92,246,0.03)' }}>
                  <Loader2 size={14} className="animate-spin" style={{ color: '#8b5cf6' }} />
                  <span className="text-xs" style={{ color: '#8b5cf6' }}>
                    {runResult?.agents?.length
                      ? `${runResult.agents.filter(a => a.status !== 'running').length}/${(selected.agents || []).length} agents complete`
                      : 'Starting experiment...'}
                  </span>
                  <span className="text-[10px] font-mono" style={{ color: 'var(--neo-text-dim)' }}>{theaterElapsed}s</span>
                  {/* Live score gauge */}
                  {liveScore && (
                    <div className="flex items-center gap-1.5 px-2 py-0.5 rounded" style={{ background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.2)' }}>
                      <span className="text-[10px] font-bold" style={{ color: liveScore.partial_score >= 70 ? '#22c55e' : liveScore.partial_score >= 40 ? '#f59e0b' : '#ef4444' }}>
                        {liveScore.partial_score}
                      </span>
                      <span className="text-[9px]" style={{ color: 'var(--neo-text-dim)' }}>/100</span>
                      <span className="text-[9px]" style={{ color: 'var(--neo-text-dim)' }}>
                        ({liveScore.scored_agents}/{liveScore.total_agents})
                      </span>
                    </div>
                  )}
                  <div className="flex-1" />
                  <div className="flex gap-1">
                    {(selected.agents || []).map((a, i) => {
                      const done = runResult?.agents?.find(ra => ra.name === a.name && ra.status !== 'running');
                      const isCurrent = !done && runResult?.agents?.length === i;
                      return (
                        <div key={i} className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px]"
                          style={{
                            background: done ? `${NODE_COLORS[i % NODE_COLORS.length]}15` : isCurrent ? `${NODE_COLORS[i % NODE_COLORS.length]}25` : 'var(--neo-surface)',
                            color: done ? NODE_COLORS[i % NODE_COLORS.length] : isCurrent ? NODE_COLORS[i % NODE_COLORS.length] : 'var(--neo-text-dim)',
                            border: isCurrent ? `1px solid ${NODE_COLORS[i % NODE_COLORS.length]}40` : '1px solid transparent',
                          }}>
                          {done ? <CheckCircle size={8} /> : isCurrent ? <Loader2 size={8} className="animate-spin" /> : <Clock size={8} />}
                          {a.name}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Live status — shows while running (with or without agent data) */}
              {running && (
                <div className="px-4 py-4">
                  <div className="flex items-center gap-3 mb-2">
                    <Loader2 size={18} className="animate-spin" style={{ color: '#8b5cf6' }} />
                    <span className="text-sm font-medium" style={{ color: 'var(--neo-text)' }}>
                      {runResult?._progress || 'Experiment is running...'}
                    </span>
                    <span className="text-[10px] font-mono" style={{ color: 'var(--neo-text-dim)' }}>{theaterElapsed}s</span>
                  </div>
                  {/* Progress log — shows all setup steps */}
                  {runResult?._progress_log && runResult._progress_log.length > 0 && (
                    <div className="mb-3 pl-2" style={{ borderLeft: '2px solid rgba(139,92,246,0.15)' }}>
                      {runResult._progress_log.map((p, i) => (
                        <div key={i} className="flex items-center gap-2 text-[10px] py-0.5" style={{ color: 'var(--neo-text-muted)' }}>
                          <span className="font-mono" style={{ color: 'var(--neo-text-dim)', minWidth: 40 }}>{(p.elapsed_ms / 1000).toFixed(1)}s</span>
                          <span>{p.msg}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="space-y-1 pl-2 mb-3" style={{ borderLeft: '2px solid rgba(139,92,246,0.2)' }}>
                    <div className="flex items-center gap-2 text-xs" style={{ color: '#22c55e' }}>
                      <CheckCircle size={10} /> Graph: {runResult?.source_graph || experimentGraphName || '...'}
                    </div>
                    <div className="flex items-center gap-2 text-xs" style={{ color: runResult?.mode === 'sandbox' ? '#f59e0b' : '#22c55e' }}>
                      <CheckCircle size={10} /> Mode: {runResult?.mode || 'sandbox'}
                      {runResult?.sandbox_namespace && <span className="font-mono text-[9px]">({runResult.sandbox_namespace})</span>}
                    </div>
                    {runResult?.llm_provider && (
                      <div className="flex items-center gap-2 text-xs" style={{ color: '#22c55e' }}>
                        <CheckCircle size={10} /> LLM: {runResult.llm_provider}
                      </div>
                    )}
                    {runResult?.agents?.length > 0 && (
                      <div className="flex items-center gap-2 text-xs" style={{ color: '#3b82f6' }}>
                        <CheckCircle size={10} /> {runResult.agents.length} agent(s) have run
                      </div>
                    )}
                  </div>
                  {(!runResult?.agents || runResult.agents.length === 0) && (
                    <div className="flex flex-wrap gap-2">
                      {(selected.agents || []).map((a, i) => (
                        <span key={i} className="px-2.5 py-1 rounded-full text-[10px] flex items-center gap-1.5"
                          style={{ background: `${NODE_COLORS[i % NODE_COLORS.length]}10`, color: NODE_COLORS[i % NODE_COLORS.length],
                            border: `1px solid ${NODE_COLORS[i % NODE_COLORS.length]}20` }}>
                          <Clock size={8} /> {a.name}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Live findings feed — shows findings as they arrive via SSE */}
              {running && liveEvents.filter(e => e.type === 'finding').length > 0 && (
                <div className="px-4 py-2" style={{ borderBottom: '1px solid var(--neo-border)', background: 'rgba(245,158,11,0.03)' }}>
                  <div className="text-[10px] font-semibold mb-1.5" style={{ color: '#f59e0b' }}>
                    Live Findings ({liveEvents.filter(e => e.type === 'finding').length})
                  </div>
                  <div className="space-y-1.5">
                    {liveEvents.filter(e => e.type === 'finding').slice(-5).map((ev, i) => (
                      <div key={i} className="flex items-start gap-2 px-2 py-1.5 rounded" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>
                        <span className="text-[9px] px-1.5 py-0.5 rounded font-bold shrink-0"
                          style={{ background: ev.data.node_type === 'Insight' ? 'rgba(139,92,246,0.15)' : 'rgba(59,130,246,0.15)',
                            color: ev.data.node_type === 'Insight' ? '#8b5cf6' : '#3b82f6' }}>
                          {ev.data.node_type || 'Finding'}
                        </span>
                        <div className="flex-1 min-w-0">
                          <span className="text-[10px] font-semibold" style={{ color: 'var(--neo-text)' }}>{ev.data.agent_name}</span>
                          <p className="text-[10px] mt-0.5 leading-relaxed" style={{ color: 'var(--neo-text-muted)' }}>
                            {(ev.data.content_preview || '').slice(0, 200)}
                          </p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Setup log — persists after completion */}
              {!running && runResult?._progress_log && runResult._progress_log.length > 0 && (
                <details className="px-4 py-2" style={{ borderBottom: '1px solid var(--neo-border)' }}>
                  <summary className="text-[10px] cursor-pointer" style={{ color: 'var(--neo-text-dim)' }}>
                    Setup log ({runResult._progress_log.length} steps, {runResult._progress_log[runResult._progress_log.length - 1]?.elapsed_ms}ms)
                  </summary>
                  <div className="mt-1 pl-2" style={{ borderLeft: '2px solid rgba(139,92,246,0.1)' }}>
                    {runResult._progress_log.map((p, i) => (
                      <div key={i} className="flex items-center gap-2 text-[9px] py-0.5" style={{ color: 'var(--neo-text-muted)' }}>
                        <span className="font-mono" style={{ color: 'var(--neo-text-dim)', minWidth: 40 }}>{(p.elapsed_ms / 1000).toFixed(1)}s</span>
                        <span>{p.msg}</span>
                      </div>
                    ))}
                  </div>
                </details>
              )}

              {/* Error */}
              {runResult?.error && (
                <div className="px-4 py-3 text-xs" style={{ background: 'rgba(239,68,68,0.05)', color: '#ef4444', borderBottom: '1px solid rgba(239,68,68,0.15)' }}>
                  <strong>Error:</strong> {runResult.error}
                  {runResult.error_detail && (
                    <pre className="mt-1 text-[9px] opacity-70 whitespace-pre-wrap">{runResult.error_detail}</pre>
                  )}
                </div>
              )}

              {/* ===== TIMELINE VIEW — shows live as agents complete ===== */}
              {runResult?.agents?.length > 0 && theaterView === 'timeline' && (() => {
                // Flatten all steps into a single timeline with agent context
                const timeline = [];
                const agentMap = {};
                (runResult.agents || []).forEach((agent, ai) => {
                  agentMap[agent.name] = { color: NODE_COLORS[ai % NODE_COLORS.length], role: agent.role, index: ai };
                  // Agent start
                  timeline.push({ type: 'agent_start', agent: agent.name, role: agent.role, prompt: agent.prompt, phase: agent.phase, context: agent.context, ai, ts: ai * 1000 });
                  (agent.steps || []).forEach((step, si) => {
                    timeline.push({ ...step, type: 'step', agent: agent.name, ai, ts: ai * 1000 + (si + 1) });
                  });
                  // Agent end
                  timeline.push({
                    type: 'agent_end', agent: agent.name, status: agent.status, ai,
                    duration: agent.duration_ms, error: agent.error, error_detail: agent.error_detail,
                    ts: ai * 1000 + (agent.steps || []).length + 1,
                  });
                });
                timeline.sort((a, b) => a.ts - b.ts);

                return (
                  <div className="px-2 py-2">
                    {timeline.map((ev, idx) => {
                      const info = agentMap[ev.agent] || { color: '#888', role: '', index: 0 };
                      const agentColor = info.color;

                      if (ev.type === 'agent_start') {
                        return (
                          <div key={idx} className="flex items-start gap-2 py-2 px-2">
                            <div className="flex flex-col items-center flex-shrink-0" style={{ width: 20 }}>
                              <div className="w-3 h-3 rounded-full" style={{ background: agentColor, boxShadow: `0 0 6px ${agentColor}40` }} />
                              <div className="w-px flex-1" style={{ background: agentColor + '30', minHeight: 8 }} />
                            </div>
                            <div className="flex-1">
                              <div className="flex items-center gap-2">
                                <span className="text-xs font-bold" style={{ color: agentColor }}>{ev.agent}</span>
                                <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: agentColor + '15', color: agentColor }}>{ev.role}</span>
                                <span className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>started</span>
                                {ev.phase && (
                                  <span className="text-[9px] px-1.5 py-0.5 rounded font-bold uppercase"
                                    style={{ background: ev.phase === 'plan' ? 'rgba(245,158,11,0.15)' : ev.phase === 'synthesize' ? 'rgba(139,92,246,0.15)' : 'rgba(59,130,246,0.15)',
                                      color: ev.phase === 'plan' ? '#f59e0b' : ev.phase === 'synthesize' ? '#8b5cf6' : '#3b82f6' }}>
                                    {ev.phase}
                                  </span>
                                )}
                              </div>
                              {ev.prompt && (
                                <div className="mt-1 px-2 py-1.5 rounded text-[10px] leading-relaxed" style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)' }}>
                                  <span className="font-bold" style={{ color: agentColor }}>Task: </span>
                                  <span style={{ color: 'var(--neo-text-muted)' }}>{ev.prompt.length > 300 ? ev.prompt.slice(0, 300) + '...' : ev.prompt}</span>
                                </div>
                              )}
                              {ev.context && (
                                <details className="mt-1">
                                  <summary className="text-[9px] cursor-pointer px-2 py-1" style={{ color: 'var(--neo-text-dim)' }}>
                                    Show agent context (what the agent sees)
                                  </summary>
                                  <pre className="mt-0.5 px-2 py-1.5 rounded text-[9px] whitespace-pre-wrap overflow-auto"
                                    style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)', maxHeight: 200 }}>
                                    {ev.context}
                                  </pre>
                                </details>
                              )}
                            </div>
                          </div>
                        );
                      }

                      if (ev.type === 'agent_end') {
                        return (
                          <div key={idx} className="flex items-start gap-2 py-2 px-2">
                            <div className="flex flex-col items-center flex-shrink-0" style={{ width: 20 }}>
                              <div className="w-3 h-3 rounded-full flex items-center justify-center"
                                style={{ background: ev.status === 'completed' ? '#22c55e20' : '#ef444420',
                                  border: `2px solid ${ev.status === 'completed' ? '#22c55e' : '#ef4444'}` }}>
                              </div>
                            </div>
                            <div className="flex-1">
                              <div className="flex items-center gap-2">
                                <span className="text-xs font-bold" style={{ color: agentColor }}>{ev.agent}</span>
                                <span className="text-[10px] px-1.5 py-0.5 rounded font-semibold"
                                  style={{ background: ev.status === 'completed' ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)',
                                    color: ev.status === 'completed' ? '#22c55e' : '#ef4444' }}>
                                  {ev.status}
                                </span>
                                <span className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>{ev.duration}ms</span>
                              </div>
                              {ev.error && (
                                <div className="mt-1 px-2 py-1 rounded text-[10px]" style={{ background: 'rgba(239,68,68,0.05)', color: '#ef4444' }}>
                                  {ev.error}
                                  {ev.error_detail && <pre className="mt-0.5 text-[9px] opacity-70 whitespace-pre-wrap">{ev.error_detail}</pre>}
                                </div>
                              )}
                            </div>
                          </div>
                        );
                      }

                      // Step
                      const isInteraction = ['add_task', 'add_knowledge', 'add_decision', 'complete_task', 'handoff_task', 'log_action'].includes(ev.tool);
                      return (
                        <div key={idx} className="flex items-start gap-2 py-1 px-2">
                          <div className="flex flex-col items-center flex-shrink-0" style={{ width: 20 }}>
                            <div className="w-1.5 h-1.5 rounded-full mt-1.5" style={{ background: ev.success === false ? '#ef4444' : isInteraction ? '#f59e0b' : agentColor + '80' }} />
                            <div className="w-px flex-1" style={{ background: agentColor + '15', minHeight: 4 }} />
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-1.5 flex-wrap">
                              <span className="text-[10px] font-semibold" style={{ color: agentColor }}>{ev.agent}</span>
                              {ev.reasoning && (
                                <span className="text-[10px]" style={{ color: '#8b5cf6' }}>{ev.reasoning.slice(0, 80)}{ev.reasoning.length > 80 ? '...' : ''}</span>
                              )}
                            </div>
                            {ev.tool && (
                              <div className="flex items-center gap-1 mt-0.5">
                                <span className="font-mono text-[10px] font-semibold" style={{ color: isInteraction ? '#f59e0b' : '#3b82f6' }}>
                                  {isInteraction ? '>' : ''}  {ev.tool}
                                </span>
                                <span className="font-mono text-[9px]" style={{ color: 'var(--neo-text-dim)' }}>
                                  ({JSON.stringify(ev.params || {}).slice(0, 80)})
                                </span>
                                {ev.success === true && <span className="text-[9px]" style={{ color: '#22c55e' }}>OK</span>}
                                {ev.success === false && <span className="text-[9px]" style={{ color: '#ef4444' }}>FAIL</span>}
                              </div>
                            )}
                            {ev.result != null && (
                              <details className="mt-0.5">
                                <summary className="text-[9px] cursor-pointer" style={{ color: 'var(--neo-text-dim)' }}>result</summary>
                                <pre className="text-[9px] mt-0.5 overflow-x-auto whitespace-pre-wrap max-h-20 overflow-y-auto rounded px-1.5 py-1"
                                  style={{ color: 'var(--neo-text-dim)', background: 'var(--neo-surface)', fontFamily: "'JetBrains Mono', monospace" }}>
                                  {typeof ev.result === 'string' ? ev.result.slice(0, 400) : JSON.stringify(ev.result, null, 1).slice(0, 400)}
                                </pre>
                              </details>
                            )}
                            {ev.summary && <p className="text-[10px] mt-0.5 font-medium" style={{ color: '#f59e0b' }}>{ev.summary}</p>}
                            {ev.error && <p className="text-[10px] mt-0.5" style={{ color: '#ef4444' }}>{ev.error}</p>}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                );
              })()}

              {/* ===== REPLAY VIEW — write steps with graph diffs ===== */}
              {runResult && theaterView === 'replay' && (
                <ReplayView runId={runResult.run_id || activeRunId} expId={selected?.id} />
              )}

              {/* ===== LOG VIEW — per-agent collapsible ===== */}
              {runResult?.agents?.length > 0 && theaterView === 'log' && (
                <div className="divide-y" style={{ borderColor: 'var(--neo-border)' }}>
                  {(runResult.agents || []).map((agentRun, ai) => (
                    <details key={ai} open>
                      <summary className="px-4 py-2.5 flex items-center justify-between cursor-pointer hover:bg-white/5"
                        style={{ borderLeft: `3px solid ${NODE_COLORS[ai % NODE_COLORS.length]}` }}>
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-semibold" style={{ color: 'var(--neo-text)' }}>{agentRun.name}</span>
                          <span className="text-[10px] px-1.5 py-0.5 rounded"
                            style={{ background: `${NODE_COLORS[ai % NODE_COLORS.length]}15`, color: NODE_COLORS[ai % NODE_COLORS.length] }}>
                            {agentRun.role}
                          </span>
                        </div>
                        <div className="flex items-center gap-2 text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
                          <span>{(agentRun.steps || []).length} steps</span>
                          <span>{agentRun.duration_ms}ms</span>
                          <span className="px-1.5 py-0.5 rounded font-semibold"
                            style={{ background: agentRun.status === 'completed' ? 'rgba(34,197,94,0.1)' : agentRun.status === 'skipped' ? 'rgba(245,158,11,0.1)' : 'rgba(239,68,68,0.1)',
                              color: agentRun.status === 'completed' ? '#22c55e' : agentRun.status === 'skipped' ? '#f59e0b' : '#ef4444' }}>
                            {agentRun.status}
                          </span>
                        </div>
                      </summary>
                      <div className="px-4 py-2 space-y-1.5" style={{ background: 'var(--neo-bg)', borderLeft: `3px solid ${NODE_COLORS[ai % NODE_COLORS.length]}20` }}>
                        {agentRun.prompt && (
                          <div className="text-[10px] px-2 py-1.5 rounded" style={{ background: 'var(--neo-surface)', color: 'var(--neo-text-muted)' }}>Task: {agentRun.prompt}</div>
                        )}
                        {(agentRun.steps || []).map((step, si) => (
                          <div key={si} className="flex items-start gap-2 text-xs pl-1">
                            <span className="font-bold flex-shrink-0 mt-0.5" style={{ color: NODE_COLORS[ai % NODE_COLORS.length], minWidth: 16 }}>{step.step}.</span>
                            <div className="flex-1 min-w-0 space-y-0.5">
                              {step.reasoning && <p style={{ color: '#8b5cf6', fontSize: 11 }}><span style={{ opacity: 0.6 }}>Think:</span> {step.reasoning}</p>}
                              {step.tool && (
                                <p className="font-mono" style={{ color: '#3b82f6', fontSize: 11 }}>
                                  {step.tool}({JSON.stringify(step.params || {}).slice(0, 120)})
                                  {step.success === true && <span style={{ color: '#22c55e' }}> OK</span>}
                                  {step.success === false && <span style={{ color: '#ef4444' }}> FAIL</span>}
                                </p>
                              )}
                              {step.result != null && (
                                <details><summary className="text-[9px] cursor-pointer" style={{ color: 'var(--neo-text-dim)' }}>result</summary>
                                  <pre className="text-[9px] overflow-x-auto whitespace-pre-wrap max-h-20 overflow-y-auto rounded px-1.5 py-1"
                                    style={{ color: 'var(--neo-text-dim)', background: 'var(--neo-surface)', fontFamily: "'JetBrains Mono', monospace" }}>
                                    {typeof step.result === 'string' ? step.result.slice(0, 500) : JSON.stringify(step.result, null, 1).slice(0, 500)}
                                  </pre>
                                </details>
                              )}
                              {step.summary && <p style={{ color: '#f59e0b', fontSize: 11 }}>Summary: {step.summary}</p>}
                              {step.error && <p style={{ color: '#ef4444', fontSize: 11 }}>Error: {step.error}</p>}
                            </div>
                          </div>
                        ))}
                        {agentRun.status === 'skipped' && <p className="text-xs px-2" style={{ color: '#f59e0b' }}>Skipped: {agentRun.reason}</p>}
                        {agentRun.status === 'failed' && agentRun.error && (
                          <div className="px-2 py-1.5 rounded text-xs" style={{ background: 'rgba(239,68,68,0.05)', border: '1px solid rgba(239,68,68,0.15)' }}>
                            <p style={{ color: '#ef4444', fontWeight: 600 }}>Failed: {agentRun.error}</p>
                            {agentRun.error_detail && <pre className="mt-1 text-[9px] opacity-70 whitespace-pre-wrap">{agentRun.error_detail}</pre>}
                          </div>
                        )}
                      </div>
                    </details>
                  ))}
                </div>
              )}

              {/* Experiment Output — final briefing/summary */}
              {runResult?.output && runResult?.status === 'completed' && (
                <div className="mt-3 p-3 rounded-lg" style={{ background: 'var(--neo-card-bg)', border: '1px solid var(--neo-cyan)', borderLeft: '4px solid var(--neo-cyan)' }}>
                  <h4 className="text-xs font-semibold mb-2" style={{ color: 'var(--neo-cyan)' }}>Experiment Output</h4>
                  <pre className="text-[11px] whitespace-pre-wrap leading-relaxed" style={{ color: 'var(--neo-text)', fontFamily: "'Inter', sans-serif" }}>
                    {runResult.output}
                  </pre>
                </div>
              )}
              {runResult?.summary && runResult?.summary?.length > 0 && runResult?.status === 'completed' && !runResult?.output && (
                <div className="mt-3 p-3 rounded-lg" style={{ background: 'var(--neo-card-bg)', border: '1px solid var(--neo-border)' }}>
                  <h4 className="text-xs font-semibold mb-2" style={{ color: 'var(--neo-text)' }}>Findings & Insights</h4>
                  {runResult.summary.map((s, i) => (
                    <div key={i} className="mb-2">
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] px-1.5 py-0.5 rounded font-medium"
                          style={{ background: s.type === 'Insight' ? 'rgba(99,102,241,0.1)' : 'rgba(34,197,94,0.1)',
                                   color: s.type === 'Insight' ? '#6366f1' : '#22c55e' }}>
                          {s.type}
                        </span>
                        <span className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>{s.agent} ({s.phase})</span>
                      </div>
                      <p className="text-[11px] mt-1 leading-relaxed" style={{ color: 'var(--neo-text)' }}>{s.content}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </SectionCard>
        ) : selected ? (
          <>
            {/* Experiment header */}
            <SectionCard className="p-4">
              <div className="flex items-center justify-between mb-2">
                <div className="flex-1 min-w-0 mr-4">
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-bold" style={{ color: 'var(--neo-text)' }}>{selected.name}</h2>
                    <span className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
                      {(selected.agents || []).length} agents · {pastRuns.length} runs
                    </span>
                  </div>
                  {!editingGoal ? (
                    <div className="mt-1.5 flex items-start gap-1.5">
                      <div className="flex-1">
                        <span className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: 'var(--neo-text-dim)' }}>Goal</span>
                        <p className="text-xs mt-0.5 leading-relaxed" style={{ color: 'var(--neo-text-muted)' }}>
                          {selected.goal || selected.description || <em style={{ opacity: 0.5 }}>No goal set — click to add</em>}
                        </p>
                      </div>
                      <button
                        onClick={() => { setEditingGoal(true); setEditGoalValue(selected.goal || selected.description || ''); }}
                        className="p-1 rounded flex-shrink-0"
                        style={{ color: 'var(--neo-text-dim)' }}
                        title="Edit goal"
                      >
                        <Pencil size={11} />
                      </button>
                    </div>
                  ) : (
                    <div className="mt-1.5 space-y-2">
                      <TextArea value={editGoalValue} onChange={setEditGoalValue} rows={2} />
                      <div className="flex gap-2">
                        <PrimaryButton onClick={saveGoal}>Save</PrimaryButton>
                        <GhostButton onClick={() => setEditingGoal(false)}>Cancel</GhostButton>
                      </div>
                    </div>
                  )}
                </div>
                <div className="flex flex-col gap-2 items-end">
                  {/* Execution mode selector */}
                  <div className="flex gap-1 w-full">
                    {[
                      { value: 'parallel', label: 'Parallel', example: '3 agents search simultaneously' },
                      { value: 'leader-follower', label: 'Leader \u2192 Workers', example: 'Planner assigns, workers execute' },
                      { value: 'sequential', label: 'Sequential', example: 'Agent 1 \u2192 Agent 2 \u2192 Agent 3' },
                    ].map(m => (
                      <button
                        key={m.value}
                        onClick={() => updateExecMode(m.value)}
                        className="flex-1 px-2 py-1.5 rounded-lg text-left text-xs transition"
                        style={{
                          background: newExecMode === m.value ? 'rgba(59, 130, 246, 0.12)' : 'var(--neo-bg)',
                          border: `1px solid ${newExecMode === m.value ? 'var(--neo-blue, #3b82f6)' : 'var(--neo-border)'}`,
                          color: newExecMode === m.value ? 'var(--neo-blue, #3b82f6)' : 'var(--neo-text-muted)',
                        }}
                      >
                        <div className="font-medium">{m.label}</div>
                        <div className="text-[9px] mt-0.5" style={{ opacity: 0.65 }}>{m.example}</div>
                      </button>
                    ))}
                  </div>
                  {/* Row 2: sandbox/live + run button */}
                  <div className="flex items-center gap-2">
                    {/* Mode toggle */}
                    <div className="flex rounded-lg overflow-hidden" style={{ border: '1px solid var(--neo-border)' }}>
                      {['sandbox', 'live'].map(m => (
                        <button key={m} onClick={() => setExperimentMode(m)}
                          className="px-3 py-1.5 text-xs font-medium transition"
                          style={{
                            background: experimentMode === m
                              ? (m === 'sandbox' ? 'rgba(245,158,11,0.15)' : 'rgba(239,68,68,0.15)')
                              : 'transparent',
                            color: experimentMode === m
                              ? (m === 'sandbox' ? '#f59e0b' : '#ef4444')
                              : 'var(--neo-text-muted)',
                          }}>
                          {m === 'sandbox' ? 'Sandbox' : 'Live'}
                        </button>
                      ))}
                    </div>
                    <select
                      value={llmModel}
                      onChange={(e) => setLlmModel(e.target.value)}
                      className="px-2 py-1 rounded-lg text-xs outline-none"
                      style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)', maxWidth: 180 }}
                    >
                      <option value="">LLM: Auto</option>
                      <option value="openai:gpt-4o">GPT-4o</option>
                      <option value="openai:gpt-4o-mini">GPT-4o Mini</option>
                      <option value="anthropic:claude-sonnet-4-20250514">Claude Sonnet</option>
                      <option value="groq:llama-3.3-70b-versatile">Groq Llama 70B</option>
                      <option value="groq:openai/gpt-oss-120b">Groq GPT-oss</option>
                      <option value="ollama:llama3.1">Ollama Local</option>
                    </select>
                    <div className="flex flex-col items-end gap-0.5">
                      <PrimaryButton onClick={runExperiment} disabled={!canRun}
                        color={experimentMode === 'live' ? '#ef4444' : '#10b981'}>
                        {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                        {experimentMode === 'live' ? 'Run Live' : 'Run Sandbox'}
                      </PrimaryButton>
                      {!hasSession && (
                        <span className="text-[9px]" style={{ color: '#ef4444' }}>Attach a Context Runtime to run</span>
                      )}
                    </div>
                    <GhostButton onClick={() => deleteExperiment(selected.id)}>
                      <Trash2 size={12} />
                    </GhostButton>
                  </div>
                </div>
              </div>

              {/* Readiness checklist */}
              <div className="flex items-center gap-4 text-xs mt-1">
                <span className="flex items-center gap-1" style={{ color: hasSession ? '#22c55e' : '#ef4444' }}>
                  {hasSession ? <CheckCircle size={11} /> : <XCircle size={11} />}
                  Context Runtime
                </span>
                <span className="flex items-center gap-1" style={{ color: hasAgents ? '#22c55e' : '#ef4444' }}>
                  {hasAgents ? <CheckCircle size={11} /> : <XCircle size={11} />}
                  Agents ({(selected.agents || []).length})
                </span>
                <span className="flex items-center gap-1" style={{ color: 'var(--neo-text-dim)' }}>
                  <Clock size={11} />
                  {selected.run_count || 0} run(s)
                </span>
                <span className="px-1.5 py-0.5 rounded text-[10px] font-medium"
                  style={{ background: selected.execution_mode === 'leader-follower' ? 'rgba(139,92,246,0.1)' : 'rgba(59,130,246,0.1)',
                    color: selected.execution_mode === 'leader-follower' ? '#8b5cf6' : '#3b82f6' }}>
                  {selected.execution_mode === 'leader-follower' ? 'Leader → Workers' : selected.execution_mode === 'parallel' ? 'Parallel' : selected.execution_mode === 'queue' ? 'Parallel' : 'Sequential'}
                </span>
              </div>
            </SectionCard>

            {/* Context Runtime */}
            <SectionCard className="p-4">
              <h3 className="text-xs font-semibold uppercase tracking-wide mb-3" style={{ color: 'var(--neo-text-muted)' }}>
                Context Runtime <span style={{ color: '#ef4444' }}>*</span>
              </h3>
              <div>
                <label className="text-xs font-medium mb-1 block" style={{ color: 'var(--neo-text-muted)' }}>
                  Session — provides the graph, agent scope, and runtime boundary
                </label>
                <SelectInput
                  value={selected.session_id || ''}
                  onChange={(val) => updateExperimentSession(val)}
                  options={[
                    { value: '', label: '-- Select a session --' },
                    ...sessions.map(s => ({
                      value: s.session_id || s.id || s.name,
                      label: `${s.name || s.session_id || s.id || ''}${s.graph_namespace ? ` → ${s.graph_namespace}` : ''}`,
                    })),
                  ]}
                  className="w-full"
                />
              </div>
              {/* Session info */}
              {selectedSession && (
                <div className="mt-2 flex items-center gap-3 text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
                  <span>Graph: <strong style={{ color: 'var(--neo-text)' }}>{selectedSession.graph_namespace || 'none'}</strong></span>
                  {selectedSession.member_count > 0 && <span>{selectedSession.member_count} members</span>}
                  <button onClick={() => navigate(`/dashboard/sessions?highlight=${selected.session_id}`)}
                    className="flex items-center gap-1" style={{ color: 'var(--neo-blue)' }}>
                    <Eye size={9} /> View Session
                  </button>
                </div>
              )}
              {!hasSession && (
                <p className="text-[10px] mt-2" style={{ color: '#ef4444' }}>
                  A Context Runtime is required. Create one in the Sessions page, then attach it here.
                </p>
              )}
              {/* Graph link (no embedded explorer) */}
              {experimentGraphName && (
                <button onClick={() => navigate(`/dashboard/graphs?name=${experimentGraphName}`)}
                  className="mt-2 flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg transition hover:opacity-80"
                  style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-blue)' }}>
                  <Database size={11} /> Open Graph: {experimentGraphName}
                </button>
              )}
            </SectionCard>

            {/* Agents — pulled from runtime context */}
            <SectionCard className="p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-xs font-semibold uppercase tracking-wide" style={{ color: 'var(--neo-text-muted)' }}>
                  Agents ({(selected.agents || []).length})
                </h3>
                <button onClick={() => setShowAddAgent(!showAddAgent)}
                  className="flex items-center gap-1 text-xs px-2 py-1 rounded"
                  style={{ color: 'var(--neo-text-dim)', border: '1px solid var(--neo-border)' }}>
                  <Settings size={11} /> {showAddAgent ? 'Done' : 'Manual'}
                </button>
              </div>

              {/* Agent chips — read-only view */}
              <div className="flex flex-wrap gap-1.5 mb-2">
                {(selected.agents || []).map((agent, i) => (
                  <div key={agent.agent_id || i}
                    className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs"
                    style={{ background: `${NODE_COLORS[i % NODE_COLORS.length]}10`,
                      border: `1px solid ${NODE_COLORS[i % NODE_COLORS.length]}25`,
                      color: NODE_COLORS[i % NODE_COLORS.length] }}>
                    <span className="font-medium">{agent.name}</span>
                    <span className="text-[10px] opacity-70">{agent.role}</span>
                    {agent.llm_model && (
                      <span className="text-[9px] px-1 py-0.5 rounded" style={{ background: 'rgba(168,85,247,0.1)', color: '#a855f7' }}>
                        {agent.llm_model.split(':').pop()}
                      </span>
                    )}
                    {showAddAgent && (
                      <button onClick={() => removeAgent(agent.agent_id)}
                        className="ml-1 opacity-50 hover:opacity-100" style={{ color: '#ef4444' }}>
                        <X size={10} />
                      </button>
                    )}
                  </div>
                ))}
              </div>

              {!hasAgents && (
                <div className="text-center py-4 px-3 rounded-lg" style={{ background: 'var(--neo-bg)', border: '1px dashed var(--neo-border)' }}>
                  <p className="text-xs mb-1" style={{ color: 'var(--neo-text-muted)' }}>
                    Agents auto-pulled from your runtime context when you run.
                  </p>
                  <p className="text-[10px]" style={{ color: 'var(--neo-text-dim)' }}>
                    Assign agents to the session in the Contexts page, or add manually below.
                  </p>
                </div>
              )}

              {/* Manual add — collapsed by default */}
              {showAddAgent && (
                <div className="mt-2 p-3 rounded-lg space-y-2" style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)' }}>
                  <div className="flex gap-2">
                    <SelectInput
                      value={selectedAgentId}
                      onChange={(val) => {
                        setSelectedAgentId(val);
                        const agent = allAgents.find(a => a.agent_id === val);
                        if (agent) { setAgentName(agent.name || ''); setAgentRole(agent.role || 'agent'); }
                      }}
                      options={allAgents.map(a => ({ value: a.agent_id, label: `${a.name} (${a.role || 'agent'})` }))}
                      placeholder="Select registered agent..."
                      className="flex-1"
                    />
                    <PrimaryButton onClick={addAgent} disabled={addingAgent} style={{ whiteSpace: 'nowrap' }}>
                      {addingAgent ? '...' : 'Add'}
                    </PrimaryButton>
                  </div>
                  <div className="flex gap-2">
                    <TextInput value={agentName} onChange={setAgentName} placeholder="Or new agent name" className="flex-1" />
                    <SelectInput value={agentRole} onChange={setAgentRole}
                      options={['researcher', 'analyst', 'reviewer', 'developer', 'writer', 'planner']} />
                    <select value={agentLlm} onChange={(e) => setAgentLlm(e.target.value)}
                      className="px-2 py-1 rounded text-[10px] outline-none"
                      style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}>
                      <option value="">Default LLM</option>
                      <option value="openai:gpt-4o">GPT-4o</option>
                      <option value="openai:gpt-4o-mini">GPT-4o Mini</option>
                      <option value="groq:llama-3.3-70b-versatile">Groq Llama 70B</option>
                    </select>
                  </div>
                </div>
              )}
            </SectionCard>

            {/* Trigger Inputs */}
            <SectionCard className="p-4">
              <h3 className="text-xs font-semibold uppercase tracking-wide mb-3" style={{ color: 'var(--neo-text-muted)' }}>
                Trigger Inputs <span className="normal-case font-normal" style={{ color: 'var(--neo-text-dim)' }}>(optional — injected into agent prompts at runtime)</span>
              </h3>
              <div className="space-y-2">
                <div>
                  <button
                    onClick={() => setShowGoalOverride(v => !v)}
                    className="text-xs font-medium text-left w-full"
                    style={{ color: 'var(--neo-text-muted)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
                  >
                    Override goal for this run {showGoalOverride ? '▴' : '▾'}
                  </button>
                  {showGoalOverride && (
                    <TextArea value={runGoal} onChange={setRunGoal}
                      placeholder={selected?.goal || selected?.description || "e.g. Analyze the latest news about technology trends..."}
                      rows={2} />
                  )}
                </div>
                <div>
                  <label className="text-xs font-medium mb-1 block" style={{ color: 'var(--neo-text-muted)' }}>
                    LLM Model <span className="font-normal" style={{ color: 'var(--neo-text-dim)' }}>(provider:model — e.g. openai:gpt-4o, groq:llama-3.3-70b-versatile)</span>
                  </label>
                  <select
                    value={llmModel}
                    onChange={(e) => setLlmModel(e.target.value)}
                    className="w-full px-2.5 py-1.5 rounded-lg text-xs outline-none"
                    style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                  >
                    <option value="">Auto (first available provider)</option>
                    <option value="openai:gpt-4o">OpenAI GPT-4o (best quality)</option>
                    <option value="openai:gpt-4o-mini">OpenAI GPT-4o Mini (fast + cheap)</option>
                    <option value="anthropic:claude-sonnet-4-20250514">Anthropic Claude Sonnet</option>
                    <option value="groq:llama-3.3-70b-versatile">Groq Llama 3.3 70B (fast)</option>
                    <option value="groq:openai/gpt-oss-120b">Groq GPT-oss 120B</option>
                    <option value="ollama:llama3.1">Ollama Llama 3.1 (local)</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs font-medium mb-1 block" style={{ color: 'var(--neo-text-muted)' }}>
                    Runtime Inputs <span className="font-normal" style={{ color: 'var(--neo-text-dim)' }}>(JSON key-value pairs agents can reference)</span>
                  </label>
                  <TextArea value={runInputs} onChange={setRunInputs}
                    placeholder={'{"topic": "AI regulation", "max_results": 10, "focus_region": "US"}'}
                    rows={2} mono />
                </div>
              </div>
            </SectionCard>

            {/* Bootstrap code viewer */}
            {bootstrapCode && bootstrapAgent && (
              <SectionCard className="overflow-hidden">
                <div className="px-4 py-2 flex items-center justify-between"
                  style={{ borderBottom: '1px solid var(--neo-border)' }}>
                  <div className="flex items-center gap-2">
                    <Terminal size={12} style={{ color: 'var(--neo-blue)' }} />
                    <span className="text-xs font-semibold" style={{ color: 'var(--neo-text-muted)' }}>
                      Bootstrap: {bootstrapAgent.name} ({bootstrapCode.framework})
                    </span>
                  </div>
                  <div className="flex items-center gap-1">
                    <button onClick={() => copyCode(bootstrapCode.code)}
                      className="flex items-center gap-1 px-2 py-1 rounded text-xs"
                      style={{ color: 'var(--neo-text-muted)', border: '1px solid var(--neo-border)' }}>
                      <Copy size={10} /> Copy Code
                    </button>
                    <button onClick={() => { setBootstrapCode(null); setBootstrapAgent(null); }}
                      className="p-1 rounded" style={{ color: 'var(--neo-text-dim)' }}><X size={12} /></button>
                  </div>
                </div>
                <div className="px-4 py-2 text-xs" style={{ background: 'var(--neo-surface)', borderBottom: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)', whiteSpace: 'pre-wrap' }}>
                  {bootstrapCode.instructions}
                </div>
                <pre className="p-4 text-xs overflow-auto" style={{
                  background: 'var(--neo-bg)', color: '#22c55e',
                  fontFamily: "'JetBrains Mono', monospace", lineHeight: 1.6, maxHeight: 400,
                }}>{bootstrapCode.code}</pre>
              </SectionCard>
            )}

            {/* Run History — comparison table */}
            {pastRuns.length > 0 && (
              <SectionCard className="overflow-hidden">
                <div className="px-4 py-2 flex items-center justify-between"
                  style={{ borderBottom: '1px solid var(--neo-border)' }}>
                  <span className="text-xs font-semibold" style={{ color: 'var(--neo-text-muted)' }}>
                    Run History ({pastRuns.length})
                  </span>
                  {pastRuns.length > 0 && (
                    <button onClick={() => { setRunResult(pastRuns[0]); setShowTheater(true); }}
                      className="text-[10px] flex items-center gap-1" style={{ color: 'var(--neo-blue)' }}>
                      <Eye size={10} /> View Last Run
                    </button>
                  )}
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-[10px]" style={{ borderCollapse: 'collapse' }}>
                    <thead>
                      <tr style={{ background: 'var(--neo-surface)', color: 'var(--neo-text-muted)' }}>
                        <th className="px-3 py-1.5 text-left font-semibold">Status</th>
                        <th className="px-3 py-1.5 text-left font-semibold">Mode</th>
                        <th className="px-3 py-1.5 text-right font-semibold">Agents</th>
                        <th className="px-3 py-1.5 text-right font-semibold">Steps</th>
                        <th className="px-3 py-1.5 text-right font-semibold">Duration</th>
                        <th className="px-3 py-1.5 text-right font-semibold">Score</th>
                        <th className="px-3 py-1.5 text-right font-semibold">Coverage</th>
                        <th className="px-3 py-1.5 text-right font-semibold">Discovery</th>
                        <th className="px-3 py-1.5 text-right font-semibold">Insight</th>
                        <th className="px-3 py-1.5 text-left font-semibold">Graph</th>
                        <th className="px-3 py-1.5 text-left font-semibold">Date</th>
                        <th className="px-3 py-1.5"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {pastRuns.map((run, ri) => {
                        const sc = run.scores || {};
                        const overall = sc.overall || 0;
                        const scoreColor = overall >= 70 ? '#22c55e' : overall >= 40 ? '#f59e0b' : overall > 0 ? '#ef4444' : 'var(--neo-text-dim)';
                        const totalSteps = (run.agents || []).reduce((sum, a) => sum + (a.steps || []).length, 0);
                        return (
                          <tr key={run.run_id || ri}
                            className="cursor-pointer hover:bg-white/5 transition"
                            onClick={() => { setRunResult(run); setShowTheater(true); }}
                            style={{ borderBottom: '1px solid var(--neo-border)' }}>
                            <td className="px-3 py-1.5">
                              <span className="px-1.5 py-0.5 rounded font-semibold"
                                style={{ background: run.status === 'completed' ? 'rgba(34,197,94,0.1)' : run.status === 'aborted' ? 'rgba(245,158,11,0.1)' : 'rgba(239,68,68,0.1)',
                                  color: run.status === 'completed' ? '#22c55e' : run.status === 'aborted' ? '#f59e0b' : '#ef4444' }}>
                                {run.status}
                              </span>
                            </td>
                            <td className="px-3 py-1.5">
                              <span className="px-1 py-0.5 rounded font-medium"
                                style={{ background: run.mode === 'sandbox' ? 'rgba(245,158,11,0.1)' : 'rgba(239,68,68,0.1)',
                                  color: run.mode === 'sandbox' ? '#f59e0b' : '#ef4444' }}>
                                {run.mode || 'live'}
                              </span>
                              {run.promoted && <span className="ml-1" style={{ color: '#22c55e' }}>promoted</span>}
                            </td>
                            <td className="px-3 py-1.5 text-right" style={{ color: 'var(--neo-text)' }}>
                              {(run.agents || []).length}
                            </td>
                            <td className="px-3 py-1.5 text-right" style={{ color: 'var(--neo-text)' }}>
                              {totalSteps}
                            </td>
                            <td className="px-3 py-1.5 text-right font-mono" style={{ color: 'var(--neo-text-dim)' }}>
                              {run.total_duration_ms ? `${(run.total_duration_ms / 1000).toFixed(1)}s` : '-'}
                            </td>
                            <td className="px-3 py-1.5 text-right font-bold" style={{ color: scoreColor }}>
                              {overall > 0 ? overall : '-'}
                            </td>
                            <td className="px-3 py-1.5 text-right" style={{ color: (sc.coverage || 0) >= 50 ? '#22c55e' : 'var(--neo-text-dim)' }}>
                              {sc.coverage || '-'}
                            </td>
                            <td className="px-3 py-1.5 text-right" style={{ color: (sc.discovery || 0) >= 50 ? '#22c55e' : 'var(--neo-text-dim)' }}>
                              {sc.discovery || '-'}
                            </td>
                            <td className="px-3 py-1.5 text-right" style={{ color: (sc.insight_quality || 0) >= 50 ? '#22c55e' : 'var(--neo-text-dim)' }}>
                              {sc.insight_quality || '-'}
                            </td>
                            <td className="px-3 py-1.5 font-mono truncate" style={{ color: 'var(--neo-text-dim)', maxWidth: 100 }}>
                              {run.source_graph || run.graph || '-'}
                            </td>
                            <td className="px-3 py-1.5" style={{ color: 'var(--neo-text-dim)' }}>
                              {run.started_at ? new Date(run.started_at).toLocaleDateString() : ''}
                            </td>
                            <td className="px-3 py-1.5">
                              <div className="flex items-center gap-1">
                                <Eye size={10} style={{ color: 'var(--neo-blue)' }} />
                                <button
                                  onClick={async (e) => {
                                    e.stopPropagation();
                                    if (!window.confirm('Delete this run?')) return;
                                    try {
                                      await api.delete(`/dashboard/experiments/${selected.id}/runs/${run.run_id}`);
                                      setPastRuns(prev => prev.filter(r => r.run_id !== run.run_id));
                                      toast.success('Run deleted');
                                    } catch { toast.error('Failed to delete'); }
                                  }}
                                  className="p-0.5 rounded hover:bg-red-500/10" title="Delete run">
                                  <Trash2 size={9} style={{ color: '#ef4444' }} />
                                </button>
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </SectionCard>
            )}
          </>
        ) : (
          <SectionCard className="flex items-center justify-center" style={{ minHeight: 400 }}>
            <div className="text-center">
              <Cpu size={32} style={{ color: 'var(--neo-border)', margin: '0 auto 8px' }} />
              <p className="text-sm font-medium mb-1" style={{ color: 'var(--neo-text-muted)' }}>
                Multi-Agent Experiments
              </p>
              <p className="text-xs mb-3" style={{ color: 'var(--neo-text-dim)' }}>
                Create an experiment, attach a context, add agents with prompts, and run.
              </p>
              <button onClick={() => setShowCreate(true)}
                className="text-xs px-3 py-2 rounded-lg font-medium"
                style={{ background: 'var(--neo-blue)', color: '#fff' }}>
                <Plus size={12} className="inline mr-1" />Create Experiment
              </button>
            </div>
          </SectionCard>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 3: Context Flow Visualizer
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Tab: All Runs — global comparison view with filters
// ---------------------------------------------------------------------------

function AllRunsTab() {
  const [allRuns, setAllRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sessions, setSessions] = useState([]);
  const [filterSession, setFilterSession] = useState('');
  const [filterMode, setFilterMode] = useState('');
  const [expandedRun, setExpandedRun] = useState(null);

  useEffect(() => {
    api.get('/dashboard/sessions').then(r => setSessions(r.data.sessions || [])).catch(() => {});
  }, []);

  const fetchRuns = useCallback(() => {
    setLoading(true);
    const params = new URLSearchParams();
    if (filterSession) params.append('session_id', filterSession);
    if (filterMode) params.append('mode', filterMode);
    params.append('limit', '100');
    api.get(`/dashboard/experiments/runs/all?${params}`)
      .then(r => setAllRuns(r.data.runs || []))
      .catch(() => setAllRuns([]))
      .finally(() => setLoading(false));
  }, [filterSession, filterMode]);

  useEffect(() => { fetchRuns(); }, [fetchRuns]);

  const scoreColor = (v) => v >= 70 ? '#22c55e' : v >= 40 ? '#f59e0b' : v > 0 ? '#ef4444' : 'var(--neo-text-dim)';

  return (
    <div className="flex flex-col gap-4">
      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-1.5">
          <Filter size={12} style={{ color: 'var(--neo-text-muted)' }} />
          <span className="text-xs font-medium" style={{ color: 'var(--neo-text-muted)' }}>Filter:</span>
        </div>
        <SelectInput
          value={filterSession}
          onChange={setFilterSession}
          options={[
            { value: '', label: 'All sessions' },
            ...sessions.map(s => ({ value: s.session_id || s.id, label: s.name || s.session_id || '' })),
          ]}
          style={{ minWidth: 200 }}
        />
        <SelectInput
          value={filterMode}
          onChange={setFilterMode}
          options={[
            { value: '', label: 'All modes' },
            { value: 'sandbox', label: 'Sandbox' },
            { value: 'live', label: 'Live' },
          ]}
          style={{ minWidth: 120 }}
        />
        <GhostButton onClick={fetchRuns}><RefreshCw size={10} /> Refresh</GhostButton>
        <span className="text-[10px] ml-auto" style={{ color: 'var(--neo-text-dim)' }}>
          {allRuns.length} runs
        </span>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 size={20} className="animate-spin" style={{ color: 'var(--neo-text-dim)' }} />
        </div>
      ) : allRuns.length === 0 ? (
        <SectionCard className="flex items-center justify-center" style={{ minHeight: 200 }}>
          <p className="text-xs" style={{ color: 'var(--neo-text-dim)' }}>No runs found. Run an experiment first.</p>
        </SectionCard>
      ) : (
        <SectionCard className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-[10px]" style={{ borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--neo-surface)', color: 'var(--neo-text-muted)' }}>
                  <th className="px-3 py-2 text-left font-semibold">Experiment</th>
                  <th className="px-3 py-2 text-left font-semibold">Status</th>
                  <th className="px-3 py-2 text-left font-semibold">Mode</th>
                  <th className="px-3 py-2 text-left font-semibold">Session</th>
                  <th className="px-3 py-2 text-right font-semibold">Agents</th>
                  <th className="px-3 py-2 text-right font-semibold">Steps</th>
                  <th className="px-3 py-2 text-right font-semibold">Time</th>
                  <th className="px-3 py-2 text-right font-semibold">Overall</th>
                  <th className="px-3 py-2 text-right font-semibold">Coverage</th>
                  <th className="px-3 py-2 text-right font-semibold">Discovery</th>
                  <th className="px-3 py-2 text-right font-semibold">Enrich</th>
                  <th className="px-3 py-2 text-right font-semibold">Collab</th>
                  <th className="px-3 py-2 text-right font-semibold">Insight</th>
                  <th className="px-3 py-2 text-left font-semibold">Graph</th>
                  <th className="px-3 py-2 text-left font-semibold">Date</th>
                </tr>
              </thead>
              <tbody>
                {allRuns.map((run, ri) => {
                  const sc = run.scores || {};
                  const overall = sc.overall || 0;
                  const totalSteps = (run.agents || []).reduce((sum, a) => sum + (a.steps || []).length, 0);
                  const sessName = sessions.find(s => (s.session_id || s.id) === run.session_id)?.name || '';
                  return (
                    <React.Fragment key={run.run_id || ri}>
                      <tr className="cursor-pointer hover:bg-white/5 transition"
                        onClick={() => setExpandedRun(expandedRun === run.run_id ? null : run.run_id)}
                        style={{ borderBottom: '1px solid var(--neo-border)' }}>
                        <td className="px-3 py-1.5 font-medium" style={{ color: 'var(--neo-text)', maxWidth: 120 }}>
                          <div className="truncate">{run.experiment_name || run.experiment_id?.slice(0, 8)}</div>
                        </td>
                        <td className="px-3 py-1.5">
                          <span className="px-1.5 py-0.5 rounded font-semibold"
                            style={{ background: run.status === 'completed' ? 'rgba(34,197,94,0.1)' : run.status === 'aborted' ? 'rgba(245,158,11,0.1)' : 'rgba(239,68,68,0.1)',
                              color: run.status === 'completed' ? '#22c55e' : run.status === 'aborted' ? '#f59e0b' : '#ef4444' }}>
                            {run.status}
                          </span>
                        </td>
                        <td className="px-3 py-1.5">
                          <span className="px-1 py-0.5 rounded"
                            style={{ background: run.mode === 'sandbox' ? 'rgba(245,158,11,0.1)' : 'rgba(239,68,68,0.1)',
                              color: run.mode === 'sandbox' ? '#f59e0b' : '#ef4444' }}>
                            {run.mode || '-'}
                          </span>
                        </td>
                        <td className="px-3 py-1.5 truncate" style={{ color: 'var(--neo-text-dim)', maxWidth: 100 }}>{sessName || '-'}</td>
                        <td className="px-3 py-1.5 text-right" style={{ color: 'var(--neo-text)' }}>{(run.agents || []).length}</td>
                        <td className="px-3 py-1.5 text-right" style={{ color: 'var(--neo-text)' }}>{totalSteps}</td>
                        <td className="px-3 py-1.5 text-right font-mono" style={{ color: 'var(--neo-text-dim)' }}>
                          {run.total_duration_ms ? `${(run.total_duration_ms / 1000).toFixed(1)}s` : '-'}
                        </td>
                        <td className="px-3 py-1.5 text-right font-bold" style={{ color: scoreColor(overall) }}>{overall || '-'}</td>
                        <td className="px-3 py-1.5 text-right" style={{ color: scoreColor(sc.coverage || 0) }}>{sc.coverage || '-'}</td>
                        <td className="px-3 py-1.5 text-right" style={{ color: scoreColor(sc.discovery || 0) }}>{sc.discovery || '-'}</td>
                        <td className="px-3 py-1.5 text-right" style={{ color: scoreColor(sc.enrichment || 0) }}>{sc.enrichment || '-'}</td>
                        <td className="px-3 py-1.5 text-right" style={{ color: scoreColor(sc.collaboration || 0) }}>{sc.collaboration || '-'}</td>
                        <td className="px-3 py-1.5 text-right" style={{ color: scoreColor(sc.insight_quality || 0) }}>{sc.insight_quality || '-'}</td>
                        <td className="px-3 py-1.5 font-mono truncate" style={{ color: 'var(--neo-text-dim)', maxWidth: 80 }}>
                          {run.source_graph || run.graph || '-'}
                        </td>
                        <td className="px-3 py-1.5 whitespace-nowrap" style={{ color: 'var(--neo-text-dim)' }}>
                          {run.started_at ? new Date(run.started_at).toLocaleString() : '-'}
                        </td>
                        <td className="px-3 py-1.5">
                          <button
                            onClick={async (e) => {
                              e.stopPropagation();
                              if (!window.confirm('Delete this run permanently?')) return;
                              try {
                                await api.delete(`/dashboard/experiments/${run.experiment_id}/runs/${run.run_id}`);
                                setAllRuns(prev => prev.filter(r => r.run_id !== run.run_id));
                                toast.success('Run deleted');
                              } catch { toast.error('Failed to delete'); }
                            }}
                            className="p-1 rounded hover:bg-red-500/10" title="Delete run">
                            <Trash2 size={10} style={{ color: '#ef4444' }} />
                          </button>
                        </td>
                      </tr>
                      {/* Expanded: agent details */}
                      {expandedRun === run.run_id && (
                        <tr><td colSpan={15} style={{ background: 'var(--neo-bg)', padding: 0 }}>
                          <div className="px-4 py-2 space-y-1">
                            <div className="flex items-center gap-3 text-[10px] mb-1" style={{ color: 'var(--neo-text-dim)' }}>
                              <span>Track: <span className="font-mono">{run.track_id}</span></span>
                              <span>LLM: {run.llm_provider || '-'}</span>
                              {run.inputs && Object.keys(run.inputs).length > 0 && (
                                <span>Inputs: {JSON.stringify(run.inputs).slice(0, 80)}</span>
                              )}
                              {run.goal && <span>Goal: {run.goal.slice(0, 80)}</span>}
                            </div>
                            {(run.agents || []).map((a, ai) => (
                              <div key={ai} className="flex items-center gap-2 text-[10px] px-2 py-1 rounded"
                                style={{ background: 'var(--neo-surface)', borderLeft: `2px solid ${NODE_COLORS[ai % NODE_COLORS.length]}` }}>
                                <span className="font-semibold" style={{ color: NODE_COLORS[ai % NODE_COLORS.length] }}>{a.name}</span>
                                <span style={{ color: 'var(--neo-text-dim)' }}>{a.role}</span>
                                {a.phase && <span className="px-1 py-0.5 rounded font-bold uppercase" style={{ fontSize: 8, background: 'rgba(139,92,246,0.1)', color: '#8b5cf6' }}>{a.phase}</span>}
                                <span style={{ color: a.status === 'completed' ? '#22c55e' : '#ef4444' }}>{a.status}</span>
                                <span style={{ color: 'var(--neo-text-dim)' }}>{(a.steps || []).length} steps</span>
                                <span style={{ color: 'var(--neo-text-dim)' }}>{a.duration_ms}ms</span>
                                {a.error && <span style={{ color: '#ef4444' }}>Error: {a.error.slice(0, 60)}</span>}
                              </div>
                            ))}
                          </div>
                        </td></tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </SectionCard>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Live Sessions Tab  (lifted from WarRoomPage)
// ---------------------------------------------------------------------------

function LiveSessionsTab() {
  const [sessions, setSessions] = useState([]);
  const [selectedSessionId, setSelectedSessionId] = useState(null);
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [agentsLoading, setAgentsLoading] = useState(false);
  const [theaterEvents, setTheaterEvents] = useState([]);
  const [graphKey, setGraphKey] = useState(0);
  const [showRuntime, setShowRuntime] = useState(true);

  const loadSessions = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get('/dashboard/sessions');
      const list = data.sessions || [];
      setSessions(list);
      if (list.length > 0 && !selectedSessionId) {
        setSelectedSessionId(list[0].session_id);
      }
    } catch (err) {
      console.error('Failed to load sessions', err);
    } finally {
      setLoading(false);
    }
  }, [selectedSessionId]);

  useEffect(() => { loadSessions(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!selectedSessionId) { setAgents([]); return; }
    let cancelled = false;
    (async () => {
      setAgentsLoading(true);
      try {
        const { data } = await api.get(`/dashboard/sessions/${selectedSessionId}/agents`, { suppressErrorToast: true });
        if (!cancelled) setAgents(data.agents || []);
      } catch (err) {
        console.error('Failed to load agents', err);
        if (!cancelled) setAgents([]);
      } finally {
        if (!cancelled) setAgentsLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [selectedSessionId]);

  useEffect(() => {
    if (!selectedSessionId) return;
    const interval = setInterval(() => setGraphKey(k => k + 1), 5000);
    return () => clearInterval(interval);
  }, [selectedSessionId]);

  const handleWsEvent = useCallback((event) => {
    setTheaterEvents(prev => [event, ...prev].slice(0, 100));
    if (event.event_type === 'agent_joined') {
      const sid = selectedSessionId;
      if (sid) {
        api.get(`/dashboard/sessions/${sid}/agents`, { suppressErrorToast: true })
          .then(res => setAgents(res.data?.agents || res.data || []))
          .catch(() => {});
      }
    }
  }, [selectedSessionId]);

  const selectedSession = sessions.find(s => s.session_id === selectedSessionId);
  const contextGraph = selectedSession?.graph_namespace || '';
  const runtimeGraph = selectedSession?.runtime_namespace || contextGraph;
  const activeGraph = showRuntime ? runtimeGraph : contextGraph;
  const agentNames = agents.map(a => a.name || a.agent_id);

  const bg = '#0a0a1a';
  const panelBg = '#111827';
  const border = '#1e293b';
  const textMuted = '#94a3b8';

  if (!loading && !selectedSessionId) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: 400, background: bg, color: textMuted, borderRadius: 12 }}>
        <Swords size={48} style={{ marginBottom: 16, opacity: 0.4 }} />
        <div style={{ fontSize: 18, fontWeight: 600 }}>No active sessions</div>
        <div style={{ fontSize: 13, marginTop: 8 }}>Create a session on the Sessions page first.</div>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 220px)', background: bg, color: '#e2e8f0', fontFamily: "'Inter', sans-serif", borderRadius: 12, overflow: 'hidden' }}>

      {/* Sub-header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px', borderBottom: `1px solid ${border}`, background: panelBg, flexShrink: 0 }}>
        <Swords size={16} style={{ color: '#c084fc' }} />

        {/* Session picker */}
        <select
          value={selectedSessionId || ''}
          onChange={e => setSelectedSessionId(e.target.value)}
          style={{ padding: '5px 10px', borderRadius: 6, background: '#1e293b', color: '#e2e8f0', border: `1px solid ${border}`, fontSize: 13, cursor: 'pointer', minWidth: 200 }}
        >
          {sessions.map(s => (
            <option key={s.session_id} value={s.session_id}>{s.name || s.session_id}</option>
          ))}
        </select>

        <button
          onClick={loadSessions}
          style={{ background: 'none', border: 'none', color: textMuted, cursor: 'pointer', padding: 4 }}
          title="Refresh sessions"
        >
          <RefreshCw size={14} />
        </button>

        {/* Agent badges */}
        <div style={{ display: 'flex', gap: 6, marginLeft: 'auto', alignItems: 'center' }}>
          {agentsLoading && <Loader2 size={13} style={{ color: textMuted }} />}
          {agentNames.map((name, i) => (
            <span
              key={name}
              style={{
                padding: '2px 10px', borderRadius: 12, fontSize: 11, fontWeight: 600,
                background: `${AGENT_COLORS[i % AGENT_COLORS.length]}22`,
                color: AGENT_COLORS[i % AGENT_COLORS.length],
                border: `1px solid ${AGENT_COLORS[i % AGENT_COLORS.length]}44`,
              }}
            >
              {name}
            </span>
          ))}
        </div>

        {/* Graph toggle */}
        {selectedSession && (
          <button
            onClick={() => setShowRuntime(r => !r)}
            style={{
              background: showRuntime ? '#7c3aed33' : '#0ea5e933',
              color: showRuntime ? '#c084fc' : '#22d3ee',
              border: `1px solid ${showRuntime ? '#7c3aed66' : '#0ea5e966'}`,
              borderRadius: 6, padding: '4px 10px', fontSize: 11, cursor: 'pointer', marginLeft: 12,
            }}
          >
            <Database size={12} style={{ marginRight: 4, verticalAlign: -2 }} />
            {showRuntime ? 'Session Graph' : 'Context Graph'}
          </button>
        )}

        {/* Status */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginLeft: 12 }}>
          <Radio size={12} style={{ color: selectedSession ? '#34d399' : textMuted }} />
          <span style={{ fontSize: 11, color: selectedSession ? '#34d399' : textMuted }}>
            {selectedSession ? 'LIVE' : 'OFFLINE'}
          </span>
        </div>
      </div>

      {/* Body */}
      {loading ? (
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Loader2 size={28} style={{ color: textMuted }} />
        </div>
      ) : (
        <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>

          {/* LEFT — AgentTheater */}
          <div style={{ width: 260, flexShrink: 0, borderRight: `1px solid ${border}`, background: panelBg, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            <div style={{ padding: '8px 12px', fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1, color: textMuted, borderBottom: `1px solid ${border}` }}>
              Agents
            </div>
            <div style={{ flex: 1, overflow: 'auto' }}>
              {selectedSessionId && (
                <AgentTheater
                  events={theaterEvents}
                  isRunning={!!selectedSession}
                  sessionId={selectedSessionId}
                  agents={agentNames}
                />
              )}
            </div>
          </div>

          {/* CENTER — Graph */}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            <div style={{ padding: '8px 12px', fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1, color: textMuted, borderBottom: `1px solid ${border}`, background: panelBg }}>
              Session Graph
            </div>
            <div style={{ flex: 1, position: 'relative' }}>
              {runtimeGraph ? (
                <DashboardGraphExplorer key={graphKey} graphName={activeGraph} embedded />
              ) : (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: textMuted, fontSize: 13 }}>
                  No runtime graph available
                </div>
              )}
            </div>
          </div>

          {/* RIGHT — Activity + Live Feed */}
          <div style={{ width: 300, flexShrink: 0, borderLeft: `1px solid ${border}`, background: panelBg, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', borderBottom: `1px solid ${border}` }}>
              <div style={{ padding: '8px 12px', fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1, color: textMuted, borderBottom: `1px solid ${border}` }}>
                Activity
              </div>
              <div style={{ flex: 1, overflow: 'auto' }}>
                {selectedSessionId && runtimeGraph && (
                  <AgentActivityFeed graphName={activeGraph} sessionId={selectedSessionId} />
                )}
              </div>
            </div>
            <div style={{ height: 220, flexShrink: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              <div style={{ padding: '8px 12px', fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1, color: textMuted, borderBottom: `1px solid ${border}` }}>
                Live Feed
              </div>
              <div style={{ flex: 1, overflow: 'auto' }}>
                {selectedSessionId && <LiveFeed sessionId={selectedSessionId} onEvent={handleWsEvent} />}
              </div>
            </div>
          </div>

        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 3: Context Flow Visualizer
// ---------------------------------------------------------------------------

function FlowVisualizerTab() {
  const [contexts, setContexts] = useState([]);
  const [flowContext, setFlowContext] = useState('');
  const [flowEvents, setFlowEvents] = useState([]);
  const [watching, setWatching] = useState(false);
  const eventsRef = useRef(null);

  useEffect(() => {
    api.get('/dashboard/contexts').then((r) => setContexts(r.data.contexts || [])).catch(() => {});
  }, []);

  useEffect(() => {
    if (eventsRef.current) eventsRef.current.scrollTop = eventsRef.current.scrollHeight;
  }, [flowEvents]);

  // Subscribe to events when watching
  useEvent('ingest_job_complete', useCallback((data) => {
    if (!watching) return;
    const ctx = contexts.find((c) => c.id === flowContext || c.name === flowContext);
    if (ctx && data?.context_id && data.context_id !== ctx.id) return;
    setFlowEvents((prev) => [...prev, {
      ts: new Date().toISOString(),
      agent: data?.agent || 'system',
      action: 'ingest',
      target: data?.source || 'unknown',
      detail: data,
    }]);
  }, [watching, flowContext, contexts]));

  useEvent('context_updated', useCallback((data) => {
    if (!watching) return;
    const ctx = contexts.find((c) => c.id === flowContext || c.name === flowContext);
    if (ctx && data?.context_id && data.context_id !== ctx.id) return;
    setFlowEvents((prev) => [...prev, {
      ts: new Date().toISOString(),
      agent: data?.agent || 'system',
      action: data?.action || 'write',
      target: data?.node_type || data?.target || 'context',
      detail: data,
    }]);
  }, [watching, flowContext, contexts]));

  useEvent('node_created', useCallback((data) => {
    if (!watching) return;
    setFlowEvents((prev) => [...prev, {
      ts: new Date().toISOString(),
      agent: data?.agent || 'unknown',
      action: 'write',
      target: data?.node_type || data?.label || 'node',
      detail: data,
    }]);
  }, [watching]));

  useEvent('search_executed', useCallback((data) => {
    if (!watching) return;
    setFlowEvents((prev) => [...prev, {
      ts: new Date().toISOString(),
      agent: data?.agent || 'unknown',
      action: 'search',
      target: data?.query || 'search',
      detail: data,
    }]);
  }, [watching]));

  const selectedCtx = contexts.find((c) => c.id === flowContext || c.name === flowContext);
  const graphNamespace = selectedCtx?.graph_namespace || selectedCtx?.name || flowContext;

  const toggleWatch = () => {
    if (!flowContext && !watching) {
      toast.error('Select a context first');
      return;
    }
    if (watching) {
      setWatching(false);
    } else {
      setFlowEvents([]);
      setWatching(true);
      toast.success('Watching for events...');
    }
  };

  const actionColors = {
    read: '#3b82f6',
    write: '#22c55e',
    search: '#f59e0b',
    ingest: '#8b5cf6',
  };

  // Hash agent name to a color
  const agentColor = (name) => {
    if (!name) return 'var(--neo-text-muted)';
    let hash = 0;
    for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
    const colors = ['#3b82f6', '#10b981', '#f59e0b', '#ec4899', '#8b5cf6', '#06b6d4', '#ef4444'];
    return colors[Math.abs(hash) % colors.length];
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Controls */}
      <div className="flex items-center gap-3">
        <SelectInput
          value={flowContext}
          onChange={setFlowContext}
          options={contexts.map((c) => ({ value: c.context_id || c.id || c.name, label: c.name }))}
          placeholder="Select context..."
          style={{ minWidth: 240 }}
        />
        <PrimaryButton
          onClick={toggleWatch}
          color={watching ? '#ef4444' : '#8b5cf6'}
        >
          {watching ? <><Square size={14} /> Stop Watching</> : <><Eye size={14} /> Start Watching</>}
        </PrimaryButton>
        {watching && (
          <div className="flex items-center gap-2">
            <span className="relative flex h-2.5 w-2.5">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style={{ background: '#22c55e' }} />
              <span className="relative inline-flex rounded-full h-2.5 w-2.5" style={{ background: '#22c55e' }} />
            </span>
            <span className="text-xs" style={{ color: '#22c55e' }}>Live</span>
          </div>
        )}
        {flowEvents.length > 0 && (
          <GhostButton onClick={() => setFlowEvents([])}>
            <Trash2 size={10} /> Clear
          </GhostButton>
        )}
      </div>

      {/* Split view */}
      <div className="flex gap-4" style={{ minHeight: 520 }}>
        {/* Left: Graph explorer */}
        <SectionCard className="overflow-hidden" style={{ width: '60%' }}>
          {graphNamespace ? (
            <DashboardGraphExplorer graphName={graphNamespace} embedded />
          ) : (
            <div className="flex items-center justify-center h-full" style={{ minHeight: 400 }}>
              <div className="text-center">
                <Layers size={32} style={{ color: 'var(--neo-border)', margin: '0 auto 8px' }} />
                <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
                  Select a context to view its graph.
                </p>
              </div>
            </div>
          )}
        </SectionCard>

        {/* Right: Event timeline */}
        <SectionCard className="flex flex-col overflow-hidden" style={{ width: '40%' }}>
          <div
            className="flex items-center justify-between px-4 py-2"
            style={{ borderBottom: '1px solid var(--neo-border)' }}
          >
            <span className="text-xs font-semibold" style={{ color: 'var(--neo-text-muted)' }}>
              Event Timeline
            </span>
            <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
              {flowEvents.length} events
            </span>
          </div>
          <div
            ref={eventsRef}
            className="flex-1 overflow-y-auto"
            style={{ background: 'var(--neo-bg)', maxHeight: 480 }}
          >
            {flowEvents.length === 0 ? (
              <div className="flex items-center justify-center h-full" style={{ minHeight: 200 }}>
                <p className="text-xs text-center px-4" style={{ color: 'var(--neo-text-muted)' }}>
                  {watching
                    ? 'Waiting for events... Perform actions on the selected context to see them here.'
                    : 'Click "Start Watching" to begin capturing events.'}
                </p>
              </div>
            ) : (
              <div className="flex flex-col">
                {flowEvents.map((ev, i) => (
                  <div
                    key={i}
                    className="flex items-start gap-2 px-3 py-2 text-xs"
                    style={{ borderBottom: '1px solid var(--neo-border)' }}
                  >
                    {/* Timeline dot */}
                    <div className="flex flex-col items-center flex-shrink-0 mt-0.5">
                      <div
                        className="w-2 h-2 rounded-full"
                        style={{ background: actionColors[ev.action] || 'var(--neo-text-muted)' }}
                      />
                      {i < flowEvents.length - 1 && (
                        <div className="w-px flex-1 mt-0.5" style={{ background: 'var(--neo-border)', minHeight: 16 }} />
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="text-[10px]" style={{ color: 'var(--neo-text-muted)' }}>
                          {new Date(ev.ts).toLocaleTimeString()}
                        </span>
                        <span className="font-semibold" style={{ color: agentColor(ev.agent) }}>
                          {ev.agent}
                        </span>
                        <span
                          className="px-1.5 py-0.5 rounded text-[10px] font-bold uppercase"
                          style={{
                            color: actionColors[ev.action] || 'var(--neo-text-muted)',
                            background: (actionColors[ev.action] || 'var(--neo-text-muted)') + '18',
                          }}
                        >
                          {ev.action}
                        </span>
                      </div>
                      <p className="mt-0.5 truncate" style={{ color: 'var(--neo-text)' }}>
                        {ev.target}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </SectionCard>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4">
        {Object.entries(actionColors).map(([action, color]) => (
          <div key={action} className="flex items-center gap-1.5">
            <div className="w-2 h-2 rounded-full" style={{ background: color }} />
            <span className="text-[10px] uppercase font-semibold" style={{ color }}>{action}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function PlaygroundPage() {
  const [activeTab, setActiveTab] = useState('tools');
  const [graphs, setGraphs] = useState([]);
  const [tools, setTools] = useState([]);

  useEffect(() => {
    api.get('/dashboard/graphs').then((r) => setGraphs(r.data.graphs || [])).catch(() => {});
    api.get('/dashboard/playground/tools').then((r) => setTools(r.data.tools || [])).catch(() => {});
  }, []);

  return (
    <div className="max-w-7xl">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-xl font-bold" style={{ color: 'var(--neo-text)' }}>Agent DevTools</h1>
        <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
          Test MCP tools, simulate agent workflows, and visualize context flows.
        </p>
      </div>

      {/* Tab bar */}
      <div
        className="flex gap-1 mb-6 p-1 rounded-xl w-fit"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
      >
        {TABS.map((tab) => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition"
              style={{
                background: isActive ? tab.color + '18' : 'transparent',
                color: isActive ? tab.color : 'var(--neo-text-muted)',
                border: isActive ? `1px solid ${tab.color}30` : '1px solid transparent',
              }}
            >
              <Icon size={15} />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      {activeTab === 'tools' && <ToolTesterTab graphs={graphs} />}
      {activeTab === 'experiments' && <ExperimentTab graphs={graphs} />}
      {activeTab === 'runs' && <AllRunsTab />}
      {activeTab === 'flow' && <FlowVisualizerTab />}
      {activeTab === 'sessions' && <LiveSessionsTab />}
    </div>
  );
}

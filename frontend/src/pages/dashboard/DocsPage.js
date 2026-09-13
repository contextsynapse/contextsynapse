import React, { useState } from 'react';
import { BookOpen, Copy, Check, ChevronDown, ChevronUp } from 'lucide-react';

const AIQL_EXAMPLES = [
  {
    title: 'Graph Management',
    commands: [
      { query: 'CREATE GRAPH my_project', desc: 'Create a new graph namespace' },
      { query: 'USE GRAPH my_project', desc: 'Switch active graph' },
      { query: 'SHOW NAMESPACES', desc: 'List all graphs' },
      { query: 'DROP GRAPH my_project', desc: 'Delete a graph and all its data' },
    ],
  },
  {
    title: 'Create Nodes',
    commands: [
      { query: 'CREATE NODE Person {name: "Alice", role: "Engineer", age: 30}', desc: 'Create node with properties' },
      { query: 'CREATE NODE Task {title: "Build Auth", status: "open", priority: "high"}', desc: 'Task node' },
      { query: 'CREATE NODE Decision {title: "Use PostgreSQL", rationale: "Better scaling"}', desc: 'Decision node' },
      { query: 'CREATE NODE Tag {}', desc: 'Node with empty properties' },
      { query: 'CREATE NODE Person {name: "Bob"} UNIQUE KEY (name)', desc: 'Prevent duplicates by key' },
    ],
  },
  {
    title: 'Query Nodes',
    commands: [
      { query: 'SELECT *', desc: 'Get all nodes in graph' },
      { query: 'SELECT * FROM Person', desc: 'Filter by node type' },
      { query: 'SELECT * FROM Task WHERE status = "open"', desc: 'Filter by property' },
      { query: 'SELECT * FROM Task WHERE priority = "high" LIMIT 5', desc: 'With limit' },
    ],
  },
  {
    title: 'Create Edges',
    commands: [
      { query: 'CREATE EDGE WORKS_ON FROM "<source-uuid>" TO "<target-uuid>" {role: "lead"}', desc: 'Edge between two nodes by UUID' },
      { query: 'CREATE EDGE DEPENDS_ON FROM "<task1-uuid>" TO "<task2-uuid>" {}', desc: 'Edge with empty properties' },
      { query: 'CREATE EDGE Person -> Project {type: "WORKS_ON"}', desc: 'Arrow syntax (by node type)' },
    ],
  },
  {
    title: 'Update & Delete',
    commands: [
      { query: 'UPDATE NODE Task WHERE title = "Build Auth" SET {status: "done"}', desc: 'Update node properties' },
      { query: 'DELETE NODE "<uuid>"', desc: 'Delete a node by UUID (removes connected edges)' },
      { query: 'DELETE ALL NODES WHERE label = "TempNode"', desc: 'Delete all matching nodes' },
      { query: 'SHOW EDGES', desc: 'List all edges in the graph' },
    ],
  },
  {
    title: 'Search & Traversal',
    commands: [
      { query: 'FIND NODES', desc: 'Find all nodes' },
      { query: 'FIND NODES WHERE name = "Alice"', desc: 'Find with property filter' },
      { query: 'NEIGHBORS FROM Person WHERE name = "Alice" DEPTH 2', desc: 'Get neighbor nodes' },
      { query: 'SHORTEST_PATH FROM "<uuid1>" TO "<uuid2>"', desc: 'Find shortest path' },
      { query: 'SHOW STATS', desc: 'Graph statistics' },
    ],
  },
];

const API_SECTIONS = [
  {
    title: 'Graph (AIQL)',
    tag: 'graph',
    endpoints: [
      {
        method: 'POST',
        path: '/aiql',
        desc: 'Execute any AIQL query',
        body: '{ "query": "CREATE NODE Person {name: \\"Alice\\"}", "namespace": "my_project" }',
        response: '{ "success": true, "node_id": "...", "nodes": [...], "edges": [...] }',
      },
      {
        method: 'POST',
        path: '/aiql',
        desc: 'Query nodes',
        body: '{ "query": "SELECT * FROM Person WHERE role = \\"Engineer\\"", "namespace": "my_project" }',
        response: '{ "success": true, "nodes": [{ "id": "...", "label": "Person", "properties": {...} }] }',
      },
      {
        method: 'POST',
        path: '/aiql',
        desc: 'Create edge between nodes',
        body: '{ "query": "CREATE EDGE WORKS_ON FROM \\"<src-uuid>\\" TO \\"<tgt-uuid>\\" {role: \\"lead\\"}", "namespace": "my_project" }',
        response: '{ "success": true, "edge_id": "...", "message": "Edge WORKS_ON created" }',
      },
      {
        method: 'POST',
        path: '/aiql',
        desc: 'Delete a node by UUID',
        body: '{ "query": "DELETE NODE \\"<uuid>\\"", "namespace": "my_project" }',
        response: '{ "success": true, "message": "Deleted node Person (id: ...), removed 2 connected edges" }',
      },
    ],
  },
  {
    title: 'Context Runtime',
    tag: 'context-runtime',
    endpoints: [
      {
        method: 'POST',
        path: '/context/sessions',
        desc: 'Create a new context runtime',
        body: '{ "name": "my-project" }',
        response: '{ "session_id": "...", "name": "my-project", "status": "active" }',
      },
      {
        method: 'GET',
        path: '/context/sessions/{session_id}',
        desc: 'Get context runtime details and metadata',
        response: '{ "session_id": "...", "name": "...", "status": "active", "member_count": 3 }',
      },
      {
        method: 'GET',
        path: '/context/sessions/{session_id}/export',
        desc: 'Export context runtime in LLM-ready format',
        params: 'format=messages|prompt|markdown&max_tokens=4000',
        response: '{ "format": "messages", "preview": [...], "estimated_tokens": 1234 }',
      },
      {
        method: 'POST',
        path: '/context/sessions/{session_id}/context',
        desc: 'Contribute context to a runtime',
        body: '{ "content": "...", "content_type": "text", "role": "background" }',
        response: '{ "status": "added" }',
      },
      {
        method: 'DELETE',
        path: '/context/sessions/{session_id}',
        desc: 'Delete a context runtime',
        response: '{ "status": "deleted" }',
      },
    ],
  },
  {
    title: 'Agents',
    tag: 'agents',
    endpoints: [
      {
        method: 'POST',
        path: '/context/agents',
        desc: 'Register a new agent and get an API key',
        body: '{ "name": "my-agent", "role": "agent", "capabilities": ["read", "write"] }',
        response: '{ "agent_id": "...", "api_key": "agent_id:secret" }',
      },
      {
        method: 'GET',
        path: '/context/agents/{agent_id}',
        desc: 'Get agent details',
        response: '{ "agent_id": "...", "name": "...", "role": "agent", "status": "active" }',
      },
      {
        method: 'DELETE',
        path: '/context/agents/{agent_id}',
        desc: 'Deregister an agent',
        response: '{ "status": "deregistered" }',
      },
    ],
  },
  {
    title: 'Access Control',
    tag: 'access',
    endpoints: [
      {
        method: 'POST',
        path: '/context/sessions/{session_id}/members',
        desc: 'Grant an agent access to a context runtime',
        body: '{ "agent_id": "...", "level": "read" }',
        response: '{ "status": "granted" }',
      },
      {
        method: 'GET',
        path: '/context/sessions/{session_id}/members',
        desc: 'List agents with access to a context runtime',
        response: '{ "members": [{ "agent_id": "...", "level": "read" }] }',
      },
      {
        method: 'DELETE',
        path: '/context/sessions/{session_id}/members/{agent_id}',
        desc: 'Revoke agent access',
        response: '{ "status": "revoked" }',
      },
    ],
  },
  {
    title: 'Files',
    tag: 'files',
    endpoints: [
      {
        method: 'POST',
        path: '/context/sessions/{session_id}/documents/upload',
        desc: 'Upload a file into context runtime (PDF, DOCX, CSV, TXT)',
        body: 'multipart/form-data: file',
        response: '{ "blob_id": "...", "filename": "report.pdf", "size": 12345 }',
      },
      {
        method: 'GET',
        path: '/context/sessions/{session_id}/files',
        desc: 'List uploaded files',
        response: '{ "files": [{ "blob_id": "...", "filename": "...", "size": 123 }] }',
      },
    ],
  },
  {
    title: 'Conversations',
    tag: 'conversations',
    endpoints: [
      {
        method: 'POST',
        path: '/context/sessions/{session_id}/conversations',
        desc: 'Create a conversation thread',
        body: '{ "title": "Analysis thread" }',
        response: '{ "conversation_id": "...", "title": "Analysis thread" }',
      },
      {
        method: 'POST',
        path: '/context/conversations/{conversation_id}/messages',
        desc: 'Append a message to a conversation',
        body: '{ "role": "assistant", "content": "Here is my analysis..." }',
        response: '{ "message_id": "..." }',
      },
      {
        method: 'GET',
        path: '/context/conversations/{conversation_id}',
        desc: 'Get conversation with messages',
        response: '{ "conversation_id": "...", "messages": [...] }',
      },
    ],
  },
  {
    title: 'Search',
    tag: 'search',
    endpoints: [
      {
        method: 'POST',
        path: '/search',
        desc: 'Semantic search across graph nodes',
        body: '{ "query": "quarterly revenue", "top_k": 10 }',
        response: '{ "results": [{ "node_id": "...", "score": 0.95, "properties": {...} }] }',
      },
    ],
  },
  {
    title: 'WebSocket',
    tag: 'websocket',
    endpoints: [
      {
        method: 'WS',
        path: '/context/sessions/{session_id}/ws',
        desc: 'Real-time event stream for a context runtime',
        body: 'Send: { "agent_id": "...", "type": "subscribe" }',
        response: 'Receive: { "event_type": "context_added", "agent_id": "...", "content_type": "text" }',
      },
    ],
  },
];

const METHOD_COLORS = {
  GET: { bg: 'rgba(76,217,100,0.15)', color: 'var(--neo-green)' },
  POST: { bg: 'rgba(0,122,255,0.15)', color: 'var(--neo-blue)' },
  PUT: { bg: 'rgba(255,149,0,0.15)', color: '#ff9500' },
  DELETE: { bg: 'rgba(242,87,87,0.15)', color: '#ef4444' },
  PATCH: { bg: 'rgba(175,82,222,0.15)', color: '#af52de' },
  WS: { bg: 'rgba(0,210,255,0.15)', color: 'var(--neo-cyan)' },
};

export default function DocsPage() {
  const [expandedSection, setExpandedSection] = useState(null);
  const [expandedEndpoint, setExpandedEndpoint] = useState(null);
  const [copied, setCopied] = useState(null);

  const copyText = (text, id) => {
    navigator.clipboard.writeText(text);
    setCopied(id);
    setTimeout(() => setCopied(null), 1500);
  };

  const baseUrl = window.location.origin;

  return (
    <div className="max-w-4xl">
      <div className="mb-6">
        <h1 className="text-xl font-bold" style={{ color: 'var(--neo-text)' }}>API Documentation</h1>
        <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
          Complete reference for the ContextSynapse Context API
        </p>
      </div>

      {/* Auth section */}
      <div
        className="mb-6 p-5 rounded-xl"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
      >
        <h3 className="text-sm font-semibold mb-2" style={{ color: 'var(--neo-text)' }}>
          Authentication
        </h3>
        <p className="text-xs mb-3" style={{ color: 'var(--neo-text-muted)' }}>
          All API requests require a Bearer token in the Authorization header.
          Get your API key by registering an agent on the Connected Agents page.
        </p>
        <pre
          className="px-4 py-3 rounded-lg text-xs"
          style={{
            background: 'var(--neo-bg)',
            border: '1px solid var(--neo-border)',
            color: 'var(--neo-green)',
            fontFamily: "'JetBrains Mono', monospace",
          }}
        >
          {`Authorization: Bearer <agent_id>:<secret>`}
        </pre>
      </div>

      {/* AIQL Query Language Reference */}
      <div className="mb-6">
        <h2 className="text-lg font-bold mb-3" style={{ color: 'var(--neo-text)' }}>
          AIQL Query Language
        </h2>
        <p className="text-xs mb-4" style={{ color: 'var(--neo-text-muted)' }}>
          AIQL is the query language for ContextSynapse graphs. Use it to create nodes, edges, query data, and manage graphs.
          All queries go through the <code style={{ color: 'var(--neo-cyan)', fontFamily: "'JetBrains Mono', monospace" }}>POST /aiql</code> endpoint.
        </p>

        <div className="space-y-3">
          {AIQL_EXAMPLES.map((section) => (
            <div
              key={section.title}
              className="rounded-xl overflow-hidden"
              style={{ border: '1px solid var(--neo-border)' }}
            >
              <button
                onClick={() => setExpandedSection(expandedSection === `aiql-${section.title}` ? null : `aiql-${section.title}`)}
                className="w-full flex items-center justify-between px-5 py-3 transition hover:opacity-90"
                style={{ background: 'var(--neo-surface)' }}
              >
                <div className="flex items-center gap-2">
                  <span style={{ color: 'var(--neo-purple)', fontSize: 14 }}>{'>'}_</span>
                  <span className="text-sm font-semibold" style={{ color: 'var(--neo-text)' }}>
                    {section.title}
                  </span>
                  <span
                    className="px-1.5 py-0.5 rounded text-xs"
                    style={{ background: 'rgba(175,82,222,0.12)', color: 'var(--neo-purple, #af52de)' }}
                  >
                    {section.commands.length}
                  </span>
                </div>
                {expandedSection === `aiql-${section.title}` ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
              </button>

              {expandedSection === `aiql-${section.title}` && (
                <div style={{ background: 'var(--neo-bg)' }}>
                  {section.commands.map((cmd, i) => {
                    const cmdKey = `aiql-${section.title}-${i}`;
                    return (
                      <div
                        key={cmdKey}
                        className="px-5 py-3 flex items-start gap-3"
                        style={{ borderTop: '1px solid var(--neo-border)' }}
                      >
                        <div className="flex-1">
                          <pre
                            className="px-3 py-2 rounded-lg text-xs mb-1 overflow-x-auto"
                            style={{
                              background: 'var(--neo-surface)',
                              border: '1px solid var(--neo-border)',
                              color: 'var(--neo-cyan)',
                              fontFamily: "'JetBrains Mono', monospace",
                            }}
                          >
                            {cmd.query}
                          </pre>
                          <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                            {cmd.desc}
                          </span>
                        </div>
                        <button
                          onClick={() => copyText(cmd.query, cmdKey)}
                          className="p-1 mt-1 shrink-0"
                          style={{ color: 'var(--neo-text-muted)' }}
                        >
                          {copied === cmdKey ? <Check size={12} /> : <Copy size={12} />}
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Workflow: How to build a graph */}
      <div
        className="mb-6 p-5 rounded-xl"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
      >
        <h3 className="text-sm font-semibold mb-3" style={{ color: 'var(--neo-text)' }}>
          Workflow: Building a Project Graph
        </h3>
        <div className="space-y-2 text-xs" style={{ color: 'var(--neo-text-muted)' }}>
          <div className="flex items-start gap-2">
            <span className="font-bold shrink-0" style={{ color: 'var(--neo-cyan)' }}>1.</span>
            <span><strong>Create a graph</strong> &mdash; <code style={{ fontFamily: "'JetBrains Mono', monospace", color: 'var(--neo-cyan)' }}>CREATE GRAPH my_project</code></span>
          </div>
          <div className="flex items-start gap-2">
            <span className="font-bold shrink-0" style={{ color: 'var(--neo-cyan)' }}>2.</span>
            <span><strong>Add team nodes</strong> &mdash; Create Person nodes for each team member with name and role</span>
          </div>
          <div className="flex items-start gap-2">
            <span className="font-bold shrink-0" style={{ color: 'var(--neo-cyan)' }}>3.</span>
            <span><strong>Add work items</strong> &mdash; Create Task, Requirement, and Decision nodes</span>
          </div>
          <div className="flex items-start gap-2">
            <span className="font-bold shrink-0" style={{ color: 'var(--neo-cyan)' }}>4.</span>
            <span><strong>Connect with edges</strong> &mdash; Use CREATE EDGE to link nodes (WORKS_ON, ASSIGNED_TO, DEPENDS_ON)</span>
          </div>
          <div className="flex items-start gap-2">
            <span className="font-bold shrink-0" style={{ color: 'var(--neo-cyan)' }}>5.</span>
            <span><strong>Query and use</strong> &mdash; SELECT queries, MCP tools (briefing, ask), or SDK to build LLM context from the graph</span>
          </div>
        </div>
      </div>

      {/* MCP / SDK Quick Start */}
      <div
        className="mb-6 p-5 rounded-xl"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
      >
        <h3 className="text-sm font-semibold mb-2" style={{ color: 'var(--neo-text)' }}>
          Agent Integration
        </h3>
        <div className="space-y-3">
          <div>
            <span className="text-xs font-medium block mb-1" style={{ color: 'var(--neo-text-muted)' }}>MCP (Claude Code / Copilot):</span>
            <pre
              className="px-3 py-2 rounded-lg text-xs"
              style={{
                background: 'var(--neo-bg)',
                border: '1px solid var(--neo-border)',
                color: 'var(--neo-green)',
                fontFamily: "'JetBrains Mono', monospace",
              }}
            >
              {`claude mcp add contextsynapse -- python -m contextsynapse.mcp.server`}
            </pre>
            <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>35 tools available: query_graph, add_knowledge, search_nodes, briefing, claim_task, etc.</span>
          </div>
          <div>
            <span className="text-xs font-medium block mb-1" style={{ color: 'var(--neo-text-muted)' }}>Python SDK:</span>
            <pre
              className="px-3 py-2 rounded-lg text-xs"
              style={{
                background: 'var(--neo-bg)',
                border: '1px solid var(--neo-border)',
                color: 'var(--neo-green)',
                fontFamily: "'JetBrains Mono', monospace",
              }}
            >
{`from contextsynapse.adapters._base import ContextSynapseConnection

conn = ContextSynapseConnection(namespace="my_project")
alice = conn.add_node("Person", {"name": "Alice"})
result = conn.query("SELECT * FROM Person")`}
            </pre>
          </div>
          <div>
            <span className="text-xs font-medium block mb-1" style={{ color: 'var(--neo-text-muted)' }}>OpenAI Function Calling:</span>
            <pre
              className="px-3 py-2 rounded-lg text-xs"
              style={{
                background: 'var(--neo-bg)',
                border: '1px solid var(--neo-border)',
                color: 'var(--neo-green)',
                fontFamily: "'JetBrains Mono', monospace",
              }}
            >
{`from contextsynapse.adapters.openai import configure, create_openai_tools, dispatch_tool_call

configure(namespace="my_project", agent_name="assistant")
tools = create_openai_tools()  # Pass to openai.chat.completions.create(tools=tools)`}
            </pre>
          </div>
        </div>
      </div>

      <h2 className="text-lg font-bold mb-3 mt-8" style={{ color: 'var(--neo-text)' }}>
        REST API Reference
      </h2>

      {/* Base URL */}
      <div
        className="mb-6 p-4 rounded-xl flex items-center justify-between"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
      >
        <div>
          <span className="text-xs font-medium" style={{ color: 'var(--neo-text-muted)' }}>Base URL: </span>
          <code className="text-xs" style={{ color: 'var(--neo-cyan)', fontFamily: "'JetBrains Mono', monospace" }}>
            {baseUrl}
          </code>
        </div>
        <button
          onClick={() => copyText(baseUrl, 'baseurl')}
          className="p-1"
          style={{ color: 'var(--neo-text-muted)' }}
        >
          {copied === 'baseurl' ? <Check size={12} /> : <Copy size={12} />}
        </button>
      </div>

      {/* Endpoint sections */}
      <div className="space-y-3">
        {API_SECTIONS.map((section) => (
          <div
            key={section.tag}
            className="rounded-xl overflow-hidden"
            style={{ border: '1px solid var(--neo-border)' }}
          >
            <button
              onClick={() => setExpandedSection(expandedSection === section.tag ? null : section.tag)}
              className="w-full flex items-center justify-between px-5 py-3 transition hover:opacity-90"
              style={{ background: 'var(--neo-surface)' }}
            >
              <div className="flex items-center gap-2">
                <BookOpen size={14} style={{ color: 'var(--neo-cyan)' }} />
                <span className="text-sm font-semibold" style={{ color: 'var(--neo-text)' }}>
                  {section.title}
                </span>
                <span
                  className="px-1.5 py-0.5 rounded text-xs"
                  style={{ background: 'rgba(0,210,255,0.1)', color: 'var(--neo-cyan)' }}
                >
                  {section.endpoints.length}
                </span>
              </div>
              {expandedSection === section.tag ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
            </button>

            {expandedSection === section.tag && (
              <div style={{ background: 'var(--neo-bg)' }}>
                {section.endpoints.map((ep, i) => {
                  const epKey = `${section.tag}-${i}`;
                  const mc = METHOD_COLORS[ep.method] || METHOD_COLORS.GET;
                  const isOpen = expandedEndpoint === epKey;

                  const curlExample = ep.method === 'WS'
                    ? `wscat -c "${baseUrl.replace('http', 'ws')}${ep.path}"`
                    : ep.body && ep.body.startsWith('multipart')
                    ? `curl -X ${ep.method} ${baseUrl}${ep.path} \\\n  -H "Authorization: Bearer <api_key>" \\\n  -F "file=@yourfile.pdf"`
                    : ep.body
                    ? `curl -X ${ep.method} ${baseUrl}${ep.path} \\\n  -H "Authorization: Bearer <api_key>" \\\n  -H "Content-Type: application/json" \\\n  -d '${ep.body}'`
                    : `curl ${ep.method !== 'GET' ? `-X ${ep.method} ` : ''}${baseUrl}${ep.path}${ep.params ? `?${ep.params}` : ''} \\\n  -H "Authorization: Bearer <api_key>"`;

                  return (
                    <div
                      key={epKey}
                      style={{ borderTop: '1px solid var(--neo-border)' }}
                    >
                      <button
                        onClick={() => setExpandedEndpoint(isOpen ? null : epKey)}
                        className="w-full flex items-center gap-3 px-5 py-3 text-left transition hover:opacity-90"
                      >
                        <span
                          className="px-2 py-0.5 rounded text-xs font-bold shrink-0"
                          style={{ background: mc.bg, color: mc.color, minWidth: 50, textAlign: 'center' }}
                        >
                          {ep.method}
                        </span>
                        <code
                          className="text-xs flex-1"
                          style={{ color: 'var(--neo-text)', fontFamily: "'JetBrains Mono', monospace" }}
                        >
                          {ep.path}
                        </code>
                        <span className="text-xs shrink-0" style={{ color: 'var(--neo-text-muted)' }}>
                          {ep.desc}
                        </span>
                      </button>

                      {isOpen && (
                        <div className="px-5 pb-4 space-y-3">
                          <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>{ep.desc}</p>

                          {ep.params && (
                            <div>
                              <span className="text-xs font-medium" style={{ color: 'var(--neo-text-muted)' }}>
                                Query Params:
                              </span>
                              <code
                                className="ml-2 text-xs"
                                style={{ color: 'var(--neo-cyan)', fontFamily: "'JetBrains Mono', monospace" }}
                              >
                                {ep.params}
                              </code>
                            </div>
                          )}

                          {ep.body && (
                            <div>
                              <span className="text-xs font-medium block mb-1" style={{ color: 'var(--neo-text-muted)' }}>
                                Request Body:
                              </span>
                              <pre
                                className="px-3 py-2 rounded-lg text-xs"
                                style={{
                                  background: 'var(--neo-surface)',
                                  border: '1px solid var(--neo-border)',
                                  color: 'var(--neo-blue)',
                                  fontFamily: "'JetBrains Mono', monospace",
                                }}
                              >
                                {ep.body}
                              </pre>
                            </div>
                          )}

                          {ep.response && (
                            <div>
                              <span className="text-xs font-medium block mb-1" style={{ color: 'var(--neo-text-muted)' }}>
                                Response:
                              </span>
                              <pre
                                className="px-3 py-2 rounded-lg text-xs"
                                style={{
                                  background: 'var(--neo-surface)',
                                  border: '1px solid var(--neo-border)',
                                  color: 'var(--neo-green)',
                                  fontFamily: "'JetBrains Mono', monospace",
                                }}
                              >
                                {ep.response}
                              </pre>
                            </div>
                          )}

                          {/* cURL example */}
                          <div>
                            <div className="flex items-center justify-between mb-1">
                              <span className="text-xs font-medium" style={{ color: 'var(--neo-text-muted)' }}>
                                cURL:
                              </span>
                              <button
                                onClick={() => copyText(curlExample, epKey)}
                                className="flex items-center gap-1 text-xs"
                                style={{ color: 'var(--neo-text-muted)' }}
                              >
                                {copied === epKey ? <Check size={10} /> : <Copy size={10} />}
                                {copied === epKey ? 'Copied' : 'Copy'}
                              </button>
                            </div>
                            <pre
                              className="px-3 py-2 rounded-lg text-xs overflow-x-auto"
                              style={{
                                background: 'var(--neo-surface)',
                                border: '1px solid var(--neo-border)',
                                color: 'var(--neo-text)',
                                fontFamily: "'JetBrains Mono', monospace",
                              }}
                            >
                              {curlExample}
                            </pre>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

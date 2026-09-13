/**
 * MobileAgentPage — Browser-based agent demo.
 *
 * Simulates an AI agent running in the browser that connects to ContextSynapse's
 * shared brain. Uses the playground tool dispatch endpoint to call orient,
 * search_nodes, add_knowledge, and rate_context.
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Smartphone, Send, RefreshCw, Lightbulb, ThumbsUp, ThumbsDown,
  Loader2, Database, Globe, Layers, X, ChevronDown, Wifi, WifiOff,
} from 'lucide-react';
import api from '../../lib/api';
import toast from 'react-hot-toast';

// ── helpers ──────────────────────────────────────────────────────────────────

function cls(...args) { return args.filter(Boolean).join(' '); }

function dispatchTool(toolName, params, graph) {
  const payload = { tool_name: toolName, params };
  if (graph) payload.graph = graph;
  return api.post('/dashboard/playground/tool', payload);
}

// ── message types ────────────────────────────────────────────────────────────

const ROLE = { AGENT: 'agent', USER: 'user', SYSTEM: 'system' };

function makeMsg(role, text, extra) {
  return { id: Date.now() + Math.random(), role, text, ts: new Date(), ...extra };
}

// ── component ────────────────────────────────────────────────────────────────

export default function MobileAgentPage() {
  // ── state ──
  const [graphs, setGraphs] = useState([]);
  const [selectedGraph, setSelectedGraph] = useState('');
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [stats, setStats] = useState(null);        // { nodes, edges }
  const [orientData, setOrientData] = useState(null);
  const [showSaveFinding, setShowSaveFinding] = useState(false);
  const [findingText, setFindingText] = useState('');
  const [savingFinding, setSavingFinding] = useState(false);

  const bottomRef = useRef(null);
  const inputRef = useRef(null);

  // auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // ── fetch graphs ──
  useEffect(() => {
    (async () => {
      try {
        const res = await api.get('/dashboard/graphs');
        const list = res.data?.graphs || res.data || [];
        setGraphs(list);
        if (list.length > 0 && !selectedGraph) {
          setSelectedGraph(typeof list[0] === 'string' ? list[0] : list[0].name || list[0].namespace || 'default');
        }
      } catch { /* ignore */ }
    })();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── connect / orient ──
  const orient = useCallback(async () => {
    if (!selectedGraph) return;
    setLoading(true);
    try {
      // Get graph stats directly (reliable — counts nodes from graph, not manifest)
      let nodeCount = '?', edgeCount = '?', nodeTypes = {};
      try {
        const statsRes = await api.get(`/dashboard/graphs/${encodeURIComponent(selectedGraph)}/stats`);
        nodeCount = statsRes.data?.node_count ?? '?';
        edgeCount = statsRes.data?.edge_count ?? '?';
        nodeTypes = statsRes.data?.node_types || {};
      } catch {
        // Fallback to graph list metadata
        const g = graphs.find(g => (g.name || g) === selectedGraph);
        if (g) {
          nodeCount = g.num_nodes || g.node_count || '?';
          edgeCount = g.num_edges || g.edge_count || '?';
        }
      }
      setStats({ nodes: nodeCount, edges: edgeCount });

      // Count entities and topics from node types
      const topicCount = nodeTypes['Topic'] || 0;
      const entityCount = (nodeTypes['Person'] || 0) + (nodeTypes['Organization'] || 0) +
        (nodeTypes['Technology'] || 0) + (nodeTypes['Tool'] || 0) + (nodeTypes['Concept'] || 0);

      setOrientData({ nodeTypes });
      setConnected(true);

      setMessages(prev => [
        ...prev,
        makeMsg(ROLE.AGENT,
          `Oriented to context on "${selectedGraph}".\n${nodeCount} nodes, ${edgeCount} edges.\nSaw: ${topicCount} topics, ${entityCount} entities.`,
          { type: 'orient' }),
      ]);
    } catch (err) {
      const detail = err.response?.data?.detail || err.message;
      setMessages(prev => [...prev, makeMsg(ROLE.SYSTEM, `Orient failed: ${detail}`)]);
      setConnected(false);
    } finally {
      setLoading(false);
    }
  }, [selectedGraph]);

  // ── search ──
  const handleSend = useCallback(async () => {
    const q = input.trim();
    if (!q || loading) return;
    setInput('');
    setMessages(prev => [...prev, makeMsg(ROLE.USER, q)]);
    setLoading(true);

    try {
      const res = await dispatchTool('search_nodes', { query: q, limit: 6 }, selectedGraph);
      const result = res.data?.result || res.data || {};
      const nodes = result.nodes || result.results || result || [];
      const list = Array.isArray(nodes) ? nodes : [];

      if (list.length === 0) {
        setMessages(prev => [...prev, makeMsg(ROLE.AGENT, 'No relevant nodes found in the graph.', { type: 'empty' })]);
      } else {
        setMessages(prev => [...prev, makeMsg(ROLE.AGENT, null, { type: 'results', nodes: list, query: q })]);
      }
    } catch (err) {
      const detail = err.response?.data?.detail || err.message;
      setMessages(prev => [...prev, makeMsg(ROLE.SYSTEM, `Search failed: ${detail}`)]);
    } finally {
      setLoading(false);
    }
  }, [input, loading, selectedGraph]);

  // ── rate ──
  const rateResult = useCallback(async (msgId, useful) => {
    setMessages(prev => prev.map(m => m.id === msgId ? { ...m, rated: useful ? 'useful' : 'misleading' } : m));
    try {
      await dispatchTool('rate_context', {
        rating: useful ? 'useful' : 'misleading',
        comment: `Browser agent rated search result`,
      }, selectedGraph);
      toast.success(useful ? 'Marked as useful' : 'Marked as misleading');
    } catch {
      toast.error('Failed to submit rating');
    }
  }, [selectedGraph]);

  // ── save finding ──
  const saveFinding = useCallback(async () => {
    const text = findingText.trim();
    if (!text) return;
    setSavingFinding(true);
    try {
      await dispatchTool('add_knowledge', {
        content: text,
        node_type: 'Finding',
        metadata: JSON.stringify({ source: 'browser-agent', ts: new Date().toISOString() }),
      }, selectedGraph);
      setMessages(prev => [...prev, makeMsg(ROLE.AGENT, `Saved finding: "${text}"`, { type: 'finding' })]);
      setFindingText('');
      setShowSaveFinding(false);
      toast.success('Finding saved to graph');
    } catch (err) {
      toast.error('Failed to save finding');
    } finally {
      setSavingFinding(false);
    }
  }, [findingText, selectedGraph]);

  // ── key handler ──
  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // ── render helpers ──
  const graphLabel = (g) => typeof g === 'string' ? g : g.name || g.namespace || 'unknown';

  return (
    <div style={{ maxWidth: 520, margin: '0 auto' }}>
      {/* ── header ── */}
      <div style={s.header}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Smartphone size={20} style={{ color: 'var(--neo-accent)' }} />
          <span style={s.title}>Browser Agent</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {connected
            ? <Wifi size={14} style={{ color: '#10b981' }} />
            : <WifiOff size={14} style={{ color: 'var(--neo-muted)' }} />}
          <span style={{ fontSize: 12, color: connected ? '#10b981' : 'var(--neo-muted)' }}>
            {connected ? 'Connected' : 'Disconnected'}
          </span>
        </div>
      </div>

      {/* ── graph selector ── */}
      <div style={s.selectorBar}>
        <label style={s.label}>Graph</label>
        <div style={{ position: 'relative', flex: 1 }}>
          <select
            value={selectedGraph}
            onChange={e => { setSelectedGraph(e.target.value); setConnected(false); }}
            style={s.select}
          >
            {graphs.map(g => (
              <option key={graphLabel(g)} value={graphLabel(g)}>{graphLabel(g)}</option>
            ))}
          </select>
          <ChevronDown size={14} style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none', color: 'var(--neo-muted)' }} />
        </div>
        <button onClick={orient} disabled={loading || !selectedGraph} style={s.orientBtn}>
          {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          Orient
        </button>
      </div>

      {/* ── stats bar ── */}
      {stats && (
        <div style={s.statsBar}>
          <div style={s.statItem}>
            <Database size={12} />
            <span>{stats.nodes} nodes, {stats.edges} edges</span>
          </div>
          {orientData?.trust_score != null && (
            <div style={s.statItem}>
              <Layers size={12} />
              <TrustBar value={orientData.trust_score} />
            </div>
          )}
        </div>
      )}

      {/* ── chat area ── */}
      <div style={s.chatArea}>
        {messages.length === 0 && (
          <div style={s.emptyState}>
            <Globe size={32} style={{ color: 'var(--neo-muted)', marginBottom: 8 }} />
            <p style={{ color: 'var(--neo-muted)', fontSize: 13 }}>
              Select a graph and press Orient to connect the agent to the shared brain.
            </p>
          </div>
        )}
        {messages.map(msg => (
          <ChatBubble key={msg.id} msg={msg} onRate={rateResult} />
        ))}
        <div ref={bottomRef} />
      </div>

      {/* ── save finding overlay ── */}
      {showSaveFinding && (
        <div style={s.findingOverlay}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--neo-text)' }}>Save Finding</span>
            <button onClick={() => setShowSaveFinding(false)} style={s.iconBtn}><X size={14} /></button>
          </div>
          <textarea
            value={findingText}
            onChange={e => setFindingText(e.target.value)}
            placeholder="Describe the finding..."
            rows={3}
            style={s.textarea}
          />
          <button onClick={saveFinding} disabled={savingFinding || !findingText.trim()} style={s.saveBtn}>
            {savingFinding ? <Loader2 size={14} className="animate-spin" /> : <Lightbulb size={14} />}
            Save to Graph
          </button>
        </div>
      )}

      {/* ── input bar ── */}
      <div style={s.inputBar}>
        <input
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Type a question..."
          disabled={!connected || loading}
          style={s.input}
        />
        <button onClick={handleSend} disabled={!connected || loading || !input.trim()} style={s.sendBtn}>
          {loading ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
        </button>
      </div>

      {/* ── action bar ── */}
      <div style={s.actionBar}>
        <button
          onClick={() => setShowSaveFinding(v => !v)}
          disabled={!connected}
          style={s.actionBtn}
        >
          <Lightbulb size={14} /> Save Finding
        </button>
        <button onClick={orient} disabled={loading || !selectedGraph} style={s.actionBtn}>
          <RefreshCw size={14} /> Re-Orient
        </button>
      </div>
    </div>
  );
}

// ── TrustBar ─────────────────────────────────────────────────────────────────

function TrustBar({ value }) {
  const pct = Math.round((value || 0) * 100);
  const filled = Math.round((value || 0) * 10);
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12 }}>
      <span style={{ display: 'inline-flex', gap: 1 }}>
        {Array.from({ length: 10 }).map((_, i) => (
          <span key={i} style={{
            width: 6, height: 10, borderRadius: 1,
            background: i < filled ? '#10b981' : 'var(--neo-border)',
          }} />
        ))}
      </span>
      <span style={{ color: 'var(--neo-muted)' }}>{pct}%</span>
    </span>
  );
}

// ── ChatBubble ───────────────────────────────────────────────────────────────

function ChatBubble({ msg, onRate }) {
  const isUser = msg.role === ROLE.USER;
  const isSystem = msg.role === ROLE.SYSTEM;

  if (isSystem) {
    return (
      <div style={{ ...s.bubble, ...s.systemBubble }}>
        <span style={{ fontSize: 12, color: '#ef4444' }}>{msg.text}</span>
      </div>
    );
  }

  if (isUser) {
    return (
      <div style={{ ...s.bubble, ...s.userBubble }}>
        <div style={s.bubbleLabel}>You</div>
        <div style={s.bubbleText}>{msg.text}</div>
      </div>
    );
  }

  // agent
  if (msg.type === 'results' && msg.nodes) {
    return (
      <div style={{ ...s.bubble, ...s.agentBubble }}>
        <div style={s.bubbleLabel}>Agent</div>
        <div style={{ fontSize: 13, color: 'var(--neo-text)', marginBottom: 6 }}>
          Found {msg.nodes.length} relevant node{msg.nodes.length !== 1 ? 's' : ''}:
        </div>
        <ul style={{ margin: 0, paddingLeft: 16, listStyle: 'none' }}>
          {msg.nodes.map((n, i) => (
            <li key={i} style={s.nodeItem}>
              <span style={s.nodeType}>{n.label || n.type || n.node_type || 'Node'}</span>
              <span style={s.nodeContent}>{n.content || n.name || n.title || n.id || JSON.stringify(n).slice(0, 80)}</span>
            </li>
          ))}
        </ul>
        {!msg.rated && (
          <div style={s.rateRow}>
            <button onClick={() => onRate(msg.id, true)} style={s.rateBtn}>
              <ThumbsUp size={13} /> Useful
            </button>
            <button onClick={() => onRate(msg.id, false)} style={{ ...s.rateBtn, ...s.rateBtnNeg }}>
              <ThumbsDown size={13} /> Misleading
            </button>
          </div>
        )}
        {msg.rated && (
          <div style={{ fontSize: 11, color: 'var(--neo-muted)', marginTop: 6 }}>
            Rated: {msg.rated}
          </div>
        )}
      </div>
    );
  }

  return (
    <div style={{ ...s.bubble, ...s.agentBubble }}>
      <div style={s.bubbleLabel}>Agent</div>
      <div style={s.bubbleText}>{msg.text}</div>
    </div>
  );
}

// ── styles ───────────────────────────────────────────────────────────────────

const s = {
  header: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '12px 16px',
    background: 'var(--neo-card-bg)',
    border: '1px solid var(--neo-border)',
    borderRadius: '12px 12px 0 0',
  },
  title: {
    fontSize: 15, fontWeight: 700, color: 'var(--neo-text)',
  },
  selectorBar: {
    display: 'flex', alignItems: 'center', gap: 8,
    padding: '8px 16px',
    background: 'var(--neo-card-bg)',
    borderLeft: '1px solid var(--neo-border)',
    borderRight: '1px solid var(--neo-border)',
  },
  label: {
    fontSize: 12, fontWeight: 600, color: 'var(--neo-muted)', whiteSpace: 'nowrap',
  },
  select: {
    width: '100%', padding: '6px 28px 6px 8px', fontSize: 13,
    background: 'var(--neo-bg)', color: 'var(--neo-text)',
    border: '1px solid var(--neo-border)', borderRadius: 6,
    appearance: 'none', cursor: 'pointer', outline: 'none',
  },
  orientBtn: {
    display: 'flex', alignItems: 'center', gap: 4,
    padding: '6px 12px', fontSize: 12, fontWeight: 600,
    background: 'var(--neo-accent)', color: '#fff',
    border: 'none', borderRadius: 6, cursor: 'pointer',
    whiteSpace: 'nowrap',
  },
  statsBar: {
    display: 'flex', alignItems: 'center', gap: 16,
    padding: '6px 16px', fontSize: 12, color: 'var(--neo-muted)',
    background: 'var(--neo-card-bg)',
    borderLeft: '1px solid var(--neo-border)',
    borderRight: '1px solid var(--neo-border)',
    borderBottom: '1px solid var(--neo-border)',
  },
  statItem: {
    display: 'flex', alignItems: 'center', gap: 4,
  },
  chatArea: {
    minHeight: 320, maxHeight: 'calc(100vh - 380px)',
    overflowY: 'auto', padding: 16,
    background: 'var(--neo-bg)',
    borderLeft: '1px solid var(--neo-border)',
    borderRight: '1px solid var(--neo-border)',
  },
  emptyState: {
    display: 'flex', flexDirection: 'column', alignItems: 'center',
    justifyContent: 'center', height: 200, textAlign: 'center',
  },
  bubble: {
    marginBottom: 12, padding: '10px 14px', borderRadius: 10,
    maxWidth: '92%', fontSize: 13, lineHeight: 1.5,
  },
  userBubble: {
    marginLeft: 'auto',
    background: 'var(--neo-accent)',
    color: '#fff',
    borderBottomRightRadius: 2,
  },
  agentBubble: {
    marginRight: 'auto',
    background: 'var(--neo-card-bg)',
    border: '1px solid var(--neo-border)',
    color: 'var(--neo-text)',
    borderBottomLeftRadius: 2,
  },
  systemBubble: {
    margin: '0 auto', textAlign: 'center',
    background: 'transparent', border: 'none',
  },
  bubbleLabel: {
    fontSize: 11, fontWeight: 700, color: 'var(--neo-muted)', marginBottom: 2,
    textTransform: 'uppercase', letterSpacing: '0.5px',
  },
  bubbleText: {
    whiteSpace: 'pre-wrap', wordBreak: 'break-word',
  },
  nodeItem: {
    marginBottom: 6, display: 'flex', alignItems: 'baseline', gap: 6,
  },
  nodeType: {
    fontSize: 10, fontWeight: 700, textTransform: 'uppercase',
    padding: '1px 5px', borderRadius: 3,
    background: 'var(--neo-accent)', color: '#fff',
    whiteSpace: 'nowrap', flexShrink: 0,
  },
  nodeContent: {
    fontSize: 13, color: 'var(--neo-text)',
  },
  rateRow: {
    display: 'flex', gap: 8, marginTop: 8,
  },
  rateBtn: {
    display: 'flex', alignItems: 'center', gap: 4,
    padding: '4px 10px', fontSize: 12, fontWeight: 500,
    background: 'var(--neo-bg)', color: '#10b981',
    border: '1px solid var(--neo-border)', borderRadius: 6,
    cursor: 'pointer',
  },
  rateBtnNeg: {
    color: '#ef4444',
  },
  findingOverlay: {
    padding: '12px 16px',
    background: 'var(--neo-card-bg)',
    borderLeft: '1px solid var(--neo-border)',
    borderRight: '1px solid var(--neo-border)',
    borderTop: '1px solid var(--neo-border)',
  },
  textarea: {
    width: '100%', padding: 8, fontSize: 13,
    background: 'var(--neo-bg)', color: 'var(--neo-text)',
    border: '1px solid var(--neo-border)', borderRadius: 6,
    resize: 'vertical', outline: 'none', fontFamily: 'inherit',
    boxSizing: 'border-box',
  },
  saveBtn: {
    display: 'flex', alignItems: 'center', gap: 4,
    padding: '6px 12px', fontSize: 12, fontWeight: 600,
    background: '#f59e0b', color: '#fff',
    border: 'none', borderRadius: 6, cursor: 'pointer',
    marginTop: 8,
  },
  inputBar: {
    display: 'flex', gap: 8, padding: '10px 16px',
    background: 'var(--neo-card-bg)',
    borderLeft: '1px solid var(--neo-border)',
    borderRight: '1px solid var(--neo-border)',
    borderTop: '1px solid var(--neo-border)',
  },
  input: {
    flex: 1, padding: '8px 12px', fontSize: 14,
    background: 'var(--neo-bg)', color: 'var(--neo-text)',
    border: '1px solid var(--neo-border)', borderRadius: 8,
    outline: 'none', fontFamily: 'inherit',
  },
  sendBtn: {
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    width: 38, height: 38,
    background: 'var(--neo-accent)', color: '#fff',
    border: 'none', borderRadius: 8, cursor: 'pointer',
    flexShrink: 0,
  },
  actionBar: {
    display: 'flex', gap: 8, padding: '8px 16px',
    background: 'var(--neo-card-bg)',
    border: '1px solid var(--neo-border)',
    borderRadius: '0 0 12px 12px',
  },
  actionBtn: {
    display: 'flex', alignItems: 'center', gap: 4,
    padding: '6px 12px', fontSize: 12, fontWeight: 500,
    background: 'var(--neo-bg)', color: 'var(--neo-text)',
    border: '1px solid var(--neo-border)', borderRadius: 6,
    cursor: 'pointer',
  },
  iconBtn: {
    background: 'none', border: 'none', color: 'var(--neo-muted)',
    cursor: 'pointer', padding: 2,
  },
};

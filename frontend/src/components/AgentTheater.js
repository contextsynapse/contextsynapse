import React, { useState, useEffect, useRef, useMemo } from 'react';
import { Send, Zap, Square, Eye, EyeOff } from 'lucide-react';

const TOOL_TO_STATE = {
  search_nodes: 'searching', query_graph: 'searching', ask: 'searching',
  rag_query: 'searching', rag_graph: 'searching', search: 'searching',
  add_knowledge: 'knowledge', add_relationship: 'knowledge',
  add_task: 'planning', add_decision: 'deciding',
  claim_task: 'claiming', complete_task: 'done',
  ws_write_file: 'writing', ws_commit: 'writing', ws_push: 'writing',
  ws_read_file: 'reading', ws_run_command: 'tool_call', ws_list_files: 'reading',
  briefing: 'thinking', get_agent_context: 'thinking',
  web_fetch: 'fetching', report_external: 'tool_call', report_tokens: 'tool_call',
  list_tasks: 'planning', my_tasks: 'planning', generate_tasks: 'planning',
};

const STATE_POSITIONS = {
  idle: { x: 15, y: 50 }, waiting: { x: 15, y: 50 },
  thinking: { x: 30, y: 25 }, planning: { x: 50, y: 15 },
  deciding: { x: 50, y: 15 }, searching: { x: 78, y: 22 },
  reading: { x: 72, y: 50 }, fetching: { x: 85, y: 18 },
  knowledge: { x: 50, y: 50 }, writing: { x: 78, y: 72 },
  tool_call: { x: 60, y: 38 }, claiming: { x: 38, y: 62 },
  done: { x: 50, y: 85 }, error: { x: 15, y: 82 }, injected: { x: 28, y: 38 },
};

const ZONES = [
  { x: 14, y: 48, label: '🏠', sublabel: 'Home', color: '#64748b', r: 28 },
  { x: 50, y: 14, label: '🧠', sublabel: 'Plan', color: '#8b5cf6', r: 28 },
  { x: 50, y: 50, label: '📊', sublabel: 'Graph', color: '#00d2ff', r: 32 },
  { x: 80, y: 22, label: '🔍', sublabel: 'Search', color: '#3b82f6', r: 26 },
  { x: 80, y: 72, label: '📝', sublabel: 'Build', color: '#22c55e', r: 28 },
  { x: 50, y: 85, label: '✅', sublabel: 'Done', color: '#4cd964', r: 24 },
];

const COLORS = ['#00d2ff', '#af52de', '#ff9500', '#4cd964', '#ff3b30', '#5856d6', '#ff2d55', '#34aadc'];

const STATE_EMOJI = {
  idle: '😊', waiting: '😐', thinking: '🤔', planning: '📋', searching: '🔍',
  reading: '📖', knowledge: '📚', writing: '✍️', deciding: '🎯', tool_call: '🔧',
  claiming: '🏃', done: '🎉', error: '😵', injected: '⚡', fetching: '🌐',
};

const STYLES = `
  @keyframes a-idle { 0%,100%{transform:translate(-50%,-50%) scale(1)} 50%{transform:translate(-50%,-50%) scale(1.05)} }
  @keyframes a-move { 0%,100%{transform:translate(-50%,-50%) translateY(0)} 25%{transform:translate(-50%,-50%) translateY(-4px)} 75%{transform:translate(-50%,-50%) translateY(-4px)} }
  @keyframes a-jump { 0%{transform:translate(-50%,-50%) translateY(0) scale(1)} 40%{transform:translate(-50%,-50%) translateY(-16px) scale(1.15)} 100%{transform:translate(-50%,-50%) translateY(0) scale(1)} }
  @keyframes a-shake { 0%,100%{transform:translate(-50%,-50%) rotate(0)} 25%{transform:translate(-50%,-50%) rotate(-6deg)} 75%{transform:translate(-50%,-50%) rotate(6deg)} }
  @keyframes pulse-r { 0%{transform:translate(-50%,-50%) scale(0.8);opacity:0.6} 100%{transform:translate(-50%,-50%) scale(2.5);opacity:0} }
  @keyframes sp { 0%{opacity:1;transform:scale(0) translateY(0)} 50%{opacity:1;transform:scale(1.3) translateY(-12px)} 100%{opacity:0;transform:scale(0.4) translateY(-24px)} }
  @keyframes pop { 0%{opacity:0;transform:translateX(-50%) scale(0.6)} 100%{opacity:1;transform:translateX(-50%) scale(1)} }
  @keyframes node-pop { 0%{transform:scale(0);opacity:0} 60%{transform:scale(1.2);opacity:1} 100%{transform:scale(1)} }
  @keyframes edge-draw { from{stroke-dashoffset:100} to{stroke-dashoffset:0} }
  @keyframes fadeIn { from{opacity:0;transform:translateY(6px)} to{opacity:1;transform:translateY(0)} }
  @keyframes trail-fade { 0%{opacity:0.5} 100%{opacity:0} }
`;

function Agent({ name, state, tool, action, lastResult, pos, prevPos, color, index }) {
  const [hovered, setHovered] = useState(false);
  const isActive = !['idle', 'done', 'waiting'].includes(state);
  const isMoving = isActive && state !== 'error';

  const anim = isMoving ? 'a-move 0.5s ease-in-out infinite'
    : ['thinking', 'planning'].includes(state) ? 'a-idle 2s ease-in-out infinite'
    : state === 'done' ? 'a-jump 0.6s ease-out'
    : state === 'error' ? 'a-shake 0.4s ease-in-out' : 'none';

  return (
    <>
      {/* Trail line from previous position */}
      {prevPos && (prevPos.x !== pos.x || prevPos.y !== pos.y) && (
        <svg style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none', zIndex: 2 }}>
          <line x1={`${prevPos.x}%`} y1={`${prevPos.y}%`} x2={`${pos.x}%`} y2={`${pos.y}%`}
            stroke={color} strokeWidth="1.5" strokeDasharray="4 4" opacity="0.25"
            style={{ animation: 'trail-fade 3s forwards' }} />
        </svg>
      )}

      <div style={{
        position: 'absolute', left: `${pos.x}%`, top: `${pos.y}%`,
        transform: 'translate(-50%, -50%)',
        transition: 'left 1.2s cubic-bezier(0.25, 0.46, 0.45, 0.94), top 1.2s cubic-bezier(0.25, 0.46, 0.45, 0.94)',
        zIndex: hovered ? 50 : 10 + index,
      }}>
        {/* Pulse ring */}
        {isActive && <div style={{
          position: 'absolute', left: '50%', top: '50%', width: 40, height: 40, borderRadius: '50%',
          border: `1.5px solid ${color}`, animation: 'pulse-r 2s ease-out infinite', pointerEvents: 'none',
        }} />}

        {/* Sparkles on knowledge/write */}
        {['knowledge', 'writing', 'done'].includes(state) && [0, 1, 2].map(i => (
          <div key={i} style={{
            position: 'absolute', top: -10 - i * 4, left: `${30 + i * 15}%`,
            fontSize: 8, animation: `sp 0.8s ease-out ${i * 0.15}s forwards`, pointerEvents: 'none',
          }}>✨</div>
        ))}

        {/* Hover tooltip */}
        {hovered && (
          <div style={{
            position: 'absolute', bottom: 44, left: '50%', transform: 'translateX(-50%)',
            background: 'rgba(8,12,28,0.95)', border: `1px solid ${color}40`,
            borderRadius: 10, padding: '8px 14px', fontSize: 10, color: '#e2e8f0',
            whiteSpace: 'nowrap', maxWidth: 220, zIndex: 100,
            boxShadow: `0 4px 24px rgba(0,0,0,0.6), 0 0 12px ${color}20`,
            animation: 'pop 0.2s ease-out',
          }}>
            <div style={{ fontWeight: 700, color, marginBottom: 3, fontSize: 12 }}>{name}</div>
            <div>{STATE_EMOJI[state] || '🤖'} {tool ? `Using ${tool}` : action || state}</div>
            {lastResult && <div style={{ marginTop: 4, fontSize: 9, color: '#94a3b8', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis' }}>→ {lastResult}</div>}
            <div style={{ position: 'absolute', bottom: -5, left: '50%', transform: 'translateX(-50%)', width: 0, height: 0, borderLeft: '5px solid transparent', borderRight: '5px solid transparent', borderTop: '5px solid rgba(8,12,28,0.95)' }} />
          </div>
        )}

        {/* Agent body */}
        <div onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}
          style={{
            width: 36, height: 36, borderRadius: 10,
            background: `radial-gradient(circle at 35% 35%, ${color}60, ${color}15)`,
            border: `2px solid ${color}`, display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 18, cursor: 'pointer', animation: anim,
            boxShadow: hovered ? `0 0 24px ${color}80, 0 0 48px ${color}20` : isActive ? `0 0 12px ${color}40` : 'none',
            transition: 'box-shadow 0.3s',
          }}>
          {STATE_EMOJI[state] || '🤖'}
        </div>

        {/* Name */}
        <div style={{
          textAlign: 'center', marginTop: 4, fontSize: 9, fontWeight: 700, letterSpacing: 0.3,
          color: isActive ? color : '#64748b',
          textShadow: isActive ? `0 0 10px ${color}60` : 'none',
        }}>{name.length > 14 ? name.slice(0, 13) + '…' : name}</div>
        {isActive && tool && <div style={{ textAlign: 'center', fontSize: 7, color: '#94a3b8', marginTop: 1 }}>{tool}</div>}
      </div>
    </>
  );
}

// Mini graph that grows as agents create nodes
function LiveGraph({ graphNodes }) {
  if (!graphNodes.length) return null;
  const recent = graphNodes.slice(-12);

  return (
    <div style={{ position: 'absolute', left: '50%', top: '50%', transform: 'translate(-50%, -50%)', pointerEvents: 'none', zIndex: 1 }}>
      <svg width="80" height="80" viewBox="-40 -40 80 80" style={{ overflow: 'visible' }}>
        {/* Edges */}
        {recent.slice(1).map((n, i) => {
          const prev = recent[i];
          const angle1 = (i * 137.5) * Math.PI / 180;
          const angle2 = ((i + 1) * 137.5) * Math.PI / 180;
          const r = 18 + (i % 3) * 8;
          return (
            <line key={`e${i}`}
              x1={Math.cos(angle1) * r} y1={Math.sin(angle1) * r}
              x2={Math.cos(angle2) * (r + 3)} y2={Math.sin(angle2) * (r + 3)}
              stroke="rgba(0,210,255,0.15)" strokeWidth="1"
              strokeDasharray="100" style={{ animation: 'edge-draw 0.5s forwards' }} />
          );
        })}
        {/* Nodes */}
        {recent.map((n, i) => {
          const angle = (i * 137.5) * Math.PI / 180; // golden angle
          const r = 18 + (i % 3) * 8;
          const x = Math.cos(angle) * r;
          const y = Math.sin(angle) * r;
          const nodeColor = n.type === 'Task' ? '#3b82f6' : n.type === 'Decision' ? '#8b5cf6' : n.type === 'Knowledge' ? '#22c55e' : '#00d2ff';
          return (
            <g key={i} style={{ animation: `node-pop 0.4s ease-out ${i * 0.05}s both` }}>
              <circle cx={x} cy={y} r={3.5} fill={nodeColor} opacity={0.7} />
              <text x={x} y={y + 8} textAnchor="middle" fontSize="4" fill="#94a3b8" opacity={0.5}>
                {(n.name || '').slice(0, 6)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function ActivityLine({ event, isNew }) {
  const icons = { turn: '🔄', tool_call: '🔧', tool_result: '📋', response: '💬', phase: '🚀', injection_applied: '💉', error: '❌', roster: '👥', done: '✅', workspace: '📁' };
  let text = '';
  switch (event.type) {
    case 'turn': text = `Turn ${event.turn}/${event.max_turns}`; break;
    case 'tool_call': text = `${event.tool}(${(event.args || '').slice(0, 50)})`; break;
    case 'tool_result': text = `${event.tool} → ${(event.result || '').slice(0, 70)}`; break;
    case 'response': text = (event.content || '').slice(0, 80); break;
    case 'phase': text = `Phase: ${event.phase}`; break;
    case 'injection_applied': text = `INJECTED: ${event.message || ''}`; break;
    case 'error': text = event.message || 'Error'; break;
    case 'done': text = 'Complete'; break;
    case 'roster': text = `Team: ${event.lead} + ${(event.workers || []).join(', ')}`; break;
    default: text = JSON.stringify(event).slice(0, 60);
  }
  return (
    <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start', padding: '2px 0', fontSize: 10, animation: isNew ? 'fadeIn 0.3s ease-out' : 'none' }}>
      <span style={{ flexShrink: 0 }}>{icons[event.type] || '●'}</span>
      <span style={{ color: event.type === 'error' ? '#ef4444' : event.type === 'injection_applied' ? '#ffd700' : '#94a3b8', flex: 1, wordBreak: 'break-word' }}>
        {event.agent && <strong style={{ color: '#00d2ff', marginRight: 3 }}>{event.agent}</strong>}
        {text}
      </span>
    </div>
  );
}

export default function AgentTheater({ events = [], isRunning = false, sessionId, agents = [], onStop }) {
  const [agentStates, setAgentStates] = useState({});
  const [prevPositions, setPrevPositions] = useState({});
  const [graphNodes, setGraphNodes] = useState([]);
  const [showFeed, setShowFeed] = useState(true);
  const [injectMsg, setInjectMsg] = useState('');
  const [injectTarget, setInjectTarget] = useState('all');
  const [injectType, setInjectType] = useState('directive');
  const [injecting, setInjecting] = useState(false);
  const feedRef = useRef(null);
  const prevLen = useRef(0);

  useEffect(() => {
    if (!events.length) return;
    const latest = events[events.length - 1];
    if (!latest || !latest.agent) return;

    setAgentStates(prev => {
      const next = { ...prev };
      const cur = next[latest.agent] || {};

      // Save previous position for trail
      const curState = cur.state || 'idle';
      const curPos = STATE_POSITIONS[curState] || STATE_POSITIONS.idle;
      setPrevPositions(p => ({ ...p, [latest.agent]: curPos }));

      switch (latest.type) {
        case 'turn': next[latest.agent] = { ...cur, state: 'thinking', tool: '', action: `Turn ${latest.turn}` }; break;
        case 'tool_call':
          next[latest.agent] = { ...cur, state: TOOL_TO_STATE[latest.tool] || 'tool_call', tool: latest.tool, action: latest.tool };
          // Track graph-building tool calls for live graph
          if (['add_task', 'add_decision', 'add_knowledge', 'add_relationship'].includes(latest.tool)) {
            const name = (latest.args || '').match(/"(?:title|name)":\s*"([^"]+)"/)?.[1] || latest.tool;
            const type = latest.tool === 'add_task' ? 'Task' : latest.tool === 'add_decision' ? 'Decision' : 'Knowledge';
            setGraphNodes(g => [...g.slice(-20), { name, type, agent: latest.agent }]);
          }
          break;
        case 'tool_result': next[latest.agent] = { ...cur, state: 'thinking', tool: '', action: `Got ${latest.tool}`, lastResult: (latest.result || '').slice(0, 100) }; break;
        case 'response': next[latest.agent] = { ...cur, state: 'done', tool: '', action: 'Done' }; break;
        case 'injection_applied':
          next[latest.agent] = { ...cur, state: 'injected', tool: '', action: '⚡ Directive' };
          setTimeout(() => setAgentStates(p => ({ ...p, [latest.agent]: { ...p[latest.agent], state: 'thinking', action: 'Processing' } })), 1200);
          break;
        case 'error': next[latest.agent] = { ...cur, state: 'error', tool: '', action: 'Error' }; break;
        case 'done': Object.keys(next).forEach(k => { next[k] = { ...next[k], state: 'done', tool: '', action: '🎉' }; }); break;
        default: break;
      }
      return next;
    });
  }, [events]);

  useEffect(() => {
    if (feedRef.current && events.length > prevLen.current) feedRef.current.scrollTop = feedRef.current.scrollHeight;
    prevLen.current = events.length;
  }, [events]);

  const handleInject = async () => {
    if (!injectMsg.trim() || !sessionId) return;
    setInjecting(true);
    try {
      const token = localStorage.getItem('contextsynapse_token');
      const base = window.__AICONTEXTDB_API || '';
      await fetch(`${base}/dashboard/sessions/${sessionId}/inject`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({ message: injectMsg, target_agent: injectTarget, type: injectType, priority: 'high' }),
      });
      setInjectMsg('');
    } catch (e) { console.error(e); }
    setInjecting(false);
  };

  const agentNames = agents.length ? agents : [...new Set(events.filter(e => e.agent && e.agent !== 'system').map(e => e.agent))];
  const turnCount = events.filter(e => e.type === 'turn').length;
  const maxTurns = events.find(e => e.type === 'turn')?.max_turns || '?';

  return (
    <>
      <style>{STYLES}</style>
      <div style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', borderRadius: 14, overflow: 'hidden' }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 14px', borderBottom: '1px solid var(--neo-border)', background: 'var(--neo-surface)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 15 }}>🎮</span>
            <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--neo-text)' }}>Agent Theater</span>
            {isRunning && <span style={{ fontSize: 8, padding: '1px 7px', borderRadius: 10, background: 'rgba(76,217,100,0.15)', color: '#4cd964', fontWeight: 600 }}>● LIVE</span>}
            {graphNodes.length > 0 && <span style={{ fontSize: 8, color: 'var(--neo-text-muted)' }}>📊 {graphNodes.length} nodes</span>}
          </div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <span style={{ fontSize: 9, color: 'var(--neo-text-muted)' }}>Turn {turnCount}/{maxTurns}</span>
            <button onClick={() => setShowFeed(!showFeed)} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 2, color: 'var(--neo-text-muted)' }}>
              {showFeed ? <EyeOff size={12} /> : <Eye size={12} />}
            </button>
            {isRunning && onStop && (
              <button onClick={onStop} style={{ background: 'rgba(239,68,68,0.15)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 4, padding: '2px 8px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 3, fontSize: 9, color: '#ef4444' }}>
                <Square size={8} /> Stop
              </button>
            )}
          </div>
        </div>

        {/* Board */}
        <div style={{
          position: 'relative', height: 320, overflow: 'hidden',
          background: 'radial-gradient(ellipse at center, rgba(0,18,36,0.4) 0%, rgba(0,8,20,0.9) 100%)',
        }}>
          {/* Starfield */}
          {useMemo(() => [...Array(20)].map((_, i) => (
            <div key={i} style={{
              position: 'absolute', left: `${(i * 41 + 7) % 100}%`, top: `${(i * 29 + 3) % 100}%`,
              width: i % 3 === 0 ? 2 : 1, height: i % 3 === 0 ? 2 : 1, borderRadius: '50%',
              background: `rgba(255,255,255,${0.15 + (i % 4) * 0.1})`,
            }} />
          )), [])}

          {/* Grid */}
          <svg style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', opacity: 0.04, pointerEvents: 'none' }}>
            {[20, 40, 60, 80].map(x => <line key={`v${x}`} x1={`${x}%`} y1="0" x2={`${x}%`} y2="100%" stroke="#fff" />)}
            {[25, 50, 75].map(y => <line key={`h${y}`} x1="0" y1={`${y}%`} x2="100%" y2={`${y}%`} stroke="#fff" />)}
          </svg>

          {/* Zone circles */}
          {ZONES.map(z => (
            <div key={z.label} style={{
              position: 'absolute', left: `${z.x}%`, top: `${z.y}%`, transform: 'translate(-50%, -50%)',
              width: z.r * 2, height: z.r * 2, borderRadius: '50%',
              border: `1px solid ${z.color}15`, background: `${z.color}05`,
              display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
              pointerEvents: 'none',
            }}>
              <span style={{ fontSize: 16, opacity: 0.3 }}>{z.label}</span>
              <span style={{ fontSize: 7, color: z.color, opacity: 0.3, fontWeight: 600 }}>{z.sublabel}</span>
            </div>
          ))}

          {/* Live graph in center */}
          <LiveGraph graphNodes={graphNodes} />

          {/* Agents */}
          {agentNames.length === 0 && (
            <div style={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', fontSize: 11, color: '#64748b' }}>Waiting for agents...</div>
          )}
          {agentNames.map((name, i) => {
            const as = agentStates[name] || {};
            const state = as.state || (isRunning ? 'waiting' : 'idle');
            const basePos = STATE_POSITIONS[state] || STATE_POSITIONS.idle;
            const offset = (i - (agentNames.length - 1) / 2) * 8;
            const pos = {
              x: Math.min(93, Math.max(7, basePos.x + offset)),
              y: Math.min(92, Math.max(8, basePos.y + (i % 2 === 0 ? -5 : 5))),
            };
            return (
              <Agent key={name} name={name} state={state}
                tool={as.tool || ''} action={as.action || ''} lastResult={as.lastResult || ''}
                pos={pos} prevPos={prevPositions[name]} color={COLORS[i % COLORS.length]} index={i} />
            );
          })}
        </div>

        {/* Injection */}
        {isRunning && (
          <div style={{ padding: '6px 14px', borderTop: '1px solid var(--neo-border)', display: 'flex', gap: 6, alignItems: 'center', background: 'rgba(255,215,0,0.02)' }}>
            <Zap size={10} style={{ color: '#ffd700', flexShrink: 0 }} />
            <select value={injectTarget} onChange={e => setInjectTarget(e.target.value)} style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', borderRadius: 4, padding: '3px 4px', fontSize: 9, color: 'var(--neo-text)', width: 70 }}>
              <option value="all">All</option>
              {agentNames.map(n => <option key={n} value={n}>{n}</option>)}
            </select>
            <select value={injectType} onChange={e => setInjectType(e.target.value)} style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', borderRadius: 4, padding: '3px 4px', fontSize: 9, color: 'var(--neo-text)', width: 70 }}>
              <option value="directive">Directive</option>
              <option value="constraint">Constraint</option>
              <option value="stop">Stop</option>
            </select>
            <input value={injectMsg} onChange={e => setInjectMsg(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleInject()}
              placeholder="Inject prompt..." style={{ flex: 1, background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', borderRadius: 4, padding: '4px 8px', fontSize: 10, color: 'var(--neo-text)', outline: 'none' }} />
            <button onClick={handleInject} disabled={injecting || !injectMsg.trim()} style={{ background: 'rgba(255,215,0,0.15)', border: '1px solid rgba(255,215,0,0.3)', borderRadius: 4, padding: '3px 8px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 3, fontSize: 9, color: '#ffd700', opacity: !injectMsg.trim() ? 0.4 : 1 }}>
              <Send size={8} /> Inject
            </button>
          </div>
        )}

        {/* Feed */}
        {showFeed && (
          <div ref={feedRef} style={{ maxHeight: 160, overflowY: 'auto', padding: '6px 14px', borderTop: '1px solid var(--neo-border)' }}>
            {events.length === 0 && <span style={{ fontSize: 10, color: '#64748b' }}>No events yet...</span>}
            {events.slice(-40).map((evt, i) => <ActivityLine key={i} event={evt} isNew={i >= events.length - 3} />)}
          </div>
        )}
      </div>
    </>
  );
}

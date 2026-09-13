import React from 'react';
import { Bot, Search, FileCode, Brain, CheckCircle, AlertTriangle, Zap, Clock, Pencil } from 'lucide-react';

const STATE_CONFIG = {
  idle: { color: 'var(--neo-text-muted)', glow: 'none', icon: Bot, label: 'Idle' },
  thinking: { color: 'var(--neo-cyan)', glow: '0 0 0 0 rgba(0,210,255,0.4)', icon: Brain, label: 'Thinking...' },
  tool_call: { color: 'var(--neo-blue)', glow: '0 0 12px rgba(0,122,255,0.3)', icon: Search, label: 'Calling tool' },
  writing: { color: 'var(--neo-green)', glow: '0 0 12px rgba(76,217,100,0.3)', icon: FileCode, label: 'Writing' },
  searching: { color: 'var(--neo-purple, #af52de)', glow: '0 0 12px rgba(175,82,222,0.3)', icon: Search, label: 'Searching' },
  deciding: { color: '#ff9500', glow: '0 0 12px rgba(255,149,0,0.3)', icon: Brain, label: 'Deciding' },
  injected: { color: '#ffd700', glow: '0 0 20px rgba(255,215,0,0.5)', icon: Zap, label: 'Directive received' },
  waiting: { color: 'var(--neo-text-muted)', glow: 'none', icon: Clock, label: 'Waiting' },
  error: { color: '#ef4444', glow: '0 0 12px rgba(239,68,68,0.3)', icon: AlertTriangle, label: 'Error' },
  done: { color: 'var(--neo-green)', glow: '0 0 12px rgba(76,217,100,0.4)', icon: CheckCircle, label: 'Done' },
  knowledge: { color: 'var(--neo-cyan)', glow: '0 0 12px rgba(0,210,255,0.3)', icon: Brain, label: 'Adding knowledge' },
  claiming: { color: '#ff9500', glow: '0 0 12px rgba(255,149,0,0.3)', icon: Pencil, label: 'Claiming task' },
};

const ANIMATION_STYLES = `
  @keyframes agent-pulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(0,210,255,0.4); }
    50% { box-shadow: 0 0 0 14px rgba(0,210,255,0); }
  }
  @keyframes agent-shake {
    0%, 100% { transform: translateX(0); }
    20% { transform: translateX(-3px); }
    40% { transform: translateX(3px); }
    60% { transform: translateX(-2px); }
    80% { transform: translateX(2px); }
  }
  @keyframes agent-bounce {
    0%, 100% { transform: scale(1); }
    50% { transform: scale(1.08); }
  }
  @keyframes agent-flash {
    0% { opacity: 1; }
    50% { opacity: 0.4; }
    100% { opacity: 1; }
  }
  @keyframes agent-glow-rotate {
    0% { box-shadow: 0 0 8px 0 rgba(0,210,255,0.4); }
    33% { box-shadow: 0 0 8px 0 rgba(175,82,222,0.4); }
    66% { box-shadow: 0 0 8px 0 rgba(76,217,100,0.4); }
    100% { box-shadow: 0 0 8px 0 rgba(0,210,255,0.4); }
  }
  @keyframes ripple {
    0% { transform: scale(0.8); opacity: 0.8; }
    100% { transform: scale(2.2); opacity: 0; }
  }
  @keyframes lightning {
    0% { opacity: 0; transform: scale(0.5); }
    30% { opacity: 1; transform: scale(1.3); }
    100% { opacity: 0; transform: scale(1); }
  }
`;

const getAnimation = (state) => {
  switch (state) {
    case 'thinking': return 'agent-pulse 1.5s ease-in-out infinite';
    case 'error': return 'agent-shake 0.4s ease-in-out';
    case 'done': return 'agent-bounce 0.5s ease-out';
    case 'injected': return 'agent-flash 0.3s ease-in-out 3';
    case 'tool_call':
    case 'searching':
    case 'writing':
    case 'knowledge':
    case 'claiming':
      return 'agent-glow-rotate 2s linear infinite';
    default: return 'none';
  }
};

export default function AgentAvatar({ name, state = 'idle', currentTool = '', lastAction = '', size = 'md' }) {
  const config = STATE_CONFIG[state] || STATE_CONFIG.idle;
  const IconComponent = config.icon;
  const isActive = state !== 'idle' && state !== 'done';
  const sizes = { sm: 48, md: 72, lg: 96 };
  const s = sizes[size] || sizes.md;
  const iconSize = s * 0.35;

  return (
    <>
      <style>{ANIMATION_STYLES}</style>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, position: 'relative' }}>
        {/* Ripple effect for active states */}
        {isActive && state !== 'waiting' && (
          <div style={{
            position: 'absolute', top: 0, left: '50%', transform: 'translateX(-50%)',
            width: s, height: s, borderRadius: '50%',
            border: `2px solid ${config.color}`, opacity: 0.3,
            animation: 'ripple 2s ease-out infinite',
            pointerEvents: 'none',
          }} />
        )}

        {/* Lightning bolt for injection */}
        {state === 'injected' && (
          <div style={{
            position: 'absolute', top: -8, right: -4, fontSize: 20, zIndex: 10,
            animation: 'lightning 0.6s ease-out',
          }}>
            <Zap size={18} fill="#ffd700" color="#ffd700" />
          </div>
        )}

        {/* Avatar circle */}
        <div
          style={{
            width: s, height: s, borderRadius: '50%',
            background: `linear-gradient(135deg, rgba(0,0,0,0.3), rgba(0,0,0,0.1))`,
            border: `2px solid ${config.color}`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            animation: getAnimation(state),
            transition: 'border-color 0.3s, box-shadow 0.3s',
            boxShadow: config.glow,
            position: 'relative',
          }}
          title={`${name}: ${config.label}${currentTool ? ` (${currentTool})` : ''}`}
        >
          <IconComponent size={iconSize} color={config.color} />
        </div>

        {/* Name */}
        <span style={{
          fontSize: 11, fontWeight: 600, color: isActive ? config.color : 'var(--neo-text-muted)',
          transition: 'color 0.3s', textAlign: 'center', maxWidth: s + 20,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {name}
        </span>

        {/* Status label */}
        <span style={{
          fontSize: 9, color: config.color, opacity: isActive ? 1 : 0.5,
          transition: 'opacity 0.3s', textAlign: 'center',
        }}>
          {currentTool || config.label}
        </span>
      </div>
    </>
  );
}

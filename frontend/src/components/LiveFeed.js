import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Radio, Wifi, WifiOff, Bot, Plus, Minus, FileText } from 'lucide-react';

const EVENT_STYLES = {
  context_added: { color: 'var(--neo-green)', icon: Plus, label: 'Context Added' },
  context_updated: { color: 'var(--neo-blue)', icon: FileText, label: 'Context Updated' },
  context_deleted: { color: '#ef4444', icon: Minus, label: 'Context Deleted' },
  agent_joined: { color: 'var(--neo-cyan)', icon: Bot, label: 'Agent Joined' },
  agent_left: { color: 'var(--neo-text-muted)', icon: Bot, label: 'Agent Left' },
};

export default function LiveFeed({ sessionId, onEvent }) {
  const [events, setEvents] = useState([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState(null);
  const wsRef = useRef(null);
  const feedRef = useRef(null);
  const reconnectRef = useRef(null);

  const connect = useCallback(() => {
    if (!sessionId) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const url = `${protocol}//${host}/context/sessions/${sessionId}/ws`;

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        setError(null);
        // Send handshake
        ws.send(JSON.stringify({ agent_id: 'dashboard-observer', type: 'subscribe' }));
      };

      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          const parsed = { ...data, _ts: new Date().toISOString(), _id: Date.now() + Math.random() };
          setEvents((prev) => [parsed, ...prev].slice(0, 100)); // Keep last 100 events
          if (onEvent) onEvent(parsed);
        } catch {
          // Non-JSON message
        }
      };

      ws.onclose = () => {
        setConnected(false);
        // Auto-reconnect after 3 seconds
        reconnectRef.current = setTimeout(connect, 3000);
      };

      ws.onerror = () => {
        setError('Could not connect to live feed');
        setConnected(false);
      };
    } catch {
      setError('WebSocket not available');
    }
  }, [sessionId]);

  useEffect(() => {
    connect();
    return () => {
      if (wsRef.current) wsRef.current.close();
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
    };
  }, [connect]);

  // Auto-scroll
  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = 0;
    }
  }, [events]);

  return (
    <div className="space-y-3">
      {/* Connection status */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Radio size={14} style={{ color: connected ? 'var(--neo-green)' : 'var(--neo-text-muted)' }} />
          <span className="text-xs font-semibold" style={{ color: 'var(--neo-text)' }}>
            Live Feed
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          {connected ? (
            <Wifi size={12} style={{ color: 'var(--neo-green)' }} />
          ) : (
            <WifiOff size={12} style={{ color: 'var(--neo-text-muted)' }} />
          )}
          <span
            className="text-xs px-1.5 py-0.5 rounded"
            style={{
              background: connected ? 'rgba(76,217,100,0.15)' : 'rgba(150,150,150,0.15)',
              color: connected ? 'var(--neo-green)' : 'var(--neo-text-muted)',
            }}
          >
            {connected ? 'Connected' : error || 'Disconnected'}
          </span>
        </div>
      </div>

      {/* Events feed */}
      <div
        ref={feedRef}
        className="space-y-1.5"
        style={{ maxHeight: 350, overflow: 'auto' }}
      >
        {events.length === 0 ? (
          <div className="text-center py-8">
            <Radio
              size={24}
              className="mx-auto mb-2"
              style={{ color: 'var(--neo-text-muted)', opacity: 0.5 }}
            />
            <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
              {connected ? 'Listening for events...' : 'Waiting for connection...'}
            </p>
            <p className="text-xs mt-1" style={{ color: 'var(--neo-text-muted)', opacity: 0.6 }}>
              Events will appear here when agents interact with this session
            </p>
          </div>
        ) : (
          events.map((evt) => {
            const evtStyle = EVENT_STYLES[evt.event_type] || EVENT_STYLES.context_added;
            const Icon = evtStyle.icon;
            return (
              <div
                key={evt._id}
                className="flex items-center justify-between px-3 py-2 rounded-lg"
                style={{ background: 'var(--neo-surface)' }}
              >
                <div className="flex items-center gap-2 min-w-0">
                  <Icon size={12} style={{ color: evtStyle.color }} />
                  <span className="text-xs font-medium" style={{ color: evtStyle.color }}>
                    {evtStyle.label}
                  </span>
                  {evt.agent_id && (
                    <span
                      className="px-1.5 py-0.5 rounded text-xs"
                      style={{ background: 'rgba(0,210,255,0.1)', color: 'var(--neo-cyan)' }}
                    >
                      {evt.agent_name || evt.agent_id.slice(0, 12)}
                    </span>
                  )}
                  {evt.content_type && (
                    <span className="text-xs truncate" style={{ color: 'var(--neo-text-muted)' }}>
                      {evt.content_type}
                    </span>
                  )}
                </div>
                <span className="text-xs shrink-0 ml-2" style={{ color: 'var(--neo-text-muted)' }}>
                  {new Date(evt._ts).toLocaleTimeString()}
                </span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

import React, { useState, useEffect, useRef } from 'react';
import { Bell, X, Database, Trash2, Workflow, Upload, AlertTriangle, Brain, RefreshCw } from 'lucide-react';
import api from '../lib/api';

const EVENT_ICONS = {
  graph_created: Database,
  graph_deleted: Trash2,
  pipeline_completed: Workflow,
  ingestion_done: Upload,
  conflict_detected: AlertTriangle,
  intelligence_suggestion: Brain,
  source_stale: RefreshCw,
};

const EVENT_LABELS = {
  graph_created: 'Graph created',
  graph_deleted: 'Graph deleted',
  pipeline_completed: 'Pipeline completed',
  ingestion_done: 'Ingestion complete',
  conflict_detected: 'Conflict detected',
  intelligence_suggestion: 'New suggestion',
  source_stale: 'Source stale',
};

function timeAgo(ts) {
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default function NotificationCenter() {
  const [events, setEvents] = useState([]);
  const [open, setOpen] = useState(false);
  const [unread, setUnread] = useState(0);
  const wsRef = useRef(null);
  const panelRef = useRef(null);

  useEffect(() => {
    // Use REST polling for notifications (EventContext owns the single WebSocket)
    const fetchEvents = () => {
      if (!localStorage.getItem('contextsynapse_token')) return;
      api.get('/events/recent')
        .then((res) => {
          const newEvents = res.data.events || [];
          if (newEvents.length) {
            setEvents((prev) => {
              const merged = [...newEvents.reverse(), ...prev].slice(0, 50);
              const newCount = newEvents.filter(e => !prev.some(p => p.timestamp === e.timestamp)).length;
              if (newCount > 0) setUnread((n) => n + newCount);
              return merged;
            });
          }
        })
        .catch(() => {});
    };

    fetchEvents();
    const interval = setInterval(fetchEvents, 10000);
    return () => clearInterval(interval);
  }, []);

  // Close panel on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e) => {
      if (panelRef.current && !panelRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const toggle = () => {
    setOpen((o) => !o);
    if (!open) setUnread(0);
  };

  return (
    <div className="relative" ref={panelRef}>
      <button
        onClick={toggle}
        className="relative p-1.5 rounded-lg transition bg-transparent border-none cursor-pointer"
        style={{ color: 'var(--neo-text-muted)' }}
        aria-label="Notifications"
      >
        <Bell size={18} />
        {unread > 0 && (
          <span
            className="absolute -top-0.5 -right-0.5 w-4 h-4 rounded-full text-xs font-bold flex items-center justify-center"
            style={{ background: 'var(--neo-red)', color: '#fff', fontSize: 9 }}
          >
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div
          className="absolute right-0 top-10 w-80 rounded-xl shadow-2xl overflow-hidden z-50"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        >
          <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: '1px solid var(--neo-border)' }}>
            <span className="text-sm font-semibold" style={{ color: 'var(--neo-text)' }}>Notifications</span>
            <button
              onClick={() => setOpen(false)}
              className="bg-transparent border-none cursor-pointer p-0"
              style={{ color: 'var(--neo-text-muted)' }}
              aria-label="Close notifications"
            >
              <X size={16} />
            </button>
          </div>

          <div className="max-h-72 overflow-y-auto">
            {events.length === 0 ? (
              <p className="px-4 py-8 text-sm text-center" style={{ color: 'var(--neo-text-muted)' }}>
                No notifications yet
              </p>
            ) : (
              events.map((evt, i) => {
                const Icon = EVENT_ICONS[evt.type] || Bell;
                return (
                  <div
                    key={`${evt.type}-${evt.timestamp}-${i}`}
                    className="flex items-start gap-3 px-4 py-3 transition"
                    style={{ borderBottom: '1px solid var(--neo-border)' }}
                  >
                    <Icon size={16} style={{ color: 'var(--neo-blue)', marginTop: 2, flexShrink: 0 }} />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm" style={{ color: 'var(--neo-text)' }}>
                        {EVENT_LABELS[evt.type] || evt.type}
                      </p>
                      {evt.data?.name && (
                        <p className="text-xs truncate" style={{ color: 'var(--neo-text-muted)' }}>
                          {evt.data.name}
                        </p>
                      )}
                    </div>
                    <span className="text-xs whitespace-nowrap" style={{ color: 'var(--neo-text-dim)' }}>
                      {timeAgo(evt.timestamp)}
                    </span>
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}

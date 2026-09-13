/**
 * EventContext — Shared event bus for real-time UI updates.
 *
 * Primary: WebSocket (/ws/events).
 * Fallback: REST polling (/events/recent) with exponential backoff when WS is down.
 *
 * Any page can subscribe to server events:
 *   const { useEvent } = useEvents();
 *   useEvent('graph_deleted', () => fetchGraphs());
 *   useEvent('context_created', () => fetchContexts());
 */

import React, { createContext, useContext, useEffect, useRef, useCallback } from 'react';

const EventContext = createContext(null);

function getWsUrl(path) {
  // Use same origin (goes through proxy in dev, direct in production)
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${window.location.host}${path}`;
}

function getApiUrl(path) {
  // Use same origin (goes through proxy in dev, direct in production)
  return `${path}`;
}

export function EventProvider({ children }) {
  const wsRef = useRef(null);
  const listenersRef = useRef({});  // { eventType: Set<callback> }
  const reconnectTimer = useRef(null);
  const pollTimer = useRef(null);
  const closedIntentionally = useRef(false);
  const wsConnected = useRef(false);
  const lastSeenTimestamp = useRef(0);
  const pollBackoff = useRef(3000); // start at 3s, max 30s

  const dispatch = useCallback((evt) => {
    const type = evt.type;
    if (listenersRef.current[type]) {
      listenersRef.current[type].forEach(cb => {
        try { cb(evt.data || evt); } catch {}
      });
    }
    if (listenersRef.current['*']) {
      listenersRef.current['*'].forEach(cb => {
        try { cb(evt); } catch {}
      });
    }
  }, []);

  const subscribe = useCallback((eventType, callback) => {
    if (!listenersRef.current[eventType]) {
      listenersRef.current[eventType] = new Set();
    }
    listenersRef.current[eventType].add(callback);

    return () => {
      listenersRef.current[eventType]?.delete(callback);
    };
  }, []);

  // REST polling fallback — only active when WebSocket is disconnected
  const startPolling = useCallback((token) => {
    if (pollTimer.current) return; // already polling

    function poll() {
      if (wsConnected.current || closedIntentionally.current) {
        // WebSocket reconnected or component unmounted — stop polling
        if (pollTimer.current) clearTimeout(pollTimer.current);
        pollTimer.current = null;
        pollBackoff.current = 3000;
        return;
      }

      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 5000); // 5s timeout
      fetch(getApiUrl('/events/recent'), {
        headers: { 'Authorization': `Bearer ${token}` },
        signal: controller.signal,
      })
        .then(r => { clearTimeout(timeoutId); return r.ok ? r.json() : []; })
        .then(events => {
          // Only dispatch events newer than last seen
          for (const evt of events) {
            if (evt.timestamp && evt.timestamp > lastSeenTimestamp.current) {
              lastSeenTimestamp.current = evt.timestamp;
              dispatch(evt);
            }
          }
          // Success — reset backoff
          pollBackoff.current = 3000;
        })
        .catch(() => {
          // Increase backoff on failure (max 30s)
          pollBackoff.current = Math.min(pollBackoff.current * 1.5, 30000);
        })
        .finally(() => {
          if (!wsConnected.current && !closedIntentionally.current) {
            pollTimer.current = setTimeout(poll, pollBackoff.current);
          }
        });
    }

    pollTimer.current = setTimeout(poll, 5000); // first poll after 5s
  }, [dispatch]);

  // Re-read token on every storage change (handles re-login)
  const [token, setToken] = React.useState(() => localStorage.getItem('contextsynapse_token'));
  useEffect(() => {
    const onStorage = () => setToken(localStorage.getItem('contextsynapse_token'));
    window.addEventListener('storage', onStorage);
    // Also poll localStorage every 2s in case login happens in same tab
    const tokenPoll = setInterval(onStorage, 2000);
    return () => { window.removeEventListener('storage', onStorage); clearInterval(tokenPoll); };
  }, []);

  useEffect(() => {
    if (!token) return;

    closedIntentionally.current = false;

    function connect() {
      try {
        const ws = new WebSocket(getWsUrl(`/ws/events?token=${token}`));
        wsRef.current = ws;

        ws.onopen = () => {
          wsConnected.current = true;
          pollBackoff.current = 3000;
          // Stop polling if running — WS is back
          if (pollTimer.current) {
            clearTimeout(pollTimer.current);
            pollTimer.current = null;
          }
        };

        ws.onmessage = (msg) => {
          try {
            const evt = JSON.parse(msg.data);
            if (evt.timestamp) lastSeenTimestamp.current = evt.timestamp;
            dispatch(evt);
          } catch {}
        };

        ws.onclose = () => {
          wsConnected.current = false;
          if (!closedIntentionally.current) {
            // Start REST polling as fallback
            startPolling(token);
            // Also try to reconnect WS with backoff
            reconnectTimer.current = setTimeout(connect, 5000);
          }
        };

        ws.onerror = () => {};
      } catch {
        // WS constructor failed — start polling
        wsConnected.current = false;
        startPolling(token);
      }
    }

    connect();

    return () => {
      closedIntentionally.current = true;
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      if (pollTimer.current) clearTimeout(pollTimer.current);
      pollTimer.current = null;
      if (wsRef.current) {
        // Only close if past CONNECTING state to avoid "closed before established" browser warning
        if (wsRef.current.readyState !== WebSocket.CONNECTING) {
          try { wsRef.current.close(); } catch {}
        } else {
          // Let it open then immediately close, or let onclose handle cleanup
          wsRef.current.onopen = () => { try { wsRef.current.close(); } catch {} };
        }
      }
    };
  }, [dispatch, startPolling, token]);

  return (
    <EventContext.Provider value={{ subscribe }}>
      {children}
    </EventContext.Provider>
  );
}

/**
 * Hook to subscribe to server events.
 * Usage: useEvent('graph_deleted', (data) => refetch());
 */
export function useEvent(eventType, callback) {
  const ctx = useContext(EventContext);
  useEffect(() => {
    if (!ctx) return;
    return ctx.subscribe(eventType, callback);
  }, [ctx, eventType, callback]);
}

export function useEvents() {
  return useContext(EventContext);
}

/**
 * useExperimentSSE — Real-time SSE hook for experiment runs.
 *
 * Connects to GET /dashboard/experiments/{expId}/runs/{runId}/stream
 * and dispatches structured events to update the UI incrementally.
 *
 * Falls back to polling if SSE connection fails (e.g., behind a proxy that
 * buffers chunked responses).
 */
import { useRef, useCallback, useEffect } from 'react';

/**
 * @param {Object} opts
 * @param {string} opts.expId - Experiment ID
 * @param {string} opts.runId - Run ID
 * @param {boolean} opts.enabled - Whether to connect
 * @param {(event: {type: string, data: object}) => void} opts.onEvent - Event handler
 * @param {() => void} opts.onComplete - Called when run finishes
 * @param {() => void} opts.onError - Called on connection failure
 */
export function useExperimentSSE({ expId, runId, enabled, onEvent, onComplete, onError }) {
  const sourceRef = useRef(null);
  const fallbackRef = useRef(null);
  const retriesRef = useRef(0);

  const disconnect = useCallback(() => {
    if (sourceRef.current) {
      sourceRef.current.close();
      sourceRef.current = null;
    }
    if (fallbackRef.current) {
      clearInterval(fallbackRef.current);
      fallbackRef.current = null;
    }
    retriesRef.current = 0;
  }, []);

  const connect = useCallback(() => {
    if (!expId || !runId || !enabled) return;
    disconnect();

    const url = `/dashboard/experiments/${expId}/runs/${runId}/stream`;
    const token = localStorage.getItem('contextsynapse_token');
    // EventSource doesn't support custom headers, so pass token as query param
    const fullUrl = token ? `${url}?token=${encodeURIComponent(token)}` : url;

    try {
      const es = new EventSource(fullUrl);
      sourceRef.current = es;

      // Define handlers for each event type
      const EVENT_TYPES = [
        'run_start', 'agent_start', 'agent_plan', 'step_done',
        'finding', 'score_update', 'agent_done', 'run_complete', 'error',
        // Legacy event names (backward compat with old backend)
        'step', 'complete',
      ];

      EVENT_TYPES.forEach(eventType => {
        es.addEventListener(eventType, (e) => {
          try {
            const data = JSON.parse(e.data);
            onEvent?.({ type: eventType, data });

            // Handle completion events
            if (eventType === 'run_complete' || eventType === 'complete') {
              onComplete?.();
              disconnect();
            }
          } catch (err) {
            console.warn('[SSE] Failed to parse event:', eventType, e.data);
          }
        });
      });

      // Generic message handler (for events without a named type)
      es.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          onEvent?.({ type: data.event || 'message', data });
        } catch {}
      };

      es.onerror = () => {
        retriesRef.current += 1;
        if (retriesRef.current > 3) {
          // SSE failed — fall back to polling
          console.warn('[SSE] Connection failed after 3 retries, falling back to polling');
          disconnect();
          onError?.();
        }
      };

      es.onopen = () => {
        retriesRef.current = 0;
      };
    } catch (err) {
      console.warn('[SSE] EventSource creation failed:', err);
      onError?.();
    }
  }, [expId, runId, enabled, onEvent, onComplete, onError, disconnect]);

  useEffect(() => {
    if (enabled && expId && runId) {
      connect();
    }
    return disconnect;
  }, [enabled, expId, runId, connect, disconnect]);

  return { disconnect };
}

export default useExperimentSSE;

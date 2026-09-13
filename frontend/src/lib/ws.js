/**
 * Build a WebSocket URL that works in both development and production.
 *
 * In development (CRA on port 3000), the HTTP proxy works for REST but
 * WebSocket upgrade through http-proxy-middleware is unreliable.
 * We connect directly to the backend on port 8000 instead.
 *
 * In production the frontend is served by the backend, so same origin works.
 */
export function getWsUrl(path = '/ws/events') {
  const token = localStorage.getItem('contextsynapse_token');
  const qs = token ? `?token=${token}` : '';

  // Production: same host
  if (process.env.NODE_ENV === 'production') {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    return `${protocol}://${window.location.host}${path}${qs}`;
  }

  // Development: connect directly to backend (default 8000)
  const backendPort = process.env.REACT_APP_BACKEND_PORT || '8000';
  return `ws://${window.location.hostname}:${backendPort}${path}${qs}`;
}

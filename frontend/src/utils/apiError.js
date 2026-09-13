/**
 * Extract a human-readable string from an API error.
 * Handles Pydantic v2 validation errors ({ detail: [{type, loc, msg, input}] })
 * as well as plain string details and generic errors.
 */
export function apiErr(err, fallback = 'Operation failed') {
  const detail = err?.response?.data?.detail;
  if (!detail) return err?.message || fallback;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(e => e.msg || JSON.stringify(e)).join(', ');
  return fallback;
}

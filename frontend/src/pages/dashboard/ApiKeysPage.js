import React, { useState, useEffect } from 'react';
import { Key, RotateCw, Copy, Check, Loader2, AlertTriangle } from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../../lib/api';

export default function ApiKeysPage() {
  const [keys, setKeys] = useState([]);
  const [loading, setLoading] = useState(true);
  const [rotating, setRotating] = useState(false);
  const [newKey, setNewKey] = useState(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api.get('/dashboard/api-keys')
      .then(res => setKeys(res.data.keys || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const handleRotate = async () => {
    if (!window.confirm('Rotate API key? The current key will stop working immediately.')) return;
    setRotating(true);
    try {
      const res = await api.post('/dashboard/api-keys/rotate');
      setNewKey(res.data.api_key);
      toast.success('API key rotated');
    } catch {
      // handled by interceptor
    } finally {
      setRotating(false);
    }
  };

  const copyKey = () => {
    if (newKey) {
      navigator.clipboard.writeText(newKey);
      setCopied(true);
      toast.success('Copied to clipboard');
      setTimeout(() => setCopied(false), 2000);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      </div>
    );
  }

  return (
    <div className="max-w-3xl">
      <h1 className="text-xl font-bold mb-1" style={{ color: 'var(--neo-text)' }}>API Keys</h1>
      <p className="text-sm mb-6" style={{ color: 'var(--neo-text-muted)' }}>
        Use your API key to authenticate graph operations via the REST API.
      </p>

      {/* New key banner */}
      {newKey && (
        <div
          className="flex items-start gap-3 p-4 rounded-xl mb-6"
          style={{ background: 'rgba(76,217,100,0.1)', border: '1px solid var(--neo-green)' }}
        >
          <AlertTriangle size={18} style={{ color: 'var(--neo-yellow)' }} className="mt-0.5" />
          <div className="flex-1">
            <p className="text-sm font-medium mb-2" style={{ color: 'var(--neo-text)' }}>
              New API key generated — copy it now. It won't be shown again.
            </p>
            <div className="flex items-center gap-2">
              <code
                className="flex-1 px-3 py-2 rounded text-xs font-mono"
                style={{ background: 'var(--neo-bg)', color: 'var(--neo-green)' }}
              >
                {newKey}
              </code>
              <button
                onClick={copyKey}
                className="p-2 rounded-lg transition hover:bg-neo-surface-light"
                style={{ color: copied ? 'var(--neo-green)' : 'var(--neo-text-muted)' }}
              >
                {copied ? <Check size={16} /> : <Copy size={16} />}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Key list */}
      <div className="space-y-3">
        {keys.map((k) => (
          <div
            key={k.tenant_id}
            className="flex items-center justify-between p-4 rounded-xl"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
          >
            <div className="flex items-center gap-3">
              <Key size={18} style={{ color: 'var(--neo-cyan)' }} />
              <div>
                <div className="font-medium text-sm" style={{ color: 'var(--neo-text)' }}>
                  {k.tenant_name}
                </div>
                <div className="text-xs mt-0.5" style={{ color: 'var(--neo-text-muted)' }}>
                  {k.role} &middot; {k.status} &middot; Key: {k.key_hint || '***'}
                </div>
              </div>
            </div>

            {k.role === 'owner' && (
              <button
                onClick={handleRotate}
                disabled={rotating}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition hover:opacity-90 disabled:opacity-50"
                style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
              >
                {rotating ? <Loader2 size={14} className="animate-spin" /> : <RotateCw size={14} />}
                Rotate
              </button>
            )}
          </div>
        ))}
      </div>

      {/* Usage example */}
      <div
        className="mt-8 p-5 rounded-xl"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
      >
        <h2 className="text-sm font-semibold mb-3" style={{ color: 'var(--neo-text)' }}>
          Using your API key
        </h2>
        <pre
          className="text-xs p-4 rounded-lg overflow-x-auto"
          style={{ background: 'var(--neo-bg)', color: 'var(--neo-text-muted)' }}
        >
{`curl -X POST http://localhost:8000/query/grql \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"query": "SELECT * FROM Person"}'`}
        </pre>
      </div>
    </div>
  );
}

import React, { useState, useEffect } from 'react';
import { Loader2, Eye, MessageSquare, FileText, Code } from 'lucide-react';
import api from '../lib/api';

const FORMAT_TABS = [
  { key: 'messages', label: 'Messages', icon: MessageSquare },
  { key: 'prompt', label: 'Prompt', icon: FileText },
  { key: 'markdown', label: 'Markdown', icon: Code },
];

function MessagesView({ data }) {
  if (!Array.isArray(data) || data.length === 0) {
    return (
      <p className="text-xs py-3 text-center" style={{ color: 'var(--neo-text-muted)' }}>
        No messages — context is empty
      </p>
    );
  }

  const ROLE_COLORS = {
    system: { bg: 'rgba(175,82,222,0.15)', color: '#af52de' },
    user: { bg: 'rgba(0,122,255,0.15)', color: 'var(--neo-blue)' },
    assistant: { bg: 'rgba(76,217,100,0.15)', color: 'var(--neo-green)' },
  };

  return (
    <div className="space-y-2">
      {data.map((msg, i) => {
        const rc = ROLE_COLORS[msg.role] || ROLE_COLORS.user;
        return (
          <div
            key={i}
            className="rounded-lg p-3"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
          >
            <div className="flex items-center gap-2 mb-1.5">
              <span
                className="px-1.5 py-0.5 rounded text-xs font-medium"
                style={{ background: rc.bg, color: rc.color }}
              >
                {msg.role}
              </span>
              <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                {msg.content.length} chars
              </span>
            </div>
            <pre
              className="text-xs whitespace-pre-wrap break-words m-0"
              style={{
                color: 'var(--neo-text)',
                fontFamily: "'JetBrains Mono', monospace",
                maxHeight: 200,
                overflow: 'auto',
              }}
            >
              {msg.content}
            </pre>
          </div>
        );
      })}
    </div>
  );
}

function TextPreview({ data, format }) {
  if (!data) {
    return (
      <p className="text-xs py-3 text-center" style={{ color: 'var(--neo-text-muted)' }}>
        No content — context is empty
      </p>
    );
  }

  return (
    <pre
      className="text-xs whitespace-pre-wrap break-words p-3 rounded-lg m-0"
      style={{
        background: 'var(--neo-surface)',
        border: '1px solid var(--neo-border)',
        color: 'var(--neo-text)',
        fontFamily: format === 'markdown' ? 'inherit' : "'JetBrains Mono', monospace",
        maxHeight: 400,
        overflow: 'auto',
      }}
    >
      {data}
    </pre>
  );
}

export default function ContextPreview({ sessionId }) {
  const [format, setFormat] = useState('messages');
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [meta, setMeta] = useState({ count: 0, estimated_tokens: 0 });

  useEffect(() => {
    if (!sessionId) return;
    setLoading(true);
    api.get(`/dashboard/sessions/${sessionId}/context/preview?format=${format}`)
      .then((res) => {
        setPreview(res.data.preview);
        setMeta({ count: res.data.count || 0, estimated_tokens: res.data.estimated_tokens || 0 });
      })
      .catch(() => setPreview(null))
      .finally(() => setLoading(false));
  }, [sessionId, format]);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Eye size={14} style={{ color: 'var(--neo-cyan)' }} />
          <span className="text-xs font-semibold" style={{ color: 'var(--neo-text)' }}>
            Agent View Preview
          </span>
          <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
            ({meta.count} items, ~{meta.estimated_tokens} tokens)
          </span>
        </div>

        <div className="flex gap-1">
          {FORMAT_TABS.map((f) => (
            <button
              key={f.key}
              onClick={() => setFormat(f.key)}
              className="flex items-center gap-1 px-2 py-1 rounded text-xs font-medium transition"
              style={{
                background: format === f.key ? 'var(--neo-cyan)' : 'transparent',
                color: format === f.key ? '#fff' : 'var(--neo-text-muted)',
              }}
            >
              <f.icon size={10} />
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-6">
          <Loader2 size={16} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
        </div>
      ) : format === 'messages' ? (
        <MessagesView data={preview} />
      ) : (
        <TextPreview data={preview} format={format} />
      )}
    </div>
  );
}

import React, { useState, useEffect } from 'react';
import { MessageSquare, Loader2, Bot, User, ChevronDown, ChevronUp } from 'lucide-react';
import api from '../lib/api';

const ROLE_STYLES = {
  user: { color: 'var(--neo-blue)', icon: User, label: 'User' },
  assistant: { color: 'var(--neo-green)', icon: Bot, label: 'Assistant' },
  system: { color: 'var(--neo-purple, #a78bfa)', icon: MessageSquare, label: 'System' },
  agent: { color: 'var(--neo-cyan)', icon: Bot, label: 'Agent' },
};

export default function ConversationPanel({ sessionId }) {
  const [conversations, setConversations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(null);
  const [messages, setMessages] = useState({});
  const [msgsLoading, setMsgsLoading] = useState({});

  useEffect(() => {
    api.get(`/dashboard/sessions/${sessionId}/conversations`)
      .then((res) => setConversations(res.data.conversations || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [sessionId]);

  const toggleConvo = async (convId) => {
    if (expanded === convId) {
      setExpanded(null);
      return;
    }
    setExpanded(convId);
    if (!messages[convId]) {
      setMsgsLoading((p) => ({ ...p, [convId]: true }));
      try {
        const res = await api.get(`/dashboard/sessions/${sessionId}/conversations/${convId}`);
        const msgs = res.data.messages || [];
        setMessages((p) => ({ ...p, [convId]: msgs }));
      } catch {
        setMessages((p) => ({ ...p, [convId]: [] }));
      } finally {
        setMsgsLoading((p) => ({ ...p, [convId]: false }));
      }
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 size={20} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      </div>
    );
  }

  if (conversations.length === 0) {
    return (
      <div className="text-center py-8">
        <MessageSquare
          size={24}
          className="mx-auto mb-2"
          style={{ color: 'var(--neo-text-muted)', opacity: 0.5 }}
        />
        <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
          No conversations yet
        </p>
        <p className="text-xs mt-1" style={{ color: 'var(--neo-text-muted)', opacity: 0.6 }}>
          Conversations appear when agents interact with this session
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {conversations.map((c) => {
        const convId = c.conversation_id || c.id;
        const isOpen = expanded === convId;

        return (
          <div key={convId}>
            <button
              onClick={() => toggleConvo(convId)}
              className="w-full flex items-center justify-between px-3 py-2.5 rounded-lg transition hover:opacity-90"
              style={{ background: 'var(--neo-surface)' }}
            >
              <div className="flex items-center gap-2 min-w-0">
                <MessageSquare size={14} style={{ color: 'var(--neo-cyan)' }} />
                <span className="text-xs font-medium truncate" style={{ color: 'var(--neo-text)' }}>
                  {c.title || `Thread ${(convId || '').slice(0, 8)}`}
                </span>
                {c.message_count != null && (
                  <span
                    className="px-1.5 py-0.5 rounded text-xs"
                    style={{ background: 'rgba(0,210,255,0.1)', color: 'var(--neo-cyan)' }}
                  >
                    {c.message_count} msgs
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                {c.created_at && (
                  <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                    {new Date(c.created_at).toLocaleDateString()}
                  </span>
                )}
                {isOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              </div>
            </button>

            {isOpen && (
              <div
                className="mx-2 p-3 rounded-b-lg -mt-0.5 space-y-2"
                style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', borderTop: 'none' }}
              >
                {msgsLoading[convId] ? (
                  <div className="flex justify-center py-4">
                    <Loader2 size={14} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
                  </div>
                ) : (messages[convId] || []).length === 0 ? (
                  <p className="text-xs text-center py-2" style={{ color: 'var(--neo-text-muted)' }}>
                    No messages
                  </p>
                ) : (
                  (messages[convId] || []).map((msg, i) => {
                    const style = ROLE_STYLES[msg.role] || ROLE_STYLES.agent;
                    const Icon = style.icon;
                    return (
                      <div key={msg.message_id || i} className="flex gap-2">
                        <div
                          className="w-6 h-6 rounded flex items-center justify-center shrink-0 mt-0.5"
                          style={{ background: `${style.color}15` }}
                        >
                          <Icon size={12} style={{ color: style.color }} />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-0.5">
                            <span className="text-xs font-medium" style={{ color: style.color }}>
                              {msg.agent_id || style.label}
                            </span>
                            {msg.created_at && (
                              <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                                {new Date(msg.created_at).toLocaleTimeString()}
                              </span>
                            )}
                          </div>
                          <p
                            className="text-xs whitespace-pre-wrap"
                            style={{ color: 'var(--neo-text)', lineHeight: 1.5 }}
                          >
                            {msg.content}
                          </p>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

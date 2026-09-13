import React, { useState, useEffect, useCallback } from 'react';
import { MessageSquare, Send, Loader2, CheckCircle, X, CornerDownRight } from 'lucide-react';
import api from '../lib/api';

function fmtTime(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  const mo = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getUTCMonth()];
  return `${mo} ${d.getUTCDate()}, ${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`;
}

export default function CommentThread({ nodeId, compact = false }) {
  const [comments, setComments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [newComment, setNewComment] = useState('');
  const [sending, setSending] = useState(false);
  const [replyTo, setReplyTo] = useState(null);

  const fetchComments = useCallback(() => {
    if (!nodeId) return;
    api.get(`/dashboard/comments/${nodeId}`)
      .then(r => setComments(r.data.comments || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [nodeId]);

  useEffect(() => { fetchComments(); }, [fetchComments]);

  const handleSend = async () => {
    if (!newComment.trim()) return;
    setSending(true);
    try {
      await api.post(`/dashboard/comments/${nodeId}`, {
        content: newComment.trim(),
        parent_id: replyTo,
      });
      setNewComment('');
      setReplyTo(null);
      fetchComments();
    } catch {} finally {
      setSending(false);
    }
  };

  const handleResolve = async (commentId, resolved) => {
    await api.patch(`/dashboard/comments/${nodeId}/${commentId}`, { resolved });
    fetchComments();
  };

  const handleDelete = async (commentId) => {
    await api.delete(`/dashboard/comments/${nodeId}/${commentId}`);
    fetchComments();
  };

  if (!nodeId) return null;

  const topLevel = comments.filter(c => !c.parent_id);
  const replies = comments.filter(c => c.parent_id);

  return (
    <div className={compact ? 'space-y-1.5' : 'space-y-2'}>
      <div className="flex items-center gap-1.5 mb-1">
        <MessageSquare size={12} style={{ color: 'var(--neo-text-muted)' }} />
        <span className="text-xs font-medium" style={{ color: 'var(--neo-text-muted)' }}>
          Comments {comments.length > 0 && `(${comments.length})`}
        </span>
      </div>

      {loading ? (
        <Loader2 size={12} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      ) : (
        <>
          {topLevel.map(c => (
            <div key={c.comment_id}>
              <div
                className="rounded-lg px-2.5 py-2"
                style={{
                  background: c.resolved ? 'rgba(34,197,94,0.05)' : 'var(--neo-bg)',
                  border: `1px solid ${c.resolved ? 'rgba(34,197,94,0.2)' : 'var(--neo-border)'}`,
                  opacity: c.resolved ? 0.7 : 1,
                }}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[10px] font-medium" style={{ color: 'var(--neo-text)' }}>
                    {c.author_name}
                  </span>
                  <div className="flex items-center gap-1">
                    <span className="text-[9px]" style={{ color: 'var(--neo-text-dim)' }}>{fmtTime(c.created_at)}</span>
                    {!c.resolved && (
                      <button onClick={() => handleResolve(c.comment_id, true)} title="Resolve"
                        className="p-0.5 rounded hover:opacity-70" style={{ color: '#22c55e' }}>
                        <CheckCircle size={10} />
                      </button>
                    )}
                    <button onClick={() => setReplyTo(c.comment_id)} title="Reply"
                      className="p-0.5 rounded hover:opacity-70" style={{ color: 'var(--neo-text-muted)' }}>
                      <CornerDownRight size={10} />
                    </button>
                    <button onClick={() => handleDelete(c.comment_id)} title="Delete"
                      className="p-0.5 rounded hover:opacity-70" style={{ color: 'var(--neo-text-dim)' }}>
                      <X size={10} />
                    </button>
                  </div>
                </div>
                <p className="text-xs leading-relaxed" style={{ color: 'var(--neo-text)', textDecoration: c.resolved ? 'line-through' : 'none' }}>
                  {c.content}
                </p>
              </div>
              {/* Replies */}
              {replies.filter(r => r.parent_id === c.comment_id).map(r => (
                <div key={r.comment_id} className="ml-4 mt-1 rounded-lg px-2 py-1.5"
                  style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)' }}>
                  <div className="flex items-center gap-1 mb-0.5">
                    <CornerDownRight size={8} style={{ color: 'var(--neo-text-dim)' }} />
                    <span className="text-[10px] font-medium" style={{ color: 'var(--neo-text)' }}>{r.author_name}</span>
                    <span className="text-[9px]" style={{ color: 'var(--neo-text-dim)' }}>{fmtTime(r.created_at)}</span>
                  </div>
                  <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>{r.content}</p>
                </div>
              ))}
            </div>
          ))}
        </>
      )}

      {/* New comment input */}
      <div className="flex gap-1">
        {replyTo && (
          <button onClick={() => setReplyTo(null)} className="text-[9px] px-1 shrink-0" style={{ color: 'var(--neo-blue)' }}>
            replying ×
          </button>
        )}
        <input
          value={newComment}
          onChange={e => setNewComment(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleSend()}
          placeholder={replyTo ? 'Reply...' : 'Add a comment...'}
          className="flex-1 px-2 py-1 rounded text-xs outline-none"
          style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
        />
        <button onClick={handleSend} disabled={sending || !newComment.trim()}
          className="p-1 rounded disabled:opacity-30" style={{ color: 'var(--neo-blue)' }}>
          {sending ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />}
        </button>
      </div>
    </div>
  );
}

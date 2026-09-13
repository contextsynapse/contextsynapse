import React, { useState, useEffect, useRef } from 'react';
import { Send, Loader2, Database, BookOpen } from 'lucide-react';
import api from '../../lib/api';

export default function RAGTab({ graphName }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Reset chat when graph changes
  useEffect(() => {
    setMessages([]);
  }, [graphName]);

  const handleSend = async (e) => {
    e?.preventDefault();
    if (!input.trim() || loading) return;

    const question = input.trim();
    setInput('');
    setMessages((prev) => [...prev, { role: 'user', content: question }]);
    setLoading(true);

    try {
      const res = await api.post('/dashboard/rag', {
        question,
        graph: graphName,
      });
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: res.data.answer || 'No answer could be generated.',
          sources: res.data.sources || [],
        },
      ]);
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: 'Sorry, I encountered an error processing your question.', sources: [] },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="h-full flex flex-col" style={{ minHeight: 400 }}>
      {/* Chat area */}
      <div
        className="flex-1 overflow-auto rounded-xl p-4 mb-4 space-y-4"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
      >
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center py-12">
            <BookOpen size={36} className="mb-3" style={{ color: 'var(--neo-text-muted)' }} />
            <p className="font-medium text-sm" style={{ color: 'var(--neo-text)' }}>
              Ask a question about your data
            </p>
            <p className="text-xs mt-1 max-w-md" style={{ color: 'var(--neo-text-muted)' }}>
              Answers are grounded in your graph with source citations.
            </p>
            <div className="flex flex-wrap gap-2 mt-4 max-w-md justify-center">
              {[
                'What entities are in my graph?',
                'Summarize the relationships',
                'What do we know about the latest project?',
              ].map((q) => (
                <button
                  key={q}
                  onClick={() => setInput(q)}
                  className="px-3 py-1.5 rounded-lg text-xs transition hover:opacity-80"
                  style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className="max-w-[80%] rounded-xl px-4 py-3"
              style={{
                background: msg.role === 'user' ? 'var(--neo-blue)' : 'var(--neo-bg)',
                color: msg.role === 'user' ? '#fff' : 'var(--neo-text)',
              }}
            >
              <p className="text-sm whitespace-pre-wrap">{msg.content}</p>
              {msg.sources && msg.sources.length > 0 && (
                <div className="mt-3 pt-2 border-t" style={{ borderColor: 'var(--neo-border)' }}>
                  <div className="text-xs font-medium mb-1" style={{ color: 'var(--neo-text-muted)' }}>
                    <Database size={12} className="inline mr-1" /> Sources
                  </div>
                  <div className="space-y-1">
                    {msg.sources.map((src, j) => (
                      <div key={j} className="text-xs px-2 py-1 rounded" style={{ background: 'var(--neo-surface)', color: 'var(--neo-text-muted)' }}>
                        <span className="font-medium" style={{ color: 'var(--neo-blue)' }}>{src.node_type || 'Node'}</span>
                        {' — '}{src.label || src.name || src.node_id}
                        {src.score !== undefined && <span className="ml-1 font-mono">({(src.score * 100).toFixed(0)}%)</span>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="rounded-xl px-4 py-3" style={{ background: 'var(--neo-bg)' }}>
              <Loader2 size={16} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <form onSubmit={handleSend} className="flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask a question about your data..."
          className="flex-1 px-4 py-3 rounded-xl text-sm outline-none"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          disabled={loading}
        />
        <button
          type="submit"
          disabled={loading || !input.trim()}
          className="px-4 py-3 rounded-xl transition hover:opacity-90 disabled:opacity-50"
          style={{ background: 'var(--neo-blue)', color: '#fff' }}
        >
          <Send size={18} />
        </button>
      </form>
    </div>
  );
}

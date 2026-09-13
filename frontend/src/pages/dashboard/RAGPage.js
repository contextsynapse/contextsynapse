import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Send, Loader2, Database, BookOpen } from 'lucide-react';
import api from '../../lib/api';

export default function RAGPage() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [graphs, setGraphs] = useState([]);
  const [selectedGraph, setSelectedGraph] = useState('');
  const messagesEndRef = useRef(null);

  const fetchGraphs = useCallback(() => {
    api.get('/dashboard/graphs')
      .then((res) => {
        const g = res.data.graphs || [];
        setGraphs(g);
        if (g.length > 0 && !selectedGraph) setSelectedGraph(g[0].name);
      })
      .catch(() => {});
  }, [selectedGraph]);

  useEffect(() => { fetchGraphs(); }, [fetchGraphs]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

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
        graph: selectedGraph,
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
    <div className="max-w-3xl h-full flex flex-col" style={{ minHeight: 'calc(100vh - 160px)' }}>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-xl font-bold" style={{ color: 'var(--neo-text)' }}>RAG Playground</h1>
          <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
            Ask questions — answers are grounded in your graph data
          </p>
        </div>
        <select
          value={selectedGraph}
          onChange={(e) => setSelectedGraph(e.target.value)}
          className="px-3 py-1.5 rounded-lg text-sm outline-none"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
        >
          <option value="">All graphs</option>
          {graphs.map((g) => (
            <option key={g.name} value={g.display_name || g.name}>{g.display_name || g.name}</option>
          ))}
        </select>
      </div>

      {/* Chat area */}
      <div
        className="flex-1 overflow-auto rounded-xl p-4 mb-4 space-y-4"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
      >
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center py-16">
            <BookOpen size={40} className="mb-3" style={{ color: 'var(--neo-text-muted)' }} />
            <p className="font-medium" style={{ color: 'var(--neo-text)' }}>
              Ask a question about your data
            </p>
            <p className="text-sm mt-1 max-w-md" style={{ color: 'var(--neo-text-muted)' }}>
              The RAG engine will retrieve relevant nodes from your graph and generate a grounded answer with citations.
            </p>
            <div className="flex flex-wrap gap-2 mt-4 max-w-md justify-center">
              {[
                'What entities are in my graph?',
                'Summarize the relationships between people',
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
              <p className="text-sm whitespace-pre-wrap">{typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content)}</p>

              {/* Source citations */}
              {msg.sources && msg.sources.length > 0 && (
                <div className="mt-3 pt-2 border-t" style={{ borderColor: 'var(--neo-border)' }}>
                  <div className="text-xs font-medium mb-1" style={{ color: 'var(--neo-text-muted)' }}>
                    <Database size={12} className="inline mr-1" /> Sources
                  </div>
                  <div className="space-y-1">
                    {msg.sources.map((src, j) => (
                      <div
                        key={j}
                        className="text-xs px-2 py-1 rounded"
                        style={{ background: 'var(--neo-surface)', color: 'var(--neo-text-muted)' }}
                      >
                        <span className="font-medium" style={{ color: 'var(--neo-blue)' }}>
                          {src.node_type || 'Node'}
                        </span>
                        {' — '}
                        {src.label || src.name || src.node_id}
                        {src.score !== undefined && (
                          <span className="ml-1 font-mono">({(src.score * 100).toFixed(0)}%)</span>
                        )}
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

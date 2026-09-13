import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Lightbulb, Database, Code2, ArrowRight, ArrowLeft, Check, Loader2, Copy,
} from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../lib/api';

const USE_CASES = [
  { id: 'ai_agents', label: 'AI Agent Memory', desc: 'Shared knowledge graph for multi-agent systems' },
  { id: 'knowledge_base', label: 'Knowledge Base', desc: 'Store and query structured knowledge' },
  { id: 'rag', label: 'RAG Pipeline', desc: 'Retrieval-augmented generation with graph context' },
  { id: 'data_modeling', label: 'Data Modeling', desc: 'Model relationships between entities' },
  { id: 'other', label: 'Something Else', desc: 'Explore what ContextSynapse can do' },
];

export default function OnboardingPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [useCase, setUseCase] = useState('');
  const [graphName, setGraphName] = useState('my_graph');
  const [creating, setCreating] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleCreateGraph = async () => {
    if (!graphName.trim()) return;
    setCreating(true);
    try {
      await api.post('/dashboard/graphs', { name: graphName.trim() });
      setStep(2);
    } catch {
      // handled by interceptor
    } finally {
      setCreating(false);
    }
  };

  const copySnippet = () => {
    const snippet = `from contextsynapse import ContextSynapse
from contextsynapse.aiql import AIQLExecutor

db = ContextSynapse()
ex = AIQLExecutor(contextsynapse=db)

ex.execute('CREATE GRAPH ${graphName}')
ex.execute('USE GRAPH ${graphName}')
ex.execute('CREATE NODE Person {name: "Alice", role: "engineer"}')
ex.execute('CREATE NODE Project {name: "ContextSynapse", status: "active"}')
ex.execute('CREATE EDGE WORKS_ON FROM Person TO Project WHERE Person.name = "Alice" AND Project.name = "ContextSynapse"')

results = ex.execute('SELECT * FROM Person')
print(results)`;
    navigator.clipboard.writeText(snippet);
    setCopied(true);
    toast.success('Copied to clipboard');
    setTimeout(() => setCopied(false), 2000);
  };

  const steps = [
    // Step 0: Use case
    {
      title: 'What are you building?',
      subtitle: 'This helps us tailor your experience',
      content: (
        <div className="space-y-2">
          {USE_CASES.map((uc) => (
            <button
              key={uc.id}
              onClick={() => setUseCase(uc.id)}
              className="w-full text-left p-4 rounded-xl transition"
              style={{
                background: useCase === uc.id ? 'rgba(76,142,218,0.15)' : 'var(--neo-bg)',
                border: useCase === uc.id ? '2px solid var(--neo-blue)' : '1px solid var(--neo-border)',
              }}
            >
              <div className="font-medium text-sm" style={{ color: 'var(--neo-text)' }}>{uc.label}</div>
              <div className="text-xs mt-0.5" style={{ color: 'var(--neo-text-muted)' }}>{uc.desc}</div>
            </button>
          ))}
        </div>
      ),
      canNext: !!useCase,
    },
    // Step 1: Create graph
    {
      title: 'Create your first graph',
      subtitle: 'This is where your data lives',
      content: (
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1" style={{ color: 'var(--neo-text-muted)' }}>
              Graph name
            </label>
            <input
              value={graphName}
              onChange={(e) => setGraphName(e.target.value)}
              className="w-full px-4 py-2.5 rounded-lg text-sm outline-none"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
              placeholder="e.g. knowledge_base"
            />
          </div>
          <button
            onClick={handleCreateGraph}
            disabled={creating || !graphName.trim()}
            className="flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            {creating ? <Loader2 size={16} className="animate-spin" /> : <Database size={16} />}
            {creating ? 'Creating...' : 'Create Graph'}
          </button>
        </div>
      ),
      canNext: false, // handled by create button
    },
    // Step 2: Connect agent
    {
      title: 'Connect your first agent',
      subtitle: 'Use this code snippet to get started',
      content: (
        <div>
          <div className="relative">
            <pre
              className="text-xs p-4 rounded-xl overflow-x-auto"
              style={{ background: 'var(--neo-bg)', color: 'var(--neo-text-muted)', lineHeight: 1.6 }}
            >
{`from contextsynapse import ContextSynapse
from contextsynapse.aiql import AIQLExecutor

db = ContextSynapse()
ex = AIQLExecutor(contextsynapse=db)

ex.execute('CREATE GRAPH ${graphName}')
ex.execute('USE GRAPH ${graphName}')
ex.execute('CREATE NODE Person {name: "Alice", role: "engineer"}')
ex.execute('CREATE NODE Project {name: "ContextSynapse", status: "active"}')
ex.execute('CREATE EDGE WORKS_ON FROM Person TO Project \\
  WHERE Person.name = "Alice" AND Project.name = "ContextSynapse"')

results = ex.execute('SELECT * FROM Person')
print(results)`}
            </pre>
            <button
              onClick={copySnippet}
              className="absolute top-3 right-3 p-1.5 rounded-lg transition hover:bg-neo-surface-light"
              style={{ color: copied ? 'var(--neo-green)' : 'var(--neo-text-muted)' }}
            >
              {copied ? <Check size={14} /> : <Copy size={14} />}
            </button>
          </div>
        </div>
      ),
      canNext: true,
    },
  ];

  const current = steps[step];
  const icons = [Lightbulb, Database, Code2];

  return (
    <div className="min-h-screen flex items-center justify-center px-4" style={{ background: 'var(--neo-bg)' }}>
      <div className="w-full max-w-lg">
        {/* Progress */}
        <div className="flex items-center justify-center gap-2 mb-8">
          {steps.map((_, i) => {
            const Icon = icons[i];
            const active = i === step;
            const done = i < step;
            return (
              <React.Fragment key={i}>
                <div
                  className="flex items-center justify-center w-8 h-8 rounded-full transition"
                  style={{
                    background: done ? 'var(--neo-green)' : active ? 'var(--neo-blue)' : 'var(--neo-surface)',
                    border: active ? 'none' : '1px solid var(--neo-border)',
                    color: done || active ? '#fff' : 'var(--neo-text-muted)',
                  }}
                >
                  {done ? <Check size={14} /> : <Icon size={14} />}
                </div>
                {i < steps.length - 1 && (
                  <div
                    className="w-12 h-0.5 rounded"
                    style={{ background: i < step ? 'var(--neo-green)' : 'var(--neo-border)' }}
                  />
                )}
              </React.Fragment>
            );
          })}
        </div>

        {/* Card */}
        <div
          className="rounded-2xl p-8 shadow-xl"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        >
          <div className="mb-6">
            <h1 className="text-xl font-bold" style={{ color: 'var(--neo-text)' }}>{current.title}</h1>
            <p className="text-sm mt-1" style={{ color: 'var(--neo-text-muted)' }}>{current.subtitle}</p>
          </div>

          {current.content}

          {/* Navigation */}
          <div className="flex items-center justify-between mt-8">
            {step > 0 ? (
              <button
                onClick={() => setStep(step - 1)}
                className="flex items-center gap-1 text-sm transition hover:opacity-80"
                style={{ color: 'var(--neo-text-muted)' }}
              >
                <ArrowLeft size={14} /> Back
              </button>
            ) : (
              <button
                onClick={() => navigate('/dashboard')}
                className="text-sm transition hover:opacity-80"
                style={{ color: 'var(--neo-text-muted)' }}
              >
                Skip setup
              </button>
            )}

            {step < steps.length - 1 && current.canNext && (
              <button
                onClick={() => setStep(step + 1)}
                className="flex items-center gap-1 px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90"
                style={{ background: 'var(--neo-blue)', color: '#fff' }}
              >
                Next <ArrowRight size={14} />
              </button>
            )}

            {step === steps.length - 1 && (
              <button
                onClick={() => navigate('/dashboard')}
                className="flex items-center gap-1 px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90"
                style={{ background: 'var(--neo-green)', color: '#fff' }}
              >
                Go to Dashboard <ArrowRight size={14} />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

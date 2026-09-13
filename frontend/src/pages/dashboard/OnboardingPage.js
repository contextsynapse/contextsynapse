import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Upload, FileText, Link, Bot, ArrowRight, ArrowLeft,
  Check, Compass, Network, Sparkles, Play, Database,
} from 'lucide-react';
import api from '../../lib/api';

const STEPS = [
  { num: 1, title: 'Create a Context', icon: Database },
  { num: 2, title: 'Add Data', icon: FileText },
  { num: 3, title: 'Connect an Agent', icon: Bot },
  { num: 4, title: 'See Results', icon: Sparkles },
];

function StepIndicator({ current, completed }) {
  return (
    <div className="flex items-center gap-2 mb-8">
      {STEPS.map((s, i) => {
        const done = completed.includes(s.num);
        const active = s.num === current;
        return (
          <React.Fragment key={s.num}>
            {i > 0 && (
              <div
                className="flex-1 h-px"
                style={{ background: done ? 'var(--neo-green)' : 'var(--neo-border)' }}
              />
            )}
            <div className="flex items-center gap-2">
              <div
                className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold"
                style={{
                  background: done
                    ? 'var(--neo-green)'
                    : active
                    ? 'var(--neo-blue)'
                    : 'var(--neo-surface)',
                  color: done || active ? '#fff' : 'var(--neo-text-muted)',
                  border: !done && !active ? '1px solid var(--neo-border)' : 'none',
                }}
              >
                {done ? <Check size={14} /> : s.num}
              </div>
              <span
                className="text-xs hidden sm:inline"
                style={{ color: active ? 'var(--neo-text)' : 'var(--neo-text-muted)' }}
              >
                {s.title}
              </span>
            </div>
          </React.Fragment>
        );
      })}
    </div>
  );
}

function StepCreateContext({ onComplete }) {
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleCreate = async () => {
    if (!name.trim()) return;
    setLoading(true);
    setError('');
    try {
      const res = await api.post('/dashboard/contexts', {
        name: name.trim(),
        context_type: 'knowledge_base',
        description: 'Created via onboarding wizard',
        tags: ['onboarding'],
      });
      onComplete({ contextName: name.trim(), contextId: res.data?.context_id });
    } catch (err) {
      setError(err.userMessage || 'Failed to create context');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <h2 className="text-xl font-bold mb-2" style={{ color: 'var(--neo-text)' }}>
        Create a Context
      </h2>
      <p className="text-sm mb-6" style={{ color: 'var(--neo-text-muted)' }}>
        A context is a container for your data. It groups related information so agents can access
        exactly what they need. Think of it as a project workspace.
      </p>
      <div className="flex gap-3 items-start">
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
          placeholder="e.g. my-project, research-notes, customer-data"
          className="flex-1 px-3 py-2 rounded-lg text-sm outline-none"
          style={{
            background: 'var(--neo-bg)',
            border: '1px solid var(--neo-border)',
            color: 'var(--neo-text)',
          }}
          autoFocus
        />
        <button
          onClick={handleCreate}
          disabled={loading || !name.trim()}
          className="px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2"
          style={{
            background: 'var(--neo-blue)',
            color: '#fff',
            opacity: loading || !name.trim() ? 0.5 : 1,
          }}
        >
          {loading ? 'Creating...' : 'Create'}
          <Database size={14} />
        </button>
      </div>
      {error && (
        <p className="text-xs mt-2" style={{ color: '#ef4444' }}>{error}</p>
      )}
    </div>
  );
}

function StepAddData({ data, onComplete }) {
  const [mode, setMode] = useState(null);
  const [text, setText] = useState('');
  const [url, setUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [llmModel, setLlmModel] = useState('');

  // Auto-detect best available LLM model
  React.useEffect(() => {
    api.get('/dashboard/ingest/models').then(res => {
      const models = res.data?.llm_models || [];
      // Prefer groq (fast) → ollama (local) → openai → anthropic
      const preferred = models.find(m => m.startsWith('groq:'))
        || models.find(m => m.startsWith('ollama:'))
        || models.find(m => m.startsWith('openai:'))
        || models[0] || '';
      setLlmModel(preferred);
    }).catch(() => {});
  }, []);

  const contextId = data.contextId;
  const contextName = data.contextName;

  const handleText = async () => {
    if (!text.trim()) return;
    setLoading(true);
    setError('');
    try {
      await api.post(`/dashboard/contexts/${contextId}/ingest/text`, {
        text: text.trim(),
        title: contextName,
        llm_model: llmModel,
      });
      onComplete({ method: 'text' });
    } catch (err) {
      setError(err.userMessage || 'Failed to ingest text');
    } finally {
      setLoading(false);
    }
  };

  const handleFile = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setLoading(true);
    setError('');
    try {
      const form = new FormData();
      form.append('file', file);
      if (llmModel) form.append('llm_model', llmModel);
      await api.post(`/dashboard/contexts/${contextId}/ingest/file`, form);
      onComplete({ method: 'file' });
    } catch (err) {
      setError(err.userMessage || 'Failed to upload file');
    } finally {
      setLoading(false);
    }
  };

  const handleUrl = async () => {
    if (!url.trim()) return;
    setLoading(true);
    setError('');
    try {
      await api.post(`/dashboard/contexts/${contextId}/ingest/url`, {
        url: url.trim(),
        llm_model: llmModel,
      });
      onComplete({ method: 'url' });
    } catch (err) {
      setError(err.userMessage || 'Failed to crawl URL');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <h2 className="text-xl font-bold mb-2" style={{ color: 'var(--neo-text)' }}>
        Add Data
      </h2>
      <p className="text-sm mb-1" style={{ color: 'var(--neo-text-muted)' }}>
        Feed data into <span className="font-mono font-medium" style={{ color: 'var(--neo-blue)' }}>{contextName}</span>.
        Choose one method to get started.
      </p>
      <p className="text-xs mb-6" style={{ color: 'var(--neo-text-dim)' }}>
        You can always add more data later from the Contexts page.
      </p>

      {!mode && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {[
            { key: 'file', icon: Upload, title: 'Upload File', desc: 'PDF, TXT, MD, JSON, CSV' },
            { key: 'text', icon: FileText, title: 'Paste Text', desc: 'Paste content directly' },
            { key: 'url', icon: Link, title: 'Enter URL', desc: 'Crawl a web page' },
          ].map((opt) => (
            <button
              key={opt.key}
              onClick={() => setMode(opt.key)}
              className="p-4 rounded-lg text-left flex flex-col gap-2 transition-colors"
              style={{
                background: 'var(--neo-bg)',
                border: '1px solid var(--neo-border)',
                color: 'var(--neo-text)',
              }}
            >
              <opt.icon size={20} style={{ color: 'var(--neo-blue)' }} />
              <span className="text-sm font-medium">{opt.title}</span>
              <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>{opt.desc}</span>
            </button>
          ))}
        </div>
      )}

      {mode === 'text' && (
        <div className="flex flex-col gap-3">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Paste your content here..."
            rows={6}
            className="w-full px-3 py-2 rounded-lg text-sm outline-none resize-y"
            style={{
              background: 'var(--neo-bg)',
              border: '1px solid var(--neo-border)',
              color: 'var(--neo-text)',
            }}
            autoFocus
          />
          <div className="flex gap-2">
            <button
              onClick={() => setMode(null)}
              className="px-3 py-2 rounded-lg text-sm"
              style={{ color: 'var(--neo-text-muted)', background: 'var(--neo-surface)' }}
            >
              Back
            </button>
            <button
              onClick={handleText}
              disabled={loading || !text.trim()}
              className="px-4 py-2 rounded-lg text-sm font-medium"
              style={{ background: 'var(--neo-blue)', color: '#fff', opacity: loading || !text.trim() ? 0.5 : 1 }}
            >
              {loading ? 'Ingesting...' : 'Ingest Text'}
            </button>
          </div>
        </div>
      )}

      {mode === 'file' && (
        <div className="flex flex-col gap-3">
          <label
            className="p-8 rounded-lg text-center cursor-pointer flex flex-col items-center gap-2"
            style={{ background: 'var(--neo-bg)', border: '2px dashed var(--neo-border)' }}
          >
            <Upload size={24} style={{ color: 'var(--neo-text-muted)' }} />
            <span className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
              {loading ? 'Uploading...' : 'Click to select a file'}
            </span>
            <input type="file" className="hidden" onChange={handleFile} disabled={loading} />
          </label>
          <button
            onClick={() => setMode(null)}
            className="px-3 py-2 rounded-lg text-sm self-start"
            style={{ color: 'var(--neo-text-muted)', background: 'var(--neo-surface)' }}
          >
            Back
          </button>
        </div>
      )}

      {mode === 'url' && (
        <div className="flex flex-col gap-3">
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleUrl()}
            placeholder="https://example.com/article"
            className="w-full px-3 py-2 rounded-lg text-sm outline-none"
            style={{
              background: 'var(--neo-bg)',
              border: '1px solid var(--neo-border)',
              color: 'var(--neo-text)',
            }}
            autoFocus
          />
          <div className="flex gap-2">
            <button
              onClick={() => setMode(null)}
              className="px-3 py-2 rounded-lg text-sm"
              style={{ color: 'var(--neo-text-muted)', background: 'var(--neo-surface)' }}
            >
              Back
            </button>
            <button
              onClick={handleUrl}
              disabled={loading || !url.trim()}
              className="px-4 py-2 rounded-lg text-sm font-medium"
              style={{ background: 'var(--neo-blue)', color: '#fff', opacity: loading || !url.trim() ? 0.5 : 1 }}
            >
              {loading ? 'Crawling...' : 'Crawl URL'}
            </button>
          </div>
        </div>
      )}

      {error && (
        <p className="text-xs mt-2" style={{ color: '#ef4444' }}>{error}</p>
      )}
    </div>
  );
}

function StepConnectAgent({ onComplete }) {
  const [agentName, setAgentName] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [apiKey, setApiKey] = useState('');

  const handleRegister = async () => {
    if (!agentName.trim()) return;
    setLoading(true);
    setError('');
    try {
      const res = await api.post('/dashboard/agents', { name: agentName.trim() });
      const key = res.data?.api_key || res.data?.token || res.data?.key || '';
      setApiKey(key);
      onComplete({ agentName: agentName.trim(), apiKey: key });
    } catch (err) {
      setError(err.userMessage || 'Failed to register agent');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <h2 className="text-xl font-bold mb-2" style={{ color: 'var(--neo-text)' }}>
        Connect an Agent
      </h2>
      <p className="text-sm mb-6" style={{ color: 'var(--neo-text-muted)' }}>
        Register an AI agent that will use your context. Each agent gets its own API key
        for authenticated access.
      </p>

      {!apiKey ? (
        <div className="flex gap-3 items-start">
          <input
            type="text"
            value={agentName}
            onChange={(e) => setAgentName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleRegister()}
            placeholder="e.g. research-bot, code-assistant"
            className="flex-1 px-3 py-2 rounded-lg text-sm outline-none"
            style={{
              background: 'var(--neo-bg)',
              border: '1px solid var(--neo-border)',
              color: 'var(--neo-text)',
            }}
            autoFocus
          />
          <button
            onClick={handleRegister}
            disabled={loading || !agentName.trim()}
            className="px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2"
            style={{
              background: 'var(--neo-blue)',
              color: '#fff',
              opacity: loading || !agentName.trim() ? 0.5 : 1,
            }}
          >
            {loading ? 'Registering...' : 'Register'}
            <Bot size={14} />
          </button>
        </div>
      ) : (
        <div
          className="p-4 rounded-lg"
          style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)' }}
        >
          <p className="text-sm font-medium mb-2" style={{ color: 'var(--neo-green)' }}>
            <Check size={14} className="inline mr-1" />
            Agent registered successfully
          </p>
          <p className="text-xs mb-2" style={{ color: 'var(--neo-text-muted)' }}>
            Your API key (save it now, it won't be shown again):
          </p>
          <code
            className="block text-xs p-2 rounded break-all select-all"
            style={{ background: 'var(--neo-surface)', color: 'var(--neo-text)' }}
          >
            {apiKey}
          </code>
        </div>
      )}

      {error && (
        <p className="text-xs mt-2" style={{ color: '#ef4444' }}>{error}</p>
      )}
    </div>
  );
}

function StepResults({ navigate }) {
  const links = [
    { to: '/dashboard/explorer', icon: Network, label: 'Graph Explorer', desc: 'Visualize your knowledge graph' },
    { to: '/dashboard/cognition', icon: Sparkles, label: 'Cognition', desc: 'AI-powered insights from your data' },
    { to: '/dashboard/playground', icon: Play, label: 'Playground', desc: 'Query your data interactively' },
    { to: '/dashboard/contexts', icon: Database, label: 'Contexts', desc: 'Manage contexts and ingestion' },
  ];

  return (
    <div>
      <h2 className="text-xl font-bold mb-2" style={{ color: 'var(--neo-text)' }}>
        You're All Set!
      </h2>
      <p className="text-sm mb-6" style={{ color: 'var(--neo-text-muted)' }}>
        Your context is created, data is ingested, and an agent is connected.
        Explore what you can do next:
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {links.map((l) => (
          <button
            key={l.to}
            onClick={() => navigate(l.to)}
            className="p-4 rounded-lg text-left flex items-start gap-3 transition-colors"
            style={{
              background: 'var(--neo-bg)',
              border: '1px solid var(--neo-border)',
              color: 'var(--neo-text)',
            }}
          >
            <l.icon size={20} style={{ color: 'var(--neo-blue)', flexShrink: 0, marginTop: 2 }} />
            <div>
              <span className="text-sm font-medium block">{l.label}</span>
              <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>{l.desc}</span>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

export default function OnboardingPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  const [completed, setCompleted] = useState([]);
  const [data, setData] = useState({});

  const markComplete = (stepNum, stepData = {}) => {
    setCompleted((prev) => [...new Set([...prev, stepNum])]);
    setData((prev) => ({ ...prev, ...stepData }));
    if (stepNum < 4) {
      setTimeout(() => setStep(stepNum + 1), 400);
    }
  };

  return (
    <div className="max-w-2xl mx-auto">
      <div className="flex items-center gap-3 mb-6">
        <Compass size={24} style={{ color: 'var(--neo-blue)' }} />
        <div>
          <h1 className="text-2xl font-bold" style={{ color: 'var(--neo-text)' }}>
            Get Started
          </h1>
          <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
            Step {step} of 4
          </p>
        </div>
      </div>

      <StepIndicator current={step} completed={completed} />

      <div
        className="p-6 rounded-xl"
        style={{
          background: 'var(--neo-surface)',
          border: '1px solid var(--neo-border)',
        }}
      >
        {step === 1 && <StepCreateContext onComplete={(d) => markComplete(1, d)} />}
        {step === 2 && <StepAddData data={data} onComplete={(d) => markComplete(2, d)} />}
        {step === 3 && <StepConnectAgent onComplete={(d) => markComplete(3, d)} />}
        {step === 4 && <StepResults navigate={navigate} />}

        {/* Navigation */}
        <div className="flex items-center justify-between mt-6 pt-4" style={{ borderTop: '1px solid var(--neo-border)' }}>
          <button
            onClick={() => setStep(Math.max(1, step - 1))}
            disabled={step === 1}
            className="flex items-center gap-1 text-sm px-3 py-1.5 rounded-lg"
            style={{
              color: step === 1 ? 'var(--neo-text-dim)' : 'var(--neo-text-muted)',
              background: 'transparent',
              opacity: step === 1 ? 0.4 : 1,
            }}
          >
            <ArrowLeft size={14} /> Back
          </button>

          {step < 4 && completed.includes(step) && (
            <button
              onClick={() => setStep(step + 1)}
              className="flex items-center gap-1 text-sm px-3 py-1.5 rounded-lg font-medium"
              style={{ color: 'var(--neo-blue)' }}
            >
              Next <ArrowRight size={14} />
            </button>
          )}

          {step < 4 && !completed.includes(step) && (
            <button
              onClick={() => setStep(step + 1)}
              className="text-xs px-3 py-1.5 rounded-lg"
              style={{ color: 'var(--neo-text-dim)' }}
            >
              Skip
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

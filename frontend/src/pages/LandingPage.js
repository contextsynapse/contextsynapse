import React from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import {
  Database, Cpu, Search, Shield, Zap, Globe,
  ArrowRight, Code2, Layers, Lock, Users, Bot,
} from 'lucide-react';

const features = [
  { icon: Database, title: 'Context as a Service', desc: 'Ingest documents, code, rules, and data into typed knowledge graphs. Schema-driven extraction creates structured, queryable knowledge — not just chunks.' },
  { icon: Cpu, title: 'Multi-Agent Collaboration', desc: 'Multiple AI agents share one brain. They plan together, execute in parallel, and see each other\'s work in real-time through a shared execution graph.' },
  { icon: Bot, title: 'Bring Your Own Model', desc: 'Works with Claude, GPT, Codex, Groq, Ollama, and any LLM. 30+ tools available via MCP, OpenAI, LangChain, CrewAI, AutoGen, and more frameworks.' },
  { icon: Layers, title: 'Scoped Execution', desc: 'Attach knowledge contexts to execution boundaries. Agents get exactly the context they need — versioned, tracked, and reproducible.' },
  { icon: Globe, title: 'Integrate Everything', desc: 'Connect Git, Jira, databases, Slack, Confluence, and more. Configure once, test, and use across all your agent workflows.' },
  { icon: Shield, title: 'Enterprise Ready', desc: 'Multi-tenant isolation, scoped API keys, usage metering, role-based access, and Stripe-ready billing. Self-host or use as SaaS.' },
];

export default function LandingPage() {
  const { isAuthenticated } = useAuth();

  return (
    <div className="min-h-screen" style={{ background: 'var(--neo-bg)', color: 'var(--neo-text)' }}>
      {/* Hero */}
      <section className="relative overflow-hidden">
        <div className="max-w-5xl mx-auto px-6 pt-24 pb-20 text-center">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-medium mb-6"
               style={{ background: 'rgba(76,142,218,0.15)', color: 'var(--neo-cyan)' }}>
            <Database size={14} /> Context platform for AI agents
          </div>

          <h1 className="text-4xl sm:text-5xl font-extrabold leading-tight mb-6">
            One context layer.
            <br />
            <span style={{ color: 'var(--neo-blue)' }}>Every agent. Any model.</span>
          </h1>

          <p className="text-lg max-w-2xl mx-auto mb-10" style={{ color: 'var(--neo-text-muted)' }}>
            ContextSynapse gives your AI agents a shared knowledge graph to read from,
            write to, and collaborate through. Ingest any data, connect any tool,
            bring any model — your agents work as a team.
          </p>

          <div className="flex items-center justify-center gap-4 flex-wrap">
            {isAuthenticated ? (
              <Link
                to="/dashboard"
                className="inline-flex items-center gap-2 px-6 py-3 rounded-lg font-semibold text-sm transition hover:opacity-90"
                style={{ background: 'var(--neo-blue)', color: '#fff' }}
              >
                Go to Dashboard <ArrowRight size={16} />
              </Link>
            ) : (
              <>
                <Link
                  to="/signup"
                  className="inline-flex items-center gap-2 px-6 py-3 rounded-lg font-semibold text-sm transition hover:opacity-90"
                  style={{ background: 'var(--neo-blue)', color: '#fff' }}
                >
                  Get Started Free <ArrowRight size={16} />
                </Link>
                <Link
                  to="/dashboard/graphs"
                  className="inline-flex items-center gap-2 px-6 py-3 rounded-lg font-semibold text-sm transition hover:opacity-80"
                  style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
                >
                  <Code2 size={16} /> Try the Explorer
                </Link>
              </>
            )}
          </div>
        </div>
      </section>

      {/* Features grid */}
      <section className="max-w-5xl mx-auto px-6 pb-24">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
          {features.map((f) => (
            <div
              key={f.title}
              className="rounded-xl p-6 transition hover:scale-[1.02]"
              style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
            >
              <f.icon size={28} style={{ color: 'var(--neo-cyan)' }} className="mb-4" />
              <h3 className="font-semibold text-base mb-2">{f.title}</h3>
              <p className="text-sm leading-relaxed" style={{ color: 'var(--neo-text-muted)' }}>
                {f.desc}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* How it works */}
      <section className="max-w-4xl mx-auto px-6 pb-20">
        <h2 className="text-2xl font-bold text-center mb-10">How it works</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-8 text-center">
          <div>
            <div className="w-12 h-12 rounded-full mx-auto mb-4 flex items-center justify-center text-lg font-bold"
                 style={{ background: 'rgba(76,142,218,0.15)', color: 'var(--neo-blue)' }}>1</div>
            <h3 className="font-semibold mb-2">Ingest Knowledge</h3>
            <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
              Upload documents, connect databases, or link a Git repo.
              AI extracts structured entities and relationships automatically.
            </p>
          </div>
          <div>
            <div className="w-12 h-12 rounded-full mx-auto mb-4 flex items-center justify-center text-lg font-bold"
                 style={{ background: 'rgba(76,142,218,0.15)', color: 'var(--neo-blue)' }}>2</div>
            <h3 className="font-semibold mb-2">Connect Agents</h3>
            <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
              Add agents from any framework — Claude, Codex, LangChain, or your own.
              Set a goal, pick integrations, and hit run.
            </p>
          </div>
          <div>
            <div className="w-12 h-12 rounded-full mx-auto mb-4 flex items-center justify-center text-lg font-bold"
                 style={{ background: 'rgba(76,142,218,0.15)', color: 'var(--neo-blue)' }}>3</div>
            <h3 className="font-semibold mb-2">Ship Together</h3>
            <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
              Agents plan, execute, and deliver in parallel. Code goes to your repo.
              Every decision, task, and artifact is versioned.
            </p>
          </div>
        </div>
      </section>

      {/* CTA footer */}
      <section className="border-t py-16 text-center" style={{ borderColor: 'var(--neo-border)' }}>
        <h2 className="text-2xl font-bold mb-3">Ready to build with your agents?</h2>
        <p className="text-sm mb-6" style={{ color: 'var(--neo-text-muted)' }}>
          Start free. Self-host or use as SaaS. No vendor lock-in.
        </p>
        {!isAuthenticated && (
          <Link
            to="/signup"
            className="inline-flex items-center gap-2 px-6 py-3 rounded-lg font-semibold text-sm transition hover:opacity-90"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            Create Free Account <ArrowRight size={16} />
          </Link>
        )}
      </section>
    </div>
  );
}

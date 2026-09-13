import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Check, Zap, ArrowRight, Loader2 } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import api from '../lib/api';

export default function PricingPage() {
  const { isAuthenticated } = useAuth();
  const [plans, setPlans] = useState(null);

  useEffect(() => {
    api.get('/billing/plans')
      .then(res => setPlans(res.data.plans || []))
      .catch(() => {
        // Fallback plans if backend unavailable
        setPlans([
          { id: 'free', name: 'Free', price_monthly: 0, max_graphs: 3, max_api_calls: 10000, max_agents: 5, max_nodes: 50000, features: ['core_db', 'aiql', 'context', 'rest_api', 'mcp'] },
          { id: 'pro', name: 'Pro', price_monthly: 49, max_graphs: 50, max_api_calls: 500000, max_agents: 50, max_nodes: 5000000, features: ['core_db', 'aiql', 'context', 'rest_api', 'mcp', 'hybrid_search', 'advanced_analytics', 'priority_support'] },
          { id: 'enterprise', name: 'Enterprise', price_monthly: null, max_graphs: -1, max_api_calls: -1, max_agents: -1, max_nodes: -1, features: ['core_db', 'aiql', 'context', 'rest_api', 'mcp', 'hybrid_search', 'advanced_analytics', 'priority_support', 'sso', 'webhooks', 'dedicated_support', 'sla'] },
        ]);
      });
  }, []);

  if (!plans) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--neo-bg)' }}>
        <Loader2 size={24} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      </div>
    );
  }

  return (
    <div className="min-h-screen py-20 px-6" style={{ background: 'var(--neo-bg)', color: 'var(--neo-text)' }}>
      <div className="max-w-5xl mx-auto">
        <div className="text-center mb-12">
          <h1 className="text-3xl font-extrabold mb-3">Simple, transparent pricing</h1>
          <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
            Start free. Scale as your agents grow.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {plans.map((plan) => {
            const isPro = plan.id === 'pro';
            return (
              <div
                key={plan.id}
                className="rounded-2xl p-6 relative"
                style={{
                  background: 'var(--neo-surface)',
                  border: isPro ? '2px solid var(--neo-blue)' : '1px solid var(--neo-border)',
                }}
              >
                {isPro && (
                  <div
                    className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-0.5 rounded-full text-xs font-bold"
                    style={{ background: 'var(--neo-blue)', color: '#fff' }}
                  >
                    Most Popular
                  </div>
                )}

                <h3 className="text-lg font-bold mb-1">{plan.name}</h3>

                <div className="mb-5">
                  {plan.price_monthly != null ? (
                    <>
                      <span className="text-3xl font-extrabold">${plan.price_monthly}</span>
                      <span className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>/mo</span>
                    </>
                  ) : (
                    <span className="text-xl font-bold" style={{ color: 'var(--neo-text-muted)' }}>
                      Custom
                    </span>
                  )}
                </div>

                {/* Limits */}
                <div className="space-y-2 mb-6 text-sm" style={{ color: 'var(--neo-text-muted)' }}>
                  <LimitRow label="Graphs" value={plan.max_graphs} />
                  <LimitRow label="API calls/mo" value={plan.max_api_calls} />
                  <LimitRow label="Agents" value={plan.max_agents} />
                  <LimitRow label="Nodes" value={plan.max_nodes} />
                </div>

                {/* Features */}
                <div className="space-y-1.5 mb-6">
                  {plan.features.map((f) => (
                    <div key={f} className="flex items-center gap-2 text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                      <Check size={13} style={{ color: 'var(--neo-green)' }} />
                      {f.replace(/_/g, ' ')}
                    </div>
                  ))}
                </div>

                {/* CTA */}
                {plan.id === 'free' && (
                  <Link
                    to={isAuthenticated ? '/dashboard' : '/signup'}
                    className="block text-center py-2.5 rounded-lg text-sm font-medium no-underline transition hover:opacity-90"
                    style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                  >
                    {isAuthenticated ? 'Go to Dashboard' : 'Get Started Free'}
                  </Link>
                )}
                {plan.id === 'pro' && (
                  <Link
                    to={isAuthenticated ? '/dashboard/billing' : '/signup'}
                    className="flex items-center justify-center gap-1.5 py-2.5 rounded-lg text-sm font-medium no-underline transition hover:opacity-90"
                    style={{ background: 'var(--neo-blue)', color: '#fff' }}
                  >
                    <Zap size={14} /> Upgrade to Pro
                  </Link>
                )}
                {plan.id === 'enterprise' && (
                  <a
                    href="mailto:hello@contextsynapse.com"
                    className="block text-center py-2.5 rounded-lg text-sm font-medium no-underline transition hover:opacity-90"
                    style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                  >
                    Contact Sales
                  </a>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function LimitRow({ label, value }) {
  return (
    <div className="flex justify-between">
      <span>{label}</span>
      <span className="font-medium" style={{ color: 'var(--neo-text)' }}>
        {value === -1 ? 'Unlimited' : value.toLocaleString()}
      </span>
    </div>
  );
}

/**
 * VerticalLoginPage — branded login for PMS and MF verticals.
 *
 * Routes:
 *   /pms/login → PMS-branded login → redirects to /pms after auth
 *   /mf/login  → MF-branded login  → redirects to /mf after auth
 *   /login     → Platform login     → redirects to /dashboard
 */
import React, { useState } from 'react';
import { useNavigate, Link, useLocation, useParams } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { useAuth } from '../context/AuthContext';
import {
  LogIn, AlertCircle, Loader2, Eye, EyeOff,
  Briefcase, TrendingUp, Shield, BarChart3,
} from 'lucide-react';

// ── Vertical branding config ────────────────────────────────
const VERTICALS = {
  pms: {
    name: 'Portfolio Management Services',
    tagline: 'SEBI-regulated portfolio management for fund managers',
    badge: 'SEBI Compliant',
    accentColor: '#34d399',
    accentGlow: 'rgba(52, 211, 153, 0.15)',
    icon: Briefcase,
    features: [
      { icon: TrendingUp, text: 'Sensor-fused conviction scoring' },
      { icon: Shield, text: 'SEBI compliance monitoring' },
      { icon: BarChart3, text: 'Real-time portfolio analytics' },
    ],
    redirectTo: '/pms',
    signupTo: '/pms/signup',
    poweredBy: 'contextsynapse',
  },
  mf: {
    name: 'Mutual Fund Management',
    tagline: 'End-to-end AMC operations — schemes, NAV, units & compliance',
    badge: 'SEBI Compliant',
    accentColor: '#5b8af0',
    accentGlow: 'rgba(91, 138, 240, 0.15)',
    icon: BarChart3,
    features: [
      { icon: TrendingUp, text: 'Daily NAV computation & AMFI upload' },
      { icon: Shield, text: 'SEBI category mandate compliance' },
      { icon: Briefcase, text: 'Unit accounting & SIP management' },
    ],
    redirectTo: '/mf',
    signupTo: '/mf/signup',
    poweredBy: 'contextsynapse',
  },
};

export default function VerticalLoginPage({ vertical: verticalProp }) {
  const { login, loading } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  // Determine vertical from prop or URL
  const vertical = verticalProp || (location.pathname.startsWith('/mf') ? 'mf' : 'pms');
  const config = VERTICALS[vertical] || VERTICALS.pms;
  const BrandIcon = config.icon;

  const from = location.state?.from?.pathname || config.redirectTo;

  const [error, setError] = useState('');
  const [showPw, setShowPw] = useState(false);

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm();

  const onSubmit = async (data) => {
    setError('');
    try {
      await login(data.email, data.password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err.userMessage || 'Invalid email or password');
    }
  };

  return (
    <div
      className="min-h-screen flex"
      style={{ background: 'var(--neo-bg)' }}
    >
      {/* Left panel — branding */}
      <div
        className="hidden lg:flex flex-col justify-center px-12"
        style={{
          width: 480,
          background: 'var(--neo-bg-secondary, #10131a)',
          borderRight: '1px solid var(--neo-border)',
        }}
      >
        {/* Logo mark */}
        <div style={{
          width: 48, height: 48, borderRadius: 12,
          background: `${config.accentColor}15`,
          border: `1px solid ${config.accentColor}30`,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          marginBottom: 24,
        }}>
          <BrandIcon size={24} style={{ color: config.accentColor }} />
        </div>

        <h1 style={{
          fontSize: 28, fontWeight: 800, color: 'var(--neo-text)',
          letterSpacing: '-0.03em', lineHeight: 1.2, marginBottom: 8,
        }}>
          {config.name}
        </h1>

        <p style={{ fontSize: 14, color: 'var(--neo-text-muted)', marginBottom: 32, lineHeight: 1.6 }}>
          {config.tagline}
        </p>

        {/* Feature list */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {config.features.map((f, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <div style={{
                width: 32, height: 32, borderRadius: 8,
                background: `${config.accentColor}10`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                flexShrink: 0,
              }}>
                <f.icon size={16} style={{ color: config.accentColor }} />
              </div>
              <span style={{ fontSize: 13, color: 'var(--neo-text-muted)' }}>{f.text}</span>
            </div>
          ))}
        </div>

        {/* Badge */}
        <div style={{ marginTop: 40, display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{
            fontSize: 9, fontWeight: 700, textTransform: 'uppercase',
            letterSpacing: '0.08em', padding: '3px 8px', borderRadius: 4,
            background: `${config.accentColor}15`,
            color: config.accentColor,
            border: `1px solid ${config.accentColor}30`,
          }}>
            {config.badge}
          </span>
          <span style={{ fontSize: 11, color: 'var(--neo-text-dim)' }}>
            powered by {config.poweredBy}
          </span>
        </div>
      </div>

      {/* Right panel — login form */}
      <div className="flex-1 flex items-center justify-center px-6">
        <div className="w-full max-w-md">
          {/* Mobile brand (shown on small screens) */}
          <div className="lg:hidden text-center mb-8">
            <div style={{
              width: 40, height: 40, borderRadius: 10,
              background: `${config.accentColor}15`,
              border: `1px solid ${config.accentColor}30`,
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              marginBottom: 12,
            }}>
              <BrandIcon size={20} style={{ color: config.accentColor }} />
            </div>
            <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--neo-text)' }}>
              {config.name}
            </h2>
          </div>

          {/* Form card */}
          <div
            className="rounded-2xl p-8"
            style={{
              background: 'var(--neo-surface)',
              border: '1px solid var(--neo-border)',
              boxShadow: `0 8px 32px rgba(0,0,0,0.3), 0 0 48px ${config.accentGlow}`,
            }}
          >
            <div className="mb-6">
              <h2 style={{ fontSize: 20, fontWeight: 700, color: 'var(--neo-text)', marginBottom: 4 }}>
                Sign in
              </h2>
              <p style={{ fontSize: 13, color: 'var(--neo-text-muted)' }}>
                Enter your credentials to continue
              </p>
            </div>

            {error && (
              <div
                className="flex items-center gap-2 mb-4 p-3 rounded-lg text-sm"
                style={{ background: 'rgba(248,113,113,0.1)', color: 'var(--neo-red)' }}
              >
                <AlertCircle size={16} /> {error}
              </div>
            )}

            <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
              <div>
                <label
                  className="block text-xs font-semibold mb-1.5 uppercase"
                  style={{ color: 'var(--neo-text-muted)', letterSpacing: '0.04em' }}
                >
                  Email
                </label>
                <input
                  {...register('email', {
                    required: 'Email is required',
                    pattern: { value: /^\S+@\S+\.\S+$/, message: 'Invalid email' },
                  })}
                  type="email"
                  className="pms-input"
                  placeholder="you@company.com"
                  autoFocus
                />
                {errors.email && (
                  <p className="mt-1 text-xs" style={{ color: 'var(--neo-red)' }}>{errors.email.message}</p>
                )}
              </div>

              <div>
                <label
                  className="block text-xs font-semibold mb-1.5 uppercase"
                  style={{ color: 'var(--neo-text-muted)', letterSpacing: '0.04em' }}
                >
                  Password
                </label>
                <div className="relative">
                  <input
                    {...register('password', { required: 'Password is required' })}
                    type={showPw ? 'text' : 'password'}
                    className="pms-input"
                    style={{ paddingRight: 40 }}
                    placeholder="Enter your password"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPw(!showPw)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 opacity-50 hover:opacity-100"
                    style={{ color: 'var(--neo-text-muted)', background: 'none', border: 'none', cursor: 'pointer' }}
                  >
                    {showPw ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
                {errors.password && (
                  <p className="mt-1 text-xs" style={{ color: 'var(--neo-red)' }}>{errors.password.message}</p>
                )}
              </div>

              <button
                type="submit"
                disabled={loading}
                className="pms-btn pms-btn-primary pms-btn-lg w-full justify-center"
                style={{
                  background: config.accentColor,
                  boxShadow: `0 4px 16px ${config.accentGlow}`,
                  width: '100%', display: 'flex',
                }}
              >
                {loading ? <Loader2 size={18} className="animate-spin" /> : <LogIn size={18} />}
                {loading ? 'Signing in...' : 'Sign In'}
              </button>
            </form>

            <p className="text-center text-sm mt-6" style={{ color: 'var(--neo-text-muted)' }}>
              Don't have an account?{' '}
              <Link to={config.signupTo} className="font-medium hover:underline" style={{ color: config.accentColor }}>
                Request access
              </Link>
            </p>
          </div>

          {/* Footer */}
          <p className="text-center mt-6" style={{ fontSize: 11, color: 'var(--neo-text-dim)' }}>
            {config.name} is a SEBI-compliant service. By signing in you agree to our terms.
          </p>
        </div>
      </div>
    </div>
  );
}

import React, { useState } from 'react';
import { useNavigate, Link, useLocation } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { useAuth } from '../context/AuthContext';
import { LogIn, AlertCircle, Loader2, Eye, EyeOff } from 'lucide-react';

export default function UserLoginPage() {
  const { login, loading } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [error, setError] = useState('');
  const [showPw, setShowPw] = useState(false);

  const from = location.state?.from?.pathname || '/dashboard';

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
    <div className="min-h-screen flex items-center justify-center px-4"
         style={{ background: 'var(--neo-bg)' }}>
      <div className="w-full max-w-md rounded-2xl p-8 shadow-xl"
           style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}>

        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold" style={{ color: 'var(--neo-blue)' }}>
            Welcome back
          </h1>
          <p className="mt-2 text-sm" style={{ color: 'var(--neo-text-muted)' }}>
            Sign in to your ContextSynapse workspace
          </p>
        </div>

        {error && (
          <div className="flex items-center gap-2 mb-4 p-3 rounded-lg text-sm"
               style={{ background: 'rgba(242,87,87,0.1)', color: 'var(--neo-red)' }}>
            <AlertCircle size={16} /> {error}
          </div>
        )}

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1" style={{ color: 'var(--neo-text-muted)' }}>
              Email
            </label>
            <input
              {...register('email', {
                required: 'Email is required',
                pattern: { value: /^\S+@\S+\.\S+$/, message: 'Invalid email' },
              })}
              type="email"
              className="w-full px-4 py-2.5 rounded-lg text-sm outline-none transition"
              style={{
                background: 'var(--neo-bg)',
                border: '1px solid var(--neo-border)',
                color: 'var(--neo-text)',
              }}
              placeholder="alice@example.com"
              autoFocus
            />
            {errors.email && (
              <p className="mt-1 text-xs" style={{ color: 'var(--neo-red)' }}>{errors.email.message}</p>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium mb-1" style={{ color: 'var(--neo-text-muted)' }}>
              Password
            </label>
            <div className="relative">
              <input
                {...register('password', { required: 'Password is required' })}
                type={showPw ? 'text' : 'password'}
                className="w-full px-4 py-2.5 pr-10 rounded-lg text-sm outline-none transition"
                style={{
                  background: 'var(--neo-bg)',
                  border: '1px solid var(--neo-border)',
                  color: 'var(--neo-text)',
                }}
                placeholder="Enter your password"
              />
              <button
                type="button"
                onClick={() => setShowPw(!showPw)}
                className="absolute right-3 top-1/2 -translate-y-1/2 opacity-50 hover:opacity-100"
                style={{ color: 'var(--neo-text-muted)' }}
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
            className="w-full flex items-center justify-center gap-2 py-2.5 rounded-lg font-medium text-sm transition hover:opacity-90 disabled:opacity-50"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            {loading ? <Loader2 size={18} className="animate-spin" /> : <LogIn size={18} />}
            {loading ? 'Signing in...' : 'Sign In'}
          </button>
        </form>

        <p className="text-center text-sm mt-6" style={{ color: 'var(--neo-text-muted)' }}>
          Don't have an account?{' '}
          <Link to="/signup" className="font-medium hover:underline" style={{ color: 'var(--neo-cyan)' }}>
            Sign up free
          </Link>
        </p>
      </div>
    </div>
  );
}

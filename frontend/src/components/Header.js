import React from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { LayoutDashboard, LogOut, User, Shield } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import NotificationCenter from './NotificationCenter';

export default function Header({ onAdminLogout }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { isAuthenticated, user, logout } = useAuth();
  const isAdmin = location.pathname.startsWith('/admin');
  const isDashboard = location.pathname.startsWith('/dashboard');

  // Hide header on vertical login/signup pages (they have their own branding)
  const isVerticalAuth = /^\/(pms|mf)\/(login|signup)$/.test(location.pathname);
  if (isVerticalAuth) return null;

  // Check if user has admin or owner role on any tenant
  const hasAdminAccess = user?.tenants?.some(
    (t) => t.role === 'admin' || t.role === 'owner'
  );

  const handleUserLogout = () => {
    logout();
    navigate('/');
  };

  return (
    <header className="h-12 bg-neo-bg border-b border-neo-border flex items-center justify-between px-4 shrink-0" role="banner">
      <div className="flex items-center gap-6">
        <Link to="/" className="text-neo-blue font-bold text-lg tracking-tight no-underline hover:text-neo-cyan transition-colors">
          ContextSynapse
        </Link>
        <nav className="flex items-center gap-1">
          {isAuthenticated && (
            <NavLink to="/dashboard" icon={<LayoutDashboard size={16} />} active={isDashboard}>
              Dashboard
            </NavLink>
          )}
        </nav>
      </div>

      <div className="flex items-center gap-3">
        {/* Notifications */}
        {isAuthenticated && <NotificationCenter />}

        {/* User menu */}
        {isAuthenticated && (
          <div className="flex items-center gap-3">
            {/* Admin console — only for admin/owner roles */}
            {hasAdminAccess && (
              <Link
                to="/admin"
                className={`flex items-center gap-1.5 px-2 py-1 rounded text-xs no-underline transition-colors ${
                  isAdmin
                    ? 'bg-neo-surface-light text-neo-text'
                    : 'text-neo-text-dim hover:text-neo-text-muted hover:bg-neo-surface'
                }`}
                title="Admin Console"
              >
                <Shield size={13} />
                Admin
              </Link>
            )}
            <span className="flex items-center gap-1.5 text-sm" style={{ color: 'var(--neo-text-muted)' }}>
              <User size={14} />
              {user?.display_name || user?.email}
            </span>
            <button
              onClick={handleUserLogout}
              className="flex items-center gap-1.5 text-neo-text-muted hover:text-neo-red text-sm transition-colors bg-transparent border-none cursor-pointer"
            >
              <LogOut size={14} />
              Sign out
            </button>
          </div>
        )}

        {/* Not logged in — show sign in link (except on auth pages) */}
        {!isAuthenticated && !['/login', '/signup'].includes(location.pathname) && (
          <Link
            to="/login"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm no-underline transition-colors text-neo-text-muted hover:text-neo-text hover:bg-neo-surface"
          >
            Sign in
          </Link>
        )}

        {/* Admin logout (separate admin-key auth, only shown when on /admin without user auth) */}
        {onAdminLogout && isAdmin && !isAuthenticated && (
          <button
            onClick={onAdminLogout}
            className="flex items-center gap-1.5 text-neo-text-muted hover:text-neo-red text-sm transition-colors bg-transparent border-none cursor-pointer"
          >
            <LogOut size={14} />
            Admin out
          </button>
        )}
      </div>
    </header>
  );
}

function NavLink({ to, icon, active, children }) {
  return (
    <Link
      to={to}
      className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-sm no-underline transition-colors ${
        active
          ? 'bg-neo-surface-light text-neo-text'
          : 'text-neo-text-muted hover:text-neo-text hover:bg-neo-surface'
      }`}
    >
      {icon}
      {children}
    </Link>
  );
}

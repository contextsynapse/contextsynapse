import React, { useState, useEffect } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import {
  LayoutDashboard, Database, BarChart3, Settings, Bot, CreditCard, Users,
  Plug, Terminal, Search, MessageSquare, Activity, Layers, Shield,
  BookOpen, Play, Lock, Server, Workflow, GitBranch, SlidersHorizontal,
  Menu, X, Command, Briefcase, Code2, Sparkles, Brain, FileText, TrendingUp,
  Compass, Globe, Smartphone, Radar, Target, Gauge, Navigation, Crosshair,
  Zap, Radio, Package,
} from 'lucide-react';
import { useJobs } from '../context/JobsContext';
import { registerPMS, getNavForRole } from '../lib/verticalRegistry';

// Register PMS vertical with its icons
registerPMS({ Briefcase, Navigation, Shield });

// Platform navigation — admins/developers see this + PMS sections
const platformNavSections = [
  {
    label: 'Context',
    items: [
      { to: '/dashboard/contexts', icon: Database, label: 'Contexts' },
      { to: '/dashboard/sessions', icon: Layers, label: 'Sessions' },
      { to: '/dashboard/graphs', icon: Database, label: 'Graphs' },
    ],
  },
  {
    label: 'Agents',
    items: [
      { to: '/dashboard/agents', icon: Bot, label: 'Agents' },
    ],
  },
  {
    label: 'Pipelines',
    items: [
      { to: '/dashboard/schemas', icon: FileText, label: 'Schemas' },
      { to: '/dashboard/pipelines', icon: Workflow, label: 'Pipelines' },
      { to: '/dashboard/jobs', icon: Briefcase, label: 'Jobs', badge: true },
    ],
  },
  {
    label: 'Intelligence',
    items: [
      { to: '/dashboard/context-lake', icon: Radar, label: 'Context Lake' },
      { to: '/dashboard/intelligence', icon: Brain, label: 'Insights' },
      { to: '/dashboard/cognition', icon: Sparkles, label: 'Cognition' },
      { to: '/dashboard/monitoring', icon: Activity, label: 'Monitoring' },
    ],
  },
  {
    label: 'SDLC',
    items: [
      { to: '/dashboard/projects', icon: Code2, label: 'Projects' },
    ],
  },
  {
    label: 'Develop',
    items: [
      { to: '/dashboard/playground', icon: Play, label: 'Playground' },
      { to: '/dashboard/mobile-agent', icon: MessageSquare, label: 'Chat' },
      { to: '/dashboard/api-docs', icon: Globe, label: 'API Docs' },
      { to: '/dashboard/docs', icon: BookOpen, label: 'AIQL' },
      { to: '/dashboard/vertical-builder', icon: Package, label: 'Build Vertical' },
    ],
  },
  {
    label: 'Settings',
    items: [
      { to: '/dashboard/integrations', icon: Plug, label: 'Integrations' },
      { to: '/dashboard/team', icon: Users, label: 'Team' },
      { to: '/dashboard/users', icon: Lock, label: 'Users' },
      { to: '/dashboard/settings', icon: Settings, label: 'Settings' },
    ],
  },
];

// Role determines which sections are visible
// Admin/developer: Home + PMS + Platform
// Fund Manager / Business: Home + PMS only
function getNavSections(userRole) {
  const isAdmin = !userRole || userRole === 'admin' || userRole === 'developer';

  const home = {
    label: '',
    items: [
      { to: '/dashboard/canvas', icon: Radar, label: 'Home' },
      ...(isAdmin ? [{ to: '/dashboard/overview', icon: LayoutDashboard, label: 'Platform' }] : []),
    ],
  };

  // Get vertical nav items based on role (from registry)
  const verticalSections = getNavForRole(userRole);

  if (isAdmin) {
    return [home, ...verticalSections, ...platformNavSections];
  }

  // Business user: Home + their vertical's items only
  return [home, ...verticalSections];
}

function NavItem({ item, onClick, activeJobCount }) {
  const showBadge = item.badge && activeJobCount > 0;
  return (
    <NavLink
      to={item.to}
      end={item.end}
      onClick={onClick}
      className={({ isActive }) =>
        `flex items-center gap-2.5 px-3 py-1.5 rounded-lg text-sm no-underline transition-colors ${
          isActive
            ? 'text-neo-text font-medium'
            : 'text-neo-text-muted hover:text-neo-text hover:bg-neo-surface-light'
        }`
      }
      style={({ isActive }) =>
        isActive
          ? {
              borderLeft: '3px solid var(--neo-blue)',
              background: 'rgba(76, 142, 218, 0.1)',
            }
          : { borderLeft: '3px solid transparent' }
      }
    >
      <item.icon size={15} />
      {item.label}
      {showBadge && (
        <span
          className="ml-auto flex items-center justify-center text-xs font-bold rounded-full"
          style={{
            background: 'var(--neo-blue)',
            color: '#fff',
            minWidth: 18,
            height: 18,
            padding: '0 5px',
            fontSize: 10,
          }}
        >
          {activeJobCount}
        </span>
      )}
    </NavLink>
  );
}

function SidebarContent({ onNavigate }) {
  const jobsCtx = useJobs();
  const activeJobCount = jobsCtx ? jobsCtx.activeCount : 0;

  // Get user role from localStorage (set during login)
  let userRole = '';
  try {
    const stored = localStorage.getItem('contextsynapse_user');
    if (stored) {
      const user = JSON.parse(stored);
      userRole = user.role || user.user_role || '';
    }
  } catch {}
  // Also check if a PMS role was explicitly set
  const pmsRole = localStorage.getItem('contextsynapse_pms_role') || '';
  const effectiveRole = pmsRole || userRole;

  const navSections = getNavSections(effectiveRole);

  return (
    <>
      {navSections.map((section, i) => (
        <div key={`${section.label}-${i}`} className={i > 0 ? 'mt-4' : ''}>
          {i > 0 && (
            <div className="px-3 mb-1">
              <span
                className="text-xs font-semibold uppercase tracking-wider"
                style={{ color: 'var(--neo-text-muted)', fontSize: '10px' }}
              >
                {section.label}
              </span>
            </div>
          )}
          <nav className="flex flex-col gap-0.5">
            {section.items.map((item) => (
              <NavItem key={item.to} item={item} onClick={onNavigate} activeJobCount={activeJobCount} />
            ))}
          </nav>
        </div>
      ))}

      {/* Keyboard shortcut hint */}
      <div className="mt-3 px-3">
        <div
          className="flex items-center gap-1.5 text-xs px-2 py-1.5 rounded-lg"
          style={{ color: 'var(--neo-text-dim)', background: 'var(--neo-bg)' }}
        >
          <Command size={11} />
          <span>Ctrl+K to search</span>
        </div>
      </div>

      {/* Admin link at bottom */}
      <div className="mt-auto pt-3" style={{ borderTop: '1px solid var(--neo-border)' }}>
        <NavLink
          to="/admin"
          onClick={onNavigate}
          className="flex items-center gap-2.5 px-3 py-1.5 rounded-lg text-sm no-underline transition-colors text-neo-text-muted hover:text-neo-text hover:bg-neo-surface-light"
        >
          <Server size={15} />
          Admin Console
        </NavLink>
      </div>
    </>
  );
}

export default function DashboardSidebar() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();

  // Close mobile sidebar on route change
  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  return (
    <>
      {/* Mobile hamburger button */}
      <button
        onClick={() => setMobileOpen(true)}
        className="md:hidden fixed top-14 left-3 z-40 p-2 rounded-lg"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
        aria-label="Open menu"
      >
        <Menu size={20} />
      </button>

      {/* Desktop sidebar */}
      <aside
        className="hidden md:flex w-56 shrink-0 border-r flex-col py-3 px-2 overflow-y-auto"
        style={{ background: 'var(--neo-surface)', borderColor: 'var(--neo-border)' }}
        role="navigation"
        aria-label="Dashboard navigation"
      >
        <SidebarContent onNavigate={() => {}} />
      </aside>

      {/* Mobile sidebar overlay */}
      {mobileOpen && (
        <div
          className="md:hidden fixed inset-0 z-50"
          style={{ background: 'rgba(0,0,0,0.5)' }}
          onClick={() => setMobileOpen(false)}
        >
          <aside
            className="w-64 h-full flex flex-col py-3 px-2 overflow-y-auto"
            style={{ background: 'var(--neo-surface)' }}
            onClick={(e) => e.stopPropagation()}
            role="navigation"
            aria-label="Dashboard navigation"
          >
            <div className="flex items-center justify-between px-3 mb-3">
              <span className="text-sm font-bold" style={{ color: 'var(--neo-text)' }}>Navigation</span>
              <button
                onClick={() => setMobileOpen(false)}
                className="bg-transparent border-none cursor-pointer p-1"
                style={{ color: 'var(--neo-text-muted)' }}
                aria-label="Close menu"
              >
                <X size={18} />
              </button>
            </div>
            <SidebarContent onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}
    </>
  );
}

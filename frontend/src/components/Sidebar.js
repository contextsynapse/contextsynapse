import React from 'react';
import { NavLink } from 'react-router-dom';
import { Users, Activity, Server, Settings, ArrowLeft } from 'lucide-react';

const NAV_ITEMS = [
  { to: '/admin', icon: Activity, label: 'Overview', end: true },
  { to: '/admin/tenants', icon: Users, label: 'Tenants' },
  { to: '/admin/health', icon: Server, label: 'Health' },
  { to: '/admin/settings', icon: Settings, label: 'Settings' },
];

export default function Sidebar() {
  return (
    <aside className="w-52 bg-neo-surface border-r border-neo-border flex flex-col py-4 shrink-0">
      <div className="px-4 mb-4 text-xs uppercase tracking-widest text-neo-text-muted font-semibold">
        Administration
      </div>
      <nav className="flex flex-col gap-0.5 px-2">
        {NAV_ITEMS.map(({ to, icon: Icon, label, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex items-center gap-2.5 px-3 py-2 rounded text-sm no-underline transition-colors ${
                isActive
                  ? 'bg-neo-blue/20 text-neo-blue'
                  : 'text-neo-text-muted hover:text-neo-text hover:bg-neo-surface-light'
              }`
            }
          >
            <Icon size={16} />
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="mt-auto pt-3 px-2" style={{ borderTop: '1px solid var(--neo-border)' }}>
        <NavLink
          to="/dashboard"
          className="flex items-center gap-2.5 px-3 py-2 rounded text-sm no-underline transition-colors text-neo-text-muted hover:text-neo-text hover:bg-neo-surface-light"
        >
          <ArrowLeft size={16} />
          Back to Dashboard
        </NavLink>
      </div>
    </aside>
  );
}

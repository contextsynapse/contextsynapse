import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useHotkeys } from 'react-hotkeys-hook';
import {
  Search, LayoutDashboard, Database, Key, BarChart3, Settings, Bot,
  CreditCard, Users, Plug, Activity, Layers, Shield, BookOpen, Play,
  Lock, Workflow, Upload, GitBranch, SlidersHorizontal, Command,
} from 'lucide-react';

const COMMANDS = [
  { label: 'Dashboard', path: '/dashboard', icon: LayoutDashboard, section: 'Navigate' },
  { label: 'Graphs', path: '/dashboard/graphs', icon: Database, section: 'Navigate' },
  { label: 'Pipelines', path: '/dashboard/pipelines', icon: Workflow, section: 'Navigate' },
  { label: 'Ingest Data', path: '/dashboard/ingest', icon: Upload, section: 'Navigate' },
  { label: 'Graph Explorer', path: '/dashboard/explorer', icon: GitBranch, section: 'Navigate' },
  { label: 'Chunking Config', path: '/dashboard/chunking', icon: SlidersHorizontal, section: 'Navigate' },
  { label: 'Agents', path: '/dashboard/agents', icon: Bot, section: 'Navigate' },
  { label: 'Sessions', path: '/dashboard/sessions', icon: Layers, section: 'Navigate' },
  { label: 'Context', path: '/dashboard/context', icon: Lock, section: 'Navigate' },
  { label: 'Integrations', path: '/dashboard/integrations', icon: Plug, section: 'Navigate' },
  { label: 'Monitoring', path: '/dashboard/monitoring', icon: Activity, section: 'Navigate' },
  { label: 'Audit Trail', path: '/dashboard/audit', icon: Shield, section: 'Navigate' },
  { label: 'Usage', path: '/dashboard/usage', icon: BarChart3, section: 'Navigate' },
  { label: 'API Docs', path: '/dashboard/docs', icon: BookOpen, section: 'Navigate' },
  { label: 'Playground', path: '/dashboard/playground', icon: Play, section: 'Navigate' },
  { label: 'API Keys', path: '/dashboard/api-keys', icon: Key, section: 'Navigate' },
  { label: 'Team', path: '/dashboard/team', icon: Users, section: 'Navigate' },
  { label: 'Billing', path: '/dashboard/billing', icon: CreditCard, section: 'Navigate' },
  { label: 'Settings', path: '/dashboard/settings', icon: Settings, section: 'Navigate' },
];

export default function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef(null);
  const navigate = useNavigate();

  // Ctrl+K / Cmd+K to toggle
  useHotkeys('mod+k', (e) => {
    e.preventDefault();
    setOpen((o) => !o);
  }, { enableOnFormTags: true });

  useEffect(() => {
    if (open) {
      setQuery('');
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  const filtered = query.trim()
    ? COMMANDS.filter((c) => c.label.toLowerCase().includes(query.toLowerCase()))
    : COMMANDS;

  const execute = useCallback((cmd) => {
    setOpen(false);
    if (cmd.path) navigate(cmd.path);
  }, [navigate]);

  const handleKeyDown = (e) => {
    if (e.key === 'Escape') { setOpen(false); return; }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex((i) => Math.min(i + 1, filtered.length - 1));
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex((i) => Math.max(i - 1, 0));
    }
    if (e.key === 'Enter' && filtered[selectedIndex]) {
      execute(filtered[selectedIndex]);
    }
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-24"
      style={{ background: 'rgba(0,0,0,0.5)', backdropFilter: 'blur(4px)' }}
      onClick={() => setOpen(false)}
    >
      <div
        className="w-full max-w-lg rounded-xl shadow-2xl overflow-hidden"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search input */}
        <div className="flex items-center gap-3 px-4 py-3" style={{ borderBottom: '1px solid var(--neo-border)' }}>
          <Search size={18} style={{ color: 'var(--neo-text-muted)' }} />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => { setQuery(e.target.value); setSelectedIndex(0); }}
            onKeyDown={handleKeyDown}
            placeholder="Search pages..."
            className="flex-1 bg-transparent border-none outline-none text-sm"
            style={{ color: 'var(--neo-text)' }}
          />
          <kbd className="px-1.5 py-0.5 rounded text-xs" style={{ background: 'var(--neo-bg)', color: 'var(--neo-text-muted)', border: '1px solid var(--neo-border)' }}>
            Esc
          </kbd>
        </div>

        {/* Results */}
        <div className="max-h-72 overflow-y-auto py-2">
          {filtered.length === 0 && (
            <p className="px-4 py-6 text-sm text-center" style={{ color: 'var(--neo-text-muted)' }}>
              No results found
            </p>
          )}
          {filtered.map((cmd, i) => {
            const Icon = cmd.icon;
            return (
              <button
                key={cmd.path}
                onClick={() => execute(cmd)}
                onMouseEnter={() => setSelectedIndex(i)}
                className="w-full flex items-center gap-3 px-4 py-2 text-sm text-left border-none cursor-pointer transition-colors"
                style={{
                  background: i === selectedIndex ? 'var(--neo-bg)' : 'transparent',
                  color: 'var(--neo-text)',
                }}
              >
                <Icon size={16} style={{ color: 'var(--neo-text-muted)', flexShrink: 0 }} />
                <span className="flex-1">{cmd.label}</span>
                <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>{cmd.section}</span>
              </button>
            );
          })}
        </div>

        {/* Footer hint */}
        <div className="px-4 py-2 flex items-center gap-4 text-xs" style={{ borderTop: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}>
          <span className="flex items-center gap-1">
            <kbd className="px-1 rounded" style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)' }}>↑↓</kbd>
            Navigate
          </span>
          <span className="flex items-center gap-1">
            <kbd className="px-1 rounded" style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)' }}>↵</kbd>
            Open
          </span>
        </div>
      </div>
    </div>
  );
}

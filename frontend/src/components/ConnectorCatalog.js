import React, { useState } from 'react';
import {
  X, Search, Cloud, Users, Server, Headphones, ClipboardList,
  MessageSquare, Globe, Webhook, Database, FolderOpen, Bot,
  GitBranch, BookOpen, FileText,
} from 'lucide-react';

const TYPE_ICONS = {
  salesforce: Cloud,
  hubspot: Users,
  sap: Server,
  servicenow: Headphones,
  jira: ClipboardList,
  slack: MessageSquare,
  rest_api: Globe,
  webhook: Webhook,
  database: Database,
  file_watcher: FolderOpen,
  s3: Cloud,
  mcp: Bot,
  git: GitBranch,
  github: GitBranch,
  confluence: BookOpen,
  notion: FileText,
};

const CATEGORY_ORDER = [
  'Source Control', 'Project Management', 'Knowledge Management',
  'CRM', 'ERP', 'ITSM', 'Communication', 'Data',
  'Cloud Storage', 'Local', 'AI Agent', 'Custom',
];

export default function ConnectorCatalog({ connectors, onSelect, onClose }) {
  const [search, setSearch] = useState('');

  const filtered = connectors.filter(
    (c) =>
      c.name.toLowerCase().includes(search.toLowerCase()) ||
      c.category.toLowerCase().includes(search.toLowerCase()) ||
      c.description.toLowerCase().includes(search.toLowerCase())
  );

  const grouped = {};
  filtered.forEach((c) => {
    if (!grouped[c.category]) grouped[c.category] = [];
    grouped[c.category].push(c);
  });

  const sortedCategories = CATEGORY_ORDER.filter((cat) => grouped[cat]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div
        className="w-full max-w-2xl max-h-[80vh] rounded-2xl overflow-hidden flex flex-col"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b" style={{ borderColor: 'var(--neo-border)' }}>
          <h2 className="text-lg font-bold" style={{ color: 'var(--neo-text)' }}>
            Add Integration
          </h2>
          <button onClick={onClose} className="p-1 rounded hover:opacity-70" style={{ color: 'var(--neo-text-muted)' }}>
            <X size={20} />
          </button>
        </div>

        {/* Search */}
        <div className="px-5 pt-4">
          <div className="relative">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: 'var(--neo-text-muted)' }} />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search connectors..."
              className="w-full pl-9 pr-3 py-2 rounded-lg text-sm outline-none"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
              autoFocus
            />
          </div>
        </div>

        {/* Connector grid */}
        <div className="flex-1 overflow-auto p-5 space-y-6">
          {sortedCategories.map((category) => (
            <div key={category}>
              <h3 className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: 'var(--neo-text-muted)' }}>
                {category}
              </h3>
              <div className="grid grid-cols-2 gap-3">
                {grouped[category].map((connector) => {
                  const Icon = TYPE_ICONS[connector.type] || Globe;
                  return (
                    <button
                      key={connector.type}
                      onClick={() => onSelect(connector)}
                      className="flex items-start gap-3 p-4 rounded-xl text-left transition hover:opacity-90"
                      style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)' }}
                    >
                      <div
                        className="w-10 h-10 rounded-lg flex items-center justify-center shrink-0"
                        style={{ background: 'var(--neo-surface)', color: 'var(--neo-blue)' }}
                      >
                        <Icon size={20} />
                      </div>
                      <div className="min-w-0">
                        <div className="font-medium text-sm" style={{ color: 'var(--neo-text)' }}>
                          {connector.name}
                        </div>
                        <div className="text-xs mt-0.5 line-clamp-2" style={{ color: 'var(--neo-text-muted)' }}>
                          {connector.description}
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}

          {sortedCategories.length === 0 && (
            <div className="text-center py-8" style={{ color: 'var(--neo-text-muted)' }}>
              No connectors match "{search}"
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

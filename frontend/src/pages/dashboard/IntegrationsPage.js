import React, { useState, useEffect, useCallback } from 'react';
import {
  Plug, Plus, Loader2, Trash2, RefreshCw, TestTube, History, Edit3,
  CheckCircle, XCircle, AlertCircle, Cloud, Users, Server,
  Headphones, ClipboardList, MessageSquare, Globe, Webhook,
  Database, FolderOpen, Bot, GitBranch, Github, BookOpen, FileText,
} from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import ConnectorCatalog from '../../components/ConnectorCatalog';
import ConnectorConfigForm from '../../components/ConnectorConfigForm';
import SyncHistory from '../../components/SyncHistory';

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

const STATUS_STYLES = {
  active: { bg: 'rgba(34,197,94,0.1)', color: 'var(--neo-green)', label: 'Active' },
  inactive: { bg: 'rgba(148,163,184,0.1)', color: 'var(--neo-text-muted)', label: 'Inactive' },
  error: { bg: 'rgba(239,68,68,0.1)', color: '#ef4444', label: 'Error' },
  syncing: { bg: 'rgba(59,130,246,0.1)', color: 'var(--neo-blue)', label: 'Syncing' },
};

export default function IntegrationsPage() {
  const [integrations, setIntegrations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [catalog, setCatalog] = useState([]);
  const [showCatalog, setShowCatalog] = useState(false);
  const [selectedConnector, setSelectedConnector] = useState(null);
  const [editingIntegration, setEditingIntegration] = useState(null);
  const [saving, setSaving] = useState(false);
  const [expandedId, setExpandedId] = useState(null);
  const [historyData, setHistoryData] = useState({});
  const [syncing, setSyncing] = useState({});

  const fetchIntegrations = useCallback(() => {
    api.get('/dashboard/integrations')
      .then((res) => setIntegrations(res.data.integrations || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const fetchCatalog = useCallback(() => {
    api.get('/dashboard/integrations/catalog')
      .then((res) => setCatalog(res.data.connectors || []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetchIntegrations();
    fetchCatalog();
  }, [fetchIntegrations, fetchCatalog]);

  const handleSelectConnector = (connector) => {
    setShowCatalog(false);
    setSelectedConnector(connector);
  };

  const handleSave = async ({ config, credentials }) => {
    setSaving(true);
    try {
      if (editingIntegration) {
        // Update existing integration
        await api.patch(`/dashboard/integrations/${editingIntegration.integration_id}`, {
          name: config._name || editingIntegration.name,
          config,
          credentials,
        });
        toast.success(`${editingIntegration.name} updated`);
        setEditingIntegration(null);
      } else {
        // Create new integration
        await api.post('/dashboard/integrations', {
          connector_type: selectedConnector.type,
          name: config._name || selectedConnector.name,
          config,
          credentials,
        });
        toast.success(`${selectedConnector.name} integration created`);
      }
      setSelectedConnector(null);
      fetchIntegrations();
    } catch {
      // handled by interceptor
    } finally {
      setSaving(false);
    }
  };

  const handleEdit = (intg) => {
    // Find the connector definition from catalog
    const connector = catalog.find(c => c.type === intg.connector_type) || {
      type: intg.connector_type, name: intg.name, config_schema: [],
    };
    setEditingIntegration(intg);
    setSelectedConnector({
      ...connector,
      _editMode: true,
      _existingConfig: intg.config || {},
      _existingName: intg.name,
    });
  };

  const handleDelete = async (id, name) => {
    if (!window.confirm(`Remove "${name}" integration?`)) return;
    try {
      await api.delete(`/dashboard/integrations/${id}`);
      toast.success('Integration removed');
      fetchIntegrations();
    } catch {}
  };

  const handleTest = async (id) => {
    try {
      const res = await api.post(`/dashboard/integrations/${id}/test`);
      if (res.data.success) {
        toast.success(res.data.message);
      } else {
        toast.error(res.data.message);
      }
      fetchIntegrations();
    } catch {}
  };

  const handleSync = async (id) => {
    setSyncing((prev) => ({ ...prev, [id]: true }));
    try {
      const res = await api.post(`/dashboard/integrations/${id}/sync`);
      toast.success(res.data.message || 'Sync complete');
      fetchIntegrations();
      // Refresh history if expanded
      if (expandedId === id) {
        fetchHistory(id);
      }
    } catch {} finally {
      setSyncing((prev) => ({ ...prev, [id]: false }));
    }
  };

  const fetchHistory = async (id) => {
    try {
      const res = await api.get(`/dashboard/integrations/${id}/history`);
      setHistoryData((prev) => ({ ...prev, [id]: res.data.history || [] }));
    } catch {}
  };

  const toggleExpanded = (id) => {
    if (expandedId === id) {
      setExpandedId(null);
    } else {
      setExpandedId(id);
      if (!historyData[id]) fetchHistory(id);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      </div>
    );
  }

  return (
    <div className="max-w-4xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold" style={{ color: 'var(--neo-text)' }}>Integrations</h1>
          <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
            Configure connections to Git, Jira, databases, and other systems. Test them here, then use in your boundaries.
          </p>
        </div>
        <button
          onClick={() => setShowCatalog(true)}
          className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90"
          style={{ background: 'var(--neo-blue)', color: '#fff' }}
        >
          <Plus size={16} /> Add Integration
        </button>
      </div>

      {/* Integration list */}
      {integrations.length === 0 ? (
        <div
          className="text-center py-16 rounded-2xl"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        >
          <Plug size={40} className="mx-auto mb-3" style={{ color: 'var(--neo-text-muted)' }} />
          <p className="font-medium" style={{ color: 'var(--neo-text)' }}>No integrations yet</p>
          <p className="text-sm mt-1 mb-4" style={{ color: 'var(--neo-text-muted)' }}>
            Connect Git, Jira, databases, or other systems. Test the connection, then use it in your boundaries.
          </p>
          <button
            onClick={() => setShowCatalog(true)}
            className="px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            Browse Connectors
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {integrations.map((intg) => {
            const Icon = TYPE_ICONS[intg.connector_type] || Globe;
            const statusStyle = STATUS_STYLES[intg.status] || STATUS_STYLES.inactive;
            const isExpanded = expandedId === intg.integration_id;

            return (
              <div
                key={intg.integration_id}
                className="rounded-xl overflow-hidden"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
              >
                <div className="flex items-center justify-between p-4">
                  <div className="flex items-center gap-3">
                    <div
                      className="w-10 h-10 rounded-lg flex items-center justify-center"
                      style={{ background: 'var(--neo-bg)', color: 'var(--neo-blue)' }}
                    >
                      <Icon size={20} />
                    </div>
                    <div>
                      <div className="font-medium text-sm" style={{ color: 'var(--neo-text)' }}>
                        {intg.name}
                      </div>
                      <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                        <span className="uppercase font-medium">{intg.connector_type}</span>
                        <span>&middot;</span>
                        <span
                          className="px-1.5 py-0.5 rounded text-xs font-medium"
                          style={{ background: statusStyle.bg, color: statusStyle.color }}
                        >
                          {statusStyle.label}
                        </span>
                        {intg.last_sync_at && (
                          <>
                            <span>&middot;</span>
                            <span>Last sync: {new Date(intg.last_sync_at).toLocaleDateString()}</span>
                          </>
                        )}
                        {intg.sync_count > 0 && (
                          <>
                            <span>&middot;</span>
                            <span>{intg.sync_count} syncs</span>
                          </>
                        )}
                      </div>
                      {/* Connection details */}
                      {intg.config && (
                        <div className="text-[10px] mt-0.5" style={{ color: 'var(--neo-text-dim)' }}>
                          {intg.config.repo_url || intg.config.domain || intg.config.instance_url || intg.config.base_url || intg.config.instance || intg.config.connection_string?.replace(/:[^@]*@/, ':***@') || intg.config.bucket || ''}
                          {intg.config.projects && ` · Project: ${intg.config.projects}`}
                          {intg.config.branch && ` · Branch: ${intg.config.branch}`}
                          {intg.config.channels && ` · ${intg.config.channels}`}
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-1.5">
                    <button
                      onClick={() => handleTest(intg.integration_id)}
                      className="p-2 rounded-lg transition hover:opacity-70"
                      style={{ color: 'var(--neo-text-muted)' }}
                      title="Test connection"
                    >
                      <TestTube size={15} />
                    </button>
                    <button
                      onClick={() => handleSync(intg.integration_id)}
                      disabled={syncing[intg.integration_id]}
                      className="p-2 rounded-lg transition hover:opacity-70"
                      style={{ color: 'var(--neo-blue)' }}
                      title="Sync now"
                    >
                      <RefreshCw size={15} className={syncing[intg.integration_id] ? 'animate-spin' : ''} />
                    </button>
                    <button
                      onClick={() => toggleExpanded(intg.integration_id)}
                      className="p-2 rounded-lg transition hover:opacity-70"
                      style={{ color: isExpanded ? 'var(--neo-blue)' : 'var(--neo-text-muted)' }}
                      title="Sync history"
                    >
                      <History size={15} />
                    </button>
                    <button
                      onClick={() => handleEdit(intg)}
                      className="p-2 rounded-lg transition hover:opacity-70"
                      style={{ color: 'var(--neo-text-muted)' }}
                      title="Edit configuration"
                    >
                      <Edit3 size={15} />
                    </button>
                    <button
                      onClick={() => handleDelete(intg.integration_id, intg.name)}
                      className="p-2 rounded-lg transition hover:opacity-70"
                      style={{ color: 'var(--neo-text-muted)' }}
                      title="Remove"
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                </div>

                {/* Error message */}
                {intg.status === 'error' && intg.error_message && (
                  <div
                    className="flex items-center gap-2 px-4 pb-3 text-xs"
                    style={{ color: '#ef4444' }}
                  >
                    <AlertCircle size={14} />
                    {intg.error_message}
                  </div>
                )}

                {/* Expanded sync history */}
                {isExpanded && (
                  <div className="px-4 pb-4 border-t" style={{ borderColor: 'var(--neo-border)' }}>
                    <h4 className="text-xs font-semibold uppercase tracking-wider my-3" style={{ color: 'var(--neo-text-muted)' }}>
                      Sync History
                    </h4>
                    <SyncHistory history={historyData[intg.integration_id] || []} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Catalog modal */}
      {showCatalog && (
        <ConnectorCatalog
          connectors={catalog}
          onSelect={handleSelectConnector}
          onClose={() => setShowCatalog(false)}
        />
      )}

      {/* Config form modal */}
      {selectedConnector && (
        <ConnectorConfigForm
          connector={selectedConnector}
          onSave={handleSave}
          onBack={() => { setSelectedConnector(null); setEditingIntegration(null); }}
          saving={saving}
          editMode={!!editingIntegration}
        />
      )}
    </div>
  );
}

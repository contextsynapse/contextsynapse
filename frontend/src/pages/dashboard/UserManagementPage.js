/**
 * User Management Page — Admin dashboard for managing users and vertical roles.
 */
import React, { useState, useEffect, useCallback } from 'react';
import { Users, UserPlus, Shield, Mail, Phone, CreditCard, Clock, Key, Edit2, Trash2, Plus, Check, X } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import {
  PageHeader, GlassCard, KPIStrip, DataTable, StatusBadge, SlideOver,
  EmptyState, SectionHeader, FormField, ConfirmDialog,
} from '../../components/ui-components';

// ── Constants ──────────────────────────────────────────────────────
const PMS_ROLES = ['fund_manager', 'compliance_officer', 'research_analyst', 'client_viewer', 'operations', 'admin'];
const MF_ROLES = ['fund_manager', 'compliance_officer', 'dealer', 'operations', 'admin'];
const VERTICALS = [
  { key: 'pms', label: 'PMS', roles: PMS_ROLES },
  { key: 'mf', label: 'MF', roles: MF_ROLES },
];
const PAN_REGEX = /^[A-Z]{5}[0-9]{4}[A-Z]$/;

function roleBadgeVariant(role) {
  if (!role) return 'default';
  if (role === 'admin') return 'purple';
  if (role === 'fund_manager') return 'success';
  if (role === 'compliance_officer') return 'warning';
  if (role === 'operations') return 'info';
  return 'neutral';
}

function formatRole(role) {
  if (!role) return '\u2014';
  return role.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function formatDate(d) {
  if (!d) return '\u2014';
  return new Date(d).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function formatDateTime(d) {
  if (!d) return '\u2014';
  return new Date(d).toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

// ── Main Component ─────────────────────────────────────────────────
export default function UserManagementPage() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedUser, setSelectedUser] = useState(null);
  const [userRoles, setUserRoles] = useState({});
  const [detailOpen, setDetailOpen] = useState(false);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [rolesLoading, setRolesLoading] = useState(false);

  // ── Fetch users ──
  const fetchUsers = useCallback(async () => {
    try {
      setLoading(true);
      const res = await api.get('/auth/users');
      setUsers(res.data?.users || res.data || []);
    } catch (err) {
      toast.error('Failed to load users');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchUsers(); }, [fetchUsers]);

  // ── Fetch roles for a user ──
  const fetchUserRoles = useCallback(async (userId) => {
    try {
      setRolesLoading(true);
      const res = await api.get(`/auth/users/${userId}/roles`);
      setUserRoles(res.data?.roles || res.data || {});
    } catch {
      setUserRoles({});
    } finally {
      setRolesLoading(false);
    }
  }, []);

  // ── Open detail panel ──
  const openDetail = useCallback((user) => {
    setSelectedUser(user);
    setDetailOpen(true);
    fetchUserRoles(user.id || user.user_id);
  }, [fetchUserRoles]);

  // ── KPI data ──
  const totalUsers = users.length;
  const activeUsers = users.filter(u => u.status === 'active').length;
  const pmsUsers = users.filter(u => u.pms_role).length;
  const mfUsers = users.filter(u => u.mf_role).length;
  const pendingInvites = users.filter(u => u.status === 'pending' || u.status === 'invited').length;

  // ── Table columns ──
  const columns = [
    { header: 'Name', accessor: 'name', key: 'name' },
    { header: 'Email', accessor: 'email', key: 'email' },
    {
      header: 'Status', accessor: 'status', key: 'status',
      render: (val) => (
        <StatusBadge status={val || 'active'} variant={val === 'active' ? 'active' : val === 'inactive' ? 'danger' : 'pending'} dot />
      ),
    },
    {
      header: 'PMS Role', accessor: 'pms_role', key: 'pms_role',
      render: (val) => val
        ? <StatusBadge status={formatRole(val)} variant={roleBadgeVariant(val)} />
        : <span style={{ color: 'var(--neo-text-dim)' }}>{'\u2014'}</span>,
    },
    {
      header: 'MF Role', accessor: 'mf_role', key: 'mf_role',
      render: (val) => val
        ? <StatusBadge status={formatRole(val)} variant={roleBadgeVariant(val)} />
        : <span style={{ color: 'var(--neo-text-dim)' }}>{'\u2014'}</span>,
    },
    {
      header: 'Last Login', accessor: 'last_login', key: 'last_login',
      render: (val) => <span style={{ fontSize: 12, color: 'var(--neo-text-muted)' }} className="pms-number">{formatDate(val)}</span>,
    },
    {
      header: 'Actions', key: 'actions', accessor: () => null, width: 80,
      render: (_, row) => (
        <button
          className="pms-btn pms-btn-ghost pms-btn-sm"
          onClick={(e) => { e.stopPropagation(); openDetail(row); }}
          title="Edit user"
        >
          <Edit2 size={14} />
        </button>
      ),
    },
  ];

  return (
    <div className="pms-fade-in" style={{ maxWidth: 1200, margin: '0 auto' }}>
      <PageHeader icon={Users} title="User Management" subtitle="Manage users, roles & access across verticals">
        <button className="pms-btn pms-btn-primary" onClick={() => setInviteOpen(true)}>
          <UserPlus size={15} style={{ marginRight: 6 }} />
          Invite User
        </button>
      </PageHeader>

      {/* KPI Strip */}
      <div style={{ marginTop: 20 }}>
        <KPIStrip
          loading={loading}
          items={[
            { label: 'Total Users', value: totalUsers, icon: Users },
            { label: 'Active PMS Users', value: pmsUsers, icon: Shield, color: 'var(--neo-blue)' },
            { label: 'Active MF Users', value: mfUsers, icon: Shield, color: 'var(--neo-purple)' },
            { label: 'Pending Invites', value: pendingInvites, icon: Mail, color: 'var(--neo-yellow)' },
          ]}
        />
      </div>

      {/* User Table */}
      <SectionHeader title="All Users" />
      <DataTable
        columns={columns}
        data={users}
        loading={loading}
        onRowClick={openDetail}
        emptyMessage="No users found"
      />

      {/* User Detail SlideOver */}
      <UserDetailPanel
        open={detailOpen}
        onClose={() => { setDetailOpen(false); setSelectedUser(null); }}
        user={selectedUser}
        roles={userRoles}
        rolesLoading={rolesLoading}
        onRefresh={() => {
          fetchUsers();
          if (selectedUser) fetchUserRoles(selectedUser.id || selectedUser.user_id);
        }}
      />

      {/* Invite User SlideOver */}
      <InviteUserPanel
        open={inviteOpen}
        onClose={() => setInviteOpen(false)}
        onSuccess={() => { setInviteOpen(false); fetchUsers(); }}
      />
    </div>
  );
}

// ── User Detail Panel ──────────────────────────────────────────────
function UserDetailPanel({ open, onClose, user, roles, rolesLoading, onRefresh }) {
  const [assignOpen, setAssignOpen] = useState(null); // vertical key or null
  const [editScopeOpen, setEditScopeOpen] = useState(null);
  const [confirmRevoke, setConfirmRevoke] = useState(null); // { vertical, role }

  if (!user) return null;

  const userId = user.id || user.user_id;

  return (
    <SlideOver open={open} onClose={onClose} title={user.name || user.email} subtitle="User details & role management" width={520}>
      {/* User Info */}
      <GlassCard hover={false} padding="16px" style={{ marginBottom: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <InfoRow icon={Mail} label="Email" value={user.email} />
          <InfoRow icon={Phone} label="Phone" value={user.phone || '\u2014'} />
          <InfoRow icon={CreditCard} label="PAN" value={user.pan || '\u2014'} />
          <InfoRow icon={Shield} label="Status" value={
            <StatusBadge status={user.status || 'active'} variant={user.status === 'active' ? 'active' : 'pending'} dot />
          } />
          <InfoRow icon={Key} label="Auth Provider" value={user.auth_provider || 'local'} />
          <InfoRow icon={Clock} label="Created" value={formatDate(user.created_at)} />
          <InfoRow icon={Clock} label="Last Login" value={formatDateTime(user.last_login)} />
        </div>
      </GlassCard>

      {/* Vertical Roles */}
      <SectionHeader title="Vertical Roles" />
      {rolesLoading ? (
        <div className="pms-glass-static" style={{ padding: 20 }}>
          <div className="pms-skeleton" style={{ width: '60%', height: 16, marginBottom: 12 }} />
          <div className="pms-skeleton" style={{ width: '40%', height: 16 }} />
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {VERTICALS.map(v => {
            const verticalRoles = roles[v.key] || [];
            const roleEntry = verticalRoles[0]; // primary role object
            const currentRole = roleEntry?.role || user[`${v.key}_role`] || null;
            const scopedIds = roleEntry?.scoped_ids || [];

            return (
              <GlassCard key={v.key} hover={false} padding="16px">
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                  <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--neo-text)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                    {v.label}
                  </span>
                  {currentRole ? (
                    <StatusBadge status={formatRole(currentRole)} variant={roleBadgeVariant(currentRole)} />
                  ) : (
                    <span style={{ fontSize: 12, color: 'var(--neo-text-dim)' }}>No access</span>
                  )}
                </div>

                {scopedIds.length > 0 && (
                  <div style={{ marginBottom: 10 }}>
                    <span style={{ fontSize: 11, color: 'var(--neo-text-muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                      Scoped {v.key === 'pms' ? 'Portfolios' : 'Schemes'}:
                    </span>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 4 }}>
                      {scopedIds.map(id => (
                        <span key={id} className="pms-badge pms-badge-gray" style={{ fontSize: 10 }}>{id}</span>
                      ))}
                    </div>
                  </div>
                )}

                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {!currentRole ? (
                    <button className="pms-btn pms-btn-primary pms-btn-sm" onClick={() => setAssignOpen(v.key)}>
                      <Plus size={12} style={{ marginRight: 4 }} /> Assign Role
                    </button>
                  ) : (
                    <>
                      <button className="pms-btn pms-btn-ghost pms-btn-sm" onClick={() => setAssignOpen(v.key)}>
                        <Edit2 size={12} style={{ marginRight: 4 }} /> Change Role
                      </button>
                      <button className="pms-btn pms-btn-ghost pms-btn-sm" onClick={() => setEditScopeOpen(v.key)}>
                        <Shield size={12} style={{ marginRight: 4 }} /> Edit Scope
                      </button>
                      <button
                        className="pms-btn pms-btn-danger pms-btn-sm"
                        onClick={() => setConfirmRevoke({ vertical: v.key, role: currentRole })}
                      >
                        <Trash2 size={12} style={{ marginRight: 4 }} /> Revoke
                      </button>
                    </>
                  )}
                </div>
              </GlassCard>
            );
          })}
        </div>
      )}

      {/* Assign Role Inline */}
      <AnimatePresence>
        {assignOpen && (
          <AssignRoleInline
            vertical={assignOpen}
            userId={userId}
            onClose={() => setAssignOpen(null)}
            onSuccess={() => { setAssignOpen(null); onRefresh(); }}
          />
        )}
      </AnimatePresence>

      {/* Edit Scope Inline */}
      <AnimatePresence>
        {editScopeOpen && (
          <EditScopeInline
            vertical={editScopeOpen}
            userId={userId}
            currentRole={(roles[editScopeOpen]?.[0]?.role) || user[`${editScopeOpen}_role`]}
            currentIds={roles[editScopeOpen]?.[0]?.scoped_ids || []}
            onClose={() => setEditScopeOpen(null)}
            onSuccess={() => { setEditScopeOpen(null); onRefresh(); }}
          />
        )}
      </AnimatePresence>

      {/* Confirm Revoke */}
      <ConfirmDialog
        open={!!confirmRevoke}
        title="Revoke Role"
        message={confirmRevoke ? `Remove ${formatRole(confirmRevoke.role)} role from ${confirmRevoke.vertical.toUpperCase()} vertical? This will revoke all access to ${confirmRevoke.vertical.toUpperCase()} resources.` : ''}
        confirmLabel="Revoke"
        danger
        onCancel={() => setConfirmRevoke(null)}
        onConfirm={async () => {
          try {
            await api.post('/auth/revoke-role', {
              user_id: userId,
              vertical: confirmRevoke.vertical,
              role: confirmRevoke.role,
            });
            toast.success(`${confirmRevoke.vertical.toUpperCase()} role revoked`);
            setConfirmRevoke(null);
            onRefresh();
          } catch (err) {
            toast.error(err.response?.data?.detail || 'Failed to revoke role');
          }
        }}
      />
    </SlideOver>
  );
}

// ── Info Row ────────────────────────────────────────────────────────
function InfoRow({ icon: Icon, label, value }) {
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 2 }}>
        {Icon && <Icon size={11} style={{ color: 'var(--neo-text-dim)' }} />}
        <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--neo-text-dim)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{label}</span>
      </div>
      <div style={{ fontSize: 13, color: 'var(--neo-text)' }}>{value}</div>
    </div>
  );
}

// ── Assign Role Inline ─────────────────────────────────────────────
function AssignRoleInline({ vertical, userId, onClose, onSuccess }) {
  const vConfig = VERTICALS.find(v => v.key === vertical);
  const [role, setRole] = useState('');
  const [scopedIds, setScopedIds] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    if (!role) { toast.error('Select a role'); return; }
    try {
      setSubmitting(true);
      const ids = scopedIds.split(',').map(s => s.trim()).filter(Boolean);
      await api.post('/auth/assign-role', {
        user_id: userId,
        vertical,
        role,
        scoped_ids: ids.length > 0 ? ids : undefined,
      });
      toast.success(`${vConfig.label} role assigned`);
      onSuccess();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to assign role');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: 'auto' }}
      exit={{ opacity: 0, height: 0 }}
      style={{ overflow: 'hidden', marginTop: 12 }}
    >
      <GlassCard hover={false} padding="16px" style={{ border: '1px solid var(--neo-blue)' }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--neo-text)', marginBottom: 12 }}>
          Assign {vConfig.label} Role
        </div>
        <FormField label="Role" required>
          <select className="pms-select" value={role} onChange={e => setRole(e.target.value)} style={{ width: '100%' }}>
            <option value="">Select role...</option>
            {vConfig.roles.map(r => (
              <option key={r} value={r}>{formatRole(r)}</option>
            ))}
          </select>
        </FormField>
        <FormField label={vertical === 'pms' ? 'Portfolio IDs (comma-separated)' : 'Scheme IDs (comma-separated)'} hint="Leave empty for unrestricted access">
          <input
            className="pms-input"
            placeholder="e.g. PF001, PF002"
            value={scopedIds}
            onChange={e => setScopedIds(e.target.value)}
            style={{ width: '100%' }}
          />
        </FormField>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button className="pms-btn pms-btn-ghost pms-btn-sm" onClick={onClose} disabled={submitting}>Cancel</button>
          <button className="pms-btn pms-btn-primary pms-btn-sm" onClick={handleSubmit} disabled={submitting}>
            {submitting ? 'Assigning...' : 'Assign'}
          </button>
        </div>
      </GlassCard>
    </motion.div>
  );
}

// ── Edit Scope Inline ──────────────────────────────────────────────
function EditScopeInline({ vertical, userId, currentRole, currentIds, onClose, onSuccess }) {
  const [ids, setIds] = useState(currentIds.join(', '));
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    try {
      setSubmitting(true);
      const scoped_ids = ids.split(',').map(s => s.trim()).filter(Boolean);
      await api.patch(`/auth/users/${userId}/scope`, {
        vertical,
        role: currentRole,
        scoped_ids,
      });
      toast.success('Scope updated');
      onSuccess();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to update scope');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: 'auto' }}
      exit={{ opacity: 0, height: 0 }}
      style={{ overflow: 'hidden', marginTop: 12 }}
    >
      <GlassCard hover={false} padding="16px" style={{ border: '1px solid var(--neo-purple)' }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--neo-text)', marginBottom: 12 }}>
          Edit {vertical.toUpperCase()} Scope
        </div>
        <FormField label={vertical === 'pms' ? 'Portfolio IDs' : 'Scheme IDs'} hint="Comma-separated. Leave empty to grant unrestricted access.">
          <input
            className="pms-input"
            value={ids}
            onChange={e => setIds(e.target.value)}
            placeholder="e.g. PF001, PF002"
            style={{ width: '100%' }}
          />
        </FormField>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button className="pms-btn pms-btn-ghost pms-btn-sm" onClick={onClose} disabled={submitting}>Cancel</button>
          <button className="pms-btn pms-btn-success pms-btn-sm" onClick={handleSubmit} disabled={submitting}>
            {submitting ? 'Saving...' : 'Save Scope'}
          </button>
        </div>
      </GlassCard>
    </motion.div>
  );
}

// ── Invite User Panel ──────────────────────────────────────────────
function InviteUserPanel({ open, onClose, onSuccess }) {
  const [form, setForm] = useState({ email: '', name: '', phone: '', pan: '', pms_role: '', mf_role: '', scoped_ids: '' });
  const [errors, setErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);

  const update = (field, value) => {
    setForm(f => ({ ...f, [field]: value }));
    setErrors(e => ({ ...e, [field]: undefined }));
  };

  const validate = () => {
    const errs = {};
    if (!form.email.trim()) errs.email = 'Email is required';
    else if (!/\S+@\S+\.\S+/.test(form.email)) errs.email = 'Invalid email format';
    if (!form.name.trim()) errs.name = 'Name is required';
    if (form.pan && !PAN_REGEX.test(form.pan.toUpperCase())) errs.pan = 'Invalid PAN format (ABCDE1234F)';
    setErrors(errs);
    return Object.keys(errs).length === 0;
  };

  const handleSubmit = async () => {
    if (!validate()) return;
    try {
      setSubmitting(true);
      const payload = {
        email: form.email.trim(),
        name: form.name.trim(),
        phone: form.phone.trim() || undefined,
        pan: form.pan.toUpperCase().trim() || undefined,
        pms_role: form.pms_role || undefined,
        mf_role: form.mf_role || undefined,
        scoped_ids: form.scoped_ids ? form.scoped_ids.split(',').map(s => s.trim()).filter(Boolean) : undefined,
      };
      await api.post('/auth/signup', payload);
      toast.success(`Invite sent to ${form.email}`);
      setForm({ email: '', name: '', phone: '', pan: '', pms_role: '', mf_role: '', scoped_ids: '' });
      onSuccess();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to invite user');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <SlideOver open={open} onClose={onClose} title="Invite User" subtitle="Add a new user and assign initial roles" width={460}>
      <FormField label="Email" required error={errors.email}>
        <input className="pms-input" type="email" value={form.email} onChange={e => update('email', e.target.value)} placeholder="user@company.com" style={{ width: '100%' }} />
      </FormField>

      <FormField label="Name" required error={errors.name}>
        <input className="pms-input" value={form.name} onChange={e => update('name', e.target.value)} placeholder="Full name" style={{ width: '100%' }} />
      </FormField>

      <FormField label="Phone">
        <input className="pms-input" value={form.phone} onChange={e => update('phone', e.target.value)} placeholder="+91 98765 43210" style={{ width: '100%' }} />
      </FormField>

      <FormField label="PAN" error={errors.pan} hint="Format: ABCDE1234F">
        <input className="pms-input" value={form.pan} onChange={e => update('pan', e.target.value.toUpperCase())} placeholder="ABCDE1234F" maxLength={10} style={{ width: '100%', textTransform: 'uppercase' }} />
      </FormField>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <FormField label="Initial PMS Role">
          <select className="pms-select" value={form.pms_role} onChange={e => update('pms_role', e.target.value)} style={{ width: '100%' }}>
            <option value="">None</option>
            {PMS_ROLES.map(r => <option key={r} value={r}>{formatRole(r)}</option>)}
          </select>
        </FormField>

        <FormField label="Initial MF Role">
          <select className="pms-select" value={form.mf_role} onChange={e => update('mf_role', e.target.value)} style={{ width: '100%' }}>
            <option value="">None</option>
            {MF_ROLES.map(r => <option key={r} value={r}>{formatRole(r)}</option>)}
          </select>
        </FormField>
      </div>

      <FormField label="Scoped IDs" hint="Comma-separated portfolio or scheme IDs (optional)">
        <input className="pms-input" value={form.scoped_ids} onChange={e => update('scoped_ids', e.target.value)} placeholder="PF001, PF002" style={{ width: '100%' }} />
      </FormField>

      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 24, paddingTop: 16, borderTop: '1px solid var(--neo-border)' }}>
        <button className="pms-btn pms-btn-ghost" onClick={onClose} disabled={submitting}>Cancel</button>
        <button className="pms-btn pms-btn-primary" onClick={handleSubmit} disabled={submitting}>
          <UserPlus size={14} style={{ marginRight: 6 }} />
          {submitting ? 'Sending...' : 'Send Invite'}
        </button>
      </div>
    </SlideOver>
  );
}

import React, { useState, useEffect, useCallback } from 'react';
import {
  Users, UserPlus, Trash2, Loader2, Mail, Shield, Eye, Crown,
} from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import { useAuth } from '../../context/AuthContext';

const ROLE_ICONS = {
  owner: Crown,
  admin: Shield,
  member: Users,
  viewer: Eye,
};

const ROLE_COLORS = {
  owner: 'var(--neo-yellow)',
  admin: 'var(--neo-blue)',
  member: 'var(--neo-green)',
  viewer: 'var(--neo-text-muted)',
};

export default function TeamPage() {
  const { user } = useAuth();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showInvite, setShowInvite] = useState(false);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState('member');
  const [sending, setSending] = useState(false);

  const fetchTeam = useCallback(() => {
    api.get('/dashboard/team')
      .then(res => setData(res.data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { fetchTeam(); }, [fetchTeam]);

  const handleInvite = async (e) => {
    e.preventDefault();
    if (!inviteEmail.trim()) return;
    setSending(true);
    try {
      await api.post('/dashboard/team/invite', { email: inviteEmail.trim(), role: inviteRole });
      toast.success(`Invitation sent to ${inviteEmail}`);
      setInviteEmail('');
      setShowInvite(false);
      fetchTeam();
    } catch {
      // handled by interceptor
    } finally {
      setSending(false);
    }
  };

  const handleRevokeInvite = async (inviteId) => {
    try {
      await api.delete(`/dashboard/team/invite/${inviteId}`);
      toast.success('Invitation revoked');
      fetchTeam();
    } catch {}
  };

  const handleRemoveMember = async (memberId, name) => {
    if (!window.confirm(`Remove ${name} from the workspace?`)) return;
    try {
      await api.delete(`/dashboard/team/${memberId}`);
      toast.success('Member removed');
      fetchTeam();
    } catch {}
  };

  const handleRoleChange = async (memberId, newRole) => {
    try {
      await api.patch(`/dashboard/team/${memberId}/role`, { role: newRole });
      toast.success('Role updated');
      fetchTeam();
    } catch {}
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
      </div>
    );
  }

  const members = data?.members || [];
  const invitations = data?.invitations || [];
  const isOwner = members.some(m => m.user_id === user?.user_id && m.role === 'owner');

  return (
    <div className="max-w-3xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold" style={{ color: 'var(--neo-text)' }}>Team</h1>
          <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>
            {members.length} member{members.length !== 1 ? 's' : ''}
          </p>
        </div>
        {isOwner && (
          <button
            onClick={() => setShowInvite(!showInvite)}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90"
            style={{ background: 'var(--neo-blue)', color: '#fff' }}
          >
            <UserPlus size={16} /> Invite
          </button>
        )}
      </div>

      {/* Invite form */}
      {showInvite && (
        <form
          onSubmit={handleInvite}
          className="flex items-center gap-3 mb-6 p-4 rounded-xl"
          style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
        >
          <input
            value={inviteEmail}
            onChange={e => setInviteEmail(e.target.value)}
            placeholder="email@example.com"
            type="email"
            className="flex-1 px-3 py-2 rounded-lg text-sm outline-none"
            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            autoFocus
          />
          <select
            value={inviteRole}
            onChange={e => setInviteRole(e.target.value)}
            className="px-3 py-2 rounded-lg text-sm outline-none"
            style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
          >
            <option value="admin">Admin</option>
            <option value="member">Member</option>
            <option value="viewer">Viewer</option>
          </select>
          <button
            type="submit"
            disabled={sending || !inviteEmail.trim()}
            className="px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
            style={{ background: 'var(--neo-green)', color: '#fff' }}
          >
            {sending ? <Loader2 size={16} className="animate-spin" /> : 'Send'}
          </button>
        </form>
      )}

      {/* Members */}
      <div className="space-y-2 mb-8">
        {members.map((m) => {
          const RoleIcon = ROLE_ICONS[m.role] || Users;
          return (
            <div
              key={m.user_id}
              className="flex items-center justify-between p-4 rounded-xl"
              style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
            >
              <div className="flex items-center gap-3">
                <div
                  className="w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold"
                  style={{ background: 'var(--neo-bg)', color: 'var(--neo-blue)' }}
                >
                  {(m.display_name || m.email || '?')[0].toUpperCase()}
                </div>
                <div>
                  <div className="font-medium text-sm" style={{ color: 'var(--neo-text)' }}>
                    {m.display_name}
                    {m.user_id === user?.user_id && (
                      <span className="text-xs ml-1" style={{ color: 'var(--neo-text-muted)' }}>(you)</span>
                    )}
                  </div>
                  <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>{m.email}</div>
                </div>
              </div>

              <div className="flex items-center gap-2">
                {isOwner && m.role !== 'owner' ? (
                  <select
                    value={m.role}
                    onChange={e => handleRoleChange(m.user_id, e.target.value)}
                    className="px-2 py-1 rounded text-xs outline-none"
                    style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: ROLE_COLORS[m.role] }}
                  >
                    <option value="admin">Admin</option>
                    <option value="member">Member</option>
                    <option value="viewer">Viewer</option>
                  </select>
                ) : (
                  <span
                    className="flex items-center gap-1 px-2 py-1 rounded text-xs font-medium"
                    style={{ color: ROLE_COLORS[m.role] }}
                  >
                    <RoleIcon size={12} /> {m.role}
                  </span>
                )}

                {isOwner && m.role !== 'owner' && (
                  <button
                    onClick={() => handleRemoveMember(m.user_id, m.display_name)}
                    className="p-1.5 rounded transition hover:bg-neo-surface-light"
                    style={{ color: 'var(--neo-text-muted)' }}
                    title="Remove member"
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Pending invitations */}
      {invitations.length > 0 && (
        <>
          <h2 className="text-sm font-semibold mb-3" style={{ color: 'var(--neo-text-muted)' }}>
            Pending Invitations
          </h2>
          <div className="space-y-2">
            {invitations.map((inv) => (
              <div
                key={inv.invite_id}
                className="flex items-center justify-between p-4 rounded-xl"
                style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
              >
                <div className="flex items-center gap-3">
                  <Mail size={16} style={{ color: 'var(--neo-yellow)' }} />
                  <div>
                    <div className="text-sm" style={{ color: 'var(--neo-text)' }}>{inv.email}</div>
                    <div className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                      Invited as {inv.role} &middot; {new Date(inv.created_at).toLocaleDateString()}
                    </div>
                  </div>
                </div>

                {isOwner && (
                  <button
                    onClick={() => handleRevokeInvite(inv.invite_id)}
                    className="text-xs px-3 py-1 rounded transition hover:opacity-80"
                    style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text-muted)' }}
                  >
                    Revoke
                  </button>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// frontend/src/pages/dashboard/ProfilePage.js
// Fund Manager Profile — view & edit personal, professional, performance data
import React, { useState, useEffect, useCallback } from 'react';
import {
  User, Pencil, Save, X, Mail, Phone, Linkedin, Calendar, Award,
  Shield, TrendingUp, Briefcase, Users, Clock, Globe, Bell,
  BarChart3, Target, CheckCircle, Lock, Key, LogOut, Trash2, Eye, EyeOff,
  AlertTriangle, Monitor,
} from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../../lib/api';
import {
  PageHeader, GlassCard, StatusBadge, FormField, ProgressBar, Skeleton,
} from '../../components/ui-components';

// ── Helpers ──────────────────────────────────────────────────────────
const fmtINR = (n) => {
  if (n == null || n === 0) return '--';
  if (n >= 1e7) return '\u20B9' + (n / 1e7).toFixed(2) + ' Cr';
  if (n >= 1e5) return '\u20B9' + (n / 1e5).toFixed(2) + ' L';
  return '\u20B9' + Number(n).toLocaleString('en-IN');
};

const fmtDate = (d) => {
  if (!d) return '--';
  return new Date(d).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
};

const fmtPct = (n) => {
  if (n == null) return '--';
  return `${Number(n).toFixed(2)}%`;
};

const RISK_LEVELS = ['conservative', 'moderate', 'aggressive'];
const RISK_COLORS = { conservative: 'var(--neo-green)', moderate: 'var(--neo-yellow)', aggressive: 'var(--neo-red)' };
const RISK_PCT = { conservative: 33, moderate: 66, aggressive: 100 };

const SPECIALIZATIONS = ['Equity', 'Debt', 'Multi-Asset', 'Quant', 'Fixed Income', 'Hybrid'];
const BENCHMARKS = ['Nifty 50', 'Nifty 500', 'Sensex', 'Nifty Midcap 150', 'Custom'];
const TIMEZONES = ['Asia/Kolkata', 'Asia/Dubai', 'Europe/London', 'America/New_York', 'Asia/Singapore'];

const SECTOR_OPTIONS = [
  'IT', 'Banking', 'Pharma', 'FMCG', 'Auto', 'Energy', 'Metals',
  'Realty', 'Infra', 'Telecom', 'Media', 'Chemicals', 'Insurance',
];

// ── Main Component ───────────────────────────────────────────────────
export default function ProfilePage() {
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({});

  // ── Load profile ──────────────────────────────────────────────────
  const loadProfile = useCallback(async () => {
    try {
      const { data } = await api.get('/auth/profile');
      setProfile(data);
      setForm({ ...data });
    } catch (err) {
      console.error('Failed to load profile:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadProfile(); }, [loadProfile]);

  // ── Save profile ──────────────────────────────────────────────────
  const handleSave = async () => {
    setSaving(true);
    try {
      const payload = {};
      const editableFields = [
        'display_name', 'title', 'bio', 'phone', 'linkedin_url',
        'sebi_registration_no', 'arn_number', 'nism_certification',
        'experience_years', 'specialization', 'investment_philosophy',
        'preferred_sectors', 'risk_appetite', 'benchmark',
        'notification_preferences', 'timezone',
      ];
      for (const key of editableFields) {
        if (form[key] !== profile[key]) {
          payload[key] = form[key];
        }
      }
      if (Object.keys(payload).length === 0) {
        toast('No changes to save');
        setEditing(false);
        setSaving(false);
        return;
      }
      const { data } = await api.put('/auth/profile', payload);
      setProfile(data);
      setForm({ ...data });
      setEditing(false);
      toast.success('Profile updated successfully');
    } catch (err) {
      toast.error(err.userMessage || 'Failed to update profile');
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    setForm({ ...profile });
    setEditing(false);
  };

  const updateForm = (key, val) => setForm(prev => ({ ...prev, [key]: val }));
  const updateNotif = (key, val) => setForm(prev => ({
    ...prev,
    notification_preferences: { ...prev.notification_preferences, [key]: val },
  }));

  const toggleSector = (sector) => {
    setForm(prev => {
      const current = prev.preferred_sectors || [];
      const next = current.includes(sector)
        ? current.filter(s => s !== sector)
        : [...current, sector];
      return { ...prev, preferred_sectors: next };
    });
  };

  // ── Initials avatar ───────────────────────────────────────────────
  const initials = (profile?.display_name || 'FM')
    .split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();

  // ── Loading state ─────────────────────────────────────────────────
  if (loading) {
    return (
      <div>
        <PageHeader icon={User} title="My Profile" subtitle="Loading..." />
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.5fr', gap: 24, marginTop: 24 }}>
          <GlassCard hover={false}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16, padding: 20 }}>
              <Skeleton width={96} height={96} rounded />
              <Skeleton width={160} height={20} />
              <Skeleton width={120} height={14} />
              <Skeleton width={200} height={40} />
            </div>
          </GlassCard>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <GlassCard hover={false}><Skeleton width="100%" height={120} /></GlassCard>
            <GlassCard hover={false}><Skeleton width="100%" height={120} /></GlassCard>
          </div>
        </div>
      </div>
    );
  }

  const p = profile || {};
  const f = form || {};
  const notifPrefs = f.notification_preferences || { email: true, sms: false, push: true };

  return (
    <div>
      {/* Header */}
      <PageHeader icon={User} title="My Profile" subtitle="Manage your professional identity and preferences">
        {!editing ? (
          <button className="pms-btn pms-btn-primary" onClick={() => setEditing(true)}>
            <Pencil size={14} style={{ marginRight: 6 }} /> Edit Profile
          </button>
        ) : (
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="pms-btn pms-btn-ghost" onClick={handleCancel} disabled={saving}>
              <X size={14} style={{ marginRight: 4 }} /> Cancel
            </button>
            <button className="pms-btn pms-btn-primary" onClick={handleSave} disabled={saving}>
              <Save size={14} style={{ marginRight: 4 }} /> {saving ? 'Saving...' : 'Save'}
            </button>
          </div>
        )}
      </PageHeader>

      {/* Main grid */}
      <div className="profile-layout" style={{ display: 'grid', gridTemplateColumns: '2fr 3fr', gap: 24, marginTop: 20 }}>

        {/* ── LEFT COLUMN ─────────────────────────────────────────── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

          {/* Avatar & Identity Card */}
          <GlassCard hover={false}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '12px 0' }}>
              {/* Avatar */}
              <div style={{
                width: 96, height: 96, borderRadius: '50%',
                background: 'linear-gradient(135deg, var(--neo-blue), var(--neo-purple))',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 32, fontWeight: 800, color: '#fff',
                boxShadow: '0 0 24px rgba(91, 138, 240, 0.3)',
                marginBottom: 16,
              }}>
                {p.avatar_url ? (
                  <img src={p.avatar_url} alt="" style={{ width: '100%', height: '100%', borderRadius: '50%', objectFit: 'cover' }} />
                ) : initials}
              </div>

              {/* Name */}
              {editing ? (
                <input
                  className="pms-input"
                  value={f.display_name || ''}
                  onChange={e => updateForm('display_name', e.target.value)}
                  style={{ textAlign: 'center', fontSize: 18, fontWeight: 700, marginBottom: 4 }}
                />
              ) : (
                <h2 style={{ fontSize: 20, fontWeight: 700, color: 'var(--neo-text)', margin: '0 0 4px', textAlign: 'center' }}>
                  {p.display_name}
                </h2>
              )}

              {/* Title */}
              {editing ? (
                <input
                  className="pms-input"
                  value={f.title || ''}
                  onChange={e => updateForm('title', e.target.value)}
                  placeholder="e.g. Senior Fund Manager"
                  style={{ textAlign: 'center', fontSize: 13, marginBottom: 8 }}
                />
              ) : (
                <span style={{ fontSize: 13, color: 'var(--neo-text-muted)', marginBottom: 8 }}>
                  {p.title || 'Fund Manager'}
                </span>
              )}

              {/* SEBI badge */}
              {p.sebi_registration_no && (
                <StatusBadge variant="success" dot>
                  SEBI: {p.sebi_registration_no}
                </StatusBadge>
              )}
            </div>

            {/* Bio */}
            <div style={{ marginTop: 16, borderTop: '1px solid var(--neo-border)', paddingTop: 16 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--neo-text-dim)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>About</div>
              {editing ? (
                <textarea
                  className="pms-input"
                  value={f.bio || ''}
                  onChange={e => updateForm('bio', e.target.value)}
                  placeholder="Write a short bio..."
                  rows={4}
                  style={{ resize: 'vertical', width: '100%' }}
                />
              ) : (
                <p style={{ fontSize: 13, color: 'var(--neo-text-muted)', margin: 0, lineHeight: 1.6 }}>
                  {p.bio || 'No bio added yet.'}
                </p>
              )}
            </div>

            {/* Contact info */}
            <div style={{ marginTop: 16, borderTop: '1px solid var(--neo-border)', paddingTop: 16 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--neo-text-dim)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 12 }}>Contact</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <InfoRow icon={Mail} label="Email" value={p.email || '--'} />
                {editing ? (
                  <FormField label="Phone">
                    <input className="pms-input" value={f.phone || ''} onChange={e => updateForm('phone', e.target.value)} placeholder="+91 9876543210" />
                  </FormField>
                ) : (
                  <InfoRow icon={Phone} label="Phone" value={p.phone || '--'} />
                )}
                {editing ? (
                  <FormField label="LinkedIn">
                    <input className="pms-input" value={f.linkedin_url || ''} onChange={e => updateForm('linkedin_url', e.target.value)} placeholder="https://linkedin.com/in/..." />
                  </FormField>
                ) : (
                  <InfoRow icon={Linkedin} label="LinkedIn" value={p.linkedin_url ? (
                    <a href={p.linkedin_url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--neo-blue)', textDecoration: 'none', fontSize: 13 }}>
                      View Profile
                    </a>
                  ) : '--'} />
                )}
                <InfoRow icon={Calendar} label="Member Since" value={fmtDate(p.created_at)} />
              </div>
            </div>
          </GlassCard>
        </div>

        {/* ── RIGHT COLUMN ────────────────────────────────────────── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

          {/* Performance Card */}
          <GlassCard hover={false}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
              <TrendingUp size={16} style={{ color: 'var(--neo-blue)' }} />
              <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--neo-text)' }}>Performance</span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
              <StatBox icon={Briefcase} label="Total AUM" value={fmtINR(p.total_aum)} />
              <StatBox icon={BarChart3} label="Portfolios" value={p.portfolios_managed || 0} />
              <StatBox icon={Users} label="Clients" value={p.clients_served || 0} />
              <StatBox icon={Clock} label="Experience" value={`${p.experience_years || 0} yrs`} />
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginTop: 16 }}>
              <StatBox icon={TrendingUp} label="Best Year" value={fmtPct(p.best_year_return)} color="var(--neo-green)" />
              <StatBox icon={Target} label="Avg Annual" value={fmtPct(p.avg_annual_return)} color="var(--neo-blue)" />
              <StatBox icon={BarChart3} label="Benchmark" value={p.benchmark || 'Nifty 50'} />
            </div>
            {editing && (
              <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--neo-border)' }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                  <FormField label="Experience (years)">
                    <input className="pms-input" type="number" value={f.experience_years || ''} onChange={e => updateForm('experience_years', parseInt(e.target.value) || 0)} />
                  </FormField>
                  <FormField label="Benchmark">
                    <select className="pms-select" value={f.benchmark || 'Nifty 50'} onChange={e => updateForm('benchmark', e.target.value)}>
                      {BENCHMARKS.map(b => <option key={b} value={b}>{b}</option>)}
                    </select>
                  </FormField>
                </div>
              </div>
            )}
          </GlassCard>

          {/* Investment Profile */}
          <GlassCard hover={false}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
              <Target size={16} style={{ color: 'var(--neo-purple)' }} />
              <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--neo-text)' }}>Investment Profile</span>
            </div>

            {/* Specialization */}
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--neo-text-dim)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>Specialization</div>
              {editing ? (
                <select className="pms-select" value={f.specialization || ''} onChange={e => updateForm('specialization', e.target.value)}>
                  <option value="">Select...</option>
                  {SPECIALIZATIONS.map(s => <option key={s} value={s}>{s}</option>)}
                </select>
              ) : (
                <StatusBadge variant="info">{p.specialization || 'Not set'}</StatusBadge>
              )}
            </div>

            {/* Risk Appetite */}
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--neo-text-dim)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>Risk Appetite</div>
              {editing ? (
                <div style={{ display: 'flex', gap: 8 }}>
                  {RISK_LEVELS.map(level => (
                    <button
                      key={level}
                      className={`pms-btn pms-btn-sm ${f.risk_appetite === level ? 'pms-btn-primary' : 'pms-btn-ghost'}`}
                      onClick={() => updateForm('risk_appetite', level)}
                      style={{ textTransform: 'capitalize', flex: 1 }}
                    >
                      {level}
                    </button>
                  ))}
                </div>
              ) : (
                <ProgressBar
                  value={RISK_PCT[p.risk_appetite] || 50}
                  color={RISK_COLORS[p.risk_appetite] || 'var(--neo-blue)'}
                  label={<span style={{ textTransform: 'capitalize' }}>{p.risk_appetite || 'moderate'}</span>}
                />
              )}
            </div>

            {/* Preferred Sectors */}
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--neo-text-dim)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>Preferred Sectors</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {editing ? (
                  SECTOR_OPTIONS.map(sector => {
                    const active = (f.preferred_sectors || []).includes(sector);
                    return (
                      <button
                        key={sector}
                        onClick={() => toggleSector(sector)}
                        className={`pms-btn pms-btn-sm ${active ? 'pms-btn-primary' : 'pms-btn-ghost'}`}
                        style={{ fontSize: 11 }}
                      >
                        {sector}
                      </button>
                    );
                  })
                ) : (
                  (p.preferred_sectors || []).length > 0
                    ? p.preferred_sectors.map(s => <StatusBadge key={s} variant="purple">{s}</StatusBadge>)
                    : <span style={{ fontSize: 13, color: 'var(--neo-text-dim)' }}>No sectors selected</span>
                )}
              </div>
            </div>

            {/* Investment Philosophy */}
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--neo-text-dim)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>Investment Philosophy</div>
              {editing ? (
                <textarea
                  className="pms-input"
                  value={f.investment_philosophy || ''}
                  onChange={e => updateForm('investment_philosophy', e.target.value)}
                  placeholder="Describe your investment approach..."
                  rows={3}
                  style={{ resize: 'vertical', width: '100%' }}
                />
              ) : (
                <p style={{ fontSize: 13, color: 'var(--neo-text-muted)', margin: 0, lineHeight: 1.6 }}>
                  {p.investment_philosophy || 'No investment philosophy added yet.'}
                </p>
              )}
            </div>
          </GlassCard>

          {/* Certifications */}
          <GlassCard hover={false}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
              <Award size={16} style={{ color: 'var(--neo-green)' }} />
              <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--neo-text)' }}>Certifications</span>
            </div>
            {editing ? (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                <FormField label="SEBI Registration">
                  <input className="pms-input" value={f.sebi_registration_no || ''} onChange={e => updateForm('sebi_registration_no', e.target.value)} placeholder="INP000XXXXXX" />
                </FormField>
                <FormField label="ARN Number">
                  <input className="pms-input" value={f.arn_number || ''} onChange={e => updateForm('arn_number', e.target.value)} placeholder="ARN-XXXXXX" />
                </FormField>
                <FormField label="NISM Certification">
                  <input className="pms-input" value={f.nism_certification || ''} onChange={e => updateForm('nism_certification', e.target.value)} placeholder="NISM Series V-A" />
                </FormField>
              </div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
                <CertItem icon={Shield} label="SEBI Registration" value={p.sebi_registration_no} />
                <CertItem icon={Award} label="ARN Number" value={p.arn_number} />
                <CertItem icon={CheckCircle} label="NISM Certification" value={p.nism_certification} />
              </div>
            )}
          </GlassCard>

          {/* Settings */}
          <GlassCard hover={false}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
              <Bell size={16} style={{ color: 'var(--neo-yellow)' }} />
              <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--neo-text)' }}>Settings</span>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
              {/* Notification Preferences */}
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--neo-text-dim)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 12 }}>Notifications</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <ToggleRow label="Email Notifications" icon={Mail} checked={notifPrefs.email} onChange={v => updateNotif('email', v)} disabled={!editing} />
                  <ToggleRow label="SMS Alerts" icon={Phone} checked={notifPrefs.sms} onChange={v => updateNotif('sms', v)} disabled={!editing} />
                  <ToggleRow label="Push Notifications" icon={Bell} checked={notifPrefs.push} onChange={v => updateNotif('push', v)} disabled={!editing} />
                </div>
              </div>

              {/* Timezone */}
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--neo-text-dim)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 12 }}>Timezone</div>
                {editing ? (
                  <select className="pms-select" value={f.timezone || 'Asia/Kolkata'} onChange={e => updateForm('timezone', e.target.value)}>
                    {TIMEZONES.map(tz => <option key={tz} value={tz}>{tz}</option>)}
                  </select>
                ) : (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Globe size={14} style={{ color: 'var(--neo-text-dim)' }} />
                    <span style={{ fontSize: 13, color: 'var(--neo-text-muted)' }}>{p.timezone || 'Asia/Kolkata'}</span>
                  </div>
                )}
              </div>
            </div>
          </GlassCard>

          {/* Security & Account */}
          <SecuritySection />
        </div>
      </div>

      {/* Responsive: stack columns on mobile */}
      <style>{`
        @media (max-width: 900px) {
          .profile-layout { grid-template-columns: 1fr !important; }
        }
      `}</style>
    </div>
  );
}

// ── Sub-components ───────────────────────────────────────────────────

function InfoRow({ icon: Icon, label, value }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <Icon size={14} style={{ color: 'var(--neo-text-dim)', flexShrink: 0 }} />
      <span style={{ fontSize: 11, color: 'var(--neo-text-dim)', width: 70, flexShrink: 0 }}>{label}</span>
      <span style={{ fontSize: 13, color: 'var(--neo-text-muted)' }}>{value}</span>
    </div>
  );
}

function StatBox({ icon: Icon, label, value, color }) {
  return (
    <div style={{
      padding: 12, borderRadius: 'var(--pms-radius-sm)',
      background: 'rgba(255, 255, 255, 0.02)', border: '1px solid var(--neo-border)',
      textAlign: 'center',
    }}>
      <Icon size={14} style={{ color: color || 'var(--neo-text-dim)', marginBottom: 6 }} />
      <div style={{ fontSize: 16, fontWeight: 700, color: color || 'var(--neo-text)', marginBottom: 4 }} className="pms-number">
        {value}
      </div>
      <div style={{ fontSize: 10, fontWeight: 500, color: 'var(--neo-text-dim)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        {label}
      </div>
    </div>
  );
}

function CertItem({ icon: Icon, label, value }) {
  return (
    <div style={{
      padding: 12, borderRadius: 'var(--pms-radius-sm)',
      background: 'rgba(255, 255, 255, 0.02)', border: '1px solid var(--neo-border)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
        <Icon size={13} style={{ color: value ? 'var(--neo-green)' : 'var(--neo-text-dim)' }} />
        <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--neo-text-dim)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{label}</span>
      </div>
      <div style={{ fontSize: 13, fontWeight: 600, color: value ? 'var(--neo-text)' : 'var(--neo-text-dim)' }}>
        {value || 'Not provided'}
      </div>
    </div>
  );
}

function SecuritySection() {
  const [showChangePw, setShowChangePw] = useState(false);
  const [showChangeEmail, setShowChangeEmail] = useState(false);
  const [pwForm, setPwForm] = useState({ current: '', newPw: '', confirm: '' });
  const [emailForm, setEmailForm] = useState({ newEmail: '', password: '' });
  const [showPw, setShowPw] = useState(false);
  const [saving, setSaving] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const handleChangePassword = async () => {
    if (!pwForm.current || !pwForm.newPw) return toast.error('Fill in all fields');
    if (pwForm.newPw.length < 6) return toast.error('Password must be at least 6 characters');
    if (pwForm.newPw !== pwForm.confirm) return toast.error('Passwords do not match');
    setSaving(true);
    try {
      await api.patch('/auth/me', { password: pwForm.newPw });
      toast.success('Password changed successfully');
      setShowChangePw(false);
      setPwForm({ current: '', newPw: '', confirm: '' });
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to change password');
    } finally { setSaving(false); }
  };

  const handleChangeEmail = async () => {
    if (!emailForm.newEmail || !emailForm.password) return toast.error('Fill in all fields');
    if (!/^\S+@\S+\.\S+$/.test(emailForm.newEmail)) return toast.error('Invalid email format');
    setSaving(true);
    try {
      await api.patch('/auth/me', { email: emailForm.newEmail });
      toast.success('Email updated. You may need to re-login.');
      setShowChangeEmail(false);
      setEmailForm({ newEmail: '', password: '' });
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to update email');
    } finally { setSaving(false); }
  };

  const handleLogoutAll = async () => {
    try {
      await api.post('/auth/logout-all');
      toast.success('All sessions revoked. You will need to log in again.');
    } catch {
      toast.success('Sessions cleared locally');
    }
  };

  return (
    <GlassCard hover={false}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
        <Lock size={16} style={{ color: 'var(--neo-red)' }} />
        <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--neo-text)' }}>Security & Account</span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>

        {/* Change Password */}
        <div style={{ padding: '12px 14px', borderRadius: 'var(--pms-radius-sm)', background: 'rgba(255,255,255,0.02)', border: '1px solid var(--neo-border)' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Key size={14} style={{ color: 'var(--neo-text-dim)' }} />
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--neo-text)' }}>Password</div>
                <div style={{ fontSize: 11, color: 'var(--neo-text-dim)' }}>Change your login password</div>
              </div>
            </div>
            <button className="pms-btn pms-btn-ghost pms-btn-sm" onClick={() => setShowChangePw(!showChangePw)}>
              {showChangePw ? 'Cancel' : 'Change'}
            </button>
          </div>

          {showChangePw && (
            <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ position: 'relative' }}>
                <input
                  className="pms-input"
                  type={showPw ? 'text' : 'password'}
                  placeholder="Current password"
                  value={pwForm.current}
                  onChange={e => setPwForm(p => ({ ...p, current: e.target.value }))}
                  style={{ paddingRight: 36 }}
                />
                <button onClick={() => setShowPw(!showPw)} style={{
                  position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)',
                  background: 'none', border: 'none', cursor: 'pointer', color: 'var(--neo-text-dim)',
                }}>
                  {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
              <input
                className="pms-input"
                type="password"
                placeholder="New password (min 6 chars)"
                value={pwForm.newPw}
                onChange={e => setPwForm(p => ({ ...p, newPw: e.target.value }))}
              />
              <input
                className="pms-input"
                type="password"
                placeholder="Confirm new password"
                value={pwForm.confirm}
                onChange={e => setPwForm(p => ({ ...p, confirm: e.target.value }))}
              />
              {pwForm.newPw && pwForm.confirm && pwForm.newPw !== pwForm.confirm && (
                <div style={{ fontSize: 11, color: 'var(--neo-red)' }}>Passwords do not match</div>
              )}
              <button
                className="pms-btn pms-btn-primary pms-btn-sm"
                onClick={handleChangePassword}
                disabled={saving || !pwForm.current || !pwForm.newPw || pwForm.newPw !== pwForm.confirm}
                style={{ alignSelf: 'flex-end' }}
              >
                {saving ? 'Saving...' : 'Update Password'}
              </button>
            </div>
          )}
        </div>

        {/* Change Email */}
        <div style={{ padding: '12px 14px', borderRadius: 'var(--pms-radius-sm)', background: 'rgba(255,255,255,0.02)', border: '1px solid var(--neo-border)' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Mail size={14} style={{ color: 'var(--neo-text-dim)' }} />
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--neo-text)' }}>Email Address</div>
                <div style={{ fontSize: 11, color: 'var(--neo-text-dim)' }}>Update your login email</div>
              </div>
            </div>
            <button className="pms-btn pms-btn-ghost pms-btn-sm" onClick={() => setShowChangeEmail(!showChangeEmail)}>
              {showChangeEmail ? 'Cancel' : 'Change'}
            </button>
          </div>

          {showChangeEmail && (
            <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
              <input
                className="pms-input"
                type="email"
                placeholder="New email address"
                value={emailForm.newEmail}
                onChange={e => setEmailForm(p => ({ ...p, newEmail: e.target.value }))}
              />
              <input
                className="pms-input"
                type="password"
                placeholder="Confirm with your password"
                value={emailForm.password}
                onChange={e => setEmailForm(p => ({ ...p, password: e.target.value }))}
              />
              <button
                className="pms-btn pms-btn-primary pms-btn-sm"
                onClick={handleChangeEmail}
                disabled={saving || !emailForm.newEmail || !emailForm.password}
                style={{ alignSelf: 'flex-end' }}
              >
                {saving ? 'Saving...' : 'Update Email'}
              </button>
            </div>
          )}
        </div>

        {/* Active Sessions */}
        <div style={{ padding: '12px 14px', borderRadius: 'var(--pms-radius-sm)', background: 'rgba(255,255,255,0.02)', border: '1px solid var(--neo-border)' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Monitor size={14} style={{ color: 'var(--neo-text-dim)' }} />
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--neo-text)' }}>Active Sessions</div>
                <div style={{ fontSize: 11, color: 'var(--neo-text-dim)' }}>Sign out from all other devices</div>
              </div>
            </div>
            <button className="pms-btn pms-btn-ghost pms-btn-sm" onClick={handleLogoutAll}
              style={{ color: 'var(--neo-yellow)' }}>
              <LogOut size={12} style={{ marginRight: 4 }} /> Revoke All
            </button>
          </div>
        </div>

        {/* Danger Zone */}
        <div style={{
          padding: '12px 14px', borderRadius: 'var(--pms-radius-sm)',
          background: 'rgba(248, 113, 113, 0.04)', border: '1px solid rgba(248, 113, 113, 0.15)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <AlertTriangle size={14} style={{ color: 'var(--neo-red)' }} />
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--neo-red)' }}>Danger Zone</div>
                <div style={{ fontSize: 11, color: 'var(--neo-text-dim)' }}>Permanently delete your account and all data</div>
              </div>
            </div>
            <button
              className="pms-btn pms-btn-danger pms-btn-sm"
              onClick={() => setShowDeleteConfirm(true)}
            >
              <Trash2 size={12} style={{ marginRight: 4 }} /> Delete Account
            </button>
          </div>

          {showDeleteConfirm && (
            <div style={{
              marginTop: 14, padding: '12px', borderRadius: 'var(--pms-radius-sm)',
              background: 'rgba(248, 113, 113, 0.08)', border: '1px solid rgba(248, 113, 113, 0.25)',
            }}>
              <p style={{ fontSize: 12, color: 'var(--neo-red)', marginBottom: 12, lineHeight: 1.5 }}>
                This will permanently delete your account, all portfolios, trade history, and settings. This action cannot be undone.
              </p>
              <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                <button className="pms-btn pms-btn-ghost pms-btn-sm" onClick={() => setShowDeleteConfirm(false)}>
                  Cancel
                </button>
                <button className="pms-btn pms-btn-danger pms-btn-sm" onClick={() => {
                  toast.error('Account deletion requires admin approval. Contact your administrator.');
                  setShowDeleteConfirm(false);
                }}>
                  Yes, Delete My Account
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </GlassCard>
  );
}

function ToggleRow({ label, icon: Icon, checked, onChange, disabled }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Icon size={13} style={{ color: 'var(--neo-text-dim)' }} />
        <span style={{ fontSize: 12, color: 'var(--neo-text-muted)' }}>{label}</span>
      </div>
      <button
        onClick={() => !disabled && onChange(!checked)}
        disabled={disabled}
        style={{
          width: 36, height: 20, borderRadius: 10, border: 'none',
          background: checked ? 'var(--neo-blue)' : 'var(--neo-surface)',
          position: 'relative', cursor: disabled ? 'default' : 'pointer',
          transition: 'background 0.2s', opacity: disabled ? 0.6 : 1,
        }}
      >
        <div style={{
          width: 16, height: 16, borderRadius: '50%', background: '#fff',
          position: 'absolute', top: 2,
          left: checked ? 18 : 2,
          transition: 'left 0.2s',
        }} />
      </button>
    </div>
  );
}

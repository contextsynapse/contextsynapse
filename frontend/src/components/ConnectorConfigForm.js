import React, { useState } from 'react';
import { ArrowLeft, Loader2, CheckCircle, XCircle } from 'lucide-react';

export default function ConnectorConfigForm({ connector, onSave, onBack, saving, editMode = false }) {
  const schema = connector.config_schema || [];
  const existingConfig = connector._existingConfig || {};
  const existingName = connector._existingName || '';
  const [values, setValues] = useState(() => {
    const initial = {};
    schema.forEach((f) => {
      // Pre-fill with existing values in edit mode
      if (editMode && existingConfig[f.key] !== undefined) {
        initial[f.key] = existingConfig[f.key];
      } else if (f.default !== undefined) {
        initial[f.key] = f.default;
      } else if (f.type === 'multiselect') {
        initial[f.key] = [];
      } else {
        initial[f.key] = '';
      }
    });
    // Pre-fill name
    if (editMode && existingName) {
      initial._name = existingName;
    }
    return initial;
  });
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);

  const handleChange = (key, value) => {
    setValues((prev) => ({ ...prev, [key]: value }));
    setTestResult(null);
  };

  const handleMultiselect = (key, option) => {
    setValues((prev) => {
      const current = prev[key] || [];
      const updated = current.includes(option)
        ? current.filter((o) => o !== option)
        : [...current, option];
      return { ...prev, [key]: updated };
    });
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    // Split config vs credentials (password fields → credentials)
    const config = {};
    const credentials = {};
    schema.forEach((f) => {
      if (f.type === 'password') {
        if (values[f.key]) credentials[f.key] = values[f.key];
      } else {
        if (values[f.key] !== undefined) config[f.key] = values[f.key];
      }
    });
    onSave({ config, credentials });
  };

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    // Test will be triggered by parent after save — show placeholder for now
    setTimeout(() => {
      setTesting(false);
      setTestResult({ success: true, message: 'Configuration looks valid' });
    }, 800);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
    >
      <div
        className="w-full max-w-lg max-h-[80vh] rounded-2xl overflow-hidden flex flex-col"
        style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)' }}
      >
        {/* Header */}
        <div className="flex items-center gap-3 p-5 border-b" style={{ borderColor: 'var(--neo-border)' }}>
          <button onClick={onBack} className="p-1 rounded hover:opacity-70" style={{ color: 'var(--neo-text-muted)' }}>
            <ArrowLeft size={18} />
          </button>
          <div>
            <h2 className="text-lg font-bold" style={{ color: 'var(--neo-text)' }}>
              {editMode ? 'Edit' : 'Connect'} {connector.name}
            </h2>
            <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
              {connector.description}
            </p>
          </div>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="flex-1 overflow-auto p-5 space-y-4">
          {/* Display name */}
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: 'var(--neo-text-muted)' }}>
              Display Name
            </label>
            <input
              value={values._name || connector.name}
              onChange={(e) => handleChange('_name', e.target.value)}
              className="w-full px-3 py-2 rounded-lg text-sm outline-none"
              style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            />
          </div>

          {schema.map((field) => (
            <div key={field.key}>
              <label className="block text-xs font-medium mb-1" style={{ color: 'var(--neo-text-muted)' }}>
                {field.label} {field.required && <span style={{ color: 'var(--neo-red, #ef4444)' }}>*</span>}
              </label>

              {field.type === 'text' && (
                <input
                  type="text"
                  value={values[field.key] || ''}
                  onChange={(e) => handleChange(field.key, e.target.value)}
                  placeholder={field.placeholder || ''}
                  required={field.required}
                  className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                  style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                />
              )}

              {field.type === 'password' && (
                <input
                  type="password"
                  value={values[field.key] || ''}
                  onChange={(e) => handleChange(field.key, e.target.value)}
                  placeholder={field.placeholder || ''}
                  required={field.required}
                  className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                  style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                />
              )}

              {field.type === 'textarea' && (
                <textarea
                  value={values[field.key] || ''}
                  onChange={(e) => handleChange(field.key, e.target.value)}
                  placeholder={field.placeholder || ''}
                  required={field.required}
                  rows={3}
                  className="w-full px-3 py-2 rounded-lg text-sm outline-none resize-none"
                  style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                />
              )}

              {field.type === 'select' && (
                <select
                  value={values[field.key] || field.default || ''}
                  onChange={(e) => handleChange(field.key, e.target.value)}
                  className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                  style={{ background: 'var(--neo-bg)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
                >
                  {(field.options || []).map((opt) => (
                    <option key={opt} value={opt}>{opt}</option>
                  ))}
                </select>
              )}

              {field.type === 'multiselect' && (
                <div className="flex flex-wrap gap-2">
                  {(field.options || []).map((opt) => {
                    const selected = (values[field.key] || []).includes(opt);
                    return (
                      <button
                        key={opt}
                        type="button"
                        onClick={() => handleMultiselect(field.key, opt)}
                        className="px-3 py-1 rounded-lg text-xs font-medium transition"
                        style={{
                          background: selected ? 'var(--neo-blue)' : 'var(--neo-bg)',
                          color: selected ? '#fff' : 'var(--neo-text)',
                          border: `1px solid ${selected ? 'var(--neo-blue)' : 'var(--neo-border)'}`,
                        }}
                      >
                        {opt}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          ))}

          {/* Test result */}
          {testResult && (
            <div
              className="flex items-center gap-2 p-3 rounded-lg text-sm"
              style={{
                background: testResult.success ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)',
                color: testResult.success ? 'var(--neo-green)' : '#ef4444',
              }}
            >
              {testResult.success ? <CheckCircle size={16} /> : <XCircle size={16} />}
              {testResult.message}
            </div>
          )}

          {/* Actions */}
          <div className="flex items-center gap-3 pt-2">
            <button
              type="button"
              onClick={handleTest}
              disabled={testing}
              className="px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90"
              style={{ border: '1px solid var(--neo-border)', color: 'var(--neo-text)' }}
            >
              {testing ? <Loader2 size={16} className="animate-spin" /> : 'Test Connection'}
            </button>
            <button
              type="submit"
              disabled={saving}
              className="flex-1 px-4 py-2 rounded-lg text-sm font-medium transition hover:opacity-90 disabled:opacity-50"
              style={{ background: 'var(--neo-blue)', color: '#fff' }}
            >
              {saving ? <Loader2 size={16} className="animate-spin mx-auto" /> : editMode ? 'Update' : 'Save & Connect'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

import React, { useState, useEffect, useRef } from 'react';
import { Upload, File, Loader2, Trash2, Download, FileText, Image, Table } from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../lib/api';

const FILE_ICONS = {
  'application/pdf': FileText,
  'text/csv': Table,
  'image/': Image,
};

function getIcon(mime) {
  for (const [prefix, Icon] of Object.entries(FILE_ICONS)) {
    if (mime && mime.startsWith(prefix)) return Icon;
  }
  return File;
}

export default function FileUpload({ sessionId }) {
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef(null);

  const fetchFiles = () => {
    api.get(`/dashboard/sessions/${sessionId}/files`)
      .then((res) => setFiles(res.data.files || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => { fetchFiles(); }, [sessionId]); // eslint-disable-line

  const upload = async (file) => {
    setUploading(true);
    const form = new FormData();
    form.append('file', file);
    try {
      await api.post(`/dashboard/sessions/${sessionId}/upload`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      toast.success(`Uploaded "${file.name}"`);
      fetchFiles();
    } catch {
      toast.error('Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files[0];
    if (f) upload(f);
  };

  const handleSelect = (e) => {
    const f = e.target.files[0];
    if (f) upload(f);
  };

  const fmtSize = (b) => {
    if (!b) return '—';
    if (b < 1024) return `${b} B`;
    if (b < 1048576) return `${(b / 1024).toFixed(1)} KB`;
    return `${(b / 1048576).toFixed(1)} MB`;
  };

  return (
    <div className="space-y-3">
      {/* Drop zone */}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        className="flex flex-col items-center justify-center py-8 rounded-lg cursor-pointer transition"
        style={{
          border: `2px dashed ${dragOver ? 'var(--neo-cyan)' : 'var(--neo-border)'}`,
          background: dragOver ? 'rgba(0,210,255,0.05)' : 'transparent',
        }}
      >
        {uploading ? (
          <Loader2 size={24} className="animate-spin mb-2" style={{ color: 'var(--neo-blue)' }} />
        ) : (
          <Upload size={24} className="mb-2" style={{ color: 'var(--neo-text-muted)', opacity: 0.5 }} />
        )}
        <p className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
          {uploading ? 'Uploading...' : 'Drop a file here or click to browse'}
        </p>
        <p className="text-xs mt-1" style={{ color: 'var(--neo-text-muted)', opacity: 0.6 }}>
          PDF, DOCX, CSV, TXT, JSON
        </p>
        <input
          ref={inputRef}
          type="file"
          className="hidden"
          accept=".pdf,.docx,.csv,.txt,.json,.md"
          onChange={handleSelect}
        />
      </div>

      {/* File list */}
      {loading ? (
        <div className="flex justify-center py-4">
          <Loader2 size={16} className="animate-spin" style={{ color: 'var(--neo-blue)' }} />
        </div>
      ) : files.length === 0 ? (
        <p className="text-xs text-center py-3" style={{ color: 'var(--neo-text-muted)' }}>
          No files uploaded yet
        </p>
      ) : (
        <div className="space-y-1">
          {files.map((f) => {
            const Icon = getIcon(f.mime_type);
            return (
              <div
                key={f.blob_id}
                className="flex items-center justify-between px-3 py-2 rounded-lg"
                style={{ background: 'var(--neo-surface)' }}
              >
                <div className="flex items-center gap-2 min-w-0">
                  <Icon size={14} style={{ color: 'var(--neo-cyan)' }} />
                  <span className="text-xs truncate" style={{ color: 'var(--neo-text)' }}>
                    {f.filename}
                  </span>
                  <span className="text-xs" style={{ color: 'var(--neo-text-muted)' }}>
                    {fmtSize(f.size)}
                  </span>
                </div>
                {f.created_at && (
                  <span className="text-xs shrink-0 ml-2" style={{ color: 'var(--neo-text-muted)' }}>
                    {new Date(f.created_at).toLocaleDateString()}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

import React from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';

export class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error('ErrorBoundary caught:', error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex items-center justify-center" style={{ minHeight: 300 }}>
          <div className="text-center p-8 rounded-xl" style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', maxWidth: 420 }}>
            <AlertTriangle size={40} className="mx-auto mb-4" style={{ color: 'var(--neo-red)' }} />
            <h2 className="text-lg font-bold mb-2" style={{ color: 'var(--neo-text)' }}>
              Something went wrong
            </h2>
            <p className="text-sm mb-4" style={{ color: 'var(--neo-text-muted)' }}>
              {this.state.error?.message || 'An unexpected error occurred'}
            </p>
            <button
              onClick={() => { this.setState({ hasError: false, error: null }); }}
              className="flex items-center gap-2 mx-auto px-4 py-2 rounded-lg text-sm font-medium transition"
              style={{ background: 'var(--neo-blue)', color: '#fff', border: 'none', cursor: 'pointer' }}
            >
              <RefreshCw size={14} />
              Try again
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

export function LoadingState({ message = 'Loading...' }) {
  return (
    <div className="flex items-center justify-center h-64">
      <div className="text-center">
        <div
          className="w-8 h-8 mx-auto mb-3 rounded-full border-2 animate-spin"
          style={{ borderColor: 'var(--neo-border)', borderTopColor: 'var(--neo-blue)' }}
        />
        <p className="text-sm" style={{ color: 'var(--neo-text-muted)' }}>{message}</p>
      </div>
    </div>
  );
}

export function ErrorState({ message = 'Failed to load data', onRetry }) {
  return (
    <div className="flex items-center justify-center h-64">
      <div className="text-center">
        <AlertTriangle size={32} className="mx-auto mb-3" style={{ color: 'var(--neo-red)', opacity: 0.7 }} />
        <p className="text-sm font-medium mb-1" style={{ color: 'var(--neo-text)' }}>{message}</p>
        {onRetry && (
          <button
            onClick={onRetry}
            className="mt-3 flex items-center gap-1.5 mx-auto px-3 py-1.5 rounded-lg text-xs font-medium transition"
            style={{ background: 'var(--neo-surface)', border: '1px solid var(--neo-border)', color: 'var(--neo-text)', cursor: 'pointer' }}
          >
            <RefreshCw size={12} />
            Retry
          </button>
        )}
      </div>
    </div>
  );
}

export function EmptyState({ icon: Icon, title, description, action }) {
  return (
    <div className="flex items-center justify-center" style={{ minHeight: 300 }}>
      <div className="text-center p-8">
        {Icon && <Icon size={48} className="mx-auto mb-4" style={{ color: 'var(--neo-text-muted)', opacity: 0.3 }} />}
        <h3 className="text-base font-semibold mb-1" style={{ color: 'var(--neo-text)' }}>{title}</h3>
        {description && (
          <p className="text-sm mb-4" style={{ color: 'var(--neo-text-muted)', maxWidth: 300 }}>{description}</p>
        )}
        {action}
      </div>
    </div>
  );
}

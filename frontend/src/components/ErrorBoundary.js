import React from 'react';
import { AlertCircle } from 'lucide-react';
import { reportError } from '@/lib/errorReporting';

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true };
  }

  componentDidCatch(error, errorInfo) {
    // SECURITY FIX L-8: Global error boundary to catch React errors
    console.error('Error caught by boundary:', error, errorInfo);
    this.setState({
      error,
      errorInfo,
    });
    reportError(error, {
      source: 'ErrorBoundary',
      componentStack: errorInfo?.componentStack ?? null,
    });
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
    window.location.href = '/';
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-aegisaxiom-base flex items-center justify-center p-4">
          <div className="max-w-md w-full bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg shadow-lg p-6">
            <div className="flex items-center justify-center w-12 h-12 mx-auto bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800/40 rounded-full">
              <AlertCircle className="w-6 h-6 text-red-600 dark:text-red-400" />
            </div>

            <h1 className="mt-4 text-xl font-bold text-center text-aegisaxiom-text">
              Something Went Wrong
            </h1>

            <p className="mt-2 text-sm text-center text-aegisaxiom-text-secondary">
              We're sorry, but something unexpected happened. The error has been logged and we'll
              look into it.
            </p>

            {process.env.NODE_ENV === 'development' && this.state.error && (
              <div className="mt-4 p-3 bg-red-950/40 rounded border border-red-200 dark:border-red-800/40">
                <p className="text-xs font-mono text-red-700 dark:text-red-300 break-all">
                  {this.state.error.toString()}
                </p>
                {this.state.errorInfo && (
                  <details className="mt-2">
                    <summary className="text-xs font-semibold text-red-600 dark:text-red-400 cursor-pointer">
                      Stack Trace
                    </summary>
                    <pre className="mt-1 text-xs text-red-600 dark:text-red-400/80 overflow-auto max-h-32">
                      {this.state.errorInfo.componentStack}
                    </pre>
                  </details>
                )}
              </div>
            )}

            <div className="mt-6 space-y-2">
              <button
                onClick={() => window.location.reload()}
                className="w-full px-4 py-2 bg-violet-600 text-white rounded hover:bg-violet-700 transition"
              >
                Reload Page
              </button>
              <button
                onClick={this.handleReset}
                className="w-full px-4 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border text-aegisaxiom-text-secondary rounded hover:bg-aegisaxiom-elevated hover:text-aegisaxiom-text transition"
              >
                Go to Home
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;

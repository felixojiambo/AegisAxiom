import React, { useState, useEffect } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { CheckCircle, AlertTriangle, Clock, MapPin, Wrench, LogIn, LogOut } from 'lucide-react';

const API_BASE = process.env.REACT_APP_BACKEND_URL || '';

export default function PublicFieldJobPage() {
  const { jobId } = useParams();
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token');

  const [job, setJob] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [action, setAction] = useState(null); // 'check-in' | 'check-out'
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [contractorName, setContractorName] = useState('');
  const [notes, setNotes] = useState('');

  useEffect(() => {
    if (!jobId || !token) {
      setError('Invalid access link. The link appears to be missing required parameters.');
      setLoading(false);
      return;
    }
    loadJob();
  }, [jobId, token]);

  const loadJob = async () => {
    try {
      const res = await fetch(
        `${API_BASE}/api/v2/field-services/jobs/${jobId}/public-access?token=${encodeURIComponent(token)}`,
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        if (res.status === 410)
          throw new Error('This access link has expired. Please request a new link.');
        if (res.status === 404)
          throw new Error('This access link is no longer valid. It may have been revoked.');
        throw new Error(err.detail || 'Failed to load job details');
      }
      const data = await res.json();
      setJob(data);
    } catch (err) {
      setError(err.message || 'Failed to load job details');
    } finally {
      setLoading(false);
    }
  };

  const handleCheckIn = async (e) => {
    e.preventDefault();
    if (!contractorName.trim()) return;
    setSubmitting(true);
    try {
      const res = await fetch(
        `${API_BASE}/api/v2/field-services/jobs/${jobId}/check-in-public?token=${encodeURIComponent(token)}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ contractor_name: contractorName, notes }),
        },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Check-in failed');
      }
      setDone(true);
      setAction('check-in-done');
    } catch (err) {
      setError(err.message || 'Check-in failed');
    } finally {
      setSubmitting(false);
    }
  };

  const handleCheckOut = async (e) => {
    e.preventDefault();
    if (!contractorName.trim()) return;
    setSubmitting(true);
    try {
      const res = await fetch(
        `${API_BASE}/api/v2/field-services/jobs/${jobId}/check-out-public?token=${encodeURIComponent(token)}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ contractor_name: contractorName, notes }),
        },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Check-out failed');
      }
      setDone(true);
      setAction('check-out-done');
    } catch (err) {
      setError(err.message || 'Check-out failed');
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-aegisaxiom-base flex items-center justify-center">
        <div className="w-10 h-10 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (error && !job) {
    return (
      <div className="min-h-screen bg-aegisaxiom-base flex items-center justify-center p-6">
        <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-md p-8 text-center">
          <div className="w-16 h-16 rounded-full bg-red-50 dark:bg-red-500/10 flex items-center justify-center mx-auto mb-4">
            <AlertTriangle size={32} className="text-red-600 dark:text-red-400" />
          </div>
          <h1 className="text-xl font-bold text-aegisaxiom-text mb-2">Access Error</h1>
          <p className="text-aegisaxiom-text-secondary text-sm">{error}</p>
        </div>
      </div>
    );
  }

  if (action === 'check-in-done' || action === 'check-out-done') {
    return (
      <div className="min-h-screen bg-aegisaxiom-base flex items-center justify-center p-6">
        <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-md p-8 text-center">
          <div className="w-16 h-16 rounded-full bg-emerald-50 dark:bg-emerald-500/10 flex items-center justify-center mx-auto mb-4">
            <CheckCircle size={32} className="text-emerald-600 dark:text-emerald-400" />
          </div>
          <h1 className="text-xl font-bold text-aegisaxiom-text mb-2">
            {action === 'check-in-done' ? 'Checked In Successfully' : 'Checked Out Successfully'}
          </h1>
          <p className="text-aegisaxiom-text-secondary text-sm mb-4">
            Your {action === 'check-in-done' ? 'check-in' : 'check-out'} for{' '}
            <strong className="text-aegisaxiom-text">{job?.name}</strong> has been recorded.
          </p>
          <p className="text-aegisaxiom-text-muted text-xs">You can close this page.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-aegisaxiom-base text-aegisaxiom-text">
      {/* Header */}
      <div className="bg-aegisaxiom-surface border-b border-aegisaxiom-border px-6 py-4">
        <div className="max-w-xl mx-auto flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-600 to-fuchsia-600 flex items-center justify-center shrink-0">
            <span className="text-aegisaxiom-text font-bold text-sm">B</span>
          </div>
          <div>
            <p
              className="text-aegisaxiom-text font-semibold text-sm"
              style={{ fontFamily: 'Manrope, sans-serif' }}
            >
              AEGISAXIOM
            </p>
            <p className="text-aegisaxiom-text-muted text-xs">Field Services Portal</p>
          </div>
        </div>
      </div>

      <div className="max-w-xl mx-auto px-6 py-8 space-y-6">
        {/* Job Details */}
        <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-6">
          <div className="flex items-start gap-3 mb-4">
            <div className="w-10 h-10 rounded-lg bg-violet-50 dark:bg-violet-600/20 border border-violet-200 dark:border-violet-600/20 flex items-center justify-center flex-shrink-0">
              <Wrench size={18} className="text-violet-600 dark:text-violet-400" />
            </div>
            <div className="flex-1 min-w-0">
              <h1 className="text-lg font-bold text-aegisaxiom-text truncate">{job?.name}</h1>
              <p className="text-aegisaxiom-text-secondary text-sm">{job?.account_name}</p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 text-sm">
            {job?.scheduled_date && (
              <div className="flex items-center gap-2 text-aegisaxiom-text-secondary">
                <Clock size={14} className="text-aegisaxiom-text-muted" />
                <span>
                  {new Date(job.scheduled_date).toLocaleDateString('en-US', {
                    month: 'short',
                    day: 'numeric',
                    year: 'numeric',
                  })}
                </span>
              </div>
            )}
            {job?.job_type_name && (
              <div className="flex items-center gap-2 text-aegisaxiom-text-secondary">
                <Wrench size={14} className="text-aegisaxiom-text-muted" />
                <span>{job.job_type_name}</span>
              </div>
            )}
          </div>

          {job?.description && (
            <p className="mt-3 text-xs text-aegisaxiom-text-muted border-t border-aegisaxiom-border pt-3">
              {job.description}
            </p>
          )}
        </div>

        {/* Action Selection */}
        {!action && (
          <div>
            <p className="text-sm text-aegisaxiom-text-secondary mb-4 text-center">What would you like to do?</p>
            <div className="grid grid-cols-2 gap-4">
              <button
                onClick={() => setAction('check-in')}
                className="flex flex-col items-center gap-3 p-6 bg-aegisaxiom-surface border border-aegisaxiom-border hover:border-emerald-600/50 hover:bg-emerald-600/5 rounded-xl transition-all"
              >
                <div className="w-12 h-12 rounded-full bg-emerald-50 dark:bg-emerald-600/20 flex items-center justify-center">
                  <LogIn size={22} className="text-emerald-600 dark:text-emerald-400" />
                </div>
                <div className="text-center">
                  <p className="font-semibold text-aegisaxiom-text">Check In</p>
                  <p className="text-xs text-aegisaxiom-text-muted mt-0.5">Arriving on site</p>
                </div>
              </button>
              <button
                onClick={() => setAction('check-out')}
                className="flex flex-col items-center gap-3 p-6 bg-aegisaxiom-surface border border-aegisaxiom-border hover:border-violet-600/50 hover:bg-violet-600/5 rounded-xl transition-all"
              >
                <div className="w-12 h-12 rounded-full bg-violet-50 dark:bg-violet-600/20 flex items-center justify-center">
                  <LogOut size={22} className="text-violet-600 dark:text-violet-400" />
                </div>
                <div className="text-center">
                  <p className="font-semibold text-aegisaxiom-text">Check Out</p>
                  <p className="text-xs text-aegisaxiom-text-muted mt-0.5">Leaving the site</p>
                </div>
              </button>
            </div>
          </div>
        )}

        {/* Check In Form */}
        {action === 'check-in' && (
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-6">
            <div className="flex items-center gap-2 mb-5">
              <LogIn size={18} className="text-emerald-600 dark:text-emerald-400" />
              <h2 className="font-semibold text-aegisaxiom-text">Check In</h2>
            </div>
            {error && (
              <div className="mb-4 p-3 rounded-lg bg-red-50 dark:bg-red-600/10 border border-red-200 dark:border-red-600/20 text-red-700 dark:text-red-300 text-sm">
                {error}
              </div>
            )}
            <form onSubmit={handleCheckIn} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-2">Your Name *</label>
                <input
                  type="text"
                  value={contractorName}
                  onChange={(e) => setContractorName(e.target.value)}
                  className="w-full px-3 py-2.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-emerald-500"
                  placeholder="Enter your full name"
                  required
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-2">
                  Notes (optional)
                </label>
                <textarea
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  rows={3}
                  className="w-full px-3 py-2.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-emerald-500 resize-none"
                  placeholder="Any notes about your arrival..."
                />
              </div>
              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={() => {
                    setAction(null);
                    setError(null);
                  }}
                  className="flex-1 px-4 py-2.5 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary rounded-lg transition-colors text-sm"
                >
                  Back
                </button>
                <button
                  type="submit"
                  disabled={submitting || !contractorName.trim()}
                  className="flex-1 px-4 py-2.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg transition-colors text-sm font-medium disabled:opacity-50"
                >
                  {submitting ? 'Checking In...' : 'Confirm Check In'}
                </button>
              </div>
            </form>
          </div>
        )}

        {/* Check Out Form */}
        {action === 'check-out' && (
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-6">
            <div className="flex items-center gap-2 mb-5">
              <LogOut size={18} className="text-violet-600 dark:text-violet-400" />
              <h2 className="font-semibold text-aegisaxiom-text">Check Out</h2>
            </div>
            {error && (
              <div className="mb-4 p-3 rounded-lg bg-red-50 dark:bg-red-600/10 border border-red-200 dark:border-red-600/20 text-red-700 dark:text-red-300 text-sm">
                {error}
              </div>
            )}
            <form onSubmit={handleCheckOut} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-2">Your Name *</label>
                <input
                  type="text"
                  value={contractorName}
                  onChange={(e) => setContractorName(e.target.value)}
                  className="w-full px-3 py-2.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500"
                  placeholder="Enter your full name"
                  required
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-2">
                  Notes (optional)
                </label>
                <textarea
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  rows={3}
                  className="w-full px-3 py-2.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 resize-none"
                  placeholder="Work summary, issues encountered..."
                />
              </div>
              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={() => {
                    setAction(null);
                    setError(null);
                  }}
                  className="flex-1 px-4 py-2.5 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary rounded-lg transition-colors text-sm"
                >
                  Back
                </button>
                <button
                  type="submit"
                  disabled={submitting || !contractorName.trim()}
                  className="flex-1 px-4 py-2.5 bg-violet-600 hover:bg-violet-700 text-white rounded-lg transition-colors text-sm font-medium disabled:opacity-50"
                >
                  {submitting ? 'Checking Out...' : 'Confirm Check Out'}
                </button>
              </div>
            </form>
          </div>
        )}
      </div>
    </div>
  );
}

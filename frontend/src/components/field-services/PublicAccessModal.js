import React, { useState } from 'react';
import { X, Link, Copy, QrCode, CheckCircle, Calendar, AlertTriangle } from 'lucide-react';
import { toast } from 'sonner';
import ConfirmModal from '../shared/ConfirmModal';

const API_BASE = process.env.REACT_APP_BACKEND_URL || '';

export default function PublicAccessModal({ job, onClose, onSuccess }) {
  const [loading, setLoading] = useState(false);
  const [generatedLink, setGeneratedLink] = useState(null); // { url, expires }
  const [isRevoked, setIsRevoked] = useState(false);
  const [copied, setCopied] = useState(false);
  const [confirmState, setConfirmState] = useState({
    open: false,
    action: null,
    title: '',
    message: '',
  });

  // Build the correct public URL for this app — /public/field-job/:jobId?token=...
  const buildPublicUrl = (jobId, token) => {
    return `${window.location.origin}/public/field-job/${jobId}?token=${token}`;
  };

  // If job already has a token, reconstruct the link display from job data
  const existingToken = !isRevoked && job.public_access_token;
  const existingUrl = existingToken ? buildPublicUrl(job.job_id, existingToken) : null;
  const existingExpiry = job.public_access_token_expires;

  // Use newly generated link data if available, otherwise fall back to job's existing data
  const displayUrl = generatedLink?.url || existingUrl;
  const displayExpiry = generatedLink?.expires || existingExpiry;
  const hasLink = Boolean(displayUrl);

  const generateLink = async () => {
    setLoading(true);
    try {
      const response = await fetch(
        `${API_BASE}/api/v2/field-services/jobs/${job.job_id}/generate-public-link`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${localStorage.getItem('aegisaxiom_session_token')}`,
          },
        },
      );

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Failed to generate link');
      }

      const data = await response.json();
      // Use the correct app route format, not the backend-provided URL
      const fullUrl = buildPublicUrl(job.job_id, data.token);
      setGeneratedLink({ url: fullUrl, expires: data.expires });
      setIsRevoked(false);
      toast.success('Public access link generated');
      onSuccess();
    } catch (error) {
      console.error('Generate link error:', error);
      toast.error(error.message || 'Failed to generate link');
    } finally {
      setLoading(false);
    }
  };

  const copyToClipboard = () => {
    if (displayUrl) {
      try {
        navigator.clipboard.writeText(displayUrl);
        setCopied(true);
        toast.success('Link copied to clipboard');
        setTimeout(() => setCopied(false), 2000);
      } catch {
        toast.error('Copy failed — use manual copy');
      }
    }
  };

  const revokeLink = () => {
    setConfirmState({
      open: true,
      title: 'Revoke Public Access Link',
      message:
        'Are you sure you want to revoke this public access link? Contractors will no longer be able to use it.',
      action: async () => {
        try {
          const response = await fetch(
            `${API_BASE}/api/v2/field-services/jobs/${job.job_id}/revoke-public-link`,
            {
              method: 'DELETE',
              headers: {
                Authorization: `Bearer ${localStorage.getItem('aegisaxiom_session_token')}`,
              },
            },
          );

          if (!response.ok) {
            throw new Error('Failed to revoke link');
          }

          toast.success('Public access link revoked');
          setGeneratedLink(null);
          setIsRevoked(true);
          onSuccess();
        } catch (error) {
          console.error('Revoke link error:', error);
          toast.error('Failed to revoke link');
        }
      },
    });
  };

  const formatExpiry = (isoString) => {
    if (!isoString) return null;
    try {
      return new Date(isoString).toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
      });
    } catch {
      return isoString;
    }
  };

  const isExpired = displayExpiry ? new Date(displayExpiry) < new Date() : false;

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-lg p-6 animate-fadeInUp">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-violet-50 dark:bg-violet-600/10 border border-violet-200 dark:border-violet-600/20 flex items-center justify-center">
              <Link size={20} className="text-violet-600 dark:text-violet-400" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-aegisaxiom-text">Public Access Link</h2>
              <p className="text-xs text-aegisaxiom-text-secondary">For contractor check-in/check-out</p>
            </div>
          </div>
          <button onClick={onClose} className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text">
            <X size={20} />
          </button>
        </div>

        {/* Job Info */}
        <div className="mb-6 p-3 rounded-lg bg-aegisaxiom-elevated border border-aegisaxiom-border">
          <p className="text-xs text-aegisaxiom-text-muted mb-1">Job</p>
          <p className="text-sm font-medium text-aegisaxiom-text">{job.name}</p>
          <p className="text-xs text-aegisaxiom-text-secondary mt-1">{job.account_name}</p>
        </div>

        {/* Link Status Badge */}
        {(hasLink || isRevoked) && (
          <div className="mb-4">
            {isRevoked ? (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-red-50 dark:bg-red-600/20 border border-red-200 dark:border-red-600/30 text-red-700 dark:text-red-300">
                <AlertTriangle size={11} />
                Revoked
              </span>
            ) : isExpired ? (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-amber-50 dark:bg-amber-600/20 border border-amber-200 dark:border-amber-600/30 text-amber-700 dark:text-amber-300">
                <AlertTriangle size={11} />
                Expired
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-50 dark:bg-emerald-600/20 border border-emerald-200 dark:border-emerald-600/30 text-emerald-700 dark:text-emerald-300">
                <CheckCircle size={11} />
                Active
              </span>
            )}
          </div>
        )}

        {/* Generate or Show Link */}
        {!hasLink ? (
          <div className="mb-6">
            <div className="p-4 rounded-lg bg-aegisaxiom-accent-muted border border-aegisaxiom-border text-center">
              <p className="text-sm text-aegisaxiom-text-secondary mb-4">
                {isRevoked
                  ? 'The previous link was revoked. Generate a new link for contractor access.'
                  : 'Generate a secure link that allows contractors to check in and check out without logging in'}
              </p>
              <button
                onClick={generateLink}
                disabled={loading}
                className="px-4 py-2 bg-violet-600 hover:bg-violet-700 text-white rounded-lg transition-colors text-sm font-medium disabled:opacity-50 inline-flex items-center gap-2"
              >
                <Link size={16} />
                {loading ? 'Generating...' : 'Generate Public Link'}
              </button>
            </div>
          </div>
        ) : (
          <>
            {/* Link Display */}
            <div className="mb-4">
              <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-2">Public URL</label>
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={displayUrl}
                  readOnly
                  className="flex-1 px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-xs font-mono"
                />
                <button
                  onClick={copyToClipboard}
                  className="px-3 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg transition-colors border border-aegisaxiom-border flex-shrink-0"
                >
                  {copied ? (
                    <CheckCircle size={18} className="text-emerald-600 dark:text-emerald-400" />
                  ) : (
                    <Copy size={18} />
                  )}
                </button>
              </div>
            </div>

            {/* Expiry Date */}
            {displayExpiry && (
              <div
                className={`flex items-center gap-2 mb-4 px-3 py-2 rounded-lg text-xs ${
                  isExpired
                    ? 'bg-amber-50 dark:bg-amber-600/10 border border-amber-200 dark:border-amber-600/20 text-amber-700 dark:text-amber-300'
                    : 'bg-aegisaxiom-elevated border border-aegisaxiom-border text-aegisaxiom-text-secondary'
                }`}
              >
                <Calendar size={13} className="flex-shrink-0" />
                <span>
                  {isExpired ? 'Expired' : 'Expires'}: {formatExpiry(displayExpiry)}
                </span>
              </div>
            )}

            {/* Actions */}
            <div className="grid grid-cols-2 gap-3 mb-4">
              <button
                onClick={copyToClipboard}
                className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg transition-colors text-sm font-medium flex items-center justify-center gap-2 border border-aegisaxiom-border"
              >
                <Copy size={16} />
                Copy Link
              </button>
              <button
                onClick={generateLink}
                disabled={loading}
                className="px-4 py-2 bg-violet-50 dark:bg-violet-600/20 hover:bg-violet-600/30 text-violet-700 dark:text-violet-300 rounded-lg transition-colors text-sm font-medium flex items-center justify-center gap-2 border border-violet-200 dark:border-violet-600/30 disabled:opacity-50"
              >
                <Link size={16} />
                {loading ? 'Regenerating...' : 'Regenerate'}
              </button>
            </div>

            {/* Revoke */}
            <button
              onClick={revokeLink}
              className="w-full px-4 py-2 bg-red-50 dark:bg-red-600/10 hover:bg-red-50 dark:bg-red-600/20 text-red-600 dark:text-red-400 rounded-lg transition-colors text-sm font-medium border border-red-200 dark:border-red-600/20"
            >
              Revoke Link
            </button>
          </>
        )}

        {/* Info */}
        <div className="mt-6 p-3 rounded-lg bg-blue-50 dark:bg-blue-600/10 border border-blue-200 dark:border-blue-600/20">
          <p className="text-xs text-blue-700 dark:text-blue-300 font-medium mb-1">How it works</p>
          <ul className="text-xs text-aegisaxiom-text-secondary space-y-1">
            <li>• Share this link with the contractor via email/SMS</li>
            <li>• They can check in and check out without logging in</li>
            <li>• All activity is tracked and logged</li>
            <li>• Link can be revoked at any time</li>
          </ul>
        </div>

        {/* Close */}
        <div className="mt-6">
          <button
            onClick={onClose}
            className="w-full px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg transition-colors text-sm font-medium"
          >
            Close
          </button>
        </div>
      </div>
      <ConfirmModal
        isOpen={confirmState.open}
        title={confirmState.title}
        message={confirmState.message}
        confirmText="Revoke"
        onConfirm={() => {
          confirmState.action?.();
          setConfirmState({ open: false, action: null, title: '', message: '' });
        }}
        onCancel={() => setConfirmState({ open: false, action: null, title: '', message: '' })}
      />
    </div>
  );
}

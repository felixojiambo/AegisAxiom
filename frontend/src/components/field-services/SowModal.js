import React, { useState, useEffect } from 'react';
import { X, FileText, Download, Mail, Edit2, Save, Eye } from 'lucide-react';
import { toast } from 'sonner';

export default function SowModal({ job, onClose, onSuccess }) {
  const [sow, setSow] = useState(null);
  const [loading, setLoading] = useState(true);
  const [editMode, setEditMode] = useState(false);
  const [saving, setSaving] = useState(false);
  const [showEmailModal, setShowEmailModal] = useState(false);

  useEffect(() => {
    loadSow();
  }, [job.job_id]);

  const loadSow = async () => {
    try {
      const response = await fetch(
        `${process.env.REACT_APP_BACKEND_URL}/api/v2/field-services/jobs/${job.job_id}/sow?format=json`,
        {
          headers: { Authorization: `Bearer ${localStorage.getItem('aegisaxiom_session_token')}` },
        },
      );

      if (!response.ok) {
        throw new Error('Failed to load SOW');
      }

      const data = await response.json();
      setSow(data.sow);
    } catch (error) {
      console.error('Load SOW error:', error);
      toast.error('Failed to load SOW');
    } finally {
      setLoading(false);
    }
  };

  const generateSow = async () => {
    setLoading(true);
    try {
      const response = await fetch(
        `${process.env.REACT_APP_BACKEND_URL}/api/v2/field-services/jobs/${job.job_id}/generate-sow`,
        {
          method: 'POST',
          headers: { Authorization: `Bearer ${localStorage.getItem('aegisaxiom_session_token')}` },
        },
      );

      if (!response.ok) {
        throw new Error('Failed to generate SOW');
      }

      const data = await response.json();
      setSow(data.sow);
      toast.success('SOW generated successfully');
      onSuccess();
    } catch (error) {
      console.error('Generate SOW error:', error);
      toast.error(error.message || 'Failed to generate SOW');
    } finally {
      setLoading(false);
    }
  };

  const saveSow = async () => {
    setSaving(true);
    try {
      const response = await fetch(
        `${process.env.REACT_APP_BACKEND_URL}/api/v2/field-services/jobs/${job.job_id}/sow`,
        {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${localStorage.getItem('aegisaxiom_session_token')}`,
          },
          body: JSON.stringify(sow),
        },
      );

      if (!response.ok) {
        throw new Error('Failed to save SOW');
      }

      toast.success('SOW saved successfully');
      setEditMode(false);
      onSuccess();
    } catch (error) {
      console.error('Save SOW error:', error);
      toast.error('Failed to save SOW');
    } finally {
      setSaving(false);
    }
  };

  const downloadSow = async (format) => {
    try {
      const response = await fetch(
        `${process.env.REACT_APP_BACKEND_URL}/api/v2/field-services/jobs/${job.job_id}/sow/download?format=${format}`,
        {
          headers: { Authorization: `Bearer ${localStorage.getItem('aegisaxiom_session_token')}` },
        },
      );

      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || 'Failed to download SOW');
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `SOW_${job.name.replace(/\s+/g, '_')}_${job.job_id}.${format}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);

      toast.success(`SOW downloaded as ${format.toUpperCase()}`);
    } catch (error) {
      console.error('Download error:', error);
      toast.error(error.message || 'Failed to download SOW');
    }
  };

  const updateSowSection = (section, field, value) => {
    setSow((prev) => ({
      ...prev,
      [section]: {
        ...prev[section],
        [field]: value,
      },
    }));
  };

  if (loading) {
    return (
      <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center">
        <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl p-8">
          <p className="text-aegisaxiom-text">Loading SOW...</p>
        </div>
      </div>
    );
  }

  if (!sow && !loading) {
    return (
      <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
        <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-md p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-bold text-aegisaxiom-text">No SOW Generated</h2>
            <button onClick={onClose} className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text">
              <X size={20} />
            </button>
          </div>
          <p className="text-sm text-aegisaxiom-text-secondary mb-6">
            No Statement of Work has been generated for this survey job yet.
          </p>
          <div className="flex gap-3">
            <button
              onClick={onClose}
              className="flex-1 px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg text-sm font-medium"
            >
              Cancel
            </button>
            <button
              onClick={generateSow}
              className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium"
            >
              Generate SOW
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
        <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-5xl p-6 animate-fadeInUp max-h-[90vh] overflow-y-auto">
          {/* Header */}
          <div className="flex items-center justify-between mb-6 pb-4 border-b border-aegisaxiom-border">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-blue-50 dark:bg-blue-600/10 border border-blue-200 dark:border-blue-600/20 flex items-center justify-center">
                <FileText size={20} className="text-blue-600 dark:text-blue-400" />
              </div>
              <div>
                <h2 className="text-lg font-bold text-aegisaxiom-text">Statement of Work</h2>
                <p className="text-xs text-aegisaxiom-text-secondary">{job.name}</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setEditMode(!editMode)}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors flex items-center gap-1.5 ${
                  editMode
                    ? 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-600/30'
                    : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary border border-aegisaxiom-border hover:bg-aegisaxiom-elevated'
                }`}
              >
                {editMode ? (
                  <>
                    <Save size={14} /> Editing
                  </>
                ) : (
                  <>
                    <Edit2 size={14} /> Edit
                  </>
                )}
              </button>
              <button onClick={onClose} className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text">
                <X size={20} />
              </button>
            </div>
          </div>

          {/* Actions Bar */}
          <div className="flex items-center gap-2 mb-6 p-3 rounded-lg bg-aegisaxiom-elevated border border-aegisaxiom-border">
            <span className="text-xs text-aegisaxiom-text-secondary mr-2">Download:</span>
            <button
              onClick={() => downloadSow('pdf')}
              className="px-3 py-1.5 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded text-xs font-medium flex items-center gap-1.5"
            >
              <Download size={12} /> PDF
            </button>
            <button
              onClick={() => downloadSow('docx')}
              className="px-3 py-1.5 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded text-xs font-medium flex items-center gap-1.5"
            >
              <Download size={12} /> DOCX
            </button>
            <button
              onClick={() => downloadSow('markdown')}
              className="px-3 py-1.5 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded text-xs font-medium flex items-center gap-1.5"
            >
              <Download size={12} /> Markdown
            </button>
            <button
              onClick={() => downloadSow('json')}
              className="px-3 py-1.5 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded text-xs font-medium flex items-center gap-1.5"
            >
              <Download size={12} /> JSON
            </button>
            <div className="flex-1" />
            <button
              onClick={() => setShowEmailModal(true)}
              className="px-3 py-1.5 bg-violet-600 hover:bg-violet-700 text-white rounded text-xs font-medium flex items-center gap-1.5"
            >
              <Mail size={12} /> Email SOW
            </button>
          </div>

          {/* SOW Content */}
          <div className="space-y-6">
            {/* Executive Summary */}
            <section className="p-4 rounded-lg bg-aegisaxiom-accent-muted border border-aegisaxiom-border">
              <h3 className="text-sm font-bold text-aegisaxiom-text mb-3">Executive Summary</h3>
              {editMode ? (
                <textarea
                  value={sow.executive_summary?.content || ''}
                  onChange={(e) => updateSowSection('executive_summary', 'content', e.target.value)}
                  rows={4}
                  className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text"
                />
              ) : (
                <p className="text-sm text-aegisaxiom-text-secondary">{sow.executive_summary?.content}</p>
              )}
            </section>

            {/* Site Information */}
            <section className="p-4 rounded-lg bg-aegisaxiom-accent-muted border border-aegisaxiom-border">
              <h3 className="text-sm font-bold text-aegisaxiom-text mb-3">Site Information</h3>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <span className="text-aegisaxiom-text-muted">Account:</span>
                  <span className="ml-2 text-aegisaxiom-text">{sow.site_information?.account_name}</span>
                </div>
                <div>
                  <span className="text-aegisaxiom-text-muted">Address:</span>
                  <span className="ml-2 text-aegisaxiom-text">{sow.site_information?.address}</span>
                </div>
              </div>
            </section>

            {/* Scope of Work */}
            <section className="p-4 rounded-lg bg-aegisaxiom-accent-muted border border-aegisaxiom-border">
              <h3 className="text-sm font-bold text-aegisaxiom-text mb-3">Scope of Work</h3>
              {editMode ? (
                <textarea
                  value={sow.scope_of_work?.description || ''}
                  onChange={(e) => updateSowSection('scope_of_work', 'description', e.target.value)}
                  rows={3}
                  className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text mb-3"
                />
              ) : (
                <p className="text-sm text-aegisaxiom-text-secondary mb-3">{sow.scope_of_work?.description}</p>
              )}
              <h4 className="text-xs font-semibold text-aegisaxiom-text-secondary mb-2">Deliverables:</h4>
              <ul className="list-disc list-inside space-y-1 text-sm text-aegisaxiom-text-secondary">
                {sow.scope_of_work?.deliverables?.map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            </section>

            {/* Materials & Equipment */}
            <section className="p-4 rounded-lg bg-aegisaxiom-accent-muted border border-aegisaxiom-border">
              <h3 className="text-sm font-bold text-aegisaxiom-text mb-3">Materials & Equipment</h3>
              {sow.materials_and_equipment?.materials?.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-aegisaxiom-border">
                        <th className="text-left py-2 text-aegisaxiom-text-secondary font-medium">Item</th>
                        <th className="text-left py-2 text-aegisaxiom-text-secondary font-medium">Quantity</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sow.materials_and_equipment.materials.map((mat, i) => (
                        <tr key={i} className="border-b border-aegisaxiom-border">
                          <td className="py-2 text-aegisaxiom-text">{mat.item}</td>
                          <td className="py-2 text-aegisaxiom-text-secondary">{mat.quantity}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {/* Cost Estimate */}
            <section className="p-4 rounded-lg bg-aegisaxiom-accent-muted border border-aegisaxiom-border">
              <h3 className="text-sm font-bold text-aegisaxiom-text mb-3">Cost Estimate</h3>
              <div className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-aegisaxiom-text-secondary">
                    Labor ({sow.cost_estimate?.labor?.hours || 0}h @ $
                    {parseFloat(sow.cost_estimate?.labor?.rate || 0).toFixed(2)}/hr)
                  </span>
                  <span className="text-aegisaxiom-text font-medium">
                    ${parseFloat(sow.cost_estimate?.labor?.subtotal || 0).toFixed(2)}
                  </span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-aegisaxiom-text-secondary">Materials</span>
                  <span className="text-aegisaxiom-text font-medium">
                    ${parseFloat(sow.cost_estimate?.materials?.estimated_cost || 0).toFixed(2)}
                  </span>
                </div>
                <div className="flex justify-between text-base font-bold border-t border-aegisaxiom-border pt-2 mt-2">
                  <span className="text-aegisaxiom-text">Total Estimate</span>
                  <span className="text-emerald-600 dark:text-emerald-400">
                    ${parseFloat(sow.cost_estimate?.total || 0).toFixed(2)}
                  </span>
                </div>
              </div>
            </section>
          </div>

          {/* Footer Actions */}
          <div className="flex gap-3 pt-6 mt-6 border-t border-aegisaxiom-border">
            <button
              onClick={onClose}
              className="flex-1 px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg text-sm font-medium"
            >
              Close
            </button>
            {editMode && (
              <button
                onClick={saveSow}
                disabled={saving}
                className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium disabled:opacity-50"
              >
                {saving ? 'Saving...' : 'Save Changes'}
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Email Modal */}
      {showEmailModal && (
        <EmailSowModal
          job={job}
          onClose={() => setShowEmailModal(false)}
          onSuccess={() => {
            setShowEmailModal(false);
            toast.success('SOW emailed successfully');
          }}
        />
      )}
    </>
  );
}

// Email Modal Component
function EmailSowModal({ job, onClose, onSuccess }) {
  const [recipients, setRecipients] = useState('');
  const [cc, setCc] = useState('');
  const [subject, setSubject] = useState(`Statement of Work - ${job.name}`);
  const [message, setMessage] = useState(
    `Please find attached the Statement of Work for ${job.name}.`,
  );
  const [format, setFormat] = useState('pdf');
  const [sending, setSending] = useState(false);

  const handleSend = async () => {
    if (!recipients.trim()) {
      toast.error('Please enter at least one recipient');
      return;
    }

    setSending(true);
    try {
      const response = await fetch(
        `${process.env.REACT_APP_BACKEND_URL}/api/v2/field-services/jobs/${job.job_id}/sow/email`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${localStorage.getItem('aegisaxiom_session_token')}`,
          },
          body: JSON.stringify({
            recipients: recipients.split(',').map((e) => e.trim()),
            cc: cc ? cc.split(',').map((e) => e.trim()) : [],
            subject,
            message,
            format,
          }),
        },
      );

      if (!response.ok) {
        throw new Error('Failed to send email');
      }

      onSuccess();
    } catch (error) {
      console.error('Email error:', error);
      toast.error('Failed to send email');
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/80 z-[60] flex items-center justify-center p-4">
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-lg p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-bold text-aegisaxiom-text">Email SOW</h3>
          <button onClick={onClose} className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text">
            <X size={20} />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1.5">
              Recipients (comma-separated) *
            </label>
            <input
              type="text"
              value={recipients}
              onChange={(e) => setRecipients(e.target.value)}
              placeholder="email@example.com, another@example.com"
              className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1.5">CC (optional)</label>
            <input
              type="text"
              value={cc}
              onChange={(e) => setCc(e.target.value)}
              placeholder="cc@example.com"
              className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1.5">Subject</label>
            <input
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1.5">Message</label>
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1.5">Format</label>
            <select
              value={format}
              onChange={(e) => setFormat(e.target.value)}
              className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-sm text-aegisaxiom-text"
            >
              <option value="pdf">PDF</option>
              <option value="docx">DOCX</option>
              <option value="markdown">Markdown</option>
              <option value="json">JSON</option>
            </select>
          </div>
        </div>

        <div className="flex gap-3 mt-6">
          <button
            onClick={onClose}
            className="flex-1 px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded text-sm font-medium"
          >
            Cancel
          </button>
          <button
            onClick={handleSend}
            disabled={sending}
            className="flex-1 px-4 py-2 bg-violet-600 hover:bg-violet-700 text-white rounded text-sm font-medium disabled:opacity-50"
          >
            {sending ? 'Sending...' : 'Send Email'}
          </button>
        </div>
      </div>
    </div>
  );
}

import React, { useState } from 'react';
import { X, Clock, CheckCircle, Upload } from 'lucide-react';
import { toast } from 'sonner';

export default function CheckInModal({ job, onClose, onSuccess }) {
  const [notes, setNotes] = useState('');
  const [checklistResponses, setChecklistResponses] = useState({});
  const [loading, setLoading] = useState(false);

  const handleChecklistChange = (itemId, value, itemNotes = '') => {
    setChecklistResponses((prev) => ({
      ...prev,
      [itemId]: { value, notes: itemNotes },
    }));
  };

  const handleSubmit = async () => {
    setLoading(true);
    try {
      const response = await fetch(
        `${process.env.REACT_APP_BACKEND_URL}/api/v2/field-services/jobs/${job.job_id}/check-in`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${localStorage.getItem('aegisaxiom_session_token')}`,
          },
          body: JSON.stringify({
            notes,
            checklist_responses: checklistResponses,
          }),
        },
      );

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Failed to check in');
      }

      const data = await response.json();
      toast.success(
        `Checked in successfully at ${new Date(data.check_in_time).toLocaleTimeString()}`,
      );
      onSuccess();
      onClose();
    } catch (error) {
      console.error('Check-in error:', error);
      toast.error(error.message || 'Failed to check in');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-2xl p-6 animate-fadeInUp max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-emerald-50 dark:bg-emerald-600/10 border border-emerald-200 dark:border-emerald-600/20 flex items-center justify-center">
              <Clock size={20} className="text-emerald-600 dark:text-emerald-400" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-aegisaxiom-text">Check In</h2>
              <p className="text-xs text-aegisaxiom-text-secondary">{job.name}</p>
            </div>
          </div>
          <button onClick={onClose} className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text">
            <X size={20} />
          </button>
        </div>

        {/* Job Info */}
        <div className="mb-6 p-4 rounded-lg bg-aegisaxiom-elevated border border-aegisaxiom-border">
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div>
              <span className="text-aegisaxiom-text-muted">Account:</span>
              <span className="ml-2 text-aegisaxiom-text font-medium">{job.account_name}</span>
            </div>
            <div>
              <span className="text-aegisaxiom-text-muted">Scheduled:</span>
              <span className="ml-2 text-aegisaxiom-text font-medium">{job.scheduled_date || 'Not set'}</span>
            </div>
          </div>
        </div>

        {/* Check-in Checklist */}
        {job.checklist_checkin && job.checklist_checkin.length > 0 && (
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-aegisaxiom-text mb-3">Check-In Checklist</h3>
            <div className="space-y-2">
              {job.checklist_checkin.map((item) => (
                <div
                  key={item.id}
                  className="p-3 rounded-lg bg-aegisaxiom-elevated border border-aegisaxiom-border"
                >
                  <div className="flex items-start gap-3">
                    <button
                      onClick={() =>
                        handleChecklistChange(item.id, !checklistResponses[item.id]?.value)
                      }
                      className={`mt-0.5 w-5 h-5 rounded border-2 flex items-center justify-center transition-colors ${
                        checklistResponses[item.id]?.value
                          ? 'bg-emerald-600 border-emerald-600'
                          : 'border-aegisaxiom-border-hover hover:border-aegisaxiom-border-hover'
                      }`}
                    >
                      {checklistResponses[item.id]?.value && (
                        <CheckCircle size={14} className="text-aegisaxiom-text" />
                      )}
                    </button>
                    <div className="flex-1">
                      <p className="text-sm text-aegisaxiom-text">{item.label}</p>
                      {item.description && (
                        <p className="text-xs text-aegisaxiom-text-muted mt-1">{item.description}</p>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Notes */}
        <div className="mb-6">
          <label className="block text-sm font-medium text-aegisaxiom-text mb-2">
            Notes <span className="text-aegisaxiom-text-muted">(Optional)</span>
          </label>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Arrived on site, have all materials and tools..."
            rows={4}
            className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-600 text-sm"
          />
        </div>

        {/* Actions */}
        <div className="flex gap-3">
          <button
            onClick={onClose}
            className="flex-1 px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg transition-colors text-sm font-medium"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading}
            className="flex-1 px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg transition-colors text-sm font-medium disabled:opacity-50"
          >
            {loading ? 'Checking In...' : 'Check In Now'}
          </button>
        </div>

        {/* Info */}
        <p className="text-xs text-aegisaxiom-text-muted mt-4 text-center">
          Job status will change to "In Progress" and customer will be notified
        </p>
      </div>
    </div>
  );
}

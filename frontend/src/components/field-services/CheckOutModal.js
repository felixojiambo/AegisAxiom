import React, { useState, useEffect } from 'react';
import { X, Clock, CheckCircle, AlertCircle } from 'lucide-react';
import { toast } from 'sonner';
import { formatDistanceToNow } from 'date-fns';

export default function CheckOutModal({ job, onClose, onSuccess }) {
  const [notes, setNotes] = useState('');
  const [checklistResponses, setChecklistResponses] = useState({});
  const [loading, setLoading] = useState(false);
  const [elapsedTime, setElapsedTime] = useState('');

  useEffect(() => {
    if (job.check_in_time) {
      const updateElapsed = () => {
        const checkInDate = new Date(job.check_in_time);
        setElapsedTime(formatDistanceToNow(checkInDate, { addSuffix: false }));
      };
      updateElapsed();
      const interval = setInterval(updateElapsed, 60000); // Update every minute
      return () => clearInterval(interval);
    }
  }, [job.check_in_time]);

  const calculateHours = () => {
    if (!job.check_in_time) return 0;
    const checkInDate = new Date(job.check_in_time);
    const now = new Date();
    const hours = (now - checkInDate) / (1000 * 60 * 60);
    return hours.toFixed(2);
  };

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
        `${process.env.REACT_APP_BACKEND_URL}/api/v2/field-services/jobs/${job.job_id}/check-out`,
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
        throw new Error(error.detail || 'Failed to check out');
      }

      const data = await response.json();

      let message = `Checked out successfully. ${data.hours_worked}h worked.`;
      if (data.time_entry_created) {
        message += ' Time entry auto-created!';
      }

      toast.success(message);
      onSuccess();
      onClose();
    } catch (error) {
      console.error('Check-out error:', error);
      toast.error(error.message || 'Failed to check out');
    } finally {
      setLoading(false);
    }
  };

  const hasContract = job.contract_id && job.contract_id !== '';
  const estimatedHours = calculateHours();

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-2xl p-6 animate-fadeInUp max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-amber-50 dark:bg-amber-600/10 border border-amber-200 dark:border-amber-600/20 flex items-center justify-center">
              <Clock size={20} className="text-amber-600 dark:text-amber-400" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-aegisaxiom-text">Check Out</h2>
              <p className="text-xs text-aegisaxiom-text-secondary">{job.name}</p>
            </div>
          </div>
          <button onClick={onClose} className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text">
            <X size={20} />
          </button>
        </div>

        {/* Time Info */}
        <div className="mb-6 p-4 rounded-lg bg-gradient-to-br from-amber-600/10 to-orange-600/10 border border-amber-200 dark:border-amber-600/20">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs text-aegisaxiom-text-secondary mb-1">Checked In</p>
              <p className="text-sm font-semibold text-aegisaxiom-text">
                {job.check_in_time ? new Date(job.check_in_time).toLocaleTimeString() : 'N/A'}
              </p>
            </div>
            <div className="text-center">
              <p className="text-xs text-aegisaxiom-text-secondary mb-1">Time Worked</p>
              <p className="text-lg font-bold text-amber-600 dark:text-amber-400">{elapsedTime}</p>
              <p className="text-xs text-aegisaxiom-text-muted">≈ {estimatedHours}h</p>
            </div>
            <div className="text-right">
              <p className="text-xs text-aegisaxiom-text-secondary mb-1">Checking Out</p>
              <p className="text-sm font-semibold text-aegisaxiom-text">{new Date().toLocaleTimeString()}</p>
            </div>
          </div>
        </div>

        {/* Time Entry Notice */}
        {hasContract ? (
          <div className="mb-6 p-3 rounded-lg bg-emerald-50 dark:bg-emerald-600/10 border border-emerald-200 dark:border-emerald-600/20 flex items-start gap-3">
            <CheckCircle size={16} className="text-emerald-600 dark:text-emerald-400 mt-0.5" />
            <div className="flex-1">
              <p className="text-sm font-medium text-emerald-700 dark:text-emerald-300">
                Time Entry Will Be Auto-Created
              </p>
              <p className="text-xs text-aegisaxiom-text-secondary mt-1">
                A time entry for {estimatedHours}h will be automatically submitted to time tracking
              </p>
            </div>
          </div>
        ) : (
          <div className="mb-6 p-3 rounded-lg bg-amber-50 dark:bg-amber-600/10 border border-amber-200 dark:border-amber-600/20 flex items-start gap-3">
            <AlertCircle size={16} className="text-amber-600 dark:text-amber-400 mt-0.5" />
            <div className="flex-1">
              <p className="text-sm font-medium text-amber-700 dark:text-amber-300">No Contract Linked</p>
              <p className="text-xs text-aegisaxiom-text-secondary mt-1">
                Hours will be tracked but no time entry will be created (no contract assigned)
              </p>
            </div>
          </div>
        )}

        {/* Check-out Checklist */}
        {job.checklist_checkout && job.checklist_checkout.length > 0 && (
          <div className="mb-6">
            <h3 className="text-sm font-semibold text-aegisaxiom-text mb-3">Check-Out Checklist</h3>
            <div className="space-y-2">
              {job.checklist_checkout.map((item) => (
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
            Completion Notes <span className="text-aegisaxiom-text-muted">(Optional)</span>
          </label>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Job completed successfully. All work tested and verified..."
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
            className="flex-1 px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white rounded-lg transition-colors text-sm font-medium disabled:opacity-50"
          >
            {loading ? 'Checking Out...' : 'Check Out & Complete'}
          </button>
        </div>

        {/* Info */}
        <p className="text-xs text-aegisaxiom-text-muted mt-4 text-center">
          Job will be marked as completed and customer will be notified
        </p>
      </div>
    </div>
  );
}

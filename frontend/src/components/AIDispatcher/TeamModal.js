import React, { useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { X } from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { DEPARTMENTS, ASSIGNMENT_MODE_LABELS } from './constants';

const schema = z.object({
  team_name: z.string().min(1, 'Team name is required'),
  department: z.enum(DEPARTMENTS, { errorMap: () => ({ message: 'Select a department' }) }),
  assignment_mode: z.enum(['round_robin', 'skill_based']),
});

export default function TeamModal({ team, onClose, onSaved }) {
  const isEdit = Boolean(team);

  const {
    register,
    handleSubmit,
    watch,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm({
    resolver: zodResolver(schema),
    defaultValues: {
      team_name: team?.team_name ?? '',
      department: team?.department ?? '',
      assignment_mode: team?.assignment_mode ?? 'round_robin',
    },
  });

  const assignmentMode = watch('assignment_mode');

  // Close on Escape
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const onSubmit = async (values) => {
    try {
      let res;
      if (isEdit) {
        res = await api.patch(`${API_URL}/api/ai-dispatcher/teams/${team.team_id}`, values);
      } else {
        res = await api.post(`${API_URL}/api/ai-dispatcher/teams`, values);
      }
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || 'Failed to save team');
      onSaved();
    } catch (err) {
      alert(err?.message || 'Failed to save team');
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl shadow-2xl w-full max-w-md mx-4">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-aegisaxiom-border">
          <h2 className="text-base font-semibold text-aegisaxiom-text">
            {isEdit ? 'Edit Team' : 'New Team'}
          </h2>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-aegisaxiom-elevated transition-colors text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <form onSubmit={handleSubmit(onSubmit)} className="px-6 py-5 space-y-4">
          {/* Team name */}
          <div>
            <label className="block text-sm text-aegisaxiom-text-secondary mb-1">
              Team name <span className="text-red-400">*</span>
            </label>
            <input
              className="input-field"
              placeholder="e.g. Order Fulfillment"
              {...register('team_name')}
            />
            {errors.team_name && (
              <p className="text-red-400 text-xs mt-1">{errors.team_name.message}</p>
            )}
          </div>

          {/* Department */}
          <div>
            <label className="block text-sm text-aegisaxiom-text-secondary mb-1">
              Department <span className="text-red-400">*</span>
            </label>
            <select
              className="input-field"
              {...register('department')}
            >
              <option value="">Select a department</option>
              {DEPARTMENTS.map((d) => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
            {errors.department && (
              <p className="text-red-400 text-xs mt-1">{errors.department.message}</p>
            )}
          </div>

          {/* Assignment mode */}
          <div>
            <label className="block text-sm text-aegisaxiom-text-secondary mb-2">
              Assignment mode <span className="text-red-400">*</span>
            </label>
            <div className="inline-flex bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg p-1 gap-1">
              {Object.entries(ASSIGNMENT_MODE_LABELS).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setValue('assignment_mode', value, { shouldValidate: true })}
                  className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                    assignmentMode === value
                      ? 'bg-violet-600 text-white'
                      : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            {isEdit && (
              <p className="text-xs text-aegisaxiom-text-muted mt-2">
                Switching modes preserves all member data. Only the active field (order vs. skill) changes.
              </p>
            )}
          </div>

          {/* Actions */}
          <div className="flex gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="btn-secondary flex-1"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="btn-primary flex-1"
            >
              {isSubmitting ? 'Saving…' : isEdit ? 'Save Changes' : 'Create Team'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

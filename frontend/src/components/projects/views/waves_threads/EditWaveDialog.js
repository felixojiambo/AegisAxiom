import React, { useEffect, useMemo, useState } from 'react';
import { X } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import { extractTaskErrorMessage } from '@/lib/safeXhr';
import DateInput from '@/components/ui/DateInput';
import API_URL from '@/lib/apiConfig';

/**
 * EditWaveDialog — adjust an existing wave's goal, dates, and dependencies.
 *
 * PMs frequently need to push wave timelines or revise goals after creation.
 * Mirrors CreateWaveDialog Step 1 chrome (read-only wave_number header,
 * goal input, start/end date inputs, depends_on chip cloud) but PATCHes the
 * existing wave instead of creating one.
 *
 * Cycle prevention: depends_on options exclude the wave being edited AND
 * every wave that depends on it (transitively) — adding any of those as a
 * dependency would create a graph cycle the backend would reject.
 *
 * Submits only changed fields to /api/projects/waves/{wave_id}. Honors the
 * same null-guard pattern from CreateWaveDialog 5dfc462 for date math
 * (start_date / end_date are nullable per the W&T mapping).
 */
export default function EditWaveDialog({
  open,
  onOpenChange,
  wave,
  waves: wavesProp,
  onWaveChanged,
}) {
  const [goal, setGoal] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [dependsOn, setDependsOn] = useState([]);
  const [submitting, setSubmitting] = useState(false);

  const waveId = wave?.wave_id ?? wave?._id;
  const allWaves = useMemo(() => wavesProp ?? [], [wavesProp]);

  // Hydrate form from the wave prop whenever the dialog opens or the wave
  // identity changes. Treat null/undefined dates as empty strings so the
  // DateInput renders cleanly when value is '' (treats it as unset).
  useEffect(() => {
    if (!open || !wave) return;
    setGoal(wave.goal ?? '');
    setStartDate(wave.start_date ?? '');
    setEndDate(wave.end_date ?? '');
    setDependsOn(Array.isArray(wave.depends_on_wave_ids) ? [...wave.depends_on_wave_ids] : []);
  }, [open, wave?.wave_id, wave?._id]);

  // Build the set of wave_ids that depend on the wave being edited
  // (directly or transitively). Adding any of these as a dependency would
  // create a cycle the backend rejects.
  const cycleSafeOptions = useMemo(() => {
    if (!waveId) return [];
    const dependsOnEdited = new Set([waveId]);
    let changed = true;
    // Iterate until no new transitively-dependent wave is found.
    while (changed) {
      changed = false;
      for (const w of allWaves) {
        const wid = w.wave_id ?? w._id;
        if (dependsOnEdited.has(wid)) continue;
        const deps = Array.isArray(w.depends_on_wave_ids) ? w.depends_on_wave_ids : [];
        if (deps.some((d) => dependsOnEdited.has(d))) {
          dependsOnEdited.add(wid);
          changed = true;
        }
      }
    }
    return allWaves
      .filter((w) => !dependsOnEdited.has(w.wave_id ?? w._id))
      .sort((a, b) => a.wave_number - b.wave_number);
  }, [allWaves, waveId]);

  const toggleDep = (id) =>
    setDependsOn((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]);

  const validate = () => {
    if (!goal.trim()) return 'Goal is required.';
    if (startDate && endDate && endDate < startDate) {
      return 'End date must be on/after start date.';
    }
    return null;
  };

  const handleSubmit = async () => {
    const err = validate();
    if (err) { toast.error(err); return; }

    // Build a patch with only the fields that actually changed.
    const patch = {};
    const trimmedGoal = goal.trim();
    if (trimmedGoal !== (wave.goal ?? '')) patch.goal = trimmedGoal;
    if ((startDate || null) !== (wave.start_date ?? null)) {
      patch.start_date = startDate || null;
    }
    if ((endDate || null) !== (wave.end_date ?? null)) {
      patch.end_date = endDate || null;
    }
    const originalDeps = Array.isArray(wave.depends_on_wave_ids) ? wave.depends_on_wave_ids : [];
    const sortedNew = [...dependsOn].sort();
    const sortedOld = [...originalDeps].sort();
    const depsChanged = sortedNew.length !== sortedOld.length
      || sortedNew.some((d, i) => d !== sortedOld[i]);
    if (depsChanged) patch.depends_on_wave_ids = dependsOn;

    if (Object.keys(patch).length === 0) {
      toast.error('Nothing to update.');
      return;
    }

    setSubmitting(true);
    try {
      const res = await api.patch(`${API_URL}/api/projects/waves/${waveId}`, patch);
      if (!res.ok) {
        const msg = await extractTaskErrorMessage(res, 'Could not update wave.');
        toast.error(msg);
        return;
      }
      toast.success(`Wave ${wave.wave_number} updated`);
      onWaveChanged?.();
      onOpenChange(false);
    } catch {
      toast.error('Could not update wave.');
    } finally {
      setSubmitting(false);
    }
  };

  if (!open || !wave) return null;

  return (
    <div
      className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center"
      onClick={() => onOpenChange(false)}
      data-testid="edit-wave-dialog-backdrop"
    >
      <div
        className="glass-card w-[640px] max-h-[85vh] overflow-y-auto p-6 relative"
        onClick={(e) => e.stopPropagation()}
        data-testid="edit-wave-dialog"
        role="dialog"
        aria-label="Edit wave"
      >
        <button
          type="button"
          onClick={() => onOpenChange(false)}
          className="absolute right-3 top-3 text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
          aria-label="Close"
        >
          <X size={14} />
        </button>

        <h3 className="text-sm font-bold text-aegisaxiom-text mb-1">
          Edit Wave {wave.wave_number}
        </h3>
        <p className="text-[11px] text-aegisaxiom-text-muted mb-4">
          Adjust goal, timeline, or dependencies. Wave number is fixed and cannot be changed.
        </p>

        <div className="space-y-4">
          <div>
            <label className="block text-xs text-aegisaxiom-text-muted mb-1">
              Goal <span className="text-red-400">*</span>
            </label>
            <input
              className="aegisaxiom-input w-full"
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              maxLength={240}
              placeholder="Ship onboarding flow to 10% of new users"
              data-testid="edit-wave-goal"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-aegisaxiom-text-muted mb-1">Start</label>
              <DateInput
                value={startDate}
                onChange={setStartDate}
                data-testid="edit-wave-start"
                placeholder="Start"
              />
            </div>
            <div>
              <label className="block text-xs text-aegisaxiom-text-muted mb-1">End</label>
              <DateInput
                value={endDate}
                onChange={setEndDate}
                data-testid="edit-wave-end"
                placeholder="End"
                min={startDate || undefined}
              />
            </div>
          </div>
          <div>
            <label className="block text-xs text-aegisaxiom-text-muted mb-1">Depends on</label>
            {cycleSafeOptions.length === 0 ? (
              <p className="text-[11px] text-aegisaxiom-text-muted">No eligible waves available.</p>
            ) : (
              <div className="flex flex-wrap gap-1.5 items-center py-1">
                {cycleSafeOptions.map((w) => {
                  const wid = w.wave_id ?? w._id;
                  const on = dependsOn.includes(wid);
                  return (
                    <button
                      key={wid}
                      type="button"
                      onClick={() => toggleDep(wid)}
                      data-testid={`edit-wave-dep-${w.wave_number}`}
                      className={`h-6 px-2 inline-flex items-center justify-center rounded-full text-[11px] font-mono border transition-colors ${on ? 'btn-primary' : 'btn-secondary'}`}
                    >
                      WAVE {w.wave_number}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        <div className="flex justify-end gap-2 mt-5">
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="btn-secondary h-8 px-3 text-xs"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting}
            className="btn-primary h-8 px-3 text-xs disabled:opacity-60"
            data-testid="edit-wave-submit"
          >
            {submitting ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      </div>
    </div>
  );
}

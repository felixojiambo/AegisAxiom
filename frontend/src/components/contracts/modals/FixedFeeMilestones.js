import React, { useEffect, useState } from 'react';
import { api } from '../../../lib/api';
import { toast } from 'sonner';
import { Milestone, X } from 'lucide-react';
import API_URL from '../../../lib/apiConfig';

const API = API_URL;
const fmt = (n) =>
  `$${(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export default function FixedFeeMilestones({
  totalAmount,
  milestones,
  onUpdateAmount,
  onUpdateMilestones,
}) {
  const [nlInput, setNlInput] = useState('');
  const [templates, setTemplates] = useState([]);
  const [parsing, setParsing] = useState(false);
  const [previewMilestones, setPreviewMilestones] = useState(null);

  useEffect(() => {
    loadTemplates();
  }, []);

  const loadTemplates = async () => {
    try {
      const res = await api.get(`${API}/api/contracts/milestone-templates`);
      if (res.ok) {
        const data = await res.json();
        setTemplates(data.templates || []);
      }
    } catch (err) {
      console.error('Failed to load templates:', err);
    }
  };

  const parseNaturalLanguage = async (description) => {
    if (!description || !description.trim()) {
      toast.error('Please enter a description');
      return;
    }
    if (!totalAmount || parseFloat(totalAmount) <= 0) {
      toast.error('Please enter total contract amount first');
      return;
    }

    setParsing(true);
    try {
      const res = await api.post(`${API}/api/contracts/parse-milestones`, {
        description: description.trim(),
        total_amount: parseFloat(totalAmount),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to parse milestones');
      }

      const data = await res.json();

      // Add IDs and status to milestones
      const parsedMilestones = data.milestones.map((m, i) => ({
        id: `ms_${Date.now()}_${i}`,
        name: m.name,
        percentage: m.percentage,
        amount: m.amount,
        description: m.description || '',
        status: 'pending',
        due_date: '',
      }));

      setPreviewMilestones({ milestones: parsedMilestones, interpretation: data.interpretation });
      toast.success('Milestones parsed! Review and apply.');
    } catch (err) {
      toast.error(err.message || 'Failed to parse milestones');
    } finally {
      setParsing(false);
    }
  };

  const applyParsedMilestones = () => {
    if (previewMilestones) {
      onUpdateMilestones(previewMilestones.milestones);
      setPreviewMilestones(null);
      setNlInput('');
      toast.success(`Applied ${previewMilestones.milestones.length} milestones`);
    }
  };

  const updateMilestone = (idx, field, value) => {
    const updated = [...milestones];
    updated[idx][field] = value;

    // Recalculate amount if percentage changes
    if (field === 'percentage' && totalAmount) {
      updated[idx].amount = parseFloat(totalAmount) * (parseFloat(value) / 100);
    }

    onUpdateMilestones(updated);
  };

  const removeMilestone = (idx) => {
    onUpdateMilestones(milestones.filter((_, i) => i !== idx));
  };

  const clearMilestones = () => {
    onUpdateMilestones([]);
    setPreviewMilestones(null);
    toast.success('Milestones cleared');
  };

  return (
    <div className="bg-purple-900/10 border border-purple-800/30 rounded-lg p-3 space-y-3">
      <h4 className="text-xs font-semibold text-purple-300">Fixed Fee Configuration</h4>

      {/* Total Amount */}
      <div>
        <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Total Contract Amount ($) *</label>
        <input
          type="number"
          step="0.01"
          value={totalAmount}
          onChange={(e) => onUpdateAmount(e.target.value)}
          required
          className="w-full px-3 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text focus:border-violet-500 outline-none"
          placeholder="e.g., 100000"
        />
      </div>

      {/* Natural Language Parser */}
      <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded p-3 space-y-2">
        <div className="flex items-center gap-2">
          <Milestone size={14} className="text-violet-600 dark:text-violet-400" />
          <label className="text-xs font-medium text-aegisaxiom-text-secondary">Describe Your Milestones</label>
        </div>

        {/* Template Dropdown */}
        <div>
          <label className="text-[10px] text-aegisaxiom-text-muted block mb-1">Quick Templates</label>
          <select
            value=""
            onChange={(e) => {
              if (e.target.value) {
                setNlInput(e.target.value);
                parseNaturalLanguage(e.target.value);
              }
            }}
            className="w-full px-2 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none"
          >
            <option value="">Choose a template...</option>
            {templates.map((t) => (
              <option key={t.name} value={t.value}>
                {t.name} — {t.description}
              </option>
            ))}
          </select>
        </div>

        {/* Natural Language Input */}
        <div>
          <label className="text-[10px] text-aegisaxiom-text-muted block mb-1">
            Or describe in your own words
          </label>
          <div className="flex gap-2">
            <input
              type="text"
              value={nlInput}
              onChange={(e) => setNlInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && parseNaturalLanguage(nlInput)}
              placeholder='e.g., "50% upfront, 50% on completion" or "3 equal milestones"'
              className="flex-1 px-3 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text placeholder-aegisaxiom-text-muted outline-none"
            />
            <button
              type="button"
              onClick={() => parseNaturalLanguage(nlInput)}
              disabled={parsing || !nlInput.trim() || !totalAmount}
              className="px-3 py-1.5 bg-violet-600 hover:bg-violet-500 disabled:bg-aegisaxiom-elevated disabled:text-aegisaxiom-text-muted text-white rounded text-xs font-medium"
            >
              {parsing ? 'Parsing...' : 'Parse'}
            </button>
          </div>
          <p className="text-[10px] text-aegisaxiom-text-muted mt-1">
            AI will convert your description into structured milestones
          </p>
        </div>
      </div>

      {/* Preview Parsed Milestones */}
      {previewMilestones && (
        <div className="bg-violet-900/20 border border-violet-700/50 rounded p-3 space-y-2">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-violet-700 dark:text-violet-300">Preview Parsed Milestones</p>
              <p className="text-[10px] text-aegisaxiom-text-muted mt-0.5">
                {previewMilestones.interpretation}
              </p>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setPreviewMilestones(null)}
                className="text-xs text-aegisaxiom-text-secondary hover:text-aegisaxiom-text-secondary"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={applyParsedMilestones}
                className="px-3 py-1 bg-violet-600 hover:bg-violet-500 text-white rounded text-xs font-medium"
              >
                Apply
              </button>
            </div>
          </div>
          <div className="space-y-1">
            {previewMilestones.milestones.map((m, i) => (
              <div
                key={i}
                className="flex items-center gap-2 bg-aegisaxiom-elevated px-2 py-1.5 rounded text-xs"
              >
                <span className="flex-1 text-aegisaxiom-text">{m.name}</span>
                <span className="text-violet-600 dark:text-violet-400">{m.percentage}%</span>
                <span className="text-aegisaxiom-text-secondary">{fmt(m.amount)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Current Milestones */}
      {milestones && milestones.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label className="text-xs text-aegisaxiom-text-secondary">
              Current Milestones ({milestones.length})
            </label>
            <button
              type="button"
              onClick={clearMilestones}
              className="text-xs text-red-600 dark:text-red-400 hover:text-red-700 dark:text-red-300"
            >
              Clear All
            </button>
          </div>
          <div className="space-y-1.5">
            {milestones.map((m, i) => (
              <div key={m.id || i} className="bg-aegisaxiom-elevated p-2 rounded space-y-2">
                <div className="flex items-center gap-2">
                  <input
                    placeholder="Milestone name"
                    value={m.name}
                    onChange={(e) => updateMilestone(i, 'name', e.target.value)}
                    className="flex-1 px-2 py-1 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none"
                  />
                  <input
                    type="number"
                    step="0.01"
                    placeholder="%"
                    value={m.percentage}
                    onChange={(e) => updateMilestone(i, 'percentage', e.target.value)}
                    className="w-16 px-2 py-1 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none"
                  />
                  <span className="text-xs text-aegisaxiom-text-secondary w-24">{fmt(m.amount || 0)}</span>
                  <button
                    type="button"
                    onClick={() => removeMilestone(i)}
                    className="text-red-600 dark:text-red-400 hover:text-red-700 dark:text-red-300"
                  >
                    <X size={14} />
                  </button>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-[10px] text-aegisaxiom-text-muted block mb-0.5">Due Date</label>
                    <input
                      type="date"
                      value={m.due_date || ''}
                      onChange={(e) => updateMilestone(i, 'due_date', e.target.value)}
                      className="w-full px-2 py-1 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none"
                    />
                  </div>
                  <div>
                    <label className="text-[10px] text-aegisaxiom-text-muted block mb-0.5">
                      Description (optional)
                    </label>
                    <input
                      placeholder="Brief description"
                      value={m.description || ''}
                      onChange={(e) => updateMilestone(i, 'description', e.target.value)}
                      className="w-full px-2 py-1 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text outline-none"
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>
          <div className="flex items-center justify-between text-xs mt-2 pt-2 border-t border-aegisaxiom-border">
            <span className="text-aegisaxiom-text-muted">Total:</span>
            <div className="flex items-center gap-3">
              <span className="text-violet-600 dark:text-violet-400">
                {milestones.reduce((sum, m) => sum + parseFloat(m.percentage || 0), 0).toFixed(2)}%
              </span>
              <span className="text-aegisaxiom-text font-medium">
                {fmt(milestones.reduce((sum, m) => sum + parseFloat(m.amount || 0), 0))}
              </span>
            </div>
          </div>
        </div>
      )}

      {(!milestones || milestones.length === 0) && !previewMilestones && (
        <div className="text-center py-6 text-aegisaxiom-text-muted text-xs">
          <Milestone size={24} className="mx-auto mb-2 opacity-30" />
          <p>No milestones yet. Use the parser above to get started.</p>
        </div>
      )}
    </div>
  );
}

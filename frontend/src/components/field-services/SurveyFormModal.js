import React, { useState } from 'react';
import { X, ClipboardList, Save, Plus, Trash2 } from 'lucide-react';
import { toast } from 'sonner';

export default function SurveyFormModal({ job, onClose, onSuccess }) {
  const [formData, setFormData] = useState({
    point_of_contact_name: '',
    point_of_contact_email: '',
    point_of_contact_phone: '',
    existing_cabling: false,
    existing_cabling_types: [],
    cable_quantity: '',
    ceiling_type: '',
    ceiling_height: '',
    mdf_location: '',
    patch_panel: '',
    cable_types_needed: [],
    hour_estimate: '',
    technicians_required: 1,
    materials_needed: '',
    tools_required: '',
    scope_of_work_description: '',
    deliverables: '',
    materials_cost: '',
    general_observations: '',
    _auto_complete: false,
  });
  const [scopeTasks, setScopeTasks] = useState([{ task: '', quantity: '1', description: '' }]);
  const [loading, setLoading] = useState(false);

  const handleChange = (field, value) => {
    setFormData((prev) => ({ ...prev, [field]: value }));
  };

  const handleArrayToggle = (field, value) => {
    setFormData((prev) => ({
      ...prev,
      [field]: prev[field].includes(value)
        ? prev[field].filter((v) => v !== value)
        : [...prev[field], value],
    }));
  };

  const addTask = () => {
    setScopeTasks([...scopeTasks, { task: '', quantity: '1', description: '' }]);
  };

  const removeTask = (index) => {
    setScopeTasks(scopeTasks.filter((_, i) => i !== index));
  };

  const updateTask = (index, field, value) => {
    const updated = [...scopeTasks];
    updated[index][field] = value;
    setScopeTasks(updated);
  };

  const handleSubmit = async () => {
    // Basic validation
    if (!formData.hour_estimate) {
      toast.error('Please provide an hour estimate');
      return;
    }

    // Validate that at least one task is filled
    const validTasks = scopeTasks.filter((t) => t.task.trim());
    if (validTasks.length === 0) {
      toast.error('Please add at least one task to the scope of work');
      return;
    }

    setLoading(true);
    try {
      const response = await fetch(
        `${process.env.REACT_APP_BACKEND_URL}/api/v2/field-services/jobs/${job.job_id}/survey-response`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${localStorage.getItem('aegisaxiom_session_token')}`,
          },
          body: JSON.stringify({
            ...formData,
            scope_tasks: validTasks, // Add structured tasks
          }),
        },
      );

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Failed to submit survey');
      }

      toast.success('Survey submitted successfully');
      onSuccess();
      onClose();
    } catch (error) {
      console.error('Survey submission error:', error);
      toast.error(error.message || 'Failed to submit survey');
    } finally {
      setLoading(false);
    }
  };

  const cableTypes = ['CAT5E', 'CAT6', 'CAT6A', 'CAT7', 'Fiber'];
  const ceilingTypes = ['Drop tile', 'Drywall', 'Open', 'Concrete'];

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-4xl p-6 animate-fadeInUp max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-blue-50 dark:bg-blue-600/10 border border-blue-200 dark:border-blue-600/20 flex items-center justify-center">
              <ClipboardList size={20} className="text-blue-600 dark:text-blue-400" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-aegisaxiom-text">Site Survey Form</h2>
              <p className="text-xs text-aegisaxiom-text-secondary">
                {job.name} - {job.account_name}
              </p>
            </div>
          </div>
          <button onClick={onClose} className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text">
            <X size={20} />
          </button>
        </div>

        <div className="space-y-6">
          {/* Contact Information */}
          <section>
            <h3 className="text-sm font-semibold text-aegisaxiom-text mb-3 pb-2 border-b border-aegisaxiom-border">
              Point of Contact
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1.5">Name</label>
                <input
                  type="text"
                  value={formData.point_of_contact_name}
                  onChange={(e) => handleChange('point_of_contact_name', e.target.value)}
                  className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-600"
                  placeholder="John Doe"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1.5">Email</label>
                <input
                  type="email"
                  value={formData.point_of_contact_email}
                  onChange={(e) => handleChange('point_of_contact_email', e.target.value)}
                  className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-600"
                  placeholder="john@example.com"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1.5">Phone</label>
                <input
                  type="tel"
                  value={formData.point_of_contact_phone}
                  onChange={(e) => handleChange('point_of_contact_phone', e.target.value)}
                  className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-600"
                  placeholder="555-1234"
                />
              </div>
            </div>
          </section>

          {/* Existing Infrastructure */}
          <section>
            <h3 className="text-sm font-semibold text-aegisaxiom-text mb-3 pb-2 border-b border-aegisaxiom-border">
              Existing Infrastructure
            </h3>
            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={formData.existing_cabling}
                  onChange={(e) => handleChange('existing_cabling', e.target.checked)}
                  className="w-4 h-4 rounded border-aegisaxiom-border-hover bg-aegisaxiom-elevated"
                />
                <label className="text-sm text-aegisaxiom-text">Existing cabling present</label>
              </div>

              {formData.existing_cabling && (
                <div>
                  <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-2">
                    Cable Types Present
                  </label>
                  <div className="flex flex-wrap gap-2">
                    {cableTypes.map((type) => (
                      <button
                        key={type}
                        onClick={() => handleArrayToggle('existing_cabling_types', type)}
                        className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                          formData.existing_cabling_types.includes(type)
                            ? 'bg-violet-600 text-white'
                            : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:bg-aegisaxiom-elevated'
                        }`}
                      >
                        {type}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1.5">
                    Cable Quantity (drops)
                  </label>
                  <input
                    type="number"
                    value={formData.cable_quantity}
                    onChange={(e) => handleChange('cable_quantity', e.target.value)}
                    className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-600"
                    placeholder="24"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1.5">
                    MDF Location
                  </label>
                  <input
                    type="text"
                    value={formData.mdf_location}
                    onChange={(e) => handleChange('mdf_location', e.target.value)}
                    className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-600"
                    placeholder="Room 101, 2nd floor"
                  />
                </div>
              </div>
            </div>
          </section>

          {/* Site Conditions */}
          <section>
            <h3 className="text-sm font-semibold text-aegisaxiom-text mb-3 pb-2 border-b border-aegisaxiom-border">
              Site Conditions
            </h3>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1.5">
                  Ceiling Type
                </label>
                <select
                  value={formData.ceiling_type}
                  onChange={(e) => handleChange('ceiling_type', e.target.value)}
                  className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-600"
                >
                  <option value="">Select...</option>
                  {ceilingTypes.map((type) => (
                    <option key={type} value={type}>
                      {type}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1.5">
                  Ceiling Height
                </label>
                <input
                  type="text"
                  value={formData.ceiling_height}
                  onChange={(e) => handleChange('ceiling_height', e.target.value)}
                  className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-600"
                  placeholder="9 feet"
                />
              </div>
            </div>
          </section>

          {/* Materials Needed */}
          <section>
            <h3 className="text-sm font-semibold text-aegisaxiom-text mb-3 pb-2 border-b border-aegisaxiom-border">
              Materials & Tools
            </h3>
            <div className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-2">
                  Cable Types Needed
                </label>
                <div className="flex flex-wrap gap-2">
                  {cableTypes.map((type) => (
                    <button
                      key={type}
                      onClick={() => handleArrayToggle('cable_types_needed', type)}
                      className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                        formData.cable_types_needed.includes(type)
                          ? 'bg-emerald-600 text-white'
                          : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:bg-aegisaxiom-elevated'
                      }`}
                    >
                      {type}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1.5">
                  Materials Needed (detailed list)
                </label>
                <textarea
                  value={formData.materials_needed}
                  onChange={(e) => handleChange('materials_needed', e.target.value)}
                  rows={3}
                  className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-600"
                  placeholder="CAT6A cable (2000ft), 48-port patch panel, 40x RJ45 jacks..."
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1.5">
                  Tools Required
                </label>
                <input
                  type="text"
                  value={formData.tools_required}
                  onChange={(e) => handleChange('tools_required', e.target.value)}
                  className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-600"
                  placeholder="Cable tester, Punch-down tool, Ladder, Fish tape"
                />
              </div>
            </div>
          </section>

          {/* Scope & Estimates */}
          <section>
            <h3 className="text-sm font-semibold text-aegisaxiom-text mb-3 pb-2 border-b border-aegisaxiom-border">
              Scope & Estimates
            </h3>
            <div className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1.5">
                  Scope of Work Description
                </label>
                <textarea
                  value={formData.scope_of_work_description}
                  onChange={(e) => handleChange('scope_of_work_description', e.target.value)}
                  rows={3}
                  className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-600"
                  placeholder="Install structured cabling system with 40 CAT6A drops..."
                />
              </div>

              {/* Structured Tasks */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="block text-xs font-medium text-aegisaxiom-text-secondary">
                    Tasks / Deliverables *
                  </label>
                  <button
                    type="button"
                    onClick={addTask}
                    className="px-2 py-1 bg-violet-50 dark:bg-violet-600/20 hover:bg-violet-600/30 text-violet-700 dark:text-violet-300 rounded text-xs flex items-center gap-1"
                  >
                    <Plus size={12} /> Add Task
                  </button>
                </div>
                <div className="space-y-2">
                  {scopeTasks.map((task, index) => (
                    <div
                      key={index}
                      className="p-3 rounded-lg bg-aegisaxiom-elevated border border-aegisaxiom-border"
                    >
                      <div className="grid grid-cols-12 gap-2">
                        <div className="col-span-5">
                          <input
                            type="text"
                            value={task.task}
                            onChange={(e) => updateTask(index, 'task', e.target.value)}
                            placeholder="Task name (e.g., Install cable runs)"
                            className="w-full px-2 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-xs"
                          />
                        </div>
                        <div className="col-span-2">
                          <input
                            type="text"
                            value={task.quantity}
                            onChange={(e) => updateTask(index, 'quantity', e.target.value)}
                            placeholder="Qty"
                            className="w-full px-2 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-xs"
                          />
                        </div>
                        <div className="col-span-4">
                          <input
                            type="text"
                            value={task.description}
                            onChange={(e) => updateTask(index, 'description', e.target.value)}
                            placeholder="Description"
                            className="w-full px-2 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-xs"
                          />
                        </div>
                        <div className="col-span-1 flex items-center justify-center">
                          {scopeTasks.length > 1 && (
                            <button
                              type="button"
                              onClick={() => removeTask(index)}
                              className="text-red-600 dark:text-red-400 hover:text-red-700 dark:text-red-300 p-1"
                            >
                              <Trash2 size={14} />
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
                <p className="text-[9px] text-aegisaxiom-text-muted mt-1">
                  These tasks will become checklists for execution jobs
                </p>
              </div>

              <div>
                <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1.5">
                  Deliverables (one per line)
                </label>
                <textarea
                  value={formData.deliverables}
                  onChange={(e) => handleChange('deliverables', e.target.value)}
                  rows={3}
                  className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-600"
                  placeholder="Install 40 CAT6A cable runs&#10;Terminate all cables at patch panel&#10;Test and certify all connections"
                />
              </div>

              <div className="grid grid-cols-3 gap-4">
                <div>
                  <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1.5">
                    Hour Estimate *
                  </label>
                  <input
                    type="number"
                    value={formData.hour_estimate}
                    onChange={(e) => handleChange('hour_estimate', e.target.value)}
                    className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-600"
                    placeholder="40"
                    required
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1.5">
                    Technicians Needed
                  </label>
                  <input
                    type="number"
                    value={formData.technicians_required}
                    onChange={(e) =>
                      handleChange('technicians_required', parseInt(e.target.value) || 1)
                    }
                    className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-600"
                    placeholder="1"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-1.5">
                    Materials Cost Est.
                  </label>
                  <input
                    type="number"
                    value={formData.materials_cost}
                    onChange={(e) => handleChange('materials_cost', e.target.value)}
                    className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-600"
                    placeholder="1500"
                  />
                </div>
              </div>
            </div>
          </section>

          {/* General Observations */}
          <section>
            <h3 className="text-sm font-semibold text-aegisaxiom-text mb-3 pb-2 border-b border-aegisaxiom-border">
              Additional Notes
            </h3>
            <textarea
              value={formData.general_observations}
              onChange={(e) => handleChange('general_observations', e.target.value)}
              rows={4}
              className="w-full px-3 py-2 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-600"
              placeholder="Any other observations, challenges, or recommendations..."
            />
          </section>

          {/* Auto-complete option */}
          <div className="flex items-center gap-2 p-3 rounded-lg bg-emerald-50 dark:bg-emerald-600/10 border border-emerald-200 dark:border-emerald-600/20">
            <input
              type="checkbox"
              checked={formData._auto_complete}
              onChange={(e) => handleChange('_auto_complete', e.target.checked)}
              className="w-4 h-4 rounded border-aegisaxiom-border-hover bg-aegisaxiom-elevated"
            />
            <label className="text-sm text-emerald-700 dark:text-emerald-300">
              Mark survey job as completed upon submission
            </label>
          </div>
        </div>

        {/* Actions */}
        <div className="flex gap-3 pt-6 mt-6 border-t border-aegisaxiom-border">
          <button
            onClick={onClose}
            className="flex-1 px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg transition-colors text-sm font-medium"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading}
            className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors text-sm font-medium disabled:opacity-50 flex items-center justify-center gap-2"
          >
            <Save size={16} />
            {loading ? 'Submitting...' : 'Submit Survey'}
          </button>
        </div>
      </div>
    </div>
  );
}

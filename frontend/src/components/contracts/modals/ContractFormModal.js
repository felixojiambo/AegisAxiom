import React from 'react';
import { X, Wrench, Folder } from 'lucide-react';
import { Link2 } from 'lucide-react';
import ContractFormFields from '../ContractFormFields';

export default function ContractFormModal({
  modal,
  form,
  setForm,
  accounts,
  users,
  defaultResourceTypes,
  deals,
  projects,
  isAdmin,
  onSubmit,
  onClose,
  loading,
}) {
  if (modal !== 'create' && modal !== 'edit') return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" />
      <div className="relative bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-lg max-h-[85vh] overflow-y-auto">
        <div className="sticky top-0 bg-aegisaxiom-surface backdrop-blur border-b border-aegisaxiom-border p-4 flex justify-between items-center">
          <h2 className="text-sm font-semibold text-aegisaxiom-text">
            {modal === 'edit' ? 'Edit Contract' : 'New Contract'}
          </h2>
          <button onClick={onClose} className="text-aegisaxiom-text-muted hover:text-aegisaxiom-text">
            <X size={18} />
          </button>
        </div>
        <form onSubmit={onSubmit} className="p-4 space-y-3">
          {/* Deal Import Indicator */}
          {form._fromDealImport && form.deal_ids?.length > 0 && (
            <div className="bg-violet-900/15 border border-violet-700/30 rounded-lg p-3 mb-1">
              <p className="text-xs text-violet-700 dark:text-violet-300 flex items-center gap-1.5">
                <Link2 size={12} />
                Linked to deal:{' '}
                <span className="font-medium">
                  {form.notes?.replace('Created from deal: ', '') || 'Unknown'}
                </span>
              </p>
            </div>
          )}

          {/* Use shared ContractFormFields component */}
          <ContractFormFields
            form={form}
            setForm={setForm}
            accounts={accounts}
            users={users}
            defaultResourceTypes={defaultResourceTypes}
            showServiceCategory={modal === 'create'}
            compact={false}
            isAdmin={isAdmin}
            deals={deals}
            showDealLinking={!form._fromDealImport}
          />

          {/* ============ PROJECT / FIELD SERVICES JOB SECTION ============ */}
          {form.service_category === 'field_services' ? (
            // Field Services Job Assignment (Placeholder for future functionality)
            <div className="bg-orange-50 dark:bg-orange-900/10 border border-orange-200 dark:border-orange-800/30 rounded-lg p-3 space-y-3">
              <div className="flex items-center gap-2">
                <Wrench size={14} className="text-orange-600 dark:text-orange-400" />
                <h4 className="text-xs font-semibold text-orange-700 dark:text-orange-300">
                  Field Services Job Assignment
                </h4>
              </div>

              {/* Job Mode Selection */}
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() =>
                    setForm((f) => ({
                      ...f,
                      project_mode: 'none',
                      project_id: '',
                      new_project_name: '',
                      new_project_description: '',
                    }))
                  }
                  className={`flex-1 px-3 py-2 rounded text-xs transition ${
                    form.project_mode === 'none'
                      ? 'bg-orange-600 text-white'
                      : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:text-aegisaxiom-text'
                  }`}
                >
                  No Job
                </button>
                <button
                  type="button"
                  onClick={() =>
                    setForm((f) => ({
                      ...f,
                      project_mode: 'existing',
                      new_project_name: '',
                      new_project_description: '',
                    }))
                  }
                  className={`flex-1 px-3 py-2 rounded text-xs transition ${
                    form.project_mode === 'existing'
                      ? 'bg-orange-600 text-white'
                      : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:text-aegisaxiom-text'
                  }`}
                >
                  Link Existing
                </button>
                <button
                  type="button"
                  onClick={() => setForm((f) => ({ ...f, project_mode: 'new', project_id: '' }))}
                  className={`flex-1 px-3 py-2 rounded text-xs transition ${
                    form.project_mode === 'new'
                      ? 'bg-orange-600 text-white'
                      : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:text-aegisaxiom-text'
                  }`}
                >
                  Create New
                </button>
              </div>

              {/* Link Existing Job */}
              {form.project_mode === 'existing' && (
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                    Select Field Services Job
                  </label>
                  <select
                    disabled
                    className="w-full px-3 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text-muted cursor-not-allowed"
                  >
                    <option value="">No jobs available yet</option>
                  </select>
                  <p className="text-xs text-orange-600 dark:text-orange-400/70 mt-1 italic">
                    Field Services job management coming soon
                  </p>
                </div>
              )}

              {/* Create New Job */}
              {form.project_mode === 'new' && (
                <div className="space-y-2">
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Job Name</label>
                    <input
                      disabled
                      placeholder="e.g., Network Installation - Building A"
                      className="w-full px-3 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text-muted cursor-not-allowed"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Job Description</label>
                    <textarea
                      disabled
                      placeholder="Job details..."
                      rows={2}
                      className="w-full px-3 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text-muted cursor-not-allowed"
                    />
                  </div>
                  <div className="bg-orange-50 dark:bg-orange-900/20 border border-orange-200 dark:border-orange-700/30 rounded p-2">
                    <p className="text-xs text-orange-700 dark:text-orange-300 font-medium mb-1">Coming Soon:</p>
                    <ul className="text-xs text-aegisaxiom-text-secondary space-y-0.5">
                      <li>• Create and assign field service jobs</li>
                      <li>• Track job progress and completion</li>
                      <li>• Assign field technicians</li>
                    </ul>
                  </div>
                </div>
              )}

              {form.project_mode === 'none' && (
                <p className="text-xs text-aegisaxiom-text-muted text-center py-2">
                  This contract won't be linked to any field services job
                </p>
              )}
            </div>
          ) : (
            // Project Assignment for Professional Services, Wholesale MRR, Referral MRR
            <div className="bg-blue-900/10 border border-blue-800/30 rounded-lg p-3 space-y-3">
              <div className="flex items-center gap-2">
                <Folder size={14} className="text-blue-600 dark:text-blue-400" />
                <h4 className="text-xs font-semibold text-blue-700 dark:text-blue-300">Project Assignment</h4>
              </div>

              {/* Project Mode Selection */}
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() =>
                    setForm((f) => ({
                      ...f,
                      project_mode: 'none',
                      project_id: '',
                      new_project_name: '',
                      new_project_description: '',
                    }))
                  }
                  className={`flex-1 px-3 py-2 rounded text-xs transition ${
                    form.project_mode === 'none'
                      ? 'bg-blue-600 text-white'
                      : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:text-aegisaxiom-text'
                  }`}
                >
                  No Project
                </button>
                <button
                  type="button"
                  onClick={() =>
                    setForm((f) => ({
                      ...f,
                      project_mode: 'existing',
                      new_project_name: '',
                      new_project_description: '',
                    }))
                  }
                  className={`flex-1 px-3 py-2 rounded text-xs transition ${
                    form.project_mode === 'existing'
                      ? 'bg-blue-600 text-white'
                      : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:text-aegisaxiom-text'
                  }`}
                >
                  Link Existing
                </button>
                <button
                  type="button"
                  onClick={() => setForm((f) => ({ ...f, project_mode: 'new', project_id: '' }))}
                  className={`flex-1 px-3 py-2 rounded text-xs transition ${
                    form.project_mode === 'new'
                      ? 'bg-blue-600 text-white'
                      : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:text-aegisaxiom-text'
                  }`}
                >
                  Create New
                </button>
              </div>

              {/* Link Existing Project */}
              {form.project_mode === 'existing' && (
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Select Project</label>
                  <select
                    value={form.project_id || ''}
                    onChange={(e) => setForm((f) => ({ ...f, project_id: e.target.value }))}
                    className="w-full px-3 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text focus:border-blue-500 outline-none"
                  >
                    <option value="">Choose a project...</option>
                    {projects
                      .filter((p) => !form.account_id || p.account_id === form.account_id)
                      .map((p) => (
                        <option key={p.project_id} value={p.project_id}>
                          {p.name} {p.status ? `(${p.status})` : ''}
                        </option>
                      ))}
                  </select>
                  {form.account_id &&
                    projects.filter((p) => p.account_id === form.account_id).length === 0 && (
                      <p className="text-xs text-aegisaxiom-text-muted mt-1">
                        No projects found for this account. Create a new one instead.
                      </p>
                    )}
                </div>
              )}

              {/* Create New Project */}
              {form.project_mode === 'new' && (
                <div className="space-y-2">
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">Project Name *</label>
                    <input
                      value={form.new_project_name || ''}
                      onChange={(e) => setForm((f) => ({ ...f, new_project_name: e.target.value }))}
                      placeholder="e.g., Website Redesign 2025"
                      className="w-full px-3 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text focus:border-blue-500 outline-none"
                      required={form.project_mode === 'new'}
                    />
                  </div>
                  <div>
                    <label className="text-xs text-aegisaxiom-text-secondary block mb-1">
                      Description (optional)
                    </label>
                    <textarea
                      value={form.new_project_description || ''}
                      onChange={(e) =>
                        setForm((f) => ({ ...f, new_project_description: e.target.value }))
                      }
                      placeholder="Brief project description..."
                      rows={2}
                      className="w-full px-3 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded text-xs text-aegisaxiom-text focus:border-blue-500 outline-none"
                    />
                  </div>
                  <div className="bg-blue-900/20 border border-blue-700/30 rounded p-2">
                    <p className="text-xs text-blue-700 dark:text-blue-300 font-medium mb-1">Auto-assigned Access:</p>
                    <ul className="text-xs text-aegisaxiom-text-secondary space-y-0.5">
                      <li>• Contractors from this contract</li>
                      <li>• Customers from the account</li>
                      <li>• Sales rep & agent of record</li>
                    </ul>
                  </div>
                </div>
              )}

              {form.project_mode === 'none' && (
                <p className="text-xs text-aegisaxiom-text-muted text-center py-2">
                  This contract won't be linked to any project
                </p>
              )}
            </div>
          )}

          <div className="flex gap-2 pt-2">
            <button
              type="submit"
              disabled={loading}
              className="flex-1 bg-violet-600 hover:bg-violet-500 text-white text-xs py-2 rounded-md transition disabled:opacity-50"
            >
              {loading ? 'Saving...' : modal === 'edit' ? 'Update Contract' : 'Create Contract'}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="px-4 text-xs text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition"
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

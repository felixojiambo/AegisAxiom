import React, { useEffect, useState } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { Plus, Search, Edit2, X, Wrench } from 'lucide-react';
import { toast } from 'sonner';
import { api, unwrap } from '../lib/api';
import API_URL from '../lib/apiConfig';

const API = API_URL;
const fmt = (n) => `$${(n || 0).toLocaleString()}`;
const STATUS_COLORS = {
  scheduled: 'bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300',
  in_progress: 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300',
  completed: 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300',
  cancelled: 'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300',
};
const STATUSES = ['scheduled', 'in_progress', 'completed', 'cancelled'];
const EMPTY = {
  name: '',
  account_id: '',
  contract_id: '',
  assigned_tech_id: '',
  labor_cost: '',
  material_cost: '',
  billable_hours: '',
  job_date: '',
  notes: '',
};

export default function FieldJobs() {
  const { user } = useAuth();
  const [jobs, setJobs] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [search, setSearch] = useState('');
  const [modal, setModal] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [loading, setLoading] = useState(false);
  const canEdit = ['admin', 'director'].includes(user.role);

  useEffect(() => {
    load();
  }, []);
  const load = async () => {
    const [jr, ar] = await Promise.all([
      api.get(`${API}/api/jobs`),
      api.get(`${API}/api/accounts`),
    ]);
    if (jr.ok) setJobs(unwrap(await jr.json()));
    if (ar.ok) setAccounts(unwrap(await ar.json()));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    const body = {
      ...form,
      labor_cost: parseFloat(form.labor_cost) || 0,
      material_cost: parseFloat(form.material_cost) || 0,
      billable_hours: parseFloat(form.billable_hours) || 0,
    };
    try {
      const url = modal === 'edit' ? `${API}/api/jobs/${form.job_id}` : `${API}/api/jobs`;
      const res = await (modal === 'edit' ? api.put(url, body) : api.post(url, body));
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.detail);
      }
      toast.success('Job saved');
      setModal(null);
      load();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  };

  const filtered = jobs.filter(
    (j) =>
      j.name?.toLowerCase().includes(search.toLowerCase()) ||
      j.account_name?.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div className="space-y-5" data-testid="field-jobs-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="page-title">Field Service Jobs</h1>
          <p className="text-aegisaxiom-text-muted text-xs mt-0.5">{jobs.length} total</p>
        </div>
        {canEdit && (
          <button
            onClick={() => {
              setForm(EMPTY);
              setModal('create');
            }}
            className="btn-primary flex items-center gap-2"
            data-testid="create-job-btn"
          >
            <Plus size={16} />
            New Job
          </button>
        )}
      </div>

      <div className="relative">
        <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted" />
        <input
          className="input-field pl-9"
          placeholder="Search jobs..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {filtered.length === 0 ? (
          <div className="col-span-3 glass-card p-16 text-center text-aegisaxiom-text-muted">
            No field jobs found.
          </div>
        ) : (
          filtered.map((j) => (
            <div
              key={j.job_id}
              className="glass-card p-5 hover:border-violet-500/30 transition-all group"
              data-testid={`job-card-${j.job_id}`}
            >
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-2.5">
                  <div className="w-9 h-9 rounded-lg bg-amber-50 dark:bg-amber-600/10 border border-amber-200 dark:border-amber-600/20 flex items-center justify-center">
                    <Wrench size={16} className="text-amber-600 dark:text-amber-400" />
                  </div>
                  <div>
                    <p className="text-aegisaxiom-text font-medium text-sm">{j.name}</p>
                    <p className="text-aegisaxiom-text-muted text-xs">{j.account_name}</p>
                  </div>
                </div>
                {canEdit && (
                  <button
                    onClick={() => {
                      setForm(j);
                      setModal('edit');
                    }}
                    className="text-aegisaxiom-text-muted hover:text-violet-600 dark:text-violet-400 opacity-0 group-hover:opacity-100 transition-all"
                  >
                    <Edit2 size={13} />
                  </button>
                )}
              </div>
              <div className="grid grid-cols-2 gap-2 text-xs mb-3">
                <div>
                  <p className="text-aegisaxiom-text-muted">Labor</p>
                  <p className="text-aegisaxiom-text-secondary">{fmt(j.labor_cost)}</p>
                </div>
                <div>
                  <p className="text-aegisaxiom-text-muted">Materials</p>
                  <p className="text-aegisaxiom-text-secondary">{fmt(j.material_cost)}</p>
                </div>
                <div>
                  <p className="text-aegisaxiom-text-muted">Total Cost</p>
                  <p className="text-amber-600 dark:text-amber-400 font-semibold">{fmt(j.total_cost)}</p>
                </div>
                <div>
                  <p className="text-aegisaxiom-text-muted">Hours</p>
                  <p className="text-aegisaxiom-text-secondary">{j.billable_hours}h</p>
                </div>
              </div>
              <div className="flex items-center justify-between">
                <span
                  className={`badge-stage ${STATUS_COLORS[j.status] || 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary'}`}
                >
                  {j.status?.replace('_', ' ')}
                </span>
                {j.job_date && <span className="text-aegisaxiom-text-muted text-xs">{j.job_date}</span>}
              </div>
            </div>
          ))
        )}
      </div>

      {modal && (
        <div
          className="fixed inset-0 bg-black/60 z-40 flex items-center justify-center p-4"
          data-testid="job-modal"
        >
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-lg p-6 animate-fadeInUp max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-5">
              <h2
                className="text-lg font-bold text-aegisaxiom-text"
                style={{ fontFamily: 'Manrope, sans-serif' }}
              >
                {modal === 'edit' ? 'Edit Job' : 'New Field Job'}
              </h2>
              <button onClick={() => setModal(null)}>
                <X size={18} className="text-aegisaxiom-text-secondary" />
              </button>
            </div>
            <form onSubmit={handleSubmit} className="space-y-3">
              <div>
                <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Job Name *</label>
                <input
                  className="input-field"
                  value={form.name || ''}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  required
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="col-span-2">
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Account *</label>
                  <select
                    className="input-field"
                    value={form.account_id || ''}
                    onChange={(e) => setForm({ ...form, account_id: e.target.value })}
                    required
                  >
                    <option value="">Select Account</option>
                    {accounts.map((a) => (
                      <option key={a.account_id} value={a.account_id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Labor Cost ($)</label>
                  <input
                    type="number"
                    className="input-field"
                    value={form.labor_cost || ''}
                    onChange={(e) => setForm({ ...form, labor_cost: e.target.value })}
                  />
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Material Cost ($)</label>
                  <input
                    type="number"
                    className="input-field"
                    value={form.material_cost || ''}
                    onChange={(e) => setForm({ ...form, material_cost: e.target.value })}
                  />
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Billable Hours</label>
                  <input
                    type="number"
                    className="input-field"
                    value={form.billable_hours || ''}
                    onChange={(e) => setForm({ ...form, billable_hours: e.target.value })}
                  />
                </div>
                <div>
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Job Date</label>
                  <input
                    type="date"
                    className="input-field"
                    value={form.job_date || ''}
                    onChange={(e) => setForm({ ...form, job_date: e.target.value })}
                  />
                </div>
                {modal === 'edit' && (
                  <div className="col-span-2">
                    <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Status</label>
                    <select
                      className="input-field"
                      value={form.status || 'scheduled'}
                      onChange={(e) => setForm({ ...form, status: e.target.value })}
                    >
                      {STATUSES.map((s) => (
                        <option key={s} value={s}>
                          {s.replace('_', ' ')}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                <div className="col-span-2">
                  <label className="text-xs text-aegisaxiom-text-secondary mb-1 block">Notes</label>
                  <textarea
                    rows={2}
                    className="input-field resize-none"
                    value={form.notes || ''}
                    onChange={(e) => setForm({ ...form, notes: e.target.value })}
                  />
                </div>
              </div>
              <div className="flex gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setModal(null)}
                  className="btn-secondary flex-1"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={loading}
                  className="btn-primary flex-1"
                  data-testid="job-save-btn"
                >
                  {loading ? 'Saving...' : 'Save Job'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

import React, { useEffect, useState, useMemo, useCallback } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import { usePermissions } from '@/hooks/usePermissions';
import {
  Plus,
  Search,
  ChevronLeft,
  ChevronRight,
  Calendar as CalendarIcon,
  Clock,
  MapPin,
  User,
  Phone,
  Mail,
  Wrench,
  X,
  Filter,
  CheckCircle2,
  AlertCircle,
  Edit2,
  Trash2,
  Eye,
  LayoutGrid,
  List,
  Bell,
  Settings,
  Send,
  MessageSquare,
  Activity,
  FileText,
  Link2,
  LogIn,
  LogOut,
  Copy,
  Loader2,
} from 'lucide-react';
import { toast } from 'sonner';
import { api, unwrap } from '../lib/api';
import API_URL from '../lib/apiConfig';
import ConfirmModal from './shared/ConfirmModal';
import {
  format,
  addDays,
  subDays,
  startOfWeek,
  endOfWeek,
  startOfMonth,
  endOfMonth,
  eachDayOfInterval,
  isSameDay,
  isSameMonth,
  isToday,
  parseISO,
  addMonths,
  subMonths,
  addWeeks,
  subWeeks,
  getDay,
} from 'date-fns';

// Import Phase 3 modals
import CheckInModal from './field-services/CheckInModal';
import CheckOutModal from './field-services/CheckOutModal';
import PublicAccessModal from './field-services/PublicAccessModal';
import SurveyFormModal from './field-services/SurveyFormModal';
import SowModal from './field-services/SowModal';

const API = API_URL;
const STATUS_COLORS = {
  scheduled: 'bg-blue-50 dark:bg-blue-500/20 text-blue-700 dark:text-blue-300 border-blue-500/30',
  confirmed: 'bg-cyan-500/20 text-cyan-700 dark:text-cyan-300 border-cyan-500/30',
  in_progress:
    'bg-amber-50 dark:bg-amber-500/20 text-amber-700 dark:text-amber-300 border-amber-500/30',
  completed:
    'bg-emerald-50 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-300 border-emerald-500/30',
  cancelled: 'bg-red-50 dark:bg-red-500/20 text-red-700 dark:text-red-300 border-red-500/30',
};
const STATUS_DOT = {
  scheduled: 'bg-blue-400',
  confirmed: 'bg-cyan-400',
  in_progress: 'bg-amber-400',
  completed: 'bg-emerald-400',
  cancelled: 'bg-red-400',
};
const PRIORITY_COLORS = {
  low: 'text-aegisaxiom-text-secondary',
  medium: 'text-blue-600 dark:text-blue-400',
  high: 'text-orange-600 dark:text-orange-400',
  urgent: 'text-red-600 dark:text-red-400',
};
const STATUSES = ['scheduled', 'confirmed', 'in_progress', 'completed', 'cancelled'];
const PRIORITIES = ['low', 'medium', 'high', 'urgent'];
const WEEKDAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const WEEKDAY_KEYS = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];
const HOURS = Array.from({ length: 13 }, (_, i) => i + 6); // 6AM to 6PM

const EMPTY_JOB = {
  name: '',
  account_id: '',
  contract_id: '',
  deal_id: '',
  priority: 'medium',
  job_category: 'execution', // survey or execution
  linked_survey_job_id: '', // Link to survey job
  job_type: '',
  assigned_tech_id: '',
  assigned_pm_id: '',
  customer_contact_name: '',
  customer_contact_email: '',
  customer_contact_phone: '',
  scheduled_date: '',
  scheduled_start: '',
  scheduled_end: '',
  estimated_hours: '',
  labor_cost: '',
  material_cost: '',
  billable_hours: '',
  description: '',
  notes: '',
  schedule_override: false,
  override_reason: '',
};

// ---- MINI COMPONENTS ----
const StatusBadge = ({ status }) => (
  <span
    className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${STATUS_COLORS[status] || 'bg-aegisaxiom-elevated text-aegisaxiom-text-secondary border-aegisaxiom-border'}`}
  >
    {(status || '').replace(/_/g, ' ')}
  </span>
);

const PriorityDot = ({ priority }) => (
  <span
    className={`text-[10px] font-medium ${PRIORITY_COLORS[priority] || 'text-aegisaxiom-text-secondary'}`}
  >
    {priority === 'urgent'
      ? '●●● '
      : priority === 'high'
        ? '●● '
        : priority === 'medium'
          ? '● '
          : '○ '}
    {priority}
  </span>
);

const StatCard = ({ label, value, color = 'text-aegisaxiom-text' }) => (
  <div className="glass-card px-4 py-3">
    <p className="text-aegisaxiom-text-muted text-[10px] uppercase tracking-wider">{label}</p>
    <p className={`text-xl font-bold ${color}`}>{value}</p>
  </div>
);

// ---- MAIN COMPONENT ----
export default function FieldServices() {
  const { user } = useAuth();
  const { hasAnyPermission, hasPermission } = usePermissions();
  const [view, setView] = useState('week'); // day, week, month, list
  const [currentDate, setCurrentDate] = useState(new Date());
  const [jobs, setJobs] = useState([]);
  const [stats, setStats] = useState({});
  const [accounts, setAccounts] = useState([]);
  const [pms, setPms] = useState([]);
  const [contractors, setContractors] = useState([]);
  const [contracts, setContracts] = useState([]);
  const [jobTypes, setJobTypes] = useState([]);
  const [filterPm, setFilterPm] = useState('');
  const [filterAccount, setFilterAccount] = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  const [search, setSearch] = useState('');
  const [testOnly, setTestOnly] = useState(false); // Test Data Only filter
  const [showUnscheduled, setShowUnscheduled] = useState(false); // Unscheduled jobs view
  const [modal, setModal] = useState(null); // 'create' | 'edit' | 'detail' | 'hours'
  const [phase3Modal, setPhase3Modal] = useState(null); // 'check-in' | 'check-out' | 'public-link' | 'survey-form' | 'sow'
  const [form, setForm] = useState(EMPTY_JOB);
  const [selectedJob, setSelectedJob] = useState(null);
  const [loading, setLoading] = useState(false);
  const [operatingHours, setOperatingHours] = useState(null);
  const [scheduleWarnings, setScheduleWarnings] = useState(null);
  const [surveyJobs, setSurveyJobs] = useState([]); // For linking execution jobs to surveys
  const canEdit = hasAnyPermission(['field_jobs.edit', 'field_jobs.edit.own', 'field_jobs.create']);
  const [confirmState, setConfirmState] = useState({
    open: false,
    action: null,
    title: '',
    message: '',
  });

  // ---- DATE RANGE ----
  const dateRange = useMemo(() => {
    if (view === 'day') return { start: currentDate, end: currentDate };
    if (view === 'week') return { start: startOfWeek(currentDate), end: endOfWeek(currentDate) };
    if (view === 'month') {
      const ms = startOfMonth(currentDate);
      const me = endOfMonth(currentDate);
      return { start: startOfWeek(ms), end: endOfWeek(me) };
    }
    // list: show 30 days
    return { start: currentDate, end: addDays(currentDate, 30) };
  }, [view, currentDate]);

  // ---- DATA LOADING ----
  const loadJobs = useCallback(async () => {
    try {
      const params = new URLSearchParams({
        start: format(dateRange.start, 'yyyy-MM-dd'),
        end: format(dateRange.end, 'yyyy-MM-dd'),
      });
      if (filterPm) params.set('pm_id', filterPm);
      if (filterAccount) params.set('account_id', filterAccount);
      if (testOnly) params.set('test_only', 'true'); // Test Data Only filter
      const r = await api.get(`${API}/api/field-services/calendar?${params}`);
      if (r.ok) {
        const data = await r.json();
        setJobs(data.jobs || []);
        if (data.operating_hours) setOperatingHours(data.operating_hours);
      }
    } catch (err) {
      console.error('Load jobs error:', err);
    }
  }, [dateRange, filterPm, filterAccount, testOnly]);

  const loadMeta = useCallback(async () => {
    try {
      const [sr, ar, pr, cr, str, jtr, svr] = await Promise.all([
        api.get(`${API}/api/field-services/stats`),
        api.get(`${API}/api/accounts`),
        api.get(`${API}/api/field-services/pms`),
        api.get(`${API}/api/field-services/contractors`),
        api.get(`${API}/api/contracts?limit=500`),
        api.get(`${API}/api/field-services/job-types`),
        api.get(`${API}/api/v2/field-services/surveys?limit=100`),
      ]);
      if (sr.ok) setStats(await sr.json());
      if (ar.ok) {
        const ad = await ar.json();
        setAccounts(Array.isArray(ad) ? ad : ad.items || []);
      }
      if (pr.ok) {
        const pd = await pr.json();
        setPms(pd.available_pms || []);
      }
      if (cr.ok) {
        const cd = await cr.json();
        setContractors(cd.items || []);
      }
      if (str.ok) {
        const sd = await str.json();
        setContracts(Array.isArray(sd) ? sd : sd.items || []);
      }
      if (jtr.ok) {
        const jtd = await jtr.json();
        setJobTypes(jtd.items || []);
      }
      if (svr.ok) {
        const svd = await svr.json();
        setSurveyJobs(svd.items || []);
      }
    } catch (err) {
      console.error('Load meta error:', err);
    }
  }, []);

  useEffect(() => {
    loadMeta();
  }, [loadMeta]);
  useEffect(() => {
    loadJobs();
  }, [loadJobs]);

  // ---- NAVIGATION ----
  const navigate = (dir) => {
    const d = dir === 'next' ? 1 : -1;
    if (view === 'day') setCurrentDate(addDays(currentDate, d));
    else if (view === 'week') setCurrentDate(addWeeks(currentDate, d));
    else if (view === 'month') setCurrentDate(addMonths(currentDate, d));
    else setCurrentDate(addDays(currentDate, d * 30));
  };

  // ---- FORM HANDLERS ----
  const openCreate = (date) => {
    setForm({ ...EMPTY_JOB, scheduled_date: date ? format(date, 'yyyy-MM-dd') : '' });
    setScheduleWarnings(null);
    setModal('create');
  };

  const openEdit = (job) => {
    setForm({ ...EMPTY_JOB, ...job });
    setScheduleWarnings(null);
    setModal('edit');
  };

  const openDetail = async (job) => {
    setSelectedJob(job);
    setModal('detail');
    // Fetch full job detail with activity
    try {
      const r = await api.get(`${API}/api/field-services/jobs/${job.job_id}`);
      if (r.ok) {
        const full = await r.json();
        setSelectedJob(full);
      }
    } catch (err) {
      console.error('Fetch detail error:', err);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    const body = {
      ...form,
      estimated_hours: parseFloat(form.estimated_hours) || 0,
      labor_cost: parseFloat(form.labor_cost) || 0,
      material_cost: parseFloat(form.material_cost) || 0,
      billable_hours: parseFloat(form.billable_hours) || 0,
    };
    try {
      const url =
        modal === 'edit'
          ? `${API}/api/field-services/jobs/${form.job_id}`
          : `${API}/api/field-services/jobs`;
      const res = modal === 'edit' ? await api.put(url, body) : await api.post(url, body);
      if (!res.ok) {
        const err = await res.json();
        if (err.detail?.requires_override) {
          setScheduleWarnings(err.detail);
          toast.error('Schedule conflict — see warnings below');
          setLoading(false);
          return;
        }
        throw new Error(
          typeof err.detail === 'string' ? err.detail : err.detail?.message || 'Save failed',
        );
      }
      toast.success('Job saved');
      setModal(null);
      loadJobs();
      loadMeta();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = (jobId) => {
    setConfirmState({
      open: true,
      title: 'Delete Job',
      message: 'Delete this job? This action cannot be undone.',
      action: async () => {
        try {
          const res = await api.delete(`${API}/api/field-services/jobs/${jobId}`);
          if (!res.ok) throw new Error('Delete failed');
          toast.success('Job deleted');
          setModal(null);
          loadJobs();
          loadMeta();
        } catch (err) {
          toast.error(err.message);
        }
      },
    });
  };

  const handleStatusChange = async (jobId, newStatus) => {
    try {
      const res = await api.put(`${API}/api/field-services/jobs/${jobId}`, { status: newStatus });
      if (!res.ok) throw new Error('Status update failed');
      toast.success(`Status → ${newStatus.replace(/_/g, ' ')}`);
      loadJobs();
      loadMeta();
    } catch (err) {
      toast.error(err.message);
    }
  };

  // ---- FILTERED JOBS ----
  const filteredJobs = useMemo(() => {
    let result = jobs;
    if (filterStatus) result = result.filter((j) => j.status === filterStatus);
    if (search) {
      const s = search.toLowerCase();
      result = result.filter(
        (j) =>
          j.name?.toLowerCase().includes(s) ||
          j.account_name?.toLowerCase().includes(s) ||
          j.assigned_tech_name?.toLowerCase().includes(s) ||
          j.assigned_pm_name?.toLowerCase().includes(s),
      );
    }
    // Filter unscheduled jobs
    if (showUnscheduled) {
      result = result.filter((j) => !j.scheduled_date || j.scheduled_date === '');
    }
    return result;
  }, [jobs, filterStatus, search, showUnscheduled]);

  // Group jobs by date for calendar
  const jobsByDate = useMemo(() => {
    const map = {};
    filteredJobs.forEach((j) => {
      const d = j.scheduled_date || j.job_date;
      if (!d) return;
      if (!map[d]) map[d] = [];
      map[d].push(j);
    });
    return map;
  }, [filteredJobs]);

  // ---- CALENDAR VIEWS ----
  const calendarDays = useMemo(() => {
    if (view === 'day') return [currentDate];
    return eachDayOfInterval({ start: dateRange.start, end: dateRange.end });
  }, [view, currentDate, dateRange]);

  const accountName = filterAccount
    ? accounts.find((a) => a.account_id === filterAccount)?.name
    : null;

  // Get operating hours for a day
  const getHoursForDay = (date) => {
    if (!operatingHours) return null;
    const key = WEEKDAY_KEYS[getDay(date)];
    return operatingHours[key];
  };

  return (
    <div data-testid="field-services-page">
      {/* HEADER */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-6">
        <div>
          <h1 className="page-title flex items-center gap-2">
            <Wrench size={20} className="text-violet-600 dark:text-violet-400" />
            Field Services
          </h1>
          <p className="text-aegisaxiom-text-muted text-xs mt-0.5">
            {stats.total || 0} jobs · {stats.this_week || 0} this week
            {accountName && (
              <span className="text-violet-600 dark:text-violet-400 ml-2">
                Viewing: {accountName}
              </span>
            )}
          </p>
        </div>
        <div className="flex flex-col sm:flex-row gap-2 w-full sm:w-auto">
          {canEdit && (
            <button
              onClick={() => setModal('notif_settings')}
              className="btn-secondary flex items-center justify-center gap-1.5 text-sm w-full sm:w-auto"
            >
              <Bell size={14} /> Notifications
            </button>
          )}
          {canEdit && (
            <button
              onClick={() => openCreate()}
              className="btn-primary flex items-center justify-center gap-1.5 text-sm w-full sm:w-auto"
              data-testid="create-job-btn"
            >
              <Plus size={15} /> New Job
            </button>
          )}
        </div>
      </div>

      {/* STATS ROW */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-2 mb-6">
        <StatCard
          label="Scheduled"
          value={stats.scheduled || 0}
          color="text-blue-600 dark:text-blue-400"
        />
        <StatCard
          label="Confirmed"
          value={stats.confirmed || 0}
          color="text-cyan-600 dark:text-cyan-400"
        />
        <StatCard
          label="In Progress"
          value={stats.in_progress || 0}
          color="text-amber-600 dark:text-amber-400"
        />
        <StatCard
          label="Completed"
          value={stats.completed || 0}
          color="text-emerald-600 dark:text-emerald-400"
        />
        <StatCard
          label="This Week"
          value={stats.this_week || 0}
          color="text-violet-600 dark:text-violet-400"
        />
        <StatCard
          label="Cancelled"
          value={stats.cancelled || 0}
          color="text-red-600 dark:text-red-400"
        />
      </div>

      {/* CONTROLS BAR */}
      <div className="glass-card p-3 mb-4">
        <div className="flex flex-col lg:flex-row lg:items-center gap-3">
          {/* Row 1: View toggle + Date Nav (always together at the top) */}
          <div className="flex flex-wrap items-center gap-3">
            {/* View Toggle — horizontally scrollable safety */}
            <div className="flex bg-aegisaxiom-elevated rounded-lg p-0.5 shrink-0">
              {['day', 'week', 'month', 'list'].map((v) => (
                <button
                  key={v}
                  onClick={() => setView(v)}
                  className={`px-3 py-1 text-xs font-medium rounded-md transition-all whitespace-nowrap ${view === v ? 'bg-violet-600 text-white' : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'}`}
                  data-testid={`view-${v}`}
                >
                  {v === 'list' ? 'List' : v.charAt(0).toUpperCase() + v.slice(1)}
                </button>
              ))}
            </div>

            {/* Date Nav */}
            <div className="flex items-center gap-1 flex-wrap">
              <button
                onClick={() => navigate('prev')}
                className="p-1.5 hover:bg-aegisaxiom-elevated rounded-lg transition-colors"
                aria-label="Previous"
              >
                <ChevronLeft size={16} className="text-aegisaxiom-text-secondary" />
              </button>
              <button
                onClick={() => setCurrentDate(new Date())}
                className="px-3 py-1 text-xs font-medium text-aegisaxiom-text-secondary hover:bg-aegisaxiom-elevated rounded-lg transition-colors"
              >
                Today
              </button>
              <button
                onClick={() => navigate('next')}
                className="p-1.5 hover:bg-aegisaxiom-elevated rounded-lg transition-colors"
                aria-label="Next"
              >
                <ChevronRight size={16} className="text-aegisaxiom-text-secondary" />
              </button>
              <span className="text-sm font-medium text-aegisaxiom-text ml-2">
                {view === 'day'
                  ? format(currentDate, 'EEEE, MMM d, yyyy')
                  : view === 'week'
                    ? `${format(dateRange.start, 'MMM d')} – ${format(dateRange.end, 'MMM d, yyyy')}`
                    : format(currentDate, 'MMMM yyyy')}
              </span>
            </div>
          </div>

          {/* Row 2: Filters (right-aligned on desktop, full-width stack on mobile) */}
          <div className="flex flex-wrap items-center gap-2 lg:ml-auto">
            <select
              value={filterPm}
              onChange={(e) => setFilterPm(e.target.value)}
              className="input-field !w-full sm:!w-40 !py-1 text-xs"
            >
              <option value="">All PMs</option>
              {pms.map((p) => (
                <option key={p.user_id} value={p.user_id}>
                  {p.name}
                </option>
              ))}
            </select>
            <select
              value={filterAccount}
              onChange={(e) => {
                setFilterAccount(e.target.value);
                setOperatingHours(null);
              }}
              className="input-field !w-full sm:!w-44 !py-1 text-xs"
            >
              <option value="">All Accounts</option>
              {accounts.map((a) => (
                <option key={a.account_id} value={a.account_id}>
                  {a.name}
                </option>
              ))}
            </select>
            <select
              value={filterStatus}
              onChange={(e) => setFilterStatus(e.target.value)}
              className="input-field !w-full sm:!w-32 !py-1 text-xs"
            >
              <option value="">All Status</option>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s.replace(/_/g, ' ')}
                </option>
              ))}
            </select>

            {/* Unscheduled Jobs Toggle */}
            <button
              onClick={() => setShowUnscheduled(!showUnscheduled)}
              className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-all whitespace-nowrap ${
                showUnscheduled
                  ? 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-600/30'
                  : 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary border border-aegisaxiom-border hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated'
              }`}
            >
              Unscheduled {showUnscheduled && `(${filteredJobs.length})`}
            </button>

            {/* Test Data Only Toggle (Admin only) */}
            {canEdit && (
              <button
                onClick={() => setTestOnly(!testOnly)}
                className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-all whitespace-nowrap ${
                  testOnly
                    ? 'bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border border-violet-200 dark:border-violet-600/30'
                    : 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary border border-aegisaxiom-border hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated'
                }`}
              >
                Test Data Only
              </button>
            )}

            <div className="relative w-full sm:w-auto">
              <Search
                size={13}
                className="absolute left-2.5 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted"
              />
              <input
                className="input-field !pl-8 !w-full sm:!w-36 !py-1 text-xs"
                placeholder="Search..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          </div>
        </div>
      </div>

      {/* CALENDAR / LIST VIEW */}
      <div>
        {view === 'list' ? (
          <ListView
            jobs={filteredJobs}
            canEdit={canEdit}
            onEdit={openEdit}
            onDetail={openDetail}
            onStatusChange={handleStatusChange}
            onDelete={handleDelete}
          />
        ) : view === 'month' ? (
          <MonthView
            days={calendarDays}
            jobsByDate={jobsByDate}
            currentDate={currentDate}
            onDayClick={(d) => {
              setCurrentDate(d);
              setView('day');
            }}
            onJobClick={openDetail}
            onCreateClick={openCreate}
            canEdit={canEdit}
            getHoursForDay={getHoursForDay}
          />
        ) : view === 'week' ? (
          <WeekView
            days={calendarDays.slice(0, 7)}
            jobsByDate={jobsByDate}
            onJobClick={openDetail}
            onCreateClick={openCreate}
            canEdit={canEdit}
            getHoursForDay={getHoursForDay}
          />
        ) : (
          <DayView
            date={currentDate}
            jobs={jobsByDate[format(currentDate, 'yyyy-MM-dd')] || []}
            onJobClick={openDetail}
            onCreateClick={openCreate}
            canEdit={canEdit}
            dayHours={getHoursForDay(currentDate)}
          />
        )}
      </div>

      {/* MODALS */}
      {(modal === 'create' || modal === 'edit') && (
        <JobFormModal
          modal={modal}
          form={form}
          setForm={setForm}
          loading={loading}
          accounts={accounts}
          pms={pms}
          contractors={contractors}
          contracts={contracts}
          jobTypes={jobTypes}
          surveyJobs={surveyJobs}
          scheduleWarnings={scheduleWarnings}
          onSubmit={handleSubmit}
          onClose={() => setModal(null)}
        />
      )}

      {modal === 'detail' && selectedJob && (
        <JobDetailModal
          job={selectedJob}
          canEdit={canEdit}
          onEdit={() => {
            openEdit(selectedJob);
          }}
          onDelete={() => handleDelete(selectedJob.job_id)}
          onStatusChange={(s) => {
            handleStatusChange(selectedJob.job_id, s);
            setModal(null);
          }}
          onPhase3Action={(action) => {
            setPhase3Modal(action);
            setModal(null);
          }}
          onClose={() => setModal(null)}
        />
      )}

      {modal === 'notif_settings' && <NotificationSettingsModal onClose={() => setModal(null)} />}

      {/* Phase 3 Modals */}
      {phase3Modal === 'check-in' && selectedJob && (
        <CheckInModal
          job={selectedJob}
          onClose={() => setPhase3Modal(null)}
          onSuccess={() => {
            loadJobs();
            setSelectedJob(null);
            setPhase3Modal(null);
          }}
        />
      )}

      {phase3Modal === 'check-out' && selectedJob && (
        <CheckOutModal
          job={selectedJob}
          onClose={() => setPhase3Modal(null)}
          onSuccess={() => {
            loadJobs();
            setSelectedJob(null);
            setPhase3Modal(null);
          }}
        />
      )}

      {phase3Modal === 'public-link' && selectedJob && (
        <PublicAccessModal
          job={selectedJob}
          onClose={() => setPhase3Modal(null)}
          onSuccess={() => {
            loadJobs();
          }}
        />
      )}

      {phase3Modal === 'survey-form' && selectedJob && (
        <SurveyFormModal
          job={selectedJob}
          onClose={() => setPhase3Modal(null)}
          onSuccess={() => {
            loadJobs();
            loadMeta();
            setSelectedJob(null);
            setPhase3Modal(null);
          }}
        />
      )}

      {phase3Modal === 'sow' && selectedJob && (
        <SowModal
          job={selectedJob}
          onClose={() => setPhase3Modal(null)}
          onSuccess={() => {
            loadJobs();
          }}
        />
      )}
      <ConfirmModal
        isOpen={confirmState.open}
        title={confirmState.title}
        message={confirmState.message}
        confirmText="Delete"
        onConfirm={() => confirmState.action?.()}
        onCancel={() => setConfirmState({ open: false, action: null, title: '', message: '' })}
      />
    </div>
  );
}

// ---- MONTH VIEW ----
function MonthView({
  days,
  jobsByDate,
  currentDate,
  onDayClick,
  onJobClick,
  onCreateClick,
  canEdit,
  getHoursForDay,
}) {
  return (
    <div className="glass-card overflow-hidden">
      {/* Horizontal scroll on mobile — 7-col month grid is unreadable below ~700px */}
      <div className="overflow-x-auto">
        <div className="min-w-[700px]">
          <div className="grid grid-cols-7 border-b border-aegisaxiom-border">
            {WEEKDAY_NAMES.map((d) => (
              <div
                key={d}
                className="px-2 py-2 text-center text-[10px] font-semibold text-aegisaxiom-text-muted uppercase tracking-wider"
              >
                {d}
              </div>
            ))}
          </div>
          <div className="grid grid-cols-7">
            {days.map((day, i) => {
              const key = format(day, 'yyyy-MM-dd');
              const dayJobs = jobsByDate[key] || [];
              const inMonth = isSameMonth(day, currentDate);
              const today = isToday(day);
              const hours = getHoursForDay(day);
              const isClosed = hours?.closed;

              return (
                <div
                  key={i}
                  onClick={() => onDayClick(day)}
                  className={`min-h-[90px] border-b border-r border-aegisaxiom-border p-1.5 cursor-pointer transition-colors
                    ${!inMonth ? 'bg-aegisaxiom-surface' : 'hover:bg-aegisaxiom-accent-muted'}
                    ${isClosed ? 'bg-red-950/20' : ''}`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span
                      className={`text-xs font-medium ${today ? 'bg-violet-600 text-white w-6 h-6 rounded-full flex items-center justify-center' : inMonth ? 'text-aegisaxiom-text-secondary' : 'text-aegisaxiom-text-muted'}`}
                    >
                      {format(day, 'd')}
                    </span>
                    {isClosed && (
                      <span className="text-[8px] text-red-600 dark:text-red-400/60">CLOSED</span>
                    )}
                    {hours && !isClosed && (
                      <span className="text-[8px] text-aegisaxiom-text-muted">
                        {hours.open}-{hours.close}
                      </span>
                    )}
                  </div>
                  <div className="space-y-0.5">
                    {dayJobs.slice(0, 3).map((j) => (
                      <div
                        key={j.job_id}
                        onClick={(e) => {
                          e.stopPropagation();
                          onJobClick(j);
                        }}
                        className={`px-1.5 py-0.5 rounded text-[10px] truncate cursor-pointer hover:brightness-125 transition-all ${STATUS_COLORS[j.status]?.split(' ')[0] || 'bg-aegisaxiom-elevated'}`}
                      >
                        {j.scheduled_start && (
                          <span className="font-semibold mr-1">{j.scheduled_start}</span>
                        )}
                        {j.name}
                      </div>
                    ))}
                    {dayJobs.length > 3 && (
                      <div className="text-[9px] text-aegisaxiom-text-muted pl-1">
                        +{dayJobs.length - 3} more
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---- WEEK VIEW ----
function WeekView({ days, jobsByDate, onJobClick, onCreateClick, canEdit, getHoursForDay }) {
  return (
    <div className="glass-card overflow-hidden">
      {/* Horizontal scroll on mobile — 7-col week grid is unreadable below ~700px */}
      <div className="overflow-x-auto">
        <div className="grid grid-cols-7 divide-x divide-aegisaxiom-border min-w-[700px]">
          {days.map((day, i) => {
            const key = format(day, 'yyyy-MM-dd');
            const dayJobs = jobsByDate[key] || [];
            const today = isToday(day);
            const hours = getHoursForDay(day);
            const isClosed = hours?.closed;

            return (
              <div key={i} className={`min-h-[400px] ${isClosed ? 'bg-red-950/10' : ''}`}>
                {/* Day Header */}
                <div
                  className={`px-3 py-2 border-b border-aegisaxiom-border ${today ? 'bg-violet-50 dark:bg-violet-600/10' : ''}`}
                >
                  <div className="text-center">
                    <p className="text-[10px] uppercase tracking-wider text-aegisaxiom-text-muted">
                      {format(day, 'EEE')}
                    </p>
                    <p
                      className={`text-lg font-bold ${today ? 'text-violet-600 dark:text-violet-400' : 'text-aegisaxiom-text'}`}
                    >
                      {format(day, 'd')}
                    </p>
                    {hours && !isClosed && (
                      <p className="text-[9px] text-aegisaxiom-text-muted">
                        {hours.open} – {hours.close}
                      </p>
                    )}
                    {isClosed && (
                      <p className="text-[9px] text-red-600 dark:text-red-400">CLOSED</p>
                    )}
                  </div>
                </div>

                {/* Jobs */}
                <div className="p-1.5 space-y-1.5">
                  {dayJobs.map((j) => (
                    <div
                      key={j.job_id}
                      onClick={() => onJobClick(j)}
                      className={`p-2 rounded-lg border cursor-pointer hover:brightness-110 transition-all ${STATUS_COLORS[j.status] || 'bg-aegisaxiom-elevated border-aegisaxiom-border'}`}
                    >
                      <p className="text-[11px] font-semibold text-aegisaxiom-text truncate">{j.name}</p>
                      <p className="text-[10px] text-aegisaxiom-text-secondary truncate">
                        {j.account_name}
                      </p>
                      {j.scheduled_start && (
                        <div className="flex items-center gap-1 mt-1">
                          <Clock size={9} className="text-aegisaxiom-text-muted" />
                          <span className="text-[9px] text-aegisaxiom-text-secondary">
                            {j.scheduled_start}
                            {j.scheduled_end ? ` – ${j.scheduled_end}` : ''}
                          </span>
                        </div>
                      )}
                      {j.assigned_tech_name && (
                        <div className="flex items-center gap-1 mt-0.5">
                          <User size={9} className="text-aegisaxiom-text-muted" />
                          <span className="text-[9px] text-aegisaxiom-text-secondary">
                            {j.assigned_tech_name}
                          </span>
                        </div>
                      )}
                      {j.estimated_hours > 0 && (
                        <span className="text-[9px] text-aegisaxiom-text-muted mt-0.5 block">
                          {j.estimated_hours}h est.
                        </span>
                      )}
                    </div>
                  ))}
                  {canEdit && !isClosed && (
                    <button
                      onClick={() => onCreateClick(day)}
                      className="w-full p-1.5 border border-dashed border-aegisaxiom-border rounded-lg text-aegisaxiom-text-muted hover:text-violet-600 dark:hover:text-violet-400 hover:border-violet-500/30 transition-all text-[10px]"
                    >
                      + Add
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ---- DAY VIEW ----
function DayView({ date, jobs, onJobClick, onCreateClick, canEdit, dayHours }) {
  const isClosed = dayHours?.closed;

  return (
    <div className="glass-card p-4">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
        <div>
          <h3 className="text-lg font-bold text-aegisaxiom-text">{format(date, 'EEEE, MMMM d')}</h3>
          {dayHours && !isClosed && (
            <p className="text-xs text-aegisaxiom-text-secondary">
              Customer hours: {dayHours.open} – {dayHours.close}
            </p>
          )}
          {isClosed && (
            <p className="text-xs text-red-600 dark:text-red-400">Customer is CLOSED this day</p>
          )}
        </div>
        {canEdit && !isClosed && (
          <button
            onClick={() => onCreateClick(date)}
            className="btn-primary flex items-center justify-center gap-1.5 text-sm w-full sm:w-auto"
          >
            <Plus size={14} /> Schedule Job
          </button>
        )}
      </div>

      {jobs.length === 0 ? (
        <div className="text-center py-12 text-aegisaxiom-text-muted">
          <CalendarIcon size={32} className="mx-auto mb-2 opacity-50" />
          <p className="text-sm">No jobs scheduled</p>
        </div>
      ) : (
        <div className="space-y-2">
          {jobs.map((j) => (
            <div
              key={j.job_id}
              onClick={() => onJobClick(j)}
              className="flex items-center gap-4 p-3 rounded-lg bg-aegisaxiom-elevated border border-aegisaxiom-border cursor-pointer hover:border-violet-500/30 transition-all"
            >
              <div
                className={`w-1 h-12 rounded-full ${STATUS_DOT[j.status] || 'bg-aegisaxiom-elevated'}`}
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <p className="text-sm font-semibold text-aegisaxiom-text truncate">{j.name}</p>
                  <StatusBadge status={j.status} />
                </div>
                <p className="text-xs text-aegisaxiom-text-secondary">{j.account_name}</p>
                <div className="flex items-center gap-3 mt-1 text-[10px] text-aegisaxiom-text-muted">
                  {j.scheduled_start && (
                    <span>
                      <Clock size={10} className="inline mr-0.5" />
                      {j.scheduled_start}
                      {j.scheduled_end ? ` – ${j.scheduled_end}` : ''}
                    </span>
                  )}
                  {j.assigned_tech_name && (
                    <span>
                      <User size={10} className="inline mr-0.5" />
                      {j.assigned_tech_name}
                    </span>
                  )}
                  {j.estimated_hours > 0 && <span>{j.estimated_hours}h est.</span>}
                  {j.assigned_pm_name && <span>PM: {j.assigned_pm_name}</span>}
                </div>
              </div>
              <PriorityDot priority={j.priority} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- LIST VIEW ----
function ListView({ jobs, canEdit, onEdit, onDetail, onStatusChange, onDelete }) {
  return (
    <div className="glass-card overflow-hidden">
      {/* Horizontal scroll on mobile — 9-10 column table doesn't fit below ~900px */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs min-w-[900px]">
          <thead>
            <tr className="border-b border-aegisaxiom-border text-aegisaxiom-text-muted uppercase tracking-wider text-[10px]">
              <th className="text-left px-3 py-2">Job</th>
              <th className="text-left px-3 py-2">Account</th>
              <th className="text-left px-3 py-2">Date</th>
              <th className="text-left px-3 py-2">Time</th>
              <th className="text-left px-3 py-2">Tech</th>
              <th className="text-left px-3 py-2">PM</th>
              <th className="text-left px-3 py-2">Hours</th>
              <th className="text-left px-3 py-2">Status</th>
              <th className="text-left px-3 py-2">Priority</th>
              {canEdit && <th className="text-right px-3 py-2">Actions</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-aegisaxiom-border/50">
            {jobs.length === 0 ? (
              <tr>
                <td
                  colSpan={canEdit ? 10 : 9}
                  className="px-3 py-12 text-center text-aegisaxiom-text-muted"
                >
                  No jobs found
                </td>
              </tr>
            ) : (
              jobs.map((j) => (
                <tr
                  key={j.job_id}
                  className="hover:bg-aegisaxiom-accent-muted transition-colors cursor-pointer"
                  onClick={() => onDetail(j)}
                >
                  <td className="px-3 py-2.5">
                    <p className="text-aegisaxiom-text font-medium">{j.name}</p>
                    {j.public_access_token && (
                      <span className="inline-flex items-center gap-1 text-[9px] text-emerald-600 dark:text-emerald-400 mt-0.5">
                        <Link2 size={8} />
                        Public link active
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-aegisaxiom-text-secondary">{j.account_name}</td>
                  <td className="px-3 py-2.5 text-aegisaxiom-text-secondary">
                    {j.scheduled_date || j.job_date || '—'}
                  </td>
                  <td className="px-3 py-2.5 text-aegisaxiom-text-secondary">
                    {j.scheduled_start
                      ? `${j.scheduled_start}${j.scheduled_end ? ` – ${j.scheduled_end}` : ''}`
                      : '—'}
                  </td>
                  <td className="px-3 py-2.5 text-aegisaxiom-text-secondary">
                    {j.assigned_tech_name || '—'}
                  </td>
                  <td className="px-3 py-2.5 text-aegisaxiom-text-secondary">
                    {j.assigned_pm_name || '—'}
                  </td>
                  <td className="px-3 py-2.5 text-aegisaxiom-text-secondary">
                    {j.estimated_hours || j.billable_hours || '—'}h
                  </td>
                  <td className="px-3 py-2.5">
                    <StatusBadge status={j.status} />
                  </td>
                  <td className="px-3 py-2.5">
                    <PriorityDot priority={j.priority} />
                  </td>
                  {canEdit && (
                    <td className="px-3 py-2.5 text-right">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onEdit(j);
                        }}
                        className="text-aegisaxiom-text-muted hover:text-violet-600 dark:hover:text-violet-400 hover:bg-violet-50 dark:hover:bg-violet-600/10 p-1 rounded transition"
                        aria-label={`Edit ${j.name}`}
                      >
                        <Edit2 size={13} />
                      </button>
                    </td>
                  )}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---- JOB FORM MODAL ----
function JobFormModal({
  modal,
  form,
  setForm,
  loading,
  accounts,
  pms,
  contractors,
  contracts,
  jobTypes,
  surveyJobs,
  scheduleWarnings,
  onSubmit,
  onClose,
}) {
  const f = (field, val) => setForm((prev) => ({ ...prev, [field]: val }));

  // Filter contracts to selected account
  const accountContracts = contracts.filter((c) => c.account_id === form.account_id);
  const selectedType = jobTypes.find((jt) => jt.type_id === form.job_type);

  return (
    <div
      className="fixed inset-0 bg-black/60 z-40 flex items-center justify-center p-2 sm:p-4"
      data-testid="job-modal"
    >
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-2xl p-4 sm:p-5 animate-fadeInUp max-h-[95vh] sm:max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4 gap-3">
          <h2 className="text-base sm:text-lg font-bold text-aegisaxiom-text">
            {modal === 'edit' ? 'Edit Job' : 'New Field Service Job'}
          </h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="p-1 -m-1 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text rounded transition shrink-0"
          >
            <X size={18} />
          </button>
        </div>

        {/* Schedule Warnings */}
        {scheduleWarnings && (
          <div className="mb-4 p-3 rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-500/30">
            <div className="flex items-center gap-2 mb-2">
              <AlertCircle size={14} className="text-red-600 dark:text-red-400" />
              <p className="text-sm font-semibold text-red-700 dark:text-red-300">
                Scheduling Conflict
              </p>
            </div>
            <ul className="text-xs text-red-700 dark:text-red-300/80 space-y-1 ml-5 list-disc">
              {scheduleWarnings.violations?.map((v, i) => (
                <li key={i}>{v}</li>
              ))}
            </ul>
            <label className="flex items-center gap-2 mt-3 text-xs text-aegisaxiom-text-secondary cursor-pointer">
              <input
                type="checkbox"
                checked={form.schedule_override}
                onChange={(e) => f('schedule_override', e.target.checked)}
                className="rounded border-aegisaxiom-border-hover bg-aegisaxiom-elevated"
              />
              Override scheduling rules
            </label>
            {form.schedule_override && (
              <input
                className="input-field mt-2 text-xs"
                placeholder="Override reason (required)"
                value={form.override_reason || ''}
                onChange={(e) => f('override_reason', e.target.value)}
              />
            )}
          </div>
        )}

        <form onSubmit={onSubmit} className="space-y-3">
          {/* Job Category & Survey Link (TOP SECTION) */}
          <div className="p-3 rounded-lg bg-aegisaxiom-accent-muted border border-aegisaxiom-border">
            <div className="grid grid-cols-2 gap-3 mb-3">
              <div>
                <label className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-1 block">
                  Job Category *
                </label>
                <select
                  className="input-field"
                  value={form.job_category || 'execution'}
                  onChange={(e) => {
                    f('job_category', e.target.value);
                    if (e.target.value === 'survey') f('linked_survey_job_id', '');
                  }}
                >
                  <option value="execution">Execution Job</option>
                  <option value="survey">Survey Job</option>
                </select>
              </div>

              {form.job_category === 'execution' && surveyJobs.length > 0 && (
                <div>
                  <label className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-1 block">
                    Link Survey Job
                  </label>
                  <select
                    className="input-field"
                    value={form.linked_survey_job_id || ''}
                    onChange={async (e) => {
                      const surveyJobId = e.target.value;
                      f('linked_survey_job_id', surveyJobId);

                      if (surveyJobId) {
                        try {
                          const response = await fetch(
                            `${process.env.REACT_APP_BACKEND_URL}/api/v2/field-services/jobs/${surveyJobId}/sow`,
                            {
                              headers: {
                                Authorization: `Bearer ${localStorage.getItem('aegisaxiom_session_token')}`,
                              },
                            },
                          );
                          if (response.ok) {
                            const data = await response.json();
                            const sow = data.sow;
                            if (sow) {
                              if (sow.timeline_estimate?.estimated_hours)
                                f('estimated_hours', sow.timeline_estimate.estimated_hours);
                              if (sow.cost_estimate?.labor?.subtotal)
                                f('labor_cost', parseFloat(sow.cost_estimate.labor.subtotal));
                              if (sow.cost_estimate?.materials?.estimated_cost)
                                f(
                                  'material_cost',
                                  parseFloat(sow.cost_estimate.materials.estimated_cost),
                                );
                              if (sow.scope_of_work?.description)
                                f('description', sow.scope_of_work.description);
                              toast.success('Auto-filled from survey SOW');
                            }
                          }
                        } catch (error) {
                          console.error('Failed to load survey SOW:', error);
                        }
                      }
                    }}
                  >
                    <option value="">No linked survey</option>
                    {surveyJobs.map((sj) => (
                      <option key={sj.job_id} value={sj.job_id}>
                        {sj.name} - {sj.account_name}
                      </option>
                    ))}
                  </select>
                  <p className="text-[9px] text-aegisaxiom-text-muted mt-1">Auto-fills from survey SOW</p>
                </div>
              )}
            </div>
          </div>

          {/* Job Name */}
          <div>
            <label className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-1 block">
              Job Name *
            </label>
            <input
              className="input-field"
              value={form.name || ''}
              onChange={(e) => f('name', e.target.value)}
              required
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            {/* Account */}
            <div>
              <label className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-1 block">
                Account *
              </label>
              <select
                className="input-field"
                value={form.account_id || ''}
                onChange={(e) => f('account_id', e.target.value)}
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

            {/* Job Type */}
            <div>
              <label className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-1 block">
                Job Type
              </label>
              <select
                className="input-field"
                value={form.job_type || ''}
                onChange={(e) => f('job_type', e.target.value)}
              >
                <option value="">Select Type</option>
                {jobTypes.map((jt) => (
                  <option key={jt.type_id} value={jt.type_id}>
                    {jt.name}
                  </option>
                ))}
              </select>
              {selectedType && (
                <p className="text-[9px] text-aegisaxiom-text-muted mt-0.5">
                  {selectedType.checkin?.length || 0} check-in +{' '}
                  {selectedType.checkout?.length || 0} check-out items
                </p>
              )}
            </div>

            {/* Contract */}
            <div>
              <label className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-1 block">
                Contract
              </label>
              <select
                className="input-field"
                value={form.contract_id || ''}
                onChange={(e) => f('contract_id', e.target.value)}
              >
                <option value="">None</option>
                {accountContracts.map((c) => (
                  <option key={c.contract_id} value={c.contract_id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </div>

            {/* Priority */}
            <div>
              <label className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-1 block">
                Priority
              </label>
              <select
                className="input-field"
                value={form.priority || 'medium'}
                onChange={(e) => f('priority', e.target.value)}
              >
                {PRIORITIES.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>

            {/* Status (edit only) */}
            {modal === 'edit' && (
              <div>
                <label className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-1 block">
                  Status
                </label>
                <select
                  className="input-field"
                  value={form.status || 'scheduled'}
                  onChange={(e) => f('status', e.target.value)}
                >
                  {STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {s.replace(/_/g, ' ')}
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>

          {/* People */}
          <div className="border-t border-aegisaxiom-border pt-3 mt-3">
            <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-2">
              Assignment
            </p>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-[10px] text-aegisaxiom-text-muted mb-1 block">
                  Project Manager
                </label>
                <select
                  className="input-field"
                  value={form.assigned_pm_id || ''}
                  onChange={(e) => f('assigned_pm_id', e.target.value)}
                >
                  <option value="">Unassigned</option>
                  {pms.map((p) => (
                    <option key={p.user_id} value={p.user_id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-[10px] text-aegisaxiom-text-muted mb-1 block">
                  Contractor / Tech
                </label>
                <select
                  className="input-field"
                  value={form.assigned_tech_id || ''}
                  onChange={(e) => f('assigned_tech_id', e.target.value)}
                >
                  <option value="">Unassigned</option>
                  {contractors.map((c) => (
                    <option key={c.user_id} value={c.user_id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          {/* Scheduling */}
          <div className="border-t border-aegisaxiom-border pt-3 mt-3">
            <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-2">
              Schedule
            </p>
            <div className="grid grid-cols-4 gap-3">
              <div>
                <label className="text-[10px] text-aegisaxiom-text-muted mb-1 block">Date</label>
                <input
                  type="date"
                  className="input-field"
                  value={form.scheduled_date || ''}
                  onChange={(e) => f('scheduled_date', e.target.value)}
                />
              </div>
              <div>
                <label className="text-[10px] text-aegisaxiom-text-muted mb-1 block">Start Time</label>
                <input
                  type="time"
                  className="input-field"
                  value={form.scheduled_start || ''}
                  onChange={(e) => f('scheduled_start', e.target.value)}
                />
              </div>
              <div>
                <label className="text-[10px] text-aegisaxiom-text-muted mb-1 block">End Time</label>
                <input
                  type="time"
                  className="input-field"
                  value={form.scheduled_end || ''}
                  onChange={(e) => f('scheduled_end', e.target.value)}
                />
              </div>
              <div>
                <label className="text-[10px] text-aegisaxiom-text-muted mb-1 block">Est. Hours</label>
                <input
                  type="number"
                  step="0.25"
                  className="input-field"
                  value={form.estimated_hours || ''}
                  onChange={(e) => f('estimated_hours', e.target.value)}
                />
              </div>
            </div>
          </div>

          {/* Customer Contact */}
          <div className="border-t border-aegisaxiom-border pt-3 mt-3">
            <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-2">
              Customer Contact
            </p>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="text-[10px] text-aegisaxiom-text-muted mb-1 block">Name</label>
                <input
                  className="input-field"
                  value={form.customer_contact_name || ''}
                  onChange={(e) => f('customer_contact_name', e.target.value)}
                />
              </div>
              <div>
                <label className="text-[10px] text-aegisaxiom-text-muted mb-1 block">Email</label>
                <input
                  type="email"
                  className="input-field"
                  value={form.customer_contact_email || ''}
                  onChange={(e) => f('customer_contact_email', e.target.value)}
                />
              </div>
              <div>
                <label className="text-[10px] text-aegisaxiom-text-muted mb-1 block">Phone</label>
                <input
                  className="input-field"
                  value={form.customer_contact_phone || ''}
                  onChange={(e) => f('customer_contact_phone', e.target.value)}
                />
              </div>
            </div>
          </div>

          {/* Financials */}
          <div className="border-t border-aegisaxiom-border pt-3 mt-3">
            <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-2">
              Financials
            </p>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="text-[10px] text-aegisaxiom-text-muted mb-1 block">
                  Labor Cost ($)
                </label>
                <input
                  type="number"
                  className="input-field"
                  value={form.labor_cost || ''}
                  onChange={(e) => f('labor_cost', e.target.value)}
                />
              </div>
              <div>
                <label className="text-[10px] text-aegisaxiom-text-muted mb-1 block">
                  Material Cost ($)
                </label>
                <input
                  type="number"
                  className="input-field"
                  value={form.material_cost || ''}
                  onChange={(e) => f('material_cost', e.target.value)}
                />
              </div>
              <div>
                <label className="text-[10px] text-aegisaxiom-text-muted mb-1 block">
                  Billable Hours
                </label>
                <input
                  type="number"
                  step="0.25"
                  className="input-field"
                  value={form.billable_hours || ''}
                  onChange={(e) => f('billable_hours', e.target.value)}
                />
              </div>
            </div>
          </div>

          {/* Description / Notes */}
          <div className="border-t border-aegisaxiom-border pt-3 mt-3">
            <div>
              <label className="text-[10px] text-aegisaxiom-text-muted mb-1 block">Description</label>
              <textarea
                rows={2}
                className="input-field resize-none"
                value={form.description || ''}
                onChange={(e) => f('description', e.target.value)}
              />
            </div>
            <div className="mt-2">
              <label className="text-[10px] text-aegisaxiom-text-muted mb-1 block">Notes</label>
              <textarea
                rows={2}
                className="input-field resize-none"
                value={form.notes || ''}
                onChange={(e) => f('notes', e.target.value)}
              />
            </div>
          </div>

          {/* Actions */}
          <div className="flex flex-col-reverse sm:flex-row gap-3 pt-3 border-t border-aegisaxiom-border">
            <button type="button" onClick={onClose} className="btn-secondary flex-1">
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
  );
}

// ---- JOB DETAIL MODAL ----
function JobDetailModal({
  job,
  canEdit,
  onEdit,
  onDelete,
  onStatusChange,
  onPhase3Action,
  onClose,
}) {
  const fmt = (n) => `$${(n || 0).toLocaleString()}`;
  const [tab, setTab] = useState('details'); // details, checklist, site_profile, activity
  const [noteText, setNoteText] = useState('');
  const [activity, setActivity] = useState(job.activity || []);
  const [checklist, setChecklist] = useState({
    checkin: job.checklist_checkin || [],
    checkout: job.checklist_checkout || [],
  });
  const [checklistPhase, setChecklistPhase] = useState('checkin');
  const [siteProfile, setSiteProfile] = useState(null);
  const [spLoading, setSpLoading] = useState(false);
  const nextStatus = { scheduled: 'confirmed', confirmed: 'in_progress', in_progress: 'completed' };
  const next = nextStatus[job.status];

  // Update activity when full job loads
  useEffect(() => {
    if (job.activity) setActivity(job.activity);
    if (job.checklist_checkin || job.checklist_checkout) {
      setChecklist({
        checkin: job.checklist_checkin || [],
        checkout: job.checklist_checkout || [],
      });
    }
  }, [job.activity, job.checklist_checkin, job.checklist_checkout]);

  // Load site profile when tab is selected
  useEffect(() => {
    if (tab === 'site_profile' && !siteProfile && job.account_id) {
      setSpLoading(true);
      api
        .get(`${API_URL}/api/field-services/accounts/${job.account_id}/site-profile`)
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (data) setSiteProfile(data);
        })
        .finally(() => setSpLoading(false));
    }
  }, [tab, siteProfile, job.account_id]);
  const notifHints = {
    confirmed: 'Sends confirmation email to contractor & customer',
    in_progress: 'Sends arrival notification to customer',
    completed: 'Sends completion email to customer',
  };

  const ACTIVITY_ICONS = {
    status_change: {
      icon: CheckCircle2,
      color: 'text-violet-600 dark:text-violet-400',
      bg: 'bg-violet-50 dark:bg-violet-500/10',
    },
    email_sent: {
      icon: Send,
      color: 'text-cyan-600 dark:text-cyan-400',
      bg: 'bg-cyan-50 dark:bg-cyan-500/10',
    },
    edit: {
      icon: Edit2,
      color: 'text-aegisaxiom-text-secondary',
      bg: 'bg-aegisaxiom-elevated',
    },
    note: {
      icon: MessageSquare,
      color: 'text-emerald-600 dark:text-emerald-400',
      bg: 'bg-emerald-50 dark:bg-emerald-500/10',
    },
    check_in: {
      icon: Clock,
      color: 'text-green-600 dark:text-green-400',
      bg: 'bg-green-50 dark:bg-green-500/10',
    },
    check_out: {
      icon: Clock,
      color: 'text-orange-600 dark:text-orange-400',
      bg: 'bg-orange-50 dark:bg-orange-500/10',
    },
  };

  const addNote = async () => {
    if (!noteText.trim()) return;
    try {
      const r = await api.post(`${API_URL}/api/field-services/jobs/${job.job_id}/activity`, {
        type: 'note',
        title: 'Note added',
        detail: noteText.trim(),
      });
      if (r.ok) {
        const entry = await r.json();
        setActivity((prev) => [entry, ...prev]);
        setNoteText('');
        toast.success('Note added');
      }
    } catch (err) {
      toast.error('Failed to add note');
    }
  };

  const updateChecklistItem = async (phase, itemId, value) => {
    const updated = checklist[phase].map((item) =>
      item.id === itemId ? { ...item, value } : item,
    );
    setChecklist((prev) => ({ ...prev, [phase]: updated }));
    try {
      await api.put(`${API_URL}/api/field-services/jobs/${job.job_id}/checklist`, {
        phase,
        items: [{ id: itemId, value }],
      });
    } catch (err) {
      console.error('Checklist update error:', err);
    }
  };

  const saveSiteProfile = async () => {
    if (!siteProfile) return;
    try {
      const r = await api.put(
        `${API_URL}/api/field-services/accounts/${job.account_id}/site-profile`,
        siteProfile,
      );
      if (r.ok) {
        toast.success('Site profile saved');
        setSiteProfile(await r.json());
      } else toast.error('Failed to save');
    } catch (err) {
      toast.error(err.message);
    }
  };

  const formatTime = (ts) => {
    if (!ts) return '';
    try {
      const d = new Date(ts);
      return d.toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        hour12: true,
      });
    } catch {
      return ts;
    }
  };

  return (
    <div className="fixed inset-0 bg-black/60 z-40 flex items-center justify-center p-2 sm:p-4">
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-2xl p-4 sm:p-5 animate-fadeInUp max-h-[95vh] sm:max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between gap-3 mb-4">
          <div className="flex items-center gap-3 min-w-0 flex-1">
            <div className="w-10 h-10 rounded-lg bg-amber-50 dark:bg-amber-600/10 border border-amber-200 dark:border-amber-600/20 flex items-center justify-center shrink-0">
              <Wrench size={18} className="text-amber-600 dark:text-amber-400" />
            </div>
            <div className="min-w-0 flex-1">
              <h2 className="text-base sm:text-lg font-bold text-aegisaxiom-text truncate">{job.name}</h2>
              <p className="text-xs text-aegisaxiom-text-secondary truncate">{job.account_name}</p>
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="p-1 -m-1 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text rounded transition shrink-0"
          >
            <X size={18} />
          </button>
        </div>

        {/* Status + Priority */}
        <div className="flex items-center gap-3 mb-4 flex-wrap">
          <StatusBadge status={job.status} />
          <PriorityDot priority={job.priority} />
          {job.schedule_override && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-orange-50 dark:bg-orange-500/20 text-orange-700 dark:text-orange-300 border border-orange-200 dark:border-orange-500/30">
              Override
            </span>
          )}
          {job.public_access_token &&
            (() => {
              const isExpired =
                job.public_access_token_expires &&
                new Date(job.public_access_token_expires) < new Date();
              return isExpired ? (
                <span className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-600/30">
                  <Link2 size={9} />
                  Link expired
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-600/30">
                  <Link2 size={9} />
                  Public link active
                </span>
              );
            })()}
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mb-4 bg-aegisaxiom-elevated rounded-lg p-0.5">
          {[
            { key: 'details', label: 'Details', icon: FileText },
            {
              key: 'checklist',
              label: 'Checklist',
              icon: CheckCircle2,
              count: checklist.checkin.length + checklist.checkout.length || null,
            },
            { key: 'site_profile', label: 'Site', icon: MapPin },
            { key: 'activity', label: 'Activity', icon: Activity, count: activity.length || null },
          ].map((t) => {
            const TabIcon = t.icon;
            const active = tab === t.key;
            return (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`flex-1 px-1.5 sm:px-2 py-1.5 text-[11px] sm:text-xs font-medium rounded-md transition-all flex items-center justify-center gap-1 sm:gap-1.5 min-w-0 ${active ? 'bg-violet-600 text-white' : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'}`}
              >
                <TabIcon size={12} className="shrink-0" />
                <span className="truncate">{t.label}</span>
                {t.count > 0 && (
                  <span
                    className={`px-1.5 py-0.5 rounded-full text-[9px] leading-none shrink-0 ${active ? 'bg-white/20 text-white' : 'bg-aegisaxiom-surface text-aegisaxiom-text-secondary border border-aegisaxiom-border'}`}
                  >
                    {t.count}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* DETAILS TAB */}
        {tab === 'details' && (
          <>
            {/* Job Type & Category badges */}
            <div className="mb-3 flex items-center gap-2">
              {job.job_type_name && (
                <>
                  <span className="text-[10px] text-aegisaxiom-text-muted uppercase">Type</span>
                  <span className="text-xs font-medium text-aegisaxiom-text px-2 py-0.5 rounded-full bg-aegisaxiom-elevated border border-aegisaxiom-border">
                    {job.job_type_name}
                  </span>
                </>
              )}
              {job.job_category === 'survey' && (
                <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-600/30">
                  Survey
                </span>
              )}
            </div>

            {/* Schedule */}
            <div className="grid grid-cols-2 gap-2 sm:gap-3 mb-4">
              <div className="p-2.5 rounded-lg bg-aegisaxiom-elevated border border-aegisaxiom-border flex items-start gap-2">
                <CalendarIcon
                  size={14}
                  className="text-violet-600 dark:text-violet-400 shrink-0 mt-0.5"
                />
                <div className="min-w-0">
                  <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider">Date</p>
                  <p className="text-sm text-aegisaxiom-text truncate">
                    {job.scheduled_date || job.job_date || '—'}
                  </p>
                </div>
              </div>
              <div className="p-2.5 rounded-lg bg-aegisaxiom-elevated border border-aegisaxiom-border flex items-start gap-2">
                <Clock size={14} className="text-cyan-600 dark:text-cyan-400 shrink-0 mt-0.5" />
                <div className="min-w-0">
                  <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider">Time</p>
                  <p className="text-sm text-aegisaxiom-text truncate">
                    {job.scheduled_start
                      ? `${job.scheduled_start}${job.scheduled_end ? ` – ${job.scheduled_end}` : ''}`
                      : '—'}
                  </p>
                </div>
              </div>
              <div className="p-2.5 rounded-lg bg-aegisaxiom-elevated border border-aegisaxiom-border flex items-start gap-2">
                <Activity size={14} className="text-blue-600 dark:text-blue-400 shrink-0 mt-0.5" />
                <div className="min-w-0">
                  <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider">
                    Est. Hours
                  </p>
                  <p className="text-sm text-aegisaxiom-text">{job.estimated_hours || '—'}h</p>
                </div>
              </div>
              <div className="p-2.5 rounded-lg bg-aegisaxiom-elevated border border-aegisaxiom-border flex items-start gap-2">
                <span className="text-amber-600 dark:text-amber-400 shrink-0 mt-0.5 text-sm font-bold leading-none w-3.5 text-center">
                  $
                </span>
                <div className="min-w-0">
                  <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider">
                    Total Cost
                  </p>
                  <p className="text-sm text-amber-600 dark:text-amber-400 font-semibold">
                    {fmt(job.total_cost)}
                  </p>
                </div>
              </div>
            </div>

            {/* People */}
            <div className="space-y-2 mb-4">
              <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider">Team</p>
              <div className="grid grid-cols-2 gap-2">
                <div className="flex items-center gap-2 p-2 rounded-lg bg-aegisaxiom-accent-muted">
                  <User size={13} className="text-violet-600 dark:text-violet-400" />
                  <div>
                    <p className="text-[10px] text-aegisaxiom-text-muted">PM</p>
                    <p className="text-xs text-aegisaxiom-text">{job.assigned_pm_name || 'Unassigned'}</p>
                  </div>
                </div>
                <div className="flex items-center gap-2 p-2 rounded-lg bg-aegisaxiom-accent-muted">
                  <Wrench size={13} className="text-amber-600 dark:text-amber-400" />
                  <div>
                    <p className="text-[10px] text-aegisaxiom-text-muted">Tech</p>
                    <p className="text-xs text-aegisaxiom-text">
                      {job.assigned_tech_name || 'Unassigned'}
                    </p>
                  </div>
                </div>
              </div>
            </div>

            {/* Customer Contact */}
            {(job.customer_contact_name ||
              job.customer_contact_email ||
              job.customer_contact_phone) && (
              <div className="mb-4">
                <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-2">
                  Customer Contact
                </p>
                <div className="p-2.5 rounded-lg bg-aegisaxiom-accent-muted text-xs space-y-1">
                  {job.customer_contact_name && (
                    <p className="text-aegisaxiom-text">{job.customer_contact_name}</p>
                  )}
                  {job.customer_contact_email && (
                    <p className="text-aegisaxiom-text-secondary flex items-center gap-1">
                      <Mail size={11} /> {job.customer_contact_email}
                    </p>
                  )}
                  {job.customer_contact_phone && (
                    <p className="text-aegisaxiom-text-secondary flex items-center gap-1">
                      <Phone size={11} /> {job.customer_contact_phone}
                    </p>
                  )}
                </div>
              </div>
            )}

            {/* Public Access Link info */}
            {job.public_access_token &&
              (() => {
                const isExpired =
                  job.public_access_token_expires &&
                  new Date(job.public_access_token_expires) < new Date();
                const publicUrl = `${window.location.origin}/public/field-job/${job.job_id}?token=${job.public_access_token}`;
                const expiryLabel = job.public_access_token_expires
                  ? new Date(job.public_access_token_expires).toLocaleString('en-US', {
                      month: 'short',
                      day: 'numeric',
                      year: 'numeric',
                      hour: 'numeric',
                      minute: '2-digit',
                    })
                  : null;
                const copyLink = async () => {
                  try {
                    await navigator.clipboard.writeText(publicUrl);
                    toast.success('Link copied');
                  } catch {
                    toast.error('Copy failed');
                  }
                };
                return (
                  <div className="mb-4">
                    <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-2">
                      Public Access Link
                    </p>
                    <div
                      className={`p-2.5 rounded-lg border text-xs space-y-1.5 ${isExpired ? 'bg-amber-50 dark:bg-amber-600/5 border-amber-200 dark:border-amber-600/20' : 'bg-emerald-50 dark:bg-emerald-600/5 border-emerald-200 dark:border-emerald-600/20'}`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2 min-w-0">
                          <Link2
                            size={11}
                            className={
                              isExpired
                                ? 'text-amber-600 dark:text-amber-400 shrink-0'
                                : 'text-emerald-600 dark:text-emerald-400 shrink-0'
                            }
                          />
                          <span
                            className={`font-medium ${isExpired ? 'text-amber-700 dark:text-amber-300' : 'text-emerald-700 dark:text-emerald-300'}`}
                          >
                            {isExpired ? 'Expired' : 'Active'}
                          </span>
                        </div>
                        {!isExpired && (
                          <button
                            onClick={copyLink}
                            className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium text-emerald-700 dark:text-emerald-300 hover:bg-emerald-100 dark:hover:bg-emerald-600/10 transition shrink-0"
                            aria-label="Copy public link"
                          >
                            <Copy size={10} /> Copy
                          </button>
                        )}
                      </div>
                      <p className="text-aegisaxiom-text-secondary font-mono text-[10px] truncate">
                        {publicUrl}
                      </p>
                      {expiryLabel && (
                        <p className="text-aegisaxiom-text-muted text-[10px]">
                          {isExpired ? 'Expired' : 'Expires'}: {expiryLabel}
                        </p>
                      )}
                    </div>
                  </div>
                );
              })()}

            {/* Description / Notes */}
            {(job.description || job.notes) && (
              <div className="mb-4">
                {job.description && (
                  <div className="mb-2">
                    <p className="text-[10px] text-aegisaxiom-text-muted uppercase">Description</p>
                    <p className="text-xs text-aegisaxiom-text-secondary mt-1">{job.description}</p>
                  </div>
                )}
                {job.notes && (
                  <div>
                    <p className="text-[10px] text-aegisaxiom-text-muted uppercase">Notes</p>
                    <p className="text-xs text-aegisaxiom-text-secondary mt-1">{job.notes}</p>
                  </div>
                )}
              </div>
            )}
          </>
        )}

        {/* CHECKLIST TAB */}
        {tab === 'checklist' && (
          <div>
            {checklist.checkin.length === 0 && checklist.checkout.length === 0 ? (
              <div className="text-center py-8 text-aegisaxiom-text-muted">
                <CheckCircle2 size={24} className="mx-auto mb-2 opacity-50" />
                <p className="text-xs">No checklist assigned</p>
                <p className="text-[10px] text-aegisaxiom-text-muted mt-1">
                  Select a job type when creating the job to auto-populate checklists
                </p>
              </div>
            ) : (
              <>
                {/* Phase toggle */}
                <div className="flex gap-1 mb-3 bg-aegisaxiom-elevated rounded-lg p-0.5">
                  <button
                    onClick={() => setChecklistPhase('checkin')}
                    className={`flex-1 px-3 py-1.5 text-xs font-medium rounded-md transition-all ${checklistPhase === 'checkin' ? 'bg-green-600 text-white' : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'}`}
                  >
                    Check-In (
                    {
                      checklist.checkin.filter(
                        (i) => i.value !== null && i.value !== '' && i.value !== undefined,
                      ).length
                    }
                    /{checklist.checkin.length})
                  </button>
                  <button
                    onClick={() => setChecklistPhase('checkout')}
                    className={`flex-1 px-3 py-1.5 text-xs font-medium rounded-md transition-all ${checklistPhase === 'checkout' ? 'bg-orange-600 text-white' : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'}`}
                  >
                    Check-Out (
                    {
                      checklist.checkout.filter(
                        (i) => i.value !== null && i.value !== '' && i.value !== undefined,
                      ).length
                    }
                    /{checklist.checkout.length})
                  </button>
                </div>

                {/* Checklist items */}
                <div className="space-y-1.5">
                  {checklist[checklistPhase].map((item) => (
                    <div
                      key={item.id}
                      className={`p-2.5 rounded-lg border transition-all ${item.value === true || (item.value && item.value !== '') ? 'bg-emerald-50 dark:bg-emerald-950/20 border-emerald-300 dark:border-emerald-500/20' : 'bg-aegisaxiom-accent-muted border-aegisaxiom-border'}`}
                    >
                      <div className="flex items-start gap-2">
                        {item.type === 'boolean' ? (
                          <button
                            onClick={() =>
                              updateChecklistItem(checklistPhase, item.id, !item.value)
                            }
                            className={`mt-0.5 w-5 h-5 rounded border-2 flex items-center justify-center flex-shrink-0 transition-all ${item.value ? 'bg-emerald-500 border-emerald-500' : 'border-aegisaxiom-border-hover hover:border-violet-500'}`}
                          >
                            {item.value && <CheckCircle2 size={12} className="text-white" />}
                          </button>
                        ) : item.type === 'select' ? (
                          <select
                            value={item.value || ''}
                            onChange={(e) =>
                              updateChecklistItem(checklistPhase, item.id, e.target.value)
                            }
                            className="input-field !py-1 !text-xs flex-shrink-0 max-w-[10rem] w-full sm:!w-40"
                          >
                            <option value="">Select...</option>
                            {(item.options || []).map((o) => (
                              <option key={o} value={o}>
                                {o}
                              </option>
                            ))}
                          </select>
                        ) : item.type === 'number' ? (
                          <input
                            type="number"
                            value={item.value || ''}
                            onChange={(e) =>
                              updateChecklistItem(checklistPhase, item.id, e.target.value)
                            }
                            className="input-field !py-1 !text-xs flex-shrink-0 !w-20"
                            placeholder="0"
                          />
                        ) : item.type === 'date' ? (
                          <input
                            type="date"
                            value={item.value || ''}
                            onChange={(e) =>
                              updateChecklistItem(checklistPhase, item.id, e.target.value)
                            }
                            className="input-field !py-1 !text-xs flex-shrink-0 !w-36"
                          />
                        ) : item.type === 'text' ? (
                          <input
                            value={item.value || ''}
                            onChange={(e) =>
                              updateChecklistItem(checklistPhase, item.id, e.target.value)
                            }
                            className="input-field !py-1 !text-xs flex-shrink-0 max-w-[12rem] w-full sm:!w-48"
                            placeholder="Enter..."
                          />
                        ) : null}
                        <div className="flex-1 min-w-0">
                          <p
                            className={`text-xs ${item.value ? 'text-aegisaxiom-text' : 'text-aegisaxiom-text-secondary'}`}
                          >
                            {item.label}
                            {item.required && (
                              <span className="text-red-600 dark:text-red-400 ml-0.5">*</span>
                            )}
                          </p>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        )}

        {/* SITE PROFILE TAB */}
        {tab === 'site_profile' && (
          <div>
            {spLoading ? (
              <div className="text-center py-8 text-aegisaxiom-text-muted">
                <p className="text-xs">Loading site profile...</p>
              </div>
            ) : !siteProfile ? (
              <div className="text-center py-8 text-aegisaxiom-text-muted">
                <MapPin size={24} className="mx-auto mb-2 opacity-50" />
                <p className="text-xs">No site profile found</p>
              </div>
            ) : (
              <div className="space-y-3">
                {/* General */}
                <div className="p-3 rounded-lg bg-aegisaxiom-accent-muted border border-aegisaxiom-border">
                  <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-2">
                    General
                  </p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <div>
                      <label className="text-[9px] text-aegisaxiom-text-muted">MDF Location</label>
                      <input
                        className="input-field !py-1 !text-xs"
                        value={siteProfile.general?.mdf_location || ''}
                        onChange={(e) =>
                          setSiteProfile((p) => ({
                            ...p,
                            general: { ...p.general, mdf_location: e.target.value },
                          }))
                        }
                      />
                    </div>
                    <div>
                      <label className="text-[9px] text-aegisaxiom-text-muted">ISP</label>
                      <input
                        className="input-field !py-1 !text-xs"
                        value={siteProfile.general?.isp || ''}
                        onChange={(e) =>
                          setSiteProfile((p) => ({
                            ...p,
                            general: { ...p.general, isp: e.target.value },
                          }))
                        }
                      />
                    </div>
                    <div>
                      <label className="text-[9px] text-aegisaxiom-text-muted">Offices</label>
                      <input
                        type="number"
                        className="input-field !py-1 !text-xs"
                        value={siteProfile.general?.offices || ''}
                        onChange={(e) =>
                          setSiteProfile((p) => ({
                            ...p,
                            general: { ...p.general, offices: parseInt(e.target.value) || 0 },
                          }))
                        }
                      />
                    </div>
                    <div>
                      <label className="text-[9px] text-aegisaxiom-text-muted">Conference Rooms</label>
                      <input
                        type="number"
                        className="input-field !py-1 !text-xs"
                        value={siteProfile.general?.conference_rooms || ''}
                        onChange={(e) =>
                          setSiteProfile((p) => ({
                            ...p,
                            general: {
                              ...p.general,
                              conference_rooms: parseInt(e.target.value) || 0,
                            },
                          }))
                        }
                      />
                    </div>
                  </div>
                </div>

                {/* Network Equipment */}
                <div className="p-3 rounded-lg bg-aegisaxiom-accent-muted border border-aegisaxiom-border">
                  <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-2">
                    Network / Rack
                  </p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <div>
                      <label className="text-[9px] text-aegisaxiom-text-muted">Rack Type</label>
                      <input
                        className="input-field !py-1 !text-xs"
                        value={siteProfile.network_equipment?.rack_type || ''}
                        onChange={(e) =>
                          setSiteProfile((p) => ({
                            ...p,
                            network_equipment: {
                              ...p.network_equipment,
                              rack_type: e.target.value,
                            },
                          }))
                        }
                      />
                    </div>
                    <div>
                      <label className="text-[9px] text-aegisaxiom-text-muted">Available U's</label>
                      <input
                        type="number"
                        className="input-field !py-1 !text-xs"
                        value={siteProfile.network_equipment?.rack_us_available || ''}
                        onChange={(e) =>
                          setSiteProfile((p) => ({
                            ...p,
                            network_equipment: {
                              ...p.network_equipment,
                              rack_us_available: parseInt(e.target.value) || 0,
                            },
                          }))
                        }
                      />
                    </div>
                    <div className="col-span-2">
                      <label className="text-[9px] text-aegisaxiom-text-muted">Equipment List</label>
                      <textarea
                        rows={2}
                        className="input-field !py-1 !text-xs resize-none"
                        value={siteProfile.network_equipment?.equipment_list || ''}
                        onChange={(e) =>
                          setSiteProfile((p) => ({
                            ...p,
                            network_equipment: {
                              ...p.network_equipment,
                              equipment_list: e.target.value,
                            },
                          }))
                        }
                      />
                    </div>
                  </div>
                </div>

                {/* Security */}
                <div className="p-3 rounded-lg bg-aegisaxiom-accent-muted border border-aegisaxiom-border">
                  <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-2">
                    Security
                  </p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <div>
                      <label className="text-[9px] text-aegisaxiom-text-muted">Cameras</label>
                      <input
                        type="number"
                        className="input-field !py-1 !text-xs"
                        value={siteProfile.security?.camera_count || ''}
                        onChange={(e) =>
                          setSiteProfile((p) => ({
                            ...p,
                            security: {
                              ...p.security,
                              camera_count: parseInt(e.target.value) || 0,
                              cameras: parseInt(e.target.value) > 0,
                            },
                          }))
                        }
                      />
                    </div>
                    <div>
                      <label className="text-[9px] text-aegisaxiom-text-muted">Camera Make/Model</label>
                      <input
                        className="input-field !py-1 !text-xs"
                        value={siteProfile.security?.camera_make_model || ''}
                        onChange={(e) =>
                          setSiteProfile((p) => ({
                            ...p,
                            security: { ...p.security, camera_make_model: e.target.value },
                          }))
                        }
                      />
                    </div>
                    <div>
                      <label className="text-[9px] text-aegisaxiom-text-muted">Access Control Type</label>
                      <input
                        className="input-field !py-1 !text-xs"
                        value={siteProfile.access_control?.type || ''}
                        onChange={(e) =>
                          setSiteProfile((p) => ({
                            ...p,
                            access_control: { ...p.access_control, type: e.target.value },
                          }))
                        }
                      />
                    </div>
                    <div>
                      <label className="text-[9px] text-aegisaxiom-text-muted">Alarm System</label>
                      <select
                        className="input-field !py-1 !text-xs"
                        value={siteProfile.security?.alarm_system ? 'yes' : 'no'}
                        onChange={(e) =>
                          setSiteProfile((p) => ({
                            ...p,
                            security: { ...p.security, alarm_system: e.target.value === 'yes' },
                          }))
                        }
                      >
                        <option value="no">No</option>
                        <option value="yes">Yes</option>
                      </select>
                    </div>
                  </div>
                </div>

                {/* Fiber */}
                <div className="p-3 rounded-lg bg-aegisaxiom-accent-muted border border-aegisaxiom-border">
                  <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-2">
                    Fiber
                  </p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <div>
                      <label className="text-[9px] text-aegisaxiom-text-muted">Type (SM/MM)</label>
                      <select
                        className="input-field !py-1 !text-xs"
                        value={siteProfile.fiber?.sm_or_mm || ''}
                        onChange={(e) =>
                          setSiteProfile((p) => ({
                            ...p,
                            fiber: { ...p.fiber, sm_or_mm: e.target.value },
                          }))
                        }
                      >
                        <option value="">—</option>
                        <option value="SM">SM</option>
                        <option value="MM">MM</option>
                      </select>
                    </div>
                    <div>
                      <label className="text-[9px] text-aegisaxiom-text-muted">Strand Count</label>
                      <input
                        type="number"
                        className="input-field !py-1 !text-xs"
                        value={siteProfile.fiber?.strand_count || ''}
                        onChange={(e) =>
                          setSiteProfile((p) => ({
                            ...p,
                            fiber: { ...p.fiber, strand_count: parseInt(e.target.value) || 0 },
                          }))
                        }
                      />
                    </div>
                    <div>
                      <label className="text-[9px] text-aegisaxiom-text-muted">Connector Type</label>
                      <select
                        className="input-field !py-1 !text-xs"
                        value={siteProfile.fiber?.connector_type || ''}
                        onChange={(e) =>
                          setSiteProfile((p) => ({
                            ...p,
                            fiber: { ...p.fiber, connector_type: e.target.value },
                          }))
                        }
                      >
                        <option value="">—</option>
                        <option value="LC">LC</option>
                        <option value="SC">SC</option>
                        <option value="ST">ST</option>
                      </select>
                    </div>
                    <div>
                      <label className="text-[9px] text-aegisaxiom-text-muted">Available Strands</label>
                      <input
                        type="number"
                        className="input-field !py-1 !text-xs"
                        value={siteProfile.fiber?.available_strands || ''}
                        onChange={(e) =>
                          setSiteProfile((p) => ({
                            ...p,
                            fiber: { ...p.fiber, available_strands: parseInt(e.target.value) || 0 },
                          }))
                        }
                      />
                    </div>
                  </div>
                </div>

                {/* Notes */}
                <div className="p-3 rounded-lg bg-aegisaxiom-accent-muted border border-aegisaxiom-border">
                  <label className="text-[9px] text-aegisaxiom-text-muted">Site Notes</label>
                  <textarea
                    rows={2}
                    className="input-field !py-1 !text-xs resize-none mt-1"
                    value={siteProfile.notes || ''}
                    onChange={(e) => setSiteProfile((p) => ({ ...p, notes: e.target.value }))}
                  />
                </div>

                <button onClick={saveSiteProfile} className="btn-primary w-full text-sm">
                  Save Site Profile
                </button>
              </div>
            )}
          </div>
        )}

        {/* ACTIVITY TAB */}
        {tab === 'activity' && (
          <div>
            {/* Add note */}
            <div className="flex gap-2 mb-4">
              <input
                className="input-field flex-1 text-xs"
                placeholder="Add a note or comment..."
                value={noteText}
                onChange={(e) => setNoteText(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && addNote()}
              />
              <button
                onClick={addNote}
                disabled={!noteText.trim()}
                className="btn-primary px-3 text-xs"
              >
                <Send size={12} />
              </button>
            </div>

            {/* Activity Timeline */}
            {activity.length === 0 ? (
              <div className="text-center py-8 text-aegisaxiom-text-muted">
                <Activity size={24} className="mx-auto mb-2 opacity-50" />
                <p className="text-xs">No activity yet</p>
              </div>
            ) : (
              <div className="relative">
                {/* Timeline line */}
                <div className="absolute left-[15px] top-0 bottom-0 w-px bg-aegisaxiom-elevated" />
                <div className="space-y-0">
                  {activity.map((a, i) => {
                    const config = ACTIVITY_ICONS[a.type] || ACTIVITY_ICONS.note;
                    const Icon = config.icon;
                    return (
                      <div key={a.activity_id || i} className="relative flex gap-3 py-2.5 pl-0">
                        {/* Icon */}
                        <div
                          className={`relative z-10 w-[30px] h-[30px] rounded-full ${config.bg} flex items-center justify-center flex-shrink-0`}
                        >
                          <Icon size={13} className={config.color} />
                        </div>
                        {/* Content */}
                        <div className="flex-1 min-w-0 pt-0.5">
                          <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-0.5 sm:gap-2">
                            <div className="min-w-0">
                              <p className="text-xs font-medium text-aegisaxiom-text">{a.title}</p>
                              {a.detail && (
                                <p className="text-[11px] text-aegisaxiom-text-secondary mt-0.5 break-words">
                                  {a.detail}
                                </p>
                              )}
                            </div>
                            <p className="text-[9px] text-aegisaxiom-text-muted whitespace-nowrap shrink-0">
                              {formatTime(a.timestamp)}
                            </p>
                          </div>
                          <p className="text-[9px] text-aegisaxiom-text-muted mt-0.5">
                            {a.user_name || 'System'}
                            {a.type === 'email_sent' && a.metadata?.to && (
                              <span className="text-cyan-600 dark:text-cyan-400 ml-1">
                                → {a.metadata.to}
                              </span>
                            )}
                          </p>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Actions */}
        {canEdit && (
          <div className="pt-3 border-t border-aegisaxiom-border space-y-2 mt-4">
            {/* Phase 3B: Check-In/Check-Out Buttons */}
            {(job.status === 'scheduled' || job.status === 'confirmed') && !job.check_in_time && (
              <button
                onClick={() => onPhase3Action('check-in')}
                className="btn-primary w-full flex items-center justify-center gap-1.5 text-sm"
              >
                <LogIn size={14} /> Check In
              </button>
            )}
            {job.status === 'in_progress' && job.check_in_time && (
              <button
                onClick={() => onPhase3Action('check-out')}
                className="btn-secondary w-full flex items-center justify-center gap-1.5 text-sm"
              >
                <LogOut size={14} /> Check Out
              </button>
            )}

            {/* Phase 3A: Survey & SOW Buttons */}
            {job.job_category === 'survey' && (
              <>
                {!job.survey_form_data && (
                  <button
                    onClick={() => onPhase3Action('survey-form')}
                    className="btn-primary w-full flex items-center justify-center gap-1.5 text-sm"
                  >
                    <FileText size={14} /> Complete Survey
                  </button>
                )}
                {job.survey_form_data && !job.sow_data && (
                  <button
                    onClick={() => onPhase3Action('sow')}
                    className="btn-primary w-full flex items-center justify-center gap-1.5 text-sm"
                  >
                    <FileText size={14} /> Generate SOW
                  </button>
                )}
                {job.sow_data && (
                  <button
                    onClick={() => onPhase3Action('sow')}
                    className="btn-secondary w-full flex items-center justify-center gap-1.5 text-sm"
                  >
                    <FileText size={14} /> View SOW
                  </button>
                )}
              </>
            )}

            {/* Public Link Button (for execution jobs) */}
            {job.job_category === 'execution' && (
              <button
                onClick={() => onPhase3Action('public-link')}
                className="btn-secondary w-full flex items-center justify-center gap-1.5 text-sm"
              >
                <Link2 size={14} />{' '}
                {job.public_access_token ? 'Manage Public Link' : 'Generate Public Link'}
              </button>
            )}

            {/* Standard Status Change */}
            {next && (
              <div>
                <button
                  onClick={() => onStatusChange(next)}
                  className="btn-primary w-full flex items-center justify-center gap-1.5 text-sm"
                >
                  <CheckCircle2 size={14} /> Move to {next.replace(/_/g, ' ')}
                </button>
                {notifHints[next] && (
                  <p className="text-[10px] text-aegisaxiom-text-muted text-center mt-1 flex items-center justify-center gap-1">
                    <Mail size={10} /> {notifHints[next]}
                  </p>
                )}
              </div>
            )}
            <div className="flex gap-2">
              <button
                onClick={onEdit}
                className="btn-secondary flex-1 flex items-center justify-center gap-1.5 text-sm"
              >
                <Edit2 size={13} /> Edit
              </button>
              <button
                onClick={onDelete}
                aria-label="Delete job"
                className="px-3 py-1.5 text-sm text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 rounded-lg transition-colors"
              >
                <Trash2 size={13} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---- NOTIFICATION SETTINGS MODAL ----
function NotificationSettingsModal({ onClose }) {
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const r = await api.get(`${API_URL}/api/field-services/notification-settings`);
        if (r.ok) setSettings(await r.json());
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const toggle = (key) => {
    setSettings((prev) => ({
      ...prev,
      [key]: { ...prev[key], enabled: !prev[key]?.enabled },
    }));
  };

  const save = async () => {
    setSaving(true);
    try {
      const r = await api.put(`${API_URL}/api/field-services/notification-settings`, settings);
      if (r.ok) {
        toast.success('Notification settings saved');
        onClose();
      } else {
        toast.error('Failed to save');
      }
    } catch (err) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  };

  const triggerReminders = async () => {
    try {
      const r = await api.post(`${API_URL}/api/field-services/send-reminders`);
      if (r.ok) {
        const data = await r.json();
        toast.success(data.message);
      }
    } catch (err) {
      toast.error('Failed to trigger reminders');
    }
  };

  const NOTIF_KEYS = [
    'day_before_contractor_reminder',
    'day_before_customer_reminder',
    'on_confirmed_contractor',
    'on_confirmed_customer',
    'on_arrival_customer',
    'on_completed_customer',
  ];

  const CATEGORY_LABELS = {
    reminders: 'Scheduled Reminders',
    status_change: 'Status Change Notifications',
  };

  const CATEGORY_ICONS = {
    reminders: Clock,
    status_change: Activity,
  };

  // Group by category
  const grouped = {};
  NOTIF_KEYS.forEach((key) => {
    const s = settings?.[key];
    if (!s || typeof s !== 'object') return;
    const cat = s.category || 'other';
    if (!grouped[cat]) grouped[cat] = [];
    grouped[cat].push({ key, ...s });
  });
  const hasGroups = Object.keys(grouped).length > 0;

  return (
    <div className="fixed inset-0 bg-black/60 z-40 flex items-center justify-center p-2 sm:p-4">
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-lg p-4 sm:p-5 animate-fadeInUp max-h-[95vh] sm:max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between gap-3 mb-5">
          <div className="flex items-center gap-3 min-w-0 flex-1">
            <div className="w-10 h-10 rounded-lg bg-violet-50 dark:bg-violet-600/10 border border-violet-200 dark:border-violet-600/20 flex items-center justify-center shrink-0">
              <Bell size={18} className="text-violet-600 dark:text-violet-400" />
            </div>
            <div className="min-w-0 flex-1">
              <h2 className="text-base sm:text-lg font-bold text-aegisaxiom-text truncate">
                Notification Settings
              </h2>
              <p className="text-xs text-aegisaxiom-text-secondary truncate">
                Configure automated field service notifications
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="p-1 -m-1 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text rounded transition shrink-0"
          >
            <X size={18} />
          </button>
        </div>

        {loading ? (
          <div className="py-16 flex flex-col items-center justify-center text-aegisaxiom-text-muted">
            <Loader2 size={22} className="animate-spin mb-3 text-violet-600 dark:text-violet-400" />
            <p className="text-xs">Loading notification settings…</p>
          </div>
        ) : !hasGroups ? (
          <div className="py-12 text-center text-aegisaxiom-text-muted">
            <Bell size={24} className="mx-auto mb-2 opacity-50" />
            <p className="text-xs">No notification settings available</p>
          </div>
        ) : (
          <div className="space-y-5">
            {Object.entries(grouped).map(([cat, items]) => {
              const CatIcon = CATEGORY_ICONS[cat];
              return (
                <div key={cat}>
                  <p className="text-[10px] text-aegisaxiom-text-muted uppercase tracking-wider mb-2 flex items-center gap-1.5">
                    {CatIcon && <CatIcon size={11} />}
                    {CATEGORY_LABELS[cat] || cat}
                  </p>
                  <div className="space-y-2">
                    {items.map((item) => (
                      <div
                        key={item.key}
                        className="flex items-center justify-between gap-3 p-3 rounded-lg bg-aegisaxiom-elevated border border-aegisaxiom-border"
                      >
                        <div className="flex-1 min-w-0">
                          <p className="text-xs font-medium text-aegisaxiom-text">{item.label}</p>
                          {item.description && (
                            <p className="text-[10px] text-aegisaxiom-text-muted mt-0.5">
                              {item.description}
                            </p>
                          )}
                        </div>
                        <button
                          onClick={() => toggle(item.key)}
                          aria-label={`Toggle ${item.label}`}
                          aria-pressed={!!item.enabled}
                          className={`relative w-10 h-5 rounded-full transition-colors shrink-0 ${item.enabled ? 'bg-violet-600' : 'bg-aegisaxiom-border'}`}
                        >
                          <span
                            className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${item.enabled ? 'left-5' : 'left-0.5'}`}
                          />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Manual trigger */}
        {!loading && hasGroups && (
          <div className="mt-5 p-3 rounded-lg bg-aegisaxiom-accent-muted border border-aegisaxiom-border">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
              <div className="min-w-0">
                <p className="text-xs font-medium text-aegisaxiom-text">Manual Reminder Trigger</p>
                <p className="text-[10px] text-aegisaxiom-text-muted">
                  Send day-before reminders for tomorrow's jobs now
                </p>
              </div>
              <button
                onClick={triggerReminders}
                className="btn-secondary text-xs flex items-center justify-center gap-1.5 w-full sm:w-auto shrink-0"
              >
                <Send size={12} /> Send Now
              </button>
            </div>
          </div>
        )}

        <div className="flex flex-col-reverse sm:flex-row gap-3 pt-4 mt-4 border-t border-aegisaxiom-border">
          <button onClick={onClose} className="btn-secondary flex-1">
            Cancel
          </button>
          <button
            onClick={save}
            disabled={saving || loading || !hasGroups}
            className="btn-primary flex-1"
          >
            {saving ? 'Saving...' : 'Save Settings'}
          </button>
        </div>
      </div>
    </div>
  );
}

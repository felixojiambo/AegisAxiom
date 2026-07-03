import React, { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { useAuth } from '../contexts/AuthContext';
import {
  DollarSign,
  Clock,
  CheckCircle,
  AlertCircle,
  XCircle,
  Calendar,
  Briefcase,
  TrendingUp,
} from 'lucide-react';
import { toast } from 'sonner';
import { useNavigate } from 'react-router-dom';
import API_URL from '../lib/apiConfig';

const API = API_URL;
const fmt = (n) =>
  `$${(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtH = (n) => `${(n || 0).toFixed(1)}h`;

const STATUS_CONFIG = {
  pending: {
    label: 'Pending Approval',
    color: 'bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 border-amber-500/30',
    icon: Clock,
  },
  approved: {
    label: 'Approved',
    color: 'bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border-emerald-500/30',
    icon: CheckCircle,
  },
  rejected: {
    label: 'Rejected',
    color: 'bg-red-50 dark:bg-red-600/20 text-red-700 dark:text-red-300 border-red-500/30',
    icon: XCircle,
  },
};

export default function ContractorPortal() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState(null);
  const [selectedStatus, setSelectedStatus] = useState('pending');

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const res = await api.get(`${API}/api/finance/contractor-payments`);
      if (res.ok) {
        setData(await res.json());
      } else {
        toast.error('Failed to load payment data');
      }
    } catch (err) {
      toast.error('Error loading data');
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="text-aegisaxiom-text-secondary text-sm">Loading your dashboard...</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="text-aegisaxiom-text-secondary text-sm">No data available</div>
      </div>
    );
  }

  const summary = data.summary || {};
  const entriesByStatus = data.entries_by_status || {};
  const contracts = data.assigned_contracts || [];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-aegisaxiom-text">Contractor Dashboard</h1>
        <p className="text-aegisaxiom-text-secondary text-sm mt-1">Track your time entries and payment status</p>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="bg-aegisaxiom-accent-soft border border-aegisaxiom-border rounded-lg p-4">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-blue-50 dark:bg-blue-600/20 text-blue-700 dark:text-blue-300 border border-blue-500/30">
              <Clock size={20} />
            </div>
            <div>
              <p className="text-xs text-aegisaxiom-text-muted">Total Hours</p>
              <p className="text-xl font-semibold text-aegisaxiom-text">{fmtH(summary.total_hours_logged)}</p>
            </div>
          </div>
        </div>

        <div className="bg-aegisaxiom-accent-soft border border-aegisaxiom-border rounded-lg p-4">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-emerald-50 dark:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30">
              <CheckCircle size={20} />
            </div>
            <div>
              <p className="text-xs text-aegisaxiom-text-muted">Approved Earnings</p>
              <p className="text-xl font-semibold text-aegisaxiom-text">
                {fmt(summary.total_earned_approved)}
              </p>
            </div>
          </div>
        </div>

        <div className="bg-aegisaxiom-accent-soft border border-aegisaxiom-border rounded-lg p-4">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-amber-50 dark:bg-amber-600/20 text-amber-700 dark:text-amber-300 border border-amber-500/30">
              <AlertCircle size={20} />
            </div>
            <div>
              <p className="text-xs text-aegisaxiom-text-muted">Pending Approval</p>
              <p className="text-xl font-semibold text-aegisaxiom-text">
                {fmt(summary.total_earned_pending)}
              </p>
            </div>
          </div>
        </div>

        <div className="bg-aegisaxiom-accent-soft border border-aegisaxiom-border rounded-lg p-4">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-violet-50 dark:bg-violet-600/20 text-violet-700 dark:text-violet-300 border border-violet-500/30">
              <TrendingUp size={20} />
            </div>
            <div>
              <p className="text-xs text-aegisaxiom-text-muted">Total Earned</p>
              <p className="text-xl font-semibold text-aegisaxiom-text">{fmt(summary.total_earned)}</p>
            </div>
          </div>
        </div>
      </div>

      {/* Quick Actions */}
      <div className="flex gap-3">
        <button
          onClick={() => navigate('/time')}
          className="flex items-center gap-2 bg-violet-600 hover:bg-violet-500 text-white text-sm px-4 py-2 rounded-lg transition"
        >
          <Clock size={16} /> Submit Time Entry
        </button>
        <button
          onClick={() => navigate('/projects')}
          className="flex items-center gap-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm text-sm px-4 py-2 rounded-lg transition"
        >
          <Briefcase size={16} /> View Projects
        </button>
      </div>

      {/* Assigned Contracts */}
      {contracts.length > 0 && (
        <div>
          <h2 className="text-sm font-semibold text-aegisaxiom-text mb-3 flex items-center gap-2">
            <Briefcase size={16} /> Your Assigned Contracts
          </h2>
          <div className="grid gap-3">
            {contracts.map((contract, i) => (
              <div key={i} className="bg-aegisaxiom-accent-soft border border-aegisaxiom-border rounded-lg p-4">
                <div className="flex items-start justify-between">
                  <div>
                    <h3 className="text-sm font-medium text-aegisaxiom-text">{contract.contract_name}</h3>
                    <p className="text-xs text-aegisaxiom-text-muted mt-1">{contract.account_name}</p>
                    {contract.role && (
                      <span className="inline-block mt-1 px-2 py-0.5 rounded text-xs bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary">
                        {contract.role}
                      </span>
                    )}
                  </div>
                  <div className="text-right">
                    <p className="text-xs text-aegisaxiom-text-muted">Your Rate</p>
                    <p className="text-sm font-semibold text-emerald-600 dark:text-emerald-400">
                      {fmt(contract.cost_rate)}/hr
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Time Entries by Status */}
      <div>
        <h2 className="text-sm font-semibold text-aegisaxiom-text mb-3">Your Time Entries</h2>

        {/* Status Tabs */}
        <div className="flex gap-1 bg-aegisaxiom-elevated rounded-lg p-0.5 mb-4 w-fit">
          {Object.entries(STATUS_CONFIG).map(([key, cfg]) => {
            const count = (entriesByStatus[key] || []).length;
            const Icon = cfg.icon;
            return (
              <button
                key={key}
                onClick={() => setSelectedStatus(key)}
                className={`px-3 py-1.5 text-xs rounded-md transition flex items-center gap-1.5 ${
                  selectedStatus === key
                    ? 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm font-medium'
                    : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'
                }`}
              >
                <Icon size={12} />
                {cfg.label}
                <span className="text-aegisaxiom-text-muted ml-1">({count})</span>
              </button>
            );
          })}
        </div>

        {/* Entries List */}
        <div className="space-y-2">
          {(entriesByStatus[selectedStatus] || []).length === 0 ? (
            <div className="bg-aegisaxiom-accent-muted border border-aegisaxiom-border rounded-lg p-8 text-center">
              <p className="text-xs text-aegisaxiom-text-secondary">No {selectedStatus} time entries</p>
            </div>
          ) : (
            (entriesByStatus[selectedStatus] || []).map((entry, i) => (
              <div
                key={i}
                className="bg-aegisaxiom-accent-soft border border-aegisaxiom-border rounded-lg p-4 hover:border-aegisaxiom-border-hover/50 transition"
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <Calendar size={14} className="text-aegisaxiom-text-muted" />
                      <span className="text-sm font-medium text-aegisaxiom-text">
                        {new Date(entry.date).toLocaleDateString('en-US', {
                          weekday: 'short',
                          month: 'short',
                          day: 'numeric',
                          year: 'numeric',
                        })}
                      </span>
                      <span
                        className={`px-2 py-0.5 rounded-full text-xs border ${STATUS_CONFIG[entry.status]?.color || ''}`}
                      >
                        {STATUS_CONFIG[entry.status]?.label || entry.status}
                      </span>
                    </div>
                    <div className="mt-2 space-y-1">
                      <p className="text-xs text-aegisaxiom-text-secondary">
                        <span className="font-medium text-aegisaxiom-text-secondary">{entry.project_name}</span>
                        <span className="mx-1">·</span>
                        <span>{entry.contract_name}</span>
                      </p>
                      {entry.notes && (
                        <p className="text-xs text-aegisaxiom-text-muted italic">{entry.notes}</p>
                      )}
                    </div>
                  </div>
                  <div className="text-right ml-4">
                    <p className="text-lg font-semibold text-aegisaxiom-text">{fmtH(entry.duration_hours)}</p>
                    <p className="text-xs text-aegisaxiom-text-muted">{fmt(entry.cost_rate)}/hr</p>
                    <p className="text-sm font-medium text-emerald-600 dark:text-emerald-400 mt-1">{fmt(entry.earned)}</p>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

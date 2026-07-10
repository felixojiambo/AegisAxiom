import React, { useEffect, useState, useCallback } from 'react';
import { Plus, Users, ChevronRight, AlertCircle } from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';
import { ASSIGNMENT_MODE_LABELS } from './constants';
import TeamModal from './TeamModal';
import TeamDetail from './TeamDetail';
import ConfirmModal from '../shared/ConfirmModal';

export default function TeamsTab() {
  const { hasPermission } = usePermissions();
  const canCreate = hasPermission('ai_dispatcher.teams.create');

  const [teams, setTeams] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // null = list view, teamId string = detail view
  const [selectedTeamId, setSelectedTeamId] = useState(null);

  // Modal state
  const [modalOpen, setModalOpen] = useState(false);
  const [editingTeam, setEditingTeam] = useState(null);

  // Confirm-delete modal state
  const [confirmState, setConfirmState] = useState({
    open: false,
    title: '',
    message: '',
    action: null,
  });
  const closeConfirm = () =>
    setConfirmState({ open: false, title: '', message: '', action: null });

  const fetchTeams = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get(`${API_URL}/api/ai-dispatcher/teams`);
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || 'Failed to load teams');
      setTeams(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err?.message || 'Failed to load teams');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTeams();
  }, [fetchTeams]);

  const handleNewTeam = () => {
    setEditingTeam(null);
    setModalOpen(true);
  };

  const handleModalClose = () => {
    setModalOpen(false);
    setEditingTeam(null);
  };

  const handleModalSaved = () => {
    setModalOpen(false);
    setEditingTeam(null);
    fetchTeams();
  };

  const performDeleteTeam = async (teamId) => {
    try {
      const res = await api.delete(`${API_URL}/api/ai-dispatcher/teams/${teamId}`);
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data?.detail || 'Failed to delete team');
      }
      fetchTeams();
    } catch (err) {
      alert(err?.message || 'Failed to delete team');
    }
  };

  const handleDeleteTeam = (teamId, e) => {
    e.stopPropagation();
    setConfirmState({
      open: true,
      title: 'Delete team?',
      message: 'This team and its membership will be removed. This cannot be undone.',
      action: () => performDeleteTeam(teamId),
    });
  };

  if (selectedTeamId) {
    return (
      <TeamDetail
        teamId={selectedTeamId}
        onBack={() => {
          setSelectedTeamId(null);
          fetchTeams();
        }}
      />
    );
  }

  return (
    <div className="space-y-4">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-aegisaxiom-text">Dispatch Teams</h2>
          <p className="text-sm text-aegisaxiom-text-secondary mt-0.5">
            Manage round-robin and skill-based assignment pools.
          </p>
        </div>
        {canCreate && (
          <button
            onClick={handleNewTeam}
            className="btn-primary flex items-center gap-2"
          >
            <Plus size={16} />
            New Team
          </button>
        )}
      </div>

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {/* Error */}
      {!loading && error && (
        <div className="glass-card p-4 flex items-start gap-3">
          <AlertCircle size={18} className="text-red-400 shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-aegisaxiom-text">Failed to load teams</p>
            <p className="text-xs text-aegisaxiom-text-secondary mt-0.5">{error}</p>
            <button
              onClick={fetchTeams}
              className="text-xs text-violet-400 hover:text-violet-300 mt-2"
            >
              Try again
            </button>
          </div>
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && teams.length === 0 && (
        <div className="glass-card p-12 text-center">
          <Users size={40} className="mx-auto text-aegisaxiom-text-muted mb-3" />
          <p className="text-aegisaxiom-text font-medium">No teams yet</p>
          <p className="text-sm text-aegisaxiom-text-secondary mt-1">
            Create your first dispatch team to get started.
          </p>
          {canCreate && (
            <button onClick={handleNewTeam} className="btn-primary mt-4 flex items-center gap-2 mx-auto">
              <Plus size={15} />
              New Team
            </button>
          )}
        </div>
      )}

      {/* Teams table */}
      {!loading && !error && teams.length > 0 && (
        <div className="glass-card overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-aegisaxiom-border">
                <th className="text-left px-4 py-3 text-aegisaxiom-text-secondary font-medium">Team Name</th>
                <th className="text-left px-4 py-3 text-aegisaxiom-text-secondary font-medium">Department</th>
                <th className="text-left px-4 py-3 text-aegisaxiom-text-secondary font-medium">Mode</th>
                <th className="text-right px-4 py-3 text-aegisaxiom-text-secondary font-medium"># Members</th>
                <th className="text-right px-4 py-3 text-aegisaxiom-text-secondary font-medium"># On-duty</th>
                <th className="w-10" />
              </tr>
            </thead>
            <tbody>
              {teams.map((team) => (
                <tr
                  key={team.team_id}
                  onClick={() => setSelectedTeamId(team.team_id)}
                  className="border-b border-aegisaxiom-border hover:bg-aegisaxiom-elevated cursor-pointer transition-colors last:border-0"
                >
                  <td className="px-4 py-3">
                    <span className="font-medium text-aegisaxiom-text">{team.team_name}</span>
                  </td>
                  <td className="px-4 py-3 text-aegisaxiom-text-secondary">{team.department}</td>
                  <td className="px-4 py-3">
                    <ModeBadge mode={team.assignment_mode} />
                  </td>
                  <td className="px-4 py-3 text-right text-aegisaxiom-text-secondary">
                    {team.member_count ?? 0}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <span className="text-aegisaxiom-text-secondary">{team.on_duty_count ?? 0}</span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <ChevronRight size={16} className="text-aegisaxiom-text-muted ml-auto" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* New / Edit Team modal */}
      {modalOpen && (
        <TeamModal
          team={editingTeam}
          onClose={handleModalClose}
          onSaved={handleModalSaved}
        />
      )}

      <ConfirmModal
        isOpen={confirmState.open}
        title={confirmState.title}
        message={confirmState.message}
        confirmText="Delete"
        variant="danger"
        onConfirm={() => confirmState.action?.()}
        onCancel={closeConfirm}
      />
    </div>
  );
}

function ModeBadge({ mode }) {
  const label = ASSIGNMENT_MODE_LABELS[mode] || mode;
  const isSkill = mode === 'skill_based';
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
        isSkill
          ? 'bg-violet-500/15 text-violet-400'
          : 'bg-blue-500/15 text-blue-400'
      }`}
    >
      {label}
    </span>
  );
}

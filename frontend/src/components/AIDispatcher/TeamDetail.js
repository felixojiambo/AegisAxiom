import React, { useEffect, useState, useCallback, useRef } from 'react';
import { ArrowLeft, Plus, AlertCircle, Pencil } from 'lucide-react';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';
import { ASSIGNMENT_MODE_LABELS } from './constants';
import MemberRow from './MemberRow';
import TeamModal from './TeamModal';
import ConfirmModal from '../shared/ConfirmModal';

export default function TeamDetail({ teamId, onBack }) {
  const { hasPermission } = usePermissions();
  const canUpdateTeam = hasPermission('ai_dispatcher.teams.update');
  const canCreateMember = hasPermission('ai_dispatcher.members.create');
  const canUpdateMember = hasPermission('ai_dispatcher.members.update');
  const canDeleteMember = hasPermission('ai_dispatcher.members.delete');

  const [team, setTeam] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Edit team modal
  const [editModalOpen, setEditModalOpen] = useState(false);

  // Add member row state
  const [newMemberName, setNewMemberName] = useState('');
  const [addingMember, setAddingMember] = useState(false);

  // Drag state
  const dragIndex = useRef(null);
  const [draggingMemberId, setDraggingMemberId] = useState(null);

  // Confirm-delete modal state
  const [confirmState, setConfirmState] = useState({
    open: false,
    title: '',
    message: '',
    action: null,
  });
  const closeConfirm = () =>
    setConfirmState({ open: false, title: '', message: '', action: null });

  const fetchTeam = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get(`${API_URL}/api/ai-dispatcher/teams/${teamId}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || 'Failed to load team');
      // Sort members by order_index for round-robin display
      if (data.members) {
        data.members = [...data.members].sort((a, b) => a.order_index - b.order_index);
      }
      setTeam(data);
    } catch (err) {
      setError(err?.message || 'Failed to load team');
    } finally {
      setLoading(false);
    }
  }, [teamId]);

  useEffect(() => {
    fetchTeam();
  }, [fetchTeam]);

  const handleUpdateMember = useCallback(async (memberId, patch) => {
    if (!canUpdateMember) return;
    try {
      const res = await api.patch(
        `${API_URL}/api/ai-dispatcher/teams/${teamId}/members/${memberId}`,
        patch
      );
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || 'Failed to update member');
      // Optimistic local update
      setTeam((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          members: prev.members.map((m) =>
            m.member_id === memberId ? { ...m, ...patch } : m
          ),
        };
      });
    } catch (err) {
      alert(err?.message || 'Failed to update member');
      fetchTeam(); // revert on error
    }
  }, [teamId, canUpdateMember, fetchTeam]);

  const performRemoveMember = useCallback(async (memberId) => {
    try {
      const res = await api.delete(`${API_URL}/api/ai-dispatcher/teams/${teamId}/members/${memberId}`);
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data?.detail || 'Failed to remove member');
      }
      setTeam((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          members: prev.members.filter((m) => m.member_id !== memberId),
        };
      });
    } catch (err) {
      alert(err?.message || 'Failed to remove member');
    }
  }, [teamId]);

  const handleRemoveMember = useCallback((memberId) => {
    if (!canDeleteMember) return;
    const target = team?.members?.find((m) => m.member_id === memberId);
    const name = target?.name || 'this member';
    setConfirmState({
      open: true,
      title: 'Remove member?',
      message: `${name} will be removed from the team. This cannot be undone.`,
      action: () => performRemoveMember(memberId),
    });
  }, [canDeleteMember, team, performRemoveMember]);

  const handleAddMember = async () => {
    const name = newMemberName.trim();
    if (!name) return;
    setAddingMember(true);
    try {
      const nextOrder = team?.members?.length ?? 0;
      const res = await api.post(`${API_URL}/api/ai-dispatcher/teams/${teamId}/members`, {
        name,
        availability: 'on_duty',
        order_index: nextOrder,
      });
      const newMember = await res.json();
      if (!res.ok) throw new Error(newMember?.detail || 'Failed to add member');
      setTeam((prev) => {
        if (!prev) return prev;
        return { ...prev, members: [...prev.members, newMember] };
      });
      setNewMemberName('');
    } catch (err) {
      alert(err?.message || 'Failed to add member');
    } finally {
      setAddingMember(false);
    }
  };

  // ── Drag-to-reorder (round-robin only) ──
  // Native HTML5 drag events — no extra library needed.

  const handleDragStart = useCallback((index, memberId) => {
    dragIndex.current = index;
    setDraggingMemberId(memberId);
  }, []);

  const handleDragOver = useCallback((e) => {
    e.preventDefault(); // required to allow drop
  }, []);

  const handleDrop = useCallback(async (dropIndex) => {
    const from = dragIndex.current;
    if (from === null || from === dropIndex) return;

    setTeam((prev) => {
      if (!prev) return prev;
      const members = [...prev.members];
      const [moved] = members.splice(from, 1);
      members.splice(dropIndex, 0, moved);
      // Reassign order_index contiguously
      const updated = members.map((m, i) => ({ ...m, order_index: i }));

      // Fire-and-forget PATCH for each member that changed order
      updated.forEach((m) => {
        const original = prev.members.find((om) => om.member_id === m.member_id);
        if (original && original.order_index !== m.order_index) {
          api
            .patch(`${API_URL}/api/ai-dispatcher/teams/${teamId}/members/${m.member_id}`, {
              order_index: m.order_index,
            })
            .catch(() => {});
        }
      });

      return { ...prev, members: updated };
    });
  }, [teamId]);

  const handleDragEnd = useCallback(() => {
    dragIndex.current = null;
    setDraggingMemberId(null);
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="w-8 h-8 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-4">
        <button onClick={onBack} className="flex items-center gap-2 text-sm text-aegisaxiom-text-secondary hover:text-aegisaxiom-text">
          <ArrowLeft size={16} /> Back to teams
        </button>
        <div className="glass-card p-4 flex items-start gap-3">
          <AlertCircle size={18} className="text-red-400 shrink-0 mt-0.5" />
          <p className="text-sm text-aegisaxiom-text">{error}</p>
        </div>
      </div>
    );
  }

  if (!team) return null;

  const modeLabel = ASSIGNMENT_MODE_LABELS[team.assignment_mode] || team.assignment_mode;
  const members = team.members ?? [];

  return (
    <div className="space-y-5">
      {/* Back + header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <button
            onClick={onBack}
            className="flex items-center gap-1.5 text-sm text-aegisaxiom-text-secondary hover:text-aegisaxiom-text mb-3 transition-colors"
          >
            <ArrowLeft size={15} />
            Back to teams
          </button>
          <h2 className="text-xl font-semibold text-aegisaxiom-text">{team.team_name}</h2>
          <div className="flex items-center gap-3 mt-1.5">
            <span className="text-sm text-aegisaxiom-text-secondary">{team.department}</span>
            <ModeBadge mode={team.assignment_mode} />
          </div>
        </div>
        {canUpdateTeam && (
          <button
            onClick={() => setEditModalOpen(true)}
            className="btn-secondary flex items-center gap-2 shrink-0"
          >
            <Pencil size={14} />
            Edit Team
          </button>
        )}
      </div>

      {/* Members section */}
      <div className="glass-card p-5 space-y-3">
        <div className="flex items-center justify-between mb-1">
          <h3 className="text-sm font-semibold text-aegisaxiom-text">
            Members
            <span className="ml-2 text-aegisaxiom-text-muted font-normal">({members.length})</span>
          </h3>
          {team.assignment_mode === 'round_robin' && members.length > 1 && (
            <p className="text-xs text-aegisaxiom-text-muted">Drag rows to reorder</p>
          )}
        </div>

        {/* Mode hint */}
        {team.assignment_mode === 'round_robin' ? (
          <p className="text-xs text-aegisaxiom-text-muted">
            Members are assigned tickets in rotation by order index.
          </p>
        ) : (
          <p className="text-xs text-aegisaxiom-text-muted">
            The AI reads each member's skill description to choose the best match for each ticket.
          </p>
        )}

        {/* Member rows */}
        {members.length === 0 && (
          <p className="text-sm text-aegisaxiom-text-secondary py-4 text-center">
            No members yet. Add the first one below.
          </p>
        )}

        <div className="space-y-2">
          {members.map((member, index) => (
            <MemberRow
              key={member.member_id}
              member={member}
              mode={team.assignment_mode}
              onUpdate={handleUpdateMember}
              onRemove={handleRemoveMember}
              isDragging={draggingMemberId === member.member_id}
              onDragStart={() => handleDragStart(index, member.member_id)}
              onDragOver={handleDragOver}
              onDrop={() => handleDrop(index)}
              onDragEnd={handleDragEnd}
            />
          ))}
        </div>

        {/* Add member row */}
        {canCreateMember && (
          <div className="flex items-center gap-2 pt-2 border-t border-aegisaxiom-border">
            <input
              className="aegisaxiom-input flex-1"
              placeholder="Member name"
              value={newMemberName}
              onChange={(e) => setNewMemberName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  handleAddMember();
                }
              }}
              aria-label="New member name"
            />
            <button
              onClick={handleAddMember}
              disabled={addingMember || !newMemberName.trim()}
              className="btn-primary flex items-center gap-1.5 shrink-0"
            >
              <Plus size={15} />
              {addingMember ? 'Adding…' : 'Add'}
            </button>
          </div>
        )}
      </div>

      {/* Edit team modal */}
      {editModalOpen && (
        <TeamModal
          team={team}
          onClose={() => setEditModalOpen(false)}
          onSaved={() => {
            setEditModalOpen(false);
            fetchTeam();
          }}
        />
      )}

      <ConfirmModal
        isOpen={confirmState.open}
        title={confirmState.title}
        message={confirmState.message}
        confirmText="Remove"
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

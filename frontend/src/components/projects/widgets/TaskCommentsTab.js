import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Edit2, Eye, MessageSquare, Send, Trash2, X } from 'lucide-react';
import { formatDistanceToNow, parseISO } from 'date-fns';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { toast } from 'sonner';
import { api, unwrap } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { extractTaskErrorMessage } from '@/lib/safeXhr';
import { useAuth } from '@/contexts/AuthContext';
import { usePermissions } from '@/hooks/usePermissions';
import ConfirmModal from '@/components/shared/ConfirmModal';

/**
 * TaskCommentsTab — threaded comments inside the task drawer.
 *
 * Wave 6 / Thread D — Contract 7. Backend at /api/projects/tasks/{id}/comments.
 *
 * Layout:
 *  - composer at the top (markdown textarea + preview toggle)
 *  - list below in newest-first order
 *  - each comment: author + relative timestamp + edited badge + body
 *  - edit/delete affordances gated on perms + ownership
 */
export default function TaskCommentsTab({ task, assignees, onChanged }) {
  const { user } = useAuth();
  const { hasPermission } = usePermissions();
  const taskId = task?.task_id ?? task?._id;

  const canView = hasPermission('projects.task.comment.view');
  const canCreate = hasPermission('projects.task.comment.create');
  const canEditAny = hasPermission('projects.task.comment.edit');
  const canDeleteAny = hasPermission('projects.task.comment.delete');
  const isAdmin = hasPermission('*');

  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState('');
  const [previewing, setPreviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [editingBody, setEditingBody] = useState('');
  const [editingPreview, setEditingPreview] = useState(false);
  const [pendingDeleteId, setPendingDeleteId] = useState(null);

  // Build a quick lookup so we can render names/avatars from the assignee
  // list the parent already loaded. Falls back to author_id if missing.
  const userMap = useMemo(() => {
    const m = new Map();
    (assignees ?? []).forEach((u) => {
      const uid = u.user_id ?? u._id;
      if (uid) m.set(uid, u);
    });
    return m;
  }, [assignees]);

  const fetchComments = useCallback(async () => {
    if (!taskId) return;
    setLoading(true);
    try {
      const res = await api.get(
        `${API_URL}/api/projects/tasks/${taskId}/comments?limit=200`,
      );
      if (!res.ok) {
        setItems([]);
        return;
      }
      const data = await res.json();
      setItems(unwrap(data));
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    fetchComments();
  }, [fetchComments]);

  const handleSubmit = async () => {
    const body = draft.trim();
    if (!body) {
      toast.error('Write something before posting.');
      return;
    }
    setSubmitting(true);
    try {
      const res = await api.post(
        `${API_URL}/api/projects/tasks/${taskId}/comments`,
        { body },
      );
      if (!res.ok) {
        const msg = await extractTaskErrorMessage(res, 'Could not post comment.');
        toast.error(msg);
        return;
      }
      setDraft('');
      setPreviewing(false);
      toast.success('Comment posted');
      await fetchComments();
      onChanged?.();
    } catch {
      toast.error('Could not post comment.');
    } finally {
      setSubmitting(false);
    }
  };

  const startEdit = (comment) => {
    setEditingId(comment.comment_id);
    setEditingBody(comment.body ?? '');
    setEditingPreview(false);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditingBody('');
    setEditingPreview(false);
  };

  const submitEdit = async () => {
    if (!editingId) return;
    const body = editingBody.trim();
    if (!body) {
      toast.error('Comment cannot be empty.');
      return;
    }
    try {
      const res = await api.patch(
        `${API_URL}/api/projects/tasks/comments/${editingId}`,
        { body },
      );
      if (!res.ok) {
        const msg = await extractTaskErrorMessage(res, 'Could not update comment.');
        toast.error(msg);
        return;
      }
      cancelEdit();
      toast.success('Comment updated');
      await fetchComments();
      onChanged?.();
    } catch {
      toast.error('Could not update comment.');
    }
  };

  const performDelete = async () => {
    if (!pendingDeleteId) return;
    try {
      const res = await api.delete(
        `${API_URL}/api/projects/tasks/comments/${pendingDeleteId}`,
      );
      if (!res.ok) {
        const msg = await extractTaskErrorMessage(res, 'Could not delete comment.');
        toast.error(msg);
        return;
      }
      toast.success('Comment deleted');
      await fetchComments();
      onChanged?.();
    } catch {
      toast.error('Could not delete comment.');
    } finally {
      setPendingDeleteId(null);
    }
  };

  if (!canView) {
    return (
      <div className="px-1 py-6 text-center text-xs text-aegisaxiom-text-muted">
        You do not have permission to view comments.
      </div>
    );
  }

  return (
    <div className="space-y-4" data-testid="task-comments-tab">
      {/* Composer */}
      {canCreate ? (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label
              htmlFor={`comment-draft-${taskId}`}
              className="text-[11px] font-medium text-aegisaxiom-text-muted"
            >
              Add a comment{' '}
              <span className="text-aegisaxiom-text-muted/70">(markdown supported)</span>
            </label>
            <button
              type="button"
              onClick={() => setPreviewing((v) => !v)}
              className="h-6 px-2 inline-flex items-center gap-1 text-[10px] text-aegisaxiom-text-muted hover:text-aegisaxiom-text border border-aegisaxiom-border rounded"
              data-testid="comment-preview-toggle"
            >
              <Eye size={10} /> {previewing ? 'Edit' : 'Preview'}
            </button>
          </div>
          {previewing ? (
            <MarkdownBody body={draft || '_Nothing to preview yet._'} />
          ) : (
            <textarea
              id={`comment-draft-${taskId}`}
              rows={3}
              className="aegisaxiom-input w-full resize-y text-sm"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Add an update, a question, or a note…"
              data-testid="comment-draft"
            />
          )}
          <div className="flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={handleSubmit}
              disabled={submitting || !draft.trim()}
              className="btn-primary h-8 px-3 inline-flex items-center gap-1.5 text-xs disabled:opacity-60"
              data-testid="comment-submit"
            >
              <Send size={12} /> {submitting ? 'Posting…' : 'Post comment'}
            </button>
          </div>
        </div>
      ) : null}

      {/* Divider */}
      <div className="border-t border-aegisaxiom-border" />

      {/* List */}
      {loading ? (
        <div className="py-6 flex items-center justify-center text-xs text-aegisaxiom-text-muted">
          Loading comments…
        </div>
      ) : items.length === 0 ? (
        <div
          className="py-8 flex flex-col items-center justify-center text-center"
          data-testid="comments-empty"
        >
          <MessageSquare size={24} className="text-aegisaxiom-text-muted/60 mb-2" />
          <p className="text-xs text-aegisaxiom-text-muted">
            No comments yet. Be the first to start the discussion.
          </p>
        </div>
      ) : (
        <ul className="space-y-3" data-testid="comments-list">
          {items.map((c) => {
            const isEditing = editingId === c.comment_id;
            const author = userMap.get(c.author_id);
            const authorName =
              author?.name ?? author?.display_name ?? c.author_id ?? 'Unknown';
            const isMine = c.author_id === user?.user_id;
            const canEditThis = isMine; // Backend enforces author-only edit.
            const canDeleteThis = isMine || canDeleteAny || isAdmin;
            return (
              <li
                key={c.comment_id}
                className="rounded-lg border border-aegisaxiom-border bg-aegisaxiom-elevated/40 p-3 space-y-2"
                data-testid={`comment-row-${c.comment_id}`}
              >
                <header className="flex items-center gap-2 text-[11px] text-aegisaxiom-text-muted">
                  <span className="font-semibold text-aegisaxiom-text">
                    {authorName}
                  </span>
                  <span>·</span>
                  <span title={c.created_at}>{relativeTime(c.created_at)}</span>
                  {c.edited ? (
                    <span
                      className="inline-flex items-center h-4 px-1.5 rounded bg-aegisaxiom-elevated text-aegisaxiom-text-muted text-[9px] uppercase tracking-wider"
                      title={c.updated_at ? `Edited ${relativeTime(c.updated_at)}` : undefined}
                      data-testid={`comment-edited-${c.comment_id}`}
                    >
                      edited
                    </span>
                  ) : null}
                  <div className="ml-auto flex items-center gap-1">
                    {!isEditing && canEditThis && canEditAny ? (
                      <button
                        type="button"
                        onClick={() => startEdit(c)}
                        className="h-5 w-5 inline-flex items-center justify-center rounded text-aegisaxiom-text-muted hover:text-aegisaxiom-text"
                        aria-label="Edit comment"
                        data-testid={`comment-edit-${c.comment_id}`}
                      >
                        <Edit2 size={10} />
                      </button>
                    ) : null}
                    {!isEditing && canDeleteThis ? (
                      <button
                        type="button"
                        onClick={() => setPendingDeleteId(c.comment_id)}
                        className="h-5 w-5 inline-flex items-center justify-center rounded text-aegisaxiom-text-muted hover:text-red-400"
                        aria-label="Delete comment"
                        data-testid={`comment-delete-${c.comment_id}`}
                      >
                        <Trash2 size={10} />
                      </button>
                    ) : null}
                  </div>
                </header>

                {isEditing ? (
                  <div className="space-y-2">
                    <div className="flex items-center justify-end">
                      <button
                        type="button"
                        onClick={() => setEditingPreview((v) => !v)}
                        className="h-6 px-2 inline-flex items-center gap-1 text-[10px] text-aegisaxiom-text-muted hover:text-aegisaxiom-text border border-aegisaxiom-border rounded"
                        data-testid={`comment-edit-preview-${c.comment_id}`}
                      >
                        <Eye size={10} /> {editingPreview ? 'Edit' : 'Preview'}
                      </button>
                    </div>
                    {editingPreview ? (
                      <MarkdownBody body={editingBody || '_Nothing to preview._'} />
                    ) : (
                      <textarea
                        rows={3}
                        className="aegisaxiom-input w-full resize-y text-sm"
                        value={editingBody}
                        onChange={(e) => setEditingBody(e.target.value)}
                        data-testid={`comment-edit-textarea-${c.comment_id}`}
                      />
                    )}
                    <div className="flex items-center justify-end gap-2">
                      <button
                        type="button"
                        onClick={cancelEdit}
                        className="h-7 px-2.5 inline-flex items-center gap-1 text-xs text-aegisaxiom-text-muted hover:text-aegisaxiom-text border border-aegisaxiom-border rounded"
                        data-testid={`comment-edit-cancel-${c.comment_id}`}
                      >
                        <X size={11} /> Cancel
                      </button>
                      <button
                        type="button"
                        onClick={submitEdit}
                        className="btn-primary h-7 px-3 inline-flex items-center gap-1 text-xs"
                        data-testid={`comment-edit-save-${c.comment_id}`}
                      >
                        Save
                      </button>
                    </div>
                  </div>
                ) : (
                  <MarkdownBody body={c.body} />
                )}
              </li>
            );
          })}
        </ul>
      )}

      <ConfirmModal
        isOpen={!!pendingDeleteId}
        title="Delete comment"
        message="Delete this comment? This cannot be undone."
        confirmText="Delete"
        variant="danger"
        onConfirm={performDelete}
        onCancel={() => setPendingDeleteId(null)}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function relativeTime(iso) {
  if (!iso) return '—';
  try {
    return formatDistanceToNow(parseISO(iso), { addSuffix: true });
  } catch {
    return iso;
  }
}

function MarkdownBody({ body }) {
  return (
    <div
      className="prose prose-sm max-w-none text-aegisaxiom-text text-xs
                 prose-headings:text-aegisaxiom-text prose-p:text-aegisaxiom-text prose-li:text-aegisaxiom-text
                 prose-strong:text-aegisaxiom-text prose-code:text-violet-400
                 prose-a:text-violet-400 hover:prose-a:text-violet-300
                 prose-ul:my-1 prose-li:my-0 prose-p:my-1"
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{body || ''}</ReactMarkdown>
    </div>
  );
}

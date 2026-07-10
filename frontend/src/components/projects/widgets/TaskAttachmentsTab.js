import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Download, FileText, Image as ImageIcon, Paperclip, Trash2, Upload,
  FileArchive, FileType,
} from 'lucide-react';
import { formatDistanceToNow, parseISO } from 'date-fns';
import { toast } from 'sonner';
import { api, apiDownload, unwrap } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { extractTaskErrorMessage } from '@/lib/safeXhr';
import { useAuth } from '@/contexts/AuthContext';
import { usePermissions } from '@/hooks/usePermissions';
import ConfirmModal from '@/components/shared/ConfirmModal';

const MAX_BYTES = 25 * 1024 * 1024; // 25 MB; mirrors backend cap.
const ALLOWED_PREFIXES = ['image/', 'text/'];
const ALLOWED_EXACT = new Set([
  'application/pdf',
  'application/zip',
  'application/x-zip-compressed',
]);

/**
 * TaskAttachmentsTab — file attachments inside the task drawer.
 *
 * Wave 6 / Thread D — Contract 8. Backend at /api/projects/tasks/{id}/attachments.
 *
 * Native HTML5 drag-drop dropzone (no react-dropzone dep). Renders the
 * existing list with download + delete buttons gated on perms + ownership.
 */
export default function TaskAttachmentsTab({ task, assignees, onChanged }) {
  const { user } = useAuth();
  const { hasPermission } = usePermissions();
  const taskId = task?.task_id ?? task?._id;

  const canView = hasPermission('projects.task.attachment.view');
  const canCreate = hasPermission('projects.task.attachment.create');
  const canDeleteAny = hasPermission('projects.task.attachment.delete');
  const isAdmin = hasPermission('*');

  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [pendingDeleteId, setPendingDeleteId] = useState(null);
  const fileInputRef = useRef(null);

  const userMap = useMemo(() => {
    const m = new Map();
    (assignees ?? []).forEach((u) => {
      const uid = u.user_id ?? u._id;
      if (uid) m.set(uid, u);
    });
    return m;
  }, [assignees]);

  const fetchAttachments = useCallback(async () => {
    if (!taskId) return;
    setLoading(true);
    try {
      const res = await api.get(
        `${API_URL}/api/projects/tasks/${taskId}/attachments`,
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
    fetchAttachments();
  }, [fetchAttachments]);

  const validateFile = (file) => {
    if (file.size === 0) {
      toast.error('Cannot upload an empty file.');
      return false;
    }
    if (file.size > MAX_BYTES) {
      toast.error(
        `File is too large (${(file.size / 1024 / 1024).toFixed(1)} MB). Max 25 MB.`,
      );
      return false;
    }
    const ct = (file.type || '').toLowerCase();
    const allowed =
      ALLOWED_PREFIXES.some((p) => ct.startsWith(p)) || ALLOWED_EXACT.has(ct);
    if (!allowed) {
      toast.error(
        'Unsupported file type. Allowed: images, PDFs, text files, ZIP archives.',
      );
      return false;
    }
    return true;
  };

  const uploadFile = async (file) => {
    if (!canCreate) return;
    if (!validateFile(file)) return;
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await api.postForm(
        `${API_URL}/api/projects/tasks/${taskId}/attachments`,
        formData,
      );
      if (!res.ok) {
        const msg = await extractTaskErrorMessage(res, 'Could not upload file.');
        toast.error(msg);
        return;
      }
      toast.success(`Uploaded ${file.name}`);
      await fetchAttachments();
      onChanged?.();
    } catch {
      toast.error('Could not upload file.');
    } finally {
      setUploading(false);
    }
  };

  const handleFiles = async (fileList) => {
    if (!fileList || fileList.length === 0) return;
    // Sequential upload — keeps the toast cadence sane for 3-5 file batches.
    for (const file of Array.from(fileList)) {
      // eslint-disable-next-line no-await-in-loop
      await uploadFile(file);
    }
  };

  const onInputChange = (e) => {
    handleFiles(e.target.files);
    // Reset so the same file can be re-uploaded if needed.
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const onDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (canCreate && !uploading) setDragActive(true);
  };

  const onDragLeave = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
  };

  const onDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (!canCreate) {
      toast.error('You do not have permission to upload attachments.');
      return;
    }
    handleFiles(e.dataTransfer?.files);
  };

  const handleDownload = async (attachment) => {
    const url = `${API_URL}/api/projects/tasks/attachments/${attachment.attachment_id}/download`;
    try {
      const blob = await apiDownload(url);
      if (!blob) {
        toast.error('Could not download file.');
        return;
      }
      // Trigger a synthetic anchor click so the browser saves the file.
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objectUrl;
      a.download = attachment.filename || 'attachment';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      // Defer revoke to next tick — Safari clears the download otherwise.
      setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
    } catch {
      toast.error('Could not download file.');
    }
  };

  const performDelete = async () => {
    if (!pendingDeleteId) return;
    try {
      const res = await api.delete(
        `${API_URL}/api/projects/tasks/attachments/${pendingDeleteId}`,
      );
      if (!res.ok) {
        const msg = await extractTaskErrorMessage(res, 'Could not delete attachment.');
        toast.error(msg);
        return;
      }
      toast.success('Attachment deleted');
      await fetchAttachments();
      onChanged?.();
    } catch {
      toast.error('Could not delete attachment.');
    } finally {
      setPendingDeleteId(null);
    }
  };

  if (!canView) {
    return (
      <div className="px-1 py-6 text-center text-xs text-aegisaxiom-text-muted">
        You do not have permission to view attachments.
      </div>
    );
  }

  return (
    <div className="space-y-3" data-testid="task-attachments-tab">
      {/* Dropzone */}
      {canCreate ? (
        <div
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          className={`relative rounded-lg border-2 border-dashed p-4 text-center transition-colors cursor-pointer ${
            dragActive
              ? 'border-violet-400 bg-violet-500/5'
              : 'border-aegisaxiom-border bg-aegisaxiom-elevated/30 hover:bg-aegisaxiom-elevated/50'
          } ${uploading ? 'opacity-60 pointer-events-none' : ''}`}
          onClick={() => fileInputRef.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') fileInputRef.current?.click();
          }}
          data-testid="attachment-dropzone"
        >
          <Upload size={20} className="mx-auto mb-2 text-aegisaxiom-text-muted" />
          <p className="text-xs text-aegisaxiom-text mb-1">
            {uploading
              ? 'Uploading…'
              : dragActive
              ? 'Drop file to upload'
              : 'Drag & drop a file, or click to browse'}
          </p>
          <p className="text-[10px] text-aegisaxiom-text-muted">
            Images, PDFs, text, ZIP · max 25 MB
          </p>
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            multiple
            onChange={onInputChange}
            data-testid="attachment-input"
            accept="image/*,application/pdf,text/*,application/zip"
          />
        </div>
      ) : null}

      {/* List */}
      {loading ? (
        <div className="py-6 flex items-center justify-center text-xs text-aegisaxiom-text-muted">
          Loading attachments…
        </div>
      ) : items.length === 0 ? (
        <div
          className="py-8 flex flex-col items-center justify-center text-center"
          data-testid="attachments-empty"
        >
          <Paperclip size={20} className="text-aegisaxiom-text-muted/60 mb-2" />
          <p className="text-xs text-aegisaxiom-text-muted">
            No attachments yet.
          </p>
        </div>
      ) : (
        <ul className="space-y-2" data-testid="attachments-list">
          {items.map((a) => {
            const uploader = userMap.get(a.uploaded_by);
            const uploaderName =
              uploader?.name ?? uploader?.display_name ?? a.uploaded_by ?? 'Unknown';
            const isMine = a.uploaded_by === user?.user_id;
            const canDeleteThis = isMine || canDeleteAny || isAdmin;
            const Icon = iconForType(a.content_type, a.filename);
            return (
              <li
                key={a.attachment_id}
                className="rounded-lg border border-aegisaxiom-border bg-aegisaxiom-elevated/40 p-2.5 flex items-center gap-3"
                data-testid={`attachment-row-${a.attachment_id}`}
              >
                <Icon size={20} className="text-aegisaxiom-text-muted shrink-0" />
                <div className="flex-1 min-w-0">
                  <p
                    className="text-xs font-medium text-aegisaxiom-text truncate"
                    title={a.filename}
                  >
                    {a.filename}
                  </p>
                  <p className="text-[10px] text-aegisaxiom-text-muted">
                    {humanSize(a.size_bytes)} · {uploaderName} ·{' '}
                    {relativeTime(a.uploaded_at)}
                  </p>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    type="button"
                    onClick={() => handleDownload(a)}
                    className="h-7 w-7 inline-flex items-center justify-center rounded text-aegisaxiom-text-muted hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated"
                    aria-label={`Download ${a.filename}`}
                    data-testid={`attachment-download-${a.attachment_id}`}
                  >
                    <Download size={12} />
                  </button>
                  {canDeleteThis ? (
                    <button
                      type="button"
                      onClick={() => setPendingDeleteId(a.attachment_id)}
                      className="h-7 w-7 inline-flex items-center justify-center rounded text-aegisaxiom-text-muted hover:text-red-400 hover:bg-red-500/10"
                      aria-label={`Delete ${a.filename}`}
                      data-testid={`attachment-delete-${a.attachment_id}`}
                    >
                      <Trash2 size={12} />
                    </button>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <ConfirmModal
        isOpen={!!pendingDeleteId}
        title="Delete attachment"
        message="Delete this attachment? This cannot be undone."
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

function iconForType(contentType, filename) {
  const ct = (contentType || '').toLowerCase();
  if (ct.startsWith('image/')) return ImageIcon;
  if (ct === 'application/pdf') return FileType;
  if (ct === 'application/zip' || ct === 'application/x-zip-compressed') {
    return FileArchive;
  }
  if (ct.startsWith('text/')) return FileText;
  // Fallback by extension if mime is generic.
  const ext = (filename || '').split('.').pop()?.toLowerCase();
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)) return ImageIcon;
  if (ext === 'pdf') return FileType;
  if (ext === 'zip') return FileArchive;
  return Paperclip;
}

function humanSize(bytes) {
  if (bytes == null) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function relativeTime(iso) {
  if (!iso) return '—';
  try {
    return formatDistanceToNow(parseISO(iso), { addSuffix: true });
  } catch {
    return iso;
  }
}

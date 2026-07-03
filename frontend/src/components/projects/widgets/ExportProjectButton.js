import React, { useState } from 'react';
import { Download } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '@/lib/apiConfig';
import { usePermissions } from '@/hooks/usePermissions';

// Wave 6 / Thread C — Contract 6
// Mounts in ProjectDetailPage header. Calls /api/projects/projects/{id}/export
// and triggers a browser download. Gated on `projects.task.view` per the
// contract — viewing tasks is the same right needed to export them.

export default function ExportProjectButton({ project }) {
  const { hasPermission } = usePermissions();
  const canExport = hasPermission('projects.task.view');
  const [downloading, setDownloading] = useState(false);

  if (!canExport || !project) return null;

  const handleClick = async () => {
    setDownloading(true);
    try {
      const projectId = project.project_id ?? project._id;
      const blob = await api.download(
        `${API_URL}/api/projects/projects/${projectId}/export?format=csv`
      );
      if (!blob) {
        toast.error('Could not export project.');
        return;
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const safeName = (project.name ?? 'project').replace(/[^A-Za-z0-9_-]+/g, '_').slice(0, 80);
      a.download = `${safeName || 'project'}_tasks.csv`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      toast.success('Export downloaded');
    } catch {
      toast.error('Could not export project.');
    } finally {
      setDownloading(false);
    }
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={downloading}
      className="btn-secondary inline-flex items-center gap-1.5 text-xs shrink-0 disabled:opacity-50"
      data-testid="project-export-button"
      aria-label="Export project tasks to CSV"
    >
      <Download size={12} />
      {downloading ? 'Exporting…' : 'Export'}
    </button>
  );
}

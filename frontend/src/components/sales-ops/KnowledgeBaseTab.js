import React, { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '../../lib/apiConfig';
import {
  BookOpen,
  Upload,
  Trash2,
  Search,
  FileText,
  Calendar,
  User,
  Layers,
  AlertTriangle,
  X,
  Eye,
} from 'lucide-react';

const API = API_URL;

export default function KnowledgeBaseTab() {
  const [guides, setGuides] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [uploading, setUploading] = useState(false);
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [uploadTitle, setUploadTitle] = useState('');
  const [selectedFile, setSelectedFile] = useState(null);
  const [deleteConfirm, setDeleteConfirm] = useState(null);
  const [previewGuide, setPreviewGuide] = useState(null);
  const [previewChunks, setPreviewChunks] = useState([]);
  const [loadingPreview, setLoadingPreview] = useState(false);

  useEffect(() => {
    loadGuides();
  }, []);

  const loadGuides = async () => {
    try {
      setLoading(true);
      const response = await api.get(`${API}/api/sales-ops/guides`);
      if (response.ok) {
        const data = await response.json();
        setGuides(data.items || []);
      } else {
        toast.error('Failed to load knowledge base');
      }
    } catch (error) {
      console.error('Error loading guides:', error);
      toast.error('Failed to connect to knowledge base');
    } finally {
      setLoading(false);
    }
  };

  const handleUpload = async () => {
    if (!selectedFile) {
      toast.error('Please select a file');
      return;
    }

    const formData = new FormData();
    formData.append('file', selectedFile);
    if (uploadTitle.trim()) {
      formData.append('title', uploadTitle.trim());
    }

    try {
      setUploading(true);
      const response = await api.postForm(`${API}/api/sales-ops/guides/upload`, formData);
      if (response.ok) {
        const data = await response.json();
        toast.success(`"${data.title}" uploaded successfully`, {
          description: `${data.chunk_count} section${data.chunk_count !== 1 ? 's' : ''} indexed for semantic search`,
        });
        setShowUploadModal(false);
        setSelectedFile(null);
        setUploadTitle('');
        await loadGuides();
      } else {
        const err = await response.json();
        toast.error(err.detail || 'Failed to upload guide');
      }
    } catch (error) {
      console.error('Error uploading guide:', error);
      toast.error('Failed to upload guide');
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (guideId) => {
    try {
      const response = await api.delete(`${API}/api/sales-ops/guides/${guideId}`);
      if (response.ok) {
        toast.success('Guide deleted');
        setGuides((prev) => prev.filter((g) => g.guide_id !== guideId));
      } else {
        const err = await response.json();
        toast.error(err.detail || 'Failed to delete guide');
      }
    } catch (error) {
      toast.error('Failed to delete guide');
    } finally {
      setDeleteConfirm(null);
    }
  };

  const handlePreview = async (guide) => {
    setPreviewGuide(guide);
    setLoadingPreview(true);
    try {
      // Search with an empty-ish query to get all chunks for this guide
      const response = await api.post(`${API}/api/sales-ops/fireflies/validate-transcripts`, {
        transcript_ids: [],
        guide_id: guide.guide_id,
      });
      // This will fail since no transcripts, but we just want to show the guide exists
      // Instead, let's just show the metadata
      setPreviewChunks([]);
    } catch {
      setPreviewChunks([]);
    } finally {
      setLoadingPreview(false);
    }
  };

  const filteredGuides = guides.filter((g) => {
    if (!searchQuery) return true;
    const search = searchQuery.toLowerCase();
    return g.title?.toLowerCase().includes(search) || g.filename?.toLowerCase().includes(search);
  });

  const formatDate = (isoString) => {
    if (!isoString) return '';
    return new Date(isoString).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const getFileIcon = (filename) => {
    const ext = (filename || '').split('.').pop()?.toLowerCase();
    if (ext === 'pdf') return '📄';
    if (ext === 'docx' || ext === 'doc') return '📝';
    if (ext === 'txt') return '📃';
    return '📎';
  };

  return (
    <div className="space-y-5 sm:space-y-6">
      {/* Subtitle + action (no H2 — parent shell already owns the title) */}
      <div className="flex items-start sm:items-center justify-between gap-3 flex-col sm:flex-row">
        <p className="text-sm text-aegisaxiom-text-secondary">
          Best-practice guides used to validate meeting transcripts before SOW generation
        </p>
        <button
          onClick={() => setShowUploadModal(true)}
          className="flex items-center gap-2 px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded-lg transition shrink-0 w-full sm:w-auto justify-center"
        >
          <Upload className="w-4 h-4" />
          Upload Guide
        </button>
      </div>

      {/* Search */}
      {guides.length > 0 && (
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-aegisaxiom-text-secondary" />
          <input
            type="text"
            placeholder="Search guides by title or filename..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 text-sm"
          />
        </div>
      )}

      {/* Guides List */}
      {loading ? (
        <div className="flex items-center justify-center py-16">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-violet-500"></div>
        </div>
      ) : filteredGuides.length === 0 ? (
        <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-12 text-center">
          <BookOpen className="w-16 h-16 text-aegisaxiom-text-muted mx-auto mb-4" />
          <h3 className="text-aegisaxiom-text font-semibold mb-2">
            {searchQuery ? 'No guides match your search' : 'No Guides Yet'}
          </h3>
          <p className="text-aegisaxiom-text-secondary text-sm mb-6 max-w-md mx-auto">
            {searchQuery
              ? 'Try a different search term'
              : 'Upload best-practice guides to help validate meeting transcripts before generating Statements of Work. Guides are automatically matched to transcripts using semantic search.'}
          </p>
          {!searchQuery && (
            <button
              onClick={() => setShowUploadModal(true)}
              className="inline-flex items-center gap-2 px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded-lg transition"
            >
              <Upload className="w-4 h-4" />
              Upload Your First Guide
            </button>
          )}
        </div>
      ) : (
        <div className="grid gap-3">
          {filteredGuides.map((guide) => (
            <div
              key={guide.guide_id}
              className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4 hover:border-aegisaxiom-border-hover transition group"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-start gap-3 flex-1 min-w-0">
                  <span className="text-2xl flex-shrink-0 mt-0.5">
                    {getFileIcon(guide.filename)}
                  </span>
                  <div className="flex-1 min-w-0">
                    <h4 className="text-aegisaxiom-text font-medium text-sm truncate">{guide.title}</h4>
                    <p className="text-xs text-aegisaxiom-text-muted mt-0.5 truncate">{guide.filename}</p>
                    <div className="flex items-center gap-4 mt-2 text-xs text-aegisaxiom-text-secondary">
                      <span className="flex items-center gap-1">
                        <Layers className="w-3 h-3" />
                        {guide.chunk_count} section{guide.chunk_count !== 1 ? 's' : ''}
                      </span>
                      <span className="flex items-center gap-1">
                        <Calendar className="w-3 h-3" />
                        {formatDate(guide.uploaded_at)}
                      </span>
                    </div>
                  </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition">
                  <button
                    onClick={() => setDeleteConfirm(guide.guide_id)}
                    className="p-2 text-aegisaxiom-text-secondary hover:text-red-600 dark:text-red-400 hover:bg-red-50 dark:bg-red-500/10 rounded-lg transition"
                    title="Delete guide"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>

              {/* Delete Confirmation */}
              {deleteConfirm === guide.guide_id && (
                <div className="mt-3 pt-3 border-t border-aegisaxiom-border flex items-center justify-between">
                  <p className="text-xs text-red-700 dark:text-red-300">
                    Delete this guide? This removes it from all future validations.
                  </p>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setDeleteConfirm(null)}
                      className="px-3 py-1 text-xs bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded transition"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={() => handleDelete(guide.guide_id)}
                      className="px-3 py-1 text-xs bg-red-600 hover:bg-red-500 text-white rounded transition"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Stats Footer */}
      {guides.length > 0 && (
        <div className="bg-aegisaxiom-accent-muted border border-aegisaxiom-border rounded-lg px-4 py-3 flex items-center justify-between text-xs text-aegisaxiom-text-secondary">
          <span>
            {guides.length} guide{guides.length !== 1 ? 's' : ''} in knowledge base
          </span>
          <span>
            {guides.reduce((sum, g) => sum + (g.chunk_count || 0), 0)} total sections indexed
          </span>
        </div>
      )}

      {/* Upload Modal */}
      {showUploadModal && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-[110] p-4">
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg w-full max-w-lg">
            <div className="p-5 border-b border-aegisaxiom-border">
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-bold text-aegisaxiom-text flex items-center gap-2">
                  <Upload className="w-5 h-5 text-violet-600 dark:text-violet-400" />
                  Upload Best Practice Guide
                </h3>
                <button
                  onClick={() => {
                    setShowUploadModal(false);
                    setSelectedFile(null);
                    setUploadTitle('');
                  }}
                  className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            <div className="p-5 space-y-4">
              {/* Title */}
              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-1.5">
                  Guide Title <span className="text-aegisaxiom-text-muted">(optional)</span>
                </label>
                <input
                  type="text"
                  value={uploadTitle}
                  onChange={(e) => setUploadTitle(e.target.value)}
                  placeholder="e.g., Discovery Call Checklist for Field Services"
                  className="w-full px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 text-sm"
                />
              </div>

              {/* File Selection */}
              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-1.5">
                  Guide File
                </label>
                {selectedFile ? (
                  <div className="flex items-center justify-between bg-aegisaxiom-base border border-violet-500/50 rounded-lg p-3">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="text-lg">{getFileIcon(selectedFile.name)}</span>
                      <div className="min-w-0">
                        <p className="text-sm text-aegisaxiom-text truncate">{selectedFile.name}</p>
                        <p className="text-xs text-aegisaxiom-text-secondary">
                          {(selectedFile.size / 1024).toFixed(1)} KB
                        </p>
                      </div>
                    </div>
                    <button
                      onClick={() => setSelectedFile(null)}
                      className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition p-1"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                ) : (
                  <label className="flex flex-col items-center justify-center w-full h-32 bg-aegisaxiom-base border border-dashed border-aegisaxiom-border-hover rounded-lg cursor-pointer hover:border-violet-500/50 transition">
                    <Upload className="w-8 h-8 text-aegisaxiom-text-muted mb-2" />
                    <p className="text-sm text-aegisaxiom-text-secondary">Click to select a file</p>
                    <p className="text-xs text-aegisaxiom-text-muted mt-1">
                      PDF, DOCX, or TXT — max 20 MB
                    </p>
                    <input
                      type="file"
                      accept=".pdf,.docx,.doc,.txt"
                      onChange={(e) => setSelectedFile(e.target.files?.[0] || null)}
                      className="hidden"
                    />
                  </label>
                )}
              </div>

              {/* Info */}
              <div className="bg-violet-50 dark:bg-violet-500/10 border border-violet-500/20 rounded-lg p-3 flex items-start gap-2">
                <BookOpen className="w-4 h-4 text-violet-600 dark:text-violet-400 mt-0.5 flex-shrink-0" />
                <p className="text-xs text-violet-700 dark:text-violet-300/80">
                  The guide will be parsed, split into sections, and indexed for semantic search.
                  When an engineer validates transcripts, the most relevant guide will be
                  automatically matched.
                </p>
              </div>
            </div>

            <div className="p-5 border-t border-aegisaxiom-border flex gap-3 justify-end">
              <button
                onClick={() => {
                  setShowUploadModal(false);
                  setSelectedFile(null);
                  setUploadTitle('');
                }}
                className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm text-sm rounded-lg transition"
              >
                Cancel
              </button>
              <button
                onClick={handleUpload}
                disabled={!selectedFile || uploading}
                className="px-5 py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded-lg transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
              >
                {uploading ? (
                  <>
                    <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white"></div>
                    Processing...
                  </>
                ) : (
                  <>
                    <Upload className="w-4 h-4" />
                    Upload & Index
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

import React, { useState, useEffect } from 'react';
import { api } from '../../lib/api';
import API_URL from '../../lib/apiConfig';
import QuizForm from './QuizForm';
import SingleSelectArtifact from './SingleSelectArtifact';
import ChatEntryChips from './ChatEntryChips';
import MultiUploadArtifact from './MultiUploadArtifact';
import AgentHandoffArtifact from './AgentHandoffArtifact';
import TranscriptAnalysisForm from './TranscriptAnalysisForm';
import AIEmployeeMatrixTable from './AIEmployeeMatrixTable';
import SingleCallSummaryArtifact from './SingleCallSummaryArtifact';
import TabbedAnalysisArtifact from './TabbedAnalysisArtifact';
import SowDraftArtifact from './SowDraftArtifact';
import CashflowChartArtifact from './CashflowChartArtifact';
import FinanceExportArtifact from './FinanceExportArtifact';
import ChatMarkdown from '../ChatMarkdown';
import { BookOpen, ChevronDown, ChevronUp } from 'lucide-react';

function ReadingCard({ payload }) {
  const [expanded, setExpanded] = useState(true);
  const { title, content, module_number } = payload;

  return (
    <div className="my-3 rounded-xl border border-violet-500/30 bg-aegisaxiom-elevated overflow-hidden">
      <button
        onClick={() => setExpanded((e) => !e)}
        className="w-full flex items-center justify-between px-5 py-3 bg-gradient-to-r from-violet-600/20 to-purple-600/20 hover:from-violet-600/30 hover:to-purple-600/30 transition-colors"
      >
        <div className="flex items-center gap-3">
          <BookOpen size={18} className="text-violet-600 dark:text-violet-400" />
          <span className="text-sm font-semibold text-aegisaxiom-text">
            {title || `Module ${module_number}`}
          </span>
        </div>
        {expanded ? (
          <ChevronUp size={16} className="text-aegisaxiom-text-secondary" />
        ) : (
          <ChevronDown size={16} className="text-aegisaxiom-text-secondary" />
        )}
      </button>
      {expanded && (
        <div className="px-5 py-4 text-sm leading-relaxed text-aegisaxiom-text max-h-[60vh] overflow-y-auto prose-sm">
          <ChatMarkdown content={content} />
        </div>
      )}
    </div>
  );
}

export default function ChatArtifact({ artifactId }) {
  const [artifact, setArtifact] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!artifactId) return;

    const fetchArtifact = async () => {
      try {
        const res = await api.get(`${API_URL}/api/ai-chat-artifacts/${artifactId}`);
        if (res.ok) {
          setArtifact(await res.json());
        } else {
          setError('Failed to load artifact');
        }
      } catch {
        setError('Connection error');
      } finally {
        setLoading(false);
      }
    };

    fetchArtifact();
  }, [artifactId]);

  const handleSubmitted = () => {
    api
      .get(`${API_URL}/api/ai-chat-artifacts/${artifactId}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data) setArtifact(data);
        // Pass grading details so the auto-message can include specifics
        const feedback = data?.ai_feedback || {};
        const wrongQs = (feedback.feedback || []).filter((f) => !f.correct);
        window.dispatchEvent(
          new CustomEvent('arc-quiz-graded', {
            detail: {
              artifactId,
              score: feedback.score,
              passed: feedback.passed,
              wrongCount: wrongQs.length,
              totalCount: (feedback.feedback || []).length,
            },
          }),
        );
      });
  };

  if (loading) {
    return (
      <div className="my-3 p-4 rounded-xl border border-aegisaxiom-border bg-aegisaxiom-elevated animate-pulse">
        <div className="h-4 bg-aegisaxiom-elevated rounded w-1/3 mb-3" />
        <div className="h-3 bg-aegisaxiom-elevated rounded w-2/3" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="my-3 p-4 rounded-xl border border-red-500/30 bg-red-500/5 text-red-600 dark:text-red-400 text-sm">
        {error}
      </div>
    );
  }

  if (!artifact) return null;

  switch (artifact.artifact_type) {
    case 'reading':
      return <ReadingCard payload={artifact.payload} />;
    case 'quiz_form':
      return <QuizForm artifact={artifact} onSubmitted={handleSubmitted} />;
    case 'single_select':
      return <SingleSelectArtifact artifact={artifact} />;
    case 'chips':
      return <ChatEntryChips artifact={artifact} />;
    case 'multi_upload':
      return <MultiUploadArtifact artifact={artifact} />;
    case 'agent_handoff':
      return <AgentHandoffArtifact artifact={artifact} />;
    case 'transcript_analysis':
      return <TranscriptAnalysisForm artifact={artifact} />;
    case 'ai_employee_matrix':
      return <AIEmployeeMatrixTable artifact={artifact} />;
    case 'single_call_summary':
      return <SingleCallSummaryArtifact artifact={artifact} />;
    case 'discovery_analysis_v3':
    case 'discovery_analysis_v3_call1':
    case 'discovery_analysis_v3_call2':
    case 'discovery_analysis_v3_call3':
      return <TabbedAnalysisArtifact artifact={artifact} />;
    case 'sow_draft':
      return <SowDraftArtifact artifact={artifact} />;
    case 'sow_section_edit':
      return <SowDraftArtifact artifact={artifact} />;
    case 'cashflow_chart':
      return <CashflowChartArtifact artifact={artifact} />;
    case 'finance_export':
      return <FinanceExportArtifact artifact={artifact} />;
    case 'sow_finalize_preview':
      return (
        <div className="my-3 p-5 rounded-xl border border-green-500/30 bg-green-500/5">
          <div className="flex items-center gap-3 mb-3">
            <span className="text-green-600 dark:text-green-400 font-semibold">SOW Finalized</span>
          </div>
          {artifact.payload?.doc_url && (
            <a
              href={artifact.payload.doc_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg text-sm transition-colors"
            >
              Open in Google Docs
            </a>
          )}
        </div>
      );
    default:
      return (
        <div className="my-3 p-4 rounded-xl border border-aegisaxiom-border bg-aegisaxiom-elevated text-sm text-aegisaxiom-text-secondary">
          Unknown artifact type: {artifact.artifact_type}
        </div>
      );
  }
}

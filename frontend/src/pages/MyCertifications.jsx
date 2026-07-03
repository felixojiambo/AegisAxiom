import React, { useState, useEffect } from 'react';
import { api } from '../lib/api';
import API_URL from '../lib/apiConfig';
import { Award, Calendar, BookOpen, CheckCircle, ArrowLeft } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

export default function MyCertifications() {
  const [certs, setCerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    const fetchCerts = async () => {
      try {
        const res = await api.get(`${API_URL}/api/users/me/certifications`);
        if (!cancelled && res.ok) setCerts(await res.json());
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchCerts();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="p-6 max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-4 mb-8">
        <button
          onClick={() => navigate(-1)}
          className="p-2 rounded-lg hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:text-aegisaxiom-text transition-colors"
        >
          <ArrowLeft size={20} />
        </button>
        <div>
          <h1 className="text-2xl font-bold text-aegisaxiom-text">My Certifications</h1>
          <p className="text-sm text-aegisaxiom-text-secondary mt-1">
            Your earned credentials and training completions
          </p>
        </div>
      </div>

      {loading ? (
        <div className="space-y-4">
          {[1, 2].map((i) => (
            <div key={i} className="h-32 bg-aegisaxiom-elevated rounded-xl animate-pulse" />
          ))}
        </div>
      ) : certs.length === 0 ? (
        <div className="text-center py-16">
          <Award size={48} className="mx-auto text-aegisaxiom-text-muted mb-4" />
          <h2 className="text-lg font-medium text-aegisaxiom-text-secondary mb-2">No certifications yet</h2>
          <p className="text-sm text-aegisaxiom-text-muted mb-6">
            Complete training programs to earn your credentials.
          </p>
          <button
            onClick={() => navigate('/ai-employees')}
            className="px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-lg text-sm transition-colors"
          >
            Browse AI Employees
          </button>
        </div>
      ) : (
        <div className="space-y-4">
          {certs.map((cert) => (
            <div
              key={cert.certification_id}
              className="border border-aegisaxiom-border bg-aegisaxiom-elevated rounded-xl p-6 hover:border-violet-500/30 transition-colors"
            >
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-4">
                  <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-violet-600 to-fuchsia-600 flex items-center justify-center">
                    <Award size={24} className="text-aegisaxiom-text" />
                  </div>
                  <div>
                    <h3 className="text-lg font-semibold text-aegisaxiom-text">
                      {cert.certification_type === 'ai_recruiter'
                        ? 'AI Recruiter Certified'
                        : cert.certification_type}
                    </h3>
                    <div className="flex items-center gap-3 mt-1 text-sm text-aegisaxiom-text-secondary">
                      <span className="flex items-center gap-1">
                        <Calendar size={13} />
                        {new Date(cert.issued_at).toLocaleDateString('en-US', {
                          month: 'long',
                          day: 'numeric',
                          year: 'numeric',
                        })}
                      </span>
                      {cert.issued_by && (
                        <span className="flex items-center gap-1">
                          <BookOpen size={13} />
                          Issued by {cert.issued_by}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <CheckCircle size={18} className="text-emerald-600 dark:text-emerald-400" />
                  <span className="text-sm font-medium text-emerald-600 dark:text-emerald-400">Certified</span>
                </div>
              </div>

              {/* Module scores */}
              {cert.module_scores && Object.keys(cert.module_scores).length > 0 && (
                <div className="mt-4 pt-4 border-t border-aegisaxiom-border">
                  <p className="text-xs text-aegisaxiom-text-muted mb-2">Module Scores</p>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(cert.module_scores)
                      .sort(([a], [b]) => Number(a) - Number(b))
                      .map(([mod, score]) => (
                        <span
                          key={mod}
                          className={`px-2 py-1 rounded-md text-xs font-medium ${
                            score >= 75
                              ? 'bg-emerald-50 dark:bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20'
                              : 'bg-amber-50 dark:bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20'
                          }`}
                        >
                          M{mod}: {score}%
                        </span>
                      ))}
                    {cert.final_test_score != null && (
                      <span className="px-2 py-1 rounded-md text-xs font-medium bg-violet-50 dark:bg-violet-500/10 text-violet-600 dark:text-violet-400 border border-violet-500/20">
                        Final: {cert.final_test_score}%
                      </span>
                    )}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

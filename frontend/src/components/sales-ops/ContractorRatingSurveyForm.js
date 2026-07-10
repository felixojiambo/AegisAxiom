import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Star, CheckCircle, AlertCircle } from 'lucide-react';
import { toast } from 'sonner';
import API_URL from '../../lib/apiConfig';

const API = API_URL;

// Survey dimensions with labels
const DIMENSIONS = [
  { key: 'overall_professionalism', label: 'Overall Professionalism' },
  { key: 'timeliness_to_meetings', label: 'Timeliness to Meetings' },
  { key: 'communication_quality', label: 'Communication to PM/Customers' },
  { key: 'quality_of_work', label: 'Quality of Work' },
  { key: 'schedule_control', label: 'Schedule Control: Progression of Work and Change Mgt' },
  { key: 'administration_required', label: 'Administration: How Much Management is Required?' },
  { key: 'project_documentation', label: 'Quality of Project Documentation' },
  { key: 'adherence_to_scope', label: 'Adherence to Project Scope' },
  { key: 'resolution_of_issues', label: 'Resolution of Scope/Issues' },
  { key: 'timesheet_submission', label: 'Timely and Accurate Submission of Timesheets' },
];

export default function ContractorRatingSurveyForm() {
  const { token } = useParams();
  const navigate = useNavigate();

  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [surveyData, setSurveyData] = useState(null);
  const [formData, setFormData] = useState({
    project_name: '',
    customer_name: '',
    hire_likelihood: 5,
    ratings: {},
  });
  const [error, setError] = useState(null);
  const [completed, setCompleted] = useState(false);

  useEffect(() => {
    loadSurvey();
  }, [token]);

  const loadSurvey = async () => {
    try {
      setLoading(true);
      const response = await fetch(`${API}/api/sales-ops/public/surveys/${token}`);

      if (!response.ok) {
        const errorData = await response.json();
        setError(errorData.detail || 'Survey not found');
        return;
      }

      const data = await response.json();
      setSurveyData(data);

      // Pre-populate if available
      if (data.project_name) {
        setFormData((prev) => ({ ...prev, project_name: data.project_name }));
      }
      if (data.customer_name) {
        setFormData((prev) => ({ ...prev, customer_name: data.customer_name }));
      }

      // Initialize all ratings to 3 (middle)
      const initialRatings = {};
      DIMENSIONS.forEach((dim) => {
        initialRatings[dim.key] = 3;
      });
      setFormData((prev) => ({ ...prev, ratings: initialRatings }));
    } catch (err) {
      console.error('Error loading survey:', err);
      setError('Failed to load survey');
    } finally {
      setLoading(false);
    }
  };

  const handleRatingChange = (dimension, rating) => {
    setFormData({
      ...formData,
      ratings: {
        ...formData.ratings,
        [dimension]: rating,
      },
    });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();

    // Validation
    if (!formData.project_name || !formData.customer_name) {
      toast.error('Please fill in project and customer name');
      return;
    }

    // Check all dimensions are rated
    const allRated = DIMENSIONS.every((dim) => formData.ratings[dim.key] !== undefined);
    if (!allRated) {
      toast.error('Please rate all dimensions');
      return;
    }

    setSubmitting(true);
    try {
      const dimension_ratings = DIMENSIONS.map((dim) => ({
        dimension: dim.key,
        rating: formData.ratings[dim.key],
      }));

      const response = await fetch(`${API}/api/sales-ops/public/surveys/${token}/submit`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          project_name: formData.project_name,
          customer_name: formData.customer_name,
          hire_likelihood: formData.hire_likelihood,
          dimension_ratings,
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to submit survey');
      }

      const result = await response.json();
      setCompleted(true);
      toast.success(`Survey submitted! Rating: ${result.calculated_rating}/5 ⭐`);
    } catch (err) {
      console.error('Error submitting survey:', err);
      toast.error(err.message || 'Failed to submit survey');
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-aegisaxiom-base to-aegisaxiom-surface flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-violet-500"></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-aegisaxiom-base to-aegisaxiom-surface flex items-center justify-center p-4">
        <div className="bg-red-50 dark:bg-red-900/20 border-2 border-red-200 dark:border-red-700/50 rounded-lg p-8 text-center max-w-md">
          <AlertCircle className="w-16 h-16 text-red-600 dark:text-red-400 mx-auto mb-4" />
          <h2 className="text-xl font-bold text-aegisaxiom-text mb-2">Survey Not Available</h2>
          <p className="text-red-200">{error}</p>
        </div>
      </div>
    );
  }

  if (completed) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-aegisaxiom-base to-aegisaxiom-surface flex items-center justify-center p-4">
        <div className="bg-green-50 dark:bg-green-900/20 border-2 border-green-200 dark:border-green-700/50 rounded-lg p-8 text-center max-w-md">
          <CheckCircle className="w-16 h-16 text-green-600 dark:text-green-400 mx-auto mb-4" />
          <h2 className="text-2xl font-bold text-aegisaxiom-text mb-2">Thank You!</h2>
          <p className="text-green-200 mb-4">Your feedback has been submitted successfully.</p>
          <p className="text-sm text-aegisaxiom-text-secondary">
            Your ratings help us improve our contractor performance.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-aegisaxiom-base to-aegisaxiom-surface py-8 px-4">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-6 mb-6">
          <h1 className="text-3xl font-bold text-aegisaxiom-text mb-2">Contractor Performance Survey</h1>
          <p className="text-aegisaxiom-text-secondary">
            Rate your experience with {surveyData.contractor_user_name}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-6">
          {/* Project Info */}
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-6">
            <h3 className="text-lg font-semibold text-aegisaxiom-text mb-4">Project Information</h3>

            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                  Customer and Project Name *
                </label>
                <input
                  type="text"
                  value={formData.customer_name}
                  onChange={(e) => setFormData({ ...formData, customer_name: e.target.value })}
                  className="w-full px-4 py-3 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500"
                  placeholder="Acme Corp - Network Installation Project"
                  required
                />
              </div>
            </div>
          </div>

          {/* Hire Likelihood */}
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-6">
            <h3 className="text-lg font-semibold text-aegisaxiom-text mb-2">Hiring Recommendation</h3>
            <p className="text-sm text-aegisaxiom-text-secondary mb-4">
              On a scale from 1-10 with 1 being very unlikely and 10 being very likely, would you
              hire this engineer to do work for you?
            </p>

            <div className="flex items-center justify-between gap-2">
              {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((num) => (
                <button
                  key={num}
                  type="button"
                  onClick={() => setFormData({ ...formData, hire_likelihood: num })}
                  className={`flex-1 py-3 rounded-lg font-semibold transition ${
                    formData.hire_likelihood === num
                      ? 'bg-violet-600 text-white border-2 border-violet-400'
                      : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary border border-aegisaxiom-border hover:bg-aegisaxiom-elevated'
                  }`}
                >
                  {num}
                </button>
              ))}
            </div>
            <div className="flex justify-between text-xs text-aegisaxiom-text-muted mt-2 px-2">
              <span>Very Unlikely</span>
              <span>Very Likely</span>
            </div>
          </div>

          {/* Matrix Rating */}
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-6">
            <h3 className="text-lg font-semibold text-aegisaxiom-text mb-2">Contractor Performance Matrix</h3>
            <p className="text-sm text-aegisaxiom-text-secondary mb-6">
              Rate each dimension from 1 (Horrible) to 5 (Awesome)
            </p>

            <div className="space-y-5">
              {DIMENSIONS.map((dim, idx) => (
                <div key={dim.key} className="border-b border-aegisaxiom-border pb-4 last:border-0">
                  <div className="flex items-center justify-between mb-3">
                    <label className="text-sm font-medium text-aegisaxiom-text">
                      {idx + 1}. {dim.label}
                    </label>
                    <span
                      className={`px-3 py-1 rounded-full text-sm font-semibold ${
                        formData.ratings[dim.key] === 5
                          ? 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300'
                          : formData.ratings[dim.key] === 4
                            ? 'bg-blue-900/30 text-blue-700 dark:text-blue-300'
                            : formData.ratings[dim.key] === 3
                              ? 'bg-yellow-50 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-300'
                              : formData.ratings[dim.key] === 2
                                ? 'bg-orange-50 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300'
                                : 'bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300'
                      }`}
                    >
                      {formData.ratings[dim.key]}/5
                    </span>
                  </div>

                  <div className="flex items-center gap-2">
                    {[1, 2, 3, 4, 5].map((rating) => (
                      <button
                        key={rating}
                        type="button"
                        onClick={() => handleRatingChange(dim.key, rating)}
                        className={`flex-1 py-2 rounded transition ${
                          formData.ratings[dim.key] === rating
                            ? rating >= 4
                              ? 'bg-green-600 text-white'
                              : rating === 3
                                ? 'bg-yellow-600 text-white'
                                : 'bg-red-600 text-white'
                            : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:bg-aegisaxiom-elevated'
                        }`}
                      >
                        {rating}
                      </button>
                    ))}
                  </div>
                  <div className="flex justify-between text-xs text-aegisaxiom-text-muted mt-1 px-1">
                    <span>Horrible</span>
                    <span>Awesome</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Submit Button */}
          <div className="flex justify-center">
            <button
              type="submit"
              disabled={submitting}
              className="px-8 py-4 bg-violet-600 hover:bg-violet-500 text-white text-lg font-semibold rounded-lg disabled:opacity-50 disabled:cursor-not-allowed transition"
            >
              {submitting ? 'Submitting...' : 'Submit Survey'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

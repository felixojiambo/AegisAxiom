import React, { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import API_URL from '../../lib/apiConfig';
import ConfirmModal from '../shared/ConfirmModal';
import TabStrip from '../shared/TabStrip';
import {
  Plus,
  Search,
  Edit,
  Edit2,
  Star,
  Shield,
  DollarSign,
  AlertCircle,
  Phone,
  Mail,
  MapPin,
  FileText,
  Award,
  Calendar,
  User,
  Users,
  X,
  Save,
  Trash2,
  MessageSquare,
  Building2,
  ExternalLink,
  Briefcase,
  Check,
  Clock,
  CheckCircle,
  XCircle,
  Package,
} from 'lucide-react';

const API = API_URL;

const PARTNERSHIP_TIERS = {
  none: { label: 'None', color: 'bg-aegisaxiom-elevated', icon: '○' },
  bronze: { label: 'Bronze', color: 'bg-amber-700', icon: '🥉' },
  silver: { label: 'Silver', color: 'bg-aegisaxiom-elevated', icon: '🥈' },
  gold: { label: 'Gold', color: 'bg-yellow-500', icon: '🥇' },
  platinum: { label: 'Platinum', color: 'bg-cyan-400', icon: '💎' },
};

// Helper function to render rating stars
const renderRating = (rating) => {
  if (!rating) return <span className="text-aegisaxiom-text-muted text-sm">Not rated</span>;

  return (
    <div className="flex items-center gap-1">
      {[1, 2, 3, 4, 5].map((star) => (
        <Star
          key={star}
          className={`w-4 h-4 ${star <= rating ? 'text-yellow-600 dark:text-yellow-400 fill-yellow-400' : 'text-aegisaxiom-text-muted'}`}
        />
      ))}
      <span className="text-aegisaxiom-text text-sm ml-1">{rating.toFixed(1)}</span>
    </div>
  );
};

export default function ContractorManagementTab() {
  const [contractors, setContractors] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all'); // all, active, inactive
  const [selectedContractor, setSelectedContractor] = useState(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [editMode, setEditMode] = useState(false);
  const [showGrievanceModal, setShowGrievanceModal] = useState(false);
  const [grievanceForm, setGrievanceForm] = useState({
    reporter_name: '',
    reporter_type: 'internal',
    severity: 'medium',
    description: '',
  });

  // Survey Management State
  const [view, setView] = useState('contractors'); // 'contractors' | 'surveys'
  const [surveys, setSurveys] = useState([]);
  const [showSendSurveyModal, setShowSendSurveyModal] = useState(false);
  const [surveyForm, setSurveyForm] = useState({
    contractor_business_id: '',
    contractor_user_id: '',
    project_name: '',
    customer_name: '',
    sent_to_name: '',
    sent_to_email: '',
  });

  useEffect(() => {
    loadContractors();
  }, [statusFilter]);

  const loadContractors = async () => {
    try {
      setLoading(true);
      const statusParam = statusFilter !== 'all' ? `?status=${statusFilter}` : '';
      const response = await api.get(`${API}/api/admin/contractor-businesses${statusParam}`);

      if (response.ok) {
        const data = await response.json();

        // Load contractor users for each business (for survey dropdown)
        const usersResponse = await api.get(`${API}/api/admin/users?role=contractor`);
        let allUsers = [];
        if (usersResponse.ok) {
          const usersData = await usersResponse.json();
          allUsers = usersData;
        }

        // Load grievance counts for each contractor
        const contractorsWithData = await Promise.all(
          (data.items || []).map(async (business) => {
            try {
              // Get grievances for this business
              const grievancesRes = await api.get(
                `${API}/api/sales-ops/contractor-businesses/${business.business_id}/grievances`,
              );
              let grievances = [];
              if (grievancesRes.ok) {
                const grievanceData = await grievancesRes.json();
                grievances = grievanceData.items || [];
              }

              return {
                ...business,
                users: allUsers.filter((u) => u.contractor_business_id === business.business_id),
                grievances: grievances,
                unresolvedGrievanceCount: grievances.filter((g) => g.status !== 'resolved').length,
              };
            } catch (err) {
              console.error(`Error loading data for ${business.business_id}:`, err);
              return {
                ...business,
                users: allUsers.filter((u) => u.contractor_business_id === business.business_id),
                grievances: [],
                unresolvedGrievanceCount: 0,
              };
            }
          }),
        );

        setContractors(contractorsWithData);
      } else {
        toast.error('Failed to load contractors');
      }
    } catch (error) {
      console.error('Error loading contractors:', error);
      toast.error('Failed to load contractors');
    } finally {
      setLoading(false);
    }
  };

  const saveContractor = async (contractorData, isEdit = false) => {
    try {
      const response =
        isEdit && selectedContractor
          ? await api.put(
              `${API}/api/admin/contractor-businesses/${selectedContractor.business_id}`,
              contractorData,
            )
          : await api.post(`${API}/api/admin/contractor-businesses`, contractorData);

      if (response.ok) {
        toast.success(isEdit ? 'Contractor updated' : 'Contractor created');
        loadContractors();
        setShowAddModal(false);
        setEditMode(false);
        setSelectedContractor(null);
      } else {
        const errorData = await response.json();
        toast.error(errorData.detail || 'Failed to save contractor');
      }
    } catch (error) {
      console.error('Error saving contractor:', error);
      toast.error('Failed to save contractor');
      throw error;
    }
  };

  const addGrievance = async () => {
    if (!grievanceForm.description || !grievanceForm.reporter_name) {
      toast.error('Please fill in all required fields');
      return;
    }

    try {
      const response = await api.post(
        `${API}/api/admin/contractor-businesses/${selectedContractor.business_id}/grievances`,
        grievanceForm,
      );

      if (response.ok) {
        toast.success('Grievance recorded');
        setShowGrievanceModal(false);
        setGrievanceForm({
          reporter_name: '',
          reporter_type: 'internal',
          severity: 'medium',
          description: '',
        });
        loadContractors();

        // Refresh selected contractor
        const updatedResponse = await api.get(
          `${API}/api/admin/contractor-businesses/${selectedContractor.business_id}`,
        );
        if (updatedResponse.ok) {
          const updated = await updatedResponse.json();
          setSelectedContractor(updated);
        }
      } else {
        toast.error('Failed to record grievance');
      }
    } catch (error) {
      console.error('Error adding grievance:', error);
      toast.error('Failed to record grievance');
    }
  };

  const loadSurveys = async () => {
    try {
      setLoading(true);
      // Load all surveys across all contractors
      const allSurveys = [];
      for (const contractor of contractors) {
        const response = await api.get(
          `${API}/api/sales-ops/contractor-businesses/${contractor.business_id}/surveys`,
        );
        if (response.ok) {
          const data = await response.json();
          allSurveys.push(
            ...data.items.map((s) => ({ ...s, business_name: contractor.business_name })),
          );
        }
      }
      setSurveys(allSurveys.sort((a, b) => new Date(b.sent_at) - new Date(a.sent_at)));
    } catch (error) {
      console.error('Error loading surveys:', error);
      toast.error('Failed to load surveys');
    } finally {
      setLoading(false);
    }
  };

  const handleSendSurvey = async () => {
    try {
      if (!surveyForm.contractor_business_id || !surveyForm.contractor_user_id) {
        toast.error('Please select contractor and user');
        return;
      }

      const response = await api.post(`${API}/api/sales-ops/surveys/send`, surveyForm);
      if (response.ok) {
        const data = await response.json();
        toast.success('Survey created! Copy link to share.');

        // Copy survey URL to clipboard
        try {
          navigator.clipboard.writeText(data.survey_url);
          toast.info('Survey link copied to clipboard!');
        } catch {
          /* clipboard blocked */
        }

        setShowSendSurveyModal(false);
        setSurveyForm({
          contractor_business_id: '',
          contractor_user_id: '',
          project_name: '',
          customer_name: '',
          sent_to_name: '',
          sent_to_email: '',
        });

        // Reload surveys
        loadSurveys();
      } else {
        const errorData = await response.json();
        toast.error(errorData.detail || 'Failed to send survey');
      }
    } catch (error) {
      console.error('Error sending survey:', error);
      toast.error('Failed to send survey');
    }
  };

  useEffect(() => {
    if (view === 'surveys' && contractors.length > 0) {
      loadSurveys();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  const filteredContractors = contractors.filter((c) => {
    if (!searchQuery) return true;
    const search = searchQuery.toLowerCase();
    return (
      c.business_name?.toLowerCase().includes(search) ||
      c.contact_name?.toLowerCase().includes(search) ||
      c.email?.toLowerCase().includes(search)
    );
  });

  return (
    <div>
      {/* View Toggle */}
      <div className="mb-5">
        <TabStrip
          variant="segmented"
          size="sm"
          stretch
          activeId={view}
          onChange={setView}
          tabs={[
            { id: 'contractors', label: 'Contractors', icon: Users },
            { id: 'surveys', label: 'Survey Management', icon: Star },
          ]}
        />
      </div>

      {/* Contractors View */}
      {view === 'contractors' && (
        <>
          {/* Toolbar */}
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-6">
            <div className="flex flex-col sm:flex-row sm:items-center gap-3 flex-1 min-w-0">
              {/* Search */}
              <div className="relative w-full sm:w-72 lg:w-80">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-aegisaxiom-text-secondary" />
                <input
                  type="text"
                  placeholder="Search contractors..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full pl-10 pr-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 text-sm"
                />
              </div>

              {/* Status Filter */}
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="px-4 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text focus:outline-none focus:border-violet-500 text-sm w-full sm:w-auto"
              >
                <option value="all">All Status</option>
                <option value="active">Active</option>
                <option value="inactive">Inactive</option>
              </select>
            </div>

            {/* Add New Contractor */}
            <button
              onClick={() => {
                setSelectedContractor(null);
                setEditMode(false);
                setShowAddModal(true);
              }}
              className="flex items-center justify-center gap-2 px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white font-medium rounded-lg transition text-sm shrink-0 w-full sm:w-auto"
            >
              <Plus className="w-4 h-4" />
              Add Contractor
            </button>
          </div>

          {/* Contractor List */}
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-violet-500"></div>
            </div>
          ) : filteredContractors.length === 0 ? (
            <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-12 text-center">
              <Users className="w-16 h-16 text-aegisaxiom-text-muted mx-auto mb-4" />
              <h3 className="text-lg font-semibold text-aegisaxiom-text mb-2">No Contractors Found</h3>
              <p className="text-aegisaxiom-text-secondary text-sm mb-4">
                {searchQuery
                  ? 'No contractors match your search'
                  : 'Get started by adding your first contractor'}
              </p>
              <button
                onClick={() => setShowAddModal(true)}
                className="px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-lg transition"
              >
                Add First Contractor
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
              {filteredContractors.map((contractor) => {
                const partnershipConfig = PARTNERSHIP_TIERS[contractor.partnership_tier || 'none'];
                const marketingFundsAvailable =
                  (contractor.marketing_funds_allocated || 0) -
                  (contractor.marketing_funds_used || 0);
                const grievanceCount = contractor.grievances?.length || 0;
                const unresolvedGrievances = contractor.unresolvedGrievanceCount || 0;

                return (
                  <div
                    key={contractor.business_id}
                    onClick={() => setSelectedContractor(contractor)}
                    className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4 hover:border-violet-500 transition cursor-pointer"
                  >
                    {/* Header */}
                    <div className="flex items-start justify-between mb-3">
                      <div className="flex-1">
                        <h3 className="font-semibold text-aegisaxiom-text mb-1">
                          {contractor.business_name}
                        </h3>
                        <p className="text-sm text-aegisaxiom-text-secondary">
                          {contractor.contact_name}
                        </p>
                      </div>
                      <span
                        className={`px-2 py-1 rounded text-xs font-medium ${
                          contractor.status === 'active'
                            ? 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300'
                            : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary'
                        }`}
                      >
                        {contractor.status}
                      </span>
                    </div>

                    {/* Rating & Partnership */}
                    <div className="flex items-center justify-between mb-3">
                      {renderRating(contractor.rating)}
                      <span
                        className={`px-2 py-1 rounded text-xs font-medium ${partnershipConfig.color} text-aegisaxiom-text`}
                      >
                        {partnershipConfig.icon} {partnershipConfig.label}
                      </span>
                    </div>

                    {/* Contact Info */}
                    <div className="space-y-1 mb-3 text-xs text-aegisaxiom-text-secondary">
                      {contractor.email && (
                        <div className="flex items-center gap-1">
                          <Mail className="w-3 h-3" />
                          <span>{contractor.email}</span>
                        </div>
                      )}
                      {contractor.phone && (
                        <div className="flex items-center gap-1">
                          <Phone className="w-3 h-3" />
                          <span>{contractor.phone}</span>
                        </div>
                      )}
                    </div>

                    {/* Marketing Funds */}
                    {contractor.marketing_funds_allocated > 0 && (
                      <div className="bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-700/40 rounded px-2 py-1 mb-2">
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-aegisaxiom-text-secondary">Marketing Funds</span>
                          <span className="text-emerald-700 dark:text-emerald-300 font-medium">
                            ${marketingFundsAvailable.toFixed(0)} available
                          </span>
                        </div>
                      </div>
                    )}

                    {/* Grievances Alert */}
                    {unresolvedGrievances > 0 && (
                      <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-700/40 rounded px-2 py-1 flex items-center gap-1">
                        <AlertCircle className="w-3 h-3 text-red-600 dark:text-red-400" />
                        <span className="text-xs text-red-700 dark:text-red-300">
                          {unresolvedGrievances} unresolved{' '}
                          {unresolvedGrievances === 1 ? 'issue' : 'issues'}
                        </span>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}

      {/* Contractor Detail Modal */}
      {selectedContractor && !editMode && (
        <ContractorDetailModal
          contractor={selectedContractor}
          onClose={() => setSelectedContractor(null)}
          onEdit={() => {
            setEditMode(true);
          }}
          onUpdate={loadContractors}
          onAddGrievance={() => setShowGrievanceModal(true)}
        />
      )}

      {/* Edit Contractor Business Modal */}
      {selectedContractor && editMode && (
        <EditContractorBusinessModal
          contractor={selectedContractor}
          onClose={() => {
            setEditMode(false);
            setSelectedContractor(null);
          }}
          onSave={() => {
            loadContractors();
            setEditMode(false);
            setSelectedContractor(null);
          }}
        />
      )}

      {/* Add Contractor Business Modal */}
      {showAddModal && (
        <AddContractorBusinessModal
          onClose={() => setShowAddModal(false)}
          onSave={async (data) => {
            await saveContractor(data);
          }}
        />
      )}

      {/* Add Grievance Modal */}
      {showGrievanceModal && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg w-full max-w-lg">
            <div className="p-6 border-b border-aegisaxiom-border">
              <h2 className="text-xl font-bold text-aegisaxiom-text">Record Grievance</h2>
            </div>
            <div className="p-6 space-y-4">
              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                  Reporter Name *
                </label>
                <input
                  type="text"
                  value={grievanceForm.reporter_name}
                  onChange={(e) =>
                    setGrievanceForm({ ...grievanceForm, reporter_name: e.target.value })
                  }
                  className="w-full px-3 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                  placeholder="Who reported this issue?"
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                    Reporter Type
                  </label>
                  <select
                    value={grievanceForm.reporter_type}
                    onChange={(e) =>
                      setGrievanceForm({ ...grievanceForm, reporter_type: e.target.value })
                    }
                    className="w-full px-3 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                  >
                    <option value="customer">Customer</option>
                    <option value="internal">Internal</option>
                    <option value="partner">Partner</option>
                  </select>
                </div>

                <div>
                  <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                    Severity
                  </label>
                  <select
                    value={grievanceForm.severity}
                    onChange={(e) =>
                      setGrievanceForm({ ...grievanceForm, severity: e.target.value })
                    }
                    className="w-full px-3 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                  >
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                    <option value="critical">Critical</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                  Description *
                </label>
                <textarea
                  value={grievanceForm.description}
                  onChange={(e) =>
                    setGrievanceForm({ ...grievanceForm, description: e.target.value })
                  }
                  rows={4}
                  className="w-full px-3 py-2 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                  placeholder="Describe the issue in detail..."
                />
              </div>

              <div className="flex gap-3 pt-4">
                <button
                  onClick={addGrievance}
                  className="flex-1 px-4 py-2 bg-red-600 hover:bg-red-500 text-white rounded-lg transition"
                >
                  Record Grievance
                </button>
                <button
                  onClick={() => {
                    setShowGrievanceModal(false);
                    setGrievanceForm({
                      reporter_name: '',
                      reporter_type: 'internal',
                      severity: 'medium',
                      description: '',
                    });
                  }}
                  className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg transition"
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Survey Management View */}
      {view === 'surveys' && (
        <div className="space-y-6">
          {/* Survey Management Header */}
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            <div>
              <h3 className="text-lg font-semibold text-aegisaxiom-text">Survey Management</h3>
              <p className="text-sm text-aegisaxiom-text-secondary mt-1">
                Send contractor rating surveys to customers and track results
              </p>
            </div>
            <button
              onClick={() => setShowSendSurveyModal(true)}
              className="flex items-center justify-center gap-2 px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white font-medium rounded-lg transition shrink-0 w-full sm:w-auto"
            >
              <Plus className="w-4 h-4" />
              Send Survey
            </button>
          </div>

          {/* Surveys Table */}
          {loading ? (
            <div className="flex justify-center items-center py-12">
              <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-violet-500"></div>
            </div>
          ) : surveys.length === 0 ? (
            <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-12 text-center">
              <Star className="w-12 h-12 text-aegisaxiom-text-muted mx-auto mb-4" />
              <p className="text-aegisaxiom-text-secondary text-lg mb-2">No surveys sent yet</p>
              <p className="text-aegisaxiom-text-muted text-sm">
                Click "Send Survey" to create your first contractor rating survey
              </p>
            </div>
          ) : (
            <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg overflow-hidden">
              <table className="w-full">
                <thead className="bg-aegisaxiom-surface border-b border-aegisaxiom-border">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary uppercase">
                      Contractor
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary uppercase">
                      Project
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary uppercase">
                      Customer
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary uppercase">
                      Sent To
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary uppercase">
                      Status
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary uppercase">
                      Rating
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-aegisaxiom-text-secondary uppercase">
                      Date
                    </th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-aegisaxiom-text-secondary uppercase">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-aegisaxiom-border">
                  {surveys.map((survey) => (
                    <tr key={survey.survey_id} className="hover:bg-aegisaxiom-accent-muted transition">
                      <td className="px-4 py-3">
                        <div className="text-sm font-medium text-aegisaxiom-text">
                          {survey.contractor_user_name}
                        </div>
                        <div className="text-xs text-aegisaxiom-text-muted">{survey.business_name}</div>
                      </td>
                      <td className="px-4 py-3 text-sm text-aegisaxiom-text-secondary">
                        {survey.project_name || '-'}
                      </td>
                      <td className="px-4 py-3 text-sm text-aegisaxiom-text-secondary">
                        {survey.customer_name || '-'}
                      </td>
                      <td className="px-4 py-3">
                        <div className="text-sm text-aegisaxiom-text-secondary">
                          {survey.sent_to_name || '-'}
                        </div>
                        {survey.sent_to_email && (
                          <div className="text-xs text-aegisaxiom-text-muted">{survey.sent_to_email}</div>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {survey.status === 'completed' ? (
                          <span className="px-2 py-1 bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-700/50 text-green-700 dark:text-green-300 text-xs rounded-full">
                            Completed
                          </span>
                        ) : (
                          <span className="px-2 py-1 bg-yellow-50 dark:bg-yellow-900/30 border border-yellow-200 dark:border-yellow-700/50 text-yellow-700 dark:text-yellow-300 text-xs rounded-full">
                            Pending
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {survey.calculated_rating ? (
                          <div className="flex items-center gap-1">
                            <Star className="w-4 h-4 text-yellow-600 dark:text-yellow-400 fill-yellow-400" />
                            <span className="text-sm font-semibold text-yellow-600 dark:text-yellow-400">
                              {survey.calculated_rating.toFixed(1)}/5
                            </span>
                          </div>
                        ) : (
                          <span className="text-sm text-aegisaxiom-text-muted">-</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-sm text-aegisaxiom-text-secondary">
                        {new Date(survey.sent_at).toLocaleDateString()}
                      </td>
                      <td className="px-4 py-3 text-right">
                        {survey.status === 'sent' && (
                          <button
                            onClick={() => {
                              try {
                                navigator.clipboard.writeText(survey.survey_url);
                                toast.success('Survey link copied!');
                              } catch {
                                toast.error('Copy failed');
                              }
                            }}
                            className="text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 transition"
                          >
                            Copy Link
                          </button>
                        )}
                        {survey.status === 'completed' && (
                          <button
                            onClick={() => {
                              // TODO: Show survey detail modal
                              toast.info('Survey details coming soon');
                            }}
                            className="text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 transition"
                          >
                            View Details
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Send Survey Modal */}
      {showSendSurveyModal && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
            <div className="p-6 border-b border-aegisaxiom-border">
              <div className="flex items-center justify-between">
                <h2 className="text-xl font-bold text-aegisaxiom-text">Send Contractor Rating Survey</h2>
                <button
                  onClick={() => setShowSendSurveyModal(false)}
                  className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
              <p className="text-sm text-aegisaxiom-text-secondary mt-2">
                Generate a unique survey link for customers to rate contractor performance
              </p>
            </div>

            <div className="p-6 space-y-4">
              {/* Contractor Business Selection */}
              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                  Contractor Business *
                </label>
                <select
                  value={surveyForm.contractor_business_id}
                  onChange={(e) => {
                    setSurveyForm({
                      ...surveyForm,
                      contractor_business_id: e.target.value,
                      contractor_user_id: '', // Reset user when business changes
                    });
                  }}
                  className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                  required
                >
                  <option value="">Select contractor business...</option>
                  {contractors.map((c) => (
                    <option key={c.business_id} value={c.business_id}>
                      {c.business_name}
                    </option>
                  ))}
                </select>
              </div>

              {/* Contractor User Selection */}
              {surveyForm.contractor_business_id && (
                <div>
                  <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                    Contractor User (Lead Engineer) *
                  </label>
                  <select
                    value={surveyForm.contractor_user_id}
                    onChange={(e) =>
                      setSurveyForm({ ...surveyForm, contractor_user_id: e.target.value })
                    }
                    className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                    required
                  >
                    <option value="">Select contractor user...</option>
                    {contractors
                      .find((c) => c.business_id === surveyForm.contractor_business_id)
                      ?.users?.map((u) => (
                        <option key={u.user_id} value={u.user_id}>
                          {u.name}
                        </option>
                      ))}
                  </select>
                  <p className="text-xs text-aegisaxiom-text-muted mt-1">
                    Select the lead engineer who worked on this project
                  </p>
                </div>
              )}

              {/* Project Name */}
              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                  Project Name
                </label>
                <input
                  type="text"
                  value={surveyForm.project_name}
                  onChange={(e) => setSurveyForm({ ...surveyForm, project_name: e.target.value })}
                  className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                  placeholder="Network Installation Project"
                />
              </div>

              {/* Customer Name */}
              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                  Customer Name
                </label>
                <input
                  type="text"
                  value={surveyForm.customer_name}
                  onChange={(e) => setSurveyForm({ ...surveyForm, customer_name: e.target.value })}
                  className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                  placeholder="Acme Corporation"
                />
              </div>

              {/* Recipient Info */}
              <div className="bg-blue-900/20 border border-blue-700/40 rounded p-4">
                <h4 className="text-sm font-semibold text-blue-200 mb-3">
                  Survey Recipient (Optional)
                </h4>
                <p className="text-xs text-blue-700 dark:text-blue-300 mb-3">
                  Who will you send this survey to? (Customer PM, Account Manager, etc.)
                </p>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-2">
                      Recipient Name
                    </label>
                    <input
                      type="text"
                      value={surveyForm.sent_to_name}
                      onChange={(e) =>
                        setSurveyForm({ ...surveyForm, sent_to_name: e.target.value })
                      }
                      className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
                      placeholder="John Smith"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-aegisaxiom-text-secondary mb-2">
                      Recipient Email
                    </label>
                    <input
                      type="email"
                      value={surveyForm.sent_to_email}
                      onChange={(e) =>
                        setSurveyForm({ ...surveyForm, sent_to_email: e.target.value })
                      }
                      className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
                      placeholder="john@customer.com"
                    />
                  </div>
                </div>
              </div>

              {/* Action Buttons */}
              <div className="flex items-center justify-end gap-3 pt-4 border-t border-aegisaxiom-border">
                <button
                  onClick={() => setShowSendSurveyModal(false)}
                  className="px-4 py-2 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text transition"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSendSurvey}
                  className="px-6 py-2 bg-violet-600 hover:bg-violet-500 text-white font-medium rounded-lg transition"
                >
                  Generate Survey Link
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Contractor Detail Modal Component
function ContractorDetailModal({ contractor, onClose, onEdit, onUpdate, onAddGrievance }) {
  const partnershipConfig = PARTNERSHIP_TIERS[contractor.partnership_tier || 'none'];
  const marketingFundsAvailable =
    (contractor.marketing_funds_allocated || 0) - (contractor.marketing_funds_used || 0);
  const grievances = contractor.grievances || [];
  const unresolvedGrievances = grievances.filter((g) => !g.resolved);

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg w-full max-w-4xl max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="p-6 border-b border-aegisaxiom-border sticky top-0 bg-aegisaxiom-surface z-10">
          <div className="flex items-start justify-between">
            <div>
              <h2 className="text-2xl font-bold text-aegisaxiom-text mb-2">{contractor.business_name}</h2>
              <div className="flex items-center gap-2">
                <span
                  className={`px-2 py-1 rounded text-xs font-medium ${
                    contractor.status === 'active'
                      ? 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300'
                      : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary'
                  }`}
                >
                  {contractor.status}
                </span>
                <span
                  className={`px-2 py-1 rounded text-xs font-medium ${partnershipConfig.color} text-aegisaxiom-text`}
                >
                  {partnershipConfig.icon} {partnershipConfig.label} Partner
                </span>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={onEdit}
                className="p-2 hover:bg-aegisaxiom-elevated rounded-lg transition"
                title="Edit"
              >
                <Edit className="w-5 h-5 text-aegisaxiom-text-secondary" />
              </button>
              <button
                onClick={onClose}
                className="p-2 hover:bg-aegisaxiom-elevated rounded-lg transition"
                title="Close"
              >
                <X className="w-5 h-5 text-aegisaxiom-text-secondary" />
              </button>
            </div>
          </div>
        </div>

        {/* Content */}
        <div className="p-6 space-y-6">
          {/* Performance Overview */}
          <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
            <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3">Performance</h3>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <div className="text-aegisaxiom-text-secondary text-xs mb-1">Rating</div>
                {renderRating(contractor.rating)}
              </div>
              <div>
                <div className="text-aegisaxiom-text-secondary text-xs mb-1">Active Contractors</div>
                <div className="text-aegisaxiom-text font-semibold">
                  {contractor.contractor_count || 0}
                </div>
              </div>
              <div>
                <div className="text-aegisaxiom-text-secondary text-xs mb-1">Grievances</div>
                <div className="flex items-center gap-2">
                  <span className="text-aegisaxiom-text font-semibold">{grievances.length}</span>
                  {unresolvedGrievances.length > 0 && (
                    <span className="text-red-600 dark:text-red-400 text-xs">
                      ({unresolvedGrievances.length} unresolved)
                    </span>
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* Contact Information */}
          <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
            <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3">
              Contact Information
            </h3>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <div className="text-aegisaxiom-text-secondary mb-1">Primary Contact</div>
                <div className="text-aegisaxiom-text">{contractor.contact_name || 'Not set'}</div>
              </div>
              <div>
                <div className="text-aegisaxiom-text-secondary mb-1">Email</div>
                <div className="text-aegisaxiom-text">{contractor.email || 'Not set'}</div>
              </div>
              <div>
                <div className="text-aegisaxiom-text-secondary mb-1">Phone</div>
                <div className="text-aegisaxiom-text">{contractor.phone || 'Not set'}</div>
              </div>
              <div>
                <div className="text-aegisaxiom-text-secondary mb-1">Website</div>
                <div className="text-aegisaxiom-text">{contractor.website || 'Not set'}</div>
              </div>
            </div>
          </div>

          {/* Emergency Contact */}
          {(contractor.emergency_contact_name || contractor.emergency_contact_phone) && (
            <div className="bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-700/40 rounded-lg p-4">
              <h3 className="text-sm font-semibold text-red-700 dark:text-red-300 mb-3 flex items-center gap-2">
                <AlertCircle className="w-4 h-4" />
                Emergency Contact
              </h3>
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <div className="text-aegisaxiom-text-secondary mb-1">Name</div>
                  <div className="text-aegisaxiom-text">{contractor.emergency_contact_name}</div>
                </div>
                <div>
                  <div className="text-aegisaxiom-text-secondary mb-1">Phone</div>
                  <div className="text-aegisaxiom-text">{contractor.emergency_contact_phone}</div>
                </div>
                {contractor.emergency_contact_relationship && (
                  <div>
                    <div className="text-aegisaxiom-text-secondary mb-1">Relationship</div>
                    <div className="text-aegisaxiom-text">
                      {contractor.emergency_contact_relationship}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Marketing Funds */}
          {contractor.marketing_funds_allocated > 0 && (
            <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
              <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3">
                Marketing Funds
              </h3>
              <div className="grid grid-cols-3 gap-4 text-sm">
                <div>
                  <div className="text-aegisaxiom-text-secondary mb-1">Allocated</div>
                  <div className="text-aegisaxiom-text font-semibold">
                    ${contractor.marketing_funds_allocated.toFixed(0)}
                  </div>
                </div>
                <div>
                  <div className="text-aegisaxiom-text-secondary mb-1">Used</div>
                  <div className="text-aegisaxiom-text font-semibold">
                    ${(contractor.marketing_funds_used || 0).toFixed(0)}
                  </div>
                </div>
                <div>
                  <div className="text-aegisaxiom-text-secondary mb-1">Available</div>
                  <div className="text-emerald-600 dark:text-emerald-400 font-bold">
                    ${marketingFundsAvailable.toFixed(0)}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Address */}
          {contractor.address_street && (
            <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
              <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-2 flex items-center gap-2">
                <MapPin className="w-4 h-4" />
                Address
              </h3>
              <p className="text-aegisaxiom-text text-sm">
                {contractor.address_street}
                <br />
                {contractor.address_city}, {contractor.address_state} {contractor.address_zip}
              </p>
            </div>
          )}

          {/* Tax & Payment Info */}
          <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
            <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3">Tax & Payment</h3>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <div className="text-aegisaxiom-text-secondary mb-1">EIN/Tax ID</div>
                <div className="text-aegisaxiom-text">{contractor.tax_id_ein || 'Not provided'}</div>
              </div>
              <div>
                <div className="text-aegisaxiom-text-secondary mb-1">W9 on File</div>
                <div className="text-aegisaxiom-text">{contractor.w9_on_file ? '✅ Yes' : '❌ No'}</div>
              </div>
              <div>
                <div className="text-aegisaxiom-text-secondary mb-1">1099 Eligible</div>
                <div className="text-aegisaxiom-text">{contractor.is_1099_eligible ? 'Yes' : 'No'}</div>
              </div>
              <div>
                <div className="text-aegisaxiom-text-secondary mb-1">Payment Method</div>
                <div className="text-aegisaxiom-text">{contractor.payment_method || 'Not set'}</div>
              </div>
              <div>
                <div className="text-aegisaxiom-text-secondary mb-1">Billing Frequency</div>
                <div className="text-aegisaxiom-text capitalize">
                  {contractor.billing_frequency?.replace('_', ' ')}
                </div>
              </div>
              <div>
                <div className="text-aegisaxiom-text-secondary mb-1">Payment Terms</div>
                <div className="text-aegisaxiom-text">
                  {contractor.payment_terms?.replace('_', ' ').toUpperCase()}
                </div>
              </div>
            </div>
          </div>

          {/* Grievances */}
          <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary">
                Grievances & Issues
              </h3>
              <button
                onClick={onAddGrievance}
                className="flex items-center gap-1 px-3 py-1 bg-red-600 hover:bg-red-500 text-white text-xs rounded transition"
              >
                <Plus className="w-3 h-3" />
                Add Grievance
              </button>
            </div>

            {grievances.length === 0 ? (
              <p className="text-aegisaxiom-text-muted text-sm text-center py-4">
                No grievances recorded
              </p>
            ) : (
              <div className="space-y-3">
                {grievances.map((grievance, idx) => (
                  <div
                    key={idx}
                    className={`border rounded-lg p-3 ${
                      grievance.resolved
                        ? 'border-aegisaxiom-border bg-aegisaxiom-surface'
                        : 'border-red-200 dark:border-red-700/40 bg-red-50 dark:bg-red-900/10'
                    }`}
                  >
                    <div className="flex items-start justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <span
                          className={`px-2 py-0.5 rounded text-[10px] font-medium ${
                            grievance.severity === 'critical'
                              ? 'bg-red-600 text-white'
                              : grievance.severity === 'high'
                                ? 'bg-orange-600 text-white'
                                : grievance.severity === 'medium'
                                  ? 'bg-yellow-600 text-white'
                                  : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm'
                          }`}
                        >
                          {grievance.severity.toUpperCase()}
                        </span>
                        <span className="text-xs text-aegisaxiom-text-secondary">
                          {grievance.reporter_type} - {grievance.reporter_name}
                        </span>
                      </div>
                      {grievance.resolved && (
                        <span className="text-xs text-green-600 dark:text-green-400">
                          ✓ Resolved
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-aegisaxiom-text mb-2">{grievance.description}</p>
                    <div className="text-xs text-aegisaxiom-text-muted">
                      {new Date(grievance.created_at).toLocaleDateString()}
                    </div>
                    {grievance.resolved && grievance.resolution_notes && (
                      <div className="mt-2 pt-2 border-t border-aegisaxiom-border">
                        <div className="text-xs text-aegisaxiom-text-secondary mb-1">Resolution:</div>
                        <div className="text-sm text-aegisaxiom-text-secondary">
                          {grievance.resolution_notes}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Interview & Onboarding */}
          {(contractor.interview_notes || contractor.onboarding_date) && (
            <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
              <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-3">
                Interview & Onboarding
              </h3>
              <div className="space-y-2 text-sm">
                {contractor.onboarding_date && (
                  <div>
                    <div className="text-aegisaxiom-text-secondary mb-1">Onboarding Date</div>
                    <div className="text-aegisaxiom-text">
                      {new Date(contractor.onboarding_date).toLocaleDateString()}
                    </div>
                  </div>
                )}
                {contractor.interviewer_name && (
                  <div>
                    <div className="text-aegisaxiom-text-secondary mb-1">Interviewed By</div>
                    <div className="text-aegisaxiom-text">{contractor.interviewer_name}</div>
                  </div>
                )}
                {contractor.interview_notes && (
                  <div>
                    <div className="text-aegisaxiom-text-secondary mb-1">Interview Notes</div>
                    <div className="text-aegisaxiom-text whitespace-pre-wrap">
                      {contractor.interview_notes}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Notes */}
          {contractor.notes && (
            <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
              <h3 className="text-sm font-semibold text-aegisaxiom-text-secondary mb-2">Notes</h3>
              <p className="text-aegisaxiom-text text-sm whitespace-pre-wrap">{contractor.notes}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Edit Contractor Business Modal - Manage Resource Types & Users
function EditContractorBusinessModal({ contractor, onClose, onSave }) {
  const [activeTab, setActiveTab] = useState('basic'); // basic, resources, users, billing, grievances
  const [formData, setFormData] = useState({
    business_name: contractor.business_name || '',
    contact_name: contractor.contact_name || '',
    email: contractor.email || '',
    phone: contractor.phone || '',
    website: contractor.website || '',
    address_street: contractor.address_street || '',
    address_city: contractor.address_city || '',
    address_state: contractor.address_state || '',
    address_zip: contractor.address_zip || '',
    partnership_tier: contractor.partnership_tier || 'none',
    status: contractor.status || 'active',
    notes: contractor.notes || '',
    // Marketing/Partnership
    marketing_funds_allocated: contractor.marketing_funds_allocated || 0,
    marketing_funds_used: contractor.marketing_funds_used || 0,
    // Billing Info
    billing_contact_name: contractor.billing_contact_name || '',
    billing_email: contractor.billing_email || '',
    billing_phone: contractor.billing_phone || '',
    billing_address_street: contractor.billing_address_street || '',
    billing_address_city: contractor.billing_address_city || '',
    billing_address_state: contractor.billing_address_state || '',
    billing_address_zip: contractor.billing_address_zip || '',
    w9_file_url: contractor.w9_file_url || '',
    tax_id: contractor.tax_id_ein || '',
    payment_method: contractor.payment_method || 'check', // check, ach, wire
    // Bank Account Details
    bank_name: contractor.bank_account_details?.bank_name || '',
    account_holder_name: contractor.bank_account_details?.account_holder_name || '',
    routing_number: contractor.bank_account_details?.routing_number || '',
    account_number: contractor.bank_account_details?.account_number || '',
    account_type: contractor.bank_account_details?.account_type || 'checking',
  });

  const [resourceTypes, setResourceTypes] = useState(contractor.resource_types_offered || []);
  const [contractorServices, setContractorServices] = useState(contractor.services_offered || []);
  const [availableResourceTypes, setAvailableResourceTypes] = useState([]);
  const [contractorUsers, setContractorUsers] = useState([]);
  const [showAddResourceForm, setShowAddResourceForm] = useState(false);
  const [showAddServiceForm, setShowAddServiceForm] = useState(false);
  const [showCreateUserForm, setShowCreateUserForm] = useState(false);
  const [newResourceForm, setNewResourceForm] = useState({
    resource_type_id: '',
    resource_type_name: '',
    pricing_models_accepted: [],
    rate_cards: {},
  });
  const [newUserForm, setNewUserForm] = useState({
    name: '',
    email: '',
    phone: '',
    resource_type_assignments: [],
  });

  const [editingUser, setEditingUser] = useState(null); // User being edited
  const [editUserForm, setEditUserForm] = useState(null); // Form data for editing

  const [grievances, setGrievances] = useState([]);
  const [showAddGrievance, setShowAddGrievance] = useState(false);
  const [newGrievance, setNewGrievance] = useState({
    title: '',
    description: '',
    severity: 'medium',
  });
  const [grievanceComments, setGrievanceComments] = useState({});

  const [uploading, setUploading] = useState(false);

  const [saving, setSaving] = useState(false);
  const [confirmState, setConfirmState] = useState({
    open: false,
    action: null,
    title: '',
    message: '',
  });

  // Payment Activity State
  const [paymentSummary, setPaymentSummary] = useState(null);
  const [paymentActivity, setPaymentActivity] = useState(null);
  const [paymentLoading, setPaymentLoading] = useState(false);
  const [paymentDateFrom, setPaymentDateFrom] = useState('');
  const [paymentDateTo, setPaymentDateTo] = useState('');
  const [paymentStatus, setPaymentStatus] = useState('');

  useEffect(() => {
    loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadData = async () => {
    try {
      // Load available resource types
      const rtRes = await api.get(`${API}/api/admin/default-resource-types`);
      if (rtRes.ok) {
        const data = await rtRes.json();
        setAvailableResourceTypes(data.default_resource_types || []);
      }

      // Load contractor users linked to this business
      const usersRes = await api.get(`${API}/api/admin/users?role=contractor&limit=100`);
      if (usersRes.ok) {
        const users = await usersRes.json();
        const linkedUsers = users.filter(
          (u) => u.contractor_business_id === contractor.business_id,
        );
        setContractorUsers(linkedUsers);
      }

      // Load services offered by this contractor
      loadServices();

      // Load grievances for this contractor
      const grievancesRes = await api.get(
        `${API}/api/sales-ops/contractor-businesses/${contractor.business_id}/grievances`,
      );
      if (grievancesRes.ok) {
        const data = await grievancesRes.json();
        setGrievances(data.items || []);
      }
    } catch (error) {
      console.error('Error loading data:', error);
    }
  };

  const loadPaymentActivity = async () => {
    if (!contractor?.business_id) return;
    setPaymentLoading(true);
    try {
      const params = new URLSearchParams();
      if (paymentDateFrom) params.append('date_from', paymentDateFrom);
      if (paymentDateTo) params.append('date_to', paymentDateTo);
      if (paymentStatus) params.append('status', paymentStatus);

      const [actRes, sumRes] = await Promise.all([
        api.get(
          `${API}/api/admin/contractor-businesses/${contractor.business_id}/payment-activity?${params}`,
        ),
        api.get(`${API}/api/admin/contractor-businesses/${contractor.business_id}/payment-summary`),
      ]);

      if (actRes.ok) setPaymentActivity(await actRes.json());
      if (sumRes.ok) setPaymentSummary(await sumRes.json());
    } catch (err) {
      toast.error('Failed to load payment data');
    } finally {
      setPaymentLoading(false);
    }
  };

  // Load payment data when billing tab is active or filters change
  useEffect(() => {
    if (activeTab === 'billing') {
      loadPaymentActivity();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, paymentDateFrom, paymentDateTo, paymentStatus]);

  const loadServices = async () => {
    try {
      const response = await api.get(
        `${API}/api/sales-ops/contractor-businesses/${contractor.business_id}/services`,
      );
      if (response.ok) {
        const data = await response.json();
        setContractorServices(data.items || []);
      }
    } catch (error) {
      console.error('Error loading services:', error);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const payload = {
        ...formData,
        resource_types_offered: resourceTypes,
        // Structure bank account details
        bank_account_details: formData.bank_name
          ? {
              bank_name: formData.bank_name,
              account_holder_name: formData.account_holder_name,
              routing_number: formData.routing_number,
              account_number: formData.account_number,
              account_type: formData.account_type,
            }
          : null,
        // Update tax_id_ein field name
        tax_id_ein: formData.tax_id,
      };

      // Remove the flattened bank fields from payload
      delete payload.bank_name;
      delete payload.account_holder_name;
      delete payload.routing_number;
      delete payload.account_number;
      delete payload.account_type;
      delete payload.tax_id;

      const response = await api.put(
        `${API}/api/admin/contractor-businesses/${contractor.business_id}`,
        payload,
      );

      if (response.ok) {
        toast.success('Contractor business updated');
        onSave();
        onClose();
      } else {
        const errorData = await response.json();
        toast.error(errorData.detail || 'Failed to update');
      }
    } catch (error) {
      console.error('Error saving:', error);
      toast.error('Failed to update contractor business');
    } finally {
      setSaving(false);
    }
  };

  const addResourceType = () => {
    if (!newResourceForm.resource_type_id) {
      toast.error('Please select a resource type');
      return;
    }

    const selectedRT = availableResourceTypes.find(
      (rt) => rt.resource_type_id === newResourceForm.resource_type_id,
    );

    const newResource = {
      resource_type_id: newResourceForm.resource_type_id,
      resource_type_name: selectedRT?.name || newResourceForm.resource_type_name,
      pricing_models_accepted: newResourceForm.pricing_models_accepted,
      rate_cards: {},
      is_active: true,
      added_at: new Date().toISOString(),
    };

    // Build rate cards based on selected pricing models
    newResourceForm.pricing_models_accepted.forEach((model) => {
      if (model === 'block_hours' || model === 'bill_actuals') {
        newResource.rate_cards[model] = {
          sell_rate: selectedRT?.sell_rate || 0,
          default_cost_rate: 0,
        };
      } else if (model === 'fixed_price') {
        newResource.rate_cards[model] = {
          default_cost_rate: 0,
        };
      }
    });

    setResourceTypes([...resourceTypes, newResource]);
    setShowAddResourceForm(false);
    setNewResourceForm({
      resource_type_id: '',
      resource_type_name: '',
      pricing_models_accepted: [],
      rate_cards: {},
    });
    toast.success('Resource type added - Remember to Save');
  };

  const removeResourceType = (rtId) => {
    setResourceTypes(resourceTypes.filter((rt) => rt.resource_type_id !== rtId));
  };

  const deleteService = (serviceId) => {
    setConfirmState({
      open: true,
      title: 'Remove Service',
      message:
        'Remove this service from the contractor? This will require re-approval if added again.',
      action: async () => {
        try {
          const response = await api.delete(
            `${API}/api/sales-ops/contractor-businesses/${contractor.business_id}/services/${serviceId}`,
          );

          if (response.ok) {
            toast.success('Service removed successfully');
            loadServices(); // Reload services list
          } else {
            const errorData = await response.json();
            toast.error(errorData.detail || 'Failed to remove service');
          }
        } catch (error) {
          console.error('Error removing service:', error);
          toast.error('Failed to remove service');
        }
      },
    });
  };

  const updateResourceRateCard = (rtId, pricingModel, field, value) => {
    setResourceTypes(
      resourceTypes.map((rt) => {
        if (rt.resource_type_id === rtId) {
          return {
            ...rt,
            rate_cards: {
              ...rt.rate_cards,
              [pricingModel]: {
                ...rt.rate_cards[pricingModel],
                [field]: parseFloat(value) || 0,
              },
            },
          };
        }
        return rt;
      }),
    );
  };

  const createContractorUser = async () => {
    if (!newUserForm.name || !newUserForm.email) {
      toast.error('Name and email are required');
      return;
    }

    try {
      const payload = {
        name: newUserForm.name,
        email: newUserForm.email,
        phone: newUserForm.phone,
        role: 'contractor',
        contractor_business_id: contractor.business_id,
        contractor_business_name: contractor.business_name,
        resource_type_assignments: newUserForm.resource_type_assignments,
        status: 'active',
      };

      const response = await api.post(`${API}/api/admin/users`, payload);

      if (response.ok) {
        toast.success('Contractor user account created');
        loadData();
        setShowCreateUserForm(false);
        setNewUserForm({ name: '', email: '', phone: '', resource_type_assignments: [] });
      } else {
        const errorData = await response.json();
        toast.error(errorData.detail || 'Failed to create user');
      }
    } catch (error) {
      console.error('Error creating user:', error);
      toast.error('Failed to create contractor user');
    }
  };

  return (
    <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-[70] p-4">
      <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg w-full max-w-6xl max-h-[95vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="p-6 border-b border-aegisaxiom-border">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-bold text-aegisaxiom-text">Edit Contractor Business</h2>
              <p className="text-sm text-aegisaxiom-text-secondary mt-1">{contractor.business_name}</p>
            </div>
            <button onClick={onClose} className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text">
              <X className="w-6 h-6" />
            </button>
          </div>

          {/* Tabs */}
          <div className="flex gap-2 mt-4">
            <button
              onClick={() => setActiveTab('basic')}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition ${
                activeTab === 'basic'
                  ? 'bg-violet-600 text-white'
                  : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:text-aegisaxiom-text'
              }`}
            >
              Basic Info
            </button>
            <button
              onClick={() => setActiveTab('resources')}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition ${
                activeTab === 'resources'
                  ? 'bg-violet-600 text-white'
                  : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:text-aegisaxiom-text'
              }`}
            >
              Resource Types ({resourceTypes.length})
            </button>
            <button
              onClick={() => setActiveTab('services')}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition ${
                activeTab === 'services'
                  ? 'bg-violet-600 text-white'
                  : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:text-aegisaxiom-text'
              }`}
            >
              Services ({contractorServices.length})
            </button>
            <button
              onClick={() => setActiveTab('users')}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition ${
                activeTab === 'users'
                  ? 'bg-violet-600 text-white'
                  : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:text-aegisaxiom-text'
              }`}
            >
              Contractor Users ({contractorUsers.length})
            </button>
            <button
              onClick={() => setActiveTab('partnership')}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition ${
                activeTab === 'partnership'
                  ? 'bg-violet-600 text-white'
                  : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:text-aegisaxiom-text'
              }`}
            >
              Partnership Benefits
            </button>
            <button
              onClick={() => setActiveTab('billing')}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition ${
                activeTab === 'billing'
                  ? 'bg-violet-600 text-white'
                  : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:text-aegisaxiom-text'
              }`}
            >
              Billing Info
            </button>
            <button
              onClick={() => setActiveTab('grievances')}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition ${
                activeTab === 'grievances'
                  ? 'bg-violet-600 text-white'
                  : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary hover:text-aegisaxiom-text'
              }`}
            >
              Grievances ({grievances.length})
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="p-6 overflow-y-auto flex-1">
          {/* TAB 1: Basic Info */}
          {activeTab === 'basic' && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                    Business Name *
                  </label>
                  <input
                    type="text"
                    value={formData.business_name}
                    onChange={(e) => setFormData({ ...formData, business_name: e.target.value })}
                    className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                    Contact Name
                  </label>
                  <input
                    type="text"
                    value={formData.contact_name}
                    onChange={(e) => setFormData({ ...formData, contact_name: e.target.value })}
                    className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                    Email
                  </label>
                  <input
                    type="email"
                    value={formData.email}
                    onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                    className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                    Phone
                  </label>
                  <input
                    type="tel"
                    value={formData.phone}
                    onChange={(e) => setFormData({ ...formData, phone: e.target.value })}
                    className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                    Partnership Tier
                  </label>
                  <select
                    value={formData.partnership_tier}
                    onChange={(e) => setFormData({ ...formData, partnership_tier: e.target.value })}
                    className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                  >
                    <option value="none">None</option>
                    <option value="bronze">Bronze</option>
                    <option value="silver">Silver</option>
                    <option value="gold">Gold</option>
                    <option value="platinum">Platinum</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                    Status
                  </label>
                  <select
                    value={formData.status}
                    onChange={(e) => setFormData({ ...formData, status: e.target.value })}
                    className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                  >
                    <option value="active">Active</option>
                    <option value="inactive">Inactive</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                  Notes
                </label>
                <textarea
                  value={formData.notes}
                  onChange={(e) => setFormData({ ...formData, notes: e.target.value })}
                  rows={3}
                  className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
                />
              </div>
            </div>
          )}

          {/* TAB 2: Resource Types Offered */}
          {activeTab === 'resources' && (
            <div className="space-y-4">
              <div className="flex justify-between items-center mb-4">
                <p className="text-sm text-aegisaxiom-text-secondary">
                  Define what resource types this business can provide and their payment terms
                </p>
                <button
                  onClick={() => setShowAddResourceForm(true)}
                  className="px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-lg text-sm flex items-center gap-2"
                >
                  <Plus className="w-4 h-4" />
                  Add Resource Type
                </button>
              </div>

              {/* Resource Types List */}
              {resourceTypes.length === 0 ? (
                <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-8 text-center">
                  <FileText className="w-12 h-12 text-aegisaxiom-text-muted mx-auto mb-3" />
                  <p className="text-aegisaxiom-text-secondary text-sm mb-4">
                    No resource types configured yet
                  </p>
                  <button
                    onClick={() => setShowAddResourceForm(true)}
                    className="px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-lg text-sm"
                  >
                    Add First Resource Type
                  </button>
                </div>
              ) : (
                <div className="space-y-3">
                  {resourceTypes.map((rt, index) => {
                    const approvalColors = {
                      pending:
                        'bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 border-amber-500/40',
                      approved:
                        'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300 border-green-500/40',
                      rejected:
                        'bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300 border-red-500/40',
                    };
                    const approvalLabels = {
                      pending: '⏳ Pending Approval',
                      approved: '✓ Approved',
                      rejected: '✗ Rejected',
                    };
                    const approvalStatus = rt.approval_status || 'approved'; // Default to approved for legacy data

                    return (
                      <div
                        key={index}
                        className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4"
                      >
                        <div className="flex items-start justify-between mb-3">
                          <div className="flex-1">
                            <div className="flex items-center gap-2 mb-1">
                              <h4 className="font-semibold text-aegisaxiom-text">
                                {rt.resource_type_name}
                              </h4>
                              <span
                                className={`px-2 py-1 rounded text-xs border ${approvalColors[approvalStatus]}`}
                              >
                                {approvalLabels[approvalStatus]}
                              </span>
                            </div>
                            <p className="text-xs text-aegisaxiom-text-secondary mt-1">
                              Accepts:{' '}
                              {rt.pricing_models_accepted
                                .map((m) => m.replace('_', ' ').toUpperCase())
                                .join(', ')}
                            </p>
                            {approvalStatus === 'pending' &&
                              rt.cost_rate &&
                              rt.default_cost_rate && (
                                <p className="text-xs text-amber-600 dark:text-amber-400 mt-1">
                                  ⚠️ Cost ${rt.cost_rate}/hr exceeds default ${rt.default_cost_rate}
                                  /hr
                                </p>
                              )}
                          </div>
                          <button
                            onClick={() => removeResourceType(rt.resource_type_id)}
                            className="text-red-600 dark:text-red-400 hover:text-red-700 dark:text-red-300"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>

                        {/* Rate Cards by Pricing Model */}
                        <div className="space-y-2">
                          {rt.pricing_models_accepted.map((model) => (
                            <div key={model} className="bg-aegisaxiom-elevated rounded p-3">
                              <div className="text-xs font-semibold text-violet-700 dark:text-violet-300 mb-2 uppercase">
                                {model.replace('_', ' ')}
                              </div>
                              <div className="grid grid-cols-1 gap-3">
                                {(model === 'block_hours' || model === 'bill_actuals') && (
                                  <>
                                    <div>
                                      <label className="block text-xs text-aegisaxiom-text-secondary mb-1">
                                        Default Cost Rate ($/hr) - What we pay
                                      </label>
                                      <input
                                        type="number"
                                        value={rt.rate_cards[model]?.default_cost_rate || ''}
                                        onChange={(e) =>
                                          updateResourceRateCard(
                                            rt.resource_type_id,
                                            model,
                                            'default_cost_rate',
                                            e.target.value,
                                          )
                                        }
                                        className="w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm"
                                        placeholder="Default pay rate"
                                      />
                                      <p className="text-xs text-aegisaxiom-text-muted mt-1">
                                        Default rate for contractors assigned to this resource type
                                      </p>
                                    </div>
                                  </>
                                )}
                                {model === 'fixed_price' && (
                                  <div>
                                    <label className="block text-xs text-aegisaxiom-text-secondary mb-1">
                                      Default Cost Rate
                                    </label>
                                    <input
                                      type="number"
                                      value={rt.rate_cards[model]?.default_cost_rate || ''}
                                      onChange={(e) =>
                                        updateResourceRateCard(
                                          rt.resource_type_id,
                                          model,
                                          'default_cost_rate',
                                          e.target.value,
                                        )
                                      }
                                      className="w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm"
                                      placeholder="$/project"
                                    />
                                  </div>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Add Resource Type Form */}
              {showAddResourceForm && (
                <div className="bg-aegisaxiom-surface border-2 border-violet-600 rounded-lg p-4">
                  <h4 className="font-semibold text-aegisaxiom-text mb-3">Add Resource Type</h4>

                  <div className="space-y-3">
                    <div>
                      <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                        Resource Type *
                      </label>
                      <select
                        value={newResourceForm.resource_type_id}
                        onChange={(e) => {
                          const rt = availableResourceTypes.find(
                            (r) => r.resource_type_id === e.target.value,
                          );
                          setNewResourceForm({
                            ...newResourceForm,
                            resource_type_id: e.target.value,
                            resource_type_name: rt?.name || '',
                          });
                        }}
                        className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                      >
                        <option value="">Select resource type...</option>
                        {availableResourceTypes
                          .filter(
                            (rt) =>
                              !resourceTypes.some(
                                (existing) => existing.resource_type_id === rt.resource_type_id,
                              ),
                          )
                          .map((rt) => (
                            <option key={rt.resource_type_id} value={rt.resource_type_id}>
                              {rt.name}
                            </option>
                          ))}
                      </select>
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                        Pricing Models Accepted *
                      </label>
                      <div className="space-y-2">
                        {['block_hours', 'bill_actuals', 'fixed_price'].map((model) => (
                          <label key={model} className="flex items-center gap-2 cursor-pointer">
                            <input
                              type="checkbox"
                              checked={newResourceForm.pricing_models_accepted.includes(model)}
                              onChange={(e) => {
                                const updated = e.target.checked
                                  ? [...newResourceForm.pricing_models_accepted, model]
                                  : newResourceForm.pricing_models_accepted.filter(
                                      (m) => m !== model,
                                    );
                                setNewResourceForm({
                                  ...newResourceForm,
                                  pricing_models_accepted: updated,
                                });
                              }}
                            />
                            <span className="text-aegisaxiom-text text-sm">
                              {model.replace('_', ' ').toUpperCase()}
                            </span>
                          </label>
                        ))}
                      </div>
                    </div>

                    <div className="flex gap-2">
                      <button
                        onClick={() => {
                          setShowAddResourceForm(false);
                          setNewResourceForm({
                            resource_type_id: '',
                            resource_type_name: '',
                            pricing_models_accepted: [],
                            rate_cards: {},
                          });
                        }}
                        className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={addResourceType}
                        disabled={
                          !newResourceForm.resource_type_id ||
                          newResourceForm.pricing_models_accepted.length === 0
                        }
                        className="px-4 py-2 bg-green-600 hover:bg-green-500 text-white rounded-lg disabled:opacity-50"
                      >
                        Add Resource Type
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* TAB 2.5: Services Offered (Wholesale MRR) */}
          {activeTab === 'services' && (
            <div className="space-y-4">
              <div className="flex justify-between items-center mb-4">
                <div>
                  <p className="text-sm text-aegisaxiom-text-secondary">
                    Wholesale MRR services this contractor can provide (SimpliSecure, SimpliComply,
                    etc.)
                  </p>
                  <p className="text-xs text-amber-600 dark:text-amber-400 mt-1">
                    ⚠️ Services require Procurement approval before they can be used in Solutions
                    Design
                  </p>
                </div>
                <button
                  onClick={() => setShowAddServiceForm(true)}
                  className="px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-lg text-sm flex items-center gap-2"
                >
                  <Plus className="w-4 h-4" />
                  Create Service
                </button>
              </div>

              {/* Services List */}
              {contractorServices.length === 0 ? (
                <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-8 text-center">
                  <Package className="w-12 h-12 text-aegisaxiom-text-muted mx-auto mb-3" />
                  <p className="text-aegisaxiom-text-secondary text-sm mb-3">No services added yet</p>
                  <button
                    onClick={() => setShowAddServiceForm(true)}
                    className="px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-lg text-sm"
                  >
                    Add First Service
                  </button>
                </div>
              ) : (
                <div className="space-y-3">
                  {contractorServices.map((service) => {
                    const approvalColors = {
                      pending:
                        'bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 border-amber-500/40',
                      approved:
                        'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300 border-green-500/40',
                      rejected:
                        'bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300 border-red-500/40',
                    };
                    const approvalLabels = {
                      pending: '⏳ Pending Approval',
                      approved: '✓ Approved',
                      rejected: '✗ Rejected',
                    };

                    return (
                      <div
                        key={service.service_id}
                        className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4"
                      >
                        <div className="flex items-start justify-between mb-3">
                          <div className="flex-1">
                            <div className="flex items-center gap-2 mb-1">
                              <h4 className="font-semibold text-aegisaxiom-text">
                                {service.service_name}
                              </h4>
                              <span
                                className={`px-2 py-1 rounded text-xs border ${approvalColors[service.approval_status]}`}
                              >
                                {approvalLabels[service.approval_status]}
                              </span>
                            </div>
                            <p className="text-xs text-aegisaxiom-text-muted uppercase">
                              {service.recurrency}
                            </p>
                            {service.service_description && (
                              <p className="text-sm text-aegisaxiom-text-secondary mt-2">
                                {service.service_description}
                              </p>
                            )}
                          </div>
                          <button
                            onClick={() => deleteService(service.service_id)}
                            className="p-2 hover:bg-aegisaxiom-elevated rounded transition"
                            title="Remove Service"
                          >
                            <Trash2 className="w-4 h-4 text-red-600 dark:text-red-400" />
                          </button>
                        </div>

                        {/* Pricing Grid - Contractor View */}
                        <div className="text-sm mt-3 pt-3 border-t border-aegisaxiom-border">
                          <div>
                            <span className="text-aegisaxiom-text-secondary block mb-1">Your Cost</span>
                            <span className="text-violet-600 dark:text-violet-400 font-semibold">
                              ${service.cost_to_business?.toFixed(0)}/{service.recurrency}
                            </span>
                          </div>
                        </div>

                        {/* Sell Price & Margin (only show if approved) */}
                        {service.approval_status === 'approved' && service.sell_price && (
                          <div className="grid grid-cols-2 gap-4 text-sm mt-2 p-3 bg-green-50 dark:bg-green-900/10 border border-green-200 dark:border-green-700/30 rounded">
                            <div>
                              <span className="text-aegisaxiom-text-secondary block mb-1">
                                Customer Sell Price
                              </span>
                              <span className="text-green-600 dark:text-green-400 font-semibold">
                                ${service.sell_price?.toFixed(0)}/{service.recurrency}
                              </span>
                            </div>
                            <div>
                              <span className="text-aegisaxiom-text-secondary block mb-1">
                                Final Margin
                              </span>
                              <span className="text-violet-600 dark:text-violet-400 font-semibold">
                                ${service.margin?.toFixed(0)} ({service.margin_percent?.toFixed(1)}
                                %)
                              </span>
                            </div>
                          </div>
                        )}

                        {/* Approval Info */}
                        {service.approval_status === 'approved' && service.approved_by_name && (
                          <div className="mt-3 pt-3 border-t border-aegisaxiom-border text-xs text-aegisaxiom-text-secondary">
                            ✓ Approved by {service.approved_by_name} on{' '}
                            {new Date(service.approved_date).toLocaleDateString()}
                          </div>
                        )}
                        {service.approval_status === 'rejected' && service.rejection_reason && (
                          <div className="mt-3 pt-3 border-t border-aegisaxiom-border text-xs text-red-600 dark:text-red-400">
                            ✗ Rejected: {service.rejection_reason}
                          </div>
                        )}
                        {service.notes && (
                          <div className="mt-3 pt-3 border-t border-aegisaxiom-border text-xs text-aegisaxiom-text-secondary italic">
                            Note: {service.notes}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Add Service Form Modal */}
              {showAddServiceForm && (
                <AddServiceForm
                  businessId={contractor?.business_id}
                  onClose={() => setShowAddServiceForm(false)}
                  onSuccess={() => {
                    setShowAddServiceForm(false);
                    loadServices();
                  }}
                />
              )}
            </div>
          )}

          {/* TAB 3: Contractor Users */}
          {activeTab === 'users' && (
            <div className="space-y-4">
              <div className="flex justify-between items-center mb-4">
                <p className="text-sm text-aegisaxiom-text-secondary">
                  Contractor users linked to this business. Assign resource types to each user.
                </p>
                <button
                  onClick={() => setShowCreateUserForm(true)}
                  className="px-4 py-2 bg-green-600 hover:bg-green-500 text-white rounded-lg text-sm flex items-center gap-2"
                >
                  <User className="w-4 h-4" />
                  Create Contractor Account
                </button>
              </div>

              {/* Users List */}
              {contractorUsers.length === 0 ? (
                <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-8 text-center">
                  <Users className="w-12 h-12 text-aegisaxiom-text-muted mx-auto mb-3" />
                  <p className="text-aegisaxiom-text-secondary text-sm mb-4">
                    No contractor users linked yet
                  </p>
                  <button
                    onClick={() => setShowCreateUserForm(true)}
                    className="px-4 py-2 bg-green-600 hover:bg-green-500 text-white rounded-lg text-sm"
                  >
                    Create First Contractor
                  </button>
                </div>
              ) : (
                <div className="space-y-3">
                  {contractorUsers.map((user) => (
                    <div
                      key={user.user_id}
                      className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4"
                    >
                      {editingUser?.user_id === user.user_id ? (
                        /* EDIT MODE */
                        <div className="space-y-3">
                          <div className="flex items-center justify-between mb-3">
                            <h4 className="font-semibold text-violet-600 dark:text-violet-400">
                              Editing: {user.name}
                            </h4>
                            <button
                              onClick={() => {
                                setEditingUser(null);
                                setEditUserForm(null);
                              }}
                              className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text"
                            >
                              <X className="w-4 h-4" />
                            </button>
                          </div>

                          <div className="grid grid-cols-2 gap-3">
                            <div>
                              <label className="block text-xs text-aegisaxiom-text-secondary mb-1">
                                Name
                              </label>
                              <input
                                type="text"
                                value={editUserForm.name}
                                onChange={(e) =>
                                  setEditUserForm({ ...editUserForm, name: e.target.value })
                                }
                                className="w-full px-2 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm"
                              />
                            </div>
                            <div>
                              <label className="block text-xs text-aegisaxiom-text-secondary mb-1">
                                Email
                              </label>
                              <input
                                type="email"
                                value={editUserForm.email}
                                onChange={(e) =>
                                  setEditUserForm({ ...editUserForm, email: e.target.value })
                                }
                                className="w-full px-2 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm"
                              />
                            </div>
                          </div>

                          <div>
                            <label className="block text-xs text-aegisaxiom-text-secondary mb-1">
                              Phone
                            </label>
                            <input
                              type="tel"
                              value={editUserForm.phone || ''}
                              onChange={(e) =>
                                setEditUserForm({ ...editUserForm, phone: e.target.value })
                              }
                              className="w-full px-2 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm"
                            />
                          </div>

                          {/* Resource Type Assignments */}
                          <div>
                            <label className="block text-xs text-aegisaxiom-text-secondary mb-2">
                              Resource Type Assignments
                            </label>
                            <div className="space-y-2 max-h-60 overflow-y-auto">
                              {resourceTypes.map((rt, idx) => {
                                const hasAssignment = editUserForm.resource_type_assignments?.some(
                                  (a) => a.resource_type_id === rt.resource_type_id,
                                );
                                const assignment = editUserForm.resource_type_assignments?.find(
                                  (a) => a.resource_type_id === rt.resource_type_id,
                                );

                                return (
                                  <div key={idx} className="bg-aegisaxiom-elevated rounded p-2">
                                    <label className="flex items-start gap-2 cursor-pointer">
                                      <input
                                        type="checkbox"
                                        checked={hasAssignment}
                                        onChange={(e) => {
                                          if (e.target.checked) {
                                            // Add assignment with default cost rate
                                            const defaultRate =
                                              rt.rate_cards?.block_hours?.default_cost_rate || 0;

                                            setEditUserForm({
                                              ...editUserForm,
                                              resource_type_assignments: [
                                                ...(editUserForm.resource_type_assignments || []),
                                                {
                                                  resource_type_id: rt.resource_type_id,
                                                  resource_type_name: rt.resource_type_name,
                                                  cost_rate: defaultRate,
                                                },
                                              ],
                                            });
                                          } else {
                                            // Remove assignment
                                            setEditUserForm({
                                              ...editUserForm,
                                              resource_type_assignments:
                                                editUserForm.resource_type_assignments.filter(
                                                  (a) => a.resource_type_id !== rt.resource_type_id,
                                                ),
                                            });
                                          }
                                        }}
                                        className="mt-0.5"
                                      />
                                      <div className="flex-1">
                                        <span className="text-aegisaxiom-text text-sm">
                                          {rt.resource_type_name}
                                        </span>

                                        {hasAssignment && (
                                          <div className="mt-2">
                                            <label className="block text-xs text-aegisaxiom-text-muted mb-1">
                                              Cost Rate ($/hr) - What we pay this contractor
                                            </label>
                                            <input
                                              type="number"
                                              value={assignment?.cost_rate || ''}
                                              onChange={(e) => {
                                                setEditUserForm({
                                                  ...editUserForm,
                                                  resource_type_assignments:
                                                    editUserForm.resource_type_assignments.map(
                                                      (a) =>
                                                        a.resource_type_id === rt.resource_type_id
                                                          ? {
                                                              ...a,
                                                              cost_rate:
                                                                parseFloat(e.target.value) || 0,
                                                            }
                                                          : a,
                                                    ),
                                                });
                                              }}
                                              className="w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-xs"
                                              placeholder="120"
                                            />
                                            {assignment &&
                                              rt.cost_rate &&
                                              parseFloat(assignment.cost_rate) > rt.cost_rate && (
                                                <p className="text-xs text-orange-600 dark:text-orange-400 mt-1">
                                                  ⚠️ Rate exceeds business approved rate ($
                                                  {rt.cost_rate}/hr) - Will require Procurement
                                                  approval
                                                </p>
                                              )}
                                          </div>
                                        )}
                                      </div>
                                    </label>
                                  </div>
                                );
                              })}
                            </div>
                          </div>

                          <div className="flex gap-2">
                            <button
                              onClick={() => {
                                setEditingUser(null);
                                setEditUserForm(null);
                              }}
                              className="px-3 py-1.5 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded text-sm"
                            >
                              Cancel
                            </button>
                            <button
                              onClick={async () => {
                                try {
                                  const response = await api.put(
                                    `${API}/api/admin/users/${user.user_id}`,
                                    editUserForm,
                                  );

                                  if (response.ok) {
                                    toast.success('Contractor user updated');
                                    loadData();
                                    setEditingUser(null);
                                    setEditUserForm(null);
                                  } else {
                                    const errorData = await response.json();
                                    toast.error(errorData.detail || 'Failed to update user');
                                  }
                                } catch (error) {
                                  console.error('Error updating user:', error);
                                  toast.error('Failed to update contractor user');
                                }
                              }}
                              className="px-3 py-1.5 bg-violet-600 hover:bg-violet-500 text-white rounded text-sm flex items-center gap-1"
                            >
                              <Save className="w-3 h-3" />
                              Save User
                            </button>
                          </div>
                        </div>
                      ) : (
                        /* VIEW MODE */
                        <>
                          <div className="flex items-start justify-between mb-2">
                            <div>
                              <h4 className="font-semibold text-aegisaxiom-text">{user.name}</h4>
                              <p className="text-sm text-aegisaxiom-text-secondary">{user.email}</p>
                            </div>
                            <div className="flex items-center gap-2">
                              <span
                                className={`px-2 py-1 rounded text-xs ${
                                  user.status === 'active'
                                    ? 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300'
                                    : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary'
                                }`}
                              >
                                {user.status}
                              </span>
                              <button
                                onClick={() => {
                                  setEditingUser(user);
                                  setEditUserForm({
                                    name: user.name,
                                    email: user.email,
                                    phone: user.phone || '',
                                    resource_type_assignments: user.resource_type_assignments || [],
                                  });
                                }}
                                className="text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 p-1"
                                title="Edit contractor user"
                              >
                                <Edit2 className="w-4 h-4" />
                              </button>
                            </div>
                          </div>

                          {/* Resource Type Assignments */}
                          <div className="mt-3">
                            <div className="text-xs text-aegisaxiom-text-secondary mb-2">
                              Resource Types:
                            </div>
                            {(user.resource_type_assignments || []).length === 0 ? (
                              <p className="text-xs text-aegisaxiom-text-muted italic">
                                No resource types assigned
                              </p>
                            ) : (
                              <div className="space-y-1">
                                {user.resource_type_assignments.map((rt, idx) => (
                                  <div
                                    key={idx}
                                    className="bg-aegisaxiom-elevated rounded px-2 py-1 text-xs flex justify-between items-center"
                                  >
                                    <span className="text-aegisaxiom-text">{rt.resource_type_name}</span>
                                    <div className="flex items-center gap-2">
                                      <span className="text-green-600 dark:text-green-400 font-medium">
                                        ${rt.cost_rate}/hr
                                      </span>
                                      {rt.approval_status === 'pending' && (
                                        <span className="px-2 py-0.5 rounded text-xs bg-orange-50 dark:bg-orange-900/40 text-orange-700 dark:text-orange-300 border border-orange-500/40">
                                          ⏳ Pending
                                        </span>
                                      )}
                                    </div>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        </>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {/* Create User Form */}
              {showCreateUserForm && (
                <div className="bg-aegisaxiom-surface border-2 border-green-600 rounded-lg p-4">
                  <h4 className="font-semibold text-aegisaxiom-text mb-3">
                    Create Contractor User Account
                  </h4>

                  <div className="space-y-3">
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                          Full Name *
                        </label>
                        <input
                          type="text"
                          value={newUserForm.name}
                          onChange={(e) => setNewUserForm({ ...newUserForm, name: e.target.value })}
                          className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                          placeholder="John Doe"
                        />
                      </div>
                      <div>
                        <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                          Email *
                        </label>
                        <input
                          type="email"
                          value={newUserForm.email}
                          onChange={(e) =>
                            setNewUserForm({ ...newUserForm, email: e.target.value })
                          }
                          className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                          placeholder="john@example.com"
                        />
                      </div>
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                        Phone
                      </label>
                      <input
                        type="tel"
                        value={newUserForm.phone}
                        onChange={(e) => setNewUserForm({ ...newUserForm, phone: e.target.value })}
                        className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                        placeholder="+1 (555) 000-0000"
                      />
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                        Assign Resource Types (from business offerings)
                      </label>
                      <div className="space-y-2 max-h-40 overflow-y-auto">
                        {resourceTypes.map((rt, idx) => (
                          <label
                            key={idx}
                            className="flex items-start gap-2 cursor-pointer bg-aegisaxiom-elevated rounded p-2"
                          >
                            <input
                              type="checkbox"
                              checked={newUserForm.resource_type_assignments.some(
                                (a) => a.resource_type_id === rt.resource_type_id,
                              )}
                              onChange={(e) => {
                                if (e.target.checked) {
                                  // Add assignment with default cost rate from business rate card
                                  const defaultRate =
                                    rt.rate_cards?.block_hours?.default_cost_rate || 0;

                                  setNewUserForm({
                                    ...newUserForm,
                                    resource_type_assignments: [
                                      ...newUserForm.resource_type_assignments,
                                      {
                                        resource_type_id: rt.resource_type_id,
                                        resource_type_name: rt.resource_type_name,
                                        cost_rate: defaultRate,
                                      },
                                    ],
                                  });
                                } else {
                                  // Remove assignment
                                  setNewUserForm({
                                    ...newUserForm,
                                    resource_type_assignments:
                                      newUserForm.resource_type_assignments.filter(
                                        (a) => a.resource_type_id !== rt.resource_type_id,
                                      ),
                                  });
                                }
                              }}
                              className="mt-0.5"
                            />
                            <div className="flex-1">
                              <span className="text-aegisaxiom-text text-sm">
                                {rt.resource_type_name}
                              </span>
                              {newUserForm.resource_type_assignments.some(
                                (a) => a.resource_type_id === rt.resource_type_id,
                              ) && (
                                <div className="mt-1">
                                  <input
                                    type="number"
                                    value={
                                      newUserForm.resource_type_assignments.find(
                                        (a) => a.resource_type_id === rt.resource_type_id,
                                      )?.cost_rate || ''
                                    }
                                    onChange={(e) => {
                                      setNewUserForm({
                                        ...newUserForm,
                                        resource_type_assignments:
                                          newUserForm.resource_type_assignments.map((a) =>
                                            a.resource_type_id === rt.resource_type_id
                                              ? { ...a, cost_rate: parseFloat(e.target.value) || 0 }
                                              : a,
                                          ),
                                      });
                                    }}
                                    className="w-full px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-xs"
                                    placeholder="Cost $/hr"
                                  />
                                  <p className="text-xs text-aegisaxiom-text-muted mt-1">
                                    Pay rate for this contractor (what we pay them)
                                  </p>
                                </div>
                              )}
                            </div>
                          </label>
                        ))}
                      </div>
                    </div>

                    <div className="flex gap-2">
                      <button
                        onClick={() => {
                          setShowCreateUserForm(false);
                          setNewUserForm({
                            name: '',
                            email: '',
                            phone: '',
                            resource_type_assignments: [],
                          });
                        }}
                        className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={createContractorUser}
                        disabled={!newUserForm.name || !newUserForm.email}
                        className="px-4 py-2 bg-green-600 hover:bg-green-500 text-white rounded-lg disabled:opacity-50 flex items-center gap-2"
                      >
                        <Save className="w-4 h-4" />
                        Create Contractor Account
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* TAB 4: Billing Information */}

          {/* TAB 4: Partnership Benefits */}
          {activeTab === 'partnership' && (
            <div className="space-y-4">
              {/* Partnership Tier */}
              <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4">
                <h4 className="text-sm font-semibold text-aegisaxiom-text mb-3 flex items-center gap-2">
                  <Award className="w-4 h-4 text-violet-600 dark:text-violet-400" />
                  Partnership Tier
                </h4>

                <div>
                  <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                    Tier Level
                  </label>
                  <select
                    value={formData.partnership_tier}
                    onChange={(e) => setFormData({ ...formData, partnership_tier: e.target.value })}
                    className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                  >
                    <option value="none">None</option>
                    <option value="bronze">🥉 Bronze Partner</option>
                    <option value="silver">🥈 Silver Partner</option>
                    <option value="gold">🥇 Gold Partner</option>
                    <option value="platinum">💎 Platinum Partner</option>
                  </select>
                  <p className="text-xs text-aegisaxiom-text-muted mt-2">
                    Partnership tier determines benefits, marketing fund allocations, and priority
                    support
                  </p>
                </div>
              </div>

              {/* Marketing Funds */}
              <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4">
                <h4 className="text-sm font-semibold text-aegisaxiom-text mb-3 flex items-center gap-2">
                  <DollarSign className="w-4 h-4 text-violet-600 dark:text-violet-400" />
                  Marketing Funds
                </h4>

                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                        Allocated ($)
                      </label>
                      <input
                        type="number"
                        value={formData.marketing_funds_allocated}
                        onChange={(e) =>
                          setFormData({
                            ...formData,
                            marketing_funds_allocated: parseFloat(e.target.value) || 0,
                          })
                        }
                        className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                        placeholder="10000"
                        min="0"
                        step="500"
                      />
                      <p className="text-xs text-aegisaxiom-text-muted mt-1">
                        Total marketing budget allocated to this partner
                      </p>
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                        Used ($)
                      </label>
                      <input
                        type="number"
                        value={formData.marketing_funds_used}
                        onChange={(e) =>
                          setFormData({
                            ...formData,
                            marketing_funds_used: parseFloat(e.target.value) || 0,
                          })
                        }
                        className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                        placeholder="2500"
                        min="0"
                        step="100"
                      />
                      <p className="text-xs text-aegisaxiom-text-muted mt-1">
                        Amount already spent/used from allocation
                      </p>
                    </div>
                  </div>

                  {/* Available Balance */}
                  <div className="bg-gradient-to-r from-green-900/20 to-green-800/10 border border-green-200 dark:border-green-700/40 rounded-lg p-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-xs text-green-600 dark:text-green-400 mb-1">
                          Available Balance
                        </p>
                        <p className="text-2xl font-bold text-green-700 dark:text-green-300">
                          $
                          {(
                            (formData.marketing_funds_allocated || 0) -
                            (formData.marketing_funds_used || 0)
                          ).toFixed(2)}
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="text-xs text-aegisaxiom-text-secondary">Utilization</p>
                        <p className="text-lg font-semibold text-aegisaxiom-text">
                          {formData.marketing_funds_allocated > 0
                            ? Math.round(
                                (formData.marketing_funds_used /
                                  formData.marketing_funds_allocated) *
                                  100,
                              )
                            : 0}
                          %
                        </p>
                      </div>
                    </div>

                    {/* Progress Bar */}
                    {formData.marketing_funds_allocated > 0 && (
                      <div className="mt-3">
                        <div className="w-full bg-aegisaxiom-elevated rounded-full h-2">
                          <div
                            className="bg-gradient-to-r from-green-500 to-emerald-400 h-2 rounded-full transition-all"
                            style={{
                              width: `${Math.min((formData.marketing_funds_used / formData.marketing_funds_allocated) * 100, 100)}%`,
                            }}
                          />
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Info Note */}
                  <div className="bg-blue-900/20 border border-blue-700/40 rounded p-3">
                    <p className="text-xs text-blue-200">
                      <AlertCircle className="w-3 h-3 inline mr-1" />
                      Marketing funds can be used for co-marketing campaigns, events, and
                      promotional activities
                    </p>
                  </div>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'billing' && (
            <div className="space-y-5">
              <div className="bg-blue-900/20 border border-blue-700/40 rounded-lg p-4 mb-4">
                <div className="flex items-start gap-2">
                  <FileText className="w-5 h-5 text-blue-600 dark:text-blue-400 flex-shrink-0 mt-0.5" />
                  <div className="text-sm text-blue-200">
                    <p className="font-semibold mb-1">Billing Information</p>
                    <p className="text-xs text-blue-200/70">
                      This information will be used to populate Accounts Payable (AP) batch payments
                    </p>
                  </div>
                </div>
              </div>

              {/* W9 Upload */}
              <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4">
                <h4 className="text-sm font-semibold text-aegisaxiom-text mb-3 flex items-center gap-2">
                  <FileText className="w-4 h-4 text-violet-600 dark:text-violet-400" />
                  W9 Tax Form
                </h4>

                <div className="space-y-3">
                  <div>
                    <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                      Tax ID / EIN
                    </label>
                    <input
                      type="text"
                      value={formData.tax_id}
                      onChange={(e) => setFormData({ ...formData, tax_id: e.target.value })}
                      className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                      placeholder="XX-XXXXXXX"
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                      W9 Form
                    </label>
                    {formData.w9_file_url ? (
                      <div className="flex items-center gap-3 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-700/40 rounded-lg p-3">
                        <FileText className="w-5 h-5 text-green-600 dark:text-green-400" />
                        <div className="flex-1">
                          <p className="text-sm text-aegisaxiom-text">W9 on file</p>
                          <a
                            href={formData.w9_file_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs text-green-600 dark:text-green-400 hover:underline"
                          >
                            View document →
                          </a>
                        </div>
                        <button
                          onClick={() => setFormData({ ...formData, w9_file_url: '' })}
                          className="text-red-600 dark:text-red-400 hover:text-red-700 dark:text-red-300"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    ) : (
                      <div className="border-2 border-dashed border-aegisaxiom-border rounded-lg p-6 text-center">
                        <input
                          type="file"
                          id="w9-upload"
                          accept=".pdf,.jpg,.jpeg,.png"
                          onChange={async (e) => {
                            const file = e.target.files[0];
                            if (!file) return;

                            setUploading(true);
                            try {
                              const formDataUpload = new FormData();
                              formDataUpload.append('file', file);

                              const response = await fetch(`${API}/api/admin/upload`, {
                                method: 'POST',
                                headers: {
                                  Authorization: `Bearer ${localStorage.getItem('aegisaxiom_session_token')}`,
                                },
                                body: formDataUpload,
                              });

                              if (response.ok) {
                                const data = await response.json();
                                setFormData({ ...formData, w9_file_url: data.file_url });
                                toast.success('W9 uploaded successfully');
                              } else {
                                toast.error('Failed to upload W9');
                              }
                            } catch (error) {
                              console.error('Error uploading W9:', error);
                              toast.error('Failed to upload W9');
                            } finally {
                              setUploading(false);
                            }
                          }}
                          className="hidden"
                        />
                        <label htmlFor="w9-upload" className="cursor-pointer">
                          <FileText className="w-10 h-10 text-aegisaxiom-text-muted mx-auto mb-2" />
                          <p className="text-sm text-aegisaxiom-text-secondary mb-1">
                            {uploading ? 'Uploading...' : 'Click to upload W9'}
                          </p>
                          <p className="text-xs text-aegisaxiom-text-muted">PDF, JPG, or PNG</p>
                        </label>
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {/* Billing Contact Information */}
              <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4">
                <h4 className="text-sm font-semibold text-aegisaxiom-text mb-3 flex items-center gap-2">
                  <Mail className="w-4 h-4 text-violet-600 dark:text-violet-400" />
                  Billing Contact
                </h4>

                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                        Contact Name
                      </label>
                      <input
                        type="text"
                        value={formData.billing_contact_name}
                        onChange={(e) =>
                          setFormData({ ...formData, billing_contact_name: e.target.value })
                        }
                        className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                        placeholder="Accounts Payable Manager"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                        Billing Email *
                      </label>
                      <input
                        type="email"
                        value={formData.billing_email}
                        onChange={(e) =>
                          setFormData({ ...formData, billing_email: e.target.value })
                        }
                        className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                        placeholder="billing@contractor.com"
                      />
                      <p className="text-xs text-aegisaxiom-text-muted mt-1">Where to send invoices</p>
                    </div>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                      Billing Phone
                    </label>
                    <input
                      type="tel"
                      value={formData.billing_phone}
                      onChange={(e) => setFormData({ ...formData, billing_phone: e.target.value })}
                      className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                      placeholder="+1 (555) 000-0000"
                    />
                    <p className="text-xs text-aegisaxiom-text-muted mt-1">For billing questions</p>
                  </div>
                </div>
              </div>

              {/* Billing Address */}
              <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4">
                <h4 className="text-sm font-semibold text-aegisaxiom-text mb-3 flex items-center gap-2">
                  <MapPin className="w-4 h-4 text-violet-600 dark:text-violet-400" />
                  Billing Address
                </h4>

                <div className="space-y-3">
                  <div>
                    <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                      Street Address
                    </label>
                    <input
                      type="text"
                      value={formData.billing_address_street}
                      onChange={(e) =>
                        setFormData({ ...formData, billing_address_street: e.target.value })
                      }
                      className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                      placeholder="123 Main St"
                    />
                  </div>

                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                        City
                      </label>
                      <input
                        type="text"
                        value={formData.billing_address_city}
                        onChange={(e) =>
                          setFormData({ ...formData, billing_address_city: e.target.value })
                        }
                        className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                        State
                      </label>
                      <input
                        type="text"
                        value={formData.billing_address_state}
                        onChange={(e) =>
                          setFormData({ ...formData, billing_address_state: e.target.value })
                        }
                        className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                        placeholder="CA"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                        ZIP
                      </label>
                      <input
                        type="text"
                        value={formData.billing_address_zip}
                        onChange={(e) =>
                          setFormData({ ...formData, billing_address_zip: e.target.value })
                        }
                        className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                      />
                    </div>
                  </div>
                </div>
              </div>

              {/* Payment Method */}
              <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4">
                <h4 className="text-sm font-semibold text-aegisaxiom-text mb-3 flex items-center gap-2">
                  <DollarSign className="w-4 h-4 text-violet-600 dark:text-violet-400" />
                  Payment Method
                </h4>

                <div>
                  <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                    Preferred Payment Method
                  </label>
                  <select
                    value={formData.payment_method}
                    onChange={(e) => setFormData({ ...formData, payment_method: e.target.value })}
                    className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                  >
                    <option value="check">Check</option>
                    <option value="ach">ACH / Direct Deposit</option>
                    <option value="wire">Wire Transfer</option>
                    <option value="credit_card">Credit Card</option>
                  </select>
                  <p className="text-xs text-aegisaxiom-text-muted mt-1">How to pay this contractor</p>
                </div>
              </div>

              {/* Bank Account Details (ACH/Wire) */}
              <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4">
                <h4 className="text-sm font-semibold text-aegisaxiom-text mb-3 flex items-center gap-2">
                  <DollarSign className="w-4 h-4 text-violet-600 dark:text-violet-400" />
                  Bank Account Details (ACH/Wire Payments)
                </h4>

                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                        Bank Name
                      </label>
                      <input
                        type="text"
                        value={formData.bank_name}
                        onChange={(e) => setFormData({ ...formData, bank_name: e.target.value })}
                        className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                        placeholder="Chase Bank"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                        Account Holder Name
                      </label>
                      <input
                        type="text"
                        value={formData.account_holder_name}
                        onChange={(e) =>
                          setFormData({ ...formData, account_holder_name: e.target.value })
                        }
                        className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                        placeholder="Business Legal Name"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                        Routing Number
                      </label>
                      <input
                        type="text"
                        value={formData.routing_number}
                        onChange={(e) =>
                          setFormData({ ...formData, routing_number: e.target.value })
                        }
                        className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                        placeholder="123456789"
                        maxLength="9"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                        Account Number
                      </label>
                      <input
                        type="text"
                        value={formData.account_number}
                        onChange={(e) =>
                          setFormData({ ...formData, account_number: e.target.value })
                        }
                        className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                        placeholder="************1234"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                        Account Type
                      </label>
                      <select
                        value={formData.account_type}
                        onChange={(e) => setFormData({ ...formData, account_type: e.target.value })}
                        className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                      >
                        <option value="checking">Checking</option>
                        <option value="savings">Savings</option>
                      </select>
                    </div>
                  </div>

                  <div className="bg-blue-900/20 border border-blue-700/40 rounded p-3">
                    <p className="text-xs text-blue-200">
                      <Shield className="w-3 h-3 inline mr-1" />
                      Bank details are stored securely and used for ACH/Wire payments
                    </p>
                  </div>
                </div>
              </div>

              {/* Payment Activity Section */}
              <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4">
                <h4 className="text-sm font-semibold text-aegisaxiom-text mb-3 flex items-center gap-2">
                  <Calendar className="w-4 h-4 text-violet-600 dark:text-violet-400" />
                  Payment Activity
                </h4>

                {paymentLoading ? (
                  <div className="flex items-center justify-center py-8">
                    <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-violet-500"></div>
                    <span className="ml-2 text-sm text-aegisaxiom-text-secondary">
                      Loading payment data...
                    </span>
                  </div>
                ) : (
                  <>
                    {/* Summary Cards */}
                    <div className="grid grid-cols-5 gap-3 mb-4">
                      <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg p-3 text-center">
                        <div className="text-xs text-aegisaxiom-text-secondary mb-1">Total Hours</div>
                        <div className="text-lg font-bold text-aegisaxiom-text">
                          {(paymentSummary?.total_hours || 0).toFixed(1)}h
                        </div>
                      </div>
                      <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg p-3 text-center">
                        <div className="text-xs text-aegisaxiom-text-secondary mb-1">Total Billed</div>
                        <div className="text-lg font-bold text-aegisaxiom-text">
                          $
                          {(paymentSummary?.total_billed || 0).toLocaleString(undefined, {
                            minimumFractionDigits: 2,
                            maximumFractionDigits: 2,
                          })}
                        </div>
                      </div>
                      <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg p-3 text-center">
                        <div className="text-xs text-aegisaxiom-text-secondary mb-1">Paid</div>
                        <div className="text-lg font-bold text-green-600 dark:text-green-400">
                          $
                          {(paymentSummary?.total_paid || 0).toLocaleString(undefined, {
                            minimumFractionDigits: 2,
                            maximumFractionDigits: 2,
                          })}
                        </div>
                      </div>
                      <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg p-3 text-center">
                        <div className="text-xs text-aegisaxiom-text-secondary mb-1">Outstanding</div>
                        <div className="text-lg font-bold text-red-600 dark:text-red-400">
                          $
                          {(paymentSummary?.outstanding || 0).toLocaleString(undefined, {
                            minimumFractionDigits: 2,
                            maximumFractionDigits: 2,
                          })}
                        </div>
                      </div>
                      <div className="bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg p-3 text-center">
                        <div className="text-xs text-aegisaxiom-text-secondary mb-1">Avg Rate</div>
                        <div className="text-lg font-bold text-violet-600 dark:text-violet-400">
                          $
                          {(paymentSummary?.avg_hourly_rate || 0).toLocaleString(undefined, {
                            minimumFractionDigits: 2,
                            maximumFractionDigits: 2,
                          })}
                          /hr
                        </div>
                      </div>
                    </div>

                    {/* Filters */}
                    <div className="flex items-center gap-3 mb-4 bg-aegisaxiom-accent-muted border border-aegisaxiom-border rounded-lg p-3">
                      <div className="flex items-center gap-2">
                        <label className="text-xs text-aegisaxiom-text-secondary whitespace-nowrap">
                          From:
                        </label>
                        <input
                          type="date"
                          value={paymentDateFrom}
                          onChange={(e) => setPaymentDateFrom(e.target.value)}
                          className="px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-xs focus:outline-none focus:border-violet-500"
                        />
                      </div>
                      <div className="flex items-center gap-2">
                        <label className="text-xs text-aegisaxiom-text-secondary whitespace-nowrap">
                          To:
                        </label>
                        <input
                          type="date"
                          value={paymentDateTo}
                          onChange={(e) => setPaymentDateTo(e.target.value)}
                          className="px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-xs focus:outline-none focus:border-violet-500"
                        />
                      </div>
                      <div className="flex items-center gap-2">
                        <label className="text-xs text-aegisaxiom-text-secondary whitespace-nowrap">
                          Status:
                        </label>
                        <select
                          value={paymentStatus}
                          onChange={(e) => setPaymentStatus(e.target.value)}
                          className="px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-xs focus:outline-none focus:border-violet-500"
                        >
                          <option value="">All</option>
                          <option value="pending">Pending</option>
                          <option value="approved">Approved</option>
                          <option value="paid">Paid</option>
                          <option value="draft">Draft</option>
                        </select>
                      </div>
                      <button
                        onClick={() => {
                          setPaymentDateFrom('');
                          setPaymentDateTo('');
                          setPaymentStatus('');
                        }}
                        className={`text-xs transition ml-auto px-2 py-1 rounded ${
                          paymentDateFrom || paymentDateTo || paymentStatus
                            ? 'text-white bg-violet-50 dark:bg-violet-600/20 border border-violet-200 dark:border-violet-600/30 hover:bg-violet-600/30'
                            : 'text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary'
                        }`}
                      >
                        Clear Filters
                      </button>
                    </div>

                    {/* Time Entries Table */}
                    <div className="mb-4">
                      <h5 className="text-xs font-semibold text-aegisaxiom-text-secondary mb-2 flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        Time Entries ({paymentActivity?.time_entries?.length || 0})
                      </h5>
                      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded overflow-hidden">
                        <table className="w-full text-xs">
                          <thead className="bg-aegisaxiom-elevated">
                            <tr>
                              <th className="px-3 py-2 text-left text-aegisaxiom-text-secondary font-medium">
                                Date
                              </th>
                              <th className="px-3 py-2 text-left text-aegisaxiom-text-secondary font-medium">
                                User
                              </th>
                              <th className="px-3 py-2 text-left text-aegisaxiom-text-secondary font-medium">
                                Project
                              </th>
                              <th className="px-3 py-2 text-right text-aegisaxiom-text-secondary font-medium">
                                Hours
                              </th>
                              <th className="px-3 py-2 text-right text-aegisaxiom-text-secondary font-medium">
                                Rate
                              </th>
                              <th className="px-3 py-2 text-center text-aegisaxiom-text-secondary font-medium">
                                Status
                              </th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-aegisaxiom-border">
                            {(paymentActivity?.time_entries || []).length === 0 ? (
                              <tr>
                                <td
                                  colSpan={6}
                                  className="px-3 py-4 text-center text-aegisaxiom-text-muted"
                                >
                                  No time entries found
                                </td>
                              </tr>
                            ) : (
                              (paymentActivity?.time_entries || []).map((te, idx) => (
                                <tr
                                  key={te.time_entry_id || idx}
                                  className="hover:bg-aegisaxiom-accent-muted"
                                >
                                  <td className="px-3 py-2 text-aegisaxiom-text-secondary">
                                    {te.date || '-'}
                                  </td>
                                  <td className="px-3 py-2 text-aegisaxiom-text-secondary">
                                    {te.user_name || '-'}
                                  </td>
                                  <td className="px-3 py-2 text-aegisaxiom-text-secondary">
                                    {te.project_name || te.project_id || '-'}
                                  </td>
                                  <td className="px-3 py-2 text-right text-aegisaxiom-text font-medium">
                                    {(te.hours || 0).toFixed(1)}h
                                  </td>
                                  <td className="px-3 py-2 text-right text-aegisaxiom-text-secondary">
                                    {te.bill_rate
                                      ? `$${(te.bill_rate || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                                      : '-'}
                                  </td>
                                  <td className="px-3 py-2 text-center">
                                    <span
                                      className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                                        te.approval_status === 'approved'
                                          ? 'bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-700/50 text-green-700 dark:text-green-300'
                                          : te.approval_status === 'pending'
                                            ? 'bg-amber-50 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-700/50 text-amber-700 dark:text-amber-300'
                                            : te.approval_status === 'rejected'
                                              ? 'bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700/50 text-red-700 dark:text-red-300'
                                              : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary'
                                      }`}
                                    >
                                      {te.approval_status || 'N/A'}
                                    </span>
                                  </td>
                                </tr>
                              ))
                            )}
                          </tbody>
                        </table>
                      </div>
                      {paymentActivity?.summary && (
                        <div className="mt-2 text-right">
                          <span className="text-sm text-aegisaxiom-text-secondary">Total Hours: </span>
                          <span className="text-sm font-bold text-aegisaxiom-text">
                            {(paymentActivity.summary.total_hours || 0).toFixed(1)}h
                          </span>
                        </div>
                      )}
                    </div>

                    {/* AP Bills Table */}
                    <div>
                      <h5 className="text-xs font-semibold text-aegisaxiom-text-secondary mb-2 flex items-center gap-1">
                        <DollarSign className="w-3 h-3" />
                        AP Bills ({paymentActivity?.ap_bills?.length || 0})
                      </h5>
                      <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded overflow-hidden">
                        <table className="w-full text-xs">
                          <thead className="bg-aegisaxiom-elevated">
                            <tr>
                              <th className="px-3 py-2 text-left text-aegisaxiom-text-secondary font-medium">
                                Date
                              </th>
                              <th className="px-3 py-2 text-left text-aegisaxiom-text-secondary font-medium">
                                Description
                              </th>
                              <th className="px-3 py-2 text-right text-aegisaxiom-text-secondary font-medium">
                                Amount
                              </th>
                              <th className="px-3 py-2 text-center text-aegisaxiom-text-secondary font-medium">
                                Status
                              </th>
                              <th className="px-3 py-2 text-left text-aegisaxiom-text-secondary font-medium">
                                Payment Date
                              </th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-aegisaxiom-border">
                            {(paymentActivity?.ap_bills || []).length === 0 ? (
                              <tr>
                                <td
                                  colSpan={5}
                                  className="px-3 py-4 text-center text-aegisaxiom-text-muted"
                                >
                                  No AP bills found
                                </td>
                              </tr>
                            ) : (
                              (paymentActivity?.ap_bills || []).map((bill, idx) => (
                                <tr
                                  key={bill.bill_id || idx}
                                  className="hover:bg-aegisaxiom-accent-muted"
                                >
                                  <td className="px-3 py-2 text-aegisaxiom-text-secondary">
                                    {bill.bill_date || '-'}
                                  </td>
                                  <td className="px-3 py-2 text-aegisaxiom-text-secondary">
                                    {bill.description || bill.memo || bill.bill_number || '-'}
                                  </td>
                                  <td
                                    className="px-3 py-2 text-right font-medium"
                                    style={{
                                      color:
                                        bill.status === 'paid'
                                          ? '#4ade80'
                                          : bill.status === 'pending' || bill.status === 'draft'
                                            ? '#fbbf24'
                                            : '#fff',
                                    }}
                                  >
                                    $
                                    {(bill.amount || bill.total || 0).toLocaleString(undefined, {
                                      minimumFractionDigits: 2,
                                      maximumFractionDigits: 2,
                                    })}
                                  </td>
                                  <td className="px-3 py-2 text-center">
                                    <span
                                      className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                                        bill.status === 'paid'
                                          ? 'bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-700/50 text-green-700 dark:text-green-300'
                                          : bill.status === 'pending' || bill.status === 'draft'
                                            ? 'bg-amber-50 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-700/50 text-amber-700 dark:text-amber-300'
                                            : bill.status === 'approved'
                                              ? 'bg-blue-900/30 border border-blue-700/50 text-blue-700 dark:text-blue-300'
                                              : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary'
                                      }`}
                                    >
                                      {bill.status || 'N/A'}
                                    </span>
                                  </td>
                                  <td className="px-3 py-2 text-aegisaxiom-text-secondary">
                                    {bill.payment_date || '-'}
                                  </td>
                                </tr>
                              ))
                            )}
                          </tbody>
                        </table>
                      </div>
                      {paymentActivity?.summary && (
                        <div className="mt-2 flex items-center justify-end gap-4">
                          <span className="text-sm text-aegisaxiom-text-secondary">
                            Pending:{' '}
                            <span className="font-bold text-amber-600 dark:text-amber-400">
                              $
                              {(paymentActivity.summary.pending_amount || 0).toLocaleString(
                                undefined,
                                { minimumFractionDigits: 2, maximumFractionDigits: 2 },
                              )}
                            </span>
                          </span>
                          <span className="text-sm text-aegisaxiom-text-secondary">
                            Paid:{' '}
                            <span className="font-bold text-green-600 dark:text-green-400">
                              $
                              {(paymentActivity.summary.paid_amount || 0).toLocaleString(
                                undefined,
                                { minimumFractionDigits: 2, maximumFractionDigits: 2 },
                              )}
                            </span>
                          </span>
                          <span className="text-sm text-aegisaxiom-text-secondary">
                            Outstanding:{' '}
                            <span className="font-bold text-red-600 dark:text-red-400">
                              $
                              {(paymentActivity.summary.outstanding || 0).toLocaleString(
                                undefined,
                                { minimumFractionDigits: 2, maximumFractionDigits: 2 },
                              )}
                            </span>
                          </span>
                        </div>
                      )}
                    </div>

                    {/* Last Payment Info */}
                    {paymentSummary?.last_payment_date && (
                      <div className="mt-3 bg-aegisaxiom-accent-muted border border-aegisaxiom-border rounded p-2 text-xs text-aegisaxiom-text-secondary text-right">
                        Last payment received:{' '}
                        <span className="text-aegisaxiom-text font-medium">
                          {paymentSummary.last_payment_date}
                        </span>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          )}

          {/* TAB 5: Grievances */}
          {activeTab === 'grievances' && (
            <div className="space-y-4">
              <div className="flex justify-between items-center mb-4">
                <p className="text-sm text-aegisaxiom-text-secondary">
                  Track issues, complaints, and resolutions with this contractor
                </p>
                <button
                  onClick={() => setShowAddGrievance(true)}
                  className="px-4 py-2 bg-red-600 hover:bg-red-500 text-white rounded-lg text-sm flex items-center gap-2"
                >
                  <AlertCircle className="w-4 h-4" />
                  Report Grievance
                </button>
              </div>

              {/* Grievances List */}
              {grievances.length === 0 ? (
                <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-8 text-center">
                  <Shield className="w-12 h-12 text-aegisaxiom-text-muted mx-auto mb-3" />
                  <p className="text-aegisaxiom-text-secondary text-sm mb-2">No grievances on record</p>
                  <p className="text-xs text-aegisaxiom-text-muted">
                    This contractor has a clean history
                  </p>
                </div>
              ) : (
                <div className="space-y-3">
                  {grievances.map((grievance) => (
                    <div
                      key={grievance.grievance_id}
                      className={`border-2 rounded-lg p-4 ${
                        grievance.status === 'resolved'
                          ? 'bg-green-50 dark:bg-green-900/10 border-green-200 dark:border-green-700/40'
                          : grievance.severity === 'high'
                            ? 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-700/50'
                            : grievance.severity === 'medium'
                              ? 'bg-yellow-50 dark:bg-yellow-900/20 border-yellow-200 dark:border-yellow-700/50'
                              : 'bg-aegisaxiom-surface border-aegisaxiom-border'
                      }`}
                    >
                      <div className="flex items-start justify-between mb-3">
                        <div className="flex-1">
                          <div className="flex items-center gap-2 mb-1">
                            <h5 className="font-semibold text-aegisaxiom-text">{grievance.title}</h5>
                            <span
                              className={`px-2 py-0.5 rounded text-xs font-medium ${
                                grievance.severity === 'high'
                                  ? 'bg-red-50 dark:bg-red-900/50 text-red-700 dark:text-red-300'
                                  : grievance.severity === 'medium'
                                    ? 'bg-yellow-50 dark:bg-yellow-900/50 text-yellow-700 dark:text-yellow-300'
                                    : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary'
                              }`}
                            >
                              {grievance.severity.toUpperCase()}
                            </span>
                            <span
                              className={`px-2 py-0.5 rounded text-xs font-medium ${
                                grievance.status === 'resolved'
                                  ? 'bg-green-50 dark:bg-green-900/50 text-green-700 dark:text-green-300'
                                  : grievance.status === 'in_progress'
                                    ? 'bg-blue-900/50 text-blue-700 dark:text-blue-300'
                                    : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm-secondary'
                              }`}
                            >
                              {grievance.status.replace('_', ' ').toUpperCase()}
                            </span>
                          </div>
                          <p className="text-sm text-aegisaxiom-text-secondary mb-2">
                            {grievance.description}
                          </p>
                          <p className="text-xs text-aegisaxiom-text-muted">
                            Reported by {grievance.reported_by_name} •{' '}
                            {new Date(grievance.created_at).toLocaleDateString()}
                          </p>
                        </div>
                      </div>

                      {/* Comments Section */}
                      <div className="mt-3 pt-3 border-t border-aegisaxiom-border">
                        <div className="text-xs font-semibold text-aegisaxiom-text-secondary mb-2">
                          Comments ({(grievance.comments || []).length})
                        </div>

                        {(grievance.comments || []).length > 0 && (
                          <div className="space-y-2 mb-3 max-h-40 overflow-y-auto">
                            {grievance.comments.map((comment, idx) => (
                              <div key={idx} className="bg-aegisaxiom-elevated rounded p-2">
                                <div className="flex justify-between items-start mb-1">
                                  <span className="text-xs font-medium text-aegisaxiom-text">
                                    {comment.commenter_name}
                                  </span>
                                  <span className="text-xs text-aegisaxiom-text-muted">
                                    {new Date(comment.created_at).toLocaleDateString()}
                                  </span>
                                </div>
                                <p className="text-xs text-aegisaxiom-text-secondary">
                                  {comment.comment}
                                </p>
                              </div>
                            ))}
                          </div>
                        )}

                        {/* Add Comment */}
                        <div className="flex gap-2">
                          <input
                            type="text"
                            placeholder="Add a comment..."
                            value={grievanceComments[grievance.grievance_id] || ''}
                            onChange={(e) =>
                              setGrievanceComments({
                                ...grievanceComments,
                                [grievance.grievance_id]: e.target.value,
                              })
                            }
                            className="flex-1 px-3 py-1.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded text-aegisaxiom-text text-sm"
                            onKeyPress={async (e) => {
                              if (
                                e.key === 'Enter' &&
                                grievanceComments[grievance.grievance_id]?.trim()
                              ) {
                                try {
                                  const response = await api.post(
                                    `${API}/api/sales-ops/grievances/${grievance.grievance_id}/comments`,
                                    { comment: grievanceComments[grievance.grievance_id] },
                                  );

                                  if (response.ok) {
                                    toast.success('Comment added');
                                    setGrievanceComments({
                                      ...grievanceComments,
                                      [grievance.grievance_id]: '',
                                    });
                                    loadData();
                                  }
                                } catch (error) {
                                  console.error('Error adding comment:', error);
                                  toast.error('Failed to add comment');
                                }
                              }
                            }}
                          />
                          {grievance.status !== 'resolved' && (
                            <button
                              onClick={async () => {
                                try {
                                  const response = await api.patch(
                                    `${API}/api/sales-ops/grievances/${grievance.grievance_id}/status`,
                                    { status: 'resolved' },
                                  );

                                  if (response.ok) {
                                    toast.success('Grievance marked as resolved');
                                    loadData();
                                  }
                                } catch (error) {
                                  console.error('Error resolving grievance:', error);
                                  toast.error('Failed to resolve grievance');
                                }
                              }}
                              className="px-3 py-1.5 bg-green-600 hover:bg-green-500 text-white rounded text-sm"
                            >
                              Resolve
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Add Grievance Form */}
              {showAddGrievance && (
                <div className="bg-red-50 dark:bg-red-900/20 border-2 border-red-200 dark:border-red-700/50 rounded-lg p-4">
                  <h4 className="font-semibold text-aegisaxiom-text mb-3">Report New Grievance</h4>

                  <div className="space-y-3">
                    <div>
                      <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                        Severity *
                      </label>
                      <select
                        value={newGrievance.severity}
                        onChange={(e) =>
                          setNewGrievance({ ...newGrievance, severity: e.target.value })
                        }
                        className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                      >
                        <option value="low">Low</option>
                        <option value="medium">Medium</option>
                        <option value="high">High</option>
                      </select>
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                        Title *
                      </label>
                      <input
                        type="text"
                        value={newGrievance.title}
                        onChange={(e) =>
                          setNewGrievance({ ...newGrievance, title: e.target.value })
                        }
                        className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                        placeholder="Brief summary of the issue"
                      />
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                        Description *
                      </label>
                      <textarea
                        value={newGrievance.description}
                        onChange={(e) =>
                          setNewGrievance({ ...newGrievance, description: e.target.value })
                        }
                        rows={3}
                        className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
                        placeholder="Detailed description of the grievance..."
                      />
                    </div>

                    <div className="flex gap-2">
                      <button
                        onClick={() => {
                          setShowAddGrievance(false);
                          setNewGrievance({ title: '', description: '', severity: 'medium' });
                        }}
                        className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={async () => {
                          if (!newGrievance.title || !newGrievance.description) {
                            toast.error('Please fill in title and description');
                            return;
                          }

                          try {
                            const response = await api.post(
                              `${API}/api/sales-ops/contractor-businesses/${contractor.business_id}/grievances`,
                              newGrievance,
                            );

                            if (response.ok) {
                              toast.success('Grievance reported');
                              setShowAddGrievance(false);
                              setNewGrievance({ title: '', description: '', severity: 'medium' });
                              loadData();
                            } else {
                              const errorData = await response.json();
                              toast.error(errorData.detail || 'Failed to report grievance');
                            }
                          } catch (error) {
                            console.error('Error reporting grievance:', error);
                            toast.error('Failed to report grievance');
                          }
                        }}
                        className="px-4 py-2 bg-red-600 hover:bg-red-500 text-white rounded-lg flex items-center gap-2"
                      >
                        <Save className="w-4 h-4" />
                        Report Grievance
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer - Save Button */}
        <div className="p-6 border-t border-aegisaxiom-border flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-6 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-lg disabled:opacity-50 flex items-center gap-2"
          >
            <Save className="w-4 h-4" />
            {saving ? 'Saving...' : 'Save Changes'}
          </button>
        </div>
      </div>
      <ConfirmModal
        isOpen={confirmState.open}
        title={confirmState.title}
        message={confirmState.message}
        confirmText="Remove"
        onConfirm={() => confirmState.action?.()}
        onCancel={() => setConfirmState({ open: false, action: null, title: '', message: '' })}
      />
    </div>
  );
}

// Add Contractor Business Modal - Minimal create form
function AddContractorBusinessModal({ onClose, onSave }) {
  const [saving, setSaving] = useState(false);
  const [formData, setFormData] = useState({
    business_name: '',
    contact_name: '',
    email: '',
    phone: '',
    website: '',
    address_street: '',
    address_city: '',
    address_state: '',
    address_zip: '',
    partnership_tier: 'none',
    status: 'active',
    notes: '',
  });

  const handleSubmit = async () => {
    if (!formData.business_name.trim()) {
      toast.error('Business name is required');
      return;
    }
    setSaving(true);
    try {
      await onSave(formData);
    } catch (err) {
      toast.error(err?.message || 'Failed to create contractor');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-[70] p-4">
      <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
        <div className="p-6 border-b border-aegisaxiom-border flex items-center justify-between">
          <div>
            <h2 className="text-xl font-bold text-aegisaxiom-text">Add Contractor Business</h2>
            <p className="text-sm text-aegisaxiom-text-secondary mt-1">
              Create a new contractor business record
            </p>
          </div>
          <button onClick={onClose} className="text-aegisaxiom-text-secondary hover:text-aegisaxiom-text">
            <X className="w-6 h-6" />
          </button>
        </div>

        <div className="p-6 overflow-y-auto flex-1 space-y-4">
          <div>
            <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
              Business Name *
            </label>
            <input
              type="text"
              value={formData.business_name}
              onChange={(e) => setFormData({ ...formData, business_name: e.target.value })}
              className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
              placeholder="Acme Contractors LLC"
              autoFocus
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                Primary Contact
              </label>
              <input
                type="text"
                value={formData.contact_name}
                onChange={(e) => setFormData({ ...formData, contact_name: e.target.value })}
                className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                placeholder="John Smith"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                Email
              </label>
              <input
                type="email"
                value={formData.email}
                onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                placeholder="contact@acme.com"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                Phone
              </label>
              <input
                type="tel"
                value={formData.phone}
                onChange={(e) => setFormData({ ...formData, phone: e.target.value })}
                className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                placeholder="+1 (555) 000-0000"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                Website
              </label>
              <input
                type="url"
                value={formData.website}
                onChange={(e) => setFormData({ ...formData, website: e.target.value })}
                className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                placeholder="https://acme.com"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
              Street Address
            </label>
            <input
              type="text"
              value={formData.address_street}
              onChange={(e) => setFormData({ ...formData, address_street: e.target.value })}
              className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
              placeholder="123 Main St"
            />
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                City
              </label>
              <input
                type="text"
                value={formData.address_city}
                onChange={(e) => setFormData({ ...formData, address_city: e.target.value })}
                className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                State
              </label>
              <input
                type="text"
                value={formData.address_state}
                onChange={(e) => setFormData({ ...formData, address_state: e.target.value })}
                className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
                placeholder="CA"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">ZIP</label>
              <input
                type="text"
                value={formData.address_zip}
                onChange={(e) => setFormData({ ...formData, address_zip: e.target.value })}
                className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                Partnership Tier
              </label>
              <select
                value={formData.partnership_tier}
                onChange={(e) => setFormData({ ...formData, partnership_tier: e.target.value })}
                className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
              >
                <option value="none">None</option>
                <option value="bronze">Bronze</option>
                <option value="silver">Silver</option>
                <option value="gold">Gold</option>
                <option value="platinum">Platinum</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
                Status
              </label>
              <select
                value={formData.status}
                onChange={(e) => setFormData({ ...formData, status: e.target.value })}
                className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
              >
                <option value="active">Active</option>
                <option value="inactive">Inactive</option>
              </select>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">Notes</label>
            <textarea
              value={formData.notes}
              onChange={(e) => setFormData({ ...formData, notes: e.target.value })}
              rows={3}
              className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
              placeholder="Any additional notes about this contractor..."
            />
          </div>

          <div className="bg-blue-900/20 border border-blue-700/40 rounded p-3 text-xs text-blue-200">
            <AlertCircle className="w-3 h-3 inline mr-1" />
            You can add resource types, contractor users, billing details, and more after creating
            the business.
          </div>
        </div>

        <div className="p-6 border-t border-aegisaxiom-border flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={saving || !formData.business_name.trim()}
            className="px-6 py-2 bg-violet-600 hover:bg-violet-500 text-white rounded-lg disabled:opacity-50 flex items-center gap-2"
          >
            <Save className="w-4 h-4" />
            {saving ? 'Creating...' : 'Create Contractor'}
          </button>
        </div>
      </div>
    </div>
  );
}

// Create Service Form Component
function AddServiceForm({ businessId, onClose, onSuccess }) {
  const [submitting, setSubmitting] = useState(false);
  const [formData, setFormData] = useState({
    service_name: '',
    service_description: '',
    recurrency: 'monthly',
    cost_to_business: '',
    notes: '',
  });

  const handleSubmit = async () => {
    if (!formData.service_name || !formData.cost_to_business) {
      toast.error('Please fill in service name and cost');
      return;
    }

    setSubmitting(true);
    try {
      const response = await api.post(
        `${API}/api/sales-ops/contractor-businesses/${businessId}/services`,
        formData,
      );

      if (response.ok) {
        toast.success('Service created - Pending Procurement approval & sell price');
        onSuccess();
      } else {
        const errorData = await response.json();
        toast.error(errorData.detail || 'Failed to create service');
      }
    } catch (error) {
      console.error('Error creating service:', error);
      toast.error('Failed to create service');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4">
      <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <div className="p-6 border-b border-aegisaxiom-border sticky top-0 bg-aegisaxiom-base z-10">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-bold text-aegisaxiom-text">Create New Wholesale MRR Service</h3>
            <button onClick={onClose} className="p-2 hover:bg-aegisaxiom-elevated rounded-lg transition">
              <X className="w-5 h-5 text-aegisaxiom-text-secondary" />
            </button>
          </div>
          <p className="text-sm text-aegisaxiom-text-secondary mt-2">
            Create a new productized service offering for this contractor
          </p>
        </div>

        <div className="p-6 space-y-4">
          {/* Service Name */}
          <div>
            <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
              Service Name *
            </label>
            <input
              type="text"
              value={formData.service_name}
              onChange={(e) => setFormData({ ...formData, service_name: e.target.value })}
              className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
              placeholder="e.g., ThreatWatch Pro, SimpliSecure Custom"
            />
          </div>

          {/* Service Description */}
          <div>
            <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
              Description
            </label>
            <textarea
              value={formData.service_description}
              onChange={(e) => setFormData({ ...formData, service_description: e.target.value })}
              rows={3}
              className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
              placeholder="Describe what this service includes..."
            />
          </div>

          {/* Recurrency */}
          <div>
            <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
              Recurrency *
            </label>
            <select
              value={formData.recurrency}
              onChange={(e) => setFormData({ ...formData, recurrency: e.target.value })}
              className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
            >
              <option value="one-time">One-Time</option>
              <option value="daily">Daily</option>
              <option value="weekly">Weekly</option>
              <option value="monthly">Monthly</option>
              <option value="annually">Annually</option>
            </select>
          </div>

          {/* Your Cost */}
          <div>
            <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">
              Your Cost (per {formData.recurrency}) *
            </label>
            <input
              type="number"
              value={formData.cost_to_business}
              onChange={(e) => setFormData({ ...formData, cost_to_business: e.target.value })}
              className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text"
              placeholder="199"
            />
            <p className="text-xs text-aegisaxiom-text-secondary mt-1">
              What you charge us for this service
            </p>
          </div>

          {/* Notes */}
          <div>
            <label className="block text-sm font-medium text-aegisaxiom-text-secondary mb-2">Notes</label>
            <textarea
              value={formData.notes}
              onChange={(e) => setFormData({ ...formData, notes: e.target.value })}
              rows={2}
              className="w-full px-3 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg text-aegisaxiom-text text-sm"
              placeholder="Any special terms or implementation notes..."
            />
          </div>

          {/* Info Banner */}
          <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700/40 rounded-lg p-3 text-sm text-amber-700 dark:text-amber-300">
            <strong>📋 Procurement Review Required:</strong> This service will be reviewed by
            Procurement, who will set the customer sell price and approve before it's available in
            Solutions Design.
          </div>

          {/* Actions */}
          <div className="flex gap-3 pt-4">
            <button
              onClick={handleSubmit}
              disabled={submitting || !formData.service_name || !formData.cost_to_business}
              className="flex-1 px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white font-medium rounded-lg transition disabled:opacity-50"
            >
              {submitting ? 'Creating...' : 'Create Service'}
            </button>
            <button
              onClick={onClose}
              className="px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-lg transition"
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

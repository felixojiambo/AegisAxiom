import React, { useState, useCallback, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Sparkles,
  Bot,
  TrendingUp,
  CheckCircle,
  Clock,
  AlertCircle,
  Settings,
  Play,
  Pause,
  Plus,
  Search,
  Filter,
  BarChart3,
  Zap,
  Target,
  Calendar,
  Users,
  FileText,
  Mail,
  MessageSquare,
  ChevronRight,
  Activity,
  Briefcase,
  Brain,
  Eye,
  Edit,
  LayoutGrid,
  List as ListIcon,
  DollarSign,
  Bell,
  Shield,
  X,
  EyeOff,
  ArrowLeft,
  ArrowRight,
  Copy,
  Link,
  Send,
  Loader2,
  RotateCcw,
  Trash2,
  Pencil,
  MousePointerClick,
} from 'lucide-react';
import { toast } from 'sonner';
import { api } from '../lib/api';
import API_URL from '../lib/apiConfig';
import ChatMarkdown from './ChatMarkdown';
import ChatInputAttachButton from './chat/ChatInputAttachButton';
import VegaSessionSidebar from './chat/VegaSessionSidebar';
import ResumeBanner from './chat/ResumeBanner';
import { usePermissions } from '../hooks/usePermissions';
import { useIsRootTenant } from '../lib/rootTenant';
import AIAccessSummary from './admin/AIAccessSummary';

const API = API_URL;

/**
 * Normalize an AI employee from the backend API format to the format
 * that existing card/detail components expect.
 */
function normalizeEmployee(apiData) {
  return {
    id: apiData.employee_id || apiData.id,
    employee_id: apiData.employee_id || apiData.id,
    name: apiData.name,
    role: apiData.role,
    description: apiData.description || '',
    avatar: apiData.avatar || '🤖',
    status: apiData.status || 'active',
    mode: apiData.mode || (apiData.type === 'conversational' ? 'manual' : 'automatic'),
    type: apiData.type || 'n8n',
    metrics: {
      primary: {
        label: apiData.metrics_config?.metric1_label || 'Executions',
        value: apiData.metrics?.metric1 ?? 0,
        unit: '',
      },
      secondary: {
        label: apiData.metrics_config?.metric2_label || 'Success Rate',
        value: apiData.metrics?.metric2 ?? 0,
        unit: apiData.metrics_config?.metric2_label?.toLowerCase().includes('rate') ? '%' : '',
      },
      tertiary: {
        label: apiData.metrics_config?.metric3_label || 'Avg Time',
        value: apiData.metrics?.metric3 ?? 0,
        unit: apiData.metrics_config?.metric3_label?.toLowerCase().includes('time') ? 'ms' : '',
      },
    },
    tasksActive: apiData.execution_count > 0 ? 1 : 0,
    specialties: apiData.specialties || [],
    lastActivity: apiData.last_execution_at
      ? formatTimeAgo(apiData.last_execution_at)
      : 'No activity yet',
    triggers:
      apiData.type === 'conversational'
        ? ['User opens chat']
        : ['N8N webhook', 'Scheduled trigger'],
    currentTasks: [],
    activityData: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    // Preserve backend fields for webhook info etc
    webhook_token: apiData.webhook_token,
    webhook_url: apiData.webhook_url,
    connection: apiData.connection,
    contractor: apiData.contractor,
    execution_count: apiData.execution_count || 0,
    success_count: apiData.success_count || 0,
    error_count: apiData.error_count || 0,
    _fromApi: true,
  };
}

function formatTimeAgo(isoString) {
  try {
    const diff = Date.now() - new Date(isoString).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'Just now';
    if (mins < 60) return `${mins} min ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours} hour${hours > 1 ? 's' : ''} ago`;
    const days = Math.floor(hours / 24);
    return `${days} day${days > 1 ? 's' : ''} ago`;
  } catch {
    return 'Unknown';
  }
}

// Mock AI Employee Data with role-specific metrics
const MOCK_AI_EMPLOYEES = [
  {
    id: 'ai_001',
    name: 'Orion',
    role: 'SOW Builder AI',
    description: 'Sales Operations Specialist',
    avatar: '🎯',
    status: 'active',
    mode: 'automatic',
    // 3 Key Metrics for SOW Builder
    metrics: {
      primary: { label: 'SOWs Generated', value: 156, unit: '' },
      secondary: { label: 'Avg Generation Time', value: 4.2, unit: 'min' },
      tertiary: { label: 'Accuracy Rate', value: 94, unit: '%' },
    },
    tasksActive: 8,
    specialties: ['SOW Generation', 'Resource Allocation', 'Margin Analysis', 'Contract Review'],
    lastActivity: '2 minutes ago',
    triggers: ['New opportunity created', 'SOW update requested', 'Margin review needed'],
    currentTasks: [
      {
        id: 't1',
        title: 'Generate SOW for DataFlow Inc - Field Services',
        priority: 'high',
        progress: 75,
        startedAt: '10 min ago',
      },
      {
        id: 't2',
        title: 'Analyze margin for TechCorp deal',
        priority: 'medium',
        progress: 40,
        startedAt: '25 min ago',
      },
      {
        id: 't3',
        title: 'Generate resource recommendations for Q1 pipeline',
        priority: 'low',
        progress: 20,
        startedAt: '1 hour ago',
      },
    ],
    activityData: [45, 52, 48, 65, 72, 68, 58, 62, 70, 75, 68, 72], // Last 12 hours
  },
  {
    id: 'ai_002',
    name: 'Pulsar',
    role: 'Automation Rules AI',
    description: 'Process Automation Specialist',
    avatar: '⚡',
    status: 'active',
    mode: 'automatic',
    // 3 Key Metrics for Automation AI
    metrics: {
      primary: { label: 'Rules Evaluated', value: 1847, unit: '' },
      secondary: { label: 'Alerts Triggered', value: 342, unit: '' },
      tertiary: { label: 'Avg Eval Time', value: 187, unit: 'ms' },
    },
    tasksActive: 12,
    specialties: ['Alert Generation', 'Rule Evaluation', 'Stage Blocking', 'Precondition Logic'],
    lastActivity: 'Just now',
    triggers: [
      'Opportunity updated',
      'SOW status change',
      'Margin threshold breach',
      'Scheduled evaluation',
    ],
    currentTasks: [
      {
        id: 't4',
        title: 'Evaluate automation rules for 15 opportunities',
        priority: 'high',
        progress: 90,
        startedAt: '3 min ago',
      },
      {
        id: 't5',
        title: 'Generate daily alert summary',
        priority: 'medium',
        progress: 60,
        startedAt: '15 min ago',
      },
      {
        id: 't6',
        title: 'Optimize precondition logic for margin rules',
        priority: 'low',
        progress: 10,
        startedAt: '45 min ago',
      },
    ],
    activityData: [120, 135, 128, 145, 158, 142, 138, 150, 162, 168, 155, 172],
  },
  {
    id: 'ai_003',
    name: 'Zenith',
    role: 'Analytics AI',
    description: 'Business Intelligence Analyst',
    avatar: '📊',
    status: 'active',
    mode: 'manual',
    // 3 Key Metrics for Analytics AI
    metrics: {
      primary: { label: 'Reports Generated', value: 89, unit: '' },
      secondary: { label: 'Forecasts Created', value: 24, unit: '' },
      tertiary: { label: 'Insights Delivered', value: 156, unit: '' },
    },
    tasksActive: 4,
    specialties: ['Performance Tracking', 'Trend Analysis', 'Forecasting', 'Dashboard Creation'],
    lastActivity: '15 minutes ago',
    triggers: ['Manual request', 'Scheduled report time', 'Data threshold reached'],
    currentTasks: [
      {
        id: 't7',
        title: 'Generate weekly performance report',
        priority: 'high',
        progress: 85,
        startedAt: '20 min ago',
      },
      {
        id: 't8',
        title: 'Analyze Q1 deal pipeline trends',
        priority: 'medium',
        progress: 50,
        startedAt: '1 hour ago',
      },
    ],
    activityData: [12, 15, 18, 22, 28, 25, 20, 24, 30, 32, 28, 35],
  },
  {
    id: 'ai_004',
    name: 'Lyra',
    role: 'Communication AI',
    description: 'Communication Specialist',
    avatar: '💬',
    status: 'paused',
    mode: 'automatic',
    // 3 Key Metrics for Communication AI
    metrics: {
      primary: { label: 'Emails Sent', value: 967, unit: '' },
      secondary: { label: 'Notifications Sent', value: 2341, unit: '' },
      tertiary: { label: 'Response Rate', value: 88, unit: '%' },
    },
    tasksActive: 0,
    specialties: ['Email Drafting', 'Notification Management', 'Stakeholder Updates', 'Follow-ups'],
    lastActivity: '1 hour ago',
    triggers: ['Alert created', 'SOW completion', 'Approval needed', 'Reminder due'],
    currentTasks: [],
    activityData: [85, 92, 88, 95, 102, 98, 0, 0, 0, 0, 0, 0], // Paused 6 hours ago
  },
  {
    id: 'ai_005',
    name: 'Scout',
    role: 'Research AI',
    description: 'Market Research Specialist',
    avatar: '🔍',
    status: 'training',
    mode: 'manual',
    // 3 Key Metrics for Research AI
    metrics: {
      primary: { label: 'Research Tasks', value: 45, unit: '' },
      secondary: { label: 'Data Points Collected', value: 1823, unit: '' },
      tertiary: { label: 'Reports Generated', value: 18, unit: '' },
    },
    tasksActive: 2,
    specialties: [
      'Competitive Analysis',
      'Industry Research',
      'Data Collection',
      'Trend Monitoring',
    ],
    lastActivity: '3 hours ago',
    triggers: ['Manual research request', 'New competitor detected', 'Industry news alert'],
    currentTasks: [
      {
        id: 't9',
        title: 'Research competitor pricing models for telecom sector',
        priority: 'low',
        progress: 30,
        startedAt: '3 hours ago',
      },
    ],
    activityData: [5, 8, 6, 10, 12, 9, 7, 11, 14, 12, 8, 10],
  },
  // AI Employees from reference image
  {
    id: 'ai_006',
    name: 'Callisto',
    role: 'Tier 1 Network Support',
    description: 'Network Support Specialist',
    avatar: '🌐',
    status: 'active',
    mode: 'automatic',
    // 3 Key Metrics from reference
    metrics: {
      primary: { label: 'Alerts Triaged', value: 150, unit: '' },
      secondary: { label: 'AVG Triage Time', value: 17, unit: ' Seconds' },
      tertiary: { label: 'Resolution Rate', value: 32, unit: '%' },
    },
    tasksActive: 5,
    specialties: [
      'Network Monitoring',
      'Alert Triage',
      'Incident Response',
      'System Health Checks',
    ],
    lastActivity: '30 seconds ago',
    triggers: [
      'Network alert detected',
      'System health threshold breach',
      'Connection timeout',
      'Bandwidth spike',
    ],
    currentTasks: [
      {
        id: 't10',
        title: 'Triage network latency alert for Production Servers',
        priority: 'high',
        progress: 65,
        startedAt: '2 min ago',
      },
      {
        id: 't11',
        title: 'Investigate connection drops in East Coast region',
        priority: 'medium',
        progress: 40,
        startedAt: '8 min ago',
      },
      {
        id: 't12',
        title: 'Monitor bandwidth usage spike on API gateway',
        priority: 'high',
        progress: 80,
        startedAt: '5 min ago',
      },
    ],
    activityData: [28, 32, 30, 35, 40, 38, 33, 37, 42, 45, 41, 43],
  },
  {
    id: 'ai_007',
    name: 'Phillip',
    role: 'Security Awareness Trainer',
    description: 'Employee Training Specialist',
    avatar: '🎓',
    status: 'active',
    mode: 'automatic',
    // 3 Key Metrics from reference
    metrics: {
      primary: { label: 'Employee Trainings Completed', value: 12, unit: '' },
      secondary: { label: 'AVG Time per Employee Training', value: 15, unit: ' minutes' },
      tertiary: { label: 'AVG Passing Score', value: 97, unit: '%' },
    },
    tasksActive: 3,
    specialties: [
      'Security Training',
      'Compliance Education',
      'Phishing Simulations',
      'Assessment Creation',
    ],
    lastActivity: '5 minutes ago',
    triggers: [
      'New employee onboarded',
      'Quarterly training due',
      'Failed phishing test',
      'Policy update',
    ],
    currentTasks: [
      {
        id: 't13',
        title: 'Conduct security awareness training for 5 new hires',
        priority: 'medium',
        progress: 70,
        startedAt: '20 min ago',
      },
      {
        id: 't14',
        title: 'Generate Q1 phishing simulation campaign',
        priority: 'low',
        progress: 25,
        startedAt: '1 hour ago',
      },
    ],
    activityData: [2, 3, 2, 4, 5, 4, 3, 4, 6, 5, 4, 5],
  },
  {
    id: 'ai_008',
    name: 'Sarah',
    role: 'Compliance Document Management',
    description: 'Compliance & Documentation Specialist',
    avatar: '📋',
    status: 'active',
    mode: 'automatic',
    // 3 Key Metrics from reference
    metrics: {
      primary: { label: 'Ingested Documents', value: 12, unit: '' },
      secondary: { label: 'Total Compliance Mappings', value: 3, unit: '' },
      tertiary: { label: 'Issues Identified', value: 3, unit: '' },
    },
    tasksActive: 4,
    specialties: [
      'Document Processing',
      'Compliance Mapping',
      'Policy Analysis',
      'Audit Trail Management',
    ],
    lastActivity: '10 minutes ago',
    triggers: [
      'New document uploaded',
      'Compliance review scheduled',
      'Policy change detected',
      'Audit requested',
    ],
    currentTasks: [
      {
        id: 't15',
        title: 'Process and map new GDPR compliance documents',
        priority: 'high',
        progress: 55,
        startedAt: '15 min ago',
      },
      {
        id: 't16',
        title: 'Review SOC 2 audit requirements against current policies',
        priority: 'medium',
        progress: 35,
        startedAt: '45 min ago',
      },
      {
        id: 't17',
        title: 'Identify compliance gaps in vendor contracts',
        priority: 'medium',
        progress: 60,
        startedAt: '30 min ago',
      },
    ],
    activityData: [3, 4, 3, 5, 6, 5, 4, 5, 7, 6, 5, 6],
  },
  {
    id: 'ai_009',
    name: 'George',
    role: 'Code Compliance Checker',
    description: 'Code Quality & Security Specialist',
    avatar: '💻',
    status: 'active',
    mode: 'automatic',
    // 3 Key Metrics from reference
    metrics: {
      primary: { label: 'Lines of Code Reviewed', value: '15k', unit: '' },
      secondary: { label: 'Issues Flagged to DevOps', value: 792, unit: '' },
      tertiary: { label: 'Open CVEs Identified', value: 64, unit: '' },
    },
    tasksActive: 6,
    specialties: [
      'Static Code Analysis',
      'Security Scanning',
      'CVE Detection',
      'Code Quality Review',
    ],
    lastActivity: 'Just now',
    triggers: [
      'Git commit detected',
      'Pull request opened',
      'Scheduled scan time',
      'Dependency update',
    ],
    currentTasks: [
      {
        id: 't18',
        title: 'Scan repository: aegisaxiom-backend for security vulnerabilities',
        priority: 'high',
        progress: 85,
        startedAt: '5 min ago',
      },
      {
        id: 't19',
        title: 'Review PR #247: Authentication refactor',
        priority: 'high',
        progress: 60,
        startedAt: '12 min ago',
      },
      {
        id: 't20',
        title: 'Check dependencies for known CVEs',
        priority: 'medium',
        progress: 45,
        startedAt: '18 min ago',
      },
      {
        id: 't21',
        title: 'Analyze code complexity in sales-ops module',
        priority: 'low',
        progress: 20,
        startedAt: '40 min ago',
      },
    ],
    activityData: [180, 195, 188, 205, 220, 210, 198, 215, 230, 240, 225, 235],
  },
  {
    id: 'ai_010',
    name: 'Felecia',
    role: 'Red Team Member',
    description: 'Security Testing Specialist',
    avatar: '🛡️',
    status: 'active',
    mode: 'automatic',
    // 3 Key Metrics from reference
    metrics: {
      primary: { label: 'IPs Scanned', value: '1.7k', unit: '' },
      secondary: { label: 'Vulnerabilities Identified', value: 435, unit: '' },
      tertiary: { label: 'Patches Deployed', value: 372, unit: '' },
    },
    tasksActive: 7,
    specialties: [
      'Penetration Testing',
      'Vulnerability Scanning',
      'Security Audits',
      'Patch Management',
    ],
    lastActivity: '1 minute ago',
    triggers: [
      'Scheduled security scan',
      'New infrastructure deployed',
      'Vulnerability database update',
      'Security incident',
    ],
    currentTasks: [
      {
        id: 't22',
        title: 'Conduct penetration test on production API endpoints',
        priority: 'high',
        progress: 75,
        startedAt: '25 min ago',
      },
      {
        id: 't23',
        title: 'Scan internal network for open ports and services',
        priority: 'high',
        progress: 90,
        startedAt: '10 min ago',
      },
      {
        id: 't24',
        title: 'Deploy critical security patches to web servers',
        priority: 'high',
        progress: 95,
        startedAt: '5 min ago',
      },
      {
        id: 't25',
        title: 'Verify patch deployment success across 50 servers',
        priority: 'medium',
        progress: 50,
        startedAt: '30 min ago',
      },
    ],
    activityData: [142, 155, 148, 165, 178, 170, 158, 172, 185, 192, 180, 188],
  },
  {
    id: 'ai_011',
    name: 'Frankie',
    role: 'Email Assistant',
    description: 'Email Management Specialist',
    avatar: '📧',
    status: 'active',
    mode: 'automatic',
    // 3 Key Metrics from reference
    metrics: {
      primary: { label: 'Emails Processed', value: 967, unit: '' },
      secondary: { label: 'Actions Taken', value: 71, unit: '' },
      tertiary: { label: 'Activities Tracked', value: 549, unit: '' },
    },
    tasksActive: 9,
    specialties: ['Email Triage', 'Auto-response', 'Priority Detection', 'Activity Tracking'],
    lastActivity: '15 seconds ago',
    triggers: [
      'New email received',
      'High-priority keyword detected',
      'Follow-up reminder due',
      'Scheduled email time',
    ],
    currentTasks: [
      {
        id: 't26',
        title: 'Process inbox: 45 new customer inquiries',
        priority: 'high',
        progress: 82,
        startedAt: '8 min ago',
      },
      {
        id: 't27',
        title: 'Draft responses to sales opportunity emails',
        priority: 'medium',
        progress: 55,
        startedAt: '15 min ago',
      },
      {
        id: 't28',
        title: 'Track follow-up activities for 12 pending deals',
        priority: 'medium',
        progress: 65,
        startedAt: '20 min ago',
      },
      {
        id: 't29',
        title: 'Categorize and tag incoming support tickets',
        priority: 'low',
        progress: 40,
        startedAt: '35 min ago',
      },
    ],
    activityData: [95, 102, 98, 110, 118, 112, 105, 115, 125, 130, 122, 128],
  },
];

const STATUS_CONFIG = {
  active: {
    label: 'Active',
    color: 'bg-green-500',
    textColor: 'text-green-700 dark:text-green-300',
    bgColor: 'bg-green-50 dark:bg-green-500/20',
  },
  paused: {
    label: 'Paused',
    color: 'bg-yellow-500',
    textColor: 'text-yellow-700 dark:text-yellow-300',
    bgColor: 'bg-yellow-500/20',
  },
  training: {
    label: 'Training',
    color: 'bg-blue-500',
    textColor: 'text-blue-700 dark:text-blue-300',
    bgColor: 'bg-blue-50 dark:bg-blue-500/20',
  },
  offline: {
    label: 'Offline',
    color: 'bg-aegisaxiom-elevated',
    textColor: 'text-aegisaxiom-text-secondary',
    bgColor: 'bg-aegisaxiom-elevated/20',
  },
};

const PRIORITY_CONFIG = {
  high: {
    color: 'text-red-600 dark:text-red-400',
    bg: 'bg-red-50 dark:bg-red-500/20',
    label: 'High',
  },
  medium: {
    color: 'text-yellow-600 dark:text-yellow-400',
    bg: 'bg-yellow-500/20',
    label: 'Medium',
  },
  low: {
    color: 'text-blue-600 dark:text-blue-400',
    bg: 'bg-blue-50 dark:bg-blue-500/20',
    label: 'Low',
  },
};

export default function AIEmployees() {
  const navigate = useNavigate();
  const { hasPermission, isAdmin } = usePermissions();
  // Phase 1D.9: showcase mocks (Orion/Pulsar/Zenith/Lyra/Scout) are
  // only relevant for SimpliMeta-internal users as walkthrough content.
  // Partner/account tenants see only real instances.
  const isRootTenant = useIsRootTenant();
  const [selectedEmployee, setSelectedEmployee] = useState(null);
  const [viewMode, setViewMode] = useState('grid');
  const [searchQuery, setSearchQuery] = useState('');
  const [filterStatus, setFilterStatus] = useState('all');
  const [showAddModal, setShowAddModal] = useState(false);
  const [editEmployee, setEditEmployee] = useState(null);
  const [employees, setEmployees] = useState([]);
  const [loading, setLoading] = useState(true);
  const [chatEmployee, setChatEmployee] = useState(null);
  const [accessSummaryEmployee, setAccessSummaryEmployee] = useState(null);

  // Permission checks
  const canCreate = isAdmin() || hasPermission('ai_employees.create');
  const canEdit = isAdmin() || hasPermission('ai_employees.edit');
  const canDelete = isAdmin() || hasPermission('ai_employees.delete');
  const canManageStatus = isAdmin() || hasPermission('ai_employees.manage');
  const canViewAccess = isAdmin() || hasPermission('ai.manage.access');

  // Load AI employees from API on mount.
  //
  // Mock-data policy (Phase 1D.9):
  //   - Root tenant (SimpliMeta-internal): mocks are merged in as
  //     showcase content alongside real data. If the API call fails or
  //     returns empty, fall back to mocks so the page is never blank
  //     for internal users.
  //   - Non-root tenants (partners/accounts): never see the mocks.
  //     Empty API response → empty page (with the existing empty
  //     state). API failure → empty + error toast handling stays the
  //     same (caught and logged below).
  const loadEmployees = useCallback(async () => {
    try {
      setLoading(true);
      const response = await api.get(`${API}/api/ai-employees`);
      const data = await response.json();

      if (response.ok && Array.isArray(data) && data.length > 0) {
        const normalized = data.map(normalizeEmployee);
        if (isRootTenant) {
          // Merge: API employees first, then mock employees that aren't duplicated
          const apiIds = new Set(normalized.map((e) => e.name.toLowerCase()));
          const uniqueMocks = MOCK_AI_EMPLOYEES.filter((m) => !apiIds.has(m.name.toLowerCase()));
          setEmployees([...normalized, ...uniqueMocks]);
        } else {
          setEmployees(normalized);
        }
      } else {
        // No API data yet — mocks for root, empty for everyone else
        setEmployees(isRootTenant ? MOCK_AI_EMPLOYEES : []);
      }
    } catch (error) {
      console.error('Error loading AI employees:', error);
      // Fallback — mocks for root, empty for everyone else
      setEmployees(isRootTenant ? MOCK_AI_EMPLOYEES : []);
    } finally {
      setLoading(false);
    }
  }, [isRootTenant]);

  useEffect(() => {
    loadEmployees();
  }, [loadEmployees]);

  const filteredEmployees = employees.filter((emp) => {
    const matchesSearch =
      emp.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      emp.role.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesFilter = filterStatus === 'all' || emp.status === filterStatus;
    return matchesSearch && matchesFilter;
  });

  // Calculate aggregate stats
  const totalTasksActive = employees.reduce((sum, emp) => sum + emp.tasksActive, 0);
  const totalMetric1 = employees.reduce((sum, emp) => {
    const val = emp.metrics.primary.value;
    return sum + (typeof val === 'number' ? val : 0);
  }, 0);
  const activeEmployees = employees.filter((emp) => emp.status === 'active').length;

  const handleAddEmployee = useCallback(
    async (formData) => {
      try {
        // Extract persona query params; don't send them in the body
        const { applyDefaults, persona, ...payload } = formData;
        const params = new URLSearchParams();
        if (applyDefaults && persona) {
          params.set('apply_defaults', 'true');
          params.set('persona', persona);
        }
        const qs = params.toString() ? `?${params.toString()}` : '';
        const response = await api.post(`${API}/api/ai-employees${qs}`, payload);
        const data = await response.json();

        if (response.ok) {
          toast.success(`${data.name} has been added as an AI employee!`);
          // Reload from API to get the full employee with webhook info
          await loadEmployees();
          return data;
        } else {
          toast.error(data.detail || 'Failed to create AI employee');
          return null;
        }
      } catch (error) {
        console.error('Error creating AI employee:', error);
        toast.error('Failed to create AI employee');
        return null;
      }
    },
    [loadEmployees],
  );

  const handleStatusChange = useCallback(
    async (employeeId, newStatus) => {
      if (!canManageStatus) {
        toast.error(
          "You don't have permission to change AI Employee status. Contact your administrator to request access.",
        );
        return;
      }

      // Check if this is a mock employee (no API backing)
      const emp = employees.find((e) => (e.employee_id || e.id) === employeeId);
      if (emp && !emp._fromApi) {
        toast.info(`Status change coming soon for mock employees`);
        return;
      }

      try {
        const response = await api.patch(`${API}/api/ai-employees/${employeeId}/status`, {
          status: newStatus,
        });
        const data = await response.json();

        if (response.ok) {
          toast.success(`Status updated to ${newStatus}`);
          await loadEmployees();
        } else {
          toast.error(data.detail || 'Failed to update status');
        }
      } catch (error) {
        console.error('Error updating status:', error);
        toast.error('Failed to update status');
      }
    },
    [employees, loadEmployees, canManageStatus],
  );

  const handleDeleteEmployee = useCallback(
    async (employeeId) => {
      if (!canDelete) {
        toast.error(
          "You don't have permission to delete AI Employees. Only administrators can perform this action.",
        );
        return;
      }

      const emp = employees.find((e) => (e.employee_id || e.id) === employeeId);
      if (emp && !emp._fromApi) {
        toast.info(`Delete coming soon for mock employees`);
        return;
      }

      try {
        const response = await api.delete(`${API}/api/ai-employees/${employeeId}`);
        const data = await response.json();

        if (response.ok) {
          toast.success('AI employee deleted');
          await loadEmployees();
        } else {
          toast.error(data.detail || 'Failed to delete');
        }
      } catch (error) {
        console.error('Error deleting AI employee:', error);
        toast.error('Failed to delete AI employee');
      }
    },
    [employees, loadEmployees, canDelete],
  );

  const handleUpdateEmployee = useCallback(
    async (employeeId, payload) => {
      if (!canEdit) {
        toast.error(
          "You don't have permission to edit AI Employees. Contact your administrator to request access.",
        );
        return null;
      }

      try {
        const response = await api.put(`${API}/api/ai-employees/${employeeId}`, payload);
        const data = await response.json();
        if (response.ok) {
          toast.success('AI employee updated');
          setEditEmployee(null);
          setSelectedEmployee(null);
          await loadEmployees();
          return data;
        } else {
          toast.error(data.detail || 'Failed to update');
          return null;
        }
      } catch (error) {
        console.error('Error updating AI employee:', error);
        toast.error('Failed to update AI employee');
        return null;
      }
    },
    [loadEmployees, canEdit],
  );

  const handleEmployeeClick = useCallback((employee) => {
    const embeddedUrl = employee.connection?.embedded_url || employee._connection?.embeddedUrl;
    if (employee.type === 'conversational' && embeddedUrl) {
      setChatEmployee(employee);
    } else {
      setSelectedEmployee(employee);
    }
  }, []);

  return (
    <div className="p-4 sm:p-6 max-w-[1800px] mx-auto">
      {/* Header */}
      <div className="mb-6 sm:mb-8">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-4 sm:mb-6">
          <div className="flex items-center gap-3">
            <div className="p-2.5 sm:p-3 bg-gradient-to-br from-violet-600 to-purple-600 rounded-xl shrink-0">
              <Sparkles className="w-6 h-6 sm:w-8 sm:h-8 text-white" />
            </div>
            <div className="min-w-0">
              <h1 className="text-xl sm:text-2xl lg:text-3xl font-bold text-aegisaxiom-text">
                My AI Employees
              </h1>
              <p className="text-aegisaxiom-text-secondary text-xs sm:text-sm">
                Intelligent agentic workforce — automatic & on-demand execution
              </p>
            </div>
          </div>

          <button
            onClick={() => {
              if (!canCreate) {
                toast.error(
                  "You don't have permission to add AI Employees. Contact your administrator to request access.",
                );
                return;
              }
              setShowAddModal(true);
            }}
            className={`flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg transition whitespace-nowrap shrink-0 text-sm font-medium w-full sm:w-auto ${canCreate ? 'bg-violet-600 hover:bg-violet-500 text-white' : 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted'}`}
          >
            <Plus className="w-4 h-4" />
            Add AI Employee
          </button>
        </div>

        {/* Stats Overview */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 sm:gap-4 mb-5 sm:mb-6">
          <StatCard
            icon={Bot}
            label="Active AI Employees"
            value={activeEmployees}
            total={employees.length}
            color="violet"
          />
          <StatCard
            icon={Zap}
            label="Tasks Running Now"
            value={totalTasksActive}
            subtext="Across all agents"
            color="amber"
          />
          <StatCard
            icon={TrendingUp}
            label="Total Actions Today"
            value={totalMetric1}
            subtext="Combined output"
            color="green"
          />
        </div>

        {/* Search, Filters & View Toggle — search full-width on mobile */}
        <div className="flex flex-col sm:flex-row gap-3">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-aegisaxiom-text-muted pointer-events-none" />
            <input
              type="text"
              placeholder="Search AI employees..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="input-field pl-10"
              aria-label="Search AI employees"
            />
          </div>

          <div className="flex gap-2 sm:gap-3">
            <select
              value={filterStatus}
              onChange={(e) => setFilterStatus(e.target.value)}
              className="input-field flex-1 sm:flex-none sm:w-40"
              aria-label="Filter by status"
            >
              <option value="all">All Status</option>
              <option value="active">Active</option>
              <option value="paused">Paused</option>
              <option value="training">Training</option>
              <option value="offline">Offline</option>
            </select>

            {/* View Mode Toggle */}
            <div
              className="flex items-center gap-1 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-1 shrink-0"
              role="group"
              aria-label="View mode"
            >
              <button
                onClick={() => setViewMode('grid')}
                aria-pressed={viewMode === 'grid'}
                aria-label="Grid view"
                className={`p-2 rounded transition ${viewMode === 'grid' ? 'bg-violet-600 text-white' : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated'}`}
                title="Grid View"
              >
                <LayoutGrid className="w-4 h-4" />
              </button>
              <button
                onClick={() => setViewMode('list')}
                aria-pressed={viewMode === 'list'}
                aria-label="List view"
                className={`p-2 rounded transition ${viewMode === 'list' ? 'bg-violet-600 text-white' : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated'}`}
                title="List View"
              >
                <ListIcon className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* AI Employees Display */}
      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4 sm:gap-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <div
              key={i}
              className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-5 animate-pulse"
            >
              <div className="flex items-center gap-3 mb-4">
                <div className="w-12 h-12 rounded-full bg-aegisaxiom-elevated" />
                <div className="flex-1 space-y-2">
                  <div className="h-4 w-24 bg-aegisaxiom-elevated rounded" />
                  <div className="h-3 w-32 bg-aegisaxiom-elevated rounded" />
                </div>
              </div>
              <div className="grid grid-cols-3 gap-3 mb-4 pb-4 border-b border-aegisaxiom-border">
                {[0, 1, 2].map((j) => (
                  <div key={j} className="space-y-1.5">
                    <div className="h-3 w-full bg-aegisaxiom-elevated rounded" />
                    <div className="h-5 w-12 bg-aegisaxiom-elevated rounded" />
                  </div>
                ))}
              </div>
              <div className="space-y-2">
                <div className="h-3 w-full bg-aegisaxiom-elevated rounded" />
                <div className="h-3 w-3/4 bg-aegisaxiom-elevated rounded" />
              </div>
            </div>
          ))}
        </div>
      ) : viewMode === 'grid' ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4 sm:gap-6">
          {filteredEmployees.map((employee) => (
            <AIEmployeeCard
              key={employee.id}
              employee={employee}
              onClick={() => handleEmployeeClick(employee)}
              canViewAccess={canViewAccess && !!employee._fromApi}
              onViewAccess={(e) => {
                e.stopPropagation();
                setAccessSummaryEmployee(employee);
              }}
            />
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-4 sm:gap-6">
          {filteredEmployees.map((employee) => (
            <AIEmployeeListItem
              key={employee.id}
              employee={employee}
              onClick={() => handleEmployeeClick(employee)}
              canViewAccess={canViewAccess && !!employee._fromApi}
              onViewAccess={(e) => {
                e.stopPropagation();
                setAccessSummaryEmployee(employee);
              }}
            />
          ))}
        </div>
      )}

      {!loading && filteredEmployees.length === 0 && (
        <div className="text-center py-12 sm:py-16">
          <Bot className="w-14 h-14 sm:w-16 sm:h-16 text-aegisaxiom-text-muted/60 mx-auto mb-4" />
          <p className="text-aegisaxiom-text-secondary text-sm sm:text-base font-medium">
            {searchQuery || filterStatus !== 'all'
              ? 'No AI employees match your filters'
              : 'No AI employees yet'}
          </p>
          <p className="text-aegisaxiom-text-muted text-xs sm:text-sm mt-1 mb-4">
            {searchQuery || filterStatus !== 'all'
              ? 'Try clearing your search or filter'
              : 'Add your first AI employee to get started'}
          </p>
          {(searchQuery || filterStatus !== 'all') && (
            <button
              onClick={() => {
                setSearchQuery('');
                setFilterStatus('all');
              }}
              className="text-violet-600 dark:text-violet-400 hover:underline text-sm font-medium"
            >
              Clear filters
            </button>
          )}
        </div>
      )}

      {/* Detail Modal */}
      {selectedEmployee && (
        <AIEmployeeDetailModal
          employee={selectedEmployee}
          onClose={() => setSelectedEmployee(null)}
          onStatusChange={handleStatusChange}
          onDelete={handleDeleteEmployee}
          onEdit={(emp) => setEditEmployee(emp)}
          canEdit={canEdit}
          canDelete={canDelete}
          canManageStatus={canManageStatus}
          canManage={canManageStatus}
        />
      )}

      {/* Add AI Employee Modal */}
      {showAddModal && (
        <AddAIEmployeeModal onClose={() => setShowAddModal(false)} onAdd={handleAddEmployee} />
      )}

      {/* Edit AI Employee Modal */}
      {editEmployee && (
        <AddAIEmployeeModal
          onClose={() => setEditEmployee(null)}
          onAdd={(payload) =>
            handleUpdateEmployee(editEmployee.employee_id || editEmployee.id, payload)
          }
          editData={editEmployee}
        />
      )}

      {/* Conversational AI Chat Modal */}
      {chatEmployee && (
        <ConversationalChatModal
          employee={chatEmployee}
          onClose={() => setChatEmployee(null)}
          onViewDetails={() => {
            setChatEmployee(null);
            setSelectedEmployee(chatEmployee);
          }}
        />
      )}

      {/* AI Access Summary panel */}
      {accessSummaryEmployee && (
        <AIAccessSummary
          employeeId={accessSummaryEmployee.employee_id || accessSummaryEmployee.id}
          employeeName={accessSummaryEmployee.name}
          onClose={() => setAccessSummaryEmployee(null)}
        />
      )}
    </div>
  );
}

// ============================================
// Conversational AI Chat Modal
// ============================================

function ConversationalChatModal({ employee, onClose, onViewDetails }) {
  const embeddedUrl = employee.connection?.embedded_url || employee._connection?.embeddedUrl || '';
  const statusConfig = STATUS_CONFIG[employee.status];

  // Escape key to close + body scroll lock.
  // Ref avoids re-running effect when parent passes a new onClose ref each render
  // (which would otherwise corrupt the prev-overflow snapshot).
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key === 'Escape') onCloseRef.current?.();
    };
    window.addEventListener('keydown', onKeyDown);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = prev;
    };
  }, []);

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 sm:p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`Chat with ${employee.name}`}
    >
      <div
        className="bg-aegisaxiom-surface border border-aegisaxiom-border w-full h-full sm:rounded-2xl sm:max-w-4xl sm:h-[85vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="bg-gradient-to-r from-violet-600/20 to-purple-600/20 border-b border-aegisaxiom-border px-4 sm:px-6 py-3 sm:py-4 flex items-center justify-between gap-3 flex-shrink-0">
          <div className="flex items-center gap-2 sm:gap-3 min-w-0 flex-1">
            <div className="text-2xl sm:text-4xl shrink-0" aria-hidden="true">
              {employee.avatar}
            </div>
            <div className="min-w-0 flex-1">
              <h2 className="text-base sm:text-xl font-bold text-aegisaxiom-text truncate">
                {employee.name}
              </h2>
              <p className="text-xs sm:text-sm text-aegisaxiom-text-secondary truncate">
                {employee.role}
              </p>
            </div>
            <div
              className={`hidden sm:flex ml-3 px-3 py-1 rounded-full text-xs font-medium ${statusConfig.bgColor} ${statusConfig.textColor} items-center gap-1.5 shrink-0`}
            >
              <span
                className={`w-2 h-2 rounded-full ${statusConfig.color} ${employee.status === 'active' ? 'animate-pulse' : ''}`}
              ></span>
              {statusConfig.label}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={onViewDetails}
              aria-label="View details"
              className="flex items-center gap-1.5 px-2 sm:px-3 py-1.5 bg-aegisaxiom-elevated hover:bg-aegisaxiom-base text-aegisaxiom-text text-xs sm:text-sm rounded-lg transition"
            >
              <Settings className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Details</span>
            </button>
            <button
              onClick={onClose}
              aria-label="Close chat"
              className="w-9 h-9 rounded-full bg-aegisaxiom-elevated hover:bg-aegisaxiom-base flex items-center justify-center transition"
            >
              <X className="w-4 h-4 text-aegisaxiom-text-secondary" />
            </button>
          </div>
        </div>

        {/* Chat iframe */}
        <div className="flex-1 bg-aegisaxiom-base min-h-0">
          {embeddedUrl ? (
            <iframe
              src={embeddedUrl}
              title={`Chat with ${employee.name}`}
              className="w-full h-full border-0"
              allow="microphone; camera; clipboard-write"
              sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads"
            />
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-center px-6">
              <MessageSquare className="w-14 h-14 sm:w-16 sm:h-16 text-aegisaxiom-text-muted mb-4" />
              <p className="text-base sm:text-lg text-aegisaxiom-text-secondary mb-2">
                No chat URL configured
              </p>
              <p className="text-sm text-aegisaxiom-text-muted mb-4 max-w-sm">
                This conversational AI doesn't have an embedded URL set up yet.
              </p>
              <button
                onClick={onViewDetails}
                className="flex items-center gap-2 px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded-lg transition"
              >
                <Settings className="w-4 h-4" />
                Configure URL
              </button>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="border-t border-aegisaxiom-border px-4 sm:px-6 py-2.5 sm:py-3 flex items-center justify-between gap-3 bg-aegisaxiom-surface flex-shrink-0">
          <div className="flex items-center gap-3 sm:gap-4 text-[11px] sm:text-xs text-aegisaxiom-text-muted min-w-0">
            <span className="flex items-center gap-1.5 truncate">
              <Activity className="w-3.5 h-3.5 shrink-0" />
              <span className="truncate">{employee.lastActivity}</span>
            </span>
            {employee.metrics && (
              <span className="hidden sm:inline truncate">
                {employee.metrics.primary.label}: {employee.metrics.primary.value}
              </span>
            )}
          </div>
          {embeddedUrl && (
            <a
              href={embeddedUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 text-[11px] sm:text-xs text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300 transition shrink-0"
            >
              <span className="hidden sm:inline">Open in new tab</span>
              <span className="sm:hidden">Open</span>
              <ChevronRight className="w-3.5 h-3.5" />
            </a>
          )}
        </div>
      </div>
    </div>
  );
}

// ============================================
// Add AI Employee Modal - 3-Step Import Wizard
// ============================================

const AVAILABLE_ICONS = [
  '🎯',
  '⚡',
  '💬',
  '📊',
  '🌐',
  '🎓',
  '📋',
  '💻',
  '🛡️',
  '📧',
  '🔍',
  '🤖',
  '🧠',
  '📡',
  '⚙️',
  '🔔',
  '📁',
  '🎪',
  '🏷️',
  '🔧',
];

const PERSONA_OPTIONS = [
  { value: 'sales_assistant', label: 'Sales Assistant' },
  { value: 'ops_assistant', label: 'Operations Assistant' },
  { value: 'finance_assistant', label: 'Finance Assistant' },
  { value: 'support_assistant', label: 'Support Assistant' },
  { value: 'general_assistant', label: 'General Assistant' },
];

const DEFAULT_FORM = {
  type: '', // 'conversational' or 'n8n'
  name: '',
  role: '',
  description: '',
  avatar: '🤖',
  // Conversational fields
  embeddedUrl: '',
  embedCode: '',
  // N8N fields
  n8nInstanceUrl: '',
  n8nApiKey: '',
  n8nWorkflowId: '',
  // Metrics
  metric1Label: '',
  metric2Label: '',
  metric3Label: '',
  metricsSource: 'manual',
  // Specialties
  specialties: [],
  // Contractor
  contractorName: '',
  agreementId: '',
  // Status
  initialStatus: 'active',
  // Persona defaults (sent as query params, not in body)
  applyDefaults: false,
  persona: '',
};

function AddAIEmployeeModal({ onClose, onAdd, editData }) {
  const isEditMode = !!editData;
  const [step, setStep] = useState(isEditMode ? 2 : 1);
  const [personaOptions, setPersonaOptions] = useState(PERSONA_OPTIONS);
  const [form, setForm] = useState(() => {
    if (editData) {
      return {
        type: editData.type || editData._connection?.type || 'conversational',
        name: editData.name || '',
        role: editData.role || '',
        description: editData.description || '',
        avatar: editData.avatar || '🤖',
        embeddedUrl: editData.connection?.embedded_url || editData._connection?.embeddedUrl || '',
        embedCode: editData.connection?.embed_code || editData._connection?.embedCode || '',
        n8nInstanceUrl:
          editData.connection?.n8n_instance_url || editData._connection?.n8nInstanceUrl || '',
        n8nApiKey: editData.connection?.n8n_api_key || editData._connection?.n8nApiKey || '',
        n8nWorkflowId:
          editData.connection?.n8n_workflow_id || editData._connection?.n8nWorkflowId || '',
        metric1Label: editData.metrics?.primary?.label || '',
        metric2Label: editData.metrics?.secondary?.label || '',
        metric3Label: editData.metrics?.tertiary?.label || '',
        metricsSource: editData.metricsSource || 'manual',
        specialties: editData.specialties || [],
        contractorName: editData.contractorName || editData.contractor_name || '',
        agreementId: editData.agreementId || editData.agreement_id || '',
        initialStatus: editData.status || 'active',
      };
    }
    return DEFAULT_FORM;
  });
  const [specialtyInput, setSpecialtyInput] = useState('');
  const [showApiKey, setShowApiKey] = useState(false);
  const [showIconPicker, setShowIconPicker] = useState(false);
  const [errors, setErrors] = useState({});
  const [attempted, setAttempted] = useState(false);

  // Fetch dynamic persona list from the API on mount; fall back to the
  // hardcoded PERSONA_OPTIONS if the endpoint isn't deployed yet.
  useEffect(() => {
    api
      .get(`${API}/api/ai-employees/personas`)
      .then(async (res) => {
        if (!res.ok) return;
        const data = await res.json();
        if (Array.isArray(data) && data.length > 0) {
          setPersonaOptions(
            data.map((p) => ({ value: p.key, label: p.label })),
          );
        }
      })
      .catch(() => {
        // Endpoint not yet available — keep the hardcoded fallback silently.
      });
  }, []);

  const updateForm = (field, value) => {
    setForm((prev) => ({ ...prev, [field]: value }));
    if (errors[field]) setErrors((prev) => ({ ...prev, [field]: null }));
  };

  const addSpecialty = () => {
    const trimmed = specialtyInput.trim();
    if (trimmed && !form.specialties.includes(trimmed)) {
      updateForm('specialties', [...form.specialties, trimmed]);
      setSpecialtyInput('');
    }
  };

  const removeSpecialty = (idx) => {
    updateForm(
      'specialties',
      form.specialties.filter((_, i) => i !== idx),
    );
  };

  const handleSpecialtyKeyDown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      addSpecialty();
    }
  };

  const canProceedStep1 = form.type !== '';
  const canProceedStep2 =
    form.name.trim() !== '' &&
    form.role.trim() !== '' &&
    (form.type === 'conversational'
      ? form.embeddedUrl.trim() !== ''
      : form.n8nInstanceUrl.trim() !== '' && form.n8nWorkflowId.trim() !== '');

  const validateStep2 = () => {
    const newErrors = {};
    if (!form.name.trim()) newErrors.name = 'Employee name is required';
    if (!form.role.trim()) newErrors.role = 'Role / title is required';
    if (form.type === 'conversational' && !form.embeddedUrl.trim()) {
      newErrors.embeddedUrl = 'Embedded URL is required for conversational AI';
    }
    if (form.type === 'n8n') {
      if (!form.n8nInstanceUrl.trim()) newErrors.n8nInstanceUrl = 'N8N instance URL is required';
      if (!form.n8nWorkflowId.trim()) newErrors.n8nWorkflowId = 'Workflow ID is required';
    }
    setErrors(newErrors);
    setAttempted(true);
    const errorList = Object.values(newErrors);
    if (errorList.length > 0) {
      toast.error(errorList[0]);
      return false;
    }
    return true;
  };

  const handleContinueStep2 = () => {
    if (validateStep2()) {
      setStep(3);
    }
  };

  const handleSubmit = async () => {
    // Build API payload matching the backend AIEmployeeCreate schema
    const payload = {
      name: form.name,
      role: form.role,
      type: form.type,
      description: form.description || undefined,
      avatar: form.avatar,
      initial_status: form.initialStatus,
      // Conversational fields
      embedded_url: form.type === 'conversational' ? form.embeddedUrl : undefined,
      embed_code: form.type === 'conversational' ? form.embedCode : undefined,
      // N8N fields
      n8n_instance_url: form.type === 'n8n' ? form.n8nInstanceUrl : undefined,
      n8n_api_key: form.type === 'n8n' ? form.n8nApiKey : undefined,
      n8n_workflow_id: form.type === 'n8n' ? form.n8nWorkflowId : undefined,
      // Metrics
      metric1_label: form.metric1Label || undefined,
      metric2_label: form.metric2Label || undefined,
      metric3_label: form.metric3Label || undefined,
      metrics_source: form.metricsSource,
      // Specialties
      specialties: form.specialties.length > 0 ? form.specialties : undefined,
      // Contractor
      contractor_name: form.contractorName || undefined,
      agreement_id: form.agreementId || undefined,
      // Persona defaults — extracted into query params by handleAddEmployee
      applyDefaults: form.applyDefaults,
      persona: form.applyDefaults ? form.persona : undefined,
    };

    const result = await onAdd(payload);
    if (result) {
      // Show webhook info for N8N employees
      if (form.type === 'n8n' && result.webhook_url) {
        toast.info(`Webhook URL: ${window.location.origin}${result.webhook_url}`, {
          duration: 10000,
        });
      }
      onClose();
    }
  };

  // Escape key to close + body scroll lock.
  // Ref avoids re-running effect when parent passes a new onClose ref each render
  // (which would otherwise corrupt the prev-overflow snapshot).
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key === 'Escape') onCloseRef.current?.();
    };
    window.addEventListener('keydown', onKeyDown);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = prev;
    };
  }, []);

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 sm:p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={isEditMode ? 'Edit AI employee' : 'Add AI employee'}
    >
      <div
        className="bg-aegisaxiom-surface border border-aegisaxiom-border w-full h-full sm:rounded-2xl sm:max-w-[560px] sm:h-auto sm:max-h-[90vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 sm:px-6 pt-4 sm:pt-5 pb-2 shrink-0">
          <h2 className="text-base sm:text-lg font-semibold text-aegisaxiom-text">
            {isEditMode ? 'Edit AI employee' : 'Add AI employee'}
          </h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="w-9 h-9 rounded-full bg-aegisaxiom-elevated hover:bg-aegisaxiom-base flex items-center justify-center transition"
          >
            <X className="w-4 h-4 text-aegisaxiom-text-secondary" />
          </button>
        </div>

        {/* Stepper */}
        <div className="flex px-5 sm:px-6 pt-3 pb-4 shrink-0">
          {[
            { num: 1, label: 'Type' },
            { num: 2, label: 'Details' },
            { num: 3, label: 'Review' },
          ].map((s, idx) => (
            <div key={s.num} className="flex-1 text-center">
              <div
                className={`w-7 h-7 rounded-full text-xs font-medium inline-flex items-center justify-center transition-all ${
                  step > s.num
                    ? 'bg-violet-600 text-white'
                    : step === s.num
                      ? step === 3
                        ? 'bg-green-500 text-white'
                        : 'bg-violet-600 text-white'
                      : 'bg-aegisaxiom-elevated border border-aegisaxiom-border-hover text-aegisaxiom-text-muted'
                }`}
              >
                {step > s.num ? '✓' : s.num}
              </div>
              <p
                className={`text-[11px] mt-1 ${step >= s.num ? (step === 3 && s.num === 3 ? 'text-green-600 dark:text-green-400' : 'text-violet-700 dark:text-violet-300') : 'text-aegisaxiom-text-muted'}`}
              >
                {s.label}
              </p>
            </div>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-5 sm:px-6 pb-2 min-h-0">
          {/* ========== STEP 1: Select Type ========== */}
          {step === 1 && (
            <div>
              <p className="text-sm text-aegisaxiom-text-secondary mb-4">
                What type of AI employee are you importing?
              </p>

              <div
                onClick={() => updateForm('type', 'conversational')}
                className={`border rounded-xl p-4 mb-3 cursor-pointer transition ${
                  form.type === 'conversational'
                    ? 'border-violet-500 bg-violet-500/5'
                    : 'border-aegisaxiom-border hover:border-aegisaxiom-border-hover'
                }`}
              >
                <div className="flex items-center gap-3">
                  <div className="w-11 h-11 rounded-lg bg-violet-50 dark:bg-violet-500/10 flex items-center justify-center text-xl">
                    💬
                  </div>
                  <div className="flex-1">
                    <p className="text-[15px] font-medium text-aegisaxiom-text">Conversational AI</p>
                    <p className="text-xs text-aegisaxiom-text-secondary mt-0.5">
                      Chat-based AI that interacts with users via an embedded interface
                    </p>
                  </div>
                  <div
                    className={`w-5 h-5 rounded-full flex items-center justify-center text-xs transition ${
                      form.type === 'conversational'
                        ? 'bg-violet-500 text-white'
                        : 'border border-aegisaxiom-border-hover'
                    }`}
                  >
                    {form.type === 'conversational' && '✓'}
                  </div>
                </div>
              </div>

              <div
                onClick={() => updateForm('type', 'n8n')}
                className={`border rounded-xl p-4 cursor-pointer transition ${
                  form.type === 'n8n'
                    ? 'border-amber-500 bg-amber-500/5'
                    : 'border-aegisaxiom-border hover:border-aegisaxiom-border-hover'
                }`}
              >
                <div className="flex items-center gap-3">
                  <div className="w-11 h-11 rounded-lg bg-amber-50 dark:bg-amber-500/10 flex items-center justify-center text-xl">
                    ⚡
                  </div>
                  <div className="flex-1">
                    <p className="text-[15px] font-medium text-aegisaxiom-text">
                      Automated workflow (N8N)
                    </p>
                    <p className="text-xs text-aegisaxiom-text-secondary mt-0.5">
                      Background automation that runs predefined tasks and reports stats
                    </p>
                  </div>
                  <div
                    className={`w-5 h-5 rounded-full flex items-center justify-center text-xs transition ${
                      form.type === 'n8n'
                        ? 'bg-amber-500 text-white'
                        : 'border border-aegisaxiom-border-hover'
                    }`}
                  >
                    {form.type === 'n8n' && '✓'}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* ========== STEP 2: Details ========== */}
          {step === 2 && (
            <div>
              {/* Type badge */}
              <div
                className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs mb-5 ${
                  form.type === 'conversational'
                    ? 'bg-violet-50 dark:bg-violet-500/10 border border-violet-500/30 text-violet-700 dark:text-violet-300'
                    : 'bg-amber-50 dark:bg-amber-500/10 border border-amber-500/30 text-amber-700 dark:text-amber-300'
                }`}
              >
                <span>{form.type === 'conversational' ? '💬' : '⚡'}</span>
                {form.type === 'conversational' ? 'Conversational AI' : 'Automated workflow (N8N)'}
              </div>

              {/* Icon + Name row */}
              <div className="flex gap-3 mb-4 items-end">
                <div className="relative">
                  <p className="text-xs text-aegisaxiom-text font-medium mb-1.5">Icon</p>
                  <button
                    onClick={() => setShowIconPicker(!showIconPicker)}
                    className="w-14 h-14 rounded-xl bg-aegisaxiom-elevated border border-aegisaxiom-border-hover hover:border-aegisaxiom-border-hover flex items-center justify-center text-3xl transition"
                  >
                    {form.avatar}
                  </button>
                  {showIconPicker && (
                    <div className="absolute top-full left-0 mt-2 bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-xl p-2.5 z-10 grid grid-cols-5 gap-1.5 w-48 shadow-xl">
                      {AVAILABLE_ICONS.map((icon) => (
                        <button
                          key={icon}
                          onClick={() => {
                            updateForm('avatar', icon);
                            setShowIconPicker(false);
                          }}
                          className={`w-8 h-8 rounded-lg flex items-center justify-center text-lg hover:bg-aegisaxiom-elevated transition ${form.avatar === icon ? 'bg-violet-500/30 ring-1 ring-violet-500' : ''}`}
                        >
                          {icon}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <div className="flex-1">
                  <p className="text-xs text-aegisaxiom-text font-medium mb-1.5">Employee name</p>
                  <input
                    type="text"
                    placeholder="e.g. Orion, Callisto, Pulsar"
                    value={form.name}
                    onChange={(e) => updateForm('name', e.target.value)}
                    required
                    className={`w-full px-3 py-2.5 bg-aegisaxiom-elevated border rounded-lg text-aegisaxiom-text text-sm placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 transition ${attempted && errors.name ? 'border-red-500' : 'border-aegisaxiom-border-hover'}`}
                  />
                  {attempted && errors.name && (
                    <p className="text-[11px] text-red-600 dark:text-red-400 mt-1">{errors.name}</p>
                  )}
                </div>
              </div>

              {/* Role */}
              <div className="mb-4">
                <p className="text-xs text-aegisaxiom-text font-medium mb-1.5">Role / title</p>
                <input
                  type="text"
                  placeholder="e.g. SOW Builder AI, Tier 1 Network Support"
                  value={form.role}
                  onChange={(e) => updateForm('role', e.target.value)}
                  required
                  className={`w-full px-3 py-2.5 bg-aegisaxiom-elevated border rounded-lg text-aegisaxiom-text text-sm placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 transition ${attempted && errors.role ? 'border-red-500' : 'border-aegisaxiom-border-hover'}`}
                />
                {attempted && errors.role && (
                  <p className="text-[11px] text-red-600 dark:text-red-400 mt-1">{errors.role}</p>
                )}
              </div>

              {/* Description */}
              <div className="mb-4">
                <p className="text-xs text-aegisaxiom-text font-medium mb-1.5">
                  Description <span className="text-aegisaxiom-text-muted font-normal">(optional)</span>
                </p>
                <input
                  type="text"
                  placeholder="Brief description of what this AI does"
                  value={form.description}
                  onChange={(e) => updateForm('description', e.target.value)}
                  className="w-full px-3 py-2.5 bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg text-aegisaxiom-text text-sm placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 transition"
                />
              </div>

              {/* === Conversational-specific fields === */}
              {form.type === 'conversational' && (
                <>
                  <div className="mb-4">
                    <div className="flex items-center justify-between mb-1.5">
                      <p className="text-xs text-aegisaxiom-text font-medium">Embedded URL</p>
                      <span className="text-[11px] text-aegisaxiom-text-muted">
                        The chat interface URL
                      </span>
                    </div>
                    <input
                      type="text"
                      placeholder="https://your-ai-platform.com/embed/agent-id"
                      value={form.embeddedUrl}
                      onChange={(e) => updateForm('embeddedUrl', e.target.value)}
                      required
                      className={`w-full px-3 py-2.5 bg-aegisaxiom-elevated border rounded-lg text-aegisaxiom-text text-sm font-mono placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 transition ${attempted && errors.embeddedUrl ? 'border-red-500' : 'border-aegisaxiom-border-hover'}`}
                    />
                    {attempted && errors.embeddedUrl ? (
                      <p className="text-[11px] text-red-600 dark:text-red-400 mt-1">
                        {errors.embeddedUrl}
                      </p>
                    ) : (
                      <p className="text-[11px] text-aegisaxiom-text-muted mt-1">
                        This URL opens the chat session when the employee card is clicked
                      </p>
                    )}
                  </div>

                  <div className="mb-4">
                    <p className="text-xs text-aegisaxiom-text font-medium mb-1.5">
                      Embed code snippet{' '}
                      <span className="text-aegisaxiom-text-muted font-normal">(optional)</span>
                    </p>
                    <textarea
                      placeholder='<iframe src="https://..." width="100%" height="600"></iframe>'
                      value={form.embedCode}
                      onChange={(e) => updateForm('embedCode', e.target.value)}
                      rows={3}
                      className="w-full px-3 py-2.5 bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg text-aegisaxiom-text text-xs font-mono placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 transition resize-y"
                    />
                  </div>
                </>
              )}

              {/* === N8N-specific fields === */}
              {form.type === 'n8n' && (
                <>
                  <div className="bg-aegisaxiom-elevated rounded-xl p-4 mb-4">
                    <div className="flex items-center gap-2 mb-3">
                      <div className="w-6 h-6 rounded-md bg-amber-50 dark:bg-amber-500/20 flex items-center justify-center text-xs">
                        🔗
                      </div>
                      <p className="text-sm font-medium text-aegisaxiom-text">N8N connection</p>
                    </div>

                    <div className="mb-3">
                      <p className="text-xs text-aegisaxiom-text font-medium mb-1.5">N8N instance URL</p>
                      <input
                        type="text"
                        placeholder="https://your-n8n-instance.com"
                        value={form.n8nInstanceUrl}
                        onChange={(e) => updateForm('n8nInstanceUrl', e.target.value)}
                        required
                        className={`w-full px-3 py-2 bg-aegisaxiom-surface border rounded-lg text-aegisaxiom-text text-sm font-mono placeholder-aegisaxiom-text-muted focus:outline-none focus:border-amber-500 transition ${attempted && errors.n8nInstanceUrl ? 'border-red-500' : 'border-aegisaxiom-border-hover'}`}
                      />
                      {attempted && errors.n8nInstanceUrl && (
                        <p className="text-[11px] text-red-600 dark:text-red-400 mt-1">
                          {errors.n8nInstanceUrl}
                        </p>
                      )}
                    </div>

                    <div className="mb-3">
                      <p className="text-xs text-aegisaxiom-text font-medium mb-1.5">
                        API key <span className="text-aegisaxiom-text-muted font-normal">(optional)</span>
                      </p>
                      <div className="relative">
                        <input
                          type={showApiKey ? 'text' : 'password'}
                          placeholder="n8n_api_xxxxxxxxxxxxxxxx"
                          value={form.n8nApiKey}
                          onChange={(e) => updateForm('n8nApiKey', e.target.value)}
                          className="w-full px-3 py-2 pr-10 bg-aegisaxiom-surface border border-aegisaxiom-border-hover rounded-lg text-aegisaxiom-text text-sm font-mono placeholder-aegisaxiom-text-muted focus:outline-none focus:border-amber-500 transition"
                        />
                        <button
                          onClick={() => setShowApiKey(!showApiKey)}
                          className="absolute right-2.5 top-1/2 -translate-y-1/2 text-aegisaxiom-text-muted hover:text-aegisaxiom-text-secondary transition"
                        >
                          {showApiKey ? (
                            <EyeOff className="w-4 h-4" />
                          ) : (
                            <Eye className="w-4 h-4" />
                          )}
                        </button>
                      </div>
                    </div>

                    <div>
                      <p className="text-xs text-aegisaxiom-text font-medium mb-1.5">Workflow ID</p>
                      <input
                        type="text"
                        placeholder="e.g. workflow_abc123 or numeric ID"
                        value={form.n8nWorkflowId}
                        onChange={(e) => updateForm('n8nWorkflowId', e.target.value)}
                        required
                        className={`w-full px-3 py-2 bg-aegisaxiom-surface border rounded-lg text-aegisaxiom-text text-sm font-mono placeholder-aegisaxiom-text-muted focus:outline-none focus:border-amber-500 transition ${attempted && errors.n8nWorkflowId ? 'border-red-500' : 'border-aegisaxiom-border-hover'}`}
                      />
                      {attempted && errors.n8nWorkflowId ? (
                        <p className="text-[11px] text-red-600 dark:text-red-400 mt-1">
                          {errors.n8nWorkflowId}
                        </p>
                      ) : (
                        <p className="text-[11px] text-aegisaxiom-text-muted mt-1">
                          Found in N8N under workflow settings or in the URL bar
                        </p>
                      )}
                    </div>
                  </div>
                </>
              )}

              {/* Display Metrics */}
              <div className="mb-4">
                <p className="text-xs text-aegisaxiom-text font-medium mb-2">
                  Display metrics{' '}
                  <span className="text-aegisaxiom-text-muted font-normal">
                    (3 stats shown on the card)
                  </span>
                </p>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <p className="text-[11px] text-aegisaxiom-text-muted mb-1">Metric 1 label</p>
                    <input
                      type="text"
                      placeholder={
                        form.type === 'conversational' ? 'e.g. Sessions' : 'e.g. Executions'
                      }
                      value={form.metric1Label}
                      onChange={(e) => updateForm('metric1Label', e.target.value)}
                      className="w-full px-2.5 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg text-aegisaxiom-text text-xs placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 transition"
                    />
                  </div>
                  <div>
                    <p className="text-[11px] text-aegisaxiom-text-muted mb-1">Metric 2 label</p>
                    <input
                      type="text"
                      placeholder={
                        form.type === 'conversational'
                          ? 'e.g. Avg Response Time'
                          : 'e.g. Success Rate'
                      }
                      value={form.metric2Label}
                      onChange={(e) => updateForm('metric2Label', e.target.value)}
                      className="w-full px-2.5 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg text-aegisaxiom-text text-xs placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 transition"
                    />
                  </div>
                  <div>
                    <p className="text-[11px] text-aegisaxiom-text-muted mb-1">Metric 3 label</p>
                    <input
                      type="text"
                      placeholder={
                        form.type === 'conversational'
                          ? 'e.g. Satisfaction'
                          : 'e.g. Tasks Completed'
                      }
                      value={form.metric3Label}
                      onChange={(e) => updateForm('metric3Label', e.target.value)}
                      className="w-full px-2.5 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg text-aegisaxiom-text text-xs placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 transition"
                    />
                  </div>
                  {form.type === 'n8n' && (
                    <div>
                      <p className="text-[11px] text-aegisaxiom-text-muted mb-1">Source</p>
                      <select
                        value={form.metricsSource}
                        onChange={(e) => updateForm('metricsSource', e.target.value)}
                        className="w-full px-2.5 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border-hover rounded-lg text-aegisaxiom-text-secondary text-xs focus:outline-none focus:border-violet-500 transition"
                      >
                        <option value="n8n">Pull from N8N API</option>
                        <option value="manual">Manual entry</option>
                      </select>
                    </div>
                  )}
                </div>
              </div>

              {/* Specialties */}
              <div className="mb-4">
                <p className="text-xs text-aegisaxiom-text font-medium mb-2">
                  Specialties{' '}
                  <span className="text-aegisaxiom-text-muted font-normal">
                    (what this AI is good at)
                  </span>
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {form.specialties.map((s, idx) => (
                    <span
                      key={idx}
                      className="inline-flex items-center gap-1 px-2.5 py-1 bg-violet-50 dark:bg-violet-500/10 border border-violet-500/30 rounded-full text-xs text-violet-700 dark:text-violet-300"
                    >
                      {s}
                      <button
                        onClick={() => removeSpecialty(idx)}
                        className="hover:text-aegisaxiom-text transition ml-0.5"
                      >
                        ×
                      </button>
                    </span>
                  ))}
                  <input
                    type="text"
                    placeholder="+ Add specialty"
                    value={specialtyInput}
                    onChange={(e) => setSpecialtyInput(e.target.value)}
                    onKeyDown={handleSpecialtyKeyDown}
                    onBlur={addSpecialty}
                    className="px-2.5 py-1 bg-transparent border border-dashed border-aegisaxiom-border-hover rounded-full text-xs text-aegisaxiom-text-secondary placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 w-28 transition"
                  />
                </div>
              </div>

              {/* Contractor details */}
              <div className="bg-aegisaxiom-elevated rounded-xl p-3 mb-3">
                <p className="text-xs text-aegisaxiom-text font-medium mb-2">
                  Contractor details{' '}
                  <span className="text-aegisaxiom-text-muted font-normal">(who built this)</span>
                </p>
                <div className="flex gap-3">
                  <input
                    type="text"
                    placeholder="Contractor name"
                    value={form.contractorName}
                    onChange={(e) => updateForm('contractorName', e.target.value)}
                    className="flex-1 px-2.5 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border-hover rounded-lg text-aegisaxiom-text text-sm placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 transition"
                  />
                  <input
                    type="text"
                    placeholder="Agreement ID"
                    value={form.agreementId}
                    onChange={(e) => updateForm('agreementId', e.target.value)}
                    className="flex-1 px-2.5 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border-hover rounded-lg text-aegisaxiom-text text-sm placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 transition"
                  />
                </div>
              </div>

              {/* Persona defaults */}
              {!isEditMode && (
                <div className="bg-aegisaxiom-elevated rounded-xl p-3 mb-2">
                  <div className="flex items-center justify-between mb-1">
                    <p className="text-xs text-aegisaxiom-text font-medium">Apply persona defaults</p>
                    <button
                      type="button"
                      role="switch"
                      aria-checked={form.applyDefaults}
                      onClick={() => updateForm('applyDefaults', !form.applyDefaults)}
                      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-violet-500 focus:ring-offset-1 ${form.applyDefaults ? 'bg-violet-600' : 'bg-aegisaxiom-border-hover'}`}
                    >
                      <span
                        className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform ${form.applyDefaults ? 'translate-x-4' : 'translate-x-1'}`}
                      />
                    </button>
                  </div>
                  {form.applyDefaults && (
                    <>
                      <select
                        value={form.persona}
                        onChange={(e) => updateForm('persona', e.target.value)}
                        className="w-full mt-2 px-2.5 py-2 bg-aegisaxiom-surface border border-aegisaxiom-border-hover rounded-lg text-aegisaxiom-text text-sm focus:outline-none focus:border-violet-500 transition"
                        aria-label="Select persona"
                      >
                        <option value="">Select a persona…</option>
                        {personaOptions.map((p) => (
                          <option key={p.value} value={p.value}>
                            {p.label}
                          </option>
                        ))}
                      </select>
                      <p className="text-[11px] text-aegisaxiom-text-muted mt-1.5 leading-relaxed">
                        Defaults will be added to any existing access — existing grants are preserved.
                      </p>
                    </>
                  )}
                </div>
              )}
            </div>
          )}

          {/* ========== STEP 3: Review ========== */}
          {step === 3 && (
            <div>
              <p className="text-sm text-aegisaxiom-text-secondary mb-4">
                Review how this AI employee will appear in AEGISAXIOM
              </p>

              {/* Card Preview */}
              <div className="bg-aegisaxiom-elevated rounded-xl p-4 mb-4">
                <p className="text-[11px] text-aegisaxiom-text-muted uppercase tracking-wider mb-3">
                  Card preview
                </p>
                <div className="bg-aegisaxiom-surface border border-aegisaxiom-border-hover rounded-lg p-4">
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex items-center gap-3">
                      <div className="text-3xl">{form.avatar}</div>
                      <div>
                        <p className="text-[15px] font-medium text-aegisaxiom-text">
                          {form.name || 'Unnamed'}
                        </p>
                        <p className="text-xs text-aegisaxiom-text-secondary mt-0.5">
                          {form.role || 'No role set'}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-1.5 bg-green-50 dark:bg-green-500/10 rounded-full px-2.5 py-1">
                      <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse"></span>
                      <span className="text-[11px] text-green-600 dark:text-green-400">Active</span>
                    </div>
                  </div>
                  <div className="grid grid-cols-3 gap-3 mb-3">
                    <div>
                      <p className="text-[11px] text-aegisaxiom-text-muted">
                        {form.metric1Label ||
                          (form.type === 'conversational' ? 'Sessions' : 'Executions')}
                      </p>
                      <p className="text-lg font-bold text-aegisaxiom-text mt-0.5">0</p>
                    </div>
                    <div>
                      <p className="text-[11px] text-aegisaxiom-text-muted">
                        {form.metric2Label ||
                          (form.type === 'conversational' ? 'Avg Response Time' : 'Success Rate')}
                      </p>
                      <p className="text-lg font-bold text-amber-600 dark:text-amber-400 mt-0.5">
                        0{form.type === 'conversational' ? 's' : '%'}
                      </p>
                    </div>
                    <div>
                      <p className="text-[11px] text-aegisaxiom-text-muted">
                        {form.metric3Label ||
                          (form.type === 'conversational' ? 'Satisfaction' : 'Tasks Completed')}
                      </p>
                      <p className="text-lg font-bold text-aegisaxiom-text mt-0.5">
                        0{form.type === 'conversational' ? '%' : ''}
                      </p>
                    </div>
                  </div>
                  {form.specialties.length > 0 && (
                    <div className="flex gap-1.5 flex-wrap">
                      {form.specialties.slice(0, 3).map((s, idx) => (
                        <span
                          key={idx}
                          className="px-2 py-0.5 bg-violet-50 dark:bg-violet-500/10 rounded text-[11px] text-violet-700 dark:text-violet-300"
                        >
                          {s}
                        </span>
                      ))}
                      {form.specialties.length > 3 && (
                        <span className="px-2 py-0.5 bg-aegisaxiom-elevated rounded text-[11px] text-aegisaxiom-text-secondary">
                          +{form.specialties.length - 3} more
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </div>

              {/* Configuration Summary */}
              <div className="bg-aegisaxiom-elevated rounded-xl p-4 mb-4">
                <p className="text-[11px] text-aegisaxiom-text-muted uppercase tracking-wider mb-3">
                  Configuration summary
                </p>
                <div className="space-y-2 text-sm">
                  <div className="flex">
                    <span className="w-28 text-aegisaxiom-text-muted flex-shrink-0">Type</span>
                    <span
                      className={
                        form.type === 'conversational'
                          ? 'text-violet-700 dark:text-violet-300'
                          : 'text-amber-700 dark:text-amber-300'
                      }
                    >
                      {form.type === 'conversational'
                        ? '💬 Conversational AI'
                        : '⚡ Automated workflow (N8N)'}
                    </span>
                  </div>

                  {form.type === 'conversational' ? (
                    <div className="flex">
                      <span className="w-28 text-aegisaxiom-text-muted flex-shrink-0">Embed URL</span>
                      <span className="text-aegisaxiom-text font-mono text-xs truncate">
                        {form.embeddedUrl}
                      </span>
                    </div>
                  ) : (
                    <>
                      <div className="flex">
                        <span className="w-28 text-aegisaxiom-text-muted flex-shrink-0">
                          N8N instance
                        </span>
                        <span className="text-aegisaxiom-text font-mono text-xs truncate">
                          {form.n8nInstanceUrl}
                        </span>
                      </div>
                      <div className="flex">
                        <span className="w-28 text-aegisaxiom-text-muted flex-shrink-0">Workflow ID</span>
                        <span className="text-aegisaxiom-text font-mono text-xs">
                          {form.n8nWorkflowId}
                        </span>
                      </div>
                      <div className="flex">
                        <span className="w-28 text-aegisaxiom-text-muted flex-shrink-0">API key</span>
                        <span className="text-aegisaxiom-text font-mono text-xs">
                          {'•'.repeat(Math.min(form.n8nApiKey.length, 16))}
                          {form.n8nApiKey.slice(-4)}
                        </span>
                      </div>
                      <div className="flex">
                        <span className="w-28 text-aegisaxiom-text-muted flex-shrink-0">
                          Metrics source
                        </span>
                        <span className="text-aegisaxiom-text">
                          {form.metricsSource === 'n8n' ? 'Pull from N8N API' : 'Manual entry'}
                        </span>
                      </div>
                    </>
                  )}

                  {form.contractorName && (
                    <div className="flex">
                      <span className="w-28 text-aegisaxiom-text-muted flex-shrink-0">Contractor</span>
                      <span className="text-aegisaxiom-text">{form.contractorName}</span>
                    </div>
                  )}
                  {form.agreementId && (
                    <div className="flex">
                      <span className="w-28 text-aegisaxiom-text-muted flex-shrink-0">Agreement</span>
                      <span className="text-aegisaxiom-text">{form.agreementId}</span>
                    </div>
                  )}

                  <div className="flex items-center">
                    <span className="w-28 text-aegisaxiom-text-muted flex-shrink-0">Initial status</span>
                    <select
                      value={form.initialStatus}
                      onChange={(e) => updateForm('initialStatus', e.target.value)}
                      className="px-2 py-1 bg-aegisaxiom-surface border border-aegisaxiom-border-hover rounded-md text-green-600 dark:text-green-400 text-xs focus:outline-none"
                    >
                      <option value="active">Active — live immediately</option>
                      <option value="review">Pending review</option>
                      <option value="paused">Paused — activate later</option>
                    </select>
                  </div>
                </div>
              </div>

              {/* Info note */}
              <div className="flex gap-2.5 bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-3">
                <div className="w-5 h-5 rounded-full bg-amber-50 dark:bg-amber-500/20 flex items-center justify-center flex-shrink-0 mt-0.5">
                  <AlertCircle className="w-3 h-3 text-amber-600 dark:text-amber-400" />
                </div>
                <p className="text-xs text-aegisaxiom-text-secondary leading-relaxed">
                  This AI employee will be visible on the My AI Employees page.{' '}
                  {form.type === 'n8n'
                    ? 'N8N credentials are stored securely and used to pull workflow statistics.'
                    : 'The embedded URL will open when the employee card is clicked.'}
                </p>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-2 px-5 sm:px-6 py-3 sm:py-4 border-t border-aegisaxiom-border shrink-0">
          <button
            onClick={() => {
              if (isEditMode && step === 2) {
                onClose();
              } else if (step === 1) {
                onClose();
              } else {
                setStep(step - 1);
              }
            }}
            className="flex items-center gap-1.5 px-3 sm:px-4 py-2 bg-transparent border border-aegisaxiom-border-hover hover:bg-aegisaxiom-surface text-aegisaxiom-text-secondary text-sm rounded-lg transition"
          >
            {step === 1 || (isEditMode && step === 2) ? (
              'Cancel'
            ) : (
              <>
                <ArrowLeft className="w-3.5 h-3.5" /> Back
              </>
            )}
          </button>

          {step < 3 ? (
            <button
              onClick={() => {
                if (step === 1) {
                  if (canProceedStep1) setStep(2);
                  else toast.error('Please select an AI employee type');
                } else {
                  handleContinueStep2();
                }
              }}
              className={`flex items-center gap-1.5 px-4 sm:px-5 py-2 text-sm rounded-lg transition font-medium ${
                (step === 1 ? canProceedStep1 : true)
                  ? 'bg-violet-600 hover:bg-violet-500 text-white'
                  : 'bg-aegisaxiom-elevated text-aegisaxiom-text-muted'
              }`}
            >
              Continue <ArrowRight className="w-3.5 h-3.5" />
            </button>
          ) : (
            <div className="flex gap-2">
              {!isEditMode && (
                <button
                  onClick={() => {
                    updateForm('initialStatus', 'paused');
                    setTimeout(handleSubmit, 0);
                  }}
                  className="hidden sm:inline-flex px-4 py-2 bg-transparent border border-aegisaxiom-border-hover hover:bg-aegisaxiom-surface text-aegisaxiom-text-secondary text-sm rounded-lg transition"
                >
                  Save as draft
                </button>
              )}
              <button
                onClick={handleSubmit}
                className="flex items-center gap-1.5 px-4 sm:px-5 py-2 bg-green-600 hover:bg-green-500 text-white text-sm rounded-lg transition font-medium"
              >
                {isEditMode ? (
                  <>
                    <CheckCircle className="w-3.5 h-3.5" /> Save changes
                  </>
                ) : (
                  <>
                    <Plus className="w-3.5 h-3.5" /> Add employee
                  </>
                )}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Stat Card Component
function StatCard({ icon: Icon, label, value, subtext, total, color }) {
  const colorMap = {
    violet: 'bg-violet-50 dark:bg-violet-500/20 text-violet-700 dark:text-violet-300',
    green: 'bg-green-50 dark:bg-green-500/20 text-green-700 dark:text-green-300',
    blue: 'bg-blue-50 dark:bg-blue-500/20 text-blue-700 dark:text-blue-300',
    amber: 'bg-amber-50 dark:bg-amber-500/20 text-amber-700 dark:text-amber-300',
  };

  return (
    <div className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-3 sm:p-4">
      <div className="flex items-center gap-2.5 sm:gap-3 mb-2">
        <div className={`p-1.5 sm:p-2 rounded-lg shrink-0 ${colorMap[color]}`}>
          <Icon className="w-4 h-4 sm:w-5 sm:h-5" />
        </div>
        <span className="text-xs sm:text-sm text-aegisaxiom-text-secondary truncate">{label}</span>
      </div>
      <div className="flex items-baseline gap-2">
        <span className="text-xl sm:text-2xl font-bold text-aegisaxiom-text">{value}</span>
        {total !== undefined && total !== null && (
          <span className="text-xs sm:text-sm text-aegisaxiom-text-muted">/ {total}</span>
        )}
      </div>
      {subtext && <p className="text-[11px] sm:text-xs text-aegisaxiom-text-muted mt-1">{subtext}</p>}
    </div>
  );
}

// AI Employee Card Component (Grid View)
function AIEmployeeCard({ employee, onClick, canViewAccess, onViewAccess }) {
  const statusConfig = STATUS_CONFIG[employee.status];
  const ModeIcon = employee.mode === 'automatic' ? Bot : MousePointerClick;

  return (
    <div
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onClick?.();
        }
      }}
      className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4 sm:p-5 hover:border-violet-500 hover:shadow-md hover:-translate-y-0.5 transition-all cursor-pointer focus:outline-none focus:ring-2 focus:ring-violet-500/50"
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3 mb-4">
        <div className="flex items-center gap-3 min-w-0 flex-1">
          <div className="text-3xl sm:text-4xl shrink-0" aria-hidden="true">
            {employee.avatar}
          </div>
          <div className="min-w-0">
            <h3 className="font-semibold text-aegisaxiom-text truncate">{employee.name}</h3>
            <p className="text-xs sm:text-sm text-aegisaxiom-text-secondary truncate">{employee.role}</p>
          </div>
        </div>
        <div
          className={`px-2 sm:px-3 py-1 rounded-full text-[11px] sm:text-xs font-medium ${statusConfig.bgColor} ${statusConfig.textColor} flex items-center gap-1.5 shrink-0`}
        >
          <span
            className={`w-1.5 h-1.5 sm:w-2 sm:h-2 rounded-full ${statusConfig.color} ${employee.status === 'active' ? 'animate-pulse' : ''}`}
          ></span>
          {statusConfig.label}
        </div>
      </div>

      {/* 3 Key Metrics */}
      <div className="grid grid-cols-3 gap-2 sm:gap-3 mb-4 pb-4 border-b border-aegisaxiom-border">
        <div className="min-w-0">
          <p className="text-[11px] sm:text-xs text-aegisaxiom-text-muted mb-1 leading-tight">
            {employee.metrics.primary.label}
          </p>
          <p className="text-base sm:text-lg font-bold text-aegisaxiom-text truncate">
            {employee.metrics.primary.value}
            {employee.metrics.primary.unit}
          </p>
        </div>
        <div className="min-w-0">
          <p className="text-[11px] sm:text-xs text-aegisaxiom-text-muted mb-1 leading-tight">
            {employee.metrics.secondary.label}
          </p>
          <p className="text-base sm:text-lg font-bold text-green-600 dark:text-green-400 truncate">
            {employee.metrics.secondary.value}
            {employee.metrics.secondary.unit}
          </p>
        </div>
        <div className="min-w-0">
          <p className="text-[11px] sm:text-xs text-aegisaxiom-text-muted mb-1 leading-tight">
            {employee.metrics.tertiary.label}
          </p>
          <p className="text-base sm:text-lg font-bold text-blue-600 dark:text-blue-400 truncate">
            {employee.metrics.tertiary.value}
            {employee.metrics.tertiary.unit}
          </p>
        </div>
      </div>

      {/* Current Tasks */}
      <div className="mb-4">
        <div className="flex items-center justify-between mb-2 gap-2">
          <p className="text-xs sm:text-sm font-medium text-aegisaxiom-text-secondary">Active Tasks</p>
          <span className="text-[11px] sm:text-xs text-aegisaxiom-text-muted shrink-0">
            {employee.tasksActive} running
          </span>
        </div>
        {employee.currentTasks.slice(0, 2).map((task) => (
          <div key={task.id} className="mb-2 last:mb-0">
            <div className="flex items-center justify-between mb-1 gap-2">
              <span className="text-[11px] sm:text-xs text-aegisaxiom-text-secondary truncate">
                {task.title}
              </span>
              <span className="text-[11px] sm:text-xs text-aegisaxiom-text-muted shrink-0">
                {task.progress}%
              </span>
            </div>
            <div className="h-1.5 bg-aegisaxiom-elevated rounded-full overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-violet-600 to-purple-600 transition-all duration-300"
                style={{ width: `${task.progress}%` }}
              ></div>
            </div>
          </div>
        ))}
        {employee.currentTasks.length === 0 && (
          <p className="text-[11px] sm:text-xs text-aegisaxiom-text-muted italic">No active tasks</p>
        )}
      </div>

      {/* Mode Badge + Access */}
      <div className="flex items-center justify-between gap-2">
        <span
          className={`inline-flex items-center gap-1.5 px-2 py-1 rounded text-[11px] sm:text-xs font-medium ${
            employee.mode === 'automatic'
              ? 'bg-blue-50 dark:bg-blue-500/20 text-blue-700 dark:text-blue-300'
              : 'bg-amber-50 dark:bg-amber-500/20 text-amber-700 dark:text-amber-300'
          }`}
        >
          <ModeIcon className="w-3 h-3" />
          {employee.mode === 'automatic' ? 'Automatic' : 'Manual'}
        </span>
        <div className="flex items-center gap-1.5">
          {canViewAccess && (
            <button
              onClick={onViewAccess}
              className="inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] sm:text-xs font-medium bg-violet-50 dark:bg-violet-500/10 text-violet-700 dark:text-violet-300 border border-violet-500/20 hover:border-violet-500/50 transition"
              title="View access summary"
            >
              <Shield className="w-3 h-3" />
              Access
            </button>
          )}
          <ChevronRight className="w-4 h-4 text-aegisaxiom-text-muted shrink-0" />
        </div>
      </div>
    </div>
  );
}

// AI Employee List Item (Vertical Stack View - like reference image)
function AIEmployeeListItem({ employee, onClick, canViewAccess, onViewAccess }) {
  const statusConfig = STATUS_CONFIG[employee.status];
  const ModeIcon = employee.mode === 'automatic' ? Bot : MousePointerClick;

  return (
    <div
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onClick?.();
        }
      }}
      className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-lg p-4 sm:p-6 hover:border-violet-500 hover:shadow-md transition-all cursor-pointer focus:outline-none focus:ring-2 focus:ring-violet-500/50"
    >
      <div className="flex flex-col sm:flex-row sm:items-center gap-4 sm:gap-6">
        {/* Avatar + Status — row on mobile, column on desktop */}
        <div className="flex items-center sm:flex-col sm:items-center gap-3 sm:gap-2 shrink-0">
          <div className="text-4xl sm:text-5xl" aria-hidden="true">
            {employee.avatar}
          </div>
          <div
            className={`px-2 py-0.5 rounded-full text-[10px] sm:text-[10px] font-medium ${statusConfig.bgColor} ${statusConfig.textColor} flex items-center gap-1`}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${statusConfig.color} ${employee.status === 'active' ? 'animate-pulse' : ''}`}
            ></span>
            {statusConfig.label}
          </div>
        </div>

        {/* Name & Role + Metrics */}
        <div className="flex-1 min-w-0">
          <h3 className="text-lg sm:text-2xl font-bold text-aegisaxiom-text truncate">{employee.name}</h3>
          <p className="text-sm sm:text-base text-aegisaxiom-text-secondary mb-3 truncate">
            {employee.role}
          </p>

          {/* Metrics — 3-col grid on mobile, inline flex on desktop */}
          <div className="grid grid-cols-3 gap-2 sm:flex sm:items-center sm:gap-8">
            <div className="min-w-0">
              <p className="text-[11px] sm:text-sm text-aegisaxiom-text-secondary mb-0.5 truncate leading-tight">
                {employee.metrics.primary.label}
              </p>
              <p className="text-base sm:text-xl font-bold text-aegisaxiom-text truncate">
                {employee.metrics.primary.value}
                {employee.metrics.primary.unit}
              </p>
            </div>
            <div className="min-w-0">
              <p className="text-[11px] sm:text-sm text-aegisaxiom-text-secondary mb-0.5 truncate leading-tight">
                {employee.metrics.secondary.label}
              </p>
              <p className="text-base sm:text-xl font-bold text-green-600 dark:text-green-400 truncate">
                {employee.metrics.secondary.value}
                {employee.metrics.secondary.unit}
              </p>
            </div>
            <div className="min-w-0">
              <p className="text-[11px] sm:text-sm text-aegisaxiom-text-secondary mb-0.5 truncate leading-tight">
                {employee.metrics.tertiary.label}
              </p>
              <p className="text-base sm:text-xl font-bold text-blue-600 dark:text-blue-400 truncate">
                {employee.metrics.tertiary.value}
                {employee.metrics.tertiary.unit}
              </p>
            </div>
          </div>
        </div>

        {/* Mode & Action — row on mobile bottom, column on desktop right */}
        <div className="flex items-center justify-between sm:flex-col sm:items-end gap-2 sm:gap-3 sm:shrink-0">
          <span
            className={`inline-flex items-center gap-1.5 px-2 py-1 sm:px-3 sm:py-1.5 rounded-lg text-[11px] sm:text-xs font-medium ${
              employee.mode === 'automatic'
                ? 'bg-blue-50 dark:bg-blue-500/20 text-blue-700 dark:text-blue-300 border border-blue-500/30'
                : 'bg-amber-50 dark:bg-amber-500/20 text-amber-700 dark:text-amber-300 border border-amber-500/30'
            }`}
          >
            <ModeIcon className="w-3 h-3" />
            {employee.mode === 'automatic' ? 'Automatic' : 'Manual'}
          </span>
          {canViewAccess && (
            <button
              onClick={onViewAccess}
              className="inline-flex items-center gap-1.5 px-2 py-1 sm:px-3 sm:py-1.5 rounded-lg text-[11px] sm:text-xs font-medium bg-violet-50 dark:bg-violet-500/10 text-violet-700 dark:text-violet-300 border border-violet-500/30 hover:border-violet-500/60 transition"
              title="View access summary"
            >
              <Shield className="w-3 h-3" />
              Access
            </button>
          )}
          <div className="flex items-center gap-2 text-[11px] sm:text-xs text-aegisaxiom-text-muted">
            <Activity className="w-3 h-3" />
            <span className="truncate">{employee.lastActivity}</span>
          </div>
          <ChevronRight className="hidden sm:block w-5 h-5 text-aegisaxiom-text-muted" />
        </div>
      </div>

      {/* Active Tasks Preview */}
      {employee.currentTasks.length > 0 && (
        <div className="mt-4 pt-4 border-t border-aegisaxiom-border">
          <p className="text-xs text-aegisaxiom-text-muted mb-2">
            Currently working on {employee.tasksActive} task
            {employee.tasksActive !== 1 ? 's' : ''}
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            {employee.currentTasks.slice(0, 3).map((task) => (
              <div key={task.id} className="bg-aegisaxiom-base border border-aegisaxiom-border rounded p-2">
                <p className="text-[11px] text-aegisaxiom-text-secondary mb-1 truncate">{task.title}</p>
                <div className="flex items-center justify-between gap-2">
                  <div className="h-1 flex-1 bg-aegisaxiom-elevated rounded-full overflow-hidden">
                    <div
                      className="h-full bg-gradient-to-r from-violet-600 to-purple-600"
                      style={{ width: `${task.progress}%` }}
                    ></div>
                  </div>
                  <span className="text-[10px] text-aegisaxiom-text-muted shrink-0">
                    {task.progress}%
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// AI Employee Detail Modal
function AIEmployeeDetailModal({
  employee,
  onClose,
  onStatusChange,
  onDelete,
  onEdit,
  canEdit,
  canDelete,
  canManageStatus,
  canManage,
}) {
  const isNativeConversational =
    employee.type === 'conversational' &&
    !employee.connection?.embedded_url &&
    !employee._connection?.embeddedUrl;
  const [activeTab, setActiveTab] = useState(isNativeConversational ? 'chat' : 'workflow');
  const [executionData, setExecutionData] = useState(null);
  const [loadingExecutions, setLoadingExecutions] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const statusConfig = STATUS_CONFIG[employee.status];

  // Fetch real execution data for API-backed employees
  useEffect(() => {
    if (employee._fromApi && employee.employee_id) {
      setLoadingExecutions(true);
      api
        .get(`${API}/api/ai-employees/${employee.employee_id}/executions?limit=100`)
        .then((res) => res.json())
        .then((data) => {
          if (data && data.executions) {
            setExecutionData(data);
          }
        })
        .catch((err) => console.error('Failed to fetch execution data:', err))
        .finally(() => setLoadingExecutions(false));
    }
  }, [employee._fromApi, employee.employee_id]);

  const handleAction = (action) => {
    const empId = employee.employee_id || employee.id;
    if (action === 'Pause') {
      onStatusChange?.(empId, 'paused');
      onClose();
    } else if (action === 'Resume') {
      onStatusChange?.(empId, 'active');
      onClose();
    } else if (action === 'Delete') {
      onDelete?.(empId);
      onClose();
    } else {
      toast.info(`${action} action coming soon for ${employee.name}!`);
    }
  };

  const copyWebhookUrl = () => {
    const url = `${window.location.origin}/api/ai-employees/webhook/${employee.employee_id || employee.id}`;
    navigator.clipboard
      .writeText(url)
      .then(() => {
        toast.success('Webhook URL copied to clipboard!');
      })
      .catch(() => {
        toast.info(url);
      });
  };

  const tabs = isNativeConversational
    ? [
        { id: 'chat', label: 'Chat', icon: MessageSquare },
        { id: 'activity', label: 'Activity Dashboard', icon: BarChart3 },
        { id: 'config', label: 'Configuration', icon: Settings },
        ...(canManage ? [{ id: 'access', label: 'Access Control', icon: Shield }] : []),
      ]
    : [
        { id: 'workflow', label: 'Workflow', icon: Target },
        { id: 'activity', label: 'Activity Dashboard', icon: BarChart3 },
        { id: 'tasks', label: 'Tasks', icon: CheckCircle },
        { id: 'logs', label: 'Live Logs', icon: Activity },
        { id: 'config', label: 'Configuration', icon: Settings },
        ...(canManage ? [{ id: 'access', label: 'Access Control', icon: Shield }] : []),
      ];

  // Escape key to close + body scroll lock.
  // Ref avoids re-running effect when parent passes a new onClose ref each render
  // (which would otherwise corrupt the prev-overflow snapshot).
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key === 'Escape') onCloseRef.current?.();
    };
    window.addEventListener('keydown', onKeyDown);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = prev;
    };
  }, []);

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 sm:p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`${employee.name} details`}
    >
      <div
        className="bg-aegisaxiom-surface border border-aegisaxiom-border w-full h-full sm:rounded-lg sm:max-w-6xl sm:max-h-[90vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="bg-gradient-to-r from-violet-600/20 to-purple-600/20 border-b border-aegisaxiom-border p-4 sm:p-6 shrink-0">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-3 sm:gap-4 min-w-0 flex-1">
              <div className="text-4xl sm:text-6xl shrink-0" aria-hidden="true">
                {employee.avatar}
              </div>
              <div className="min-w-0">
                <h2 className="text-lg sm:text-2xl font-bold text-aegisaxiom-text truncate">
                  {employee.name}
                </h2>
                <p className="text-sm text-aegisaxiom-text-secondary mb-2 truncate">{employee.role}</p>
                <div className="flex items-center gap-2 flex-wrap">
                  <div
                    className={`inline-flex items-center gap-1.5 px-2 sm:px-3 py-1 rounded-full text-xs sm:text-sm font-medium ${statusConfig.bgColor} ${statusConfig.textColor}`}
                  >
                    <span
                      className={`w-1.5 h-1.5 sm:w-2 sm:h-2 rounded-full ${statusConfig.color} ${employee.status === 'active' ? 'animate-pulse' : ''}`}
                    ></span>
                    {statusConfig.label}
                  </div>
                  <span
                    className={`inline-flex items-center gap-1.5 px-2 sm:px-3 py-1 rounded-full text-xs sm:text-sm font-medium ${
                      employee.mode === 'automatic'
                        ? 'bg-blue-50 dark:bg-blue-500/20 text-blue-700 dark:text-blue-300'
                        : 'bg-amber-50 dark:bg-amber-500/20 text-amber-700 dark:text-amber-300'
                    }`}
                  >
                    {employee.mode === 'automatic' ? (
                      <Bot className="w-3 h-3" />
                    ) : (
                      <MousePointerClick className="w-3 h-3" />
                    )}
                    {employee.mode === 'automatic' ? 'Automatic' : 'Manual'}
                  </span>
                </div>
              </div>
            </div>
            <button
              onClick={onClose}
              aria-label="Close"
              className="w-9 h-9 rounded-full bg-aegisaxiom-elevated hover:bg-aegisaxiom-base flex items-center justify-center transition shrink-0"
            >
              <X className="w-4 h-4 text-aegisaxiom-text-secondary" />
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 px-3 sm:px-6 pt-3 sm:pt-4 pb-1 border-b border-aegisaxiom-border overflow-x-auto shrink-0">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              aria-pressed={activeTab === tab.id}
              className={`flex items-center gap-2 px-3 sm:px-4 py-2 text-xs sm:text-sm font-medium rounded-t-lg transition whitespace-nowrap ${
                activeTab === tab.id
                  ? 'bg-aegisaxiom-base text-aegisaxiom-text border-t border-x border-aegisaxiom-border'
                  : 'text-aegisaxiom-text-secondary hover:text-aegisaxiom-text'
              }`}
            >
              <tab.icon className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
              <span className="hidden sm:inline">{tab.label}</span>
              <span className="sm:hidden">{tab.label.split(' ')[0]}</span>
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-4 sm:px-6 pt-4 sm:pt-8 pb-6 min-h-0">
          {activeTab === 'chat' && <EmbeddedChatTab employee={employee} />}
          {activeTab === 'workflow' && <WorkflowTab employee={employee} />}
          {activeTab === 'activity' && (
            <ActivityDashboardTab employee={employee} executionData={executionData} />
          )}
          {activeTab === 'tasks' && (
            <TasksTab
              employee={employee}
              executionData={executionData}
              loadingExecutions={loadingExecutions}
            />
          )}
          {activeTab === 'logs' && (
            <LiveLogsTab
              employee={employee}
              executionData={executionData}
              loadingExecutions={loadingExecutions}
            />
          )}
          {activeTab === 'config' && <ConfigTab employee={employee} />}
          {activeTab === 'access' && canManage && <AccessControlTab employee={employee} />}
        </div>

        {/* Actions */}
        <div className="border-t border-aegisaxiom-border p-3 sm:p-4 flex flex-col sm:flex-row sm:justify-between gap-2 sm:gap-3 shrink-0">
          <div className="flex flex-wrap gap-2">
            {employee.mode === 'manual' && employee.status === 'active' && (
              <button
                onClick={() => handleAction('Run Now')}
                className="flex items-center gap-2 px-3 sm:px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded-lg transition"
              >
                <Play className="w-4 h-4" />
                Run Now
              </button>
            )}
            {employee.status === 'active' && (
              <button
                onClick={() => {
                  if (!canManageStatus) {
                    toast.error(
                      "You don't have permission to change AI Employee status. Contact your administrator.",
                    );
                    return;
                  }
                  handleAction('Pause');
                }}
                className={`flex items-center gap-2 px-3 sm:px-4 py-2 text-sm rounded-lg transition ${canManageStatus ? 'bg-yellow-600 hover:bg-yellow-500 text-white' : 'bg-yellow-50 dark:bg-yellow-600/20 text-yellow-700 dark:text-yellow-300/50'}`}
              >
                <Pause className="w-4 h-4" />
                Pause
              </button>
            )}
            {employee.status === 'paused' && (
              <button
                onClick={() => {
                  if (!canManageStatus) {
                    toast.error(
                      "You don't have permission to change AI Employee status. Contact your administrator.",
                    );
                    return;
                  }
                  handleAction('Resume');
                }}
                className={`flex items-center gap-2 px-3 sm:px-4 py-2 text-sm rounded-lg transition ${canManageStatus ? 'bg-green-600 hover:bg-green-500 text-white' : 'bg-green-50 dark:bg-green-600/20 text-green-700 dark:text-green-300/50'}`}
              >
                <Play className="w-4 h-4" />
                Resume
              </button>
            )}
            <button
              onClick={() => handleAction('Configure')}
              className="flex items-center gap-2 px-3 sm:px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-base text-aegisaxiom-text text-sm rounded-lg transition"
            >
              <Settings className="w-4 h-4" />
              <span className="hidden sm:inline">Configure</span>
              <span className="sm:hidden">Config</span>
            </button>
            {/* Webhook URL button for API-backed employees */}
            {employee._fromApi && (
              <button
                onClick={copyWebhookUrl}
                className="flex items-center gap-2 px-3 sm:px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-base text-aegisaxiom-text text-sm rounded-lg transition"
                title="Copy webhook URL for N8N"
              >
                <Link className="w-4 h-4" />
                <span className="hidden sm:inline">Webhook URL</span>
                <span className="sm:hidden">Webhook</span>
              </button>
            )}
            {/* Edit button for API-backed employees */}
            {employee._fromApi && (
              <button
                onClick={() => {
                  if (!canEdit) {
                    toast.error(
                      "You don't have permission to edit AI Employees. Contact your administrator to request access.",
                    );
                    return;
                  }
                  onEdit?.(employee);
                  onClose();
                }}
                className={`flex items-center gap-2 px-3 sm:px-4 py-2 text-sm rounded-lg transition ${canEdit ? 'bg-blue-50 dark:bg-blue-600/20 hover:bg-blue-600/30 text-blue-700 dark:text-blue-300' : 'bg-aegisaxiom-accent-muted text-aegisaxiom-text-muted'}`}
              >
                <Pencil className="w-4 h-4" />
                Edit
              </button>
            )}
          </div>
          <div className="flex gap-2">
            {/* Delete button */}
            <button
              onClick={() => {
                if (!canDelete) {
                  toast.error(
                    "You don't have permission to delete AI Employees. Only administrators can perform this action.",
                  );
                  return;
                }
                setShowDeleteConfirm(true);
              }}
              className={`flex items-center gap-2 px-3 sm:px-4 py-2 text-sm rounded-lg transition ${canDelete ? 'bg-red-50 dark:bg-red-600/20 hover:bg-red-600/30 text-red-700 dark:text-red-300' : 'bg-aegisaxiom-accent-muted text-aegisaxiom-text-muted'}`}
            >
              <Trash2 className="w-4 h-4" />
              Delete
            </button>
            <button
              onClick={onClose}
              className="flex-1 sm:flex-none px-3 sm:px-4 py-2 bg-aegisaxiom-elevated hover:bg-aegisaxiom-base text-aegisaxiom-text text-sm rounded-lg transition"
            >
              Close
            </button>
          </div>
        </div>

        {/* Delete Confirmation Modal */}
        {showDeleteConfirm && (
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4 sm:rounded-lg"
            onClick={() => setShowDeleteConfirm(false)}
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="delete-ai-employee-title"
          >
            <div
              className="bg-aegisaxiom-surface border border-aegisaxiom-border rounded-xl w-full max-w-sm p-5 sm:p-6 animate-fadeInUp"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-full bg-red-50 dark:bg-red-600/20 flex items-center justify-center shrink-0">
                  <Trash2 className="w-5 h-5 text-red-600 dark:text-red-400" />
                </div>
                <div>
                  <h3
                    id="delete-ai-employee-title"
                    className="text-base font-semibold text-aegisaxiom-text"
                  >
                    Delete AI Employee
                  </h3>
                  <p className="text-xs text-aegisaxiom-text-secondary mt-0.5">
                    This action cannot be undone
                  </p>
                </div>
              </div>
              <p className="text-sm text-aegisaxiom-text-secondary mb-6">
                Are you sure you want to permanently delete{' '}
                <span className="font-medium text-aegisaxiom-text">"{employee.name}"</span>? All
                associated data, logs, and configurations will be removed.
              </p>
              <div className="flex gap-3">
                <button
                  onClick={() => setShowDeleteConfirm(false)}
                  className="flex-1 px-4 py-2.5 bg-aegisaxiom-elevated hover:bg-aegisaxiom-base text-aegisaxiom-text text-sm font-medium rounded-lg transition"
                >
                  Cancel
                </button>
                <button
                  onClick={() => {
                    setShowDeleteConfirm(false);
                    handleAction('Delete');
                  }}
                  className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-red-600 hover:bg-red-500 text-white text-sm font-medium rounded-lg transition"
                >
                  <Trash2 className="w-4 h-4" />
                  Delete
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// Tab Content Components

// Embedded Chat Tab - Full chat interface for native conversational employees
function EmbeddedChatTab({ employee }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [session, setSession] = useState(null);
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [attachments, setAttachments] = useState([]);
  // Wave 7 — resume bookkeeping. resumedSession drives the "Resumed from..."
  // banner; sidebarRefreshKey bumps the sidebar list when a new session is
  // created or renamed.
  const [resumedSession, setResumedSession] = useState(null);
  const [sidebarRefreshKey, setSidebarRefreshKey] = useState(0);
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const initializedRef = useRef(false);

  const agentRoute = `emp_${employee.employee_id || employee.id}`;
  const isVega = (employee.employee_id || employee.id) === 'ai_vega';

  // Initialize once: find or create a pinned session. Resume from the
  // sidebar runs as a separate one-shot callback (handleResume below), so
  // it never re-triggers this effect — that race was making every "old
  // session" click re-load the active pinned chat instead of the resumed
  // one.
  useEffect(() => {
    if (initializedRef.current) return;
    initializedRef.current = true;
    const init = async () => {
      // Look for existing pinned session
      const sessRes = await api.get(`${API}/api/chat/sessions`);
      if (sessRes.ok) {
        const sessions = await sessRes.json();
        const existing = sessions.find((s) => s.pinned_agent === agentRoute);
        if (existing) {
          setSession(existing);
          const msgRes = await api.get(`${API}/api/chat/sessions/${existing.session_id}/messages`);
          if (msgRes.ok) setMessages(await msgRes.json());
          return;
        }
      }
      // Create new session; auto-greeting agents write their greeting to DB, so fetch it
      const newRes = await api.post(`${API}/api/chat/sessions`, { agent: agentRoute });
      if (newRes.ok) {
        const newSession = await newRes.json();
        setSession(newSession);
        const autoGreetAgents = ['ai_vega', 'ai_orion'];
        if (autoGreetAgents.includes(employee.employee_id)) {
          const msgRes = await api.get(
            `${API}/api/chat/sessions/${newSession.session_id}/messages`,
          );
          if (msgRes.ok) setMessages(await msgRes.json());
        } else {
          setMessages([
            {
              message_id: `welcome_${Date.now()}`,
              role: 'assistant',
              content:
                employee.employee_id === 'ai_arc'
                  ? `Welcome to the **AI Recruiter Certification** program. I'm ${employee.name || 'ARC'}, and I'll be walking you through 7 modules covering everything from the build vs. buy paradigm to objection handling and live demo strategy.\n\nDo you have any questions before we begin, or would you like to jump straight into **Module 1**?`
                  : `Hi! I'm **${employee.name}**. How can I help you today?`,
              created_at: new Date().toISOString(),
            },
          ]);
        }
        setSidebarRefreshKey((k) => k + 1);
      }
    };
    init();
  }, [agentRoute]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleResume = async (target) => {
    if (!target?.session_id) return;
    try {
      const res = await api.get(
        `${API}/api/vega/my-sessions/${target.session_id}/resume`,
      );
      if (!res.ok) return;
      const bundle = await res.json();
      if (bundle.chat_session) setSession(bundle.chat_session);
      setMessages(bundle.chat_messages || []);
      setResumedSession({
        session_id: target.session_id,
        session_name: target.session_name,
        updated_at: target.updated_at,
      });
    } catch {
      /* ignore — sidebar will show its own error state on next refresh */
    }
  };

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    if (session && !loading) inputRef.current?.focus();
  }, [session, loading]);

  const sendMessage = async (e) => {
    e?.preventDefault();
    if ((!input.trim() && attachments.length === 0) || loading || !session) return;

    const userText = input.trim();
    // Same convention as ChatPage — prepends one [ATTACHED: …] marker per
    // file. The backend conductor's _parse_upload_ids regex.findall picks
    // up all of them. Vega's freeflow mode prompt handles the inline-drop
    // UX (Q&A / plan / Call N analysis / full analysis).
    const markers = attachments
      .map((a) => `[ATTACHED: ${a.filename} (upload_id=${a.upload_id})]`)
      .join(' ');
    const content = attachments.length
      ? `${markers}${userText ? `\n\n${userText}` : ''}`
      : userText;
    // Friendlier display version — don't show the raw markers in the chat bubble.
    const displayMarkers = attachments.map((a) => `📎 ${a.filename}`).join('  ');
    const displayContent = attachments.length
      ? `${displayMarkers}${userText ? `\n\n${userText}` : ''}`
      : userText;
    setInput('');
    setAttachments([]);
    setMessages((prev) => [
      ...prev,
      {
        message_id: `tmp_${Date.now()}`,
        role: 'user',
        content: displayContent,
        created_at: new Date().toISOString(),
      },
    ]);
    setLoading(true);

    try {
      const res = await api.post(`${API}/api/chat/sessions/${session.session_id}/messages`, {
        content,
      });
      if (res.ok) {
        const aiMsg = await res.json();
        if (aiMsg.content && aiMsg.content.trim()) {
          setMessages((prev) => [...prev, aiMsg]);
        }
      } else {
        setMessages((prev) => [
          ...prev,
          {
            message_id: `err_${Date.now()}`,
            role: 'assistant',
            content: 'Something went wrong. Please try again.',
            created_at: new Date().toISOString(),
          },
        ]);
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          message_id: `err_${Date.now()}`,
          role: 'assistant',
          content: 'Connection error. Please try again.',
          created_at: new Date().toISOString(),
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  // Ref to avoid re-registering listener on every loading toggle
  const loadingRef = useRef(loading);
  loadingRef.current = loading;

  // Listen for quiz grading events — the backend now auto-generates AI feedback,
  // so we just need to refresh messages to show it (no fake user message needed)
  useEffect(() => {
    const handler = async () => {
      if (!session) return;
      // The submit endpoint returns ai_feedback_message and saves it to DB.
      // Refresh messages from the server to pick up both the grading system
      // message and the AI feedback response.
      setLoading(true);
      try {
        const res = await api.get(`${API}/api/chat/sessions/${session.session_id}/messages`);
        if (res.ok) setMessages(await res.json());
      } catch {
        /* ignore */
      } finally {
        setLoading(false);
      }
    };
    window.addEventListener('arc-quiz-graded', handler);
    // Also listen for artifact submissions (single_select, multi_upload) that auto-trigger AI responses
    window.addEventListener('artifact-submitted', handler);
    return () => {
      window.removeEventListener('arc-quiz-graded', handler);
      window.removeEventListener('artifact-submitted', handler);
    };
  }, [session]); // eslint-disable-line react-hooks/exhaustive-deps

  // Wave 7 — start a fresh Vega session from the sidebar's "+" button.
  // Wipes the existing pinned chat and seeds a fresh one inline, then
  // restores the message + session state.
  const handleNewSession = async () => {
    if (!isVega || loading) return;
    setMessages([]);
    setSession(null);
    setResumedSession(null);
    // The init effect's pinned-session find will still match the existing
    // ai_vega pinned session — to truly start fresh, we bump the chat
    // history wipe via the reset endpoint (same one Reset Session uses).
    try {
      const sessRes = await api.get(`${API}/api/chat/sessions`);
      if (sessRes.ok) {
        const sessions = await sessRes.json();
        const existing = sessions.find((s) => s.pinned_agent === agentRoute);
        if (existing) {
          await api.delete(`${API}/api/chat/sessions/${existing.session_id}`);
        }
      }
      const newRes = await api.post(`${API}/api/chat/sessions`, { agent: agentRoute });
      if (newRes.ok) {
        const newSession = await newRes.json();
        setSession(newSession);
        const msgRes = await api.get(`${API}/api/chat/sessions/${newSession.session_id}/messages`);
        if (msgRes.ok) setMessages(await msgRes.json());
        setSidebarRefreshKey((k) => k + 1);
      }
    } catch {
      /* ignore */
    }
  };

  const chatLayout = (
    <div className="flex flex-col h-[60vh] flex-1 min-w-0">
      {resumedSession && (
        <ResumeBanner session={resumedSession} onDismiss={() => setResumedSession(null)} />
      )}
      {/* Messages */}
      <div className="flex-1 overflow-y-auto space-y-4 pb-4">
        {messages.length === 0 && !loading && (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <div className="text-5xl mb-4">{employee.avatar || '🎓'}</div>
            <h3 className="text-lg font-medium text-aegisaxiom-text mb-1">{employee.name}</h3>
            <p className="text-sm text-aegisaxiom-text-secondary max-w-md">
              {employee.description || employee.role}
            </p>
            <p className="text-xs text-aegisaxiom-text-muted mt-4">Send a message to get started</p>
          </div>
        )}
        {messages
          .filter((m) => {
            // Hide system-injected messages from the UI
            if (m.role === 'user' && typeof m.content === 'string') {
              if (m.content.startsWith('[INTAKE SELECTION]')) return false;
              if (m.content.startsWith('[UPLOADS SUBMITTED]')) return false;
              if (m.content.startsWith('[QUIZ GRADED]')) return false;
            }
            return true;
          })
          .map((m) => (
            <div
              key={m.message_id}
              className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`flex gap-3 ${m.role === 'user' ? 'flex-row-reverse max-w-2xl' : 'max-w-[90%]'}`}
              >
                {m.role === 'assistant' && (
                  <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-600 to-fuchsia-600 flex items-center justify-center shrink-0 mt-0.5 text-sm">
                    {employee.avatar || '🤖'}
                  </div>
                )}
                <div
                  className={`rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                    m.role === 'user'
                      ? 'bg-violet-600 text-white rounded-br-md'
                      : 'bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm rounded-bl-md border border-aegisaxiom-border'
                  }`}
                >
                  {m.role === 'user' ? (
                    <p className="whitespace-pre-wrap">{m.content}</p>
                  ) : (
                    <ChatMarkdown content={m.content} />
                  )}
                </div>
              </div>
            </div>
          ))}
        {loading && (
          <div className="flex justify-start">
            <div className="flex gap-3">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-600 to-fuchsia-600 flex items-center justify-center shrink-0 text-sm">
                {employee.avatar || '🤖'}
              </div>
              <div className="rounded-2xl px-4 py-3 bg-aegisaxiom-elevated border border-aegisaxiom-border">
                <Loader2 size={16} className="animate-spin text-violet-600 dark:text-violet-400" />
              </div>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <form onSubmit={sendMessage} className="flex gap-3 pt-4 border-t border-aegisaxiom-border items-end">
        {/* Wave 7 — Reset Chat button removed for Vega. Sessions now persist
            and live in the sidebar; users start fresh via the sidebar's
            "+" button. Other employees keep the Reset affordance. */}
        {!isVega && (
          <button
            type="button"
            onClick={() => setShowResetConfirm(true)}
            disabled={loading || !session}
            title="Reset chat and progress"
            className="px-3 py-3 text-aegisaxiom-text-secondary hover:text-aegisaxiom-text hover:bg-aegisaxiom-elevated disabled:opacity-50 rounded-xl transition-colors shrink-0"
          >
            <RotateCcw size={16} />
          </button>
        )}
        {session && (
          <ChatInputAttachButton
            sessionId={session.session_id}
            attachments={attachments}
            onChange={setAttachments}
            disabled={loading}
          />
        )}
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              if ((input.trim() || attachments.length > 0) && !loading && session) {
                e.target.form.requestSubmit();
              }
            }
          }}
          onInput={(e) => {
            e.target.style.height = 'auto';
            e.target.style.height = Math.min(e.target.scrollHeight, 200) + 'px';
          }}
          placeholder={
            attachments.length === 1
              ? `Add a note or send to share ${attachments[0].filename}…`
              : attachments.length > 1
                ? `Add a note or send to share ${attachments.length} files…`
                : `Message ${employee.name}...`
          }
          disabled={loading || !session}
          rows={1}
          style={{ maxHeight: '200px' }}
          className="flex-1 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-xl px-4 py-3 text-sm text-aegisaxiom-text placeholder-aegisaxiom-text-muted focus:outline-none focus:border-violet-500 disabled:opacity-50 resize-none overflow-y-auto"
        />
        <button
          type="submit"
          disabled={loading || (!input.trim() && attachments.length === 0) || !session}
          className="px-4 py-3 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 disabled:hover:bg-violet-600 text-white rounded-xl transition-colors shrink-0"
        >
          <Send size={16} />
        </button>
      </form>

      {showResetConfirm && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50">
          <div className="bg-aegisaxiom-card border border-aegisaxiom-border rounded-2xl p-6 max-w-sm mx-4 shadow-xl">
            <h3 className="text-lg font-semibold text-aegisaxiom-text mb-2">Reset Session?</h3>
            <p className="text-sm text-aegisaxiom-text-secondary mb-4">
              This will clear your current session and all progress. This cannot be undone.
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setShowResetConfirm(false)}
                className="px-4 py-2 text-sm text-aegisaxiom-text-secondary hover:text-aegisaxiom-text rounded-lg transition"
              >
                Cancel
              </button>
              <button
                onClick={async () => {
                  setShowResetConfirm(false);
                  if (!session) return;
                  const empId = employee.employee_id || employee.id;
                  await Promise.all([
                    api.delete(`${API}/api/chat/sessions/${session.session_id}`),
                    api.delete(`${API}/api/ai-employees/${empId}/user-state`),
                  ]);
                  setMessages([]);
                  setSession(null);
                  const newRes = await api.post(`${API}/api/chat/sessions`, { agent: agentRoute });
                  if (newRes.ok) {
                    const newSession = await newRes.json();
                    setSession(newSession);
                    const autoGreetAgents = ['ai_vega', 'ai_orion'];
                    if (autoGreetAgents.includes(employee.employee_id)) {
                      const msgRes = await api.get(
                        `${API}/api/chat/sessions/${newSession.session_id}/messages`,
                      );
                      if (msgRes.ok) setMessages(await msgRes.json());
                    } else {
                      setMessages([
                        {
                          message_id: `welcome_${Date.now()}`,
                          role: 'assistant',
                          content:
                            employee.employee_id === 'ai_arc'
                              ? `Welcome to the **AI Recruiter Certification** program. I'm ${employee.name || 'ARC'}, and I'll be walking you through 7 modules covering everything from the build vs. buy paradigm to objection handling and live demo strategy.\n\nDo you have any questions before we begin, or would you like to jump straight into **Module 1**?`
                              : `Hi! I'm **${employee.name}**. How can I help you today?`,
                          created_at: new Date().toISOString(),
                        },
                      ]);
                    }
                  }
                }}
                className="px-4 py-2 text-sm bg-red-600 hover:bg-red-500 text-white rounded-lg transition"
              >
                Reset
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );

  if (!isVega) return chatLayout;

  return (
    <div className="flex h-[60vh]">
      <VegaSessionSidebar
        activeSessionId={resumedSession?.session_id}
        onResume={handleResume}
        onNewSession={handleNewSession}
        refreshKey={sidebarRefreshKey}
      />
      {chatLayout}
    </div>
  );
}

// Workflow Tab - Shows visual workflow/process
function WorkflowTab({ employee }) {
  const workflowSteps = {
    ai_001: [
      // SOW Builder AI
      { id: 1, label: 'Trigger: New Opportunity', icon: Target, color: 'blue' },
      { id: 2, label: 'Analyze Requirements', icon: Brain, color: 'violet' },
      { id: 3, label: 'Calculate Resource Needs', icon: Users, color: 'green' },
      { id: 4, label: 'Generate SOW Draft', icon: FileText, color: 'amber' },
      { id: 5, label: 'Validate Margins', icon: DollarSign, color: 'red' },
      { id: 6, label: 'Submit for Review', icon: CheckCircle, color: 'green' },
    ],
    ai_002: [
      // Automation Rules AI
      { id: 1, label: 'Detect Event', icon: Zap, color: 'yellow' },
      { id: 2, label: 'Check Preconditions', icon: Filter, color: 'blue' },
      { id: 3, label: 'Evaluate Rules', icon: Target, color: 'violet' },
      { id: 4, label: 'Determine Actions', icon: Settings, color: 'amber' },
      { id: 5, label: 'Trigger Alerts/Blocks', icon: AlertCircle, color: 'red' },
      { id: 6, label: 'Log Activity', icon: Activity, color: 'green' },
    ],
    ai_003: [
      // Analytics AI
      { id: 1, label: 'Receive Request', icon: Target, color: 'blue' },
      { id: 2, label: 'Collect Data', icon: Search, color: 'violet' },
      { id: 3, label: 'Analyze Patterns', icon: BarChart3, color: 'green' },
      { id: 4, label: 'Generate Insights', icon: Brain, color: 'amber' },
      { id: 5, label: 'Create Visualizations', icon: TrendingUp, color: 'purple' },
      { id: 6, label: 'Deliver Report', icon: FileText, color: 'green' },
    ],
    ai_004: [
      // Communication AI
      { id: 1, label: 'Detect Notification Trigger', icon: Bell, color: 'blue' },
      { id: 2, label: 'Identify Recipients', icon: Users, color: 'violet' },
      { id: 3, label: 'Draft Message', icon: Edit, color: 'amber' },
      { id: 4, label: 'Select Channel', icon: MessageSquare, color: 'green' },
      { id: 5, label: 'Send Communication', icon: Mail, color: 'purple' },
      { id: 6, label: 'Track Delivery', icon: Eye, color: 'blue' },
    ],
    ai_005: [
      // Research AI
      { id: 1, label: 'Receive Research Task', icon: Target, color: 'blue' },
      { id: 2, label: 'Gather Data Sources', icon: Search, color: 'violet' },
      { id: 3, label: 'Extract Information', icon: FileText, color: 'amber' },
      { id: 4, label: 'Analyze Findings', icon: Brain, color: 'purple' },
      { id: 5, label: 'Synthesize Report', icon: Edit, color: 'green' },
      { id: 6, label: 'Deliver Insights', icon: CheckCircle, color: 'green' },
    ],
    ai_006: [
      // Callisto - Network Support
      { id: 1, label: 'Detect Network Alert', icon: AlertCircle, color: 'red' },
      { id: 2, label: 'Triage Issue Priority', icon: Filter, color: 'yellow' },
      { id: 3, label: 'Diagnose Root Cause', icon: Search, color: 'blue' },
      { id: 4, label: 'Apply Resolution', icon: Settings, color: 'violet' },
      { id: 5, label: 'Verify System Health', icon: CheckCircle, color: 'green' },
      { id: 6, label: 'Log Incident Details', icon: FileText, color: 'slate' },
    ],
    ai_007: [
      // Phillip - Security Trainer
      { id: 1, label: 'Identify Training Need', icon: Target, color: 'blue' },
      { id: 2, label: 'Prepare Training Materials', icon: FileText, color: 'violet' },
      { id: 3, label: 'Deliver Training Session', icon: Users, color: 'amber' },
      { id: 4, label: 'Conduct Assessment', icon: Edit, color: 'purple' },
      { id: 5, label: 'Score & Provide Feedback', icon: TrendingUp, color: 'green' },
      { id: 6, label: 'Track Completion', icon: CheckCircle, color: 'green' },
    ],
    ai_008: [
      // Sarah - Compliance
      { id: 1, label: 'Ingest New Document', icon: FileText, color: 'blue' },
      { id: 2, label: 'Parse & Extract Data', icon: Search, color: 'violet' },
      { id: 3, label: 'Map to Compliance Framework', icon: Target, color: 'amber' },
      { id: 4, label: 'Identify Gaps/Issues', icon: AlertCircle, color: 'red' },
      { id: 5, label: 'Generate Compliance Report', icon: FileText, color: 'purple' },
      { id: 6, label: 'Archive & Index', icon: CheckCircle, color: 'green' },
    ],
    ai_009: [
      // George - Code Checker
      { id: 1, label: 'Monitor Code Repository', icon: Eye, color: 'blue' },
      { id: 2, label: 'Run Static Analysis', icon: Search, color: 'violet' },
      { id: 3, label: 'Scan for Vulnerabilities', icon: Shield, color: 'red' },
      { id: 4, label: 'Check CVE Database', icon: AlertCircle, color: 'amber' },
      { id: 5, label: 'Flag Issues to DevOps', icon: Bell, color: 'yellow' },
      { id: 6, label: 'Generate Quality Report', icon: FileText, color: 'green' },
    ],
    ai_010: [
      // Felecia - Red Team
      { id: 1, label: 'Initiate Security Scan', icon: Zap, color: 'red' },
      { id: 2, label: 'Scan IP Ranges', icon: Search, color: 'violet' },
      { id: 3, label: 'Test for Vulnerabilities', icon: AlertCircle, color: 'amber' },
      { id: 4, label: 'Assess Risk Severity', icon: TrendingUp, color: 'yellow' },
      { id: 5, label: 'Deploy Security Patches', icon: Settings, color: 'blue' },
      { id: 6, label: 'Verify Patch Success', icon: CheckCircle, color: 'green' },
    ],
    ai_011: [
      // Frankie - Email Assistant
      { id: 1, label: 'Monitor Inbox', icon: Mail, color: 'blue' },
      { id: 2, label: 'Categorize Emails', icon: Filter, color: 'violet' },
      { id: 3, label: 'Detect Priority/Urgency', icon: AlertCircle, color: 'red' },
      { id: 4, label: 'Draft Auto-responses', icon: Edit, color: 'amber' },
      { id: 5, label: 'Track Follow-up Actions', icon: CheckCircle, color: 'purple' },
      { id: 6, label: 'Log Activity Metrics', icon: BarChart3, color: 'green' },
    ],
  };

  // Default workflow templates based on employee type/role
  const defaultWorkflows = {
    n8n: [
      { id: 1, label: 'Receive Trigger Event', icon: Zap, color: 'yellow' },
      { id: 2, label: 'Validate Input Data', icon: Filter, color: 'blue' },
      { id: 3, label: 'Process & Transform', icon: Brain, color: 'violet' },
      { id: 4, label: 'Execute Core Logic', icon: Target, color: 'amber' },
      { id: 5, label: 'Generate Output', icon: FileText, color: 'green' },
      { id: 6, label: 'Report Results to AEGISAXIOM', icon: CheckCircle, color: 'green' },
    ],
    conversational: [
      { id: 1, label: 'User Opens Chat Session', icon: MessageSquare, color: 'blue' },
      { id: 2, label: 'Parse User Input', icon: Brain, color: 'violet' },
      { id: 3, label: 'Process Request', icon: Target, color: 'amber' },
      { id: 4, label: 'Generate Response', icon: Edit, color: 'green' },
      { id: 5, label: 'Deliver to User', icon: CheckCircle, color: 'green' },
      { id: 6, label: 'Log Interaction', icon: Activity, color: 'slate' },
    ],
  };

  // Role-specific workflow overrides for common patterns
  const roleWorkflows = {
    email: [
      { id: 1, label: 'Receive Incoming Email', icon: Mail, color: 'blue' },
      { id: 2, label: 'Extract Email Metadata', icon: Search, color: 'violet' },
      { id: 3, label: 'Classify Content', icon: Brain, color: 'amber' },
      { id: 4, label: 'Assign Label/Category', icon: Target, color: 'green' },
      { id: 5, label: 'Apply Label to Email', icon: CheckCircle, color: 'green' },
      { id: 6, label: 'Report to AEGISAXIOM', icon: Activity, color: 'slate' },
    ],
    support: [
      { id: 1, label: 'Detect Support Alert', icon: AlertCircle, color: 'red' },
      { id: 2, label: 'Triage Issue Priority', icon: Filter, color: 'yellow' },
      { id: 3, label: 'Diagnose Root Cause', icon: Search, color: 'blue' },
      { id: 4, label: 'Apply Resolution', icon: Settings, color: 'violet' },
      { id: 5, label: 'Verify Resolution', icon: CheckCircle, color: 'green' },
      { id: 6, label: 'Log Incident Details', icon: FileText, color: 'slate' },
    ],
    security: [
      { id: 1, label: 'Initiate Security Scan', icon: Shield, color: 'red' },
      { id: 2, label: 'Scan for Vulnerabilities', icon: Search, color: 'violet' },
      { id: 3, label: 'Assess Risk Level', icon: AlertCircle, color: 'amber' },
      { id: 4, label: 'Apply Remediation', icon: Settings, color: 'blue' },
      { id: 5, label: 'Verify Patch Success', icon: CheckCircle, color: 'green' },
      { id: 6, label: 'Generate Security Report', icon: FileText, color: 'green' },
    ],
  };

  // Determine which steps to show
  let steps = workflowSteps[employee.id] || [];

  if (steps.length === 0) {
    // Try to match by role keywords
    const roleLower = (employee.role || '').toLowerCase();
    const nameLower = (employee.name || '').toLowerCase();
    const combined = `${roleLower} ${nameLower}`;

    if (
      combined.includes('email') ||
      combined.includes('classifier') ||
      combined.includes('inbox')
    ) {
      steps = roleWorkflows['email'];
    } else if (
      combined.includes('support') ||
      combined.includes('network') ||
      combined.includes('triage')
    ) {
      steps = roleWorkflows['support'];
    } else if (
      combined.includes('security') ||
      combined.includes('red team') ||
      combined.includes('compliance')
    ) {
      steps = roleWorkflows['security'];
    } else {
      // Fall back to type-based default
      steps = defaultWorkflows[employee.type] || defaultWorkflows['n8n'];
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-aegisaxiom-text">Execution Workflow</h3>
        <span className="text-sm text-aegisaxiom-text-secondary">
          How {employee.name} processes tasks
        </span>
      </div>

      {/* Workflow Steps */}
      <div className="relative">
        {steps.map((step, idx) => (
          <div key={step.id} className="flex items-start gap-4 mb-6 last:mb-0">
            {/* Step Number & Line */}
            <div className="flex flex-col items-center">
              <div
                className={`w-10 h-10 rounded-full bg-${step.color}-500/20 border-2 border-${step.color}-500/50 flex items-center justify-center`}
              >
                <step.icon className={`w-5 h-5 text-${step.color}-400`} />
              </div>
              {idx < steps.length - 1 && (
                <div className="w-0.5 h-12 bg-gradient-to-b from-aegisaxiom-elevated to-transparent mt-2"></div>
              )}
            </div>

            {/* Step Content */}
            <div className="flex-1 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4 mt-1">
              <div className="flex items-center justify-between mb-2">
                <h4 className="font-medium text-aegisaxiom-text">
                  Step {step.id}: {step.label}
                </h4>
                <span className="text-xs text-aegisaxiom-text-muted">
                  ~{Math.floor(Math.random() * 5 + 1)}s
                </span>
              </div>
              <p className="text-sm text-aegisaxiom-text-secondary">
                {step.id === 1 && 'Monitors for triggering events and conditions'}
                {step.id === 2 && 'Processes and validates input data'}
                {step.id === 3 && 'Performs core analysis and decision-making'}
                {step.id === 4 && 'Determines appropriate actions or outputs'}
                {step.id === 5 && 'Executes final actions and notifications'}
                {step.id === 6 && 'Completes workflow and logs results'}
              </p>
            </div>
          </div>
        ))}
      </div>

      {/* Triggers Section */}
      <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4 mt-6">
        <h4 className="text-sm font-semibold text-aegisaxiom-text mb-3 flex items-center gap-2">
          <Zap className="w-4 h-4 text-yellow-600 dark:text-yellow-400" />
          Automatic Triggers
        </h4>
        <div className="flex flex-wrap gap-2">
          {employee.triggers.map((trigger, idx) => (
            <span
              key={idx}
              className="px-3 py-1.5 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg text-xs text-aegisaxiom-text-secondary"
            >
              {trigger}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

// Activity Dashboard Tab - Shows real-time and historical activity
function ActivityDashboardTab({ employee, executionData }) {
  const hours = [
    '12AM',
    '2AM',
    '4AM',
    '6AM',
    '8AM',
    '10AM',
    '12PM',
    '2PM',
    '4PM',
    '6PM',
    '8PM',
    '10PM',
  ];

  // Build activity data from real executions if available
  let activityData = employee.activityData;
  let isRealData = false;

  if (executionData?.executions?.length > 0) {
    // Group executions by 2-hour buckets for last 24 hours
    const buckets = new Array(12).fill(0);
    const now = Date.now();
    const oneDayAgo = now - 24 * 60 * 60 * 1000;

    executionData.executions.forEach((exec) => {
      if (exec.timestamp) {
        const ts = new Date(exec.timestamp).getTime();
        if (ts >= oneDayAgo) {
          const hourOfDay = new Date(exec.timestamp).getHours();
          const bucketIdx = Math.floor(hourOfDay / 2);
          buckets[bucketIdx]++;
        }
      }
    });

    // Only use real data if we have any entries in the buckets
    if (buckets.some((v) => v > 0)) {
      activityData = buckets;
      isRealData = true;
    }
  }

  const maxActivity = Math.max(...activityData, 1);

  // Real stats from execution data
  const totalExecutions = executionData?.total_executions || employee.execution_count || 0;
  const successCount = executionData?.success_count || employee.success_count || 0;
  const errorCount = executionData?.error_count || employee.error_count || 0;
  const successRate = totalExecutions > 0 ? Math.round((successCount / totalExecutions) * 100) : 0;

  return (
    <div className="space-y-6">
      {/* Real-Time Activity */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-aegisaxiom-text flex items-center gap-2">
            <Activity className="w-5 h-5 text-green-600 dark:text-green-400 animate-pulse" />
            {isRealData ? 'Activity — Last 24 Hours' : 'Live Activity - Last 12 Hours'}
            {isRealData && (
              <span className="px-2 py-0.5 bg-green-50 dark:bg-green-500/20 text-green-600 dark:text-green-400 text-xs rounded-full">
                Live data
              </span>
            )}
          </h3>
          <span className="text-sm text-aegisaxiom-text-secondary">
            {employee.status === 'active'
              ? '🟢 Active Now'
              : employee.status === 'paused'
                ? '🟡 Paused'
                : '🔵 Training'}
          </span>
        </div>

        {/* Activity Chart */}
        <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-6">
          <div className="h-64 flex items-end justify-between gap-2">
            {activityData.map((value, idx) => (
              <div key={idx} className="flex-1 flex flex-col items-center gap-2">
                <div className="w-full relative group">
                  <div
                    className={`w-full rounded-t transition-all duration-300 ${isRealData ? 'bg-gradient-to-t from-green-600 to-emerald-500 hover:from-green-500 hover:to-emerald-400' : 'bg-gradient-to-t from-violet-600 to-purple-500 hover:from-violet-500 hover:to-purple-400'}`}
                    style={{
                      height: `${(value / maxActivity) * 200}px`,
                      minHeight: value > 0 ? '4px' : '0px',
                    }}
                  ></div>
                  <div className="absolute -top-8 left-1/2 -translate-x-1/2 bg-aegisaxiom-surface px-2 py-1 rounded text-xs text-aegisaxiom-text opacity-0 group-hover:opacity-100 transition whitespace-nowrap">
                    {value} {isRealData ? 'executions' : 'actions'}
                  </div>
                </div>
                <span className="text-[10px] text-aegisaxiom-text-muted">{hours[idx]}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <TrendingUp className="w-4 h-4 text-green-600 dark:text-green-400" />
            <span className="text-xs text-aegisaxiom-text-secondary">
              {isRealData ? 'Total Executions' : 'Peak Hour'}
            </span>
          </div>
          <p className="text-xl font-bold text-aegisaxiom-text">
            {isRealData ? totalExecutions : hours[activityData.indexOf(Math.max(...activityData))]}
          </p>
          <p className="text-xs text-aegisaxiom-text-muted mt-1">
            {isRealData
              ? `${successCount} success, ${errorCount} errors`
              : `${Math.max(...activityData)} actions`}
          </p>
        </div>

        <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <BarChart3 className="w-4 h-4 text-blue-600 dark:text-blue-400" />
            <span className="text-xs text-aegisaxiom-text-secondary">
              {isRealData ? 'Success Rate' : 'Avg/Hour'}
            </span>
          </div>
          <p className="text-xl font-bold text-aegisaxiom-text">
            {isRealData
              ? `${successRate}%`
              : Math.round(activityData.reduce((a, b) => a + b, 0) / activityData.length)}
          </p>
          <p className="text-xs text-aegisaxiom-text-muted mt-1">
            {isRealData ? 'All time' : 'Last 12 hours'}
          </p>
        </div>

        <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <Clock className="w-4 h-4 text-amber-600 dark:text-amber-400" />
            <span className="text-xs text-aegisaxiom-text-secondary">
              {isRealData ? 'Last Active' : 'Uptime'}
            </span>
          </div>
          <p className="text-xl font-bold text-aegisaxiom-text">
            {isRealData
              ? employee.lastActivity || 'N/A'
              : employee.status === 'active'
                ? '24/7'
                : 'Paused'}
          </p>
          <p className="text-xs text-aegisaxiom-text-muted mt-1">
            {isRealData ? 'Most recent execution' : 'Continuous operation'}
          </p>
        </div>
      </div>

      {/* Historical Performance */}
      <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
        <h4 className="text-sm font-semibold text-aegisaxiom-text mb-3">
          {isRealData
            ? `Performance Overview (${totalExecutions} executions)`
            : 'Historical Performance (Last 7 Days)'}
        </h4>
        {isRealData ? (
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-aegisaxiom-elevated rounded-lg p-3">
              <p className="text-xs text-aegisaxiom-text-secondary mb-1">Success rate</p>
              <div className="flex items-center gap-3">
                <div className="flex-1 h-3 bg-aegisaxiom-elevated rounded-full overflow-hidden">
                  <div
                    className="h-full bg-green-500 rounded-full"
                    style={{ width: `${successRate}%` }}
                  ></div>
                </div>
                <span className="text-sm font-bold text-green-600 dark:text-green-400">
                  {successRate}%
                </span>
              </div>
            </div>
            <div className="bg-aegisaxiom-elevated rounded-lg p-3">
              <p className="text-xs text-aegisaxiom-text-secondary mb-1">Error rate</p>
              <div className="flex items-center gap-3">
                <div className="flex-1 h-3 bg-aegisaxiom-elevated rounded-full overflow-hidden">
                  <div
                    className="h-full bg-red-500 rounded-full"
                    style={{
                      width: `${totalExecutions > 0 ? Math.round((errorCount / totalExecutions) * 100) : 0}%`,
                    }}
                  ></div>
                </div>
                <span className="text-sm font-bold text-red-600 dark:text-red-400">
                  {totalExecutions > 0 ? Math.round((errorCount / totalExecutions) * 100) : 0}%
                </span>
              </div>
            </div>
          </div>
        ) : (
          <div className="flex items-end justify-around h-32 gap-2">
            {[92, 88, 95, 91, 89, 94, 96].map((val, idx) => (
              <div key={idx} className="flex-1 flex flex-col items-center gap-2">
                <div className="text-xs font-medium text-violet-600 dark:text-violet-400">
                  {val}%
                </div>
                <div
                  className="w-full bg-gradient-to-t from-violet-600 to-purple-600 rounded-t"
                  style={{ height: `${val}%` }}
                ></div>
                <span className="text-[10px] text-aegisaxiom-text-muted">D{idx + 1}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// Tasks Tab - Shows current and historical tasks
function TasksTab({ employee, executionData, loadingExecutions }) {
  // Convert real execution data to completed task items
  const realCompletedTasks = (executionData?.executions || []).map((exec) => {
    const isSuccess = ['success', 'ok', 'completed', 'true'].includes(
      (exec.status || '').toLowerCase(),
    );
    const label = exec.label || exec.workflow_id || 'Unknown';
    const subject = exec.subject || '';

    let title;
    if (subject && label) {
      title = `${isSuccess ? 'Processed' : 'Failed'}: "${subject}" → ${label}`;
    } else if (label) {
      title = `${isSuccess ? 'Completed' : 'Failed'}: ${label}`;
    } else {
      title = `Workflow execution ${exec.status || 'completed'}`;
    }

    return {
      title,
      completedAt: exec.timestamp ? formatTimeAgo(exec.timestamp) : 'Unknown',
      duration: exec.execution_time_ms ? `${exec.execution_time_ms}ms` : '-',
      status: isSuccess ? 'success' : 'error',
    };
  });

  const hasRealData = realCompletedTasks.length > 0;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-aegisaxiom-text flex items-center gap-2">
          Active Tasks ({employee.tasksActive})
          {hasRealData && (
            <span className="px-2 py-0.5 bg-green-50 dark:bg-green-500/20 text-green-600 dark:text-green-400 text-xs rounded-full">
              Live data
            </span>
          )}
        </h3>
        {employee.mode === 'manual' && (
          <button
            onClick={() => toast.info('Task assignment coming soon!')}
            className="flex items-center gap-2 px-3 py-1.5 bg-violet-600 hover:bg-violet-500 text-white text-sm rounded-lg transition"
          >
            <Plus className="w-4 h-4" />
            Assign Task
          </button>
        )}
      </div>

      {employee.currentTasks.length > 0 ? (
        <div className="space-y-3">
          {employee.currentTasks.map((task) => (
            <TaskCard key={task.id} task={task} employee={employee} />
          ))}
        </div>
      ) : (
        <div className="text-center py-12 bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg">
          <CheckCircle className="w-12 h-12 text-aegisaxiom-text-muted mx-auto mb-3" />
          <p className="text-aegisaxiom-text-muted">No active tasks</p>
          <p className="text-xs text-aegisaxiom-text-muted mt-1">
            {employee.mode === 'automatic'
              ? 'Waiting for triggers'
              : 'Assign a task to get started'}
          </p>
        </div>
      )}

      {loadingExecutions && (
        <div className="text-center py-4 text-aegisaxiom-text-muted text-sm">
          Loading execution history...
        </div>
      )}

      {/* Completed Tasks — real data or mock */}
      <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4 mt-6">
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-sm font-semibold text-aegisaxiom-text">
            Recent Completions
            {hasRealData && (
              <span className="text-xs text-aegisaxiom-text-muted font-normal ml-2">
                ({executionData?.total_executions || 0} total — {executionData?.success_count || 0}{' '}
                success, {executionData?.error_count || 0} errors)
              </span>
            )}
          </h4>
        </div>
        <div className="space-y-2">
          {hasRealData ? (
            realCompletedTasks.slice(0, 10).map((task, idx) => (
              <div
                key={idx}
                className="flex items-start gap-3 p-3 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg"
              >
                {task.status === 'success' ? (
                  <CheckCircle className="w-5 h-5 text-green-600 dark:text-green-400 flex-shrink-0 mt-0.5" />
                ) : (
                  <AlertCircle className="w-5 h-5 text-red-600 dark:text-red-400 flex-shrink-0 mt-0.5" />
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-aegisaxiom-text truncate">{task.title}</p>
                  <div className="flex items-center gap-3 mt-1">
                    <span className="text-xs text-aegisaxiom-text-muted">{task.completedAt}</span>
                    <span className="text-xs text-aegisaxiom-text-muted">•</span>
                    <span className="text-xs text-aegisaxiom-text-muted">Duration: {task.duration}</span>
                  </div>
                </div>
              </div>
            ))
          ) : (
            <>
              <CompletedTaskItem
                title="Generated SOW for DataFlow Inc - Professional Services"
                completedAt="5 min ago"
                duration="4.2 min"
              />
              <CompletedTaskItem
                title="Evaluated automation rules for TechCorp opportunity"
                completedAt="18 min ago"
                duration="12s"
              />
              <CompletedTaskItem
                title="Created weekly performance dashboard"
                completedAt="2 hours ago"
                duration="6.8 min"
              />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// Live Logs Tab - Real-time activity log
function LiveLogsTab({ employee, executionData, loadingExecutions }) {
  const [autoScroll, setAutoScroll] = useState(true);

  // Convert real execution log entries to display format
  const realLogs = (executionData?.executions || []).map((exec) => {
    const ts = exec.timestamp ? new Date(exec.timestamp) : new Date();
    const timeStr = ts.toLocaleTimeString('en-US', {
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
    const isSuccess = ['success', 'ok', 'completed', 'true'].includes(
      (exec.status || '').toLowerCase(),
    );

    return {
      time: timeStr,
      level: isSuccess ? 'success' : 'error',
      message: exec.label
        ? `${isSuccess ? 'Processed' : 'Failed'}: ${exec.subject || 'Task execution'}`
        : `Workflow execution ${exec.status || 'completed'}`,
      details: [
        exec.label ? `Label: ${exec.label}` : null,
        exec.workflow_id ? `Workflow: ${exec.workflow_id}` : null,
        exec.execution_time_ms ? `Duration: ${exec.execution_time_ms}ms` : null,
      ]
        .filter(Boolean)
        .join(' | '),
    };
  });

  // Mock logs for non-API employees
  const mockLogs = [
    {
      time: '14:32:15',
      level: 'info',
      message: 'Task execution started: Generate SOW for DataFlow Inc',
      details: 'opportunity_id: opp_001',
    },
    {
      time: '14:32:16',
      level: 'info',
      message: 'Fetching opportunity data from database',
      details: 'Query executed in 145ms',
    },
    {
      time: '14:32:17',
      level: 'success',
      message: 'Retrieved 15 opportunities matching criteria',
      details: 'Cache hit: 87%',
    },
    {
      time: '14:32:18',
      level: 'info',
      message: 'Analyzing resource requirements',
      details: 'LOB: Field Services',
    },
    {
      time: '14:32:19',
      level: 'info',
      message: 'Calculating margin projections',
      details: '3 rate cards evaluated',
    },
    {
      time: '14:32:20',
      level: 'warning',
      message: 'Margin below target threshold',
      details: 'Current: 14.2%, Target: 15%',
    },
    {
      time: '14:32:21',
      level: 'info',
      message: 'Generating SOW document',
      details: 'Template: standard_sow_v2',
    },
    {
      time: '14:32:24',
      level: 'success',
      message: 'SOW generated successfully',
      details: 'Duration: 4.2 minutes',
    },
    {
      time: '14:32:25',
      level: 'info',
      message: 'Triggering notification to sales team',
      details: '3 recipients',
    },
    {
      time: '14:32:25',
      level: 'success',
      message: 'Task completed',
      details: 'Total execution time: 10s',
    },
  ];

  const logs = realLogs.length > 0 ? realLogs : mockLogs;
  const isRealData = realLogs.length > 0;

  const levelColors = {
    info: {
      bg: 'bg-blue-50 dark:bg-blue-500/10',
      text: 'text-blue-700 dark:text-blue-300',
      dot: 'bg-blue-500',
    },
    success: {
      bg: 'bg-green-50 dark:bg-green-500/10',
      text: 'text-green-700 dark:text-green-300',
      dot: 'bg-green-500',
    },
    warning: {
      bg: 'bg-yellow-500/10',
      text: 'text-yellow-700 dark:text-yellow-300',
      dot: 'bg-yellow-500',
    },
    error: {
      bg: 'bg-red-50 dark:bg-red-500/10',
      text: 'text-red-700 dark:text-red-300',
      dot: 'bg-red-500',
    },
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-aegisaxiom-text flex items-center gap-2">
          <Activity className="w-5 h-5 text-green-600 dark:text-green-400 animate-pulse" />
          {isRealData ? 'Execution Log' : 'Live Activity Log'}
          {isRealData && (
            <span className="px-2 py-0.5 bg-green-50 dark:bg-green-500/20 text-green-600 dark:text-green-400 text-xs rounded-full ml-1">
              Live data
            </span>
          )}
        </h3>
        <div className="flex items-center gap-3">
          {isRealData && (
            <span className="text-xs text-aegisaxiom-text-muted">
              {executionData?.total_executions || 0} total executions
            </span>
          )}
          <label className="flex items-center gap-2 text-sm text-aegisaxiom-text-secondary">
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={(e) => setAutoScroll(e.target.checked)}
              className="rounded border-aegisaxiom-border-hover bg-aegisaxiom-elevated"
            />
            Auto-scroll
          </label>
          <button
            onClick={() => toast.info('Log export coming soon!')}
            className="px-3 py-1.5 bg-aegisaxiom-elevated hover:bg-aegisaxiom-surface text-aegisaxiom-text shadow-sm text-sm rounded-lg transition"
          >
            Export Logs
          </button>
        </div>
      </div>

      {loadingExecutions && (
        <div className="text-center py-8 text-aegisaxiom-text-muted">Loading execution data...</div>
      )}

      {/* Log Console */}
      <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4 h-96 overflow-y-auto font-mono text-xs">
        {logs.map((log, idx) => (
          <div
            key={idx}
            className={`flex gap-3 py-2 px-2 rounded mb-1 ${levelColors[log.level].bg} hover:bg-opacity-70 transition`}
          >
            <span className="text-aegisaxiom-text-muted">{log.time}</span>
            <span className={`font-semibold uppercase min-w-[60px] ${levelColors[log.level].text}`}>
              [{log.level}]
            </span>
            <div className="flex-1">
              <p className="text-aegisaxiom-text">{log.message}</p>
              {log.details && (
                <p className="text-aegisaxiom-text-muted mt-0.5 text-[10px]">{log.details}</p>
              )}
            </div>
          </div>
        ))}
        {employee.status !== 'active' && (
          <div className="text-center py-8 text-aegisaxiom-text-muted">
            <p>Agent is {employee.status} - no live logs</p>
          </div>
        )}
      </div>
    </div>
  );
}

// Configuration Tab
function AccessControlTab({ employee }) {
  const [roles, setRoles] = useState([]);
  const [users, setUsers] = useState([]);
  const [allowedRoles, setAllowedRoles] = useState([]);
  const [allowedUserIds, setAllowedUserIds] = useState([]);
  const [rolesWithPermission, setRolesWithPermission] = useState([]);
  const [requiredPermissions, setRequiredPermissions] = useState([]);
  const [userSearch, setUserSearch] = useState('');
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);

  const employeeId = employee.employee_id || employee.id;

  useEffect(() => {
    if (!employee._fromApi || !employeeId) {
      setLoading(false);
      return;
    }
    Promise.all([
      api.get(`${API}/api/roles`).then((r) => r.json()),
      api.get(`${API}/api/admin/users`).then((r) => r.json()),
      api.get(`${API}/api/ai-employees/${employeeId}/access`).then((r) => r.json()),
    ])
      .then(([rolesData, usersData, accessData]) => {
        setRoles(Array.isArray(rolesData) ? rolesData : []);
        setUsers(Array.isArray(usersData) ? usersData : []);
        setAllowedRoles(accessData.allowed_roles || []);
        setAllowedUserIds(accessData.allowed_user_ids || []);
        setRolesWithPermission(accessData.roles_with_permission || []);
        setRequiredPermissions(accessData.required_permissions || []);
      })
      .catch((err) => {
        console.error('Failed to load access control data:', err);
        toast.error('Failed to load access control settings');
      })
      .finally(() => setLoading(false));
  }, [employeeId, employee._fromApi]);

  const toggleRole = (roleName) => {
    setAllowedRoles((prev) =>
      prev.includes(roleName) ? prev.filter((r) => r !== roleName) : [...prev, roleName],
    );
  };

  const addUserOverride = (userId) => {
    if (!allowedUserIds.includes(userId)) {
      setAllowedUserIds((prev) => [...prev, userId]);
    }
    setUserSearch('');
  };

  const removeUserOverride = (userId) => {
    setAllowedUserIds((prev) => prev.filter((id) => id !== userId));
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const res = await api.put(`${API}/api/ai-employees/${employeeId}/access`, {
        allowed_roles: allowedRoles,
        allowed_user_ids: allowedUserIds,
      });
      if (res.ok) {
        toast.success('Access control settings saved');
      } else {
        const data = await res.json().catch(() => ({}));
        toast.error(data.detail || 'Failed to save access control settings');
      }
    } catch {
      toast.error('Failed to save access control settings');
    } finally {
      setSaving(false);
    }
  };

  if (!employee._fromApi) {
    return (
      <div className="space-y-6">
        <h3 className="text-lg font-semibold text-aegisaxiom-text">Access Control</h3>
        <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-6 text-center">
          <Shield className="w-8 h-8 text-aegisaxiom-text-muted mx-auto mb-2" />
          <p className="text-sm text-aegisaxiom-text-secondary">
            Access control is only available for API-backed AI employees.
          </p>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="w-6 h-6 text-violet-500 animate-spin" />
      </div>
    );
  }

  const filteredUsers = users.filter(
    (u) =>
      !allowedUserIds.includes(u.user_id) &&
      (userSearch === '' ||
        u.name?.toLowerCase().includes(userSearch.toLowerCase()) ||
        u.email?.toLowerCase().includes(userSearch.toLowerCase())),
  );

  const allowedUsersResolved = users.filter((u) => allowedUserIds.includes(u.user_id));

  return (
    <div className="space-y-6">
      <h3 className="text-lg font-semibold text-aegisaxiom-text">Access Control</h3>

      {/* Role-based access */}
      <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
        <h4 className="text-sm font-semibold text-aegisaxiom-text mb-1">Role Access</h4>
        <p className="text-xs text-aegisaxiom-text-muted mb-4">
          Roles with access to this AI employee. Leave all unchecked to allow all roles.
          {requiredPermissions.length > 0 && (
            <>
              {' '}Roles that already have{' '}
              <code className="text-[11px] text-violet-300">
                {requiredPermissions.join(', ')}
              </code>{' '}
              in their permissions are auto-granted access — manage that on the role itself.
            </>
          )}
        </p>
        <div className="space-y-2">
          {roles.map((role) => {
            const explicit = allowedRoles.includes(role.name);
            const byPermission = rolesWithPermission.includes(role.name);
            const checked = explicit || byPermission;
            return (
              <label
                key={role.name}
                className={
                  'flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-aegisaxiom-elevated transition-colors ' +
                  (byPermission && !explicit ? 'cursor-not-allowed opacity-90' : 'cursor-pointer')
                }
                title={
                  byPermission && !explicit
                    ? `Granted via ${requiredPermissions.join(' / ')} permission on role "${role.name}"`
                    : undefined
                }
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={byPermission && !explicit}
                  onChange={() => toggleRole(role.name)}
                  className="rounded border-aegisaxiom-border-hover text-violet-500 focus:ring-violet-500 focus:ring-offset-0 bg-aegisaxiom-elevated disabled:opacity-70"
                />
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-aegisaxiom-text">{role.display_name || role.name}</p>
                </div>
                {byPermission && !explicit && (
                  <span className="text-[10px] uppercase tracking-wider px-2 py-0.5 rounded bg-violet-500/10 text-violet-300 border border-violet-500/30">
                    via permission
                  </span>
                )}
              </label>
            );
          })}
          {roles.length === 0 && (
            <p className="text-xs text-aegisaxiom-text-muted py-2">No roles found</p>
          )}
        </div>
      </div>

      {/* User overrides */}
      <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
        <h4 className="text-sm font-semibold text-aegisaxiom-text mb-1">User Overrides</h4>
        <p className="text-xs text-aegisaxiom-text-muted mb-4">
          Individual users granted direct access regardless of their role.
        </p>

        {/* Current overrides list */}
        {allowedUsersResolved.length > 0 ? (
          <div className="space-y-1 mb-4">
            {allowedUsersResolved.map((u) => (
              <div
                key={u.user_id}
                className="flex items-center justify-between px-3 py-2 bg-aegisaxiom-elevated rounded-lg"
              >
                <div className="min-w-0">
                  <p className="text-xs text-aegisaxiom-text truncate">{u.name || 'Unnamed'}</p>
                  <p className="text-[11px] text-aegisaxiom-text-muted truncate">{u.email}</p>
                </div>
                <button
                  onClick={() => removeUserOverride(u.user_id)}
                  aria-label={`Remove ${u.name}`}
                  className="ml-3 text-aegisaxiom-text-muted hover:text-red-500 transition-colors shrink-0"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs text-aegisaxiom-text-muted mb-4 italic">No user overrides set</p>
        )}

        {/* Search to add users */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-aegisaxiom-text-muted" />
          <input
            type="text"
            placeholder="Search users to add..."
            value={userSearch}
            onChange={(e) => setUserSearch(e.target.value)}
            className="input-field text-xs w-full pl-8"
          />
        </div>
        {userSearch && filteredUsers.length > 0 && (
          <div className="mt-1 border border-aegisaxiom-border rounded-lg overflow-hidden max-h-40 overflow-y-auto">
            {filteredUsers.slice(0, 8).map((u) => (
              <button
                key={u.user_id}
                type="button"
                onClick={() => addUserOverride(u.user_id)}
                className="w-full text-left px-3 py-2 text-xs hover:bg-aegisaxiom-elevated transition-colors border-b border-aegisaxiom-border last:border-0"
              >
                <span className="text-aegisaxiom-text">{u.name || 'Unnamed'}</span>
                <span className="text-aegisaxiom-text-muted ml-2">{u.email}</span>
              </button>
            ))}
          </div>
        )}
        {userSearch && filteredUsers.length === 0 && (
          <p className="mt-2 text-xs text-aegisaxiom-text-muted">No matching users found</p>
        )}
      </div>

      {/* Save button */}
      <div className="flex justify-end">
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex items-center gap-2 px-4 py-2 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white text-sm rounded-lg transition"
        >
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Shield className="w-4 h-4" />}
          {saving ? 'Saving...' : 'Save Access Settings'}
        </button>
      </div>
    </div>
  );
}

function ConfigTab({ employee }) {
  return (
    <div className="space-y-6">
      <h3 className="text-lg font-semibold text-aegisaxiom-text">Configuration & Settings</h3>

      {/* Operation Mode */}
      <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
        <h4 className="text-sm font-semibold text-aegisaxiom-text mb-4">Operation Mode</h4>
        <div className="space-y-3">
          <label className="flex items-center justify-between cursor-pointer">
            <span className="text-sm text-aegisaxiom-text-secondary">Automatic Execution</span>
            <div
              className={`relative w-12 h-6 rounded-full transition ${employee.mode === 'automatic' ? 'bg-violet-600' : 'bg-aegisaxiom-elevated'}`}
            >
              <span
                className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${employee.mode === 'automatic' ? 'translate-x-6' : 'translate-x-0'}`}
              ></span>
            </div>
          </label>
          <p className="text-xs text-aegisaxiom-text-muted">
            {employee.mode === 'automatic'
              ? 'Agent runs automatically when triggers are detected'
              : 'Agent requires manual initiation for each task'}
          </p>
        </div>
      </div>

      {/* Automation Settings */}
      <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
        <h4 className="text-sm font-semibold text-aegisaxiom-text mb-4">Automation Settings</h4>
        <div className="space-y-3">
          <SettingToggle label="Send completion notifications" enabled={true} />
          <SettingToggle label="Generate activity reports" enabled={true} />
          <SettingToggle label="Auto-retry on failures" enabled={false} />
          <SettingToggle label="Priority task override" enabled={true} />
        </div>
      </div>

      {/* Working Schedule */}
      <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
        <h4 className="text-sm font-semibold text-aegisaxiom-text mb-4">Working Schedule</h4>
        <p className="text-sm text-aegisaxiom-text-secondary mb-3">Active: 24/7 (AI never sleeps 🌙)</p>
        <button
          onClick={() => toast.info('Schedule configuration coming soon!')}
          className="text-sm text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:text-violet-300"
        >
          Configure custom schedule →
        </button>
      </div>

      {/* Specialties */}
      <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
        <h4 className="text-sm font-semibold text-aegisaxiom-text mb-4">Capabilities & Specialties</h4>
        <div className="flex flex-wrap gap-2">
          {employee.specialties.map((specialty, idx) => (
            <span
              key={idx}
              className="px-3 py-1.5 bg-violet-50 dark:bg-violet-500/20 border border-violet-500/30 rounded-lg text-sm text-violet-700 dark:text-violet-300"
            >
              {specialty}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

// Helper Components
function TaskCard({ task, employee }) {
  const priorityConfig = PRIORITY_CONFIG[task.priority];

  return (
    <div className="bg-aegisaxiom-base border border-aegisaxiom-border rounded-lg p-4">
      <div className="flex items-start justify-between mb-3">
        <div className="flex-1">
          <h4 className="text-sm font-medium text-aegisaxiom-text mb-1">{task.title}</h4>
          <div className="flex items-center gap-2">
            <span
              className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${priorityConfig.bg} ${priorityConfig.color}`}
            >
              {priorityConfig.label} Priority
            </span>
            <span className="text-xs text-aegisaxiom-text-muted">Started {task.startedAt}</span>
          </div>
        </div>
        <span className="text-lg font-bold text-violet-600 dark:text-violet-400">
          {task.progress}%
        </span>
      </div>
      <div className="h-2 bg-aegisaxiom-elevated rounded-full overflow-hidden">
        <div
          className="h-full bg-gradient-to-r from-violet-600 to-purple-600 transition-all duration-300"
          style={{ width: `${task.progress}%` }}
        ></div>
      </div>
    </div>
  );
}

function CompletedTaskItem({ title, completedAt, duration }) {
  return (
    <div className="flex items-start gap-3 p-3 bg-aegisaxiom-elevated border border-aegisaxiom-border rounded-lg">
      <CheckCircle className="w-5 h-5 text-green-600 dark:text-green-400 flex-shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <p className="text-sm text-aegisaxiom-text truncate">{title}</p>
        <div className="flex items-center gap-3 mt-1">
          <span className="text-xs text-aegisaxiom-text-muted">{completedAt}</span>
          <span className="text-xs text-aegisaxiom-text-muted">•</span>
          <span className="text-xs text-aegisaxiom-text-muted">Duration: {duration}</span>
        </div>
      </div>
    </div>
  );
}

function SettingToggle({ label, enabled }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-aegisaxiom-text-secondary">{label}</span>
      <button
        onClick={() => toast.info('Setting toggle coming soon!')}
        className={`relative w-11 h-6 rounded-full transition ${enabled ? 'bg-violet-600' : 'bg-aegisaxiom-elevated'}`}
      >
        <span
          className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${enabled ? 'translate-x-5' : 'translate-x-0'}`}
        ></span>
      </button>
    </div>
  );
}

import React from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { AuthProvider } from './contexts/AuthContext';
import { ChatProvider } from './contexts/ChatContext';
import { ThemeProvider, useTheme } from './contexts/ThemeContext';
import { useAuth } from './contexts/AuthContext';
import { Toaster } from 'sonner';
import ErrorBoundary from './components/ErrorBoundary';
import getRoleHome from './lib/roleHome';

import HomePage from './components/HomePage';
import AuthPage from './components/AuthPage';
import AuthCallback from './components/AuthCallback';
import AcceptInvite from './components/AcceptInvite';
import ResetPassword from './components/ResetPassword';
import ProtectedRoute from './components/ProtectedRoute';
import PermissionRoute from './components/PermissionRoute';
import Layout from './components/Layout';
import Dashboard from './components/Dashboard';
import Accounts from './components/Accounts';
import Commissions from './components/Commissions';
import Contracts from './components/Contracts';
import Invoices from './components/Invoices';
import FieldServices from './components/FieldServices';
import UserManagement from './components/admin/UserManagement';
import ContractApprovalAccess from './components/admin/ContractApprovalAccess';
import Integrations from './components/admin/Integrations';
import WebhookLog from './components/admin/WebhookLog';
import RolesPermissions from './components/admin/RolesPermissions';
import ContractorBusinesses from './components/admin/ContractorBusinesses';
import ContractTemplates from './components/admin/ContractTemplates';
import SOWMilestoneTemplates from './components/admin/SOWMilestoneTemplates';
import DefaultResourceTypes from './components/DefaultResourceTypes';
import AuditLog from './components/admin/AuditLog';
import PolicyAdmin from './components/admin/PolicyAdmin';
import WorkflowRulesAdmin from './components/admin/WorkflowRulesAdmin';
import MyCertifications from './pages/MyCertifications';
import MyDiscoverySessions from './pages/MyDiscoverySessions';
import AIChatWidget from './components/AIChatWidget';
import AgentDashboard from './components/AgentDashboard';
import ChatPage from './components/ChatPage';
import FinancePortal from './components/FinancePortal';
import SalesRepDashboard from './components/SalesRepDashboard';
import TimeManagement from './components/TimeManagement';
import ProjectsPortal from './components/ProjectsPortal';
import ContractorPortal from './components/ContractorPortal';
import MatchPortal from './components/MatchPortal';
import CommissionSettings from './components/CommissionSettings';
import ProductsServices from './components/ProductsServices';
import DealManagement from './components/DealManagement';
import OpportunityManagement from './components/OpportunityManagement';
import Contacts from './components/Contacts';
import SalesOpsWorkspace from './components/SalesOpsWorkspace';
import ContractorRatingSurveyForm from './components/sales-ops/ContractorRatingSurveyForm';
import AIEmployees from './components/AIEmployees';
import OrganizationBranding from './components/admin/OrganizationBranding';
import AppIngestionAdmin from './components/admin/AppIngestionAdmin';
import TenantManagement from './components/admin/TenantManagement';
import TenantDetail from './components/admin/TenantManagement/TenantDetail';
import PublicFieldJobPage from './components/field-services/PublicFieldJobPage';
import OperationsContractsPage from './pages/OperationsContractsPage';
import InvoiceBatchesPage from './pages/InvoiceBatchesPage';
import ContractApprovalQueuePage from './pages/ContractApprovalQueuePage';
import PrivacyPolicy from './components/legal/PrivacyPolicy';
import TermsOfService from './components/legal/TermsOfService';
import AIDispatcherPortal from './AIDispatcherPortal';

// Smart root: logged-in users go to role-appropriate home, guests see the home page
function SmartHome() {
  const { user, loading } = useAuth();
  if (loading)
    return (
      <div
        className="min-h-screen flex items-center justify-center"
        style={{ background: 'var(--aegisaxiom-bg-base)' }}
      >
        <div className="w-10 h-10 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  if (!user) return <HomePage />;
  return <Navigate to={getRoleHome(user)} replace />;
}

function AppRouter() {
  const location = useLocation();
  return (
    <>
      <Routes>
        {/* Public routes */}
        <Route path="/" element={<SmartHome />} />
        <Route path="/login" element={<AuthPage />} />
        <Route path="/auth/google/callback" element={<AuthCallback />} />
        <Route path="/accept-invite" element={<AcceptInvite />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        <Route path="/privacy" element={<PrivacyPolicy />} />
        <Route path="/terms" element={<TermsOfService />} />
        <Route path="/public/survey/:token" element={<ContractorRatingSurveyForm />} />
        <Route path="/public/field-job/:jobId" element={<PublicFieldJobPage />} />
        {/* Protected routes */}
        <Route element={<ProtectedRoute />}>
          <Route element={<Layout />}>
            <Route path="dashboard" element={<Dashboard />} />
            <Route
              path="deals"
              element={
                <PermissionRoute permission={['deals.view', 'deals.view.own']}>
                  <DealManagement />
                </PermissionRoute>
              }
            />
            <Route
              path="opportunities"
              element={
                <PermissionRoute
                  permission={['opportunities.view', 'opportunities.view.own']}
                  roles={['sales_rep', 'agent', 'sales_ops']}
                >
                  <OpportunityManagement />
                </PermissionRoute>
              }
            />
            <Route
              path="sales-ops-workspace"
              element={
                <PermissionRoute roles={['admin', 'sales_ops', 'director']}>
                  <SalesOpsWorkspace />
                </PermissionRoute>
              }
            />
            <Route
              path="ai-employees"
              element={
                <PermissionRoute permission={['ai_employees.view']}>
                  <AIEmployees />
                </PermissionRoute>
              }
            />
            <Route
              path="contacts"
              element={
                <PermissionRoute permission={['contacts.view', 'contacts.view.own']}>
                  <Contacts />
                </PermissionRoute>
              }
            />
            <Route
              path="accounts"
              element={
                <PermissionRoute permission={['accounts.view', 'accounts.view.own']}>
                  <Accounts />
                </PermissionRoute>
              }
            />
            <Route
              path="commissions"
              element={
                <PermissionRoute permission={['commissions.view', 'commissions.view.own']}>
                  <Commissions />
                </PermissionRoute>
              }
            />
            <Route
              path="contracts"
              element={
                <PermissionRoute permission={['contracts.view', 'contracts.view.own']}>
                  <Contracts />
                </PermissionRoute>
              }
            />
            <Route
              path="invoices"
              element={
                <PermissionRoute permission={['invoices.view', 'invoices.view.own']}>
                  <Invoices />
                </PermissionRoute>
              }
            />
            <Route
              path="field-jobs"
              element={
                <PermissionRoute permission={['field_jobs.view', 'field_jobs.view.own']}>
                  <FieldServices />
                </PermissionRoute>
              }
            />
            <Route
              path="my-dashboard"
              element={
                <PermissionRoute permission={['dashboard.agent']}>
                  <AgentDashboard />
                </PermissionRoute>
              }
            />
            <Route
              path="admin/users"
              element={
                <PermissionRoute permission="users.manage">
                  <UserManagement />
                </PermissionRoute>
              }
            />
            <Route
              path="admin/contract-approvers"
              element={
                <PermissionRoute permission={['users.edit', 'users.view']}>
                  <ContractApprovalAccess />
                </PermissionRoute>
              }
            />
            <Route
              path="admin/tenants"
              element={
                <PermissionRoute permission="tenants.view">
                  <TenantManagement />
                </PermissionRoute>
              }
            />
            <Route
              path="admin/tenants/:tenantId"
              element={
                <PermissionRoute permission="tenants.view">
                  <TenantDetail />
                </PermissionRoute>
              }
            />
            <Route
              path="admin/contractor-businesses"
              element={
                <PermissionRoute permission="admin.contractors.view">
                  <ContractorBusinesses />
                </PermissionRoute>
              }
            />
            <Route
              path="admin/contract-templates"
              element={
                <PermissionRoute permission="contract_templates.view">
                  <ContractTemplates />
                </PermissionRoute>
              }
            />
            <Route
              path="admin/sow-milestone-templates"
              element={
                <PermissionRoute permission="sales_ops.solutions_design.view">
                  <SOWMilestoneTemplates />
                </PermissionRoute>
              }
            />
            <Route
              path="admin/roles"
              element={
                <PermissionRoute permission="roles.manage">
                  <RolesPermissions />
                </PermissionRoute>
              }
            />
            <Route
              path="admin/resource-types"
              element={
                <PermissionRoute permission="admin.resource_types.view">
                  <DefaultResourceTypes />
                </PermissionRoute>
              }
            />
            <Route
              path="admin/commission-settings"
              element={
                <PermissionRoute permission="commissions.template.view">
                  <CommissionSettings />
                </PermissionRoute>
              }
            />
            <Route
              path="admin/products"
              element={
                <PermissionRoute permission="products.view">
                  <ProductsServices />
                </PermissionRoute>
              }
            />
            <Route
              path="admin/integrations"
              element={
                <PermissionRoute permission="integrations.manage">
                  <Integrations />
                </PermissionRoute>
              }
            />
            <Route
              path="admin/webhooks"
              element={
                <PermissionRoute permission="webhooks.view">
                  <WebhookLog />
                </PermissionRoute>
              }
            />
            <Route
              path="admin/policies"
              element={
                <PermissionRoute permission="policies.view">
                  <PolicyAdmin />
                </PermissionRoute>
              }
            />
            <Route
              path="admin/workflow-rules"
              element={
                <PermissionRoute permission="workflow_rules.view">
                  <WorkflowRulesAdmin />
                </PermissionRoute>
              }
            />
            <Route
              path="admin/audit"
              element={
                <PermissionRoute permission="audit.view">
                  <AuditLog />
                </PermissionRoute>
              }
            />
            <Route
              path="admin/branding"
              element={
                <PermissionRoute permission="branding.manage">
                  <OrganizationBranding />
                </PermissionRoute>
              }
            />
            <Route
              path="admin/app-ingestion"
              element={
                <PermissionRoute permission="admin.manage">
                  <AppIngestionAdmin />
                </PermissionRoute>
              }
            />
            <Route
              path="chat"
              element={
                <PermissionRoute permission="chat.access">
                  <ChatPage />
                </PermissionRoute>
              }
            />
            <Route
              path="certifications"
              element={
                <PermissionRoute permission={['certifications.view']}>
                  <MyCertifications />
                </PermissionRoute>
              }
            />
            <Route
              path="discovery-sessions"
              element={
                <PermissionRoute permission="vega.use">
                  <MyDiscoverySessions />
                </PermissionRoute>
              }
            />
            <Route
              path="finance/*"
              element={
                <PermissionRoute permission={['finance.portal', 'finance.access']}>
                  <FinancePortal />
                </PermissionRoute>
              }
            />
            <Route
              path="operations/contracts"
              element={
                <PermissionRoute permission={['operations.contracts.view']}>
                  <OperationsContractsPage />
                </PermissionRoute>
              }
            />
            <Route
              path="finance/invoice-batches"
              element={
                <PermissionRoute
                  permission={[
                    'invoice_batches.view',
                    'invoice_batches.approve',
                    'invoice_batches.build',
                    'finance.portal',
                    'contracts.view.all',
                  ]}
                >
                  <InvoiceBatchesPage />
                </PermissionRoute>
              }
            />
            {/*
              Approval queue. Per-contract approver overrides
              (Wave 6) admit override-only users — the backend
              filters the queue by override membership and 403s
              callers with neither global approve nor any active
              overrides. The route gate admits broader
              contract-view perms so override-only users can mount
              the page; the backend is the canonical security
              boundary.
            */}
            <Route
              path="contracts/approval-queue"
              element={
                <PermissionRoute
                  permission={[
                    'contracts.approve',
                    'contracts.view.all',
                    'contracts.view',
                    'contracts.view.own',
                  ]}
                >
                  <ContractApprovalQueuePage />
                </PermissionRoute>
              }
            />
            <Route
              path="contractor-payments"
              element={<Navigate to="/time/my-earnings" replace />}
            />
            <Route
              path="sales-rep-dashboard"
              element={
                <PermissionRoute permission={['dashboard.sales_rep']}>
                  <SalesRepDashboard />
                </PermissionRoute>
              }
            />
            <Route
              path="contractor-dashboard"
              element={
                <PermissionRoute permission={['dashboard.contractor']}>
                  <ContractorPortal />
                </PermissionRoute>
              }
            />
            <Route
              path="time-management"
              element={
                <PermissionRoute permission={['time.view', 'time.view.own', 'time.entry']}>
                  <TimeManagement />
                </PermissionRoute>
              }
            />
            <Route
              path="time"
              element={
                <PermissionRoute permission={['time.view', 'time.view.own', 'time.entry']}>
                  <TimeManagement />
                </PermissionRoute>
              }
            />
            <Route
              path="projects/*"
              element={
                <PermissionRoute permission={['projects.access']}>
                  <ProjectsPortal />
                </PermissionRoute>
              }
            />
            <Route
              path="ai-dispatcher/*"
              element={
                <PermissionRoute permission={['ai_dispatcher.teams.view']}>
                  <AIDispatcherPortal />
                </PermissionRoute>
              }
            />
            <Route
              path="match/*"
              element={
                <PermissionRoute permission={['match.access']}>
                  <MatchPortal />
                </PermissionRoute>
              }
            />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <AIChatWidget />
    </>
  );
}

// Theme-aware Toaster — adapts to current theme
function ThemeAwareToaster() {
  const { theme } = useTheme();
  return <Toaster theme={theme} richColors position="top-right" />;
}

export default function App() {
  return (
    // SECURITY FIX L-8: Wrap entire app with ErrorBoundary
    <ErrorBoundary>
      <BrowserRouter>
        <ThemeProvider>
          <AuthProvider>
            <ChatProvider>
              <AppRouter />
              <ThemeAwareToaster />
            </ChatProvider>
          </AuthProvider>
        </ThemeProvider>
      </BrowserRouter>
    </ErrorBoundary>
  );
}

import React, { useState, useEffect } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { JobsProvider } from '../context/JobsContext';
import { EventProvider } from '../context/EventContext';
import DashboardSidebar from '../components/DashboardSidebar';
import CommandPalette from '../components/CommandPalette';
import OnboardingTour from '../components/OnboardingTour';

// ── Core pages (open-source ContextSynapse) ──
import OverviewPage from './dashboard/OverviewPage';
import GraphsPage from './dashboard/GraphsPage';
import IntegrationsPage from './dashboard/IntegrationsPage';
import UsagePage from './dashboard/UsagePage';
import AgentsPage from './dashboard/AgentsPage';
import SessionsPage from './dashboard/SessionsPage';
import MonitoringPage from './dashboard/MonitoringPage';
import AuditPage from './dashboard/AuditPage';
import DocsPage from './dashboard/DocsPage';
import PlaygroundPage from './dashboard/PlaygroundPage';
import ComparePage from './dashboard/ComparePage';
import AgentAnalyticsPage from './dashboard/AgentAnalyticsPage';
import TeamPage from './dashboard/TeamPage';
import BillingPage from './dashboard/BillingPage';
import ContextPage from './dashboard/ContextPage';
import ContextsPage from './dashboard/ContextsPage';
import SettingsPage from './dashboard/SettingsPage';
import PipelinesPage from './dashboard/PipelinesPage';
import ExplorerPage from './dashboard/ExplorerPage';
import ChunkingConfigPage from './dashboard/ChunkingConfigPage';
import JobsPage from './dashboard/JobsPage';
import IntelligencePage from './dashboard/IntelligencePage';
import ContextLakePage from './dashboard/ContextLakePage';
import SchemasPage from './dashboard/SchemasPage';
import ProjectsPage from './dashboard/ProjectsPage';
import CognitionPage from './dashboard/CognitionPage';
import OnboardingPage from './dashboard/OnboardingPage';
import ApiDocsPage from './dashboard/ApiDocsPage';
import MobileAgentPage from './dashboard/MobileAgentPage';
import VerticalBuilderPage from './dashboard/VerticalBuilderPage';
import UserManagementPage from './dashboard/UserManagementPage';

// PMS vertical pages removed — use /pms/* routes instead

export default function Dashboard() {
  const { refreshProfile } = useAuth();
  const [showTour, setShowTour] = useState(false);

  useEffect(() => {
    refreshProfile();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!localStorage.getItem('contextsynapse_tour_complete')) {
      setShowTour(true);
    }
  }, []);

  return (
    <EventProvider>
    <JobsProvider>
      <div className="flex" style={{ minHeight: 'calc(100vh - 48px)' }}>
        <DashboardSidebar />
        <CommandPalette />
        <OnboardingTour run={showTour} />
        <main className="flex-1 overflow-auto p-6" style={{ background: 'var(--neo-bg)' }} role="main">
          <Routes>
            {/* Default landing: platform overview */}
            <Route index element={<Navigate to="/dashboard/overview" replace />} />
            <Route path="overview" element={<OverviewPage />} />
            {/* PMS redirect — send to /pms */}
            <Route path="canvas" element={<Navigate to="/pms" replace />} />

            {/* Context */}
            <Route path="contexts" element={<ContextsPage />} />
            <Route path="context" element={<Navigate to="/dashboard/contexts" replace />} />
            <Route path="graphs" element={<GraphsPage />} />
            <Route path="sessions" element={<SessionsPage />} />
            <Route path="context-lake" element={<ContextLakePage />} />
            <Route path="explorer" element={<ExplorerPage />} />

            {/* Agents */}
            <Route path="agents" element={<AgentsPage />} />
            <Route path="agent-analytics" element={<AgentAnalyticsPage />} />
            <Route path="mobile-agent" element={<MobileAgentPage />} />

            {/* Pipelines & Ingestion */}
            <Route path="schemas" element={<SchemasPage />} />
            <Route path="pipelines" element={<PipelinesPage />} />
            <Route path="jobs" element={<JobsPage />} />
            <Route path="chunking" element={<ChunkingConfigPage />} />
            <Route path="ingest" element={<Navigate to="/dashboard/contexts" replace />} />

            {/* Intelligence */}
            <Route path="intelligence" element={<IntelligencePage />} />
            <Route path="cognition" element={<CognitionPage />} />
            <Route path="monitoring" element={<MonitoringPage />} />

            {/* SDLC */}
            <Route path="projects" element={<ProjectsPage />} />

            {/* Develop */}
            <Route path="playground" element={<PlaygroundPage />} />
            <Route path="docs" element={<DocsPage />} />
            <Route path="api-docs" element={<ApiDocsPage />} />
            <Route path="vertical-builder" element={<VerticalBuilderPage />} />
            <Route path="compare" element={<ComparePage />} />

            {/* Settings & Admin */}
            <Route path="integrations" element={<IntegrationsPage />} />
            <Route path="team" element={<TeamPage />} />
            <Route path="billing" element={<BillingPage />} />
            <Route path="usage" element={<UsagePage />} />
            <Route path="users" element={<UserManagementPage />} />
            <Route path="audit" element={<AuditPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="onboarding" element={<OnboardingPage />} />

            {/* ── PMS Vertical — redirect to /pms/* ── */}
            <Route path="portfolio" element={<Navigate to="/pms/portfolio" replace />} />
            <Route path="cockpit" element={<Navigate to="/pms/cockpit" replace />} />
            <Route path="hud" element={<Navigate to="/pms/hud" replace />} />
            <Route path="hud/:stockId" element={<Navigate to="/pms/hud" replace />} />
            <Route path="formation" element={<Navigate to="/pms/formation" replace />} />
            <Route path="market" element={<Navigate to="/pms/market" replace />} />
            <Route path="market-hero" element={<Navigate to="/pms/market-hero" replace />} />
            <Route path="screener" element={<Navigate to="/pms/screener" replace />} />
            <Route path="research" element={<Navigate to="/pms/research" replace />} />
            <Route path="stock/:stockId" element={<Navigate to="/pms" replace />} />
            <Route path="command" element={<Navigate to="/pms/command" replace />} />
            <Route path="simulator" element={<Navigate to="/pms/simulator" replace />} />
            <Route path="automations" element={<Navigate to="/pms/automations" replace />} />
            <Route path="compliance" element={<Navigate to="/pms/compliance" replace />} />
            <Route path="rules" element={<Navigate to="/pms/rules" replace />} />
            <Route path="backtest" element={<Navigate to="/pms/backtest" replace />} />
            <Route path="threats" element={<Navigate to="/pms/threats" replace />} />
            <Route path="sensors" element={<Navigate to="/pms/sensors" replace />} />
            <Route path="sensor-settings" element={<Navigate to="/pms/sensor-settings" replace />} />
            <Route path="flight-recorder" element={<Navigate to="/pms/flight-recorder" replace />} />
            <Route path="debrief" element={<Navigate to="/pms/debrief" replace />} />
            <Route path="roster" element={<Navigate to="/pms/roster" replace />} />
            <Route path="client" element={<Navigate to="/pms/client" replace />} />

            {/* Legacy redirects */}
            <Route path="query" element={<Navigate to="/dashboard/graphs" replace />} />
            <Route path="search" element={<Navigate to="/dashboard/graphs" replace />} />
            <Route path="rag" element={<Navigate to="/dashboard/graphs" replace />} />
            <Route path="api-keys" element={<Navigate to="/dashboard/agents" replace />} />
          </Routes>
        </main>
      </div>
    </JobsProvider>
    </EventProvider>
  );
}

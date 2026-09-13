import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import MFSidebar from '../components/MFSidebar';
import MFDashboardPage from './dashboard/MFDashboardPage';
import MFLabPage from './dashboard/MFLabPage';
import FundManagerPage from './dashboard/FundManagerPage';

export default function MFApp() {
  return (
    <div className="flex" style={{ minHeight: 'calc(100vh - 48px)' }}>
      <MFSidebar />
      <main className="flex-1 overflow-auto p-6" style={{ background: 'var(--neo-bg)' }} role="main">
        <Routes>
          {/* Default → schemes tab */}
          <Route index element={<Navigate to="/mf/schemes" replace />} />

          {/* All tabs handled by MFDashboardPage */}
          <Route path="schemes" element={<MFDashboardPage initialTab="schemes" />} />
          <Route path="schemes/new" element={<MFDashboardPage initialTab="schemes" createOpen />} />
          <Route path="schemes/:id" element={<Navigate to="/mf/schemes" replace />} />
          <Route path="dealing" element={<MFDashboardPage initialTab="dealing" />} />
          <Route path="nav" element={<MFDashboardPage initialTab="nav" />} />
          <Route path="units" element={<MFDashboardPage initialTab="units" />} />
          <Route path="compliance" element={<MFDashboardPage initialTab="compliance" />} />
          <Route path="expenses" element={<MFDashboardPage initialTab="expenses" />} />
          <Route path="distributors" element={<MFDashboardPage initialTab="distributors" />} />
          <Route path="lab" element={<MFLabPage />} />
          <Route path="fund-managers" element={<FundManagerPage />} />

          {/* Fallback */}
          <Route path="*" element={<Navigate to="/mf/schemes" replace />} />
        </Routes>
      </main>
    </div>
  );
}

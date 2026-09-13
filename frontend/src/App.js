import React, { useState, useCallback } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Toaster } from 'react-hot-toast';

import { AuthProvider } from './context/AuthContext';
import ProtectedRoute from './components/ProtectedRoute';
import Header from './components/Header';
import { ErrorBoundary } from './components/ErrorBoundary';

// Pages
import LandingPage from './pages/LandingPage';
import UserLoginPage from './pages/UserLoginPage';
import SignupPage from './pages/SignupPage';
import Dashboard from './pages/Dashboard';
import PMSApp from './pages/PMSApp';
import MFApp from './pages/MFApp';
import PricingPage from './pages/PricingPage';
import OnboardingPage from './pages/OnboardingPage';
import LoginPage from './pages/LoginPage';        // admin-key login (legacy)
import AdminDashboard from './pages/AdminDashboard';
import VerticalLoginPage from './pages/VerticalLoginPage';

import './App.css';
import './admin.css';

const ADMIN_TOKEN_KEY = 'contextsynapse_admin_token';

function App() {
  // Admin key auth (kept separate from user auth)
  const [adminToken, setAdminToken] = useState(() => sessionStorage.getItem(ADMIN_TOKEN_KEY));

  const handleAdminLogin = useCallback((jwt) => {
    sessionStorage.setItem(ADMIN_TOKEN_KEY, jwt);
    setAdminToken(jwt);
  }, []);

  const handleAdminLogout = useCallback(() => {
    sessionStorage.removeItem(ADMIN_TOKEN_KEY);
    setAdminToken(null);
  }, []);

  return (
    <AuthProvider>
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <div className="app-root">
          <Header onAdminLogout={adminToken ? handleAdminLogout : null} />

          <Routes>
            {/* Public */}
            <Route path="/" element={<LandingPage />} />
            <Route path="/login" element={<UserLoginPage />} />
            <Route path="/signup" element={<SignupPage />} />
            <Route path="/pricing" element={<PricingPage />} />

            {/* Vertical-specific login (public, no auth required) */}
            <Route path="/pms/login" element={<VerticalLoginPage vertical="pms" />} />
            <Route path="/pms/signup" element={<VerticalLoginPage vertical="pms" />} />
            <Route path="/mf/login" element={<VerticalLoginPage vertical="mf" />} />
            <Route path="/mf/signup" element={<VerticalLoginPage vertical="mf" />} />

            {/* Onboarding (protected) */}
            <Route
              path="/onboarding"
              element={
                <ProtectedRoute>
                  <OnboardingPage />
                </ProtectedRoute>
              }
            />

            {/* User dashboard — platform admin (protected) */}
            <Route
              path="/dashboard/*"
              element={
                <ProtectedRoute>
                  <ErrorBoundary>
                    <Dashboard />
                  </ErrorBoundary>
                </ProtectedRoute>
              }
            />

            {/* PMS vertical — fund manager (protected) */}
            <Route
              path="/pms/*"
              element={
                <ProtectedRoute>
                  <ErrorBoundary>
                    <PMSApp />
                  </ErrorBoundary>
                </ProtectedRoute>
              }
            />

            {/* MF vertical — mutual fund (protected) */}
            <Route
              path="/mf/*"
              element={
                <ProtectedRoute>
                  <ErrorBoundary>
                    <MFApp />
                  </ErrorBoundary>
                </ProtectedRoute>
              }
            />

            {/* Admin area (admin-key auth, kept as-is) */}
            <Route
              path="/admin/*"
              element={
                adminToken ? (
                  <AdminDashboard token={adminToken} />
                ) : (
                  <LoginPage onLogin={handleAdminLogin} />
                )
              }
            />

            {/* Fallback */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>

        <Toaster
          position="top-right"
          toastOptions={{
            style: {
              background: 'var(--neo-surface)',
              color: 'var(--neo-text)',
              border: '1px solid var(--neo-border)',
            },
          }}
        />
      </BrowserRouter>
    </AuthProvider>
  );
}


export default App;

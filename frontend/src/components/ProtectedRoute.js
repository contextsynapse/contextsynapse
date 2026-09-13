import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

export default function ProtectedRoute({ children }) {
  const { isAuthenticated } = useAuth();
  const location = useLocation();

  if (!isAuthenticated) {
    // Redirect to vertical-specific login based on current path
    let loginPath = '/login';
    if (location.pathname.startsWith('/pms')) loginPath = '/pms/login';
    else if (location.pathname.startsWith('/mf')) loginPath = '/mf/login';

    return <Navigate to={loginPath} state={{ from: location }} replace />;
  }

  return children;
}

import React from 'react';
import { render, screen } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
import ProtectedRoute from './ProtectedRoute';

jest.mock('../lib/api', () => ({
  defaults: { headers: {} },
  get: jest.fn().mockResolvedValue({ data: {} }),
}));

beforeEach(() => {
  localStorage.clear();
});

test('ProtectedRoute module loads', () => {
  expect(ProtectedRoute).toBeDefined();
});

test('ProtectedRoute redirects when not authenticated', () => {
  render(
    <BrowserRouter>
      <AuthProvider>
        <ProtectedRoute>
          <div data-testid="protected">Secret</div>
        </ProtectedRoute>
      </AuthProvider>
    </BrowserRouter>
  );
  // Should not show protected content when not logged in
  expect(screen.queryByTestId('protected')).toBeNull();
});

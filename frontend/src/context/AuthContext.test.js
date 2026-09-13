import React from 'react';
import { render, screen, act } from '@testing-library/react';
import { AuthProvider, useAuth } from './AuthContext';

// Helper component that exposes auth context for testing
function AuthConsumer() {
  const auth = useAuth();
  return (
    <div>
      <span data-testid="is-auth">{auth.isAuthenticated ? 'yes' : 'no'}</span>
      <span data-testid="user">{auth.user ? auth.user.email : 'none'}</span>
      <button onClick={auth.logout}>Logout</button>
    </div>
  );
}

beforeEach(() => {
  localStorage.clear();
});

test('useAuth throws outside AuthProvider', () => {
  // Suppress console.error for expected error
  const spy = jest.spyOn(console, 'error').mockImplementation(() => {});
  expect(() => render(<AuthConsumer />)).toThrow('useAuth must be used within <AuthProvider>');
  spy.mockRestore();
});

test('initial state is unauthenticated', () => {
  render(
    <AuthProvider>
      <AuthConsumer />
    </AuthProvider>
  );
  expect(screen.getByTestId('is-auth')).toHaveTextContent('no');
  expect(screen.getByTestId('user')).toHaveTextContent('none');
});

test('restores user from localStorage', () => {
  const mockUser = { email: 'test@example.com', display_name: 'Test' };
  localStorage.setItem('contextsynapse_token', 'fake-token');
  localStorage.setItem('contextsynapse_user', JSON.stringify(mockUser));

  render(
    <AuthProvider>
      <AuthConsumer />
    </AuthProvider>
  );
  expect(screen.getByTestId('is-auth')).toHaveTextContent('yes');
  expect(screen.getByTestId('user')).toHaveTextContent('test@example.com');
});

test('logout clears state and localStorage', () => {
  localStorage.setItem('contextsynapse_token', 'fake-token');
  localStorage.setItem('contextsynapse_user', JSON.stringify({ email: 'test@example.com' }));

  render(
    <AuthProvider>
      <AuthConsumer />
    </AuthProvider>
  );

  expect(screen.getByTestId('is-auth')).toHaveTextContent('yes');

  act(() => {
    screen.getByText('Logout').click();
  });

  expect(screen.getByTestId('is-auth')).toHaveTextContent('no');
  expect(localStorage.getItem('contextsynapse_token')).toBeNull();
  expect(localStorage.getItem('contextsynapse_user')).toBeNull();
});

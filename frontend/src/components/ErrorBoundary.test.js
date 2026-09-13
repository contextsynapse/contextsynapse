import React from 'react';
import { render, screen } from '@testing-library/react';
import ErrorBoundary from './ErrorBoundary';

function ThrowingChild({ shouldThrow }) {
  if (shouldThrow) throw new Error('Test error');
  return <div data-testid="child">OK</div>;
}

beforeEach(() => {
  jest.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  console.error.mockRestore();
});

test('renders children when no error', () => {
  render(
    <ErrorBoundary>
      <ThrowingChild shouldThrow={false} />
    </ErrorBoundary>
  );
  expect(screen.getByTestId('child')).toHaveTextContent('OK');
});

test('catches errors and shows fallback', () => {
  render(
    <ErrorBoundary>
      <ThrowingChild shouldThrow={true} />
    </ErrorBoundary>
  );
  expect(screen.queryByTestId('child')).toBeNull();
});

import React from 'react';
import { render, screen } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';

// Mock the Sidebar — it uses router + auth context
jest.mock('../lib/api', () => ({
  defaults: { headers: {} },
  get: jest.fn().mockResolvedValue({ data: {} }),
}));

test('Sidebar module loads without errors', () => {
  // Verify the component can be imported
  const Sidebar = require('./Sidebar').default;
  expect(Sidebar).toBeDefined();
});

test('Sidebar renders within router context', () => {
  const Sidebar = require('./Sidebar').default;
  const { container } = render(
    <BrowserRouter>
      <AuthProvider>
        <Sidebar />
      </AuthProvider>
    </BrowserRouter>
  );
  expect(container).toBeTruthy();
});

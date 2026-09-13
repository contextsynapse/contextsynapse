import React from 'react';
import { render } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';

jest.mock('../lib/api', () => ({
  defaults: { headers: {} },
  get: jest.fn().mockResolvedValue({ data: {} }),
}));

test('Header module loads without errors', () => {
  const Header = require('./Header').default;
  expect(Header).toBeDefined();
});

test('Header renders without crash', () => {
  const Header = require('./Header').default;
  const { container } = render(
    <BrowserRouter>
      <AuthProvider>
        <Header />
      </AuthProvider>
    </BrowserRouter>
  );
  expect(container).toBeTruthy();
});

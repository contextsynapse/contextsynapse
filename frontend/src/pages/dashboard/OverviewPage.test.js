import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import OverviewPage from './OverviewPage';

// Mock the API module
jest.mock('../../lib/api', () => ({
  get: jest.fn(),
}));

// Mock AuthContext
jest.mock('../../context/AuthContext', () => ({
  useAuth: () => ({ user: { email: 'test@example.com' }, token: 'fake', isAuthenticated: true, refreshProfile: jest.fn() }),
}));

const api = require('../../lib/api');

function renderWithRouter(ui) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

test('shows loading spinner initially', () => {
  api.get.mockReturnValue(new Promise(() => {})); // never resolves
  renderWithRouter(<OverviewPage />);
  // Should show the loading spinner (Loader2 renders as an SVG with animate-spin)
  const spinner = document.querySelector('.animate-spin');
  expect(spinner).toBeInTheDocument();
});

test('renders overview data after loading', async () => {
  api.get.mockResolvedValue({
    data: {
      graph_count: 3,
      node_count: 150,
      edge_count: 75,
      agent_count: 2,
      recent_activity: [],
    },
  });

  renderWithRouter(<OverviewPage />);

  await waitFor(() => {
    expect(screen.getByText('Dashboard')).toBeInTheDocument();
  });

  // Stats should appear
  expect(screen.getByText('3')).toBeInTheDocument();
  expect(screen.getByText('150')).toBeInTheDocument();
});

test('handles API error gracefully', async () => {
  api.get.mockRejectedValue(new Error('Network error'));
  renderWithRouter(<OverviewPage />);

  await waitFor(() => {
    // Should not crash — loading should complete
    expect(document.querySelector('.animate-spin')).not.toBeInTheDocument();
  });
});

import React from 'react';
import { render, screen } from '@testing-library/react';

jest.mock('../lib/api', () => ({
  defaults: { headers: {} },
  get: jest.fn().mockResolvedValue({ data: { jobs: [] } }),
}));

test('JobsContext module loads', () => {
  const mod = require('./JobsContext');
  expect(mod.JobsProvider).toBeDefined();
  expect(mod.useJobs).toBeDefined();
});

test('JobsProvider renders children', () => {
  const { JobsProvider } = require('./JobsContext');
  render(
    <JobsProvider>
      <div data-testid="child">Jobs</div>
    </JobsProvider>
  );
  expect(screen.getByTestId('child')).toHaveTextContent('Jobs');
});

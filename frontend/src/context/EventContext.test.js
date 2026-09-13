import React from 'react';
import { render, screen, act } from '@testing-library/react';

test('EventContext module loads', () => {
  const mod = require('./EventContext');
  expect(mod.EventProvider).toBeDefined();
  expect(mod.useEvent).toBeDefined();
});

test('EventProvider renders children', () => {
  const { EventProvider } = require('./EventContext');
  render(
    <EventProvider>
      <div data-testid="child">Hello</div>
    </EventProvider>
  );
  expect(screen.getByTestId('child')).toHaveTextContent('Hello');
});

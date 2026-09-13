import React, { useState, useEffect, useCallback } from 'react';
import Joyride, { STATUS } from 'react-joyride';

const STORAGE_KEY = 'contextsynapse_onboarding_complete';

const tourSteps = [
  {
    target: 'aside[role="navigation"]',
    content:
      'Welcome to the ContextSynapse dashboard! This sidebar is your navigation hub — access all features from graphs to monitoring right here.',
    title: 'Welcome to ContextSynapse',
    placement: 'right',
    disableBeacon: true,
  },
  {
    target: 'a[href="/dashboard/graphs"]',
    content:
      'Start by creating your first graph. Graphs are the core data structure in ContextSynapse — store nodes, edges, and properties all in one place.',
    title: 'Create Your First Graph',
    placement: 'right',
  },
  {
    target: 'a[href="/dashboard/playground"]',
    content:
      'Use the Playground to run AIQL queries against your graphs. Write Cypher-like queries to create, match, and traverse your data.',
    title: 'Run AIQL Queries',
    placement: 'right',
  },
  {
    target: 'a[href="/dashboard/monitoring"]',
    content:
      'Monitor query performance, storage usage, and system health in real time from the Monitoring page.',
    title: 'Monitor Your Data',
    placement: 'right',
  },
  {
    target: 'a[href="/dashboard/api-keys"]',
    content:
      'Generate API keys to connect external applications, agents, and integrations to your ContextSynapse instance.',
    title: 'Get API Keys',
    placement: 'right',
  },
];

const joyrideStyles = {
  options: {
    arrowColor: '#1a1a2e',
    backgroundColor: '#1a1a2e',
    overlayColor: 'rgba(0, 0, 0, 0.6)',
    primaryColor: '#00bcd4',
    textColor: '#e0e0e0',
    zIndex: 10000,
  },
  tooltip: {
    borderRadius: '10px',
    border: '1px solid rgba(0, 188, 212, 0.3)',
    boxShadow: '0 8px 32px rgba(0, 188, 212, 0.15)',
  },
  tooltipTitle: {
    color: '#00bcd4',
    fontSize: '16px',
    fontWeight: 600,
  },
  tooltipContent: {
    fontSize: '14px',
    lineHeight: 1.6,
    color: '#c0c0c0',
  },
  buttonNext: {
    backgroundColor: '#00bcd4',
    color: '#0a0a1a',
    fontWeight: 600,
    borderRadius: '6px',
    padding: '8px 18px',
  },
  buttonBack: {
    color: '#00bcd4',
    marginRight: 8,
  },
  buttonSkip: {
    color: '#888',
    fontSize: '13px',
  },
  buttonClose: {
    color: '#888',
  },
  beacon: {
    display: 'none',
  },
};

export default function OnboardingTour({ run }) {
  const [shouldRun, setShouldRun] = useState(false);

  useEffect(() => {
    if (run && !localStorage.getItem(STORAGE_KEY)) {
      setShouldRun(true);
    }
  }, [run]);

  const handleCallback = useCallback((data) => {
    const { status } = data;
    if (status === STATUS.FINISHED || status === STATUS.SKIPPED) {
      localStorage.setItem(STORAGE_KEY, 'true');
      setShouldRun(false);
    }
  }, []);

  if (!shouldRun) return null;

  return (
    <Joyride
      steps={tourSteps}
      run={shouldRun}
      continuous
      showProgress
      showSkipButton
      scrollToFirstStep
      disableOverlayClose
      callback={handleCallback}
      styles={joyrideStyles}
      locale={{
        back: 'Back',
        close: 'Close',
        last: 'Get Started',
        next: 'Next',
        skip: 'Skip tour',
      }}
    />
  );
}

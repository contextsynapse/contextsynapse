import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import api from '../lib/api';

const JobsContext = createContext(null);

export function useJobs() {
  return useContext(JobsContext);
}

/**
 * Global job state provider.
 * Mounts a single WebSocket at the Dashboard level so job progress
 * survives page navigation.  Exposes:
 *   jobs          – array of all jobs (sorted newest-first)
 *   activeCount   – number of currently processing jobs
 *   refreshJobs() – manual fetch from server
 *   jobsForContext(contextId) – filter helper
 */
export function JobsProvider({ children }) {
  const [jobs, setJobs] = useState([]);

  // ---- Fetch jobs from backend ----
  const refreshJobs = useCallback(async () => {
    try {
      const res = await api.get('/dashboard/jobs');
      setJobs(res.data.jobs || []);
    } catch {}
  }, []);

  // ---- Poll for job updates (instead of dedicated WebSocket) ----
  // Uses REST polling to avoid multiple WebSocket connections racing.
  // EventContext owns the single WebSocket; jobs refresh via polling.
  useEffect(() => {
    const token = localStorage.getItem('contextsynapse_token');
    if (!token) return;

    refreshJobs();

    // Poll every 5 seconds while there are active jobs
    const interval = setInterval(() => {
      refreshJobs();
    }, 5000);

    return () => clearInterval(interval);
  }, [refreshJobs]);

  // ---- Derived state ----
  const activeCount = jobs.filter(j => j.status === 'processing').length;

  const jobsForContext = useCallback(
    (contextId) => jobs.filter(j => j.context_id === contextId),
    [jobs]
  );

  return (
    <JobsContext.Provider value={{ jobs, activeCount, refreshJobs, jobsForContext }}>
      {children}
    </JobsContext.Provider>
  );
}

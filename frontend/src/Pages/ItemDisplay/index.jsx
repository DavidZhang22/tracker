import React, { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';

import { get, post } from '../../helper';
import Button from '@mui/material/Button';
import Box from '@mui/material/Box';
import CssBaseline from '@mui/material/CssBaseline';
import Container from '@mui/material/Container';
import Alert from '@mui/material/Alert';
import Chip from '@mui/material/Chip';
import CircularProgress from '@mui/material/CircularProgress';

export default function ItemDisplay() {
  const { ItemID } = useParams();
  const [tracker, setTracker] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const loadTracker = useCallback(() => {
    setLoading(true);
    get(`/trackers/${ItemID}/`).then((res) => {
      if (res.success) {
        setTracker(res.data);
        setError('');
      } else if (res.status === 401) {
        window.location.href = '/login';
      } else {
        setError(res.error || 'Could not load tracker.');
      }
      setLoading(false);
    });
  }, [ItemID]);

  useEffect(() => {
    loadTracker();
  }, [loadTracker]);

  const refresh = () => {
    setRefreshing(true);
    post(`/trackers/${ItemID}/refresh/`, {}).then((res) => {
      setRefreshing(false);
      if (res.success) {
        setTracker(res.data);
      } else if (res.status === 401) {
        window.location.href = '/login';
      } else {
        setError(res.error || 'Could not refresh tracker.');
      }
    });
  };

  const markSeen = () => {
    post(`/trackers/${ItemID}/seen/`, {}).then((res) => {
      if (res.success) {
        setTracker(res.data);
      } else if (res.status === 401) {
        window.location.href = '/login';
      }
    });
  };

  return (
    <Container component="main" maxWidth="lg">
      <CssBaseline />
      {error && <Alert severity="error" sx={{ mt: 4 }}>{error}</Alert>}
      {loading ? (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mt: 6 }}>
          <CircularProgress size={24} />
          <span>Loading tracker...</span>
        </Box>
      ) : tracker && (
        <Box sx={{ mt: 6 }}>
          <div className="flex flex-col gap-4 border-b border-slate-200 pb-6 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-3xl font-semibold text-slate-900">{tracker.name}</h1>
                {tracker.newCount > 0 && <Chip color="success" label={`${tracker.newCount} new`} />}
              </div>
              <a className="mt-2 block break-all text-blue-700 hover:underline" href={tracker.targetUrl} target="_blank" rel="noreferrer">
                {tracker.targetUrl}
              </a>
              <p className="mt-3 text-sm text-slate-600">
                {tracker.lastCheckedAt ? `Last checked ${new Date(tracker.lastCheckedAt).toLocaleString()}` : 'Not checked yet'}
              </p>
            </div>
            <div className="flex gap-2">
              <Button disabled={refreshing} variant="contained" onClick={refresh}>
                {refreshing ? 'Checking...' : 'Check now'}
              </Button>
              <Button disabled={tracker.newCount === 0} variant="outlined" onClick={markSeen}>Mark seen</Button>
            </div>
          </div>

          <div className="mt-6 grid gap-3">
            {tracker.entries.length === 0 && (
              <div className="border border-dashed border-slate-300 p-8 text-center text-slate-500">
                No links have been found on this page yet.
              </div>
            )}
            {tracker.entries.map((entry) => (
              <article key={entry.id} className="border border-slate-200 bg-white p-5">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <a className="text-lg font-semibold text-slate-900 hover:text-blue-700" href={entry.url} target="_blank" rel="noreferrer">
                        {entry.title}
                      </a>
                      {entry.isNew && <Chip color="success" size="small" label="New" />}
                    </div>
                    {entry.summary && <p className="mt-2 text-sm text-slate-600">{entry.summary}</p>}
                    <p className="mt-3 text-xs text-slate-500">First seen {new Date(entry.firstSeenAt).toLocaleString()}</p>
                  </div>
                </div>
              </article>
            ))}
          </div>
        </Box>
      )}
    </Container>
  );
}

import React, { useEffect, useState } from 'react';
import { get, post } from '../../helper';
import Button from '@mui/material/Button';
import TextField from '@mui/material/TextField';
import Box from '@mui/material/Box';
import CssBaseline from '@mui/material/CssBaseline';
import Container from '@mui/material/Container';
import Alert from '@mui/material/Alert';
import CircularProgress from '@mui/material/CircularProgress';

import Item from '../../Components/Item';

export default function Dashboard() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const loadTrackers = () => {
    setLoading(true);
    get('/trackers/').then((res) => {
      if (res.success) {
        setItems(res.data);
        setError('');
      } else if (res.status === 401) {
        window.location.href = '/login';
      } else {
        setError(res.error || 'Could not load trackers.');
      }
      setLoading(false);
    });
  };

  useEffect(() => {
    loadTrackers();
  }, []);

  const addItem = (e) => {
    e.preventDefault();
    setSaving(true);
    setError('');

    const data = new FormData(e.currentTarget);
    const body = {
      name: data.get('name'),
      sourceUrl: data.get('sourceUrl'),
      currentUrl: data.get('currentUrl'),
      checkIntervalMinutes: Number(data.get('checkIntervalMinutes') || 60),
    };

    post('/trackers/create/', body).then((res) => {
      setSaving(false);
      if (res.success) {
        e.currentTarget.reset();
        loadTrackers();
      } else if (res.status === 401) {
        window.location.href = '/login';
      } else {
        setError(res.error || 'Could not create tracker.');
      }
    });
  };

  return (
    <Container component="main" maxWidth="xl">
      <CssBaseline />
      <Box sx={{ marginTop: 6, display: 'grid', gridTemplateColumns: { xs: '1fr', md: 'minmax(0, 1fr) 390px' }, gap: 4 }}>
        <Box>
          <h1 className="text-3xl font-semibold text-slate-900">Tracked media pages</h1>
          <p className="mt-2 text-slate-600">Each tracker watches a listing page and records newly discovered links.</p>

          {error && <Alert severity="error" sx={{ mt: 3 }}>{error}</Alert>}

          {loading ? (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mt: 4 }}>
              <CircularProgress size={24} />
              <span>Checking trackers...</span>
            </Box>
          ) : (
            <div className="mt-6 grid gap-4">
              {items.length === 0 && (
                <div className="border border-dashed border-slate-300 p-8 text-center text-slate-500">
                  Add a media listing page to start collecting article links.
                </div>
              )}
              {items.map((item) => <Item key={item.id} item={item} />)}
            </div>
          )}
        </Box>

        <Box component="form" onSubmit={addItem} noValidate sx={{ border: '1px solid #e2e8f0', p: 3, alignSelf: 'start' }}>
          <h2 className="text-xl font-semibold text-slate-900">Add tracker</h2>
          <TextField margin="normal" required fullWidth id="name" label="Name" name="name" autoComplete="name" />
          <TextField margin="normal" required fullWidth name="sourceUrl" label="Listing page URL" type="url" id="sourceUrl" />
          <TextField margin="normal" fullWidth name="currentUrl" label="Current page URL (optional)" type="url" id="currentUrl" />
          <TextField margin="normal" required fullWidth name="checkIntervalMinutes" label="Check interval minutes" type="number" id="checkIntervalMinutes" defaultValue="60" inputProps={{ min: 1 }} />
          <Button disabled={saving} type="submit" variant="contained" sx={{ mt: 3, mb: 1 }}>
            {saving ? 'Adding...' : 'Add tracker'}
          </Button>
        </Box>
      </Box>
    </Container>
  );
}

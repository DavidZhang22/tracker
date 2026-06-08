import React from 'react';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';

export default function Item({ item }) {
  return (
    <div className="border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-xl font-semibold text-slate-900">{item.name}</h2>
            {item.newCount > 0 && <Chip color="success" size="small" label={`${item.newCount} new`} />}
          </div>
          <a className="mt-2 block break-all text-sm text-blue-700 hover:underline" href={item.targetUrl} target="_blank" rel="noreferrer">
            {item.targetUrl}
          </a>
          <div className="mt-3 flex flex-wrap gap-3 text-sm text-slate-600">
            <span>{item.entryCount} entries</span>
            <span>Every {item.checkIntervalMinutes} min</span>
            <span>{item.lastCheckedAt ? `Last checked ${new Date(item.lastCheckedAt).toLocaleString()}` : 'Not checked yet'}</span>
          </div>
        </div>
        <Button variant="outlined" href={`/dashboard/${item.id}`}>Open</Button>
      </div>
    </div>
  );
}

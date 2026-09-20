import { performance } from "node:perf_hooks";
import { writeFile } from "node:fs/promises";
import { day, checked, dateTime } from "../src/dates.js";

const rows = Array.from({ length: 500 }, (_, index) =>
  new Date(Date.UTC(2026, 8, 20, 12) - index * 3600000).toISOString(),
);
const original = (value) => {
  const date = new Date(value);
  return [
    date.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
      timeZone: "UTC",
    }),
    date.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }),
    date.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }),
  ];
};
const current = (value) => [day(value), checked(value), dateTime(value)];
if (JSON.stringify(rows.map(original)) !== JSON.stringify(rows.map(current)))
  throw new Error("Date formatting parity failed");
const measure = (operation) => {
  const samples = [];
  for (let run = 0; run < 9; run++) {
    const start = performance.now();
    rows.forEach(operation);
    samples.push(performance.now() - start);
  }
  return samples.sort((a, b) => a - b)[4];
};
const report = {
  baseline_commit: "f2d546d",
  runtime: process.version,
  rows: rows.length,
  formats_per_row: 3,
  parity: true,
  baseline_ms: measure(original),
  reuse_ms: measure(current),
  scope:
    "Synthetic date-formatting microbenchmark; excludes browser rendering and network. Three formatter objects, no per-item cache.",
};
const output = process.argv[2];
if (output) await writeFile(output, JSON.stringify(report, null, 2) + "\n");
console.log(JSON.stringify(report, null, 2));

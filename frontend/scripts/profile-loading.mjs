import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { gzipSync } from "node:zlib";
import { performance } from "node:perf_hooks";
import { searchSnapshot } from "../src/Search/snapshot.js";
import {
  sameResult,
  createLinkCache,
  LINK_CACHE_BYTES,
} from "../src/Hooks/linkCache.js";

const [baselinePath, candidatePath, reportPath] = process.argv.slice(2);
if (!baselinePath || !candidatePath || !reportPath || !global.gc) {
  throw new Error(
    "Usage: node --expose-gc scripts/profile-loading.mjs BASELINE_BUILD CANDIDATE_BUILD REPORT_JSON",
  );
}
const median = (values) =>
  [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];
const measure = (run, repetitions = 100) => {
  for (let i = 0; i < 10; i++) run();
  const elapsed = [];
  for (let i = 0; i < repetitions; i++) {
    const started = performance.now();
    run();
    elapsed.push(performance.now() - started);
  }
  const p95 = [...elapsed]
    .sort((a, b) => a - b)
    .at(Math.floor(repetitions * 0.95));
  return {
    median_ms: +median(elapsed).toFixed(4),
    p95_ms: +p95.toFixed(4),
  };
};
function bundle(path) {
  const manifest = JSON.parse(
    readFileSync(resolve(path, ".vite/manifest.json"), "utf8"),
  );
  const entry = Object.entries(manifest).find(
    ([, value]) => value.isEntry,
  )?.[0];
  const seen = new Set();
  function visit(key) {
    if (seen.has(key)) return;
    seen.add(key);
    for (const dependency of manifest[key].imports || []) visit(dependency);
  }
  visit(entry);
  const files = [...seen]
    .map((key) => manifest[key].file)
    .filter((file) => file.endsWith(".js"));
  const sizes = files.map((file) => {
    const data = readFileSync(resolve(path, file));
    return { file, raw_bytes: data.length, gzip_bytes: gzipSync(data).length };
  });
  return {
    static_javascript: sizes,
    raw_bytes: sizes.reduce((sum, file) => sum + file.raw_bytes, 0),
    gzip_bytes: sizes.reduce((sum, file) => sum + file.gzip_bytes, 0),
  };
}
const items = JSON.parse(
  JSON.stringify(
    Array.from({ length: 500 }, (_, i) => ({
      id: `item-${i}`,
      title: `Example series ${i}`,
      url: `https://example.test/series/${i}`,
      source_name: "Example",
      kind: "novel",
      description:
        `A fictional series ${i}. ` + "An author description. ".repeat(15),
      source_summary:
        `Source ${i} ` +
        "Source context for the media collection. ".repeat(190),
      search_tags: ["novel", "fiction", `topic-${i % 20}`],
      deleted: false,
    })),
  ),
);
function legacySnapshot(rows, previous) {
  const revision = JSON.stringify(
    rows.map(
      ({
        id,
        title,
        url,
        source_name,
        kind,
        description,
        source_summary,
        search_tags,
        deleted,
      }) => ({
        id,
        title,
        url,
        source_name,
        kind,
        description,
        source_summary,
        search_tags,
        deleted,
      }),
    ),
  );
  if (previous?.revision === revision) return previous;
  return { revision, rows: JSON.parse(revision) };
}
const previous = searchSnapshot(items);
const legacyPrevious = legacySnapshot(items);
const refreshRows = items.map((item) => ({
  ...item,
  unread_count: 3,
  favorite: true,
}));
const changedRows = refreshRows.map((item, i) =>
  i === 250 ? { ...item, description: "A new description" } : item,
);
let held;
function retained(run) {
  run();
  const samples = [];
  for (let i = 0; i < 7; i++) {
    held = null;
    global.gc();
    const before = process.memoryUsage().heapUsed;
    held = run();
    if (!held) throw new Error("Benchmark result unexpectedly empty");
    global.gc();
    samples.push(process.memoryUsage().heapUsed - before);
  }
  held = null;
  return Math.max(0, Math.round(median(samples)));
}
const page = {
  total: 4999,
  links: Array.from({ length: 50 }, (_, i) => ({
    id: `link-${i}`,
    title: `Entry ${i}`,
    context: `Company ${i}; location; role; qualifications. `
      .repeat(105)
      .slice(0, 4000),
    members: [{ id: `member-${i}`, title: `Alternate ${i}`, read: false }],
  })),
};
const samePage = JSON.parse(JSON.stringify(page));
const legacyCache = new Map();
const cache = createLinkCache();
for (let i = 0; i < 8; i++) {
  legacyCache.set(String(i), page);
  cache.set(String(i), page, 0);
}
const before = bundle(baselinePath),
  after = bundle(candidatePath);
const result = {
  environment: {
    node: process.version,
    platform: process.platform,
    architecture: process.arch,
    timing:
      "Local single process, warm synthetic operations; medians of 100 iterations, not browser page-load timings.",
  },
  fixture: {
    items: items.length,
    source_summary_chars: items[0].source_summary.length,
    link_page_rows: page.links.length,
    link_context_chars: page.links[0].context.length,
    private_or_live_data: false,
  },
  initial_bundle: {
    before,
    after,
    raw_reduction_percent: +(
      (1 - after.raw_bytes / before.raw_bytes) *
      100
    ).toFixed(2),
    gzip_reduction_percent: +(
      (1 - after.gzip_bytes / before.gzip_bytes) *
      100
    ).toFixed(2),
    note: "Sums all static JS imports of entry, separately gzipped. Deferred private/public routes are excluded until visited.",
  },
  unchanged_metadata_refresh: {
    before: measure(() => legacySnapshot(refreshRows, legacyPrevious)),
    after: measure(() => searchSnapshot(refreshRows, previous)),
  },
  changed_metadata_refresh: {
    before: measure(() => legacySnapshot(changedRows, legacyPrevious)),
    after: measure(() => searchSnapshot(changedRows, previous)),
  },
  retained_metadata_heap_bytes: {
    before: retained(() => legacySnapshot(items)),
    after: retained(() => searchSnapshot(items)),
    note: "GC-based approximate retained V8 heap above the existing API rows; excludes worker heap and DOM.",
  },
  repeated_link_page_comparison: {
    before: measure(() => JSON.stringify(page) === JSON.stringify(samePage)),
    after: measure(() => sameResult(page, samePage)),
  },
  link_cache: {
    before_page_count: legacyCache.size,
    after_page_count: Array.from({ length: 8 }, (_, i) =>
      cache.get(String(i)),
    ).filter(Boolean).length,
    estimate_limit_bytes: LINK_CACHE_BYTES,
    note: "Retained-size estimate is conservative UTF16 text plus structural overhead, not a browser heap guarantee. Oversized pages render but are not cached.",
  },
};
mkdirSync(dirname(resolve(reportPath)), { recursive: true });
writeFileSync(resolve(reportPath), JSON.stringify(result, null, 2) + "\n");
console.log(JSON.stringify(result, null, 2));

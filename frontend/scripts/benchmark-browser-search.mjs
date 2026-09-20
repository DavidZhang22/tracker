import { performance } from "node:perf_hooks";
import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { librarySearchIndex as current } from "../src/media.js";

const baselinePath = process.argv[2];
if (!baselinePath)
  throw new Error("Pass the previous media.js file for a comparable baseline.");
const { librarySearchIndex: baseline } = await import(
  pathToFileURL(resolve(baselinePath))
);
const topics = [
  ["Grand Blue", "college comedy scuba diving manga episodes adventure"],
  ["Python Bytes", "programming podcast interviews software releases tutorial"],
  [
    "Astronomy Archive",
    "research journal stars planets space physics telescope",
  ],
  ["Kitchen Notes", "recipes baking cooking food restaurant culture"],
  ["History Weekly", "history civilization documentary books geography maps"],
  ["Sound Studies", "music concerts audio album composer reviews"],
  ["Garden Guide", "plants seeds gardening nature biology flowers"],
  ["Career Board", "jobs employment engineering graduate internships remote"],
];
const makeRows = (count) =>
  Array.from({ length: count }, (_, index) => {
    const [title, words] = topics[index % topics.length];
    return {
      id: String(index),
      title: `${title} ${index + 1}`,
      url: `https://source${index % 37}.example/collection/${index + 1}`,
      source_name: `Source ${index % 37}`,
      search_tags: words.split(" "),
      description: `Follow ${title.toLowerCase()} for ${words}. This collection brings together ${words} with new entries, detailed explanations, and selected reading from independent creators. Series ${index + 1} covers ongoing stories and recent developments for interested readers.`,
    };
  });
const queries = [
  ...topics.flatMap(([title, words]) => [
    title.toLowerCase(),
    ...words.split(" "),
  ]),
  "grnad blue",
  "pyhton bytes",
  "astronoy",
  "gradute",
  "softwrae",
  "engineering remote",
  ...Array.from({ length: 80 }, (_, index) => `collection ${index + 1}`),
];
const median = (samples) =>
  samples.sort((a, b) => a - b)[Math.floor(samples.length / 2)];
const measure = (operation) => {
  const samples = [];
  for (let run = 0; run < 9; run++) {
    const start = performance.now();
    operation();
    samples.push(performance.now() - start);
  }
  return median(samples);
};
const report = {
  runtime: process.version,
  note: "Synthetic public-style metadata; 500 is the account limit. Pure JS timings exclude worker startup and browser rendering.",
  queries: queries.length,
  results: [],
};
for (const count of [50, 200, 500, 2000]) {
  const rows = makeRows(count);
  const oldSearch = baseline(rows),
    newSearch = current(rows);
  let maxError = 0,
    changedMembership = 0;
  for (const query of queries) {
    const oldScores = oldSearch(query),
      newScores = newSearch(query);
    for (const [id, score] of oldScores) {
      maxError = Math.max(maxError, Math.abs(score - newScores.get(id)));
      if (score > 0 !== newScores.get(id) > 0) changedMembership++;
    }
  }
  const sample = {
    items: count,
    metadata_bytes: Buffer.byteLength(JSON.stringify(rows)),
    max_score_error: maxError,
    changed_membership: changedMembership,
  };
  for (const [name, build] of [
    ["before", baseline],
    ["after", current],
  ]) {
    const search = build(rows);
    sample[name] = {
      build_ms: measure(() => build(rows)),
      queries_ms: measure(() => queries.forEach((query) => search(query))),
      repeated_queries_ms: measure(() => {
        for (let run = 0; run < 20; run++)
          queries.slice(0, 20).forEach((query) => search(query));
      }),
    };
  }
  report.results.push(sample);
}
if (process.argv[3])
  await writeFile(
    resolve(process.argv[3]),
    `${JSON.stringify(report, null, 2)}\n`,
  );
process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);

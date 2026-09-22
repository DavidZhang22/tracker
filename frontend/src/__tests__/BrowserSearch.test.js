import { vi } from "vitest";
import { createLocalSearch } from "../Search/localSearch";
import { createSearchHandler } from "../Search/workerHandler";
import { librarySearchIndex } from "../Search/searchIndex";

const rows = [
  {
    id: "a",
    title: "Grand Blue",
    description: "College scuba comedy",
    url: "https://a.example",
  },
  {
    id: "b",
    title: "Python Bytes",
    description: "Programming podcast",
    url: "https://b.example",
  },
];
let instances;
class FakeWorker {
  messages = [];
  terminate = vi.fn();
  constructor() {
    instances.push(this);
  }
  postMessage(message) {
    this.messages.push(message);
  }
  reply(id, scores) {
    this.onmessage({ data: { id, scores } });
  }
}
beforeEach(() => {
  instances = [];
  vi.useFakeTimers();
  vi.stubGlobal("Worker", FakeWorker);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test("worker ranking matches inline ranking without receiving reading state", () => {
  const received = [];
  const handle = createSearchHandler((value) => received.push(value));
  handle({ type: "index", items: rows });
  for (const [id, query] of [
    "grnad blue",
    "programming",
    "scuba comedy",
    "nonsense",
  ].entries()) {
    handle({ type: "query", id, query });
    expect(received.at(-1)).toEqual({
      id,
      scores: [...librarySearchIndex(rows)(query)],
    });
  }
});

test("new worker queries cancel old waiters and ignore late responses", async () => {
  const client = createLocalSearch(rows);
  const old = client.query("scuba");
  const rejected = expect(old).rejects.toMatchObject({ name: "AbortError" });
  const latest = client.query("python");
  const worker = instances[0];
  worker.reply(1, [["a", 1]]);
  worker.reply(2, [["b", 1]]);
  await rejected;
  expect(await latest).toEqual(new Map([["b", 1]]));
  client.dispose();
  expect(worker.terminate).toHaveBeenCalledTimes(1);
});

test("worker failure and unresponsive workers fall back to the same local index", async () => {
  const client = createLocalSearch(rows);
  const first = client.query("pyhton");
  instances[0].onerror(new Error("Worker blocked"));
  expect((await first).get("b")).toBeGreaterThan(0);
  expect((await client.query("college")).get("a")).toBeGreaterThan(0);
  expect(instances[0].messages).toHaveLength(2);
  client.dispose();
  const stalled = createLocalSearch(rows);
  const next = stalled.query("scuba");
  await vi.advanceTimersByTimeAsync(1500);
  expect((await next).get("a")).toBeGreaterThan(0);
  stalled.dispose();
});

test("unmount closes work, clears pending requests, and forbids reuse", async () => {
  const client = createLocalSearch(rows);
  const pending = client.query("grand");
  const rejected = expect(pending).rejects.toMatchObject({
    name: "AbortError",
  });
  client.dispose();
  await rejected;
  await expect(client.query("grand")).rejects.toMatchObject({
    name: "AbortError",
  });
  expect(vi.getTimerCount()).toBe(0);
});

test("unsupported Worker falls back without network or persistent storage", async () => {
  vi.stubGlobal("Worker", undefined);
  const client = createLocalSearch(rows);
  expect((await client.query("grnad blue")).get("a")).toBeGreaterThan(0);
  client.dispose();
});

test("malformed worker scores fall back instead of stranding selection", async () => {
  const client = createLocalSearch(rows);
  const result = client.query("grand");
  instances[0].reply(1, ["invalid map entry"]);
  expect((await result).get("a")).toBeGreaterThan(0);
  expect(vi.getTimerCount()).toBe(0);
  client.dispose();
});

test("an unused library does not start a worker or construct a search index", async () => {
  const client = createLocalSearch(rows);
  expect(instances).toHaveLength(0);
  const result = client.query("grand");
  expect(instances).toHaveLength(1);
  instances[0].reply(1, [["a", 1]]);
  await result;
  client.dispose();
});

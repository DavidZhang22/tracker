import { librarySearchIndex } from "../media.js";

export const WORKER_THRESHOLD = 50;

export function createLocalSearch(items) {
  let worker = null;
  let fallback = null;
  let pending = null;
  let sequence = 0;
  let disposed = false;
  const abort = () => {
    if (!pending) return;
    clearTimeout(pending.timer);
    pending.reject(new DOMException("Search superseded", "AbortError"));
    pending = null;
  };
  const fail = () => {
    worker?.terminate();
    worker = null;
    if (!pending) return;
    const request = pending;
    pending = null;
    clearTimeout(request.timer);
    fallback ||= librarySearchIndex(items);
    request.resolve(fallback(request.query));
  };
  try {
    worker = new Worker(new URL("./library.worker.js", import.meta.url), {
      type: "module",
      name: "library-search",
    });
    worker.onmessage = ({ data }) => {
      if (!pending || data?.id !== pending.id) return;
      if (
        !Array.isArray(data.scores) ||
        data.scores.some(
          (entry) =>
            !Array.isArray(entry) ||
            entry.length !== 2 ||
            typeof entry[0] !== "string" ||
            !Number.isFinite(entry[1]),
        )
      )
        return fail();
      clearTimeout(pending.timer);
      pending.resolve(new Map(data.scores));
      pending = null;
    };
    worker.onerror = fail;
    worker.onmessageerror = fail;
    worker.postMessage({ type: "index", items });
  } catch {
    worker?.terminate();
    worker = null;
  }
  return {
    query(query) {
      abort();
      if (disposed)
        return Promise.reject(new DOMException("Search closed", "AbortError"));
      if (!worker) {
        fallback ||= librarySearchIndex(items);
        return Promise.resolve(fallback(query));
      }
      return new Promise((resolve, reject) => {
        const id = ++sequence;
        pending = { id, query, resolve, reject, timer: setTimeout(fail, 1500) };
        try {
          worker.postMessage({ type: "query", id, query });
        } catch {
          fail();
        }
      });
    },
    dispose() {
      disposed = true;
      abort();
      worker?.terminate();
      worker = null;
      fallback = null;
    },
  };
}

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { librarySearchIndex, normalizeLibraryQuery } from "../media";
import { createLocalSearch, WORKER_THRESHOLD } from "../Search/localSearch";

export default function useLibrarySearch(
  items,
  query,
  trash,
  mode = "semantic",
) {
  const revision = useMemo(
    () =>
      JSON.stringify(
        items.map((item) => ({
          id: item.id,
          title: item.title,
          url: item.url,
          source_name: item.source_name,
          kind: item.kind,
          description: item.description,
          source_summary: item.source_summary,
          search_tags: item.search_tags,
          deleted: item.deleted,
        })),
      ),
    [items],
  );
  // Read counts and favorite changes do not invalidate an expensive text index.
  const rows = useMemo(() => JSON.parse(revision), [revision]);
  const key = normalizeLibraryQuery(query);
  const remote = mode !== "local" && key.length >= 2 && rows.length > 0;
  const threaded =
    rows.length >= WORKER_THRESHOLD && typeof Worker !== "undefined";
  const index = useMemo(
    () => (threaded ? null : librarySearchIndex(rows)),
    [rows, threaded],
  );
  const all = useMemo(() => new Map(rows.map(({ id }) => [id, 1])), [rows]);
  const inline = useMemo(() => index?.(key), [index, key]);
  const client = useRef(null);
  const [local, setLocal] = useState(null);
  useEffect(() => {
    if (!threaded) return;
    const search = createLocalSearch(rows);
    client.current = search;
    return () => {
      search.dispose();
      if (client.current === search) client.current = null;
    };
  }, [rows, threaded]);
  useEffect(() => {
    if (!threaded || !key) return;
    let current = true;
    client.current
      .query(key)
      .then((scores) => {
        if (current) setLocal({ revision, key, scores });
      })
      .catch(() => {});
    return () => {
      current = false;
    };
  }, [key, revision, threaded]);
  const fallback =
    inline ||
    (local?.revision === revision && local.key === key ? local.scores : null);
  const cache = useRef({ revision, trash, scores: new Map() });
  if (cache.current.revision !== revision || cache.current.trash !== trash)
    cache.current = { revision, trash, scores: new Map() };
  const generation = useRef(0);
  const [settled, setSettled] = useState(null);

  useEffect(() => {
    const version = ++generation.current;
    const savedScores = cache.current.scores;
    if (!remote || savedScores.has(key)) return;
    const controller = new AbortController();
    let deadline;
    const timer = setTimeout(async () => {
      deadline = setTimeout(() => {
        if (version !== generation.current) return;
        controller.abort();
        setSettled({ key, revision, trash, scores: null });
      }, 8000);
      try {
        const result = await api("/search", {
          method: "POST",
          body: JSON.stringify({ query: key, trash }),
          signal: controller.signal,
        });
        if (controller.signal.aborted || version !== generation.current) return;
        if (
          !Array.isArray(result?.scores) ||
          result.scores.some(
            ({ id, score }) =>
              typeof id !== "string" || !Number.isFinite(score),
          )
        )
          throw new Error("Invalid search results");
        const scores = new Map(
          result.scores.map(({ id, score }) => [id, score]),
        );
        savedScores.set(key, scores);
        if (savedScores.size > 24)
          savedScores.delete(savedScores.keys().next().value);
        setSettled({ key, revision, trash, scores });
      } catch {
        if (controller.signal.aborted || version !== generation.current) return;
        setSettled({ key, revision, trash, scores: null });
      } finally {
        clearTimeout(deadline);
      }
    }, 250);
    return () => {
      clearTimeout(timer);
      clearTimeout(deadline);
      controller.abort();
    };
  }, [key, remote, revision, trash]);

  if (!key) return { scores: all, updating: false };
  if (!remote) return { scores: fallback || all, updating: !fallback };
  if (cache.current.scores.has(key))
    return { scores: cache.current.scores.get(key), updating: false };
  const sameLibrary = settled?.revision === revision && settled.trash === trash;
  if (sameLibrary && settled.key === key)
    return {
      scores: settled.scores || fallback || all,
      updating: !settled.scores && !fallback,
    };
  const localMatches =
    fallback && [...fallback.values()].some((score) => score > 0);
  return {
    scores: localMatches
      ? fallback
      : (sameLibrary && settled.scores) || fallback || all,
    updating: true,
  };
}

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { librarySearchIndex } from "./media";

export default function useLibrarySearch(items, query, trash) {
  const revision = JSON.stringify(
    items.map((item) => [
      item.id,
      item.title,
      item.url,
      item.source_name,
      item.kind,
      item.description,
      item.source_summary,
      item.search_tags,
      item.deleted,
    ]),
  );
  const text = query.trim().slice(0, 200);
  const cache = useRef(new Map());
  const generation = useRef(0);
  const [settled, setSettled] = useState(null);
  const index = useMemo(() => librarySearchIndex(items), [items]);
  const fallback = useMemo(() => index(text), [index, text]);
  const key = `${trash}:${revision}:${text.toLowerCase()}`;

  useEffect(() => {
    const version = ++generation.current;
    if (!text || cache.current.has(key)) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const result = await api("/search", {
          method: "POST",
          body: JSON.stringify({ query: text, trash }),
          signal: controller.signal,
        });
        if (
          controller.signal.aborted ||
          version !== generation.current ||
          !Array.isArray(result?.scores)
        )
          return;
        const scores = new Map(
          result.scores.map(({ id, score }) => [id, score]),
        );
        cache.current.set(key, scores);
        if (cache.current.size > 24)
          cache.current.delete(cache.current.keys().next().value);
        setSettled({ key, revision, trash, scores });
      } catch (error) {
        if (controller.signal.aborted || version !== generation.current) return;
        setSettled({ key, revision, trash, scores: null });
      }
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [key, revision, text, trash]);

  if (!text) return fallback;
  if (cache.current.has(key)) return cache.current.get(key);
  if (settled?.key === key) return settled.scores || fallback;
  // Retain the last completed view while a new query is debounced or in flight.
  if (
    settled?.revision === revision &&
    settled.trash === trash &&
    settled.scores
  )
    return settled.scores;
  return fallback;
}

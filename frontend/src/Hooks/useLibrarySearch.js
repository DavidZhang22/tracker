import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { librarySearchIndex } from "../media";

export default function useLibrarySearch(items, query, trash) {
  const revision = useMemo(
    () =>
      JSON.stringify(
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
      ),
    [items],
  );
  const text = query.trim().slice(0, 200);
  const cache = useRef({ revision, trash, scores: new Map() });
  if (cache.current.revision !== revision || cache.current.trash !== trash)
    cache.current = { revision, trash, scores: new Map() };
  const generation = useRef(0);
  const [settled, setSettled] = useState(null);
  const index = useMemo(() => librarySearchIndex(items), [items]);
  const fallback = useMemo(() => index(text), [index, text]);
  const key = text.toLowerCase();

  useEffect(() => {
    const version = ++generation.current;
    const savedScores = cache.current.scores;
    if (!text || savedScores.has(key)) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const result = await api("/search", {
          method: "POST",
          body: JSON.stringify({ query: text, trash }),
          signal: controller.signal,
        });
        if (controller.signal.aborted || version !== generation.current) return;
        if (!Array.isArray(result?.scores))
          throw new Error("Invalid search results");
        const scores = new Map(
          result.scores.map(({ id, score }) => [id, score]),
        );
        savedScores.set(key, scores);
        if (savedScores.size > 24)
          savedScores.delete(savedScores.keys().next().value);
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

  if (!text) return { scores: fallback, updating: false };
  if (cache.current.scores.has(key))
    return { scores: cache.current.scores.get(key), updating: false };
  const sameLibrary = settled?.revision === revision && settled.trash === trash;
  if (sameLibrary && settled.key === key)
    return { scores: settled.scores || fallback, updating: false };
  // Retain the last completed view while a new query is debounced or in flight.
  return {
    scores: (sameLibrary && settled.scores) || fallback,
    updating: true,
  };
}

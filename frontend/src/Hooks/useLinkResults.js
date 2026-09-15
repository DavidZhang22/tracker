import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";

const EMPTY = { links: [], total: 0 };

export function useLinkResults({
  id,
  filter,
  sort,
  direction,
  search,
  offset,
  version,
  setOffset,
}) {
  const normalized = search.trim();
  const [query, setQuery] = useState(normalized);
  const [result, setResult] = useState(null);
  const [pending, setPending] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const cache = useRef(new Map());
  const epoch = useRef("");
  const scope = `${id}:${version}`;
  if (epoch.current !== scope) {
    cache.current.clear();
    epoch.current = scope;
  }
  useEffect(() => {
    const timer = setTimeout(() => setQuery(normalized), 250);
    return () => clearTimeout(timer);
  }, [normalized]);
  const path = useMemo(
    () =>
      `/items/${id}/links?${new URLSearchParams({ filter, sort, direction, search: query, offset, limit: 50 })}`,
    [id, filter, sort, direction, query, offset],
  );
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setError("");
    const receive = (data) => {
      if (!active || epoch.current !== scope) return;
      setResult((previous) => ({
        id,
        path,
        scope,
        data:
          previous?.id === id &&
          JSON.stringify(previous.data) === JSON.stringify(data)
            ? previous.data
            : data,
      }));
      if (offset && offset >= data.total) setOffset(0);
      setPending(false);
    };
    const saved = cache.current.get(path);
    if (saved && Date.now() - saved.time < 30000) receive(saved.data);
    else {
      setPending(true);
      api(path, { signal: controller.signal })
        .then((data) => {
          if (!active || epoch.current !== scope) return;
          cache.current.set(path, { data, time: Date.now() });
          if (cache.current.size > 8)
            cache.current.delete(cache.current.keys().next().value);
          receive(data);
        })
        .catch((e) => {
          if (active && e.name !== "AbortError") {
            setError(e.message);
            setPending(false);
          }
        });
    }
    return () => {
      active = false;
      controller.abort();
    };
  }, [id, path, scope, offset, setOffset, retry]);
  const current = result?.id === id;
  return {
    data: current ? result.data : EMPTY,
    initialLoading: !current && pending,
    updating:
      pending ||
      query !== normalized ||
      !current ||
      result.path !== path ||
      result.scope !== scope,
    error,
    submitSearch: () => {
      if (query === normalized && error) setRetry((value) => value + 1);
      else setQuery(normalized);
    },
  };
}

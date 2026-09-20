import {
  createContext,
  useContext,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useLocation } from "react-router-dom";
import { api } from "../api";

const StartupDataContext = createContext(api);
export const useStartupData = () => useContext(StartupDataContext);

function startupPaths(pathname, search) {
  if (pathname === "/") {
    return new URLSearchParams(search).get("filter") === "trash"
      ? ["/items", "/items?trash=true"]
      : ["/items"];
  }
  const match = /^\/items\/([^/]+)\/?$/.exec(pathname);
  if (!match) return [];
  try {
    return [`/items/${decodeURIComponent(match[1])}`];
  } catch {
    return [];
  }
}

function startupRequests() {
  const available = new Map();
  const pending = new Set();
  const discard = (keep = []) => {
    const allowed = new Set(keep);
    for (const [path, record] of available) {
      if (!allowed.has(path)) {
        available.delete(path);
        record.controller.abort();
      }
    }
    for (const record of pending) {
      if (!allowed.has(record.path)) {
        pending.delete(record);
        record.controller.abort();
      }
    }
  };
  return {
    start(paths) {
      for (const path of paths) {
        const record = { path, controller: new AbortController() };
        pending.add(record);
        record.promise = api(path, { signal: record.controller.signal }).then(
          (value) => {
            pending.delete(record);
            return value;
          },
          (error) => {
            pending.delete(record);
            throw error;
          },
        );
        // Preferences and route code may still be loading when a request fails.
        record.promise.catch(() => {});
        available.set(path, record);
      }
    },
    take(path) {
      const record = available.get(path);
      available.delete(path);
      return record ? record.promise : api(path);
    },
    discard,
  };
}

export function StartupDataProvider({ children }) {
  const location = useLocation();
  const initialRoute = useRef(location);
  const [requests] = useState(startupRequests);
  useLayoutEffect(() => {
    requests.start(
      startupPaths(initialRoute.current.pathname, initialRoute.current.search),
    );
    return () => requests.discard();
  }, [requests]);
  useLayoutEffect(() => {
    requests.discard(startupPaths(location.pathname, location.search));
  }, [requests, location.pathname, location.search]);
  return (
    <StartupDataContext.Provider value={requests.take}>
      {children}
    </StartupDataContext.Provider>
  );
}

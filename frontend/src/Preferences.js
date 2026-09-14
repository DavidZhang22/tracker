import { createContext, useContext, useEffect, useState } from "react";
import { api, patch } from "./api";
import { Notice } from "./Notice";

export const defaults = {
  link_sort: "auto",
  link_direction: "desc",
  library_sort: "recent",
  auto_read: true,
  refresh_mode: "light",
  source_method: "auto",
};
const PreferencesContext = createContext({ preferences: defaults });
export const usePreferences = () => useContext(PreferencesContext);

export function PreferencesProvider({ children }) {
  const [preferences, setPreferences] = useState(null);
  const [error, setError] = useState("");
  const [attempt, retry] = useState(0);
  useEffect(() => {
    let active = true;
    api("/settings")
      .then((saved) => active && setPreferences(saved))
      .catch((e) => active && setError(e.message));
    return () => {
      active = false;
    };
  }, [attempt]);
  if (!preferences)
    return (
      <div className="form-panel preferences-loading">
        {error ? (
          <>
            <Notice error>{error}</Notice>
            <button
              className="button"
              onClick={() => {
                setError("");
                retry((n) => n + 1);
              }}
            >
              Retry settings
            </button>
          </>
        ) : (
          <p role="status">Loading preferences…</p>
        )}
      </div>
    );
  return (
    <PreferencesContext.Provider
      value={{
        preferences,
        savePreferences: async (values) => {
          const saved = await patch("/settings", values);
          setPreferences(saved);
          return saved;
        },
      }}
    >
      {children}
    </PreferencesContext.Provider>
  );
}

export const linkSortOptions = [
  ["auto", "Automatic order"],
  ["number", "Chapter / episode number"],
  ["date", "Content date"],
  ["source", "Source order"],
  ["discovered", "Date discovered"],
  ["title", "Title"],
];

export function orderedPreview(entries, preferences, orderHint = "") {
  let sort = preferences.link_sort;
  if (sort === "auto")
    sort =
      orderHint === "source"
        ? "source"
        : entries.every((e) => e.number != null)
          ? "number"
          : entries.every((e) => e.published_at)
            ? "date"
            : "source";
  const direction = preferences.link_direction === "desc" ? -1 : 1;
  const value = (e, index) =>
    sort === "number"
      ? e.number
      : sort === "date"
        ? e.published_at
          ? Date.parse(e.published_at)
          : null
        : sort === "title"
          ? e.title.toLowerCase()
          : index;
  return entries
    .map((entry, index) => ({ entry, index }))
    .sort((a, b) => {
      const x = value(a.entry, a.index),
        y = value(b.entry, b.index);
      const missingX = x == null || Number.isNaN(x),
        missingY = y == null || Number.isNaN(y);
      if (missingX !== missingY) return missingX ? 1 : -1;
      return direction * ((x < y ? -1 : x > y ? 1 : 0) || a.index - b.index);
    });
}

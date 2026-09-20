import { mediaTypes } from "./media";

export const libraryFilters = [
  "all",
  "new",
  "unread",
  "favorites",
  "ignored",
  "trash",
];
const kinds = ["all", ...mediaTypes.map(([value]) => value)];
const sorts = ["recent", "unread", "title"];
const modes = ["semantic", "local"];
const choice = (value, options, fallback) =>
  options.includes(value) ? value : fallback;

export function libraryView(params, defaultSort = "recent") {
  return {
    query: (params.get("q") || "").slice(0, 200),
    filter: choice(params.get("filter"), libraryFilters, "all"),
    kind: choice(params.get("kind"), kinds, "all"),
    sort: choice(
      params.get("sort"),
      sorts,
      choice(defaultSort, sorts, "recent"),
    ),
    search_mode: choice(params.get("mode"), modes, "semantic"),
  };
}

export function libraryViewParams(current, changes, defaultSort = "recent") {
  const value = { ...libraryView(current, defaultSort), ...changes };
  const next = new URLSearchParams(current);
  for (const [key, field, fallback] of [
    ["q", "query", ""],
    ["filter", "filter", "all"],
    ["kind", "kind", "all"],
    ["sort", "sort", choice(defaultSort, sorts, "recent")],
    ["mode", "search_mode", "semantic"],
  ]) {
    if (value[field] === fallback) next.delete(key);
    else next.set(key, value[field]);
  }
  return next;
}

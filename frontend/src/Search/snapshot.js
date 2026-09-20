const FIELDS = [
  "id",
  "title",
  "url",
  "source_name",
  "kind",
  "description",
  "source_summary",
  "deleted",
];
const sameTags = (left = [], right = []) =>
  left === right ||
  (left.length === right.length && left.every((tag, i) => tag === right[i]));

export function searchSnapshot(items, previous) {
  if (
    previous?.length === items.length &&
    items.every(
      (item, i) =>
        FIELDS.every((field) => item[field] === previous[i][field]) &&
        sameTags(item.search_tags, previous[i].search_tags),
    )
  )
    return previous;
  return items.map((item) => ({
    ...Object.fromEntries(FIELDS.map((field) => [field, item[field]])),
    search_tags: item.search_tags,
  }));
}

export function workerSearchRows(items) {
  return items.map(
    ({
      id,
      title,
      url,
      source_name,
      search_tags,
      description,
      source_summary,
    }) => ({
      id,
      title,
      url,
      source_name,
      search_tags,
      description: String(description || source_summary || "")
        .normalize("NFKC")
        .toLowerCase()
        .slice(0, 4000),
    }),
  );
}

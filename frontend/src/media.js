export const mediaTypes = [
  ["comic", "Manga & comics"],
  ["novel", "Books & novels"],
  ["blog", "Blogs & articles"],
  ["youtube", "YouTube"],
  ["video", "Video & TV"],
  ["podcast", "Podcasts"],
  ["music", "Music"],
  ["events", "Events"],
  ["jobs", "Jobs"],
  ["software", "Software releases"],
  ["course", "Courses & tutorials"],
  ["research", "Research"],
  ["website", "Website"],
];
export const mediaLabel = (kind) =>
  mediaTypes.find(([value]) => value === kind)?.[1] || "Website";
const fold = (text) =>
  String(text || "")
    .normalize("NFKC")
    .toLowerCase();
const words = (text) =>
  new Set(
    fold(text)
      .slice(0, 4000)
      .match(/[\p{L}\p{N}]{2,}/gu) || [],
  );

// Rebuild sparse TF-IDF vectors only when library metadata changes.
export function librarySearchIndex(items) {
  const frequency = new Map();
  const documents = items.map((item) => {
    const exact = fold(`${item.title} ${item.url} ${item.source_name || ""}`);
    const weights = new Map();
    for (const [text, weight] of [
      [exact, 1],
      [item.title, 3],
      [(item.search_tags || []).join(" "), 2],
    ]) {
      for (const word of words(text))
        weights.set(word, Math.max(weight, weights.get(word) || 0));
    }
    for (const word of weights.keys())
      frequency.set(word, (frequency.get(word) || 0) + 1);
    return { id: item.id, exact, weights };
  });
  const idf = new Map(
    [...frequency].map(([word, count]) => [
      word,
      1 + Math.log((items.length + 1) / (count + 1)),
    ]),
  );
  const vectors = documents.map(({ weights, ...doc }) => {
    const vector = new Map(
      [...weights].map(([word, weight]) => [word, weight * idf.get(word)]),
    );
    return { ...doc, vector, norm: Math.hypot(...vector.values()) || 1 };
  });
  return (query) => {
    const text = fold(query).trim(),
      terms = [...words(text)];
    const queryNorm =
      Math.hypot(...terms.map((word) => idf.get(word) || 1)) || 1;
    return new Map(
      vectors.map(({ id, exact, vector, norm }) => {
        const complete =
          terms.length && terms.every((word) => vector.has(word));
        const cosine = complete
          ? terms.reduce(
              (sum, word) => sum + vector.get(word) * idf.get(word),
              0,
            ) /
            (norm * queryNorm)
          : 0;
        return [id, !text ? 1 : (exact.includes(text) ? 2 : 0) + cosine];
      }),
    );
  };
}

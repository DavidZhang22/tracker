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

export const normalizeLibraryQuery = (query) =>
  fold(query).trim().replace(/\s+/g, " ").slice(0, 200);

const near = (a, b) => {
  if (Math.min(a.length, b.length) < 4 || Math.abs(a.length - b.length) > 1)
    return false;
  let i = 0;
  while (i < a.length && a[i] === b[i]) i++;
  if (a.length === b.length)
    return (
      a.slice(i + 1) === b.slice(i + 1) ||
      (a[i] === b[i + 1] &&
        a[i + 1] === b[i] &&
        a.slice(i + 2) === b.slice(i + 2))
    );
  return a.length > b.length
    ? a.slice(i + 1) === b.slice(i)
    : a.slice(i) === b.slice(i + 1);
};

// The posting lists avoid visiting every token in every item for each query.
export function librarySearchIndex(items) {
  const vocabulary = new Map();
  const documents = items.map((item) => {
    const exact = fold(
      `${item.title || ""} ${item.url || ""} ${item.source_name || ""}`,
    );
    const weights = new Map();
    for (const [text, weight] of [
      [exact, 1],
      [item.title, 3],
      [(item.search_tags || []).join(" "), 2],
      [item.description || item.source_summary, 1],
    ]) {
      for (const word of words(text))
        weights.set(word, Math.max(weight, weights.get(word) || 0));
    }
    for (const word of weights.keys()) {
      const token = vocabulary.get(word);
      if (token) token.count++;
      else
        vocabulary.set(word, {
          count: 1,
          order: vocabulary.size,
          postings: [],
        });
    }
    return { id: item.id, exact, weights };
  });
  const lengths = new Map();
  for (const [word, token] of vocabulary) {
    token.idf = 1 + Math.log((items.length + 1) / (token.count + 1));
    if (!lengths.has(word.length)) lengths.set(word.length, []);
    lengths.get(word.length).push(word);
  }
  const vectors = documents.map(({ weights, ...doc }, index) => {
    const values = [];
    for (const [word, weight] of weights) {
      const token = vocabulary.get(word);
      const value = weight * token.idf;
      token.postings.push(index, value);
      values.push(value);
    }
    return { ...doc, norm: Math.hypot(...values) || 1 };
  });
  for (const token of vocabulary.values())
    token.postings = new Float64Array(token.postings);
  const cache = new Map();
  return (query) => {
    const text = normalizeLibraryQuery(query),
      terms = [...words(text)];
    if (cache.has(text)) return cache.get(text);
    const queryNorm =
      Math.hypot(...terms.map((word) => vocabulary.get(word)?.idf || 1)) || 1;
    const sums = new Float64Array(vectors.length);
    const matches = new Uint16Array(vectors.length);
    for (const term of terms) {
      const alternatives = vocabulary.has(term)
        ? [term]
        : [term.length - 1, term.length, term.length + 1]
            .flatMap((length) => lengths.get(length) || [])
            .filter((word) => near(term, word))
            .sort((a, b) => vocabulary.get(a).order - vocabulary.get(b).order);
      const seen = new Uint8Array(vectors.length);
      for (const word of alternatives) {
        const token = vocabulary.get(word);
        for (let offset = 0; offset < token.postings.length; offset += 2) {
          const index = token.postings[offset],
            value = token.postings[offset + 1];
          if (seen[index]) continue;
          seen[index] = 1;
          matches[index]++;
          sums[index] += value * token.idf;
        }
      }
    }
    const scores = new Map(
      vectors.map(({ id, exact, norm }, index) => {
        const cosine =
          terms.length && matches[index] === terms.length
            ? sums[index] / (norm * queryNorm)
            : 0;
        return [id, !text ? 1 : (exact.includes(text) ? 2 : 0) + cosine];
      }),
    );
    cache.set(text, scores);
    if (cache.size > 24) cache.delete(cache.keys().next().value);
    return scores;
  };
}

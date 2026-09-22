import { searchSnapshot, workerSearchRows } from "../Search/snapshot";
import { librarySearchIndex } from "../Search/searchIndex";

const item = {
  id: "one",
  title: "A title",
  url: "https://example.test",
  description: "Scuba comedy",
  source_summary: "Long original source context",
  search_tags: ["manga", "comedy"],
  deleted: false,
};

test("metadata snapshots share strings and survive reading changes without serializing source context", () => {
  const initial = searchSnapshot([item]);
  expect(initial[0].source_summary).toBe(item.source_summary);
  expect(initial[0]).not.toBe(item);
  const updated = searchSnapshot(
    [
      {
        ...item,
        favorite: true,
        unread_count: 2,
        search_tags: [...item.search_tags],
      },
    ],
    initial,
  );
  expect(updated).toBe(initial);
  expect(
    searchSnapshot(
      [{ ...item, source_summary: "New source context" }],
      initial,
    ),
  ).not.toBe(initial);
  expect(
    searchSnapshot([{ ...item, search_tags: ["fiction"] }], initial),
  ).not.toBe(initial);
  expect(searchSnapshot([{ ...item, deleted: true }], initial)).not.toBe(
    initial,
  );
  expect(searchSnapshot([], initial)).toEqual([]);
});

test("worker rows omit unused fields and bound normalized context without changing results", () => {
  const rows = [
    { ...item, favorite: true },
    {
      ...item,
      id: "two",
      description: "",
      source_summary: "ＰＯＤＣＡＳＴ " + "comedy ".repeat(1000),
    },
  ];
  const compact = workerSearchRows(rows);
  expect(compact[0]).not.toHaveProperty("source_summary");
  expect(compact[0]).not.toHaveProperty("favorite");
  expect(compact[0]).not.toHaveProperty("deleted");
  expect(compact[1].description).toHaveLength(4000);
  for (const query of ["podcast", "scuba", "comdey", "a title", "not present"])
    expect(librarySearchIndex(compact)(query)).toEqual(
      librarySearchIndex(rows)(query),
    );
});

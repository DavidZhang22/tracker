import { librarySearchIndex, mediaLabel } from "../media";

const items = [
  {
    id: "manga",
    title: "Grand Blue Dreaming",
    url: "https://example.org/series",
    search_tags: [
      "manga comics",
      "comedy",
      "college university",
      "diving scuba",
    ],
  },
  {
    id: "blog",
    title: "Scuba safety",
    url: "https://blog.example/",
    search_tags: ["blogs articles posts", "diving scuba"],
  },
  {
    id: "import",
    title: "My saved links",
    url: "import:jobs",
    source_name: "Career opportunities.xlsx",
    search_tags: ["jobs careers employment"],
  },
];

test("hidden descriptors support vector search and require all query concepts", () => {
  const search = librarySearchIndex(items);
  expect(search("college comedy").get("manga")).toBeGreaterThan(0);
  expect(search("college comedy").get("blog")).toBe(0);
  expect(search("scuba").get("blog")).toBeGreaterThan(
    search("scuba").get("manga"),
  );
  expect(search("scuba employment").get("manga")).toBe(0);
  expect(search("ｅｍｐｌｏｙｍｅｎｔ").get("import")).toBeGreaterThan(0);
  expect([...search(" ").values()]).toEqual([1, 1, 1]);
});

test("search keeps exact filenames, punctuation and legacy items working", () => {
  const search = librarySearchIndex(items);
  expect(search("opportunities.xlsx").get("import")).toBeGreaterThan(0);
  expect(
    librarySearchIndex([
      { id: "old", title: "An old source", url: "https://old.example/" },
    ])("old").get("old"),
  ).toBeGreaterThan(0);
  expect(search("zzzzzzz").get("manga")).toBe(0);
  expect(mediaLabel("comic")).toBe("Manga & comics");
});

test("repeated queries reuse bounded result maps and normalize spacing", () => {
  const search = librarySearchIndex(items);
  expect(search(" college   comedy ")).toBe(search("COLLEGE COMEDY"));
  const saved = search("scuba");
  for (let i = 0; i < 30; i++) search(`unmatched ${i}`);
  expect(search("scuba")).not.toBe(saved);
  expect(search("scuba")).toEqual(saved);
});

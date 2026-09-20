import {
  createLinkCache,
  sameResult,
  LINK_CACHE_BYTES,
} from "../Hooks/linkCache";

test("link result equality preserves repeated data and detects nested edits", () => {
  const page = {
    total: 2,
    links: [
      {
        id: "one",
        read: false,
        members: [
          { title: "Member", context: "Company: Example", number: null },
        ],
      },
    ],
  };
  expect(sameResult(page, JSON.parse(JSON.stringify(page)))).toBe(true);
  expect(sameResult(page, { ...page, total: 3 })).toBe(false);
  expect(
    sameResult(page, { ...page, links: [{ ...page.links[0], read: true }] }),
  ).toBe(false);
  expect(sameResult({ a: undefined }, { b: undefined })).toBe(false);
  expect(sameResult([], {})).toBe(false);
});

test("page caching is limited by page count and estimated retained bytes", () => {
  const cache = createLinkCache();
  for (let i = 0; i < 9; i++)
    cache.set(String(i), { links: [{ title: `Link ${i}` }] }, i);
  expect(cache.get("0")).toBeUndefined();
  expect(cache.get("8").time).toBe(8);
  cache.clear();
  const big = { links: [{ context: "x".repeat(LINK_CACHE_BYTES / 4) }] };
  cache.set("one", big, 1);
  cache.set("two", big, 2);
  expect(cache.get("one")).toBeUndefined();
  expect(cache.get("two").data).toBe(big);
  cache.set("huge", { links: [{ context: "x".repeat(LINK_CACHE_BYTES) }] }, 3);
  expect(cache.get("huge")).toBeUndefined();
  expect(cache.get("two")).toBeDefined();
  cache.clear();
  expect(cache.get("two")).toBeUndefined();
});

test("replacing a cached path accounts for the new payload only", () => {
  const cache = createLinkCache();
  for (let i = 0; i < 20; i++)
    cache.set(
      "same",
      { links: [{ context: "x".repeat(40000), read: i % 2 === 0 }] },
      i,
    );
  expect(cache.get("same").time).toBe(19);
  cache.set("next", { total: 0, links: [] }, 21);
  expect(cache.get("same")).toBeDefined();
});

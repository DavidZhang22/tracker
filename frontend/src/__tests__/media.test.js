import { mediaLabel } from "../media";

test("media labels use the display name", () => {
  expect(mediaLabel("comic")).toBe("Manga & comics");
});

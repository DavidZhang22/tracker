import { vi } from "vitest";
import { pageLoaders, preloadPage } from "../pageLoaders";

afterEach(() => vi.restoreAllMocks());

test("prefetch downloads only the matching private route", async () => {
  const loads = Object.fromEntries(
    Object.keys(pageLoaders).map((name) => [
      name,
      vi.spyOn(pageLoaders, name).mockResolvedValue({}),
    ]),
  );
  await preloadPage("/items/one");
  expect(loads.item).toHaveBeenCalledOnce();
  expect(loads.add).not.toHaveBeenCalled();
  expect(loads.account).not.toHaveBeenCalled();
  expect(loads.settings).not.toHaveBeenCalled();
  await preloadPage("/settings/");
  expect(loads.settings).toHaveBeenCalledOnce();
  await preloadPage("/");
  await preloadPage("/privacy");
  await preloadPage("/items/one/unknown");
  expect(loads.item).toHaveBeenCalledOnce();
  expect(loads.settings).toHaveBeenCalledOnce();
});

test("a failed speculative code download is handled by normal route rendering", async () => {
  vi.spyOn(pageLoaders, "add").mockRejectedValue(new Error("Offline"));
  await expect(preloadPage("/add")).resolves.toBeUndefined();
});

/* Run against a production build with fixture APIs; never touches a real library.
   PLAYWRIGHT_MODULE can point to an existing Playwright installation.
   npm run build && node scripts/mobile-check.cjs */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const root = path.resolve(__dirname, "../build");
const output = path.resolve(__dirname, "../.mobile-check");
fs.mkdirSync(output, { recursive: true });

function fixtureApi() {
  let signedIn = true;
  const calls = [];
  const links = Array.from({ length: 65 }, (_, index) => ({
    id: `link${index + 1}`,
    number: index + 1,
    title:
      index === 0
        ? "Chapter 1: A longer title that should remain comfortable to read on a small screen"
        : `Chapter ${index + 1}: An unexpected discovery`,
    url: `https://fiction.example/chapter/${index + 1}`,
    read: false,
    favorite: false,
    ignored: false,
    is_new: index < 3,
    published_at: index % 3 ? null : "2026-09-09T13:30:00Z",
    date_kind: "published",
    date_source: "Series chapter list",
    date_precision: "time",
  }));
  const item = {
    id: "one",
    title: "Climbing the Tower with Time-Stop Ability",
    kind: "novel",
    url: "https://fiction.example/series/a-story",
    favorite: false,
    ignored: false,
    auto_read: true,
    error: "Source is temporarily unavailable.",
    warnings: [
      "Some dates are unavailable. Source order is used where needed.",
    ],
    methods: ["page", "link classifier"],
    pages_scanned: 2,
    created_at: "2026-09-08",
    last_checked_at: "2026-09-09T13:30:00Z",
    selector: "",
    include_path: "",
    dated_count: 22,
    total_count: 65,
    ignored_count: 0,
    read_count: 0,
    unread_count: 65,
    new_count: 3,
    latest_link: links[64],
  };
  const items = [
    item,
    {
      ...item,
      id: "two",
      title:
        "Stories about the natural world, science, and the places we visit",
      kind: "blog",
      new_count: 0,
    },
    {
      ...item,
      id: "three",
      title: "音楽と物語 — a collection across languages",
      kind: "youtube",
      new_count: 0,
    },
  ];
  return {
    calls,
    links,
    item,
    signOut: () => {
      signedIn = false;
    },
    async route(route) {
      const request = route.request();
      const url = new URL(request.url());
      const routePath = url.pathname.replace(/^\/api/, "");
      const method = request.method();
      const body = request.postDataJSON();
      calls.push({ path: routePath, method, body });
      let data;
      let status = 200;
      if (routePath === "/auth/status")
        data = {
          required: true,
          registration: true,
          user: signedIn ? { username: "mobile_reader" } : null,
        };
      else if (routePath === "/auth/logout") {
        signedIn = false;
        data = {};
      } else if (routePath === "/auth/login") {
        signedIn = true;
        data = { user: { username: "mobile_reader" } };
      } else if (routePath === "/auth/password") data = {};
      else if (routePath === "/items" && method === "GET")
        data = items.filter((i) =>
          url.searchParams.get("trash") ? i.deleted : !i.deleted,
        );
      else if (routePath === "/items/one" && method === "GET") data = item;
      else if (routePath === "/items/one" && method === "PATCH") {
        Object.assign(item, body);
        data = item;
      } else if (routePath === "/items/one/links") {
        const filter = url.searchParams.get("filter");
        const search = (url.searchParams.get("search") || "").toLowerCase();
        const matching = links.filter(
          (l) =>
            (filter === "favorites"
              ? l.favorite
              : filter === "new"
                ? l.is_new
                : filter === "trash"
                  ? l.deleted
                  : filter === "ignored"
                    ? l.ignored
                    : !l.deleted && !l.ignored) &&
            l.title.toLowerCase().includes(search),
        );
        const offset = Number(url.searchParams.get("offset") || 0);
        data = {
          links: matching.slice(offset, offset + 50),
          total: matching.length,
          sort_used:
            url.searchParams.get("sort") === "auto"
              ? "number"
              : url.searchParams.get("sort"),
        };
      } else if (/^\/links\/link\d+$/.test(routePath) && method === "PATCH") {
        data = links.find((l) => l.id === routePath.split("/").pop());
        Object.assign(data, body);
      } else if (["/links/bulk", "/items/bulk"].includes(routePath)) {
        const records = routePath.startsWith("/links") ? links : items;
        const updates = {
          favorite: { favorite: true },
          unfavorite: { favorite: false },
          ignore: { ignored: true },
          unignore: { ignored: false },
          delete: { deleted: true },
          restore: { deleted: false },
          read: { read: true },
          unread: { read: false },
        };
        records
          .filter((r) => body.ids.includes(r.id))
          .forEach((r) => Object.assign(r, updates[body.action]));
        data = { updated: body.ids.length };
      } else if (routePath === "/scans")
        data = {
          scan_id: "fixture-scan",
          title: item.title,
          entries: links.slice(0, 26),
          kind: "novel",
          pages_scanned: 1,
          warnings: [],
        };
      else if (routePath === "/items" && method === "POST") data = item;
      else if (routePath.endsWith("/refresh") || routePath === "/refresh")
        data = { ok: true, checked: 3, new_count: 0 };
      else {
        status = 404;
        data = { detail: `Unmocked API: ${routePath}` };
      }
      await route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(data),
      });
    },
  };
}

async function withinViewport(page, label) {
  const layout = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  assert(
    layout.scrollWidth <= layout.width + 1,
    `${label}: page overflows (${JSON.stringify(layout)})`,
  );
}

async function check() {
  assert(
    fs.existsSync(path.join(root, "index.html")),
    "Build the frontend first",
  );
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, "http://localhost");
    let target = path.resolve(root, `.${decodeURIComponent(url.pathname)}`);
    if (
      !target.startsWith(root + path.sep) ||
      !fs.existsSync(target) ||
      fs.statSync(target).isDirectory()
    )
      target = path.join(root, "index.html");
    response.setHeader(
      "Content-Type",
      {
        ".js": "text/javascript",
        ".css": "text/css",
        ".svg": "image/svg+xml",
        ".json": "application/json",
      }[path.extname(target)] || "text/html",
    );
    fs.createReadStream(target).pipe(response);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.BROWSER_CHANNEL
      ? { channel: process.env.BROWSER_CHANNEL }
      : {}),
  });
  const report = [];
  try {
    for (const [width, height, touch] of [
      [320, 740, true],
      [390, 844, true],
      [430, 932, true],
      [768, 1024, true],
      [844, 390, true],
      [1024, 768, false],
      [1440, 1000, false],
    ]) {
      const context = await browser.newContext({
        viewport: { width, height },
        isMobile: touch,
        hasTouch: touch,
        deviceScaleFactor: 1,
      });
      // No requests to source sites, even when testing links that open a new tab.
      await context.route("**/*", (route) =>
        new URL(route.request().url()).origin === base
          ? route.continue()
          : route.fulfill({
              contentType: "text/html",
              body: "Fixture destination",
            }),
      );
      const page = await context.newPage();
      const api = fixtureApi();
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.route("**/api/**", (route) => api.route(route));
      await page.goto(base);
      await page
        .getByRole("link", { name: api.item.title, exact: true })
        .waitFor();
      await withinViewport(page, `${width} library`);
      const note = page.locator(".row-scan-note").first();
      await note.locator("summary").click();
      assert(await note.getByText(/Source is temporarily unavailable/).isVisible());
      await note.getByRole("button", { name: /Dismiss scan notes/ }).click();
      assert(
        !(await page
          .getByRole("button", {
            name: `Dismiss scan notes for ${api.item.title}`,
            exact: true,
          })
          .count()),
      );
      const latest = page.getByRole("link", {
        name: `Latest entry for ${api.item.title}: ${api.links[64].title}`,
        exact: true,
      });
      const popupPromise = context.waitForEvent("page");
      const latestSaved = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/links/link65") &&
          response.request().method() === "PATCH",
      );
      await latest.click();
      const popup = await popupPromise;
      await popup.close();
      await latestSaved;
      assert(
        api.calls.some(
          (c) => c.path === "/links/link65" && c.body?.read === true,
        ),
      );
      await page.screenshot({
        path: path.join(output, `library-${width}.png`),
      });
      const mobile = touch && width <= 1000;
      if (mobile) {
        assert(
          !(await page.locator(".item-row .row-select").first().isVisible()),
        );
        await page.getByRole("button", { name: "Menu", exact: true }).tap();
        await page.screenshot({
          path: path.join(output, `navigation-${width}.png`),
        });
        await page
          .getByRole("link", { name: "mobile_reader · Account" })
          .click();
        await page
          .getByRole("heading", { name: "Account", exact: true })
          .waitFor();
        assert(
          await page
            .getByRole("button", { name: "Menu", exact: true })
            .isVisible(),
        );
        await withinViewport(page, `${width} account`);
        await page.getByRole("button", { name: "Menu", exact: true }).click();
        await page.keyboard.press("Escape");
        assert(
          await page
            .getByRole("button", { name: "Menu", exact: true })
            .evaluate((el) => el === document.activeElement),
        );
        await page
          .getByRole("link", { name: /Library/ })
          .first()
          .click();
        await page.getByRole("button", { name: "Filter", exact: true }).click();
        await page.getByLabel("Sort items").selectOption("title");
        await page
          .getByRole("button", { name: "Select items", exact: true })
          .click();
        await page
          .getByLabel(`Select ${api.item.title}`, { exact: true })
          .check();
        await page
          .getByRole("button", { name: "Favorite", exact: true })
          .click();
        assert(
          api.calls.some(
            (c) => c.path === "/items/bulk" && c.body.action === "favorite",
          ),
        );
        await page
          .getByRole("button", {
            name: `Actions for ${api.item.title}`,
            exact: true,
          })
          .click();
        const menu = page.getByRole("menu");
        await menu.waitFor();
        const bounds = await menu.boundingBox();
        assert(
          bounds.x >= 0 &&
            bounds.y >= 0 &&
            bounds.x + bounds.width <= width + 1 &&
            bounds.y + bounds.height <= height + 1,
          "Menu outside viewport",
        );
        await page.keyboard.press("Escape");
      }
      await page
        .getByRole("link", { name: api.item.title, exact: true })
        .click();
      await page.locator(".entry-row").first().waitFor();
      await withinViewport(page, `${width} item`);
      await page.screenshot({ path: path.join(output, `item-${width}.png`) });
      const date = page.locator(".entry-date details").first();
      assert(
        !(await date.locator("summary").innerText()).includes("Published"),
      );
      await date.locator("summary").click();
      assert(await date.locator(".date-origin").isVisible());
      await withinViewport(page, `${width} expanded date`);
      await page.locator(".entry-row").first().scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(output, `links-${width}.png`) });
      if (mobile) {
        const first = page.locator(".entry-row").first();
        for (const button of [
          first.locator(".read-status"),
          first.locator("[data-row-menu]"),
        ]) {
          const box = await button.boundingBox();
          assert(box.width >= 44 && box.height >= 44, "Small touch target");
        }
        await first.locator(".read-status").tap();
        assert(
          api.calls.some(
            (c) => c.path === "/links/link1" && c.body?.read === true,
          ),
        );
        await first.locator("[data-row-menu]").tap();
        await page
          .getByRole("menuitem", { name: "Mark unread", exact: true })
          .tap();
        await page.getByRole("menu").waitFor({ state: "hidden" });
        await page.getByRole("button", { name: "Sort", exact: true }).click();
        await page.getByLabel("Order links by").selectOption("date");
        await page.getByLabel("Order direction").selectOption("desc");
        await page
          .getByRole("button", { name: "Select links", exact: true })
          .click();
        await page
          .getByLabel(`Select link: ${api.links[0].title}`, { exact: true })
          .check();
        const checkboxTarget = await first.locator(".row-select").boundingBox();
        assert(checkboxTarget.width >= 44 && checkboxTarget.height >= 44);
        await page.locator(".entry-row").nth(8).scrollIntoViewIfNeeded();
        const bulkBar = await page.locator(".selection-bar").boundingBox();
        assert(
          bulkBar.y >= -1 && bulkBar.y < 50,
          `Bulk actions not sticky: ${bulkBar.y}`,
        );
        await withinViewport(page, `${width} bulk selection`);
        await page.screenshot({ path: path.join(output, `bulk-${width}.png`) });
        await page
          .getByRole("button", { name: "Favorite", exact: true })
          .click();
        assert(
          api.calls.some(
            (c) => c.path === "/links/bulk" && c.body.action === "favorite",
          ),
        );
      }
      await page.getByRole("button", { name: "Next", exact: true }).click();
      await page.getByText(/51–65 of 65/).waitFor();
      await withinViewport(page, `${width} pagination`);
      await page.goto(base + "/add");
      await page
        .getByLabel("Source URL")
        .fill("https://fiction.example/series/a-story");
      await page
        .getByRole("button", { name: "Scan links", exact: true })
        .click();
      await page.getByText("26 links found").waitFor();
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      const saveBounds = await page
        .getByRole("button", { name: "Add to library", exact: true })
        .boundingBox();
      assert(
        saveBounds.y >= 0 && saveBounds.y + saveBounds.height <= height,
        "Save button must stay in view after scrolling",
      );

      await withinViewport(page, `${width} add preview`);
      await page.locator(".scan-results").scrollIntoViewIfNeeded();
      await page.screenshot({
        path: path.join(output, `preview-${width}.png`),
      });
      await page.getByRole("button", { name: "Next", exact: true }).click();
      await page.getByText("2 / 2", { exact: true }).waitFor();
      await page
        .getByLabel("Item name")
        .fill(
          "A collection with a very long title " + "long-title-".repeat(12),
        );
      await withinViewport(page, `${width} long form`);
      api.signOut();
      await page.goto(base + "/account");
      await page
        .getByRole("heading", { name: "Sign in", exact: true })
        .waitFor();
      await withinViewport(page, `${width} sign in`);
      const inputSize = await page
        .getByLabel("Username")
        .evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
      if (mobile)
        assert(inputSize >= 16, "Small input can trigger mobile zoom");
      await page.screenshot({ path: path.join(output, `signin-${width}.png`) });
      assert.deepEqual(errors, [], "Browser errors");
      report.push({ width, height, touch, result: "passed" });
      console.log(`${width}x${height}: passed`);
      await context.close();
    }
    fs.writeFileSync(
      path.join(output, "report.json"),
      JSON.stringify(report, null, 2),
    );
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
}
check().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

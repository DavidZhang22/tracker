# Hydrated listings and JavaScript pagination

September 16, 2026. No new model weights, service, or database are required.

The parser now recognizes dated record arrays inside JSON hydration payloads,
including nested URLs such as `action.payload.url`. At least two records must
match visible titles and URLs before that shape is trusted. Image/product
metadata is excluded from path inference. Invalid records, private literal
destinations and executable URLs are rejected. This uses the existing classifier
to establish the visible records; JavaScript is never evaluated by the parser.

If a substantial hidden collection is recovered, normal scans use it directly.
The result remains partial when pagination exists. Browser mode or Full Refresh
can check further. Otherwise, automatic scans recognize Show more, Load more,
Next page, and Next controls in pagination containers even when initial links
already exist. Prose mentioning those words does not start the browser.

The existing networkless Chromium worker now follows up to 20 bounded click or
scroll steps. It retains replaced/virtualized rows, fingerprints links rather
than unrelated changing text, and stops after two unchanged attempts. Explicit
Next anchors can authorize only same-origin page/cursor coordinates or numbered
page paths. Forms, carousels, disabled buttons, arbitrary article navigation,
private addresses, non-GET requests and credentials remain blocked. Common
telemetry resources are also skipped. Limits remain 40 resources, 50 seconds,
and 5 MB total snapshots, within separate 1-GiB app/browser containers. The
five-minute shared source cooldown is unchanged. Existing validated JSON API
learning continues to avoid repeat browser work when a supported API is observed.

## TFT observation

The [TFT news page](https://teamfighttactics.leagueoflegends.com/en-us/news/)
initially showed 12 records but embedded 200 dated records. The old parser found
12; the new normal scan returned all **200**, with all **200 dates**, using **one
listing response and no article requests or browser calls**. It also retains
the publisher's video and external news links. A captured-page end-to-end local
scan took approximately 0.27 seconds, excluding network time.

A real sandboxed Chromium check exercised the page's Show more control. It did
not expose news beyond the embedded collection. Broader archive metadata in the
payload is not proof that this page exposes the entire archive. The application
does not guess API hosts/offsets or label this result complete. Public HTML and
scripts were reused as recorded fixtures during repeated debugging; raw captures
are not committed.

## Verification and profiling

- Full backend suite: 760 tests. New tests cover nested records, dates, context,
  unsafe links, automatic fallback, hydration reuse, control discrimination,
  telemetry filtering and approved versus forbidden browser navigation.
- UI: 48 tests; production build passed. Removed the read-on-open defaults hint
  from the add-item review.
- Real network-disabled Chromium fixtures: appended lists and virtualized Next
  both recovered 12 dated entries across six windows; explicit Next links
  recovered six dated entries across three pages; unrelated changing text
  stopped after two steps. No article requests. Run
  `python ml/verify_pagination_runtime.py` against the sandbox socket.
- Existing `verify_browser_runtime.py` still recovered six entries, learned its
  API, then refreshed eight entries through four API requests without rendering.
- Final-image browser fixtures peaked at 189.2 MiB in the isolated browser
  container; this excludes the app container and is not a production-load estimate.
- Local parser profile, seven warm samples with the same cascade and Python
  inference: TFT 285 ms before / 238 ms after; Royal Road 413 / 355 ms; ordinary
  250-row listing 539 / 496 ms. The latter two returned identical URL/title/date
  hashes. These small local samples show no observed regression, not a claimed
  production speedup. Full browser interaction remains far slower than parsing
  already embedded records, which is why normal scans prefer that path.

Support remains bounded: authentication, access checks, unsupported controls and
archives larger than the limits can leave partial coverage. Browser interactions
do not change a source site's access policy or authorize protected APIs.

# Mobile layout checks

The phone layout also applies to touch screens up to 1000 CSS pixels wide, so
phone landscape uses the same navigation and controls. Larger desktop screens
keep the sidebar and inline filters. Inputs retain browser zoom, use a 16px
minimum font on mobile, and allow safe-area spacing around screen cutouts.

Build and run the fixture browser check:

```sh
npm run build
node scripts/mobile-check.cjs
```

The script needs Playwright with Chromium installed. `PLAYWRIGHT_MODULE` can
point to an existing Playwright package; `BROWSER_CHANNEL=msedge` or `chrome`
can use an installed browser. It starts a temporary loopback server and mocks
every API and external destination. It never accesses a real library or scans
source sites. Screenshots and its report go in ignored `.mobile-check/`.

Verified September 10, 2026 with headless Edge and touch emulation:

- 320×740, 390×844, 430×932, 768×1024, 844×390, 1024×768, 1440×1000.
- Library, item, scan preview, account, and sign-in have no page-wide horizontal
  overflow, including long titles and date disclosures.
- Navigation exposes account access, closes on navigation, and returns focus to
  Menu on Escape.
- Read and row menu actions work by tap; row checkboxes, read buttons, and menu
  buttons have touch targets at least 44×44 CSS pixels.
- Filters, date sorting, item and link bulk actions, sticky selection controls,
  and both kinds of pagination work.
- Latest-entry shortcuts honor automatic reading; failed checks can be expanded
  and dismissed. Publication labels and time-zone details stay inside date disclosures.
- All 31 frontend component tests pass, including selection cancellation and
  reset when changing lists.

Phone screenshots were visually reviewed. Physical iOS Safari and Android
devices have not been tested; touch emulation does not reproduce their software
keyboards, screen cutouts, or every browser behavior.

The Add to library action is sticky at the top of the scan page. Browser checks verify it stays in the viewport after scrolling to the bottom of a long preview.

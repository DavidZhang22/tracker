# Trackify frontend

React renders the library, item views, file/text imports and account controls. Use Node.js 24. The Vite development server binds to `127.0.0.1:3000` and proxies `/api` to FastAPI at `127.0.0.1:8000`; production serves the built files through the backend.

```sh
npm ci
npm start
```

Open http://127.0.0.1:3000 with the backend running on port 8000.

| Location | Purpose |
| --- | --- |
| `index.html` | Vite HTML entry point and public asset links |
| `src/index.jsx` | React entry point and global styles |
| `src/App.jsx` | Public routes, authentication and application providers |
| `src/Pages/` | Library, item, import, settings, privacy and suggestions pages |
| `src/Components/` | Navigation shell, icons, notices and reusable controls |
| `src/Auth/` | Account sessions, authentication forms and data controls |
| `src/Contexts/` | Saved preferences and their provider |
| `src/Hooks/` | Search result caching and source detection |
| `src/api.js` | HTTP requests and streamed refresh updates |
| `src/styles/` | Shared and responsive styles |
| `src/__tests__/` | Interaction and API regression tests |
| `scripts/` | Browser checks using fixture APIs |
| `public/` | App icon, manifest and downloadable CSV example |
| `vite.config.js` | Development proxy, production output and Vitest configuration |
| `eslint.config.js` | ESLint flat configuration |

```sh
npm run lint
npm test
npm run test:tooling
npm run build
```

Lint checks application JavaScript and JSX with ESLint's flat configuration. `npm test` runs the Vitest interaction and API regression suite once; `npm run test:watch` reruns relevant tests as files change. `npm run test:tooling` checks the Vite development proxy, incremental API streaming, SPA routes and development server access restrictions using temporary local servers.

Tests use two VM workers with a fresh context and DOM per file. Workers recycle above a 256 MB memory threshold to limit retained module caches; this is a recycling threshold, not a hard process memory cap.

Run backend tests before the production build, which replaces the static files used by backend route tests. Vite writes to `build/`, with generated JavaScript and CSS under `build/static/`; FastAPI and both Docker build paths use these locations. Files in `public/` are copied to the build root. The application is hosted at `/`, including lazy page imports and `/examples/links.csv`. See [mobile checks](MOBILE_QA.md) and the [project setup](../README.MD).

Run `npm audit` and `npm audit --omit=dev` when updating dependencies. Review advisories in both the build tools and browser runtime dependencies, then rerun lint, tests and the production build after changes. Keep the development server local and use the production build for hosting.

See the [visual and frontend performance review](UI_REVIEW.md) for responsive screenshots, loading measurements, bounded client caches, and the offline Chromium harness.

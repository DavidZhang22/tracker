# Trackify frontend

React renders the library, item views, file/text imports and account controls. The development server proxies `/api` to FastAPI at `127.0.0.1:8000`; production serves the built files through the backend.

```sh
npm ci
npm start
```

| Location | Purpose |
| --- | --- |
| `src/App.js` | Public routes, authentication and application providers |
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

```sh
npm run lint
npm test -- --watchAll=false --runInBand
npm run build
```

Lint checks application JavaScript; the test command verifies interaction behavior. Run backend tests before the production build, which replaces the static files used by backend route tests. See [mobile checks](MOBILE_QA.md) and the [project setup](../README.MD).

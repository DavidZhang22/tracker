import { lazy, Suspense, useEffect } from "react";
import {
  BrowserRouter,
  Routes,
  Route,
  Outlet,
  useLocation,
} from "react-router-dom";
import { AuthBoundary } from "./Auth/Auth";
import { PreferencesProvider } from "./Contexts/Preferences";
import { StartupDataProvider } from "./Contexts/StartupData";
import { preloadPage } from "./pageLoaders";
import PublicLayout from "./Components/PublicLayout";
import AppLoading from "./Components/AppLoading";
import PageBoundary from "./Components/PageBoundary";

const PrivacyPage = lazy(() => import("./Pages/PrivacyPage"));
const RecoveryPage = lazy(() => import("./Auth/RecoveryPage"));
const loadShell = () =>
  import("./Components/Shell").then(({ Shell }) => ({ default: Shell }));
const Shell = lazy(loadShell);

function Workspace() {
  const location = useLocation();
  useEffect(() => {
    loadShell().catch(() => {});
    preloadPage(location.pathname);
  }, [location.pathname]);
  return (
    <StartupDataProvider>
      <PreferencesProvider>
        <Suspense fallback={<AppLoading />}>
          <Shell />
        </Suspense>
      </PreferencesProvider>
    </StartupDataProvider>
  );
}
export default function App() {
  return (
    <BrowserRouter>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <div className="site-frame">
        <PageBoundary>
          <Suspense fallback={<AppLoading />}>
            <Routes>
              <Route
                element={
                  <PublicLayout>
                    <Outlet />
                  </PublicLayout>
                }
              >
                <Route path="/privacy" element={<PrivacyPage />} />
                <Route path="/terms" element={<PrivacyPage terms />} />
              </Route>
              <Route
                element={
                  <PublicLayout compact>
                    <Outlet />
                  </PublicLayout>
                }
              >
                <Route
                  path="/account/recover"
                  element={<RecoveryPage key="recover" />}
                />
                <Route
                  path="/account/verify-email"
                  element={<RecoveryPage key="verify" verify />}
                />
              </Route>
              <Route
                path="*"
                element={
                  <AuthBoundary>
                    <Workspace />
                  </AuthBoundary>
                }
              />
            </Routes>
          </Suspense>
        </PageBoundary>
      </div>
    </BrowserRouter>
  );
}

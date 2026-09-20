import { lazy, Suspense, useEffect } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { AuthBoundary } from "./Auth/Auth";
import { PreferencesProvider } from "./Contexts/Preferences";
import Footer from "./Components/Footer";
import AppLoading from "./Components/AppLoading";
import PageBoundary from "./Components/PageBoundary";

const PrivacyPage = lazy(() => import("./Pages/PrivacyPage"));
const RecoveryPage = lazy(() => import("./Auth/RecoveryPage"));
const loadShell = () =>
  import("./Components/Shell").then(({ Shell }) => ({ default: Shell }));
const Shell = lazy(loadShell);

function Workspace() {
  // Fetch the workspace code while account preferences are loading.
  useEffect(() => {
    loadShell().catch(() => {});
  }, []);
  return (
    <PreferencesProvider>
      <Suspense fallback={<AppLoading />}>
        <Shell />
      </Suspense>
    </PreferencesProvider>
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
              <Route path="/privacy" element={<PrivacyPage />} />
              <Route path="/terms" element={<PrivacyPage terms />} />
              <Route
                path="/account/recover"
                element={<RecoveryPage key="recover" />}
              />
              <Route
                path="/account/verify-email"
                element={<RecoveryPage key="verify" verify />}
              />
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
        <Footer />
      </div>
    </BrowserRouter>
  );
}

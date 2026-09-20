import { BrowserRouter, Routes, Route } from "react-router-dom";
import { AuthBoundary } from "./Auth/Auth";
import { PreferencesProvider } from "./Contexts/Preferences";
import PrivacyPage from "./Pages/PrivacyPage";
import RecoveryPage from "./Auth/RecoveryPage";
import { Shell } from "./Components/Shell";
import Footer from "./Components/Footer";
export default function App() {
  return (
    <BrowserRouter>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <div className="site-frame">
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
                <PreferencesProvider>
                  <Shell />
                </PreferencesProvider>
              </AuthBoundary>
            }
          />
        </Routes>
        <Footer />
      </div>
    </BrowserRouter>
  );
}

import { CollectionIcon } from "@heroicons/react/outline";
import { Icon } from "./Icons";
import "../styles/loading.css";
import Footer from "./Footer";

export default function AppLoading({ label = "Loading…", children }) {
  return (
    <div className="app-shell startup-shell">
      <aside className="sidebar" aria-hidden="true">
        <div className="brand">
          <span className="brand-mark">
            <Icon as={CollectionIcon} />
          </span>
          Trackify<span className="brand-dot">.</span>
        </div>
      </aside>
      <div className="workspace">
        <main id="main" tabIndex={-1}>
          <h1 className="startup-heading">Trackify</h1>
          {children || (
            <p role="status" className="startup-status">
              {label}
            </p>
          )}
          {!children && (
            <div className="startup-placeholders" aria-hidden="true">
              <div />
              <div />
              <div />
            </div>
          )}
        </main>
        <Footer />
      </div>
    </div>
  );
}

import { Link, useLocation } from "react-router-dom";
import "../styles/footer.css";

export default function Footer() {
  const { pathname, hash } = useLocation();
  return (
    <footer className="site-footer">
      <div className="footer-content">
        <Link className="footer-brand" to="/">
          Trackify
        </Link>
        <nav aria-label="Legal and support">
          <Link
            to="/privacy"
            aria-current={
              pathname === "/privacy" && hash !== "#contact"
                ? "page"
                : undefined
            }
          >
            Privacy
          </Link>
          <Link
            to="/terms"
            aria-current={pathname === "/terms" ? "page" : undefined}
          >
            Terms
          </Link>
          <Link
            to="/privacy#contact"
            aria-current={
              pathname === "/privacy" && hash === "#contact"
                ? "location"
                : undefined
            }
          >
            Contact
          </Link>
        </nav>
      </div>
    </footer>
  );
}

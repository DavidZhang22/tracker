import { Link } from "react-router-dom";
import "../styles/footer.css";

export default function Footer() {
  return (
    <footer className="site-footer page-footer">
      <div className="footer-content">
        <Link className="footer-brand" to="/">
          Trackify
        </Link>
        <nav aria-label="Legal and support">
          <Link to="/privacy">Privacy</Link>
          <Link to="/terms">Terms</Link>
          <Link to="/privacy#contact">Contact</Link>
        </nav>
      </div>
    </footer>
  );
}

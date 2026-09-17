import { Link } from "react-router-dom";

export default function Footer() {
  return (
    <footer className="site-footer">
      <Link className="footer-brand" to="/">
        Trackify
      </Link>
      <nav aria-label="Legal and support">
        <Link to="/privacy">Privacy and data rights</Link>
        <Link to="/terms">Terms</Link>
        <Link to="/privacy#contact">Contact</Link>
      </nav>
    </footer>
  );
}

import { Component } from "react";
import { useLocation } from "react-router-dom";
import "../styles/loading.css";

class PageErrorBoundary extends Component {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidUpdate(previous) {
    if (this.state.failed && previous.route !== this.props.route)
      this.setState({ failed: false });
  }

  render() {
    if (!this.state.failed) return this.props.children;
    const Container = this.props.embedded ? "section" : "main";
    return (
      <Container
        className="page-load-error"
        id={this.props.embedded ? undefined : "main"}
        role="alert"
        tabIndex={-1}
      >
        <h1>Page unavailable</h1>
        <p>Check your connection, then reload this page.</p>
        <button className="button" onClick={() => window.location.reload()}>
          Reload page
        </button>
      </Container>
    );
  }
}

export default function PageBoundary({ children, embedded = false }) {
  const location = useLocation();
  return (
    <PageErrorBoundary route={location.pathname} embedded={embedded}>
      {children}
    </PageErrorBoundary>
  );
}

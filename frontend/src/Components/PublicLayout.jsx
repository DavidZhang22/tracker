import Footer from "./Footer";

export default function PublicLayout({ children, compact = false }) {
  return (
    <div className={`public-layout${compact ? " public-layout--compact" : ""}`}>
      {children}
      <Footer />
    </div>
  );
}

import { Link } from "react-router-dom";

export default function Description({ item, preview = false }) {
  if (preview && !item.description) return null;
  return (
    <section className="item-description" aria-label="Description">
      <div className="description-heading">
        <h2>Description</h2>
        {!preview && !item.deleted && (
          <Link to={`/settings?item=${item.id}#item-settings`}>Edit</Link>
        )}
      </div>
      {item.description ? (
        <p>{item.description}</p>
      ) : (
        <p className="muted">No description available from this source.</p>
      )}
    </section>
  );
}

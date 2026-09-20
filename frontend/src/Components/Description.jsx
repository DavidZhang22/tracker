import { Link } from "react-router-dom";

export default function Description({ item, preview = false }) {
  if (preview && !item.description && !item.description_suppressed) return null;
  return (
    <section className="item-description" aria-label="Description">
      <div className="description-heading">
        <h2>Description</h2>
        {!preview && !item.deleted && (
          <Link
            className="text-button"
            to={`/settings?item=${item.id}#item-settings`}
          >
            Edit
          </Link>
        )}
      </div>
      {item.description ? (
        <p>{item.description}</p>
      ) : (
        <p className="muted">
          {item.description_suppressed
            ? "Automatic description hidden."
            : "No description available from this source."}
        </p>
      )}
    </section>
  );
}

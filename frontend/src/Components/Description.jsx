export default function Description({
  item,
  preview = false,
  onEdit,
  disabled = false,
}) {
  if (preview && !item.description && !item.description_suppressed) return null;
  return (
    <section className="item-description" aria-label="Description">
      <div className="description-heading">
        <h2>Description</h2>
        {!preview && !item.deleted && onEdit && (
          <button
            className="text-button"
            type="button"
            onClick={onEdit}
            disabled={disabled}
            aria-haspopup="dialog"
          >
            Edit
          </button>
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

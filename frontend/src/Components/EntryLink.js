export default function EntryLink({ entry, className, onClick, onAuxClick }) {
  if (!entry.url)
    return (
      <span
        className={className}
        title="This entry has no link on the source page."
      >
        {entry.title}
      </span>
    );
  return (
    <a
      className={className}
      href={entry.url}
      target="_blank"
      rel="noopener noreferrer"
      onClick={onClick}
      onAuxClick={onAuxClick}
    >
      {entry.title} <span aria-hidden="true">↗</span>
    </a>
  );
}

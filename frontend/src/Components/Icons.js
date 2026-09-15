import { BookOpenIcon, PlayIcon, RssIcon } from "@heroicons/react/outline";
export function Icon({ as: Component, ...props }) {
  return <Component className="icon" aria-hidden="true" {...props} />;
}
export function TypeIcon({ kind }) {
  return (
    <span className={`type-icon ${kind}`}>
      <Icon
        as={
          kind === "youtube"
            ? PlayIcon
            : kind === "blog"
              ? RssIcon
              : BookOpenIcon
        }
      />
    </span>
  );
}
export function IconButton({ icon, label, active, ...props }) {
  return (
    <button
      className={`icon-button ${active ? "active" : ""}`}
      title={label}
      aria-label={label}
      aria-pressed={active === undefined ? undefined : Boolean(active)}
      {...props}
    >
      <Icon as={icon} />
    </button>
  );
}

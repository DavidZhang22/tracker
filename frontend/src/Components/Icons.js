import {
  AcademicCapIcon,
  BookOpenIcon,
  BriefcaseIcon,
  CalendarIcon,
  CodeIcon,
  CollectionIcon,
  MicrophoneIcon,
  MusicNoteIcon,
  PlayIcon,
  RssIcon,
} from "@heroicons/react/outline";
const typeIcons = {
  comic: BookOpenIcon,
  novel: BookOpenIcon,
  youtube: PlayIcon,
  video: PlayIcon,
  blog: RssIcon,
  podcast: MicrophoneIcon,
  music: MusicNoteIcon,
  events: CalendarIcon,
  jobs: BriefcaseIcon,
  software: CodeIcon,
  course: AcademicCapIcon,
  research: AcademicCapIcon,
};
export function Icon({ as: Component, ...props }) {
  return <Component className="icon" aria-hidden="true" {...props} />;
}
export function TypeIcon({ kind }) {
  return (
    <span className={`type-icon ${kind}`}>
      <Icon as={typeIcons[kind] || CollectionIcon} />
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

import { useRef } from "react";
import { Link } from "react-router-dom";
import { ArrowRightIcon, ExternalLinkIcon } from "@heroicons/react/outline";
import { patch } from "../api";
import { Icon } from "./Icons";

export default function ContinueLink({
  item,
  onRead,
  onError,
  className = "latest-link",
}) {
  const pending = useRef(false);
  const entry = item.next_unread;
  if (
    item.deleted ||
    item.ignored ||
    !entry ||
    entry.read ||
    entry.ignored ||
    entry.deleted
  )
    return null;
  const direct =
    /^https?:\/\//i.test(entry.url || "") &&
    Math.max(entry.link_count || 1, entry.members?.length || 1) === 1;
  const sort = ["number", "date", "source"].includes(entry.sort)
    ? entry.sort
    : "auto";
  const offset =
    Number.isInteger(entry.offset) &&
    entry.offset >= 0 &&
    entry.offset <= 4950 &&
    entry.offset % 50 === 0
      ? entry.offset
      : 0;
  const destination = `/items/${item.id}?filter=unread&sort=${sort}&direction=asc${offset ? `&offset=${offset}` : ""}#entry-${encodeURIComponent(entry.id)}`;
  const label = `Continue ${item.title}: ${entry.title}`;
  const open = async (event) => {
    if (
      !item.auto_read ||
      pending.current ||
      (event.type !== "click" && event.button !== 1)
    )
      return;
    pending.current = true;
    try {
      await patch(`/links/${entry.id}`, { read: true });
      await onRead?.();
    } catch (error) {
      onError?.(error.message);
    } finally {
      pending.current = false;
    }
  };
  return direct ? (
    <a
      className={`${className} continue-link`}
      href={entry.url}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={label}
      title={`Next unread: ${entry.title}`}
      onClick={open}
      onAuxClick={open}
    >
      Continue <Icon as={ExternalLinkIcon} />
    </a>
  ) : (
    <Link
      className={`${className} continue-link`}
      to={destination}
      aria-label={label}
      title={`Next unread: ${entry.title}`}
    >
      Continue <Icon as={ArrowRightIcon} />
    </Link>
  );
}

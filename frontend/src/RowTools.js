import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Menu } from "@headlessui/react";
import {
  AdjustmentsIcon,
  DotsHorizontalIcon,
  ChevronDownIcon,
  RefreshIcon,
} from "@heroicons/react/outline";
import { day } from "./api";

export function openRowMenu(event) {
  if (event.shiftKey) return; // Shift + right click keeps the browser menu available.
  const button = event.currentTarget.querySelector("[data-row-menu]");
  if (button) {
    event.preventDefault();
    if (button.getAttribute("aria-expanded") !== "true") button.click();
    button.focus();
  }
}

export function FilterOptions({ children, label = "Filter" }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        className="button filter-options-toggle"
        aria-expanded={open}
        aria-controls="filter-options"
        onClick={() => setOpen((value) => !value)}
      >
        <AdjustmentsIcon className="icon" aria-hidden="true" />
        {label}
      </button>
      <div
        id="filter-options"
        className={`filter-options ${open ? "is-open" : ""}`}
      >
        {children}
      </div>
    </>
  );
}

export function RefreshControl({
  onRefresh,
  label,
  busy = false,
  disabled = false,
  primary = false,
}) {
  const anchor = useRef();
  const blocked = disabled || busy;
  return (
    <div className={`refresh-control ${primary ? "primary" : ""}`}>
      <button
        className={`button ${primary ? "primary" : ""}`}
        disabled={blocked}
        title="Use learned detection rules; run full detection automatically when needed."
        onClick={() => onRefresh(false)}
      >
        <RefreshIcon
          className={`icon ${busy ? "spinning" : ""}`}
          aria-hidden="true"
        />
        {busy ? "Refreshing…" : label}
      </button>
      <Menu as="div" className="action-menu">
        {({ open }) => (
          <>
            <Menu.Button
              ref={anchor}
              className={`button refresh-options ${primary ? "primary" : ""}`}
              disabled={blocked}
              aria-label={`Options for ${label.toLowerCase()}`}
            >
              <ChevronDownIcon className="icon" aria-hidden="true" />
            </Menu.Button>
            <FloatingMenu anchor={anchor} open={open}>
              <Menu.Item>
                {({ active }) => (
                  <button
                    className={`action-menu-item ${active ? "focused" : ""}`}
                    title="Run the full model scan and rebuild detection rules."
                    aria-label={
                      label === "Refresh all"
                        ? "Deep refresh all"
                        : "Deep refresh"
                    }
                    onClick={() => onRefresh(true)}
                  >
                    {label === "Refresh all"
                      ? "Deep refresh all"
                      : "Deep refresh"}
                  </button>
                )}
              </Menu.Item>
            </FloatingMenu>
          </>
        )}
      </Menu>
    </div>
  );
}

export function ActionMenu({
  record,
  onAction,
  links = false,
  disabled = false,
  rangeActions = false,
}) {
  const button = useRef();
  const actions = record.deleted
    ? [["restore", "Restore from Trash"]]
    : [
        [
          record.favorite ? "unfavorite" : "favorite",
          record.favorite ? "Unfavorite" : "Favorite",
        ],
        [
          record.ignored ? "unignore" : "ignore",
          record.ignored ? "Stop ignoring" : "Ignore",
        ],
        ...(links
          ? [
              [
                record.read ? "unread" : "read",
                record.read ? "Mark unread" : "Mark read",
              ],
            ]
          : []),
        ...(rangeActions && !record.ignored
          ? [
              ["read-before", "Mark all before as read"],
              ["read-after", "Mark all after as read"],
            ]
          : []),
        ["delete", "Delete"],
      ];
  return (
    <Menu as="div" className="action-menu">
      {({ open }) => (
        <>
          <Menu.Button
            ref={button}
            data-row-menu
            className="icon-button"
            aria-label={`Actions for ${record.title}`}
            disabled={disabled}
          >
            <DotsHorizontalIcon className="icon" aria-hidden="true" />
          </Menu.Button>
          <FloatingMenu anchor={button} open={open}>
            {actions.map(([action, label]) => (
              <Menu.Item key={action}>
                {({ active }) => (
                  <button
                    className={`action-menu-item ${active ? "focused" : ""} ${action === "delete" ? "danger-text" : ""}`}
                    onClick={() => onAction(action)}
                  >
                    {label}
                  </button>
                )}
              </Menu.Item>
            ))}
          </FloatingMenu>
        </>
      )}
    </Menu>
  );
}

function FloatingMenu({ anchor, open, children }) {
  const menu = useRef();
  const [position, setPosition] = useState({});
  useLayoutEffect(() => {
    if (!open) return;
    const locate = () => {
      if (!anchor.current || !menu.current) return;
      const rect = anchor.current.getBoundingClientRect();
      const viewport = window.visualViewport;
      const left = viewport?.offsetLeft || 0;
      const top = viewport?.offsetTop || 0;
      const width = viewport?.width || window.innerWidth;
      const height = viewport?.height || window.innerHeight;
      const menuWidth = Math.min(224, width - 16);
      const maxHeight = Math.max(44, Math.min(320, height - 16));
      const menuHeight = Math.min(menu.current.scrollHeight, maxHeight);
      const below = rect.bottom + 4;
      setPosition({
        width: menuWidth,
        maxHeight,
        left: Math.max(
          left + 8,
          Math.min(rect.right - menuWidth, left + width - menuWidth - 8),
        ),
        top: Math.max(
          top + 8,
          Math.min(
            below + menuHeight <= top + height - 8
              ? below
              : rect.top - menuHeight - 4,
            top + height - menuHeight - 8,
          ),
        ),
      });
    };
    locate();
    window.addEventListener("resize", locate);
    window.addEventListener("scroll", locate, true);
    window.visualViewport?.addEventListener("resize", locate);
    window.visualViewport?.addEventListener("scroll", locate);
    return () => {
      window.removeEventListener("resize", locate);
      window.removeEventListener("scroll", locate, true);
      window.visualViewport?.removeEventListener("resize", locate);
      window.visualViewport?.removeEventListener("scroll", locate);
    };
  }, [anchor, open]);
  return createPortal(
    <Menu.Items ref={menu} className="action-dropdown" style={position}>
      {children}
    </Menu.Items>,
    document.body,
  );
}

export function useSelection(scope) {
  const [ids, setIds] = useState([]);
  const [selecting, setSelecting] = useState(false);
  useEffect(() => {
    setIds([]);
    setSelecting(false);
  }, [scope]);
  return {
    ids,
    selecting: selecting || ids.length > 0,
    start: () => setSelecting(true),
    clear: () => {
      setIds([]);
      setSelecting(false);
    },
    toggle: (id) =>
      setIds((old) =>
        old.includes(id)
          ? old.filter((i) => i !== id)
          : [...old, id].slice(0, 4999),
      ),
    replace: (values) => {
      setSelecting(true);
      setIds(values.slice(0, 4999));
    },
    all: (visible) =>
      setIds((old) =>
        visible.every((id) => old.includes(id))
          ? old.filter((id) => !visible.includes(id))
          : [...new Set([...old, ...visible])].slice(0, 4999),
      ),
  };
}

export function SelectionBar({
  selection,
  visible,
  onAction,
  busy,
  trash = false,
  links = false,
  total = visible.length,
  onSelectAll,
  rangeActions = false,
}) {
  const check = useRef();
  const selected = selection.ids;
  const onPage = selected.filter((id) => visible.includes(id));
  useEffect(() => {
    if (check.current)
      check.current.indeterminate =
        onPage.length > 0 && onPage.length < visible.length;
  }, [onPage.length, visible.length]);
  return (
    <div
      className={`selection-bar ${selected.length ? "has-selection" : ""} ${selection.selecting ? "is-selecting" : ""}`}
    >
      <button
        className="text-button mobile-select-toggle"
        disabled={busy || !visible.length}
        onClick={selection.start}
      >
        Select {links ? "links" : "items"}
      </button>
      <div className="selection-control select-all">
        <label className="checkbox">
          <input
            ref={check}
            aria-label="Select this page"
            type="checkbox"
            disabled={busy || !visible.length}
            checked={!!visible.length && onPage.length === visible.length}
            onChange={() => selection.all(visible)}
          />
          {selected.length ? `${selected.length} selected` : "Select"}
        </label>
        <select
          aria-label="Selection options"
          value=""
          disabled={busy || !visible.length}
          onChange={(e) => {
            if (e.target.value === "all") onSelectAll();
            else selection.replace(visible);
          }}
        >
          <option value="" disabled>
            Select…
          </option>
          <option value="page">This page ({visible.length})</option>
          {onSelectAll && total > visible.length && (
            <option value="all">All matching links ({total})</option>
          )}
        </select>
      </div>
      {(!!selected.length || selection.selecting) && (
        <div className="actions">
          {!!selected.length && (
            <>
              {trash ? (
                <button
                  className="text-button"
                  disabled={busy}
                  onClick={() => onAction("restore", selected)}
                >
                  Restore
                </button>
              ) : (
                <>
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={() => onAction("favorite", selected)}
                  >
                    Favorite
                  </button>
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={() => onAction("ignore", selected)}
                  >
                    Ignore
                  </button>
                  <button
                    className="text-button danger-text"
                    disabled={busy}
                    onClick={() => onAction("delete", selected)}
                  >
                    Delete
                  </button>
                  <select
                    aria-label="More bulk actions"
                    value=""
                    disabled={busy}
                    onChange={(e) => onAction(e.target.value, selected)}
                  >
                    <option value="" disabled>
                      More actions
                    </option>
                    <option value="unfavorite">Unfavorite</option>
                    <option value="unignore">Stop ignoring</option>
                    {links && (
                      <>
                        <option value="read">Mark read</option>
                        <option value="unread">Mark unread</option>
                        {rangeActions && selected.length === 1 && (
                          <>
                            <option value="read-before">
                              Mark all before as read
                            </option>
                            <option value="read-after">
                              Mark all after as read
                            </option>
                          </>
                        )}
                      </>
                    )}
                  </select>
                </>
              )}
            </>
          )}
          <button className="text-button" onClick={selection.clear}>
            Cancel selection
          </button>
        </div>
      )}
    </div>
  );
}

export function LinkDate({ entry }) {
  const value = entry.published_at;
  const precise = entry.date_precision === "time";
  const label =
    {
      published: "Published",
      updated: "Updated",
      listed: "Listed",
      scheduled: "Starts",
      inferred: "Date in URL",
    }[entry.date_kind] || "Date";
  if (!value) return <span className="link-date">Date unavailable</span>;
  const origin = `${entry.date_source || "Source page"}${precise ? ` · ${new Date(value).toISOString()} (shown in your time zone)` : " · Date only"}`;
  return (
    <details className="link-date" title={`${label} · ${origin}`}>
      <summary>
        <time dateTime={value || undefined}>
          {value
            ? precise
              ? new Date(value).toLocaleString(undefined, {
                  year: "numeric",
                  month: "short",
                  day: "numeric",
                  hour: "numeric",
                  minute: "2-digit",
                })
              : day(value)
            : "Date unavailable"}
        </time>
        {value && entry.date_kind !== "published" && <small>{label}</small>}
      </summary>
      <span className="date-origin">
        {label} · {origin}
      </span>
    </details>
  );
}

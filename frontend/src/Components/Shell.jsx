import React, { useEffect, useRef, useState } from "react";
import { Routes, Route, Link, Navigate, useLocation } from "react-router-dom";
import {
  CollectionIcon,
  StarIcon,
  EyeOffIcon,
  PlusIcon,
  MenuIcon,
  XIcon,
  ClockIcon,
  CogIcon,
} from "@heroicons/react/outline";
import { useAuth } from "../Auth/Auth";
import { Notice } from "./Notice";
import PageBoundary from "./PageBoundary";
import { Icon } from "./Icons";
import LibraryPage from "../Pages/LibraryPage";
export function Shell() {
  const auth = useAuth();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButton = useRef();
  const main = useRef();
  useEffect(() => setMenuOpen(false), [location.pathname, location.search]);
  useEffect(() => {
    if (!menuOpen) return;
    const escape = (event) => {
      if (event.key === "Escape") {
        setMenuOpen(false);
        menuButton.current?.focus();
      }
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [menuOpen]);
  const currentFilter =
    new URLSearchParams(location.search).get("filter") || "all";
  const navProps = (filter) => {
    const active = location.pathname === "/" && currentFilter === filter;
    return {
      className: active ? "active" : undefined,
      "aria-current": active ? "page" : undefined,
    };
  };
  const [accountError, setAccountError] = useState("");
  return (
    <div className="app-shell" data-design-seed="0xD81EDED8F">
      <aside className={`sidebar ${menuOpen ? "menu-open" : ""}`}>
        <Link to="/" className="brand">
          <span className="brand-mark">
            <Icon as={CollectionIcon} />
          </span>
          Trackify<span className="brand-dot">.</span>
        </Link>
        <button
          ref={menuButton}
          className="mobile-menu-toggle"
          aria-expanded={menuOpen}
          aria-controls="sidebar-navigation"
          onClick={() => setMenuOpen((open) => !open)}
        >
          <Icon as={menuOpen ? XIcon : MenuIcon} />
          {menuOpen ? "Close menu" : "Menu"}
        </button>
        <div
          id="sidebar-navigation"
          className="sidebar-content"
          onClick={(event) => {
            if (menuOpen && event.target.closest("a[href]")) {
              const anchor = event.target.closest("a[href]").hash?.slice(1);
              setMenuOpen(false);
              requestAnimationFrame(() =>
                (document.getElementById(anchor) || main.current)?.focus({
                  preventScroll: true,
                }),
              );
            }
          }}
        >
          <div className="nav-caption">YOUR MEDIA</div>
          <nav aria-label="Main navigation">
            <Link to="/" {...navProps("all")}>
              <Icon as={CollectionIcon} />
              Library
            </Link>
            <Link to="/?filter=new" {...navProps("new")}>
              <Icon as={ClockIcon} />
              New content
            </Link>
            <Link to="/?filter=favorites" {...navProps("favorites")}>
              <Icon as={StarIcon} />
              Favorites
            </Link>
            <Link to="/?filter=ignored" {...navProps("ignored")}>
              <Icon as={EyeOffIcon} />
              Muted
            </Link>
            <Link
              to="/settings"
              className={
                location.pathname === "/settings" ? "active" : undefined
              }
              aria-current={
                location.pathname === "/settings" ? "page" : undefined
              }
            >
              <Icon as={CogIcon} />
              Settings
            </Link>
          </nav>
          <Link className="button primary side-add" to="/add">
            <Icon as={PlusIcon} />
            Add item
          </Link>
          <div className="sidebar-foot">
            {auth.required ? (
              <>
                <Link to="/settings#account">
                  {auth.user.username} · Account
                </Link>
                <button
                  className="text-button"
                  onClick={async () => {
                    try {
                      await auth.logout();
                    } catch (e) {
                      setAccountError(e.message);
                    }
                  }}
                >
                  Sign out
                </button>
                <Notice error>{accountError}</Notice>
              </>
            ) : (
              <>
                Personal library
                <span className="muted">Saved on this server</span>
              </>
            )}
          </div>
        </div>
      </aside>
      <div className="workspace">
        <main id="main" ref={main} tabIndex={-1}>
          <PageBoundary embedded>
            <Routes>
              <Route path="/" element={<LibraryPage />} />
              <Route
                path="/account"
                element={
                  <React.Suspense
                    fallback={
                      <p className="page-loading" role="status">
                        Loading page…
                      </p>
                    }
                  >
                    <AccountPage />
                  </React.Suspense>
                }
              />
              <Route
                path="/settings"
                element={
                  <React.Suspense
                    fallback={
                      <p className="page-loading" role="status">
                        Loading page…
                      </p>
                    }
                  >
                    <SettingsPage />
                  </React.Suspense>
                }
              />
              <Route
                path="/suggestions"
                element={<Navigate to="/" replace />}
              />
              <Route
                path="/add"
                element={
                  <React.Suspense
                    fallback={
                      <p className="page-loading" role="status">
                        Loading page…
                      </p>
                    }
                  >
                    <AddPage />
                  </React.Suspense>
                }
              />
              <Route
                path="/items/:id"
                element={
                  <React.Suspense
                    fallback={
                      <p className="page-loading" role="status">
                        Loading page…
                      </p>
                    }
                  >
                    <ItemPage />
                  </React.Suspense>
                }
              />
              <Route
                path="*"
                element={
                  <div className="empty">
                    <h1>Page not found</h1>
                    <Link to="/">Back to library</Link>
                  </div>
                }
              />
            </Routes>
          </PageBoundary>
        </main>
      </div>
    </div>
  );
}
const AddPage = React.lazy(() => import("../Pages/AddPage"));
const ItemPage = React.lazy(() => import("../Pages/ItemPage"));
const SettingsPage = React.lazy(() => import("../Pages/SettingsPage"));

const AccountPage = React.lazy(() => import("../Auth/AccountPage"));

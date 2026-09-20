export const pageLoaders = {
  add: () => import("./Pages/AddPage"),
  item: () => import("./Pages/ItemPage"),
  settings: () => import("./Pages/SettingsPage"),
  account: () => import("./Auth/AccountPage"),
};

export function preloadPage(pathname) {
  const path = pathname.replace(/\/$/, "");
  const load = /^\/items\/[^/]+$/.test(path)
    ? pageLoaders.item
    : path === "/add"
      ? pageLoaders.add
      : path === "/settings"
        ? pageLoaders.settings
        : path === "/account"
          ? pageLoaders.account
          : null;
  // Rendering the route handles a failed download through its page boundary.
  return load?.().catch(() => {});
}

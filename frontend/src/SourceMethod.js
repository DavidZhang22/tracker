export const apiMethods = [
  ["wordpress_com", "WordPress.com API"],
  ["wordpress", "WordPress API (self-hosted)"],
  ["devto", "DEV.to API"],
  ["github", "GitHub releases API"],
  ["codeforces", "Codeforces API"],
  ["mastodon", "Mastodon API"],
  ["youtube", "YouTube API"],
  ["ghost", "Ghost Content API"],
  ["mangadex", "MangaDex API"],
];

const hints = {
  auto: "Uses the existing page, feed, and supported site detection.",
  sitemap:
    "Lists pages from the site’s sitemap. Titles come from URLs; dates indicate updates. Use “URL must contain” to narrow the list.",
  wordpress_com:
    "Lists published posts and pages, with titles and publication dates. External links inside articles are not included.",
  wordpress:
    "Use the site home URL, or its /wp-json/ URL for a subdirectory installation. Lists public posts and pages.",
  devto: "Use a DEV.to author or organization URL.",
  github:
    "Lists repository releases. Use Automatic for README links, including job tables.",
  codeforces:
    "Use codeforces.com/contests to list contests and their start times.",
  mastodon:
    "Use a profile URL. Lists public posts; some servers restrict API access.",
  youtube:
    "Use a channel or playlist URL. Requires a YouTube API key configured on the server; Automatic also works without one.",
  ghost:
    "Requires a Ghost Content API key configured on the server for this site. Sitemap works without a key when available.",
  mangadex:
    "Use a title URL. Keywords such as English can filter chapter languages.",
};

export default function SourceMethod({
  value = "auto",
  onChange,
  disabled,
  label = "Source method",
}) {
  return (
    <label className="field">
      {label}
      <select
        aria-label={label}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="auto">Automatic</option>
        <option value="sitemap">Sitemap</option>
        <optgroup label="Public APIs">
          {apiMethods.map(([id, name]) => (
            <option key={id} value={id}>
              {name}
            </option>
          ))}
        </optgroup>
      </select>
      <span className="hint">{hints[value]}</span>
    </label>
  );
}

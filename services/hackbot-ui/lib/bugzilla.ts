const BUGZILLA_HOSTS = [
  "bugzilla.mozilla.org",
  "bugzilla.allizom.org",
  "bugzilla-dev.allizom.org",
];

/**
 * Extract a bug ID from user input: either a bare ID ("1846789") or a Bugzilla
 * URL ("https://bugzilla.mozilla.org/show_bug.cgi?id=1846789").
 * Returns null when the input does not contain a usable bug ID.
 */
export function parseBugId(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;

  let candidate = trimmed;
  if (trimmed.includes("/")) {
    // Accept URLs with no scheme ("bugzilla.mozilla.org/…") and
    // protocol-relative ones ("//bugzilla.mozilla.org/…") as well.
    const withScheme = /^https?:\/\//i.test(trimmed)
      ? trimmed
      : `https://${trimmed.replace(/^\/+/, "")}`;
    let url: URL;
    try {
      url = new URL(withScheme);
    } catch {
      return null;
    }
    if (!BUGZILLA_HOSTS.includes(url.hostname.toLowerCase())) return null;
    // show_bug.cgi?id=123, or the short form bugzilla.mozilla.org/123
    candidate =
      url.searchParams.get("id") ?? url.pathname.replace(/^\/+|\/+$/g, "");
  }

  if (!/^\d+$/.test(candidate)) return null;
  const parsed = Number.parseInt(candidate, 10);
  return parsed > 0 ? parsed : null;
}

export interface PullRequestRef {
  // The repository's full name, "owner/repo".
  repo: string;
  number: number;
}

// The same shapes the API accepts (see BugFixInputs in hackbot-api).
const REPO = /^[A-Za-z0-9-]+\/[A-Za-z0-9._-]+$/;
export const GIT_SHA = /^[0-9a-f]{40}$/;
// No leading "-" (an option to git fetch), whitespace or ":" (a refspec).
export const GIT_REF = /^[^-\s:][^\s:]*$/;

function pullRequest(repo: string, number: string): PullRequestRef | null {
  if (!REPO.test(repo) || !/^\d+$/.test(number)) return null;
  const parsed = Number.parseInt(number, 10);
  return parsed > 0 ? { repo, number: parsed } : null;
}

/**
 * Extract a pull request from user input: either a GitHub URL
 * ("https://github.com/mozilla-firefox/firefox/pull/42", including its
 * "/files" or "/commits" tabs) or the short form "mozilla-firefox/firefox#42".
 * Returns null when the input does not name a pull request.
 */
export function parsePullRequest(value: string): PullRequestRef | null {
  const trimmed = value.trim();
  if (!trimmed) return null;

  const short = /^([^\s#]+)#(\d+)$/.exec(trimmed);
  if (short) return pullRequest(short[1], short[2]);

  const withScheme = /^https?:\/\//i.test(trimmed)
    ? trimmed
    : `https://${trimmed.replace(/^\/+/, "")}`;
  let url: URL;
  try {
    url = new URL(withScheme);
  } catch {
    return null;
  }
  if (url.hostname.toLowerCase() !== "github.com") return null;
  const [owner, repo, kind, number] = url.pathname.split("/").filter(Boolean);
  if (kind !== "pull" || !owner || !repo || !number) return null;
  return pullRequest(`${owner}/${repo}`, number);
}

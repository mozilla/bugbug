const PRODUCTION_HOSTNAME = "hackbot.moz.tools";

// The Sentry environment the Console runs in, from its hostname or URL. The
// names match the API's and the deploys the build pipeline records:
// production, staging (stage.<nonprod domain>), and development otherwise.
export function resolveEnvironment(hostnameOrUrl = ""): string {
  let hostname = hostnameOrUrl;
  if (hostnameOrUrl.includes("://")) {
    try {
      hostname = new URL(hostnameOrUrl).hostname;
    } catch {
      return "development";
    }
  }
  if (hostname === PRODUCTION_HOSTNAME) return "production";
  if (hostname.startsWith("stage.")) return "staging";
  return "development";
}

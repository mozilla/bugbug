// The Sentry environment of each of the Console's hostnames, custom and
// platform; anything else (dev, local) is development. The names match the
// API's and the deploys the build pipeline records. Platform hostnames are
// what an environment advertises until it switches to its custom domain
// (webservices-infra, use_custom_domains).
const ENVIRONMENTS = new Map([
  ["hackbot.moz.tools", "production"],
  ["hackbot.prod.webservices.mozgcp.net", "production"],
  ["hackbot.allizom.org", "staging"],
  ["stage.hackbot.nonprod.webservices.mozgcp.net", "staging"],
]);

// The Sentry environment the Console runs in, from its hostname or URL.
export function resolveEnvironment(hostnameOrUrl = ""): string {
  let hostname = hostnameOrUrl;
  if (hostnameOrUrl.includes("://")) {
    try {
      hostname = new URL(hostnameOrUrl).hostname;
    } catch {
      return "development";
    }
  }
  return ENVIRONMENTS.get(hostname) ?? "development";
}

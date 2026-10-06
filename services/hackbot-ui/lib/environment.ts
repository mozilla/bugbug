const PRODUCTION_HOSTNAME = "hackbot.moz.tools";
const PRODUCTION_URL = `https://${PRODUCTION_HOSTNAME}`;

export function resolveEnvironment(hostnameOrUrl = ""): string {
  return hostnameOrUrl === PRODUCTION_HOSTNAME ||
    hostnameOrUrl.startsWith(PRODUCTION_URL)
    ? "production"
    : "development";
}

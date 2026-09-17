export const PROFILER_ORIGIN = "https://profiler.firefox.com";
export const PROFILER_INJECT_URL = `${PROFILER_ORIGIN}/from-post-message/`;

const READY_POLL_MS = 100;
const READY_TIMEOUT_MS = 15000;

export type ProfilerWindow = Pick<Window, "postMessage">;
export type MessageSource = Pick<
  Window,
  "addEventListener" | "removeEventListener"
>;

// Hand a profile to a window opened on PROFILER_INJECT_URL: the profiler
// answers `ready:request` with `ready:response` once loaded, then accepts
// `inject-profile`. See firefox-devtools/profiler docs-developer/loading-in-profiles.md.
export function injectProfile(
  target: ProfilerWindow,
  profile: unknown,
  source: MessageSource = window
): Promise<void> {
  return new Promise((resolve, reject) => {
    const request = () =>
      target.postMessage({ name: "ready:request" }, PROFILER_ORIGIN);
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== PROFILER_ORIGIN) return;
      if (event.data?.name !== "ready:response") return;
      cleanup();
      target.postMessage({ name: "inject-profile", profile }, PROFILER_ORIGIN);
      resolve();
    };
    const poll = setInterval(request, READY_POLL_MS);
    const timeout = setTimeout(() => {
      cleanup();
      reject(new Error("profiler.firefox.com did not become ready"));
    }, READY_TIMEOUT_MS);
    const cleanup = () => {
      clearInterval(poll);
      clearTimeout(timeout);
      source.removeEventListener("message", onMessage);
    };
    source.addEventListener("message", onMessage);
    request();
  });
}

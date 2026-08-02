import "server-only";

import type {
  AgentDescriptor,
  FeedbackDimension,
  FeedbackDoc,
  FeedbackRating,
  FeedbackTarget,
  RunAction,
  RunDoc,
  RunRef,
} from "./types";

// Thin server-side client for the hackbot-api. The API key lives here and is
// never exposed to the browser — every browser request goes through the
// /api/* route handlers, which call into this module.

export class HackbotError extends Error {
  constructor(
    message: string,
    readonly status: number
  ) {
    super(message);
    this.name = "HackbotError";
  }
}

function config(): { baseUrl: string; apiKey: string } {
  const baseUrl = process.env.HACKBOT_API_URL;
  const apiKey = process.env.HACKBOT_API_KEY;
  if (!baseUrl) {
    throw new HackbotError("HACKBOT_API_URL is not configured", 500);
  }
  if (!apiKey) {
    throw new HackbotError("HACKBOT_API_KEY is not configured", 500);
  }
  return { baseUrl: baseUrl.replace(/\/$/, ""), apiKey };
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { baseUrl, apiKey } = config();
  const res = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers: {
      "X-API-Key": apiKey,
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
    // Always hit the upstream fresh; run state changes over time.
    cache: "no-store",
  });

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) {
        detail =
          typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(body.detail);
      }
    } catch {
      // non-JSON error body; keep the status line
    }
    throw new HackbotError(detail, res.status);
  }

  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

export function listAgents(): Promise<AgentDescriptor[]> {
  return request<AgentDescriptor[]>("/agents");
}

export function createRun(
  agentName: string,
  inputs: Record<string, unknown>,
  requestedBy?: string | null
): Promise<RunRef> {
  return request<RunRef>(`/agents/${encodeURIComponent(agentName)}/runs`, {
    method: "POST",
    body: JSON.stringify(inputs),
    headers: requestedBy ? { "X-On-Behalf-Of": requestedBy } : undefined,
  });
}

export function getRun(runId: string): Promise<RunDoc> {
  return request<RunDoc>(`/runs/${encodeURIComponent(runId)}`);
}

export interface ListRunsParams {
  limit?: number;
  offset?: number;
  agent?: string;
  status?: string;
  requestedBy?: string;
}

export function listRuns(params: ListRunsParams = {}): Promise<RunDoc[]> {
  const qs = new URLSearchParams();
  qs.set("limit", String(params.limit ?? 50));
  if (params.offset) qs.set("offset", String(params.offset));
  if (params.agent) qs.set("agent", params.agent);
  if (params.status) qs.set("status", params.status);
  if (params.requestedBy) qs.set("requested_by", params.requestedBy);
  return request<RunDoc[]>(`/runs?${qs.toString()}`);
}

export function listRunActions(runId: string): Promise<RunAction[]> {
  return request<RunAction[]>(`/runs/${encodeURIComponent(runId)}/actions`);
}

// Manually apply all of a run's pending actions; returns their updated state.
export function applyRunActions(runId: string): Promise<RunAction[]> {
  return request<RunAction[]>(
    `/runs/${encodeURIComponent(runId)}/actions/apply`,
    { method: "POST" }
  );
}

// The comment a public rater is being asked to judge, plus the nonce that
// authorises their submission. Read-only: fetching this never records a vote.
export function getFeedbackTarget(token: string): Promise<FeedbackTarget> {
  return request<FeedbackTarget>(`/rate/${encodeURIComponent(token)}`);
}

export interface SubmitFeedbackBody {
  rating: FeedbackRating;
  nonce: string;
  dimensions: FeedbackDimension[];
  comment: string | null;
}

// `rater` carries the signals hackbot-api salts into a pseudonymous dedupe
// key: a per-browser cookie id, falling back to the original visitor's IP and
// user agent, both of which are otherwise lost behind this server-side hop.
export function submitFeedback(
  token: string,
  body: SubmitFeedbackBody,
  rater: {
    raterKey?: string | null;
    forwardedFor?: string | null;
    userAgent?: string | null;
  } = {}
): Promise<{ message: string }> {
  const headers: Record<string, string> = {};
  if (rater.raterKey) headers["X-Rater-Key"] = rater.raterKey;
  if (rater.forwardedFor) headers["X-Forwarded-For"] = rater.forwardedFor;
  if (rater.userAgent) headers["User-Agent"] = rater.userAgent;
  return request<{ message: string }>(`/rate/${encodeURIComponent(token)}`, {
    method: "POST",
    body: JSON.stringify(body),
    headers,
  });
}

export interface ListFeedbackParams {
  agent?: string;
  rating?: FeedbackRating;
  runId?: string;
  limit?: number;
  offset?: number;
}

// Every recorded rating, for the internal review page. Returns rater-adjacent
// data the public routes never expose, so callers must be SSO-authenticated.
export function listFeedback(
  params: ListFeedbackParams = {}
): Promise<FeedbackDoc[]> {
  const qs = new URLSearchParams();
  qs.set("limit", String(params.limit ?? 100));
  if (params.offset) qs.set("offset", String(params.offset));
  if (params.agent) qs.set("agent", params.agent);
  if (params.rating) qs.set("rating", params.rating);
  if (params.runId) qs.set("run_id", params.runId);
  return request<FeedbackDoc[]>(`/feedback?${qs.toString()}`);
}

// Ask hackbot-api for a short-lived signed download URL for one artifact.
// `artifactName` may contain slashes; each segment is encoded individually so
// the upstream `{artifact_path:path}` route still sees the directory structure.
export function getArtifactDownloadUrl(
  runId: string,
  artifactName: string
): Promise<{ url: string }> {
  const encodedPath = artifactName.split("/").map(encodeURIComponent).join("/");
  return request<{ url: string }>(
    `/runs/${encodeURIComponent(runId)}/artifacts/${encodedPath}`
  );
}

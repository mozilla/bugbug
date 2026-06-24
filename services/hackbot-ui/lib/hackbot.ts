import "server-only";

import type { AgentDescriptor, RunDoc, RunRef } from "./types";

// Thin server-side client for the hackbot-api. The API key lives here and is
// never exposed to the browser — every browser request goes through the
// /api/* route handlers, which call into this module.

export class HackbotError extends Error {
  constructor(
    message: string,
    readonly status: number,
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

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
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
): Promise<RunRef> {
  return request<RunRef>(
    `/agents/${encodeURIComponent(agentName)}/runs`,
    {
      method: "POST",
      body: JSON.stringify(inputs),
    },
  );
}

export function getRun(runId: string): Promise<RunDoc> {
  return request<RunDoc>(`/runs/${encodeURIComponent(runId)}`);
}

export function listRuns(limit = 50): Promise<RunDoc[]> {
  return request<RunDoc[]>(`/runs?limit=${limit}`);
}

// Ask hackbot-api for a short-lived signed download URL for one artifact.
// `artifactName` may contain slashes; each segment is encoded individually so
// the upstream `{artifact_path:path}` route still sees the directory structure.
export function getArtifactDownloadUrl(
  runId: string,
  artifactName: string,
): Promise<{ url: string }> {
  const encodedPath = artifactName
    .split("/")
    .map(encodeURIComponent)
    .join("/");
  return request<{ url: string }>(
    `/runs/${encodeURIComponent(runId)}/artifacts/${encodedPath}`,
  );
}

import type { TranscriptEntry } from "./transcripts";

// The parts of a Firefox Profiler processed profile that the markers touch.
export interface Profile {
  meta: {
    startTime: number;
    categories: { name: string }[];
    markerSchema: object[];
  };
  shared: { stringArray: string[] };
  threads: {
    markers: {
      length: number;
      name: number[];
      startTime: (number | null)[];
      endTime: (number | null)[];
      phase: number[];
      category: number[];
      data: (object | null)[];
    };
  }[];
}

export interface SessionSpan {
  title: string;
  start: number;
  end: number;
}

const MARKER_NAME = "Session";
const INTERVAL = 1;
const TITLE_LENGTH = 60;

const SESSION_SCHEMA = {
  name: MARKER_NAME,
  tooltipLabel: "{marker.data.title}",
  tableLabel: "Session {marker.data.index}: {marker.data.title}",
  chartLabel: "{marker.data.title}",
  display: ["marker-chart", "marker-table", "timeline-overview"],
  fields: [
    { key: "index", label: "Session", format: "integer" },
    { key: "title", label: "Title", format: "string" },
  ],
};

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

// The title Claude Code gave the session, else its first prompt.
function sessionTitle(entries: TranscriptEntry[]): string | null {
  for (const e of [...entries].reverse()) {
    const title = text(e.aiTitle) ?? text(e.summary);
    if (title && (e.type === "ai-title" || e.type === "summary")) return title;
  }
  for (const e of entries) {
    const message = e.message as
      | { role?: string; content?: unknown }
      | undefined;
    if (e.type === "user" && message?.role === "user") {
      const prompt = text(message.content);
      if (prompt) return prompt.slice(0, TITLE_LENGTH);
    }
  }
  return null;
}

// One span per session, in start order. Like claude-profiler, only user and
// assistant messages count for timing, so the first span starts at zero.
export function sessionSpans(sessions: TranscriptEntry[][]): SessionSpan[] {
  const spans: SessionSpan[] = [];
  for (const entries of sessions) {
    const times = entries
      .filter((e) => e.type === "user" || e.type === "assistant")
      .map((e) => (e.timestamp ? Date.parse(e.timestamp) : NaN))
      .filter((t) => !Number.isNaN(t));
    if (!times.length) continue;
    spans.push({
      title: sessionTitle(entries) ?? "",
      start: Math.min(...times),
      end: Math.max(...times),
    });
  }
  return spans
    .sort((a, b) => a.start - b.start)
    .map((s, i) => ({ ...s, title: s.title || `Session ${i + 1}` }));
}

// Mark each session as an interval on the main track so the analysis and fix
// stages of a run are visible in the timeline. Skipped for single-session runs.
export function addSessionMarkers(
  profile: Profile,
  spans: SessionSpan[]
): void {
  if (spans.length < 2 || !profile.threads.length) return;
  profile.meta.markerSchema.push(SESSION_SCHEMA);
  const strings = profile.shared.stringArray;
  let name = strings.indexOf(MARKER_NAME);
  if (name < 0) name = strings.push(MARKER_NAME) - 1;
  const category = Math.max(
    0,
    profile.meta.categories.findIndex((c) => c.name === "Messages")
  );
  const markers = profile.threads[0].markers;
  spans.forEach((span, i) => {
    markers.name.push(name);
    markers.startTime.push(span.start - profile.meta.startTime);
    markers.endTime.push(span.end - profile.meta.startTime);
    markers.phase.push(INTERVAL);
    markers.category.push(category);
    markers.data.push({ type: MARKER_NAME, index: i + 1, title: span.title });
    markers.length++;
  });
}

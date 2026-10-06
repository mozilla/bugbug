import { RunDetail } from "@/components/RunDetail";
import { weaveProject, weaveRunTracesUrl } from "@/lib/weave";

export default async function RunPage({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const { runId } = await params;

  return (
    <RunDetail
      key={runId}
      runId={runId}
      tracesUrl={weaveRunTracesUrl(weaveProject(), runId)}
    />
  );
}

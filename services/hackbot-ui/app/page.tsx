import { Suspense } from "react";

import { RecentRuns } from "@/components/RecentRuns";
import { TriggerForm } from "@/components/TriggerForm";

export default function HomePage() {
  return (
    <>
      <div className="panel">
        <h2>Trigger a run</h2>
        <Suspense fallback={null}>
          <TriggerForm />
        </Suspense>
      </div>

      <div className="panel">
        <h2>Recent runs</h2>
        <RecentRuns />
      </div>
    </>
  );
}

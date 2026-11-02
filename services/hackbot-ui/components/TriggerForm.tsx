"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import {
  AGENTS,
  type AgentValue,
  GECKO_TRIAGE_TEAMS,
  type GeckoTriageTeam,
  parseGeckoTriageTeam,
} from "@/lib/agents";
import { parseBugId } from "@/lib/bugzilla";
import { saveRun } from "@/lib/store";
import type { RunRef } from "@/lib/types";
import { parseUpliftSources } from "@/lib/uplift";

function parseAgent(value: string | null): AgentValue {
  return AGENTS.some((a) => a.value === value)
    ? (value as AgentValue)
    : "bug-fix";
}

export function TriggerForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [agent, setAgent] = useState<AgentValue>(() =>
    parseAgent(params.get("agent"))
  );
  const [bugId, setBugId] = useState(() => params.get("bug_id") ?? "");
  const [bugData, setBugData] = useState(() => params.get("bug_data") ?? "");
  const [requireReview, setRequireReview] = useState(
    () => params.get("require_review") === "true"
  );
  const [gitCommit, setGitCommit] = useState(
    () => params.get("git_commit") ?? ""
  );
  const [failureTasks, setFailureTasks] = useState(
    () => params.get("failure_tasks") ?? ""
  );
  const [parentRunId, setParentRunId] = useState(
    () => params.get("parent_run_id") ?? ""
  );
  const [runTryPush, setRunTryPush] = useState(
    () => params.get("run_try_push") === "true"
  );
  const [featureName, setFeatureName] = useState(
    () => params.get("feature_name") ?? ""
  );
  const [featureDescription, setFeatureDescription] = useState(
    () => params.get("feature_description") ?? ""
  );
  const [testScope, setTestScope] = useState(
    () => params.get("test_scope") ?? ""
  );
  const [targetBranch, setTargetBranch] = useState(
    () => params.get("target_branch") ?? ""
  );
  const [upliftSources, setUpliftSources] = useState(
    () => params.get("sources") ?? ""
  );
  const [dataset, setDataset] = useState(() => params.get("dataset") ?? "");
  const [harness, setHarness] = useState(
    () => params.get("harness") ?? "claude_code"
  );
  const [reps, setReps] = useState(() => params.get("reps") ?? "");
  const [evalTasks, setEvalTasks] = useState(() => params.get("tasks") ?? "");
  const [exampleTimeout, setExampleTimeout] = useState(
    () => params.get("example_timeout_min") ?? ""
  );
  const [weaveProject, setWeaveProject] = useState(
    () => params.get("weave_project") ?? ""
  );
  const [evalLabel, setEvalLabel] = useState(() => params.get("label") ?? "");
  const [judgeRubric, setJudgeRubric] = useState(
    () => params.get("judge_rubric") ?? ""
  const [team, setTeam] = useState(() =>
    parseGeckoTriageTeam(params.get("team"))
  );
  const [model, setModel] = useState(() => params.get("model") ?? "");
  const [maxTurns, setMaxTurns] = useState(() => params.get("max_turns") ?? "");
  const [effort, setEffort] = useState(() => params.get("effort") ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isWebcompatAgent = agent.startsWith("autowebcompat-");
  const isBuildRepairAgent = agent === "build-repair";
  const isTestRepairAgent = agent === "test-repair";
  const isTestRepairFixAgent = agent === "test-repair-fix";
  const isTestPlanAgent = agent === "test-plan-generator";
  const isUpliftAgent = agent === "uplift-resolve";
  const isDatasetEvalAgent = agent === "dataset-eval";
  const isGeckoTriageAgent = agent === "gecko-triage";
  const needsFailureTasks = isBuildRepairAgent || isTestRepairAgent;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    const inputs: Record<string, unknown> = { require_review: requireReview };

    const parsedBugId = parseBugId(bugId);
    const hasBugId = parsedBugId !== null;
    const hasBugData = isWebcompatAgent && bugData.trim().length > 0;

    if (isTestRepairFixAgent) {
      if (!parentRunId.trim()) {
        setError("Enter the test-repair run to continue from.");
        return;
      }
      inputs.parent_run_id = parentRunId.trim();
    } else if (needsFailureTasks) {
      if (isBuildRepairAgent) {
        if (hasBugId) inputs.bug_id = parsedBugId;
        if (!gitCommit.trim()) {
          setError("Enter a git commit hash.");
          return;
        }
        inputs.git_commit = gitCommit.trim();
      }
      if (!failureTasks.trim()) {
        setError("Enter failure tasks as a JSON object.");
        return;
      }
      let parsedTasks: unknown;
      try {
        parsedTasks = JSON.parse(failureTasks.trim());
      } catch {
        setError(
          'Failure tasks must be valid JSON (e.g. {"task-name": "task-id"}).'
        );
        return;
      }
      if (
        typeof parsedTasks !== "object" ||
        parsedTasks === null ||
        Array.isArray(parsedTasks)
      ) {
        setError(
          "Failure tasks must be a JSON object mapping task names to task IDs."
        );
        return;
      }
      inputs.failure_tasks = parsedTasks;
      if (isBuildRepairAgent) inputs.run_try_push = runTryPush;
    } else if (isTestPlanAgent) {
      if (!featureName.trim()) {
        setError("Enter a feature name.");
        return;
      }
      if (!featureDescription.trim()) {
        setError("Enter a feature description.");
        return;
      }
      if (!testScope.trim()) {
        setError("Enter a test scope.");
        return;
      }
      inputs.feature_name = featureName.trim();
      inputs.feature_description = featureDescription.trim();
      inputs.test_scope = testScope.trim();
    } else if (isUpliftAgent) {
      if (!targetBranch.trim()) {
        setError("Enter the target uplift branch.");
        return;
      }
      const parsed = parseUpliftSources(upliftSources);
      if (parsed.error) {
        setError(parsed.error);
        return;
      }
      inputs.target_branch = targetBranch.trim();
      inputs.sources = parsed.sources;
      if (hasBugId) inputs.bug_id = parsedBugId;
    } else if (isDatasetEvalAgent) {
      if (!dataset.trim() || !judgeRubric.trim()) {
        setError("Enter a Weave dataset ref and a judge rubric ref.");
        return;
      }
      inputs.dataset = dataset.trim();
      inputs.harness = harness;
      const tasks = evalTasks
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean);
      if (tasks.length) inputs.tasks = tasks;
      for (const [key, value] of [
        ["reps", reps],
        ["example_timeout_min", exampleTimeout],
      ] as const) {
        const n = Number.parseInt(value, 10);
        if (Number.isInteger(n) && n > 0) inputs[key] = n;
      }
      if (weaveProject.trim()) inputs.weave_project = weaveProject.trim();
      inputs.judge_rubric = judgeRubric.trim();
      if (evalLabel.trim()) inputs.label = evalLabel.trim();
    } else if (!isWebcompatAgent) {
      if (!hasBugId) {
        setError("Enter a valid Bugzilla bug ID or bug URL.");
        return;
      }
      inputs.bug_id = parsedBugId;
      if (isGeckoTriageAgent && team) inputs.team = team;
    } else {
      if (!hasBugId && !hasBugData) {
        setError(
          "Provide a Bugzilla bug ID (or bug URL) or paste report text."
        );
        return;
      }
      if (hasBugId) inputs.bug_id = parsedBugId;
      if (hasBugData) inputs.bug_data = bugData.trim();
    }

    if (model.trim()) inputs.model = model.trim();
    if (maxTurns.trim()) {
      const n = Number.parseInt(maxTurns, 10);
      if (Number.isInteger(n) && n > 0) inputs.max_turns = n;
    }
    if (!needsFailureTasks && !isTestRepairFixAgent && effort.trim())
      inputs.effort = effort.trim();

    setSubmitting(true);
    try {
      const res = await fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent, inputs }),
      });
      const body = await res.json();
      if (!res.ok) {
        throw new Error(body?.error ?? `Request failed (${res.status})`);
      }
      const run = body as RunRef;
      const label = isBuildRepairAgent
        ? `commit ${gitCommit.trim().slice(0, 12)}`
        : isTestRepairAgent
          ? Object.keys(inputs.failure_tasks as Record<string, string>)[0] ??
            "test failure"
          : isTestRepairFixAgent
            ? `fix for run ${parentRunId.trim().slice(0, 8)}`
            : isTestPlanAgent
              ? featureName.trim()
              : isUpliftAgent
                ? `uplift to ${targetBranch.trim()}`
                : isDatasetEvalAgent
                  ? `eval ${dataset.trim()} (${harness})`
                  : hasBugId
                    ? `bug ${parsedBugId}`
                    : "inline report";
      saveRun({
        run_id: run.run_id,
        agent: run.agent,
        status: run.status,
        label,
        created_at: new Date().toISOString(),
      });
      router.push(`/runs/${run.run_id}`);
    } catch (err) {
      setError((err as Error).message);
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={onSubmit}>
      {error && <div className="error-banner">{error}</div>}

      <div className="field">
        <label htmlFor="agent">Agent</label>
        <select
          id="agent"
          value={agent}
          onChange={(e) => {
            setAgent(e.target.value as AgentValue);
            setError(null);
          }}
        >
          {AGENTS.map((a) => (
            <option key={a.value} value={a.value}>
              {a.label}
            </option>
          ))}
        </select>
      </div>

      {isTestRepairFixAgent && (
        <div className="field">
          <label htmlFor="parentRunId">test-repair run ID *</label>
          <input
            id="parentRunId"
            placeholder="e.g. 3f1c2b4a-..."
            value={parentRunId}
            onChange={(e) => setParentRunId(e.target.value)}
            required
          />
        </div>
      )}

      {!needsFailureTasks &&
        !isTestRepairFixAgent &&
        !isTestPlanAgent &&
        !isUpliftAgent &&
        !isDatasetEvalAgent && (
          <div className="field">
            <label htmlFor="bugId">
              {isWebcompatAgent
                ? "Bugzilla bug ID or URL (optional if report text provided)"
                : "Bugzilla bug ID or URL *"}
            </label>
            <input
              id="bugId"
              placeholder="e.g. 1846789 or https://bugzilla.mozilla.org/show_bug.cgi?id=1846789"
              value={bugId}
              onChange={(e) => setBugId(e.target.value)}
              required={!isWebcompatAgent}
            />
          </div>
        )}

      {isGeckoTriageAgent && (
        <div className="field">
          <label htmlFor="team">Team</label>
          <select
            id="team"
            value={team}
            onChange={(e) => setTeam(e.target.value as GeckoTriageTeam)}
          >
            {GECKO_TRIAGE_TEAMS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
            <option value="">no team (shared rules only)</option>
          </select>
        </div>
      )}

      {isWebcompatAgent && (
        <div className="field">
          <label htmlFor="bugData">
            Report text (optional if bug ID provided)
          </label>
          <textarea
            id="bugData"
            placeholder="Paste the web-compatibility report text here…"
            rows={5}
            value={bugData}
            onChange={(e) => setBugData(e.target.value)}
          />
        </div>
      )}

      {isBuildRepairAgent && (
        <>
          <div className="field">
            <label htmlFor="bugId">Bugzilla bug ID or URL (optional)</label>
            <input
              id="bugId"
              placeholder="e.g. 1846789 or https://bugzilla.mozilla.org/show_bug.cgi?id=1846789"
              value={bugId}
              onChange={(e) => setBugId(e.target.value)}
            />
          </div>

          <div className="field">
            <label htmlFor="gitCommit">Git commit *</label>
            <input
              id="gitCommit"
              placeholder="e.g. abc123def456"
              value={gitCommit}
              onChange={(e) => setGitCommit(e.target.value)}
              required
            />
          </div>
        </>
      )}

      {needsFailureTasks && (
        <div className="field">
          <label htmlFor="failureTasks">
            Failure tasks * (JSON object: task name to task ID)
          </label>
          <textarea
            id="failureTasks"
            placeholder={
              isTestRepairAgent
                ? 'e.g. {"test-linux1804-64/opt-xpcshell-1": "Abc123XYZ"}'
                : 'e.g. {"build-linux64": "Abc123XYZ"}'
            }
            rows={4}
            value={failureTasks}
            onChange={(e) => setFailureTasks(e.target.value)}
            required
          />
        </div>
      )}

      {isBuildRepairAgent && (
        <div className="field">
          <label>
            <input
              type="checkbox"
              checked={runTryPush}
              onChange={(e) => setRunTryPush(e.target.checked)}
            />{" "}
            Run try push after fix
          </label>
        </div>
      )}

      {isUpliftAgent && (
        <>
          <div className="field">
            <label htmlFor="targetBranch">Target uplift branch *</label>
            <input
              id="targetBranch"
              placeholder="e.g. release, beta, esr128"
              value={targetBranch}
              onChange={(e) => setTargetBranch(e.target.value)}
              required
            />
          </div>

          <div className="field">
            <label htmlFor="upliftSources">
              Sources * (JSON list, applied in order)
            </label>
            <textarea
              id="upliftSources"
              placeholder={
                '[{"kind": "git", "commit": "9f4a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a"},\n' +
                ' {"kind": "phabricator", "revision_id": 12345}]'
              }
              rows={4}
              value={upliftSources}
              onChange={(e) => setUpliftSources(e.target.value)}
              required
            />
          </div>

          <div className="field">
            <label htmlFor="bugId">Bugzilla bug ID or URL (optional)</label>
            <input
              id="bugId"
              placeholder="e.g. 1846789 or https://bugzilla.mozilla.org/show_bug.cgi?id=1846789"
              value={bugId}
              onChange={(e) => setBugId(e.target.value)}
            />
          </div>
        </>
      )}

      {isDatasetEvalAgent && (
        <>
          <div className="field">
            <label htmlFor="dataset">Weave dataset *</label>
            <input
              id="dataset"
              placeholder="e.g. my-dataset:v1"
              value={dataset}
              onChange={(e) => setDataset(e.target.value)}
              required
            />
          </div>

          <div className="row">
            <div className="field">
              <label htmlFor="harness">Harness</label>
              <select
                id="harness"
                value={harness}
                onChange={(e) => setHarness(e.target.value)}
              >
                <option value="claude_code">claude_code</option>
                <option value="codex">codex</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="reps">Reps (optional)</label>
              <input
                id="reps"
                inputMode="numeric"
                placeholder="1"
                value={reps}
                onChange={(e) => setReps(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="exampleTimeout">
                Minutes per example (optional)
              </label>
              <input
                id="exampleTimeout"
                inputMode="numeric"
                placeholder="30"
                value={exampleTimeout}
                onChange={(e) => setExampleTimeout(e.target.value)}
              />
            </div>
          </div>

          <div className="field">
            <label htmlFor="evalTasks">
              Task ids (optional, comma-separated)
            </label>
            <input
              id="evalTasks"
              placeholder="all"
              value={evalTasks}
              onChange={(e) => setEvalTasks(e.target.value)}
            />
          </div>

          <div className="field">
            <label htmlFor="evalLabel">Label (optional)</label>
            <input
              id="evalLabel"
              placeholder="e.g. full-task-prompt"
              maxLength={64}
              value={evalLabel}
              onChange={(e) => setEvalLabel(e.target.value)}
            />
          </div>

          <div className="field">
            <label htmlFor="judgeRubric">Judge rubric ref</label>
            <input
              id="judgeRubric"
              placeholder="e.g. judge-rubric:v1"
              value={judgeRubric}
              onChange={(e) => setJudgeRubric(e.target.value)}
            />
          </div>

          <div className="field">
            <label htmlFor="weaveProject">Weave project (optional)</label>
            <input
              id="weaveProject"
              placeholder="moz-bugbug/hackbot-evals"
              value={weaveProject}
              onChange={(e) => setWeaveProject(e.target.value)}
            />
          </div>
        </>
      )}

      {isTestPlanAgent && (
        <>
          <div className="field">
            <label htmlFor="featureName">Feature name *</label>
            <input
              id="featureName"
              placeholder="e.g. Tab Groups"
              value={featureName}
              onChange={(e) => setFeatureName(e.target.value)}
              required
            />
          </div>

          <div className="field">
            <label htmlFor="featureDescription">Feature description *</label>
            <textarea
              id="featureDescription"
              placeholder="Describe the feature to generate test cases for…"
              rows={5}
              value={featureDescription}
              onChange={(e) => setFeatureDescription(e.target.value)}
              required
            />
          </div>

          <div className="field">
            <label htmlFor="testScope">Test scope *</label>
            <textarea
              id="testScope"
              placeholder="Describe what the test plan should cover…"
              rows={3}
              value={testScope}
              onChange={(e) => setTestScope(e.target.value)}
              required
            />
          </div>
        </>
      )}

      <div className="row">
        <div className="field">
          <label htmlFor="model">Model (optional)</label>
          <input
            id="model"
            placeholder="default"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="maxTurns">Max turns (optional)</label>
          <input
            id="maxTurns"
            inputMode="numeric"
            placeholder="default"
            value={maxTurns}
            onChange={(e) => setMaxTurns(e.target.value)}
          />
        </div>
        {!needsFailureTasks && !isTestRepairFixAgent && (
          <div className="field">
            <label htmlFor="effort">Effort (optional)</label>
            <input
              id="effort"
              placeholder="default"
              value={effort}
              onChange={(e) => setEffort(e.target.value)}
            />
          </div>
        )}
      </div>

      <div className="field checkbox-field">
        <label>
          <input
            type="checkbox"
            checked={requireReview}
            onChange={(e) => setRequireReview(e.target.checked)}
          />
          Review before publishing
        </label>
      </div>

      <button type="submit" disabled={submitting}>
        {submitting ? "Triggering…" : `Trigger ${agent} agent`}
      </button>
    </form>
  );
}

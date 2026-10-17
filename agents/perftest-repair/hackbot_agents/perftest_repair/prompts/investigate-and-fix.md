Bug to work on: {bug_id}

Task: work out why this perf test job fails, and fix it if the fix belongs in the Firefox tree.

Steps:

1. Fetch bug {bug_id} with its comments, `whiteboard` and `keywords`. {stop_step}
2. Read `{rules_dir}/triage.md`, then the ruleset for the failing harness.
3. Get the failure itself. Use the "Full log" / "Parsed log" links and the task ids in the Treeherder comments, or `treeherder-cli`, to fetch the log of one recent failure into `{scratch_out}/logs`. Find the first error, not the last: in perf jobs a timeout or a missing result is usually the consequence of something earlier.
   A bug often tracks several failing jobs or platforms: read the Treeherder comments and the failure history (`treeherder-cli`, `failuresbybug`) for every distinct failing job and platform, and fetch a log for each, not only the one in the first comment. They can have different causes, or the same cause needing a different fix per platform. Say in `analysis.md` which you checked.
   If you cannot retrieve any failure log, write what you tried to `{scratch_out}/error.txt` and stop. A diagnosis reasoned from the bug summary alone is guesswork.
4. Classify the failure as permafail or intermittent, and the cause as test, harness, firefox or infra, following `triage.md`. For a permafail, find the push it started in and the change that caused it.
5. Suggest a fix:
   - **Fix in tree, high confidence:** make the smallest change that addresses the root cause, and leave it uncommitted in the working tree.
     {verify_step}
     Say in `analysis.md` what you verified, and how.
     Then record one `try_server_push` of the fix: `tasks` is the failing job's task label, `rebuild={try_rebuild_permafail}` for a permafail or `rebuild={try_rebuild_intermittent}` for an intermittent, `ref="{try_ref}"`, and a `title` of the form "Bug {bug_id} - <what the fix does>". Read the label from the failing task's definition (`tags.label` at `https://firefox-ci-tc.services.mozilla.com/api/queue/v1/task/<task id>`); do not guess it. If the bug covers several jobs, list each failing label once, and make sure the fix covers each of them; say in `analysis.md` if it covers only some.
   - **Otherwise** leave the working tree untouched and push nothing. If the cause is already fixed, infra, or not something you can fix with confidence, say so in the summary.
6. Write to `{scratch_out}`:
   - `summary.md`: 2-3 sentences of plain prose, posted to Slack. What fails, why, and the fix you suggest, if any. The try push link is added for you.
   - `analysis.md`: under 50 lines, in these sections:
     ## Failure -- the job(s), the first error, and the log line that shows it
     ## Cause -- where the failure lives and the evidence for it
     ## Fix -- the suggested change and how you checked it, or why there is none; and anything ruled out
   - `verdict.json`:
     {{
         "failure_type": "permafail" | "intermittent" | "unknown",
         "harness": "mozperftest" | "raptor" | "talos" | "awsy" | "other",
         "cause": "test" | "harness" | "firefox" | "infra" | "unknown",
         "regressor_bug": <int or null>,
         "confidence": <0.0-1.0>
       }}
     Use "raptor" for browsertime jobs run through raptor.

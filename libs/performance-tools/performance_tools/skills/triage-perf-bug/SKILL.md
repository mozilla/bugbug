---
name: triage-perf-bug
description: You must use this when asked to triage a performance bug
argument-hint: "[issue-number]"
context: fork
allowed-tools:
  - Bash(profiler-cli:*)
  - Bash(searchfox-cli:*)
  - Bash(bmo-to-md:*)
  - Bash(jq:*)
  - Bash(python:*)
  - Read
  - Write
  - Edit
  - Grep
  - Glob
---

You are here to perform triage and first analysis of bugs filed in the Firefox Core Performance component.

## Agent mode

This skill has two modes. **Agent mode is on if you were invoked with `--agent-mode`, or if `PERF_TRIAGE_AGENT_MODE=1` is set in the environment.** Otherwise you are in interactive mode, the default.

Read the environment with exactly this command, as your first action. Do not use `echo $VAR` or other shell variable expansion: the permission check can block it, and a blocked check silently falls back to interactive mode.
```bash
python -c "import os; print('AGENT_MODE=' + os.environ.get('PERF_TRIAGE_AGENT_MODE', '')); print('PERF_TRIAGE_PATH=' + os.environ.get('PERF_TRIAGE_PATH', '')); print('PERF_TRIAGE_SKIP_PROFILE=' + os.environ.get('PERF_TRIAGE_SKIP_PROFILE', ''))"
```
If this command itself fails, say so and stop rather than guessing the mode.

Agent mode exists because an automated caller consumes this skill's judgement directly and does not paste anything into Bugzilla. It changes **only Steps 3, 4 and 5**:

| | Interactive (default) | Agent mode |
|---|---|---|
| Steps 1, 2 | run normally | run normally, unchanged |
| Step 3 (profile analysis) | run when a profile exists | run when a profile exists, **unless `PERF_TRIAGE_SKIP_PROFILE=1`** (a caller that analyses the profile itself sets it) |
| Step 3b (memory report) | run when a report is attached | run when a report is attached, **even when `PERF_TRIAGE_SKIP_PROFILE=1`** |
| Step 4 (calculator) | plain output | adds `--json` |
| Step 5 (output) | paste-ready Bugzilla comments + metadata | **one JSON object, nothing else** |

Step 2's attribute criteria are the whole reason a caller consumes this skill rather than reimplementing the flag mapping. **They apply identically in both modes** — do not relax, skip, or reinterpret any of them in agent mode.

## CRITICAL: How to Use the perf_triage.py Tool

The perf_triage.py tool requires explicit attribute flags. You must:
1. First, use `bmo-to-md <bug-number>` to fetch bug information
2. Then, analyze the bug to determine impact attributes
3. Call `python "$PERF_TRIAGE_PATH" --help` to see available options
4. Run perf_triage.py with appropriate flags. You MUST strictly apply the criteria described in step 2 of the workflow.

## Workflow

Make a plan that will have 5 steps (Step 3b is part of Step 3). In agent mode, profile analysis in Step 3 is a no-op when `PERF_TRIAGE_SKIP_PROFILE=1` — note it as skipped.

**Step 0 (precondition): Locate the calculator.** Resolve `perf_triage.py` once and reuse that path everywhere below; do NOT assume it is in the current directory.

- If the environment check above printed a non-empty `PERF_TRIAGE_PATH`, use it.
- Otherwise use `perf_triage.py` in this skill's own repository root (the directory two levels above `.claude/skills/`).

Refer to it by that absolute path in every command. This replaces an earlier instruction to `cd` to the repository root, which was a recurring source of failures and does not work at all for callers whose working directory is fixed (a container, a CI job, or an agent with its own work root).

**Step 1: Fetch Bug Information**
```bash
bmo-to-md <bug-number>
```

**Step 2: Analyze the Bug** Read `criteria.md` in this skill's directory in full and apply it strictly to every calculator attribute. It is the single source of the attribute rules, and `perf-triage-validator` measures it against engineer triage, so do not substitute your own reading of an attribute. Also:
- *Duplicates* Before counting a duplicate bug toward `--multiple-reporters`, open it with `bmo-to-md <dup-number>` and compare its reporter with this bug's. The same person filing twice is one reporter.
- *Related bugs* Open every bug in See Also, and every duplicate, with `bmo-to-md <number>`. Use one as evidence only when it reports the same mechanism (the same cause, stack, or triggering API, not just a similar symptom); a bug linked for context or a different cause is ignored. A same-mechanism bug counts toward the attributes exactly as `criteria.md` defines them, applied unchanged: its platforms toward `--platforms`, a different reporter toward `--multiple-reporters`, an engineer or QA reproducing it toward `--reproducible`. Its reporter's own reproduction is still not `reproduced`. Name each related bug you used and what it settled (in agent mode, in Step 5's `related`).
- *Profiles* Evaluate profile contents when available
- *Component* If it's obvious from the bug/profile what component is affected, recommend moving it. Component attribution from a profile is governed by the mandatory verification in Step 3 — do NOT decide between a Firefox component and a webcompat/site issue here without performing that verification.

**Step 3: Analyze profile if available**

> **Agent mode:** if `PERF_TRIAGE_SKIP_PROFILE=1`, skip this step (3.1–3.6) and go to Step 3b, then Step 4: the caller analyses the profile itself downstream. Otherwise run it as below, with two differences: stop once you have settled the attributes (3.6), can name the component, and can write a 2–3 sentence root-cause summary (no deeper investigation; 3.6 is never skipped), and if the profile cannot be loaded, do not ask anyone anything — record why in Step 5's `profile.not_analysed_reason` and continue to Step 4 on the bug text alone.

1. Find the profile. It is either a profile URL on the bug (`share.firefox.dev`, `profiler.firefox.com`) or an attached profile file: an attachment in the `bmo-to-md` output whose filename or description marks it as a profile and whose type is `.json` or `.json.gz`. Skip this step if the bug has neither. If there are several, prefer the most recent non-obsolete one, and say which you used. A `.json.gz` extension alone does not make an attachment a profile: `memory-report.json.gz` is a memory report (Step 3b).
   Download an attachment with the skill's script, as its own command (not chained with `&&`) and not with `bmo-to-md -a` (that saves the REST JSON wrapper under the attachment's filename, not the file). It caches under `~/.cache/triage-attachments/<bug-number>/` and prints the file path:
   ```bash
   python -I <this skill's directory>/fetch_attachment.py <bug-number> <attachment-id>
   ```
   If the download is not a gzip or JSON file (a login page, a 404), treat it as a failed load below.
2. **If the `profiler-analysis` skill is available to you, use it for this step**: it carries guidance from Firefox performance engineers on reading Gecko profiles, which `profiler-cli` alone does not. Otherwise run `profiler-cli guide` first and read the **entire** output. It is approximately 400 lines. The Bash tool may silently truncate long output, causing you to miss the command reference and analysis patterns that appear later in the guide, so read all of it before proceeding.
3. Load the profile (the URL, or the downloaded file path) with `profiler-cli load`. If it fails because the installed `profiler-cli` is too old for the profile, do NOT upgrade it on your own: it is a global tool on the engineer's machine. Ask the engineer for permission, giving the exact command (`npm install -g @firefox-devtools/profiler-cli@latest`) and the version mismatch, using AskUserQuestion if it is available to you. If they agree, run it and continue. If they decline, or you cannot ask, skip the rest of this step, say in the output that the profile was not analysed and why, and give the upgrade command.
4. Walk through the analysis interactively. Run commands and interpret the output.
5. **Mandatory JS-frame attribution check.** Before describing any JS-heavy stack as "page JS", "site code", or a "third-party" issue — and ALWAYS if the bug summary contains any trigger word: `pdf`, `reader view`, `devtools`, `extension`, `addon`, `webextension`, `newtab`, `about:` — you MUST:
   - Identify the top hot frames in the profile and quote the **exact script URL** (including scheme) for each in your written analysis.
   - Map the URL scheme to a component:
     - `resource://pdf.js/...` → **Firefox :: PDF Viewer**
     - `resource://devtools/...` or `chrome://devtools/...` → **DevTools :: <subcomponent>**
     - `moz-extension://...` → **WebExtensions** (and identify the extension)
     - `resource://activity-stream/...` or `about:newtab` scripts → **Firefox :: New Tab Page**
     - `chrome://...` / other `resource://...` → Firefox-shipped JS; identify the component before blaming the page
     - `https://<site-origin>/...` → site code; only this justifies a webcompat conclusion
   - If you cannot produce a concrete script URL from the profile, you must NOT claim the cause is page JS. Say the attribution is unverified instead.
6. **Settle the attributes against the profile before Step 4.** Go back over the Step 2 attributes and confirm or change each one the profile speaks to, applying `criteria.md` as before:
   - *Impact on browser vs site*: a block on the parent process main thread points to browser impact; jank confined to a content process points to site impact.
   - *Resource impact*: sustained CPU, GPU, or memory use visible across the capture.
   - *Animation*: refresh driver or CSS/Web animation markers driving the work.
   - *Page load impact*: navigation and load markers, and how long they take.
   - *Platforms*: the OS the profile was captured on is a platform where the bug occurs.
   Note in Comment 2 any attribute the profile changed, meaning its value differs from what Step 2 took from the bug text; a confirmed value is not a change. Then use the knowledge for component selection.

**Step 3b: Analyze an attached memory report if available**

Runs whenever the bug has an attachment that is an `about:memory` report (usually `memory-report.json.gz`; its JSON has a top-level `reports` list). It is independent of the profile and runs in agent mode even when `PERF_TRIAGE_SKIP_PROFILE=1`, because that variable only covers profile analysis. Download it as in Step 3.1, then, as a separate command:
```bash
python -I <this skill's directory>/memory_report_summary.py <path printed by fetch_attachment.py>
```
If it exits with "not a memory report", the attachment is not one: treat Step 3b as not applicable. It prints each process's `explicit/` total and largest groups, sorted largest first, and says when no GPU process is in the report. Use it for the following, and only for these:
- *Resource impact*: apply `criteria.md` as before. A large total is not enough; resource use must be what the bug is about. If the report only confirms the value Step 2 already took from the bug, that is not a change.
- *Component*: name the dominant group (e.g. `explicit/gfx` in the main process, `window-objects` in content processes) and the process it is in, then use that for component selection. A group is only grounds to move the bug when it is dominant in the report and its owner is clear (`explicit/gfx` → Graphics, `explicit/window-objects` in content processes is page memory and does not by itself justify a move); otherwise leave the component alone. If the profile points to a different component, the profile wins; put the memory finding in Comment 2.
- A single report shows size, not growth: do not say memory is growing, leaking or unbounded unless the bug text or several reports show it.
- Quote the figures you rely on (process, path, MB). If the report is truncated, anonymised, or lacks the process the bug is about, say so and do not draw conclusions about it.

**Step 4: Run Triage Calculator**
```bash
python "$PERF_TRIAGE_PATH" --platforms <platforms> --browser-impact <type> [other flags]
```

In agent mode, add `--json` so the calculator emits the object Step 5 nests:
```bash
python "$PERF_TRIAGE_PATH" --platforms <platforms> --browser-impact <type> [other flags] --json
```

**Step 5: Output results**

### Established inputs

Both modes report which calculator inputs the bug did not establish: agent mode as `unestablished`, interactive mode as section C. An input is established when the bug, a same-mechanism related bug from Step 2, or the profile (Step 3.6) states the fact the chosen value's criterion turns on: the sites named in the steps to reproduce establish `pages-affected`, the reporter's OS establishes `platforms`, "even typing is slow" establishes `--site-impact noticeable`. Evidence missing only for a higher value does not unestablish the chosen one. An input is unestablished only when that fact is missing and you guessed or fell back to a default: `--reproducible not-attempted`, `--pages-affected unclear`, and `--platforms` filled with the all-desktop (or android) default because the bug gives no platform information. Never treat `chrome` as unestablished for want of a Chrome comparison: `--chrome` is on only when someone reports Chrome shows the same problem, so off is the correct default, not a guess.

### Step 5, agent mode

Run the calculator with `--json` in Step 4 and emit **exactly one JSON object as your entire final message**. No preamble, no fences, no `### Comment N` markers, no prose before or after it.

```json
{
  "calculator": { },
  "severity": "S3",
  "unestablished": [],
  "related": [{"bug": 1234567, "settled": ["platforms", "multiple-reporters"]}],
  "component": {"action": "keep", "name": "Core :: Performance", "basis": "bug", "verified": true, "justification": "One line."},
  "memory_report": null,
  "profile": {
    "url": "https://share.firefox.dev/...",
    "attachment": null,
    "analysed": true,
    "not_analysed_reason": null,
    "changed_attributes": {"browser-impact": "jank"},
    "component": "Core :: DOM: Core & HTML",
    "summary": "Two to three sentences on the hot path and what it implies."
  }
}
```

- `calculator` — the `--json` object from `perf_triage.py`, nested **verbatim and unmodified**. Do not re-key, reformat, or drop fields; it already carries `impact`, `score`, `keywords`, `url`, `comment`, and `selections`. Nesting it whole is what keeps the two outputs from drifting.
- `severity` — one of `S1`, `S2`, `S3`, `S4`. Your judgement; the calculator does not compute it.
- `unestablished` — the inputs that are not established (see Established inputs), given as `perf_triage.py` argument names without `--` (`platforms`, `browser-impact`, `site-impact`, `configuration`, `pageload-impact`, `pages-affected`, `resource-impact`, `animation`, `reproducible`, `multiple-sites`, `multiple-reporters`, `chrome`). `[]` when every input is established.
- `related` — each same-mechanism related bug from Step 2 that changed or established an input: `bug` its number, `settled` the inputs it settled, as the same argument names as `unestablished`. `[]` when none did. A related bug you opened but did not use is left out.
- `component` — the final component recommendation, always present, the same decision as interactive section D: `action` `keep` or `move`; `name` the current `Product :: Component` for `keep`, the target for `move`; `basis` `bug`, `profile` or `memory_report`, whichever decided it (the profile wins a disagreement with the memory report); `verified` false only when the Step 3.5 attribution is unverified, in which case `action` is `keep`; `justification` one line. `profile.component` and `memory_report.component` stay as what each piece of evidence points to.
- `memory_report` — `null` when no memory report was attached or it could not be read. Otherwise: `attachment` its id; `dominant` the largest group as `{"process": "...", "path": "explicit/gfx", "mb": 12098}`; `component` as `Product :: Component` only when it should move, else `null`, exactly as `profile.component`; `summary` 1–2 sentences on what it implies; `changed_attributes` the inputs whose value it changed, keyed as in `profile`.
- `profile` — `null` when no profile (URL or attachment) was found or Step 3 was skipped. Otherwise: `url` the profile URL analysed, or `null`; `attachment` the attachment id when a profile file was analysed, else `null`; `analysed` false with `not_analysed_reason` when loading or analysis failed; `changed_attributes` the inputs Step 3.6 changed, keyed by argument name, with their new values as the `perf_triage.py` argument would take them: a string for single-value inputs (`"jank"`), a list of strings for `platforms` (`["windows", "macos"]`), and `true`/`false` for on/off flags (`animation`, `multiple-sites`, `multiple-reporters`, `chrome`) (an input the profile or report only confirms is not changed: leave it out, and `{}` when none changed); `component` as `Product :: Component` only when it should move, else `null`; `summary` 2–3 sentences, or `null` if not analysed. The Step 3.5 attribution check governs `component` exactly as in interactive mode.

Those seven keys are the whole object.

Before emitting it, write the object with the Write tool to `~/.cache/triage-results/<bug-number>.json` (absolute home path) and run `jq . <that path>` as a separate command. If `jq` reports a parse error, fix the file and run it again; emit the object only once `jq` parses it.

Then stop. Everything below this point is interactive mode only.

### Step 5, interactive mode

This skill runs in a forked context, and its final message is relayed to the user by the parent agent, which can reformat or HTML-escape it. So the full result goes to one file, written with the Write tool to `~/.cache/triage-results/<bug-number>.md` (expand `~` to the absolute home path; Write creates the directory), with these sections in order:

**A. `## Comment 1 (paste into Bugzilla, "Use Markdown" on)`** The calculator's stdout (run without `--show-score`), copied exactly: do not retype, summarize, or HTML-escape it. The calculator URL contains literal `&`; `&amp;` must not appear.

**B. `## Comment 2 (paste into Bugzilla, "Use Markdown" on)`** Only if a profile (Step 3) or memory report (Step 3b) was analyzed; 1–2 paragraphs per analysis. Cover the hot path, key marker counts/durations, what the data implies about root cause, and any attribute the profile changed. Use Markdown that renders in Bugzilla: bare URLs (auto-linkified), backticks for code identifiers, `**bold**` for emphasis, no `#` headings inside the comment. This is a separate comment from Comment 1.

**C** and **D** below, as `## Verify before applying` and `## Triage metadata`.

Your final message is an instruction to the parent agent and nothing else. Begin it with this exact line, filling in the bug number:

> Tell the user the triage is saved at `~/.cache/triage-results/<bug-number>.md` and that the Bugzilla comments should be copied from that file. Then read it with the Read tool and show its full contents unchanged, inside a four-backtick ` ````markdown ` fence. Do not summarize, reformat, or retype it; read the file rather than copying from this message.

Then one line with the impact rating and score.

**C. Verify before applying** (not a comment — for the human running the triage)
List only the inputs that are not established (see Established inputs), each with what would settle it, one line each. Do not list inputs that are backed by the bug, and do not confirm choices you made correctly; a list padded with reassurance gets skimmed. If every input is backed, write one line saying so. Do not omit this section.

**D. Triage metadata** (not a comment — just listed for the human to apply)
- Severity (S1–S4)
- Performance Impact flag (low/medium/high). If you explain what drove it, name only inputs whose multiplier is not ×1 (`--reproducible not-attempted` and `--pages-affected unclear` are ×1 and never raise or lower the score).
- Keywords to add: run the same calculator command again with `--json` and list its `keywords` that the bug does not already have (plain stdout does not print them)
- Component recommendation: output exactly one of
  - `Keep in <current Product :: Component>`, or
  - `Move to <Product :: Component>`

  followed by a one-line justification. Hedged or conditional recommendations ("leave it but consider…", "Firefox-side, maybe webcompat") are NOT permitted — pick one. If the profile attribution from Step 3.5 is unverified, say `Keep in <current>` and note that profile re-analysis is required.
- Related bugs used: each same-mechanism related bug from Step 2 and the inputs it settled, one line each; `None` when none was used.
- Profile not analysed: only when a profile was found but not analysed — the reason, and the upgrade command if `profiler-cli` was too old.

Note: Posting comments and moving bugs is currently manual - you have no write access. (This note is part of the interactive contract; in agent mode it is not emitted, and nothing is written to Bugzilla by this skill in either mode.)

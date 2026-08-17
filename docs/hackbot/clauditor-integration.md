# Running Clauditor Smart Window Mode as a Hackbot Agent

**Status:** Draft for discussion.
**Audience:** Hackbot, Clauditor, and Smart Window engineering teams

## Executive summary

**Smart Window** is an optional window type in Firefox that includes a built-in, AI-powered
assistant to help you work with your tabs and browsing history.

[**Clauditor**](clauditor) is a security agent that hunts for Firefox vulnerabilities and proves them
with a testcase. It has a new **Smart Window mode**: it audits Smart Window's
[security guards](smart-window-security) for private-data leaks and proves a finding with a browser test.

[**Hackbot**](hackbot) is a platform that launches agents on demand. It already runs several Firefox
agents (bug fixing, build repair, test repair). It handles the checkout, the model
credentials, the results, and a human-review step before any change is applied.

We want Hackbot to launch Clauditor's Smart Window mode. The goal is to run the audit
ad-hoc or nightly, surface findings in the Hackbot UI, and let an engineer click a
button to file the bug.

**Why do this.** Smart Window ships an AI assistant that can read a user's tabs,
history, and memories. A bug could leak that private data. So, we want a standing,
automated check for those bugs, running on the same platform the Firefox teams already use.

We use Hackbot's checkout, review, and bug-filing plumbing instead of clauditor's, and 
instead of building our own. The audit runs on a schedule we control, and no bug is filed 
without a human saying yes.

The integration is small. Smart Window mode is the lightest Clauditor mode. It
needs a source checkout and a test build. It does not need the heavy instrumented
builds the memory-corruption modes need. And since Smart Window is a JavaScript feature,
hackbot can even build an **artifact build** of Firefox.

## Existing systems overview

### Clauditor context

Clauditor drives Claude or GPT through an analyzer-verifier loop to find and prove
Firefox vulnerabilities.

- The CLI entry is `clauditor --mode <mode> --target <file-or-dir>`. `--mode` is
  required; there is no default.
- An **analyzer** agent explores the code and forms a hypothesis. A **verifier** agent
  independently checks the finding. This reduces false positives.
- Each mode has its own prompts and its own idea of "proof." `generic` is the mode for
  memory-corruption work: one analyzer covers every vulnerability class and declares
  which class it found. It proves a crash in an ASAN build, so it needs heavy
  instrumented builds and browser or JS-shell evaluator tools. The older single-class
  modes (`vuln`, `js_shell`, `ipc`, `nss`, ...) still exist for comparison against
  `generic`.
- **Smart Window mode sits outside that system.** ([See below](#new-clauditor-smart-window-mode-context)) It has its own analyzer and verifier
  prompts, no entry in the shared vulnerability-class inventory, and no ASAN build. It
  only needs a source checkout and an artifact test build. This makes it stable to
  integrate: it will not shift as the `generic` inventory evolves.
- Clauditor can file bugs. Its cloud filing step (`src/cloud/auto_file.py` in the
  Clauditor repo) supports a `--dry-run` flag that reports what it *would* file without
  filing it. But in our case, **Hackbot will file the bugs - not Clauditor.**

#### New Clauditor Smart Window mode context

Smart Window prevents private-data leaks by blocking cross-origin fetches when two flags 
are present at the same time: `privateData` and `untrustedInput`.

Smart Window trusts some fields of input because it **hardens** its prompts against malicious input.

So, the Clauditor Smart Window mode hunts for a **hardening bypass** in `browser/components/aiwindow`: 
a path where attacker-controlled web content reaches the model as if it were trusted when it should
not be trusted. If it finds one, it proves exfiltration end to end.

What the mode needs to run:

- A Firefox checkout with a **test-capable build** (opt or debug).
- Plain file tools and a shell to write a browser-chrome mochitest and run `./mach test --headless`.
- That's it.

What the mode does **not** need:

- No full build.
- No ASAN instrumented build.
- No browser or JS-shell evaluator tools.

The code under test is JavaScript. The guard files are `.sys.mjs` modules, and the
proof is a browser-chrome mochitest. So a run never needs a C++ compile. It needs only
the JS built and packaged. This is what makes an artifact build the right fit.

This makes Smart Window the easiest Clauditor mode to host elsewhere. It is source plus
an artifact build plus `mach test`.

**The run leaves the checkout clean.** Both the analyzer and the verifier delete their
test file and revert `browser.toml` when they finish. The mochitest is not left behind
in the tree — it travels in Clauditor's structured output and lands in Clauditor's own
result directory. This matters for how Hackbot collects results; see
[Where the results land](#where-the-results-land).

**Both agents run the test.** The verifier does not trust the analyzer's run. It
rewrites the mochitest from the analyzer's output and runs `./mach test` itself. So one
Clauditor run pays for two test runs against the same build.

### Hackbot context

Hackbot is a job launcher. Each agent is a self-contained folder under `agents/<name>/`.

- The platform runs the agent as a **Cloud Run Job**.
- The agent receives one object, `HackbotContext` which carries the prepared source
  checkout, Firefox build paths, Anthropic credentials, and the results plumbing.
- The platform passes per-run inputs as environment variables.
- The agent returns a result object on success and raises to fail. The runtime writes
  `summary.json` and uploads artifacts to GCS.
- Any file the agent changes in the checkout is captured into `changes/changes.patch`.
  Smart Window runs leave the checkout clean, so this patch will be empty for us; the
  agent publishes its files as artifacts instead (`ctx.publish_file`).

#### How runs get triggered today 

Three paths:

1. a direct API POST (`POST /agents/{name}/runs`)
2. a Phabricator webhook
3. a pulse-listener that reacts to CI failures 

**There is no scheduler and no cron.** Nightly runs need an external timer that calls the API.

#### The action-review flow (important for bug filing)

Hackbot separates *recording* an action from *applying* it.

- An agent records an intent with a tool like `bugzilla.create_bug`. Nothing is written
  to Bugzilla. The intent lands in `summary.json` and shows in the UI.
- Auto-apply is **off** by default. The bug sits as a pending action.
- The UI applies it via `POST /runs/{run_id}/actions/apply`. This is the
  "Yes, file this bug" button.
- On apply, the handler files the real bug using the Hackbot token. The
  `bugzilla.create_bug` action accepts a `groups` field, so the bug can be filed into a
  **security-restricted group** rather than a public bug.

This is exactly the review step we want for a private-data leak finding.

## How a run works

A run alternates between the two systems. Each step is marked with the system that
performs it. This sequence is the same across all three integration options below.

1. **Hackbot** prepares the checkout at the target revision (`ctx.prepare_repo()`, from
   the `[source]` declaration).
2. **Hackbot** runs the build (`./mach build` in artifact mode), producing a
   test-capable Firefox.
3. **Clauditor (analyzer)** analyzes the checkout: it writes a browser-chrome mochitest
   and registers it in `browser.toml`.
4. **Clauditor (analyzer)** runs the test (`./mach test <test> --headless`).
   `mach build faster` repackages the JS and test manifests as needed. A failing secure
   assertion is the proof of a leak. The analyzer then reverts its changes.
5. **Clauditor (verifier)** independently rewrites the mochitest and runs it again, then
   reverts. It rejects findings it cannot reproduce. This is the false-positive gate, and
   it means one run pays for two test runs.
6. **Clauditor** writes the results — the mochitest, the test output, and the verifier's
   report — to its output directory. The Firefox checkout is left clean.
7. **Hackbot** captures the outputs: the result files as run artifacts, and the finding
   plus a pending `bugzilla.create_bug` action into `summary.json`.

The result of these steps is a run summary: whether Smart Window has a leak, and if so,
a write-up of the bug and a pending Bugzilla filing an engineer can approve. Everything
downstream — the UI, the review, the filed bug — works off that summary.

## Options for integration

Three ways to link Clauditor into Hackbot, loosest to tightest.

### Recommended: A) CLI wrapper

A new Hackbot agent folder whose image installs Clauditor. The agent's `main()` calls
`ctx.prepare_repo()`, then runs `clauditor --mode smart_window --target <dir>` as a
subprocess. It reads Clauditor's finding from the output directory and translates it
into a Hackbot result and a recorded `bugzilla.create_bug` action.

**A bridge from clauditor results to hackbot results**: The Clauditor subprocess does not know 
about Hackbot's action recorder. So a wrapper performs the handoff:

1. Run Clauditor find-only (or `--dry-run`) so Clauditor files nothing itself.
2. Read the finding from Clauditor's output directory.
3. Publish that directory's files as Hackbot artifacts (`ctx.publish_file`).
4. Record a Hackbot `bugzilla.create_bug` action with the finding, filed into a security
   group.

Clauditor already splits finding from filing, and Hackbot already separates *recording* 
an action from *applying* it, so this is a clean handoff.

#### Where the results land

Clauditor writes its results to `<output>/<target-stem>_<YYYYmmdd_HHMMSS>/`. Point
`--output` at a known directory and read the one child. It contains:

| File | What it is |
|---|---|
| `browser_security_*.js` | The mochitest that proves the leak |
| `crash_stack.txt` | The `mach test` output with the failing secure assertion |
| `analysis.md` / `analysis.json` | The verifier's authoritative report |
| `verifier_verdict-*.md` | The verifier's verdict per iteration |

These files are the deliverable. The wrapper publishes them as artifacts and builds the
bug description from `analysis.md`.

**One gotcha for whoever writes the bridge.** The leak evidence lives in a field named
after crash reports — `crashdata` on the analyzer, `asan_report` on the verifier — which
is why it lands in `crash_stack.txt`. Nothing ASAN is involved. Smart Window mode reuses
the shared schema's evidence slot for its `mach test` output.

- **Pros:** Almost no Clauditor changes. Clauditor stays the source of truth. Fastest to
  ship. Prompt changes on the Clauditor side flow through with no Hackbot work.
- **Cons:** Two config systems sit side by side (Clauditor `.env` and Hackbot env). The
  result-parsing bridge is glue that can drift if Clauditor's output shape changes.

### B) Library embed

The Hackbot agent imports Clauditor's `analyze()` entry and drives it in-process.
Clauditor becomes a dependency in the agent's `pyproject.toml`. The agent records the
`bugzilla.create_bug` action directly from the typed finding — no output-file parsing.

- **Pros:** No subprocess and no result-file parsing. Reuses Clauditor's native
  `claude-agent-sdk` backend, which lines up with Hackbot's Anthropic auth. Structured
  output stays typed end to end. Removes the fragile part of Option A.
- **Cons:** Couples Clauditor's internals to Hackbot's runtime. Clauditor must expose a
  clean entry function that takes its config by argument, not only by env and globals.

### C) Native re-port

Port only the Smart Window prompts into a Hackbot-native agent, built like `bug-fix`,
using `hackbot-runtime` and `agent-tools`. Clauditor is not a runtime dependency. The
prompts are shared or copied.

- **Pros:** Fully idiomatic Hackbot. Gets the action recorder and change
  capture with no adapter.
- **Cons:** Duplicates Clauditor's analyzer-verifier loop and supervisor. Two copies of
  the prompts will diverge. Most work.

## Open questions and decisions

1. **What drives a nightly run?**
   Hackbot has no scheduler. An external timer must call the API once a day. 
   Options:
   * GCP Cloud Scheduler 
   * Taskcluster cron hook.
 
   **Decision needed from Hackbot.**

2. **Bug filing details.** We plan to file into a security-restricted group via a pending
   action an engineer approves. We need:
   * the target product, component, and group, and a named owner who triages the pending bug.

   **Decision needed from Smart Window.**

3. **How does Hackbot get the Clauditor code?**
   Clauditor lives in a separate repo (`MozillaSecurity/clauditor`), not in the Hackbot
   codebase. Options A and B put Clauditor inside the Hackbot agent image, so the image
   build needs a way to pull it. If the repo is private, that pull needs credentials.
   Options:
   * (Recommended) **Install from Git (see below)** — the agent's `pyproject.toml` depends on Clauditor
     at a pinned git ref. Simplest; a private repo needs a build-time deploy token or key.
   * **Publish a package** — Clauditor ships to PyPI or a private Artifact Registry, and
     the agent pins a version. Cleaner versioning; Clauditor must set up publishing.
   * **Vendor the prompts (Option C only)** — no code dependency. Copy the Smart Window
     prompts into Hackbot and keep them in sync by hand.

   **Decision needed from Clauditor + Hackbot.**

   ### Installing from Git

   This fits the flow Hackbot agents already use. Each agent builds its image with
   `uv sync`, and Clauditor is a standard `hatchling` package with a `clauditor` console
   script. The mechanics:

   1. **Declare the dependency.** Add Clauditor to the agent's `pyproject.toml` as a
      pinned git source:

      ```toml
      [tool.uv.sources]
      clauditor = { git = "https://github.com/MozillaSecurity/clauditor.git", rev = "<commit-or-tag>" }
      ```

      with `clauditor` listed under `dependencies`.

   2. **Build installs it.** `uv sync` clones the ref and installs Clauditor into the
      image's virtualenv. The wheel bundles the prompt templates (they live inside the
      `clauditor` package), so the install is self-contained. Option A then has the
      `clauditor` command on `PATH`; Option B can `import clauditor`.

   3. **Pin to a commit or tag**, not a branch, so image builds are reproducible.

   4. **Authenticate the pull without baking the secret in.** The repo is private and the
      build needs read access to it. Mount a read-only GitHub deploy token as a Docker
      build secret (`RUN --mount=type=secret,...`), so the
      credential never lands in an image layer. A fine-grained token scoped to the one
      repo is least privilege.

   One trade-off to note: installing Clauditor pulls its whole dependency tree
   (pydantic-ai, claude-agent-sdk, and the Google, Azure, and Taskcluster libraries it
   uses elsewhere), so the image grows.

   [clauditor]: https://github.com/MozillaSecurity/clauditor
   [hackbot]: https://github.com/mozilla/bugbug/tree/master/agents
   [smart-window-security]: https://docs.google.com/document/d/1NIWj4oqKASpf_vdL1PidX9hkPc7H68u1uDasDS2i1uQ/

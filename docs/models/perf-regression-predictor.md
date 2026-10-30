# Perf Regression Predictor

The Perf Regression Predictor is an inference-only binary transformer
model. It predicts whether a commit is likely to introduce a performance
regression.

The HTTP service resolves a push `(branch, rev)` server-side against its own
local Mercurial clone (the same clone `schedule_tests` uses), loads the full
stack of commits with `automationrelevance`, and scores every commit in the
push separately. The per-commit input is that commit's message plus its diff,
both taken from an `hg export` of the commit. Before inference, leading
bracketed tags, parenthesized tags, and prefixes such as `Bug 123456` or
`Bug #123456` are removed from the first non-empty line of the commit message.
The diff is converted to the structured representation used to train the
checkpoint. The combined text is truncated to the checkpoint's context window
(512 tokens for the current CodeBERT checkpoint). Since nothing past a few
dozen characters per token can reach that window, the model drops the tail of
a large diff (beyond `max_input_chars`) before structuring and tokenizing it,
and the service exports one patch at a time.

Some commits in a push are not meaningful to score and are skipped: merges,
automated `wptsync` imports and commits marked `ignore-this-changeset`. They
are still listed in the result with an `ignored_reason` and `null` scores.
Backouts are scored like any other commit, since a revert is a real code
change that can regress a benchmark, and the training set treats them the same
way. The rules live in `IGNORED_COMMIT_RULES` in `bugbug/perf/commit_risk.py`,
shared with the training set export so the model is never trained on a kind of
commit it does not score; to ignore another kind of commit, add an entry
mapping the reason to a predicate over the hg.mozilla.org `automationrelevance`
changeset. Rules are evaluated in order and the first match wins.

Each scored commit gets its own `risk_score`; the push-level `risk_score` is
the maximum across the scored commits, or `null` when every commit in the push
was ignored.

The `risk_score` is the uncalibrated softmax probability for positive class
`1`. It must not be interpreted as a calibrated probability for operational
decision-making.

## Local inference with the CLI

The CLI runs preprocessing and model inference directly. It does not start the
HTTP service, Redis, an RQ worker, or resolve pushes from a Mercurial clone.

From the Bugbug repository root, run the included sample patch against a local
Hugging Face checkpoint:

```sh
cd /path/to/bugbug

uv run --extra perf-regression-predictor \
  bugbug-predict-perf-regression \
  --model-dir /absolute/path/to/predictor_model \
  --patch-file examples/perf_regression_predictor.patch
```

The sample is a Git `format-patch`, so the command extracts its commit message
automatically. For a raw diff, provide the message directly:

```sh
uv run --extra perf-regression-predictor \
  bugbug-predict-perf-regression \
  --model-dir /absolute/path/to/predictor_model \
  --patch-file /absolute/path/to/change.patch \
  --commit-message "Bug 123456 - Improve rendering performance"
```

Alternatively, use `--commit-message-file /path/to/commit-message.txt`. If no
message argument is provided, the CLI tries Git `format-patch` and Mercurial
export formats. A raw diff without a detectable message is still accepted,
with a warning.

The command prints the predicted binary `class`, both class probabilities in
`prob`, and the uncalibrated positive-class `risk_score`.

## HTTP service

The generic push prediction endpoint (`/{model_name}/predict/push/{branch}/{rev}`)
uses the service's existing Redis/RQ worker and API-key presence check:

```text
GET /perfregressionpredictor/predict/push/{branch}/{rev}
X-Api-Key: ...
```

`branch` is an hg.mozilla.org repository path such as `integration/autoland` or
`try` (the alias `autoland` is accepted for `integration/autoland`), and `rev`
is a changeset in that push. The first request normally returns
`202 {"ready": false}`. Poll the same URL until it returns `200`. The worker
resolves the push from its own local hg clone, so no Phabricator credentials are
needed. If hg.mozilla.org cannot provide the push's stack (for example a
transient error or a lagging mirror) nothing is cached and the next poll
schedules a new attempt, like the test scheduling endpoint. A push with no
commits to analyze yields `{"available": false}`.

See [HTTP service local development](../../http_service/README.perf-regression-predictor.md)
for the complete Docker Compose setup, including the local model mount.

Example result:

```json
{
  "branch": "integration/autoland",
  "rev": "76383a875678",
  "risk_score": 0.75,
  "commits": [
    {
      "node": "76383a875678",
      "prob": [0.25, 0.75],
      "class": 1,
      "risk_score": 0.75,
      "ignored_reason": null
    },
    {
      "node": "0c1d2e3f4a5b",
      "prob": null,
      "class": null,
      "risk_score": null,
      "ignored_reason": "merge"
    }
  ],
  "extra_data": {
    "model_name": "Perf Regression Predictor",
    "model_version": null,
    "max_length": 512,
    "calibrated": false,
    "commit_count": 2,
    "ignored_commit_count": 1
  }
}
```

The push-level `risk_score` is the maximum of the per-commit `risk_score`
values of the scored commits in `commits`. Ignored commits have
`ignored_reason` set and `null` for `prob`, `class` and `risk_score`; a `null`
means "not scored", not "no risk". When every commit was ignored the push-level
`risk_score` is `null` too.

## Model artifact

This checkpoint is trained outside Bugbug, so no `bugbug-train` workflow is
registered for it and no train task publishes an artifact for it to the
Taskcluster index. Instead the model class declares where its archive is
published:

```python
class PerfRegressionPredictorModel(Model):
    training_supported = False
    artifact_url = "https://storage.googleapis.com/.../perf-regression-predictor-v1.tar.zst"
```

`utils.download_model()` fetches that URL for any model declaring an
`artifact_url` and falls back to the Taskcluster index for the rest, so every
caller (including the http service's `download_models()`) downloads this model
alongside the others with no special casing.

The archive must be a `.tar.zst` holding a single directory named
`perfregressionpredictormodel`, which is what the rest of the service expects
on disk. When the model is retrained, publish it under a new versioned path
and update `artifact_url`, rather than overwriting the existing object: that
keeps a change of the deployed model a reviewable diff.

Once training moves into Bugbug, the model can implement `train()`, set
`training_supported = True` and drop `artifact_url`, at which point it is
published by a normal `bugbug-train` task like every other model.

For local Docker development before the artifact is published, mount the local
checkpoint at `/code/perfregressionpredictormodel` in the background
worker. This is the same fixed-directory convention used by the other models.

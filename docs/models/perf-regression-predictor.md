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
(512 tokens for the current CodeBERT checkpoint).

Each commit gets its own `risk_score`; the push-level `risk_score` is the
maximum across the commits in the stack.

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

The endpoint uses the service's existing Redis/RQ worker and API-key presence
check:

```text
GET /perfregressionpredictor/predict/push/{branch}/{rev}
X-Api-Key: ...
```

`branch` is an hg.mozilla.org repository path such as `integration/autoland` or
`try` (the alias `autoland` is accepted for `integration/autoland`), and `rev`
is a changeset in that push. The first request normally returns
`202 {"ready": false}`. Poll the same URL until it returns `200`. The worker
resolves the push from its own local hg clone, so no Phabricator credentials are
needed; if the push cannot be found the result is `{"available": false}`.

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
      "risk_score": 0.75
    }
  ],
  "extra_data": {
    "model_name": "Perf Regression Predictor",
    "model_version": null,
    "max_length": 512,
    "calibrated": false,
    "commit_count": 1
  }
}
```

The push-level `risk_score` is the maximum of the per-commit `risk_score`
values in `commits`.

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

`download_models()` fetches that URL for any model declaring an `artifact_url`
and falls back to the Taskcluster index for the rest, so the background worker
downloads this model alongside the others with no special casing.

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

# Perf Regression Predictor

The Perf Regression Predictor is an inference-only binary transformer
model. It predicts whether a public Phabricator diff is likely to
introduce a performance regression.

The input is the commit message from the diff's `commits` attachment plus the
raw diff. If a diff has multiple uploaded local commits, each message is cleaned
independently and the messages are separated by blank lines. If Phabricator did
not retain commit metadata, the revision title and summary are used as a
fallback. Before inference, leading bracketed tags, parenthesized tags, and
prefixes such as `Bug 123456` or `Bug #123456` are removed from the first
non-empty line of each commit message.
The diff is converted to the structured representation used to train the
checkpoint. The combined text is truncated to the checkpoint's context window
(512 tokens for the current CodeBERT checkpoint).

The `risk_score` is the uncalibrated softmax probability for positive class
`1`. It must not be interpreted as a calibrated probability for operational
decision-making.

## Local inference with the CLI

The CLI runs preprocessing and model inference directly. It does not start the
HTTP service, Redis, an RQ worker, or fetch data from Phabricator.

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
GET /perfregressionpredictor/predict/phabricator/{diff_id}
X-Api-Key: ...
```

The first request normally returns `202 {"ready": false}`. Poll the same URL
until it returns `200`. The worker requires `PHABRICATOR_API_KEY`; a custom
Phabricator host can be set with `PHABRICATOR_URL`.

See [HTTP service local development](../../http_service/README.perf-regression-predictor.md)
for the complete Docker Compose setup, including the local model mount and
secret file.

Example result:

```json
{
  "revision_id": 123456,
  "diff_id": 789012,
  "prob": [0.25, 0.75],
  "class": 1,
  "risk_score": 0.75,
  "extra_data": {
    "model_name": "Perf Regression Predictor",
    "model_version": null,
    "max_length": 512,
    "calibrated": false,
    "commit_message_source": "diff_metadata",
    "commit_message_count": 1
  }
}
```

Only public revisions are processed, and the worker verifies that the diff
belongs to a public revision. The `revision_id` in the response is derived from
the diff metadata.

## Model artifact

Production follows the existing Bugbug model-artifact convention. The
checkpoint directory must be named `perfregressionpredictormodel` and
published as:

```text
public/perfregressionpredictormodel.tar.zst
```

under the indexed Taskcluster namespace
`project.bugbug.train_perfregressionpredictor.<version>`. For this first
iteration, the archive can be created and published by a one-off Taskcluster
task; no `bugbug-train` workflow is registered for this model. The standard
background-worker image then downloads it alongside the other model artifacts.

For local Docker development before the artifact is published, mount the local
checkpoint at `/code/perfregressionpredictormodel` in the background
worker. This is the same fixed-directory convention used by the other models.

#!/usr/bin/env bash
#
# Deploy the build-failure Pulse listener to Cloud Run as a worker pool.
#
# A worker pool runs an always-on, non-request workload (no HTTP port). This
# service consumes Taskcluster build-failure pulse messages and triggers the
# build-repair hackbot agent via the hackbot-api.
#
# Prereqs (one-time):
#   gcloud auth login
#   gcloud config set project <PROJECT_ID>
#   gcloud components install beta   # worker pools live under the beta track
#   gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
#       cloudbuild.googleapis.com secretmanager.googleapis.com
#
# Secrets — the values use the same env var names as the app's .env, so
# `source .env` populates them. Any secret missing from Secret Manager is
# created from its value; existing secrets are never overwritten (rotate with
# `gcloud secrets versions add`):
#   PULSE_PASSWORD    -> secret `pulse-password`
#   SENDGRID_API_KEY  -> secret `sendgrid-api-key`
#   HACKBOT_API_KEY   -> secret `external-api-key` (shared with hackbot-api)
#
# Usage:
#   source .env   # provides PULSE_PASSWORD, HACKBOT_API_KEY, SENDGRID_API_KEY, etc.
#   PROJECT=my-proj REGION=us-central1 \
#   HACKBOT_API_URL=https://hackbot-api-xxxx.run.app \
#   HACKBOT_UI_URL=https://hackbot-ui-xxxx.run.app \
#   PULSE_USER=my-pulse-user NOTIFICATION_SENDER=<verified SendGrid sender> \
#   NOTIFICATION_TEAM_EMAIL=hackbot-developers@mozilla.com \
#   ./deploy.sh
set -euo pipefail

PROJECT="${PROJECT:?set PROJECT to your GCP project id}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-hackbot-pulse-listener}"
REPO="${REPO:-hackbot}"
HACKBOT_API_URL="${HACKBOT_API_URL:?set HACKBOT_API_URL to the hackbot-api base URL}"
HACKBOT_UI_URL="${HACKBOT_UI_URL:?set HACKBOT_UI_URL to the hackbot-ui base URL}"
PULSE_USER="${PULSE_USER:?set PULSE_USER (https://pulseguardian.mozilla.org)}"
WATCHED_REPOS="${WATCHED_REPOS:-autoland}"
NOTIFICATION_SENDER="${NOTIFICATION_SENDER:?set NOTIFICATION_SENDER (verified SendGrid sender)}"
NOTIFICATION_TEAM_EMAIL="${NOTIFICATION_TEAM_EMAIL:-}"

SA_NAME="${SA_NAME:-hackbot-pulse-listener-run}"
SA_EMAIL="${SA_EMAIL:-${SA_NAME}@${PROJECT}.iam.gserviceaccount.com}"

# Secret Manager secret names (where the values live).
PULSE_SECRET="${PULSE_SECRET:-pulse-password}"
API_KEY_SECRET="${API_KEY_SECRET:-external-api-key}"
SENDGRID_SECRET="${SENDGRID_SECRET:-sendgrid-api-key}"

# Secret values, using the same names as the app's .env so `source .env` works.
# Used only to seed a secret that does not exist yet (never overwrites).
PULSE_PASSWORD="${PULSE_PASSWORD:-}"
HACKBOT_API_KEY="${HACKBOT_API_KEY:-}"
SENDGRID_API_KEY="${SENDGRID_API_KEY:-}"

IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${SERVICE}:latest"
# Build context is the repo root (the Dockerfile needs the workspace lock files).
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "==> Ensuring runtime service account '${SA_EMAIL}' exists"
gcloud iam service-accounts describe "${SA_EMAIL}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="Hackbot Pulse Listener (Cloud Run runtime)"

echo "==> Ensuring secrets exist (seeding from env when missing)"
ensure_secret() {  # secret_name value
  local name="$1" value="${2:-}"
  if gcloud secrets describe "$name" >/dev/null 2>&1; then
    return 0
  fi
  if [ -z "$value" ]; then
    echo "ERROR: secret '$name' is missing and no value was provided to create it" >&2
    exit 1
  fi
  printf '%s' "$value" | gcloud secrets create "$name" --data-file=-
}
ensure_secret "${PULSE_SECRET}" "${PULSE_PASSWORD}"
ensure_secret "${API_KEY_SECRET}" "${HACKBOT_API_KEY}"
ensure_secret "${SENDGRID_SECRET}" "${SENDGRID_API_KEY}"

echo "==> Granting the SA read access to its secrets"
for s in "${PULSE_SECRET}" "${API_KEY_SECRET}" "${SENDGRID_SECRET}"; do
  gcloud secrets add-iam-policy-binding "$s" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role=roles/secretmanager.secretAccessor >/dev/null
done

echo "==> Ensuring Artifact Registry repo '${REPO}' exists in ${REGION}"
gcloud artifacts repositories describe "${REPO}" --location="${REGION}" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "${REPO}" \
    --repository-format=docker --location="${REGION}" \
    --description="Hackbot container images"

echo "==> Building & pushing image with Cloud Build: ${IMAGE}"
gcloud builds submit "${ROOT_DIR}" \
  --config <(printf 'steps:\n- name: gcr.io/cloud-builders/docker\n  env: ["DOCKER_BUILDKIT=1"]\n  args: ["build","-t","%s","-f","services/%s/Dockerfile","."]\nimages: ["%s"]\n' "${IMAGE}" "${SERVICE}" "${IMAGE}")

echo "==> Deploying worker pool"
ENV_VARS="HACKBOT_API_URL=${HACKBOT_API_URL},HACKBOT_UI_URL=${HACKBOT_UI_URL}"
ENV_VARS="${ENV_VARS},ENVIRONMENT=production"
ENV_VARS="${ENV_VARS},PULSE_USER=${PULSE_USER},WATCHED_REPOS=${WATCHED_REPOS}"
ENV_VARS="${ENV_VARS},NOTIFICATION_SENDER=${NOTIFICATION_SENDER}"
ENV_VARS="${ENV_VARS},NOTIFICATION_TEAM_EMAIL=${NOTIFICATION_TEAM_EMAIL}"

gcloud beta run worker-pools deploy "${SERVICE}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --scaling 1 \
  --service-account "${SA_EMAIL}" \
  --set-env-vars "${ENV_VARS}" \
  --set-secrets "PULSE_PASSWORD=${PULSE_SECRET}:latest,HACKBOT_API_KEY=${API_KEY_SECRET}:latest,SENDGRID_API_KEY=${SENDGRID_SECRET}:latest"

echo "==> Deployed worker pool '${SERVICE}'"

#!/usr/bin/env bash
#
# Deploy the Hackbot Console to Cloud Run.
#
# Prereqs (one-time):
#   gcloud auth login
#   gcloud config set project <PROJECT_ID>
#   gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
#       cloudbuild.googleapis.com secretmanager.googleapis.com
#
# Secrets (one-time) — store sensitive values in Secret Manager.
# The hackbot-api X-API-Key reuses the EXISTING `external-api-key` secret
# (override with API_KEY_SECRET=...); only these two are UI-specific:
#   printf '%s' "$(openssl rand -base64 32)" | gcloud secrets create hackbot-ui-auth-secret   --data-file=-
#   printf '%s' '<google client secret>'      | gcloud secrets create hackbot-ui-google-secret --data-file=-
#
# This script creates a dedicated least-privilege runtime service account
# (hackbot-ui-run@...) and grants it secretAccessor on the three secrets it reads.
# The identity running this script needs roles/run.admin, roles/iam.serviceAccountUser
# on that SA (to deploy a service that runs as it), and rights to manage secrets.
#
# Usage:
#   PROJECT=my-proj REGION=us-central1 \
#   HACKBOT_API_URL=https://hackbot-api-xxxx.run.app \
#   GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com \
#   ./deploy.sh
#
# After the FIRST deploy, copy the printed service URL into BETTER_AUTH_URL
# (and the Google OAuth redirect URI), then re-run this script.
set -euo pipefail

PROJECT="${PROJECT:?set PROJECT to your GCP project id}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-hackbot-ui}"
REPO="${REPO:-hackbot}"
HACKBOT_API_URL="${HACKBOT_API_URL:?set HACKBOT_API_URL to the hackbot-api base URL}"
GOOGLE_CLIENT_ID="${GOOGLE_CLIENT_ID:?set GOOGLE_CLIENT_ID}"
# BETTER_AUTH_URL is optional on the first deploy; set it on the second pass.
BETTER_AUTH_URL="${BETTER_AUTH_URL:-}"

# Dedicated, least-privilege runtime identity for the UI (only needs to read
# its secrets). Override SA_NAME/SA_EMAIL to use an existing account.
SA_NAME="${SA_NAME:-hackbot-ui-run}"
SA_EMAIL="${SA_EMAIL:-${SA_NAME}@${PROJECT}.iam.gserviceaccount.com}"

# Secret Manager secret names. The API key reuses the existing shared secret.
AUTH_SECRET="${AUTH_SECRET:-hackbot-ui-auth-secret}"
API_KEY_SECRET="${API_KEY_SECRET:-external-api-key}"
GOOGLE_SECRET="${GOOGLE_SECRET:-hackbot-ui-google-secret}"

IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${SERVICE}:latest"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Ensuring runtime service account '${SA_EMAIL}' exists"
gcloud iam service-accounts describe "${SA_EMAIL}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="Hackbot Console (Cloud Run runtime)"

echo "==> Granting the SA read access to its secrets"
for s in "${AUTH_SECRET}" "${API_KEY_SECRET}" "${GOOGLE_SECRET}"; do
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
gcloud builds submit "${SCRIPT_DIR}" --tag "${IMAGE}"

echo "==> Deploying to Cloud Run"
ENV_VARS="HACKBOT_API_URL=${HACKBOT_API_URL},GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}"
[ -n "${BETTER_AUTH_URL}" ] && ENV_VARS="${ENV_VARS},BETTER_AUTH_URL=${BETTER_AUTH_URL}"

gcloud run deploy "${SERVICE}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --service-account "${SA_EMAIL}" \
  --set-env-vars "${ENV_VARS}" \
  --set-secrets "BETTER_AUTH_SECRET=${AUTH_SECRET}:latest,HACKBOT_API_KEY=${API_KEY_SECRET}:latest,GOOGLE_CLIENT_SECRET=${GOOGLE_SECRET}:latest"

URL="$(gcloud run services describe "${SERVICE}" --region "${REGION}" --format='value(status.url)')"
echo
echo "==> Deployed: ${URL}"
if [ -z "${BETTER_AUTH_URL}" ]; then
  echo "NEXT STEPS (first deploy):"
  echo "  1. Re-run with BETTER_AUTH_URL=${URL}"
  echo "  2. Add this to your Google OAuth client's authorized redirect URIs:"
  echo "       ${URL}/api/auth/callback/google"
fi

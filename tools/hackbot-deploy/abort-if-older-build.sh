#!/usr/bin/env bash
# Fail this build if an older build of the same trigger is still running.
#
# Deploys of one agent or service aren't serialised, and two of them updating
# the same Job or service at once can leave it running one image while the
# deployment record names the other. So the newer build stops before doing any
# work; once the older one finishes, rerun the trigger to deploy the newer
# commit.
#
# Usage: abort-if-older-build.sh BUILD_ID LOCATION
set -euo pipefail

build_id=$1
location=$2

# csv rather than value: a tab-separated empty trigger id would be skipped.
IFS=, read -r trigger created < <(
  gcloud builds describe "$build_id" --region "$location" \
    --format='csv[no-heading](buildTriggerId,createTime)'
)
# Started by hand rather than by a trigger: nothing to compare against.
[ -n "$trigger" ] || exit 0
older=$(
  gcloud builds list --ongoing --region "$location" \
    --filter="buildTriggerId=$trigger AND createTime<'$created'" \
    --format='value(id)'
)
if [ -n "$older" ]; then
  echo "An older build of this trigger is still running: $older" >&2
  echo "Rerun this trigger once it finishes to deploy this commit." >&2
  exit 1
fi

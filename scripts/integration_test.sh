#!/bin/bash
set -euox pipefail

# Script that runs the whole data pipeline as fast as possible to validate
# that every part is working with the others

# Supposed to be run from the repository root directory

# Remove the models and any old data
rm defectenhancementtaskmodel* || true;
rm backout* || true;
rm -Rf data || true;

ls -lh

# First retrieve a subset of bug data
bugbug-data-bugzilla --limit 500
ls -lh
ls -lh data

# The bug data force download the commit DB
# Removes it to ensure the commit retrieval work as expected
rm data/commit*

# Then generate a test dataset of fixed inline comments
bugbug-fixed-comments --limit 150
ls -lh
ls -lh data

# Remove DB to ensure it works as expected
rm data/fixed_comments.json

# Then retrieve a subset of commit data
bugbug-data-commits --limit 500 "${CACHE_DIR:-cache}"
test -d ${CACHE_DIR:-cache}/mozilla-central
ls -lh
ls -lh data


# Then train a bug model
bugbug-train defectenhancementtask --limit 500 --no-download

# Then train a commit model
bugbug-train backout --limit 30000 --no-download

# Then spin the http service up
# This part duplicates the http service Dockerfiles because we cannot easily spin Docker containers
# up on Taskcluster
cp VERSION http_service/VERSION
pip install --disable-pip-version-check --quiet --no-cache-dir ./http_service

export REDIS_URL=redis://localhost:6379/4

# Start Redis
redis-server >/dev/null 2>&1 &
redis_pid=$!

sleep 1

# Uncomment following line to clean up the redis-server
redis-cli -n 4 FLUSHDB

# Start the http server
gunicorn -b 127.0.0.1:8000 bugbug_http.app --preload --timeout 30 -w 3 &
gunicorn_pid=$!

# Start the background worker
env BUGBUG_ALLOW_MISSING_MODELS=1 BUGBUG_REPO_DIR=${CACHE_DIR:-cache}/mozilla-central bugbug-http-worker high default low &
worker_pid=$!

# Ensure we take down the containers at the end
trap 'kill $gunicorn_pid && kill $worker_pid && kill $redis_pid' EXIT

# Then check that we can correctly classify a bug
sleep 10 && python http_service/tests/test_integration.py

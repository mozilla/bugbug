#!/bin/bash
set -euox pipefail

# Script that runs the whole data pipeline as fast as possible to validate
# that every part is working with the others

# Supposed to be run from the repository root directory

cd http_service/models/;

# Remove the models and any old data
rm defectenhancementtaskmodel* || true;
rm backout* || true;
rm -Rf data || true;

# First retrieve a subset of bugs data
# TODO: Let the script download the previous DB as it should be pretty fast?
bugbug-data-bugzilla --limit 100

# Then retrieve a subset of commit data
mkdir -p cache
bugbug-data-commits --limit 100 cache

# Then train a bug model
bugbug-train --limit 500 --no-download defectenhancementtask

# Then train a commit model
bugbug-train --limit 30000 --no-download backout

# Then spin the http service up
# This part duplicates the http service Dockerfiles because we cannot easily spin Docker containers
# up on Taskcluster
cd ../
pip install -r requirements.txt
cd ../
pwd
ls http_service/models/

export REDIS_URL=redis://localhost:6379/4

# Start Redis
redis-server >/dev/null 2>&1 &
redis_pid=$!

sleep 1

# Uncomment following line to clean up the redis-server
# redis-cli -u $REDIS_URL FLUSHDB

# Start the http server
gunicorn -b 127.0.0.1:8000 http_service.app --preload --timeout 30 -w 3 &
gunicorn_pid=$!

# Start the background worker
env BUGBUG_ALLOW_MISSING_MODELS=1 python http_service/worker.py high default low &
worker_pid=$!

# Ensure we take down the containers at the end
trap 'kill $gunicorn_pid && kill $worker_pid && kill $redis_pid' EXIT

# Then check that we can correctly classify a bug
sleep 10 && python http_service/tests/test_integration.py
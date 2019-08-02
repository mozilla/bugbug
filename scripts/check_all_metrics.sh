#!/bin/bash
set -euox pipefail

all_metrics="backout component defectenhancementtask duplicate regression regressionrange regressor stepstoreproduce tracking"

# Create the directory if they do not exists
mkdir -p metrics
mkdir -p metrics_out

# Retrieve all metrics
for metric_name in $all_metrics; do
    python scripts/retrieve_training_metrics.py -d metrics "$metric_name" 2019
done

# Then analyze them
python scripts/analyze_training_metrics.py metrics metrics_out
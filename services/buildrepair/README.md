# Build Repair Agent

It can automatically analyze a build failure in Firefox and propose a fix.

## Evaluation

Weights and Biases Weave [dashboard](https://wandb.ai/moz-bugbug/bugbug-build-repair-eval/weave/evaluations).

To run locally:

1. Clone Firefox to a separate directory

2. Prepare the Docker image

Pull the base Docker image to build Firefox from Taskcluster.
From the Firefox repo run:

```bash
./mach taskgraph load-image --task-id aQDejwXUQsSHxvwE2qQcQg
```

Make sure to have enough resources available for the Docker engine (at least 16gb RAM and 128GB disk, better 256GB).

3. Set environment variables

```bash
# Full path to the Firefox repo
export FIREFOX_GIT_REPO=$(pwd)
export ANTHROPIC_API_KEY=<The key to run Agents SDK>
export WANDB_API_KEY=<Weights and Biases key for Weave>
# If on Mac with ARM CPU
export DOCKER_DEFAULT_PLATFORM=linux/amd64
```

4. `cd` to this repo

5. (Optional) Prebuild the Docker image and use `image: build-repair-debian-base` in `docker-compose.dev.yml`

```bash
docker build -t build-repair-debian-base -f services/buildrepair/Dockerfile .
```

6. Attach to the container by running:

```bash
docker compose -f services/buildrepair/docker-compose.dev.yml run build-repair
```

7. Run the evaluation script.

To test:

```bash
/opt/venv/bin/python scripts/build_repair_eval.py --no-try-push --limit 1
```

To run full evaluation (with 3 trials):

```bash
/opt/venv/bin/python scripts/build_repair_eval.py --no-try-push --parallellism 8 --trials 3
```

It will run each of 85 examples from the evaluation dataset 3 times.
It will build Firefox each time with the proposed fix, then write results to Weave.

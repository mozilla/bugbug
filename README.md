# bugbug

[![Task Status](https://community-tc.services.mozilla.com/api/github/v1/repository/mozilla/bugbug/master/badge.svg)](https://community-tc.services.mozilla.com/api/github/v1/repository/mozilla/bugbug/master/latest)
[![codecov](https://codecov.io/gh/mozilla/bugbug/branch/master/graph/badge.svg)](https://codecov.io/gh/mozilla/bugbug)
<a href="https://chat.mozilla.org/#/room/#bugbug:mozilla.org" target="_blank">
<img src="https://img.shields.io/badge/chat%20on%20[m]-%23bugbug%3Amozilla.org-blue">
</a>

Bugbug aims at leveraging machine learning techniques to help with bug and quality management, and other software engineering tasks (such as test selection and defect prediction).

Chat with us in the [bugbug](https://chat.mozilla.org/#/room/#bugbug:mozilla.org) Matrix room.

More information on the Mozilla Hacks blog:

- https://hacks.mozilla.org/2020/07/testing-firefox-more-efficiently-with-machine-learning/
- https://hacks.mozilla.org/2019/04/teaching-machines-to-triage-firefox-bugs/

## Classifiers

- **assignee** - The aim of this classifier is to suggest an appropriate assignee for a bug.

- **backout** - The aim of this classifier is to detect patches that might be more likely to be backed-out (because of build or test failures). It could be used for test prioritization/scheduling purposes.

- **bugtype** - The aim of this classifier is to classify bugs according to their type. The labels are gathered automatically from bugs: right now they are "crash/memory/performance/security". The plan is to add more types after manual labeling.

- **component** - The aim of this classifier is to assign product/component to (untriaged) bugs.

- **defect vs enhancement vs task** - Extension of the **defect** classifier to detect differences also between feature requests and development tasks.

- **defect** - Bugs on Bugzilla aren't always bugs. Sometimes they are feature requests, refactorings, and so on. The aim of this classifier is to distinguish between bugs that are actually bugs and bugs that aren't. The dataset currently contains 2110 bugs, the accuracy of the current classifier is ~93% (precision ~95%, recall ~94%).

- **devdocneeded** - The aim of this classifier is to detect bugs which should be documented for developers.

- **duplicate** - The aim of this classifier is to detect duplicate bugs.

- **qaneeded** - The aim of this classifier is to detect bugs that would need QA verification.

- **regression vs non-regression** - Bugzilla has a `regression` keyword to identify bugs that are regressions. Unfortunately it isn't used consistently. The aim of this classifier is to detect bugs that are regressions.

- **regressionrange** - The aim of this classifier is to detect regression bugs that have a regression range vs those that don't.

- [**regressor**](docs/models/regressor.md) - The aim of this classifier is to detect patches which are more likely to cause regressions. It could be used to make riskier patches undergo more scrutiny.

- **spam** - The aim of this classifier is to detect bugs which are spam.

- **stepstoreproduce** - The aim of this classifier is to detect bugs that have steps to reproduce vs those that don't.

- **testfailure** - The aim of this classifier is to detect patches that might be more likely to cause test failures.

- **testselect** - The aim of this classifier is to select relevant tests to run for a given patch.

- **tracking** - The aim of this classifier is to detect bugs to track.

- **uplift** - The aim of this classifier is to detect bugs for which uplift should be approved and bugs for which uplift should not be approved.

## Setup and Prerequisites

Install the Python dependencies:

```
pip3 install -r requirements.txt
```

You may also need `pip install -r test-requirements.txt`. Depending on the parts of bugbug you want to run, you might need to install dependencies from other requirement files (find them with `find . -name "*requirements*"`).

Currently, Python 3.7+ is required. You can double check the version we use by looking at setup.py.

Also, libgit2 (needs [v1.0.0](https://github.com/libgit2/libgit2/releases/tag/v1.0.0), only in [experimental on Debian](https://wiki.debian.org/DebianExperimental)), **might** be required (if you can't install it, skip this step).

```
sudo apt-get -t experimental install libgit2-dev
```

### Auto-formatting

This project is using [pre-commit](https://pre-commit.com/). Please run `pre-commit install` to install the git pre-commit hooks on your clone.

Every time you will try to commit, pre-commit will run checks on your files to make sure they follow our style standards and they aren't affected by some simple issues. If the checks fail, pre-commit won't let you commit.

## Usage

### Training

Run the `trainer.py` script with the command `python -m scripts.trainer` (with `--help` to see the required and optional arguments of the command) to perform training (warning this takes 30min+).

### Testing

To use a model to classify a given bug, you can run `python -m scripts.bug_classifier MODEL_NAME --bug-id ID_OF_A_BUG_FROM_BUGZILLA`. N.B.: If you run the classifier script without training a model first, it will automatically download an already trained model.

### Example for the "defect" model

**training** To train the model for mode `defect`:

    python3 -m scripts.trainer defect

**testing** To use the model to classify a given bug, you can run `python -m scripts.bug_classifier defect --bug-id ID_OF_A_BUG_FROM_BUGZILLA`.

### Running the repository mining script

Note: This section is only necessary if you want to perform changes to the repository mining script. Otherwise, you can simply use the commits data we generate automatically.

1. Clone https://hg.mozilla.org/mozilla-central/.
2. Run `./mach vcs-setup` in the directory where you have cloned mozilla-central.
3. Enable the extensions mentioned in [infra/hgrc](https://github.com/mozilla/bugbug/blob/master/infra/hgrc). For example, if you are on Linux, you can add `firefoxtree` to the extensions section of the `~/.hgrc` file as:
   ```
   firefoxtree = ~/.mozbuild/version-control-tools/hgext/firefoxtree
   ```
4. Run the `repository.py` script, with the only argument being the path to the mozilla-central repository.

Note: If you run into problems, it's possible the version of Mercurial you are using is not supported. Check the Docker definition at infra/dockerfile.commit_retrieval to see what we are using in production.

Note: the script will take a long time to run (on my laptop more than 7 hours). If you want to test a simple change and you don't intend to actually mine the data, you can modify the repository.py script to limit the number of analyzed commits. Simply add `limit=1024` to the call to the `log` command.

## Structure of the project

- `bugbug/labels` contains manually collected labels;
- `bugbug/db.py` is an implementation of a really simple JSON database;
- `bugbug/bugzilla.py` contains the functions to retrieve bugs from the Bugzilla tracking system;
- `bugbug/repository.py` contains the functions to mine data from the mozilla-central (Firefox) repository;
- `bugbug/bug_features.py` contains functions to extract features from bug/commit data;
- `bugbug/model.py` contains the base class that all models derive from;
- `bugbug/models` contains implementations of specific models;
- `bugbug/nn.py` contains utility functions to include Keras models into a scikit-learn pipeline;
- `bugbug/utils.py` contains misc utility functions;
- `bugbug/nlp` contains utility functions for NLP;
- `bugbug/labels.py` contains utility functions for handling labels;
- `bugbug/bug_snapshot.py` contains a module to play back the history of a bug.

## Using bugbug for non-Mozilla projects

Bugbug is focussing on Mozilla use-cases for Firefox and Bugzilla.
However, we will be happy to accept pull requests adding support for other projects or bug trackers.

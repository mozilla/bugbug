# bugbug

## Classifiers
- **bug vs feature** - Bugs on Bugzilla aren't always bugs. Sometimes they are feature requests, refactorings, and so on. The aim of this classifier is to distinguish between bugs that are actually bugs and bugs that aren't. The dataset currently contains 2110 bugs, the accuracy of the current classifier is ~93% (precision ~95%, recall ~94%).

- **defect vs feature vs task** - Extension of the previous classifier to detect differences also between feature requests and development tasks.

- **component** - The aim of this classifier is to assign product/component to (untriaged) bugs.

- **regression vs non-regression** - Bugzilla has a `regression` keyword to identify bugs that are regressions. Unfortunately it isn't used consistently. The aim of this classifier is to detect bugs that are regressions.

- **tracking** - The aim of this classifier is to detect bugs to track.

- **uplift** - The aim of this classifier is to detect bugs for which uplift should be approved and bugs for which uplift should not be approved.

- **devdocneeded** - The aim of this classifier is to detect bugs which should be documented for developers.

- **qaneeded** - The aim of this classifier is to detect bugs that would need QA verification.

- **bugtype** - The aim of this classifier is to classify bugs according to their type.

## Setup

Run `pip install -r requirements.txt` and `pip install -r test-requirements.txt`

If you update the bugs database, run `xz -v9 -k data/bugs.json`.
If you update the commits database, run `xz -v9 -k data/commits.json`.


## Usage

Run the `run.py` script to perform training / classification. The first time `run.py` is executed, the `--train` argument should be used to automatically download databases containing bugs and commits data.


### Running the repository mining script

1. Clone https://hg.mozilla.org/mozilla-central/.
2. Run `./mach vcs-setup` in the directory where you have cloned mozilla-central.
3. Enable the pushlog, hgmo and mozext extensions. For example, if you are on Linux, add the following to the extensions section of the `~/.hgrc` file:
    ```
    pushlog = ~/.mozbuild/version-control-tools/hgext/pushlog
    hgmo = ~/.mozbuild/version-control-tools/hgext/hgmo
    mozext = ~/.mozbuild/version-control-tools/hgext/mozext
    firefoxtree = ~/.mozbuild/version-control-tools/hgext/firefoxtree
    ```
3. Run the `repository.py` script, with the only argument being the path to the mozilla-central repository.

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

## Auto-formatting setup

This project is using [pre-commit](https://pre-commit.com/). Please run `pre-commit install` to install the git pre-commit hooks on your clone.

Then every time you will try to commit, it will check that the files are correctly formatted before letting you commit.

# bugbug

## Classifiers
- **bug vs feature** - Bugs on Bugzilla aren't always bugs. Sometimes they are feature requests, refactorings, and so on. The aim of this classifier is to distinguish between bugs that are actually bugs and bugs that aren't. The dataset currently contains 2110 bugs, the accuracy of the current classifier is ~93% (precision ~95%, recall ~94%).

- **regression vs non-regression** - Bugzilla has a `regression` keyword to identify bugs that are regressions. Unfortunately it isn't used consistently. The aim of this classifier is to detect bugs that are regressions.

- **tracking** - The aim of this classifier is to detect bugs to track.

- **uplift** - The aim of this classifier is to detect bugs for which uplift should be approved and bugs for which uplift should not be approved.


## Setup

Run `pip install -r requirements.txt` and `pip install -r test-requirements.txt`

If you update the bugs database, run `xz -v9 -k data/bugs.json`.
If you update the commits database, run `xz -v9 -k data/commits.json`.


## Usage

Run the `run.py` script to perform training / classification. The first time `run.py` is executed, the `--download` argument should be used to download databases containing bugs and commits data.

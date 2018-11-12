# bugbug - Classify Bugzilla bugs between actual bugs and bugs that aren't bugs

Bugs on Bugzilla aren't always bugs. Sometimes they are feature requests, refactorings, and so on. The aim of this project is to distinguish between bugs that are actually bugs and bugs that aren't.

The dataset currently contains 2110 bugs, the accuracy of the current classifier is ~93% (precision ~95%, recall ~94%).

## Setup

1. Run `pip install -r requirements.txt` and `pip install -r test-requirements.txt`
2. Run `cat data/bugs.json.xz.part* | unxz > data/bugs.json`
3. Run `cat data/commits.json.xz.part* | unxz > data/commits.json`

If you update the bugs database, run `cat data/bugs.json | xz -v9 - | split -d -b 20MB - data/bugs.json.xz.part`.
If you update the commits database, run `cat data/commits.json | xz -v9 - | split -d -b 20MB - data/commits.json.xz.part`.

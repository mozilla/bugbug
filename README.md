# bugbug - Classify Bugzilla bugs between actual bugs and bugs that aren't bugs

Bugs on Bugzilla aren't always bugs. Sometimes they are feature requests, refactorings, and so on. The aim of this project is to distinguish between bugs that are actually bugs and bugs that aren't.

The dataset currently contains 1913 bugs, the accuracy of the current classifier is ~92% (precision ~97%, recall ~93%).

## Setup

1. Run `pip install -r requirements.txt` and `pip install -r test-requirements.txt`
2. Run `cat data/bugs.json.xz.part* | unxz > data/bugs.json`

If you update the bugs database, run `cat data/bugs.json | xz -v3 - | split -d -b 20MB - data/bugs.json.xz.part`.

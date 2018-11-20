# bugbug - Classify Bugzilla bugs between actual bugs and bugs that aren't bugs

Bugs on Bugzilla aren't always bugs. Sometimes they are feature requests, refactorings, and so on. The aim of this project is to distinguish between bugs that are actually bugs and bugs that aren't.

The dataset currently contains 2110 bugs, the accuracy of the current classifier is ~93% (precision ~95%, recall ~94%).

## Setup

1. Run `pip install -r requirements.txt` and `pip install -r test-requirements.txt`
2. Run `wget https://www.dropbox.com/s/mz3afgncx0siijc/commits.json.xz?dl=0 -O data/commits.json.xz && unxz data/commits.json.xz`
3. Run `wget https://www.dropbox.com/s/xm6wzac9jl81irz/bugs.json.xz?dl=0 -O data/bugs.json.xz && unxz data/bugs.json.xz`

If you update the bugs database, run `xz -v9 -k data/bugs.json`.
If you update the commits database, run `xz -v9 -k data/commits.json`.

# bugbug - Classify Bugzilla bugs between actual bugs and bugs that aren't bugs

Bugs on Bugzilla aren't always bugs. Sometimes they are feature requests, refactorings, and so on. The aim of this project is to distinguish between bugs that are actually bugs and bugs that aren't.

The dataset currently contains 1913 bugs, the accuracy of the current classifier is ~92% (precision ~97%, recall ~93%).

## Setup

1. Run `pip install -r requirements.txt` and `pip install -r test-requirements.txt`
2. Install MongoDB
3. Run `mongo bugbug --eval "db.bugs.drop()"`
4. Run `cat data/bugs.json.xz.part* | unxz > data/bugs.json`
5. Run `mongoimport --db bugbug --collection bugs --file data/bugs.json`

If you update the bugs database, run:
1. `mongoexport -d bugbug -c bugs -o data/bugs.json`
2. `cat data/bugs.json | xz -v1 - | split -d -b 20MB - data/bugs.json.xz.part`

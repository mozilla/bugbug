# bugbug - Classify Bugzilla bugs between actual bugs and bugs that aren't bugs

Bugs on Bugzilla aren't always bugs. Sometimes they are feature requests, refactorings, and so on. The aim of this project is to distinguish between bugs that are actually bugs and bugs that aren't.

This is currently done with a set of handwritten rules.

The dataset currently contains 380 bugs, the precision of the current classifier is ~90%, the recall ~80%.

Supported languages
------------------

The regressor model supports all languages supported by rust-code-analysis: https://github.com/mozilla/rust-code-analysis#supported-languages.

Training the model for another project
--------------------------------------

There are quite a few steps to reproduce the results on another project, and they kind of depend on the processes followed by the specific project. Here is the current pipeline, which depends on Mozilla's processes. Some steps might me not necessary for other projects (and some projects might require additional steps).

1. Gather bugs from the project's Bugzilla; 
1. Mine commits from the repository;
1. Create a list of commits to ignore (formatting changes and so on, which surely can't have introduced regressions);
1. Classify bugs between actual bugs and feature requests (we recently introduced a new "type" field in Bugzilla that developers fill, so we have a high precision in this step; for old bugs where the type field is absent, we use the "defect" model to classify the bug);
1. Use SZZ to find the commits which introduced the bugs from the list from step 4 (making git blame ignore and skip over commits from step 3);
1. Now we have a dataset of commits which introduced bugs and commits which did not introduce bugs, so we can actually train the regressor model.

Step 1 is in scripts/bug_retriever.py and bugbug/bugzilla.py;
Step 2 is scripts/commit_retriever.py and bugbug/repository.py;
Step 3 and 4 and 5 are in scripts/regressor_finder.py;
Step 6 is the actual "regressor" model, in bugbug/models/regressor.py.

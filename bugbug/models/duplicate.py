# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline
import shap
from bugbug import bug_features, bugzilla, feature_cleanup
import pandas as pd
from sklearn.feature_extraction.text import CountVectorizer, TfidfTransformer, TfidfVectorizer
import scipy
from sklearn.metrics import accuracy_score
import numpy as np
from sklearn.model_selection import train_test_split, cross_validate
from sklearn.svm import LinearSVC



class DuplicateModel:
    def __init__(self, lemmatization=False):
        self.cross_validation_enabled = False
        self.sampler = None

        self.calculate_importance = True
        self.pipeline = Pipeline([
            ("tfidf", TfidfVectorizer(preprocessor=self.transform)),
            ("clf", LinearSVC())
        ])

    def transform(self, X):
        cleanup_functions = [
            feature_cleanup.responses(),
            feature_cleanup.hex(),
            feature_cleanup.dll(),
            feature_cleanup.fileref(),
            feature_cleanup.url(),
            feature_cleanup.synonyms(),
            feature_cleanup.crash(),
        ]
        for cleanup_function in cleanup_functions:
            X = cleanup_function(X)
        return X

    def get_text(self, bug):
        return bug["summary"] + " ".join(comment["text"] for comment in bug["comments"])
    
    def get_duplicate_map(self):
        
        bugs = bugzilla.get_bugs()
        bug = {}
        duplicate_bug_map = {}
        count = 500
        for bug_data in bugs:
            bug_id = int(bug_data["id"])

            if bug_data["resolution"] == "DUPLICATE":
                count -= 1
                duplicate_bug_map[bug_id] = int(bug_data["dupe_of"])
            
            bug[bug_id] = bug_data
            if not count :
                break

        return bug , duplicate_bug_map

    def get_labels(self):
        
        bugs, duplicate_map = self.get_duplicate_map()

        res = pd.DataFrame()

        text1 = []
        text2 = []

        for ind, val in duplicate_map.items():
            if val in bugs:
                text1.append(self.get_text(bugs[ind]))
                text2.append(self.get_text(bugs[val]))

        l = len(text1)
        count = 500
        for ind, val in bugs.items():
            if not count:
                break
            if "duplicate" not in val["resolution"]:
                for ind2, val2 in bugs.items():
                    if ind != ind2:
                        text1.append(self.get_text(bugs[ind]))
                        text2.append(self.get_text(bugs[ind2]))
                        count -=1
                    break

        res["text1"] = text1
        res["text2"] = text2
        res["labels"] = np.concatenate([np.ones(l),np.zeros(len(text1) - l)])

        return res

    def train(self, importance_cutoff = 0.15):
        df = self.get_labels()
        class_names= [0,1]
        df["text"] = df["text1"] + df["text2"]
        X_train, x_test, y_train, y_test = train_test_split(df["text"], df["labels"])
        print(X_train.shape)
        print(x_test.shape)
        #self.pipeline.fit(X_train, y_train)

        if self.cross_validation_enabled:
            scorings = ["accuracy"]
            if len(class_names) == 2:
                scorings += ["precision", "recall"]

            scores = cross_validate(self.pipeline, X_train, y_train, scoring=scorings, cv=5)

            print("Cross Validation scores:")
            for scoring in scorings:
                score = scores[f"test_{scoring}"]
                print(
                    f"{scoring.capitalize()}: f{score.mean()} (+/- {score.std() * 2})"
                )
        else:
            self.pipeline.fit(X_train, y_train)
            print("Accuracy is {}".format(accuracy_score(y_test, self.pipeline.predict(x_test))))

        
    def get_feature_names(self):
        return self.pipeline.named_steps["tfidf"].get_feature_names()

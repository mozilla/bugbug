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
from collections import defaultdict


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
        return bug["summary"] + " ".join(comment["text"] for comment in bug["comments"][0:1])
    
    def get_duplicate_map(self):
        
        bugs = bugzilla.get_bugs()
        bug = {}
        dup_bugs = []
        duplicate_bug_map = defaultdict(list)
        #count = 2500
        for bug_data in bugs:
            bug_id = int(bug_data["id"])

            if bug_data["duplicates"] != []:
            #    count -= 1
                for ele2 in bug_data["duplicates"]:
                    
                    duplicate_bug_map[bug_id].append(int(ele2)) 
                    dup_bugs.append(bug_id)
                    dup_bugs.append(int(ele2))
            
            bug[bug_id] = bug_data
            #if not count :
            #    break

        return bug , duplicate_bug_map, list(set(dup_bugs))

    def get_labels(self):
        
        bugs, duplicate_map, dup_bugs = self.get_duplicate_map()

        res = pd.DataFrame()

        text1 = []
        text2 = []

        for ind, dups in duplicate_map.items():
            for val in dups:
                if val in bugs:
                    text1.append(self.get_text(bugs[ind]))
                    text2.append(self.get_text(bugs[val]))

        l = len(text1)
        print(str(l) + ' purely duplicate bugs')
        #count = 1200
        for ind, val in bugs.items():
            #if not count:
            #    break
            if ind not in dup_bugs:
                for ind2, val2 in bugs.items():
                    if ind != ind2 and ind2 not in dup_bugs:
                        text1.append(self.get_text(bugs[ind]))
                        text2.append(self.get_text(bugs[ind2]))
                        #count -=1
                    #break
        l2 = len(text1) - l
        print(str(l2) + " number of purely non duplicate bugs")
        #count = 1200    
        for dup1, b1 in duplicate_map.items():
            
            for dup2, b2 in duplicate_map.items():
                if dup1 != dup2:
                    for bug in b2:
                        if bug in bugs:
                            text1.append(self.get_text(bugs[dup1]))
                            text2.append(self.get_text(bugs[bug]))
                            #count -= 1
                        #if not count:
                        #    break
                #if not count:
                #    break

            #if not count:
            #    break  

        
        print(str(len(text1) - l2) + " number of hybrid bugs")
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

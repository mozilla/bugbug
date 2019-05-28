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
from bugbug.model import BugCoupleModel
from imblearn.over_sampling import BorderlineSMOTE
from sklearn.calibration import CalibratedClassifierCV

class DuplicateModel(BugCoupleModel):
    def __init__(self, lemmatization=False):

        BugCoupleModel.__init__(self, lemmatization)

        self.cross_validation_enabled = True
        self.sampler = BorderlineSMOTE(random_state=0)
        self.calculate_importance = False

        self.bugs = {}
        for bug_data in bugzilla.get_bugs():
            self.bugs[bug_data["id"]] = bug_data

        self.extraction_pipeline = Pipeline([
            ("tfidf", TfidfVectorizer(preprocessor=self.transform)),
        ])

        self.clf = CalibratedClassifierCV(LinearSVC())

    def transform(self, class_ids):
    
        index1, index2 = class_ids.split('|')

        text = self.get_text(self.bugs[int(index1)]) + " " + self.get_text(self.bugs[int(index2)])

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
            text = cleanup_function(text)
        return text

    def get_labels(self):
        
        classes = {}
        duplicate_ids = []          # Only store id's of bugs that have duplicate or are duplicate
        all_ids = []                # Store all remaining ids

        count = 2500
        for bug_data in bugzilla.get_bugs():

            if bug_data["duplicates"] != []:
                duplicate_ids.append(bug_data["id"])
                for duplicate_bug in bug_data["duplicates"]:
                    count -= 1
                    duplicate_ids.append(duplicate_bug)
                    if bug_data["id"] in self.bugs and int(duplicate_bug) in self.bugs:
                        classes[str(bug_data["id"]) + "|" + str(duplicate_bug)] = 1

                    if not count:
                        break
            if not count:
                break
            else:
                all_ids.append(int(bug_data["id"]))

        l = len(classes)
        print("Purely duplicate bugs : ", l)

        all_ids = set(all_ids)

        duplicate_ids = set(duplicate_ids)
        
        # When the bug has no duplicates, we create dup-nondup labels = 0
        count = 2500
        for bug_data in bugzilla.get_bugs():
            if bug_data["duplicates"] == []:
                for dup_bug_id in duplicate_ids:
                    count -= 1
                    if bug_data["id"] in self.bugs and int(dup_bug_id) in self.bugs:
                        classes[str(bug_data["id"]) + "|" + str(dup_bug_id)] = 0
                    if not count:
                        break
                if not count:
                    break

        l2 = len(classes)
        print("Hybrid bugs : ", l2-l)
        # Non we map non-dup to non-dup bug. However, these are really
        # large in number, so I'm restricting the number to 2.5K

        count = 2500
        for bug_data in bugzilla.get_bugs():
            if int(bug_data["id"]) in all_ids:
                for non_dup_bug_id in all_ids:
                    if int(bug_data["id"]) != non_dup_bug_id:
                        if bug_data["id"] in self.bugs and int(non_dup_bug_id) in self.bugs:
                            classes[str(bug_data["id"]) + "|" + str(non_dup_bug_id)] = 0
                        count -= 1
                    if not count:
                        break
                
                if not count:
                    break

        print("Purely non-dup bugs : ", len(classes) - l2)
        return classes, [0, 1]


    def get_text(self, bug):
        return bug["summary"] + " ".join(comment["text"] for comment in bug["comments"][0:1])

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["tfidf"].get_feature_names()
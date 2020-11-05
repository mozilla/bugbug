# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import xgboost
from sklearn.svm import SVC, LinearSVC
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import BaggingClassifier, RandomForestClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.neighbors import KNeighborsClassifier
from sklearn.naive_bayes import ComplementNB, BernoulliNB
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
from imblearn.under_sampling import RandomUnderSampler

import numpy as np

from imblearn.under_sampling import RandomUnderSampler

from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.multiclass import OneVsRestClassifier
from sklearn.model_selection import RepeatedStratifiedKFold
from sklearn.pipeline import Pipeline

from bugbug import bug_features, bugzilla, feature_cleanup, labels, utils
from bugbug.model import BugModel

from sklearn.naive_bayes import MultinomialNB
from imblearn.combine import SMOTEENN


# param all_labels  if all_labels==True, it uses general label names
#                   elif all_labels==False, it uses the label names with the subcategories
class BugTypeClassificationModel(BugModel):
    def __init__(self, lemmatization=False, all_labels=True, grid_search=False, single_class=None, classifier='linear_svc', clf_params=True, compact_statistics=False, cv=5):
        BugModel.__init__(self, lemmatization)


        #
        # Define the classes
        #
        # These are all the  categories in our taxonomy
        # both the general and
        # the sub-categories
        self.label_names = [
            'API issue',
            'Network Usage issue', 
            'Database-related issue',
            'GUI-related issue',
            'Performance issue',
            'Security issue',
            'Program Anomaly issue',
            'Development issue' 
        ]


        label_names_sub_classes = [
            'Add-on or plug-in incompatibility',
            'Permission-Deprecation issue',
            'Web incompatibility',
            'Incompatibility',
            'Crash',
            'Hang',
            'Wrong Functionality',
            'Incorrect Rendering',
            'Test code',
            'Compile'
        ]



        # Set the all_labels  ( General VS Subcategory )
        self.all_labels = all_labels

        # Set the class all_labels ( Single class VS All the category )
        self.single_class = single_class

        # Add the sub-classes to the classification list
        if not self.all_labels:
            self.label_names = self.label_names + label_names_sub_classes

        # Check if the element is based over the single class
        if self.single_class is not None:
            assert self.single_class is not None, f"unexpected class\n\tUse the args --single_class with one of the following {self.label_names}"
            assert self.single_class in self.label_names, f"unexpected class {self.single_class}\n\tUse the args --single_class with one of the following {self.label_names}"
            self.label_names = [self.single_class, "Other"]
            self.sampler = RandomUnderSampler(random_state=0)

        self.target_size = len(self.label_names)
        if self.single_class is None:
            self.list_labels = self.label_names
        else:
            self.list_labels = [0, 1]


        #
        # Define the data set file name
        #
        self.name_file_list_labels = "bug_types"



        self.calculate_importance = True
        '''
        # Not used now, always true
        if self.single_class is not None:
            self.calculate_importance = True
        else:
            self.calculate_importance = True
        '''

        feature_extractors = [
            bug_features.has_str(),
            bug_features.severity(),
            # Ignore keywords that would make the ML completely skewed
            # (we are going to use them as 100% rules in the evaluation phase).
            bug_features.keywords(set(self.label_names)),
            bug_features.is_coverity_issue(),
            bug_features.has_crash_signature(),
            bug_features.has_url(),
            bug_features.has_w3c_url(),
            bug_features.has_github_url(),
            bug_features.whiteboard(),
            bug_features.patches(),
            bug_features.landings(),
            bug_features.blocked_bugs_number(),
            bug_features.ever_affected(),
            bug_features.affected_then_unaffected(),
            bug_features.product(),
            bug_features.component(),
        ]

        cleanup_functions = [
            feature_cleanup.url(),
            feature_cleanup.fileref(),
            feature_cleanup.synonyms(),
        ]

        self.extraction_pipeline = Pipeline(
            [
                (
                    "bug_extractor",
                    bug_features.BugExtractor(feature_extractors, cleanup_functions),
                ),
                (
                    "union",
                    ColumnTransformer(
                        [
                            ("data", DictVectorizer(), "data"),
                            ("title", self.text_vectorizer(min_df=0.001), "title"),
                            (
                                "first_comment",
                                self.text_vectorizer(min_df=0.001),
                                "first_comment",
                            ),
                            (
                                "comments",
                                self.text_vectorizer(min_df=0.01),
                                "comments",
                            ),
                        ]
                    ),
                ),
            ]
        )



        #
        # Define the Classifier
        #
        if classifier == 'linear_svc':
            self.clf_name = "LinearSVC"
            if grid_search is False:
                if clf_params:
                    self.clf_name += " tuned"
                    self.classifier = LinearSVC(C=0.1, fit_intercept=True, intercept_scaling=0.1, tol=0.0001, penalty='l2', loss='squared_hinge', dual=True, multi_class='ovr', class_weight='balanced')
                else:
                    self.classifier = LinearSVC()
            else:
                self.classifier = LinearSVC()
                self.parameters = {
                    "estimator__C": [0, 0.1, 0.5, 0.8, 1, 10],
                    "estimator__fit_intercept": [True, False],
                    "estimator__intercept_scaling": [0, 0.1, 0.2, 0.5, 0.8, 1],
                    "estimator__penalty": ['l1', 'l2'],
                    "estimator__loss": ['hinge', 'squared_hinge'],
                    "estimator__tol": [0.001, 0.0001, 0.00001, 0.000001],
                    "estimator__multi_class": ['ovr'],
                    "estimator__max_iter": [-1, 100, 500, 1000],
                }

        elif classifier == 'bayes':
            self.clf_name = "Complement Naive Bayes"
            if grid_search is False:
                if clf_params:
                    self.clf_name += " tuned"
                    self.classifier = ComplementNB(alpha=0.1)
                else:
                    self.classifier = ComplementNB()
            else:
                self.classifier = ComplementNB()
                self.parameters = {
                    "estimator__alpha": [0,0.1,0.4,0.5,0.7,0.9,1],
                    "estimator__norm": [False, True],
                }

        elif classifier == 'knn':
            self.clf_name = "K-NN"
            if grid_search is False:
                if clf_params:
                    self.clf_name += " tuned"
                    self.classifier = KNeighborsClassifier(weights='distance', algorithm='brute', leaf_size=30, p=2, n_jobs=utils.get_physical_cpu_count())
                else:
                    self.classifier = KNeighborsClassifier()
            else:
                self.classifier = KNeighborsClassifier()
                self.parameters = {
                    "estimator__n_neighbors": [3,5,11,19],
                    "estimator__weights": ["uniform","distance"],
                    "estimator__metric":["euclidean","manhattan"],
                }

        elif classifier == 'xgboost':
            self.clf_name = "XGBoost"
            if grid_search is False:
                if clf_params:
                    self.clf_name += " tuned"
                    self.classifier = xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count())
                else:
                    self.classifier = xgboost.XGBClassifier()
            else:
                self.classifier = xgboost.XGBClassifier()
                self.parameters = {
                    "estimator__loss": ['deviance', 'exponential'],
                    "estimator__learning_rate": [0.001, 0.01, 0.1, 0.2, 0.3],
                    "estimator__n_estimators": [100, 500, 1000],
                    "estimator__criterion": ['friedman_mse', 'mse', 'mae'],
                    "estimator__init": ['zero', None],
                    "estimator__max_features": ['auto', 'sqrt', 'log2'],
                    "estimator__tol": [0.001, 0.0001, 0.00001, 0.000001],
                }
                

        #
        # Define the classifier for the correct output
        #
        if self.single_class is None:
            if compact_statistics is False:
                self.classifier = CalibratedClassifierCV(self.classifier)
            self.clf = OneVsRestClassifier(self.classifier)

        else:
            if compact_statistics is False:
                self.classifier = CalibratedClassifierCV(self.classifier)
            self.clf = self.classifier

        #
        # Define the cv
        #
        self.cv = cv
    
    #
    # Define a function that compute the priors no more longer usefull
    #
    def get_class_weight(self):
        cw = self.get_labels(True)
        for i, v in cw.items():
            cw[i] = sum(cw.values()) / (self.target_size * cw[i])
        l = [n for _, n in cw.items()]
        print(l)
        print(self.target_size)
        return l


    def get_labels(self, requestCategories=None):
        classes = {}
        categories = {}
        if self.single_class is not None:
            categories[self.single_class] = 0
            categories["Other"] = 0

        for bug_id, *bug_labels in labels.get_labels(self.name_file_list_labels):
            if self.single_class is None:
                target = np.zeros(self.target_size)
                if self.all_labels:
                    # consider only the last three fields in the bug_labels vector
                    # the ones related to the general labels classification
                    for keyword in bug_labels[3:]:
                        if keyword != '':
                            assert keyword in self.list_labels, f"unexpected category {keyword}"
                            target[self.list_labels.index(keyword)] = 1
                            if keyword in categories.keys():
                                categories[keyword] = categories[keyword] + 1
                            else:
                                categories[keyword] = 1
                else:
                    # consider only the first three fields in the bug_labels vector
                    # the ones related to the subcategory labels classification
                    for keyword in bug_labels[:3]:
                        if keyword != '':
                            assert keyword in self.list_labels, f"unexpected category {keyword}"
                            target[self.list_labels.index(keyword)] = 1
                            if keyword in categories.keys():
                                categories[keyword] = categories[keyword] + 1
                            else:
                                categories[keyword] = 1
                classes[int(bug_id)] = target

            else:
                classes[int(bug_id)] = 0
                if self.all_labels:
                    for keyword in bug_labels[3:]:
                        if keyword != '' and keyword == self.single_class:
                            classes[int(bug_id)] = 1
                            categories[self.single_class] = categories[self.single_class] + 1
                        elif keyword != '' and keyword != self.single_class:
                            categories["Other"] = categories["Other"] + 1
                else:
                    for keyword in bug_labels[:3]:
                        if keyword != '' and keyword == self.single_class:
                            classes[int(bug_id)] = 1
                            categories[self.single_class] = categories[self.single_class] + 1
                        elif keyword != '' and keyword != self.single_class:
                            categories["Other"] = categories["Other"] + 1

        # Some logging so we have an idea of the number of examples for each class
        if requestCategories is None:
            if self.single_class is None:
                for lbl in self.label_names:
                    print(str(categories[lbl]) + " " + lbl)
            else:
                print(str(categories[self.single_class]) + " " + self.single_class)
                print(str(categories["Other"]) + " Other")
            return classes, self.list_labels
        elif requestCategories:
            return categories#[n for k, n in categories.items()]


    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names()


    def overwrite_classes(self, bugs, classes, probabilities):
        for i, bug in enumerate(bugs):
            for keyword in bug:
                if self.single_class is None:
                    if keyword in self.list_labels:
                        if probabilities:
                            classes[i][self.list_labels.index(keyword)] = 1.0
                        else:
                            classes[i][self.list_labels.index(keyword)] = 1
                else:
                    if keyword in [self.single_class, "Other"]:
                        if probabilities:
                            if keyword == self.single_class:
                                classes[i] = 1.0
                            else:
                                classes[i] = 0.0
                        else:
                            classes[i] = 0.0
        return classes

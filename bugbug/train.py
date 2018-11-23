# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import numpy as np
import xgboost
from imblearn.under_sampling import RandomUnderSampler
from sklearn import metrics
from sklearn.externals import joblib
from sklearn.feature_extraction import DictVectorizer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.model_selection import cross_val_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import FeatureUnion
from sklearn.pipeline import Pipeline

from bugbug import bug_features
from bugbug import bugzilla
from bugbug import repository
from bugbug.nlp import SpacyVectorizer
from bugbug.utils import DictSelector


def train(classes, model=None, lemmatization=False):
    commit_messages_map = {}
    for commit in repository.get_commits():
        bug_id = commit['bug_id']

        if not bug_id:
            continue

        if bug_id not in commit_messages_map:
            commit_messages_map[bug_id] = ' '

        commit_messages_map[bug_id] += commit['desc']

    # Get bugs.
    bugs = bugzilla.get_bugs()

    # Filter out bugs for which we have no labels.
    bugs = [bug for bug in bugs if bug['id'] in classes]

    # Calculate labels.
    y = np.array([1 if classes[bug['id']] else 0 for bug in bugs])

    if lemmatization:
        text_vectorizer = SpacyVectorizer
    else:
        text_vectorizer = TfidfVectorizer

    # TODO: Try bag-of-words with word/char 1-gram, 2-gram, 3-grams, word2vec, doc2vec, 1d-cnn (both using pretrained word embeddings and not)

    # Extract features from the bugs.
    extraction_pipeline = Pipeline([
        ('bug_extractor', bug_features.BugExtractor(commit_messages_map)),
        ('union', FeatureUnion(
            transformer_list=[
                ('data', Pipeline([
                    ('selector', DictSelector(key='data')),
                    ('vect', DictVectorizer()),
                ])),

                ('title', Pipeline([
                    ('selector', DictSelector(key='title')),
                    ('tfidf', text_vectorizer(stop_words='english')),
                ])),

                ('comments', Pipeline([
                    ('selector', DictSelector(key='comments')),
                    ('tfidf', text_vectorizer(stop_words='english')),
                ])),

                # ('commits', Pipeline([
                #     ('selector', DictSelector(key='commits')),
                #     ('tfidf', text_vectorizer(stop_words='english')),
                # ])),
            ],
        )),
    ])

    X = extraction_pipeline.fit_transform(bugs)

    # Under-sample the 'bug' class, as there are too many compared to 'feature'.
    X, y = RandomUnderSampler(random_state=0).fit_sample(X, y)

    # Split dataset in training and test.
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.1, random_state=0)
    print(X_train.shape, y_train.shape)
    print(X_test.shape, y_test.shape)

    # Define classifier.
    clf = xgboost.XGBClassifier(n_jobs=16)
    # clf = svm.SVC(kernel='linear', C=1)
    # clf = ensemble.GradientBoostingClassifier()
    # clf = autosklearn.classification.AutoSklearnClassifier()

    # Use k-fold cross validation to evaluate results.
    scores = cross_val_score(clf, X_train, y_train, cv=5)
    print('CV Accuracy: %0.2f (+/- %0.2f)' % (scores.mean(), scores.std() * 2))

    # Evaluate results on the test set.
    clf.fit(X_train, y_train)
    y_pred = clf.predict(X_test)
    print('Accuracy: {}'.format(metrics.accuracy_score(y_test, y_pred)))
    print('Precision: {}'.format(metrics.precision_score(y_test, y_pred)))
    print('Recall: {}'.format(metrics.recall_score(y_test, y_pred)))
    print(metrics.confusion_matrix(y_test, y_pred))

    if model is not None:
        joblib.dump((extraction_pipeline, clf), model)

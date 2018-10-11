# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
from typing import Dict

import numpy as np
import spacy
import xgboost
from imblearn.under_sampling import RandomUnderSampler
from sklearn import metrics
from sklearn.feature_extraction import DictVectorizer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.model_selection import cross_val_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import FeatureUnion
from sklearn.pipeline import Pipeline

import bug_features
from get_bugs import get_bugs
from get_bugs import get_labels
from utils import ItemSelector

nlp = spacy.load('en')


def spacy_token_lemmatizer(text):
    if len(text) > nlp.max_length:
        text = text[:nlp.max_length - 1]
    doc = nlp(text)
    return [token.lemma_ for token in doc]


class SpacyVectorizer(TfidfVectorizer):
    def __init__(self, *args, **kwargs):
        super().__init__(tokenizer=spacy_token_lemmatizer, *args, **kwargs)


def go(lemmatization=False):
    # Get labels.
    classes = get_labels(augmentation=True)

    # Retrieve bugs from the local db.
    bugs_map = get_bugs()

    # Turn the classes map into a numpy array for scikit-learn consumption.
    y = np.array([1 if is_bug is True else 0 for is_bug in classes.values()])

    bugs: Dict = {
        'data': [],
        'title': [],
        'comments': [],
    }
    for bug_id, _ in classes.items():
        data = {}

        bug = bugs_map[bug_id]

        for f in bug_features.feature_extractors:
            res = f(bug)

            if res is None:
                continue

            if isinstance(res, list):
                for item in res:
                    data[f.__name__ + '-' + item] = 'True'
                continue

            if isinstance(res, bool):
                res = str(res)

            data[f.__name__] = res

        # TODO: Alternative features, to integreate in bug_features.py
        # for f in bugbug.feature_rules + bugbug.bug_rules:
        #     data[f.__name__] = f(bug)

        # TODO: Try simply using all possible fields instead of extracting features manually.

        bugs['data'].append(data)
        bugs['title'].append(bug['summary'])
        bugs['comments'].append(' '.join([c['text'] for c in bug['comments']]))

    if lemmatization:
        text_vectorizer = SpacyVectorizer
    else:
        text_vectorizer = TfidfVectorizer

    # TODO: Try bag-of-words with word/char 1-gram, 2-gram, 3-grams, word2vec, doc2vec, 1d-cnn (both using pretrained word embeddings and not)

    # TODO: Text cleanup (replace stack traces with "STACK_TRACE", replace file references with "FILE_REFERENCE", etc.)

    # Extract features from the bugs.
    extraction_pipeline = Pipeline([
        ('union', FeatureUnion(
            transformer_list=[
                ('data', Pipeline([
                    ('selector', ItemSelector(key='data')),
                    ('vect', DictVectorizer()),
                ])),

                ('title', Pipeline([
                    ('selector', ItemSelector(key='title')),
                    ('tfidf', text_vectorizer(stop_words='english')),
                ])),

                ('comments', Pipeline([
                    ('selector', ItemSelector(key='comments')),
                    ('tfidf', text_vectorizer(stop_words='english')),
                ])),
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


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--lemmatization', help='Perform lemmatization (using spaCy)', action='store_true')
    args = parser.parse_args()

    go(lemmatization=args.lemmatization)

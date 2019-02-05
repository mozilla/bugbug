# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import numpy as np
import pandas as pd
from keras import Input
from keras import layers
from keras.layers import GRU
from keras.layers import Bidirectional
from keras.layers import Dense
from keras.layers import Dropout
from keras.layers import Embedding
from keras.layers import Flatten
from keras.layers import GlobalMaxPooling1D
from keras.layers import SpatialDropout1D
from keras.models import Model as KerasModel
from keras.utils import to_categorical
from sklearn.base import BaseEstimator
from sklearn.base import TransformerMixin
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bug_features
from bugbug.models.component import ComponentModel
from bugbug.nn import KerasClassifier
from bugbug.nn import KerasTextToSequences
from bugbug.utils import StructuredColumnTransformer


class CategoricalExtractor(TransformerMixin):
    def transform(self, bugs):
        features = ['bug_reporter', 'platform', 'op_sys']

        for feature in features:
            bugs[feature] = bugs['data'].map(lambda data: data[feature]).astype('category').cat.codes

        bugs = bugs.drop(columns=['data'])
        bugs['data'] = bugs[features].to_dict('records')
        return bugs

    def fit(self, *args, **kwargs):
        return self


class PassthroughTransformer(BaseEstimator, TransformerMixin):
    def fit(self, X, y=None):
        return self

    def transform(self, X):
        self.X = X
        return np.array([X.tolist()]).T


class TriageModel(ComponentModel):
    def __init__(self, *args, **kwargs):
        super(TriageModel, self).__init__(*args, **kwargs)

        self.short_desc_maxlen = 20
        self.short_desc_vocab_size = 25000
        self.short_desc_emb_sz = 300
        self.long_desc_maxlen = 100
        self.long_desc_vocab_size = 25000
        self.long_desc_emb_sz = 300
        self.cross_validation_enabled = False
        self.calculate_importance = False
        self.calculate_classification_metrics = True

        feature_extractors = [
            bug_features.bug_reporter(),
            bug_features.platform(),
            bug_features.op_sys()
        ]

        cleanup_functions = []

        self.extraction_pipeline = Pipeline([
            ('bug_extractor', bug_features.BugExtractor(feature_extractors, cleanup_functions)),
            ('categorical_extractor', CategoricalExtractor()),
            ('union', StructuredColumnTransformer([
                ('platform', PassthroughTransformer(), 'platform'),
                ('op_sys', PassthroughTransformer(), 'op_sys'),
                ('bug_reporter', PassthroughTransformer(), 'bug_reporter'),
                ('title_sequence', KerasTextToSequences(
                    self.short_desc_maxlen, self.short_desc_vocab_size), 'title'),
                ('first_comment_sequence', KerasTextToSequences(
                    self.long_desc_maxlen, self.long_desc_vocab_size), 'first_comment'),
                ('title_char_tfidf', TfidfVectorizer(
                    strip_accents='unicode',
                    analyzer='char',
                    stop_words='english',
                    ngram_range=(2, 4),
                    max_features=25000,
                    sublinear_tf=True
                ), 'title'),
                ('title_word_tfidf', TfidfVectorizer(
                    strip_accents='unicode',
                    min_df=0.0001,
                    max_df=0.1,
                    analyzer='word',
                    token_pattern=r'\w{1,}',
                    stop_words='english',
                    ngram_range=(2, 4),
                    max_features=30000,
                    sublinear_tf=True
                ), 'title')
            ])),
        ])

        kwargs = {
            'short_desc_maxlen': self.short_desc_maxlen,
            'short_desc_vocab_size': self.short_desc_vocab_size,
            'short_desc_emb_sz': self.short_desc_emb_sz,
            'long_desc_maxlen': self.long_desc_maxlen,
            'long_desc_vocab_size': self.long_desc_vocab_size,
            'long_desc_emb_sz': self.long_desc_emb_sz
        }
        self.clf = TriageClassifier(**kwargs)

    def get_labels(self, *args, **kwargs):
        labels = super(TriageModel, self).get_labels(*args, **kwargs)
        labels = [{'bug_id': k, 'component': v} for k, v in labels.items()]
        df = pd.DataFrame(labels)
        df['component'] = df['component'].astype('category').cat.codes
        return {row['bug_id']: row['component'] for _, row in df.iterrows()}

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps['union'].named_transformers_.keys()

    def calculate_labels(self, classes, bugs):
        labels = super(TriageModel, self).calculate_labels(classes, bugs)
        return to_categorical(labels)


class TriageClassifier(KerasClassifier):
    def __init__(self, **kwargs):
        super(TriageClassifier, self).__init__(epochs=2, batch_size=256)
        self.short_desc_maxlen = kwargs.pop('short_desc_maxlen')
        self.short_desc_vocab_size = kwargs.pop('short_desc_vocab_size')
        self.short_desc_emb_sz = kwargs.pop('short_desc_emb_sz')
        self.long_desc_maxlen = kwargs.pop('long_desc_maxlen')
        self.long_desc_vocab_size = kwargs.pop('long_desc_vocab_size')
        self.long_desc_emb_sz = kwargs.pop('long_desc_emb_sz')

    def model_creator(self, X, y):
        short_desc_inp = Input(shape=(self.short_desc_maxlen,), name='title_sequence')
        short_desc_emb = Embedding(self.short_desc_vocab_size, self.short_desc_emb_sz)(short_desc_inp)
        short_desc_emb = SpatialDropout1D(0.2)(short_desc_emb)
        short_desc_encoded = Bidirectional(
            GRU(256, dropout=0.45, recurrent_dropout=0.5, return_sequences=True)
        )(short_desc_emb)
        short_desc_encoded = GlobalMaxPooling1D()(short_desc_encoded)

        long_desc_inp = Input(shape=(self.long_desc_maxlen,), name='first_comment_sequence')
        long_desc_emb = Embedding(self.long_desc_vocab_size, self.long_desc_emb_sz)(long_desc_inp)
        long_desc_emb = SpatialDropout1D(0.25)(long_desc_emb)
        long_desc_encoded = Bidirectional(
            GRU(256, dropout=0.5, recurrent_dropout=0.55, return_sequences=True)
        )(long_desc_emb)
        long_desc_encoded = GlobalMaxPooling1D()(long_desc_encoded)

        rep_platform_inp = Input(shape=(1,), name='platform')
        rep_platform_emb = Embedding(input_dim=14, output_dim=25, input_length=1)(
            rep_platform_inp
        )
        rep_platform_emb = SpatialDropout1D(0.1)(rep_platform_emb)
        rep_platform_emb = Flatten()(rep_platform_emb)
        rep_platform_emb = Dropout(0.45)(rep_platform_emb)

        op_sys_inp = Input(shape=(1,), name='op_sys')
        op_sys_emb = Embedding(input_dim=48, output_dim=50, input_length=1)(op_sys_inp)
        op_sys_emb = SpatialDropout1D(0.1)(op_sys_emb)
        op_sys_emb = Flatten()(op_sys_emb)
        op_sys_emb = Dropout(0.45)(op_sys_emb)

        reporter_inp = Input(shape=(1,), name='bug_reporter')
        reporter_emb = Embedding(input_dim=46544, output_dim=100, input_length=1)(
            reporter_inp
        )
        reporter_emb = SpatialDropout1D(0.15)(reporter_emb)
        reporter_emb = Flatten()(reporter_emb)
        reporter_emb = Dropout(0.5)(reporter_emb)

        tfidf_word_inp = Input(shape=(X['title_word_tfidf'].shape[1],), name='title_word_tfidf')
        tfidf_word = Dense(600, activation='relu')(tfidf_word_inp)
        tfidf_word = Dropout(0.5)(tfidf_word)

        tfidf_char_inp = Input(shape=(X['title_char_tfidf'].shape[1],), name='title_char_tfidf')
        tfidf_char = Dense(500, activation='relu')(tfidf_char_inp)
        tfidf_char = Dropout(0.5)(tfidf_char)

        x = layers.concatenate(
            [
                short_desc_encoded,
                long_desc_encoded,
                rep_platform_emb,
                op_sys_emb,
                reporter_emb,
                tfidf_word,
                tfidf_char,
            ],
            axis=-1,
        )

        x = Dense(2000, activation='relu')(x)
        x = Dropout(0.6)(x)
        x = Dense(y.shape[1], activation='softmax')(x)

        model = KerasModel(
            [
                short_desc_inp,
                long_desc_inp,
                rep_platform_inp,
                op_sys_inp,
                reporter_inp,
                tfidf_word_inp,
                tfidf_char_inp,
            ],
            x,
        )
        model.compile(optimizer='adam', loss=['categorical_crossentropy'], metrics=['acc'])

        return model

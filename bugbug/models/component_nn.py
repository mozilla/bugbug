# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from sklearn.ensemble import VotingClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import OrdinalEncoder

from bugbug import bug_features
from bugbug.models.component import ComponentModel
from bugbug.nn import KerasClassifier, KerasTextToSequences
from bugbug.utils import (
    DictExtractor,
    MissingOrdinalEncoder,
    StructuredColumnTransformer,
)

OPT_MSG_MISSING = (
    "Optional dependencies are missing, install them with: pip install bugbug[nn]\n"
)

try:
    from tensorflow.keras import Input, layers
    from tensorflow.keras.layers import (
        GRU,
        Bidirectional,
        Dense,
        Dropout,
        Embedding,
        Flatten,
        GlobalMaxPooling1D,
        SpatialDropout1D,
    )
    from tensorflow.keras.models import Model as KerasModel
except ImportError:
    raise ImportError(OPT_MSG_MISSING)


class ComponentNNClassifier(KerasClassifier):
    def __init__(self, **kwargs):
        # (epochs, batch_size) combinations
        fit_params = [(2, 256), (2, 512), (1, 1024)]
        super().__init__(fit_params=fit_params)

        self.set_params(**kwargs)

    def get_params(self, deep=True):
        return {
            "short_desc_maxlen": self.short_desc_maxlen,
            "short_desc_vocab_size": self.short_desc_vocab_size,
            "short_desc_emb_sz": self.short_desc_emb_sz,
            "long_desc_maxlen": self.long_desc_maxlen,
            "long_desc_vocab_size": self.long_desc_vocab_size,
            "long_desc_emb_sz": self.long_desc_emb_sz,
            "params": self.model_params,
        }

    def set_params(self, **kwargs):
        self.short_desc_maxlen = kwargs.pop("short_desc_maxlen")
        self.short_desc_vocab_size = kwargs.pop("short_desc_vocab_size")
        self.short_desc_emb_sz = kwargs.pop("short_desc_emb_sz")
        self.long_desc_maxlen = kwargs.pop("long_desc_maxlen")
        self.long_desc_vocab_size = kwargs.pop("long_desc_vocab_size")
        self.long_desc_emb_sz = kwargs.pop("long_desc_emb_sz")
        self.model_params = kwargs.pop("params")

        for (k, v) in self.model_params.items():
            setattr(self, k, v)

        return self

    def model_creator(self, X, y):
        short_desc_inp = Input(shape=(self.short_desc_maxlen,), name="title_sequence")
        short_desc_emb = Embedding(self.short_desc_vocab_size, self.short_desc_emb_sz)(
            short_desc_inp
        )
        short_desc_emb = SpatialDropout1D(self.short_desc_emb_dropout_rate)(
            short_desc_emb
        )
        short_desc_encoded = Bidirectional(
            GRU(
                self.short_desc_encoded_gru_units,
                dropout=self.short_desc_encoded_gru_dropout,
                recurrent_dropout=self.short_desc_encoded_recurrent_dropout,
                return_sequences=True,
            )
        )(short_desc_emb)
        short_desc_encoded = GlobalMaxPooling1D()(short_desc_encoded)

        long_desc_inp = Input(
            shape=(self.long_desc_maxlen,), name="first_comment_sequence"
        )
        long_desc_emb = Embedding(self.long_desc_vocab_size, self.long_desc_emb_sz)(
            long_desc_inp
        )
        long_desc_emb = SpatialDropout1D(self.long_desc_emb_dropout_rate)(long_desc_emb)
        long_desc_encoded = Bidirectional(
            GRU(
                self.long_desc_encoded_gru_units,
                dropout=self.long_desc_encoded_dropout,
                recurrent_dropout=self.long_desc_encoded_recurrent_dropout,
                return_sequences=True,
            )
        )(long_desc_emb)
        long_desc_encoded = GlobalMaxPooling1D()(long_desc_encoded)

        rep_platform_inp = Input(shape=(1,), name="platform")
        rep_platform_emb = Embedding(
            input_dim=self.rep_platform_emb_input_dim,
            output_dim=self.rep_platform_emb_output_dim,
            input_length=1,
        )(rep_platform_inp)
        rep_platform_emb = SpatialDropout1D(self.rep_platform_emb_spatial_dropout_rate)(
            rep_platform_emb
        )
        rep_platform_emb = Flatten()(rep_platform_emb)
        rep_platform_emb = Dropout(self.rep_platform_emb_dropout_rate)(rep_platform_emb)

        op_sys_inp = Input(shape=(1,), name="op_sys")
        op_sys_emb = Embedding(
            input_dim=self.op_sys_emb_input_dim,
            output_dim=self.op_sys_emb_output_dim,
            input_length=1,
        )(op_sys_inp)
        op_sys_emb = SpatialDropout1D(self.op_sys_emb_spatial_dropout_rate)(op_sys_emb)
        op_sys_emb = Flatten()(op_sys_emb)
        op_sys_emb = Dropout(self.op_sys_emb_dropout_rate)(op_sys_emb)

        reporter_inp = Input(shape=(1,), name="bug_reporter")
        reporter_emb = Embedding(
            input_dim=self.reporter_emb_input_dim,
            output_dim=self.reporter_emb_output_dim,
            input_length=1,
        )(reporter_inp)
        reporter_emb = SpatialDropout1D(self.reporter_emb_spatial_dropout_rate)(
            reporter_emb
        )
        reporter_emb = Flatten()(reporter_emb)
        reporter_emb = Dropout(self.reporter_emb_dropout_rate)(reporter_emb)

        tfidf_word_inp = Input(
            shape=(X["title_word_tfidf"].shape[1],), name="title_word_tfidf"
        )
        tfidf_word = Dense(self.tfidf_word_dense_units, activation="relu")(
            tfidf_word_inp
        )
        tfidf_word = Dropout(self.tfidf_word_dropout_rate)(tfidf_word)

        tfidf_char_inp = Input(
            shape=(X["title_char_tfidf"].shape[1],), name="title_char_tfidf"
        )
        tfidf_char = Dense(self.tfidf_char_inp_dense_unit, activation="relu")(
            tfidf_char_inp
        )
        tfidf_char = Dropout(self.tfidf_char_inp_dropout_rate)(tfidf_char)

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

        x = Dense(self.x_dense_unit, activation="relu")(x)
        x = Dropout(self.x_dropout_rate)(x)
        x = Dense(y.shape[1], activation="softmax")(x)

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
        model.compile(
            optimizer="adam", loss=["categorical_crossentropy"], metrics=["acc"]
        )

        return model


class ComponentNNModel(ComponentModel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.short_desc_maxlen = 20
        self.short_desc_vocab_size = 25000
        self.short_desc_emb_sz = 300
        self.long_desc_maxlen = 100
        self.long_desc_vocab_size = 25000
        self.long_desc_emb_sz = 300
        self.cross_validation_enabled = False

        self.params = [
            {
                "short_desc_emb_dropout_rate": 0.2,
                "short_desc_encoded_gru_units": 256,
                "short_desc_encoded_gru_dropout": 0.45,
                "short_desc_encoded_recurrent_dropout": 0.5,
                "long_desc_emb_dropout_rate": 0.25,
                "long_desc_encoded_gru_units": 256,
                "long_desc_encoded_dropout": 0.5,
                "long_desc_encoded_recurrent_dropout": 0.55,
                "rep_platform_emb_input_dim": 14,
                "rep_platform_emb_output_dim": 25,
                "rep_platform_emb_spatial_dropout_rate": 0.1,
                "rep_platform_emb_dropout_rate": 0.45,
                "op_sys_emb_input_dim": 48,
                "op_sys_emb_output_dim": 50,
                "op_sys_emb_spatial_dropout_rate": 0.1,
                "op_sys_emb_dropout_rate": 0.45,
                "reporter_emb_input_dim": 46544,
                "reporter_emb_output_dim": 100,
                "reporter_emb_spatial_dropout_rate": 0.15,
                "reporter_emb_dropout_rate": 0.5,
                "tfidf_word_dense_units": 600,
                "tfidf_word_dropout_rate": 0.5,
                "tfidf_char_inp_dense_unit": 500,
                "tfidf_char_inp_dropout_rate": 0.5,
                "x_dense_unit": 2000,
                "x_dropout_rate": 0.6,
            },
            {
                "short_desc_emb_dropout_rate": 0.2,
                "short_desc_encoded_gru_units": 250,
                "short_desc_encoded_gru_dropout": 0.45,
                "short_desc_encoded_recurrent_dropout": 0.45,
                "long_desc_emb_dropout_rate": 0.25,
                "long_desc_encoded_gru_units": 250,
                "long_desc_encoded_dropout": 0.45,
                "long_desc_encoded_recurrent_dropout": 0.45,
                "rep_platform_emb_input_dim": 14,
                "rep_platform_emb_output_dim": 30,
                "rep_platform_emb_spatial_dropout_rate": 0.1,
                "rep_platform_emb_dropout_rate": 0.4,
                "op_sys_emb_input_dim": 48,
                "op_sys_emb_output_dim": 55,
                "op_sys_emb_spatial_dropout_rate": 0.1,
                "op_sys_emb_dropout_rate": 0.4,
                "reporter_emb_input_dim": 46544,
                "reporter_emb_output_dim": 110,
                "reporter_emb_spatial_dropout_rate": 0.15,
                "reporter_emb_dropout_rate": 0.45,
                "tfidf_word_dense_units": 610,
                "tfidf_word_dropout_rate": 0.45,
                "tfidf_char_inp_dense_unit": 510,
                "tfidf_char_inp_dropout_rate": 0.5,
                "x_dense_unit": 1970,
                "x_dropout_rate": 0.5,
            },
            {
                "short_desc_emb_dropout_rate": 0.2,
                "short_desc_encoded_gru_units": 266,
                "short_desc_encoded_gru_dropout": 0.45,
                "short_desc_encoded_recurrent_dropout": 0.45,
                "long_desc_emb_dropout_rate": 0.25,
                "long_desc_encoded_gru_units": 266,
                "long_desc_encoded_dropout": 0.45,
                "long_desc_encoded_recurrent_dropout": 0.55,
                "rep_platform_emb_input_dim": 14,
                "rep_platform_emb_output_dim": 35,
                "rep_platform_emb_spatial_dropout_rate": 0.1,
                "rep_platform_emb_dropout_rate": 0.45,
                "op_sys_emb_input_dim": 48,
                "op_sys_emb_output_dim": 60,
                "op_sys_emb_spatial_dropout_rate": 0.1,
                "op_sys_emb_dropout_rate": 0.45,
                "reporter_emb_input_dim": 46544,
                "reporter_emb_output_dim": 120,
                "reporter_emb_spatial_dropout_rate": 0.15,
                "reporter_emb_dropout_rate": 0.45,
                "tfidf_word_dense_units": 620,
                "tfidf_word_dropout_rate": 0.5,
                "tfidf_char_inp_dense_unit": 520,
                "tfidf_char_inp_dropout_rate": 0.45,
                "x_dense_unit": 1950,
                "x_dropout_rate": 0.5,
            },
        ]

        feature_extractors = [
            bug_features.bug_reporter(),
            bug_features.platform(),
            bug_features.op_sys(),
        ]

        cleanup_functions = []

        self.extraction_pipeline = Pipeline(
            [
                (
                    "bug_extractor",
                    bug_features.BugExtractor(feature_extractors, cleanup_functions),
                ),
                (
                    "union",
                    StructuredColumnTransformer(
                        [
                            (
                                "platform",
                                make_pipeline(
                                    DictExtractor("platform"), OrdinalEncoder()
                                ),
                                "data",
                            ),
                            (
                                "op_sys",
                                make_pipeline(
                                    DictExtractor("op_sys"), OrdinalEncoder()
                                ),
                                "data",
                            ),
                            (
                                "bug_reporter",
                                make_pipeline(
                                    DictExtractor("bug_reporter"),
                                    MissingOrdinalEncoder(),
                                ),
                                "data",
                            ),
                            (
                                "title_sequence",
                                KerasTextToSequences(
                                    self.short_desc_maxlen, self.short_desc_vocab_size
                                ),
                                "title",
                            ),
                            (
                                "first_comment_sequence",
                                KerasTextToSequences(
                                    self.long_desc_maxlen, self.long_desc_vocab_size
                                ),
                                "first_comment",
                            ),
                            (
                                "title_char_tfidf",
                                TfidfVectorizer(
                                    strip_accents="unicode",
                                    analyzer="char",
                                    stop_words="english",
                                    ngram_range=(2, 4),
                                    max_features=25000,
                                    sublinear_tf=True,
                                ),
                                "title",
                            ),
                            (
                                "title_word_tfidf",
                                TfidfVectorizer(
                                    strip_accents="unicode",
                                    min_df=0.0001,
                                    max_df=0.1,
                                    analyzer="word",
                                    token_pattern=r"\w{1,}",
                                    stop_words="english",
                                    ngram_range=(2, 4),
                                    max_features=30000,
                                    sublinear_tf=True,
                                ),
                                "title",
                            ),
                        ]
                    ),
                ),
            ]
        )

        kwargs = {
            "short_desc_maxlen": self.short_desc_maxlen,
            "short_desc_vocab_size": self.short_desc_vocab_size,
            "short_desc_emb_sz": self.short_desc_emb_sz,
            "long_desc_maxlen": self.long_desc_maxlen,
            "long_desc_vocab_size": self.long_desc_vocab_size,
            "long_desc_emb_sz": self.long_desc_emb_sz,
        }

        estimators = []
        for i, params in enumerate(self.params):
            kwargs["params"] = params
            estimator = ComponentNNClassifier(**kwargs)
            estimators.append(("model_{}".format(i), estimator))

        self.clf = VotingClassifier(
            estimators=estimators, voting="soft", weights=[1, 1, 1]
        )

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].named_transformers_.keys()

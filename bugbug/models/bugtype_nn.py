# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import OrdinalEncoder

from bugbug import bug_features
from bugbug.models.bugtype import KEYWORD_DICT, BugTypeModel
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
    from keras import Input, layers
    from keras.layers import (
        GRU,
        Bidirectional,
        Dense,
        Dropout,
        Embedding,
        Flatten,
        GlobalMaxPooling1D,
        SpatialDropout1D,
    )
    from keras.models import Model
except ImportError:
    raise ImportError(OPT_MSG_MISSING)


class BugtypeNNClassifier(KerasClassifier):
    def __init__(self, **kwargs):
        # (epochs, batch_size) combinations
        fit_params = [(2, 512)]
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

        has_str_inp = Input(shape=(1,), name="has_str")
        has_str_emb = Embedding(
            input_dim=self.has_str_emb_input_dim,
            output_dim=self.has_str_emb_output_dim,
            input_length=1,
        )(has_str_inp)
        has_str_emb = SpatialDropout1D(self.has_str_emb_spatial_dropout_rate)(
            has_str_emb
        )
        has_str_emb = Flatten()(has_str_emb)
        has_str_emb = Dropout(self.has_str_emb_dropout_rate)(has_str_emb)

        severity_inp = Input(shape=(1,), name="severity")
        severity_emb = Embedding(
            input_dim=self.severity_emb_input_dim,
            output_dim=self.severity_emb_output_dim,
            input_length=1,
        )(severity_inp)
        severity_emb = SpatialDropout1D(self.severity_emb_spatial_dropout_rate)(
            severity_emb
        )
        severity_emb = Flatten()(severity_emb)
        severity_emb = Dropout(self.severity_emb_dropout_rate)(severity_emb)

        is_coverity_issue_inp = Input(shape=(1,), name="is_coverity_issue")
        is_coverity_issue_emb = Embedding(
            input_dim=self.is_coverity_issue_emb_input_dim,
            output_dim=self.is_coverity_issue_emb_output_dim,
            input_length=1,
        )(is_coverity_issue_inp)
        is_coverity_issue_emb = SpatialDropout1D(
            self.is_coverity_issue_emb_spatial_dropout_rate
        )(is_coverity_issue_emb)
        is_coverity_issue_emb = Flatten()(is_coverity_issue_emb)
        is_coverity_issue_emb = Dropout(self.is_coverity_issue_emb_dropout_rate)(
            is_coverity_issue_emb
        )

        has_crash_signature_inp = Input(shape=(1,), name="has_crash_signature")
        has_crash_signature_emb = Embedding(
            input_dim=self.has_crash_signature_emb_input_dim,
            output_dim=self.has_crash_signature_emb_output_dim,
            input_length=1,
        )(has_crash_signature_inp)
        has_crash_signature_emb = SpatialDropout1D(
            self.has_crash_signature_emb_spatial_dropout_rate
        )(has_crash_signature_emb)
        has_crash_signature_emb = Flatten()(has_crash_signature_emb)
        has_crash_signature_emb = Dropout(self.has_crash_signature_emb_dropout_rate)(
            has_crash_signature_emb
        )

        blocked_bugs_number_inp = Input(shape=(1,), name="blocked_bugs_number")
        blocked_bugs_number_emb = Embedding(
            input_dim=self.blocked_bugs_number_emb_input_dim,
            output_dim=self.blocked_bugs_number_emb_output_dim,
            input_length=1,
        )(blocked_bugs_number_inp)
        blocked_bugs_number_emb = SpatialDropout1D(
            self.blocked_bugs_number_emb_spatial_dropout_rate
        )(blocked_bugs_number_emb)
        blocked_bugs_number_emb = Flatten()(blocked_bugs_number_emb)
        blocked_bugs_number_emb = Dropout(self.blocked_bugs_number_emb_dropout_rate)(
            blocked_bugs_number_emb
        )

        ever_affected_inp = Input(shape=(1,), name="ever_affected")
        ever_affected_emb = Embedding(
            input_dim=self.ever_affected_emb_input_dim,
            output_dim=self.ever_affected_emb_output_dim,
            input_length=1,
        )(ever_affected_inp)
        ever_affected_emb = SpatialDropout1D(
            self.ever_affected_emb_spatial_dropout_rate
        )(ever_affected_emb)
        ever_affected_emb = Flatten()(ever_affected_emb)
        ever_affected_emb = Dropout(self.ever_affected_emb_dropout_rate)(
            ever_affected_emb
        )

        affected_then_unaffected_inp = Input(
            shape=(1,), name="affected_then_unaffected"
        )
        affected_then_unaffected_emb = Embedding(
            input_dim=self.affected_then_unaffected_emb_input_dim,
            output_dim=self.affected_then_unaffected_emb_output_dim,
            input_length=1,
        )(affected_then_unaffected_inp)
        affected_then_unaffected_emb = SpatialDropout1D(
            self.affected_then_unaffected_emb_spatial_dropout_rate
        )(affected_then_unaffected_emb)
        affected_then_unaffected_emb = Flatten()(affected_then_unaffected_emb)
        affected_then_unaffected_emb = Dropout(
            self.affected_then_unaffected_emb_dropout_rate
        )(affected_then_unaffected_emb)

        product_inp = Input(shape=(1,), name="product")
        product_emb = Embedding(
            input_dim=self.product_emb_input_dim,
            output_dim=self.product_emb_output_dim,
            input_length=1,
        )(product_inp)
        product_emb = SpatialDropout1D(self.product_emb_spatial_dropout_rate)(
            product_emb
        )
        product_emb = Flatten()(product_emb)
        product_emb = Dropout(self.product_emb_dropout_rate)(product_emb)

        component_inp = Input(shape=(1,), name="component")
        component_emb = Embedding(
            input_dim=self.component_emb_input_dim,
            output_dim=self.component_emb_output_dim,
            input_length=1,
        )(component_inp)
        component_emb = SpatialDropout1D(self.component_emb_spatial_dropout_rate)(
            component_emb
        )
        component_emb = Flatten()(component_emb)
        component_emb = Dropout(self.component_emb_dropout_rate)(component_emb)

        keywords_inp = Input(shape=(1,), name="keywords")
        keywords_emb = Embedding(
            input_dim=self.keywords_emb_input_dim,
            output_dim=self.keywords_emb_output_dim,
            input_length=1,
        )(keywords_inp)
        keywords_emb = SpatialDropout1D(self.keywords_emb_spatial_dropout_rate)(
            keywords_emb
        )
        keywords_emb = Flatten()(keywords_emb)
        keywords_emb = Dropout(self.keywords_emb_dropout_rate)(keywords_emb)

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
                has_str_emb,
                severity_emb,
                is_coverity_issue_emb,
                has_crash_signature_emb,
                blocked_bugs_number_emb,
                ever_affected_emb,
                affected_then_unaffected_emb,
                product_emb,
                component_emb,
                keywords_emb,
                tfidf_word,
                tfidf_char,
            ],
            axis=-1,
        )

        x = Dense(self.x_dense_unit, activation="relu")(x)
        x = Dropout(self.x_dropout_rate)(x)
        x = Dense(y.shape[1], activation="sigmoid")(x)

        model = Model(
            [
                short_desc_inp,
                long_desc_inp,
                has_str_inp,
                severity_inp,
                is_coverity_issue_inp,
                has_crash_signature_inp,
                blocked_bugs_number_inp,
                ever_affected_inp,
                affected_then_unaffected_inp,
                product_inp,
                component_inp,
                keywords_inp,
                tfidf_word_inp,
                tfidf_char_inp,
            ],
            x,
        )
        model.compile(optimizer="adam", loss=["binary_crossentropy"], metrics=["acc"])

        return model


class BugTypeNNModel(BugTypeModel):
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
                "short_desc_encoded_gru_units": 250,
                "short_desc_encoded_gru_dropout": 0.45,
                "short_desc_encoded_recurrent_dropout": 0.45,
                "long_desc_emb_dropout_rate": 0.25,
                "long_desc_encoded_gru_units": 250,
                "long_desc_encoded_dropout": 0.45,
                "long_desc_encoded_recurrent_dropout": 0.45,
                "has_str_emb_input_dim": 14,
                "has_str_emb_output_dim": 30,
                "has_str_emb_spatial_dropout_rate": 0.1,
                "has_str_emb_dropout_rate": 0.4,
                "severity_emb_input_dim": 14,
                "severity_emb_output_dim": 30,
                "severity_emb_spatial_dropout_rate": 0.1,
                "severity_emb_dropout_rate": 0.4,
                "is_coverity_issue_emb_input_dim": 14,
                "is_coverity_issue_emb_output_dim": 30,
                "is_coverity_issue_emb_spatial_dropout_rate": 0.1,
                "is_coverity_issue_emb_dropout_rate": 0.4,
                "has_crash_signature_emb_input_dim": 14,
                "has_crash_signature_emb_output_dim": 30,
                "has_crash_signature_emb_spatial_dropout_rate": 0.1,
                "has_crash_signature_emb_dropout_rate": 0.4,
                "blocked_bugs_number_emb_input_dim": 14,
                "blocked_bugs_number_emb_output_dim": 30,
                "blocked_bugs_number_emb_spatial_dropout_rate": 0.1,
                "blocked_bugs_number_emb_dropout_rate": 0.4,
                "ever_affected_emb_input_dim": 14,
                "ever_affected_emb_output_dim": 30,
                "ever_affected_emb_spatial_dropout_rate": 0.1,
                "ever_affected_emb_dropout_rate": 0.4,
                "affected_then_unaffected_emb_input_dim": 14,
                "affected_then_unaffected_emb_output_dim": 30,
                "affected_then_unaffected_emb_spatial_dropout_rate": 0.1,
                "affected_then_unaffected_emb_dropout_rate": 0.4,
                "product_emb_input_dim": 14,
                "product_emb_output_dim": 30,
                "product_emb_spatial_dropout_rate": 0.1,
                "product_emb_dropout_rate": 0.4,
                "component_emb_input_dim": 48,
                "component_emb_output_dim": 55,
                "component_emb_spatial_dropout_rate": 0.1,
                "component_emb_dropout_rate": 0.4,
                "keywords_emb_input_dim": 46544,
                "keywords_emb_output_dim": 110,
                "keywords_emb_spatial_dropout_rate": 0.15,
                "keywords_emb_dropout_rate": 0.45,
                "tfidf_word_dense_units": 610,
                "tfidf_word_dropout_rate": 0.45,
                "tfidf_char_inp_dense_unit": 510,
                "tfidf_char_inp_dropout_rate": 0.5,
                "x_dense_unit": 1970,
                "x_dropout_rate": 0.5,
            }
        ]

        feature_extractors = [
            bug_features.has_str(),
            bug_features.severity(),
            # Ignore keywords that would make the ML completely skewed
            # (we are going to use them as 100% rules in the evaluation phase).
            bug_features.keywords(set(KEYWORD_DICT.keys())),
            bug_features.is_coverity_issue(),
            bug_features.has_crash_signature(),
            bug_features.blocked_bugs_number(),
            bug_features.ever_affected(),
            bug_features.affected_then_unaffected(),
            bug_features.product(),
            bug_features.component(),
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
                                "has_str",
                                make_pipeline(
                                    DictExtractor("has_str"), MissingOrdinalEncoder()
                                ),
                                "data",
                            ),
                            (
                                "severity",
                                make_pipeline(
                                    DictExtractor("severity"), OrdinalEncoder()
                                ),
                                "data",
                            ),
                            (
                                "keywords",
                                make_pipeline(
                                    DictExtractor("keywords"), MissingOrdinalEncoder()
                                ),
                                "data",
                            ),
                            (
                                "is_coverity_issue",
                                make_pipeline(
                                    DictExtractor("is_coverity_issue"),
                                    MissingOrdinalEncoder(),
                                ),
                                "data",
                            ),
                            (
                                "has_crash_signature",
                                make_pipeline(
                                    DictExtractor("has_crash_signature"),
                                    MissingOrdinalEncoder(),
                                ),
                                "data",
                            ),
                            (
                                "blocked_bugs_number",
                                make_pipeline(
                                    DictExtractor("blocked_bugs_number"),
                                    MissingOrdinalEncoder(),
                                ),
                                "data",
                            ),
                            (
                                "ever_affected",
                                make_pipeline(
                                    DictExtractor("ever_affected"),
                                    MissingOrdinalEncoder(),
                                ),
                                "data",
                            ),
                            (
                                "affected_then_unaffected",
                                make_pipeline(
                                    DictExtractor("affected_then_unaffected"),
                                    MissingOrdinalEncoder(),
                                ),
                                "data",
                            ),
                            (
                                "product",
                                make_pipeline(
                                    DictExtractor("product"), OrdinalEncoder()
                                ),
                                "data",
                            ),
                            (
                                "component",
                                make_pipeline(
                                    DictExtractor("component"), OrdinalEncoder()
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

        kwargs["params"] = self.params[0]
        self.clf = BugtypeNNClassifier(**kwargs)

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].named_transformers_.keys()

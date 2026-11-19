# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import CountVectorizer

from bugbug import sklearn_utils


def test_StructuredColumnTransformer() -> None:
    transformers = [
        ("feat1_transformed", CountVectorizer(), "feat1"),
        ("feat2_transformed", CountVectorizer(), "feat2"),
    ]

    df = pd.DataFrame(
        [
            {
                "feat1": "First",
                "feat2": "Second",
            },
            {
                "feat1": "Third",
                "feat2": "Fourth",
            },
        ]
    )

    np.testing.assert_array_equal(
        ColumnTransformer(transformers).fit_transform(df),
        np.array([[1, 0, 0, 1], [0, 1, 1, 0]]),
    )

    np.testing.assert_array_equal(
        sklearn_utils.StructuredColumnTransformer(transformers).fit_transform(df),
        np.array(
            [[([1, 0], [0, 1])], [([0, 1], [1, 0])]],
            dtype=[
                ("feat1_transformed", "<i8", (2,)),
                ("feat2_transformed", "<i8", (2,)),
            ],
        ),
    )

    np.testing.assert_array_equal(
        sklearn_utils.StructuredColumnTransformer(transformers)
        .fit_transform(df)
        .view(np.dtype("int64")),
        ColumnTransformer(transformers).fit_transform(df),
    )

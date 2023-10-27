# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import torch
from sklearn.base import BaseEstimator, TransformerMixin
from torch import nn
from transformers import AutoModel, AutoModelForSequenceClassification

OPT_MSG_MISSING = (
    "Optional dependencies are missing, install them with: pip install bugbug[nn]\n"
)


class ExtractEmbeddings(BaseEstimator, TransformerMixin):
    def __init__(self, model_name: str):
        self.model = AutoModel.from_pretrained(model_name)

    def fit(self, X, y):
        return self

    def transform(self, X):
        with torch.no_grad():
            # TODO: support .last_hidden_state.mean(dim=1) as an alternative
            return self.model(**X).last_hidden_state[:, 0, :]


def get_training_device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


class DistilBertModule(nn.Module):
    def __init__(self, name, num_labels, last_layer_only=False):
        super().__init__()
        self.name = name
        self.num_labels = num_labels
        self.last_layer_only = last_layer_only

        self.reset_weights()

    def reset_weights(self):
        self.bert = AutoModelForSequenceClassification.from_pretrained(
            self.name, num_labels=self.num_labels
        )
        if self.last_layer_only:
            for param in self.bert.distilbert.parameters():
                param.requires_grad = False

    def forward(self, **kwargs):
        pred = self.bert(**kwargs)
        return pred.logits

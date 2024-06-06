# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import re

import nltk
import numpy as np
import pandas as pd
from nltk.corpus import stopwords
from nltk.corpus import wordnet as wn
from nltk.tokenize import word_tokenize
from sklearn.feature_extraction.text import CountVectorizer

nltk.download("verbnet")
nltk.download("stopwords")
nltk.download("punkt")


def clean_text(x):
    if x is not None:
        if isinstance(x, list):
            x = "\n".join(x)
        x = re.sub(r"^\W+", "", x)  # remove special characters
        x = re.sub(r"\W+$", "", x)  # remove special characters
        x = re.sub(r"\W+", " ", x)  # remove special characters
        x = re.sub(r"[0-9]+", "<NUMBER>", x)  # remove numbers
        x = re.sub(r"_", " ", x)  # remove numbers
        x = re.sub(r"^\w\s", " ", x)  # remove single characters
        x = re.sub(r"\s\w\s", " ", x)  # remove single characters
        x = re.sub(r"\s\w$", " ", x)  # remove single characters
        x = re.sub(r"^\w$", " ", x)  # remove single characters
        x = re.sub(r"\s+", " ", x)  # remove single characters
        return x
    else:
        return None


def get_verb_intent(verb):
    # Returns the type of verb intent for the given verb.
    verb_tags = nltk.pos_tag([verb])
    verb_tag = verb_tags[0][1]
    return [verb_tags]
    if verb_tag.startswith("VB"):
        return "Action"
    elif verb_tag == "MD":
        return "Modal"
    else:
        return ""


def stop_word_labelling(w):
    stop_words = set(stopwords.words("english"))
    word_tokens = word_tokenize(w)
    # converts the words in word_tokens to lower case and then checks whether
    # they are present in stop_words or not
    filtered_sentence = [w for w in word_tokens if w.lower() not in stop_words]
    # with no lower case conversion
    filtered_sentence = []
    for w in word_tokens:
        if w not in stop_words:
            filtered_sentence.append(w)
    return len(filtered_sentence) == 0


def filter_data(data, target_X="com_body"):
    info = ""

    # BoW to find popular words:
    ngram = (2, 2)
    num_use = 25
    vectorizer = CountVectorizer(ngram_range=ngram)
    clean_X = [clean_text(e) for e in data[target_X]]

    # Create DataFrame of Bag-of-Words
    vec = vectorizer.fit_transform(clean_X)
    feat = list(vectorizer.get_feature_names_out())
    feat_id = {f: i for i, f in enumerate(feat)}
    count_tot = np.sum(vec, axis=0).tolist()[0]
    count_use = np.sum(vec > 0, axis=0).tolist()[0]
    bow_count = pd.DataFrame({"words": feat, "count": count_tot, "use": count_use})

    init_len = len(bow_count)
    info += f"Initial number of ngram {ngram} : {init_len}\n"
    bow_count = bow_count.sort_values(by="use", ascending=False)
    bow_count = bow_count[bow_count["use"] > num_use]  # filter out uncommon ngrams
    info += f"Remove if used less than {num_use}X : {len(bow_count)} ({100000*len(bow_count) // init_len/1000}%)\n"
    bow_count["stop"] = [not stop_word_labelling(w) for w in bow_count["words"]]
    bow_count = bow_count[
        [e is True for e in bow_count["stop"]]
    ]  # remove if ngram composed of stopwords
    info += f"Remove if used less stopwords ngrams : {len(bow_count)} ({100000*len(bow_count) // init_len/1000}%)\n"

    # Defining two type of verbs labels
    feat = bow_count["words"]
    type = None
    type2 = None
    type = [[wn.synsets(w) for w in phrase.split(" ")] for phrase in feat]
    type_v = [("v" in [e.pos() for f in clean_X for e in f]) for clean_X in type]
    type2 = [nltk.pos_tag(w.split(" ")) for w in feat]
    type_v2 = [
        np.any(["MD" == e[1] or "VB" in e[1] for e in clean_X]) for clean_X in type2
    ]
    ana = pd.DataFrame(
        {
            "words": feat,
            "type": type,
            "type2": type2,
            "is_verb1": type_v,
            "is_verb2": type_v2,
        }
    )
    ana = ana[ana["is_verb1"] & ana["is_verb2"]]
    bow_count = bow_count.merge(ana, on=["words"])
    info += f"Remove not-verbs : {len(bow_count)} ({100000*len(bow_count) // init_len/1000}%)\n"

    ids = [feat_id[w] for w in bow_count["words"]]

    init_data_len = len(data)
    info += f"FULL DATASET : {init_data_len} \n"

    data = data[np.array(np.sum(vec[:, ids], axis=1).ravel().tolist()[0]) > 0]

    info += f"After filter : {len(data)} ({1000*len(data) // init_data_len/10}%)\n"

    return data.reset_index()

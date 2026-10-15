# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import pickle

import pytest

from bugbug import models
from bugbug.model import Model


def test_load_unpickles_model_pkl(tmp_path) -> None:
    model_directory = tmp_path / "somemodel"
    model_directory.mkdir()
    with (model_directory / "model.pkl").open("wb") as f:
        pickle.dump({"pickled": True}, f)

    assert Model.load(str(model_directory)) == {"pickled": True}


def test_load_dispatches_to_registered_class_without_model_pkl(
    tmp_path, monkeypatch
) -> None:
    loaded_directories: list[str] = []
    sentinel = object()

    class CheckpointModel(Model):
        @classmethod
        def load(cls, model_directory):
            loaded_directories.append(model_directory)
            return sentinel

    requested_names: list[str] = []

    def get_model_class(model_name):
        requested_names.append(model_name)
        return CheckpointModel

    monkeypatch.setattr(models, "get_model_class", get_model_class)

    # No model.pkl: the directory name identifies the registered class.
    model_directory = tmp_path / "somecustommodel"
    model_directory.mkdir()

    assert Model.load(str(model_directory)) is sentinel
    assert requested_names == ["somecustom"]
    assert loaded_directories == [str(model_directory)]


def test_load_raises_when_registered_class_does_not_override_load(
    tmp_path, monkeypatch
) -> None:
    class PickledModel(Model):
        pass

    monkeypatch.setattr(models, "get_model_class", lambda model_name: PickledModel)

    model_directory = tmp_path / "pickledmodel"
    model_directory.mkdir()

    # The registered class relies on model.pkl too, so there is nothing to
    # dispatch to; the original error is preserved instead of recursing.
    with pytest.raises(FileNotFoundError):
        Model.load(str(model_directory))


def test_load_raises_for_unregistered_model_without_model_pkl(tmp_path) -> None:
    model_directory = tmp_path / "notregisteredmodel"
    model_directory.mkdir()

    with pytest.raises(FileNotFoundError):
        Model.load(str(model_directory))

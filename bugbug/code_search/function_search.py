# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Function:
    name: str
    start: int
    file: str
    source: str


class FunctionSearch(ABC):
    @abstractmethod
    def get_function_by_line(
        self, commit_hash: str, path: str, line: int
    ) -> list[Function]:
        raise NotImplementedError

    @abstractmethod
    def get_function_by_name(
        self, commit_hash: str, path: str, function_name: str
    ) -> list[Function]:
        raise NotImplementedError


function_search_classes = {}


def register_function_search(name, cls):
    function_search_classes[name] = cls

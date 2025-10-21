# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from abc import ABC, abstractmethod
from typing import Any


class GenerativeModelTool(ABC):
    @property
    @abstractmethod
    def version(self) -> str: ...

    @abstractmethod
    def run(self, *args, **kwargs) -> Any: ...

    @staticmethod
    def _print_answer(answer):
        print(f"\u001b[33;1m\033[1;3m{answer}\u001b[0m")

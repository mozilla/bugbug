from abc import ABC, abstractmethod
from typing import Any


class MLCommentFilter(ABC):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

    @abstractmethod
    def query_ml_filter(self, comments, *args, **kwargs) -> Any: ...


ml_comment_filters = {}


def register_ml_comment_filters(name, cls):
    ml_comment_filters[name] = cls

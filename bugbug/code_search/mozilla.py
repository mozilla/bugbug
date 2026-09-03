from bugbug.code_search.function_search import (
    Function,
    FunctionSearch,
    register_function_search,
)
from bugbug.code_search.parser import FunctionSearchParser
from bugbug.code_search.searchfox_api import FunctionSearchSearchfoxAPI
from bugbug.code_search.searchfox_data import FunctionSearchSearchfoxData


class FunctionSearchMozilla(FunctionSearch):
    def __init__(self, repo_dir, get_file=None, fast=False):
        super().__init__()
        self.repo_dir = repo_dir
        self.get_file = get_file or FunctionSearchSearchfoxAPI._get_file
        self.fast = fast

    def get_function(
        self, commit_hash, path, function_name=None, line=None, fast=False
    ):
        # FIXME: we should use commit_hash. However, since not all stages support
        # retrieving data based on commit_hash, we are using "default" as a
        # workaround to guarantee the integrity of the data.
        commit_hash = "default"

        # In fast mode, only use Searchfox website.
        # In slow mode (for historical data), try Searchfox first and fallback on parsing with rust-code-analysis.

        result = []

        if not fast:
            function_search_searchfox_data = FunctionSearchSearchfoxData()
            if function_name is not None:
                result = function_search_searchfox_data.get_function_by_name(
                    commit_hash, path, function_name
                )
            elif line is not None:
                result = function_search_searchfox_data.get_function_by_line(
                    commit_hash, path, line
                )

            # If we were not able to find the function by using Searchfox, fallback on parsing.
            if not result:
                function_search_parser = FunctionSearchParser(
                    self.repo_dir, self.get_file
                )
                if function_name is not None:
                    result = function_search_parser.get_function_by_name(
                        commit_hash, path, function_name
                    )
                elif line is not None:
                    result = function_search_parser.get_function_by_line(
                        commit_hash, path, line
                    )

        if not result:
            function_search_searchfox_api = FunctionSearchSearchfoxAPI(self.get_file)
            if function_name is not None:
                result = function_search_searchfox_api.get_function_by_name(
                    commit_hash, path, function_name
                )
            elif line is not None:
                result = function_search_searchfox_api.get_function_by_line(
                    commit_hash, path, line
                )

        # Filter out results when there are too many.
        # TODO: If the function is in a JS file and we found its definition in both JS and C++ files, use the definition in JS file.
        # TODO: If the function is in a non-test file and we found its definition in both test and non-test files, use the definition in the non-test file (and the opposite for functions in test files)

        perfect_match = [func for func in result if func.name == function_name]

        return perfect_match if perfect_match else result

    def get_function_by_line(
        self, commit_hash: str, path: str, line: int
    ) -> list[Function]:
        return self.get_function(commit_hash, path, line=line, fast=self.fast)

    def get_function_by_name(
        self, commit_hash: str, path: str, function_name: str
    ) -> list[Function]:
        return self.get_function(
            commit_hash, path, function_name=function_name, fast=self.fast
        )


register_function_search("mozilla", FunctionSearchMozilla)

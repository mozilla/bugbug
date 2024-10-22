from bugbug.code_search.function_search import (
    Function,
    FunctionSearch,
    register_function_search,
)
from bugbug.code_search.parser import FunctionSearchParser
from bugbug.code_search.searchfox_api import FunctionSearchSearchfoxAPI
from bugbug.code_search.searchfox_data import FunctionSearchSearchfoxData
from bugbug.tools.code_review import PhabricatorPatch


class FunctionSearchMozilla(FunctionSearch):
    def __init__(self, repo_dir, get_file, fast=False):
        super().__init__()
        self.repo_dir = repo_dir
        self.get_file = get_file
        self.fast = fast

    def get_function(
        self, commit_hash, path, function_name=None, line=None, fast=False
    ):
        # FIXME: we should use commit_hash. However, since not all stages support
        # retrieving data based on commit_hash, we are using "tip" as a
        # workaround to guarantee the integrity of the data.
        commit_hash = "tip"

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


if __name__ == "__main__":
    import sys

    from libmozdata.phabricator import PhabricatorAPI

    from bugbug.utils import get_secret, get_session, get_user_agent, setup_libmozdata

    setup_libmozdata()

    phabricator = PhabricatorAPI(
        get_secret("PHABRICATOR_TOKEN"), get_secret("PHABRICATOR_URL")
    )

    def get_file(commit_hash, path):
        r = get_session("hgmo").get(
            f"https://hg.mozilla.org/mozilla-unified/raw-file/{commit_hash}/{path}",
            headers={
                "User-Agent": get_user_agent(),
            },
        )
        r.raise_for_status()
        return r.text

    repo_dir = sys.argv[1]

    function_search_mozilla = FunctionSearchMozilla(repo_dir, get_file, False)

    # https://phabricator.services.mozilla.com/D199272?id=811858
    patch1 = PhabricatorPatch("811858")

    # In this case, the function was not used before the patch.
    print(
        function_search_mozilla.get_function_by_name(
            patch1.base_commit_hash,
            "dom/base/nsObjectLoadingContent.cpp",
            "LowerCaseEqualsASCII",
        )
    )

    # In this case, the function was used before the patch.
    print(
        function_search_mozilla.get_function_by_name(
            patch1.base_commit_hash,
            "dom/base/nsObjectLoadingContent.cpp",
            "HtmlObjectContentTypeForMIMEType",
        )
    )

    # https://phabricator.services.mozilla.com/D199248?id=811740
    patch2 = PhabricatorPatch("811740")

    # In this case, it is a JS file.
    print(
        function_search_mozilla.get_function_by_name(
            patch2.base_commit_hash,
            "testing/modules/XPCShellContentUtils.sys.mjs",
            "registerPathHandler",
        )
    )

    patch3 = PhabricatorPatch("721783")

    print(
        function_search_mozilla.get_function_by_name(
            patch3.base_commit_hash,
            "dom/performance/Performance.cpp",
            "Performance::MemoryPressure",
        )
    )

    patch4 = PhabricatorPatch("736446")

    function_search_mozilla.get_function_by_line(
        patch4.base_commit_hash,
        "browser/base/content/test/webrtc/browser_devices_select_audio_output.js",
        180,
    )

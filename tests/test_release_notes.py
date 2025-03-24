import argparse

from bugbug import generative_model_tool
from bugbug.tools.release_notes import ReleaseNotesCommitsSelector


def test_get_previous_version():
    parser = argparse.ArgumentParser(description="Generate Firefox release notes.")
    generative_model_tool.create_llm_to_args(parser)

    args = parser.parse_args()
    llm = generative_model_tool.create_llm_from_args(args)
    selector = ReleaseNotesCommitsSelector(chunk_size=100, llm=llm)
    assert (
        selector.get_previous_version("FIREFOX_BETA_135_BASE")
        == "FIREFOX_BETA_134_BASE"
    )
    assert selector.get_previous_version("FIREFOX_NIGHTLY_132") == "FIREFOX_NIGHTLY_131"
    assert (
        selector.get_previous_version("FIREFOX_RELEASE_130_2")
        == "FIREFOX_RELEASE_129_2"
    )

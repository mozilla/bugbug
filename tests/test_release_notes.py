from bugbug.tools.release_notes import get_previous_version


def test_get_previous_version():
    assert get_previous_version("FIREFOX_BETA_135_BASE") == "FIREFOX_BETA_134_BASE"
    assert get_previous_version("FIREFOX_NIGHTLY_132") == "FIREFOX_NIGHTLY_131"
    assert get_previous_version("FIREFOX_RELEASE_130_2") == "FIREFOX_RELEASE_129_2"

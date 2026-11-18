from hackbot_agents.autowebcompat_etp.firefox_build import firefox_build

APPLICATION_INI = (
    "[App]\nVendor=Mozilla\nName=Firefox\nVersion=147.0a1\nBuildID=20261118093012\n"
)


def test_firefox_build_linux(tmp_path):
    (tmp_path / "application.ini").write_text(APPLICATION_INI)
    assert firefox_build(tmp_path / "firefox") == ("147.0a1", "20261118093012")


def test_firefox_build_macos(tmp_path):
    contents = tmp_path / "Nightly.app" / "Contents"
    (contents / "Resources").mkdir(parents=True)
    (contents / "Resources" / "application.ini").write_text(APPLICATION_INI)
    binary = contents / "MacOS" / "firefox"
    assert firefox_build(binary) == ("147.0a1", "20261118093012")


def test_firefox_build_without_application_ini(tmp_path):
    assert firefox_build(tmp_path / "firefox") == (None, None)


def test_firefox_build_with_a_broken_application_ini(tmp_path):
    (tmp_path / "application.ini").write_text("[App]\nVersion=147.0a1\n")
    assert firefox_build(tmp_path / "firefox") == (None, None)

from unittest import mock

import mozdownload
import pytest
from hackbot_agents.autowebcompat_repro import browser


def _install(side_effect, channel="nightly"):
    with (
        mock.patch.object(
            browser.mozdownload, "FactoryScraper", side_effect=side_effect
        ) as factory,
        mock.patch.object(browser.mozinstall, "install"),
        mock.patch.object(browser.mozinstall, "get_binary", return_value="/ff"),
        mock.patch.object(browser.tempfile, "mkdtemp", return_value="/tmp/ff"),
    ):
        browser.install_firefox(channel)
    return factory


def test_nightly_falls_back_to_earlier_day():
    scraper = mock.Mock()
    factory = _install([mozdownload.errors.NotFoundError("x", "y"), scraper])

    assert "date" not in factory.call_args_list[0].kwargs
    assert "date" in factory.call_args_list[1].kwargs
    scraper.download.assert_called_once()


def test_nightly_gives_up_after_fallback_days():
    errors = [mozdownload.errors.NotFoundError("x", "y")] * (
        browser.NIGHTLY_FALLBACK_DAYS + 1
    )
    with pytest.raises(mozdownload.errors.NotFoundError):
        _install(errors)


def test_stable_does_not_fall_back():
    with pytest.raises(mozdownload.errors.NotFoundError):
        _install([mozdownload.errors.NotFoundError("x", "y")], channel="stable")

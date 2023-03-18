# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import dateutil.parser

from bugbug.bug_features import business_day_range


def test_consecutive_work_days():
    result = business_day_range(
        dateutil.parser.parse("March 17 2023"), dateutil.parser.parse("March 13th 2023")
    )
    assert result == 4.0, f"Dates did not match, actual result: {result}"


def test_work_days_with_weekend():
    result = business_day_range(
        dateutil.parser.parse("March 17 2023"), dateutil.parser.parse("March 8th 2023")
    )
    assert result == 7.0, f"Dates did not match, actual result: {result}"


def test_work_days_full_month():
    result = business_day_range(
        dateutil.parser.parse("February 28th 2023"),
        dateutil.parser.parse("February 1st 2023"),
    )
    assert result == 19.0, f"Dates did not match, actual result: {result}"


def test_work_days_start_on_weekend():
    result = business_day_range(
        dateutil.parser.parse("March 10th 2023"),
        dateutil.parser.parse("March 5th 2023"),
    )
    assert result == 5.0, f"Dates did not match, actual result: {result}"


def test_work_days_end_on_weekend():
    result = business_day_range(
        dateutil.parser.parse("March 11th 2023"),
        dateutil.parser.parse("March 6th 2023"),
    )
    assert result == 5.0, f"Dates did not match, actual result: {result}"


def test_work_days_start_and_end_on_weekend():
    result = business_day_range(
        dateutil.parser.parse("March 11th 2023"),
        dateutil.parser.parse("March 5th 2023"),
    )
    assert result == 6.0, f"Dates did not match, actual result: {result}"


def test_work_days_start_and_end_on_weekend_over_year_change():
    result = business_day_range(
        dateutil.parser.parse("January 7th 2023"),
        dateutil.parser.parse("December 25th 2022"),
    )
    assert result == 11.0, f"Dates did not match, actual result: {result}"

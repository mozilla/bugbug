# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

TEST_CASES_PROMPT_TEMPLATE = """You are an expert Quality Assurance Engineer with expertise in designing high level test cases for features of the {target_software} web browser.
You are given a feature's description, its scope of testing, and a list of already existing test cases.
Using the knowledge and information you are given, generate no more than 15 test cases that have been missed for the feature.

-- This is the feature's description --:
{feature_description}

-- These are the existing test cases so far for the feature --:
{qa_test_cases}

-- This is the feature's scope of testing --:
{test_scope}

-- Here are some tips for success --:
1. Thoroughly understand the feature from the description, scope of testing and the existing test cases.
2. Alter the wording while generating test cases.
3. Check to see if each generated case is relevant to the feature.
4. Check to see if each generated case is within the scope of testing.
5. Check to see if each generated case is dissimilar to any existing test cases.

The test cases should be presented in a numbered list, with each entry being a single, concise test case.
Avoid using a title and markdown formatting."""


TEST_STEPS_PROMPT_TEMPLATE = """You are an expert Quality Assurance Engineer with expertise in designing detailed test steps for test cases of features of the {target_software} web browser.
You are given a feature's description and a list of test cases.
Using the knowledge and information you are given, generate test steps for each test case.

-- This is the feature's description --:
{feature_description}

-- These are the test cases for the feature --:
{test_cases}

-- Here are some tips for success --:
1. Thoroughly understand the feature from the description and the test cases.
2. For each test case, generate clear and concise steps to execute the test case.
3. Each test case should have its own set of steps.
4. Present the steps in a numbered list under each test case.
Avoid using a title and markdown formatting.

-- Here are some examples --:
Test Case 1: Ensure that Rich suggestions entries match the design
Test Steps:
1. Launch Firefox.
2. Start typing a popular keyword inside the Address Bar.
3. Observe the Rich entities icon and description.

Test Case 2: Search-shortcut - Ensure that Rich entities are accessible via keyboard
Test Steps:
1. Launch Firefox.
2. Observe the Address Bar.
3. Click inside the Address Bar, select the google search shortcut.
4. Press 'Down' arrow key.
5. Navigate through the Rich entities using Up/Down arrow keys."""

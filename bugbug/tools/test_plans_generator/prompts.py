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
6. Return only valid JSON with a "test_cases" key.
7. Each entry in "test_cases" must have an "id" integer and a "test_case" string.

Avoid using a title, markdown formatting, comments, or any text outside the JSON object.

-- Here is an example of the expected output format --:
{{
  "test_cases": [
    {{
      "id": 1,
      "test_case": "Verify that sponsored suggestions can be disabled from Settings."
    }},
    {{
      "id": 2,
      "test_case": "Verify that organic search suggestions continue to appear when sponsored suggestions are disabled."
    }}
  ]
}}"""


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
3. Do not include the Launch Firefox step, as it is assumed to be the first step for every test case.
4. Each test case should have its own set of steps.
5. Return only valid JSON with a "test_cases" key.
6. Keep the same "id" and "test_case" values from the input test cases.
7. Each entry in "test_cases" must have an "id" integer, a "test_case" string, and a "test_steps" array of strings.
Avoid using a title, markdown formatting, comments, or any text outside the JSON object.

-- Here are some examples --:
{{
  "test_cases": [
    {{
      "id": 1,
      "test_case": "Ensure that Rich suggestions entries match the design",
      "test_steps": [
        "Start typing a popular keyword inside the Address Bar.",
        "Observe the Rich entities icon and description."
      ]
    }},
    {{
      "id": 2,
      "test_case": "Search-shortcut - Ensure that Rich entities are accessible via keyboard",
      "test_steps": [
        "Observe the Address Bar.",
        "Click inside the Address Bar, select the google search shortcut.",
        "Press 'Down' arrow key.",
        "Navigate through the Rich entities using Up/Down arrow keys."
      ]
    }}
  ]
}}"""

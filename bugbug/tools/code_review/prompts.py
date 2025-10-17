# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Prompt templates for code review agent."""

TARGET_SOFTWARE: str | None = None

PROMPT_TEMPLATE_SUMMARIZATION = """You are an expert reviewer for {experience_scope}, with experience on source code reviews.

Please, analyze the code provided and report a summarization about the new changes; for that, focus on the coded added represented by lines that start with "+".

The summarization should have two parts:
    1. **Intent**: Describe the intent of the changes, what they are trying to achieve, and how they relate to the bug or feature request.
    2. **Structure**: Describe the structure of the changes, including any new functions, classes, or modules introduced, and how they fit into the existing codebase.

Do not include any code in the summarization, only a description of the changes.

**Bug title**:
{bug_title}

**Commit message**:
{patch_title}

**Diff**:
{patch}"""

PROMPT_TEMPLATE_REVIEW = """**Task**:

Generate high-quality code review comments for the patch provided below.

**Instructions**:

1. **Analyze the Changes**:

   * Understand the intent and structure of the changes in the patch.
   * Use the provided summarization for context, but prioritize what's visible in the diff.

2. **Identify Issues**:

   * Detect bugs, logical errors, performance concerns, security issues, or violations of the `{target_code_consistency}` coding standards.
   * Focus only on **new or changed lines** (lines beginning with `+`).

3. **Assess Confidence and Order**:

   * **Sort the comments by descending confidence and importance**:
     * Start with issues you are **certain are valid**.
     * Also, prioritize important issues that you are **confident about**.
     * Follow with issues that are **plausible but uncertain** (possible false positives).
   * Assign each comment a numeric `order`, starting at 1.

4. **Write Clear, Constructive Comments**:

   * Use **direct, declarative language**.
   * Keep comments **short and specific**.
   * Focus strictly on code-related concerns.
   * Avoid hedging language (e.g., don’t use “maybe”, “might want to”, or form questions).
   * Avoid repeating what the code is doing unless it supports your critique.

5. **Use available tools**:
    * Consider using available tools to better understand the context of the code changes you are reviewing.
    * Limit the use of tools to only when you need more context to analyze the code changes.

**Avoid Comments That**:

* Refer to unmodified code (lines without a `+` prefix).
* Ask for verification or confirmation (e.g., “Check if…”).
* Provide praise or restate obvious facts.
* Focus on testing.

---

**Output Format**:

Respond only with a **JSON list**. Each object must contain the following fields:

* `"file"`: The relative path to the file the comment applies to.
* `"code_line"`: The number of the specific changed line of code that the comment refers to.
* `"comment"`: A concise review comment.
* `"explanation"`: A brief rationale for the comment, including how confident you are and why.
* `"order"`: An integer indicating the comment’s priority (1 = highest confidence/importance).

---

**Examples**:

{comment_examples}
{approved_examples}

---

**Patch Summary**:

{patch_summarization}

---

**Patch to Review**:

{patch}
"""


TEMPLATE_COMMENT_EXAMPLE = """Patch example {example_number}:

{patch}

Review comments for example {example_number}:

{comments}"""


PROMPT_TEMPLATE_FILTERING_ANALYSIS = """Filter review comments to keep those that:
- are consistent with the {target_code_consistency} source code;
- focus on reporting possible bugs, functional regressions, issues, or similar concerns;
- report readability or design concerns.

Exclude comments that:
- only describe the change;
- restate obvious facts like renamed variables or replaced code;
- include praising;
- ask if changes are intentional or ask to ensure things exist.

Only return a valid JSON list. Do not drop any key from the JSON objects.

Comments:
{comments}

As examples of not expected comments, not related to the current patch, please, check some below:
    - {rejected_examples}
"""


DEFAULT_REJECTED_EXAMPLES = """Please note that these are minor improvements and the overall quality of the patch is good. The documentation is being expanded in a clear and structured way, which will likely be beneficial for future development.
    - Please note that these are just suggestions and the code might work perfectly fine as it is. It's always a good idea to test all changes thoroughly to ensure they work as expected.
    - Overall, the patch seems to be well implemented with no major concerns. The developers have made a conscious decision to align with Chrome's behavior, and the reasoning is well documented.
    - There are no complex code changes in this patch, so there's no potential for major readability regressions or bugs introduced by the changes.
    - The `focus(...)` method is called without checking if the element and its associated parameters exist or not. It would be better to check if the element exists before calling the `focus()` method to avoid potential errors.
    - It's not clear if the `SearchService.sys.mjs` file exists or not. If it doesn't exist, this could cause an error. Please ensure that the file path is correct.
    - This is a good addition to the code."""


STATIC_COMMENT_EXAMPLES = [
    {
        "comment": {
            "filename": "netwerk/streamconv/converters/mozTXTToHTMLConv.cpp",
            "start_line": 1211,
            "content": "You are using `nsAutoStringN<256>` instead of `nsString`. This is a good change as `nsAutoStringN<256>` is more efficient for small strings. However, you should ensure that the size of `tempString` does not exceed 256 characters, as `nsAutoStringN<256>` has a fixed size.",
            "explanation": "THE JUSTIFICATION GOES HERE",
        },
        "raw_hunk": """@@ -1206,11 +1206,11 @@
     } else {
       uint32_t start = uint32_t(i);
       i = aInString.FindChar('<', i);
       if (i == kNotFound) i = lengthOfInString;

-      nsString tempString;
+      nsAutoStringN<256> tempString;
       tempString.SetCapacity(uint32_t((uint32_t(i) - start) * growthRate));
       UnescapeStr(uniBuffer, start, uint32_t(i) - start, tempString);
       ScanTXT(tempString, whattodo, aOutString);
     }
   }""",
    },
    {
        "comment": {
            "filename": "toolkit/components/extensions/ExtensionDNR.sys.mjs",
            "start_line": 1837,
            "content": "The `filterAAR` function inside `#updateAllowAllRequestRules()` is created every time the method is called. Consider defining this function outside of the method to avoid unnecessary function creation.",
            "explanation": "THE JUSTIFICATION GOES HERE",
        },
        "raw_hunk": """@@ -1812,18 +1821,27 @@
       rulesets.push(
         this.makeRuleset(id, idx + PRECEDENCE_STATIC_RULESETS_BASE, rules)
       );
     }
     this.enabledStaticRules = rulesets;
+    this.#updateAllowAllRequestRules();
   }

   getSessionRules() {
     return this.sessionRules.rules;
   }

   getDynamicRules() {
     return this.dynamicRules.rules;
+  }
+
+  #updateAllowAllRequestRules() {
+    const filterAAR = rule => rule.action.type === "allowAllRequests";
+    this.hasRulesWithAllowAllRequests =
+      this.sessionRules.rules.some(filterAAR) ||
+      this.dynamicRules.rules.some(filterAAR) ||
+      this.enabledStaticRules.some(ruleset => ruleset.rules.some(filterAAR));
   }
 }

 function getRuleManager(extension, createIfMissing = true) {
   let ruleManager = gRuleManagers.find(rm => rm.extension === extension);""",
    },
    {
        "comment": {
            "filename": "devtools/shared/network-observer/NetworkUtils.sys.mjs",
            "start_line": 496,
            "content": "The condition in the `if` statement is a bit complex and could be simplified for better readability. Consider extracting `!Components.isSuccessCode(status) && blockList.includes(ChromeUtils.getXPCOMErrorName(status))` into a separate function with a descriptive name, such as `isBlockedError`.",
            "explanation": "THE JUSTIFICATION GOES HERE",
        },
        "raw_hunk": """@@ -481,26 +481,21 @@
     }
   } catch (err) {
     // "cancelledByExtension" doesn't have to be available.
   }

-  const ignoreList = [
-    // This is emitted when the request is already in the cache.
-    "NS_ERROR_PARSED_DATA_CACHED",
-    // This is emitted when there is some issues around imgages e.g When the img.src
-    // links to a non existent url. This is typically shown as a 404 request.
-    "NS_IMAGELIB_ERROR_FAILURE",
-    // This is emitted when there is a redirect. They are shown as 301 requests.
-    "NS_BINDING_REDIRECTED",
+  const blockList = [
+    // When a host is not found (NS_ERROR_UNKNOWN_HOST)
+    "NS_ERROR_UNKNOWN_HOST",
   ];

   // If the request has not failed or is not blocked by a web extension, check for
   // any errors not on the ignore list. e.g When a host is not found (NS_ERROR_UNKNOWN_HOST).
   if (
     blockedReason == 0 &&
     !Components.isSuccessCode(status) &&
-    !ignoreList.includes(ChromeUtils.getXPCOMErrorName(status))
+    blockList.includes(ChromeUtils.getXPCOMErrorName(status))
   ) {
     blockedReason = ChromeUtils.getXPCOMErrorName(status);
   }

   return { blockingExtension, blockedReason };""",
    },
]

TEMPLATE_PATCH_FROM_HUNK = """diff --git a/{filename} b/{filename}
--- a/{filename}
+++ b/{filename}
{raw_hunk}
"""

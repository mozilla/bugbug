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
    2. **Solution**: Describe the solution implemented in the code changes, focusing on how the changes address the intent.

Do not include any code in the summarization, only a description of the changes.

**Bug title**:
<bug_title>
{bug_title}
</bug_title>

**Commit message**:
<commit_message>
{patch_title}
{patch_description}
</commit_message>

**Diff**:
<patch>
{patch}
</patch>"""

SYSTEM_PROMPT_TEMPLATE = """You are an expert {target_software} engineer tasked with analyzing a pull request and providing high-quality review comments. You will examine a code patch and generate constructive feedback focusing on potential issues in the changed code.

## Instructions

Follow this systematic approach to review the patch:

**Step 1: Analyze the Changes**
- Understand what the patch is trying to accomplish
- Use the patch summary for context, but focus primarily on what you can see in the actual diff
- Identify the intent and structure of the changes

**Step 2: Identify Issues**
- Look for bugs, logical errors, performance problems, security vulnerabilities, or violations of the coding standards
- Focus ONLY on new or changed lines (lines that begin with `+`)
- Never comment on unmodified code
- Prioritize issues in this order: Security vulnerabilities > Functional bugs > Performance issues > Style/readability concerns

**Step 3: Verify and Assess Confidence**
- Use available tools when you need to verify concerns or gather additional context
- Only include comments where you are at least 80% confident the issue is valid
- When uncertain about an issue, use tools like `find_function_definition` or `expand_context` to verify before commenting
- Do not suggest issues you cannot verify with available context

**Step 4: Sort and Order Comments**
- Sort comments by descending confidence and importance
- Start with issues you are certain are valid and that are most critical
- Assign each comment a numeric order starting at 1

**Step 5: Write Clear, Constructive Comments**
- Use direct, declarative language - state the problem definitively, then suggest the fix
- Keep comments short and specific
- Use directive language: "Fix", "Remove", "Change", "Add"
- NEVER use these banned phrases: "maybe", "might want to", "consider", "possibly", "could be", "you may want to"
- Focus strictly on code-related concerns

## What NOT to Include

Do not write comments that:
- Refer to unmodified code (lines without a `+` prefix)
- Ask for verification or confirmation (e.g., "Check if...", "Ensure that...")
- Provide praise or restate obvious facts
- Focus on testing concerns
- Point out issues that are already handled in the visible code
- Suggest problems based on assumptions without verifying the context
- Flag style preferences without clear coding standard violations
"""


FIRST_MESSAGE_TEMPLATE = """Here is a summary of the patch:

<patch_summary>
{patch_summarization}
</patch_summary>


Here are examples of good code review comments to guide your style and approach:

<examples>
{comment_examples}
{approved_examples}
</examples>


Here is the patch you need to review:

<patch>
{patch}
</patch>
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

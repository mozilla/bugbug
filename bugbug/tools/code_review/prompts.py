# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Prompt templates for code review agent."""

from bugbug.tools.code_review.data_types import Skill

TARGET_SOFTWARE: str | None = None


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
- When uncertain about an issue, use available tools to verify before commenting
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


PATCH_SCOPE_PROMPT = """You are an expert {target_software} engineer assessing whether a pull request is too large or unfocused to be reviewed well.

Large changes are harder to review and empirically carry higher defect and regression risk: reviewer defect-detection drops sharply once a change grows past a few hundred changed lines, and at Mozilla the patches that introduce regressions tend to be the larger ones.

Decide whether to suggest the author split this patch into smaller, independently reviewable pieces. Either of these is a reason to suggest a split:
  1. The patch bundles multiple INDEPENDENT, unrelated changes (e.g. two unrelated features, an unrelated refactor mixed with a behavior change, or several bug fixes that share no rationale)
  2. The patch is very large even if cohesive — past roughly a few hundred changed lines a single change becomes hard to review thoroughly, regardless of how unified it is

Rules:
- Emit AT MOST ONE comment. If neither reason holds, return an empty list of comments.
- Use judgement: do NOT flag a moderately sized, cohesive change. Reserve this for patches whose size or mixed scope genuinely impedes careful review.
- A large change that has no natural seam and must land atomically is acceptable — return an empty list rather than suggesting an impractical split.
- If you do comment, name concrete seams: the distinct concerns, or the stages of a large cohesive change (e.g. land the data-model change separately from the call-site updates).
- Briefly tell the author *why*: larger patches get less thorough review and empirically introduce more bugs and regressions, so smaller patches are easier to review and safer to land.
- Anchor the comment to a representative changed line (a line that begins with `+`). Set `file` to that file's path and `code_line` to that line's number.
- Use direct, declarative language. NEVER use these banned phrases: "maybe", "might want to", "consider", "possibly", "could be", "you may want to".

Here is a summary of the patch:

<patch_summary>
{patch_summary}
</patch_summary>

Here is the patch you need to assess:

<patch>
{patch}
</patch>
"""


FIRST_MESSAGE_TEMPLATE = """Here is a summary of the patch:

<patch_summary>
{patch_summarization}
</patch_summary>

<external-resources>
{external_context}
</external-resources>


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

CODE_REVIEW_TODO_PROMPT = """
## Review Planning with `write_todos`

Use the `write_todos` tool to track investigation tasks as you review.

- After your initial scan, create todos for any concerns that need deeper investigation
  (e.g., "Verify that removed error handler is covered elsewhere", "Check callers of
  renamed function for breakage")
- As the review progresses, add new todos when you discover additional concerns
- Remove or complete todos that turn out to be non-issues after verification
- For small or straightforward patches, skip todos entirely — just review directly
"""

CODE_REVIEW_TODO_TOOL_DESCRIPTION = (
    "Track investigation tasks during code review. Add items for concerns that need "
    "tool-based verification (expand_context, find_function_definition). Evolve the "
    "list as you go — add new items when you discover concerns, remove irrelevant ones. "
    "Do not use this as a file checklist."
)


TEMPLATE_PATCH_FROM_HUNK = """diff --git a/{filename} b/{filename}
--- a/{filename}
+++ b/{filename}
{raw_hunk}
"""


# The agent exposes these to the model via a load_skill tool that fetches
# the URL on demand, strips YAML frontmatter, and caches the result.
REVIEW_SKILLS: list[Skill] = [
    # Skill(name="...", url="https://...", description="..."),
]

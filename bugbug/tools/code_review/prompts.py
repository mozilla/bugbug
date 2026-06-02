# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Prompt templates for code review agent."""

from pathlib import Path

from bugbug.tools.code_review.data_types import Skill

_PROMPTS_DIR = Path(__file__).parent / "prompts"

TARGET_SOFTWARE: str | None = None

_SYSTEM_PROMPT_RAW = (_PROMPTS_DIR / "system.md").read_text()

_CONTEXT_TOOLS_AGENT = """\
  - `expand_context` to read surrounding code in the patched files
  - `find_function_definition` to look up callers or definitions\
"""

_CONTEXT_TOOLS_LOCAL = """\
  - `Read` and `Grep` to inspect files in the local checkout
  - `Bash` with `searchfox-cli` to query Searchfox for definitions, callers, and cross-references\
"""

SYSTEM_PROMPT_TEMPLATE = _SYSTEM_PROMPT_RAW.format(
    target_software="{target_software}",
    context_tools=_CONTEXT_TOOLS_AGENT,
)

LOCAL_SYSTEM_PROMPT_TEMPLATE = _SYSTEM_PROMPT_RAW.format(
    target_software="{target_software}",
    context_tools=_CONTEXT_TOOLS_LOCAL,
)

FIRST_MESSAGE_TEMPLATE = (_PROMPTS_DIR / "first_message.md").read_text()


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

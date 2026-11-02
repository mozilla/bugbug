# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Prompt templates for suggestion filtering."""

PROMPT_TEMPLATE_FILTERING_ANALYSIS = """Filter review comments to keep those that:
- are consistent with the {target_code_consistency} source code;
- focus on reporting possible bugs, functional regressions, issues, or similar concerns;
- report readability or design concerns.

Exclude comments that:
- only describe the change;
- restate obvious facts like renamed variables or replaced code;
- include praising;
- ask if changes are intentional or ask to ensure things exist.

Comments:
<comments-to-filter>
{comments}
</comments-to-filter>

As examples of not expected comments, not related to the current patch, please, check some below:
<rejected-examples>
    - {rejected_examples}
</rejected-examples>
"""


DEFAULT_REJECTED_EXAMPLES = """Please note that these are minor improvements and the overall quality of the patch is good. The documentation is being expanded in a clear and structured way, which will likely be beneficial for future development.
    - Please note that these are just suggestions and the code might work perfectly fine as it is. It's always a good idea to test all changes thoroughly to ensure they work as expected.
    - Overall, the patch seems to be well implemented with no major concerns. The developers have made a conscious decision to align with Chrome's behavior, and the reasoning is well documented.
    - There are no complex code changes in this patch, so there's no potential for major readability regressions or bugs introduced by the changes.
    - The `focus(...)` method is called without checking if the element and its associated parameters exist or not. It would be better to check if the element exists before calling the `focus()` method to avoid potential errors.
    - It's not clear if the `SearchService.sys.mjs` file exists or not. If it doesn't exist, this could cause an error. Please ensure that the file path is correct.
    - This is a good addition to the code."""

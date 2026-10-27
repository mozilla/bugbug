"""Model text clean-up shared by every agent that posts what a model wrote."""

from __future__ import annotations

import html


def clean(text: str | None) -> str:
    """Strip, and undo HTML escaping the model applied to its own prose.

    Live runs on 2026-08-25 returned "Core :: DOM: Core &amp; HTML" as a
    component and "nsTArray_Impl&lt;nsCOMPtr&lt;nsIContent&gt;&gt;" inside a hot
    path. Bugzilla comments are Markdown, not HTML, so those entities post
    literally -- and the escaped component also fails to match the plain one, so
    a bug filed in the right place is told to move to a mangled version of its
    own component. Two fields hit it in one run, which is the argument for
    fixing it at the single point every model string already passes through
    rather than field by field.
    """
    return html.unescape(text or "").strip()

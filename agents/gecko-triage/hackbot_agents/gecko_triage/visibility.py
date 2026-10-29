"""Bugzilla's privacy mark on a comment or an attachment."""


def marked_public(item: dict) -> bool:
    """Whether ``item`` says it is public; an item that does not say so is private.

    Bugzilla's REST API marks a comment `false` or `true` and an attachment `0` or `1`.
    """
    mark = item.get("is_private")
    return mark is False or (type(mark) is int and mark == 0)

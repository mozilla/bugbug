"""Tell an agent about the files its run was started with.

The runtime downloads a run's attachments before ``main()`` (see
``HackbotContext.input_attachment_files``). The model only knows about them if
its prompt says where they are, so every agent that accepts attachments adds
this text to its prompt.
"""

from pathlib import Path


def input_attachments_prompt(files: list[Path]) -> str:
    """Tell the agent where the run's attachments are, or "" if none."""
    if not files:
        return ""
    listing = "\n".join(f"- {path}" for path in files)
    return (
        "The request came with these attached files. Use the Read tool to look "
        "at any that help, such as screenshots or logs:\n"
        f"{listing}\n\n"
    )

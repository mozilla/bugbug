# Triage rules

Drop `.md` files in this directory. Each file is one ruleset (e.g.
`frontend-triage.md`).

The agent does **not** load everything automatically — it Globs this
directory and Reads only the rulesets it judges relevant to the bug at
hand. Name your files descriptively and start each one with a short
paragraph explaining when it applies (e.g. "These rules apply to Firefox
frontend defects with a video or steps-to-reproduce but no crash.").

Rules are free-form prose. Be explicit about:

- **When** the rule applies (which products/components/keywords/states)
- **What** the comment should say. A comment is the only thing the agent can write
  to a bug, so a rule that directs a field change directs something impossible.
- **What confidence threshold** is needed before acting

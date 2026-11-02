# Triage rules

Drop `.md` files in this directory. Each file is one ruleset (e.g.
`general.md`, `crash-triage.md`).

The agent does **not** load everything automatically — it Globs this
directory and Reads only the rulesets it judges relevant to the bug at
hand. Name your files descriptively and start each one with a short
paragraph explaining when it applies (e.g. "These rules apply to bugs
with a `sec-*` keyword.").

Rules are free-form prose. Be explicit about:

- **When** the rule applies (which products/components/keywords/states)
- **What** field changes or comments the agent should make
- **What confidence threshold** is needed before acting

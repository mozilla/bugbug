import logging
import re
from itertools import batched
from typing import Iterator, Optional

import requests
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate
from libmozdata.bugzilla import Bugzilla

KEYWORDS_TO_REMOVE = [
    "Backed out",
    "a=testonly",
    "DONTBUILD",
    "add tests",
    "disable test",
    "back out",
    "backout",
    "add test",
    "added test",
    "ignore-this-changeset",
    "CLOSED TREE",
    "nightly",
]

PRODUCT_OR_COMPONENT_TO_IGNORE = [
    "Firefox Build System::Task Configuration",
    "Developer Infrastructure::",
]


def fetch_bug_components(bug_ids: list[int]) -> dict[int, str]:
    bug_id_to_component = {}

    def bug_handler(bug):
        bug_id_to_component[bug["id"]] = f"{bug['product']}::{bug['component']}"

    Bugzilla(
        bugids=bug_ids,
        include_fields=["id", "product", "component"],
        bughandler=bug_handler,
    ).wait()

    return bug_id_to_component


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ReleaseNotesCommitsSelector:
    def __init__(self, chunk_size: int, llm: LLMChain):
        self.chunk_size = chunk_size
        self.bug_id_to_component: dict[int, str] = {}
        self.llm = llm
        self.summarization_prompt = PromptTemplate(
            input_variables=["commit_list"],
            template="""You are an expert in writing Firefox release notes. Your task is to analyze a list of commits and identify important user-facing changes. Follow these steps:

1. Must Include Only Meaningful Changes:
   - Only keep commits that significantly impact users and are strictly user-facing, such as:
     - New features
     - UI changes
     - Major performance improvements
     - Security patches (if user-facing)
     - Web platform changes that affect how websites behave
   - DO NOT include:
     - Small bug fixes unless critical
     - Internal code refactoring
     - Test changes or documentation updates
     - Developer tooling or CI/CD pipeline changes
Again, only include changes that are STRICTLY USER-FACING.

2. Output Format:
   - Use simple, non-technical language suitable for release notes.
   - Use the following strict format for each relevant commit, in CSV FORMAT:
[Type of Change],Description of the change,Bug XXXX,Reason why the change is impactful for end users
   - Possible types of change: [Feature], [Fix], [Performance], [Security], [UI], [DevTools], [Web Platform], etc.

3. Be Aggressive in Filtering:
    - If you're unsure whether a commit impacts end users, EXCLUDE it.
    - Do not list developer-focused changes.

4. Select Only the Top 10 Commits:
    - If there are more than 10 relevant commits, choose the most impactful ones.

5. Output Requirements:
   - Output must be raw CSV text—no formatting, no extra text.
   - Do not wrap the output in triple backticks (` ``` `) or use markdown formatting.
   - Do not include the words "CSV" or any headers—just the data.

6. Input:
   Here is the list of commits you need to focus on:
   {commit_list}
""",
        )

        self.summarization_chain = LLMChain(
            llm=self.llm,
            prompt=self.summarization_prompt,
        )

        self.cleanup_prompt = PromptTemplate(
            input_variables=["combined_list"],
            template="""Review the following list of release notes and remove anything that is not worthy of official release notes. Keep only changes that are meaningful, impactful, and directly relevant to end users, such as:
- New features that users will notice and interact with.
- Significant fixes that resolve major user-facing issues.
- Performance improvements that make a clear difference in speed or responsiveness.
- Accessibility enhancements that improve usability for a broad set of users.
- Critical security updates that protect users from vulnerabilities.

Strict Filtering Criteria - REMOVE the following:
- Overly technical web platform changes (e.g., spec compliance tweaks, behind-the-scenes API adjustments).
- Developer-facing features that have no direct user impact.
- Minor UI refinements (e.g., button width adjustments, small animation tweaks).
- Bug fixes that don’t impact most users.
- Obscure web compatibility changes that apply only to edge-case websites.
- Duplicate entries or similar changes that were already listed.

Instructions:
- KEEP THE SAME FORMAT (do not change the structure of entries that remain).
- REMOVE UNWORTHY ENTRIES ENTIRELY (do not rewrite them—just delete).
- DO NOT ADD ANY TEXT BEFORE OR AFTER THE LIST.
- The output must be only the cleaned-up list, formatted exactly the same way.

Here is the list to filter:
{combined_list}
""",
        )

        self.cleanup_chain = LLMChain(
            llm=self.llm,
            prompt=self.cleanup_prompt,
        )

    def batch_commit_logs(self, commit_log: str) -> list[str]:
        return [
            "\n".join(batch)
            for batch in batched(commit_log.strip().split("\n"), self.chunk_size)
        ]

    def generate_commit_shortlist(self, commit_log_list: list[str]) -> list[str]:
        commit_log_list_combined = "\n".join(commit_log_list)
        chunks = self.batch_commit_logs(commit_log_list_combined)
        return [
            self.summarization_chain.run({"commit_list": chunk}).strip()
            for chunk in chunks
        ]

    def filter_irrelevant_commits(self, commit_log_list: list[dict]) -> Iterator[str]:
        ignore_revs_url = "https://hg.mozilla.org/mozilla-central/raw-file/tip/.hg-annotate-ignore-revs"
        response = requests.get(ignore_revs_url)
        response.raise_for_status()
        raw_commits_to_ignore = response.text.strip().splitlines()
        hashes_to_ignore = {
            line.split(" ", 1)[0]
            for line in raw_commits_to_ignore
            if re.search(r"Bug \d+", line, re.IGNORECASE)
        }

        for commit in commit_log_list:
            desc = commit["desc"]
            author = commit["author"]
            node = commit["node"]
            bug_id = commit["bug_id"]

            if (
                not any(
                    keyword.lower() in desc.lower() for keyword in KEYWORDS_TO_REMOVE
                )
                and bug_id
                and re.search(r"\br=[^\s,]+", desc)
                and author
                != "Mozilla Releng Treescript <release+treescript@mozilla.org>"
                and node not in hashes_to_ignore
            ):
                bug_component = self.bug_id_to_component.get(bug_id)
                if bug_component and any(
                    to_ignore in bug_component
                    for to_ignore in PRODUCT_OR_COMPONENT_TO_IGNORE
                ):
                    continue
                yield desc

    def get_commit_logs(
        self, target_release: int, channel: str
    ) -> Optional[list[dict]]:
        preceding_release = target_release - 1

        target_version = f"FIREFOX_{channel}_{target_release}_BASE".upper()
        preceding_version = f"FIREFOX_{channel}_{preceding_release}_BASE".upper()

        url = f"https://hg.mozilla.org/releases/mozilla-{channel.lower()}/json-pushes?fromchange={preceding_version}&tochange={target_version}&full=1"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        commit_log_list = []
        for push_data in data.values():
            for changeset in push_data["changesets"]:
                if "desc" in changeset and changeset["desc"].strip():
                    desc = changeset["desc"].strip()
                    author = changeset.get("author", "").strip()
                    node = changeset.get("node", "").strip()
                    match = re.search(r"Bug (\d+)", desc, re.IGNORECASE)
                    bug_id = int(match.group(1)) if match else None
                    commit_log_list.append(
                        {
                            "desc": desc,
                            "author": author,
                            "node": node,
                            "bug_id": bug_id,
                        }
                    )
        return commit_log_list if commit_log_list else None

    def remove_duplicate_bugs(self, csv_text: str) -> str:
        seen = set()
        unique_lines = []
        for line in csv_text.strip().splitlines():
            parts = line.split(",", 3)
            if len(parts) < 3:
                continue
            bug_id = parts[2].strip()
            if bug_id not in seen:
                seen.add(bug_id)
                unique_lines.append(line)
        return "\n".join(unique_lines)

    def get_final_release_notes_commits(
        self, target_release: int, channel: str
    ) -> Optional[list[str]]:
        logger.info(
            f"Generating commit shortlist for release {target_release} in channel {channel}"
        )
        commit_log_list = self.get_commit_logs(
            target_release=target_release, channel=channel
        )

        if not commit_log_list:
            return None

        bug_ids = [commit["bug_id"] for commit in commit_log_list if commit["bug_id"]]

        self.bug_id_to_component = fetch_bug_components(bug_ids)
        filtered_commits = list(self.filter_irrelevant_commits(commit_log_list))

        if not filtered_commits:
            return None

        commit_shortlist = self.generate_commit_shortlist(filtered_commits)

        if not commit_shortlist:
            return None

        combined_list = "\n".join(commit_shortlist)
        cleaned = self.cleanup_chain.run({"combined_list": combined_list}).strip()

        deduped = self.remove_duplicate_bugs(cleaned)
        return deduped.splitlines()

import logging
import os
import re
from typing import List, Optional

import requests
import tiktoken
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate
from openai import OpenAI

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ReleaseNotesCommitsSelector:
    def __init__(self, chunk_size: int, llm: LLMChain):
        self.chunk_size = chunk_size
        self.llm = llm
        self.summarization_prompt = PromptTemplate(
            input_variables=["input_text"],
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

5. Input:
   Here is the chunk of commit logs you need to focus on:
   {input_text}

6. Output Requirements:
   - Output must be raw CSV text—no formatting, no extra text.
   - Do not wrap the output in triple backticks (` ``` `) or use markdown formatting.
   - Do not include the words "CSV" or any headers—just the data.
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

Here is the list to filter:
{combined_list}

Instructions:
- KEEP THE SAME FORMAT (do not change the structure of entries that remain).
- REMOVE UNWORTHY ENTRIES ENTIRELY (do not rewrite them—just delete).
- DO NOT ADD ANY TEXT BEFORE OR AFTER THE LIST.
- The output must be only the cleaned-up list, formatted exactly the same way.
""",
        )

        self.cleanup_chain = LLMChain(
            llm=self.llm,
            prompt=self.cleanup_prompt,
        )

    def get_previous_version(self, current_version: str) -> str:
        match = re.match(r"(FIREFOX_BETA_)(\d+)(_BASE)", current_version)
        if not match:
            raise ValueError("Invalid version format")
        prefix, version_number, suffix = match.groups()
        previous_version_number = int(version_number) - 1
        return f"{prefix}{previous_version_number}{suffix}"

    def get_token_count(self, text: str) -> int:
        if hasattr(self.llm, "model_name"):
            model_name = self.llm.model_name
        else:
            raise ValueError("LLM model name not found.")

        encoding = tiktoken.encoding_for_model(model_name)
        return len(encoding.encode(text))

    def split_into_chunks(self, commit_log: str) -> List[str]:
        commit_blocks = commit_log.split("\n")
        chunks = []
        current_chunk: List[str] = []
        current_token_count = 0

        for block in commit_blocks:
            block_token_count = self.get_token_count(block)

            if current_token_count + block_token_count > self.chunk_size:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_token_count = 0

            current_chunk.append(block)
            current_token_count += block_token_count

        if current_chunk:
            chunks.append("\n\n".join(current_chunk))

        return chunks

    def shortlist_with_gpt(self, input_text: str) -> str:
        return self.summarization_chain.run({"input_text": input_text}).strip()

    def generate_commit_shortlist(self, commit_log_list: List[str]) -> List[str]:
        commit_log_list_combined = "\n".join(commit_log_list)
        chunks = self.split_into_chunks(commit_log_list_combined)
        return [self.shortlist_with_gpt(chunk) for chunk in chunks]

    def clean_commits(
        self, commit_log_list: List[str], keywords: List[str]
    ) -> List[str]:
        cleaned_commits = []

        for block in commit_log_list:
            if (
                not any(
                    re.search(rf"\b{keyword}\b", block, re.IGNORECASE)
                    for keyword in keywords
                )
                and re.search(r"Bug \d+", block, re.IGNORECASE)
                and not re.search(
                    r"release\+treescript@mozilla\.org", block, re.IGNORECASE
                )
                and not re.search(r"nightly", block, re.IGNORECASE)
            ):
                bug_id_match = re.search(r"Bug (\d+)", block, re.IGNORECASE)
                if not bug_id_match:
                    continue

                bug_position = re.search(r"Bug \d+.*", block, re.IGNORECASE)
                if bug_position:
                    block = bug_position.group(0)

                commit_summary = block
                cleaned_commits.append(commit_summary)

        return cleaned_commits

    def remove_unworthy_commits(self, summaries: List[str]) -> str:
        combined_list = "\n".join(summaries)
        return self.cleanup_chain.run({"combined_list": combined_list}).strip()

    def select_worthy_commits(self, version: str) -> Optional[str]:
        self.version2 = version
        self.version1 = self.get_previous_version(version)
        self.output_file = f"version_summary_{self.version2}.txt"

        logger.info(f"Generating list of commits for version: {self.version2}")
        url = f"https://hg.mozilla.org/releases/mozilla-release/json-pushes?fromchange={self.version1}&tochange={self.version2}&full=1"
        response = requests.get(url)
        response.raise_for_status()

        data = response.json()
        commit_log_list = []

        for push_data in data.values():
            changesets = push_data["changesets"]

            for changeset in changesets:
                desc = changeset["desc"].strip()
                if desc:
                    commit_log_list.append(desc)

        if not commit_log_list:
            return None

        logger.info("Cleaning commit log...")
        keywords_to_remove = [
            "Backed out",
            "a=testonly",
            "DONTBUILD",
            "add tests",
            "disable test",
        ]
        cleaned_commits = self.clean_commits(commit_log_list, keywords_to_remove)

        logger.info("Generating summaries for cleaned commits...")
        summaries_list = self.generate_commit_shortlist(cleaned_commits)

        logger.info("Removing unworthy commits from the list...")
        return self.remove_unworthy_commits(summaries_list)

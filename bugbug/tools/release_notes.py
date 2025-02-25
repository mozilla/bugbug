import logging
import os
import re
import subprocess

import tiktoken
from openai import OpenAI

from bugbug import db
from bugbug.bugzilla import BUGS_DB

MODEL = "gpt-4o"

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ReleaseNotesGenerator:
    def __init__(self, repo_directory, version, chunk_size=10000):
        self.repo_directory = repo_directory
        self.chunk_size = chunk_size

    def get_previous_version(self, current_version):
        match = re.match(r"(FIREFOX_BETA_)(\d+)(_BASE)", current_version)
        if not match:
            raise ValueError("Invalid version format")
        prefix, version_number, suffix = match.groups()
        previous_version_number = int(version_number) - 1
        return f"{prefix}{previous_version_number}{suffix}"

    def run_hg_log(self, query):
        try:
            result = subprocess.run(
                ["hg", "log", "-r", query],
                cwd=self.repo_directory,
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logger.error(f"Error running hg log: {e}")
            return None

    def get_token_count(self, text):
        encoding = tiktoken.encoding_for_model(MODEL)
        return len(encoding.encode(text))

    def split_into_chunks(self, commit_log):
        commit_blocks = commit_log.split("\n\n")
        chunks = []
        current_chunk = []
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

    def summarize_with_gpt(self, input_text):
        prompt = f"""
You are an expert in writing Firefox release notes. Your task is to analyze a list of commits and identify important user-facing changes. Follow these steps:

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

3. Bad Example (DO NOT FOLLOW):
[Feature],Enable async FlushRendering during resizing window if Windows DirectComposition is used,Bug 1922721,Improves performance and responsiveness when resizing windows on systems using Windows DirectComposition.
We should exclude this change because it contains technical jargon that is unclear to general users, making it difficult to understand. Additionally, the impact is limited to a specific subset of Windows users with DirectComposition enabled, and the improvement is not significant enough to be noteworthy in the release notes.

4. Be Aggressive in Filtering:
    - If you're unsure whether a commit impacts end users, EXCLUDE it.
    - Do not list developer-focused changes.

5. Select Only the Top 10 Commits:
    - If there are more than 10 relevant commits, choose the most impactful ones.

6. Input:
   Here is the chunk of commit logs you need to focus on:
   {input_text}

7. Output Requirements:
   - Output must be raw CSV text—no formatting, no extra text.
   - Do not wrap the output in triple backticks (` ``` `) or use markdown formatting.
   - Do not include the words "CSV" or any headers—just the data.
"""
        try:
            response = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=MODEL,
                temperature=0.1,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Error calling OpenAI API: {e}")
            return "Error: Unable to generate summary."

    def generate_summaries(self, commit_log):
        chunks = self.split_into_chunks(commit_log)
        return [self.summarize_with_gpt(chunk) for chunk in chunks]

    def clean_commits(self, commit_log, keywords, bug_dict):
        cleaned_commits = []
        commit_blocks = commit_log.split("\n\n")

        for block in commit_blocks:
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

                bug_id = int(bug_id_match.group(1))
                if bug_id not in bug_dict:
                    continue

                bug_info = bug_dict[bug_id]
                should_exclude = False

                if "blocks" in bug_info:
                    for blocked_bug_id in bug_info["blocks"]:
                        if blocked_bug_id in bug_dict:
                            blocked_bug = bug_dict[blocked_bug_id]
                            if "[meta]" in blocked_bug.get("summary", ""):
                                if (
                                    not blocked_bug.get("version")
                                    or blocked_bug["version"].lower() == "unspecified"
                                ):
                                    should_exclude = True
                                    break

                if should_exclude:
                    continue

                match = re.search(r"summary:\s+(.+)", block)
                commit_summary = match.group(1) if match else None
                cleaned_commits.append(commit_summary)

        return "\n\n".join(cleaned_commits)

    def load_bug_data(self):
        bug_data = list(db.read(BUGS_DB))
        return {bug["id"]: bug for bug in bug_data}

    def remove_unworthy_commits(self, input_text):
        prompt = f"""Review the following list of release notes and remove anything that is not worthy of official release notes. Keep only changes that are meaningful, impactful, and directly relevant to end users, such as:
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
{input_text}

Instructions:
- KEEP THE SAME FORMAT (do not change the structure of entries that remain).
- REMOVE UNWORTHY ENTRIES ENTIRELY (do not rewrite them—just delete).
- DO NOT ADD ANY TEXT BEFORE OR AFTER THE LIST.
- The output must be only the cleaned-up list, formatted exactly the same way.
"""
        try:
            response = client.chat.completions.create(
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                model=MODEL,
                temperature=0.1,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Error while calling OpenAI API: {e}")
            return "Error: Unable to remove unworthy commits."

    def generate_worthy_commits(self, version):
        bug_dict = self.load_bug_data()
        self.version2 = version
        self.version1 = self.get_previous_version(version)
        self.output_file = f"version_summary_{self.version2}.txt"

        logger.info(f"Generating list of commits for version: {self.version2}")

        logger.info("Finding the branching point commit...")
        branching_commit_query = f"ancestor({self.version1}, {self.version2})"
        branching_commit_output = self.run_hg_log(branching_commit_query)

        if not branching_commit_output:
            logger.error("Failed to find the branching point commit. Exiting.")
            return

        branching_commit_hash = branching_commit_output.split(":")[1].split()[0]
        logger.info(f"Branching point commit: {branching_commit_hash}")

        logger.info("Fetching the list of changes...")
        changes_query = (
            f"descendants({branching_commit_hash}) and ancestors({self.version2})"
        )
        changes_output = self.run_hg_log(changes_query)

        if not changes_output:
            logger.error("Failed to fetch the list of changes. Exiting.")
            return

        logger.info("Cleaning commit log...")
        keywords_to_remove = [
            "Backed out",
            "a=testonly",
            "DONTBUILD",
            "add tests",
            "disable test",
        ]
        cleaned_commits = self.clean_commits(
            changes_output, keywords_to_remove, bug_dict
        )

        logger.info("Generating summaries for cleaned commits...")
        summaries = self.generate_summaries(cleaned_commits)
        combined_list = "\n".join(summaries)

        logger.info("Removing unworthy commits from the list...")
        combined_list = self.remove_unworthy_commits(combined_list)

        with open(self.output_file, "w") as file:
            file.write(combined_list)

        logger.info(f"Worthy commits saved to {self.output_file}")

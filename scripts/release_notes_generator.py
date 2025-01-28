import logging
import os
import re
import subprocess

import tiktoken
from openai import OpenAI

MODEL = "gpt-4o"

client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY"),
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FIREFOX_VERSION_1 = "FIREFOX_BETA_132_BASE"
FIREFOX_VERSION_2 = "FIREFOX_BETA_133_BASE"
REPO_DIRECTORY = "hg_dir"
OUTPUT_FILE = f"version_summary_{FIREFOX_VERSION_2}.txt"
CHUNK_SIZE = 4000


def run_hg_log(query, repo_dir):
    try:
        result = subprocess.run(
            ["hg", "log", "-r", query],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error running hg log: {e}")
        return None


def get_token_count(text, model=MODEL):
    encoding = tiktoken.encoding_for_model(model)
    return len(encoding.encode(text))


def split_into_chunks(commit_log, chunk_size, model="gpt-4"):
    commit_blocks = commit_log.split("\n\n")
    chunks = []
    current_chunk = []
    current_token_count = 0

    for block in commit_blocks:
        block_token_count = get_token_count(block, model=model)

        if current_token_count + block_token_count > chunk_size:
            # Add the current chunk to the chunks list and start a new chunk
            chunks.append("\n\n".join(current_chunk))
            current_chunk = []
            current_token_count = 0

        current_chunk.append(block)
        current_token_count += block_token_count

    # Add the last chunk if any content remains
    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks


def summarize_with_gpt(input_text):
    prompt = f"""
You are an expert in analyzing commit logs. Your task is to analyze a chunk of commit logs and produce a summary in a clear and user-friendly format. Follow these steps:

1. **Analyze Commit Logs**:
   - Identify commits or groups of commits relevant for potential release notes. Focus on changes that:
     - Are meaningful to **end users**, such as new features, user-facing improvements, or critical updates.
   - Exclude:
     - Internal refactorings, test-related updates, or minor low-level changes that are not relevant to end users.
     - Highly technical details or jargon that might confuse non-developers.

2. **Enhance Context**:
   - If a commit lacks sufficient information (e.g., vague descriptions or unexplained references to functions), break the process into two steps:
     - Step 1: Explain why the commit's description is insufficient for end users (e.g., the function's purpose is unclear or its relevance is ambiguous).
     - Step 2: Perform a reasoning step where you hypothesize or research the broader context, including the potential impact on security, performance, or user experience.
   - Use your analysis to enhance clarity and add relevant context to the description. This ensures that whatever you are adding to the list is actually worthy of being in the release notes, rather than you adding it with no understanding of it.

3. **Output Format**:
   - Use simple, non-technical language suitable for release notes.
   - Use the following strict format for each relevant commit:
     - [Type of Change] Description of the change (bug XXXXX)
   - Possible types of change: [Feature], [Fix], [Performance], [Security], [UI], [DevTools], [Web Platform], etc.

4. **Example Commit Logs**:
   - Input: `- [Security] Enforce validateRequestHeaders in HTTP parser (bug 1931456)`
     - **Step 1**: Identify insufficient details. "validateRequestHeaders" is unclear without understanding its role in the HTTP parser.
     - **Step 2**: Contextual reasoning. This function likely enforces stricter checks on HTTP headers, mitigating potential attack vectors.
     - Output: `[Security] Enhanced HTTP request validation by enforcing stricter header checks, reducing the risk of malformed or malicious requests (bug 1931456).`

5. **Output Strictness**:
   - The output must only be the final list, following the specified format.
   - Ensure every description is clear, complete, and directly relevant to end users.

6. **Input**:
   Here is the chunk of commit logs you need to focus on:
   {input_text}

7. **Output**:
   The output should just be the list. Nothing more and nothing less.
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
        return "Error: Unable to generate summary."


def remove_duplicates(input_text):
    prompt = f"""Given the following list, remove any duplicate entries. That is, if two or more entries talk abou the same change (does not have to be identical wording), remove the less descriptive one. Do not alter anything else.

    Here is the list:
    {input_text}

    The output should just be the list with the duplicates removed. Nothing more, nothing less.
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
        return "Error: Unable to remove duplicates."


def generate_summaries(commit_log):
    chunks = split_into_chunks(commit_log, CHUNK_SIZE)
    summaries = [summarize_with_gpt(chunk) for chunk in chunks]
    return summaries


def clean_commits(commit_log, keywords):
    cleaned_commits = []
    commit_blocks = commit_log.split("\n\n")

    for block in commit_blocks:
        if (
            not any(
                re.search(rf"\b{keyword}\b", block, re.IGNORECASE)
                for keyword in keywords
            )
            and re.search(r"Bug \d+", block, re.IGNORECASE)
            and not re.search(r"release\+treescript@mozilla\.org", block, re.IGNORECASE)
            and not re.search(r"nightly", block, re.IGNORECASE)
        ):
            cleaned_commits.append(block)

    return "\n\n".join(cleaned_commits)


def generate_worthy_commits():
    logger.info(f"Generating list of commits for version: {FIREFOX_VERSION_2}")

    logger.info("Finding the branching point commit...")
    branching_commit_query = f"ancestor({FIREFOX_VERSION_1}, {FIREFOX_VERSION_2})"
    branching_commit_output = run_hg_log(branching_commit_query, REPO_DIRECTORY)

    if not branching_commit_output:
        logger.error("Failed to find the branching point commit. Exiting.")
        exit(1)

    branching_commit_hash = branching_commit_output.split(":")[1].split()[0]
    logger.info(f"Branching point commit: {branching_commit_hash}")

    logger.info("Fetching the list of changes...")
    changes_query = (
        f"descendants({branching_commit_hash}) and ancestors({FIREFOX_VERSION_2})"
    )
    changes_output = run_hg_log(changes_query, REPO_DIRECTORY)

    if not changes_output:
        logger.error("Failed to fetch the list of changes. Exiting.")
        exit(1)

    logger.info("Cleaning commit log...")
    keywords_to_remove = [
        "Backed out",
        "a=testonly",
        "a=release",
        "DONTBUILD",
        "add tests",
        "disable test",
    ]
    cleaned_commits = clean_commits(changes_output, keywords_to_remove)
    cleaned_commits = cleaned_commits[0:40000]

    logger.info("Generating summaries for cleaned commits...")
    summaries = generate_summaries(cleaned_commits)

    combined_list = "\n".join(summaries)

    logger.info("Removing duplicates from the list...")
    combined_list = remove_duplicates(combined_list)

    with open(OUTPUT_FILE, "w") as file:
        file.write(combined_list)

    logger.info(f"Worthy commits saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    generate_worthy_commits()

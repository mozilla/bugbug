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
OUTPUT_FILE = f"worthy_commits_{FIREFOX_VERSION_2}.txt"
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
You are an expert in analyzing commit logs. I will provide you with a chunk of commit logs. Your task is to:
1. Identify commits or groups of commits that are relevant for potential release notes. Focus on changes that:
   - Are meaningful to **end users**, such as new features or importan changes
2. Exclude:
   - Internal refactorings, test-related updates, or minor low-level changes that are not relevant to end users.
   - Changes that were made to the codebase that may be relevant to Mozilla engineers, but not to end users. These can include references to random functions, files, etc.
   - Highly technical details or jargon in the descriptions that might confuse non-developers.
3. Use simple and user-friendly language for descriptions, particularly for end-user-facing changes.

Here is an example of release notes that were generated by another script. Do not follow the format, but use it to understand what kind of changes we want to include:

Firefox Changelog: 133 to 134
Accessibility Improvements

    Enhanced accessibility for scrolling events and text fragment navigation (bug 1926214).
    Improved handling of anchor jumps to ensure accessibility events are fired correctly (bug 1926198).
    Updated DevTools to better support High Contrast Mode, improving visibility of UI elements like charts and declarations (bug 1916391, bug 1926794, bug 1926851, bug 1926852).

Android-Specific Changes

    Added support for autocorrect in Android's GeckoView backend (bug 1725806).
    Removed the Extensions chevron icon from the Homepage Menu as part of the Android Menu Redesign (bug 1925005).
    Improved handling of translation prompts, ensuring "Not now" cancels translation (bug 1913602).
    Enabled biometric authentication for accessing saved logins on Android (bug 1932575).

DevTools Enhancements

    Updated DevTools to better handle High Contrast Mode, improving visibility of UI elements like charts and declarations (bug 1916391, bug 1926794, bug 1926851, bug 1926852).
    Refactored the Start Performance Analysis button style for better usability (bug 1926878).
    Made the "WhyPaused" debugger panel a live region and added paused location information for better accessibility (bug 1843320).
    Improved High Contrast Mode support for markup nodes and console borders (bug 1916688, bug 1931502).

Performance Optimizations

    Optimized layout calculations for fixed-position frames in display ports to improve rendering performance (bug 1927375).
    Improved handling of JavaScript IC (Inline Cache) operations to enhance performance (bug 1922981).
    Enhanced memory handling for WebAssembly, increasing memory limits and enabling memory64 by default (bug 1931401, bug 1929590).
    Optimized garbage collection by avoiding full GC during ongoing CC (bug 1932394).

Security and Privacy

    Removed the security.external_protocol_requires_permission pref, simplifying external protocol handling (bug 1925479).
    Updated CRLite filter channel to use experimental+deltas on Nightly for improved certificate revocation checks (bug 1927598).
    Improved clipboard content analysis to handle multiple clipboard items securely (bug 1915351).
    Enabled biometric authentication for accessing saved logins on Android (bug 1932575).

Web Platform and Standards

    Implemented PushManager.supportedContentEncodings for better web push support (bug 1497430).
    Added support for ReadableStreamBYOBReader.prototype.read(view, { min }) to align with web standards (bug 1864406).
    Improved handling of text fragments and scrolling behavior for better web compatibility (bug 1907808).
    Shipped js-string-builtins and improved WebAssembly memory handling (bug 1913964, bug 1932087).

Localization and Internationalization

    Migrated necko error messages from properties to Fluent for better localization support (bug 1733498).
    Updated various localization strings and configurations across Firefox and Mobile (multiple l10n bumps).
    Improved handling of city/state in MLSuggest subjects (bug 1932671).

UI and User Experience

    Updated the URL bar's search mode behavior and layout (bug 1921731, bug 1925532).
    Improved tab dragging behavior by moving tabs when hitting 70% of their width (bug 1932425).
    Enabled save and close functionality for tab groups (bug 1923652).
    Added a restore tab group API to session management (bug 1932670).
    Improved URL bar geolocation utilities and Yelp suggestion matching (bug 1932537, bug 1931964).

Media and WebRTC

    Enabled simulcast for screensharing sources and added tests to ensure compatibility (bug 1692873).
    Added AV1 codec support for WebRTC, including negotiation, parameter handling, and tests (bug 1921154).
    Improved H264 handling in WebRTC tests and ensured consistent use of fake GMP plugins (bug 1534688).

Miscellaneous

    Updated Sentry to version 7.16.0 for better error reporting (bug 1927169).
    Improved filename sanitization for downloads to enhance security and usability (bug 1914858).
    Enabled ScotchBonnet on Nightly for improved UI testing (bug 1916679).
    Updated Rust dependencies (zerovec-derive, shlex) (bug 1932319, bug 1932316).

This changelog focuses on user-visible changes, performance improvements, and security enhancements, providing a high-level overview of the most impactful updates in this Firefox release.

Here is the chunk of commit logs you need to focus on:
{input_text}

Here is the STRICT format I want you to follow. No extra text please:
- [Type of Change] Description of the change (bug XXXXX)

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
            temperature=0.2,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Error while calling OpenAI API: {e}")
        return "Error: Unable to generate summary."


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

    combined_list = "\n\n".join(summaries)

    with open(OUTPUT_FILE, "w") as file:
        file.write(combined_list)

    logger.info(f"Worthy commits saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    generate_worthy_commits()

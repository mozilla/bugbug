import logging
import os
import re
import subprocess

import tiktoken
from openai import OpenAI

client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY"),
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FIREFOX_VERSION_1 = "FIREFOX_BETA_133_BASE"
FIREFOX_VERSION_2 = "FIREFOX_BETA_134_BASE"
REPO_DIRECTORY = "hg_dir"
OUTPUT_FILE = f"release_notes_{FIREFOX_VERSION_2}.txt"
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


def get_token_count(text, model="gpt-4o-mini"):
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


def summarize_with_gpt(input_text, step="summary"):
    if step == "summary":
        prompt = f"""You are an expert in summarizing commit logs for release notes, skilled at identifying important updates, features, and fixes while ensuring traceability.
I will provide you with a chunk of commit logs. Your task is to:
1. **Review the commit logs carefully.**
2. **Identify and summarize only those commits that are significant for release notes, such as user-facing changes, critical bug fixes, performance improvements, and new features.**
3. **Include the associated bug numbers in parentheses at the end of each summary item, if available.**
4. Use a concise bulleted list format. For each item, begin with a category tag like [Feature], [Fix], [Improvement], [Change], followed by a brief description and the bug number (e.g., bug 123456).

Here is the chunk of commit logs:
{input_text}

Output the summary in this format:
- [Category] Description of the change (bug XXXXX).
- [Category] Another important update (bug XXXXX, bug XXXXX).
"""
    elif step == "final":
        prompt = f"""You are an expert in creating professional, user-friendly release notes.
I will provide you with a combined summary of updates and fixes derived from commit logs. Your task is to:
1. **Review the provided summary carefully and polish it into a cohesive document.**
2. **Group the updates into categories such as Accessibility Improvements, Performance Optimizations, Security and Privacy, etc.**
3. **Ensure that each item in the release notes includes its corresponding bug number(s) in parentheses at the end, as provided in the summaries.**
4. Use simple, professional language suitable for both technical and non-technical audiences, and avoid overly technical jargon.
5. Begin with a short introductory paragraph summarizing the release, including the version number and key highlights.
6. MAKE SURE THESE ARE USER-FACING RELEASE NOTES. Avoid any references to code, functions etc. They should be about Firefox features.

Here is an example of release notes:

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


Here is the summarized list of updates:
{input_text}

Output the release notes in this format:
Release Notes for Version {FIREFOX_VERSION_2}

### Accessibility Improvements
- Enhanced accessibility for scrolling events and text fragment navigation (bug XXXXX).
- Improved handling of anchor jumps to ensure accessibility events are fired correctly (bug XXXXX).

### Performance Optimizations
- Optimized layout calculations for fixed-position frames (bug XXXXX).
- Enhanced memory handling for WebAssembly, increasing memory limits (bug XXXXX, bug XXXXX).

### Security and Privacy
- Removed outdated preferences for external protocol handling (bug XXXXX).
- Updated certificate revocation checks for improved security (bug XXXXX).
"""

    try:
        response = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            model="gpt-4o-mini",
            temperature=0.1,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Error while calling OpenAI API: {e}")
        return "Error: Unable to generate summary or release notes."


def generate_summaries(commit_log):
    chunks = split_into_chunks(commit_log, CHUNK_SIZE)
    summaries = [summarize_with_gpt(chunk, step="summary") for chunk in chunks]
    return summaries


def clean_commits(commit_log, keywords):
    cleaned_commits = []
    commit_blocks = commit_log.split("\n\n")

    for block in commit_blocks:
        if not any(
            re.search(rf"\b{keyword}\b", block, re.IGNORECASE) for keyword in keywords
        ):
            cleaned_commits.append(block)

    return "\n\n".join(cleaned_commits)


def generate_release_notes():
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

    # TEMP FOR NOW
    cleaned_commits = cleaned_commits[0:20000]

    logger.info("Generating summaries for cleaned commits...")
    summaries = generate_summaries(cleaned_commits)

    combined_summary = "\n\n".join(summaries)

    logger.info("Polishing combined summary with GPT...")
    final_notes = summarize_with_gpt(combined_summary, step="final")

    with open(OUTPUT_FILE, "w") as file:
        file.write(final_notes)

    logger.info(f"Release notes saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    generate_release_notes()

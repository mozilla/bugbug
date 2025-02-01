# import logging
import os

# import re
# import subprocess
# import tiktoken
from openai import OpenAI

MODEL = "gpt-4o"

client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY"),
)

# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger(__name__)

# FIREFOX_VERSION_1 = "FIREFOX_BETA_132_BASE"
# FIREFOX_VERSION_2 = "FIREFOX_BETA_133_BASE"
# REPO_DIRECTORY = "hg_dir"
# OUTPUT_FILE = f"version_summary_{FIREFOX_VERSION_2}.txt"
# CHUNK_SIZE = 5000


# def run_hg_log(query, repo_dir):
#     try:
#         result = subprocess.run(
#             ["hg", "log", "-r", query],
#             cwd=repo_dir,
#             capture_output=True,
#             text=True,
#             check=True,
#         )
#         return result.stdout.strip()
#     except subprocess.CalledProcessError as e:
#         print(f"Error running hg log: {e}")
#         return None


# def get_token_count(text, model=MODEL):
#     encoding = tiktoken.encoding_for_model(model)
#     return len(encoding.encode(text))


# def split_into_chunks(commit_log, chunk_size, model="gpt-4"):
#     commit_blocks = commit_log.split("\n\n")
#     chunks = []
#     current_chunk = []
#     current_token_count = 0

#     for block in commit_blocks:
#         block_token_count = get_token_count(block, model=model)

#         if current_token_count + block_token_count > chunk_size:
#             chunks.append("\n\n".join(current_chunk))
#             current_chunk = []
#             current_token_count = 0

#         current_chunk.append(block)
#         current_token_count += block_token_count

#     if current_chunk:
#         chunks.append("\n\n".join(current_chunk))

#     return chunks


# def summarize_with_gpt(input_text):
#     prompt = f"""
# You are an expert in analyzing commit logs. Your task is to analyze a chunk of commit logs and produce a summary in a clear and user-friendly format. Follow these steps:

# 1. **Analyze Commit Logs**:
#    - Identify commits or groups of commits relevant for potential release notes. Focus on changes that:
#      - Are meaningful to **end users**, such as new features, user-facing improvements, or critical updates.
#    - Exclude:
#      - Internal refactorings, test-related updates, or minor low-level changes that are not relevant to end users.
#      - Highly technical details or jargon that might confuse non-developers.

# 2. **Enhance Context**:
#    - If a commit lacks sufficient information (e.g., vague descriptions or unexplained references to functions), break the process into two steps:
#      - Step 1: Explain why the commit's description is insufficient for end users (e.g., the function's purpose is unclear or its relevance is ambiguous).
#      - Step 2: Perform a reasoning step where you hypothesize or research the broader context, including the potential impact on security, performance, or user experience.
#    - Use your analysis to enhance clarity and add relevant context to the description. This ensures that whatever you are adding to the list is actually worthy of being in the release notes, rather than you adding it with no understanding of it.

# 3. **Output Format**:
#    - Use simple, non-technical language suitable for release notes.
#    - Use the following strict format for each relevant commit, in CSV FORMAT:
# [Type of Change],Description of the change,Bug XXXX,Reasoning behind the change (if necessary)
#    - Possible types of change: [Feature], [Fix], [Performance], [Security], [UI], [DevTools], [Web Platform], etc.

# 4. **Output Strictness**:
#    - The output must only be the final list, following the specified format.
#    - Ensure every description is clear, complete, and directly relevant to end users.

# 6. **Input**:
#    Here is the chunk of commit logs you need to focus on:
#    {input_text}

# 7. **Output**:
#    The output should just be the list. Nothing more and nothing less.
# """

#     try:
#         response = client.chat.completions.create(
#             messages=[
#                 {
#                     "role": "user",
#                     "content": prompt,
#                 }
#             ],
#             model=MODEL,
#             temperature=0.1,
#         )
#         return response.choices[0].message.content.strip()
#     except Exception as e:
#         logger.error(f"Error while calling OpenAI API: {e}")
#         return "Error: Unable to generate summary."


# def remove_duplicates(input_text):
#     prompt = f"""Given the following list, remove any duplicate entries. That is, if two or more entries talk abou the same change (does not have to be identical wording), remove the less descriptive one. Do not alter anything else.

#     Here is the list:
#     {input_text}

#     The output should just be the list with the duplicates removed. Nothing more, nothing less. Do not add any text before or after the list.
#     """

#     try:
#         response = client.chat.completions.create(
#             messages=[
#                 {
#                     "role": "user",
#                     "content": prompt,
#                 }
#             ],
#             model=MODEL,
#             temperature=0.1,
#         )
#         return response.choices[0].message.content.strip()
#     except Exception as e:
#         logger.error(f"Error while calling OpenAI API: {e}")
#         return "Error: Unable to remove duplicates."


# def remove_unworthy_commits(input_text):
#     prompt = f"""Review the following list of release notes and remove anything list entry that is not worthy or necessary for inclusion in official release notes. Focus on keeping only changes that are meaningful, impactful, and directly relevant to end users, such as new features, significant fixes, performance improvements, accessibility enhancements, or critical security updates. Remove anything minor, overly technical, or irrelevant.

# Here is the list:
# {input_text}

# Return the cleaned-up list in the same format. Only remove the list entries you do not deem worthy of being included in the release notes. KEEP THE SAME FORMAT, DO NOT ALTER THE ENTRIES THEMSELVES. Do not add any text before or after the list."""

#     try:
#         response = client.chat.completions.create(
#             messages=[
#                 {
#                     "role": "user",
#                     "content": prompt,
#                 }
#             ],
#             model=MODEL,
#             temperature=0.1,
#         )
#         return response.choices[0].message.content.strip()
#     except Exception as e:
#         logger.error(f"Error while calling OpenAI API: {e}")
#         return "Error: Unable to remove unworthy commits."


# def generate_summaries(commit_log):
#     chunks = split_into_chunks(commit_log, CHUNK_SIZE)
#     print(f"LENGTH OF CHUNKS: {len(chunks)}")
#     print(f"LENGTH OF FIRST CHUNK: {len(chunks[0])}")
#     summaries = [summarize_with_gpt(chunk) for chunk in chunks]
#     return summaries


# def clean_commits(commit_log, keywords):
#     cleaned_commits = []
#     commit_blocks = commit_log.split("\n\n")

#     for block in commit_blocks:
#         if (
#             not any(
#                 re.search(rf"\b{keyword}\b", block, re.IGNORECASE)
#                 for keyword in keywords
#             )
#             and re.search(r"Bug \d+", block, re.IGNORECASE)
#             and not re.search(r"release\+treescript@mozilla\.org", block, re.IGNORECASE)
#             and not re.search(r"nightly", block, re.IGNORECASE)
#         ):
#             match = re.search(r"summary:\s+(.+)", block)
#             commit_summary = match.group(1) if match else None
#             cleaned_commits.append(commit_summary)

#     return "\n\n".join(cleaned_commits)


# def generate_worthy_commits():
#     logger.info(f"Generating list of commits for version: {FIREFOX_VERSION_2}")

#     logger.info("Finding the branching point commit...")
#     branching_commit_query = f"ancestor({FIREFOX_VERSION_1}, {FIREFOX_VERSION_2})"
#     branching_commit_output = run_hg_log(branching_commit_query, REPO_DIRECTORY)

#     if not branching_commit_output:
#         logger.error("Failed to find the branching point commit. Exiting.")
#         exit(1)

#     branching_commit_hash = branching_commit_output.split(":")[1].split()[0]
#     logger.info(f"Branching point commit: {branching_commit_hash}")

#     logger.info("Fetching the list of changes...")
#     changes_query = (
#         f"descendants({branching_commit_hash}) and ancestors({FIREFOX_VERSION_2})"
#     )
#     changes_output = run_hg_log(changes_query, REPO_DIRECTORY)

#     if not changes_output:
#         logger.error("Failed to fetch the list of changes. Exiting.")
#         exit(1)

#     logger.info("Cleaning commit log...")
#     keywords_to_remove = [
#         "Backed out",
#         "a=testonly",
#         "a=release",
#         "DONTBUILD",
#         "add tests",
#         "disable test",
#     ]
#     cleaned_commits = clean_commits(changes_output, keywords_to_remove)
#     # cleaned_commits = cleaned_commits[0:40000]

#     logger.info("Generating summaries for cleaned commits...")
#     summaries = generate_summaries(cleaned_commits)

#     combined_list = "\n".join(summaries)

#     # logger.info("Removing duplicates from the list...")
#     # combined_list = remove_duplicates(combined_list)

#     # logger.info("Removing unworthy commits from the list...")
#     # combined_list = remove_unworthy_commits(combined_list)

#     with open(OUTPUT_FILE, "w") as file:
#         file.write(combined_list)

#     logger.info(f"Worthy commits saved to {OUTPUT_FILE}")
#     # with open(OUTPUT_FILE, "r", encoding="utf-8") as file:
#     #     file_contents = file.read()

#     # cleaned_commits = remove_duplicates(file_contents)
#     # cleaned_commits = remove_unworthy_commits(cleaned_commits)

#     # with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
#     #     file.write(cleaned_commits)


# if __name__ == "__main__":
#     generate_worthy_commits()


# import openai

# MODEL = "gpt-4o"

# # Pre-existing conversation

# message1 = """You are a software developer who has to change the code below by following a given Code Review.
# The Code Review is attached to the line of code starting with the line number Start_Line and
# ending with the line number End_Line. There are also characters (- and +) showing where a line
# of code in the diff hunk has been removed (marked with a - at the beginning of the line) or added
# (marked with a + at the beginning of the line). The New Code Diff should be in the correct Git diff
# format, where added lines (on top of the diff hunk) are denoted with the + character. Lines removed
# from the Diff Hunk should be denoted with the - character. Your output must not contain any trailing
# tokens/characters. Your output must adhere to the following format: "Short Explanation: [...]

# New Code Diff: [...]"

# Start_Line:
# 1469

# End_Line:
# 1470

# Code Review:
# {'raw': 'Please fix this warning while you are here.'}

# Diff Hunk:

# Code Review:
# {comment_content}

# Diff Hunk:
# ```
# diff -u b/xpfe/appshell/AppWindow.cpp b/xpfe/appshell/AppWindow.cpp
# --- b/xpfe/appshell/AppWindow.cpp
# +++ b/xpfe/appshell/AppWindow.cpp
# @@ -1466,8 +1466,9 @@
#      nsresult errorCode;
#      int32_t zLevel = stateString.ToInteger(&errorCode);
#      if (NS_SUCCEEDED(errorCode) && zLevel >= int32_t(lowestZ) &&
# -        zLevel <= int32_t(highestZ))
# +        zLevel <= int32_t(highestZ)) {
#        SetZLevel(zLevel);
# +    }
#    }

#    return gotState;
# ```
# """

# message2 = """('Short Explanation: The code review suggests fixing a warning related to type conversion by explicitly casting `lowestZ` and `highestZ` to `int32_t`.\n\nNew Code Diff:\n```diff\ndiff --git a/xpfe/appshell/AppWindow.cpp b/xpfe/appshell/AppWindow.cpp\n--- a/xpfe/appshell/AppWindow.cpp\n+++ b/xpfe/appshell/AppWindow.cpp\n@@ -1463,11 +1463,12 @@\n   // zlevel\n   windowElement->GetAttribute(ZLEVEL_ATTRIBUTE, stateString);\n   if (!stateString.IsEmpty()) {\n     nsresult errorCode;\n     int32_t zLevel = stateString.ToInteger(&errorCode);\n-    if (NS_SUCCEEDED(errorCode) && zLevel >= lowestZ && zLevel <= highestZ)\n+    if (NS_SUCCEEDED(errorCode) && zLevel >= int32_t(lowestZ) &&\n+        zLevel <= int32_t(highestZ))\n       SetZLevel(zLevel);\n   }\n \n   return gotState;\n }\n```', '\n        You are a software developer who has to change the code below by following a given Code Review.\n        The Code Review is attached to the line of code starting with the line number Start_Line and\n        ending with the line number End_Line. There are also characters (- and +) showing where a line\n        of code in the diff hunk has been removed (marked with a - at the beginning of the line) or added\n        (marked with a + at the beginning of the line). The New Code Diff should be in the correct Git diff\n        format, where added lines (on top of the diff hunk) are denoted with the + character. Lines removed\n        from the Diff Hunk should be denoted with the - character. Your output must not contain any trailing\n        tokens/characters. Your output must adhere to the following format: "Short Explanation: [...] \n\n        New Code Diff: [...]"\n\n        Start_Line:\n        1469\n\n        End_Line:\n        1470\n\n        Code Review:\n        {\'raw\': \'Please fix this warning while you are here.\'}\n\n        Diff Hunk:\n        ```\n        diff --git a/xpfe/appshell/AppWindow.cpp b/xpfe/appshell/AppWindow.cpp\n--- a/xpfe/appshell/AppWindow.cpp\n+++ b/xpfe/appshell/AppWindow.cpp\n@@ -1463,11 +1463,12 @@\n   // zlevel\n   windowElement->GetAttribute(ZLEVEL_ATTRIBUTE, stateString);\n   if (!stateString.IsEmpty()) {\n     nsresult errorCode;\n     int32_t zLevel = stateString.ToInteger(&errorCode);\n-    if (NS_SUCCEEDED(errorCode) && zLevel >= lowestZ && zLevel <= highestZ)\n+    if (NS_SUCCEEDED(errorCode) && zLevel >= int32_t(lowestZ) &&\n+        zLevel <= int32_t(highestZ))\n       SetZLevel(zLevel);\n   }\n \n   return gotState;\n }\n\n        ```\n        ')
# """

# messages = [
#     {"role": "user", "content": message1},
#     {"role": "assistant", "content": message2}
# ]

# def chat(prompt):
#     global messages

#     messages.append({"role": "user", "content": prompt})

#     response = client.chat.completions.create(
#         messages=messages,
#         model=MODEL,
#         temperature=0.1,
#     )
#     return response.choices[0].message.content.strip()

#     # assistant_message = response["choices"][0]["message"]["content"].strip()

#     # messages.append({"role": "assistant", "content": assistant_message})

#     # return assistant_message

# print(chat("How did you know that this was the fix?"))

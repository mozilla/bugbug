# %%

import pandas as pd

from scripts.code_review_tool_evaluator import get_latest_evaluation_results_file

evaluation_results = pd.read_csv(
    get_latest_evaluation_results_file("../evaluation_results")
)

# %%

variant_names = evaluation_results["variant_name"].unique()
variant_name = variant_names[0]

df = evaluation_results[evaluation_results["variant_name"] == variant_name]


# %%
new_comments_count = df["new_comment"].count()
new_valid_comments = len(df[~df["new_comment"].isna() & (df["evaluation"] == "VALID")])
new_invalid_comments = len(
    df[~df["new_comment"].isna() & (df["evaluation"] == "INVALID")]
)
new_unevaluated_comments = len(df[~df["new_comment"].isna() & df["evaluation"].isna()])

old_comments_count = df["old_comments_count"].sum()
old_valid_comments = df[df["evaluation"] == "VALID"]["old_comments_count"].sum()
old_invalid_comments = df[df["evaluation"] == "INVALID"]["old_comments_count"].sum()

matched_valid_comments = df[
    ~df["new_comment"].isna()
    & ~df["old_comment"].isna()
    & (df["evaluation"] == "VALID")
]["old_comments_count"].sum()
matched_invalid_comments = df[
    ~df["new_comment"].isna()
    & ~df["old_comment"].isna()
    & (df["evaluation"] == "INVALID")
]["old_comments_count"].sum()

print("--------------------")
print("Variant Name:", variant_name)
print("--------------------")
print("New Comments:", new_comments_count)
print("New Valid Comments:", new_valid_comments)
print("New Invalid Comments:", new_invalid_comments)
print("New Unevaluated Comments:", new_unevaluated_comments)
print("--------------------")
print("Old Comments:", old_comments_count)
print("Old Valid Comments:", old_valid_comments)
print("Old Invalid Comments:", old_invalid_comments)
print("--------------------")
print(
    "Recalled comments:",
    (matched_valid_comments + matched_invalid_comments) / old_comments_count * 100,
)
print("Recalled valid comments:", matched_valid_comments / old_valid_comments * 100)
print(
    "Recalled invalid comments:", matched_invalid_comments / old_invalid_comments * 100
)
print("--------------------")
print(
    "Missed valid comments:",
    (old_valid_comments - matched_valid_comments) / old_valid_comments * 100,
)
print(
    "Missed invalid comments:",
    (old_invalid_comments - matched_invalid_comments) / old_invalid_comments * 100,
)


# %%


df = evaluation_results[
    evaluation_results["evaluation"].isin(["VALID", "INVALID"])
    & ~evaluation_results["new_comment"].isna()
].sort_values(by=["evaluation", "revision_id", "new_comment"])


df["id"] = df["diff_id"].astype(str) + " | " + df["new_comment"].astype(str)
df_old = df[df["variant_name"] == variant_names[0]]
df_new = df[df["variant_name"] == variant_names[1]]

in_new_but_not_in_old = df_new[~df_new["id"].isin(df_old["id"])]

print(
    "Examples of comments that were filtered by the old version but were not filtered by the new version:\n"
)
print(
    in_new_but_not_in_old[["revision_id", "new_comment", "evaluation"]].to_markdown(
        index=False
    )
)


in_old_but_not_in_new = df_old[~df_old["id"].isin(df_new["id"])]
print(
    "\n\nExamples of comments that were filtered by the new version but were not filtered by the old version:\n"
)
print(
    in_old_but_not_in_new[["revision_id", "new_comment", "evaluation"]].to_markdown(
        index=False
    )
)

# %%

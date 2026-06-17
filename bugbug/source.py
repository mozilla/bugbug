import subprocess
from pathlib import Path


def ensure_source_repo(repo_url, dest_path):
    if not isinstance(repo_url, str) or not repo_url:
        raise ValueError("repo_url must be a non-empty string")

    dest = Path(dest_path)

    if dest.exists():
        subprocess.run(
            ["git", "pull"],
            cwd=str(dest),
            check=True,
        )
    else:
        subprocess.run(
            ["git", "clone", repo_url, str(dest)],
            check=True,
        )

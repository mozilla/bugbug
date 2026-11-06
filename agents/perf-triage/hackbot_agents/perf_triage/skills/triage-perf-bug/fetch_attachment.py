#!/usr/bin/env python3
"""Download a Bugzilla attachment into the attachment cache and print its path.

usage: fetch_attachment.py <bug-number> <attachment-id>

Saves to ~/.cache/triage-attachments/<bug-number>/<attachment-id>.json.gz.
Reuses a cached copy. Exits non-zero if the download is not gzip or JSON
(a login page, a 404).
"""
import gzip
import json
import os
import sys
import urllib.request

URL = "https://bugzilla.mozilla.org/attachment.cgi?id={}"


def main():
    if len(sys.argv) != 3 or not all(a.isdigit() for a in sys.argv[1:]):
        sys.exit(__doc__)
    bug, att = sys.argv[1:]
    d = os.path.expanduser(f"~/.cache/triage-attachments/{bug}")
    path = f"{d}/{att}.json.gz"
    if not os.path.exists(path):
        os.makedirs(d, exist_ok=True)
        with urllib.request.urlopen(URL.format(att), timeout=60) as r:
            data = r.read()
        try:
            raw = data if data[:2] != b"\x1f\x8b" else gzip.decompress(data)
            # Firefox can write invalid UTF-8 in path names (e.g. add-on names).
            json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            sys.exit(f"attachment {att}: download is not gzip or JSON")
        if data[:2] != b"\x1f\x8b":
            data = gzip.compress(data)
        with open(path, "wb") as f:
            f.write(data)
    print(path)


if __name__ == "__main__":
    main()

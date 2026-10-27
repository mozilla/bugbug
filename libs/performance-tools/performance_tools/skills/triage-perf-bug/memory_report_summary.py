#!/usr/bin/env python3
"""Summarise an about:memory report (memory-report.json or .json.gz).

usage: memory_report_summary.py <report> [--top N]

Prints, per process, the explicit/ total and its largest second-level groups,
plus the process-level heap and gfx figures. Processes are sorted by explicit
total, largest first.
"""
import argparse
import gzip
import json
import re
import sys
from collections import defaultdict

MB = 1 << 20


def load(path):
    opener = gzip.open if path.endswith(".gz") else open
    # Firefox can write invalid UTF-8 in path names (e.g. add-on names).
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        data = json.load(f)
    if not isinstance(data, dict) or "reports" not in data:
        sys.exit(f"{path}: not a memory report (no top-level 'reports')")
    return data["reports"]


def process_kind(name):
    return re.sub(r"\s*\(pid .*", "", name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args()

    explicit = defaultdict(lambda: defaultdict(int))  # process -> group -> bytes
    other = defaultdict(dict)  # process -> path -> bytes
    for r in load(args.report):
        proc, path, amount = r["process"], r["path"], r["amount"]
        if path.startswith("explicit/"):
            parts = path.split("/")
            explicit[proc]["/".join(parts[:2])] += amount
        elif path in ("heap-allocated", "resident", "resident-unique", "vsize") or "gfx" in path or "gpu" in path.lower():
            other[proc][path] = amount

    procs = sorted(explicit, key=lambda p: -sum(explicit[p].values()))
    print(f"{len(procs)} processes; kinds: {sorted({process_kind(p) for p in procs})}")
    for proc in procs:
        groups = explicit[proc]
        print(f"\n{proc}: explicit {sum(groups.values()) / MB:.0f} MB")
        for g, v in sorted(groups.items(), key=lambda kv: -kv[1])[: args.top]:
            print(f"  {g}: {v / MB:.0f} MB")
        for p, v in sorted(other[proc].items(), key=lambda kv: -kv[1])[: args.top]:
            print(f"  [{p}]: {v / MB:.0f} MB")
    if not any("GPU" in process_kind(p) for p in procs):
        print("\nNo GPU process in this report.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Audit direction keyword dead nodes against a corpus without changing the direction."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import direction_core  # noqa: E402


def load_jobs(path: Path) -> list[dict]:
    files = sorted(path.rglob("*.json")) if path.is_dir() else [path]
    jobs = []
    for file in files:
        value = json.loads(file.read_text(encoding="utf-8"))
        if isinstance(value, list):
            jobs.extend(value)
        elif isinstance(value, dict) and isinstance(value.get("cards"), list):
            jobs.extend(value["cards"])
        elif isinstance(value, dict):
            jobs.append(value)
        else:
            raise ValueError(f"job corpus entry is not an object: {file}")
    return jobs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--jobs", required=True, type=Path,
                        help="one JobCard JSON file, a JSON list/cards object, or a directory")
    parser.add_argument("--mine-aliases", action="store_true",
                        help="measure corpus-written subphrases for dead non-title terms")
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    jobs = load_jobs(args.jobs)
    result = (direction_core.mine_direction_aliases(profile, jobs) if args.mine_aliases
              else direction_core.calibrate_direction_keywords(profile, jobs))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

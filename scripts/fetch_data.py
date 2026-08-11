#!/usr/bin/env python3
"""Check, fetch and regenerate the F1 datasets this app reads.

Three groups of files live at the repo root, and they are not the same kind of
thing:

**Required seed CSVs** -- ``results``, ``races``, ``drivers``, ``seasons``,
``constructors``, ``driver_standings``. These are tracked in git. ``load_data()``
falls back to them whenever the database is unreachable or unseeded, the health
probe checks for them, and the test suite scores whole eras of ``results.csv``
against Ergast's own points column. A clone without them cannot start.

**Optional bulk CSVs** -- ``lap_times`` (18 MB), ``constructor_results``,
``constructor_standings``, ``circuits``. Nothing in the codebase reads any of
them. They are no longer tracked; fetch them here if you want them.

**Derived files** -- ``adjusted_results.csv`` is output, not input: it is what
``adjusted_points.py`` writes. It was tracked as though it were data. Regenerate
it with ``--regenerate-adjusted`` instead.

Usage::

    python scripts/fetch_data.py            # report what is present
    python scripts/fetch_data.py --check    # exit 1 if a required file is bad (CI)
    python scripts/fetch_data.py --fetch-optional
    python scripts/fetch_data.py --regenerate-adjusted
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Upstream: the Kaggle mirror of the Ergast database, which is where these CSVs
# came from. Ergast itself was retired at the end of 2024.
KAGGLE_DATASET = "rohanrao/formula-1-world-championship-1950-2020"
KAGGLE_URL = f"https://www.kaggle.com/datasets/{KAGGLE_DATASET}"

# name -> (minimum plausible row count, columns that must exist)
REQUIRED = {
    "results.csv": (20000, ("resultId", "raceId", "driverId", "positionText", "positionOrder", "points", "rank")),
    "races.csv": (1000, ("raceId", "year", "round", "name")),
    "drivers.csv": (800, ("driverId", "forename", "surname")),
    "seasons.csv": (70, ("year",)),
    "constructors.csv": (200, ("constructorId", "name")),
    "driver_standings.csv": (30000, ("driverStandingsId", "raceId", "driverId", "points")),
}

OPTIONAL = (
    "lap_times.csv",
    "constructor_results.csv",
    "constructor_standings.csv",
    "circuits.csv",
)


def _read_header_and_count(path):
    """Row count and header of a CSV, without pulling 18 MB into a DataFrame."""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        header = handle.readline().strip().lstrip("﻿")
        rows = sum(1 for _ in handle)
    return [column.strip().strip('"') for column in header.split(",")], rows


def check(verbose: bool = True) -> int:
    """Validate the required CSVs. Returns the number of problems found."""
    problems = []

    for name, (min_rows, needed_columns) in REQUIRED.items():
        path = os.path.join(ROOT, name)
        if not os.path.isfile(path):
            problems.append(f"{name}: missing")
            continue

        columns, rows = _read_header_and_count(path)
        missing = [column for column in needed_columns if column not in columns]
        if missing:
            problems.append(f"{name}: missing column(s) {', '.join(missing)}")
        elif rows < min_rows:
            # A truncated file is worse than a missing one: the app starts and
            # quietly serves a partial championship.
            problems.append(f"{name}: only {rows} rows, expected at least {min_rows}")
        elif verbose:
            print(f"  ok       {name:<24} {rows:>6} rows")

    if verbose:
        for name in OPTIONAL:
            present = os.path.isfile(os.path.join(ROOT, name))
            print(f"  {'present ' if present else 'absent  '} {name:<24} (optional, unused by the app)")

        derived = os.path.join(ROOT, "adjusted_results.csv")
        state = "present" if os.path.isfile(derived) else "absent"
        print(f"  {state:<8} {'adjusted_results.csv':<24} (derived; --regenerate-adjusted)")

    for problem in problems:
        print(f"  FAIL     {problem}", file=sys.stderr)
    return len(problems)


def fetch_optional() -> int:
    """Download the optional bulk CSVs from Kaggle, if the CLI is configured."""
    try:
        subprocess.run(["kaggle", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        print(
            "The Kaggle CLI is not available or not authenticated.\n"
            f"  pip install kaggle, put an API token in ~/.kaggle/kaggle.json, then rerun.\n"
            f"  Or download by hand from {KAGGLE_URL} and unzip into the repo root.\n"
            f"  These files are optional: nothing in the app reads them.",
            file=sys.stderr,
        )
        return 1

    for name in OPTIONAL:
        print(f"fetching {name} ...")
        subprocess.run(
            ["kaggle", "datasets", "download", "-d", KAGGLE_DATASET, "-f", name, "-p", ROOT, "--unzip"],
            check=True,
        )
    return 0


def regenerate_adjusted() -> int:
    """Rebuild adjusted_results.csv from results.csv via adjusted_points.py."""
    script = os.path.join(ROOT, "adjusted_points.py")
    if not os.path.isfile(script):
        print("adjusted_points.py is gone; nothing to regenerate.", file=sys.stderr)
        return 1
    print("running adjusted_points.py ...")
    return subprocess.run([sys.executable, script], cwd=ROOT).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit non-zero if a required CSV is missing or short")
    parser.add_argument("--fetch-optional", action="store_true", help="download the untracked bulk CSVs from Kaggle")
    parser.add_argument("--regenerate-adjusted", action="store_true", help="rebuild adjusted_results.csv")
    args = parser.parse_args()

    if args.fetch_optional:
        return fetch_optional()
    if args.regenerate_adjusted:
        return regenerate_adjusted()

    print("F1 datasets:")
    problems = check(verbose=not args.check)

    if problems:
        print(
            f"\n{problems} problem(s) with the required seed CSVs.\n"
            f"They are tracked in git, so `git checkout -- '*.csv'` usually fixes this.\n"
            f"Otherwise re-download from {KAGGLE_URL}.",
            file=sys.stderr,
        )
        return 1

    if not args.check:
        print("\nAll required seed CSVs look good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

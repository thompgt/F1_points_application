"""Seed the F1 CSV datasets into the configured SQL database (MySQL by default).

The app reads its F1 data through `load_data()` in main.py, which queries these
tables. The CSVs are only the *seed* -- once this has run, MySQL is the point of
retrieval for anything requiring data.

Usage:
    docker compose up -d          # start MySQL first
    python scripts/seed_mysql.py  # skips tables that already hold rows
    python scripts/seed_mysql.py --force   # drop + reload everything
    python scripts/seed_mysql.py --tables results races
"""

import argparse
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import Float, Integer, String, inspect, text

# Load .env before importing db.py, which reads DATABASE_URL at import time.
REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")
sys.path.insert(0, str(REPO_ROOT))

from db import engine, DATABASE_URL  # noqa: E402

# Kaggle's F1 exports use the literal two-character sequence \N for missing values.
NA_VALUES = ["\\N"]

# table -> (csv filename, column dtypes, index columns)
# Integer columns are read as pandas' nullable Int64 so that missing values stay
# NULL in SQL instead of silently becoming floats.
TABLES = {
    "results": (
        "results.csv",
        {
            "resultId": Integer, "raceId": Integer, "driverId": Integer,
            "constructorId": Integer, "number": Integer, "grid": Integer,
            "position": Integer, "positionText": String(8), "positionOrder": Integer,
            "points": Float, "laps": Integer, "time": String(32),
            "milliseconds": Integer, "fastestLap": Integer, "rank": Integer,
            "fastestLapTime": String(16), "fastestLapSpeed": Float,
            "statusId": Integer,
        },
        ["resultId", "raceId", "driverId", "constructorId"],
    ),
    "races": (
        "races.csv",
        {
            "raceId": Integer, "year": Integer, "round": Integer,
            "circuitId": Integer, "name": String(128), "date": String(16),
            "time": String(16), "url": String(255),
            "fp1_date": String(16), "fp1_time": String(16),
            "fp2_date": String(16), "fp2_time": String(16),
            "fp3_date": String(16), "fp3_time": String(16),
            "quali_date": String(16), "quali_time": String(16),
            "sprint_date": String(16), "sprint_time": String(16),
        },
        ["raceId", "year", "circuitId"],
    ),
    "drivers": (
        "drivers.csv",
        {
            "driverId": Integer, "driverRef": String(64), "number": Integer,
            "code": String(8), "forename": String(64), "surname": String(64),
            "dob": String(16), "nationality": String(64), "url": String(255),
        },
        ["driverId"],
    ),
    "seasons": ("seasons.csv", {"year": Integer, "url": String(255)}, ["year"]),
    "constructors": (
        "constructors.csv",
        {
            "constructorId": Integer, "constructorRef": String(64),
            "name": String(128), "nationality": String(64), "url": String(255),
        },
        ["constructorId"],
    ),
    "driver_standings": (
        "driver_standings.csv",
        {
            "driverStandingsId": Integer, "raceId": Integer, "driverId": Integer,
            "points": Float, "position": Integer, "positionText": String(8),
            "wins": Integer,
        },
        ["driverStandingsId", "raceId", "driverId"],
    ),
}


def read_csv(path, dtypes):
    df = pd.read_csv(path, na_values=NA_VALUES, keep_default_na=True)
    for column, sql_type in dtypes.items():
        if column not in df.columns:
            continue
        if sql_type is Integer:
            df[column] = pd.to_numeric(df[column], errors="coerce").astype("Int64")
        elif sql_type is Float:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def row_count(table):
    inspector = inspect(engine)
    if not inspector.has_table(table):
        return None
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar()


def create_indexes(table, columns):
    """Add lookup indexes. Skipped silently if they already exist."""
    with engine.begin() as conn:
        for column in columns:
            index_name = f"ix_{table}_{column}"
            try:
                conn.execute(text(f"CREATE INDEX `{index_name}` ON `{table}` (`{column}`)"))
            except Exception:
                # Index already present (MySQL has no CREATE INDEX IF NOT EXISTS).
                pass


def seed(table, data_dir, force):
    csv_name, dtypes, index_columns = TABLES[table]
    csv_path = Path(data_dir) / csv_name
    if not csv_path.exists():
        print(f"  {table:18} SKIP  missing {csv_path}")
        return False

    existing = row_count(table)
    if existing and not force:
        print(f"  {table:18} SKIP  already holds {existing:,} rows (use --force to reload)")
        return False

    df = read_csv(csv_path, dtypes)
    df.to_sql(
        table,
        engine,
        if_exists="replace",
        index=False,
        chunksize=5000,
        method="multi",
        dtype={c: t for c, t in dtypes.items() if c in df.columns},
    )
    create_indexes(table, [c for c in index_columns if c in df.columns])
    print(f"  {table:18} OK    {len(df):,} rows from {csv_name}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="drop and reload tables that already have rows")
    parser.add_argument("--tables", nargs="*", choices=sorted(TABLES), help="only seed these tables")
    parser.add_argument("--data-dir", default=os.getenv("DATA_DIR", str(REPO_ROOT)), help="directory holding the CSVs")
    args = parser.parse_args()

    safe_url = DATABASE_URL
    if "@" in safe_url:  # never print credentials
        safe_url = safe_url.split("://", 1)[0] + "://***@" + safe_url.rsplit("@", 1)[1]
    print(f"Seeding {safe_url} from {args.data_dir}")

    if engine.dialect.name == "sqlite":
        print(
            "WARNING: DATABASE_URL points at SQLite, not MySQL.\n"
            "         Start MySQL with `docker compose up -d` and set DATABASE_URL in .env, e.g.\n"
            "         DATABASE_URL=mysql+pymysql://f1user:f1pass@localhost:3306/f1"
        )

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        print(f"ERROR: cannot connect to the database: {exc}")
        return 1

    seeded = 0
    for table in (args.tables or list(TABLES)):
        if seed(table, args.data_dir, args.force):
            seeded += 1

    print(f"\nDone. {seeded} table(s) loaded.")
    for table in TABLES:
        count = row_count(table)
        print(f"  {table:18} {'-' if count is None else format(count, ',')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

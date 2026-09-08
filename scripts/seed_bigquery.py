"""Seed the F1 CSV datasets into Google Cloud BigQuery.

Creates a BigQuery dataset (default: `f1_points`) and populates it with the
historical Formula 1 datasets with explicit schemas and table clustering.

Usage:
    python scripts/seed_bigquery.py --dry-run
    python scripts/seed_bigquery.py --project my-gcp-project --dataset f1_points
    python scripts/seed_bigquery.py --force   # overwrite existing tables
    python scripts/seed_bigquery.py --tables results races
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

try:
    from google.cloud import bigquery
    from google.cloud.bigquery import SchemaField
    BIGQUERY_AVAILABLE = True
except ImportError:
    bigquery = None
    SchemaField = None
    BIGQUERY_AVAILABLE = False


# Kaggle's F1 exports use the literal two-character sequence \N for missing values.
NA_VALUES = ["\\N"]

# ---------------------------------------------------------------------------
# Table schemas and clustering configurations
# ---------------------------------------------------------------------------

TABLE_SCHEMAS: Dict[str, Dict] = {
    "results": {
        "csv": "results.csv",
        "clustering_fields": ["raceId", "driverId", "constructorId"],
        "schema": [
            ("resultId", "INTEGER", "REQUIRED", "Unique result identifier"),
            ("raceId", "INTEGER", "REQUIRED", "Foreign key to races table"),
            ("driverId", "INTEGER", "REQUIRED", "Foreign key to drivers table"),
            ("constructorId", "INTEGER", "REQUIRED", "Foreign key to constructors table"),
            ("number", "INTEGER", "NULLABLE", "Driver car number"),
            ("grid", "INTEGER", "NULLABLE", "Starting grid position"),
            ("position", "INTEGER", "NULLABLE", "Official finishing position"),
            ("positionText", "STRING", "NULLABLE", "Finishing position as text (e.g. '1' or 'R')"),
            ("positionOrder", "INTEGER", "REQUIRED", "Numeric finishing order for sorting"),
            ("points", "FLOAT", "NULLABLE", "Points scored in the race"),
            ("laps", "INTEGER", "NULLABLE", "Laps completed"),
            ("time", "STRING", "NULLABLE", "Race time or gap"),
            ("milliseconds", "INTEGER", "NULLABLE", "Race time in milliseconds"),
            ("fastestLap", "INTEGER", "NULLABLE", "Lap number of driver's fastest lap"),
            ("rank", "INTEGER", "NULLABLE", "Rank of driver's fastest lap"),
            ("fastestLapTime", "STRING", "NULLABLE", "Fastest lap time string"),
            ("fastestLapSpeed", "FLOAT", "NULLABLE", "Fastest lap average speed in km/h"),
            ("statusId", "INTEGER", "NULLABLE", "Status code (finished, collision, engine, etc.)"),
        ],
    },
    "races": {
        "csv": "races.csv",
        "clustering_fields": ["year", "circuitId"],
        "schema": [
            ("raceId", "INTEGER", "REQUIRED", "Unique race identifier"),
            ("year", "INTEGER", "REQUIRED", "Championship season year"),
            ("round", "INTEGER", "NULLABLE", "Round number within the season"),
            ("circuitId", "INTEGER", "NULLABLE", "Foreign key to circuits table"),
            ("name", "STRING", "NULLABLE", "Grand Prix official name"),
            ("date", "STRING", "NULLABLE", "Race date (YYYY-MM-DD)"),
            ("time", "STRING", "NULLABLE", "Race start time (UTC)"),
            ("url", "STRING", "NULLABLE", "Wikipedia URL"),
            ("fp1_date", "STRING", "NULLABLE", "Free practice 1 date"),
            ("fp1_time", "STRING", "NULLABLE", "Free practice 1 time"),
            ("fp2_date", "STRING", "NULLABLE", "Free practice 2 date"),
            ("fp2_time", "STRING", "NULLABLE", "Free practice 2 time"),
            ("fp3_date", "STRING", "NULLABLE", "Free practice 3 date"),
            ("fp3_time", "STRING", "NULLABLE", "Free practice 3 time"),
            ("quali_date", "STRING", "NULLABLE", "Qualifying date"),
            ("quali_time", "STRING", "NULLABLE", "Qualifying time"),
            ("sprint_date", "STRING", "NULLABLE", "Sprint race date"),
            ("sprint_time", "STRING", "NULLABLE", "Sprint race time"),
        ],
    },
    "drivers": {
        "csv": "drivers.csv",
        "clustering_fields": ["driverId"],
        "schema": [
            ("driverId", "INTEGER", "REQUIRED", "Unique driver identifier"),
            ("driverRef", "STRING", "NULLABLE", "Unique text slug (e.g. 'hamilton')"),
            ("number", "INTEGER", "NULLABLE", "Permanent driver number"),
            ("code", "STRING", "NULLABLE", "Three-letter driver code (e.g. 'HAM')"),
            ("forename", "STRING", "NULLABLE", "Driver first name"),
            ("surname", "STRING", "NULLABLE", "Driver last name"),
            ("dob", "STRING", "NULLABLE", "Date of birth (YYYY-MM-DD)"),
            ("nationality", "STRING", "NULLABLE", "Driver nationality"),
            ("url", "STRING", "NULLABLE", "Wikipedia URL"),
        ],
    },
    "seasons": {
        "csv": "seasons.csv",
        "clustering_fields": ["year"],
        "schema": [
            ("year", "INTEGER", "REQUIRED", "Championship season year"),
            ("url", "STRING", "NULLABLE", "Wikipedia URL"),
        ],
    },
    "constructors": {
        "csv": "constructors.csv",
        "clustering_fields": ["constructorId"],
        "schema": [
            ("constructorId", "INTEGER", "REQUIRED", "Unique constructor identifier"),
            ("constructorRef", "STRING", "NULLABLE", "Unique text slug (e.g. 'ferrari')"),
            ("name", "STRING", "NULLABLE", "Constructor team name"),
            ("nationality", "STRING", "NULLABLE", "Constructor nationality"),
            ("url", "STRING", "NULLABLE", "Wikipedia URL"),
        ],
    },
    "driver_standings": {
        "csv": "driver_standings.csv",
        "clustering_fields": ["raceId", "driverId"],
        "schema": [
            ("driverStandingsId", "INTEGER", "REQUIRED", "Unique standing entry identifier"),
            ("raceId", "INTEGER", "REQUIRED", "Foreign key to races table"),
            ("driverId", "INTEGER", "REQUIRED", "Foreign key to drivers table"),
            ("points", "FLOAT", "NULLABLE", "Cumulative championship points after this race"),
            ("position", "INTEGER", "NULLABLE", "Championship standing position"),
            ("positionText", "STRING", "NULLABLE", "Championship standing as text"),
            ("wins", "INTEGER", "NULLABLE", "Total race wins in the season up to this race"),
        ],
    },
}


def build_bigquery_schema(table_name: str) -> List[Any]:
    """Return google.cloud.bigquery.SchemaField list for a table."""
    if not BIGQUERY_AVAILABLE:
        raise RuntimeError("google-cloud-bigquery is not installed.")
    
    table_meta = TABLE_SCHEMAS[table_name]
    return [
        SchemaField(name=col, field_type=ftype, mode=mode, description=desc)
        for col, ftype, mode, desc in table_meta["schema"]
    ]


def load_clean_csv(table_name: str, data_dir: Path) -> pd.DataFrame:
    """Read a seed CSV into pandas, enforcing types and nullable integers."""
    meta = TABLE_SCHEMAS[table_name]
    csv_path = data_dir / meta["csv"]
    if not csv_path.exists():
        raise FileNotFoundError(f"Seed CSV not found: {csv_path}")

    # Build dtype map
    dtype_map = {}
    for col, ftype, _, _ in meta["schema"]:
        if ftype == "INTEGER":
            dtype_map[col] = "Int64"  # pandas nullable integer
        elif ftype == "FLOAT":
            dtype_map[col] = "float64"
        elif ftype == "STRING":
            dtype_map[col] = "string"

    df = pd.read_csv(csv_path, na_values=NA_VALUES, dtype=dtype_map)
    # Drop any unexpected columns not in schema
    expected_cols = [c[0] for c in meta["schema"]]
    available_cols = [c for c in expected_cols if c in df.columns]
    return df[available_cols]


def seed_bigquery(
    project_id: str,
    dataset_name: str = "f1_points",
    location: str = "us-central1",
    tables: Optional[List[str]] = None,
    force: bool = False,
    dry_run: bool = False,
    data_dir: Optional[Path] = None,
) -> Dict[str, int]:
    """Seed CSV tables into BigQuery."""
    data_dir = data_dir or REPO_ROOT
    selected_tables = tables or list(TABLE_SCHEMAS.keys())

    # Validate table choices
    for t in selected_tables:
        if t not in TABLE_SCHEMAS:
            raise ValueError(f"Unknown table '{t}'. Available: {list(TABLE_SCHEMAS.keys())}")

    summary = {}

    if dry_run:
        print(f"[DRY-RUN] Target: BigQuery `{project_id}.{dataset_name}` (location: {location})")
        for table_name in selected_tables:
            meta = TABLE_SCHEMAS[table_name]
            df = load_clean_csv(table_name, data_dir)
            summary[table_name] = len(df)
            print(
                f"  - Table `{table_name}`: {len(df):,} rows from {meta['csv']}. "
                f"Columns: {len(df.columns)}. Clustering: {meta['clustering_fields']}"
            )
        print("[DRY-RUN] Verification complete. No API requests were sent.")
        return summary

    if not BIGQUERY_AVAILABLE:
        print("ERROR: google-cloud-bigquery is not installed. Install with `pip install google-cloud-bigquery db-dtypes`", file=sys.stderr)
        sys.exit(1)

    client = bigquery.Client(project=project_id, location=location)

    # 1. Create or verify dataset
    dataset_id = f"{project_id}.{dataset_name}"
    dataset_ref = bigquery.Dataset(dataset_id)
    dataset_ref.location = location
    dataset_ref.description = "Formula 1 Historical World Championship Dataset (Ergast archive 1950-present)"

    dataset = client.create_dataset(dataset_ref, exists_ok=True)
    print(f"Verified BigQuery dataset `{dataset.dataset_id}` in `{location}`")

    # 2. Upload tables
    for table_name in selected_tables:
        meta = TABLE_SCHEMAS[table_name]
        table_id = f"{dataset_id}.{table_name}"
        table_ref = bigquery.Table(table_id, schema=build_bigquery_schema(table_name))

        # Check existing table
        table_exists = False
        try:
            existing_table = client.get_table(table_id)
            table_exists = True
            if existing_table.num_rows and existing_table.num_rows > 0 and not force:
                print(f"Table `{table_name}` already exists with {existing_table.num_rows:,} rows. Skipping (use --force to reload).")
                summary[table_name] = existing_table.num_rows
                continue
        except Exception:
            table_exists = False

        if table_exists and force:
            client.delete_table(table_id)
            print(f"Dropped existing table `{table_name}`")

        # Set clustering
        if meta.get("clustering_fields"):
            table_ref.clustering_fields = meta["clustering_fields"]

        df = load_clean_csv(table_name, data_dir)
        print(f"Loading {len(df):,} rows into `{table_name}`...")

        job_config = bigquery.LoadJobConfig(
            schema=build_bigquery_schema(table_name),
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE if force else bigquery.WriteDisposition.WRITE_EMPTY,
            clustering_fields=meta.get("clustering_fields"),
        )

        load_job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
        load_job.result()  # Waits for table load to complete

        dest_table = client.get_table(table_id)
        print(f"Successfully loaded `{table_name}`: {dest_table.num_rows:,} rows in BigQuery.")
        summary[table_name] = dest_table.num_rows

    return summary


def main():
    parser = argparse.ArgumentParser(description="Seed Formula 1 datasets into Google Cloud BigQuery.")
    parser.add_argument(
        "--project",
        default=os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT"),
        help="Google Cloud Project ID (defaults to GCP_PROJECT_ID env var)",
    )
    parser.add_argument(
        "--dataset",
        default=os.getenv("BIGQUERY_DATASET", "f1_points"),
        help="BigQuery dataset name (default: f1_points)",
    )
    parser.add_argument(
        "--location",
        default=os.getenv("GCP_REGION", "us-central1"),
        help="BigQuery dataset location (default: us-central1)",
    )
    parser.add_argument(
        "--tables",
        nargs="+",
        choices=list(TABLE_SCHEMAS.keys()),
        help="Specific tables to seed (default: all tables)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Drop and recreate existing tables if already populated",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate CSVs and schema types locally without sending BigQuery API requests",
    )

    args = parser.parse_args()

    project = args.project or "local-dryrun-project"
    if not args.dry_run and not args.project:
        print("ERROR: --project or GCP_PROJECT_ID environment variable is required when not in --dry-run mode.", file=sys.stderr)
        sys.exit(1)

    print("--- Formula 1 BigQuery Seeder ---")
    seed_bigquery(
        project_id=project,
        dataset_name=args.dataset,
        location=args.location,
        tables=args.tables,
        force=args.force,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()

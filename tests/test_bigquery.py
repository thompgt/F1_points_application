"""Unit tests for BigQuery seeder script, schema mapping, and fallback loader."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pandas as pd

from scripts.seed_bigquery import (
    TABLE_SCHEMAS,
    build_bigquery_schema,
    load_clean_csv,
    seed_bigquery,
)
import main


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_bigquery_table_schemas():
    """Verify all core tables have valid schema and clustering definitions."""
    expected_tables = ["results", "races", "drivers", "seasons", "constructors", "driver_standings"]
    for table in expected_tables:
        assert table in TABLE_SCHEMAS
        meta = TABLE_SCHEMAS[table]
        assert "csv" in meta
        assert "schema" in meta
        assert len(meta["schema"]) > 0


def test_bigquery_build_schema():
    """Verify SchemaField objects are built with correct types and modes."""
    fields = build_bigquery_schema("results")
    assert len(fields) == 18
    field_dict = {f.name: f for f in fields}

    assert "resultId" in field_dict
    assert field_dict["resultId"].field_type == "INTEGER"
    assert field_dict["resultId"].mode == "REQUIRED"

    assert "points" in field_dict
    assert field_dict["points"].field_type == "FLOAT"


def test_bigquery_clean_csv_loading():
    """Verify CSV loader reads files with nullable Int64 types."""
    df_seasons = load_clean_csv("seasons", REPO_ROOT)
    assert not df_seasons.empty
    assert "year" in df_seasons.columns
    assert str(df_seasons["year"].dtype) == "Int64"


def test_bigquery_seeder_dry_run():
    """Verify dry_run parses all tables and returns row count summaries."""
    summary = seed_bigquery(
        project_id="test-project",
        dataset_name="test_dataset",
        dry_run=True,
        data_dir=REPO_ROOT,
    )
    assert len(summary) == 6
    assert summary["results"] > 20000
    assert summary["races"] > 1000
    assert summary["drivers"] > 800
    assert summary["seasons"] > 70


def test_main_bigquery_fallback_on_error(monkeypatch):
    """Verify _load_data_cached falls back to CSV if BigQuery raises an error."""
    monkeypatch.setenv("BIGQUERY_DATASET", "invalid_mock_dataset")
    monkeypatch.setenv("GCP_PROJECT_ID", "mock-project")

    # Clear cached data
    main.load_data.cache_clear()

    # Should not raise; falls back gracefully to CSVs
    results, races, drivers, seasons, constructors, driver_standings = main.load_data()
    assert not results.empty
    assert not races.empty
    assert not drivers.empty


def test_main_load_from_bigquery_mock():
    """Verify _load_from_bigquery constructs queries and collects frames."""
    mock_df = pd.DataFrame({"dummy": [1, 2, 3]})

    with patch("google.cloud.bigquery.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_job = MagicMock()
        mock_job.to_dataframe.return_value = mock_df
        mock_client.query.return_value = mock_job

        frames = main._load_from_bigquery("test_dataset", project_id="test-project")
        assert len(frames) == len(main.DATA_TABLES)
        assert all(f.equals(mock_df) for f in frames)
        assert mock_client.query.call_count == len(main.DATA_TABLES)

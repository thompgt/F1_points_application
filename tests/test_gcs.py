"""Unit tests for Google Cloud Storage integration, endpoints, and FastMCP tools."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

import gcs_storage
import mcp_server
from main import app
from scripts.sync_gcs import check_bucket_access, upload_local_reports


@pytest.fixture
def client():
    return TestClient(app)


def test_is_gcs_enabled_toggle(monkeypatch):
    """Verify is_gcs_enabled correctly reflects GCS_BUCKET_NAME env variable."""
    monkeypatch.delenv("GCS_BUCKET_NAME", raising=False)
    assert gcs_storage.is_gcs_enabled() is False

    monkeypatch.setenv("GCS_BUCKET_NAME", "my-test-f1-bucket")
    assert gcs_storage.is_gcs_enabled() is True


def test_get_report_blob_name():
    """Verify consistent blob naming and character sanitization."""
    name1 = gcs_storage.get_report_blob_name(2021, "Modern")
    assert name1 == "season_reports/F1_Season_2021_Modern.pdf"

    name2 = gcs_storage.get_report_blob_name(1995, "1991-2002 / Top 6")
    assert name2 == "season_reports/F1_Season_1995_1991-2002___Top_6.pdf"


def test_report_exists_mocked(monkeypatch):
    """Verify report_exists queries blob.exists on configured bucket."""
    monkeypatch.setenv("GCS_BUCKET_NAME", "test-bucket")
    gcs_storage._client_cache = None

    with patch("gcs_storage.get_storage_client") as mock_get_client:
        mock_client = MagicMock()
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_blob.exists.return_value = True
        mock_bucket.blob.return_value = mock_blob
        mock_client.bucket.return_value = mock_bucket
        mock_get_client.return_value = mock_client

        exists = gcs_storage.report_exists("season_reports/test.pdf")
        assert exists is True
        mock_bucket.blob.assert_called_once_with("season_reports/test.pdf")
        mock_blob.exists.assert_called_once()


def test_upload_report_mocked(tmp_path, monkeypatch):
    """Verify upload_report correctly invokes blob.upload_from_filename."""
    monkeypatch.setenv("GCS_BUCKET_NAME", "test-bucket")
    gcs_storage._client_cache = None

    test_file = tmp_path / "test_report.pdf"
    test_file.write_bytes(b"%PDF-1.4 mock content")

    with patch("gcs_storage.get_storage_client") as mock_get_client:
        mock_client = MagicMock()
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_client.bucket.return_value = mock_bucket
        mock_get_client.return_value = mock_client

        result = gcs_storage.upload_report(str(test_file))
        assert result == "season_reports/test_report.pdf"
        mock_blob.upload_from_filename.assert_called_once_with(
            str(test_file), content_type="application/pdf"
        )


def test_generate_report_signed_url_mocked(monkeypatch):
    """Verify generate_report_signed_url generates v4 signed GET URLs."""
    monkeypatch.setenv("GCS_BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("GCS_SIGNED_URL_EXPIRATION_MINUTES", "45")
    gcs_storage._client_cache = None

    with patch("gcs_storage.get_storage_client") as mock_get_client:
        mock_client = MagicMock()
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_blob.generate_signed_url.return_value = "https://storage.googleapis.com/test-bucket/signed-url"
        mock_bucket.blob.return_value = mock_blob
        mock_client.bucket.return_value = mock_bucket
        mock_get_client.return_value = mock_client

        url = gcs_storage.generate_report_signed_url("season_reports/report.pdf")
        assert url == "https://storage.googleapis.com/test-bucket/signed-url"
        mock_blob.generate_signed_url.assert_called_once()


def test_download_report_mocked(tmp_path, monkeypatch):
    """Verify download_report creates parent dirs and downloads blob."""
    monkeypatch.setenv("GCS_BUCKET_NAME", "test-bucket")
    gcs_storage._client_cache = None

    dest_path = tmp_path / "sub" / "downloaded.pdf"

    with patch("gcs_storage.get_storage_client") as mock_get_client:
        mock_client = MagicMock()
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_client.bucket.return_value = mock_bucket
        mock_get_client.return_value = mock_client

        success = gcs_storage.download_report("season_reports/test.pdf", str(dest_path))
        assert success is True
        mock_blob.download_to_filename.assert_called_once_with(str(dest_path))


def test_list_stored_reports_mocked(monkeypatch):
    """Verify list_stored_reports filters and formats blob metadata."""
    monkeypatch.setenv("GCS_BUCKET_NAME", "test-bucket")
    gcs_storage._client_cache = None

    blob1 = MagicMock()
    blob1.name = "season_reports/F1_Season_2021_Modern.pdf"
    blob1.size = 1048576
    blob1.updated = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)

    blob2 = MagicMock()
    blob2.name = "season_reports/metadata.json"  # Non-PDF should be ignored

    with patch("gcs_storage.get_storage_client") as mock_get_client:
        mock_client = MagicMock()
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"
        mock_client.bucket.return_value = mock_bucket
        mock_client.list_blobs.return_value = [blob1, blob2]
        mock_get_client.return_value = mock_client

        reports = gcs_storage.list_stored_reports()
        assert len(reports) == 1
        assert reports[0]["filename"] == "F1_Season_2021_Modern.pdf"
        assert reports[0]["size_bytes"] == 1048576
        assert reports[0]["gcs_uri"] == "gs://test-bucket/season_reports/F1_Season_2021_Modern.pdf"


def test_api_list_reports_local(client, monkeypatch):
    """Verify GET /api/reports falls back to local storage when GCS is disabled."""
    monkeypatch.delenv("GCS_BUCKET_NAME", raising=False)
    resp = client.get("/api/reports")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "local"
    assert isinstance(data["reports"], list)


def test_api_list_reports_gcs(client, monkeypatch):
    """Verify GET /api/reports queries GCS when enabled."""
    monkeypatch.setenv("GCS_BUCKET_NAME", "test-bucket")
    gcs_storage._client_cache = None

    with patch("gcs_storage.list_stored_reports") as mock_list:
        mock_list.return_value = [
            {"filename": "test.pdf", "blob_name": "season_reports/test.pdf", "size_bytes": 500}
        ]
        resp = client.get("/api/reports")
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "gcs"
        assert len(data["reports"]) == 1
        assert data["reports"][0]["filename"] == "test.pdf"


def test_api_get_report_url(client, monkeypatch):
    """Verify GET /api/reports/{filename}/url behavior under various states."""
    # 1. Disabled GCS -> 503
    monkeypatch.delenv("GCS_BUCKET_NAME", raising=False)
    resp_disabled = client.get("/api/reports/test.pdf/url")
    assert resp_disabled.status_code == 503

    # 2. Enabled GCS, but report doesn't exist -> 404
    monkeypatch.setenv("GCS_BUCKET_NAME", "test-bucket")
    with patch("gcs_storage.report_exists", return_value=False):
        resp_404 = client.get("/api/reports/nonexistent.pdf/url")
        assert resp_404.status_code == 404

    # 3. Enabled GCS, report exists -> 200 with signed URL
    with patch("gcs_storage.report_exists", return_value=True), \
         patch("gcs_storage.generate_report_signed_url", return_value="https://storage.googleapis.com/download"):
        resp_ok = client.get("/api/reports/available.pdf/url")
        assert resp_ok.status_code == 200
        data = resp_ok.json()
        assert data["filename"] == "available.pdf"
        assert data["download_url"] == "https://storage.googleapis.com/download"
        assert data["expires_in_minutes"] == 60


def test_mcp_gcs_tools_and_resource(monkeypatch):
    """Verify FastMCP report tools and JSON resource."""
    # Test tool: list_available_season_reports
    monkeypatch.delenv("GCS_BUCKET_NAME", raising=False)
    reports = mcp_server.list_available_season_reports()
    assert isinstance(reports, list)

    # Test tool: get_season_report_url
    url_info = mcp_server.get_season_report_url(2021, "Modern")
    assert isinstance(url_info, dict)
    assert url_info["season"] == 2021
    assert "available" in url_info

    # Test resource: f1://reports
    res = mcp_server.resource_season_reports()
    import json
    data = json.loads(res)
    assert "storage_type" in data
    assert "reports" in data


def test_sync_gcs_script_helpers(tmp_path, monkeypatch):
    """Verify scripts/sync_gcs.py helper functions with mocks."""
    monkeypatch.setenv("GCS_BUCKET_NAME", "test-bucket")
    gcs_storage._client_cache = None

    with patch("gcs_storage.get_storage_client") as mock_get_client:
        mock_client = MagicMock()
        mock_bucket = MagicMock()
        mock_client.get_bucket.return_value = mock_bucket
        mock_get_client.return_value = mock_client

        # check_bucket_access
        assert check_bucket_access("test-bucket") is True

        # upload_local_reports dry run
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"dummy")
        uploaded = upload_local_reports(tmp_path, dry_run=True)
        assert uploaded == 1

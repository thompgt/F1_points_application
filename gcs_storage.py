"""Google Cloud Storage (GCS) integration for report persistence and artifacts.

Provides functions for uploading generated PDF season simulation reports to a GCS
bucket, generating secure time-limited Signed URLs for direct client downloads,
checking report cache existence, and listing stored reports.

Falls back gracefully when GCS is disabled or unconfigured, preserving local
filesystem operations.
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("f1_api")

try:
    from google.cloud import storage
    GCS_AVAILABLE = True
except ImportError:
    storage = None
    GCS_AVAILABLE = False


_client_cache: Optional[Any] = None


def is_gcs_enabled() -> bool:
    """Return True if google-cloud-storage is installed and GCS_BUCKET_NAME is configured."""
    return bool(GCS_AVAILABLE and os.getenv("GCS_BUCKET_NAME"))


def get_gcs_bucket_name() -> str:
    """Return configured GCS bucket name."""
    return os.getenv("GCS_BUCKET_NAME", "").strip()


def get_signed_url_expiration_minutes() -> int:
    """Return signed URL expiration in minutes (default: 60)."""
    try:
        return int(os.getenv("GCS_SIGNED_URL_EXPIRATION_MINUTES", "60"))
    except ValueError:
        return 60


def get_storage_client() -> Optional[Any]:
    """Get or initialize the Google Cloud Storage client."""
    global _client_cache
    if not GCS_AVAILABLE:
        return None
    if _client_cache is None:
        try:
            project_id = os.getenv("GCP_PROJECT_ID")
            _client_cache = storage.Client(project=project_id)
        except Exception as exc:
            logger.warning(f"Failed to initialize GCS client: {exc}")
            return None
    return _client_cache


def get_report_blob_name(season_year: int, points_system_name: str) -> str:
    """Generate a consistent blob name for a season simulation report."""
    safe_system = points_system_name.replace(" ", "_").replace("/", "_")
    return f"season_reports/F1_Season_{season_year}_{safe_system}.pdf"


def report_exists(blob_name: str) -> bool:
    """Check if a generated report already exists in the GCS bucket."""
    if not is_gcs_enabled():
        return False
    try:
        client = get_storage_client()
        if client is None:
            return False
        bucket = client.bucket(get_gcs_bucket_name())
        blob = bucket.blob(blob_name)
        return blob.exists()
    except Exception as exc:
        logger.warning(f"Error checking GCS report existence for '{blob_name}': {exc}")
        return False


def upload_report(
    local_file_path: str,
    destination_blob_name: Optional[str] = None,
    content_type: str = "application/pdf",
) -> Optional[str]:
    """Upload a local report file to GCS.

    Args:
        local_file_path: Path to the local PDF file.
        destination_blob_name: Optional custom GCS blob path. If None, defaults to
                               season_reports/{filename}.
        content_type: MIME type of the file.

    Returns:
        The GCS blob name if upload succeeded, or None if failed / GCS disabled.
    """
    if not is_gcs_enabled():
        return None

    file_path = Path(local_file_path)
    if not file_path.exists():
        logger.error(f"Local file '{local_file_path}' does not exist for GCS upload.")
        return None

    blob_name = destination_blob_name or f"season_reports/{file_path.name}"
    bucket_name = get_gcs_bucket_name()

    try:
        client = get_storage_client()
        if client is None:
            return None
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        blob.upload_from_filename(str(file_path), content_type=content_type)
        logger.info(f"Uploaded report '{file_path.name}' to gs://{bucket_name}/{blob_name}")
        return blob_name
    except Exception as exc:
        logger.error(f"Failed to upload '{file_path.name}' to GCS: {exc}")
        return None


def generate_report_signed_url(
    blob_name: str,
    expiration_minutes: Optional[int] = None,
) -> Optional[str]:
    """Generate a time-limited v4 Signed URL for direct client download.

    Args:
        blob_name: The GCS blob path.
        expiration_minutes: Minutes until URL expires (defaults to env or 60).

    Returns:
        The signed HTTPS URL string or None if generation failed.
    """
    if not is_gcs_enabled():
        return None

    mins = expiration_minutes or get_signed_url_expiration_minutes()
    bucket_name = get_gcs_bucket_name()

    try:
        client = get_storage_client()
        if client is None:
            return None
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        # Generate signed URL
        url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=mins),
            method="GET",
        )
        return url
    except Exception as exc:
        logger.warning(f"Could not generate signed URL for '{blob_name}': {exc}")
        return None


def download_report(blob_name: str, local_destination_path: str) -> bool:
    """Download a report from GCS to local filesystem.

    Args:
        blob_name: The GCS blob path.
        local_destination_path: Local path where the file will be saved.

    Returns:
        True if download succeeded, False otherwise.
    """
    if not is_gcs_enabled():
        return False

    dest = Path(local_destination_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        client = get_storage_client()
        if client is None:
            return False
        bucket = client.bucket(get_gcs_bucket_name())
        blob = bucket.blob(blob_name)

        blob.download_to_filename(str(dest))
        logger.info(f"Downloaded '{blob_name}' from GCS to '{dest}'")
        return True
    except Exception as exc:
        logger.error(f"Failed to download '{blob_name}' from GCS: {exc}")
        return False


def list_stored_reports(prefix: str = "season_reports/") -> List[Dict[str, Any]]:
    """List all available season simulation reports stored in GCS."""
    if not is_gcs_enabled():
        return []

    try:
        client = get_storage_client()
        if client is None:
            return []
        bucket = client.bucket(get_gcs_bucket_name())
        blobs = client.list_blobs(bucket, prefix=prefix)

        reports = []
        for b in blobs:
            if b.name.endswith(".pdf"):
                filename = Path(b.name).name
                reports.append({
                    "filename": filename,
                    "blob_name": b.name,
                    "size_bytes": b.size,
                    "updated_at": b.updated.isoformat() if b.updated else None,
                    "gcs_uri": f"gs://{bucket.name}/{b.name}",
                })
        return reports
    except Exception as exc:
        logger.warning(f"Failed to list GCS reports with prefix '{prefix}': {exc}")
        return []


def sync_datasets_to_gcs(
    data_dir: Path,
    target_prefix: str = "datasets/",
) -> List[str]:
    """Upload core F1 seed CSV files to a GCS bucket under target_prefix."""
    if not is_gcs_enabled():
        raise RuntimeError("GCS is not enabled. Set GCS_BUCKET_NAME to use sync.")

    uploaded = []
    client = get_storage_client()
    if client is None:
        raise RuntimeError("GCS client unavailable.")

    bucket = client.bucket(get_gcs_bucket_name())

    # Find all CSV files in data_dir
    csv_files = list(data_dir.glob("*.csv"))
    for csv_file in csv_files:
        blob_name = f"{target_prefix}{csv_file.name}"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(str(csv_file), content_type="text/csv")
        logger.info(f"Synced '{csv_file.name}' to gs://{bucket.name}/{blob_name}")
        uploaded.append(blob_name)

    return uploaded

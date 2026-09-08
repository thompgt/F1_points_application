"""Sync and manage Formula 1 assets in Google Cloud Storage (GCS).

Allows checking bucket access, syncing generated PDF reports, uploading dataset CSVs,
and listing stored simulation reports.

Usage:
    python scripts/sync_gcs.py --help
    python scripts/sync_gcs.py --check-bucket
    python scripts/sync_gcs.py --upload-reports
    python scripts/sync_gcs.py --upload-datasets
    python scripts/sync_gcs.py --list-reports
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")
sys.path.insert(0, str(REPO_ROOT))

import gcs_storage  # noqa: E402


def check_bucket_access(bucket_name: str, project_id: str | None = None) -> bool:
    """Verify bucket existence and accessibility."""
    if not gcs_storage.GCS_AVAILABLE:
        print("ERROR: google-cloud-storage is not installed.", file=sys.stderr)
        return False

    client = gcs_storage.get_storage_client()
    if client is None:
        print("ERROR: Could not create Storage client. Check GCP credentials.", file=sys.stderr)
        return False

    try:
        bucket = client.get_bucket(bucket_name)
        print(f"Bucket 'gs://{bucket.name}' exists in location '{bucket.location}'.")
        print(f"Storage class: {bucket.storage_class}. Access verified!")
        return True
    except Exception as exc:
        print(f"ERROR: Cannot access bucket '{bucket_name}': {exc}", file=sys.stderr)
        return False


def upload_local_reports(exports_dir: Path, dry_run: bool = False) -> int:
    """Upload all PDF files in exports_dir to GCS."""
    if not exports_dir.exists():
        print(f"Exports directory '{exports_dir}' does not exist. Nothing to upload.")
        return 0

    pdf_files = list(exports_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDF reports found in '{exports_dir}'.")
        return 0

    print(f"Found {len(pdf_files)} report(s) in '{exports_dir}':")
    count = 0
    for pdf in pdf_files:
        size_kb = round(pdf.stat().st_size / 1024, 1)
        if dry_run:
            print(f"  [DRY-RUN] Would upload '{pdf.name}' ({size_kb} KB)")
            count += 1
        else:
            blob = gcs_storage.upload_report(str(pdf))
            if blob:
                print(f"  Uploaded '{pdf.name}' -> gs://{gcs_storage.get_gcs_bucket_name()}/{blob}")
                count += 1
            else:
                print(f"  Failed to upload '{pdf.name}'", file=sys.stderr)
    return count


def main():
    parser = argparse.ArgumentParser(description="Google Cloud Storage sync utility for F1 points application.")
    parser.add_argument("--bucket", default=os.getenv("GCS_BUCKET_NAME"), help="Target GCS bucket name")
    parser.add_argument("--project", default=os.getenv("GCP_PROJECT_ID"), help="GCP project ID")
    parser.add_argument("--check-bucket", action="store_true", help="Test bucket existence and permissions")
    parser.add_argument("--upload-reports", action="store_true", help="Upload local exports/*.pdf to GCS")
    parser.add_argument("--upload-datasets", action="store_true", help="Upload local CSV datasets to GCS")
    parser.add_argument("--list-reports", action="store_true", help="List all PDF simulation reports in GCS")
    parser.add_argument("--dry-run", action="store_true", help="Simulate actions without performing network requests")

    args = parser.parse_args()

    bucket = args.bucket
    if not bucket and not args.dry_run:
        print("ERROR: GCS bucket name not provided. Set GCS_BUCKET_NAME or pass --bucket.", file=sys.stderr)
        sys.exit(1)

    if args.check_bucket:
        success = check_bucket_access(bucket, args.project)
        sys.exit(0 if success else 1)

    if args.list_reports:
        reports = gcs_storage.list_stored_reports()
        if not reports:
            print("No stored simulation reports found in GCS.")
        else:
            print(f"Found {len(reports)} stored report(s) in GCS:")
            for r in reports:
                size_kb = round((r.get("size_bytes") or 0) / 1024, 1)
                print(f"  - {r['filename']} ({size_kb} KB) [Updated: {r.get('updated_at')}]")

    if args.upload_reports:
        exports_dir = REPO_ROOT / "exports"
        uploaded = upload_local_reports(exports_dir, dry_run=args.dry_run)
        print(f"Report sync finished: {uploaded} file(s) processed.")

    if args.upload_datasets:
        if args.dry_run:
            csvs = list(REPO_ROOT.glob("*.csv"))
            print(f"[DRY-RUN] Would upload {len(csvs)} CSV files to gs://{bucket}/datasets/:")
            for c in csvs:
                print(f"  - {c.name} ({round(c.stat().st_size/1024, 1)} KB)")
        else:
            uploaded = gcs_storage.sync_datasets_to_gcs(REPO_ROOT)
            print(f"Dataset sync finished: {len(uploaded)} file(s) uploaded.")


if __name__ == "__main__":
    main()

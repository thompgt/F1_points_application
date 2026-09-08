# Google Cloud Deployment & Activation Checklist

This checklist details the steps required to activate live **Google Cloud BigQuery**, **Google Cloud Storage (GCS)**, and **Cloud Run** continuous deployment for the Formula 1 Points Application.

> [!NOTE]
> **Zero-Configuration Fallback:** No cloud setup is required for local development or automated testing. When GCP variables are unset, the application automatically falls back to local CSV datasets and the `./exports/` directory.

---

## 1. Prerequisites & GCP CLI Authentication

Run these commands once in your local terminal to authenticate and set up your project:

```bash
# 1. Log in with Application Default Credentials (ADC)
gcloud auth application-default login

# 2. Set your default GCP Project ID
gcloud config set project <YOUR_GCP_PROJECT_ID>

# 3. Enable the required Google Cloud APIs
gcloud services enable \
  bigquery.googleapis.com \
  storage.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com
```

---

## 2. Seed the BigQuery Warehouse

Create the clustered BigQuery tables and load the 75+ years of historical Formula 1 data:

```bash
# Optional: Validate schemas and row counts locally without cloud requests
python scripts/seed_bigquery.py --dry-run

# Seed tables into BigQuery (automatically creates the dataset if it doesn't exist)
python scripts/seed_bigquery.py --project <YOUR_GCP_PROJECT_ID> --dataset f1_points
```

Tables created and clustered:
* `results` (clustered by `[raceId, driverId, constructorId]`)
* `races` (clustered by `[year, circuitId]`)
* `drivers` (clustered by `[driverId]`)
* `seasons` (clustered by `[year]`)
* `constructors` (clustered by `[constructorId]`)
* `driver_standings` (clustered by `[raceId, driverId]`)

---

## 3. Create the Google Cloud Storage (GCS) Bucket

Create a dedicated GCS bucket to persist generated simulation PDF reports and serve time-limited Signed URLs:

```bash
# Create the storage bucket in your desired region
gcloud storage buckets create gs://<YOUR_UNIQUE_BUCKET_NAME> --location=us-central1

# Verify accessibility and permissions using the sync utility
python scripts/sync_gcs.py --bucket <YOUR_UNIQUE_BUCKET_NAME> --check-bucket

# (Optional) Upload any existing local simulation reports to GCS
python scripts/sync_gcs.py --bucket <YOUR_UNIQUE_BUCKET_NAME> --upload-reports

# (Optional) Back up raw CSV datasets to gs://<YOUR_UNIQUE_BUCKET_NAME>/datasets/
python scripts/sync_gcs.py --bucket <YOUR_UNIQUE_BUCKET_NAME> --upload-datasets
```

---

## 4. Local Environment Configuration

Add the cloud variables to your local `.env` file:

```ini
# Google Cloud Platform & BigQuery
GCP_PROJECT_ID=<YOUR_GCP_PROJECT_ID>
GCP_REGION=us-central1
BIGQUERY_DATASET=f1_points

# Google Cloud Storage (GCS)
GCS_BUCKET_NAME=<YOUR_UNIQUE_BUCKET_NAME>
GCS_SIGNED_URL_EXPIRATION_MINUTES=60
```

Start the application:
```bash
python main.py
```
Verify the startup logs output:
```
Loaded F1 datasets from Google Cloud BigQuery dataset 'f1_points'
FastMCP SSE server mounted at /mcp (SSE: /mcp/sse, messages: /mcp/messages)
```

---

## 5. Automated Cloud Run Deployment (GitHub Actions)

Continuous deployment is handled by `.github/workflows/cd.yml` whenever code is pushed to `main` and passes all tests in `ci.yml`.

### A. Create the Artifact Registry Repository
```bash
gcloud artifacts repositories create f1-points \
  --repository-format=docker \
  --location=us-central1 \
  --description="F1 Points Application Docker images"
```

### B. Create Deployer Service Account & Assign Roles
```bash
# Create service account for GitHub Actions deployment
gcloud iam service-accounts create f1-points-deployer \
  --description="Service account for GitHub Actions Cloud Run deployments"

# Assign Cloud Run Admin role
gcloud projects add-iam-policy-binding <YOUR_GCP_PROJECT_ID> \
  --member="serviceAccount:f1-points-deployer@<YOUR_GCP_PROJECT_ID>.iam.gserviceaccount.com" \
  --role="roles/run.admin"

# Assign Artifact Registry Writer role
gcloud projects add-iam-policy-binding <YOUR_GCP_PROJECT_ID> \
  --member="serviceAccount:f1-points-deployer@<YOUR_GCP_PROJECT_ID>.iam.gserviceaccount.com" \
  --role="roles/artifactregistry.writer"

# Assign Service Account User role
gcloud projects add-iam-policy-binding <YOUR_GCP_PROJECT_ID> \
  --member="serviceAccount:f1-points-deployer@<YOUR_GCP_PROJECT_ID>.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser"

# Generate JSON key file for GitHub Actions secret
gcloud iam service-accounts keys create key.json \
  --iam-account=f1-points-deployer@<YOUR_GCP_PROJECT_ID>.iam.gserviceaccount.com
```

### C. Configure GitHub Secrets & Variables
In your GitHub repository under **Settings → Secrets and variables → Actions**:

* **Repository Secret**:
  * `GCP_SA_KEY`: The full contents of `key.json`. *(Delete the local `key.json` file immediately after pasting)*
* **Repository Variables**:
  * `GCP_PROJECT_ID`: Your GCP project ID.
  * `GCP_REGION`: Target region (e.g. `us-central1`).

### D. Grant Cloud Run Access to BigQuery & GCS
Cloud Run instances execute using the default Compute Engine service account (`<PROJECT_NUMBER>-compute@developer.gserviceaccount.com`). Grant it permissions to query BigQuery and write/read from GCS:

```bash
# Allow Cloud Run to query BigQuery tables
gcloud projects add-iam-policy-binding <YOUR_GCP_PROJECT_ID> \
  --member="serviceAccount:<PROJECT_NUMBER>-compute@developer.gserviceaccount.com" \
  --role="roles/bigquery.dataViewer"

gcloud projects add-iam-policy-binding <YOUR_GCP_PROJECT_ID> \
  --member="serviceAccount:<PROJECT_NUMBER>-compute@developer.gserviceaccount.com" \
  --role="roles/bigquery.jobUser"

# Allow Cloud Run to manage reports in your GCS bucket
gcloud storage buckets add-iam-policy-binding gs://<YOUR_UNIQUE_BUCKET_NAME> \
  --member="serviceAccount:<PROJECT_NUMBER>-compute@developer.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

---

## 6. Post-Deployment Verification

After the service deploys to Cloud Run, verify the live endpoints:

1. **Health Check Probes**:
   ```bash
   curl https://<CLOUD_RUN_SERVICE_URL>/health
   curl https://<CLOUD_RUN_SERVICE_URL>/ready
   ```
2. **BigQuery Data Serving**:
   ```bash
   curl https://<CLOUD_RUN_SERVICE_URL>/api/seasons
   ```
3. **GCS Report Storage & Signed URLs**:
   ```bash
   # List available reports
   curl https://<CLOUD_RUN_SERVICE_URL>/api/reports

   # Get a v4 Signed URL for direct client download
   curl https://<CLOUD_RUN_SERVICE_URL>/api/reports/F1_Season_2021_Modern.pdf/url
   ```
4. **FastMCP Server Endpoints**:
   ```bash
   curl -N https://<CLOUD_RUN_SERVICE_URL>/mcp/sse
   ```

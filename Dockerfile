# Single-stage: the app is a plain FastAPI/uvicorn service with no compiled
# extensions, and the seed CSVs it ships (lap_times.csv is 18MB) are the
# actual runtime data in the documented DATABASE_URL="" fallback mode -- a
# multi-stage build here would only add complexity, not shrink the image.
FROM python:3.13-slim

WORKDIR /app

# Dependencies first so this layer only invalidates when requirements.txt
# changes, not on every source edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Runs as an unprivileged user -- Cloud Run doesn't require this, but there's
# no reason to run a public-facing container as root.
RUN useradd --create-home appuser
USER appuser

# Cloud Run injects $PORT (8080) and routes traffic to whatever the container
# listens on; hardcoding 8000 here would make the container fail to start.
# No DATABASE_URL is set, so the app takes the same seed-CSV fallback path
# that CI already exercises on every push -- no Cloud SQL instance to pay for.
ENV PORT=8080
EXPOSE 8080
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT}

"""
Health check and readiness endpoints for the F1 Points Calculator API.
Provides Kubernetes-compatible health probes.
"""

from datetime import datetime
from typing import Optional
import os

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import text

from db import SessionLocal, engine
from metrics import register_health_collector

# Try to import Redis client
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


# ============================================================================
# Response Models
# ============================================================================

class HealthStatus(BaseModel):
    """Health check response model."""
    status: str  # 'healthy', 'degraded', 'unhealthy'
    version: str
    environment: str
    timestamp: str


class ReadinessStatus(BaseModel):
    """Readiness check response model with dependency status."""
    status: str  # 'ready', 'not_ready'
    checks: dict
    timestamp: str


class LivenessStatus(BaseModel):
    """Liveness check response model."""
    status: str  # 'alive'
    uptime_seconds: float
    timestamp: str


class DetailedHealthStatus(BaseModel):
    """Detailed health status for monitoring systems."""
    status: str
    version: str
    environment: str
    database: dict
    cache: dict
    data_files: dict
    timestamp: str


# ============================================================================
# Health Check Functions
# ============================================================================

# Track application start time for uptime calculation
_start_time = datetime.utcnow()


def get_app_version() -> str:
    """Get application version from environment or default."""
    return os.getenv("APP_VERSION", "1.0.0")


def get_environment() -> str:
    """Get current environment name."""
    return os.getenv("ENVIRONMENT", "development")


def check_database() -> dict:
    """Check database connectivity and status."""
    try:
        db = SessionLocal()
        # Execute a simple query to verify connection
        db.execute(text("SELECT 1"))
        db.close()
        return {
            "status": "connected",
            # mysql / postgresql / sqlite -- report what's actually configured
            "type": engine.dialect.name,
            "healthy": True
        }
    except Exception as e:
        return {
            "status": "disconnected",
            "error": str(e)[:100],
            "healthy": False
        }


def check_redis() -> dict:
    """Check Redis cache connectivity."""
    if not REDIS_AVAILABLE:
        return {
            "status": "disabled",
            "healthy": True,
            "message": "Redis not installed"
        }
    
    redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    try:
        client = redis.Redis.from_url(
            redis_url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2
        )
        client.ping()
        info = client.info()
        return {
            "status": "connected",
            "healthy": True,
            "version": info.get("redis_version", "unknown"),
            "used_memory_human": info.get("used_memory_human", "unknown")
        }
    except redis.ConnectionError:
        return {
            "status": "disconnected",
            "healthy": False,
            "message": "Cannot connect to Redis"
        }
    except Exception as e:
        return {
            "status": "error",
            "healthy": False,
            "error": str(e)[:100]
        }


def check_data_files() -> dict:
    """Check if required CSV data files are accessible."""
    required_files = [
        'results.csv',
        'races.csv',
        'drivers.csv',
        'seasons.csv',
        'constructors.csv',
        'driver_standings.csv'
    ]
    
    missing_files = []
    accessible_files = []
    
    for filename in required_files:
        if os.path.isfile(filename):
            accessible_files.append(filename)
        else:
            missing_files.append(filename)
    
    return {
        "status": "complete" if not missing_files else "incomplete",
        "healthy": len(missing_files) == 0,
        "total_files": len(required_files),
        "accessible": len(accessible_files),
        "missing": missing_files if missing_files else None
    }


def calculate_uptime() -> float:
    """Calculate application uptime in seconds."""
    return (datetime.utcnow() - _start_time).total_seconds()


# ============================================================================
# Router
# ============================================================================

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthStatus)
async def health_check():
    """
    Basic health check endpoint.
    Returns 200 if the application is running.
    Used by load balancers and monitoring systems.
    """
    return HealthStatus(
        status="healthy",
        version=get_app_version(),
        environment=get_environment(),
        timestamp=datetime.utcnow().isoformat()
    )


@router.get("/ready", response_model=ReadinessStatus)
async def readiness_check(response: Response):
    """
    Readiness probe endpoint.
    Checks if the application is ready to accept traffic.
    Verifies database and cache connectivity.
    """
    db_status = check_database()
    redis_status = check_redis()
    data_status = check_data_files()

    # Application is ready if database and data files are healthy
    # Redis is optional (degraded mode acceptable)
    is_ready = db_status["healthy"] and data_status["healthy"]

    # Readiness probes are polled on status code, not just body -- a 200
    # here regardless of readiness defeats the point of the probe.
    response.status_code = 200 if is_ready else 503

    return ReadinessStatus(
        status="ready" if is_ready else "not_ready",
        checks={
            "database": db_status,
            "cache": redis_status,
            "data_files": data_status
        },
        timestamp=datetime.utcnow().isoformat()
    )


@router.get("/live", response_model=LivenessStatus)
async def liveness_check():
    """
    Liveness probe endpoint.
    Returns 200 if the application process is alive.
    Used by Kubernetes to determine if container should be restarted.
    """
    return LivenessStatus(
        status="alive",
        uptime_seconds=calculate_uptime(),
        timestamp=datetime.utcnow().isoformat()
    )


@router.get("/health/detailed", response_model=DetailedHealthStatus)
async def detailed_health_check():
    """
    Detailed health check with all dependency statuses.
    Useful for debugging and monitoring dashboards.
    """
    db_status = check_database()
    redis_status = check_redis()
    data_status = check_data_files()
    
    # Determine overall status
    if db_status["healthy"] and data_status["healthy"]:
        if redis_status["healthy"]:
            overall_status = "healthy"
        else:
            overall_status = "degraded"
    else:
        overall_status = "unhealthy"
    
    return DetailedHealthStatus(
        status=overall_status,
        version=get_app_version(),
        environment=get_environment(),
        database=db_status,
        cache=redis_status,
        data_files=data_status,
        timestamp=datetime.utcnow().isoformat()
    )


# ============================================================================
# Metrics
# ============================================================================
#
# This module used to hand-roll a GET /metrics that formatted four gauges into a
# string. That route is gone: prometheus-fastapi-instrumentator now owns /metrics
# (see main.py), and it would collide with anything registered here.
#
# The four series it published -- f1_api_up, f1_api_uptime_seconds,
# f1_api_database_healthy, f1_api_cache_healthy -- are preserved under the same
# names as real prometheus_client gauges, evaluated at scrape time by the check
# functions above rather than by a duplicate implementation. Registering here
# rather than in main.py keeps the checks and their exports in one file.

register_health_collector(
    check_database=check_database,
    check_redis=check_redis,
    check_data_files=check_data_files,
    calculate_uptime=calculate_uptime,
)

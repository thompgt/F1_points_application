"""
Prometheus metric definitions for the F1 Points Calculator.

Everything here registers into prometheus_client's default REGISTRY, which is the
same registry ``prometheus-fastapi-instrumentator`` exposes at ``/metrics`` (wired
up in main.py). Import this module for the metric objects; do not build a second
registry, or half the series will silently vanish from the scrape.

Cardinality rule: every label value in this module is drawn from a small, closed
set (a points-system name, an exception class, a cache outcome). Never label a
metric with a season year, driver id, race id, or client IP -- those are unbounded
and will blow up the time series database. Per-route HTTP metrics come from the
instrumentator, which labels by *route template* (``/api/race/{race_id}``) rather
than the concrete path, for the same reason.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Callable, Iterable, Optional, Sequence

from prometheus_client import Counter, Gauge, Histogram, REGISTRY
from prometheus_client.core import GaugeMetricFamily


# ============================================================================
# Points calculation
# ============================================================================

# The four systems the UI offers (/api/points-systems). Anything else a caller
# sends through the custom-points field collapses to "custom" so the label stays
# bounded no matter what array arrives in the request body.
_KNOWN_POINTS_SYSTEMS = {
    (25, 18, 15, 12, 10, 8, 6, 4, 2, 1): "modern",
    (10, 6, 4, 3, 2, 1): "classic",
    (9, 6, 4, 3, 2, 1): "pre-1991",
}


def classify_points_system(points_system: Optional[Sequence[float]]) -> str:
    """Map a points array onto a bounded label value."""
    if not points_system:
        return "modern"  # None means the endpoint falls back to DEFAULT_POINTS
    try:
        key = tuple(int(p) for p in points_system)
    except (TypeError, ValueError):
        return "custom"
    return _KNOWN_POINTS_SYSTEMS.get(key, "custom")


points_calculation_duration = Histogram(
    "f1_points_calculation_duration_seconds",
    "Time spent adjusting points and computing season standings.",
    labelnames=("points_system",),
    # A calculation is a few pandas merges over ~26k result rows: single-digit
    # to low-hundreds of milliseconds warm, seconds on a cold CSV load.
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

points_calculations_total = Counter(
    "f1_points_calculations_total",
    "Season standings calculations, by points system and outcome.",
    labelnames=("points_system", "outcome"),
)


@contextmanager
def observe_points_calculation(points_system: Optional[Sequence[float]]):
    """Time a standings calculation and record its outcome."""
    label = classify_points_system(points_system)
    start = time.perf_counter()
    outcome = "success"
    try:
        yield label
    except Exception:
        outcome = "error"
        raise
    finally:
        points_calculation_duration.labels(points_system=label).observe(
            time.perf_counter() - start
        )
        points_calculations_total.labels(points_system=label, outcome=outcome).inc()


# ============================================================================
# CSV data loading (load_data() is lru_cache'd; lap_times.csv alone is ~18MB)
# ============================================================================

data_load_cache_total = Counter(
    "f1_data_load_cache_total",
    "load_data() calls, split by whether the lru_cache served them.",
    labelnames=("result",),  # hit | miss
)

data_load_duration_seconds = Gauge(
    "f1_data_load_duration_seconds",
    "Wall-clock seconds spent in the most recent cache-missing CSV load.",
)

# Pre-create both children so a fresh process exports f1_data_load_cache_total{result="hit"}
# as 0 rather than omitting it -- otherwise the cache-hit-ratio panel reads as "No data"
# until the first hit lands.
data_load_cache_total.labels(result="hit")
data_load_cache_total.labels(result="miss")


def record_data_load(hit: bool, duration_seconds: Optional[float] = None) -> None:
    data_load_cache_total.labels(result="hit" if hit else "miss").inc()
    if not hit and duration_seconds is not None:
        data_load_duration_seconds.set(duration_seconds)


# ============================================================================
# Middleware: rate limiting and errors
# ============================================================================

rate_limit_rejections_total = Counter(
    "f1_rate_limit_rejections_total",
    "Requests rejected with 429 by RateLimitMiddleware, by which limit tripped.",
    labelnames=("limit",),  # burst | per_minute | per_hour
)

errors_total = Counter(
    "f1_errors_total",
    "Exceptions caught by ErrorHandlerMiddleware, by exception class and response status.",
    labelnames=("exception", "status_code"),
)


def record_error(exc: BaseException, status_code: int) -> None:
    errors_total.labels(
        exception=type(exc).__name__, status_code=str(status_code)
    ).inc()


# ============================================================================
# Ollama (season simulator)
# ============================================================================

ollama_calls_total = Counter(
    "f1_ollama_calls_total",
    "Calls to the local Ollama server for AI season summaries.",
    labelnames=("outcome",),  # success | http_error | empty_response | exception
)

ollama_request_duration_seconds = Histogram(
    "f1_ollama_request_duration_seconds",
    "Latency of Ollama /api/generate calls.",
    # A local llama3.1:8b summary is tens of seconds, not milliseconds.
    buckets=(1, 5, 10, 20, 30, 60, 90, 120, 180),
)


# ============================================================================
# Health gauges (migrated from the hand-rolled /metrics that used to live in
# health.py). Same four series names, now backed by prometheus_client.
# ============================================================================

class _HealthCollector:
    """
    Exports the health gauges by calling health.py's existing check functions at
    scrape time.

    A custom collector rather than four plain Gauges because these values come
    from live I/O (a DB round trip, a Redis PING) and nothing else in the process
    would ever refresh them. Results are cached for ``ttl`` seconds so a tight
    scrape interval -- or several Prometheus instances -- cannot turn /metrics
    into a database load generator.
    """

    def __init__(self, ttl: float = 5.0):
        self.ttl = ttl
        self._checks: Optional[dict] = None
        self._cached: Optional[list] = None
        self._cached_at: float = 0.0

    def bind(
        self,
        *,
        check_database: Callable[[], dict],
        check_redis: Callable[[], dict],
        check_data_files: Callable[[], dict],
        calculate_uptime: Callable[[], float],
    ) -> None:
        self._checks = {
            "database": check_database,
            "redis": check_redis,
            "data_files": check_data_files,
            "uptime": calculate_uptime,
        }

    def _sample(self) -> list:
        c = self._checks
        assert c is not None

        def healthy(fn) -> float:
            try:
                return 1.0 if fn().get("healthy") else 0.0
            except Exception:
                return 0.0

        return [
            ("f1_api_up", "Whether the API process is serving requests.", 1.0),
            (
                "f1_api_uptime_seconds",
                "Application uptime in seconds.",
                float(c["uptime"]()),
            ),
            (
                "f1_api_database_healthy",
                "1 if the database answers a SELECT 1, else 0.",
                healthy(c["database"]),
            ),
            (
                "f1_api_cache_healthy",
                "1 if the Redis cache is reachable (or intentionally disabled), else 0.",
                healthy(c["redis"]),
            ),
            (
                "f1_api_data_files_healthy",
                "1 if every required CSV data file is present, else 0.",
                healthy(c["data_files"]),
            ),
        ]

    def collect(self) -> Iterable[GaugeMetricFamily]:
        if self._checks is None:
            return
        now = time.monotonic()
        if self._cached is None or (now - self._cached_at) > self.ttl:
            self._cached = self._sample()
            self._cached_at = now
        for name, doc, value in self._cached:
            yield GaugeMetricFamily(name, doc, value=value)


health_collector = _HealthCollector()
_health_collector_registered = False


def register_health_collector(
    *,
    check_database: Callable[[], dict],
    check_redis: Callable[[], dict],
    check_data_files: Callable[[], dict],
    calculate_uptime: Callable[[], float],
) -> None:
    """Wire health.py's check functions into the scrape path (idempotent)."""
    global _health_collector_registered
    health_collector.bind(
        check_database=check_database,
        check_redis=check_redis,
        check_data_files=check_data_files,
        calculate_uptime=calculate_uptime,
    )
    if not _health_collector_registered:
        REGISTRY.register(health_collector)
        _health_collector_registered = True

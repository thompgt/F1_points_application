"""
Middleware components for the F1 Points Calculator API.
Includes error handling, rate limiting, logging, and request tracking.
"""

import time
import ipaddress
import logging
import uuid
import contextvars
from datetime import datetime
from typing import Callable, Optional
from collections import defaultdict

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import ValidationError

from metrics import rate_limit_rejections_total, record_error

# ============================================================================
# Logging Configuration
# ============================================================================

def setup_logging(
    level: str = "INFO",
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] - %(message)s",
    date_format: str = "%Y-%m-%d %H:%M:%S"
) -> logging.Logger:
    """Configure application logging with structured format."""
    
    # Custom filter to add request_id context
    class RequestIdFilter(logging.Filter):
        def filter(self, record):
            if not hasattr(record, 'request_id'):
                record.request_id = 'no-request'
            return True

    # Get or create logger
    logger = logging.getLogger("f1_api")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    
    # Remove existing handlers
    logger.handlers.clear()
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(log_format, date_format))
    console_handler.addFilter(RequestIdFilter())
    logger.addHandler(console_handler)
    
    return logger


# Global logger instance
logger = setup_logging()


# ============================================================================
# Request Context
# ============================================================================

class RequestContext:
    """
    Per-request context storage using contextvars, which is correctly
    isolated across concurrently-running async tasks (unlike a shared
    class-level dict, which would let concurrent requests clobber each
    other's request_id).
    """
    _request_id: contextvars.ContextVar = contextvars.ContextVar('request_id', default='unknown')

    @classmethod
    def set(cls, key: str, value):
        if key == 'request_id':
            cls._request_id.set(value)

    @classmethod
    def get(cls, key: str, default=None):
        if key == 'request_id':
            return cls._request_id.get()
        return default

    @classmethod
    def clear(cls):
        cls._request_id.set('unknown')

    @classmethod
    def get_request_id(cls) -> str:
        return cls._request_id.get()


# ============================================================================
# Error Handling Middleware
# ============================================================================

class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """
    Centralized error handling middleware.
    Catches all exceptions and returns consistent error responses.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        try:
            response = await call_next(request)
            return response
        
        except ValidationError as e:
            # Pydantic validation errors
            logger.warning(f"Validation error: {e.errors()}", extra={'request_id': RequestContext.get_request_id()})
            record_error(e, 422)
            return JSONResponse(
                status_code=422,
                content={
                    "error": "Validation Error",
                    "detail": e.errors(),
                    "status_code": 422,
                    "path": str(request.url.path),
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
        
        except HTTPException as e:
            # FastAPI HTTP exceptions (pass through with additional context)
            logger.warning(f"HTTP exception: {e.status_code} - {e.detail}", extra={'request_id': RequestContext.get_request_id()})
            record_error(e, e.status_code)
            return JSONResponse(
                status_code=e.status_code,
                content={
                    "error": "HTTP Error",
                    "detail": e.detail,
                    "status_code": e.status_code,
                    "path": str(request.url.path),
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
        
        except ValueError as e:
            # Value errors (usually from validation)
            logger.warning(f"Value error: {str(e)}", extra={'request_id': RequestContext.get_request_id()})
            record_error(e, 400)
            return JSONResponse(
                status_code=400,
                content={
                    "error": "Bad Request",
                    "detail": str(e),
                    "status_code": 400,
                    "path": str(request.url.path),
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
        
        except Exception as e:
            # Unexpected errors - log full stack trace
            logger.exception(f"Unhandled exception: {str(e)}", extra={'request_id': RequestContext.get_request_id()})
            record_error(e, 500)
            return JSONResponse(
                status_code=500,
                content={
                    "error": "Internal Server Error",
                    "detail": "An unexpected error occurred. Please try again later.",
                    "status_code": 500,
                    "path": str(request.url.path),
                    "timestamp": datetime.utcnow().isoformat()
                }
            )


# ============================================================================
# Request Logging Middleware
# ============================================================================

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Logs all incoming requests with timing information.
    Adds correlation ID for request tracing.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Generate unique request ID
        request_id = str(uuid.uuid4())[:8]
        RequestContext.set('request_id', request_id)
        
        # Record start time
        start_time = time.time()
        
        # Log incoming request
        logger.info(
            f"Request: {request.method} {request.url.path}",
            extra={'request_id': request_id}
        )
        
        # Process request
        response = await call_next(request)
        
        # Calculate duration
        duration_ms = (time.time() - start_time) * 1000
        
        # Log response
        logger.info(
            f"Response: {response.status_code} - {duration_ms:.2f}ms",
            extra={'request_id': request_id}
        )
        
        # Add request ID to response headers
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{duration_ms:.2f}ms"
        
        # Clear context
        RequestContext.clear()
        
        return response


# ============================================================================
# Rate Limiting Middleware
# ============================================================================

def _parse_trusted_proxies(raw: Optional[str]):
    """Parse TRUSTED_PROXIES into networks. Empty means trust nothing.

    Accepts single addresses and CIDR blocks, comma-separated, e.g.
    "10.0.0.0/8, 127.0.0.1". Anything unparseable is dropped with a warning
    rather than silently widening or narrowing what gets trusted.
    """
    networks = []
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.warning("Ignoring unparseable TRUSTED_PROXIES entry: %r", entry)
    return networks


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Simple in-memory rate limiting middleware.

    Two caveats worth knowing before relying on this in production:

    * Counters are per-process. Run four uvicorn workers and the effective limit
      is four times what is configured. Moving the buckets into Redis would fix
      it; Redis is an optional dependency here, so this has not been done.
    * The limiter is only as trustworthy as TRUSTED_PROXIES. Deployed behind a
      load balancer with that unset, every request appears to come from the load
      balancer and shares one bucket.
    """

    def __init__(
        self,
        app: FastAPI,
        requests_per_minute: int = 60,
        requests_per_hour: int = 1000,
        burst_limit: int = 10,
        trusted_proxies: Optional[str] = None,
    ):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.requests_per_hour = requests_per_hour
        self.burst_limit = burst_limit
        self.trusted_proxies = _parse_trusted_proxies(trusted_proxies)

        # In-memory storage (use Redis in production)
        self.minute_requests: dict = defaultdict(list)
        self.hour_requests: dict = defaultdict(list)

    def _peer_is_trusted_proxy(self, host: str) -> bool:
        if not self.trusted_proxies:
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return False
        return any(address in network for network in self.trusted_proxies)

    def _get_client_id(self, request: Request) -> str:
        """Identify the client, trusting X-Forwarded-For only behind a known proxy.

        The header used to be honoured unconditionally, which makes the limiter
        decorative: any client sends a fresh value and gets a fresh bucket, so
        the thing meant to cap request rates could be stepped around with one
        header. Worse, a spoofed value is also the dictionary key, so an attacker
        could grow the two defaultdicts without bound.

        X-Forwarded-For is appended to by each hop, so the right-hand entries are
        the ones added by infrastructure you control. Walk it from the right,
        discard entries contributed by trusted proxies, and the first thing left
        is the furthest hop this deployment can actually vouch for. With no
        TRUSTED_PROXIES configured, nothing is trusted and only the real peer
        address is used.
        """
        peer = request.client.host if request.client else "unknown"
        if not self._peer_is_trusted_proxy(peer):
            return peer

        forwarded = request.headers.get("X-Forwarded-For", "")
        for candidate in reversed([entry.strip() for entry in forwarded.split(",") if entry.strip()]):
            if not self._peer_is_trusted_proxy(candidate):
                return candidate

        # Every hop in the chain was a trusted proxy, so there is no client
        # address to attribute this to beyond the proxy itself.
        return peer

    def _cleanup_old_requests(self, client_id: str, current_time: float):
        """Remove expired request timestamps."""
        minute_ago = current_time - 60
        hour_ago = current_time - 3600
        
        self.minute_requests[client_id] = [
            t for t in self.minute_requests[client_id] if t > minute_ago
        ]
        self.hour_requests[client_id] = [
            t for t in self.hour_requests[client_id] if t > hour_ago
        ]

    def _check_rate_limit(self, client_id: str) -> tuple[bool, Optional[str], Optional[int]]:
        """
        Check if request should be rate limited.
        Returns: (is_allowed, error_message, retry_after_seconds)
        """
        current_time = time.time()
        self._cleanup_old_requests(client_id, current_time)
        
        minute_count = len(self.minute_requests[client_id])
        hour_count = len(self.hour_requests[client_id])
        
        # Rejections are counted by *which* limit tripped, not by client -- a
        # per-client label would be attacker-controlled unbounded cardinality.

        # Check burst limit (requests in last second)
        recent = [t for t in self.minute_requests[client_id] if t > current_time - 1]
        if len(recent) >= self.burst_limit:
            rate_limit_rejections_total.labels(limit="burst").inc()
            return False, "Burst limit exceeded", 1

        # Check per-minute limit
        if minute_count >= self.requests_per_minute:
            oldest = min(self.minute_requests[client_id])
            retry_after = int(60 - (current_time - oldest)) + 1
            rate_limit_rejections_total.labels(limit="per_minute").inc()
            return False, "Rate limit exceeded (per minute)", retry_after

        # Check per-hour limit
        if hour_count >= self.requests_per_hour:
            oldest = min(self.hour_requests[client_id])
            retry_after = int(3600 - (current_time - oldest)) + 1
            rate_limit_rejections_total.labels(limit="per_hour").inc()
            return False, "Rate limit exceeded (per hour)", retry_after
        
        return True, None, None

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip rate limiting for health checks
        if request.url.path in ["/health", "/ready", "/api/health"]:
            return await call_next(request)
        
        client_id = self._get_client_id(request)
        current_time = time.time()
        
        is_allowed, error_msg, retry_after = self._check_rate_limit(client_id)
        
        if not is_allowed:
            logger.warning(
                f"Rate limit exceeded for {client_id}: {error_msg}",
                extra={'request_id': RequestContext.get_request_id()}
            )
            response = JSONResponse(
                status_code=429,
                content={
                    "error": "Too Many Requests",
                    "detail": error_msg,
                    "status_code": 429,
                    "retry_after": retry_after,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
            response.headers["Retry-After"] = str(retry_after)
            return response
        
        # Record request
        self.minute_requests[client_id].append(current_time)
        self.hour_requests[client_id].append(current_time)
        
        # Add rate limit headers to response
        response = await call_next(request)
        
        minute_remaining = self.requests_per_minute - len(self.minute_requests[client_id])
        response.headers["X-RateLimit-Limit"] = str(self.requests_per_minute)
        response.headers["X-RateLimit-Remaining"] = str(max(0, minute_remaining))
        response.headers["X-RateLimit-Reset"] = str(int(current_time) + 60)
        
        return response


# ============================================================================
# Security Headers Middleware
# ============================================================================

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        
        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # Content Security Policy (adjust as needed)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' cdn.jsdelivr.net cdn.plot.ly cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' cdn.jsdelivr.net cdnjs.cloudflare.com; "
            "img-src 'self' data: blob:; "
            "font-src 'self' cdnjs.cloudflare.com; "
            "connect-src 'self';"
        )
        
        return response


# ============================================================================
# Helper Functions
# ============================================================================

def add_middleware_stack(app: FastAPI, config: Optional[dict] = None):
    """
    Add all middleware to the FastAPI application.
    Order matters - middleware is executed in reverse order of addition.
    """
    config = config or {}
    
    # Add middleware in order (first added = last executed)
    
    # 1. Security headers (outermost)
    app.add_middleware(SecurityHeadersMiddleware)
    
    # 2. Rate limiting
    if config.get('enable_rate_limiting', True):
        app.add_middleware(
            RateLimitMiddleware,
            requests_per_minute=config.get('requests_per_minute', 60),
            requests_per_hour=config.get('requests_per_hour', 1000),
            burst_limit=config.get('burst_limit', 10),
            trusted_proxies=config.get('trusted_proxies'),
        )
    
    # 3. Request logging
    app.add_middleware(RequestLoggingMiddleware)
    
    # 4. Error handling (innermost - catches all errors)
    app.add_middleware(ErrorHandlerMiddleware)
    
    logger.info("Middleware stack initialized")


def get_logger() -> logging.Logger:
    """Get the configured logger instance."""
    return logger

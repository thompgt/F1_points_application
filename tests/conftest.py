import os

# The rate limiter's burst limit (10 req/sec) is easily tripped by a fast
# test suite hitting many endpoints back-to-back from the same TestClient
# host -- disable it for tests so we're testing app logic, not the limiter.
os.environ.setdefault("ENABLE_RATE_LIMITING", "false")

from prometheus_client import Counter, Histogram

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration",
    ["method", "endpoint", "status"],
)

REQUEST_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

CACHE_HITS = Counter("cache_hits_total", "Total cache hits")
CACHE_MISSES = Counter("cache_misses_total", "Total cache misses")

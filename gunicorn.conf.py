"""Gunicorn configuration for the API container."""

from __future__ import annotations

import multiprocessing
import os

bind = os.getenv("BIND", "0.0.0.0:8000")

# Uvicorn workers: the app is fully async, so concurrency comes from the event
# loop rather than the process count. Two per core is a reasonable default that
# still tolerates one worker blocking briefly.
workers = int(os.getenv("WEB_CONCURRENCY", str(min(multiprocessing.cpu_count() * 2, 8))))
worker_class = "uvicorn.workers.UvicornWorker"

# Long enough for SSE streams to stay open; the run itself is bounded elsewhere.
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
keepalive = 65

# Trust the reverse proxy in front of us for the client address and scheme.
forwarded_allow_ips = os.getenv("FORWARDED_ALLOW_IPS", "*")
proxy_protocol = False

max_requests = int(os.getenv("MAX_REQUESTS", "2000"))
max_requests_jitter = 200

accesslog = None  # access logging is handled by our own structured middleware
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()

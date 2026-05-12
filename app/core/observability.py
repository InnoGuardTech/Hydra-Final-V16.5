from __future__ import annotations

import time
from contextlib import contextmanager

import sentry_sdk
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from app.core.config_loader import get_settings

_METER_PREFIX = "hydra"

REQUEST_COUNTER = Counter(f"{_METER_PREFIX}_http_requests_total", "HTTP requests", ["method", "path", "status"])
REQUEST_ERRORS = Counter(f"{_METER_PREFIX}_http_errors_total", "HTTP request errors", ["method", "path"])
REQUEST_LATENCY = Histogram(f"{_METER_PREFIX}_http_request_duration_seconds", "Request latency", ["method", "path"])
ACTIVE_REQUESTS = Gauge(f"{_METER_PREFIX}_http_active_requests", "Active in-flight requests")

BOOKING_OPS = Counter(f"{_METER_PREFIX}_booking_operations_total", "Booking operations", ["operation", "status"])
QUEUE_OPS = Counter(f"{_METER_PREFIX}_queue_operations_total", "Queue operations", ["operation", "status"])
PROXY_OPS = Counter(f"{_METER_PREFIX}_proxy_operations_total", "Proxy operations", ["operation", "status"])
BROWSER_OPS = Counter(f"{_METER_PREFIX}_browser_operations_total", "Browser operations", ["operation", "status"])

_settings = get_settings()
TRACER = trace.get_tracer("hydra.observability")


def metrics_response() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


def observe_request(method: str, path: str, status: int, duration: float) -> None:
    labels = {"method": method, "path": path}
    REQUEST_COUNTER.labels(status=str(status), **labels).inc()
    REQUEST_LATENCY.labels(**labels).observe(duration)
    if status >= 500:
        REQUEST_ERRORS.labels(**labels).inc()


def mark_operation(metric_name: str, operation: str, status: str = "ok") -> None:
    metric_map = {
        "booking": BOOKING_OPS,
        "queue": QUEUE_OPS,
        "proxy": PROXY_OPS,
        "browser": BROWSER_OPS,
    }
    metric = metric_map.get(metric_name)
    if metric is not None:
        metric.labels(operation=operation, status=status).inc()


@contextmanager
def traced_operation(span_name: str, **attrs: str):
    with TRACER.start_as_current_span(span_name) as span:
        for key, value in attrs.items():
            span.set_attribute(key, value)
        start = time.perf_counter()
        try:
            yield span
        finally:
            span.set_attribute("duration_ms", (time.perf_counter() - start) * 1000)


def setup_tracing(app) -> bool:
    if not _settings.tracing_enabled:
        return False
    provider = TracerProvider(resource=Resource.create({"service.name": _settings.service_name}))
    if _settings.otlp_endpoint:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=_settings.otlp_endpoint)))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    return True


def setup_sentry() -> bool:
    if not _settings.sentry_enabled or not _settings.sentry_dsn:
        return False
    sentry_sdk.init(dsn=_settings.sentry_dsn, traces_sample_rate=0.1)
    return True


def capture_exception(exc: BaseException) -> None:
    if _settings.sentry_enabled and _settings.sentry_dsn:
        sentry_sdk.capture_exception(exc)

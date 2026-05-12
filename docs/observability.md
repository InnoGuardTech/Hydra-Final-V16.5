# Observability Guide

## Endpoints
- `GET /health`: lightweight runtime + observability status.
- `GET /metrics`: Prometheus exposition format.

## Metrics
The app exports:
- `hydra_http_requests_total`
- `hydra_http_errors_total`
- `hydra_http_request_duration_seconds`
- `hydra_http_active_requests`
- `hydra_booking_operations_total`
- `hydra_queue_operations_total`
- `hydra_proxy_operations_total`
- `hydra_browser_operations_total`

## Tracing
OpenTelemetry tracing is controlled by:
- `TRACING_ENABLED=true|false`
- `SERVICE_NAME=hydra-final`
- `OTLP_ENDPOINT=http://otel-collector:4318/v1/traces`

When enabled, FastAPI requests are instrumented and spans can be created in services via `traced_operation(...)`.

## Sentry
Controlled by:
- `SENTRY_ENABLED=true|false`
- `SENTRY_DSN=...`

If DSN is missing, startup continues safely.

## Suggested Prometheus scrape
```yaml
scrape_configs:
  - job_name: hydra
    metrics_path: /metrics
    static_configs:
      - targets: ["hydra:8080"]
```

## Grafana
- Add Prometheus as data source.
- Build dashboards around p95 latency and error rate by path.

## Troubleshooting
- Empty `/metrics`: ensure `METRICS_ENABLED=true` and app route is reachable.
- No traces: enable `TRACING_ENABLED` and set `OTLP_ENDPOINT`.
- Startup exceptions: verify `ADMIN_PASSWORD` and env vars.

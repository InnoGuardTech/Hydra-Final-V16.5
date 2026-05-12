from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware

from app.core.observability import ACTIVE_REQUESTS, capture_exception, observe_request


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        correlation_id = request.headers.get("x-correlation-id") or request_id
        request.state.request_id = request_id
        request.state.correlation_id = correlation_id

        start = time.perf_counter()
        ACTIVE_REQUESTS.inc()
        try:
            response = await call_next(request)
        except Exception as exc:
            observe_request(request.method, request.url.path, 500, time.perf_counter() - start)
            capture_exception(exc)
            raise
        finally:
            ACTIVE_REQUESTS.dec()

        duration = time.perf_counter() - start
        observe_request(request.method, request.url.path, response.status_code, duration)
        response.headers["x-request-id"] = request_id
        response.headers["x-correlation-id"] = correlation_id
        return response

from __future__ import annotations

from app.core.observability import capture_exception, mark_operation


async def capture_and_mark(exc: Exception, op: str) -> None:
    capture_exception(exc)
    mark_operation("browser", op, "error")

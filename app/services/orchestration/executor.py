from __future__ import annotations

from typing import Any

from app.core.observability import mark_operation, traced_operation
from app.services.orchestration.workers import WorkerPool


class BookingExecutor:
    def __init__(self, workers: WorkerPool) -> None:
        self._workers = workers

    async def execute(self, fn) -> Any:
        with traced_operation("orchestration.execute"):
            result = await self._workers.run(fn)
            mark_operation("booking", "execute", "ok")
            return result

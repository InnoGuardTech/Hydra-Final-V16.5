from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Awaitable, Callable

from app.core.observability import capture_exception, mark_operation, traced_operation
from app.services.orchestration.events import (
    BookingFailed,
    BookingQueued,
    BookingRetrying,
    BookingStarted,
    BookingSucceeded,
)
from app.services.orchestration.executor import BookingExecutor
from app.services.orchestration.pipeline import BookingPipeline
from app.services.orchestration.policies import RetryPolicy, classify_failure
from app.services.orchestration.scheduler import AsyncScheduler
from app.services.orchestration.state_machine import BookingState, BookingStateMachine
from app.services.orchestration.workers import WorkerPool
from app.services.stealth.manager import StealthManager

log = logging.getLogger("orchestrator")


class BookingOrchestrator:
    def __init__(self, concurrency: int = 4) -> None:
        self._stealth = StealthManager()
        self._events: list[Any] = []
        self._scheduler = AsyncScheduler()
        self._executor = BookingExecutor(WorkerPool(concurrency=concurrency))
        self._pipeline = BookingPipeline()
        self._policy = RetryPolicy()

    @property
    def events(self) -> list[Any]:
        return self._events

    async def run(self, booking_id: str, work: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        correlation_id = str(uuid.uuid4())
        machine = BookingStateMachine()
        self._events.append(BookingStarted(booking_id, correlation_id, "BookingStarted"))
        self._events.append(BookingQueued(booking_id, correlation_id, "BookingQueued"))
        with traced_operation("orchestration.run", booking_id=booking_id, correlation_id=correlation_id):
            for attempt in range(1, self._policy.max_retries + 1):
                try:
                    async with self._stealth.contexts.lease() as _ctx:
                        result = await self._scheduler.schedule(
                            lambda: self._executor.execute(lambda: self._pipeline.run(machine, work))
                        )
                    machine.transition(BookingState.SUCCESS)
                    self._events.append(BookingSucceeded(booking_id, correlation_id, "BookingSucceeded", result))
                    mark_operation("booking", "orchestration", "ok")
                    return result
                except Exception as exc:
                    capture_exception(exc)
                    failure_type = classify_failure(exc)
                    self._events.append(BookingRetrying(booking_id, correlation_id, "BookingRetrying", {"attempt": attempt, "failure": failure_type}))
                    if attempt >= self._policy.max_retries:
                        machine.transition(BookingState.FAILED)
                        self._events.append(BookingFailed(booking_id, correlation_id, "BookingFailed", {"failure": failure_type}))
                        mark_operation("booking", "orchestration", "error")
                        raise
                    machine.transition(BookingState.RETRYING)
                    await asyncio.sleep(self._policy.backoff(attempt))

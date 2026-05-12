from __future__ import annotations

from app.core.observability import mark_operation, traced_operation
from app.services.orchestration.state_machine import BookingState


class BookingPipeline:
    steps = [
        BookingState.QUEUED,
        BookingState.STEALTH_INIT,
        BookingState.PROCESSING,
        BookingState.WAITING_QUEUE,
    ]

    async def run(self, machine, handler) -> dict:
        with traced_operation("orchestration.pipeline"):
            for step in self.steps:
                machine.transition(step)
                mark_operation("booking", f"state_{step.value.lower()}", "ok")
            return await handler()

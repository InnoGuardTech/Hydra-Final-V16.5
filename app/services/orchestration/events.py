from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Any


@dataclass(slots=True)
class BookingEvent:
    booking_id: str
    correlation_id: str
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time)


class BookingStarted(BookingEvent):
    pass


class BookingQueued(BookingEvent):
    pass


class BookingProcessing(BookingEvent):
    pass


class BookingRetrying(BookingEvent):
    pass


class BookingFailed(BookingEvent):
    pass


class BookingSucceeded(BookingEvent):
    pass

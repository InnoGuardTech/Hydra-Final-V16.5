from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from time import time


class BookingState(str, Enum):
    INIT = "INIT"
    QUEUED = "QUEUED"
    STEALTH_INIT = "STEALTH_INIT"
    PROCESSING = "PROCESSING"
    WAITING_QUEUE = "WAITING_QUEUE"
    RETRYING = "RETRYING"
    FAILED = "FAILED"
    SUCCESS = "SUCCESS"


@dataclass(slots=True)
class StateTransition:
    state: BookingState
    ts: float = field(default_factory=time)


@dataclass(slots=True)
class BookingStateMachine:
    state: BookingState = BookingState.INIT
    history: list[StateTransition] = field(default_factory=lambda: [StateTransition(BookingState.INIT)])

    def transition(self, new_state: BookingState) -> None:
        self.state = new_state
        self.history.append(StateTransition(new_state))

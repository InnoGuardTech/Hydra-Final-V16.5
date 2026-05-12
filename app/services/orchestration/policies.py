from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 0.5

    def backoff(self, attempt: int) -> float:
        return self.base_delay * (2 ** max(0, attempt - 1))


def classify_failure(exc: Exception) -> str:
    msg = str(exc).lower()
    if "proxy" in msg:
        return "proxy_failure"
    if "timeout" in msg or "network" in msg:
        return "network_failure"
    if "browser" in msg or "stealth" in msg:
        return "stealth_failure"
    if "reject" in msg:
        return "booking_rejection"
    return "transient_error"

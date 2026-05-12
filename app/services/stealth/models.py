from __future__ import annotations

from dataclasses import dataclass, field
from time import time


@dataclass(slots=True)
class BrowserLease:
    backend: str
    context_id: str
    created_at: float = field(default_factory=time)

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


async def retry_async(fn: Callable[[], Awaitable[T]], attempts: int = 3, delay: float = 0.25) -> T:
    last_err: Exception | None = None
    for idx in range(attempts):
        try:
            return await fn()
        except Exception as exc:
            last_err = exc
            if idx < attempts - 1:
                await asyncio.sleep(delay * (idx + 1))
    assert last_err is not None
    raise last_err

import pytest

from app.services.stealth.retry import retry_async


@pytest.mark.asyncio
async def test_retry_async_recovers():
    state = {"n": 0}

    async def _fn():
        state["n"] += 1
        if state["n"] < 2:
            raise RuntimeError("boom")
        return "ok"

    assert await retry_async(_fn, attempts=3) == "ok"

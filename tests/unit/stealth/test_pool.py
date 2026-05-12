import pytest

from app.services.stealth.browser_pool import BrowserPool


class _Ctx:
    async def close(self):
        return None


class _Backend:
    name = "fake"
    async def start(self):
        return None
    async def stop(self):
        return None
    async def new_context(self, **kwargs):
        return _Ctx()


@pytest.mark.asyncio
async def test_pool_acquire_release():
    pool = BrowserPool(_Backend(), max_contexts=1)
    c1 = await pool.acquire()
    await pool.release(c1)
    c2 = await pool.acquire()
    assert c2 is c1

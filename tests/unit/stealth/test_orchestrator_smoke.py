import pytest

from app.services.orchestration.orchestrator import BookingOrchestrator


@pytest.mark.asyncio
async def test_orchestrator_success(monkeypatch):
    orch = BookingOrchestrator(concurrency=1)

    class _Dummy:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(orch._stealth.contexts, "lease", lambda: _Dummy())

    async def work():
        return {"ok": True}

    result = await orch.run("b1", work)
    assert result["ok"] is True

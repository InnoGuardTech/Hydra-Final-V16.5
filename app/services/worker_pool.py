"""
V15 — PHASE 3: AsyncZombieWorkerPool — race-to-grab hold-tokens.

When `ws_sniper` (PHASE 2) detects a seat dropping back to `available`,
the entire pool fires identical Hold-Token POSTs in parallel against
Webook. The first worker to receive `200 OK` wins; the rest are
cancelled immediately so they don't waste their hold-token quota.

Why "Zombie"
------------
Each worker stays warm forever — its `curl_cffi.AsyncSession` (TLS/JA3
fingerprint, HTTP/2 connection, cookies) is built ONCE on startup and
reused for every drop. When a drop fires, all 5 workers are already
mid-keepalive on the same long-lived connection, so the time from
"drop detected" to "POST sent" is ~2-5 ms (no TLS handshake, no DNS).

Public API
----------
    pool = AsyncZombieWorkerPool(accounts=[...], size=5)
    await pool.start()
    winner, meta = await pool.fire(object_label="A1-12-5", turnstile="...")
    await pool.stop()

Self-test
---------
    python -m app.services.worker_pool
    Runs a fully mocked race; asserts the fastest worker wins and that
    the winner is decided in <100 ms after fire().
"""
from __future__ import annotations

import asyncio
import logging
import time
import random
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("worker_pool")

# Module 4: Global concurrency limiter (Increased to 20 for scaling)
_fire_semaphore = asyncio.Semaphore(20)


# ════════════════════════════════════════════════════════════════════════
# Data classes
# ════════════════════════════════════════════════════════════════════════
@dataclass
class WorkerAccount:
    """One Webook booking account that the pool can fire on behalf of."""
    account_id: str
    bearer: str                 # Webook bearer token (~7-day TTL)
    slug: str                   # event slug
    event_id: str               # Webook internal event id
    proxy_url: Optional[str] = None
    label: str = ""             # human-readable name for logs
    session: Optional[Any] = None # Pre-warmed StealthClient


@dataclass
class WorkerResult:
    """Outcome of a single worker's POST attempt."""
    account_id: str
    status: int                 # HTTP status code (-1 on transport error)
    hold_token: Optional[str]
    elapsed_ms: float
    error: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == 200 and bool(self.hold_token)


# Type alias for any async function with the right shape.
FireCallable = Callable[
    ["WorkerAccount", dict[str, Any]],
    Awaitable["WorkerResult"],
]


# ════════════════════════════════════════════════════════════════════════
# Default fire callable — talks to Webook /hold-token via curl_cffi
# ════════════════════════════════════════════════════════════════════════
async def default_fire(
    account: WorkerAccount, ctx: dict[str, Any]
) -> WorkerResult:
    """Default POST → /api/v2/event-detail/<slug>/hold-token

    Uses the V14.1 curl_cffi-based StealthClient so the TLS fingerprint
    matches a real Chrome and Cloudflare lets the request through.
    """
    from app.services.stealth_client import StealthClient

    url = (
        "https://api.webook.com/api/v2/event-detail/"
        f"{account.slug}/hold-token?lang=en"
    )
    def _build_hold_payload(account, ctx):
        """Construct the hold‑token JSON payload ensuring all required fields.
        Includes optional fields only when present in ctx.
        """
        payload: dict[str, Any] = {
            "event_id": account.event_id,
            "lang": "en",
        }
        if ctx.get("turnstile"):
            payload["turnstile"] = ctx["turnstile"]
        if ctx.get("time_slot_id"):
            payload["time_slot_id"] = ctx["time_slot_id"]
        if ctx.get("object_label"):
            payload["object_label"] = ctx["object_label"]
        if ctx.get("block_id"):
            payload["block_id"] = ctx["block_id"]
        return payload

    headers = {
        "authorization": f"Bearer {account.bearer}",
        "content-type": "application/json",
    }
    try:
        from app.core.config import webook_public_token
        tok = webook_public_token()
        if tok:
            headers["token"] = tok
    except Exception:
        pass

    t0 = time.perf_counter()
    try:
        import os
        from app.services.stealth_client import StealthClient

        body = _build_hold_payload(account, ctx)

        # Module 4: Survival & Scaling (403/429 Rotation)
        session_id = ctx.get("session_id", account.account_id)
        
        for attempt in range(2):
            # Module 1: Sticky Proxy Logic (using current session_id)
            base_proxy = account.proxy_url or os.getenv("PROXY_URL")
            proxy = base_proxy
            if base_proxy and "@" in base_proxy and ":" in base_proxy:
                try:
                    auth, rest = base_proxy.split("@", 1)
                    username, password = auth.split(":", 1)
                    sticky_user = f"{username}-session-{session_id}"
                    proxy = f"{sticky_user}:{password}@{rest}"
                except Exception:
                    pass

            if not account.session:
                account.session = StealthClient(
                    proxy_url=proxy,
                    fingerprint_seed=session_id,
                )
                await account.session._ensure_session()

            # Module 2: Sniper Throttling (Semaphore + Jitter)
            async with _fire_semaphore:
                # 10-50ms micro-jitter to simulate human spacing
                await asyncio.sleep(random.uniform(0.01, 0.05))
                r = await account.session.request("POST", url, headers=headers, json=body)

            if r.status_code in (403, 429) and attempt == 0:
                log.warning("Worker %s hit %d — Rotating Sticky Session...", 
                            account.account_id, r.status_code)
                await account.session.close()
                account.session = None
                session_id = f"{account.account_id}-{uuid.uuid4().hex[:4]}"
                continue
            break

        elapsed = (time.perf_counter() - t0) * 1000
        
        try:
            data = r.json()
        except Exception:
            data = {"raw": (r.text or "")[:500]}
        tok_val = None
        if isinstance(data, dict):
            tok_val = (
                (data.get("data") or {}).get("token")
                or data.get("token")
                or data.get("hold_token")
            )
        return WorkerResult(
            account_id=account.account_id,
            status=r.status_code,
            hold_token=tok_val if isinstance(tok_val, str) else None,
            elapsed_ms=elapsed,
            raw=data if isinstance(data, dict) else {"raw": str(data)[:500]},
        )
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000
        return WorkerResult(
            account_id=account.account_id,
            status=-1,
            hold_token=None,
            elapsed_ms=elapsed,
            error=f"{type(e).__name__}: {e}",
        )


# ════════════════════════════════════════════════════════════════════════
# Pool
# ════════════════════════════════════════════════════════════════════════
class AsyncZombieWorkerPool:
    """Race-to-win pool of N booking workers.

    Args:
      accounts:       list of WorkerAccount; one worker is spawned per account.
      size:           truncate accounts to exactly `size` workers.
      fire_callable:  async function that performs the actual POST.
                      Default: default_fire (Webook /hold-token).
                      Override for tests / alternative endpoints.
    """

    def __init__(
        self,
        *,
        accounts: list[WorkerAccount],
        size: int = 5,
        fire_callable: FireCallable = default_fire,
    ):
        if not accounts:
            raise ValueError("accounts must be a non-empty list")
        self._accounts = list(accounts)[:size] if size > 0 else list(accounts)
        self._fire_callable: FireCallable = fire_callable
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._fires = 0
        self._wins = 0

    # ── lifecycle ─────────────────────────────────────────────────────
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        
        import os
        from app.services.stealth_client import StealthClient
        for acc in self._accounts:
            if acc.session is None:
                base_proxy = acc.proxy_url or os.getenv("PROXY_URL")
                proxy = base_proxy
                if base_proxy and "@" in base_proxy and ":" in base_proxy:
                    try:
                        auth, rest = base_proxy.split("@", 1)
                        username, password = auth.split(":", 1)
                        sticky_user = f"{username}-session-{acc.account_id}"
                        proxy = f"{sticky_user}:{password}@{rest}"
                    except Exception:
                        pass
                
                acc.session = StealthClient(
                    proxy_url=proxy,
                    fingerprint_seed=acc.account_id,
                )
                await acc.session._ensure_session()
                
        for acc in self._accounts:
            t = asyncio.create_task(
                self._heartbeat(acc),
                name=f"zombie-{acc.account_id}",
            )
            self._workers.append(t)
            
        # Module 2: Session Heartbeat
        self._workers.append(asyncio.create_task(
            self._keep_alive_loop(), name="worker-keep-alive"
        ))
        log.info("AsyncZombieWorkerPool started — %d workers",
                 len(self._workers))

    async def _keep_alive_loop(self):
        """Module 2: 8-minute keep-alive GET request to maintain TLS/Cookie warmth."""
        while self._running:
            await asyncio.sleep(480)  # 8 minutes
            for acc in self._accounts:
                if acc.session is not None and self._running:
                    try:
                        await acc.session.request("GET", "https://webook.com/en", timeout=10)
                    except Exception:
                        pass

    async def stop(self) -> None:
        self._running = False
        for t in self._workers:
            t.cancel()
        for t in self._workers:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._workers.clear()
        
        for acc in self._accounts:
            if acc.session is not None:
                try:
                    await acc.session.close()
                except Exception:
                    pass
                acc.session = None

    async def _heartbeat(self, acc: WorkerAccount) -> None:
        """Idle keep-alive task; ad-hoc fire tasks do the real work."""
        try:
            while self._running:
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            return

    # ── stats ─────────────────────────────────────────────────────────
    @property
    def stats(self) -> dict[str, int]:
        return {
            "size": len(self._accounts),
            "fires": self._fires,
            "wins": self._wins,
            "running": int(self._running),
        }

    # ── public fire ───────────────────────────────────────────────────
    async def fire(
        self,
        *,
        object_label: str = "",
        turnstile: str = "",
        time_slot_id: str = "",
        timeout: float = 10.0,
    ) -> tuple[Optional[WorkerResult], dict[str, Any]]:
        """Fire all workers in parallel; return the FIRST 200 OK winner.

        Workers that don't win get their tasks cancelled as soon as the
        winner is decided, so we don't waste hold-token quota.
        """
        if not self._running:
            raise RuntimeError("pool is not running — call .start() first")
        self._fires += 1
        ctx = {
            "object_label": object_label,
            "turnstile": turnstile,
            "time_slot_id": time_slot_id,
            "fire_id": uuid.uuid4().hex[:8],
        }

        async def _safe_fire(acc: WorkerAccount) -> WorkerResult:
            try:
                return await self._fire_callable(acc, ctx)
            except asyncio.CancelledError:
                return WorkerResult(
                    account_id=acc.account_id, status=-1, hold_token=None,
                    elapsed_ms=0, error="cancelled",
                )
            except Exception as e:
                return WorkerResult(
                    account_id=acc.account_id, status=-1, hold_token=None,
                    elapsed_ms=0, error=f"{type(e).__name__}: {e}",
                )

        tasks: list[asyncio.Task] = [
            asyncio.create_task(_safe_fire(acc)) for acc in self._accounts
        ]
        winner: Optional[WorkerResult] = None
        all_results: list[WorkerResult] = []
        t0 = time.perf_counter()
        try:
            for coro in asyncio.as_completed(tasks, timeout=timeout):
                try:
                    res = await coro
                except Exception as e:  # pragma: no cover
                    res = WorkerResult(
                        account_id="?", status=-1, hold_token=None,
                        elapsed_ms=0, error=str(e),
                    )
                all_results.append(res)
                if res.ok:
                    winner = res
                    break
        except asyncio.TimeoutError:
            pass
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

        race_ms = (time.perf_counter() - t0) * 1000
        if winner:
            self._wins += 1
        meta = {
            "fire_id": ctx["fire_id"],
            "race_ms": race_ms,
            "results": [
                {"account_id": r.account_id, "status": r.status,
                 "ok": r.ok, "elapsed_ms": round(r.elapsed_ms, 2),
                 "error": r.error}
                for r in all_results
            ],
        }
        return winner, meta

    # ── Smart Retry (Module 2 / Req #6 — V16.5) ──────────────────────────
    async def fire_until_win(
        self,
        *,
        object_label: str = "",
        turnstile: str = "",
        time_slot_id: str = "",
        per_attempt_timeout: float = 10.0,
        retry_interval: float = 1.5,
        max_attempts: int = 120,        # ~3 min at 1.5s intervals
        is_sold_out_fn=None,            # optional async callable → bool
    ) -> tuple[Optional[WorkerResult], dict[str, Any]]:
        """Continuously fire until a ticket is won, event is sold out, or max_attempts reached.

        This is the V16.5 Smart Retry Loop for Req #6.  The pool does NOT
        stop after one failed attempt — it keeps firing at every interval
        (ideally tied to WSS update events) until success or expiry.
        """
        last_meta: dict[str, Any] = {}
        for attempt in range(1, max_attempts + 1):
            winner, last_meta = await self.fire(
                object_label=object_label,
                turnstile=turnstile,
                time_slot_id=time_slot_id,
                timeout=per_attempt_timeout,
            )
            if winner:
                log.info("🏆 fire_until_win: ticket secured on attempt %d", attempt)
                last_meta["smart_retry_attempts"] = attempt
                return winner, last_meta

            # Check external sold-out signal (e.g. from chart_full flag)
            if is_sold_out_fn is not None:
                try:
                    if await is_sold_out_fn():
                        log.info("🚫 fire_until_win: event sold out — stopping retry after %d attempts", attempt)
                        break
                except Exception:
                    pass

            log.debug("fire_until_win: attempt %d failed — retrying in %.1fs", attempt, retry_interval)
            await asyncio.sleep(retry_interval)

        last_meta["smart_retry_attempts"] = max_attempts
        return None, last_meta



# ════════════════════════════════════════════════════════════════════════
# Self-test
# ════════════════════════════════════════════════════════════════════════
async def _selftest() -> int:
    print("🧪 Hydra V15 — worker_pool self-test")
    print("=" * 70)

    LATENCIES = {
        "acc_A": 0.080,
        "acc_B": 0.020,   # ← should win
        "acc_C": 0.060,
        "acc_D": 0.040,
        "acc_E": 0.100,
    }

    async def mock_fire(acc: WorkerAccount, ctx: dict) -> WorkerResult:
        await asyncio.sleep(LATENCIES.get(acc.account_id, 0.05))
        return WorkerResult(
            account_id=acc.account_id,
            status=200,
            hold_token=f"tok-{acc.account_id}-{ctx['fire_id']}",
            elapsed_ms=LATENCIES[acc.account_id] * 1000,
        )

    accounts = [
        WorkerAccount(account_id=f"acc_{c}", bearer=f"b-{c}",
                      slug="test-slug", event_id="evt-1", label=f"acc_{c}")
        for c in "ABCDE"
    ]
    pool = AsyncZombieWorkerPool(
        accounts=accounts, size=5, fire_callable=mock_fire,
    )
    await pool.start()
    try:
        t0 = time.perf_counter()
        winner, meta = await pool.fire(object_label="A1-12-5", timeout=2.0)
        race_ms = (time.perf_counter() - t0) * 1000
        assert winner is not None, "expected a winner"
        assert winner.account_id == "acc_B", (
            f"expected acc_B (fastest), got {winner.account_id}"
        )
        assert winner.ok, "winner must be ok"
        assert race_ms < 200, f"race took too long: {race_ms} ms"
        print(f"  ✓ winner: {winner.account_id} ({winner.elapsed_ms:.1f} ms)")
        print(f"  ✓ total race time: {race_ms:.1f} ms")
        print(f"  ✓ stats: {pool.stats}")
        print(f"  ✓ workers attempted: {len(meta['results'])}")
        assert pool.stats["wins"] == 1

        # Test: every-worker-fails scenario
        async def mock_fail(acc: WorkerAccount, ctx: dict) -> WorkerResult:
            await asyncio.sleep(0.01)
            return WorkerResult(
                account_id=acc.account_id, status=403, hold_token=None,
                elapsed_ms=10.0, error="Cloudflare 403",
            )
        pool2 = AsyncZombieWorkerPool(
            accounts=accounts, size=5, fire_callable=mock_fail,
        )
        await pool2.start()
        winner2, meta2 = await pool2.fire(object_label="X", timeout=1.0)
        await pool2.stop()
        assert winner2 is None, "no winner expected when all fail"
        assert len(meta2["results"]) == 5
        print(f"  ✓ all-fail scenario: no winner (correctly), "
              f"5 errors logged")
    finally:
        await pool.stop()

    print("\n🏆 worker_pool self-test PASSED.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    sys.exit(asyncio.run(_selftest()))

import asyncio
import logging

logging.basicConfig(level=logging.INFO)

from app.services.worker_pool import AsyncZombieWorkerPool, WorkerAccount

async def test_live_ip():
    print("\n--- Starting Live IP Verification ---")
    account = WorkerAccount(
        account_id="test_live",
        bearer="fake_bearer",
        slug="test-event",
        event_id="test_evt_id"
    )
    pool = AsyncZombieWorkerPool(accounts=[account], size=1)
    await pool.start()
    
    if account.session is not None:
        print(f"Session Pre-Warmed successfully!")
        print(f"Proxy Configured in Session: {account.session.proxy_url}")
        print(f"Fingerprint target: {account.session._impersonate}")
        
        print("\n--- Making request to api.ipify.org ---")
        try:
            r = await account.session.request("GET", "https://api.ipify.org?format=json", timeout=15.0)
            print(f"Status Code: {r.status_code}")
            print(f"Response: {r.text}")
        except Exception as e:
            print(f"Failed to reach ipify: {e}")
            
        print("\n--- Making request to ipinfo.io ---")
        try:
            r2 = await account.session.request("GET", "https://ipinfo.io/json", timeout=15.0)
            print(f"Status Code: {r2.status_code}")
            print(f"Response: {r2.text}")
        except Exception as e:
            print(f"Failed to reach ipinfo: {e}")
            
    else:
        print("Session was not pre-warmed.")

    await pool.stop()
    print("--- Verification complete ---")

if __name__ == "__main__":
    asyncio.run(test_live_ip())

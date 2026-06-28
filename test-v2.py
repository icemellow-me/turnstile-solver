#!/usr/bin/env python3
"""
Test script for Turnstile Solver v2.
Tests all solver endpoints and benchmarks against real Turnstile sites.
"""

import json
import sys
import time
import urllib.request
import urllib.parse

API_KEY = "8010000000ccojr5nrbg516w5jvw1wu9"
BASE_URL = "http://localhost:8878"

# Test sites with known Turnstile widgets
TEST_SITES = [
    {
        "name": "Turnstile Demo (Managed)",
        "url": "https://demo.turnstile.workers.dev",
        "sitekey": "0x4AAAAAAABJFP0y4bGzwqHT",
        "method": "turnstile",
    },
    {
        "name": "Turnstile Demo (Invisible)",
        "url": "https://demo.turnstile.workers.dev/?mode=invisible",
        "sitekey": "0x4AAAAAAABJFP0y4bGzwqHT",
        "method": "turnstile",
    },
]


def api_post(endpoint, data):
    """POST with form data."""
    encoded = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(f"{BASE_URL}{endpoint}", data=encoded)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode()
    except Exception as e:
        return f"ERROR: {e}"


def api_get(endpoint, params=None):
    """GET with query params."""
    url = f"{BASE_URL}{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return resp.read().decode()
    except Exception as e:
        return f"ERROR: {e}"


def test_health():
    print("=== Health Check ===")
    result = api_get("/health")
    try:
        data = json.loads(result)
        print(f"  Status: {data.get('status')}")
        print(f"  Version: {data.get('version')}")
        print(f"  Engines: {data.get('engines')}")
        print(f"  Queue: {data.get('queue')}")
        print(f"  Solved: {data.get('solved')}")
        return data.get('status') == 'ok'
    except:
        print(f"  Raw: {result}")
        return False


def test_solve(site):
    print(f"\n--- Testing: {site['name']} ---")
    print(f"  URL: {site['url']}")

    start = time.time()

    # Submit task
    data = {
        "key": API_KEY,
        "method": site["method"],
        "sitekey": site["sitekey"],
        "pageurl": site["url"],
    }
    result = api_post("/in.php", data)
    print(f"  Submit: {result}")

    if not result.startswith("OK|"):
        print(f"  ❌ Submit failed")
        return None

    task_id = result.split("|", 1)[1]
    print(f"  Task ID: {task_id}")

    # Poll for result
    for i in range(60):  # 5 min timeout
        time.sleep(5)
        elapsed = time.time() - start

        poll_result = api_get("/res.php", {
            "key": API_KEY,
            "action": "get",
            "id": task_id,
        })

        if poll_result.startswith("OK|"):
            token = poll_result.split("|", 1)[1]
            print(f"  ✅ SOLVED in {elapsed:.1f}s")
            print(f"  Token: {token[:60]}...")
            return {
                "success": True,
                "time": elapsed,
                "token_length": len(token),
            }
        elif poll_result == "CAPCHA_NOT_READY":
            if i % 6 == 0:  # Print every 30s
                print(f"  ... waiting ({elapsed:.0f}s)")
        else:
            print(f"  ❌ Failed: {poll_result}")
            return {
                "success": False,
                "time": elapsed,
                "error": poll_result,
            }

    print(f"  ❌ Timeout after {time.time()-start:.0f}s")
    return {"success": False, "time": time.time()-start, "error": "timeout"}


def main():
    print("Turnstile Solver v2 — Test Suite")
    print("=" * 50)

    # Health check
    if not test_health():
        print("\n❌ Health check failed!")
        sys.exit(1)

    # Test each site
    results = []
    for site in TEST_SITES:
        result = test_solve(site)
        if result:
            result["name"] = site["name"]
            results.append(result)

    # Summary
    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    for r in results:
        status = "✅" if r.get("success") else "❌"
        print(f"  {status} {r['name']}: {r.get('time', 0):.1f}s")
    
    total = len(results)
    passed = sum(1 for r in results if r.get("success"))
    print(f"\n  {passed}/{total} tests passed")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

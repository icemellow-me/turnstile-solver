# Turnstile Solver V2

Self-hosted **Cloudflare Turnstile** solver with a **2captcha-compatible API** — now powered by **nodriver** (primary) and **camoufox** (fallback) for maximum stealth and reliability.

> V1 (Playwright-based) is still available as `solver-server.py` but is deprecated. V2 (`solver-server-v2.py`) is faster, more reliable, and harder to fingerprint.

---

## ✨ What's New in V2

- **nodriver** engine — CDP-based, no Playwright fingerprint, solves in ~5-10s
- **camoufox** fallback — Firefox-based stealth browser with anti-fingerprinting
- **`json=1` support** — structured JSON responses for Chrome extension integration
- **Dual-engine architecture** — tries nodriver first, falls back to camoufox automatically
- **Simplified token extraction** — single-line JS evaluations for maximum compatibility
- **Auto-retry** — each engine retries up to 30 attempts before failing

---

## Architecture

```
                    ┌──────────────────────────┐
                    │   Turnstile Solver V2    │
                    │   (solver-server-v2.py)   │
                    └──────────┬───────────────┘
                               │
                    ┌──────────▼───────────────┐
                    │  Task Queue + Scheduler  │
                    └──────┬───────────┬───────┘
                           │           │
                 ┌─────────▼──┐   ┌────▼────────┐
                 │  nodriver   │   │  camoufox   │
                 │  (primary)  │   │  (fallback) │
                 │  Chromium   │   │  Firefox     │
                 │  CDP-based  │   │  Anti-fp     │
                 └──────┬──────┘   └──────┬──────┘
                        │                  │
                        └──────┬───────────┘
                               ▼
                        Turnstile Token
```

### How It Works

1. **Submit** a Turnstile task via the 2captcha-compatible API (`POST /in.php`)
2. The server queues the task and assigns it to the **nodriver** engine
3. nodriver launches Chromium via CDP, navigates to the target page with the Turnstile widget
4. It extracts the token (typically in 5-10 seconds for non-interactive challenges)
5. If nodriver fails after max attempts, **camoufox** takes over as a fallback
6. camoufox launches a Firefox-based stealth browser and retries
7. **Poll** for the result via `GET /res.php`

---

## Quick Start

### Option 1: Docker (Recommended)

The easiest way — everything is containerized with browser dependencies pre-installed.

```bash
# Build the image
docker build -f Dockerfile.v2 -t turnstile-solver-v2 .

# Run on port 8878 (original instance, plain-text responses)
docker run -d \
  --name turnstile-solver-v2 \
  --restart unless-stopped \
  -p 8878:8878 \
  turnstile-solver-v2 \
  python3 /app/solver-server-v2.py --api-key YOUR_KEY --port 8878

# Run on port 8822 (extension-specific instance, json=1 support)
docker run -d \
  --name captcha-ext-turnstile \
  --restart unless-stopped \
  -p 8822:8822 \
  turnstile-solver-v2 \
  python3 /app/solver-server-v2.py --api-key YOUR_KEY --port 8822
```

### Option 2: Manual Install

```bash
# Install Python dependencies
pip install -r requirements.txt

# Install Chromium (Debian/Ubuntu)
sudo apt-get install -y chromium chromium-driver

# Or on macOS
brew install chromium

# Run the solver
python3 solver-server-v2.py --api-key YOUR_KEY --port 8878
```

### Requirements

- **Python 3.11+**
- **Chromium** browser (`/usr/bin/chromium` on Linux, or set via env)
- **nodriver** — `pip install nodriver`
- **camoufox** — `pip install camoufox` (also downloads Firefox browser on first run)

---

## API Reference

The solver exposes a **2captcha-compatible API** — drop-in replacement for any existing 2captcha client or library.

### Submit a Task

```
POST /in.php
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `key` | ✅ | Your API key |
| `method` | ✅ | Must be `turnstile` |
| `sitekey` | ✅ | The Turnstile sitekey from the target page |
| `pageurl` | ✅ | The full URL of the page containing the Turnstile widget |
| `json` | ⬜ | Set to `1` for JSON response format |

**Plain-text response:**
```
OK|a1b2c3d4e5f6...
```

**JSON response (with `json=1`):**
```json
{"status": 1, "request": "a1b2c3d4e5f6..."}
```

**Error responses:**
```
ERROR_WRONG_KEY           — Invalid API key
ERROR_WRONG_PARAMETER     — Missing required field
```

### Poll for Result

```
GET /res.php?key=YOUR_KEY&id=TASK_ID
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `key` | ✅ | Your API key |
| `id` | ✅ | Task ID returned from `/in.php` |
| `json` | ⬜ | Set to `1` for JSON response format |

**Responses:**

| Response | Meaning |
|----------|---------|
| `CAPCHA_NOT_READY` | Still solving, poll again in 2-5s |
| `OK\|TOKEN_HERE` | Solved! Token after the pipe |
| `ERROR_CAPTCHA_UNSOLVABLE` | Failed after all retry attempts |

**JSON format (with `json=1`):**
```json
// Still processing
{"status": 0, "request": "CAPCHA_NOT_READY"}

// Success
{"status": 1, "request": "0.XXXXX.DUMMY.TOKEN.XXXX..."}

// Error
{"status": 0, "request": "ERROR_CAPTCHA_UNSOLVABLE"}
```

### Health Check

```
GET /health
```

```json
{
  "status": "ok",
  "version": "2.0",
  "queue": 0,
  "solved": 15,
  "active": 1,
  "engines": ["nodriver", "camoufox"]
}
```

### Direct Solve (POST /solve)

For non-2captcha clients, you can also POST a JSON payload:

```bash
curl -X POST http://localhost:8878/solve \
  -H "Content-Type: application/json" \
  -d '{
    "key": "YOUR_KEY",
    "method": "turnstile",
    "sitekey": "0x4AAAAAAABJFP0y4bGzwqHT",
    "pageurl": "https://demo.turnstile.workers.dev"
  }'
```

This blocks until solved (up to 120s) and returns:
```json
{"status": "solved", "solution": "0.XXXXX.TOKEN.XXXX..."}
```

---

## Usage Examples

### curl (plain-text mode)

```bash
# Step 1: Submit the task
TASK_ID=$(curl -s -X POST http://YOUR_SERVER:8878/in.php \
  -d "key=YOUR_KEY" \
  -d "method=turnstile" \
  -d "sitekey=0x4AAAAAAABJFP0y4bGzwqHT" \
  -d "pageurl=https://demo.turnstile.workers.dev" \
  | cut -d'|' -f2)

echo "Task ID: $TASK_ID"

# Step 2: Poll for result (every 3 seconds)
while true; do
  RESULT=$(curl -s "http://YOUR_SERVER:8878/res.php?key=YOUR_KEY&id=$TASK_ID")
  if echo "$RESULT" | grep -q "OK|"; then
    TOKEN=$(echo "$RESULT" | cut -d'|' -f2)
    echo "Solved! Token: $TOKEN"
    break
  fi
  echo "Waiting..."
  sleep 3
done
```

### curl (JSON mode)

```bash
# Step 1: Submit
SUBMIT=$(curl -s -X POST http://YOUR_SERVER:8822/in.php \
  -d "key=YOUR_KEY" \
  -d "method=turnstile" \
  -d "sitekey=0x4AAAAAAABJFP0y4bGzwqHT" \
  -d "pageurl=https://demo.turnstile.workers.dev" \
  -d "json=1")
TASK_ID=$(echo "$SUBMIT" | jq -r '.request')
echo "Task ID: $TASK_ID"

# Step 2: Poll
while true; do
  RESULT=$(curl -s "http://YOUR_SERVER:8822/res.php?key=YOUR_KEY&id=$TASK_ID&json=1")
  STATUS=$(echo "$RESULT" | jq -r '.status')
  if [ "$STATUS" = "1" ]; then
    TOKEN=$(echo "$RESULT" | jq -r '.request')
    echo "Solved! Token: $TOKEN"
    break
  fi
  echo "Waiting..."
  sleep 3
done
```

### Python

```python
import urllib.request
import urllib.parse
import time
import json

SERVER = "http://YOUR_SERVER:8878"
API_KEY = "YOUR_KEY"

# Submit task
data = urllib.parse.urlencode({
    "key": API_KEY,
    "method": "turnstile",
    "sitekey": "0x4AAAAAAABJFP0y4bGzwqHT",
    "pageurl": "https://demo.turnstile.workers.dev",
}).encode()
resp = urllib.request.urlopen(f"{SERVER}/in.php", data)
task_id = resp.read().decode().split("|", 1)[1]
print(f"Task ID: {task_id}")

# Poll for result
start = time.time()
while time.time() - start < 120:
    url = f"{SERVER}/res.php?key={API_KEY}&id={task_id}"
    resp = urllib.request.urlopen(url)
    result = resp.read().decode()
    if result.startswith("OK|"):
        token = result.split("|", 1)[1]
        print(f"Solved in {time.time()-start:.1f}s! Token: {token[:30]}...")
        break
    time.sleep(3)
```

### Python with JSON mode

```python
import urllib.request
import urllib.parse
import time
import json

SERVER = "http://YOUR_SERVER:8822"
API_KEY = "YOUR_KEY"

# Submit task
data = urllib.parse.urlencode({
    "key": API_KEY,
    "method": "turnstile",
    "sitekey": "0x4AAAAAAABJFP0y4bGzwqHT",
    "pageurl": "https://demo.turnstile.workers.dev",
    "json": "1",
}).encode()
resp = urllib.request.urlopen(f"{SERVER}/in.php", data)
submit = json.loads(resp.read().decode())
task_id = submit["request"]
print(f"Task ID: {task_id}")

# Poll for result
start = time.time()
while time.time() - start < 120:
    url = f"{SERVER}/res.php?key={API_KEY}&id={task_id}&json=1"
    resp = urllib.request.urlopen(url)
    result = json.loads(resp.read().decode())
    if result["status"] == 1:
        token = result["request"]
        print(f"Solved in {time.time()-start:.1f}s! Token: {token[:30]}...")
        break
    time.sleep(3)
```

### Integrating with 2captcha Libraries

Since the API is 2captcha-compatible, you can use any existing 2captcha client library and just change the server URL:

```python
# Example with 2captcha-python
from twocaptcha import TwoCaptcha

solver = TwoCaptcha("YOUR_KEY")
# Override the API URL to your self-hosted solver
solver.API_URL = "http://YOUR_SERVER:8878"

result = solver.turnstile(
    sitekey="0x4AAAAAAABJFP0y4bGzwqHT",
    url="https://demo.turnstile.workers.dev"
)
print(f"Token: {result['code']}")
```

---

## Chrome Extension Integration

This solver works with the [captcha-solver-extension](https://github.com/icemellow-me/captcha-solver-extension). Two instances typically run side by side:

| Instance | Port | Response Format | Purpose |
|----------|------|----------------|---------|
| **Original** | 8878 | Plain-text (`OK\|id`) | Scripts, CLI tools, direct API calls |
| **Extension** | 8822 | JSON (`json=1`) | Chrome extension via Universal Solver (8844) |

The extension routes through a **Universal Solver** on port 8844, which forwards Turnstile tasks to port 8822. See the [extension README](https://github.com/icemellow-me/captcha-solver-extension) for setup details.

---

## Command-Line Options

```
python3 solver-server-v2.py [OPTIONS]

  --api-key KEY       API key for authentication (default: from SOLVER_API_KEY env)
  --port PORT         Server port (default: 8878)
  --max-sessions N    Max concurrent browser sessions (default: 2)
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SOLVER_API_KEY` | (none) | API key if not passed via `--api-key` |
| `CHROME_PATH` | `/usr/bin/chromium` | Path to Chromium binary |

---

## Supported Challenge Types

| Type | Description | nodriver | camoufox |
|------|-------------|----------|----------|
| **Non-interactive** | Auto-solves without user action | ✅ ~5-10s | ✅ ~5-10s |
| **Managed** | Cloudflare decides if interaction needed | ✅ | ✅ |
| **Invisible** | Runs in background, no UI | ✅ | ✅ |

---

## Engine Details

### nodriver (Primary)

- **Browser:** Chromium via Chrome DevTools Protocol (CDP)
- **Why primary:** Faster launch, lower resource usage, native CDP control
- **Stealth:** No Playwright/WebDriver fingerprint markers
- **Typical solve time:** 5-10 seconds

### camoufox (Fallback)

- **Browser:** Firefox with anti-fingerprinting patches
- **Why fallback:** Slower to launch but stronger anti-detection
- **Stealth:** Randomized canvas, WebGL, font, and audio fingerprints
- **Typical solve time:** 5-15 seconds after browser launch

---

## Docker Deployment

### Full Stack with Universal Solver

For production, pair with the [Universal Captcha Solver](https://github.com/icemellow-me/universal-captcha-solver) to handle reCAPTCHA, hCaptcha, and image captchas too:

```bash
# Turnstile V2 — original instance
docker run -d \
  --name turnstile-solver-v2 \
  --restart unless-stopped \
  -p 8878:8878 \
  turnstile-solver-v2 \
  python3 /app/solver-server-v2.py --api-key YOUR_KEY --port 8878

# Turnstile V2 — extension instance (json=1)
docker run -d \
  --name captcha-ext-turnstile \
  --restart unless-stopped \
  -p 8822:8822 \
  turnstile-solver-v2 \
  python3 /app/solver-server-v2.py --api-key YOUR_KEY --port 8822

# Universal Solver (forwards Turnstile to 8878)
docker run -d \
  --name universal-captcha-solver \
  --restart unless-stopped \
  -p 8855:8855 \
  -e TURNSTILE_SOLVER_URL=http://172.17.0.1:8878 \
  -e RECAPTCHA_SOLVER_URL=http://172.17.0.1:8866 \
  universal-captcha-solver
```

---

## Troubleshooting

### "nodriver failed to launch"
- Ensure Chromium is installed: `which chromium` or `which chromium-browser`
- Set `CHROME_PATH` env if Chromium is at a non-standard location
- Add `--no-sandbox` flag is needed (container environments)

### "camoufox download failed"
- On first run, camoufox downloads a patched Firefox binary (~80MB)
- Ensure the container has internet access during first startup
- Subsequent runs use the cached browser

### Token extraction returns empty
- Some Turnstile widgets use iframes — the solver handles this automatically
- If consistently failing, check that the `sitekey` and `pageurl` match the target site
- Demo tokens (e.g., `XXXX.DUMMY.TOKEN.XXXX`) are valid — they're how the demo site responds

### Slow solve times (>30s)
- First request is always slower (browser cold start)
- Increase `--max-sessions` for higher throughput (uses more RAM)
- nodriver is typically 2-5x faster than camoufox for non-interactive challenges

---

## V1 (Legacy)

The original Playwright-based solver (`solver-server.py`) is still available but deprecated. It uses:
- Playwright + headless Chrome
- CaptchaPlugin extension for managed challenges
- Single-engine (no fallback)

To use V1:
```bash
python3 solver-server.py --api-key YOUR_KEY --port 8877 --ext-path /path/to/captchaplugin
```

---

## License

MIT

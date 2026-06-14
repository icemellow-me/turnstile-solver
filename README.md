# Turnstile-Solver

Self-hosted Cloudflare Turnstile + JS Challenge solver with a 2captcha-compatible API.

Also includes a **FlareSolverr-compatible API** for drop-in replacement.

## Features
- **Non-interactive Turnstile** — solves automatically via headless Chrome
- **Cloudflare JS challenges** — waits for cf_clearance cookies
- **CaptchaPlugin extension support** — loads extension for managed challenges
- **System Chrome** — uses `/usr/bin/chromium` to avoid Playwright fingerprinting
- **Persistent browser context** — extension loads properly with `launch_persistent_context`
- **2captcha-compatible API** — drop-in replacement for any 2captcha client
- **FlareSolverr API** — POST /v1 for FlareSolverr-compatible requests

## Quick Start

```bash
# Install dependencies
pip install playwright aiohttp
playwright install chromium

# Or use install script
bash install-solvers.sh

# Run solver
python3 solver-server.py --api-key YOUR_KEY --port 8877

# Non-headless mode (better for managed challenges)
python3 solver-server.py --api-key YOUR_KEY --port 8877 --no-headless

# With CaptchaPlugin extension
python3 solver-server.py --api-key YOUR_KEY --port 8877 --ext-path /path/to/extension
```

## API Usage

### Submit task
```bash
curl -X POST http://localhost:8877/in.php \
  -d "method=turnstile" \
  -d "key=YOUR_KEY" \
  -d "sitekey=0x4AAAAAAA..." \
  -d "pageurl=https://example.com/page"
```

### Poll result
```bash
curl "http://localhost:8877/res.php?key=YOUR_KEY&id=TASK_ID"
```

### Health check
```bash
curl http://localhost:8877/health
```

## Supported Challenge Types
| Type | Sitekey Prefix | Status |
|------|---------------|--------|
| Non-interactive | `0x4AAAAAAAax...` | ✅ Working |
| Managed | `0x4AAAAAAAiw...` | ⚠️ Needs CaptchaPlugin extension |
| Invisible | varies | ✅ Working |

## Requirements
- Python 3.11+
- Chromium browser
- Xvfb (for non-headless mode on servers)
- aiohttp, playwright

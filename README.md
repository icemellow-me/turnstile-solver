# Turnstile-Solver

Self-hosted Cloudflare Turnstile + JS Challenge solver with a 2captcha-compatible API.

Also includes a **FlareSolverr-compatible API** for drop-in replacement.

## What It Solves

- ✅ **Cloudflare Turnstile** (interactive checkbox widgets)
- ✅ **Cloudflare Managed Challenges** (invisible/auto-resolving)
- ✅ **Cloudflare JS Challenges** ("Just a moment... / Checking your browser")
- ✅ Returns `cf_clearance` cookies and/or `cf-turnstile-response` tokens

## How It Works

1. Launches a **stealth Chromium** instance via Playwright
2. Injects anti-detection scripts (removes `navigator.webdriver`, fakes plugins, etc.)
3. Navigates to the target page
4. **Auto-detects** challenge type or uses the specified method
5. For Turnstile widgets: detects iframe → clicks checkbox → extracts token
6. For JS challenges: waits for auto-resolution → extracts `cf_clearance` cookie
7. Returns results via API

## Install

```bash
pip install playwright aiohttp
playwright install chromium
```

## Usage

```bash
# Start the solver server
python3 solver-server.py --api-key YOUR_KEY --port 8877
```

### With Xvfb (headless server)

```bash
Xvfb :100 -screen 0 1920x1080x24 &
export DISPLAY=:100
python3 solver-server.py --api-key YOUR_KEY --port 8877
```

## API

### 2captcha-compatible API

**Submit a Turnstile task:**
```
POST /in.php
key=YOUR_KEY&method=turnstile&sitekey=SITE_KEY&pageurl=https://example.com
```
Response: `OK|task_id`

**Submit a Cloudflare challenge:**
```
POST /in.php
key=YOUR_KEY&method=challenge&pageurl=https://protected-site.com
```

**Get result:**
```
GET /res.php?key=YOUR_KEY&action=get&id=task_id
```
- `CAPCHA_NOT_READY` — still processing
- `OK|token_value` — solved successfully
- `ERROR_CAPTCHA_UNSOLVABLE` — failed

### FlareSolverr-compatible API

```json
POST /v1
{
  "cmd": "request.get",
  "url": "https://protected-site.com",
  "maxTimeout": 60000
}
```

Response:
```json
{
  "status": "ok",
  "solution": {
    "url": "https://protected-site.com",
    "cookies": {"cf_clearance": "..."},
    "userAgent": "...",
    "html": "..."
  }
}
```

### Health Check

```
GET /health
```

## Architecture

```
solver-server.py     # Main server: HTTP API + Playwright automation
├── TurnstileSolver  # Browser automation engine
│   ├── _solve_turnstile()   # Interactive Turnstile widgets
│   ├── _solve_challenge()   # JS challenges (auto-wait)
│   ├── _solve_managed()     # Managed/invisible challenges
│   └── _solve_auto()        # Auto-detect challenge type
├── 2captcha API     # /in.php + /res.php
└── FlareSolverr API # /v1
```

## Detection Strategy

The solver auto-detects challenge type by examining the page:
- **"Just a moment"** title → JS challenge → wait for `cf_clearance`
- **`challenges.cloudflare.com` iframe** → Turnstile → click + extract token
- **No visible challenge** → Managed → wait for auto-resolution

## Anti-Detection

Built-in stealth measures:
- Removes `navigator.webdriver` flag
- Fakes plugin array (Chrome PDF Plugin, etc.)
- Overrides `navigator.languages`
- Adds `window.chrome.runtime`
- Uses realistic user agent and viewport
- Supports Firefox (`--browser firefox`) for better fingerprint evasion

## Comparison with FlareSolverr

| Feature | FlareSolverr | This Project |
|---------|-------------|--------------|
| JS Challenges | ✅ | ✅ |
| Turnstile Widgets | ❌ | ✅ |
| 2captcha API | ❌ | ✅ |
| FlareSolverr API | ✅ | ✅ |
| Auto-detect | ❌ | ✅ |
| Stealth Scripts | Basic | Advanced |

## TODO

- [ ] Add camoufox/nodriver integration for better fingerprint evasion
- [ ] Support Turnstile on sites that require interaction first
- [ ] Add proxy rotation support
- [ ] Add session persistence (reuse cookies)
- [ ] Support Cloudflare WAF challenges

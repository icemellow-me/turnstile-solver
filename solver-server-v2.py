#!/usr/bin/env python3
"""
Turnstile Solver v2 — Stealth-based Cloudflare Turnstile solver.

Uses multiple stealth strategies:
1. nodriver (CDP-based undetected Chrome) — primary
2. camoufox (hardened Firefox) — fallback
3. Playwright with full stealth injection — last resort

Key anti-detection patches:
- navigator.webdriver removal
- window.chrome object injection
- navigator.plugins/mock injection
- Permissions API masking
- WebGL/Canvas fingerprint normalization
- CDP artifact cleanup
- Realistic browser fingerprinting
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger('turnstile-solver-v2')

API_KEY=os.environ.get('SOLVER_API_KEY', '')


# ─── Stealth JS Injection Scripts ───

STEALTH_JS = """
() => {
    // 1. Remove navigator.webdriver
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
        configurable: true
    });

    // 2. Inject window.chrome object (only in Chromium)
    if (typeof window.chrome === 'undefined') {
        window.chrome = {
            app: {
                isInstalled: false,
                InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
                RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
                getDetails: function() { return null; },
                getIsInstalled: function() { return false; },
                installState: function() { return 'not_installed'; },
                launch: function() { return false; },
                runningState: function() { return 'cannot_run'; }
            },
            runtime: {
                OnInstalledReason: {
                    CHROME_UPDATE: 'chrome_update',
                    INSTALL: 'install',
                    SHARED_MODULE_UPDATE: 'shared_module_update',
                    UPDATE: 'update'
                },
                OnRestartRequiredReason: {
                    APP_UPDATE: 'app_update',
                    OS_UPDATE: 'os_update',
                    PERIODIC: 'periodic'
                },
                PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
                PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
                PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
                RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' },
                connect: function() { return { onMessage: { addListener: function(){} }, postMessage: function(){} }; },
                sendMessage: function() {}
            },
            csi: function() { return { startE: 0, onloadT: 0, pageT: 0, tran: 15 }; },
            loadTimes: function() { return { commitLoadTime: 0, connectionInfo: 'h2', finishDocumentLoadTime: 0, finishLoadTime: 0, firstPaintAfterLoadTime: 0, firstPaintTime: 0, navigationType: 'Other', npnNegotiated: 'h2', requestTime: 0, startLoadTime: 0, wasAlternateProtocolAvailable: false, wasFetchedViaSpdy: true, wasNpnNegotiated: true }; }
        };
    }

    // 3. Inject navigator.plugins (PDF viewer, etc.)
    const fakePlugins = [
        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format', mimeTypes: [{ type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format' }] },
        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '', mimeTypes: [{ type: 'application/pdf', suffixes: 'pdf', description: '' }] },
        { name: 'Native Client', filename: 'internal-nacl-plugin', description: '', mimeTypes: [{ type: 'application/x-nacl', suffixes: '', description: 'Native Client Executable' }, { type: 'application/x-pnacl', suffixes: '', description: 'Portable Native Client Executable' }] }
    ];

    const pluginArray = [];
    for (const fp of fakePlugins) {
        const plugin = Object.create(Plugin.prototype);
        Object.defineProperties(plugin, {
            name: { get: () => fp.name, enumerable: true },
            filename: { get: () => fp.filename, enumerable: true },
            description: { get: () => fp.description, enumerable: true },
            length: { get: () => fp.mimeTypes.length, enumerable: true }
        });
        pluginArray.push(plugin);
    }
    Object.defineProperty(navigator, 'plugins', {
        get: () => pluginArray,
        configurable: true
    });

    // 4. Fix Permissions API (webdriver leaks through notifications query)
    const origQuery = window.Permissions.prototype.query;
    window.Permissions.prototype.query = function(parameters) {
        if (parameters.name === 'notifications') {
            return Promise.resolve({ state: Notification.permission });
        }
        return origQuery.call(this, parameters);
    };

    // 5. Fix iframe contentWindow detection
    Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
        get: function() {
            return window;
        }
    });

    // 6. Fix connection speed (headless often shows 0)
    if (navigator.connection) {
        Object.defineProperty(navigator.connection, 'rtt', { get: () => 100 });
        Object.defineProperty(navigator.connection, 'downlink', { get: () => 10 });
        Object.defineProperty(navigator.connection, 'effectiveType', { get: () => '4g' });
    }

    // 7. Mask toString on native functions (prevents Proxy detection)
    const origToString = Function.prototype.toString;
    const nativeToString = origToString.bind(origToString);
    Function.prototype.toString = function() {
        if (this === window.Permissions.prototype.query) {
            return 'function query() { [native code] }';
        }
        return nativeToString(this);
    };

    // 8. Fix Chrome-specific objects
    if (!window.chrome.runtime) {
        window.chrome.runtime = {
            OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
            PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
            connect: function() { return {}; },
            sendMessage: function() {}
        };
    }

    // 9. Inject realistic languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
        configurable: true
    });

    // 10. Fix hardwareConcurrency (headless often reports fewer cores)
    Object.defineProperty(navigator, 'hardwareConcurrency', {
        get: () => 8,
        configurable: true
    });

    // 11. Fix deviceMemory
    Object.defineProperty(navigator, 'deviceMemory', {
        get: () => 8,
        configurable: true
    });
}
"""

STEALTH_JS_CDP = """
() => {
    // Additional CDP-specific patches
    // Remove __nightmare, _selenium, callSelenium, __webdriver_evaluate etc.
    delete window.__nightmare;
    delete window._selenium;
    delete window.callSelenium;
    delete window.__webdriver_evaluate;
    delete window.__selenium_evaluate;
    delete window.__webdriver_script_function;
    delete window.__webdriver_script_func;
    delete window.__driver_evaluate;
    delete window.__webdriver_unwrapped;
    delete window.__driver_unwrapped;
    delete window.__selenium_unwrapped;
    delete window.__fxdriver_evaluate;
    delete window.__fxdriver_unwrapped;

    // Remove document attributes that expose automation
    for (const key of Object.getOwnPropertyNames(document)) {
        if (key.startsWith('$') || key.startsWith('__wd')) {
            try { delete document[key]; } catch(e) {}
        }
    }
}
"""


# ─── Data Models ───

@dataclass
class TurnstileTask:
    task_id: str
    sitekey: str = ""
    pageurl: str = ""
    method: str = "turnstile"
    subtype: str = "turnstile"
    status: str = "pending"
    token: str = ""
    cookies: dict = field(default_factory=dict)
    html: str = ""
    user_agent: str = ""
    created_at: float = 0.0
    solved_at: float = 0.0
    error: str = ""


# ─── Solver Engine ───

class TurnstileSolverV2:
    """
    Stealth Turnstile solver using nodriver (CDP) as primary,
    camoufox as fallback, and patched Playwright as last resort.
    """

    def __init__(self, api_key: str, port: int = 8878, max_sessions: int = 2):
        self.api_key = api_key
        self.port = port
        self.max_sessions = max_sessions
        self.tasks: Dict[str, TurnstileTask] = {}
        self.active_sessions = 0
        self.solved_count = 0
        self._queue = asyncio.Queue()
        self._lock = asyncio.Lock()

    async def start(self):
        """Start solver workers."""
        for i in range(self.max_sessions):
            asyncio.create_task(self._solver_worker(i))
            log.info(f"Solver worker {i} started")

    async def _solver_worker(self, worker_id: int):
        """Worker coroutine processing tasks."""
        while True:
            task = await self._queue.get()
            try:
                self.active_sessions += 1
                task.status = "processing"
                log.info(f"Worker {worker_id}: solving {task.task_id} method={task.method}")

                result = await self._solve_with_nodriver(task)

                if not result:
                    log.info(f"Worker {worker_id}: nodriver failed, trying camoufox...")
                    result = await self._solve_with_camoufox(task)

                if result:
                    task.status = "solved"
                    task.solved_at = time.time()
                    self.solved_count += 1
                    log.info(f"Worker {worker_id}: SOLVED {task.task_id}")
                else:
                    task.status = "failed"
                    task.error = task.error or "All methods failed"
                    log.warning(f"Worker {worker_id}: FAILED {task.task_id}: {task.error}")

            except Exception as e:
                task.status = "failed"
                task.error = str(e)
                log.error(f"Worker {worker_id}: ERROR {task.task_id}: {e}")
            finally:
                self.active_sessions -= 1

    # ─── nodriver solver (CDP + undetected Chrome) ───

    async def _solve_with_nodriver(self, task: TurnstileTask) -> bool:
        """Solve using nodriver (undetected CDP browser)."""
        try:
            import nodriver as uc
        except ImportError:
            log.warning("nodriver not available, skipping")
            return False

        browser = None
        try:
            log.info(f"[nodriver] Launching undetected Chrome for {task.pageurl}")

            # nodriver needs a Config with browser_executable_path
            # The `uc.start()` utility doesn't propagate it, so use Browser.create directly
            chrome_path = os.environ.get('CHROME_PATH', '/usr/bin/chromium')
            # Use a random high port for CDP to avoid conflicts
            import random
            cdp_port = random.randint(9200, 9300)
            cfg = uc.Config(
                browser_executable_path=chrome_path,
                sandbox=False,
                headless=True,
                port=cdp_port,
                browser_args=[
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--window-size=1920,1080',
                    '--no-first-run',
                    '--no-default-browser-check',
                    '--disable-background-timer-throttling',
                    '--disable-backgrounding-occluded-windows',
                    '--disable-renderer-backgrounding',
                ],
            )
            browser = await uc.Browser.create(config=cfg, headless=True, sandbox=False)

            page = await browser.get(task.pageurl)
            log.info(f"[nodriver] Page loaded: {task.pageurl}")

            # Wait for page to settle
            await asyncio.sleep(3)

            # nodriver already patches navigator.webdriver to False
            # No need to inject stealth patches that might conflict
            log.info("[nodriver] Using built-in anti-detection (webdriver=False)")

            # Wait for Turnstile
            token = await self._wait_for_turnstile_nodriver(page, task)

            if token:
                task.token = token
                try:
                    task.html = await page.get_content()
                except:
                    pass
                try:
                    task.user_agent = await page.evaluate("navigator.userAgent")
                except:
                    pass
                return True
            else:
                task.error = "nodriver: Turnstile token not obtained"
                return False

        except Exception as e:
            log.error(f"[nodriver] Error: {e}")
            task.error = f"nodriver error: {e}"
            return False
        finally:
            if browser:
                try:
                    browser.stop()
                except:
                    pass

    async def _wait_for_turnstile_nodriver(self, page, task: TurnstileTask) -> Optional[str]:
        """Wait for Turnstile to solve on a nodriver page."""
        for attempt in range(30):
            await asyncio.sleep(2)

            try:
                # Use simple one-liner evaluates — nodriver handles these better
                # than complex multi-statement blocks
                token = await page.evaluate(
                    'document.querySelector("input[name=cf-turnstile-response]")?.value || ""'
                )
                if token and isinstance(token, str) and len(token) > 10:
                    log.info(f"[nodriver] Token found via input on attempt {attempt+1}: {token[:60]}...")
                    return token

                # Fallback: check window.turnstile API
                token = await page.evaluate(
                    'window.turnstile ? (window.turnstile.getResponse() || "") : ""'
                )
                if token and isinstance(token, str) and len(token) > 10:
                    log.info(f"[nodriver] Token found via API on attempt {attempt+1}: {token[:60]}...")
                    return token

                # No token yet, check if we need to interact
                # Use simple one-liners for nodriver compatibility
                has_iframe = await page.evaluate(
                    '!!document.querySelector("iframe[src*=challenges.cloudflare.com]")'
                )
                if has_iframe:
                    log.info(f"[nodriver] Turnstile iframe found (attempt {attempt+1}), auto-solve should work")
                else:
                    has_widget = await page.evaluate(
                        '!!document.querySelector(".cf-turnstile, [data-sitekey]")'
                    )
                    if has_widget:
                        log.info(f"[nodriver] Widget container found but no iframe yet (attempt {attempt+1})")

                # Check for JS challenge page
                title = await page.evaluate('document.title')
                if isinstance(title, str) and ('just a moment' in title.lower() or 'checking' in title.lower()):
                    log.info("[nodriver] JS challenge detected, waiting for auto-resolution...")
                    await asyncio.sleep(5)
                    # Check for cf_clearance cookie
                    token = await page.evaluate(
                        'document.cookie.split(";").find(c=>c.trim().startsWith("cf_clearance="))?.split("=")[1] || ""'
                    )
                    if token and isinstance(token, str) and len(token) > 10:
                        log.info(f"[nodriver] Got cf_clearance: {token[:40]}...")
                        return token

            except Exception as e:
                log.debug(f"[nodriver] Detection attempt {attempt+1} error: {e}")

        return None

    # ─── camoufox solver (hardened Firefox) ───

    async def _solve_with_camoufox(self, task: TurnstileTask) -> bool:
        """Solve using camoufox (hardened Firefox-based browser)."""
        try:
            from camoufox.sync_api import Camoufox as SyncCamoufox
            from camoufox.async_api import AsyncCamoufox
        except ImportError:
            log.warning("camoufox not available, skipping")
            return False

        try:
            log.info(f"[camoufox] Launching hardened Firefox for {task.pageurl}")

            async with AsyncCamoufox(headless=True) as browser:
                page = await browser.new_page()

                # Navigate
                try:
                    await page.goto(task.pageurl, wait_until='domcontentloaded', timeout=30000)
                except Exception as e:
                    log.warning(f"[camoufox] Navigation warning: {e}")

                log.info(f"[camoufox] Page loaded")
                await asyncio.sleep(3)

                # Wait for Turnstile to auto-solve (camoufox is stealthy enough)
                token = await self._wait_for_turnstile_camoufox(page, task)

                if token:
                    task.token = token
                    try:
                        task.html = await page.content()
                    except:
                        pass
                    return True
                else:
                    task.error = "camoufox: Turnstile token not obtained"
                    return False

        except Exception as e:
            log.error(f"[camoufox] Error: {e}")
            task.error = f"camoufox error: {e}"
            return False

    async def _wait_for_turnstile_camoufox(self, page, task: TurnstileTask) -> Optional[str]:
        """Wait for Turnstile on a camoufox page (uses Playwright API)."""
        from playwright.async_api import Page

        for attempt in range(30):
            await asyncio.sleep(2)

            try:
                # Extract token
                token = await page.evaluate("""
                    () => {
                        const inputs = document.querySelectorAll('input[name="cf-turnstile-response"]');
                        for (const input of inputs) {
                            if (input.value && input.value.length > 10) return input.value;
                        }
                        if (window.turnstile) {
                            try {
                                const resp = window.turnstile.getResponse();
                                if (resp && resp.length > 10) return resp;
                            } catch(e) {}
                        }
                        const widgets = document.querySelectorAll('[data-sitekey]');
                        for (const w of widgets) {
                            if (w.dataset.response && w.dataset.response.length > 10) return w.dataset.response;
                        }
                        return null;
                    }
                """)

                if token and len(token) > 10:
                    log.info(f"[camoufox] Token found on attempt {attempt+1}: {token[:60]}...")
                    return token

                # Try clicking iframe if present
                iframe = await page.query_selector('iframe[src*="challenges.cloudflare.com"]')
                if iframe:
                    log.info(f"[camoufox] Turnstile iframe found, clicking (attempt {attempt+1})")
                    try:
                        box = await iframe.bounding_box()
                        if box:
                            await page.mouse.click(box['x'] + 28, box['y'] + box['height'] / 2)
                            log.info("[camoufox] Clicked checkbox area")
                    except:
                        pass
                    # Wait for token after click
                    for w in range(15):
                        await asyncio.sleep(2)
                        token = await page.evaluate("""
                            () => {
                                const inputs = document.querySelectorAll('input[name="cf-turnstile-response"]');
                                for (const input of inputs) {
                                    if (input.value && input.value.length > 10) return input.value;
                                }
                                return null;
                            }
                        """)
                        if token and len(token) > 10:
                            return token
                    return None

                # Check JS challenge
                title = await page.title()
                if 'just a moment' in title.lower():
                    log.info("[camoufox] JS challenge detected, waiting...")
                    await asyncio.sleep(5)

            except Exception as e:
                log.debug(f"[camoufox] Attempt {attempt+1} error: {e}")

        return None

    # ─── Task Management ───

    def submit_task(self, method: str, sitekey: str, pageurl: str,
                    subtype: str = None, **kwargs) -> str:
        task_id = hashlib.md5(
            f"{sitekey}{pageurl}{time.time()}{uuid.uuid4()}".encode()
        ).hexdigest()[:24]

        if subtype is None:
            subtype = method if method in ("turnstile", "challenge", "managed") else "auto"

        task = TurnstileTask(
            task_id=task_id, sitekey=sitekey, pageurl=pageurl,
            method=method, subtype=subtype, status="pending",
            created_at=time.time(),
        )
        self.tasks[task_id] = task
        self._queue.put_nowait(task)
        log.info(f"Submitted task {task_id}: method={method} url={pageurl}")
        return task_id

    def get_task(self, task_id: str) -> Optional[TurnstileTask]:
        return self.tasks.get(task_id)


# ─── HTTP API ───

def create_app(solver: TurnstileSolverV2) -> web.Application:
    app = web.Application()

    async def in_php(request: web.Request):
        data = await request.post()
        if solver.api_key:
            key = data.get('key', '')
            if key != solver.api_key:
                return web.Response(text='ERROR_WRONG_KEY')
        method = data.get('method', 'turnstile')
        sitekey = data.get('googlekey', '') or data.get('sitekey', '')
        pageurl = data.get('pageurl', '')
        subtype = data.get('subtype', '') or None
        json_mode = str(data.get('json', '')) == '1' or request.query.get('json', '') == '1'
        if not pageurl:
            if json_mode:
                return web.json_response({'status': 0, 'request': 'ERROR_WRONG_PARAMETER'})
            return web.Response(text='ERROR_WRONG_PARAMETER')
        task_id = solver.submit_task(method=method, sitekey=sitekey, pageurl=pageurl, subtype=subtype)
        if json_mode:
            return web.json_response({'status': 1, 'request': task_id})
        return web.Response(text=f'OK|{task_id}')

    async def res_php(request: web.Request):
        json_mode = request.query.get('json', '') == '1'
        if solver.api_key:
            key = request.query.get('key', '')
            if key != solver.api_key:
                if json_mode:
                    return web.json_response({'status': 0, 'request': 'ERROR_WRONG_KEY'})
                return web.Response(text='ERROR_WRONG_KEY')
        task_id = request.query.get('id', '')
        task = solver.get_task(task_id)
        if not task:
            if json_mode:
                return web.json_response({'status': 0, 'request': 'ERROR_CAPTCHA_UNSOLVABLE'})
            return web.Response(text='ERROR_CAPTCHA_UNSOLVABLE')
        if task.status in ('pending', 'processing'):
            if json_mode:
                return web.json_response({'status': 0, 'request': 'CAPCHA_NOT_READY'})
            return web.Response(text='CAPCHA_NOT_READY')
        elif task.status == 'solved':
            if json_mode:
                return web.json_response({'status': 1, 'request': task.token})
            return web.Response(text=f'OK|{task.token}')
        else:
            if json_mode:
                return web.json_response({'status': 0, 'request': 'ERROR_CAPTCHA_UNSOLVABLE'})
            return web.Response(text='ERROR_CAPTCHA_UNSOLVABLE')

    async def health(request: web.Request):
        return web.json_response({
            'status': 'ok', 'version': '2.0',
            'queue': solver._queue.qsize(),
            'solved': solver.solved_count,
            'active': solver.active_sessions,
            'engines': ['nodriver', 'camoufox'],
        })

    app.router.add_post('/in.php', in_php)
    app.router.add_get('/res.php', res_php)
    app.router.add_get('/health', health)
    return app


# ─── Main ───

async def main():
    import argparse
    parser = argparse.ArgumentParser(description='Turnstile Solver v2 (Stealth)')
    parser.add_argument('--api-key', default=API_KEY, help='API key')
    parser.add_argument('--port', type=int, default=8878, help='Port (default: 8878)')
    parser.add_argument('--max-sessions', type=int, default=2, help='Max sessions')
    args = parser.parse_args()

    solver = TurnstileSolverV2(api_key=args.api_key, port=args.port, max_sessions=args.max_sessions)
    await solver.start()

    app = create_app(solver)
    log.info(f"Turnstile Solver v2 on port {args.port}")
    log.info(f"Engines: nodriver (primary) → camoufox (fallback)")
    log.info(f"API key: {args.api_key[:8]}...")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', args.port)
    await site.start()

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down...")

if __name__ == '__main__':
    asyncio.run(main())

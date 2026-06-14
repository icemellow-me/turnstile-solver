#!/usr/bin/python3
"""
Turnstile + Cloudflare Challenge Solver Server
2captcha-compatible API + FlareSolverr-style API

Handles:
- Cloudflare Turnstile (interactive + managed/invisible)
- Cloudflare JS challenges ("Checking your browser")
- Returns cf_clearance cookies and/or turnstile tokens

API endpoints:
  POST /in.php          - 2captcha-compatible submit
  GET  /res.php         - 2captcha-compatible poll
  POST /v1              - FlareSolverr-compatible API
  GET  /health          - Health check
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urlparse

from aiohttp import web

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger('turnstile-solver')

# ===== Data Models =====

@dataclass
class TurnstileTask:
    task_id: str
    sitekey: str = ""
    pageurl: str = ""
    method: str = "turnstile"
    subtype: str = "turnstile"  # turnstile, challenge, managed
    status: str = "pending"  # pending, processing, solved, failed
    token: str = ""
    cookies: dict = field(default_factory=dict)
    html: str = ""
    user_agent: str = ""
    created_at: float = 0.0
    solved_at: float = 0.0
    error: str = ""


# ===== Turnstile Solver Engine =====

class TurnstileSolver:
    """
    Solves Cloudflare Turnstile and JS challenges using Playwright.
    
    Strategy:
    1. Launch a stealth Chromium instance
    2. Navigate to the target page
    3. For Turnstile widgets: detect iframe → click checkbox → extract token
    4. For JS challenges: wait for challenge to resolve → extract cookies
    5. Return tokens/cookies via API
    """

    def __init__(self, api_key: str, port: int = 8877, max_sessions: int = 3,
                 headless: bool = True, browser_type: str = "chromium", ext_path: str = ""):
        self.api_key = api_key
        self.port = port
        self.max_sessions = max_sessions
        self.headless = headless
        self.ext_path = ext_path
        self.browser_type = browser_type
        self.tasks: Dict[str, TurnstileTask] = {}
        self.active_sessions = 0
        self.solved_count = 0
        self._playwright = None
        self._browser = None
        self._lock = asyncio.Lock()
        self._queue = asyncio.Queue()
        
    async def start(self):
        """Initialize browser pool and start solver loop."""
        from playwright.async_api import async_playwright
        import tempfile
        
        self._playwright = await async_playwright().start()
        
        # Use persistent context for extension support
        CAPTCHA_EXT_PATH = self.ext_path or os.environ.get('CAPTCHA_EXT_PATH', '/opt/recaptcha-v2-solver/extension')
        self._user_data_dir = tempfile.mkdtemp(prefix='turnstile-browser-')
        
        launch_args = [
            '--no-sandbox',
            '--disable-blink-features=AutomationControlled',
            '--disable-dev-shm-usage',
            '--disable-gpu',
            '--window-size=1920,1080',
            '--disable-infobars',
            '--disable-background-timer-throttling',
            '--disable-backgrounding-occluded-windows',
            '--disable-renderer-backgrounding',
            '--no-first-run',
            '--no-default-browser-check',
            f'--load-extension={CAPTCHA_EXT_PATH}',
            f'--disable-extensions-except={CAPTCHA_EXT_PATH}',
        ]
        
        if self.headless:
            launch_args.append('--headless=new')
        
        self._browser = await self._playwright.chromium.launch_persistent_context(
            self._user_data_dir,
            headless=self.headless,
            args=launch_args,
            executable_path='/usr/bin/chromium',
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36',
            locale='en-US',
            timezone_id='America/New_York',
            extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'},
        )
        # Wait for extension to initialize
        await asyncio.sleep(5)
        
        log.info(f"Browser launched: {self.browser_type} (headless={self.headless}), ext={CAPTCHA_EXT_PATH}")
        
        # Start solver workers
        for i in range(self.max_sessions):
            asyncio.create_task(self._solver_worker(i))
            log.info(f"Solver worker {i} started")

    async def stop(self):
        """Cleanup browser resources."""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _solver_worker(self, worker_id: int):
        """Worker coroutine that processes tasks from the queue."""
        while True:
            task = await self._queue.get()
            try:
                self.active_sessions += 1
                task.status = "processing"
                log.info(f"Worker {worker_id}: solving {task.task_id} "
                         f"method={task.method} subtype={task.subtype}")
                
                result = await self._solve_task(task)
                
                if result:
                    task.status = "solved"
                    task.solved_at = time.time()
                    self.solved_count += 1
                    log.info(f"Worker {worker_id}: SOLVED {task.task_id}")
                else:
                    task.status = "failed"
                    task.error = task.error or "Failed to solve"
                    log.warning(f"Worker {worker_id}: FAILED {task.task_id}: {task.error}")
                    
            except Exception as e:
                task.status = "failed"
                task.error = str(e)
                log.error(f"Worker {worker_id}: ERROR {task.task_id}: {e}")
            finally:
                self.active_sessions -= 1

    async def _solve_task(self, task: TurnstileTask) -> bool:
        """Solve a single Turnstile/challenge task with timeout protection."""
        page = None
        try:
            # Persistent context — get existing page or create new one
            if len(self._browser.pages) > 0:
                page = self._browser.pages[0]
            else:
                page = await self._browser.new_page()
            page.set_default_timeout(30000)
            
            # Run solve with overall 120s timeout to prevent worker hangs
            try:
                if task.subtype == "turnstile":
                    result = await asyncio.wait_for(self._solve_turnstile(page, task), timeout=120)
                elif task.subtype == "challenge":
                    result = await asyncio.wait_for(self._solve_challenge(page, task), timeout=120)
                elif task.subtype == "managed":
                    result = await asyncio.wait_for(self._solve_managed(page, task), timeout=120)
                else:
                    result = await asyncio.wait_for(self._solve_auto(page, task), timeout=120)
            except asyncio.TimeoutError:
                log.error(f"Task {task.task_id} timed out after 120s")
                task.error = "Solver timed out (120s)"
                return False
            
            return result
                
        except Exception as e:
            task.error = str(e)
            return False
        finally:
            if page:
                try:
                    task.cookies = await context.cookies()
                    task.user_agent = await page.evaluate("navigator.userAgent")
                    task.html = await page.content()
                except:
                    pass
            # persistent context — no cleanup per task

    async def _extract_turnstile_token(self, page) -> Optional[str]:
        """Extract Turnstile token from all possible sources."""
        return await page.evaluate("""
            () => {
                // Method 1: Hidden input fields
                const inputs = document.querySelectorAll('input[name="cf-turnstile-response"]');
                for (const input of inputs) {
                    if (input.value && input.value.length > 10) return input.value;
                }
                // Method 2: Widget data attributes
                const widgets = document.querySelectorAll('.cf-turnstile, [data-sitekey]');
                for (const w of widgets) {
                    const resp = w.querySelector('[name="cf-turnstile-response"]');
                    if (resp && resp.value && resp.value.length > 10) return resp.value;
                    // Check data-response attribute
                    if (w.dataset.response && w.dataset.response.length > 10) return w.dataset.response;
                }
                // Method 3: Check for window.turnstile.getResponse()
                if (window.turnstile) {
                    try {
                        const widgets = document.querySelectorAll('.cf-turnstile');
                        for (const w of widgets) {
                            const id = w.id || w.dataset.widgetId;
                            if (id) {
                                const resp = window.turnstile.getResponse(id);
                                if (resp && resp.length > 10) return resp;
                            }
                        }
                        // Try without ID
                        const resp = window.turnstile.getResponse();
                        if (resp && resp.length > 10) return resp;
                    } catch(e) {}
                }
                // Method 4: Check textarea elements (some implementations)
                const textareas = document.querySelectorAll('textarea[name="cf-turnstile-response"]');
                for (const ta of textareas) {
                    if (ta.value && ta.value.length > 10) return ta.value;
                }
                // Method 5: Check iframe content for success indicator
                const iframes = document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]');
                for (const iframe of iframes) {
                    // Check if iframe has success state (checkbox is checked)
                    const successInput = iframe.parentElement?.querySelector('input[name="cf-turnstile-response"]');
                    if (successInput && successInput.value && successInput.value.length > 10) return successInput.value;
                }
                // Method 6: Check intercepted token from init script
                if (window.__turnstile_token && window.__turnstile_token.length > 20) {
                    return window.__turnstile_token;
                }
                return null;
            }
        """)

    async def _solve_turnstile(self, page, task: TurnstileTask) -> bool:
        """Solve an interactive Turnstile widget."""
        log.info(f"Navigating to {task.pageurl}")
        
        try:
            response = await page.goto(task.pageurl, wait_until='domcontentloaded', timeout=30000)
            log.info(f"Page loaded: status={response.status if response else 'unknown'}")
        except Exception as e:
            log.warning(f"Navigation error (continuing anyway): {e}")
        
        # Wait for page to settle
        try:
            await asyncio.wait_for(asyncio.sleep(3), timeout=5)
        except asyncio.TimeoutError:
            pass
        
        log.info("Starting Turnstile detection loop")
        
        token = None
        
        # Wait for Turnstile iframe/widget to appear
        for attempt in range(20):
            try:
                log.debug(f"Turnstile detection attempt {attempt + 1}")
                
                # First check if token already exists (auto-solved / managed challenge)
                token = await self._extract_turnstile_token(page)
                if token and "DUMMY" not in token and "XXXX" not in token and not token.startswith("test"):
                    log.info(f"Token already present (auto-solved): {token[:40]}...")
                    break
                
                # Check for Turnstile iframe
                iframe_elem = await page.query_selector('iframe[src*="challenges.cloudflare.com"]')
                if not iframe_elem:
                    # Also check for turnstile widget container
                    iframe_elem = await page.query_selector('.cf-turnstile iframe, [data-sitekey] iframe')
                
                if iframe_elem:
                    log.info(f"Found Turnstile iframe (attempt {attempt + 1})")
                    
                    # Try clicking inside the Turnstile iframe
                    try:
                        frame = await iframe_elem.content_frame()
                        if frame:
                            await asyncio.sleep(1)
                            
                            # Try multiple selectors for the checkbox
                            clicked = False
                            for selector in ['input[type="checkbox"]', '.mark', '.cb-lb', 'label', '.ctp-checkbox-container', '#success', '.check']:
                                try:
                                    checkbox = await frame.query_selector(selector)
                                    if checkbox:
                                        await checkbox.click(force=True)
                                        log.info(f"Clicked Turnstile element: {selector}")
                                        clicked = True
                                        break
                                except:
                                    pass
                            
                            if not clicked:
                                # Click the checkbox area of the widget iframe
                                box = await iframe_elem.bounding_box()
                                if box:
                                    await page.mouse.click(
                                        box['x'] + 28,
                                        box['y'] + box['height'] / 2
                                    )
                                    log.info("Clicked Turnstile iframe left-center (checkbox)")
                    except Exception as e:
                        log.warning(f"iframe content_frame failed (cross-origin?): {e}")
                        # Fall back to clicking the iframe bounding box
                        box = await iframe_elem.bounding_box()
                        if box:
                            await page.mouse.click(
                                box['x'] + 28,
                                box['y'] + box['height'] / 2
                            )
                            log.info("Clicked Turnstile bounding box (fallback)")
                    
                    # Wait for token to appear after click
                    for wait in range(25):
                        await asyncio.sleep(1)
                        token = await self._extract_turnstile_token(page)
                        if token and "DUMMY" not in token and "XXXX" not in token and not token.startswith("test"):
                            log.info(f"Turnstile token obtained after {wait+1}s: {token[:40]}...")
                            break
                    
                    if token and "DUMMY" not in token and "XXXX" not in token and not token.startswith("test"):
                        break
                else:
                    # Check for the widget container (sometimes iframe loads late)
                    widget = await page.query_selector('.cf-turnstile, [data-sitekey]')
                    if widget:
                        log.info(f"Found Turnstile container (no iframe yet, attempt {attempt + 1})")
                        # Wait more for iframe to load
                        await asyncio.sleep(3)
                        continue
                    
                    # No Turnstile detected at all — maybe it's a JS challenge
                    title = await page.title()
                    if 'just a moment' in title.lower() or 'checking' in title.lower():
                        log.info("No Turnstile widget found — appears to be JS challenge, switching...")
                        task.subtype = "challenge"
                        return await self._solve_challenge(page, task)
                    
            except Exception as e:
                log.warning(f"Turnstile attempt {attempt + 1} error: {e}")
            
            await asyncio.sleep(2)
        
        if token and "DUMMY" not in token and "XXXX" not in token and not token.startswith("test"):
            task.token = token
            try:
                task.html = await page.content()
            except:
                pass
            return True
        else:
            # Last resort: check if page loaded successfully despite no token
            title = await page.title()
            cookies = await page.context.cookies()
            cf_clearance = next((c['value'] for c in cookies if c['name'] == 'cf_clearance'), '')
            if cf_clearance:
                task.token = cf_clearance
                task.cookies = {c['name']: c['value'] for c in cookies}
                log.info(f"Got cf_clearance as fallback: {cf_clearance[:40]}...")
                return True
            task.error = "Could not obtain Turnstile token"
            return False

    async def _solve_challenge(self, page, task: TurnstileTask) -> bool:
        """Solve a Cloudflare JS challenge (the 'Checking your browser' page)."""
        log.info(f"Navigating to {task.pageurl}")
        
        try:
            await page.goto(task.pageurl, wait_until='domcontentloaded', timeout=30000)
        except Exception as e:
            log.warning(f"Navigation warning: {e}")
        
        # Wait for the challenge to resolve (Cloudflare auto-submits)
        for i in range(30):
            await asyncio.sleep(2)
            
            # Check if challenge page is still showing
            title = await page.title()
            url = page.url
            
            # Check for cf_clearance cookie
            cookies = await page.context.cookies()
            cf_clearance = None
            for c in cookies:
                if c['name'] == 'cf_clearance':
                    cf_clearance = c['value']
                    break
            
            if cf_clearance:
                log.info(f"Got cf_clearance cookie: {cf_clearance[:30]}...")
                task.cookies = {c['name']: c['value'] for c in cookies}
                task.token = cf_clearance
                task.html = await page.content()
                return True
            
            # Check if we've passed the challenge (page title changed from "Just a moment...")
            if 'just a moment' not in title.lower() and 'checking' not in title.lower():
                log.info(f"Challenge appears resolved (title: {title})")
                cookies = await page.context.cookies()
                task.cookies = {c['name']: c['value'] for c in cookies}
                cf_clearance = next((c['value'] for c in cookies if c['name'] == 'cf_clearance'), '')
                task.token = cf_clearance or 'challenge_passed'
                task.html = await page.content()
                return True
            
            log.info(f"Challenge still running... ({i+1}/30, title: {title})")
        
        task.error = "Cloudflare challenge did not resolve within timeout"
        return False

    async def _solve_managed(self, page, task: TurnstileTask) -> bool:
        """Solve a managed/invisible Turnstile challenge."""
        # Managed challenges often auto-resolve if the browser looks legitimate
        # Navigate and wait for the challenge to complete
        
        log.info(f"Navigating to {task.pageurl}")
        
        try:
            await page.goto(task.pageurl, wait_until='domcontentloaded', timeout=30000)
        except Exception as e:
            log.warning(f"Navigation warning: {e}")
        
        # Wait for auto-resolution
        for i in range(20):
            await asyncio.sleep(2)
            
            # Check for Turnstile token
            token = await page.evaluate("""
                () => {
                    const inputs = document.querySelectorAll('input[name="cf-turnstile-response"]');
                    for (const input of inputs) {
                        if (input.value && input.value.length > 10) return input.value;
                    }
                    return null;
                }
            """)
            
            if token and "DUMMY" not in token and "XXXX" not in token and not token.startswith("test"):
                task.token = token
                task.html = await page.content()
                return True
            
            # Check for cf_clearance
            cookies = await page.context.cookies()
            cf_clearance = next((c['value'] for c in cookies if c['name'] == 'cf_clearance'), '')
            if cf_clearance:
                task.cookies = {c['name']: c['value'] for c in cookies}
                task.token = cf_clearance
                task.html = await page.content()
                return True
            
            log.info(f"Managed challenge waiting... ({i+1}/20)")
        
        task.error = "Managed challenge did not auto-resolve"
        return False

    async def _solve_auto(self, page, task: TurnstileTask) -> bool:
        """Auto-detect and solve whatever Cloudflare challenge is present."""
        log.info(f"Auto-detecting challenge at {task.pageurl}")
        
        try:
            await page.goto(task.pageurl, wait_until='domcontentloaded', timeout=30000)
        except Exception as e:
            log.warning(f"Navigation warning: {e}")
        
        await asyncio.sleep(2)
        
        # Check page content to determine challenge type
        title = await page.title()
        html = await page.content()
        
        is_js_challenge = 'just a moment' in title.lower() or 'checking your browser' in title.lower()
        has_turnstile = 'challenges.cloudflare.com' in html or 'cf-turnstile' in html
        
        log.info(f"Detected: title='{title}', js_challenge={is_js_challenge}, turnstile={has_turnstile}")
        
        if is_js_challenge:
            task.subtype = "challenge"
            return await self._solve_challenge(page, task)
        elif has_turnstile:
            task.subtype = "turnstile"
            return await self._solve_turnstile(page, task)
        else:
            # Maybe already passed, or managed challenge
            task.subtype = "managed"
            return await self._solve_managed(page, task)

    # ===== Task Management =====

    def submit_task(self, method: str, sitekey: str, pageurl: str, 
                    subtype: str = None, **kwargs) -> str:
        """Submit a new task and return the task ID."""
        task_id = hashlib.md5(
            f"{sitekey}{pageurl}{time.time()}{uuid.uuid4()}".encode()
        ).hexdigest()[:24]
        
        if subtype is None:
            if method == "turnstile":
                subtype = "turnstile"
            elif method == "challenge":
                subtype = "challenge"
            else:
                subtype = "auto"
        
        task = TurnstileTask(
            task_id=task_id,
            sitekey=sitekey,
            pageurl=pageurl,
            method=method,
            subtype=subtype,
            status="pending",
            created_at=time.time(),
        )
        
        self.tasks[task_id] = task
        self._queue.put_nowait(task)
        log.info(f"Submitted task {task_id}: method={method} subtype={subtype} url={pageurl}")
        return task_id

    def get_task(self, task_id: str) -> Optional[TurnstileTask]:
        return self.tasks.get(task_id)


# ===== HTTP API Server =====

def create_app(solver: TurnstileSolver) -> web.Application:
    app = web.Application()
    
    # Authentication middleware
    async def check_api_key(request: web.Request):
        key = request.query.get('key', '') or (await request.post()).get('key', '') if request.method == 'POST' else ''
        if solver.api_key and key != solver.api_key:
            return web.json_response({'error': 'Invalid API key'}, status=403)
        return None
    
    # ===== 2captcha-compatible endpoints =====
    
    async def in_php(request: web.Request):
        """2captcha-compatible task submission."""
        # Auth check
        data = await request.post()
        if solver.api_key:
            key = data.get('key', '')
            if key != solver.api_key:
                return web.Response(text='ERROR_WRONG_KEY')
        method = data.get('method', 'turnstile')
        sitekey = data.get('googlekey', '') or data.get('sitekey', '')
        pageurl = data.get('pageurl', '')
        subtype = data.get('subtype', '') or None
        
        if not pageurl:
            return web.Response(text='ERROR_WRONG_PARAMETER')
        
        task_id = solver.submit_task(
            method=method,
            sitekey=sitekey,
            pageurl=pageurl,
            subtype=subtype,
        )
        return web.Response(text=f'OK|{task_id}')
    
    async def res_php(request: web.Request):
        """2captcha-compatible result polling."""
        if solver.api_key:
            key = request.query.get('key', '')
            if key != solver.api_key:
                return web.Response(text='ERROR_WRONG_KEY')
        
        task_id = request.query.get('id', '')
        action = request.query.get('action', 'get')
        
        task = solver.get_task(task_id)
        if not task:
            return web.Response(text='ERROR_CAPTCHA_UNSOLVABLE')
        
        if task.status == 'pending' or task.status == 'processing':
            return web.Response(text='CAPCHA_NOT_READY')
        elif task.status == 'solved':
            return web.Response(text=f'OK|{task.token}')
        else:
            return web.Response(text=f'ERROR_CAPTCHA_UNSOLVABLE')
    
    # ===== FlareSolverr-compatible endpoint =====
    
    async def v1_api(request: web.Request):
        """FlareSolverr-compatible API endpoint."""
        data = await request.json()
        cmd = data.get('cmd', '')
        
        if cmd == 'request.get':
            url = data.get('url', '')
            max_timeout = data.get('maxTimeout', 60000)
            
            if not url:
                return web.json_response({'status': 'error', 'message': 'URL is required'})
            
            task_id = solver.submit_task(
                method='challenge',
                sitekey='',
                pageurl=url,
                subtype='auto',
            )
            
            # Wait for result
            deadline = time.time() + max_timeout / 1000
            while time.time() < deadline:
                task = solver.get_task(task_id)
                if task.status == 'solved':
                    cookies_dict = {}
                    if isinstance(task.cookies, dict):
                        cookies_dict = task.cookies
                    elif isinstance(task.cookies, list):
                        cookies_dict = {c['name']: c['value'] for c in task.cookies if 'name' in c and 'value' in c}
                    
                    return web.json_response({
                        'status': 'ok',
                        'solution': {
                            'url': task.pageurl,
                            'status': 200,
                            'cookies': cookies_dict,
                            'userAgent': task.user_agent,
                            'html': task.html,
                            'headers': {},
                        }
                    })
                elif task.status == 'failed':
                    return web.json_response({
                        'status': 'error',
                        'message': task.error,
                    })
                await asyncio.sleep(2)
            
            return web.json_response({'status': 'error', 'message': 'Timeout'})
        
        elif cmd == 'sessions.create':
            session_id = hashlib.md5(str(uuid.uuid4()).encode()).hexdigest()[:12]
            return web.json_response({'status': 'ok', 'session': session_id})
        
        elif cmd == 'sessions.list':
            return web.json_response({'status': 'ok', 'sessions': []})
        
        elif cmd == 'sessions.destroy':
            return web.json_response({'status': 'ok'})
        
        else:
            return web.json_response({'status': 'error', 'message': f'Unknown cmd: {cmd}'})
    
    # ===== Health endpoint =====
    
    async def health(request: web.Request):
        return web.json_response({
            'status': 'ok',
            'queue': solver._queue.qsize(),
            'solved': solver.solved_count,
            'active': solver.active_sessions,
            'browser': solver.browser_type,
        })
    
    # Register routes
    app.router.add_post('/in.php', in_php)
    app.router.add_get('/res.php', res_php)
    app.router.add_post('/v1', v1_api)
    app.router.add_get('/health', health)
    
    return app


# ===== Main =====

async def main():
    import argparse
    parser = argparse.ArgumentParser(description='Turnstile + Cloudflare Challenge Solver')
    parser.add_argument('--api-key', required=True, help='API key for authentication')
    parser.add_argument('--port', type=int, default=8877, help='HTTP API port (default: 8877)')
    parser.add_argument('--max-sessions', type=int, default=3, help='Max concurrent browser sessions')
    parser.add_argument('--no-headless', action='store_true', help='Run browser in visible mode')
    parser.add_argument('--ext-path', default=os.environ.get('CAPTCHA_EXT_PATH', '/opt/recaptcha-v2-solver/extension'),
                       help='Path to CaptchaPlugin extension for managed challenges')
    parser.add_argument('--browser', default='chromium', choices=['chromium', 'firefox'],
                       help='Browser engine to use')
    args = parser.parse_args()
    
    solver = TurnstileSolver(
        api_key=args.api_key,
        port=args.port,
        max_sessions=args.max_sessions,
        headless=not args.no_headless,
        browser_type=args.browser,
        ext_path=args.ext_path,
    )
    
    await solver.start()
    
    app = create_app(solver)
    
    log.info(f"Turnstile Solver API on port {args.port}")
    log.info(f"API key: {args.api_key[:8]}...")
    log.info(f"Browser: {args.browser} (headless={not args.no_headless})")
    log.info(f"Max sessions: {args.max_sessions}")
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', args.port)
    await site.start()
    
    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down...")
        await solver.stop()


if __name__ == '__main__':
    asyncio.run(main())

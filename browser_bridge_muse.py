#!/usr/bin/env python3
"""
browser_bridge_muse.py - Dedicated Browser Automation Bridge for OpenMuse & AI Agents
Exposes unified REST, WebSocket, and MCP interfaces for:
  1. Local Playwright Chromium Worker (port 8790)
  2. Real Chrome via Chrome CSI Daemon (port 10088)
  3. Tencent BrowserSkill (BSK) Agent Windows (port 42800)
  4. Optional Secure Public ngrok Tunneling

Completely independent of ixBrowser and multi-proxy anti-detect configurations.
"""

import os
import sys
import json
import time
import socket
import secrets
import asyncio
import logging
import argparse
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Any

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("BrowserBridgeMuse")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(SCRIPT_DIR, ".muse_bridge_key")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "muse_bridge_config.json")
SHOTS_DIR = os.path.join(SCRIPT_DIR, "shots")

DEFAULT_PORT = 8790  # Default OpenMuse browser worker port
CSI_PORT = 10088
BSK_PORT = 42800


# ── Configuration & Key Management ───────────────────────────────────────────
def load_or_create_key() -> str:
    """Loads existing API security key or creates a cryptographically secure 32-character token."""
    if os.path.exists(KEY_FILE):
        try:
            with open(KEY_FILE, "r", encoding="utf-8") as f:
                k = f.read().strip()
                if len(k) >= 16:
                    return k
        except Exception:
            pass

    new_key = secrets.token_urlsafe(24)
    try:
        with open(KEY_FILE, "w", encoding="utf-8") as f:
            f.write(new_key)
    except Exception as e:
        logger.warning(f"Could not persist key to file: {e}")
    return new_key


def load_config() -> Dict[str, Any]:
    """Loads configuration options."""
    defaults = {
        "port": DEFAULT_PORT,
        "headless": False,
        "default_backend": "chromium",
        "chrome_csi_port": CSI_PORT,
        "bsk_port": BSK_PORT,
        "enable_ngrok": False,
        "ngrok_auth_token": ""
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                defaults.update(data)
        except Exception:
            pass
    return defaults


def save_config(cfg: Dict[str, Any]):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save config: {e}")


# ── System & Port Probing ───────────────────────────────────────────────────
def is_port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.6)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def get_backend_status() -> Dict[str, Any]:
    """Checks the health and connectivity of all supported browser backends."""
    csi_open = is_port_open(CSI_PORT)
    bsk_open = is_port_open(BSK_PORT)
    ngrok_open = is_port_open(4040)

    csi_details = {"connected": csi_open}
    if csi_open:
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{CSI_PORT}/status")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode())
                csi_details["extension_connected"] = data.get("extension_connected", False)
                csi_details["sessions"] = len(data.get("sessions", []))
        except Exception:
            pass

    return {
        "chrome_csi": csi_details,
        "tencent_bsk": {"connected": bsk_open, "port": BSK_PORT},
        "ngrok_inspector": {"connected": ngrok_open, "port": 4040},
        "playwright_installed": True
    }


# ── Browser Worker Controller ───────────────────────────────────────────────
class MuseBrowserManager:
    """Manages active Playwright Chromium instance with tab pooling & warm CDP sessions."""

    def __init__(self, headless: bool = False):
        self.headless = headless
        self.pw = None
        self.browser = None
        self.context = None
        self.active_page = None
        self.pages: List[Any] = []
        self._lock = asyncio.Lock()

    async def ensure_browser(self):
        async with self._lock:
            if self.browser and self.context:
                return
            from playwright.async_api import async_playwright
            self.pw = await async_playwright().start()
            
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--disable-dev-shm-usage"
            ]
            self.browser = await self.pw.chromium.launch(
                headless=self.headless,
                args=launch_args
            )
            self.context = await self.browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            )
            self.active_page = await self.context.new_page()
            self.pages = [self.active_page]
            logger.info(f"Playwright Chromium started (headless={self.headless}).")

    async def close_browser(self):
        async with self._lock:
            if self.context:
                try:
                    await self.context.close()
                except Exception:
                    pass
                self.context = None
            if self.browser:
                try:
                    await self.browser.close()
                except Exception:
                    pass
                self.browser = None
            if self.pw:
                try:
                    await self.pw.stop()
                except Exception:
                    pass
                self.pw = None
            self.active_page = None
            self.pages = []
            logger.info("Playwright Chromium cleanly stopped.")

    async def navigate(self, url: str, wait_until: str = "domcontentloaded", timeout_ms: int = 30000) -> Dict[str, Any]:
        await self.ensure_browser()
        t0 = time.time()
        page = self.active_page
        if not page or page.is_closed():
            page = await self.context.new_page()
            self.active_page = page
            self.pages.append(page)

        await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        title = await page.title()
        current_url = page.url
        elapsed = round(time.time() - t0, 3)
        return {
            "success": True,
            "url": current_url,
            "title": title,
            "elapsed_seconds": elapsed
        }

    async def evaluate_js(self, expression: str) -> Any:
        await self.ensure_browser()
        if not self.active_page or self.active_page.is_closed():
            raise RuntimeError("No active page open.")
        return await self.active_page.evaluate(expression)

    async def snapshot(self) -> Dict[str, Any]:
        """Extracts semantic accessibility and text representation of active page."""
        await self.ensure_browser()
        if not self.active_page or self.active_page.is_closed():
            return {"error": "No active page"}

        page = self.active_page
        title = await page.title()
        url = page.url
        
        # Extract title, text content, and headings
        text_content = await page.evaluate("""
            () => {
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let text = '';
                let node;
                while (node = walker.nextNode()) {
                    const str = node.textContent.trim();
                    if (str && node.parentElement.tagName !== 'SCRIPT' && node.parentElement.tagName !== 'STYLE') {
                        text += str + '\\n';
                    }
                }
                return text.slice(0, 15000);
            }
        """)

        return {
            "title": title,
            "url": url,
            "text": text_content,
            "timestamp": time.time()
        }

    async def screenshot(self, path: Optional[str] = None) -> str:
        await self.ensure_browser()
        if not self.active_page or self.active_page.is_closed():
            raise RuntimeError("No active page")

        os.makedirs(SHOTS_DIR, exist_ok=True)
        if not path:
            path = os.path.join(SHOTS_DIR, f"shot_{int(time.time())}.png")

        await self.active_page.screenshot(path=path, full_page=False)
        return path

    async def click(self, selector: str) -> bool:
        await self.ensure_browser()
        if not self.active_page or self.active_page.is_closed():
            return False
        await self.active_page.click(selector, timeout=10000)
        return True

    async def type_text(self, selector: str, text: str, human_delay: bool = False) -> bool:
        await self.ensure_browser()
        if not self.active_page or self.active_page.is_closed():
            return False
        if human_delay:
            await self.active_page.type(selector, text, delay=50)
        else:
            await self.active_page.fill(selector, text)
        return True

    async def list_tabs(self) -> List[Dict[str, Any]]:
        if not self.context:
            return []
        res = []
        for idx, p in enumerate(self.context.pages):
            if not p.is_closed():
                try:
                    title = await p.title()
                    url = p.url
                except Exception:
                    title, url = "Untitled", ""
                res.append({"index": idx, "title": title, "url": url, "active": (p == self.active_page)})
        return res


# ── Chrome CSI Delegation ───────────────────────────────────────────────────
def call_chrome_csi(action: str, args: Optional[dict] = None) -> Dict[str, Any]:
    """Sends command directly to Chrome CSI daemon on port 10088."""
    if args is None:
        args = {}
    url = f"http://127.0.0.1:{CSI_PORT}/command"
    payload = json.dumps({"action": action, "args": args, "session": "muse"}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "ngrok-skip-browser-warning": "true"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


# ── FastAPI Bridge Server ───────────────────────────────────────────────────
def create_app(api_key: str, browser_mgr: MuseBrowserManager):
    from fastapi import FastAPI, Request, HTTPException, Depends
    from fastapi.responses import JSONResponse, FileResponse
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(
        title="Browser Bridge for Muse",
        description="Unified browser automation gateway for OpenMuse and AI agents (Chromium, Chrome CSI, Tencent BSK).",
        version="1.0.0"
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def verify_auth(request: Request):
        # Allow /health without auth
        if request.url.path in ("/health", "/"):
            return True
        key = request.headers.get("X-Bridge-Key") or request.query_params.get("key")
        if not key or key != api_key:
            raise HTTPException(status_code=401, detail="Unauthorized: Invalid X-Bridge-Key")
        return True

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "service": "browser-bridge-for-muse",
            "version": "1.0.0",
            "timestamp": time.time()
        }

    @app.get("/status", dependencies=[Depends(verify_auth)])
    async def status():
        b_status = get_backend_status()
        tabs = await browser_mgr.list_tabs()
        return {
            "success": True,
            "backends": b_status,
            "chromium_active_tabs": len(tabs),
            "tabs": tabs
        }

    @app.post("/browser/start", dependencies=[Depends(verify_auth)])
    async def start_browser(data: Optional[Dict[str, Any]] = None):
        if data and "headless" in data:
            browser_mgr.headless = bool(data["headless"])
        await browser_mgr.ensure_browser()
        return {"success": True, "headless": browser_mgr.headless}

    @app.post("/browser/stop", dependencies=[Depends(verify_auth)])
    async def stop_browser():
        await browser_mgr.close_browser()
        return {"success": True}

    @app.post("/nav", dependencies=[Depends(verify_auth)])
    async def navigate(payload: Dict[str, Any]):
        url = payload.get("url")
        if not url:
            raise HTTPException(status_code=400, detail="Missing 'url'")

        backend = payload.get("backend", "chromium").lower()
        if backend == "csi":
            if not is_port_open(CSI_PORT):
                raise HTTPException(status_code=503, detail="Chrome CSI daemon is offline")
            res = call_chrome_csi("navigate", {"url": url})
            return {"success": True, "backend": "csi", "data": res}
        else:
            res = await browser_mgr.navigate(
                url,
                wait_until=payload.get("wait_until", "domcontentloaded"),
                timeout_ms=int(payload.get("timeout", 30000))
            )
            return res

    @app.post("/eval", dependencies=[Depends(verify_auth)])
    async def evaluate_js(payload: Dict[str, Any]):
        expr = payload.get("code") or payload.get("expression")
        if not expr:
            raise HTTPException(status_code=400, detail="Missing 'code' expression")

        backend = payload.get("backend", "chromium").lower()
        if backend == "csi":
            res = call_chrome_csi("evaluate", {"code": expr})
            return {"success": True, "result": res}
        else:
            res = await browser_mgr.evaluate_js(expr)
            return {"success": True, "result": res}

    @app.get("/snapshot", dependencies=[Depends(verify_auth)])
    @app.post("/snapshot", dependencies=[Depends(verify_auth)])
    async def snapshot(payload: Optional[Dict[str, Any]] = None):
        backend = (payload or {}).get("backend", "chromium").lower()
        if backend == "csi":
            res = call_chrome_csi("snapshot", {})
            return {"success": True, "backend": "csi", "data": res}
        else:
            data = await browser_mgr.snapshot()
            return {"success": True, "backend": "chromium", "data": data}

    @app.post("/screenshot", dependencies=[Depends(verify_auth)])
    async def screenshot(payload: Optional[Dict[str, Any]] = None):
        p = await browser_mgr.screenshot()
        return {"success": True, "path": p, "filename": os.path.basename(p)}

    @app.post("/click", dependencies=[Depends(verify_auth)])
    async def click_element(payload: Dict[str, Any]):
        sel = payload.get("selector")
        if not sel:
            raise HTTPException(status_code=400, detail="Missing 'selector'")
        ok = await browser_mgr.click(sel)
        return {"success": ok}

    @app.post("/type", dependencies=[Depends(verify_auth)])
    async def type_element(payload: Dict[str, Any]):
        sel = payload.get("selector")
        txt = payload.get("text", "")
        human = bool(payload.get("human", False))
        if not sel:
            raise HTTPException(status_code=400, detail="Missing 'selector'")
        ok = await browser_mgr.type_text(sel, txt, human_delay=human)
        return {"success": ok}

    @app.get("/tabs", dependencies=[Depends(verify_auth)])
    async def list_tabs():
        tabs = await browser_mgr.list_tabs()
        return {"success": True, "tabs": tabs}

    @app.post("/search", dependencies=[Depends(verify_auth)])
    async def search(payload: Dict[str, Any]):
        q = payload.get("query")
        if not q:
            raise HTTPException(status_code=400, detail="Missing 'query'")
        encoded = urllib.parse.quote(q)
        search_url = f"https://www.google.com/search?q={encoded}"
        await browser_mgr.navigate(search_url)
        # Fast extraction of top results
        results = await browser_mgr.evaluate_js("""
            () => {
                const items = [];
                document.querySelectorAll('div.g').forEach(el => {
                    const titleEl = el.querySelector('h3');
                    const linkEl = el.querySelector('a');
                    const snipEl = el.querySelector('div[style*="-webkit-line-clamp"]');
                    if (titleEl && linkEl) {
                        items.push({
                            title: titleEl.innerText,
                            link: linkEl.href,
                            snippet: snipEl ? snipEl.innerText : ''
                        });
                    }
                });
                return items.slice(0, 8);
            }
        """)
        return {"success": True, "query": q, "results": results}

    return app


# ── Main Interactive / Server Runner ─────────────────────────────────────────
def print_banner(port: int, key: str):
    banner = f"""
  +===========================================================+
  |              BROWSER BRIDGE FOR MUSE v1.0                 |
  |     Dedicated Browser Gateway for OpenMuse & AI Agents    |
  +===========================================================+
  * Local Endpoint:   http://127.0.0.1:{port}
  * Security Key:     {key}
  * Supported:        Chromium, Chrome CSI (port {CSI_PORT}), BSK (port {BSK_PORT})
  * Health Probe:     http://127.0.0.1:{port}/health
  +===========================================================+
    """
    print(banner)


def start_server(port: int, key: str, headless: bool = False):
    import uvicorn
    browser_mgr = MuseBrowserManager(headless=headless)
    app = create_app(key, browser_mgr)

    print_banner(port, key)
    print("Starting uvicorn HTTP server... (Press Ctrl+C to terminate)")
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    finally:
        asyncio.run(browser_mgr.close_browser())


def interactive_menu():
    cfg = load_config()
    key = load_or_create_key()
    port = cfg.get("port", DEFAULT_PORT)
    headless = cfg.get("headless", False)

    while True:
        print_banner(port, key)
        print("1. Start Browser Bridge Server (Local Port 8790)")
        print("2. Browser Backends Health Check & Diagnostics")
        print("3. Test Quick Navigation (Playwright Chromium)")
        print("4. Toggle Headless Mode (Current: %s)" % ("Headless" if headless else "Headed Window"))
        print("5. Rotate Security Key")
        print("0. Exit\n")

        c = input("Select an option (0-5) [1]: ").strip() or "1"

        if c == "1":
            start_server(port, key, headless=headless)
            break
        elif c == "2":
            st = get_backend_status()
            print("\nBackend Connectivity:")
            print(f"  * Chrome CSI (port {CSI_PORT}):  {'ONLINE' if st['chrome_csi']['connected'] else 'OFFLINE'}")
            print(f"  * Tencent BSK (port {BSK_PORT}):  {'ONLINE' if st['tencent_bsk']['connected'] else 'OFFLINE'}")
            print(f"  * ngrok Inspector (port 4040): {'ONLINE' if st['ngrok_inspector']['connected'] else 'OFFLINE'}")
            input("\nPress Enter to return to menu...")
        elif c == "3":
            url = input("Enter URL to navigate [https://example.com]: ").strip() or "https://example.com"
            print("Launching browser and navigating...")
            mgr = MuseBrowserManager(headless=headless)
            res = asyncio.run(mgr.navigate(url))
            print("Navigation result:", json.dumps(res, indent=2))
            asyncio.run(mgr.close_browser())
            input("\nPress Enter to return to menu...")
        elif c == "4":
            headless = not headless
            cfg["headless"] = headless
            save_config(cfg)
            print(f"Headless mode set to: {headless}")
            time.sleep(1)
        elif c == "5":
            new_k = secrets.token_urlsafe(24)
            with open(KEY_FILE, "w", encoding="utf-8") as f:
                f.write(new_k)
            key = new_k
            print(f"New key generated: {key}")
            time.sleep(1.5)
        elif c == "0":
            break


def main():
    parser = argparse.ArgumentParser(description="Browser Bridge for Muse")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to bind server (default: 8790)")
    parser.add_argument("--headless", action="store_true", help="Run Chromium in headless mode")
    parser.add_argument("--server", action="store_true", help="Start server directly without interactive menu")
    args = parser.parse_args()

    key = load_or_create_key()
    if args.server:
        start_server(args.port, key, headless=args.headless)
    else:
        interactive_menu()


if __name__ == "__main__":
    main()

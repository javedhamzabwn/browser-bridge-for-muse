#!/usr/bin/env python3
"""
ixBrowser All-In-One Bridge v2.2 -- interactive edition for the Windows PC.

Just double-click Run_Bridge.bat (or run: python ixbridge_all_in_one.py)
and pick what you want from the menu:

  1. Start bridge + ngrok tunnel   - one stable web address for everything:
       POST /ix/...         -> ixBrowser local API (profiles, groups, cookies...)
       POST /ixc/profile-list -> compact profile list (id, name, group only)
       POST /task/run       -> open a profile, check it in a real browser, close it
       GET  /task/run_stream-> same, but with live progress events (SSE)
       POST /task/run_many  -> check many profiles (parallel now), compact results
       POST /task/open_many -> open many profiles IN PARALLEL, leave them open
       GET  /task/open_profiles -> profiles the bridge currently has open
       POST /exec           -> run a terminal command on this PC, get the output
       POST /csi            -> talk to your real Chrome via the CSI daemon
       GET  /tools          -> plain-language catalog of every endpoint
       GET  /dashboard      -> status page (needs ?key=YOUR_API_KEY)
       GET  /health         -> liveness probe
       /mcp/sse             -> MCP server for local AI tools (needs: pip install mcp)

v2.2 speedups: warm CDP connections are reused across tasks (no re-attach
every call), smart goto skips navigation when the tab is already on the
target page, fixed sleeps replaced with event-based waits, lite mode blocks
images/fonts/video for data-only tasks, run_many runs in parallel.
  2. Check my setup                  - diagnostics
  3. Change my saved API key
  4. Show my last connection details
  5. Choose which services are on    - toggle ixBrowser / CSI / terminal routes
  6. View recent log
  7. Exit

Local AI tools on this PC (Hermes, Claude Code, anything using HTTP) can use
the bridge at http://127.0.0.1:PORT with the same X-Bridge-Key header.
The ngrok address is only needed for remote operators.

Logs: bridge.log and audit.log are written next to this script.
Press Ctrl+C at any time to stop.
"""

import base64
import concurrent.futures
import getpass
import json
import logging
import asyncio
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import html
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ------------------------------------------------------------------ config
BRIDGE_VERSION = "2.2"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(SCRIPT_DIR, ".ixbridge_key")
INFO_FILE = os.path.join(SCRIPT_DIR, "bridge_connection.txt")
LOG_FILE = os.path.join(SCRIPT_DIR, "bridge.log")
AUDIT_FILE = os.path.join(SCRIPT_DIR, "audit.log")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "bridge_config.json")
DEFAULT_IX_PORT = 53200
REQUIRED_PACKAGES = ["fastapi", "uvicorn", "httpx", "playwright", "pydantic"]

START_TIME = time.time()
TASK_HISTORY: deque = deque(maxlen=50)   # recent runs for the dashboard
OPEN_PROFILES: set = set()               # profiles opened but not yet closed

# v2.2: warm CDP connections, reused across tasks instead of re-attaching
# every call. _CDP[profile_id] = {"ws_url", "browser", "context", "page",
# "lite" (bool: lite routes currently applied), "last_used" (epoch)}.
_CDP: Dict[int, dict] = {}
_PW: Dict[str, Any] = {"pw": None}        # one Playwright driver, bridge lifetime
_LITE_RE = (r"\.(png|jpe?g|gif|webp|svg|ico|bmp|avif|mp4|webm|ogv|mov|"
            r"woff2?|ttf|otf|eot)(\?|#|$)")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"),
              logging.StreamHandler(sys.stdout)])
log = logging.getLogger("bridge")


# ============================================================ input helpers
INTERACTIVE = sys.stdin.isatty()


def line(char="=", width=64):
    print(char * width)


def banner(text):
    print()
    line()
    print("  " + text)
    line()
    print()


def ask(prompt, default=None):
    if default:
        full = "{} [{}]: ".format(prompt, default)
    else:
        full = "{}: ".format(prompt)
    try:
        answer = input(full).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)
    return answer or (default or "")


def ask_secret(prompt):
    try:
        return getpass.getpass(prompt + " (hidden, Enter = generate): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)


def ask_yes_no(prompt, default=True):
    hint = "Y/n" if default else "y/N"
    answer = ask("{} [{}]".format(prompt, hint)).lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def pause():
    try:
        input("\nPress Enter to continue...")
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)


def ok(text):
    print("  [OK] " + text)


def warn(text):
    print("  [!!] " + text)


def fail(text):
    print("  [XX] " + text)
    raise SystemExit(1)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ============================================================ config file
def default_config() -> dict:
    return {"port": 8000,
            "ix_port": 0,   # 0 = auto-detect ixBrowser port
            "modes": {"ix": True, "csi": True, "term": True}}


def load_config() -> dict:
    cfg = default_config()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved.get("port"), int):
            cfg["port"] = saved["port"]
        if isinstance(saved.get("ix_port"), int) and 0 <= saved["ix_port"] <= 65535:
            cfg["ix_port"] = saved["ix_port"]
        if isinstance(saved.get("modes"), dict):
            for k in cfg["modes"]:
                if k in saved["modes"]:
                    cfg["modes"][k] = bool(saved["modes"][k])
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError as e:
        log.warning("could not save config: %s", e)


# ============================================================ audit + history
def audit_log(endpoint: str, detail: str, client_ip: str = "-"):
    """Append one JSON line per sensitive action to audit.log."""
    try:
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": utcnow_iso(), "ip": client_ip,
                                "endpoint": endpoint,
                                "detail": detail[:500]}) + "\n")
    except OSError as e:
        log.warning("audit write failed: %s", e)


def record_task(kind: str, detail: str, status: str, ms: Optional[int] = None):
    TASK_HISTORY.appendleft({"ts": utcnow_iso(), "kind": kind,
                             "detail": detail[:160], "status": status,
                             "ms": ms})


# ============================================================ ixBrowser port
_IX_CACHE = {"port": DEFAULT_IX_PORT, "ts": 0.0}
_IX_LOCK = threading.Lock()


def _ix_probe(port: int) -> bool:
    """True if something on port answers like the ixBrowser API."""
    try:
        data = json.dumps({"page": 1, "page_size": 1}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:{}/api/v2/profile-list".format(port),
            data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as r:
            env = json.loads(r.read().decode())
        return isinstance(env, dict) and ("data" in env or "error" in env)
    except Exception:
        return False


def parse_ix_port(text) -> Optional[int]:
    """Accept '53400' or 'http://127.0.0.1:53400' and return the port number."""
    text = (text or "").strip()
    if not text or text == "0":
        return None
    m = re.search(r":(\d{1,5})(?:/|$)", text)
    try:
        port = int(m.group(1)) if m else int(text)
    except ValueError:
        return None
    return port if 1 <= port <= 65535 else None


def detect_ix_port() -> Optional[int]:
    """Scan likely ports for the ixBrowser client. Returns port or None.

    Covers the classic default zone (53195-53210) plus the 53379-53424 gap,
    which Windows often leaves free when Hyper-V excludes 53179-53378.
    Probes run in parallel so a full scan takes ~2 seconds.
    A manual ix_port in bridge_config.json always wins over this scan.
    """
    candidates = [DEFAULT_IX_PORT]
    candidates += [p for p in range(53195, 53211) if p != DEFAULT_IX_PORT]
    candidates += list(range(53379, 53425))
    found = []

    def check(port):
        if _ix_probe(port):
            found.append(port)

    with concurrent.futures.ThreadPoolExecutor(max_workers=24) as ex:
        list(ex.map(check, candidates))
    if not found:
        return None
    return min(found, key=candidates.index)


def get_ix_port(force: bool = False) -> int:
    """Cached ixBrowser port; re-detects when stale or unreachable."""
    manual = load_config().get("ix_port", 0)
    if manual:
        return manual
    with _IX_LOCK:
        fresh = (time.time() - _IX_CACHE["ts"]) < 120
        if not force and fresh:
            s = socket.socket(); s.settimeout(2)
            try:
                s.connect(("127.0.0.1", _IX_CACHE["port"]))
                return _IX_CACHE["port"]
            except OSError:
                pass
            finally:
                s.close()
        port = detect_ix_port()
        if port:
            _IX_CACHE["port"] = port
            _IX_CACHE["ts"] = time.time()
            return port
        raise RuntimeError("ixBrowser client not found (is it running?)")


def ix_base() -> str:
    return "http://127.0.0.1:{}".format(get_ix_port())


CSI_PORT = 10088
CSI_INSTALL_PS = ("$env:CSI_NO_EXTENSION='1'; "
                  "irm https://raw.githubusercontent.com/ximing/csi/master/scripts/install.ps1 | iex")


def csi_reachable() -> bool:
    s = socket.socket(); s.settimeout(2)
    try:
        s.connect(("127.0.0.1", CSI_PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


def find_csi_bin() -> Optional[str]:
    """Locate the CSI daemon binary (ximing/csi)."""
    found = shutil.which("csi")
    if found:
        return found
    home = os.path.expanduser("~")
    cand = os.path.join(home, ".csi", "bin",
                        "csi.exe" if os.name == "nt" else "csi")
    return cand if os.path.isfile(cand) else None


def csi_status() -> Optional[dict]:
    """GET /status from the CSI daemon; None when unreachable."""
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:{}/status".format(CSI_PORT),
                timeout=3) as r:
            env = json.loads(r.read().decode())
        return env if isinstance(env, dict) else None
    except Exception:
        return None


def start_csi_daemon():
    """Start the CSI daemon if it is not running.

    Returns True when the daemon answers, "missing" when the binary is
    not installed, False when the start attempt failed.
    `csi start` is idempotent: safe to run even if the daemon is up.
    """
    if csi_reachable():
        return True
    csi_bin = find_csi_bin()
    if not csi_bin:
        return "missing"
    try:
        if os.name == "nt":
            subprocess.Popen(
                [csi_bin, "start"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, close_fds=True,
                creationflags=subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            subprocess.Popen(
                [csi_bin, "start"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, close_fds=True,
                start_new_session=True)
    except Exception as e:
        log.warning("csi start failed: %s", e)
        return False
    for _ in range(15):
        time.sleep(1)
        if csi_reachable():
            return True
    return False


# ============================================================ bridge app
def build_app(api_key: str, modes: dict, public_url_holder: dict):
    from fastapi import FastAPI, Header, HTTPException, Request
    from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
    from starlette.middleware.gzip import GZipMiddleware
    from pydantic import BaseModel, Field

    app = FastAPI(title="ixBrowser Local Automation Bridge")
    # Big responses (screenshots, profile lists) get gzipped: this fixes the
    # truncated reads seen through the ngrok tunnel.
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # ---------------- auth helpers ----------------
    def check_key_header(x_bridge_key: Optional[str]):
        if x_bridge_key != api_key:
            raise HTTPException(401, "bad or missing X-Bridge-Key")

    def check_key_any(x_bridge_key: Optional[str], key_param: Optional[str]):
        if x_bridge_key == api_key or key_param == api_key:
            return
        raise HTTPException(401, "bad or missing API key")

    def client_ip(request: Request) -> str:
        try:
            return request.client.host
        except Exception:
            return "-"

    def ix_error_message(envelope: dict) -> str:
        err = envelope.get("error") or {}
        return "ixBrowser error {}: {}".format(err.get("code"),
                                               err.get("message"))

    # ---------------- v2.2: warm connection pool ----------------
    async def _pw_driver():
        """One Playwright driver for the whole bridge lifetime."""
        if _PW["pw"] is None:
            from playwright.async_api import async_playwright
            _PW["pw"] = await async_playwright().start()
        return _PW["pw"]

    def _same_url(a: str, b: str) -> bool:
        """True when two URLs point at the same page (ignores trailing /)."""
        def norm(u: str) -> str:
            u = (u or "").strip().lower()
            u = re.sub(r"^https?://", "", u)
            return u.rstrip("/")
        return bool(a and b) and norm(a) == norm(b)

    async def _evict_profile(profile_id: int):
        """Drop a cached CDP connection (never closes the real window)."""
        entry = _CDP.pop(profile_id, None)
        if entry is not None:
            try:
                # For CDP-attached browsers this only drops our session;
                # the ixBrowser window keeps running (same as the old
                # per-task cleanup, which never killed windows either).
                await entry["browser"].close()
            except Exception:
                pass

    async def _attach_profile(profile_id: int, ix: str,
                              retries: int = 2) -> dict:
        """Return a warm (browser, context, page) for a profile.

        Reuses the cached CDP session when it is still alive; otherwise
        opens the profile through ixBrowser (idempotent) and attaches
        fresh. Never closes the real browser window.
        """
        entry = _CDP.get(profile_id)
        if entry is not None:
            try:
                if (entry["browser"].is_connected()
                        and not entry["page"].is_closed()):
                    entry["last_used"] = time.time()
                    entry["reused"] = True
                    return entry
            except Exception:
                pass
            await _evict_profile(profile_id)

        import httpx as _httpx
        last_err = "unknown"
        for attempt in range(max(1, retries)):
            try:
                async with _httpx.AsyncClient(timeout=120.0) as client:
                    res = await client.post(
                        ix + "/api/v2/profile-open",
                        json={"profile_id": profile_id,
                              "cookies_backup": True})
                    env = res.json()
                err = env.get("error") or {}
                if (err.get("code") or 0) != 0:
                    last_err = ix_error_message(env)
                else:
                    ws_url = (env.get("data") or {}).get("ws")
                    if not ws_url:
                        last_err = "profile-open returned no ws URL"
                    else:
                        pw = await _pw_driver()
                        browser = None
                        for _try in range(2):
                            try:
                                browser = await pw.chromium \
                                    .connect_over_cdp(ws_url, timeout=30000)
                                break
                            except Exception:
                                if _try == 0:
                                    await asyncio.sleep(3)
                                else:
                                    raise
                        context = browser.contexts[0]
                        pages = context.pages
                        page = None
                        for pg in pages:
                            try:
                                if "reddit.com" in (pg.url or ""):
                                    page = pg
                                    break
                            except Exception:
                                pass
                        if page is None:
                            page = (pages[0] if pages
                                    else await context.new_page())
                        entry = {"ws_url": ws_url, "browser": browser,
                                 "context": context, "page": page,
                                 "lite": False, "last_used": time.time(),
                                 "reused": attempt > 0}
                        _CDP[profile_id] = entry
                        OPEN_PROFILES.add(profile_id)
                        return entry
            except Exception as e:
                last_err = str(e)[:200]
            await asyncio.sleep(2 * (attempt + 1))
        raise RuntimeError(
            "attach failed for profile {}: {}".format(profile_id, last_err))

    async def _apply_lite(page, enable: bool, entry: Optional[dict]):
        """Block images/fonts/video (data-only tasks load much faster)."""
        import re as _re
        current = bool(entry and entry.get("lite"))
        if current == enable:
            return
        try:
            # We only ever add our own routes, so unroute_all is safe.
            await page.unroute_all(behavior="wait")
        except Exception:
            pass
        if enable:
            pattern = _re.compile(_LITE_RE, _re.I)

            async def _abort(route):
                await route.abort()

            await page.route(pattern, _abort)
        if entry is not None:
            entry["lite"] = enable

    async def _smart_goto(page, target_url: str, entry: Optional[dict],
                          lite: bool = False,
                          wait_selector: Optional[str] = None) -> dict:
        """Navigate only when needed; event-based wait, no fixed sleeps.

        Returns {"navigated": bool}. When lite=True, heavy resources are
        blocked before navigating.
        """
        cur = ""
        try:
            cur = page.url or ""
        except Exception:
            pass
        if _same_url(cur, target_url):
            # Already there: make sure lite state matches, then done.
            await _apply_lite(page, lite, entry)
            return {"navigated": False}
        await _apply_lite(page, lite, entry)
        await page.goto(target_url, wait_until="domcontentloaded",
                        timeout=60000)
        # Event-based wait instead of a blind sleep: page is usable as
        # soon as its app shell renders.
        try:
            await page.wait_for_selector(
                wait_selector or "shreddit-app, main, body", timeout=8000)
        except Exception:
            pass
        return {"navigated": True}

    # ---------------- core profile task ----------------
    async def _run_profile(profile_id: int, target_url: str,
                           evaluate_js=None, screenshot=False,
                           screenshot_max_width=None,
                           wait_for_selector=None, wait_timeout_ms=20000,
                           progress=None,
                           close_after: bool = True,
                           lite: bool = False) -> dict:
        """Open profile -> goto URL -> inspect -> optionally close.
        close_after=False leaves the profile open (no profile-close).
        v2.2: reuses a warm CDP connection, skips navigation when the tab
        is already on target_url, uses event-based waits instead of fixed
        sleeps, and supports lite mode (blocks images/fonts/video for
        data-only tasks)."""
        async def emit(stage: str, extra: Optional[dict] = None):
            if progress:
                payload = {"stage": stage, "profile_id": profile_id,
                           "ts": utcnow_iso()}
                if extra:
                    payload.update(extra)
                await progress(payload)

        t0 = time.time()
        timing: Dict[str, int] = {}
        result: Dict[str, Any] = {"status": "success",
                                  "profile_id": profile_id}
        try:
            ix = ix_base()
        except RuntimeError as e:
            result.update(status="error", error=str(e))
            await emit("error", {"error": str(e)})
            return result

        await emit("opening")
        try:
            try:
                entry = await _attach_profile(profile_id, ix)
            except RuntimeError as e:
                result.update(status="error", error=str(e))
                await emit("error", {"error": result["error"]})
                return result
            timing["open_ms"] = int((time.time() - t0) * 1000)
            result["reused_connection"] = bool(entry.get("reused"))
            await emit("opened", {"open_ms": timing["open_ms"],
                                  "reused": result["reused_connection"]})
            context = entry["context"]
            page = entry["page"]
            try:
                await emit("navigating", {"url": target_url})
                nav = await _smart_goto(
                    page, target_url, entry,
                    lite=(lite and not screenshot),
                    wait_selector=wait_for_selector)
                timing["nav_ms"] = int((time.time() - t0) * 1000)
                result["navigated"] = nav["navigated"]
                await emit("navigated", {"nav_ms": timing["nav_ms"],
                                         "navigated": nav["navigated"]})
                if wait_for_selector:
                    try:
                        await page.wait_for_selector(
                            wait_for_selector,
                            timeout=wait_timeout_ms)
                    except Exception as e:
                        result["wait_warning"] = \
                            "selector not found: {}".format(e)[:200]
                result["page_title"] = await page.title()
                result["page_url"] = page.url
                cookies = await context.cookies()
                result["cookie_count"] = len(cookies)
                result["cookie_names"] = sorted(
                    {c["name"] for c in cookies})
                result["reddit_session"] = \
                    "reddit_session" in result["cookie_names"]
                if evaluate_js:
                    try:
                        result["eval_result"] = await page.evaluate(
                            evaluate_js)
                    except Exception as e:
                        result["eval_error"] = str(e)[:300]
                if screenshot:
                    if screenshot_max_width:
                        vp = page.viewport_size or {"width": 1366,
                                                 "height": 900}
                        if vp["width"] > screenshot_max_width:
                            await page.set_viewport_size(
                                {"width": screenshot_max_width,
                                 "height": max(400, int(
                                     vp["height"] * screenshot_max_width
                                     / vp["width"]))})
                    shot = await page.screenshot(type="jpeg",
                                                 quality=60)
                    result["screenshot_b64"] = base64.b64encode(
                        shot).decode()
                await emit("inspected")
            except Exception as e:
                result["status"] = "task_error"
                result["error"] = str(e)[:300]
                await emit("error", {"error": result["error"]})
        finally:
            if close_after:
                await _evict_profile(profile_id)
                try:
                    import httpx as _httpx2
                    async with _httpx2.AsyncClient(timeout=30.0) as client:
                        await client.post(ix + "/api/v2/profile-close",
                                          json={"profile_id": profile_id})
                except Exception as e:
                    log.warning("profile-close failed (tolerated): %s", e)
                OPEN_PROFILES.discard(profile_id)
            # else: profile intentionally left open (connection stays warm)
        timing["total_ms"] = int((time.time() - t0) * 1000)
        result["timing_ms"] = timing
        await emit("done", {"total_ms": timing["total_ms"]})
        return result

    # ---------------- /health ----------------
    @app.get("/health")
    def health():
        try:
            ix_port = get_ix_port()
            ix_ok = True
        except RuntimeError:
            ix_port, ix_ok = None, False
        return {"ok": True, "version": BRIDGE_VERSION,
                "uptime_s": int(time.time() - START_TIME),
                "modes": modes, "ix_port": ix_port,
                "ix_reachable": ix_ok, "csi_reachable": csi_reachable(),
                "mcp": bool(public_url_holder.get("mcp")),
                "public_url": public_url_holder.get("url")}

    # ---------------- /tools ----------------
    @app.get("/tools")
    def tools_catalog(x_bridge_key: Optional[str] = Header(default=None)):
        """Plain-language catalog of every endpoint, for humans and AI tools."""
        check_key_header(x_bridge_key)
        catalog = []

        def add(name, method, path, desc, args=None, example=None):
            catalog.append({"name": name, "method": method, "path": path,
                            "description": desc, "args": args or {},
                            "example": example or {}})

        add("health", "GET", "/health",
            "Liveness probe. Returns version, uptime, which services are on, "
            "ixBrowser port, CSI reachability. No key needed.")
        if modes.get("ix"):
            add("ixbrowser_api", "POST", "/ix/{path}",
                "Call any ixBrowser local API path and get its answer back. "
                "Example path: api/v2/profile-list. Body is forwarded as JSON.",
                {"path": "ixBrowser API path, without leading slash",
                 "body": "JSON object forwarded to ixBrowser"},
                {"path": "api/v2/profile-list",
                 "body": {"page": 1, "page_size": 10}})
            add("profiles_compact", "POST", "/ixc/profile-list",
                "Small profile list: only profile_id, name and group_name. "
                "Use this instead of the full list when responses get cut off.",
                {"page": "page number, default 1",
                 "page_size": "profiles per page, default 100"})
            add("open_profile", "POST", "/task/run",
                "Open one ixBrowser profile in a real browser, load a page, "
                "report title/cookies/timing, then close the profile. "
                "reddit_session=true in the answer means logged in.",
                {"profile_id": "number, required",
                 "target_url": "page to open, default https://www.reddit.com",
                 "screenshot": "true/false, default false",
                 "screenshot_max_width": "optional, shrink screenshot",
                 "evaluate_js": "optional JS to run on the page",
                 "wait_for_selector": "optional CSS selector to wait for",
                 "webhook_url": "optional URL to POST the result to"},
                {"profile_id": 193, "target_url": "https://www.reddit.com",
                 "lite": "true = block images/fonts/video for data-only "
                         "tasks (much faster page loads)"},
                {"profile_id": 193, "target_url": "https://www.reddit.com"})
            add("open_profile_stream", "GET", "/task/run_stream",
                "Same as open_profile but streams live progress events "
                "(opening, connected, navigated, done) as SSE. "
                "Pass the key as ?key=... query param.",
                {"profile_id": "number, required", "key": "API key",
                 "target_url": "page to open", "screenshot": "true/false"})
            add("open_profiles_batch", "POST", "/task/run_many",
                "Check many profiles with a single call. v2.2: runs IN "
                "PARALLEL by default (up to 10 at once) and reuses warm "
                "browser connections, so repeat checks are much faster. "
                "Returns compact results (no screenshots by default). "
                "close_after=false leaves every profile open. Results are "
                "also saved to results/ on the PC, and the loop aborts if "
                "the client disconnects.",
                {"profile_ids": "list of numbers, required, max 50",
                 "target_url": "page to open",
                 "screenshot": "true/false, default false",
                 "close_after": "true/false, default true",
                 "parallel": "true/false, default true",
                 "max_concurrency": "1-10, default 4",
                 "lite": "true = block images/fonts/video (faster loads)"},
                {"profile_ids": [193, 194],
                 "target_url": "https://www.reddit.com"})
            add("open_profiles_parallel", "POST", "/task/open_many",
                "Open many profiles IN PARALLEL (up to 10 at once) and "
                "leave them all open. Each profile ends with one tab on "
                "target_url; other tabs are closed. Flaky opens are "
                "retried automatically. v2.2: warm connections are reused "
                "and navigation is skipped when the tab is already on the "
                "target page. Nothing is closed afterwards.",
                {"profile_ids": "list of numbers, required, max 50",
                 "target_url": "page to open, default https://www.reddit.com",
                 "max_concurrency": "1-10, default 4",
                 "retries": "1-5, default 2",
                 "close_other_tabs": "true/false, default true",
                 "lite": "true = block images/fonts/video (faster loads)"},
                {"profile_ids": [193, 194, 195]})
            add("open_profiles_list", "GET", "/task/open_profiles",
                "Profiles the bridge currently has open (not yet closed). "
                "v2.2 keeps a warm browser connection per listed profile, "
                "reused across tasks.",
                {}, {})
        if modes.get("term"):
            add("terminal", "POST", "/exec",
                "Run a terminal command on this PC and get the output. "
                "Full shell access. Every call is written to audit.log.",
                {"command": "string, required",
                 "timeout_s": "1-300, default 60",
                 "cwd": "optional working directory"},
                {"command": "whoami"})
        if modes.get("csi"):
            add("chrome_csi", "POST", "/csi",
                "Drive the user's real logged-in Chrome through the CSI "
                "daemon (port 10088). Actions mirror the csi-remote CLI: "
                "list_tabs, navigate, snapshot, evaluate, click, fill, "
                "close_tab, close_session, ...",
                {"action": "string, required", "args": "object",
                 "session": "tab-group name"},
                {"action": "navigate",
                 "args": {"url": "https://example.com", "newTab": True},
                 "session": "work"})
        add("dashboard", "GET", "/dashboard",
            "Status page in a browser: services, recent tasks, log tail. "
            "Open /dashboard?key=YOUR_API_KEY",
            {"key": "API key as query param"})
        add("tasks_recent", "GET", "/tasks/recent",
            "Last 50 task runs with timing, for the dashboard and operators.")
        return {"bridge": "ixbrowser-bridge v" + BRIDGE_VERSION,
                "modes": modes, "tools": catalog}

    # ---------------- ix routes ----------------
    if modes.get("ix"):
        @app.post("/ix/{path:path}")
        async def ix_passthrough(path: str, request: Request,
                                 body: Optional[Dict[str, Any]] = None,
                                 x_bridge_key: Optional[str] = Header(
                                     default=None)):
            """Forward any ixBrowser local-API call. Returns its answer."""
            check_key_header(x_bridge_key)
            import httpx as _httpx
            try:
                ix = ix_base()
            except RuntimeError as e:
                raise HTTPException(502, str(e))
            url = "{}/{}".format(ix, path)
            try:
                async with _httpx.AsyncClient(timeout=60.0) as client:
                    res = await client.post(url, json=body or {})
            except _httpx.ConnectError:
                raise HTTPException(502, "cannot reach ixBrowser at " + ix)
            try:
                return JSONResponse(content=res.json(),
                                    status_code=res.status_code)
            except ValueError:
                raise HTTPException(502,
                                    "non-JSON response from ixBrowser: " + url)

        @app.post("/ixc/profile-list")
        async def profiles_compact(request: Request,
                                   body: Optional[Dict[str, Any]] = None,
                                   x_bridge_key: Optional[str] = Header(
                                       default=None)):
            """Compact profile list: id, name, group only (never truncated)."""
            check_key_header(x_bridge_key)
            import httpx as _httpx
            try:
                ix = ix_base()
            except RuntimeError as e:
                raise HTTPException(502, str(e))
            page = (body or {}).get("page", 1)
            page_size = min(int((body or {}).get("page_size", 100)), 200)
            try:
                async with _httpx.AsyncClient(timeout=60.0) as client:
                    res = await client.post(
                        ix + "/api/v2/profile-list",
                        json={"page": page, "page_size": page_size})
                    env = res.json()
            except Exception as e:
                raise HTTPException(502, "ixBrowser unreachable: "
                                         + str(e)[:200])
            items = ((env.get("data") or {}).get("data")) or []
            compact = [{"profile_id": p.get("profile_id"),
                        "name": p.get("name"),
                        "group_name": p.get("group_name")} for p in items]
            audit_log("ixc/profile-list",
                      "page={} size={}".format(page, page_size),
                      client_ip(request))
            return {"profiles": compact,
                    "total": ((env.get("data") or {}).get("total"))}

        class TaskRequest(BaseModel):
            profile_id: int
            target_url: str = "https://www.reddit.com"
            evaluate_js: Optional[str] = None
            screenshot: bool = False
            screenshot_max_width: Optional[int] = None
            wait_for_selector: Optional[str] = None
            wait_timeout_ms: int = Field(default=20000, le=120000)
            webhook_url: Optional[str] = None
            close_after: bool = True
            lite: bool = False

        async def _post_webhook(url: str, payload: dict):
            import httpx as _httpx
            try:
                async with _httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(url, json=payload)
            except Exception as e:
                log.warning("webhook POST failed: %s", e)

        @app.post("/task/run")
        async def run_task(req: TaskRequest, request: Request,
                           x_bridge_key: Optional[str] = Header(
                               default=None)):
            """Open profile -> goto URL -> inspect -> close."""
            check_key_header(x_bridge_key)
            result = await _run_profile(
                req.profile_id, req.target_url, req.evaluate_js,
                req.screenshot, req.screenshot_max_width,
                req.wait_for_selector, req.wait_timeout_ms,
                close_after=req.close_after, lite=req.lite)
            audit_log("task/run", "profile_id={} status={}".format(
                req.profile_id, result.get("status")), client_ip(request))
            record_task("task", "profile {}".format(req.profile_id),
                        result.get("status"),
                        (result.get("timing_ms") or {}).get("total_ms"))
            if req.webhook_url:
                asyncio.create_task(_post_webhook(req.webhook_url, result))
            return result

        @app.get("/task/run_stream")
        async def run_task_stream(
                profile_id: int, request: Request,
                target_url: str = "https://www.reddit.com",
                screenshot: bool = False,
                close_after: bool = True,
                key: Optional[str] = None,
                x_bridge_key: Optional[str] = Header(default=None)):
            """Same as /task/run but streams live progress as SSE."""
            check_key_any(x_bridge_key, key)

            async def gen():
                q: asyncio.Queue = asyncio.Queue()

                async def progress(payload: dict):
                    await q.put(payload)

                task = asyncio.create_task(_run_profile(
                    profile_id, target_url, None, screenshot, None,
                    None, 20000, progress, close_after=close_after))
                try:
                    while True:
                        try:
                            payload = await asyncio.wait_for(q.get(),
                                                             timeout=150)
                        except asyncio.TimeoutError:
                            yield ": keep-alive\n\n"
                            continue
                        yield "data: {}\n\n".format(json.dumps(payload))
                        if payload.get("stage") in ("done", "error"):
                            break
                    final = await task
                    yield "data: {}\n\n".format(json.dumps(
                        {"stage": "result", "result": final}))
                    audit_log("task/run_stream",
                              "profile_id={} status={}".format(
                                  profile_id, final.get("status")),
                              client_ip(request))
                    record_task("task_stream",
                                "profile {}".format(profile_id),
                                final.get("status"),
                                (final.get("timing_ms") or {}).get(
                                    "total_ms"))
                finally:
                    if not task.done():
                        task.cancel()

            return StreamingResponse(gen(),
                                     media_type="text/event-stream")

        class RunManyRequest(BaseModel):
            profile_ids: List[int]
            target_url: str = "https://www.reddit.com"
            screenshot: bool = False
            max_profiles: int = Field(default=50, le=100)
            close_after: bool = True
            parallel: bool = True
            max_concurrency: int = Field(default=4, ge=1, le=10)
            lite: bool = False

        def _compact_run_result(pid: int, r: dict) -> dict:
            return {
                "profile_id": pid, "status": r.get("status"),
                "page_title": r.get("page_title"),
                "cookie_count": r.get("cookie_count"),
                "reddit_session": r.get("reddit_session", False),
                "reused_connection": r.get("reused_connection", False),
                "navigated": r.get("navigated"),
                "timing_ms": r.get("timing_ms"),
                "error": r.get("error")}

        @app.post("/task/run_many")
        async def run_many(req: RunManyRequest, request: Request,
                           x_bridge_key: Optional[str] = Header(
                               default=None)):
            """Check many profiles; compact results, one call.

            v2.2: parallel by default (same semaphore+stagger pattern as
            open_many). Set parallel=false for the old sequential walk.
            """
            check_key_header(x_bridge_key)
            ids = req.profile_ids[:req.max_profiles]

            async def _one(pid: int):
                if await request.is_disconnected():
                    return {"profile_id": pid, "status": "aborted",
                            "error": "client disconnected"}
                r = await _run_profile(pid, req.target_url, None,
                                       req.screenshot, None, None, 20000,
                                       close_after=req.close_after,
                                       lite=req.lite)
                return _compact_run_result(pid, r)

            results = []
            if req.parallel:
                sem = asyncio.Semaphore(max(1, min(req.max_concurrency,
                                                   10)))

                async def _guarded(pid: int, idx: int):
                    async with sem:
                        if await request.is_disconnected():
                            return {"profile_id": pid, "status": "aborted",
                                    "error": "client disconnected"}
                        await asyncio.sleep(0.3 * idx)  # stagger launches
                        return await _one(pid)

                results = list(await asyncio.gather(
                    *[_guarded(pid, i) for i, pid in enumerate(ids)]))
            else:
                for pid in ids:
                    if await request.is_disconnected():
                        # client went away (e.g. operator hit stop): stop
                        # opening more profiles instead of churning solo
                        results.append({"profile_id": pid,
                                        "status": "aborted",
                                        "error": "client disconnected"})
                        break
                    results.append(await _one(pid))
                    await asyncio.sleep(2)  # breathe between profiles
            ok_n = sum(1 for r in results if r["status"] == "success")
            audit_log("task/run_many",
                      "profiles={} ok={}".format(len(ids), ok_n),
                      client_ip(request))
            record_task("batch", "{} profiles".format(len(ids)),
                        "ok {}/{}".format(ok_n, len(ids)))
            try:
                os.makedirs("results", exist_ok=True)
                with open("results/run_many-{}.json".format(
                        time.strftime("%Y%m%d-%H%M%S")), "w",
                        encoding="utf-8") as f:
                    json.dump({"results": results,
                               "summary": {"checked": len(ids), "ok": ok_n},
                               "close_after": req.close_after,
                               "parallel": req.parallel}, f, indent=1)
            except Exception as e:
                log.warning("could not persist run_many results: %s", e)
            return {"results": results,
                    "summary": {"checked": len(ids), "ok": ok_n}}

        @app.get("/task/open_profiles")
        async def open_profiles(request: Request,
                                x_bridge_key: Optional[str] = Header(
                                    default=None)):
            """Profiles the bridge currently has open (not yet closed)."""
            check_key_header(x_bridge_key)
            return {"open_profiles": sorted(OPEN_PROFILES)}

        async def _open_leave_open(profile_id: int, target_url: str,
                                   close_other_tabs: bool,
                                   retries: int,
                                   lite: bool = False) -> dict:
            """Open profile (idempotent), tidy tabs, goto URL, leave open.

            Never calls browser.close() or profile-close. Retries the
            flaky ixBrowser open. The last tab is never closed (that
            would kill the window); one tab is reused for navigation.
            v2.2: warm connection reuse + smart goto (no reload when the
            tab is already on target_url) + optional lite mode.
            """
            last_err = "unknown"
            for attempt in range(max(1, retries)):
                try:
                    ix = ix_base()
                    entry = await _attach_profile(profile_id, ix,
                                                  retries=1)
                    context = entry["context"]
                    keep = entry["page"]
                    if close_other_tabs:
                        for pg in list(context.pages):
                            if pg is not keep:
                                try:
                                    await pg.close()
                                except Exception:
                                    pass
                    nav = await _smart_goto(keep, target_url, entry,
                                            lite=lite)
                    if close_other_tabs:
                        for pg in list(context.pages):
                            if pg is not keep:
                                try:
                                    await pg.close()
                                except Exception:
                                    pass
                    return {"profile_id": profile_id,
                            "status": "open",
                            "tabs": len(context.pages),
                            "navigated": nav["navigated"],
                            "reused_connection": bool(entry.get("reused"))}
                except Exception as e:
                    last_err = str(e)[:200]
                await asyncio.sleep(2 * (attempt + 1))
            return {"profile_id": profile_id, "status": "error",
                    "error": last_err}

        class OpenManyRequest(BaseModel):
            profile_ids: List[int]
            target_url: str = "https://www.reddit.com"
            max_concurrency: int = Field(default=4, ge=1, le=10)
            retries: int = Field(default=2, ge=1, le=5)
            close_other_tabs: bool = True
            lite: bool = False
            max_profiles: int = Field(default=50, le=100)

        @app.post("/task/open_many")
        async def open_many(req: OpenManyRequest, request: Request,
                            x_bridge_key: Optional[str] = Header(
                                default=None)):
            """Open many profiles IN PARALLEL and leave them open.

            Each profile ends with one tab on target_url; other tabs are
            closed. Flaky opens are retried. Nothing is closed afterwards.
            """
            check_key_header(x_bridge_key)
            ids = req.profile_ids[:req.max_profiles]
            sem = asyncio.Semaphore(max(1, min(req.max_concurrency, 10)))

            async def one(pid: int, idx: int):
                async with sem:
                    if await request.is_disconnected():
                        return {"profile_id": pid, "status": "aborted",
                                "error": "client disconnected"}
                    await asyncio.sleep(0.3 * idx)  # stagger launches
                    return await _open_leave_open(
                        pid, req.target_url, req.close_other_tabs,
                        req.retries, lite=req.lite)

            results = list(await asyncio.gather(
                *[one(pid, i) for i, pid in enumerate(ids)]))
            ok_n = sum(1 for r in results if r.get("status") == "open")
            audit_log("task/open_many",
                      "profiles={} ok={}".format(len(ids), ok_n),
                      client_ip(request))
            record_task("batch_open", "{} profiles".format(len(ids)),
                        "ok {}/{}".format(ok_n, len(ids)))
            try:
                os.makedirs("results", exist_ok=True)
                with open("results/open_many-{}.json".format(
                        time.strftime("%Y%m%d-%H%M%S")), "w",
                        encoding="utf-8") as f:
                    json.dump({"results": results,
                               "summary": {"opened": len(ids), "ok": ok_n}},
                              f, indent=1)
            except Exception as e:
                log.warning("could not persist open_many results: %s", e)
            return {"results": results,
                    "summary": {"opened": len(ids), "ok": ok_n}}

    # ---------------- terminal ----------------
    if modes.get("term"):
        class ExecRequest(BaseModel):
            command: str
            timeout_s: int = Field(default=60, le=300)
            cwd: Optional[str] = None

        @app.post("/exec")
        async def exec_command(req: ExecRequest, request: Request,
                               x_bridge_key: Optional[str] = Header(
                                   default=None)):
            """Run a terminal command on this PC. Logged to audit.log."""
            check_key_header(x_bridge_key)
            ip = client_ip(request)
            audit_log("exec", "cmd: {}".format(req.command), ip)
            t0 = time.time()
            try:
                proc = await asyncio.create_subprocess_shell(
                    req.command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=req.cwd or None)
                try:
                    out, _ = await asyncio.wait_for(proc.communicate(),
                                                    timeout=req.timeout_s)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    record_task("exec", req.command[:80], "timeout")
                    return {"ok": False,
                            "error": "timed out after {}s".format(
                                req.timeout_s)}
                text = out.decode(errors="replace")
                if len(text) > 200000:
                    text = text[:200000] + "\n...[output truncated]..."
                record_task("exec", req.command[:80],
                            "ok" if proc.returncode == 0 else "rc={}".format(
                                proc.returncode),
                            int((time.time() - t0) * 1000))
                return {"ok": proc.returncode == 0,
                        "returncode": proc.returncode, "output": text}
            except Exception as e:
                raise HTTPException(500, str(e)[:300])

    # ---------------- CSI ----------------
    if modes.get("csi"):
        class CsiRequest(BaseModel):
            action: str
            args: dict = Field(default_factory=dict)
            session: Optional[str] = None

        @app.post("/csi")
        async def csi_forward(req: CsiRequest, request: Request,
                              x_bridge_key: Optional[str] = Header(
                                  default=None)):
            """Forward a command to the CSI daemon (real Chrome, port 10088)."""
            check_key_header(x_bridge_key)
            body = {"action": req.action, "args": req.args or {}}
            if req.session:
                body["session"] = req.session

            def _call():
                data = json.dumps(body).encode()
                creq = urllib.request.Request(
                    "http://127.0.0.1:10088/command", data=data,
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(creq, timeout=120) as r:
                    return r.read().decode()

            t0 = time.time()
            try:
                text = await asyncio.to_thread(_call)
            except Exception as e:
                raise HTTPException(
                    502, "CSI daemon unreachable on 127.0.0.1:10088: "
                         + str(e)[:200])
            audit_log("csi", "action={} session={}".format(
                req.action, req.session), client_ip(request))
            record_task("csi", "{} {}".format(req.action, req.session or ""),
                        "ok", int((time.time() - t0) * 1000))
            try:
                return json.loads(text)
            except Exception:
                return {"ok": False, "raw": text[:2000]}

    # ---------------- recent tasks + dashboard ----------------
    @app.get("/tasks/recent")
    def tasks_recent(x_bridge_key: Optional[str] = Header(default=None)):
        check_key_header(x_bridge_key)
        return {"tasks": list(TASK_HISTORY)}

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(key: Optional[str] = None):
        if key != api_key:
            return HTMLResponse(
                "<h3>Need ?key=YOUR_API_KEY in the URL.</h3>", status_code=401)
        try:
            ix_port = get_ix_port()
            ix_ok = True
        except RuntimeError:
            ix_port, ix_ok = None, False

        def dot(ok_: bool) -> str:
            return ("<span style='color:#16a34a'>● ON</span>" if ok_
                    else "<span style='color:#dc2626'>● OFF</span>")

        rows = "".join(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>"
            .format(html.escape(t["ts"]), html.escape(t["kind"]),
                    html.escape(t["detail"]), html.escape(t["status"]),
                    t["ms"] if t["ms"] is not None else "-")
            for t in list(TASK_HISTORY)[:30])
        try:
            with open(LOG_FILE, "r", encoding="utf-8",
                      errors="replace") as f:
                tail = f.readlines()[-25:]
        except OSError:
            tail = ["(no log yet)"]
        log_html = html.escape("".join(tail))

        mode_txt = ", ".join(
            "{}={}".format(k, "on" if v else "off")
            for k, v in modes.items())
        up_min = int((time.time() - START_TIME) / 60)
        return """<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="20">
<title>ixBridge dashboard</title>
<style>body{{font-family:Arial,sans-serif;margin:24px;background:#f8fafc;color:#111}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:16px;margin-bottom:16px}}
h2{{margin:0 0 12px}}table{{border-collapse:collapse;width:100%;font-size:13px}}
td,th{{border:1px solid #e2e8f0;padding:6px 8px;text-align:left}}
pre{{background:#0f172a;color:#e2e8f0;padding:12px;border-radius:8px;overflow:auto;font-size:12px}}
.k{{color:#64748b;font-size:13px}}</style></head><body>
<h2>ixBridge dashboard <span class="k">v{ver} · up {upm} min</span></h2>
<div class="card"><b>Services</b> <span class="k">{modes}</span><br><br>
<b>Local URL</b> <span class="k">(for AI tools on this PC)</span>: {local}<br>
<b>Public URL</b>: {pub}<br><b>MCP</b>: {mcp}<br><br>
ixBrowser: {ixdot} <span class="k">port {ixport}</span><br>
CSI (real Chrome): {csidot} <span class="k">port 10088</span></div>
<div class="card"><b>Recent tasks</b>
<table><tr><th>Time (UTC)</th><th>Kind</th><th>Detail</th><th>Status</th><th>ms</th></tr>
{rows}</table></div>
<div class="card"><b>Log tail</b> <span class="k">(refreshes every 20s)</span>
<pre>{loghtml}</pre></div>
</body></html>""".format(
            ver=BRIDGE_VERSION, upm=up_min, modes=html.escape(mode_txt),
            local=html.escape(public_url_holder.get("local", "-")),
            pub=html.escape(public_url_holder.get("url") or "tunnel off"),
            mcp=dot(bool(public_url_holder.get("mcp"))),
            ixdot=dot(ix_ok), ixport=ix_port or "-",
            csidot=dot(csi_reachable()), rows=rows or
            "<tr><td colspan=5>No tasks yet</td></tr>", loghtml=log_html)

    # ---------------- MCP server mode (optional) ----------------
    def mount_mcp() -> bool:
        """Expose bridge tools over MCP SSE for local AI (Claude Code etc).

        Requires: pip install mcp
        """
        try:
            from mcp.server.fastmcp import FastMCP
        except ImportError:
            log.warning("mcp package not installed: MCP mode disabled "
                        "(run: pip install mcp)")
            return False
        try:
            mcp = FastMCP("ixbridge")

            @mcp.tool()
            def bridge_health() -> str:
                """Check bridge status: version, uptime, services on/off."""
                return json.dumps(health())

            if modes.get("ix"):
                @mcp.tool()
                def ix_profile_list_compact(page: int = 1) -> str:
                    """List ixBrowser profiles (id, name, group only)."""
                    import httpx as _h
                    ix = ix_base()
                    r = _h.post(ix + "/api/v2/profile-list",
                                json={"page": page, "page_size": 100},
                                timeout=60.0)
                    items = ((r.json().get("data") or {}).get("data")) or []
                    return json.dumps([{"profile_id": p.get("profile_id"),
                                        "name": p.get("name"),
                                        "group_name": p.get("group_name")}
                                       for p in items])

                @mcp.tool()
                async def ix_open_profile(profile_id: int,
                                          target_url: str = "https://www.reddit.com") -> str:
                    """Open an ixBrowser profile, load a page, report login
                    state (reddit_session=true means logged in), close it."""
                    r = await _run_profile(profile_id, target_url)
                    r.pop("screenshot_b64", None)
                    r.pop("cookie_names", None)
                    return json.dumps(r)

            if modes.get("term"):
                @mcp.tool()
                async def terminal_exec(command: str,
                                        timeout_s: int = 60) -> str:
                    """Run a terminal command on this PC, return output."""
                    proc = await asyncio.create_subprocess_shell(
                        command, stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT)
                    try:
                        out, _ = await asyncio.wait_for(
                            proc.communicate(),
                            timeout=min(timeout_s, 300))
                    except asyncio.TimeoutError:
                        proc.kill()
                        return json.dumps({"ok": False,
                                           "error": "timed out"})
                    audit_log("mcp/exec", "cmd: " + command, "mcp-local")
                    return json.dumps({"ok": proc.returncode == 0,
                                       "output": out.decode(
                                           errors="replace")[:50000]})

            if modes.get("csi"):
                @mcp.tool()
                async def chrome_csi(action: str, args: str = "{}",
                                     session: str = "") -> str:
                    """Drive the real Chrome via CSI. action like
                    list_tabs, navigate, snapshot, evaluate, close_session."""
                    body = {"action": action, "args": json.loads(args or "{}")}
                    if session:
                        body["session"] = session

                    def _call():
                        data = json.dumps(body).encode()
                        creq = urllib.request.Request(
                            "http://127.0.0.1:10088/command", data=data,
                            headers={"Content-Type": "application/json"})
                        with urllib.request.urlopen(creq,
                                                     timeout=120) as r:
                            return r.read().decode()

                    text = await asyncio.to_thread(_call)
                    audit_log("mcp/csi",
                              "action={}".format(action), "mcp-local")
                    return text[:50000]

            app.mount("/mcp", mcp.sse_app())
            log.info("MCP server mounted at /mcp (SSE)")
            return True
        except Exception as e:
            log.warning("MCP mount failed: %s", e)
            return False

    public_url_holder["mcp"] = mount_mcp()
    return app


# ============================================================ system helpers
def port_free(port: int) -> bool:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def tcp_reachable(host, port, timeout=3) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def wait_for_health(port: int, timeout: int = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:{}/health".format(port), timeout=3) as r:
                if json.loads(r.read().decode()).get("ok"):
                    return True
        except Exception:
            time.sleep(1)
    return False


def list_tunnels():
    """All tunnels from ngrok's local inspector API (empty list if none)."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels",
                                    timeout=5) as r:
            return json.loads(r.read().decode()).get("tunnels", [])
    except Exception:
        return []


def find_http_tunnel(port: int, timeout: int = 60) -> Optional[str]:
    """Public https URL of an ngrok tunnel pointing at `port`, or None."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for t in list_tunnels():
            addr = str((t.get("config") or {}).get("addr", ""))
            if (t.get("proto") == "https" and str(port) in addr
                    and t.get("public_url")):
                return t["public_url"]
        time.sleep(2)
    return None


def self_test(public_url: str) -> bool:
    try:
        req = urllib.request.Request(
            public_url + "/health",
            headers={"ngrok-skip-browser-warning": "true"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode()).get("ok", False)
    except Exception as e:
        log.warning("tunnel self-test failed: %s", e)
        return False


def copy_to_clipboard(text) -> bool:
    try:
        if os.name == "nt":
            subprocess.run(["clip"], input=text.encode(), timeout=5, check=True)
        else:
            subprocess.run(["xclip", "-selection", "clipboard"],
                           input=text.encode(), timeout=5, check=True)
        return True
    except Exception:
        return False


def load_saved_key() -> Optional[str]:
    try:
        with open(KEY_FILE, "r") as f:
            return f.read().strip() or None
    except OSError:
        return None


def save_key(key: str):
    with open(KEY_FILE, "w") as f:
        f.write(key)
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass


def check_packages():
    missing = []
    for pkg in REQUIRED_PACKAGES:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    return missing


def check_optional_mcp() -> bool:
    try:
        __import__("mcp")
        return True
    except ImportError:
        return False


# ============================================================ watchdog
def watchdog_loop(stop_event: threading.Event, port: int,
                  ngrok_bin: str, state: dict):
    """Background watchdog: ngrok alive? tunnel present? ixBrowser port fresh?

    state holds {"ngrok_proc": Popen|None, "public_url": str|None,
                 "started_ngrok": bool}.
    """
    ix_refresh = 0
    log.info("watchdog started (30s interval)")
    while not stop_event.wait(30):
        try:
            proc = state.get("ngrok_proc")
            if state.get("started_ngrok") and proc is not None \
                    and proc.poll() is not None:
                log.warning("watchdog: ngrok died, restarting ...")
                try:
                    new_proc = subprocess.Popen(
                        [ngrok_bin, "http", str(port), "--log=stdout"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.STDOUT)
                    time.sleep(3)
                    if new_proc.poll() is None:
                        url = find_http_tunnel(port, timeout=45)
                        state["ngrok_proc"] = new_proc
                        if url:
                            state["public_url"] = url
                            log.info("watchdog: ngrok restarted, URL=%s", url)
                            try:
                                with open(INFO_FILE, "a",
                                          encoding="utf-8") as f:
                                    f.write("tunnel restarted {}: {}\n"
                                            .format(utcnow_iso(), url))
                            except OSError:
                                pass
                        else:
                            log.warning("watchdog: ngrok restarted but no "
                                        "public URL appeared")
                    else:
                        log.warning("watchdog: ngrok restart failed "
                                    "(check login: ngrok config "
                                    "add-authtoken <token>)")
                        state["ngrok_proc"] = None
                except Exception as e:
                    log.warning("watchdog: ngrok restart error: %s", e)
            else:
                # tunnel still pointing at our port?
                if not any(str(port) in str(
                        (t.get("config") or {}).get("addr", ""))
                        and t.get("proto") == "https"
                        for t in list_tunnels()):
                    log.warning("watchdog: no ngrok https tunnel found for "
                                "port %s", port)
            # refresh ixBrowser port every ~5 minutes
            ix_refresh += 1
            if ix_refresh >= 10:
                ix_refresh = 0
                try:
                    p = get_ix_port(force=True)
                    log.info("watchdog: ixBrowser port re-detected: %s", p)
                except RuntimeError:
                    log.warning("watchdog: ixBrowser client not reachable")
        except Exception as e:
            log.warning("watchdog error: %s", e)
    log.info("watchdog stopped")


def close_leftover_profiles():
    """Best effort: close any ixBrowser profiles we opened but never closed."""
    # v2.2: drop warm CDP sessions first (detach only; the real windows
    # stay alive until profile-close below runs).
    if _CDP:
        print("  Dropping {} warm browser session(s) ...".format(len(_CDP)))
        _CDP.clear()
    pw = _PW.get("pw")
    if pw is not None:
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(pw.stop())
            loop.close()
        except Exception:
            pass
        _PW["pw"] = None
    if not OPEN_PROFILES:
        return
    print("  Closing {} leftover profile(s) ...".format(len(OPEN_PROFILES)))
    try:
        ix = ix_base()
    except RuntimeError:
        return
    for pid in list(OPEN_PROFILES):
        try:
            data = json.dumps({"profile_id": pid}).encode()
            req = urllib.request.Request(
                ix + "/api/v2/profile-close", data=data,
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15)
            OPEN_PROFILES.discard(pid)
        except Exception as e:
            log.warning("leftover close failed for %s: %s", pid, e)


# ============================================================ menu actions
def action_diagnostics():
    banner("Setup diagnostics")
    passed, total = 0, 8

    print("1. Python version")
    if sys.version_info >= (3, 10):
        ok("Python {} (3.10+ required)".format(sys.version.split()[0])); passed += 1
    else:
        warn("Python {} - please install Python 3.10 or newer".format(sys.version.split()[0]))

    print("2. Python packages")
    missing = check_packages()
    if not missing:
        ok("fastapi, uvicorn, httpx, playwright, pydantic all installed"); passed += 1
    else:
        warn("Missing: " + ", ".join(missing))

    print("3. ngrok")
    if shutil.which("ngrok"):
        ok("ngrok found on PATH"); passed += 1
    else:
        warn("ngrok not found - download: https://ngrok.com/download")

    print("4. ixBrowser client (auto-detect)")
    try:
        p = get_ix_port(force=True)
        ok("ixBrowser local API found on port {}".format(p)); passed += 1
    except RuntimeError:
        warn("ixBrowser not reachable - is the desktop client running?")

    print("5. CSI daemon (your real Chrome)")
    st = csi_status()
    if st:
        ok("CSI daemon answering on 127.0.0.1:{}".format(CSI_PORT)); passed += 1
        if st.get("extension_connected"):
            ok("Chrome extension connected")
        else:
            warn("Daemon up but Chrome extension not connected - open Chrome")
    else:
        warn("CSI daemon not found - option 1 will try to start it")

    print("6. Saved API key")
    key = load_saved_key()
    if key:
        ok("A saved API key exists ({}...)".format(key[:4])); passed += 1
    else:
        warn("No saved API key yet - option 1 will create one")

    print("7. MCP support (optional)")
    if check_optional_mcp():
        ok("mcp package installed - MCP server mode will work"); passed += 1
    else:
        warn("mcp not installed - optional; run: pip install mcp")

    print("8. Log files")
    ok("Logs go to:\n      {}\n      {}".format(LOG_FILE, AUDIT_FILE))
    passed += 1

    cfg = load_config()
    print()
    print("  Saved mode: " + ", ".join(
        "{}={}".format(k, "on" if v else "off")
        for k, v in cfg["modes"].items()))
    print()
    line("-")
    print("  Result: {}/{} checks passed.".format(passed, total))
    line("-")
    pause()


def action_change_key():
    banner("Change saved API key")
    old = load_saved_key()
    if old:
        print("  Current key starts with: {}...".format(old[:4]))
    print("  Press Enter to generate a strong random key, or paste your own.")
    new_key = ask_secret("New key")
    if not new_key:
        new_key = secrets.token_urlsafe(24)
        print("  Generated: {}".format(new_key))
    save_key(new_key)
    ok("New API key saved. It will be used next time you start the bridge.")
    pause()


def action_show_details():
    banner("Last connection details")
    try:
        with open(INFO_FILE, "r") as f:
            print(f.read())
    except OSError:
        warn("No saved connection details yet - start the bridge first (option 1).")
        pause()
        return
    if ask_yes_no("Copy the public URL to clipboard?", default=True):
        url = None
        with open(INFO_FILE) as f:
            for l in f:
                if l.startswith("Public URL:"):
                    url = l.split(":", 1)[1].strip()
        if url and copy_to_clipboard(url):
            ok("URL copied.")
        else:
            warn("Could not access the clipboard.")
    pause()


def action_modes():
    banner("Choose which services are on")
    print("  These switch routes on the single bridge port,")
    print("  they are not separate ports.")
    print()
    cfg = load_config()
    modes = cfg["modes"]
    names = {"ix": "ixBrowser routes (/ix/*, /task/*)",
             "csi": "Real-Chrome routes (/csi)",
             "term": "Terminal routes (/exec)"}
    while True:
        for i, k in enumerate(("ix", "csi", "term"), start=1):
            print("  {}. {:45s} [{}]".format(
                i, names[k], "ON" if modes[k] else "OFF"))
        print("  4. Turn ALL on")
        ixport = cfg.get("ix_port", 0)
        print("  5. ixBrowser port [{}]".format(
            "auto-detect" if not ixport else ixport))
        print("  0. Back (saves)")
        print()
        choice = ask("Choose", default="0")
        if choice == "0":
            break
        elif choice in ("1", "2", "3"):
            k = ("ix", "csi", "term")[int(choice) - 1]
            modes[k] = not modes[k]
            print("  {} is now {}".format(names[k],
                                          "ON" if modes[k] else "OFF"))
        elif choice == "4":
            modes.update({"ix": True, "csi": True, "term": True})
            print("  All services ON.")
        elif choice == "5":
            raw = ask("ixBrowser port (0 = auto-detect)", default=str(ixport))
            try:
                cfg["ix_port"] = max(0, min(65535, int(raw)))
            except ValueError:
                warn("Not a number, keeping {}.".format(ixport))
            else:
                print("  ixBrowser port set to {}.".format(
                    "auto-detect" if not cfg["ix_port"] else cfg["ix_port"]))
        else:
            warn("'{}' is not a valid option.".format(choice))
        print()
    save_config(cfg)
    ok("Mode saved: " + ", ".join(
        "{}={}".format(k, "on" if v else "off") for k, v in modes.items()))
    pause()


def action_log_tail():
    banner("Recent log (last 30 lines)")
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-30:]
        print("".join(lines) if lines else "  (log is empty)")
    except OSError:
        warn("No log file yet - start the bridge first (option 1).")
    print()
    print("  Full log: {}".format(LOG_FILE))
    print("  Audit log: {}".format(AUDIT_FILE))
    pause()


def ensure_packages():
    missing = check_packages()
    if not missing:
        ok("All required packages are installed.")
        return
    warn("Missing packages: " + ", ".join(missing))
    if INTERACTIVE and ask_yes_no("Install them now with pip?", default=True):
        print("  Installing ... (this can take a minute)")
        rc = subprocess.run(
            [sys.executable, "-m", "pip", "install"] + missing).returncode
        if rc != 0:
            fail("pip install failed. Try manually:\n"
                 "    pip install " + " ".join(missing))
        ok("Packages installed.")
    else:
        fail("Cannot continue without the packages.")


def ensure_api_key():
    api_key = os.environ.get("BRIDGE_API_KEY", "") or load_saved_key()
    if api_key:
        print("  Using saved API key ({}...).".format(api_key[:4]))
        return api_key
    print("  This key protects your bridge. Keep it private.")
    print("  Press Enter and I will generate a strong random key for you,")
    print("  or paste your own.")
    api_key = ask_secret("API key") or secrets.token_urlsafe(24)
    if ask_yes_no("Remember this key for next time?", default=True):
        save_key(api_key)
        ok("Key saved on this PC.")
    return api_key


def action_start():
    banner("Starting bridge + ngrok tunnel  (v" + BRIDGE_VERSION + ")")
    total = 6

    print("\n[1/{}] Checking packages".format(total))
    ensure_packages()

    print("\n[2/{}] Checking ngrok and ixBrowser".format(total))
    ngrok_bin = shutil.which("ngrok")
    if not ngrok_bin:
        print("  ngrok not found. Fix:")
        print("    1. Download: https://ngrok.com/download")
        print("    2. Run: ngrok config add-authtoken <your-token>")
        fail("ngrok is required.")
    ok("ngrok found.")
    cfg = load_config()
    saved_ix = cfg.get("ix_port", 0)
    raw = ask("ixBrowser port - paste its URL or just the port "
              "(Enter = auto-detect)",
              default=str(saved_ix) if saved_ix else None)
    manual = parse_ix_port(raw)
    if manual:
        cfg["ix_port"] = manual
        save_config(cfg)
        if _ix_probe(manual):
            ok("ixBrowser responding on port {}.".format(manual))
        else:
            warn("Port {} not responding yet - will keep retrying it.".format(
                manual))
    else:
        if raw.strip() == "0":
            cfg["ix_port"] = 0
            save_config(cfg)
        try:
            ix_port = get_ix_port(force=True)
            ok("ixBrowser detected on port {}.".format(ix_port))
        except RuntimeError:
            warn("ixBrowser not detected right now.")
            print("  The bridge will keep re-detecting its port automatically,")
            print("  so you can start ixBrowser later and it will just work.")
    if cfg["modes"].get("csi"):
        if csi_reachable():
            ok("CSI daemon detected (real Chrome control available).")
        else:
            print("  CSI daemon not detected - trying to start it...")
            res = start_csi_daemon()
            if res is True:
                ok("CSI daemon started.")
                if not (csi_status() or {}).get("extension_connected"):
                    warn("Daemon is up but the Chrome extension is not connected -")
                    print("  open Chrome and check that the CSI extension shows connected.")
            elif res == "missing":
                warn("CSI daemon is not installed - /csi will fail until you install it.")
                print("  Install with PowerShell:")
                print("    " + CSI_INSTALL_PS)
            else:
                warn("Could not start the CSI daemon - /csi will fail until you start it.")
                print("  Try manually: csi start")
                print("  Tip: run 'csi autostart on' once to survive reboots.")
    else:
        print("  CSI routes disabled (menu option 5).")

    print("\n[3/{}] API key".format(total))
    api_key = ensure_api_key()

    print("\n[4/{}] Services and port".format(total))
    cfg = load_config()
    modes = cfg["modes"]
    print("  Services: " + ", ".join(
        "{}={}".format(k, "on" if v else "off") for k, v in modes.items())
        + "  (change with menu option 5)")
    port = cfg.get("port", 8000)
    raw = ask("Local port for the bridge", default=str(port))
    try:
        port = int(raw)
    except ValueError:
        fail("'{}' is not a valid port.".format(raw))
    while not port_free(port):
        warn("Port {} is busy.".format(port))
        raw = ask("Pick another port", default=str(port + 1))
        try:
            port = int(raw)
        except ValueError:
            fail("'{}' is not a valid port.".format(raw))
    cfg["port"] = port
    save_config(cfg)
    ok("Using port {}.".format(port))

    print("\n[5/{}] Launching".format(total))
    import uvicorn
    public_url_holder: dict = {"url": None,
                               "local": "http://127.0.0.1:{}".format(port),
                               "mcp": False}
    app = build_app(api_key, modes, public_url_holder)
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    print("  Waiting for the bridge ...")
    if not wait_for_health(port):
        fail("Bridge did not start on port {}.".format(port))
    ok("Bridge running on 127.0.0.1:{}.".format(port))
    if public_url_holder["mcp"]:
        ok("MCP server live at http://127.0.0.1:{}/mcp/sse".format(port))
    else:
        warn("MCP mode off (optional) - run: pip install mcp")

    print("\n[6/{}] Tunnel".format(total))
    print("  Checking for an existing ngrok tunnel ...")
    public_url = find_http_tunnel(port, timeout=8)
    ngrok_proc = None
    started_ngrok = False
    if public_url:
        ok("Found your already-running ngrok tunnel, reusing it.")
    else:
        print("  Starting ngrok ...")
        ngrok_proc = subprocess.Popen(
            [ngrok_bin, "http", str(port), "--log=stdout"],
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        time.sleep(2)
        if ngrok_proc.poll() is not None:
            public_url = find_http_tunnel(port, timeout=10)
            ngrok_proc = None
            if not public_url:
                fail("ngrok exited immediately. Is it logged in?\n"
                     "    ngrok config add-authtoken <your-token>")
            ok("Reusing the tunnel from your other ngrok session.")
        else:
            started_ngrok = True
            print("  Waiting for your public web address ...")
            public_url = find_http_tunnel(port, timeout=60)
            if not public_url:
                ngrok_proc.terminate()
                ngrok_proc = None
                server.should_exit = True
                fail("No public URL appeared in 60s. Check ngrok login/internet.")
    public_url_holder["url"] = public_url

    print("  Verifying the tunnel ...")
    tunnel_ok = self_test(public_url)

    try:
        with open(INFO_FILE, "w") as f:
            f.write("ixBrowser bridge connection details (v{})\n".format(
                BRIDGE_VERSION))
            f.write("Public URL: {}\n".format(public_url))
            f.write("API key: {}\n".format(api_key))
            f.write("Local bridge (this PC, for local AI tools): "
                    "http://127.0.0.1:{}\n".format(port))
            f.write("Dashboard: {}/dashboard?key=YOUR_API_KEY\n".format(
                public_url))
            f.write("Operator headers: X-Bridge-Key, "
                    "ngrok-skip-browser-warning\n")
    except OSError:
        pass

    # watchdog: keeps ngrok alive, re-detects ixBrowser port
    wd_state = {"ngrok_proc": ngrok_proc, "public_url": public_url,
                "started_ngrok": started_ngrok}
    wd_stop = threading.Event()
    wd_thread = threading.Thread(target=watchdog_loop,
                                 args=(wd_stop, port, ngrok_bin, wd_state),
                                 daemon=True)
    wd_thread.start()
    ok("Watchdog running (restarts ngrok if it dies, re-detects ixBrowser).")

    print()
    line("*")
    print("  BRIDGE IS LIVE  (v{})".format(BRIDGE_VERSION))
    print()
    print("  PUBLIC URL : {}".format(public_url))
    print("  LOCAL URL  : http://127.0.0.1:{}  (for AI tools on this PC)".format(port))
    print("  API KEY    : {}".format(api_key))
    print()
    print("  Send PUBLIC URL + API KEY to your operator.")
    print("  Endpoints: /ix/*, /ixc/profile-list, /task/run, /task/run_many,")
    print("             /task/open_many (parallel, leaves open),")
    print("             /task/open_profiles, /task/run_stream (live),")
    print("             /csi, /exec, /tools, /dashboard, /health"
          + (", /mcp/sse" if public_url_holder["mcp"] else ""))
    print("  Logs: bridge.log (all) + audit.log (sensitive calls)")
    print("  Tunnel self-test: {}".format(
        "OK" if tunnel_ok else "FAILED - may not work yet"))
    line("*")
    print()

    if ask_yes_no("Copy the public URL to clipboard?", default=True):
        if copy_to_clipboard(public_url):
            ok("URL copied - paste it to your operator.")
        else:
            warn("Clipboard unavailable - copy it manually from above.")

    print()
    print("Keep this window OPEN while your operator works.")
    print("Press Ctrl+C to stop (leftover profiles will be closed).")
    try:
        while thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down ...")
    finally:
        wd_stop.set()
        close_leftover_profiles()
        proc = wd_state.get("ngrok_proc")
        if proc is not None and wd_state.get("started_ngrok"):
            proc.terminate()
        server.should_exit = True
    print("Stopped.")


# ============================================================ menu
def main():
    if not INTERACTIVE:
        # non-interactive (piped) run: go straight to start with env defaults
        action_start()
        return

    banner("ixBrowser All-In-One Bridge  v" + BRIDGE_VERSION)
    while True:
        print("What would you like to do?")
        print()
        print("  1. Start bridge + ngrok tunnel  (go live)")
        print("  2. Check my setup               (diagnostics)")
        print("  3. Change my saved API key")
        print("  4. Show my last connection details")
        print("  5. Choose which services are on (ixBrowser / CSI / terminal)")
        print("  6. View recent log")
        print("  7. Exit")
        print()
        choice = ask("Choose an option", default="1")
        print()
        if choice == "1":
            action_start()
        elif choice == "2":
            action_diagnostics()
        elif choice == "3":
            action_change_key()
        elif choice == "4":
            action_show_details()
        elif choice == "5":
            action_modes()
        elif choice == "6":
            action_log_tail()
        elif choice == "7":
            print("Goodbye!")
            return
        else:
            warn("'{}' is not a valid option.".format(choice))


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        if e.code not in (0, None):
            print("\nStopped. Fix the issue above and run again.")
        sys.exit(e.code or 0)

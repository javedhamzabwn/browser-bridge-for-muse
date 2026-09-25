#!/usr/bin/env python3
"""bsk_driver.py - Tencent BrowserSkill Automation Driver for Pilot.

Connects to the user's logged-in personal Chrome/Edge browser via BrowserSkill (bsk).
Uses port 42800 by default (bypassing Windows Hyper-V reserved port exclusions).
"""
import json
import os
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional


BSK_DEFAULT_PORT = 42800
BSK_EXE = shutil.which("bsk") or r"C:\Users\Javed Hamza\.local\bin\bsk.exe"


def _run_bsk(args: List[str], timeout: float = 15.0, use_json: bool = True) -> Dict[str, Any]:
    """Execute a bsk CLI command and return parsed JSON or status dict."""
    cmd = [BSK_EXE] + args
    if use_json and "--json" not in cmd:
        cmd.append("--json")

    env = os.environ.copy()
    env["BSK_AUTO_START"] = "0"          # Prevent auto-start on blocked port 52800
    env["BSK_BROWSER_WAIT_MS"] = "200"   # Fast probe (0.2s instead of 5s default wait)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            encoding="utf-8",
            errors="replace"
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        if stdout.startswith("{") or stdout.startswith("["):
            try:
                return {"success": proc.returncode == 0, "data": json.loads(stdout), "raw": stdout}
            except json.JSONDecodeError:
                pass

        return {
            "success": proc.returncode == 0,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": proc.returncode
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"bsk command timed out after {timeout}s"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def is_daemon_running() -> bool:
    """Check if the bsk daemon is running and responsive."""
    res = _run_bsk(["status"], timeout=6.0)
    if res.get("success") and "data" in res:
        data = res["data"]
        return bool(data.get("pid") and data.get("ws_port"))
    return False


def get_status() -> Dict[str, Any]:
    """Return full bsk daemon and browser status."""
    res = _run_bsk(["status"], timeout=6.0)
    if res.get("success") and "data" in res:
        return res["data"]
    return {
        "running": False,
        "error": res.get("error") or res.get("stderr") or "Daemon not responding",
        "hint": f"Run ensure_daemon() to start bsk on port {BSK_DEFAULT_PORT}"
    }


def ensure_daemon(port: int = BSK_DEFAULT_PORT) -> bool:
    """Ensure the bsk daemon is running on the specified unreserved port."""
    if is_daemon_running():
        return True

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    cmd = [BSK_EXE, "daemon", "start", "--port", str(port), "--foreground"]
    try:
        subprocess.Popen(
            cmd,
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True
        )
    except Exception as e:
        print(f"[bsk_driver] Failed to launch daemon: {e}")
        return False

    # Wait up to 6s for readiness
    for _ in range(12):
        time.sleep(0.5)
        if is_daemon_running():
            return True

    return False


def list_browsers() -> List[Dict[str, Any]]:
    """List all browsers currently connected to bsk via the browser extension."""
    if not is_daemon_running():
        ensure_daemon()
    res = _run_bsk(["browsers"], timeout=6.0)
    if res.get("success") and isinstance(res.get("data"), list):
        return res["data"]
    return []


# =====================================================================
# Session Management
# =====================================================================
class BskSession:
    """Context manager for an automated BrowserSkill Agent Window session."""

    def __init__(self, browser: Optional[str] = None, no_focus: bool = True):
        self.browser = browser
        self.no_focus = no_focus
        self.session_id: Optional[str] = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self) -> str:
        ensure_daemon()
        args = ["session", "start"]
        if self.no_focus:
            args.append("--no-focus")
        if self.browser:
            args.extend(["--browser", self.browser])

        res = _run_bsk(args, timeout=12.0)
        if res.get("success") and "data" in res:
            self.session_id = res["data"].get("session_id")
            return self.session_id
        raise RuntimeError(f"Failed to start bsk session: {res.get('error') or res.get('stderr') or res.get('raw')}")

    def stop(self) -> bool:
        if not self.session_id:
            return True
        res = _run_bsk(["session", "stop", self.session_id], timeout=6.0, use_json=False)
        self.session_id = None
        return res.get("success", False)

    def navigate(self, url: str) -> Dict[str, Any]:
        if not self.session_id:
            raise RuntimeError("No active session")
        return _run_bsk(["navigate", url, "--session", self.session_id], timeout=45.0)

    def observe(self) -> Dict[str, Any]:
        if not self.session_id:
            raise RuntimeError("No active session")
        return _run_bsk(["observe", "--session", self.session_id], timeout=15.0)

    def snapshot(self) -> Dict[str, Any]:
        if not self.session_id:
            raise RuntimeError("No active session")
        return _run_bsk(["snapshot", "--session", self.session_id], timeout=10.0)

    def click(self, target: str) -> Dict[str, Any]:
        if not self.session_id:
            raise RuntimeError("No active session")
        return _run_bsk(["click", target, "--session", self.session_id], timeout=10.0)

    def fill(self, target: str, value: str) -> Dict[str, Any]:
        if not self.session_id:
            raise RuntimeError("No active session")
        return _run_bsk(["fill", target, "--value", value, "--session", self.session_id], timeout=10.0)

    def press(self, key: str, target: Optional[str] = None) -> Dict[str, Any]:
        if not self.session_id:
            raise RuntimeError("No active session")
        args = ["press", key, "--session", self.session_id]
        if target:
            args.extend(["--ref", target])
        return _run_bsk(args, timeout=10.0)

    def scroll_to(self, target: str) -> Dict[str, Any]:
        if not self.session_id:
            raise RuntimeError("No active session")
        return _run_bsk(["scroll-to", target, "--session", self.session_id], timeout=10.0)

    def evaluate(self, js_expr: str) -> Dict[str, Any]:
        if not self.session_id:
            raise RuntimeError("No active session")
        return _run_bsk(["evaluate", js_expr, "--session", self.session_id], timeout=15.0)

    def get_html(self) -> str:
        if not self.session_id:
            raise RuntimeError("No active session")
        res = _run_bsk(["get-html", "--session", self.session_id], timeout=15.0, use_json=False)
        return res.get("stdout", "")

    def screenshot(self, out_path: str, full_page: bool = False) -> Dict[str, Any]:
        if not self.session_id:
            raise RuntimeError("No active session")
        args = ["screenshot", "--out", out_path, "--session", self.session_id]
        if full_page:
            args.append("--full-page")
        return _run_bsk(args, timeout=20.0)


# =====================================================================
# High-Level One-Shot Helpers
# =====================================================================
def bsk_read_url(url: str, wait_ms: int = 1500) -> Dict[str, Any]:
    """Open a URL in BrowserSkill, wait for load, return page observation."""
    with BskSession() as sess:
        sess.navigate(url)
        if wait_ms > 0:
            time.sleep(wait_ms / 1000.0)
        obs = sess.observe()
        return {
            "url": url,
            "observation": obs.get("data") or obs.get("stdout"),
            "session_id": sess.session_id
        }


def bsk_eval_url(url: str, js_expr: str) -> Any:
    """Open URL, evaluate JavaScript, and return result."""
    with BskSession() as sess:
        sess.navigate(url)
        time.sleep(1.0)
        res = sess.evaluate(js_expr)
        return res.get("data") or res.get("stdout")


if __name__ == "__main__":
    print("=== BrowserSkill Driver Diagnostic ===")
    running = is_daemon_running()
    print(f"Daemon running: {running}")
    if not running:
        print(f"Starting daemon on port {BSK_DEFAULT_PORT}...")
        started = ensure_daemon()
        print(f"Daemon started: {started}")

    status = get_status()
    print("\nDaemon Status:")
    print(json.dumps(status, indent=2))

    browsers = list_browsers()
    print(f"\nConnected Browsers ({len(browsers)}):")
    for b in browsers:
        print(f"  - {b}")

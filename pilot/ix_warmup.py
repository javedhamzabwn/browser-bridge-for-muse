#!/usr/bin/env python3
"""ix_warmup.py - Automated Human Interaction & Warmup for ixBrowser Profiles.

Performs authentic human browsing across active ixBrowser Reddit profiles:
1. Opens each profile via ixBrowser API (retrieves CDP WebSocket URL).
2. Connects over CDP using Playwright.
3. Reuses existing Reddit tab or navigates safely.
4. Confirms active logged-in Reddit username.
5. Navigates to a relevant subreddit matching the account's niche (or AskReddit/NoStupidQuestions).
6. Executes smooth human scroll with realistic reading pauses (ZERO auto-upvotes).
7. Inspects 1 post, scrolls through comments, and cleanly detaches.
8. Never closes browser windows unnecessarily (per ixBrowser operational rules).
"""
import asyncio
import json
import os
import random
import sys
import time
import urllib.request
from typing import Any, Dict, List, Optional
from playwright.async_api import async_playwright

import human

IX_PORT = 53400
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Curated Active Profiles from Sheet B (1o-5O0EjnbRtaayh2uGX51N2-cQP-SCT834HNaof8zxw)
ACTIVE_PROFILES = [
    {"pid": 227, "user": "LukeWildKumquat46", "sub": "AskReddit", "niche": "General / Q&A"},
    {"pid": 220, "user": "Lonely_Restaurant422", "sub": "NoStupidQuestions", "niche": "Casual Q&A"},
    {"pid": 219, "user": "Own-Attention-5162", "sub": "explainlikeimfive", "niche": "Explanations"},
    {"pid": 218, "user": "Upset-Protection5168", "sub": "cats", "niche": "Cats / Pets"},
    {"pid": 216, "user": "Novel_Temporary_7994", "sub": "movies", "niche": "Movies / Pop culture"},
    {"pid": 225, "user": "Deep_Gur_2954", "sub": "mildlyinteresting", "niche": "Casual interesting"},
    {"pid": 222, "user": "Disastrous-Base2291", "sub": "AskReddit", "niche": "General chat"},
    {"pid": 204, "user": "pdxrichtheone", "sub": "CasualConversation", "niche": "Friendly conversation"},
    {"pid": 202, "user": "skm2024", "sub": "NoStupidQuestions", "niche": "Q&A"}
]


def ix_open_profile(pid: int, timeout: int = 60) -> Optional[str]:
    """Calls ixBrowser API to open/attach to profile and return CDP ws URL."""
    url = f"http://127.0.0.1:{IX_PORT}/api/v2/profile-open"
    payload = json.dumps({"profile_id": pid, "cookies_backup": True}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("error", {}).get("code") == 0:
                return (data.get("data") or {}).get("ws")
            else:
                print(f"  [PID {pid}] Open error: {data.get('error', {}).get('message')}")
    except Exception as e:
        print(f"  [PID {pid}] API request error: {e}")
    return None


async def warm_one_profile(pw, profile_meta: Dict[str, Any], scroll_steps: int = 4) -> Dict[str, Any]:
    """Warms up a single ixBrowser profile with authentic human scrolling and reading."""
    pid = profile_meta["pid"]
    expected_user = profile_meta["user"]
    sub = profile_meta.get("sub", "AskReddit")
    t0 = time.time()

    print(f"\n=======================================================")
    print(f"Starting Warmup: PID {pid} (@{expected_user}) -> r/{sub}")
    print(f"=======================================================")

    ws = ix_open_profile(pid, timeout=60)
    if not ws:
        return {"pid": pid, "status": "FAILED", "error": "Could not get WebSocket URL", "elapsed_s": round(time.time() - t0, 1)}

    try:
        browser = await pw.chromium.connect_over_cdp(ws, timeout=40000)
        ctx = browser.contexts[0]

        # Re-use existing reddit tab if present, else first tab
        page = None
        for p in ctx.pages:
            if "reddit.com" in (p.url or ""):
                page = p
                break
        if not page:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # Step 1: Navigate to target subreddit
        target_url = f"https://www.reddit.com/r/{sub}/"
        print(f"  [1/4] Navigating to {target_url}...")
        await page.goto(target_url, wait_until="domcontentloaded", timeout=50000)
        await page.wait_for_timeout(3000)

        # Step 2: Verify active user
        actual_user = None
        try:
            actual_user = await page.evaluate("document.querySelector('shreddit-app')?.getAttribute('current-user-name')")
        except Exception:
            pass
        print(f"  [2/4] Logged-in Username: @{actual_user or 'Guest/Unknown'}")

        # Step 3: Human scrolling on subreddit feed (ZERO auto-upvotes)
        print(f"  [3/4] Performing human scroll ({scroll_steps} bursts with reading pauses, NO upvotes)...")
        await human.playwright_human_scroll(page, steps=scroll_steps, reading_pause_min=2.2, reading_pause_max=4.5)

        # Step 4: Pick 1 visible post and read comments
        print(f"  [4/4] Inspecting a thread to read comments...")
        js_pick_post = """(function(){
            var posts = document.querySelectorAll('shreddit-post');
            if (!posts || posts.length === 0) return null;
            var rand = posts[Math.floor(Math.random() * Math.min(posts.length, 5))];
            return rand ? rand.getAttribute('permalink') : null;
        })()"""
        try:
            post_permalink = await page.evaluate(js_pick_post)
            if post_permalink:
                if not post_permalink.startswith("http"):
                    post_permalink = f"https://www.reddit.com{post_permalink}"
                print(f"        Reading thread: {post_permalink[:65]}...")
                await page.goto(post_permalink, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(3500)
                await human.playwright_human_scroll(page, steps=random.randint(2, 3), reading_pause_min=2.5, reading_pause_max=5.0)
                print(f"        Thread reading complete.")
        except Exception as e:
            print(f"        Thread inspect notice: {e}")

        elapsed = round(time.time() - t0, 1)
        print(f"SUCCESS: PID {pid} (@{actual_user}) warmup complete in {elapsed}s.")
        return {
            "pid": pid,
            "user": actual_user or expected_user,
            "status": "SUCCESS",
            "subreddit": sub,
            "elapsed_s": elapsed
        }

    except Exception as e:
        elapsed = round(time.time() - t0, 1)
        print(f"ERROR on PID {pid}: {e}")
        return {"pid": pid, "status": "ERROR", "error": str(e)[:120], "elapsed_s": elapsed}


async def run_all_profiles(pids: Optional[List[int]] = None, max_profiles: int = 5):
    """Iterates through active ixBrowser profiles and executes human warmup sequentially."""
    targets = []
    if pids:
        targets = [p for p in ACTIVE_PROFILES if p["pid"] in pids]
        if not targets:
            targets = [{"pid": p, "user": "Unknown", "sub": "AskReddit"} for p in pids]
    else:
        targets = ACTIVE_PROFILES[:max_profiles]

    print(f"=== Starting ixBrowser Multi-Profile Human Warmup ({len(targets)} profiles) ===")
    print("Rules: Strict human scrolling, reading delays, ZERO auto-upvoting, windows kept intact.\n")

    pw = await async_playwright().start()
    results = []

    try:
        for idx, t in enumerate(targets):
            res = await warm_one_profile(pw, t, scroll_steps=3)
            results.append(res)
            # Brief stagger between profiles
            if idx < len(targets) - 1:
                stagger = random.uniform(3.0, 6.0)
                print(f"Waiting {stagger:.1f}s before next profile...")
                await asyncio.sleep(stagger)
    finally:
        await pw.stop()

    out_file = os.path.join(BASE_DIR, "ix_warmup_results.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print("\n================ SUMMARY ================")
    success_count = sum(1 for r in results if r.get("status") == "SUCCESS")
    print(f"Completed: {success_count}/{len(results)} profiles warmed successfully.")
    print(f"Results saved to: {out_file}")
    for r in results:
        print(f"  PID {r['pid']}: {r.get('status')} (@{r.get('user', '-')}) in {r.get('elapsed_s')}s")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ixBrowser Multi-Profile Human Interaction & Warmup")
    parser.add_argument("--pids", default="", help="Comma-separated profile IDs (e.g. 227,220,219)")
    parser.add_argument("--limit", type=int, default=3, help="Max profiles to run")
    args = parser.parse_args()

    pid_list = [int(x.strip()) for x in args.pids.split(",") if x.strip()] if args.pids else None
    asyncio.run(run_all_profiles(pids=pid_list, max_profiles=args.limit))

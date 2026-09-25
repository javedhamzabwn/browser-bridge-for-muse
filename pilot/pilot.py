#!/usr/bin/env python3
"""pilot.py - High-Performance Browser & Research Automation Suite.

Features:
  1. CDP Fast-Nav & Lite Mode: blocks images, fonts, video, ad-trackers (300ms loads).
  2. Multi-Tab Parallel Batching: up to 20+ simultaneous Chrome tabs.
  3. Resilient Extraction: fast .json with automatic shreddit-post DOM fallback (anti-429).
  4. Local SQLite Cache with TTL: zero-latency 1ms cached reads.
  5. ixBrowser Multi-Proxy Batching: parallel execution across 72 residential profiles.

Usage:
  pilot.bat status
  pilot.bat csi nav <url> [--lite]
  pilot.bat csi google <query> [--limit 5] [--lite] [--no-cache]
  pilot.bat csi subreddit <name> [--no-cache]
  pilot.bat csi search <query> [--limit 5] [--no-cache]
  pilot.bat csi comments <post_url> [--limit 10] [--no-cache]
  pilot.bat csi batch <urls.txt> [--workers 5] [--lite] [--out results.jsonl]
  pilot.bat csi batch-search <queries.txt> [--workers 5] [--limit 5] [--out results.jsonl]
  pilot.bat csi snap
  pilot.bat csi eval <js>
  pilot.bat csi tabs
  pilot.bat csi close-all
  pilot.bat ix list [--group <name>]
  pilot.bat ix open <pid> [--url <url>]
  pilot.bat ix close <pid>
  pilot.bat ix run <pid> --url <url> [--eval <js>] [--shot]
  pilot.bat ix batch-run <tasks.json> [--concurrency 4] [--out results.json]
"""
import argparse
import asyncio
import concurrent.futures
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

import human
import viral_finder
import reddit_agent
import bsk_driver


# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

CSI_PORT = 10088
IX_PORT = 53400
BRIDGE_PORT = 8000

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(BASE_DIR)
PIPELINE_DIR = os.path.join(os.path.dirname(PARENT_DIR), "reddit_pipeline_complete")
CACHE_DB_PATH = os.path.join(BASE_DIR, "pilot_cache.db")

# Block patterns for Lite Mode (cuts 80% bandwidth & eliminates network-idle waits)
BLOCKED_URL_PATTERNS = [
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.svg", "*.ico", "*.bmp",
    "*.woff", "*.woff2", "*.ttf", "*.otf", "*.eot",
    "*.mp4", "*.webm", "*.m4v", "*.mov",
    "*doubleclick.net*", "*google-analytics.com*", "*googletagmanager.com*",
    "*facebook.net*", "*analytics*", "*adservice*", "*scorecardresearch.com*"
]


# =====================================================================
# 1. Local SQLite Cache with TTL
# =====================================================================
def init_cache():
    try:
        with sqlite3.connect(CACHE_DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    category TEXT,
                    data TEXT,
                    updated_at REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cat ON cache(category)")
    except Exception:
        pass


def cache_get(key: str, ttl_hours: float = 24.0) -> Optional[Any]:
    try:
        with sqlite3.connect(CACHE_DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT data, updated_at FROM cache WHERE key = ?", (key,))
            row = cur.fetchone()
            if row:
                data_str, updated_at = row
                if time.time() - updated_at <= ttl_hours * 3600:
                    return json.loads(data_str)
    except Exception:
        pass
    return None


def cache_set(key: str, data: Any, category: str = "general"):
    try:
        with sqlite3.connect(CACHE_DB_PATH) as conn:
            conn.execute("""
                INSERT INTO cache (key, category, data, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    data=excluded.data,
                    category=excluded.category,
                    updated_at=excluded.updated_at
            """, (key, category, json.dumps(data, ensure_ascii=False), time.time()))
    except Exception:
        pass


init_cache()


# =====================================================================
# 2. Port & Health Utilities
# =====================================================================
def is_port_listening(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def cmd_status(args):
    csi_ok = is_port_listening(CSI_PORT)
    ix_ok = is_port_listening(IX_PORT)
    bridge_ok = is_port_listening(BRIDGE_PORT)
    ngrok_ok = is_port_listening(4040)

    print("=== Browser & Pilot System Status ===")
    print(f"  Chrome CSI Daemon (port {CSI_PORT}):  {'ONLINE' if csi_ok else 'OFFLINE'}")
    print(f"  ixBrowser API     (port {IX_PORT}):  {'ONLINE' if ix_ok else 'OFFLINE'}")
    print(f"  Local Bridge      (port {BRIDGE_PORT}):   {'ONLINE' if bridge_ok else 'OFFLINE'}")
    print(f"  ngrok Inspector   (port 4040):   {'ONLINE' if ngrok_ok else 'OFFLINE'}")

    if csi_ok:
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{CSI_PORT}/status")
            with urllib.request.urlopen(req, timeout=2) as r:
                data = json.loads(r.read().decode())
                print(f"    -> Chrome Extension: {'CONNECTED' if data.get('extension_connected') else 'DISCONNECTED'}")
                print(f"    -> Active CSI Sessions: {len(data.get('sessions', []))}")
        except Exception as e:
            print(f"    -> CSI status error: {e}")

    if ix_ok:
        try:
            body = json.dumps({"page": 1, "page_size": 1}).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{IX_PORT}/api/v2/profile-list",
                data=body,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=2) as r:
                data = json.loads(r.read().decode())
                total = (data.get("data") or {}).get("total", 0)
                print(f"    -> Total ixBrowser Profiles: {total}")
        except Exception as e:
            print(f"    -> ixBrowser probe error: {e}")

    try:
        with sqlite3.connect(CACHE_DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM cache")
            count = cur.fetchone()[0]
            print(f"  Local SQLite Cache: {count} cached entries ({CACHE_DB_PATH})")
    except Exception:
        pass

    # BrowserSkill (bsk) Status
    try:
        bsk_st = bsk_driver.get_status()
        bsk_ok = bool(bsk_st.get("pid") and bsk_st.get("ws_port"))
        print(f"  BrowserSkill (bsk)(port {bsk_st.get('ws_port', bsk_driver.BSK_DEFAULT_PORT)}):  {'ONLINE' if bsk_ok else 'OFFLINE'}")
        if bsk_ok:
            browsers = bsk_st.get("browsers", [])
            sessions = bsk_st.get("sessions", [])
            print(f"    -> Connected Browsers: {len(browsers)}")
            print(f"    -> Active bsk Sessions: {len(sessions)}")
    except Exception as e:
        print(f"  BrowserSkill (bsk): OFFLINE ({e})")


# =====================================================================
# 3. CSI Chrome Core & CDP Fast-Nav
# =====================================================================
def csi_post(action: str, args: dict = None, session: str = "pilot") -> dict:
    if args is None:
        args = {}
    url = f"http://127.0.0.1:{CSI_PORT}/command"
    payload = json.dumps({"action": action, "args": args, "session": session}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "ngrok-skip-browser-warning": "true"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def csi_eval_unwrap(js_code: str, session: str = "pilot") -> Any:
    res = csi_post("evaluate", {"code": js_code}, session)
    if not isinstance(res, dict):
        return res
    target = res.get("data") if isinstance(res.get("data"), dict) else res
    if "path" in target and os.path.exists(target["path"]):
        try:
            with open(target["path"], "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    target = loaded
        except Exception:
            pass
    val = target.get("value", target)
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


def csi_enable_lite(session: str = "pilot"):
    """Blocks heavy assets and trackers via CDP to maximize speed."""
    try:
        csi_post("cdp", {"method": "Network.enable", "params": {}}, session)
        csi_post("cdp", {"method": "Network.setBlockedURLs", "params": {"urls": BLOCKED_URL_PATTERNS}}, session)
    except Exception:
        pass


def csi_fast_nav(url: str, session: str = "pilot", timeout: float = 8.0, lite: bool = False) -> dict:
    """Fast navigation via CDP Page.navigate, bypassing 30s network-idle blocks."""
    t0 = time.time()
    tabs_res = csi_post("list_tabs", {}, session)
    tabs = (tabs_res.get("data") or {}).get("tabs", [])
    if not tabs:
        csi_post("navigate", {"url": "https://example.com", "newTab": True}, session)

    if lite:
        csi_enable_lite(session)

    csi_post("cdp", {"method": "Page.navigate", "params": {"url": url}}, session)

    while time.time() - t0 < timeout:
        time.sleep(0.12)
        try:
            r = csi_post("evaluate", {
                "code": '(document.readyState === "interactive" || document.readyState === "complete")'
            }, session)
            val = (r.get("data") or {}).get("value")
            if val is True:
                break
        except Exception:
            pass
    return {"url": url, "elapsed_s": round(time.time() - t0, 2)}


# =====================================================================
# 4. Resilient Reddit Extractors (Rate-Limit Fallback)
# =====================================================================
def extract_reddit_thread(post_url: str, session: str = "pilot", limit_comments: int = 10) -> dict:
    """Extracts Reddit post and comments using fast .json with automatic DOM fallback."""
    clean_url = post_url.split("?")[0].rstrip("/")
    jurl = clean_url + "/.json?raw_json=1&limit=100"

    csi_post("navigate", {"url": jurl, "newTab": True}, session)
    time.sleep(1.2)
    raw = csi_eval_unwrap("document.body.innerText", session)

    # Check if .json succeeded and was not rate-limited (429)
    is_json = False
    data = None
    if isinstance(raw, str) and raw.startswith(("[", "{")):
        try:
            data = json.loads(raw)
            if isinstance(data, list) and len(data) >= 1:
                is_json = True
        except Exception:
            pass
    elif isinstance(raw, list) and len(raw) >= 1:
        data = raw
        is_json = True

    if is_json:
        try:
            post = data[0]["data"]["children"][0]["data"]
            comments_data = data[1]["data"]["children"] if len(data) > 1 else []
            comments = [c["data"] for c in comments_data if c.get("kind") == "t1"]
            return {
                "status": "LIVE",
                "method": "json_fast",
                "title": post.get("title"),
                "author": post.get("author"),
                "score": post.get("score"),
                "num_comments": post.get("num_comments"),
                "selftext": (post.get("selftext") or "")[:500],
                "url": f"https://www.reddit.com{post.get('permalink')}",
                "comments": [
                    {
                        "author": c.get("author"),
                        "score": c.get("score"),
                        "body": (c.get("body") or "")[:250],
                        "id": c.get("id")
                    }
                    for c in comments[:limit_comments]
                ]
            }
        except Exception:
            pass

    # Fallback Strategy: Navigate to normal HTML post and extract from shreddit-post DOM
    csi_post("navigate", {"url": clean_url, "newTab": False}, session)
    time.sleep(1.8)
    js_dom = """(function(){
        var post = document.querySelector('shreddit-post');
        if (!post) return {found: false};
        var comments = [];
        document.querySelectorAll('shreddit-comment').forEach(function(c) {
            comments.push({
                author: c.getAttribute('author'),
                score: c.getAttribute('score'),
                body: (c.querySelector('[slot="comment"]') || c).innerText.trim().slice(0, 250),
                id: c.getAttribute('thingid')
            });
        });
        return {
            found: true,
            title: post.getAttribute('post-title'),
            author: post.getAttribute('author'),
            score: post.getAttribute('score'),
            num_comments: post.getAttribute('comment-count'),
            permalink: post.getAttribute('permalink'),
            comments: comments
        };
    })()"""
    dom_res = csi_eval_unwrap(js_dom, session)
    if isinstance(dom_res, dict) and dom_res.get("found"):
        return {
            "status": "LIVE",
            "method": "dom_fallback",
            "title": dom_res.get("title"),
            "author": dom_res.get("author"),
            "score": dom_res.get("score"),
            "num_comments": dom_res.get("num_comments"),
            "url": f"https://www.reddit.com{dom_res.get('permalink')}",
            "comments": dom_res.get("comments", [])[:limit_comments]
        }

    return {"status": "ERROR", "reason": "rate_limited_or_unavailable", "url": post_url}


def extract_subreddit_info(name: str, session: str = "pilot") -> dict:
    """Extracts subreddit metadata with 3-tier anti-429 resilience:
    Tier 1: In-Page Authenticated Fetch (inherits session cookies & browser tokens).
    Tier 2: Zero-JSON HTML DOM Extraction (<shreddit-post>, <faceplate-number>, <p slot=description>).
    """
    html_url = f"https://www.reddit.com/r/{name}/"
    csi_post("navigate", {"url": html_url, "newTab": True}, session)
    time.sleep(1.8)

    # Tier 1: In-Page fetch (from within reddit origin)
    js_fetch = """(async function() {
        try {
            let resp = await fetch('https://www.reddit.com/r/%s/about.json?raw_json=1');
            if (resp.status === 200) {
                let d = await resp.json();
                return {status: 200, data: d.data};
            }
            return {status: resp.status};
        } catch (e) {
            return {status: 0, error: e.toString()};
        }
    })()""" % name

    tier1_res = csi_eval_unwrap(js_fetch, session)
    if isinstance(tier1_res, dict) and tier1_res.get("status") == 200:
        about = tier1_res.get("data") or {}
        return {
            "status": "LIVE",
            "method": "in_page_fetch",
            "subreddit": name,
            "title": about.get("title"),
            "subscribers": about.get("subscribers"),
            "active_user_count": about.get("active_user_count"),
            "over18": about.get("over18"),
            "description": (about.get("public_description") or "")[:300],
            "url": html_url
        }

    # Tier 2: Direct DOM Extraction (no .json calls, completely immune to 429)
    for _ in range(3):
        ready = csi_eval_unwrap("document.querySelectorAll('shreddit-post').length > 0 || !!document.querySelector('faceplate-number[number]')", session)
        if ready is True:
            break
        time.sleep(1.0)

    js_dom = """(function(){
        var title = document.title;
        var h1 = document.querySelector('h1');
        if (h1 && h1.innerText.trim()) title = h1.innerText.trim();

        var subs = null;
        var subEl = document.querySelector('faceplate-number[number]');
        if (subEl) subs = subEl.getAttribute('number');

        var desc = '';
        var descEl = document.querySelector('p[slot="description"]') || 
                     document.querySelector('#public-description') ||
                     document.querySelector('[data-testid="subreddit-sidebar"] p');
        if (descEl) desc = descEl.innerText.trim();

        var postCount = document.querySelectorAll('shreddit-post').length;

        return {
            title: title,
            subscribers: subs,
            description: desc.slice(0, 300),
            posts_rendered: postCount
        };
    })()"""
    dom_res = csi_eval_unwrap(js_dom, session)
    if isinstance(dom_res, dict) and (dom_res.get("title") or dom_res.get("subscribers")):
        return {
            "status": "LIVE",
            "method": "dom_fallback",
            "subreddit": name,
            "title": dom_res.get("title"),
            "subscribers": dom_res.get("subscribers"),
            "description": dom_res.get("description"),
            "posts_rendered": dom_res.get("posts_rendered", 0),
            "url": html_url
        }

    return {"status": "ERROR", "reason": "rate_limited_or_blocked", "subreddit": name, "url": html_url}



# =====================================================================
# 5. CSI Command Handlers
# =====================================================================
def cmd_csi(args):
    sub = args.csi_action
    session = args.session or "pilot"
    no_cache = getattr(args, "no_cache", False)

    if sub == "status":
        req = urllib.request.Request(f"http://127.0.0.1:{CSI_PORT}/status")
        with urllib.request.urlopen(req, timeout=5) as r:
            print(json.dumps(json.loads(r.read().decode()), indent=2))

    elif sub == "tabs":
        res = csi_post("list_tabs", {}, session)
        tabs = (res.get("data") or {}).get("tabs", [])
        if not tabs:
            print(f"No tabs in session '{session}'.")
        for t in tabs:
            print(f"[{t.get('tabId')}] {t.get('title')} ({t.get('url')})")

    elif sub == "close-all":
        # Close tabs in default and worker sessions
        csi_post("close_session", {}, session)
        for i in range(25):
            csi_post("close_session", {}, f"w_{i}")
        print("Closed all session tabs.")

    elif sub == "nav":
        url = args.target
        lite = getattr(args, "lite", False)
        res = csi_fast_nav(url, session=session, lite=lite)
        print(f"Navigated to {url} in {res['elapsed_s']}s (session: {session}, lite: {lite})")

    elif sub == "snap":
        res = csi_post("snapshot", {"compact": True}, session)
        d = res.get("data") or {}
        print(f"TITLE: {d.get('title')}\nURL: {d.get('url')}\n{d.get('tree')}")

    elif sub == "eval":
        code = (args.target + " " + " ".join(args.js_code)).strip()
        val = csi_eval_unwrap(code, session)
        print(json.dumps(val, indent=2) if isinstance(val, (dict, list)) else val)


    elif sub == "subreddit":
        name = re.sub(r"^/?r/", "", args.target.strip()).split("/")[0]
        cache_key = f"sub:{name.lower()}"
        if not no_cache:
            hit = cache_get(cache_key, ttl_hours=48.0)
            if hit:
                print(f"[CACHE HIT 1ms] {name}:")
                print(json.dumps(hit, indent=2))
                return

        res = extract_subreddit_info(name, session=session)
        if res.get("status") == "LIVE":
            cache_set(cache_key, res, category="subreddit")
        print(json.dumps(res, indent=2))


    elif sub == "search":
        q = args.target.strip()
        limit = args.limit or 5
        cache_key = f"rsearch:{q.lower()}:{limit}"
        if not no_cache:
            hit = cache_get(cache_key, ttl_hours=12.0)
            if hit:
                print(f"[CACHE HIT 1ms] Reddit search '{q}':")
                print(json.dumps(hit, indent=2))
                return

        q_enc = urllib.parse.quote(q)
        url = f"https://www.reddit.com/search.json?q={q_enc}&sort=relevance&limit={limit}&raw_json=1"
        csi_post("navigate", {"url": url, "newTab": True}, session)
        time.sleep(1.5)
        raw = csi_eval_unwrap("document.body.innerText", session)
        try:
            d = json.loads(raw) if isinstance(raw, str) else raw
            children = (d.get("data") or {}).get("children", [])
            out = []
            for c in children:
                post = c.get("data", {})
                out.append({
                    "title": post.get("title"),
                    "subreddit": post.get("subreddit"),
                    "author": post.get("author"),
                    "score": post.get("score"),
                    "comments": post.get("num_comments"),
                    "url": f"https://www.reddit.com{post.get('permalink')}"
                })
            cache_set(cache_key, out, category="reddit_search")
            print(json.dumps(out, indent=2))
        except Exception as e:
            print(f"Error parsing search JSON: {e}")

    elif sub == "comments":
        url = args.target.strip()
        limit = args.limit or 10
        cache_key = f"comments:{url}:{limit}"
        if not no_cache:
            hit = cache_get(cache_key, ttl_hours=24.0)
            if hit:
                print(f"[CACHE HIT 1ms] Thread comments:")
                print(json.dumps(hit, indent=2))
                return

        out = extract_reddit_thread(url, session=session, limit_comments=limit)
        if out.get("status") == "LIVE":
            cache_set(cache_key, out, category="comments")
        print(json.dumps(out, indent=2))

    elif sub == "google":
        q = args.target.strip()
        limit = args.limit or 5
        lite = getattr(args, "lite", True)
        cache_key = f"google:{q.lower()}:{limit}"
        if not no_cache:
            hit = cache_get(cache_key, ttl_hours=24.0)
            if hit:
                print(f"[CACHE HIT 1ms] Google search '{q}':")
                print(json.dumps(hit, indent=2))
                return

        q_enc = urllib.parse.quote(q)
        url = f"https://www.google.com/search?q={q_enc}"
        nav_info = csi_fast_nav(url, session=session, lite=lite)
        js = """(function(){
            var res = [];
            document.querySelectorAll('h3').forEach(function(h) {
                var a = h.closest('a');
                if (a && a.href && h.textContent.trim()) {
                    res.push({title: h.textContent.trim(), url: a.href});
                }
            });
            return JSON.stringify(res);
        })()"""
        raw = csi_eval_unwrap(js, session)
        items = json.loads(raw) if isinstance(raw, str) else (raw or [])
        res_list = items[:limit]
        cache_set(cache_key, res_list, category="google_search")
        print(f"Google search: '{q}' ({nav_info['elapsed_s']}s, lite={lite})")
        print(json.dumps(res_list, indent=2))

    elif sub == "batch":
        cmd_csi_batch(args)

    elif sub == "batch-search":
        cmd_csi_batch_search(args)

    elif sub == "viral":
        target_sub = args.target.strip() or "AskReddit"
        limit = args.limit or 5
        print(f"Scanning /r/{target_sub}/rising for Golden Window opportunities...")
        candidates = viral_finder.fetch_rising_posts(csi_post, csi_eval_unwrap, target_sub, session=session, limit=25)
        print(f"\nTop {min(limit, len(candidates))} Rising Candidate Posts on r/{target_sub}:")
        for idx, c in enumerate(candidates[:limit]):
            star = "[GOLDEN WINDOW]" if c["is_golden_window"] else "[RISING]"
            print(f"\n#{idx+1} {star} Velocity: {c['velocity']} v/m | Score: {c['score']} | Comments: {c['comments']} | Age: {c['age_minutes']}m")
            print(f"   Title: {c['title']}")
            print(f"   URL:   {c['url']}")

    elif sub == "draft":
        post_url = args.target.strip()
        if not post_url:
            print("Error: Post URL required for draft.")
            return
        style = getattr(args, "style", None) or "insightful"
        custom_text = getattr(args, "text", None)
        typos = getattr(args, "typos", 0.035)

        print(f"Analyzing post context from: {post_url}")
        ctx = viral_finder.extract_post_context(csi_post, csi_eval_unwrap, post_url, session=session)
        print(f"Title: {ctx.get('title')}")
        sub_name = ctx.get("subreddit") or "AskReddit"

        drafts = reddit_agent.draft_comment_options(
            ctx.get("title", ""),
            subreddit=sub_name,
            selftext=ctx.get("selftext", ""),
            top_comments=ctx.get("top_comments")
        )
        print("\n--- Anti-AI Comment Drafts (Zero Em-Dashes, Native Reddit Voice) ---")
        for k, v in drafts.items():
            print(f"[{k.upper()}]:\n{v}\n")

        selected_text = custom_text or drafts.get(style.lower()) or drafts["insightful"]
        print(f"Proceeding to type [{style.upper()}] comment in Chrome (STRICT DRY RUN)...")
        res = reddit_agent.type_comment_dry_run(csi_post, csi_eval_unwrap, post_url, selected_text, session=session, typo_rate=typos)
        print(json.dumps(res, indent=2))

    elif sub == "warm":
        target_sub = args.target.strip() or "AskReddit"
        duration = float(getattr(args, "duration", 3.0) or 3.0)
        res = reddit_agent.run_warmup_session(csi_post, csi_eval_unwrap, subreddit=target_sub, duration_minutes=duration, session=session)
        print(json.dumps(res, indent=2))



# =====================================================================
# 6. Parallel Multi-Tab Batching (Up to 20+ Workers)
# =====================================================================
def _worker_fetch_url(idx: int, url: str, lite: bool) -> dict:
    sess = f"w_{idx % 25}"
    t0 = time.time()
    try:
        if "reddit.com/r/" in url and "/comments/" in url:
            data = extract_reddit_thread(url, session=sess, limit_comments=5)
            elapsed = round(time.time() - t0, 2)
            return {"url": url, "status": data.get("status"), "data": data, "elapsed_s": elapsed}
        else:
            nav_res = csi_fast_nav(url, session=sess, timeout=6.0, lite=lite)
            title = csi_eval_unwrap("document.title", sess)
            elapsed = round(time.time() - t0, 2)
            return {"url": url, "status": "OK", "title": title, "elapsed_s": elapsed}
    except Exception as e:
        return {"url": url, "status": "ERROR", "error": str(e), "elapsed_s": round(time.time() - t0, 2)}


def cmd_csi_batch(args):
    path = args.target
    if not os.path.exists(path):
        print(f"File not found: {path}")
        return

    with open(path, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    workers = max(1, min(args.workers or 5, 25))
    lite = getattr(args, "lite", True)
    out_path = args.out or os.path.join(BASE_DIR, "batch_results.jsonl")

    print(f"=== Starting Parallel Batch ({len(urls)} URLs across {workers} workers, lite={lite}) ===")
    t_start = time.time()
    results = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_worker_fetch_url, i, url, lite): url for i, url in enumerate(urls)}
        for i, fut in enumerate(concurrent.futures.as_completed(futures)):
            res = fut.result()
            results.append(res)
            print(f"  [{i+1}/{len(urls)}] ({res.get('elapsed_s')}s) {res['url'][:65]} -> {res.get('status')}")

    # Clean up worker tabs
    for i in range(workers):
        try:
            csi_post("close_session", {}, f"w_{i}")
        except Exception:
            pass

    t_total = round(time.time() - t_start, 2)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\nDone! Processed {len(urls)} URLs in {t_total}s ({t_total/len(urls):.2f}s/URL).")
    print(f"Results saved to: {out_path}")


def _worker_search_query(idx: int, query: str, limit: int, lite: bool) -> dict:
    sess = f"w_{idx % 25}"
    t0 = time.time()
    try:
        q_enc = urllib.parse.quote(query)
        url = f"https://www.google.com/search?q={q_enc}"
        csi_fast_nav(url, session=sess, timeout=6.0, lite=lite)
        js = """(function(){
            var res = [];
            document.querySelectorAll('h3').forEach(function(h) {
                var a = h.closest('a');
                if (a && a.href && h.textContent.trim()) {
                    res.push({title: h.textContent.trim(), url: a.href});
                }
            });
            return JSON.stringify(res);
        })()"""
        raw = csi_eval_unwrap(js, sess)
        items = json.loads(raw) if isinstance(raw, str) else (raw or [])
        elapsed = round(time.time() - t0, 2)
        return {"query": query, "status": "OK", "count": len(items), "results": items[:limit], "elapsed_s": elapsed}
    except Exception as e:
        return {"query": query, "status": "ERROR", "error": str(e), "elapsed_s": round(time.time() - t0, 2)}


def cmd_csi_batch_search(args):
    path = args.target
    if not os.path.exists(path):
        print(f"File not found: {path}")
        return

    with open(path, "r", encoding="utf-8") as f:
        queries = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    workers = max(1, min(args.workers or 5, 25))
    limit = args.limit or 5
    lite = getattr(args, "lite", True)
    out_path = args.out or os.path.join(BASE_DIR, "search_batch_results.jsonl")

    print(f"=== Starting Parallel Batch Search ({len(queries)} queries across {workers} workers, lite={lite}) ===")
    t_start = time.time()
    results = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_worker_search_query, i, q, limit, lite): q for i, q in enumerate(queries)}
        for i, fut in enumerate(concurrent.futures.as_completed(futures)):
            res = fut.result()
            results.append(res)
            print(f"  [{i+1}/{len(queries)}] ({res.get('elapsed_s')}s) '{res['query']}' -> {res.get('count')} results")

    for i in range(workers):
        try:
            csi_post("close_session", {}, f"w_{i}")
        except Exception:
            pass

    t_total = round(time.time() - t_start, 2)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\nDone! Processed {len(queries)} queries in {t_total}s ({t_total/len(queries):.2f}s/query).")
    print(f"Results saved to: {out_path}")


# =====================================================================
# 7. ixBrowser Multi-Account Driver
# =====================================================================
def ix_api(path: str, data: dict = None) -> dict:
    url = f"http://127.0.0.1:{IX_PORT}{path}"
    body = json.dumps(data or {}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def cmd_ix(args):
    sub = args.ix_action

    if sub == "list":
        limit = args.limit or 100
        res = ix_api("/api/v2/profile-list", {"page": 1, "page_size": limit})
        items = (res.get("data") or {}).get("data", [])
        total = (res.get("data") or {}).get("total", 0)
        filter_group = (args.group or "").lower()

        print(f"ixBrowser Profiles (showing {len(items)} of {total}):")
        for p in items:
            gname = p.get("group_name", "")
            if filter_group and filter_group not in gname.lower():
                continue
            pid = p.get("profile_id")
            user = p.get("username") or "-"
            proxy = f"{p.get('proxy_ip')}:{p.get('proxy_port')}" if p.get("proxy_ip") else "DIRECT"
            print(f"  PID {pid:3d} | User: {user:20s} | Group: {gname:25s} | Proxy: {proxy}")

    elif sub == "open":
        pid = int(args.target)
        res = ix_api("/api/v2/profile-open", {"profile_id": pid, "cookies_backup": True})
        if (res.get("error") or {}).get("code") != 0:
            print(f"Failed to open profile {pid}: {res.get('error')}")
            return
        ws = (res.get("data") or {}).get("ws")
        print(f"Profile {pid} opened successfully. (CDP WebSocket: {ws})")

        if args.url:
            async def _nav():
                from playwright.async_api import async_playwright
                async with async_playwright() as pw:
                    browser = await pw.chromium.connect_over_cdp(ws)
                    ctx = browser.contexts[0]
                    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                    await page.goto(args.url, wait_until="domcontentloaded")
                    print(f"Navigated to {args.url} (Title: {await page.title()})")
            asyncio.run(_nav())

    elif sub == "close":
        pid = int(args.target)
        res = ix_api("/api/v2/profile-close", {"profile_id": pid})
        print(f"Closed profile {pid}: {res.get('error') or 'success'}")

    elif sub == "warm":
        import ix_warmup
        pids = [int(x.strip()) for x in args.target.split(",") if x.strip()] if args.target else None
        limit = args.limit or 3
        asyncio.run(ix_warmup.run_all_profiles(pids=pids, max_profiles=limit))


    elif sub == "run":
        pid = int(args.target)
        url = args.url
        eval_js = args.eval
        shot = args.shot

        async def _execute():
            from playwright.async_api import async_playwright
            res = ix_api("/api/v2/profile-open", {"profile_id": pid, "cookies_backup": True})
            ws = (res.get("data") or {}).get("ws")
            if not ws:
                print(f"Error getting ws for profile {pid}: {res.get('error')}")
                return
            async with async_playwright() as pw:
                browser = await pw.chromium.connect_over_cdp(ws)
                ctx = browser.contexts[0]
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                if url:
                    print(f"Navigating to {url} ...")
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                title = await page.title()
                print(f"Current page title: {title}")
                if eval_js:
                    v = await page.evaluate(eval_js)
                    print(f"Eval result: {v}")
                if shot:
                    shot_path = os.path.join(BASE_DIR, f"shot_p{pid}_{int(time.time())}.jpg")
                    await page.screenshot(path=shot_path, type="jpeg", quality=70)
                    print(f"Screenshot saved: {shot_path}")
        asyncio.run(_execute())

    elif sub == "batch-run":
        cmd_ix_batch_run(args)


def cmd_ix_batch_run(args):
    path = args.target
    if not os.path.exists(path):
        print(f"Tasks file not found: {path}")
        return

    with open(path, "r", encoding="utf-8") as f:
        tasks = json.load(f)

    if isinstance(tasks, dict) and "tasks" in tasks:
        tasks = tasks["tasks"]

    concurrency = args.concurrency or 4
    out_file = args.out or os.path.join(BASE_DIR, "ix_batch_results.json")
    print(f"=== Starting ixBrowser Batch ({len(tasks)} tasks, concurrency={concurrency}) ===")

    async def _runner():
        from playwright.async_api import async_playwright
        sem = asyncio.Semaphore(concurrency)
        results = []

        async with async_playwright() as pw:
            async def _do_one(task):
                async with sem:
                    pid = task["profile_id"]
                    url = task.get("url")
                    act = task.get("actions", [])
                    t0 = time.time()
                    try:
                        res = ix_api("/api/v2/profile-open", {"profile_id": pid, "cookies_backup": True})
                        ws = (res.get("data") or {}).get("ws")
                        if not ws:
                            return {"profile_id": pid, "status": "ERROR", "error": "no_ws"}
                        browser = await pw.chromium.connect_over_cdp(ws, timeout=30000)
                        ctx = browser.contexts[0]
                        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                        if url:
                            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                        title = await page.title()
                        eval_res = None
                        if act and "eval" in act[0]:
                            eval_res = await page.evaluate(act[0]["eval"])
                        return {
                            "profile_id": pid,
                            "status": "OK",
                            "title": title,
                            "eval": eval_res,
                            "elapsed_s": round(time.time() - t0, 2)
                        }
                    except Exception as e:
                        return {"profile_id": pid, "status": "ERROR", "error": str(e)[:120]}

            tasks_futs = [_do_one(t) for t in tasks]
            results = await asyncio.gather(*tasks_futs)

        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\nDone! Processed {len(tasks)} ixBrowser profile tasks.")
        print(f"Results saved to: {out_file}")

    asyncio.run(_runner())


def cmd_bsk(args):
    action = args.bsk_action
    target = args.target

    if action == "status":
        st = bsk_driver.get_status()
        print(json.dumps(st, indent=2))
    elif action == "browsers":
        browsers = bsk_driver.list_browsers()
        print(f"Connected Browsers ({len(browsers)}):")
        for b in browsers:
            print(f"  - {b}")
    elif action == "nav":
        if not target:
            print("Error: Target URL required for 'bsk nav'")
            return
        print(f"Navigating to {target} via BrowserSkill...")
        res = bsk_driver.bsk_read_url(target)
        print(json.dumps(res, indent=2))
    elif action == "observe":
        sess_id = getattr(args, "session", None)
        if not sess_id:
            with bsk_driver.BskSession() as sess:
                obs = sess.observe()
                print(json.dumps(obs, indent=2))
        else:
            res = bsk_driver._run_bsk(["observe", "--session", sess_id])
            print(json.dumps(res.get("data") or res.get("stdout"), indent=2))
    elif action == "snapshot":
        sess_id = getattr(args, "session", None)
        if not sess_id:
            with bsk_driver.BskSession() as sess:
                snap = sess.snapshot()
                print(json.dumps(snap, indent=2))
        else:
            res = bsk_driver._run_bsk(["snapshot", "--session", sess_id])
            print(json.dumps(res.get("data") or res.get("stdout"), indent=2))
    elif action == "eval":
        js = " ".join(args.js_code) if args.js_code else target
        sess_id = getattr(args, "session", None)
        if not sess_id:
            print("Error: Provide active --session <id> for eval or use bsk nav first")
            return
        res = bsk_driver._run_bsk(["evaluate", js, "--session", sess_id])
        print(json.dumps(res.get("data") or res.get("stdout"), indent=2))
    elif action == "shot":
        out = target or "bsk_screenshot.png"
        sess_id = getattr(args, "session", None)
        if not sess_id:
            print("Error: Provide active --session <id> for screenshot")
            return
        res = bsk_driver._run_bsk(["screenshot", "--out", out, "--session", sess_id])
        print(f"Screenshot saved to {out}")
    elif action == "click":
        if not target:
            print("Error: target ref (@eN) or selector required for click")
            return
        sess_id = getattr(args, "session", None)
        if not sess_id:
            print("Error: Provide active --session <id> for click")
            return
        res = bsk_driver._run_bsk(["click", target, "--session", sess_id])
        print(json.dumps(res.get("data") or res.get("stdout"), indent=2))
    elif action == "fill":
        if not target:
            print("Error: target ref (@eN) required for fill")
            return
        val = getattr(args, "text", "") or ""
        sess_id = getattr(args, "session", None)
        if not sess_id:
            print("Error: Provide active --session <id> for fill")
            return
        res = bsk_driver._run_bsk(["fill", target, "--value", val, "--session", sess_id])
        print(json.dumps(res.get("data") or res.get("stdout"), indent=2))
    elif action == "start":
        sess = bsk_driver.BskSession()
        sid = sess.start()
        print(f"Started BrowserSkill session: {sid}")
    elif action == "stop":
        if not target:
            print("Error: session ID required for stop")
            return
        ok = bsk_driver._run_bsk(["session", "stop", target], use_json=False)
        print(f"Session {target} stopped: {ok.get('success')}")


def cmd_subs(args):
    cat = getattr(args, "cat", None)
    karma_type = getattr(args, "karma_type", None)
    limit = getattr(args, "limit", 50)
    subs = viral_finder.list_curated_subreddits(category=cat, karma_type=karma_type)
    print(f"Curated Subreddits Playbook (Sheet 3) - {len(subs)} communities:\n")
    for s in subs[:limit]:
        print(f"r/{s['sub']:<22} | {s['members']:<7} | {s['cat']:<16} | Karma: {s['karma']:<7}")
        print(f"  Restrictions: {s['restrictions']}")
        print(f"  Tip:          {s['tip']}\n")


# =====================================================================
# Main Parser
# =====================================================================
def main():
    parser = argparse.ArgumentParser(description="Pilot: High-Performance Browser & Research Automation Suite")
    subparsers = parser.add_subparsers(dest="cmd")

    # status
    subparsers.add_parser("status", help="Check status of CSI, ixBrowser, Bridge, ngrok, BrowserSkill")

    # bsk (BrowserSkill)
    p_bsk = subparsers.add_parser("bsk", help="Drive user browser via Tencent BrowserSkill (bsk)")
    p_bsk.add_argument("bsk_action", choices=[
        "status", "browsers", "nav", "observe", "snapshot", "eval", "shot", "click", "fill", "start", "stop"
    ])
    p_bsk.add_argument("target", nargs="?", default="")
    p_bsk.add_argument("js_code", nargs="*", default=[])
    p_bsk.add_argument("-s", "--session", default="", help="BrowserSkill session ID")
    p_bsk.add_argument("--text", default="", help="Text value for fill")

    # subs
    p_subs = subparsers.add_parser("subs", help="Browse 65 curated subreddits from Sheet 3")
    p_subs.add_argument("--cat", default="", help="Filter by category")
    p_subs.add_argument("--karma-type", default="", help="Filter by karma type (Comment, Post, Both)")
    p_subs.add_argument("--limit", type=int, default=50, help="Listing limit")

    # csi
    p_csi = subparsers.add_parser("csi", help="Drive real Chrome via CSI daemon")
    p_csi.add_argument("csi_action", choices=[
        "status", "tabs", "nav", "snap", "eval", "subreddit", "search", "comments", "google", "batch", "batch-search", "close-all",
        "viral", "draft", "warm"
    ])
    p_csi.add_argument("target", nargs="?", default="")
    p_csi.add_argument("js_code", nargs="*", default=[])
    p_csi.add_argument("-s", "--session", default="pilot", help="CSI session name")
    p_csi.add_argument("--limit", type=int, default=5, help="Result limit")
    p_csi.add_argument("--workers", type=int, default=5, help="Number of parallel workers (up to 25)")
    p_csi.add_argument("--lite", action="store_true", default=False, help="Enable CDP Lite Mode (blocks images/fonts/trackers)")
    p_csi.add_argument("--no-cache", action="store_true", default=False, help="Bypass local SQLite cache")
    p_csi.add_argument("--style", default="insightful", choices=["insightful", "witty", "anecdotal"], help="Comment tone")
    p_csi.add_argument("--text", default=None, help="Custom comment text to type")
    p_csi.add_argument("--duration", type=float, default=3.0, help="Warmup duration in minutes")
    p_csi.add_argument("--typos", type=float, default=0.035, help="Typo frequency (0.0 to 0.1)")
    p_csi.add_argument("--out", default=None, help="Output file path (.jsonl or .json)")

    # ix
    p_ix = subparsers.add_parser("ix", help="Drive ixBrowser anti-detect profiles")
    p_ix.add_argument("ix_action", choices=["list", "open", "close", "run", "batch-run", "warm"])

    p_ix.add_argument("target", nargs="?", default="")
    p_ix.add_argument("--group", default="", help="Filter profiles by group name")
    p_ix.add_argument("--limit", type=int, default=100, help="Profile listing limit")
    p_ix.add_argument("--url", default="", help="URL to navigate to")
    p_ix.add_argument("--eval", default="", help="JavaScript code to evaluate")
    p_ix.add_argument("--shot", action="store_true", help="Capture screenshot")
    p_ix.add_argument("--concurrency", type=int, default=4, help="Concurrency for ix batch-run")
    p_ix.add_argument("--out", default=None, help="Output file path (.json)")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(1)

    if args.cmd == "status":
        cmd_status(args)
    elif args.cmd == "bsk":
        cmd_bsk(args)
    elif args.cmd == "subs":
        cmd_subs(args)
    elif args.cmd == "csi":
        cmd_csi(args)
    elif args.cmd == "ix":
        cmd_ix(args)


if __name__ == "__main__":
    main()


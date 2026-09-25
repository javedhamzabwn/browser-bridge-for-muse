#!/usr/bin/env python3
"""Parallel ixBrowser profile task runner. Runs ON the bridge PC (via /exec).

Commands:
  python ptask.py open 193,201,203 [--url URL] [--concurrency 4] [--retries 3]
  python ptask.py run <tasks.json | b64:...> [--concurrency 4] [--out results.json]
  python ptask.py close 193,201
  python ptask.py ping 193,201

Task JSON: {"tasks": [{"profile_id": 193, "url": "https://...",
  "actions": [{"goto": "https://..."}, {"wait": "sel", "timeout": 15000},
  {"click": "sel"}, {"fill": ["sel", "text"]}, {"eval": "document.title"},
  {"cookies": true}, {"shot": true}, {"title": true}]}]}

Notes:
- profile-open is idempotent: on an already-open profile ixBrowser returns
  the live ws URL, so attach never needs a separate state file.
- Profiles are NEVER closed here (no profile-close, no CDP browser.close);
  the browser windows stay open. Use "close" only for deliberate cleanup.
- Screenshots are saved under shots/ on this PC; only the path is returned
  (full images over the tunnel previously caused IncompleteRead).
- Tab rule: the last tab is never closed (that kills the window). One tab is
  reused for navigation, the rest are closed.
"""

import argparse
import asyncio
import base64
import json
import os
import sys
import time

IX = "http://127.0.0.1:53400"
HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(HERE, "shots")


async def ix_api(client, path, body):
    r = await client.post(IX + path, json=body, timeout=120.0)
    return r.json()


async def attach(pw, pid, client, retries=3):
    """Attach to a profile via CDP. Idempotent: works whether the profile
    was already open or not."""
    last = None
    for attempt in range(retries):
        try:
            env = await ix_api(client, "/api/v2/profile-open",
                               {"profile_id": pid, "cookies_backup": True})
            err = env.get("error") or {}
            if (err.get("code") or 0) != 0:
                last = "open: {}".format(err)
            else:
                ws = (env.get("data") or {}).get("ws")
                if not ws:
                    last = "open: no ws url"
                else:
                    browser = await pw.chromium.connect_over_cdp(ws,
                                                                 timeout=30000)
                    if not browser.contexts:
                        last = "open: no contexts"
                    else:
                        return browser
        except Exception as e:
            last = "attach: {}".format(str(e)[:120])
        await asyncio.sleep(2 * (attempt + 1))
    raise RuntimeError(last or "attach failed")


async def tidy_to_url(ctx, url):
    """Keep one tab (existing reddit tab if any, else the first tab), close
    the rest, navigate it to url. The last tab is never closed."""
    pages = ctx.pages
    keep = None
    for pg in pages:
        try:
            if "reddit.com" in (pg.url or ""):
                keep = pg
                break
        except Exception:
            pass
    if keep is None:
        keep = pages[0] if pages else await ctx.new_page()
    for pg in list(ctx.pages):
        if pg is not keep:
            try:
                await pg.close()
            except Exception:
                pass
    await keep.goto(url, wait_until="domcontentloaded", timeout=60000)
    await keep.wait_for_timeout(1200)
    for pg in list(ctx.pages):
        if pg is not keep:
            try:
                await pg.close()
            except Exception:
                pass
    return keep


async def do_action(page, ctx, act):
    try:
        if "goto" in act:
            await page.goto(act["goto"], wait_until="domcontentloaded",
                            timeout=60000)
            return {"ok": True, "url": page.url,
                    "title": (await page.title())[:80]}
        if "wait" in act:
            await page.wait_for_selector(act["wait"],
                                         timeout=int(act.get("timeout", 15000)))
            return {"ok": True}
        if "click" in act:
            await page.click(act["click"], timeout=30000)
            return {"ok": True}
        if "fill" in act:
            sel, text = act["fill"][0], act["fill"][1]
            await page.fill(sel, text, timeout=30000)
            return {"ok": True}
        if "eval" in act:
            val = await page.evaluate(act["eval"])
            return {"ok": True, "value": val}
        if act.get("cookies"):
            cks = await ctx.cookies()
            names = sorted({c["name"] for c in cks})
            return {"ok": True, "count": len(cks),
                    "reddit_session": "reddit_session" in names}
        if act.get("title"):
            return {"ok": True, "title": (await page.title())[:80],
                    "url": page.url}
        if act.get("shot"):
            os.makedirs(SHOTS, exist_ok=True)
            path = os.path.join(
                SHOTS, "p{}_{}.jpg".format(act.get("_pid", "?"),
                                           int(time.time())))
            await page.screenshot(path=path, type="jpeg", quality=60)
            return {"ok": True, "shot": path}
        return {"ok": False,
                "error": "unknown action {}".format(sorted(act.keys()))}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


async def open_one(pid, url, pw, client, sem, idx, retries):
    async with sem:
        await asyncio.sleep(0.3 * idx)  # stagger launches

        async def _go():
            browser = await attach(pw, pid, client, retries)
            ctx = browser.contexts[0]
            keep = await tidy_to_url(ctx, url)
            title = ""
            try:
                title = await keep.title()
            except Exception:
                pass
            # detach only: never browser.close(), never profile-close
            return {"profile_id": pid, "status": "open",
                    "tabs": len(ctx.pages), "title": title[:60]}

        try:
            return await asyncio.wait_for(_go(), timeout=180)
        except Exception as e:
            return {"profile_id": pid, "status": "error",
                    "error": str(e)[:200]}


async def run_one(task, pw, client, sem, idx, retries):
    async with sem:
        await asyncio.sleep(0.3 * idx)
        pid = task["profile_id"]

        async def _go():
            browser = await attach(pw, pid, client, retries)
            ctx = browser.contexts[0]
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            res = []
            if task.get("url"):
                res.append(await do_action(page, ctx,
                                           {"goto": task["url"],
                                            "_pid": pid}))
            for act in task.get("actions", []):
                act = dict(act)
                act["_pid"] = pid
                res.append(await do_action(page, ctx, act))
            return {"profile_id": pid, "status": "done", "actions": res}

        try:
            return await asyncio.wait_for(_go(), timeout=300)
        except Exception as e:
            return {"profile_id": pid, "status": "error",
                    "error": str(e)[:200]}


async def close_one(pid, client, sem):
    async with sem:
        try:
            env = await ix_api(client, "/api/v2/profile-close",
                               {"profile_id": pid})
            err = env.get("error") or {}
            ok = (err.get("code") or 0) == 0
            return {"profile_id": pid,
                    "status": "closed" if ok else "error",
                    "detail": str(err)[:120]}
        except Exception as e:
            return {"profile_id": pid, "status": "error",
                    "error": str(e)[:150]}


async def ping_one(pid, pw, client, sem, idx):
    async with sem:
        await asyncio.sleep(0.2 * idx)
        try:
            b = await attach(pw, pid, client, 1)
            n = len(b.contexts[0].pages) if b.contexts else 0
            return {"profile_id": pid, "status": "alive", "tabs": n}
        except Exception as e:
            return {"profile_id": pid, "status": "dead",
                    "error": str(e)[:150]}


def parse_pids(s):
    return [int(x) for x in s.split(",") if x.strip()]


def load_tasks(arg):
    if arg.startswith("b64:"):
        return json.loads(base64.b64decode(arg[4:]).decode("utf-8"))
    if os.path.isfile(arg):
        with open(arg, encoding="utf-8") as f:
            return json.load(f)
    return json.loads(arg)


async def main_async(args):
    import httpx
    from playwright.async_api import async_playwright

    results = []
    async with httpx.AsyncClient() as client:
        pw = await async_playwright().start()
        try:
            sem = asyncio.Semaphore(max(1, args.concurrency))
            if args.cmd == "open":
                pids = parse_pids(args.pids)
                coros = [open_one(pid, args.url, pw, client, sem, i,
                                  args.retries)
                         for i, pid in enumerate(pids)]
                results = await asyncio.gather(*coros)
            elif args.cmd == "run":
                spec = load_tasks(args.tasks)
                tasks = spec["tasks"] if isinstance(spec, dict) else spec
                coros = [run_one(t, pw, client, sem, i, args.retries)
                         for i, t in enumerate(tasks)]
                results = await asyncio.gather(*coros)
            elif args.cmd == "close":
                pids = parse_pids(args.pids)
                coros = [close_one(pid, client, sem) for pid in pids]
                results = await asyncio.gather(*coros)
            elif args.cmd == "ping":
                pids = parse_pids(args.pids)
                coros = [ping_one(pid, pw, client, sem, i)
                         for i, pid in enumerate(pids)]
                results = await asyncio.gather(*coros)
        finally:
            await pw.stop()
    return results


def main():
    ap = argparse.ArgumentParser(
        description="Parallel ixBrowser profile tasks (runs on bridge PC)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("open")
    p.add_argument("pids")
    p.add_argument("--url", default="https://www.reddit.com")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--retries", type=int, default=3)
    p = sub.add_parser("run")
    p.add_argument("tasks")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--out", default=None)
    p = sub.add_parser("close")
    p.add_argument("pids")
    p.add_argument("--concurrency", type=int, default=4)
    p = sub.add_parser("ping")
    p.add_argument("pids")
    p.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()
    results = asyncio.run(main_async(args))
    out = json.dumps(results, indent=1, default=str)
    if getattr(args, "out", None):
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
    print(out)


if __name__ == "__main__":
    main()

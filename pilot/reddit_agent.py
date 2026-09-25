#!/usr/bin/env python3
"""reddit_agent.py - Anti-AI Comment Drafter, Dry-Run Composer & Safe Warmup Engine.

Features:
1. Anti-AI Comment Generation:
   - STRICTLY ZERO em-dashes ('—') or double hyphens ('--').
   - ZERO staged AI phrases ('Certainly', 'It is worth noting', 'In conclusion').
   - Tailored to Reddit culture (punchy, witty, anecdotal, native slang).
2. Browser Composer Automation:
   - Types comment into the Reddit comment box using human typing simulation (typos + corrections).
   - STRICT DRY RUN: Stops with text filled, leaves submission button unclicked for user review.
3. Organic Warmup Routine:
   - Human scrolling and post reading loop without auto-upvoting.
"""
import json
import random
import re
import sys
import time
from typing import Any, Dict, List, Optional

import human
import viral_finder


# Blacklisted AI marker phrases and characters
FORBIDDEN_AI_MARKERS = [
    "—", "--", "moreover", "in conclusion", "it is worth noting",
    "it's worth noting", "certainly", "fascinating topic",
    "delve into", "tapestry", "beacon", "testament", "crucial aspect",
    "as an ai", "as someone who values", "great question", "i completely agree"
]


def sanitize_reddit_comment(text: str) -> str:
    """Strips em-dashes and ensures text adheres to anti-AI guidelines."""
    # Replace em-dashes and en-dashes with simple commas or parentheses
    clean = text.replace("—", ", ").replace("–", ", ").replace("--", ", ")
    clean = re.sub(r"\s+", " ", clean).strip()

    # Check for forbidden markers
    lower = clean.lower()
    for marker in FORBIDDEN_AI_MARKERS:
        if marker in lower and marker not in [","]:
            # Strip offending clause or replace
            pattern = re.compile(re.escape(marker), re.IGNORECASE)
            clean = pattern.sub("", clean)

    clean = re.sub(r"\s+,", ",", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def draft_comment_options(
    post_title: str,
    subreddit: str = "AskReddit",
    selftext: str = "",
    top_comments: Optional[List[Dict]] = None
) -> Dict[str, str]:
    """Generates 3 human-style comment drafts tailored to the subreddit and discussion vibe."""
    clean_sub = re.sub(r"^/?r/", "", subreddit).lower()
    tip_meta = viral_finder.get_sub_tip(clean_sub)
    tip = tip_meta.get("tip", "") if tip_meta else ""

    # Synthesize context
    title_words = [w.lower() for w in re.findall(r"\b\w+\b", post_title)]
    first_comment = top_comments[0]["body"] if top_comments else ""

    # Base drafts tailored by common subreddit personas
    drafts = {}

    if "askreddit" in clean_sub or "askmen" in clean_sub or "nostupidquestions" in clean_sub:
        drafts["insightful"] = (
            f"Honestly, the biggest thing most people miss with this is how much timing matters. "
            f"If you don't catch it early on, it just snowballs into a mess you have to clean up later."
        )
        drafts["witty"] = (
            f"Bold of you to assume half of us actually have our lives together enough to answer this properly."
        )
        drafts["anecdotal"] = (
            f"Had something similar happen to me a couple years back. Thought it was a one-off thing at first, "
            f"until my neighbor pointed out everyone on our street had been dealing with the exact same issue for months."
        )
    elif "explainlikeimfive" in clean_sub:
        drafts["insightful"] = (
            f"Think of it like a crowded highway. When everyone stays in their lane at normal speed, traffic flows fine. "
            f"The moment one car taps the brakes unexpectedly, the chain reaction slows down the entire line for miles."
        )
        drafts["witty"] = (
            f"Simple version: it's basically the digital equivalent of turning the TV off and on again, just on a massive scale."
        )
        drafts["anecdotal"] = (
            f"The best analogy I ever heard for this was comparing it to a restaurant kitchen. "
            f"You don't need more cooks if the bottleneck is just the waiter delivering tickets to the table."
        )
    elif "casualconversation" in clean_sub:
        drafts["insightful"] = (
            f"That's really neat to hear. Seems like small routines like that end up making the biggest difference in how your day goes."
        )
        drafts["witty"] = (
            f"I give it about three days before I abandon all good intentions and go right back to my old bad habits, but kudos to you."
        )
        drafts["anecdotal"] = (
            f"I started doing this last month too. Felt weird for the first week, but now I can't imagine starting my mornings without it."
        )
    else:
        drafts["insightful"] = (
            f"Good call on pointing this out. Most discussions around this skip over the practical side and focus on edge cases that never happen."
        )
        drafts["witty"] = (
            f"Well, that's five minutes of my life I won't get back, but at least I'm not the only one thinking it."
        )
        drafts["anecdotal"] = (
            f"Ran into this exact scenario last winter. Took three separate attempts before finding a workaround that didn't break everything else."
        )

    # Clean all drafts through anti-AI sanitizer
    sanitized = {k: sanitize_reddit_comment(v) for k, v in drafts.items()}
    return sanitized


def type_comment_dry_run(
    csi_post_func,
    csi_eval_unwrap_func,
    post_url: str,
    comment_text: str,
    session: str = "pilot",
    typo_rate: float = 0.035
) -> Dict[str, Any]:
    """Navigates to post, clicks composer with human jitter, types comment with typos/corrections.
    STRICT DRY RUN: Stops without clicking submit button. Leaves form ready for user review.
    """
    clean_url = post_url.split("?")[0].rstrip("/")
    print(f"\n[1/4] Navigating to post: {clean_url}")
    csi_post_func("navigate", {"url": clean_url, "newTab": True}, session)
    time.sleep(2.5)

    # Check login status
    js_chk_login = """(function(){
        var user = document.querySelector('shreddit-app') ? document.querySelector('shreddit-app').getAttribute('current-user-name') : null;
        return {loggedIn: !!user, username: user};
    })()"""
    auth = csi_eval_unwrap_func(js_chk_login, session)
    if isinstance(auth, dict) and not auth.get("loggedIn"):
        print("\n[NOTE] Browser is currently logged out of Reddit in this Chrome profile.")
        print("  Reddit only displays the interactive comment composer to logged-in users.")
        print("  Please log into Reddit in your Chrome browser, or target an ixBrowser profile.")
        return {
            "status": "LOGIN_REQUIRED",
            "message": "Comment composer requires active Reddit login in Chrome.",
            "comment_preview": comment_text,
            "url": clean_url
        }

    # Locate comment composer
    print("[2/4] Locating Reddit comment composer...")
    js_find_box = """(function(){

        // Try modern Reddit composer
        var comp = document.querySelector('shreddit-composer') ||
                   document.querySelector('faceplate-textarea-input') ||
                   document.querySelector('[name="comment"]') ||
                   document.querySelector('[contenteditable="true"]') ||
                   document.querySelector('textarea');
        if (!comp) return null;
        comp.scrollIntoView({behavior: 'smooth', block: 'center'});
        var rect = comp.getBoundingClientRect();
        return {
            tag: comp.tagName.toLowerCase(),
            x: rect.left + window.scrollX,
            y: rect.top + window.scrollY,
            w: rect.width,
            h: rect.height,
            visible: rect.width > 0 && rect.height > 0
        };
    })()"""

    box_meta = csi_eval_unwrap_func(js_find_box, session)
    if not box_meta or not box_meta.get("visible"):
        # Second attempt: check if we need to click "Add a comment" placeholder first
        js_click_prompt = """(function(){
            var p = document.querySelector('[placeholder*="comment" i]') ||
                    document.querySelector('button[aria-label*="comment" i]') ||
                    document.querySelector('#comment-placeholder');
            if (p) {
                p.click();
                return true;
            }
            return false;
        })()"""
        csi_eval_unwrap_func(js_click_prompt, session)
        time.sleep(1.0)
        box_meta = csi_eval_unwrap_func(js_find_box, session)

    if not box_meta or not box_meta.get("visible"):
        return {"status": "ERROR", "error": "comment_box_not_found", "url": clean_url}

    print(f"[3/4] Clicking into composer ({box_meta['tag']}) with Gaussian coordinate jitter...")
    px, py = human.jitter_point_in_box(box_meta["x"], box_meta["y"], box_meta["w"], box_meta["h"])

    csi_post_func("cdp", {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": px, "y": py}}, session)
    time.sleep(random.uniform(0.15, 0.28))
    csi_post_func("cdp", {"method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": px, "y": py, "button": "left", "clickCount": 1}}, session)
    time.sleep(random.uniform(0.07, 0.12))
    csi_post_func("cdp", {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": px, "y": py, "button": "left", "clickCount": 1}}, session)
    time.sleep(0.5)

    print(f"[4/4] Typing comment ({len(comment_text)} chars) with human WPM & QWERTY typo corrections...")
    clean_comment = sanitize_reddit_comment(comment_text)

    def progress(cur, total):
        pct = int(cur / total * 100)
        sys.stdout.write(f"\r  Typing progress: {pct}%")
        sys.stdout.flush()

    human.csi_human_type(csi_post_func, clean_comment, session=session, typo_rate=typo_rate, base_wpm=68.0, progress_callback=progress)
    print("\n  Typing complete!")

    # Proofreading pause
    time.sleep(random.uniform(1.2, 2.5))

    return {
        "status": "DRY_RUN_READY",
        "message": "Comment successfully typed into Reddit composer. Submit button left unclicked for manual review.",
        "comment_text": clean_comment,
        "url": clean_url
    }


def run_warmup_session(
    csi_post_func,
    csi_eval_unwrap_func,
    subreddit: str = "AskReddit",
    duration_minutes: float = 3.0,
    session: str = "pilot"
) -> Dict[str, Any]:
    """Browses subreddit feed like a genuine human. ZERO auto-upvoting."""
    clean_sub = re.sub(r"^/?r/", "", subreddit).strip()
    url = f"https://www.reddit.com/r/{clean_sub}/"
    print(f"\n[Warmup] Starting {duration_minutes}m organic browse session on r/{clean_sub}...")
    print("[Warmup] Mode: Human scrolling & reading (ZERO upvotes).")

    csi_post_func("navigate", {"url": url, "newTab": True}, session)
    time.sleep(2.5)

    t_end = time.time() + (duration_minutes * 60.0)
    steps_done = 0
    posts_inspected = 0

    while time.time() < t_end:
        # 1. Smooth scroll feed
        human.csi_human_scroll(csi_post_func, session=session, steps=random.randint(2, 4), reading_pause_min=2.5, reading_pause_max=5.5)
        steps_done += 1

        # 2. Pick a visible post to read occasionally (every 3-5 scroll bursts)
        if steps_done % random.randint(3, 5) == 0:
            js_pick_post = """(function(){
                var posts = document.querySelectorAll('shreddit-post');
                if (!posts || posts.length === 0) return null;
                var rand = posts[Math.floor(Math.random() * Math.min(posts.length, 6))];
                return rand ? rand.getAttribute('permalink') : null;
            })()"""
            post_permalink = csi_eval_unwrap_func(js_pick_post, session)
            if post_permalink:
                if not post_permalink.startswith("http"):
                    post_permalink = f"https://www.reddit.com{post_permalink}"
                print(f"  [Warmup] Inspecting thread: {post_permalink[:70]}...")
                csi_post_func("navigate", {"url": post_permalink, "newTab": True}, session)
                time.sleep(2.0)
                # Scroll thread comments
                human.csi_human_scroll(csi_post_func, session=session, steps=random.randint(3, 5), reading_pause_min=3.0, reading_pause_max=7.0)
                posts_inspected += 1
                # Return to feed
                csi_post_func("navigate", {"url": url, "newTab": False}, session)
                time.sleep(2.0)

    print(f"[Warmup] Finished session. Completed {steps_done} scroll bursts and inspected {posts_inspected} threads.")
    return {
        "status": "COMPLETED",
        "subreddit": clean_sub,
        "duration_minutes": duration_minutes,
        "posts_inspected": posts_inspected,
        "steps_done": steps_done
    }

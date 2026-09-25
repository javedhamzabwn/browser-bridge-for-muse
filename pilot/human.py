#!/usr/bin/env python3
"""human.py - Realistic Human Input Simulation for Browser Automation.

Simulates genuine human physics:
1. QWERTY keystroke timing with WPM variability (50-85 WPM).
2. Human typo injection based on keyboard physical adjacency with reaction pauses and Backspace correction.
3. Natural punctuation thinking pauses (commas, periods, line breaks).
4. Gaussian bounding-box jitter clicking (avoids dead-center bot detection).
5. Organic smooth scrolling with reading pauses and ZERO auto-upvoting.
"""
import random
import time
from typing import Dict, List, Optional, Tuple

# Physical QWERTY keyboard adjacency map for realistic typos
QWERTY_ADJACENT = {
    'a': ['q', 'w', 's', 'z'],
    'b': ['v', 'g', 'h', 'n'],
    'c': ['x', 'd', 'f', 'v'],
    'd': ['s', 'e', 'r', 'f', 'c', 'x'],
    'e': ['w', 'r', 's', 'd'],
    'f': ['d', 'r', 't', 'g', 'v', 'c'],
    'g': ['f', 't', 'y', 'h', 'b', 'v'],
    'h': ['g', 'y', 'u', 'j', 'n', 'b'],
    'i': ['u', 'o', 'j', 'k'],
    'j': ['h', 'u', 'i', 'k', 'n', 'm'],
    'k': ['j', 'i', 'o', 'l', 'm'],
    'l': ['k', 'o', 'p'],
    'm': ['n', 'j', 'k'],
    'n': ['b', 'h', 'j', 'm'],
    'o': ['i', 'p', 'k', 'l'],
    'p': ['o', 'l'],
    'q': ['w', 'a', 's'],
    'r': ['e', 't', 'd', 'f'],
    's': ['a', 'w', 'e', 'd', 'x', 'z'],
    't': ['r', 'y', 'f', 'g'],
    'u': ['y', 'i', 'h', 'j'],
    'v': ['c', 'f', 'g', 'b'],
    'w': ['q', 'e', 'a', 's'],
    'x': ['z', 's', 'd', 'c'],
    'y': ['t', 'u', 'g', 'h'],
    'z': ['a', 's', 'x'],
    '1': ['2', 'q'],
    '2': ['1', '3', 'w', 'q'],
    '3': ['2', '4', 'e', 'w'],
    '4': ['3', '5', 'r', 'e'],
    '5': ['4', '6', 't', 'r'],
    '6': ['5', '7', 'y', 't'],
    '7': ['6', '8', 'u', 'y'],
    '8': ['7', '9', 'i', 'u'],
    '9': ['8', '0', 'o', 'i'],
    '0': ['9', 'p', 'o']
}


def generate_typing_plan(
    text: str,
    typo_rate: float = 0.035,
    base_wpm: float = 65.0
) -> List[Dict]:
    """Generates a list of keystroke events with authentic human timing and typos."""
    base_char_delay = 60.0 / (base_wpm * 5.0)  # Average seconds per character (~0.18s)
    events: List[Dict] = []

    i = 0
    while i < len(text):
        target_char = text[i]
        lower_char = target_char.lower()

        # Decide if a typo should occur
        should_typo = (
            lower_char in QWERTY_ADJACENT
            and random.random() < typo_rate
            and len(text) > 3
        )

        if should_typo:
            # 1. Type 1 (or rarely 2) wrong adjacent keys
            wrong_char = random.choice(QWERTY_ADJACENT[lower_char])
            if target_char.isupper():
                wrong_char = wrong_char.upper()

            err_delay = max(0.04, random.gauss(base_char_delay, 0.03))
            events.append({"action": "key", "char": wrong_char, "delay": err_delay})

            double_typo = random.random() < 0.20
            if double_typo and wrong_char.lower() in QWERTY_ADJACENT:
                wrong_char2 = random.choice(QWERTY_ADJACENT[wrong_char.lower()])
                err_delay2 = max(0.04, random.gauss(base_char_delay * 0.8, 0.02))
                events.append({"action": "key", "char": wrong_char2, "delay": err_delay2})

            # 2. Reaction pause (150-350ms to 'realize' mistake)
            reaction_pause = random.uniform(0.16, 0.36)
            events.append({"action": "pause", "delay": reaction_pause})

            # 3. Backspace strokes
            bs_count = 2 if double_typo else 1
            for _ in range(bs_count):
                bs_delay = random.uniform(0.08, 0.14)
                events.append({"action": "backspace", "delay": bs_delay})

            # 4. Tiny pause before typing intended char
            events.append({"action": "pause", "delay": random.uniform(0.06, 0.12)})

        # Type the actual character
        char_delay = max(0.035, random.gauss(base_char_delay, 0.03))

        # Add thinking pauses for punctuation
        if target_char in [',', ';', ':']:
            char_delay += random.uniform(0.22, 0.45)
        elif target_char in ['.', '!', '?']:
            char_delay += random.uniform(0.40, 0.85)
        elif target_char == '\n':
            char_delay += random.uniform(0.50, 1.10)
        elif target_char == ' ':
            char_delay += random.uniform(0.04, 0.12)

        events.append({"action": "key", "char": target_char, "delay": char_delay})
        i += 1

    return events


def jitter_point_in_box(
    x: float, y: float, width: float, height: float, inner_ratio: float = 0.65
) -> Tuple[float, float]:
    """Picks a random coordinate inside the central box area using Gaussian distribution."""
    margin_w = width * (1.0 - inner_ratio) / 2.0
    margin_h = height * (1.0 - inner_ratio) / 2.0

    min_x = x + margin_w
    max_x = x + width - margin_w
    min_y = y + margin_h
    max_y = y + height - margin_h

    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0

    # Gaussian jitter around center
    std_x = (max_x - min_x) / 6.0
    std_y = (max_y - min_y) / 6.0

    px = max(min_x, min(max_x, random.gauss(center_x, std_x)))
    py = max(min_y, min(max_y, random.gauss(center_y, std_y)))
    return (round(px, 1), round(py, 1))


# =====================================================================
# CDP Input Drivers (for user's Chrome via CSI)
# =====================================================================
def csi_human_type(
    csi_post_func,
    text: str,
    session: str = "pilot",
    typo_rate: float = 0.035,
    base_wpm: float = 65.0,
    progress_callback=None
):
    """Executes realistic human typing in Chrome via CSI CDP commands."""
    plan = generate_typing_plan(text, typo_rate=typo_rate, base_wpm=base_wpm)
    total_events = len(plan)

    for idx, ev in enumerate(plan):
        action = ev["action"]
        delay = ev["delay"]

        if action == "key":
            ch = ev["char"]
            if ch == "\n":
                # Enter key
                csi_post_func("cdp", {
                    "method": "Input.dispatchKeyEvent",
                    "params": {"type": "rawKeyDown", "windowsVirtualKeyCode": 13, "key": "Enter", "code": "Enter"}
                }, session)
                csi_post_func("cdp", {
                    "method": "Input.dispatchKeyEvent",
                    "params": {"type": "keyUp", "windowsVirtualKeyCode": 13, "key": "Enter", "code": "Enter"}
                }, session)
            else:
                csi_post_func("cdp", {
                    "method": "Input.dispatchKeyEvent",
                    "params": {"type": "keyDown", "text": ch, "key": ch}
                }, session)
                csi_post_func("cdp", {
                    "method": "Input.dispatchKeyEvent",
                    "params": {"type": "keyUp", "key": ch}
                }, session)

        elif action == "backspace":
            csi_post_func("cdp", {
                "method": "Input.dispatchKeyEvent",
                "params": {"type": "rawKeyDown", "windowsVirtualKeyCode": 8, "key": "Backspace", "code": "Backspace"}
            }, session)
            csi_post_func("cdp", {
                "method": "Input.dispatchKeyEvent",
                "params": {"type": "keyUp", "windowsVirtualKeyCode": 8, "key": "Backspace", "code": "Backspace"}
            }, session)

        if delay > 0:
            time.sleep(delay)

        if progress_callback and idx % 10 == 0:
            progress_callback(idx, total_events)


def csi_human_click(
    csi_post_func,
    selector: str,
    session: str = "pilot"
) -> bool:
    """Clicks an element using Gaussian coordinate jitter and pre-click hover."""
    # Get bounding box via JavaScript
    js = """(function(){
        var el = document.querySelector('%s');
        if (!el) return null;
        el.scrollIntoView({behavior: 'smooth', block: 'center'});
        var rect = el.getBoundingClientRect();
        return {
            x: rect.left + window.scrollX,
            y: rect.top + window.scrollY,
            w: rect.width,
            h: rect.height,
            visible: rect.width > 0 && rect.height > 0
        };
    })()""" % selector.replace("'", "\\'")

    r = csi_post_func("evaluate", {"code": js}, session)
    val = (r.get("data") or {}).get("value")
    if not val or not isinstance(val, dict) or not val.get("visible"):
        # Fallback to standard click if bounding box unavailable
        csi_post_func("click", {"selector": selector}, session)
        return False

    px, py = jitter_point_in_box(val["x"], val["y"], val["w"], val["h"])

    # 1. Hover
    csi_post_func("cdp", {
        "method": "Input.dispatchMouseEvent",
        "params": {"type": "mouseMoved", "x": px, "y": py}
    }, session)
    time.sleep(random.uniform(0.12, 0.28))

    # 2. MouseDown
    csi_post_func("cdp", {
        "method": "Input.dispatchMouseEvent",
        "params": {"type": "mousePressed", "x": px, "y": py, "button": "left", "clickCount": 1}
    }, session)
    time.sleep(random.uniform(0.065, 0.135))

    # 3. MouseUp
    csi_post_func("cdp", {
        "method": "Input.dispatchMouseEvent",
        "params": {"type": "mouseReleased", "x": px, "y": py, "button": "left", "clickCount": 1}
    }, session)
    return True


def csi_human_scroll(
    csi_post_func,
    session: str = "pilot",
    steps: int = 4,
    min_chunk: int = 180,
    max_chunk: int = 380,
    reading_pause_min: float = 2.0,
    reading_pause_max: float = 5.0
):
    """Scrolls down smoothly with realistic human pauses. ZERO auto-upvoting."""
    for _ in range(steps):
        chunk = random.randint(min_chunk, max_chunk)
        # Smooth scroll step
        js = f"window.scrollBy({{top: {chunk}, behavior: 'smooth'}})"
        csi_post_func("evaluate", {"code": js}, session)

        # Occasional 40-80px upward re-reading scroll
        if random.random() < 0.25:
            time.sleep(random.uniform(0.8, 1.6))
            up_chunk = random.randint(40, 80)
            csi_post_func("evaluate", {"code": f"window.scrollBy({{top: -{up_chunk}, behavior: 'smooth'}})"}, session)

        pause = random.uniform(reading_pause_min, reading_pause_max)
        time.sleep(pause)


# =====================================================================
# Playwright Input Drivers (for ixBrowser Anti-Detect profiles)
# =====================================================================
async def playwright_human_type(
    page,
    text: str,
    typo_rate: float = 0.035,
    base_wpm: float = 65.0
):
    """Executes realistic human typing on a Playwright page."""
    import asyncio
    plan = generate_typing_plan(text, typo_rate=typo_rate, base_wpm=base_wpm)

    for ev in plan:
        action = ev["action"]
        delay = ev["delay"]

        if action == "key":
            ch = ev["char"]
            if ch == "\n":
                await page.keyboard.press("Enter")
            else:
                await page.keyboard.type(ch)
        elif action == "backspace":
            await page.keyboard.press("Backspace")

        if delay > 0:
            await asyncio.sleep(delay)


async def playwright_human_click(page, selector: str):
    """Clicks an element in Playwright using Gaussian coordinate jitter."""
    import asyncio
    el = await page.wait_for_selector(selector, timeout=8000)
    box = await el.bounding_box()
    if not box:
        await page.click(selector)
        return

    px, py = jitter_point_in_box(box["x"], box["y"], box["width"], box["height"])
    await page.mouse.move(px, py)
    await asyncio.sleep(random.uniform(0.10, 0.25))
    await page.mouse.down()
    await asyncio.sleep(random.uniform(0.065, 0.135))
    await page.mouse.up()


async def playwright_human_scroll(
    page,
    steps: int = 4,
    reading_pause_min: float = 2.0,
    reading_pause_max: float = 5.0
):
    """Scrolls down smoothly in Playwright with human pauses. ZERO auto-upvoting."""
    import asyncio
    for _ in range(steps):
        chunk = random.randint(180, 380)
        await page.evaluate(f"window.scrollBy({{top: {chunk}, behavior: 'smooth'}})")
        if random.random() < 0.25:
            await asyncio.sleep(random.uniform(0.8, 1.5))
            up_chunk = random.randint(40, 80)
            await page.evaluate(f"window.scrollBy({{top: -{up_chunk}, behavior: 'smooth'}})")
        await asyncio.sleep(random.uniform(reading_pause_min, reading_pause_max))

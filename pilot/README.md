# Pilot: Browser & Task Automation Suite

Dedicated automation tool for orchestrating:
1. **Real Chrome** via CSI daemon (port 10088)
2. **Anti-Detect Profiles** via ixBrowser (port 53400) + Playwright CDP
3. **Bridge & ngrok** (ports 8000 / 4040)

Does **not** touch or rewrite parent batch files (`Run_Bridge.bat`, `ptask.cmd`, `ptask.py`).

---

## Quick Start

### 1. Health Check
```cmd
pilot.bat status
```
Reports connectivity across CSI (Chrome), ixBrowser API, Bridge, and ngrok tunnel.

---

## Subcommands

### A. Chrome CSI Commands (`pilot.bat csi ...`)
Drive the user's active, logged-in Chrome browser via the CSI extension.

```cmd
# List open tabs in session
pilot.bat csi tabs

# Navigate to a URL in a new tab
pilot.bat csi nav https://www.reddit.com/r/webdev

# Snapshot current page accessibility DOM tree
pilot.bat csi snap

# Inspect subreddit details (subscribers, rules, description)
pilot.bat csi subreddit webdev

# Search Reddit posts live via Chrome (fast .json)
pilot.bat csi search "FastAPI" --limit 5

# Search Google live via Chrome (fast CDP, ~1.4s)
pilot.bat csi google "AI coding agents" --limit 5

# Harvest Reddit thread comments
pilot.bat csi comments "https://www.reddit.com/r/..." --limit 5

# Evaluate custom JavaScript in current tab (auto-unwraps large outputs)
pilot.bat csi eval document.title
```

---

### B. ixBrowser Multi-Account Commands (`pilot.bat ix ...`)
Drive isolated anti-detect profiles over individual residential ISP proxies.

```cmd
# List profiles (shows PID, username, group, proxy IP:Port)
pilot.bat ix list

# Filter profiles by group name
pilot.bat ix list --group "Javed"

# Open a profile window (and navigate to URL)
pilot.bat ix open 231 --url https://www.reddit.com

# Close a profile window
pilot.bat ix close 231

# Run an automated action in a profile (navigate, eval JS, screenshot)
pilot.bat ix run 231 --url https://www.reddit.com/r/Python --eval "document.title" --shot
```
Screenshots are saved inside `pilot/` as `shot_p<pid>_<timestamp>.jpg`.

---

### C. Tencent BrowserSkill Commands (`pilot.bat bsk ...`)
Drive the user's logged-in Chrome/Edge browser via Tencent's `BrowserSkill` (`bsk`) in isolated Agent Windows.
Runs on unreserved port `42800` (bypassing Windows Hyper-V NAT exclusions).

```cmd
# Check BrowserSkill daemon and connected browser status
pilot.bat bsk status

# List connected browsers
pilot.bat bsk browsers

# Navigate to a URL and return semantic VOM observation
pilot.bat bsk nav https://www.reddit.com/r/Python

# Take observation / accessibility snapshot in active session
pilot.bat bsk observe
pilot.bat bsk snapshot

# Capture viewport screenshot
pilot.bat bsk shot out.png

# Evaluate JavaScript expression inside Agent Window
pilot.bat bsk eval "document.title"
```

---

## File Overview

- [pilot.bat](file:///D:/Javed%20Hamza/Documents/ai_projects/muse/ngrok_prts/pilot/pilot.bat): Windows batch wrapper. Automatically invokes `C:\Python314\python.exe` (or system Python).
- [pilot.py](file:///D:/Javed%20Hamza/Documents/ai_projects/muse/ngrok_prts/pilot/pilot.py): Pure Python orchestrator with zero extra dependencies for core operations and native Playwright CDP support for ixBrowser tasks.

# Browser Bridge for Muse

A high-performance browser automation bridge and orchestration suite for **OpenMuse**, Claude Code, Cursor, and personal AI agents.

Provides two dedicated bridge engines:
1. **`browser_bridge_muse.py` / `Run_Browser_Bridge.bat` (Lightweight Core)**:
   - Dedicated exclusively for **OpenMuse** and AI agent browser sessions on port `8790`.
   - **Zero ixBrowser dependencies**: directly drives **Playwright Chromium**, **Real Chrome via Chrome CSI (port 10088)**, and **Tencent BrowserSkill (port 42800)**.
   - Clean REST endpoints: `/nav`, `/eval`, `/snapshot`, `/screenshot`, `/click`, `/type`, `/search`, `/tabs`.
2. **`ixbridge_all_in_one.py` / `Run_Bridge.bat` (Anti-Detect & Multi-Proxy Suite)**:
   - Orchestrates 70+ isolated ixBrowser profiles over residential proxies with ngrok tunneling.

---

## Architecture Overview

```
                      +---------------------------------------+
                      |         Local / Remote AI Agent        |
                      |   (OpenMuse, Claude Code, Hermes, etc)|
                      +-------------------+-------------------+
                                          |
                        HTTP / WebSocket / MCP (/mcp/sse)
                                          |
                                          v
+---------------------------------------------------------------------------------+
|                       Browser Bridge for Muse (:8790 / :8000)                   |
|                      (Optional ngrok Secure Public Tunnel)                      |
+--------------------+--------------------+-------------------+-------------------+
                     |                    |                   |
                     v                    v                   v
           +------------------+  +------------------+  +------------------+
           | Playwright Worker|  |    Chrome CSI    |  |  Tencent BSK     |
           |      (:8790)     |  |     (:10088)     |  |     (:42800)     |
           |   (Direct CDP)   |  |   (Direct Tab)   |  | (Agent Windows)  |
           +------------------+  +------------------+  +------------------+
                     |                    |                   |
                     v                    v                   v
                OpenMuse /             Logged-in User       Isolated VOM
             Chromium Browser            Browser            DOM Browser
```

---

## Key Features

1. **Dedicated Muse Browser Bridge (`browser_bridge_muse.py`)**:
   - Built specifically for OpenMuse agent goals on port `8790`.
   - Direct Playwright Chromium lifecycle control (`/browser/start`, `/browser/stop`).
   - Semantic accessibility snapshots (`/snapshot`) formatted for LLM consumption.
   - Zero-key live web searching via headless browser (`/search`).

2. **Unified ixBrowser & ngrok Bridge (`ixbridge_all_in_one.py`)**:
   - Single port (`:8000`) multiplexing ixBrowser local API, Playwright CDP tasks, and Chrome CSI daemon.
   - Built-in ngrok integration for immediate, password-protected remote tunneling.
   - Reusable warm CDP connections with event-based waits.
   - Integrated Model Context Protocol (MCP) server at `/mcp/sse`.

2. **Parallel Task Runner (`ptask.py` / `ptask.cmd`)**:
   - High-throughput parallel browser automation engine for ixBrowser profiles.
   - Idempotent profile attach: opens or connects to existing CDP sessions without destroying tabs.
   - Batch navigation, element interaction, JavaScript execution, and screenshot capture.

3. **Pilot Automation Suite (`pilot/`)**:
   - Multi-driver browser controller supporting Chrome CSI, ixBrowser profiles, and Tencent BrowserSkill.
   - Subreddit analyzer, live Reddit & Google search without API keys.
   - Anti-detection humanized typing and cursor simulator (`human.py`).
   - Account warmup and activity automation (`ix_warmup.py`).
   - Viral thread discovery and engagement monitor (`viral_finder.py`).

---

## Quick Start

### 1. Requirements
- Windows 10/11
- Python 3.10+ (Python 3.12+ recommended)
- [ixBrowser](https://www.ixbrowser.com/) installed and running on port 53400 (if using anti-detect features)
- Google Chrome with Chrome CSI extension (if using Chrome CSI features)

Install Python dependencies:
```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Launching the Bridge
Double-click `Run_Bridge.bat` or execute:
```bash
python ixbridge_all_in_one.py
```

The interactive menu lets you:
- **1**: Start bridge server + ngrok public tunnel
- **2**: Run system diagnostics & port connectivity checks
- **3**: View or rotate security API key
- **4**: Display connection endpoints and headers
- **5**: Toggle route modes (ixBrowser, Chrome CSI, Terminal execution)

### 3. Using the Pilot CLI
Navigate to `pilot/` or run `pilot\pilot.bat`:

```cmd
# Health check across all browser backends
pilot\pilot.bat status

# Chrome CSI operations
pilot\pilot.bat csi tabs
pilot\pilot.bat csi nav https://www.reddit.com/r/Python
pilot\pilot.bat csi search "FastAPI" --limit 5
pilot\pilot.bat csi google "AI Agent Workflows" --limit 5

# ixBrowser anti-detect operations
pilot\pilot.bat ix list
pilot\pilot.bat ix open 231 --url https://www.reddit.com
pilot\pilot.bat ix run 231 --url https://example.com --eval "document.title" --shot

# Tencent BrowserSkill operations
pilot\pilot.bat bsk status
pilot\pilot.bat bsk nav https://www.python.org
pilot\pilot.bat bsk observe
```

### 4. Parallel Task Execution (`ptask`)
Execute actions across multiple profiles simultaneously:
```cmd
# Open multiple profiles concurrently
ptask.cmd open 193,201,203 --url https://www.reddit.com --concurrency 4

# Close profiles
ptask.cmd close 193,201
```

---

## API Endpoints Reference

All requests must supply the API key via `X-Bridge-Key: <YOUR_KEY>` or query parameter `?key=<YOUR_KEY>`.

| Route | Method | Description |
|---|---|---|
| `/health` | `GET` | Service liveness probe |
| `/dashboard` | `GET` | Real-time web status dashboard |
| `/tools` | `GET` | Plain-language JSON catalog of endpoints |
| `/ix/...` | `POST` | Proxy to ixBrowser local API (profile creation, proxy setup, etc.) |
| `/ixc/profile-list` | `POST` | Compact profile list (ID, name, proxy IP, group) |
| `/task/run` | `POST` | Open profile, execute actions via CDP, and return results |
| `/task/run_stream` | `GET` | SSE stream for live step-by-step automation logs |
| `/task/open_many` | `POST` | Batch open multiple profiles in parallel and leave open |
| `/task/open_profiles` | `GET` | Active profiles currently attached by the bridge |
| `/csi` | `POST` | Interact with real user Chrome tabs via CSI daemon |
| `/exec` | `POST` | Execute local shell commands (if terminal mode enabled) |
| `/mcp/sse` | `GET/POST` | MCP endpoint for integration with Claude Code / Cursor / AI IDEs |

---

## Security Best Practices

- **API Keys**: Generated dynamically on first run in `.ixbridge_key` with strict file permissions. Never commit this file.
- **Terminal Execution**: The `/exec` route is disabled by default in `bridge_config.json`. Only enable it in trusted, authenticated environments.
- **Tunnel Safety**: The ngrok tunnel automatically requires API key verification and skips browser warnings.

---

## License

MIT License. See [LICENSE](LICENSE) for details.

# Desktop P&L Widget

Always-on-top floating window that shows live trading P&L from the AI Trader Bot.

![widget preview](https://img.shields.io/badge/Python-tkinter-blue)

## Setup

1. **Copy the environment file:**
   ```
   cp .env.example .env
   ```

2. **Edit `.env`** with your values:
   - `BOT_URL` — your dashboard URL + `/api/cmd` (default `http://localhost:8050/api/cmd`)
   - `DASHBOARD_CMD_TOKEN` — the same token from your docker-compose secrets
   - `REFRESH_INTERVAL` — polling interval in seconds (default 30)

3. **Launch:**
   - Double-click `start_widget.bat`, or
   - Run `python widget.py` from a terminal

## Features

- **Always on top** — floats above all windows
- **Draggable** — click and drag to reposition
- **Live P&L** — auto-refreshes every 30 seconds
- **Colour-coded** — green background when profitable, red when losing
- **Position list** — each open trade with pair, direction, and P&L
- **Close buttons** — close individual positions or all at once
- **Minimise** — click the dash (–) to collapse to a tiny pill showing just total P&L
- **Right-click menu** — Refresh, Close All, Settings, Exit
- **Bot status** — green dot when bot is online, red when offline

## Requirements

- Python 3.8+
- No extra packages needed (tkinter is built into Python)
- Bot must be running with the dashboard (port 8050) or command API (port 8060) accessible

## Keyboard

| Action | How |
|--------|-----|
| Move widget | Click and drag anywhere |
| Minimise/expand | Click the – button or click the pill |
| Context menu | Right-click anywhere |
| Close position | Click the X next to a pair |
| Close all | Click CLOSE ALL or right-click > Close All |

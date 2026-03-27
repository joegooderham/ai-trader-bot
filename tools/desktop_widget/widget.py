"""
AI Trader Bot — Desktop P&L Widget

Always-on-top floating window showing live trading P&L.
Uses the bot's command API (via dashboard proxy) to fetch positions
and send close commands. No extra dependencies — pure tkinter.

Usage:
    python widget.py
    # Or double-click start_widget.bat
"""

import json
import os
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


# ── Config ──────────────────────────────────────────────────────────────

def load_env():
    """Load .env file from the same directory as this script."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip("'\"")
        os.environ.setdefault(key.strip(), value)


load_env()

# Bot command API base URL — dashboard proxy is exposed on port 8050
BOT_URL = os.environ.get("BOT_URL", "http://localhost:8050/api/cmd")
# Auth token (same as DASHBOARD_CMD_TOKEN)
CMD_TOKEN = os.environ.get("DASHBOARD_CMD_TOKEN", "")
# Refresh interval in seconds
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL", "30"))

# ── Colours ─────────────────────────────────────────────────────────────

GREEN_BG = "#0d6b3b"
RED_BG = "#8b1a1a"
NEUTRAL_BG = "#2b2b2b"
TEXT_WHITE = "#ffffff"
TEXT_MUTED = "#aaaaaa"
TEXT_PROFIT = "#5cff5c"
TEXT_LOSS = "#ff5c5c"
PILL_BG = "#1e1e1e"
CLOSE_BTN_BG = "#cc3333"
CLOSE_ALL_BG = "#991111"
HEADER_BG = "#1a1a1a"

FONT_FAMILY = "Segoe UI"


# ── API helpers ─────────────────────────────────────────────────────────

def _headers():
    h = {"Content-Type": "application/json"}
    if CMD_TOKEN:
        h["Authorization"] = f"Bearer {CMD_TOKEN}"
    return h


def api_get(endpoint):
    """GET request to bot command API. Returns parsed JSON or None."""
    url = f"{BOT_URL}/{endpoint}"
    req = Request(url, headers=_headers(), method="GET")
    try:
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except (URLError, HTTPError, json.JSONDecodeError, OSError):
        return None


def api_post(endpoint, body=None):
    """POST request to bot command API. Returns parsed JSON or None."""
    url = f"{BOT_URL}/{endpoint}"
    data = json.dumps(body).encode() if body else b"{}"
    req = Request(url, data=data, headers=_headers(), method="POST")
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except (URLError, HTTPError, json.JSONDecodeError, OSError):
        return None


# ── Widget ──────────────────────────────────────────────────────────────

class TradingWidget:
    """Always-on-top floating P&L widget."""

    # Layout
    EXPANDED_W = 280
    EXPANDED_H = 200
    PILL_W = 120
    PILL_H = 40
    SCREEN_MARGIN = 20

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AI Trader P&L")
        self.root.overrideredirect(True)      # No title bar
        self.root.attributes("-topmost", True) # Always on top
        self.root.attributes("-alpha", 0.92)   # Slight transparency
        self.root.configure(bg=NEUTRAL_BG)

        # State
        self.minimised = False
        self.positions = []
        self.total_pl = 0.0
        self.bot_online = False
        self.last_updated = None
        self._drag_x = 0
        self._drag_y = 0
        self._refresh_job = None

        # Position in top-right corner
        screen_w = self.root.winfo_screenwidth()
        x = screen_w - self.EXPANDED_W - self.SCREEN_MARGIN
        y = self.SCREEN_MARGIN
        self.root.geometry(f"{self.EXPANDED_W}x{self.EXPANDED_H}+{x}+{y}")

        # Build UI
        self._build_expanded_view()
        self._build_pill_view()
        self._build_context_menu()

        # Dragging — bind on root so it works everywhere
        self.root.bind("<Button-1>", self._start_drag)
        self.root.bind("<B1-Motion>", self._on_drag)

        # Start data loop
        self._schedule_refresh()

    # ── UI construction ─────────────────────────────────────────────

    def _build_expanded_view(self):
        """Main expanded view with P&L, positions, and controls."""
        self.expanded_frame = tk.Frame(self.root, bg=NEUTRAL_BG)
        self.expanded_frame.pack(fill="both", expand=True)

        # ── Header row: status dot + title + minimise button ──
        header = tk.Frame(self.expanded_frame, bg=HEADER_BG, height=28)
        header.pack(fill="x")
        header.pack_propagate(False)

        self.status_dot = tk.Label(
            header, text="\u25cf", font=(FONT_FAMILY, 10),
            fg=TEXT_MUTED, bg=HEADER_BG
        )
        self.status_dot.pack(side="left", padx=(8, 4), pady=2)

        tk.Label(
            header, text="AI Trader", font=(FONT_FAMILY, 9, "bold"),
            fg=TEXT_WHITE, bg=HEADER_BG
        ).pack(side="left", pady=2)

        # Minimise button (underscore)
        min_btn = tk.Label(
            header, text="\u2013", font=(FONT_FAMILY, 12, "bold"),
            fg=TEXT_MUTED, bg=HEADER_BG, cursor="hand2"
        )
        min_btn.pack(side="right", padx=8, pady=2)
        min_btn.bind("<Button-1>", lambda e: self._toggle_minimise())

        # ── Total P&L ──
        self.pl_label = tk.Label(
            self.expanded_frame, text="£0.00",
            font=(FONT_FAMILY, 22, "bold"), fg=TEXT_WHITE, bg=NEUTRAL_BG
        )
        self.pl_label.pack(pady=(6, 2))

        # ── Positions list (scrollable frame) ──
        self.pos_frame = tk.Frame(self.expanded_frame, bg=NEUTRAL_BG)
        self.pos_frame.pack(fill="both", expand=True, padx=6)

        # ── Footer: timestamp + close all button ──
        footer = tk.Frame(self.expanded_frame, bg=NEUTRAL_BG)
        footer.pack(fill="x", padx=6, pady=(0, 6))

        self.time_label = tk.Label(
            footer, text="--:--:--", font=(FONT_FAMILY, 7),
            fg=TEXT_MUTED, bg=NEUTRAL_BG
        )
        self.time_label.pack(side="left")

        self.close_all_btn = tk.Label(
            footer, text=" CLOSE ALL ", font=(FONT_FAMILY, 7, "bold"),
            fg=TEXT_WHITE, bg=CLOSE_ALL_BG, cursor="hand2",
            relief="flat", padx=4, pady=1
        )
        self.close_all_btn.pack(side="right")
        self.close_all_btn.bind("<Button-1>", lambda e: self._close_all())

    def _build_pill_view(self):
        """Minimised pill showing just total P&L."""
        self.pill_frame = tk.Frame(self.root, bg=PILL_BG)
        # Not packed yet — shown when minimised

        self.pill_pl_label = tk.Label(
            self.pill_frame, text="£0.00",
            font=(FONT_FAMILY, 13, "bold"), fg=TEXT_WHITE, bg=PILL_BG
        )
        self.pill_pl_label.pack(expand=True)

        # Click pill to expand
        self.pill_frame.bind("<Button-1>", lambda e: self._toggle_minimise())
        self.pill_pl_label.bind("<Button-1>", lambda e: self._toggle_minimise())

    def _build_context_menu(self):
        """Right-click context menu."""
        self.ctx_menu = tk.Menu(self.root, tearoff=0, bg="#333", fg=TEXT_WHITE,
                                activebackground="#555", activeforeground=TEXT_WHITE,
                                font=(FONT_FAMILY, 9))
        self.ctx_menu.add_command(label="Refresh", command=self._do_refresh)
        self.ctx_menu.add_command(label="Close All Positions", command=self._close_all)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="Settings...", command=self._show_settings)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="Exit", command=self._exit)

        self.root.bind("<Button-3>", self._show_context_menu)

    # ── Dragging ────────────────────────────────────────────────────

    def _start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag(self, event):
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y() + event.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    # ── Minimise / expand ───────────────────────────────────────────

    def _toggle_minimise(self):
        if self.minimised:
            # Expand
            self.pill_frame.pack_forget()
            self.expanded_frame.pack(fill="both", expand=True)
            geo = self.root.geometry()
            pos = geo.split("+", 1)[1]  # keep current position
            self.root.geometry(f"{self.EXPANDED_W}x{self.EXPANDED_H}+{pos}")
            self.minimised = False
        else:
            # Minimise to pill
            self.expanded_frame.pack_forget()
            self.pill_frame.pack(fill="both", expand=True)
            geo = self.root.geometry()
            pos = geo.split("+", 1)[1]
            self.root.geometry(f"{self.PILL_W}x{self.PILL_H}+{pos}")
            self.minimised = True

    # ── Context menu ────────────────────────────────────────────────

    def _show_context_menu(self, event):
        try:
            self.ctx_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.ctx_menu.grab_release()

    def _show_settings(self):
        """Simple settings dialog."""
        win = tk.Toplevel(self.root)
        win.title("Widget Settings")
        win.geometry("300x180")
        win.attributes("-topmost", True)
        win.configure(bg="#2b2b2b")
        win.resizable(False, False)

        tk.Label(win, text="Bot URL:", fg=TEXT_WHITE, bg="#2b2b2b",
                 font=(FONT_FAMILY, 9)).pack(anchor="w", padx=10, pady=(10, 0))
        url_var = tk.StringVar(value=BOT_URL)
        tk.Entry(win, textvariable=url_var, width=35, font=(FONT_FAMILY, 9)).pack(padx=10, pady=2)

        tk.Label(win, text="Refresh interval (seconds):", fg=TEXT_WHITE, bg="#2b2b2b",
                 font=(FONT_FAMILY, 9)).pack(anchor="w", padx=10, pady=(8, 0))
        int_var = tk.StringVar(value=str(REFRESH_INTERVAL))
        tk.Entry(win, textvariable=int_var, width=10, font=(FONT_FAMILY, 9)).pack(anchor="w", padx=10, pady=2)

        def save():
            global BOT_URL, REFRESH_INTERVAL
            BOT_URL = url_var.get().rstrip("/")
            try:
                REFRESH_INTERVAL = max(5, int(int_var.get()))
            except ValueError:
                pass
            win.destroy()
            self._do_refresh()

        tk.Button(win, text="Save", command=save, font=(FONT_FAMILY, 9, "bold"),
                  bg="#444", fg=TEXT_WHITE, relief="flat", padx=12, pady=4).pack(pady=12)

    # ── Data refresh ────────────────────────────────────────────────

    def _schedule_refresh(self):
        """Schedule the next refresh on a background thread."""
        self._do_refresh()
        self._refresh_job = self.root.after(REFRESH_INTERVAL * 1000, self._schedule_refresh)

    def _do_refresh(self):
        """Fetch data in background thread, then update UI on main thread."""
        threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def _fetch_and_update(self):
        """Fetch positions and status from bot API."""
        pos_data = api_get("positions")
        status_data = api_get("status")

        if pos_data and "positions" in pos_data:
            positions = pos_data["positions"]
            total_pl = sum(p.get("unrealizedPL", 0) for p in positions)
            online = True
        else:
            positions = []
            total_pl = 0.0
            online = pos_data is not None  # API reachable but no positions

        bot_online = online and status_data is not None

        # Update UI on main thread
        self.root.after(0, self._update_ui, positions, total_pl, bot_online)

    def _update_ui(self, positions, total_pl, bot_online):
        """Update all UI elements with fresh data."""
        self.positions = positions
        self.total_pl = total_pl
        self.bot_online = bot_online
        self.last_updated = datetime.now()

        # Background colour based on P&L
        if not positions:
            bg = NEUTRAL_BG
        elif total_pl >= 0:
            bg = GREEN_BG
        else:
            bg = RED_BG

        self.expanded_frame.configure(bg=bg)
        self.pl_label.configure(bg=bg)
        self.pos_frame.configure(bg=bg)

        # Update footer bg
        for child in self.expanded_frame.winfo_children():
            if isinstance(child, tk.Frame) and child not in (self.pos_frame,):
                # Skip header (HEADER_BG) and pos_frame
                pass
            if child == self.pos_frame:
                child.configure(bg=bg)

        # Footer frame is the last frame child
        footer = self.expanded_frame.winfo_children()[-1]
        if isinstance(footer, tk.Frame):
            footer.configure(bg=bg)
            self.time_label.configure(bg=bg)

        # Total P&L text
        sign = "+" if total_pl >= 0 else ""
        pl_text = f"\u00a3{sign}{total_pl:.2f}"
        pl_colour = TEXT_PROFIT if total_pl >= 0 else TEXT_LOSS
        self.pl_label.configure(text=pl_text, fg=pl_colour)

        # Pill label
        self.pill_pl_label.configure(text=pl_text, fg=pl_colour)
        pill_bg = GREEN_BG if total_pl >= 0 else RED_BG if positions else PILL_BG
        self.pill_frame.configure(bg=pill_bg)
        self.pill_pl_label.configure(bg=pill_bg)

        # Status dot
        self.status_dot.configure(fg="#00ff00" if bot_online else "#ff3333")

        # Timestamp
        if self.last_updated:
            self.time_label.configure(text=self.last_updated.strftime("%H:%M:%S"))

        # Rebuild positions list
        for widget in self.pos_frame.winfo_children():
            widget.destroy()

        if not positions:
            tk.Label(
                self.pos_frame, text="No open positions",
                font=(FONT_FAMILY, 8), fg=TEXT_MUTED, bg=bg
            ).pack(pady=2)
        else:
            for pos in positions:
                self._add_position_row(pos, bg)

        # Dynamically resize height to fit content
        # Header(28) + PL(44) + positions(18 each) + footer(24) + padding(20)
        num_rows = max(1, len(positions))
        needed_h = 28 + 44 + (num_rows * 18) + 24 + 20
        needed_h = max(self.EXPANDED_H, min(needed_h, 400))
        if not self.minimised:
            geo = self.root.geometry()
            pos_str = geo.split("+", 1)[1]
            self.root.geometry(f"{self.EXPANDED_W}x{needed_h}+{pos_str}")

    def _add_position_row(self, pos, bg):
        """Add a single position row with pair, P&L, and close button."""
        row = tk.Frame(self.pos_frame, bg=bg)
        row.pack(fill="x", pady=1)

        pair = pos.get("pair", pos.get("instrument", "???"))
        direction = pos.get("direction", "?")
        upl = pos.get("unrealizedPL", 0)
        deal_id = pos.get("dealId", "")

        # Direction arrow
        arrow = "\u25b2" if direction == "BUY" else "\u25bc"
        arrow_colour = TEXT_PROFIT if direction == "BUY" else TEXT_LOSS

        tk.Label(
            row, text=arrow, font=(FONT_FAMILY, 8),
            fg=arrow_colour, bg=bg, width=2
        ).pack(side="left")

        # Pair name (compact: EUR/USD not EUR_USD)
        display_pair = pair.replace("_", "/")
        tk.Label(
            row, text=display_pair, font=(FONT_FAMILY, 8),
            fg=TEXT_WHITE, bg=bg, anchor="w", width=8
        ).pack(side="left")

        # P&L
        pl_sign = "+" if upl >= 0 else ""
        pl_colour = TEXT_PROFIT if upl >= 0 else TEXT_LOSS
        tk.Label(
            row, text=f"\u00a3{pl_sign}{upl:.2f}",
            font=(FONT_FAMILY, 8, "bold"), fg=pl_colour, bg=bg, width=8
        ).pack(side="left", padx=(4, 0))

        # Close button
        close_btn = tk.Label(
            row, text=" X ", font=(FONT_FAMILY, 7, "bold"),
            fg=TEXT_WHITE, bg=CLOSE_BTN_BG, cursor="hand2", relief="flat"
        )
        close_btn.pack(side="right", padx=(4, 0))
        close_btn.bind("<Button-1>", lambda e, d=deal_id: self._close_position(d))

    # ── Trading actions ─────────────────────────────────────────────

    def _close_all(self):
        """Close all open positions via API."""
        if not self.positions:
            return

        # Visual feedback
        self.close_all_btn.configure(text=" CLOSING... ", bg="#666")
        self.root.update_idletasks()

        def do_close():
            result = api_post("close-all")
            self.root.after(0, self._on_close_all_done, result)

        threading.Thread(target=do_close, daemon=True).start()

    def _on_close_all_done(self, result):
        self.close_all_btn.configure(text=" CLOSE ALL ", bg=CLOSE_ALL_BG)
        # Refresh immediately to show updated state
        self._do_refresh()

    def _close_position(self, deal_id):
        """Close a single position by deal ID."""
        if not deal_id:
            return

        def do_close():
            api_post(f"close/{deal_id}")
            self.root.after(500, self._do_refresh)

        threading.Thread(target=do_close, daemon=True).start()

    # ── Lifecycle ───────────────────────────────────────────────────

    def _exit(self):
        if self._refresh_job:
            self.root.after_cancel(self._refresh_job)
        self.root.destroy()
        sys.exit(0)

    def run(self):
        self.root.mainloop()


# ── Entry point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    widget = TradingWidget()
    widget.run()

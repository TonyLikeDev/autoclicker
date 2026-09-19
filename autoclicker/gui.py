"""Tkinter front end.

Threading rule: the engine and the hotkey dispatcher both run off the Tk
thread, so they never touch widgets directly -- they push onto ``_ui_queue``
and ``_pump`` drains it on the Tk thread every 50 ms.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import config as cfg
from . import winapi
from .config import ClickPoint, Hotkey, Settings
from .engine import ClickEngine
from .hotkeys import HotkeyManager
from .tray import TrayIcon, make_icon_file

APP_TITLE = "AutoClicker"
VERSION = "1.0.0"

THEMES = {
    "dark": {
        "bg": "#1e1f22", "panel": "#26282c", "field": "#303339", "fg": "#e8eaed",
        "muted": "#9aa0a6", "accent": "#4f8cff", "ok": "#3ba55c", "warn": "#f0b232",
        "err": "#ed4245", "border": "#3a3d43", "sel": "#2f4f8f",
    },
    "light": {
        "bg": "#f2f3f5", "panel": "#ffffff", "field": "#ffffff", "fg": "#1c1e21",
        "muted": "#6b7075", "accent": "#0a66ff", "ok": "#1a7f37", "warn": "#9a6700",
        "err": "#cf222e", "border": "#d4d6da", "sel": "#cfe0ff",
    },
}

BACKGROUND_HELP = (
    "Background mode posts WM_*BUTTONDOWN/UP messages directly to the window, so "
    "the window can be minimised or behind others and your real cursor is never "
    "moved. It works with most ordinary Win32 applications. It does not work with "
    "software that reads raw input or DirectInput - most full-screen games fall "
    "into that group - and it cannot reach windows owned by an elevated process "
    "unless this app is elevated too."
)


def _fmt_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, rem = divmod(int(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    tenths = int((seconds - int(seconds)) * 10)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, secs)
    return "%02d:%02d.%d" % (minutes, secs, tenths)


class AutoClickerApp:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or cfg.load_settings()
        self.settings.validate()

        self.root = tk.Tk()
        self.root.title("%s %s" % (APP_TITLE, VERSION))
        self.root.minsize(700, 620)
        self.root.geometry("760x680")

        self._ui_queue: queue.Queue = queue.Queue()
        self._vars: dict = {}
        self._syncing = False
        self._pick_mode = ""
        self._hidden = False
        self._closing = False
        self._window_list: list = []
        self._canvases: list = []

        self.engine = ClickEngine(self.settings, on_event=self._engine_event)
        self.hotkeys = HotkeyManager(on_error=lambda m: self._post("log", m))

        self._icon_dir = cfg.config_dir() / "icons"
        self._icon_idle = self._icon_dir / "idle.ico"
        self._icon_run = self._icon_dir / "running.ico"
        self._ensure_icons()

        self.tray: TrayIcon | None = None

        self._build_vars()
        self._build_ui()
        self._apply_theme()
        self._pull()
        self._rebind_hotkeys()
        self.hotkeys.start()
        self._apply_runtime_options()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Unmap>", self._on_unmap)
        self.root.after(50, self._pump)

        if self.settings.minimize_to_tray:
            self._start_tray()
        if self.settings.start_minimized:
            self.root.after(200, self._hide_window)

        self.log("%s %s ready." % (APP_TITLE, VERSION))
        self.log("Toggle: %s   Stop: %s   Panic: %s"
                 % (self.settings.hk_toggle.label(), self.settings.hk_stop.label(),
                    self.settings.hk_panic.label()))
        if not winapi.is_elevated():
            self.log("Note: not running as administrator - clicks will not reach "
                     "windows owned by elevated processes.")

    # ==================================================================
    # Icons
    # ==================================================================
    def _ensure_icons(self) -> None:
        try:
            if not self._icon_idle.exists():
                make_icon_file(self._icon_idle, (110, 118, 129))
            if not self._icon_run.exists():
                make_icon_file(self._icon_run, (59, 165, 92))
            self.root.iconbitmap(default=str(self._icon_idle))
        except Exception:
            pass

    # ==================================================================
    # Variables
    # ==================================================================
    def _var(self, name: str, kind, default=None):
        var = kind(master=self.root)
        if default is not None:
            var.set(default)
        self._vars[name] = var
        return var

    def _build_vars(self) -> None:
        s = self.settings
        v = self._var
        for name in ("interval_hours", "interval_minutes", "interval_seconds",
                     "interval_millis", "interval_micros", "clicks_per_event",
                     "jitter_radius", "repeat_count", "duration_hours",
                     "duration_minutes", "duration_seconds", "burst_size",
                     "move_steps", "fixed_x", "fixed_y", "scroll_amount"):
            v(name, tk.IntVar, getattr(s, name))
        for name in ("multi_click_gap_ms", "hold_duration_ms", "random_amount",
                     "random_hold_ms", "start_delay_ms", "burst_pause_ms",
                     "move_duration_ms"):
            v(name, tk.DoubleVar, getattr(s, name))
        for name in ("action_type", "mouse_button", "random_unit",
                     "random_distribution", "position_mode", "sequence_order",
                     "repeat_mode", "hotkey_mode", "process_priority",
                     "move_style", "target_mode", "target_title", "key_text",
                     "theme"):
            v(name, tk.StringVar, getattr(s, name))
        for name in ("randomize_interval", "randomize_hold", "jitter_enabled",
                     "restore_cursor", "burst_enabled", "suppress_hotkeys",
                     "suppress_mouse_hotkeys", "target_match_by_title",
                     "background_client_coords", "background_target_child",
                     "always_on_top",
                     "minimize_to_tray", "start_minimized", "close_to_tray",
                     "sound_feedback", "high_precision", "timer_resolution",
                     "prevent_sleep", "confirm_exit_while_running",
                     "key_use_scancode", "scroll_horizontal", "autosave"):
            v(name, tk.BooleanVar, getattr(s, name))

        v("cps_text", tk.StringVar, "10")
        v("key_choice", tk.StringVar, winapi.key_name(s.key_code))
        v("status", tk.StringVar, "Idle")
        v("stat_clicks", tk.StringVar, "0")
        v("stat_events", tk.StringVar, "0")
        v("stat_elapsed", tk.StringVar, "00:00.0")
        v("stat_cps", tk.StringVar, "0.0")
        v("stat_skipped", tk.StringVar, "0")
        v("cursor_pos", tk.StringVar, "0, 0")
        v("window_choice", tk.StringVar, "")
        v("profile_name", tk.StringVar, "")

        for name in ("interval_hours", "interval_minutes", "interval_seconds",
                     "interval_millis", "interval_micros"):
            self._vars[name].trace_add("write", self._on_interval_changed)
        self._vars["always_on_top"].trace_add("write", lambda *_: self._apply_topmost())
        self._vars["theme"].trace_add("write", lambda *_: self._apply_theme())
        self._vars["position_mode"].trace_add("write", lambda *_: self._sync_enabled())
        self._vars["action_type"].trace_add("write", lambda *_: self._sync_enabled())
        self._vars["repeat_mode"].trace_add("write", lambda *_: self._sync_enabled())
        self._vars["target_mode"].trace_add("write", lambda *_: self._sync_enabled())
        self._vars["randomize_interval"].trace_add("write", lambda *_: self._sync_enabled())
        self._vars["jitter_enabled"].trace_add("write", lambda *_: self._sync_enabled())
        self._vars["burst_enabled"].trace_add("write", lambda *_: self._sync_enabled())
        self._vars["move_style"].trace_add("write", lambda *_: self._sync_enabled())

    # ==================================================================
    # Layout
    # ==================================================================
    def _build_ui(self) -> None:
        root = self.root
        outer = ttk.Frame(root, style="App.TFrame", padding=10)
        outer.pack(fill="both", expand=True)

        self._build_header(outer)

        self.nb = ttk.Notebook(outer)
        self.nb.pack(fill="both", expand=True, pady=(10, 8))
        self._build_click_tab()
        self._build_position_tab()
        self._build_repeat_tab()
        self._build_hotkeys_tab()
        self._build_target_tab()
        self._build_options_tab()
        self._build_profiles_tab()
        self._build_log_tab()

        self._build_footer(outer)

    def _build_header(self, parent) -> None:
        bar = ttk.Frame(parent, style="Card.TFrame", padding=(12, 10))
        bar.pack(fill="x")

        self.status_dot = tk.Canvas(bar, width=14, height=14, highlightthickness=0, bd=0)
        self.status_dot.pack(side="left", padx=(0, 8))
        self._dot = self.status_dot.create_oval(2, 2, 12, 12, fill="#9aa0a6", outline="")

        ttk.Label(bar, textvariable=self._vars["status"],
                  style="Status.TLabel").pack(side="left")

        stats = ttk.Frame(bar, style="Card.TFrame")
        stats.pack(side="right")
        for label, key in (("CPS", "stat_cps"), ("Elapsed", "stat_elapsed"),
                           ("Clicks", "stat_clicks")):
            cell = ttk.Frame(stats, style="Card.TFrame")
            cell.pack(side="left", padx=10)
            ttk.Label(cell, text=label, style="Muted.TLabel").pack()
            ttk.Label(cell, textvariable=self._vars[key], style="Metric.TLabel").pack()

    def _build_footer(self, parent) -> None:
        bar = ttk.Frame(parent, style="Card.TFrame", padding=(12, 10))
        bar.pack(fill="x")

        self.btn_start = ttk.Button(bar, text="Start", style="Start.TButton",
                                    command=self.start, width=14)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(bar, text="Stop", style="Stop.TButton",
                                   command=self.stop, width=14, state="disabled")
        self.btn_stop.pack(side="left", padx=6)

        self.hint = ttk.Label(bar, text="", style="Muted.TLabel")
        self.hint.pack(side="right")

    # ---------------------------------------------------------- click tab
    def _build_click_tab(self) -> None:
        tab = self._tab("Click")

        box = self._group(tab, "Click interval")
        row = ttk.Frame(box, style="Card.TFrame")
        row.grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 6))
        for label, name, to in (("Hours", "interval_hours", 999),
                                ("Minutes", "interval_minutes", 59),
                                ("Seconds", "interval_seconds", 59),
                                ("Millis", "interval_millis", 999),
                                ("Micros", "interval_micros", 999)):
            cell = ttk.Frame(row, style="Card.TFrame")
            cell.pack(side="left", padx=(0, 10))
            ttk.Label(cell, text=label, style="Muted.TLabel").pack(anchor="w")
            ttk.Spinbox(cell, from_=0, to=to, width=6,
                        textvariable=self._vars[name]).pack()

        cps_row = ttk.Frame(box, style="Card.TFrame")
        cps_row.grid(row=1, column=0, columnspan=6, sticky="w", pady=(4, 2))
        ttk.Label(cps_row, text="Clicks per second").pack(side="left")
        entry = ttk.Entry(cps_row, textvariable=self._vars["cps_text"], width=10)
        entry.pack(side="left", padx=6)
        entry.bind("<Return>", lambda _e: self._apply_cps())
        ttk.Button(cps_row, text="Apply", width=7,
                   command=self._apply_cps).pack(side="left")
        ttk.Label(cps_row, text="Presets:", style="Muted.TLabel").pack(side="left", padx=(16, 4))
        for cps in (1, 5, 10, 25, 50, 100):
            ttk.Button(cps_row, text=str(cps), width=4,
                       command=lambda c=cps: self._set_cps(c)).pack(side="left", padx=1)
        ttk.Button(cps_row, text="Max", width=5,
                   command=lambda: self._set_interval_total(0.0)).pack(side="left", padx=(4, 0))

        box = self._group(tab, "Action")
        ttk.Label(box, text="Type").grid(row=0, column=0, sticky="w", pady=3)
        combo = ttk.Combobox(box, state="readonly", width=22,
                             values=[cfg.ACTION_LABELS[a] for a in cfg.ACTION_TYPES])
        combo.grid(row=0, column=1, sticky="w", padx=6)
        combo.bind("<<ComboboxSelected>>",
                   lambda _e: self._vars["action_type"].set(
                       cfg.ACTION_TYPES[combo.current()]))
        self.action_combo = combo

        ttk.Label(box, text="Mouse button").grid(row=1, column=0, sticky="w", pady=3)
        self.button_combo = ttk.Combobox(
            box, state="readonly", width=22,
            values=[cfg.BUTTON_LABELS[b] for b in cfg.MOUSE_BUTTONS])
        self.button_combo.grid(row=1, column=1, sticky="w", padx=6)
        self.button_combo.bind("<<ComboboxSelected>>",
                               lambda _e: self._vars["mouse_button"].set(
                                   cfg.MOUSE_BUTTONS[self.button_combo.current()]))

        ttk.Label(box, text="Clicks per event").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Spinbox(box, from_=1, to=10, width=6,
                    textvariable=self._vars["clicks_per_event"]).grid(row=2, column=1, sticky="w", padx=6)
        ttk.Label(box, text="1 = single, 2 = double, 3 = triple",
                  style="Muted.TLabel").grid(row=2, column=2, sticky="w")

        ttk.Label(box, text="Gap between them (ms)").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Spinbox(box, from_=0, to=5000, increment=5, width=8,
                    textvariable=self._vars["multi_click_gap_ms"]).grid(row=3, column=1, sticky="w", padx=6)

        ttk.Label(box, text="Hold duration (ms)").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Spinbox(box, from_=0, to=60000, increment=5, width=8,
                    textvariable=self._vars["hold_duration_ms"]).grid(row=4, column=1, sticky="w", padx=6)
        ttk.Label(box, text="how long the button stays pressed",
                  style="Muted.TLabel").grid(row=4, column=2, sticky="w")

        self.key_row = ttk.Frame(box, style="Card.TFrame")
        self.key_row.grid(row=5, column=0, columnspan=3, sticky="w", pady=3)
        ttk.Label(self.key_row, text="Key").pack(side="left")
        self.key_combo = ttk.Combobox(self.key_row, state="readonly", width=14,
                                      textvariable=self._vars["key_choice"],
                                      values=sorted(winapi.VK_NAMES.keys()))
        self.key_combo.pack(side="left", padx=6)
        ttk.Button(self.key_row, text="Capture key", width=12,
                   command=self._capture_action_key).pack(side="left")
        ttk.Checkbutton(self.key_row, text="Send scan codes (better for games)",
                        variable=self._vars["key_use_scancode"]).pack(side="left", padx=10)

        self.text_row = ttk.Frame(box, style="Card.TFrame")
        self.text_row.grid(row=6, column=0, columnspan=3, sticky="w", pady=3)
        ttk.Label(self.text_row, text="Text").pack(side="left")
        ttk.Entry(self.text_row, textvariable=self._vars["key_text"],
                  width=40).pack(side="left", padx=6)

        self.scroll_row = ttk.Frame(box, style="Card.TFrame")
        self.scroll_row.grid(row=7, column=0, columnspan=3, sticky="w", pady=3)
        ttk.Label(self.scroll_row, text="Scroll notches").pack(side="left")
        ttk.Spinbox(self.scroll_row, from_=-100, to=100, width=6,
                    textvariable=self._vars["scroll_amount"]).pack(side="left", padx=6)
        ttk.Checkbutton(self.scroll_row, text="Horizontal",
                        variable=self._vars["scroll_horizontal"]).pack(side="left")

        box = self._group(tab, "Randomization")
        ttk.Checkbutton(box, text="Randomize the interval",
                        variable=self._vars["randomize_interval"]).grid(
                            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(box, text="Variation +/-").grid(row=1, column=0, sticky="w", pady=3)
        self.rand_amount = ttk.Spinbox(box, from_=0, to=100000, increment=5, width=8,
                                       textvariable=self._vars["random_amount"])
        self.rand_amount.grid(row=1, column=1, sticky="w", padx=6)
        self.rand_unit = ttk.Combobox(box, state="readonly", width=13,
                                      values=["milliseconds", "percent"])
        self.rand_unit.grid(row=1, column=2, sticky="w")
        self.rand_unit.bind("<<ComboboxSelected>>",
                            lambda _e: self._vars["random_unit"].set(
                                cfg.RANDOM_UNITS[self.rand_unit.current()]))
        ttk.Label(box, text="Distribution").grid(row=2, column=0, sticky="w", pady=3)
        self.rand_dist = ttk.Combobox(box, state="readonly", width=10,
                                      values=["uniform", "gaussian"])
        self.rand_dist.grid(row=2, column=1, sticky="w", padx=6)
        self.rand_dist.bind("<<ComboboxSelected>>",
                            lambda _e: self._vars["random_distribution"].set(
                                cfg.DISTRIBUTIONS[self.rand_dist.current()]))
        ttk.Label(box, text="gaussian clusters near the target, uniform is flat",
                  style="Muted.TLabel").grid(row=2, column=2, sticky="w")

        ttk.Checkbutton(box, text="Randomize hold duration too",
                        variable=self._vars["randomize_hold"]).grid(
                            row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.rand_hold = ttk.Spinbox(box, from_=0, to=10000, increment=1, width=8,
                                     textvariable=self._vars["random_hold_ms"])
        self.rand_hold.grid(row=3, column=2, sticky="w")

    # ------------------------------------------------------- position tab
    def _build_position_tab(self) -> None:
        tab = self._tab("Position")

        box = self._group(tab, "Where to click")
        for i, mode in enumerate(cfg.POSITION_MODES):
            ttk.Radiobutton(box, text=cfg.POSITION_LABELS[mode], value=mode,
                            variable=self._vars["position_mode"]).grid(
                                row=i, column=0, sticky="w", pady=2)

        fixed = ttk.Frame(box, style="Card.TFrame")
        fixed.grid(row=1, column=1, sticky="w", padx=(20, 0))
        ttk.Label(fixed, text="X").pack(side="left")
        self.fx = ttk.Spinbox(fixed, from_=-32000, to=32000, width=7,
                              textvariable=self._vars["fixed_x"])
        self.fx.pack(side="left", padx=(2, 8))
        ttk.Label(fixed, text="Y").pack(side="left")
        self.fy = ttk.Spinbox(fixed, from_=-32000, to=32000, width=7,
                              textvariable=self._vars["fixed_y"])
        self.fy.pack(side="left", padx=2)
        ttk.Button(fixed, text="Use cursor", width=11,
                   command=self._use_cursor_pos).pack(side="left", padx=6)
        self.btn_pick = ttk.Button(fixed, text="Pick...", width=8,
                                   command=self._arm_pick_position)
        self.btn_pick.pack(side="left")
        ttk.Label(box, textvariable=self._vars["cursor_pos"],
                  style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(20, 0))

        box = self._group(tab, "Point sequence")
        cols = ("n", "x", "y", "button", "delay", "label")
        self.tree = ttk.Treeview(box, columns=cols, show="headings", height=7,
                                 selectmode="extended")
        for col, text, width in (("n", "#", 36), ("x", "X", 70), ("y", "Y", 70),
                                 ("button", "Button", 90), ("delay", "Delay", 80),
                                 ("label", "Label", 200)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w" if col == "label" else "center")
        self.tree.grid(row=0, column=0, columnspan=4, sticky="nsew")
        self.tree.bind("<Double-1>", lambda _e: self._edit_point())
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=4, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        box.columnconfigure(3, weight=1)

        buttons = ttk.Frame(box, style="Card.TFrame")
        buttons.grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))
        for text, command in (("Add at cursor", self._add_point_cursor),
                              ("Add...", self._edit_point_new),
                              ("Edit", self._edit_point),
                              ("Remove", self._remove_points),
                              ("Up", lambda: self._move_point(-1)),
                              ("Down", lambda: self._move_point(1)),
                              ("Clear", self._clear_points)):
            ttk.Button(buttons, text=text, width=13 if " " in text else 8,
                       command=command).pack(side="left", padx=2)

        order = ttk.Frame(box, style="Card.TFrame")
        order.grid(row=2, column=0, columnspan=5, sticky="w", pady=(6, 0))
        ttk.Label(order, text="Order").pack(side="left")
        self.order_combo = ttk.Combobox(order, state="readonly", width=12,
                                        values=["cycle", "once", "random", "pingpong"])
        self.order_combo.pack(side="left", padx=6)
        self.order_combo.bind("<<ComboboxSelected>>",
                              lambda _e: self._vars["sequence_order"].set(
                                  cfg.SEQUENCE_ORDERS[self.order_combo.current()]))
        ttk.Label(order, text="a point's own delay overrides the global interval",
                  style="Muted.TLabel").pack(side="left", padx=10)

        box = self._group(tab, "Cursor behaviour")
        ttk.Checkbutton(box, text="Add random jitter around the target",
                        variable=self._vars["jitter_enabled"]).grid(row=0, column=0, sticky="w")
        ttk.Label(box, text="Radius (px)").grid(row=0, column=1, sticky="e", padx=(20, 4))
        self.jitter_spin = ttk.Spinbox(box, from_=0, to=2000, width=6,
                                       textvariable=self._vars["jitter_radius"])
        self.jitter_spin.grid(row=0, column=2, sticky="w")

        ttk.Checkbutton(box, text="Return the cursor to where it was after each click",
                        variable=self._vars["restore_cursor"]).grid(
                            row=1, column=0, columnspan=3, sticky="w", pady=2)

        ttk.Label(box, text="Movement").grid(row=2, column=0, sticky="w", pady=2)
        self.move_combo = ttk.Combobox(box, state="readonly", width=12,
                                       values=["instant", "linear", "smooth"])
        self.move_combo.grid(row=2, column=1, sticky="w")
        self.move_combo.bind("<<ComboboxSelected>>",
                             lambda _e: self._vars["move_style"].set(
                                 cfg.MOVE_STYLES[self.move_combo.current()]))
        ttk.Label(box, text="Travel (ms)").grid(row=3, column=0, sticky="w", pady=2)
        self.move_dur = ttk.Spinbox(box, from_=0, to=10000, increment=10, width=8,
                                    textvariable=self._vars["move_duration_ms"])
        self.move_dur.grid(row=3, column=1, sticky="w")
        ttk.Label(box, text="Steps").grid(row=3, column=2, sticky="e", padx=(10, 4))
        self.move_steps = ttk.Spinbox(box, from_=2, to=500, width=6,
                                      textvariable=self._vars["move_steps"])
        self.move_steps.grid(row=3, column=3, sticky="w")

    # --------------------------------------------------------- repeat tab
    def _build_repeat_tab(self) -> None:
        tab = self._tab("Repeat")

        box = self._group(tab, "How long to run")
        ttk.Radiobutton(box, text="Repeat until stopped", value="infinite",
                        variable=self._vars["repeat_mode"]).grid(row=0, column=0, sticky="w", pady=2)
        ttk.Radiobutton(box, text="Repeat a fixed number of times", value="count",
                        variable=self._vars["repeat_mode"]).grid(row=1, column=0, sticky="w", pady=2)
        self.count_spin = ttk.Spinbox(box, from_=1, to=100000000, width=10,
                                      textvariable=self._vars["repeat_count"])
        self.count_spin.grid(row=1, column=1, sticky="w", padx=10)

        ttk.Radiobutton(box, text="Repeat for a set time", value="duration",
                        variable=self._vars["repeat_mode"]).grid(row=2, column=0, sticky="w", pady=2)
        dur = ttk.Frame(box, style="Card.TFrame")
        dur.grid(row=2, column=1, sticky="w", padx=10)
        self.dur_spins = []
        for label, name, to in (("h", "duration_hours", 999),
                                ("m", "duration_minutes", 59),
                                ("s", "duration_seconds", 59)):
            spin = ttk.Spinbox(dur, from_=0, to=to, width=5, textvariable=self._vars[name])
            spin.pack(side="left")
            ttk.Label(dur, text=label, style="Muted.TLabel").pack(side="left", padx=(2, 8))
            self.dur_spins.append(spin)

        box = self._group(tab, "Start delay")
        ttk.Label(box, text="Wait before the first click (ms)").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(box, from_=0, to=600000, increment=100, width=10,
                    textvariable=self._vars["start_delay_ms"]).grid(row=0, column=1, sticky="w", padx=10)
        ttk.Label(box, text="gives you time to move the mouse into place",
                  style="Muted.TLabel").grid(row=0, column=2, sticky="w")

        box = self._group(tab, "Burst mode")
        ttk.Checkbutton(box, text="Click in bursts, then pause",
                        variable=self._vars["burst_enabled"]).grid(
                            row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(box, text="Clicks per burst").grid(row=1, column=0, sticky="w", pady=3)
        self.burst_size = ttk.Spinbox(box, from_=1, to=100000, width=8,
                                      textvariable=self._vars["burst_size"])
        self.burst_size.grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(box, text="Pause after burst (ms)").grid(row=2, column=0, sticky="w", pady=3)
        self.burst_pause = ttk.Spinbox(box, from_=0, to=3600000, increment=100, width=8,
                                       textvariable=self._vars["burst_pause_ms"])
        self.burst_pause.grid(row=2, column=1, sticky="w", padx=6)

    # -------------------------------------------------------- hotkeys tab
    def _build_hotkeys_tab(self) -> None:
        tab = self._tab("Hotkeys")

        box = self._group(tab, "Trigger mode")
        ttk.Radiobutton(box, text="Toggle - press once to start, again to stop",
                        value="toggle", variable=self._vars["hotkey_mode"],
                        command=self._rebind_hotkeys).grid(row=0, column=0, sticky="w", pady=2)
        ttk.Radiobutton(box, text="Hold - click only while the key is held down",
                        value="hold", variable=self._vars["hotkey_mode"],
                        command=self._rebind_hotkeys).grid(row=1, column=0, sticky="w", pady=2)

        box = self._group(tab, "Bindings")
        self.hotkey_labels = {}
        rows = (
            ("hk_toggle", "Start / stop toggle"),
            ("hk_start", "Start only"),
            ("hk_stop", "Stop only"),
            ("hk_panic", "Panic stop (always active)"),
            ("hk_pick", "Capture cursor position / window"),
        )
        for i, (name, text) in enumerate(rows):
            ttk.Label(box, text=text).grid(row=i, column=0, sticky="w", pady=3)
            var = tk.StringVar(value=getattr(self.settings, name).label())
            self._vars["lbl_" + name] = var
            ttk.Label(box, textvariable=var, style="Key.TLabel", width=22,
                      anchor="center").grid(row=i, column=1, padx=8)
            ttk.Button(box, text="Set", width=6,
                       command=lambda n=name: self._capture_hotkey(n)).grid(row=i, column=2, padx=2)
            ttk.Button(box, text="Clear", width=7,
                       command=lambda n=name: self._clear_hotkey(n)).grid(row=i, column=3, padx=2)

        ttk.Label(box, text="Any key or mouse button works, modifiers included. "
                            "Mouse buttons are captured too - bind X1/X2 if you like.",
                  style="Muted.TLabel", wraplength=560).grid(
                      row=len(rows), column=0, columnspan=4, sticky="w", pady=(8, 0))

        box = self._group(tab, "Suppression")
        ttk.Checkbutton(box, text="Swallow keyboard hotkeys so other apps do not see them",
                        variable=self._vars["suppress_hotkeys"],
                        command=self._rebind_hotkeys).grid(row=0, column=0, sticky="w", pady=2)
        ttk.Checkbutton(box, text="Swallow mouse-button hotkeys (recommended)",
                        variable=self._vars["suppress_mouse_hotkeys"],
                        command=self._rebind_hotkeys).grid(row=1, column=0, sticky="w", pady=2)

    # --------------------------------------------------------- target tab
    def _build_target_tab(self) -> None:
        tab = self._tab("Target")

        box = self._group(tab, "Delivery")
        ttk.Radiobutton(box, text="Normal - inject real input wherever the cursor is",
                        value="global", variable=self._vars["target_mode"]).grid(
                            row=0, column=0, sticky="w", pady=2)
        ttk.Radiobutton(box, text="Only while the target window is in the foreground",
                        value="active_only", variable=self._vars["target_mode"]).grid(
                            row=1, column=0, sticky="w", pady=2)
        ttk.Radiobutton(box, text="Background - post messages straight to the window",
                        value="background", variable=self._vars["target_mode"]).grid(
                            row=2, column=0, sticky="w", pady=2)

        box = self._group(tab, "Target window")
        ttk.Label(box, text="Window").grid(row=0, column=0, sticky="w", pady=3)
        self.window_combo = ttk.Combobox(box, state="readonly", width=52,
                                         textvariable=self._vars["window_choice"])
        self.window_combo.grid(row=0, column=1, columnspan=3, sticky="w", padx=6)
        self.window_combo.bind("<<ComboboxSelected>>", self._on_window_selected)

        buttons = ttk.Frame(box, style="Card.TFrame")
        buttons.grid(row=1, column=1, sticky="w", padx=6, pady=(4, 0))
        ttk.Button(buttons, text="Refresh list", width=13,
                   command=self._refresh_windows).pack(side="left")
        ttk.Button(buttons, text="Pick under cursor", width=17,
                   command=self._arm_pick_window).pack(side="left", padx=4)

        ttk.Label(box, text="Match by title").grid(row=2, column=0, sticky="w", pady=(8, 3))
        ttk.Entry(box, textvariable=self._vars["target_title"],
                  width=52).grid(row=2, column=1, columnspan=3, sticky="w", padx=6, pady=(8, 3))
        ttk.Checkbutton(box, text="Re-find the window by title each time (survives restarts)",
                        variable=self._vars["target_match_by_title"]).grid(
                            row=3, column=1, columnspan=3, sticky="w", padx=6)
        ttk.Checkbutton(box, text="Post to the child control under the point (recommended)",
                        variable=self._vars["background_target_child"]).grid(
                            row=4, column=1, columnspan=3, sticky="w", padx=6, pady=2)
        ttk.Checkbutton(box, text="Coordinates are already client-relative",
                        variable=self._vars["background_client_coords"]).grid(
                            row=5, column=1, columnspan=3, sticky="w", padx=6, pady=2)

        box = self._group(tab, "About background mode")
        help_label = ttk.Label(box, wraplength=600, style="Muted.TLabel",
                               text=BACKGROUND_HELP, justify="left")
        help_label.grid(row=0, column=0, sticky="w")

    # -------------------------------------------------------- options tab
    def _build_options_tab(self) -> None:
        tab = self._tab("Options")

        box = self._group(tab, "Window")
        ttk.Checkbutton(box, text="Always on top",
                        variable=self._vars["always_on_top"]).grid(row=0, column=0, sticky="w", pady=2)
        ttk.Checkbutton(box, text="Minimize to the system tray",
                        variable=self._vars["minimize_to_tray"],
                        command=self._toggle_tray).grid(row=1, column=0, sticky="w", pady=2)
        ttk.Checkbutton(box, text="Closing the window hides it instead of quitting",
                        variable=self._vars["close_to_tray"]).grid(row=2, column=0, sticky="w", pady=2)
        ttk.Checkbutton(box, text="Start minimized",
                        variable=self._vars["start_minimized"]).grid(row=3, column=0, sticky="w", pady=2)
        ttk.Checkbutton(box, text="Ask before quitting while clicking",
                        variable=self._vars["confirm_exit_while_running"]).grid(
                            row=4, column=0, sticky="w", pady=2)
        ttk.Label(box, text="Theme").grid(row=5, column=0, sticky="w", pady=(8, 2))
        theme = ttk.Combobox(box, state="readonly", width=10, values=["dark", "light"],
                             textvariable=self._vars["theme"])
        theme.grid(row=5, column=1, sticky="w")

        box = self._group(tab, "Timing and performance")
        ttk.Checkbutton(box, text="High precision waiting (spin-wait the last ~1.5 ms)",
                        variable=self._vars["high_precision"]).grid(row=0, column=0, sticky="w", pady=2)
        ttk.Checkbutton(box, text="Raise the system timer resolution to 1 ms while running",
                        variable=self._vars["timer_resolution"]).grid(row=1, column=0, sticky="w", pady=2)
        ttk.Checkbutton(box, text="Keep the display and system awake while running",
                        variable=self._vars["prevent_sleep"]).grid(row=2, column=0, sticky="w", pady=2)
        ttk.Label(box, text="Process priority").grid(row=3, column=0, sticky="w", pady=(8, 2))
        prio = ttk.Combobox(box, state="readonly", width=14, values=cfg.PRIORITIES,
                            textvariable=self._vars["process_priority"])
        prio.grid(row=3, column=1, sticky="w")
        prio.bind("<<ComboboxSelected>>", lambda _e: self._apply_runtime_options())
        ttk.Label(box, text="higher priority steadies fast intervals; realtime can "
                            "make the desktop sluggish",
                  style="Muted.TLabel", wraplength=560).grid(row=4, column=0, columnspan=3, sticky="w")

        box = self._group(tab, "Feedback")
        ttk.Checkbutton(box, text="Beep when clicking starts and stops",
                        variable=self._vars["sound_feedback"]).grid(row=0, column=0, sticky="w", pady=2)
        ttk.Checkbutton(box, text="Save settings automatically on exit",
                        variable=self._vars["autosave"]).grid(row=1, column=0, sticky="w", pady=2)

        box = self._group(tab, "Maintenance")
        row = ttk.Frame(box, style="Card.TFrame")
        row.grid(row=0, column=0, sticky="w")
        ttk.Button(row, text="Save settings now", width=18,
                   command=self._save_now).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Open settings folder", width=19,
                   command=lambda: webbrowser.open(str(cfg.config_dir()))).pack(side="left", padx=6)
        ttk.Button(row, text="Reset to defaults", width=18,
                   command=self._reset_defaults).pack(side="left", padx=6)
        status = "elevated (administrator)" if winapi.is_elevated() else "standard user"
        ttk.Label(box, text="Running as: %s   -   settings live in %s"
                            % (status, cfg.config_dir()),
                  style="Muted.TLabel", wraplength=600).grid(row=1, column=0, sticky="w", pady=(8, 0))

    # ------------------------------------------------------- profiles tab
    def _build_profiles_tab(self) -> None:
        tab = self._tab("Profiles")

        box = self._group(tab, "Saved profiles", expand=True)
        self.profile_list = tk.Listbox(box, height=10, activestyle="none", exportselection=False)
        self.profile_list.grid(row=0, column=0, rowspan=6, sticky="nsew", padx=(0, 10))
        self.profile_list.bind("<Double-1>", lambda _e: self._load_profile())
        box.columnconfigure(0, weight=1)
        box.rowconfigure(5, weight=1)

        for i, (text, command) in enumerate((
                ("Load", self._load_profile),
                ("Save as...", self._save_profile_as),
                ("Overwrite", self._overwrite_profile),
                ("Delete", self._delete_profile),
                ("Export...", self._export_profile),
                ("Import...", self._import_profile))):
            ttk.Button(box, text=text, width=14, command=command).grid(
                row=i, column=1, sticky="w", pady=2)

        self._refresh_profiles()

    # ------------------------------------------------------------ log tab
    def _build_log_tab(self) -> None:
        tab = self._tab("Log")

        box = self._group(tab, "Statistics")
        cells = (("Total clicks", "stat_clicks"), ("Events", "stat_events"),
                 ("Skipped", "stat_skipped"), ("Elapsed", "stat_elapsed"),
                 ("Measured CPS", "stat_cps"))
        for i, (label, key) in enumerate(cells):
            ttk.Label(box, text=label, style="Muted.TLabel").grid(row=0, column=i, padx=12)
            ttk.Label(box, textvariable=self._vars[key],
                      style="Metric.TLabel").grid(row=1, column=i, padx=12)

        box = self._group(tab, "Activity", expand=True)
        self.log_text = tk.Text(box, height=14, wrap="word", relief="flat",
                                state="disabled", font=("Consolas", 9))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        ttk.Button(box, text="Clear log", width=12,
                   command=self._clear_log).grid(row=1, column=0, sticky="w", pady=(6, 0))

    # ----------------------------------------------------------- builders
    def _tab(self, title: str) -> ttk.Frame:
        """A tab whose body scrolls, so no group can be clipped by a short
        window or a large system font."""
        outer = ttk.Frame(self.nb, style="App.TFrame")
        self.nb.add(outer, text="  %s  " % title)

        canvas = tk.Canvas(outer, highlightthickness=0, bd=0, takefocus=0)
        bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas, style="App.TFrame", padding=10)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)

        def resized(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(window, width=canvas.winfo_width())
            # Only show the scrollbar when it is actually needed.
            if inner.winfo_reqheight() > canvas.winfo_height():
                if not bar.winfo_ismapped():
                    bar.pack(side="right", fill="y")
            elif bar.winfo_ismapped():
                bar.pack_forget()
                canvas.yview_moveto(0)

        inner.bind("<Configure>", resized)
        canvas.bind("<Configure>", resized)

        def wheel(event):
            if inner.winfo_reqheight() > canvas.winfo_height():
                canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        self._canvases.append(canvas)
        return inner

    def _group(self, parent, title: str, expand: bool = False) -> ttk.Frame:
        outer = ttk.LabelFrame(parent, text=" %s " % title, style="Group.TLabelframe",
                               padding=10)
        outer.pack(fill="both", expand=expand, pady=(0, 10))
        inner = ttk.Frame(outer, style="Card.TFrame")
        inner.pack(fill="both", expand=True)
        return inner

    # ==================================================================
    # Theme
    # ==================================================================
    def _apply_theme(self) -> None:
        name = self._vars["theme"].get() if "theme" in self._vars else "dark"
        colors = THEMES.get(name, THEMES["dark"])
        self._colors = colors
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        bg, panel, field = colors["bg"], colors["panel"], colors["field"]
        fg, muted, accent = colors["fg"], colors["muted"], colors["accent"]
        border = colors["border"]

        self.root.configure(bg=bg)
        style.configure(".", background=bg, foreground=fg, fieldbackground=field,
                        bordercolor=border, focuscolor=accent)
        style.configure("App.TFrame", background=bg)
        style.configure("Card.TFrame", background=panel)
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=panel, foreground=fg)
        style.configure("Muted.TLabel", background=panel, foreground=muted, font=("Segoe UI", 8))
        style.configure("Status.TLabel", background=panel, foreground=fg,
                        font=("Segoe UI Semibold", 11))
        style.configure("Metric.TLabel", background=panel, foreground=accent,
                        font=("Segoe UI Semibold", 11))
        style.configure("Key.TLabel", background=field, foreground=fg,
                        font=("Consolas", 9), padding=4, relief="flat")
        style.configure("Group.TLabelframe", background=panel, bordercolor=border,
                        relief="solid", borderwidth=1)
        style.configure("Group.TLabelframe.Label", background=panel, foreground=muted,
                        font=("Segoe UI Semibold", 9))
        # clam draws a 3D bevel from lightcolor/darkcolor; left at the default
        # it paints a white edge on every field in the dark theme.
        style.configure(".", lightcolor=border, darkcolor=border,
                        troughcolor=bg, arrowcolor=fg,
                        selectbackground=colors["sel"], selectforeground=fg,
                        insertcolor=fg)
        style.configure("TButton", background=field, foreground=fg, borderwidth=1,
                        focusthickness=0, padding=(8, 4),
                        lightcolor=border, darkcolor=border, bordercolor=border)
        style.map("TButton",
                  background=[("active", accent), ("disabled", panel)],
                  foreground=[("active", "#ffffff"), ("disabled", muted)],
                  lightcolor=[("active", accent)], darkcolor=[("active", accent)])
        style.configure("Start.TButton", background=colors["ok"], foreground="#ffffff",
                        font=("Segoe UI Semibold", 10), padding=(10, 6))
        style.map("Start.TButton", background=[("active", colors["ok"]),
                                               ("disabled", panel)])
        style.configure("Stop.TButton", background=colors["err"], foreground="#ffffff",
                        font=("Segoe UI Semibold", 10), padding=(10, 6))
        style.map("Stop.TButton", background=[("active", colors["err"]),
                                              ("disabled", panel)])
        style.configure("TCheckbutton", background=panel, foreground=fg)
        style.map("TCheckbutton", background=[("active", panel)])
        style.configure("TRadiobutton", background=panel, foreground=fg)
        style.map("TRadiobutton", background=[("active", panel)])
        field_opts = dict(fieldbackground=field, foreground=fg, background=field,
                          bordercolor=border, lightcolor=border, darkcolor=border,
                          arrowcolor=fg, insertcolor=fg, borderwidth=1)
        style.configure("TEntry", **field_opts)
        style.configure("TSpinbox", padding=(3, 2), **field_opts)
        style.configure("TCombobox", padding=(3, 2), **field_opts)
        for name in ("TEntry", "TSpinbox", "TCombobox"):
            style.map(name,
                      fieldbackground=[("readonly", field), ("disabled", panel)],
                      foreground=[("disabled", muted)],
                      background=[("readonly", field), ("disabled", panel)],
                      arrowcolor=[("disabled", muted)],
                      bordercolor=[("focus", accent)],
                      lightcolor=[("focus", accent)],
                      darkcolor=[("focus", accent)])
        self.root.option_add("*TCombobox*Listbox.background", field)
        self.root.option_add("*TCombobox*Listbox.foreground", fg)
        self.root.option_add("*TCombobox*Listbox.selectBackground", colors["sel"])
        style.configure("TNotebook", background=bg, borderwidth=0)
        style.configure("TNotebook.Tab", background=panel, foreground=muted,
                        padding=(12, 6), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", bg)],
                  foreground=[("selected", accent)])
        style.configure("Treeview", background=field, fieldbackground=field,
                        foreground=fg, borderwidth=0, rowheight=22)
        style.configure("Treeview.Heading", background=panel, foreground=muted,
                        borderwidth=0)
        style.map("Treeview", background=[("selected", colors["sel"])])
        style.configure("TScrollbar", background=panel, troughcolor=bg,
                        bordercolor=border, arrowcolor=muted)

        for widget, kwargs in (
                (getattr(self, "log_text", None),
                 {"bg": field, "fg": fg, "insertbackground": fg}),
                (getattr(self, "profile_list", None),
                 {"bg": field, "fg": fg, "selectbackground": colors["sel"],
                  "selectforeground": fg, "highlightthickness": 0, "borderwidth": 0}),
                (getattr(self, "status_dot", None), {"bg": panel})):
            if widget is not None:
                try:
                    widget.configure(**kwargs)
                except tk.TclError:
                    pass
        for canvas in getattr(self, "_canvases", ()):
            try:
                canvas.configure(bg=bg)
            except tk.TclError:
                pass
        self._update_status_dot()

    def _apply_topmost(self) -> None:
        try:
            self.root.attributes("-topmost", bool(self._vars["always_on_top"].get()))
        except tk.TclError:
            pass

    def _apply_runtime_options(self) -> None:
        self._apply_topmost()
        winapi.set_process_priority(self._vars["process_priority"].get())

    # ==================================================================
    # Settings <-> widgets
    # ==================================================================
    def _pull(self) -> None:
        """Settings -> widgets."""
        self._syncing = True
        s = self.settings
        try:
            for name, var in self._vars.items():
                if hasattr(s, name):
                    value = getattr(s, name)
                    if not isinstance(value, (Hotkey, list)):
                        var.set(value)
            self.action_combo.current(cfg.ACTION_TYPES.index(s.action_type))
            self.button_combo.current(cfg.MOUSE_BUTTONS.index(s.mouse_button))
            self.rand_unit.current(cfg.RANDOM_UNITS.index(s.random_unit))
            self.rand_dist.current(cfg.DISTRIBUTIONS.index(s.random_distribution))
            self.order_combo.current(cfg.SEQUENCE_ORDERS.index(s.sequence_order))
            self.move_combo.current(cfg.MOVE_STYLES.index(s.move_style))
            self._vars["key_choice"].set(winapi.key_name(s.key_code))
            for name in ("hk_toggle", "hk_start", "hk_stop", "hk_panic", "hk_pick"):
                self._vars["lbl_" + name].set(getattr(s, name).label())
        finally:
            self._syncing = False
        self._refresh_points()
        self._refresh_cps_display()
        self._sync_enabled()
        self._update_hint()

    def _push(self) -> None:
        """Widgets -> settings."""
        s = self.settings
        for name, var in self._vars.items():
            if not hasattr(s, name):
                continue
            current = getattr(s, name)
            if isinstance(current, (Hotkey, list)):
                continue
            try:
                setattr(s, name, var.get())
            except (tk.TclError, ValueError):
                continue  # a spinbox left mid-edit; keep the old value
        name = self._vars["key_choice"].get()
        if name in winapi.VK_NAMES:
            s.key_code = winapi.VK_NAMES[name]
        s.validate()

    def _sync_enabled(self) -> None:
        """Grey out whatever the current mode does not use."""
        if not hasattr(self, "fx"):
            return

        def state(widgets, on):
            for widget in widgets:
                try:
                    widget.configure(state=("normal" if on else "disabled"))
                except tk.TclError:
                    pass

        mode = self._vars["position_mode"].get()
        state((self.fx, self.fy, self.btn_pick), mode == "fixed")
        try:
            self.tree.configure(selectmode="extended" if mode == "sequence" else "none")
        except tk.TclError:
            pass
        self.order_combo.configure(state="readonly" if mode == "sequence" else "disabled")

        action = self._vars["action_type"].get()
        for widget in self.key_row.winfo_children():
            try:
                widget.configure(state=("normal" if action == "keypress" else "disabled"))
            except tk.TclError:
                pass
        if action == "keypress":
            self.key_combo.configure(state="readonly")
        for widget in self.text_row.winfo_children():
            try:
                widget.configure(state=("normal" if action == "keytext" else "disabled"))
            except tk.TclError:
                pass
        for widget in self.scroll_row.winfo_children():
            try:
                widget.configure(state=("normal" if action == "scroll" else "disabled"))
            except tk.TclError:
                pass
        self.button_combo.configure(
            state="readonly" if action in ("click", "double_click") else "disabled")

        repeat = self._vars["repeat_mode"].get()
        state((self.count_spin,), repeat == "count")
        state(self.dur_spins, repeat == "duration")

        rand = bool(self._vars["randomize_interval"].get())
        state((self.rand_amount,), rand)
        self.rand_unit.configure(state="readonly" if rand else "disabled")
        self.rand_dist.configure(state="readonly" if rand else "disabled")
        state((self.rand_hold,), bool(self._vars["randomize_hold"].get()))
        state((self.jitter_spin,), bool(self._vars["jitter_enabled"].get()))
        state((self.burst_size, self.burst_pause), bool(self._vars["burst_enabled"].get()))
        moving = self._vars["move_style"].get() != "instant"
        state((self.move_dur, self.move_steps), moving)

    # ------------------------------------------------------------- interval
    def _on_interval_changed(self, *_args) -> None:
        if not self._syncing:
            self._refresh_cps_display()

    def _refresh_cps_display(self) -> None:
        try:
            total = (self._vars["interval_hours"].get() * 3600
                     + self._vars["interval_minutes"].get() * 60
                     + self._vars["interval_seconds"].get()
                     + self._vars["interval_millis"].get() / 1000.0
                     + self._vars["interval_micros"].get() / 1_000_000.0)
        except (tk.TclError, ValueError):
            return
        self._syncing = True
        try:
            if total <= 0:
                self._vars["cps_text"].set("unlimited")
            else:
                cps = 1.0 / total
                self._vars["cps_text"].set(("%.4g" % cps) if cps < 1000 else "%.0f" % cps)
        finally:
            self._syncing = False
        self._update_hint()

    def _apply_cps(self) -> None:
        text = self._vars["cps_text"].get().strip().lower()
        if text in ("unlimited", "max", "0"):
            self._set_interval_total(0.0)
            return
        try:
            cps = float(text)
        except ValueError:
            messagebox.showerror(APP_TITLE, "Enter clicks per second as a number.")
            return
        if cps <= 0:
            self._set_interval_total(0.0)
        else:
            self._set_interval_total(1.0 / cps)

    def _set_cps(self, cps: float) -> None:
        self._set_interval_total(1.0 / cps if cps > 0 else 0.0)

    def _set_interval_total(self, seconds: float) -> None:
        temp = Settings()
        temp.set_interval_seconds(seconds)
        self._syncing = True
        try:
            for name in ("interval_hours", "interval_minutes", "interval_seconds",
                         "interval_millis", "interval_micros"):
                self._vars[name].set(getattr(temp, name))
        finally:
            self._syncing = False
        self._refresh_cps_display()

    def _update_hint(self) -> None:
        if not hasattr(self, "hint"):
            return
        self.hint.configure(text="%s to toggle   -   %s to stop"
                                 % (self.settings.hk_toggle.label(),
                                    self.settings.hk_stop.label()))

    # ==================================================================
    # Points
    # ==================================================================
    def _refresh_points(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for i, point in enumerate(self.settings.points, 1):
            delay = "global" if point.delay_ms < 0 else "%g ms" % point.delay_ms
            button = cfg.BUTTON_LABELS.get(point.button, "global")
            mark = "" if point.enabled else "  (off)"
            self.tree.insert("", "end", values=(i, point.x, point.y, button,
                                                delay, point.label + mark))

    def _selected_indices(self) -> list:
        return sorted(self.tree.index(item) for item in self.tree.selection())

    def _add_point_cursor(self) -> None:
        x, y = winapi.get_cursor_pos()
        self.settings.points.append(ClickPoint(x=x, y=y))
        self._refresh_points()
        self._vars["position_mode"].set("sequence")
        self.log("Added point %d, %d" % (x, y))

    def _edit_point_new(self) -> None:
        point = ClickPoint(*winapi.get_cursor_pos())
        if PointDialog(self.root, point, self._colors).result:
            self.settings.points.append(point)
            self._refresh_points()
            self._vars["position_mode"].set("sequence")

    def _edit_point(self) -> None:
        indices = self._selected_indices()
        if not indices:
            return
        point = self.settings.points[indices[0]]
        if PointDialog(self.root, point, self._colors).result:
            self._refresh_points()

    def _remove_points(self) -> None:
        for index in reversed(self._selected_indices()):
            del self.settings.points[index]
        self._refresh_points()

    def _move_point(self, delta: int) -> None:
        indices = self._selected_indices()
        if not indices:
            return
        points = self.settings.points
        order = indices if delta < 0 else list(reversed(indices))
        moved = []
        for index in order:
            target = index + delta
            if 0 <= target < len(points):
                points[index], points[target] = points[target], points[index]
                moved.append(target)
            else:
                moved.append(index)
        self._refresh_points()
        children = self.tree.get_children()
        self.tree.selection_set([children[i] for i in moved if i < len(children)])

    def _clear_points(self) -> None:
        if self.settings.points and messagebox.askyesno(APP_TITLE, "Remove every point?"):
            self.settings.points.clear()
            self._refresh_points()

    def _use_cursor_pos(self) -> None:
        x, y = winapi.get_cursor_pos()
        self._vars["fixed_x"].set(x)
        self._vars["fixed_y"].set(y)

    # ==================================================================
    # Picking
    # ==================================================================
    def _arm_pick_position(self) -> None:
        if not self.settings.hk_pick.is_set():
            messagebox.showinfo(APP_TITLE, "Set the capture hotkey on the Hotkeys tab first.")
            return
        self._pick_mode = "position"
        self._vars["status"].set("Move the cursor to the target and press %s"
                                 % self.settings.hk_pick.label())

    def _arm_pick_window(self) -> None:
        if not self.settings.hk_pick.is_set():
            messagebox.showinfo(APP_TITLE, "Set the capture hotkey on the Hotkeys tab first.")
            return
        self._pick_mode = "window"
        self._vars["status"].set("Hover the target window and press %s"
                                 % self.settings.hk_pick.label())

    def _on_pick(self) -> None:
        """Runs on the hotkey dispatcher thread."""
        x, y = winapi.get_cursor_pos()
        mode = self._pick_mode or ("position" if self._vars["position_mode"].get() != "sequence"
                                   else "point")
        self._pick_mode = ""
        if mode == "window":
            hwnd = winapi.root_window(winapi.window_from_point(x, y))
            self._post("picked_window", (hwnd, winapi.window_title(hwnd)))
        else:
            self._post("picked_position", (x, y, mode))

    # ==================================================================
    # Hotkeys
    # ==================================================================
    def _rebind_hotkeys(self) -> None:
        s = self.settings
        hk = self.hotkeys
        hk.clear()
        suppress_key = bool(self._vars["suppress_hotkeys"].get())
        suppress_mouse = bool(self._vars["suppress_mouse_hotkeys"].get())

        def suppress_for(hotkey: Hotkey) -> bool:
            return suppress_mouse if hotkey.kind == "mouse" else suppress_key

        if s.hotkey_mode == "hold":
            hk.register("toggle", s.hk_toggle,
                        on_press=self._hk_start, on_release=self._hk_stop,
                        suppress=suppress_for(s.hk_toggle))
        else:
            hk.register("toggle", s.hk_toggle, on_press=self._hk_toggle,
                        suppress=suppress_for(s.hk_toggle))
        hk.register("start", s.hk_start, on_press=self._hk_start,
                    suppress=suppress_for(s.hk_start))
        hk.register("stop", s.hk_stop, on_press=self._hk_stop,
                    suppress=suppress_for(s.hk_stop))
        hk.register("panic", s.hk_panic, on_press=self._hk_panic, suppress=False)
        hk.register("pick", s.hk_pick, on_press=self._on_pick,
                    suppress=suppress_for(s.hk_pick))
        self._update_hint()

    def _hk_toggle(self) -> None:
        self._post("toggle")

    def _hk_start(self) -> None:
        self._post("start")

    def _hk_stop(self) -> None:
        self._post("stop")

    def _hk_panic(self) -> None:
        self._post("panic")

    def _capture_hotkey(self, name: str) -> None:
        self._vars["lbl_" + name].set("Press any key...")
        self._vars["status"].set("Press a key or mouse button (Esc cancels)")

        def done(hotkey: Hotkey):
            self._post("captured", (name, hotkey))

        self.hotkeys.capture_next(done, include_mouse=True)

    def _clear_hotkey(self, name: str) -> None:
        setattr(self.settings, name, Hotkey(enabled=False))
        self._vars["lbl_" + name].set("Not set")
        self._rebind_hotkeys()

    def _capture_action_key(self) -> None:
        self._vars["status"].set("Press the key to send (Esc cancels)")

        def done(hotkey: Hotkey):
            self._post("captured_action_key", hotkey)

        self.hotkeys.capture_next(done, include_mouse=False)

    # ==================================================================
    # Window targeting
    # ==================================================================
    def _refresh_windows(self) -> None:
        own_title = self.root.title()
        self._window_list = [(h, t) for h, t in winapi.enum_windows() if t != own_title]
        labels = ["%s   [0x%X]" % (title[:70], hwnd) for hwnd, title in self._window_list]
        self.window_combo.configure(values=labels)
        self.log("Found %d windows." % len(labels))

    def _on_window_selected(self, _event=None) -> None:
        index = self.window_combo.current()
        if 0 <= index < len(self._window_list):
            hwnd, title = self._window_list[index]
            self.settings.target_hwnd = hwnd
            self._vars["target_title"].set(title)
            self.log("Target window: %s (0x%X)" % (title, hwnd))

    # ==================================================================
    # Profiles
    # ==================================================================
    def _refresh_profiles(self) -> None:
        self.profile_list.delete(0, "end")
        for name in cfg.list_profiles():
            self.profile_list.insert("end", name)

    def _selected_profile(self) -> str:
        selection = self.profile_list.curselection()
        return self.profile_list.get(selection[0]) if selection else ""

    def _save_profile_as(self) -> None:
        name = SimplePrompt(self.root, "Profile name", "Save profile as:",
                            self._colors, self._selected_profile()).result
        if not name:
            return
        self._push()
        cfg.save_profile(name, self.settings)
        self.settings.last_profile = name
        self._refresh_profiles()
        self.log("Saved profile '%s'." % name)

    def _overwrite_profile(self) -> None:
        name = self._selected_profile()
        if not name:
            messagebox.showinfo(APP_TITLE, "Select a profile first.")
            return
        self._push()
        cfg.save_profile(name, self.settings)
        self.log("Updated profile '%s'." % name)

    def _load_profile(self) -> None:
        name = self._selected_profile()
        if not name:
            return
        if self.engine.running:
            messagebox.showinfo(APP_TITLE, "Stop clicking before loading a profile.")
            return
        loaded = cfg.load_profile(name)
        loaded.last_profile = name
        self._replace_settings(loaded)
        self.log("Loaded profile '%s'." % name)

    def _delete_profile(self) -> None:
        name = self._selected_profile()
        if not name:
            return
        if messagebox.askyesno(APP_TITLE, "Delete profile '%s'?" % name):
            cfg.delete_profile(name)
            self._refresh_profiles()
            self.log("Deleted profile '%s'." % name)

    def _export_profile(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Export settings", defaultextension=".json",
            filetypes=[("JSON", "*.json")], initialfile="autoclicker-profile.json")
        if not path:
            return
        self._push()
        cfg.save_settings(self.settings, Path(path))
        self.log("Exported to %s" % path)

    def _import_profile(self) -> None:
        path = filedialog.askopenfilename(title="Import settings",
                                          filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = Settings.from_dict(json.load(fh))
        except (OSError, ValueError) as exc:
            messagebox.showerror(APP_TITLE, "Could not read that file:\n%s" % exc)
            return
        self._replace_settings(loaded)
        self.log("Imported %s" % path)

    def _replace_settings(self, new: Settings) -> None:
        self.settings = new
        self.engine.settings = new
        self._pull()
        self._rebind_hotkeys()
        self._apply_theme()
        self._apply_runtime_options()

    def _save_now(self) -> None:
        self._push()
        path = cfg.save_settings(self.settings)
        self.log("Settings saved to %s" % path)

    def _reset_defaults(self) -> None:
        if not messagebox.askyesno(APP_TITLE, "Reset every setting to its default?"):
            return
        self._replace_settings(Settings())
        self.log("Settings reset to defaults.")

    # ==================================================================
    # Engine control
    # ==================================================================
    def start(self) -> None:
        if self.engine.running:
            return
        self._push()
        if self.settings.autosave:
            try:
                cfg.save_settings(self.settings)
            except OSError:
                pass
        if not self.engine.start():
            return

    def stop(self) -> None:
        self.engine.stop()

    def toggle(self) -> None:
        if self.engine.running:
            self.stop()
        else:
            self.start()

    def _engine_event(self, kind: str, payload=None) -> None:
        self._post("engine:" + kind, payload)

    def _beep(self, start: bool) -> None:
        if not self.settings.sound_feedback:
            return

        def play():
            try:
                import winsound

                winsound.Beep(880 if start else 440, 90)
            except Exception:
                pass

        # Beep is synchronous, so keep it off the Tk thread.
        threading.Thread(target=play, daemon=True).start()

    # ==================================================================
    # UI plumbing
    # ==================================================================
    def _post(self, kind: str, payload=None) -> None:
        self._ui_queue.put((kind, payload))

    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", "[%s] %s\n" % (stamp, message))
            # Keep the buffer from growing without bound during long sessions.
            if int(self.log_text.index("end-1c").split(".")[0]) > 500:
                self.log_text.delete("1.0", "100.0")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except tk.TclError:
            pass

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _update_status_dot(self) -> None:
        if not hasattr(self, "status_dot"):
            return
        colors = getattr(self, "_colors", THEMES["dark"])
        running = self.engine.running
        try:
            self.status_dot.itemconfigure(
                self._dot, fill=colors["ok"] if running else colors["muted"])
        except tk.TclError:
            pass

    def _pump(self) -> None:
        """Drains cross-thread messages and refreshes live numbers."""
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                self._handle_ui(kind, payload)
        except queue.Empty:
            pass

        stats = self.engine.stats
        self._vars["stat_clicks"].set("{:,}".format(stats.clicks))
        self._vars["stat_events"].set("{:,}".format(stats.events))
        self._vars["stat_skipped"].set("{:,}".format(stats.skipped))
        self._vars["stat_elapsed"].set(_fmt_duration(stats.elapsed))
        self._vars["stat_cps"].set("%.1f" % stats.actual_cps)
        x, y = winapi.get_cursor_pos()
        self._vars["cursor_pos"].set("cursor at %d, %d" % (x, y))

        if not self._closing:
            self.root.after(50, self._pump)

    def _handle_ui(self, kind: str, payload) -> None:
        if kind == "toggle":
            self.toggle()
        elif kind == "start":
            self.start()
        elif kind == "stop":
            self.stop()
        elif kind == "panic":
            self.stop()
            self.log("Panic stop.")
        elif kind == "quit":
            self.quit()
        elif kind == "log":
            self.log(str(payload))
        elif kind == "captured":
            name, hotkey = payload
            self._vars["status"].set("Idle" if not self.engine.running else "Clicking")
            if hotkey.kind == "key" and hotkey.code == winapi.VK_ESCAPE and not hotkey.mods:
                self._vars["lbl_" + name].set(getattr(self.settings, name).label())
                self.log("Hotkey capture cancelled.")
                return
            clash = self.hotkeys.conflicts(hotkey, ignore=name.replace("hk_", ""))
            setattr(self.settings, name, hotkey)
            self._vars["lbl_" + name].set(hotkey.label())
            self._rebind_hotkeys()
            self.log("Bound %s to %s%s" % (name.replace("hk_", ""), hotkey.label(),
                                           (" (also used by %s)" % ", ".join(clash))
                                           if clash else ""))
        elif kind == "captured_action_key":
            self._vars["status"].set("Idle" if not self.engine.running else "Clicking")
            if payload.kind != "key" or not payload.code:
                return
            if payload.code == winapi.VK_ESCAPE and not payload.mods:
                self.log("Key capture cancelled.")
                return
            self.settings.key_code = payload.code
            self._vars["key_choice"].set(winapi.key_name(payload.code))
            self.log("Action key set to %s" % winapi.key_name(payload.code))
        elif kind == "picked_position":
            x, y, mode = payload
            self._vars["status"].set("Idle" if not self.engine.running else "Clicking")
            if mode == "point" or self._vars["position_mode"].get() == "sequence":
                self.settings.points.append(ClickPoint(x=x, y=y))
                self._refresh_points()
                self.log("Added point %d, %d" % (x, y))
            else:
                self._vars["fixed_x"].set(x)
                self._vars["fixed_y"].set(y)
                self._vars["position_mode"].set("fixed")
                self.log("Fixed position set to %d, %d" % (x, y))
        elif kind == "picked_window":
            hwnd, title = payload
            self._vars["status"].set("Idle" if not self.engine.running else "Clicking")
            if hwnd:
                self.settings.target_hwnd = hwnd
                self._vars["target_title"].set(title)
                self._refresh_windows()
                self.log("Target window: %s (0x%X)" % (title or "(untitled)", hwnd))
        elif kind == "engine:started":
            self._on_engine_started()
        elif kind == "engine:stopped":
            self._on_engine_stopped()
        elif kind == "engine:limit":
            self.log(str(payload))
        elif kind == "engine:error":
            self.log("Error: %s" % payload)
            messagebox.showerror(APP_TITLE, str(payload))
        # engine:tick is intentionally ignored - stats are polled instead, so a
        # 1000 CPS run does not flood the Tk event loop.

    def _on_engine_started(self) -> None:
        self._vars["status"].set("Clicking")
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self._update_status_dot()
        if self.tray:
            self.tray.set_icon(self._icon_run, "%s - running" % APP_TITLE)
        self._beep(True)
        self.log("Started (%s, every %s)."
                 % (cfg.ACTION_LABELS[self.settings.action_type],
                    self._interval_text()))

    def _on_engine_stopped(self) -> None:
        self._vars["status"].set("Idle")
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self._update_status_dot()
        if self.tray:
            self.tray.set_icon(self._icon_idle, "%s - idle" % APP_TITLE)
        self._beep(False)
        stats = self.engine.stats
        self.log("Stopped after %s clicks in %s (%.1f CPS)."
                 % ("{:,}".format(stats.clicks), _fmt_duration(stats.elapsed),
                    stats.actual_cps))

    def _interval_text(self) -> str:
        total = self.settings.interval_seconds_total()
        if total <= 0:
            return "no delay"
        if total < 1:
            return "%.3g ms" % (total * 1000)
        return "%.4g s" % total

    # ==================================================================
    # Tray and window state
    # ==================================================================
    def _start_tray(self) -> None:
        if self.tray:
            return
        menu = [
            ("Show / hide", self._toggle_window),
            (None, None),
            (lambda: "Stop" if self.engine.running else "Start", self._tray_toggle),
            (None, None),
            ("Quit", self._tray_quit),
        ]
        self.tray = TrayIcon("%s - idle" % APP_TITLE, self._icon_idle,
                             on_activate=self._toggle_window, menu=menu)
        if not self.tray.start():
            self.tray = None
            self.log("The tray icon could not be created; the window will stay visible.")

    def _stop_tray(self) -> None:
        if self.tray:
            self.tray.stop()
            self.tray = None

    def _toggle_tray(self) -> None:
        if self._vars["minimize_to_tray"].get():
            self._start_tray()
        else:
            self._show_window()
            self._stop_tray()

    def _tray_toggle(self) -> None:
        self._post("toggle")

    def _tray_quit(self) -> None:
        self._post("quit")

    def _hide_window(self) -> None:
        if self.tray:
            self.root.withdraw()
            self._hidden = True
        else:
            self.root.iconify()

    def _show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self._hidden = False

    def _toggle_window(self) -> None:
        # Called from the tray thread.
        self.root.after(0, lambda: self._hide_window() if not self._hidden
                        else self._show_window())

    def _on_unmap(self, event) -> None:
        if event.widget is not self.root:
            return
        if self.settings.minimize_to_tray and self.tray and \
                self.root.state() == "iconic":
            self.root.after(10, self._hide_window)

    def _on_close(self) -> None:
        if self._vars["close_to_tray"].get() and self.tray:
            self._hide_window()
            return
        self.quit()

    def quit(self) -> None:
        if self.engine.running and self._vars["confirm_exit_while_running"].get():
            if not messagebox.askyesno(APP_TITLE, "Clicking is still running. Quit anyway?"):
                return
        self._closing = True
        try:
            self._push()
            if self.settings.autosave:
                cfg.save_settings(self.settings)
        except Exception:
            pass
        self.engine.stop(wait=True)
        winapi.keep_awake(False)
        self.hotkeys.stop()
        self._stop_tray()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self) -> None:
        self.root.mainloop()


# ==========================================================================
# Small dialogs
# ==========================================================================


class _Modal(tk.Toplevel):
    def __init__(self, parent, title: str, colors: dict):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.configure(bg=colors["panel"])
        self.transient(parent)
        self.result = None

    def _finish(self) -> None:
        self.update_idletasks()
        px, py = self.master.winfo_rootx(), self.master.winfo_rooty()
        pw, ph = self.master.winfo_width(), self.master.winfo_height()
        x = px + (pw - self.winfo_width()) // 2
        y = py + (ph - self.winfo_height()) // 3
        self.geometry("+%d+%d" % (max(0, x), max(0, y)))
        self.grab_set()
        self.wait_window(self)


class PointDialog(_Modal):
    """Edit one entry of the click sequence."""

    def __init__(self, parent, point: ClickPoint, colors: dict):
        super().__init__(parent, "Click point", colors)
        self.point = point

        body = ttk.Frame(self, style="Card.TFrame", padding=12)
        body.pack(fill="both", expand=True)

        self.x = tk.IntVar(value=point.x)
        self.y = tk.IntVar(value=point.y)
        self.label = tk.StringVar(value=point.label)
        self.enabled = tk.BooleanVar(value=point.enabled)
        self.use_delay = tk.BooleanVar(value=point.delay_ms >= 0)
        self.delay = tk.DoubleVar(value=point.delay_ms if point.delay_ms >= 0 else 100.0)
        self.button = tk.StringVar(value=point.button or "global")

        ttk.Label(body, text="X").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Spinbox(body, from_=-32000, to=32000, width=10,
                    textvariable=self.x).grid(row=0, column=1, sticky="w")
        ttk.Label(body, text="Y").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Spinbox(body, from_=-32000, to=32000, width=10,
                    textvariable=self.y).grid(row=1, column=1, sticky="w")
        ttk.Button(body, text="Use cursor", width=12,
                   command=self._use_cursor).grid(row=0, column=2, rowspan=2, padx=8)

        ttk.Label(body, text="Button").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Combobox(body, state="readonly", width=12, textvariable=self.button,
                     values=["global"] + cfg.MOUSE_BUTTONS).grid(row=2, column=1, sticky="w")

        ttk.Checkbutton(body, text="Own delay (ms)", variable=self.use_delay).grid(
            row=3, column=0, sticky="w", pady=3)
        ttk.Spinbox(body, from_=0, to=3600000, increment=10, width=10,
                    textvariable=self.delay).grid(row=3, column=1, sticky="w")

        ttk.Label(body, text="Label").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Entry(body, textvariable=self.label, width=26).grid(
            row=4, column=1, columnspan=2, sticky="w")

        ttk.Checkbutton(body, text="Enabled", variable=self.enabled).grid(
            row=5, column=0, sticky="w", pady=(6, 0))

        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.grid(row=6, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", width=10,
                   command=self.destroy).pack(side="right", padx=4)
        ttk.Button(buttons, text="OK", width=10, command=self._ok).pack(side="right")

        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self._finish()

    def _use_cursor(self) -> None:
        x, y = winapi.get_cursor_pos()
        self.x.set(x)
        self.y.set(y)

    def _ok(self) -> None:
        try:
            self.point.x = int(self.x.get())
            self.point.y = int(self.y.get())
            self.point.delay_ms = float(self.delay.get()) if self.use_delay.get() else -1.0
        except (tk.TclError, ValueError):
            messagebox.showerror(APP_TITLE, "X, Y and the delay must be numbers.")
            return
        button = self.button.get()
        self.point.button = "" if button == "global" else button
        self.point.label = self.label.get()
        self.point.enabled = bool(self.enabled.get())
        self.result = True
        self.destroy()


class SimplePrompt(_Modal):
    def __init__(self, parent, title: str, prompt: str, colors: dict, initial: str = ""):
        super().__init__(parent, title, colors)
        body = ttk.Frame(self, style="Card.TFrame", padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=prompt).pack(anchor="w")
        self.var = tk.StringVar(value=initial)
        entry = ttk.Entry(body, textvariable=self.var, width=34)
        entry.pack(fill="x", pady=8)
        entry.focus_set()
        entry.select_range(0, "end")
        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.pack(anchor="e")
        ttk.Button(buttons, text="Cancel", width=10,
                   command=self.destroy).pack(side="right", padx=4)
        ttk.Button(buttons, text="OK", width=10, command=self._ok).pack(side="right")
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self._finish()

    def _ok(self) -> None:
        name = self.var.get().strip()
        if name:
            self.result = name
        self.destroy()

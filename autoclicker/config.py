"""Settings model, JSON persistence and named profiles."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path

APP_NAME = "AutoClicker"


def app_root() -> Path:
    """Where the bundled files live, in source and inside a PyInstaller exe."""
    if getattr(sys, "frozen", False):
        # onefile unpacks to _MEIPASS; onedir keeps them beside the exe.
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def asset(name: str) -> Path:
    return app_root() / "assets" / name

MOUSE_BUTTONS = ["left", "right", "middle", "x1", "x2"]
BUTTON_LABELS = {
    "left": "Left", "right": "Right", "middle": "Middle",
    "x1": "X1 (back)", "x2": "X2 (forward)",
}

ACTION_TYPES = ["click", "double_click", "keypress", "keytext", "scroll"]
ACTION_LABELS = {
    "click": "Mouse click",
    "double_click": "Mouse double-click",
    "keypress": "Keyboard key press",
    "keytext": "Type text",
    "scroll": "Scroll wheel",
}

POSITION_MODES = ["cursor", "fixed", "sequence"]
POSITION_LABELS = {
    "cursor": "Current cursor position",
    "fixed": "Fixed screen position",
    "sequence": "Sequence of points",
}

SEQUENCE_ORDERS = ["cycle", "once", "random", "pingpong"]
RANDOM_UNITS = ["ms", "percent"]
DISTRIBUTIONS = ["uniform", "gaussian"]
REPEAT_MODES = ["infinite", "count", "duration"]
HOTKEY_MODES = ["toggle", "hold"]
PRIORITIES = ["normal", "above normal", "high", "realtime"]
MOVE_STYLES = ["instant", "linear", "smooth"]


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def profiles_dir() -> Path:
    path = config_dir() / "profiles"
    path.mkdir(parents=True, exist_ok=True)
    return path


SETTINGS_FILE = "settings.json"


@dataclass
class Hotkey:
    """A global binding. ``kind`` is ``key`` or ``mouse``; ``code`` is a VK or
    a 1-5 mouse button id. ``mods`` is a MOD_* bitmask."""

    kind: str = "key"
    code: int = 0
    mods: int = 0
    enabled: bool = True

    def is_set(self) -> bool:
        return self.enabled and self.code != 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data) -> "Hotkey":
        if not isinstance(data, dict):
            return cls()
        return cls(
            kind=str(data.get("kind", "key")),
            code=int(data.get("code", 0) or 0),
            mods=int(data.get("mods", 0) or 0),
            enabled=bool(data.get("enabled", True)),
        )

    def label(self) -> str:
        from . import winapi

        if not self.code:
            return "Not set"
        if self.kind == "mouse":
            base = winapi.MOUSE_BUTTON_NAMES.get(self.code, "Mouse %d" % self.code)
        else:
            base = winapi.key_name(self.code)
        mods = winapi.modifier_text(self.mods)
        return ("%s+%s" % (mods, base)) if mods else base


@dataclass
class ClickPoint:
    x: int = 0
    y: int = 0
    button: str = ""        # blank -> use the global button
    delay_ms: float = -1.0  # < 0 -> use the global interval
    label: str = ""
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data) -> "ClickPoint":
        return cls(
            x=int(data.get("x", 0)),
            y=int(data.get("y", 0)),
            button=str(data.get("button", "") or ""),
            delay_ms=float(data.get("delay_ms", -1.0)),
            label=str(data.get("label", "") or ""),
            enabled=bool(data.get("enabled", True)),
        )


@dataclass
class Settings:
    # ---------------------------------------------------------- interval
    interval_hours: int = 0
    interval_minutes: int = 0
    interval_seconds: int = 0
    interval_millis: int = 100
    interval_micros: int = 0

    # ------------------------------------------------------------ action
    action_type: str = "click"
    mouse_button: str = "left"
    clicks_per_event: int = 1        # 1 = single, 2 = double, 3 = triple, ...
    multi_click_gap_ms: float = 40.0
    # How long the button stays pressed. Defaults to 0: any hold longer than
    # the interval becomes the real speed limit, so a non-zero default would
    # silently cap the click rate.
    hold_duration_ms: float = 0.0
    key_code: int = 0x20             # Space, for the keypress action
    key_text: str = ""
    key_use_scancode: bool = False
    scroll_amount: int = 1
    scroll_horizontal: bool = False

    # --------------------------------------------------------- randomize
    randomize_interval: bool = False
    random_unit: str = "ms"          # ms | percent
    random_amount: float = 20.0
    random_distribution: str = "uniform"
    randomize_hold: bool = False
    random_hold_ms: float = 5.0

    # --------------------------------------------------------- position
    position_mode: str = "cursor"
    fixed_x: int = 0
    fixed_y: int = 0
    points: list = field(default_factory=list)
    sequence_order: str = "cycle"
    jitter_enabled: bool = False
    jitter_radius: int = 3
    restore_cursor: bool = False
    move_style: str = "instant"
    move_duration_ms: float = 60.0
    move_steps: int = 20

    # ----------------------------------------------------------- repeat
    repeat_mode: str = "infinite"
    repeat_count: int = 100
    duration_hours: int = 0
    duration_minutes: int = 1
    duration_seconds: int = 0
    start_delay_ms: float = 0.0
    burst_enabled: bool = False
    burst_size: int = 10
    burst_pause_ms: float = 1000.0

    # ---------------------------------------------------------- hotkeys
    hotkey_mode: str = "toggle"
    hk_toggle: Hotkey = field(default_factory=lambda: Hotkey("key", 0x74, 0))   # F5
    hk_start: Hotkey = field(default_factory=lambda: Hotkey("key", 0, 0, False))
    hk_stop: Hotkey = field(default_factory=lambda: Hotkey("key", 0x75, 0))     # F6
    hk_panic: Hotkey = field(default_factory=lambda: Hotkey("key", 0x1B, 0))    # Esc
    hk_pick: Hotkey = field(default_factory=lambda: Hotkey("key", 0x71, 0))     # F2
    suppress_hotkeys: bool = False
    suppress_mouse_hotkeys: bool = True

    # ----------------------------------------------------------- target
    target_mode: str = "global"      # global | active_only | background
    target_hwnd: int = 0
    target_title: str = ""
    target_match_by_title: bool = True
    background_client_coords: bool = False
    background_target_child: bool = True

    # ---------------------------------------------------------- options
    always_on_top: bool = False
    minimize_to_tray: bool = True
    start_minimized: bool = False
    close_to_tray: bool = False
    sound_feedback: bool = False
    high_precision: bool = True
    timer_resolution: bool = True
    process_priority: str = "normal"
    prevent_sleep: bool = False
    confirm_exit_while_running: bool = True
    theme: str = "dark"
    show_overlay: bool = False
    autosave: bool = True

    # ------------------------------------------------------------ misc
    last_profile: str = ""

    # ------------------------------------------------------- conversions
    def interval_seconds_total(self) -> float:
        total = (
            self.interval_hours * 3600.0
            + self.interval_minutes * 60.0
            + self.interval_seconds
            + self.interval_millis / 1000.0
            + self.interval_micros / 1_000_000.0
        )
        return max(0.0, total)

    def duration_seconds_total(self) -> float:
        return max(
            0.0,
            self.duration_hours * 3600.0
            + self.duration_minutes * 60.0
            + self.duration_seconds,
        )

    def cps(self) -> float:
        interval = self.interval_seconds_total()
        return (1.0 / interval) if interval > 0 else 0.0

    def set_interval_seconds(self, total: float) -> None:
        total = max(0.0, float(total))
        self.interval_hours = int(total // 3600)
        total -= self.interval_hours * 3600
        self.interval_minutes = int(total // 60)
        total -= self.interval_minutes * 60
        self.interval_seconds = int(total)
        total -= self.interval_seconds
        micros_total = int(round(total * 1_000_000))
        self.interval_millis = micros_total // 1000
        self.interval_micros = micros_total % 1000

    # --------------------------------------------------------- (de)serial
    def to_dict(self) -> dict:
        data = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, Hotkey):
                data[f.name] = value.to_dict()
            elif f.name == "points":
                data[f.name] = [
                    p.to_dict() if isinstance(p, ClickPoint) else dict(p) for p in value
                ]
            else:
                data[f.name] = value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        obj = cls()
        if not isinstance(data, dict):
            return obj
        for f in fields(cls):
            if f.name not in data:
                continue
            raw = data[f.name]
            try:
                if f.name == "points":
                    obj.points = [ClickPoint.from_dict(p) for p in raw if isinstance(p, dict)]
                elif f.type == "Hotkey" or isinstance(getattr(obj, f.name), Hotkey):
                    setattr(obj, f.name, Hotkey.from_dict(raw))
                else:
                    current = getattr(obj, f.name)
                    if isinstance(current, bool):
                        setattr(obj, f.name, bool(raw))
                    elif isinstance(current, int):
                        setattr(obj, f.name, int(raw))
                    elif isinstance(current, float):
                        setattr(obj, f.name, float(raw))
                    elif isinstance(current, str):
                        setattr(obj, f.name, str(raw))
                    else:
                        setattr(obj, f.name, raw)
            except (TypeError, ValueError):
                continue  # keep the default for anything malformed
        obj.validate()
        return obj

    def validate(self) -> None:
        """Clamp anything that would make the engine misbehave."""
        self.interval_hours = max(0, min(999, self.interval_hours))
        self.interval_minutes = max(0, min(59, self.interval_minutes))
        self.interval_seconds = max(0, min(59, self.interval_seconds))
        self.interval_millis = max(0, min(999, self.interval_millis))
        self.interval_micros = max(0, min(999, self.interval_micros))
        self.clicks_per_event = max(1, min(10, self.clicks_per_event))
        self.multi_click_gap_ms = max(0.0, min(5000.0, self.multi_click_gap_ms))
        self.hold_duration_ms = max(0.0, min(60000.0, self.hold_duration_ms))
        self.random_amount = max(0.0, self.random_amount)
        self.random_hold_ms = max(0.0, self.random_hold_ms)
        self.jitter_radius = max(0, min(2000, self.jitter_radius))
        self.repeat_count = max(1, self.repeat_count)
        self.burst_size = max(1, self.burst_size)
        self.burst_pause_ms = max(0.0, self.burst_pause_ms)
        self.start_delay_ms = max(0.0, self.start_delay_ms)
        self.move_steps = max(2, min(500, self.move_steps))
        self.move_duration_ms = max(0.0, min(10000.0, self.move_duration_ms))
        self.scroll_amount = max(-100, min(100, self.scroll_amount)) or 1
        if self.action_type not in ACTION_TYPES:
            self.action_type = "click"
        if self.mouse_button not in MOUSE_BUTTONS:
            self.mouse_button = "left"
        if self.position_mode not in POSITION_MODES:
            self.position_mode = "cursor"
        if self.sequence_order not in SEQUENCE_ORDERS:
            self.sequence_order = "cycle"
        if self.random_unit not in RANDOM_UNITS:
            self.random_unit = "ms"
        if self.random_distribution not in DISTRIBUTIONS:
            self.random_distribution = "uniform"
        if self.repeat_mode not in REPEAT_MODES:
            self.repeat_mode = "infinite"
        if self.hotkey_mode not in HOTKEY_MODES:
            self.hotkey_mode = "toggle"
        if self.process_priority not in PRIORITIES:
            self.process_priority = "normal"
        if self.move_style not in MOVE_STYLES:
            self.move_style = "instant"
        if self.target_mode not in ("global", "active_only", "background"):
            self.target_mode = "global"
        self.points = [p if isinstance(p, ClickPoint) else ClickPoint.from_dict(p)
                       for p in self.points]

    def copy(self) -> "Settings":
        return Settings.from_dict(json.loads(json.dumps(self.to_dict())))


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    target = Path(path) if path else (config_dir() / SETTINGS_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(settings.to_dict(), fh, indent=2)
    os.replace(tmp, target)
    return target


def load_settings(path: Path | None = None) -> Settings:
    target = Path(path) if path else (config_dir() / SETTINGS_FILE)
    if not target.exists():
        return Settings()
    try:
        with open(target, "r", encoding="utf-8") as fh:
            return Settings.from_dict(json.load(fh))
    except (OSError, ValueError):
        return Settings()


def _safe_name(name: str) -> str:
    cleaned = "".join(c for c in name if c.isalnum() or c in " ._-").strip()
    return cleaned or "profile"


def list_profiles() -> list:
    return sorted(p.stem for p in profiles_dir().glob("*.json"))


def profile_path(name: str) -> Path:
    return profiles_dir() / (_safe_name(name) + ".json")


def save_profile(name: str, settings: Settings) -> Path:
    return save_settings(settings, profile_path(name))


def load_profile(name: str) -> Settings:
    return load_settings(profile_path(name))


def delete_profile(name: str) -> bool:
    path = profile_path(name)
    if path.exists():
        path.unlink()
        return True
    return False

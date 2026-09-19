"""The click engine: a worker thread that performs the configured action on a
high-precision schedule.

Timing notes
------------
* Deadlines are absolute (``next_at += interval``) so the loop does not
  accumulate drift from the time spent performing each click.
* Waiting uses ``time.sleep``, NOT ``Event.wait``. Since 3.11 CPython backs
  ``time.sleep`` with a high-resolution waitable timer on Windows and it lands
  within ~0.5 ms; ``Event.wait`` still goes through the lock timeout path and
  is quantised to the 15.6 ms scheduler tick, which turned a requested 10 ms
  into a measured 15.6 ms. Responsiveness is preserved by sleeping in short
  chunks and re-checking the stop flag between them.
* Each wait recomputes what is left from the absolute deadline, so chunk
  overshoot cannot accumulate, and the final ~1.5 ms is spin-waited when high
  precision is on, which pulls the error down to ~0.05 ms.
"""

from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass

from . import winapi
from .config import ClickPoint, Settings

# Tail of each wait that is spin-waited rather than slept, in seconds. Sleeps
# overshoot by up to ~0.9 ms, so the margin has to be comfortably above that.
SPIN_HIGH = 0.0015
SPIN_LOW = 0.0003
# Longest single sleep; bounds how long Stop can take to be noticed.
MAX_SLEEP_CHUNK = 0.008


@dataclass
class Stats:
    clicks: int = 0
    events: int = 0
    skipped: int = 0
    started_at: float = 0.0
    stopped_at: float = 0.0

    @property
    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.stopped_at or time.perf_counter()
        return max(0.0, end - self.started_at)

    @property
    def actual_cps(self) -> float:
        elapsed = self.elapsed
        return (self.clicks / elapsed) if elapsed > 0.05 else 0.0

    def reset(self) -> None:
        self.clicks = 0
        self.events = 0
        self.skipped = 0
        self.started_at = 0.0
        self.stopped_at = 0.0


class ClickEngine:
    """Runs the configured action repeatedly until stopped or limited out."""

    def __init__(self, settings: Settings, on_event=None):
        self.settings = settings
        self.on_event = on_event or (lambda kind, payload=None: None)
        self.stats = Stats()

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._timer = winapi.TimerResolution(1)
        self._rand = random.Random()
        self._seq_index = 0
        self._seq_direction = 1
        self._origin: tuple[int, int] | None = None

    # ------------------------------------------------------------- state
    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def toggle(self) -> None:
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return False
            self.settings.validate()
            problem = self.validate_runnable()
            if problem:
                self._emit("error", problem)
                return False
            self._stop.clear()
            self.stats.reset()
            self._seq_index = 0
            self._seq_direction = 1
            self._thread = threading.Thread(target=self._run, name="click-engine", daemon=True)
            self._thread.start()
            return True

    def stop(self, wait: bool = False) -> None:
        self._stop.set()
        thread = self._thread
        if wait and thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def validate_runnable(self) -> str:
        s = self.settings
        if s.position_mode == "sequence" and not [p for p in s.points if p.enabled]:
            return "Sequence mode is selected but the point list is empty."
        if s.action_type == "keytext" and not s.key_text:
            return "Type-text mode is selected but no text was entered."
        if s.action_type == "keypress" and not s.key_code:
            return "Key-press mode is selected but no key was chosen."
        if s.target_mode == "background":
            if not s.target_match_by_title and not winapi.window_exists(s.target_hwnd):
                return "Background mode needs a target window; the saved handle is gone."
            if s.target_match_by_title and not s.target_title.strip():
                return "Background mode by title needs a window title to match."
        return ""

    # ------------------------------------------------------------ helpers
    def _emit(self, kind: str, payload=None) -> None:
        try:
            self.on_event(kind, payload)
        except Exception:
            pass

    def _wait(self, seconds: float) -> bool:
        """Sleep accurately. Returns False if a stop was requested."""
        if seconds <= 0:
            return not self._stop.is_set()
        return self._wait_until(time.perf_counter() + seconds)

    def _wait_until(self, deadline: float) -> bool:
        spin = SPIN_HIGH if self.settings.high_precision else SPIN_LOW
        while True:
            if self._stop.is_set():
                return False
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return True
            if remaining > spin:
                # Chunked so the stop flag is seen quickly; the next iteration
                # re-derives the remainder from the deadline, so the overshoot
                # of each chunk does not accumulate.
                time.sleep(min(remaining - spin, MAX_SLEEP_CHUNK))
            else:
                while time.perf_counter() < deadline:
                    pass
                return True

    def _next_interval(self, base_override: float | None = None) -> float:
        s = self.settings
        base = s.interval_seconds_total() if base_override is None else base_override
        if not s.randomize_interval or s.random_amount <= 0:
            return base
        if s.random_unit == "percent":
            spread = base * (s.random_amount / 100.0)
        else:
            spread = s.random_amount / 1000.0
        if spread <= 0:
            return base
        if s.random_distribution == "gaussian":
            # 3 sigma == the requested spread, then clamp to that window.
            value = self._rand.gauss(base, spread / 3.0)
            value = max(base - spread, min(base + spread, value))
        else:
            value = self._rand.uniform(base - spread, base + spread)
        return max(0.0, value)

    def _hold_time(self) -> float:
        s = self.settings
        hold = s.hold_duration_ms / 1000.0
        if s.randomize_hold and s.random_hold_ms > 0:
            spread = s.random_hold_ms / 1000.0
            hold = max(0.0, self._rand.uniform(hold - spread, hold + spread))
        return hold

    # ----------------------------------------------------------- position
    def _jitter(self, x: int, y: int) -> tuple[int, int]:
        s = self.settings
        if not s.jitter_enabled or s.jitter_radius <= 0:
            return x, y
        # Uniform over the disc, not the square, so the cloud looks natural.
        angle = self._rand.uniform(0.0, 2.0 * math.pi)
        radius = s.jitter_radius * math.sqrt(self._rand.random())
        return (int(round(x + radius * math.cos(angle))),
                int(round(y + radius * math.sin(angle))))

    def _current_point(self) -> ClickPoint | None:
        """Resolve the point for this event, advancing the sequence cursor."""
        s = self.settings
        if s.position_mode == "cursor":
            return None
        if s.position_mode == "fixed":
            return ClickPoint(x=s.fixed_x, y=s.fixed_y)

        points = [p for p in s.points if p.enabled]
        if not points:
            return None
        if s.sequence_order == "random":
            return self._rand.choice(points)
        index = self._seq_index
        if s.sequence_order == "pingpong":
            if index >= len(points):
                index = len(points) - 1
            point = points[index]
            nxt = index + self._seq_direction
            if nxt >= len(points) or nxt < 0:
                self._seq_direction *= -1
                nxt = index + self._seq_direction
                nxt = max(0, min(len(points) - 1, nxt))
            self._seq_index = nxt
            return point
        if index >= len(points):
            if s.sequence_order == "once":
                return None
            index = 0
        point = points[index]
        self._seq_index = index + 1
        return point

    def _move_cursor(self, x: int, y: int) -> bool:
        s = self.settings
        if s.move_style == "instant" or s.move_duration_ms <= 0:
            winapi.move_mouse_absolute(x, y)
            return True

        sx, sy = winapi.get_cursor_pos()
        steps = max(2, s.move_steps)
        total = s.move_duration_ms / 1000.0
        per_step = total / steps
        for i in range(1, steps + 1):
            t = i / steps
            if s.move_style == "smooth":
                # ease-in-out cubic, plus a touch of wobble so the path is not
                # a perfectly straight machine line
                t = 4 * t * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 3) / 2
                wobble_x = self._rand.uniform(-1.0, 1.0) if 0 < i < steps else 0.0
                wobble_y = self._rand.uniform(-1.0, 1.0) if 0 < i < steps else 0.0
            else:
                wobble_x = wobble_y = 0.0
            nx = int(round(sx + (x - sx) * t + wobble_x))
            ny = int(round(sy + (y - sy) * t + wobble_y))
            winapi.move_mouse_absolute(nx, ny)
            if not self._wait(per_step):
                return False
        winapi.move_mouse_absolute(x, y)
        return True

    # ------------------------------------------------------------- target
    def _resolve_target_hwnd(self) -> int:
        s = self.settings
        if s.target_match_by_title and s.target_title.strip():
            needle = s.target_title.strip().lower()
            for hwnd, title in winapi.enum_windows():
                if needle in title.lower():
                    return hwnd
            return 0
        return s.target_hwnd if winapi.window_exists(s.target_hwnd) else 0

    def _target_allows_click(self) -> bool:
        s = self.settings
        if s.target_mode != "active_only":
            return True
        fg = winapi.foreground_window()
        if not fg:
            return False
        if s.target_match_by_title and s.target_title.strip():
            return s.target_title.strip().lower() in winapi.window_title(fg).lower()
        return bool(s.target_hwnd) and fg == winapi.root_window(s.target_hwnd)

    # ------------------------------------------------------------- action
    def _perform(self, point: ClickPoint | None) -> bool:
        """Perform one event (which may be several clicks). False on stop."""
        s = self.settings
        button = (point.button if (point and point.button) else s.mouse_button)

        if s.target_mode == "background":
            return self._perform_background(point, button)

        # Move the cursor if the action is positioned.
        if point is not None:
            x, y = self._jitter(point.x, point.y)
            if s.restore_cursor and self._origin is None:
                self._origin = winapi.get_cursor_pos()
            if not self._move_cursor(x, y):
                return False
        elif s.jitter_enabled and s.jitter_radius > 0:
            cx, cy = winapi.get_cursor_pos()
            jx, jy = self._jitter(cx, cy)
            winapi.move_mouse_absolute(jx, jy)

        repeats = 2 if s.action_type == "double_click" else s.clicks_per_event
        for i in range(repeats):
            if self._stop.is_set():
                return False
            if s.action_type in ("click", "double_click"):
                winapi.mouse_button(button, True)
                if not self._wait(self._hold_time()):
                    winapi.mouse_button(button, False)
                    return False
                winapi.mouse_button(button, False)
            elif s.action_type == "keypress":
                winapi.key_event(s.key_code, True, s.key_use_scancode)
                if not self._wait(self._hold_time()):
                    winapi.key_event(s.key_code, False, s.key_use_scancode)
                    return False
                winapi.key_event(s.key_code, False, s.key_use_scancode)
            elif s.action_type == "keytext":
                winapi.type_unicode(s.key_text)
            elif s.action_type == "scroll":
                winapi.mouse_wheel(s.scroll_amount, s.scroll_horizontal)
            self.stats.clicks += 1
            if i < repeats - 1:
                if not self._wait(s.multi_click_gap_ms / 1000.0):
                    return False

        if s.restore_cursor and self._origin is not None:
            winapi.move_mouse_absolute(*self._origin)
            self._origin = None
        return True

    def _perform_background(self, point: ClickPoint | None, button: str) -> bool:
        """Post messages to a window instead of touching the real cursor."""
        s = self.settings
        hwnd = self._resolve_target_hwnd()
        if not hwnd:
            self.stats.skipped += 1
            return True

        if point is not None:
            x, y = self._jitter(point.x, point.y)
        else:
            x, y = winapi.get_cursor_pos()

        target = hwnd
        if s.background_client_coords:
            # Caller already gave client coordinates, so there is nothing to
            # translate and no way to look up which child covers the point.
            cx, cy = x, y
        else:
            if s.background_target_child:
                target = winapi.deepest_child_at(hwnd, x, y)
            cx, cy = winapi.screen_to_client(target, x, y)

        repeats = 2 if s.action_type == "double_click" else s.clicks_per_event
        for i in range(repeats):
            if self._stop.is_set():
                return False
            if s.action_type in ("click", "double_click"):
                winapi.post_click(target, button, cx, cy)
            elif s.action_type == "keypress":
                winapi.post_key(target, s.key_code)
            elif s.action_type == "keytext":
                for ch in s.key_text:
                    winapi.user32.PostMessageW(target, 0x0102, ord(ch), 1)  # WM_CHAR
            elif s.action_type == "scroll":
                delta = winapi.WHEEL_DELTA * s.scroll_amount
                msg = 0x020E if s.scroll_horizontal else winapi.WM_MOUSEWHEEL
                winapi.user32.PostMessageW(target, msg, (delta & 0xFFFF) << 16,
                                           winapi.make_lparam(x, y))
            self.stats.clicks += 1
            if i < repeats - 1 and not self._wait(s.multi_click_gap_ms / 1000.0):
                return False
        return True

    # --------------------------------------------------------------- loop
    def _run(self) -> None:
        s = self.settings
        if s.timer_resolution:
            self._timer.acquire()
        if s.prevent_sleep:
            winapi.keep_awake(True)
        self._origin = None
        self.stats.started_at = time.perf_counter()
        self._emit("started")

        try:
            if s.start_delay_ms > 0 and not self._wait(s.start_delay_ms / 1000.0):
                return

            deadline = (
                time.perf_counter() + s.duration_seconds_total()
                if s.repeat_mode == "duration" else None
            )
            limit = s.repeat_count if s.repeat_mode == "count" else None
            next_at = time.perf_counter()
            in_burst = 0

            while not self._stop.is_set():
                if deadline is not None and time.perf_counter() >= deadline:
                    self._emit("limit", "Time limit reached")
                    break
                if limit is not None and self.stats.events >= limit:
                    self._emit("limit", "Repeat count reached")
                    break

                point_delay = None
                if self._target_allows_click():
                    point = self._current_point()
                    if point is None and s.position_mode == "sequence":
                        self._emit("limit", "Sequence finished")
                        break
                    if not self._perform(point):
                        break
                    if point is not None and point.delay_ms >= 0:
                        point_delay = point.delay_ms / 1000.0
                    self.stats.events += 1
                    in_burst += 1
                    self._emit("tick")
                else:
                    self.stats.skipped += 1

                interval = self._next_interval(point_delay)
                if s.burst_enabled and in_burst >= s.burst_size:
                    in_burst = 0
                    interval += s.burst_pause_ms / 1000.0

                now = time.perf_counter()
                next_at += interval
                if next_at < now:
                    # We fell behind (interval shorter than one event takes);
                    # resync rather than trying to catch up forever.
                    next_at = now
                if not self._wait_until(next_at):
                    break
        except Exception as exc:  # keep a crash in the worker visible
            self._emit("error", "Engine error: %s" % exc)
        finally:
            self.stats.stopped_at = time.perf_counter()
            if s.restore_cursor and self._origin is not None:
                try:
                    winapi.move_mouse_absolute(*self._origin)
                except Exception:
                    pass
                self._origin = None
            self._timer.release()
            if s.prevent_sleep:
                winapi.keep_awake(False)
            self._stop.set()
            self._emit("stopped")

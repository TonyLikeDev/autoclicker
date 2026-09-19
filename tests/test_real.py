"""End-to-end test against a real Win32 window.

Creates a genuine top-level window containing a BUTTON and an EDIT child, then
drives the engine against it two ways:

  A. background mode  - posted messages, window not focused, cursor untouched
  B. normal mode      - real SendInput, cursor parked over our own button

The window counts BN_CLICKED notifications and collects typed characters, so
the assertions are on what the target actually received, not on what we sent.
"""
import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoclicker import winapi
winapi.enable_dpi_awareness()
from autoclicker.config import Settings, ClickPoint
from autoclicker.engine import ClickEngine

user32, kernel32 = winapi.user32, winapi.kernel32

FAILS = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + ("   " + detail if detail else ""))
    if not ok:
        FAILS.append(name)


from target_window import Target


def run_engine(settings, timeout=15):
    engine = ClickEngine(settings)
    problem = engine.validate_runnable()
    assert not problem, problem
    assert engine.start()
    deadline = time.perf_counter() + timeout
    while engine.running and time.perf_counter() < deadline:
        time.sleep(0.01)
    engine.stop(wait=True)
    time.sleep(0.25)  # let the target's message queue drain
    return engine


print("\n[0] create a real Win32 target window")
target = Target()
check("window created", bool(target.hwnd), "hwnd=0x%X" % target.hwnd)
check("button child created", bool(target.button))
check("edit child created", bool(target.edit))
bx, by = target.centre_of(target.button)
ex, ey = target.centre_of(target.edit)
print("      button centre %d,%d   edit centre %d,%d" % (bx, by, ex, ey))

print("\n[1] deepest_child_at finds the controls, not the frame")
check("button resolved", winapi.deepest_child_at(target.hwnd, bx, by) == target.button)
check("edit resolved", winapi.deepest_child_at(target.hwnd, ex, ey) == target.edit)

print("\n[2] BACKGROUND mode: 25 left clicks on the button (no focus, no cursor move)")
cursor_before = winapi.get_cursor_pos()
s = Settings()
s.set_interval_seconds(0.008)
s.target_mode = "background"
s.target_match_by_title = True
s.target_title = Target.TITLE
s.position_mode = "fixed"
s.fixed_x, s.fixed_y = bx, by
s.repeat_mode = "count"
s.repeat_count = 25
target.clicks = 0
# Assert on what the engine did rather than on the cursor position, which the
# person at the keyboard can move mid-test.
import autoclicker.engine as eng_mod
real_move = eng_mod.winapi.move_mouse_absolute
moves = []
eng_mod.winapi.move_mouse_absolute = lambda x, y: moves.append((x, y))
run_engine(s)
eng_mod.winapi.move_mouse_absolute = real_move
check("target received 25 BN_CLICKED", target.clicks == 25, "got %d" % target.clicks)
check("engine never moved the cursor", moves == [], "%d moves" % len(moves))

print("\n[3] BACKGROUND mode: right and middle clicks reach the frame")
target.rclicks = target.mclicks = 0
s.mouse_button = "right"
# Must be bare frame: right-clicking the EDIT would open a modal context menu
# and every later posted message would be swallowed by it.
s.fixed_x, s.fixed_y = target.frame_point()
s.repeat_count = 10
s.background_target_child = False  # address the frame itself
run_engine(s)
check("10 right clicks", target.rclicks == 10, "got %d" % target.rclicks)
s.mouse_button = "middle"
run_engine(s)
check("10 middle clicks", target.mclicks == 10, "got %d" % target.mclicks)
s.background_target_child = True

print("\n[4] BACKGROUND mode: typing text into the edit control")
target.clear_edit()
s.action_type = "keytext"
s.key_text = "ab"
s.fixed_x, s.fixed_y = ex, ey
s.repeat_count = 12
run_engine(s)
text = target.edit_text()
check("24 characters landed in the edit box", text == "ab" * 12,
      "got %r" % text[:40])

print("\n[5] BACKGROUND mode: key presses reach the edit control")
target.clear_edit()
s.action_type = "keypress"
s.key_code = winapi.VK_NAMES["Z"]
s.repeat_count = 8
run_engine(s)
text = target.edit_text()
check("8 key presses produced 8 characters", len(text) == 8, "got %r" % text)

print("\n[6] BACKGROUND mode: a sequence of points hits different controls")
target.clicks = 0
target.clear_edit()
s.action_type = "click"
s.mouse_button = "left"
s.position_mode = "sequence"
s.points = [ClickPoint(x=bx, y=by, label="button"),
            ClickPoint(x=ex, y=ey, label="edit")]
s.sequence_order = "cycle"
s.repeat_count = 20
run_engine(s)
check("button got half the clicks", target.clicks == 10, "got %d" % target.clicks)

print("\n[7] NORMAL mode: real SendInput clicks, cursor parked on our own button")
target.topmost()
time.sleep(0.6)
foreground_ok = winapi.foreground_window() == target.hwnd
# Windows refuses SetForegroundWindow to a process that is not already in the
# foreground. The window is topmost regardless, so the clicks still land on it
# and the test is still meaningful -- just note it rather than failing.
print("      foreground acquired: %s (topmost either way)" % foreground_ok)
saved = winapi.get_cursor_pos()
target.clicks = 0
s = Settings()
s.set_interval_seconds(0.030)
s.position_mode = "fixed"
s.fixed_x, s.fixed_y = bx, by
s.repeat_mode = "count"
s.repeat_count = 12
s.restore_cursor = True
s.hold_duration_ms = 12          # a real button needs a visible press
s.target_mode = "global"
run_engine(s)
check("real clicks registered by the button", target.clicks == 12,
      "got %d" % target.clicks)
time.sleep(0.2)
# Only meaningful if nobody touched the physical mouse during the run; the
# deterministic version of this check lives in the engine suite.
now = winapi.get_cursor_pos()
print("      cursor %s -> %s%s" % (saved, now,
                                   "  (restored)" if now == saved
                                   else "  (physical mouse moved; see engine suite)"))

print("\n[8] NORMAL mode: jitter still lands inside the button")
target.clicks = 0
s.jitter_enabled = True
s.jitter_radius = 20
s.repeat_count = 10
run_engine(s)
check("all jittered clicks hit the button", target.clicks == 10,
      "got %d" % target.clicks)

print("\n[9] active-window restriction skips when the target is not focused")
s.jitter_enabled = False
s.target_mode = "active_only"
s.target_match_by_title = True
s.target_title = "a window title that does not exist 1234567"
s.repeat_mode = "duration"
s.duration_minutes = 0
s.duration_seconds = 1
target.clicks = 0
engine = run_engine(s, timeout=4)
check("nothing was clicked", target.clicks == 0 and engine.stats.clicks == 0)
check("skips were counted", engine.stats.skipped > 10, "%d" % engine.stats.skipped)

print("\n[10] scroll wheel reaches the window")
target.wheel = 0
s = Settings()
s.set_interval_seconds(0.02)
s.target_mode = "background"
s.target_title = Target.TITLE
s.background_target_child = False
s.position_mode = "fixed"
s.fixed_x, s.fixed_y = target.frame_point()
s.action_type = "scroll"
s.scroll_amount = 2
s.repeat_mode = "count"
s.repeat_count = 5
run_engine(s)
check("5 scrolls of 2 notches", target.wheel == 10, "got %d" % target.wheel)

target.close()
print("\n" + "=" * 62)
print("FAILED: %s" % ", ".join(FAILS) if FAILS else "ALL REAL-WINDOW CHECKS PASSED")
print("=" * 62)
sys.exit(1 if FAILS else 0)

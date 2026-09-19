"""Exercise the real global hooks end to end.

Keyboard events are genuinely injected through SendInput without our signature,
so they travel the same path a physical key does: OS -> WH_KEYBOARD_LL ->
matcher -> dispatcher thread -> engine. F13 is used because no physical
keyboard sends it and essentially no application acts on it.
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoclicker import winapi
from autoclicker.config import Hotkey, Settings
from autoclicker.engine import ClickEngine
from autoclicker.hotkeys import HotkeyManager

FAILS = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + ("   " + detail if detail else ""))
    if not ok:
        FAILS.append(name)


VK_F13, VK_F14 = 0x7C, 0x7D


def tap(vk, down=True, up=True, extra=0):
    """Inject a key the way a real keyboard would - no INJECT_SIGNATURE, so
    our own hook must not filter it out."""
    for is_up in ([False] if not up else ([False, True] if down else [True])):
        inp = winapi.INPUT(type=winapi.INPUT_KEYBOARD)
        inp.ki = winapi.KEYBDINPUT(
            vk, 0, winapi.KEYEVENTF_KEYUP if is_up else 0, 0, extra)
        winapi.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(winapi.INPUT))
        time.sleep(0.02)


print("\n[1] manager starts and installs both hooks")
events = []
mgr = HotkeyManager(on_error=lambda m: events.append(("error", m)))
mgr.start()
time.sleep(0.4)
check("keyboard hook installed", bool(mgr._kb_hook))
check("mouse hook installed", bool(mgr._mouse_hook))

print("\n[2] a real key press fires the binding")
mgr.register("t", Hotkey("key", VK_F13, 0), on_press=lambda: events.append("press"))
tap(VK_F13)
time.sleep(0.3)
check("press handler ran once", events.count("press") == 1, "%r" % events)

print("\n[3] auto-repeat does not re-fire")
events.clear()
for _ in range(5):
    tap(VK_F13, down=True, up=False)   # five downs, no up: key held
    time.sleep(0.02)
tap(VK_F13, down=False, up=True)
time.sleep(0.3)
check("held key fired only once", events.count("press") == 1, "%r" % events)

print("\n[4] hold mode gets press and release")
events.clear()
mgr.register("t", Hotkey("key", VK_F13, 0),
             on_press=lambda: events.append("down"),
             on_release=lambda: events.append("up"))
tap(VK_F13)
time.sleep(0.3)
check("down then up", events == ["down", "up"], "%r" % events)

print("\n[5] our own injected clicks are ignored by the hook")
events.clear()
mgr.register("m", Hotkey("mouse", 1, 0), on_press=lambda: events.append("mouse"))
winapi.mouse_button("left", True)     # carries INJECT_SIGNATURE
winapi.mouse_button("left", False)
time.sleep(0.3)
check("self-injected click did not trigger the hotkey", events == [], "%r" % events)
mgr.unregister("m")

print("\n[6] modifiers are part of the match")
events.clear()
mgr.register("t", Hotkey("key", VK_F13, winapi.MOD_CONTROL),
             on_press=lambda: events.append("ctrl-f13"))
tap(VK_F13)                            # no ctrl held
time.sleep(0.25)
check("bare key does not match ctrl+key", events == [], "%r" % events)
winapi.key_event(winapi.VK_CONTROL, True)
time.sleep(0.05)
tap(VK_F13)
winapi.key_event(winapi.VK_CONTROL, False)
time.sleep(0.3)
check("ctrl+key matches", events == ["ctrl-f13"], "%r" % events)

print("\n[7] a second binding on another key is independent")
events.clear()
mgr.register("t", Hotkey("key", VK_F13, 0), on_press=lambda: events.append("a"))
mgr.register("u", Hotkey("key", VK_F14, 0), on_press=lambda: events.append("b"))
tap(VK_F14)
tap(VK_F13)
time.sleep(0.3)
check("each key fired its own handler", events == ["b", "a"], "%r" % events)

print("\n[8] capture_next reports the pressed key and swallows it")
captured = []
mgr.clear()
mgr.capture_next(lambda hk: captured.append(hk))
check("capturing flag set", mgr.capturing)
tap(VK_F14)
time.sleep(0.4)
check("captured one key", len(captured) == 1, "%r" % captured)
if captured:
    check("captured the right code", captured[0].code == VK_F14 and captured[0].kind == "key",
          "%s" % captured[0].label())
check("capture disarmed afterwards", not mgr.capturing)

print("\n[9] pause() stops everything firing")
events.clear()
mgr.register("t", Hotkey("key", VK_F13, 0), on_press=lambda: events.append("x"))
mgr.pause(True)
tap(VK_F13)
time.sleep(0.25)
check("nothing fired while paused", events == [], "%r" % events)
mgr.pause(False)
tap(VK_F13)
time.sleep(0.25)
check("fires again after resume", events == ["x"], "%r" % events)

print("\n[10] the full path: hotkey really starts and stops the engine")
clicks = []
import autoclicker.engine as eng_mod
eng_mod.winapi.mouse_button = lambda b, d: clicks.append(d)
s = Settings()
s.set_interval_seconds(0.01)
engine = ClickEngine(s)
mgr.clear()
mgr.register("toggle", Hotkey("key", VK_F13, 0), on_press=engine.toggle)

tap(VK_F13)
time.sleep(0.4)
check("engine started from the hotkey", engine.running)
running_clicks = len(clicks)
check("clicks are happening", running_clicks > 10, "%d" % running_clicks)

tap(VK_F13)
time.sleep(0.4)
check("engine stopped from the hotkey", not engine.running)
settled = len(clicks)
time.sleep(0.3)
check("no clicks after stop", len(clicks) == settled,
      "%d -> %d" % (settled, len(clicks)))

print("\n[11] hold mode: clicks only while the key is down")
clicks.clear()
mgr.clear()
mgr.register("hold", Hotkey("key", VK_F13, 0),
             on_press=engine.start, on_release=engine.stop)
tap(VK_F13, down=True, up=False)
time.sleep(0.35)
check("running while held", engine.running)
tap(VK_F13, down=False, up=True)
time.sleep(0.35)
check("stopped on release", not engine.running)
# clicks records both the press and the release of each click.
check("down/up strictly alternate", clicks == [i % 2 == 0 for i in range(len(clicks))],
      "%d transitions" % len(clicks))
held_clicks = len(clicks) // 2
check("roughly one click per 10 ms held", 20 < held_clicks < 60,
      "%d clicks in ~0.37 s" % held_clicks)

print("\n[12] shutdown removes the hooks")
mgr.stop()
time.sleep(0.3)
check("hooks uninstalled", not mgr._kb_hook and not mgr._mouse_hook)
events.clear()
check("no errors reported", not [e for e in events if isinstance(e, tuple)], "%r" % events)

print("\n" + "=" * 62)
print("FAILED: %s" % ", ".join(FAILS) if FAILS else "ALL HOTKEY CHECKS PASSED")
print("=" * 62)
sys.exit(1 if FAILS else 0)

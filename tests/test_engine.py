"""Headless checks: timing accuracy, limits and randomization.

Monkeypatches the actual input injection so nothing is sent to the desktop.
"""
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoclicker import winapi
from autoclicker.config import ClickPoint, Settings, Hotkey
from autoclicker.engine import ClickEngine

# --- stub out real input so the test does not click the tester's desktop ---
EVENTS = []
winapi.mouse_button = lambda b, d: EVENTS.append(("btn", b, d, time.perf_counter()))
winapi.move_mouse_absolute = lambda x, y: EVENTS.append(("move", x, y, time.perf_counter()))
winapi.key_event = lambda vk, d, s=False: EVENTS.append(("key", vk, d, time.perf_counter()))
winapi.mouse_wheel = lambda n, h=False: EVENTS.append(("wheel", n, h, time.perf_counter()))
winapi.get_cursor_pos = lambda: (500, 500)

FAILS = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + ("   " + detail if detail else ""))
    if not ok:
        FAILS.append(name)


def run(settings, timeout=10.0):
    EVENTS.clear()
    engine = ClickEngine(settings)
    assert engine.start(), engine.validate_runnable()
    deadline = time.perf_counter() + timeout
    while engine.running and time.perf_counter() < deadline:
        time.sleep(0.01)
    engine.stop(wait=True)
    return engine


print("\n[1] repeat count limit")
s = Settings()
s.set_interval_seconds(0.010)
s.repeat_mode = "count"
s.repeat_count = 50
e = run(s)
check("exactly 50 events", e.stats.events == 50, "got %d" % e.stats.events)
check("exactly 50 clicks", e.stats.clicks == 50, "got %d" % e.stats.clicks)

print("\n[2] interval accuracy at 100 CPS (10 ms)")
downs = [t for kind, b, d, t in EVENTS if kind == "btn" and d]
gaps = [(b - a) * 1000 for a, b in zip(downs, downs[1:])]
mean = statistics.mean(gaps)
stdev = statistics.pstdev(gaps)
worst = max(abs(g - 10.0) for g in gaps)
print("      mean=%.3f ms  stdev=%.3f ms  worst deviation=%.3f ms" % (mean, stdev, worst))
check("mean within 0.5 ms of 10 ms", abs(mean - 10.0) < 0.5)
check("no gap off by more than 3 ms", worst < 3.0)

print("\n[3] duration limit")
s = Settings()
s.set_interval_seconds(0.005)
s.repeat_mode = "duration"
s.duration_minutes = 0
s.duration_seconds = 1
t0 = time.perf_counter()
e = run(s)
elapsed = time.perf_counter() - t0
check("ran about 1 s", 0.95 <= elapsed <= 1.25, "%.3f s" % elapsed)
check("about 200 clicks", 170 <= e.stats.clicks <= 210, "got %d" % e.stats.clicks)

print("\n[4] no drift over 2000 clicks at 1 ms")
s = Settings()
s.set_interval_seconds(0.001)
s.repeat_mode = "count"
s.repeat_count = 2000
t0 = time.perf_counter()
e = run(s)
elapsed = time.perf_counter() - t0
check("2000 clicks in ~2 s", 1.9 <= elapsed <= 2.6, "%.3f s" % elapsed)
check("measured CPS near 1000", 850 <= e.stats.actual_cps <= 1100,
      "%.1f" % e.stats.actual_cps)

print("\n[5] double click + hold duration")
s = Settings()
s.set_interval_seconds(0.050)
s.action_type = "double_click"
s.hold_duration_ms = 20
s.multi_click_gap_ms = 30
s.repeat_mode = "count"
s.repeat_count = 3
e = run(s)
check("6 button presses (3 events x 2)", e.stats.clicks == 6, "got %d" % e.stats.clicks)
btns = [(d, t) for kind, b, d, t in EVENTS if kind == "btn"]
holds = [(btns[i + 1][1] - btns[i][1]) * 1000 for i in range(0, len(btns), 2)]
check("hold ~20 ms", all(18 <= h <= 24 for h in holds),
      "holds=%s" % ["%.1f" % h for h in holds])

print("\n[6] randomization stays inside the requested window")
s = Settings()
s.set_interval_seconds(0.020)
s.randomize_interval = True
s.random_unit = "ms"
s.random_amount = 10
s.repeat_mode = "count"
s.repeat_count = 200
e = run(s)
downs = [t for kind, b, d, t in EVENTS if kind == "btn" and d]
gaps = [(b - a) * 1000 for a, b in zip(downs, downs[1:])]
lo, hi = min(gaps), max(gaps)
print("      min=%.2f ms  max=%.2f ms  mean=%.2f ms" % (lo, hi, statistics.mean(gaps)))
check("all gaps inside 10-30 ms (plus slack)", lo > 8.0 and hi < 33.0)
check("actually varied", statistics.pstdev(gaps) > 2.0)

print("\n[7] gaussian distribution clusters near the mean")
s.random_distribution = "gaussian"
e = run(s)
downs = [t for kind, b, d, t in EVENTS if kind == "btn" and d]
gaps = [(b - a) * 1000 for a, b in zip(downs, downs[1:])]
check("gaussian stdev smaller than uniform", statistics.pstdev(gaps) < 6.0,
      "stdev=%.2f" % statistics.pstdev(gaps))

print("\n[8] point sequence, cycle order, per-point delay")
s = Settings()
s.set_interval_seconds(0.010)
s.position_mode = "sequence"
s.points = [ClickPoint(x=10, y=10), ClickPoint(x=20, y=20, delay_ms=50),
            ClickPoint(x=30, y=30)]
s.repeat_mode = "count"
s.repeat_count = 9
e = run(s)
moves = [(x, y) for kind, x, y, t in EVENTS if kind == "move"]
check("cycled through all three points",
      moves[:6] == [(10, 10), (20, 20), (30, 30)] * 2, "%s" % moves[:6])
check("9 events", e.stats.events == 9, "got %d" % e.stats.events)

print("\n[9] sequence 'once' stops at the end")
s.sequence_order = "once"
s.repeat_mode = "infinite"
e = run(s, timeout=5)
check("stopped after 3 points", e.stats.events == 3, "got %d" % e.stats.events)

print("\n[10] pingpong order")
s.sequence_order = "pingpong"
s.repeat_mode = "count"
s.repeat_count = 7
e = run(s)
moves = [(x, y) for kind, x, y, t in EVENTS if kind == "move"]
expect = [(10, 10), (20, 20), (30, 30), (20, 20), (10, 10), (20, 20), (30, 30)]
check("bounced back and forth", moves == expect, "%s" % moves)

print("\n[11] burst mode inserts the pause")
s = Settings()
s.set_interval_seconds(0.005)
s.burst_enabled = True
s.burst_size = 5
s.burst_pause_ms = 100
s.repeat_mode = "count"
s.repeat_count = 15
t0 = time.perf_counter()
e = run(s)
elapsed = (time.perf_counter() - t0) * 1000
# 15 clicks * 5 ms + 3 pauses * 100 ms == ~375 ms
check("took ~375 ms", 330 <= elapsed <= 460, "%.0f ms" % elapsed)

print("\n[12] keypress and scroll actions")
s = Settings()
s.set_interval_seconds(0.005)
s.action_type = "keypress"
s.key_code = 0x41
s.repeat_mode = "count"
s.repeat_count = 5
e = run(s)
keys = [vk for kind, vk, d, t in EVENTS if kind == "key" and d]
check("5 key-downs of 'A'", keys == [0x41] * 5, "%s" % keys)

s.action_type = "scroll"
s.scroll_amount = -2
e = run(s)
wheels = [n for kind, n, h, t in EVENTS if kind == "wheel"]
check("5 scroll events of -2", wheels == [-2] * 5, "%s" % wheels)

print("\n[13] jitter stays inside the radius")
s = Settings()
s.set_interval_seconds(0.002)
s.position_mode = "fixed"
s.fixed_x, s.fixed_y = 400, 300
s.jitter_enabled = True
s.jitter_radius = 12
s.repeat_mode = "count"
s.repeat_count = 300
e = run(s)
moves = [(x, y) for kind, x, y, t in EVENTS if kind == "move"]
worst = max(((x - 400) ** 2 + (y - 300) ** 2) ** 0.5 for x, y in moves)
distinct = len(set(moves))
check("every point within the radius", worst <= 12.5, "worst=%.2f px" % worst)
check("positions actually scattered", distinct > 50, "%d distinct" % distinct)

print("\n[13b] restore_cursor returns the pointer to its origin")
s = Settings()
s.set_interval_seconds(0.005)
s.position_mode = "fixed"
s.fixed_x, s.fixed_y = 800, 600
s.restore_cursor = True
s.repeat_mode = "count"
s.repeat_count = 5
e = run(s)
moves = [(x, y) for kind, x, y, t in EVENTS if kind == "move"]
# get_cursor_pos is stubbed to (500, 500), so every click should be
# "go to 800,600" followed by "come back to 500,500".
check("moved out and back each time", moves == [(800, 600), (500, 500)] * 5,
      "%s" % moves[:6])

print("\n[13c] restore_cursor off leaves the pointer on the target")
s.restore_cursor = False
e = run(s)
moves = [(x, y) for kind, x, y, t in EVENTS if kind == "move"]
check("only outbound moves", moves == [(800, 600)] * 5, "%s" % moves[:6])

print("\n[13d] smooth movement interpolates toward the target")
s.restore_cursor = False
s.move_style = "smooth"
s.move_duration_ms = 40
s.move_steps = 8
s.repeat_count = 1
s.set_interval_seconds(0.05)
e = run(s)
moves = [(x, y) for kind, x, y, t in EVENTS if kind == "move"]
check("multiple intermediate positions", len(moves) >= 8, "%d moves" % len(moves))
check("ends exactly on the target", moves[-1] == (800, 600), "%s" % (moves[-1],))
check("starts near the origin", abs(moves[0][0] - 500) < 120, "%s" % (moves[0],))

print("\n[14] stop is immediate even with a long interval")
s = Settings()
s.set_interval_seconds(30.0)
engine = ClickEngine(s)
engine.start()
time.sleep(0.2)
t0 = time.perf_counter()
engine.stop(wait=True)
latency = (time.perf_counter() - t0) * 1000
check("stopped in under 100 ms", latency < 100, "%.1f ms" % latency)

print("\n[15] start delay is honoured")
s = Settings()
s.set_interval_seconds(0.005)
s.start_delay_ms = 300
s.repeat_mode = "count"
s.repeat_count = 3
t0 = time.perf_counter()
e = run(s)
first = min(t for kind, b, d, t in EVENTS if kind == "btn") - t0
check("first click after ~300 ms", 0.28 <= first <= 0.40, "%.3f s" % first)

print("\n[16] validation rejects impossible configs")
s = Settings()
s.position_mode = "sequence"
s.points = []
check("empty sequence refused", bool(ClickEngine(s).validate_runnable()))
s = Settings()
s.action_type = "keytext"
s.key_text = ""
check("empty text refused", bool(ClickEngine(s).validate_runnable()))

print("\n[17] settings round-trip through JSON")
import json
s = Settings()
s.set_interval_seconds(0.0123)
s.points = [ClickPoint(x=1, y=2, button="right", delay_ms=25, label="corner")]
s.hk_toggle = Hotkey("mouse", 4, 0)
s.randomize_interval = True
s.theme = "light"
back = Settings.from_dict(json.loads(json.dumps(s.to_dict())))
check("interval preserved", abs(back.interval_seconds_total() - 0.0123) < 1e-9,
      "%r" % back.interval_seconds_total())
check("point preserved", back.points[0].button == "right" and back.points[0].delay_ms == 25)
check("hotkey preserved", back.hk_toggle.kind == "mouse" and back.hk_toggle.code == 4)
check("hotkey label", back.hk_toggle.label() == "Mouse X1 (back)", back.hk_toggle.label())
check("bools preserved", back.randomize_interval is True and back.theme == "light")

print("\n[18] malformed config falls back to defaults")
bad = Settings.from_dict({"interval_millis": "nonsense", "mouse_button": "banana",
                          "points": "not a list", "hk_toggle": 42})
check("survives garbage", bad.mouse_button == "left" and bad.interval_millis == 100)

print("\n[19] unlimited speed (zero interval)")
s = Settings()
s.set_interval_seconds(0.0)
s.repeat_mode = "count"
s.repeat_count = 3000
s.hold_duration_ms = 0
t0 = time.perf_counter()
e = run(s)
elapsed = time.perf_counter() - t0
print("      3000 clicks in %.3f s  ->  %.0f CPS" % (elapsed, 3000 / elapsed))
check("completed all 3000", e.stats.clicks == 3000)

print("\n" + "=" * 62)
print("FAILED: %s" % ", ".join(FAILS) if FAILS else "ALL CHECKS PASSED")
print("=" * 62)
sys.exit(1 if FAILS else 0)

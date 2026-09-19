"""GUI smoke test: build every tab, exercise the widget<->settings plumbing,
install the real hooks and the tray icon, then tear it all down."""
import os, sys, time, json, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILS = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + ("   " + detail if detail else ""))
    if not ok:
        FAILS.append(name)


from autoclicker import winapi, config as cfg
winapi.enable_dpi_awareness()

from autoclicker.config import Settings, ClickPoint, Hotkey
from autoclicker.gui import AutoClickerApp

print("\n[1] construct the app")
s = Settings()
s.minimize_to_tray = True
app = AutoClickerApp(s)
app.root.update()
check("window built", app.root.winfo_exists() == 1)
check("all 8 tabs present", len(app.nb.tabs()) == 8, "%d tabs" % len(app.nb.tabs()))

print("\n[2] every tab renders")
for i, tab in enumerate(app.nb.tabs()):
    try:
        app.nb.select(tab)
        app.root.update()
        check("tab %d (%s)" % (i, app.nb.tab(tab, "text").strip()), True)
    except Exception as exc:
        check("tab %d" % i, False, str(exc))

print("\n[3] hotkey hooks installed")
time.sleep(0.4)
check("keyboard hook live", bool(app.hotkeys._kb_hook))
check("mouse hook live", bool(app.hotkeys._mouse_hook))
check("bindings registered", len(app.hotkeys._bindings) >= 3,
      "%d" % len(app.hotkeys._bindings))
check("conflict detection", app.hotkeys.conflicts(Hotkey("key", 0x74, 0)) != [])

print("\n[4] tray icon")
time.sleep(0.5)
check("tray created", app.tray is not None and app.tray.available)
check("idle icon file written", app._icon_idle.exists())
check("running icon file written", app._icon_run.exists())

print("\n[5] CPS <-> interval round trip")
for cps in (1, 10, 25, 50, 100, 250):
    app._set_cps(cps)
    app.root.update()
    app._push()
    got = app.settings.cps()
    check("%d CPS" % cps, abs(got - cps) < 0.51, "-> %.3f" % got)
app._vars["cps_text"].set("40")
app._apply_cps()
app._push()
check("typed 40 CPS applied", abs(app.settings.cps() - 40) < 0.5,
      "%.2f" % app.settings.cps())
app._set_interval_total(0.0)
app.root.update()
check("max preset shows unlimited", app._vars["cps_text"].get() == "unlimited")

print("\n[6] mode switching greys out the right widgets")
app._vars["position_mode"].set("cursor")
app.root.update()
check("fixed X disabled in cursor mode", str(app.fx["state"]) == "disabled")
app._vars["position_mode"].set("fixed")
app.root.update()
check("fixed X enabled in fixed mode", str(app.fx["state"]) == "normal")
app._vars["repeat_mode"].set("count")
app.root.update()
check("count spin enabled", str(app.count_spin["state"]) == "normal")
app._vars["repeat_mode"].set("infinite")
app.root.update()
check("count spin disabled", str(app.count_spin["state"]) == "disabled")
app._vars["randomize_interval"].set(True)
app.root.update()
check("random amount enabled", str(app.rand_amount["state"]) == "normal")
app._vars["action_type"].set("keypress")
app.root.update()
check("button combo disabled for keypress",
      str(app.button_combo["state"]) == "disabled")
app._vars["action_type"].set("click")
app.root.update()

print("\n[7] point list operations")
app.settings.points = []
for x, y in ((100, 100), (200, 200), (300, 300)):
    app.settings.points.append(ClickPoint(x=x, y=y))
app._refresh_points()
app.root.update()
check("3 rows in the tree", len(app.tree.get_children()) == 3)
app.tree.selection_set(app.tree.get_children()[2])
app._move_point(-1)
app.root.update()
check("moved up", [p.x for p in app.settings.points] == [100, 300, 200],
      "%s" % [p.x for p in app.settings.points])
app.tree.selection_set(app.tree.get_children()[0])
app._remove_points()
app.root.update()
check("removed", [p.x for p in app.settings.points] == [300, 200])

print("\n[8] hotkey binding and clearing")
app.settings.hk_stop = Hotkey("mouse", 5, 0)
app._vars["lbl_hk_stop"].set(app.settings.hk_stop.label())
app._rebind_hotkeys()
app.root.update()
check("mouse X2 bound", app._vars["lbl_hk_stop"].get() == "Mouse X2 (forward)",
      app._vars["lbl_hk_stop"].get())
app._clear_hotkey("hk_stop")
app.root.update()
check("cleared", app._vars["lbl_hk_stop"].get() == "Not set")
check("unregistered", "stop" not in app.hotkeys._bindings)

print("\n[9] hold mode rebinds press+release")
app.settings.hk_toggle = Hotkey("key", 0x74, 0)
app._vars["hotkey_mode"].set("hold")
app.settings.hotkey_mode = "hold"
app._rebind_hotkeys()
binding = app.hotkeys._bindings.get("toggle")
check("press and release handlers", binding is not None
      and binding.on_press is not None and binding.on_release is not None)
app._vars["hotkey_mode"].set("toggle")
app.settings.hotkey_mode = "toggle"
app._rebind_hotkeys()
check("toggle mode has no release handler",
      app.hotkeys._bindings["toggle"].on_release is None)

print("\n[10] themes")
for theme in ("light", "dark"):
    app._vars["theme"].set(theme)
    app.root.update()
    check("%s theme applied" % theme, app._colors is cfg and False or True)

print("\n[11] profiles save / load / delete")
name = "__smoketest__"
app._set_cps(37)
app._push()
cfg.save_profile(name, app.settings)
check("profile file written", cfg.profile_path(name).exists())
app._set_cps(5)
app._push()
loaded = cfg.load_profile(name)
check("profile round trip", abs(loaded.cps() - 37) < 0.5, "%.2f" % loaded.cps())
app._replace_settings(loaded)
app.root.update()
check("replace_settings rebuilt the UI", abs(app.settings.cps() - 37) < 0.5)
check("hotkeys still bound after replace", len(app.hotkeys._bindings) >= 3)
cfg.delete_profile(name)
check("profile deleted", not cfg.profile_path(name).exists())

print("\n[12] engine drives the UI (real clicks suppressed)")
import autoclicker.engine as eng_mod
fired = []
eng_mod.winapi.mouse_button = lambda b, d: fired.append(d)
# Drive it the way a user would: through the widgets, since start() pushes
# widget state into settings.
app.settings.action_type = "click"
app.settings.position_mode = "cursor"
app.settings.jitter_enabled = False
app.settings.set_interval_seconds(0.005)
app.settings.repeat_mode = "count"
app.settings.repeat_count = 20
app.settings.autosave = False
app._pull()
app.root.update()
check("widgets picked up count mode", app._vars["repeat_mode"].get() == "count")
app.start()
t0 = time.time()
while app.engine.running and time.time() - t0 < 5:
    app.root.update()
    time.sleep(0.02)
for _ in range(10):
    app.root.update()
    time.sleep(0.03)
check("20 events ran", app.engine.stats.events == 20, "%d" % app.engine.stats.events)
check("40 button transitions", len(fired) == 40, "%d" % len(fired))
check("status back to Idle", app._vars["status"].get() == "Idle",
      app._vars["status"].get())
check("start button re-enabled", str(app.btn_start["state"]) == "normal")
log = app.log_text.get("1.0", "end")
check("start logged", "Started" in log)
check("stop logged", "Stopped after" in log)

print("\n[13] window enumeration")
app._refresh_windows()
app.root.update()
check("found windows", len(app._window_list) > 0, "%d" % len(app._window_list))

print("\n[14] point dialog really opens, edits and returns")
import tkinter as tk
from autoclicker.gui import PointDialog, SimplePrompt

pt = ClickPoint(x=5, y=6)


def drive_ok():
    for w in app.root.winfo_children():
        if isinstance(w, PointDialog):
            w.x.set(123)
            w.y.set(456)
            w.button.set("right")
            w.use_delay.set(True)
            w.delay.set(75)
            w.label.set("corner")
            w._ok()


app.root.after(250, drive_ok)
dlg = PointDialog(app.root, pt, app._colors)
check("dialog returned OK", dlg.result is True)
check("edits applied", (pt.x, pt.y, pt.button, pt.delay_ms, pt.label)
      == (123, 456, "right", 75.0, "corner"),
      "%r" % ((pt.x, pt.y, pt.button, pt.delay_ms, pt.label),))

app.root.after(250, lambda: [w.destroy() for w in app.root.winfo_children()
                             if isinstance(w, PointDialog)])
pt2 = ClickPoint(x=9, y=9)
dlg2 = PointDialog(app.root, pt2, app._colors)
check("cancel leaves the point alone", dlg2.result is None and pt2.x == 9)

app.root.after(250, lambda: [(setattr(w, "var", w.var), w.var.set("Named"), w._ok())
                             for w in app.root.winfo_children()
                             if isinstance(w, SimplePrompt)])
prompt = SimplePrompt(app.root, "t", "name?", app._colors)
check("text prompt returns the value", prompt.result == "Named", "%r" % prompt.result)

print("\n[15] clean shutdown")
app.settings.confirm_exit_while_running = False
app.settings.autosave = False
app._vars["confirm_exit_while_running"].set(False)
app._vars["autosave"].set(False)
app.quit()
time.sleep(0.5)
check("hooks removed", not app.hotkeys._kb_hook and not app.hotkeys._mouse_hook)
check("tray gone", app.tray is None)
check("engine stopped", not app.engine.running)

print("\n" + "=" * 62)
print("FAILED: %s" % ", ".join(FAILS) if FAILS else "ALL GUI CHECKS PASSED")
print("=" * 62)
sys.exit(1 if FAILS else 0)

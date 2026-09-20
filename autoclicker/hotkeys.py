"""Global hotkeys via low-level keyboard and mouse hooks.

``RegisterHotKey`` was deliberately not used: it cannot bind mouse buttons, it
cannot report key-release (needed for hold-to-click), and it fails outright
when another app already owns the combination. A ``WH_KEYBOARD_LL`` /
``WH_MOUSE_LL`` pair has none of those limits.

The hook callbacks run on the OS input thread, so they do the absolute minimum
-- match the binding and push an item onto a queue -- while a separate
dispatcher thread runs the user callbacks. A slow hook callback stalls input
for the whole desktop, so this split matters.
"""

from __future__ import annotations

import ctypes
import queue
import threading
from ctypes import wintypes
from dataclasses import dataclass

from . import winapi
from .config import Hotkey

# Virtual keys that only ever act as modifiers. During a capture these must not
# end the capture -- pressing Shift to build "Shift+F5" would otherwise bind
# bare Shift the instant it went down.
MODIFIER_VKS = frozenset({
    winapi.VK_SHIFT, winapi.VK_CONTROL, winapi.VK_MENU,
    winapi.VK_LWIN, winapi.VK_RWIN,
    0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5,
})


@dataclass
class Binding:
    name: str
    hotkey: Hotkey
    on_press: object = None
    on_release: object = None
    suppress: bool = False


class HotkeyManager:
    def __init__(self, on_error=None):
        self._bindings: dict[str, Binding] = {}
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._dispatcher: threading.Thread | None = None
        self._thread_id = 0
        self._queue: queue.Queue = queue.Queue()
        self._running = threading.Event()
        self._ready = threading.Event()
        self._kb_hook = None
        self._mouse_hook = None
        self._kb_proc = None
        self._mouse_proc = None
        self._down: set[tuple[str, int]] = set()
        self._paused = False
        self.on_error = on_error or (lambda msg: None)

        # Set while a capture is in progress; swallows the event and reports it.
        self._capture_cb = None
        self._capture_mouse = True
        self._capture_progress = None
        self._capture_release: tuple[str, int] | None = None

    # ------------------------------------------------------------ lifecycle
    def start(self) -> bool:
        with self._lock:
            if self._running.is_set():
                return True
            self._ready.clear()
            self._running.set()
            self._dispatcher = threading.Thread(target=self._dispatch_loop,
                                                name="hotkey-dispatch", daemon=True)
            self._dispatcher.start()
            self._thread = threading.Thread(target=self._hook_loop,
                                            name="hotkey-hooks", daemon=True)
            self._thread.start()
            self._ready.wait(timeout=3.0)
            return True

    def stop(self) -> None:
        with self._lock:
            if not self._running.is_set():
                return
            self._running.clear()
            if self._thread_id:
                winapi.user32.PostThreadMessageW(self._thread_id, winapi.WM_QUIT, 0, 0)
            self._queue.put(None)
        for thread in (self._thread, self._dispatcher):
            if thread and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=2.0)
        self._thread = None
        self._dispatcher = None

    def pause(self, paused: bool = True) -> None:
        """Temporarily ignore all bindings without tearing the hooks down."""
        self._paused = paused

    # ----------------------------------------------------------- bindings
    def register(self, name: str, hotkey: Hotkey, on_press=None,
                 on_release=None, suppress: bool = False) -> None:
        with self._lock:
            if hotkey and hotkey.is_set():
                self._bindings[name] = Binding(name, hotkey, on_press, on_release, suppress)
            else:
                self._bindings.pop(name, None)

    def unregister(self, name: str) -> None:
        with self._lock:
            self._bindings.pop(name, None)

    def clear(self) -> None:
        with self._lock:
            self._bindings.clear()

    def conflicts(self, hotkey: Hotkey, ignore: str = "") -> list:
        """Names of bindings already using this key, so the UI can warn."""
        if not hotkey.is_set():
            return []
        with self._lock:
            return [
                b.name for b in self._bindings.values()
                if b.name != ignore
                and b.hotkey.kind == hotkey.kind
                and b.hotkey.code == hotkey.code
                and b.hotkey.mods == hotkey.mods
            ]

    # ------------------------------------------------------------ capture
    def capture_next(self, callback, include_mouse: bool = True,
                     on_progress=None) -> None:
        """Swallow the next key/button press and report it as a Hotkey.

        Bare modifier presses do not finish the capture; they are folded into
        the combination instead, so holding Shift and then tapping F5 yields
        Shift+F5. ``on_progress`` is called with the live modifier mask each
        time a modifier goes down or up, for "Shift+..." style feedback.
        """
        self._capture_mouse = include_mouse
        self._capture_progress = on_progress
        self._capture_cb = callback

    def cancel_capture(self) -> None:
        self._capture_cb = None
        self._capture_progress = None
        self._capture_release = None

    @property
    def capturing(self) -> bool:
        return self._capture_cb is not None

    # ------------------------------------------------------------- hooks
    def _hook_loop(self) -> None:
        self._thread_id = winapi.kernel32.GetCurrentThreadId()
        module = winapi.kernel32.GetModuleHandleW(None)

        self._kb_proc = winapi.HOOKPROC(self._on_keyboard)
        self._mouse_proc = winapi.HOOKPROC(self._on_mouse)
        self._kb_hook = winapi.user32.SetWindowsHookExW(
            winapi.WH_KEYBOARD_LL, self._kb_proc, module, 0)
        self._mouse_hook = winapi.user32.SetWindowsHookExW(
            winapi.WH_MOUSE_LL, self._mouse_proc, module, 0)

        if not self._kb_hook:
            self.on_error("Could not install the keyboard hook; hotkeys are disabled.")
        self._ready.set()

        msg = wintypes.MSG()
        try:
            while self._running.is_set():
                result = winapi.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result in (0, -1):
                    break
                winapi.user32.TranslateMessage(ctypes.byref(msg))
                winapi.user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self._kb_hook:
                winapi.user32.UnhookWindowsHookEx(self._kb_hook)
                self._kb_hook = None
            if self._mouse_hook:
                winapi.user32.UnhookWindowsHookEx(self._mouse_hook)
                self._mouse_hook = None
            self._ready.set()

    def _match(self, kind: str, code: int, mods: int) -> list:
        with self._lock:
            out = []
            for binding in self._bindings.values():
                hk = binding.hotkey
                if hk.kind == kind and hk.code == code and hk.mods == mods:
                    out.append(binding)
            return out

    def _handle(self, kind: str, code: int, pressed: bool) -> bool:
        """Return True to swallow the event. Must stay fast."""
        if not pressed and self._capture_release == (kind, code):
            # Swallow the release that belongs to a press we just captured, so
            # the foreground app never sees a lone key-up.
            self._capture_release = None
            return True

        if self._capture_cb is not None:
            if kind == "key" and code in MODIFIER_VKS:
                # Part of the combination, not the end of it. Left unswallowed
                # so a cancelled capture cannot stick a modifier down.
                if self._capture_progress:
                    self._queue.put(("progress", self._capture_progress,
                                     winapi.current_modifiers()))
                return False
            if not pressed:
                return False
            if kind == "mouse" and not self._capture_mouse:
                return False
            callback, self._capture_cb = self._capture_cb, None
            self._capture_progress = None
            self._capture_release = (kind, code)
            self._queue.put(("capture", callback,
                             Hotkey(kind, code, winapi.current_modifiers())))
            return True

        if self._paused:
            return False

        mods = winapi.current_modifiers()
        if kind == "key":
            # A modifier used as the trigger must not also count as a modifier.
            if code in (winapi.VK_CONTROL, 0xA2, 0xA3):
                mods &= ~winapi.MOD_CONTROL
            elif code in (winapi.VK_MENU, 0xA4, 0xA5):
                mods &= ~winapi.MOD_ALT
            elif code in (winapi.VK_SHIFT, 0xA0, 0xA1):
                mods &= ~winapi.MOD_SHIFT
            elif code in (winapi.VK_LWIN, winapi.VK_RWIN):
                mods &= ~winapi.MOD_WIN

        key = (kind, code)
        matches = self._match(kind, code, mods)
        if pressed:
            if not matches:
                return False
            if key in self._down:
                return any(b.suppress for b in matches)  # auto-repeat
            self._down.add(key)
            for binding in matches:
                if binding.on_press:
                    self._queue.put(("fire", binding.on_press, None))
            return any(b.suppress for b in matches)

        # release: only fire if we saw the matching press
        if key not in self._down:
            return False
        self._down.discard(key)
        # Modifiers may have changed between press and release, so release
        # handlers are matched on the key alone.
        with self._lock:
            releases = [b for b in self._bindings.values()
                        if b.hotkey.kind == kind and b.hotkey.code == code]
        for binding in releases:
            if binding.on_release:
                self._queue.put(("fire", binding.on_release, None))
        return any(b.suppress for b in releases)

    def _on_keyboard(self, ncode, wparam, lparam):
        if ncode == 0:
            try:
                info = ctypes.cast(lparam, ctypes.POINTER(winapi.KBDLLHOOKSTRUCT)).contents
                if info.dwExtraInfo != winapi.INJECT_SIGNATURE:
                    pressed = wparam in (winapi.WM_KEYDOWN, winapi.WM_SYSKEYDOWN)
                    released = wparam in (winapi.WM_KEYUP, winapi.WM_SYSKEYUP)
                    if (pressed or released) and self._handle("key", int(info.vkCode), pressed):
                        return 1
            except Exception:
                pass
        return winapi.user32.CallNextHookEx(None, ncode, wparam, lparam)

    _MOUSE_DOWN = {
        winapi.WM_LBUTTONDOWN: 1, winapi.WM_RBUTTONDOWN: 2,
        winapi.WM_MBUTTONDOWN: 3,
    }
    _MOUSE_UP = {
        winapi.WM_LBUTTONUP: 1, winapi.WM_RBUTTONUP: 2, winapi.WM_MBUTTONUP: 3,
    }

    def _on_mouse(self, ncode, wparam, lparam):
        if ncode == 0 and wparam != winapi.WM_MOUSEMOVE:
            try:
                info = ctypes.cast(lparam, ctypes.POINTER(winapi.MSLLHOOKSTRUCT)).contents
                if not (info.flags & winapi.LLMHF_INJECTED) and \
                        info.dwExtraInfo != winapi.INJECT_SIGNATURE:
                    button = self._MOUSE_DOWN.get(wparam)
                    pressed = button is not None
                    if button is None:
                        button = self._MOUSE_UP.get(wparam)
                    if button is None and wparam in (winapi.WM_XBUTTONDOWN, winapi.WM_XBUTTONUP):
                        pressed = wparam == winapi.WM_XBUTTONDOWN
                        button = 3 + ((info.mouseData >> 16) & 0xFFFF)  # 4 or 5
                    if button and self._handle("mouse", int(button), pressed):
                        return 1
            except Exception:
                pass
        return winapi.user32.CallNextHookEx(None, ncode, wparam, lparam)

    # --------------------------------------------------------- dispatcher
    def _dispatch_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                break
            try:
                kind, callback, payload = item
                if kind in ("capture", "progress"):
                    callback(payload)
                else:
                    callback()
            except Exception as exc:
                self.on_error("Hotkey handler failed: %s" % exc)
            if not self._running.is_set() and self._queue.empty():
                break

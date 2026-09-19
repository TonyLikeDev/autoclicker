"""Thin ctypes layer over the Win32 APIs the auto clicker needs.

Everything here is stdlib-only. Input is injected with ``SendInput`` (the
modern, non-deprecated path) and tagged with a magic ``dwExtraInfo`` value so
our own global hooks can recognise and ignore synthetic events.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

if sys.platform != "win32":  # pragma: no cover - the app is Windows-only
    raise RuntimeError("AutoClicker requires Windows.")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
winmm = ctypes.WinDLL("winmm")
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

# wintypes has no pointer-sized integer alias.
if ctypes.sizeof(ctypes.c_void_p) == 8:
    ULONG_PTR = ctypes.c_uint64
    LONG_PTR = ctypes.c_int64
else:
    ULONG_PTR = ctypes.c_ulong
    LONG_PTR = ctypes.c_long

LRESULT = LONG_PTR
WPARAM = ULONG_PTR
LPARAM = LONG_PTR

# Stamped onto every event we inject so our hooks can ignore it.
INJECT_SIGNATURE = 0x1AC71C5E

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

XBUTTON1 = 0x0001
XBUTTON2 = 0x0002
WHEEL_DELTA = 120

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

MAPVK_VK_TO_VSC = 0

SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C

WM_QUIT = 0x0012
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_DESTROY = 0x0002
WM_APP = 0x8000

LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_ESCAPE = 0x1B

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

# Keys that must carry the extended-key flag to be interpreted correctly.
EXTENDED_KEYS = frozenset({
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,  # page/home/end/arrows
    0x2D, 0x2E,                                       # insert, delete
    0x6F,                                             # numpad divide
    0x90,                                             # numlock
    0x5B, 0x5C, 0x5D,                                 # win keys, apps
    0xA3, 0xA5,                                       # right ctrl / right alt
})

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

IDLE_PRIORITY_CLASS = 0x00000040
NORMAL_PRIORITY_CLASS = 0x00000020
ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
HIGH_PRIORITY_CLASS = 0x00000080
REALTIME_PRIORITY_CLASS = 0x00000100

# --------------------------------------------------------------------------
# Structures
# --------------------------------------------------------------------------


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, LPARAM)

# --------------------------------------------------------------------------
# Prototypes
# --------------------------------------------------------------------------

user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
user32.GetCursorPos.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
user32.SetCursorPos.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, WPARAM, LPARAM)
user32.CallNextHookEx.restype = LRESULT
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, WPARAM, LPARAM)
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)
user32.PostMessageW.restype = wintypes.BOOL
user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)
user32.SendMessageW.restype = LRESULT
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.IsWindow.argtypes = (wintypes.HWND,)
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsWindowVisible.restype = wintypes.BOOL
user32.EnumWindows.argtypes = (WNDENUMPROC, LPARAM)
user32.EnumWindows.restype = wintypes.BOOL
user32.ScreenToClient.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
user32.ScreenToClient.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
user32.ClientToScreen.restype = wintypes.BOOL
user32.WindowFromPoint.argtypes = (wintypes.POINT,)
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetAncestor.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.SetThreadExecutionState.argtypes = (wintypes.DWORD,)
kernel32.SetThreadExecutionState.restype = wintypes.DWORD
kernel32.SetPriorityClass.argtypes = (wintypes.HANDLE, wintypes.DWORD)
kernel32.SetPriorityClass.restype = wintypes.BOOL
kernel32.GetCurrentProcess.restype = wintypes.HANDLE

winmm.timeBeginPeriod.argtypes = (wintypes.UINT,)
winmm.timeEndPeriod.argtypes = (wintypes.UINT,)

# --------------------------------------------------------------------------
# Virtual key table
# --------------------------------------------------------------------------

VK_NAMES: dict[str, int] = {
    "Backspace": 0x08, "Tab": 0x09, "Clear": 0x0C, "Enter": 0x0D,
    "Pause": 0x13, "CapsLock": 0x14, "Esc": 0x1B, "Space": 0x20,
    "PageUp": 0x21, "PageDown": 0x22, "End": 0x23, "Home": 0x24,
    "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28,
    "PrintScreen": 0x2C, "Insert": 0x2D, "Delete": 0x2E,
    "Shift": 0x10, "Ctrl": 0x11, "Alt": 0x12,
    "LShift": 0xA0, "RShift": 0xA1, "LCtrl": 0xA2, "RCtrl": 0xA3,
    "LAlt": 0xA4, "RAlt": 0xA5, "LWin": 0x5B, "RWin": 0x5C, "Apps": 0x5D,
    "NumLock": 0x90, "ScrollLock": 0x91,
    "Num0": 0x60, "Num1": 0x61, "Num2": 0x62, "Num3": 0x63, "Num4": 0x64,
    "Num5": 0x65, "Num6": 0x66, "Num7": 0x67, "Num8": 0x68, "Num9": 0x69,
    "Num*": 0x6A, "Num+": 0x6B, "Num-": 0x6D, "Num.": 0x6E, "Num/": 0x6F,
    "Semicolon": 0xBA, "Equals": 0xBB, "Comma": 0xBC, "Minus": 0xBD,
    "Period": 0xBE, "Slash": 0xBF, "Backtick": 0xC0, "LBracket": 0xDB,
    "Backslash": 0xDC, "RBracket": 0xDD, "Quote": 0xDE,
    "VolumeMute": 0xAD, "VolumeDown": 0xAE, "VolumeUp": 0xAF,
    "MediaNext": 0xB0, "MediaPrev": 0xB1, "MediaStop": 0xB2, "MediaPlay": 0xB3,
}
for _i in range(1, 25):
    VK_NAMES["F%d" % _i] = 0x6F + _i
for _c in range(ord("0"), ord("9") + 1):
    VK_NAMES[chr(_c)] = _c
for _c in range(ord("A"), ord("Z") + 1):
    VK_NAMES[chr(_c)] = _c

VK_CODES: dict[int, str] = {}
for _name, _code in VK_NAMES.items():
    VK_CODES.setdefault(_code, _name)

MOUSE_BUTTON_NAMES = {
    1: "Mouse Left", 2: "Mouse Right", 3: "Mouse Middle",
    4: "Mouse X1 (back)", 5: "Mouse X2 (forward)",
}

BUTTON_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, 0),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, 0),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, 0),
    "x1": (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON1),
    "x2": (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON2),
}

BUTTON_MESSAGES = {
    "left": (WM_LBUTTONDOWN, WM_LBUTTONUP, 0x0001),
    "right": (WM_RBUTTONDOWN, WM_RBUTTONUP, 0x0002),
    "middle": (WM_MBUTTONDOWN, WM_MBUTTONUP, 0x0010),
    "x1": (WM_XBUTTONDOWN, WM_XBUTTONUP, 0x0020),
    "x2": (WM_XBUTTONDOWN, WM_XBUTTONUP, 0x0040),
}


def key_name(code: int) -> str:
    return VK_CODES.get(code, "VK 0x%02X" % code)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def enable_dpi_awareness() -> None:
    """Coordinates must be real pixels or fixed-point clicking drifts."""
    try:
        ctx = ctypes.c_void_p(-4)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if user32.SetProcessDpiAwarenessContext(ctx):
            return
    except Exception:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def get_cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def virtual_screen() -> tuple[int, int, int, int]:
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        max(1, user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)),
        max(1, user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)),
    )


def _send(inputs: list) -> int:
    array = (INPUT * len(inputs))(*inputs)
    return user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))


def _mouse_input(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(dx, dy, data, flags, 0, INJECT_SIGNATURE)
    return inp


def move_mouse_absolute(x: int, y: int) -> None:
    """Absolute move across the whole virtual desktop.

    SendInput is used rather than SetCursorPos because a fair number of games
    read raw input and ignore the latter.
    """
    vx, vy, vw, vh = virtual_screen()
    nx = int(round((x - vx) * 65535 / max(1, vw - 1)))
    ny = int(round((y - vy) * 65535 / max(1, vh - 1)))
    nx = max(0, min(65535, nx))
    ny = max(0, min(65535, ny))
    flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    if _send([_mouse_input(flags, nx, ny)]) == 0:
        user32.SetCursorPos(int(x), int(y))


def mouse_button(button: str, down: bool) -> None:
    down_flag, up_flag, data = BUTTON_FLAGS[button]
    _send([_mouse_input(down_flag if down else up_flag, data=data)])


def mouse_wheel(clicks: int, horizontal: bool = False) -> None:
    flag = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
    _send([_mouse_input(flag, data=ctypes.c_ulong(clicks * WHEEL_DELTA).value)])


def key_event(vk: int, down: bool, use_scancode: bool = False) -> None:
    flags = 0 if down else KEYEVENTF_KEYUP
    scan = 0
    if use_scancode:
        scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
        if scan:
            flags |= KEYEVENTF_SCANCODE
    if vk in EXTENDED_KEYS:
        flags |= KEYEVENTF_EXTENDEDKEY
    inp = INPUT(type=INPUT_KEYBOARD)
    send_vk = 0 if (flags & KEYEVENTF_SCANCODE) else vk
    inp.ki = KEYBDINPUT(send_vk, scan, flags, 0, INJECT_SIGNATURE)
    _send([inp])


def type_unicode(text: str) -> None:
    """Send literal characters, independent of the active keyboard layout."""
    for ch in text:
        code = ord(ch)
        if code > 0xFFFF:  # surrogate pair
            code -= 0x10000
            units = [0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)]
        else:
            units = [code]
        for unit in units:
            for up in (False, True):
                flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
                inp = INPUT(type=INPUT_KEYBOARD)
                inp.ki = KEYBDINPUT(0, unit, flags, 0, INJECT_SIGNATURE)
                _send([inp])


def current_modifiers() -> int:
    mods = 0
    if user32.GetAsyncKeyState(VK_CONTROL) & 0x8000:
        mods |= MOD_CONTROL
    if user32.GetAsyncKeyState(VK_MENU) & 0x8000:
        mods |= MOD_ALT
    if user32.GetAsyncKeyState(VK_SHIFT) & 0x8000:
        mods |= MOD_SHIFT
    if (user32.GetAsyncKeyState(VK_LWIN) & 0x8000) or (user32.GetAsyncKeyState(VK_RWIN) & 0x8000):
        mods |= MOD_WIN
    return mods


def modifier_text(mods: int) -> str:
    parts = []
    if mods & MOD_CONTROL:
        parts.append("Ctrl")
    if mods & MOD_ALT:
        parts.append("Alt")
    if mods & MOD_SHIFT:
        parts.append("Shift")
    if mods & MOD_WIN:
        parts.append("Win")
    return "+".join(parts)


# ------------------------------- windows ----------------------------------


def window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def enum_windows() -> list:
    found = []

    @WNDENUMPROC
    def _cb(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            title = window_title(hwnd)
            if title:
                found.append((int(hwnd), title))
        return True

    user32.EnumWindows(_cb, 0)
    return found


def foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0)


def window_exists(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def window_from_point(x: int, y: int) -> int:
    pt = wintypes.POINT(x, y)
    hwnd = user32.WindowFromPoint(pt)
    return int(hwnd or 0)


def root_window(hwnd: int) -> int:
    return int(user32.GetAncestor(hwnd, 2) or hwnd)  # GA_ROOT


CWP_SKIPINVISIBLE = 0x0001
CWP_SKIPDISABLED = 0x0002
CWP_SKIPTRANSPARENT = 0x0004

user32.ChildWindowFromPointEx.argtypes = (wintypes.HWND, wintypes.POINT, wintypes.UINT)
user32.ChildWindowFromPointEx.restype = wintypes.HWND


def deepest_child_at(hwnd: int, screen_x: int, screen_y: int, max_depth: int = 8) -> int:
    """Descend to the innermost child control covering a screen point.

    Posted messages are not routed by the window manager, so they have to be
    addressed to the control that would really receive them -- a click posted
    to a top-level frame is simply ignored by the edit box inside it. Unlike
    WindowFromPoint this never escapes into another application, because it
    only ever walks down from ``hwnd``.
    """
    current = hwnd
    for _ in range(max_depth):
        pt = wintypes.POINT(int(screen_x), int(screen_y))
        user32.ScreenToClient(current, ctypes.byref(pt))
        child = user32.ChildWindowFromPointEx(
            current, pt, CWP_SKIPINVISIBLE | CWP_SKIPTRANSPARENT)
        child = int(child or 0)
        if not child or child == current:
            break
        current = child
    return current


def screen_to_client(hwnd: int, x: int, y: int) -> tuple[int, int]:
    pt = wintypes.POINT(x, y)
    user32.ScreenToClient(hwnd, ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def make_lparam(x: int, y: int) -> int:
    return (int(y) & 0xFFFF) << 16 | (int(x) & 0xFFFF)


def post_click(hwnd: int, button: str, x: int, y: int, double: bool = False) -> bool:
    """Deliver a click straight to a window without moving the real cursor."""
    if not user32.IsWindow(hwnd):
        return False
    down, up, key_flag = BUTTON_MESSAGES[button]
    lparam = make_lparam(x, y)
    if button in ("x1", "x2"):
        wparam = (XBUTTON1 if button == "x1" else XBUTTON2) << 16
        up_wparam = wparam
    else:
        wparam = key_flag
        up_wparam = 0
    ok = bool(user32.PostMessageW(hwnd, down, wparam, lparam))
    ok = bool(user32.PostMessageW(hwnd, up, up_wparam, lparam)) and ok
    if double:
        ok = bool(user32.PostMessageW(hwnd, down, wparam, lparam)) and ok
        ok = bool(user32.PostMessageW(hwnd, up, up_wparam, lparam)) and ok
    return ok


def post_key(hwnd: int, vk: int) -> bool:
    if not user32.IsWindow(hwnd):
        return False
    scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    down_lparam = 1 | (scan << 16)
    up_lparam = down_lparam | (1 << 30) | (1 << 31)
    ok = bool(user32.PostMessageW(hwnd, WM_KEYDOWN, vk, down_lparam))
    ok = bool(user32.PostMessageW(hwnd, WM_KEYUP, vk, up_lparam)) and ok
    return ok


# ----------------------------- system tuning -------------------------------


class TimerResolution:
    """timeBeginPeriod(1) so sleep granularity drops from ~15.6 ms to ~1 ms."""

    def __init__(self, ms: int = 1):
        self.ms = ms
        self._active = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_exc):
        self.release()

    def acquire(self) -> None:
        if not self._active:
            try:
                winmm.timeBeginPeriod(self.ms)
                self._active = True
            except Exception:
                pass

    def release(self) -> None:
        if self._active:
            try:
                winmm.timeEndPeriod(self.ms)
            except Exception:
                pass
            self._active = False


PRIORITY_CLASSES = {
    "idle": IDLE_PRIORITY_CLASS,
    "normal": NORMAL_PRIORITY_CLASS,
    "above normal": ABOVE_NORMAL_PRIORITY_CLASS,
    "high": HIGH_PRIORITY_CLASS,
    "realtime": REALTIME_PRIORITY_CLASS,
}


def set_process_priority(level: str) -> bool:
    value = PRIORITY_CLASSES.get(str(level).lower())
    if value is None:
        return False
    return bool(kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), value))


def keep_awake(enabled: bool) -> None:
    flags = ES_CONTINUOUS | ((ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED) if enabled else 0)
    kernel32.SetThreadExecutionState(wintypes.DWORD(flags))


def is_elevated() -> bool:
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False

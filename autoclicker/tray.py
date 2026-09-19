"""System tray icon built on Shell_NotifyIcon.

Tk has no tray support, so this runs a message-only window plus its own
message pump on a private thread. Everything is wrapped defensively: if the
shell refuses the icon the app degrades to a normal window instead of dying.
"""

from __future__ import annotations

import ctypes
import struct
import threading
from ctypes import wintypes
from pathlib import Path

from . import winapi

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

NIIF_INFO = 0x00000001

WM_TRAY = winapi.WM_APP + 17

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
MF_CHECKED = 0x00000008
MF_GRAYED = 0x00000001

TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

CW_USEDEFAULT = -2147483648
HWND_MESSAGE = -3

shell32 = winapi.shell32
user32 = winapi.user32
kernel32 = winapi.kernel32


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", winapi.WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


shell32.Shell_NotifyIconW.argtypes = (wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
shell32.Shell_NotifyIconW.restype = wintypes.BOOL

# Without explicit restypes ctypes assumes int, which truncates every handle
# returned by these calls on 64-bit Python.
HMENU = wintypes.HANDLE
user32.RegisterClassW.argtypes = (ctypes.POINTER(WNDCLASSW),)
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = (
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT,
                                  winapi.WPARAM, winapi.LPARAM)
user32.DefWindowProcW.restype = winapi.LRESULT
user32.DestroyWindow.argtypes = (wintypes.HWND,)
user32.PostQuitMessage.argtypes = (ctypes.c_int,)
user32.LoadImageW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT)
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadIconW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR)
user32.LoadIconW.restype = wintypes.HICON
user32.DestroyIcon.argtypes = (wintypes.HICON,)
user32.CreatePopupMenu.restype = HMENU
user32.DestroyMenu.argtypes = (HMENU,)
user32.AppendMenuW.argtypes = (HMENU, wintypes.UINT, winapi.ULONG_PTR, wintypes.LPCWSTR)
user32.TrackPopupMenu.argtypes = (HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wintypes.HWND, wintypes.LPVOID)
user32.TrackPopupMenu.restype = wintypes.BOOL

IDI_APPLICATION = ctypes.cast(ctypes.c_void_p(32512), wintypes.LPCWSTR)


# --------------------------------------------------------------------------
# Icon generation - avoids shipping a binary asset
# --------------------------------------------------------------------------


def make_icon_file(path: Path, rgb: tuple[int, int, int], size: int = 32) -> Path:
    """Write a 32-bit ICO of a filled circle with a soft ring."""
    r, g, b = rgb
    cx = cy = (size - 1) / 2.0
    radius = size / 2.0 - 1.0

    # BMP rows are bottom-up.
    pixels = bytearray()
    for y in range(size - 1, -1, -1):
        for x in range(size):
            dx, dy = x - cx, y - cy
            dist = (dx * dx + dy * dy) ** 0.5
            if dist <= radius - 1.2:
                alpha = 255
                shade = 1.0
            elif dist <= radius:
                alpha = int(255 * max(0.0, (radius - dist) / 1.2))
                shade = 1.0
            else:
                alpha = 0
                shade = 1.0
            # Slight vertical gradient so the dot does not look flat.
            if alpha:
                factor = 0.75 + 0.25 * (1.0 - (y / float(size)))
                shade = factor
            pixels += bytes((int(b * shade), int(g * shade), int(r * shade), alpha))

    mask_row = ((size + 31) // 32) * 4
    mask = bytes(mask_row * size)

    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                         len(pixels) + len(mask), 0, 0, 0, 0)
    image = header + bytes(pixels) + mask
    ico = struct.pack("<HHH", 0, 1, 1)
    ico += struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(image), 22)
    ico += image

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ico)
    return path


def load_icon(path: Path):
    handle = user32.LoadImageW(None, str(path), IMAGE_ICON, 0, 0,
                              LR_LOADFROMFILE | LR_DEFAULTSIZE)
    if not handle:
        handle = user32.LoadIconW(None, IDI_APPLICATION)
    return handle


# --------------------------------------------------------------------------


class TrayIcon:
    """Menu items are ``(label, callback)``; ``(None, None)`` is a separator."""

    def __init__(self, title: str, icon_path: Path, on_activate=None, menu=None):
        self.title = title
        self.icon_path = Path(icon_path)
        self.on_activate = on_activate or (lambda: None)
        self.menu_items = list(menu or [])
        self.available = False

        self._hwnd = 0
        self._icon = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._wndproc = None
        self._class_name = "AutoClickerTrayWnd"
        self._registered = False
        self._lock = threading.RLock()

    # -------------------------------------------------------------- public
    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return self.available
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3.0)
        return self.available

    def stop(self) -> None:
        if self._hwnd:
            try:
                user32.PostMessageW(self._hwnd, winapi.WM_CLOSE, 0, 0)
            except Exception:
                pass
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
        self.available = False

    def set_icon(self, path: Path, tooltip: str | None = None) -> None:
        with self._lock:
            self.icon_path = Path(path)
            if tooltip is not None:
                self.title = tooltip
            if not self._hwnd:
                return
            icon = load_icon(self.icon_path)
            data = self._nid(NIF_ICON | NIF_TIP)
            data.hIcon = icon
            data.szTip = self.title[:127]
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))
            if self._icon:
                try:
                    user32.DestroyIcon(self._icon)
                except Exception:
                    pass
            self._icon = icon

    def set_tooltip(self, text: str) -> None:
        with self._lock:
            self.title = text
            if not self._hwnd:
                return
            data = self._nid(NIF_TIP)
            data.szTip = text[:127]
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))

    def notify(self, title: str, message: str) -> None:
        with self._lock:
            if not self._hwnd:
                return
            data = self._nid(NIF_INFO)
            data.szInfo = message[:255]
            data.szInfoTitle = title[:63]
            data.dwInfoFlags = NIIF_INFO
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))

    # ------------------------------------------------------------ internals
    def _nid(self, flags: int) -> NOTIFYICONDATAW:
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = flags
        data.uCallbackMessage = WM_TRAY
        return data

    def _run(self) -> None:
        try:
            self._create_window()
            self._add_icon()
            self.available = True
        except Exception:
            self.available = False
            self._ready.set()
            return
        self._ready.set()

        msg = wintypes.MSG()
        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result in (0, -1):
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._remove_icon()
            self.available = False

    def _create_window(self) -> None:
        self._wndproc = winapi.WNDPROC(self._wnd_proc)
        instance = kernel32.GetModuleHandleW(None)

        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = instance
        wc.lpszClassName = self._class_name
        user32.RegisterClassW(ctypes.byref(wc))  # harmless if already present
        self._registered = True

        self._hwnd = user32.CreateWindowExW(
            0, self._class_name, self.title, 0,
            CW_USEDEFAULT, CW_USEDEFAULT, 0, 0,
            wintypes.HWND(HWND_MESSAGE), None, instance, None)
        if not self._hwnd:
            raise OSError("CreateWindowExW failed")

    def _add_icon(self) -> None:
        self._icon = load_icon(self.icon_path)
        data = self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        data.hIcon = self._icon
        data.szTip = self.title[:127]
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
            raise OSError("Shell_NotifyIconW(NIM_ADD) failed")

    def _remove_icon(self) -> None:
        if self._hwnd:
            try:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid(0)))
            except Exception:
                pass
        if self._icon:
            try:
                user32.DestroyIcon(self._icon)
            except Exception:
                pass
            self._icon = None
        self._hwnd = 0

    def _show_menu(self) -> None:
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        ids = {}
        next_id = 1000
        for label, callback in self.menu_items:
            if label is None:
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                continue
            text = label() if callable(label) else label
            user32.AppendMenuW(menu, MF_STRING, next_id, str(text))
            ids[next_id] = callback
            next_id += 1

        user32.SetForegroundWindow(self._hwnd)
        x, y = winapi.get_cursor_pos()
        chosen = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                       x, y, 0, self._hwnd, None)
        user32.PostMessageW(self._hwnd, 0, 0, 0)
        user32.DestroyMenu(menu)
        callback = ids.get(int(chosen or 0))
        if callback:
            try:
                callback()
            except Exception:
                pass

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            event = lparam & 0xFFFF
            if event in (winapi.WM_LBUTTONUP, winapi.WM_LBUTTONDBLCLK):
                try:
                    self.on_activate()
                except Exception:
                    pass
                return 0
            if event == winapi.WM_RBUTTONUP:
                self._show_menu()
                return 0
        elif msg == winapi.WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        elif msg == winapi.WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

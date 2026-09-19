"""A real Win32 window with BUTTON and EDIT children, for driving tests."""
import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from autoclicker import winapi

user32, kernel32 = winapi.user32, winapi.kernel32

# --------------------------------------------------------------------------
# A real Win32 target window
# --------------------------------------------------------------------------
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_CHILD, WS_VISIBLE, WS_BORDER = 0x40000000, 0x10000000, 0x00800000
BS_PUSHBUTTON = 0x0000
ES_MULTILINE, ES_AUTOVSCROLL = 0x0004, 0x0040
SW_SHOW = 5
BN_CLICKED = 0
WM_SETTEXT, WM_GETTEXT, WM_GETTEXTLENGTH = 0x000C, 0x000D, 0x000E
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_SHOWWINDOW = 0x0001, 0x0002, 0x0040

ID_BUTTON, ID_EDIT = 101, 102


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", winapi.WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


user32.CreateWindowExW.argtypes = (
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HANDLE, wintypes.HINSTANCE, wintypes.LPVOID)
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, winapi.WPARAM, winapi.LPARAM)
user32.DefWindowProcW.restype = winapi.LRESULT
user32.RegisterClassW.argtypes = (ctypes.POINTER(WNDCLASSW),)
user32.RegisterClassW.restype = wintypes.ATOM
user32.LoadCursorW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR)
user32.LoadCursorW.restype = wintypes.HANDLE
user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT)
user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.DestroyWindow.argtypes = (wintypes.HWND,)


class Target:
    TITLE = "AutoClicker Test Target 9471"

    def __init__(self):
        self.hwnd = 0
        self.button = 0
        self.edit = 0
        self.clicks = 0
        self.rclicks = 0
        self.mclicks = 0
        self.wheel = 0
        self.ready = threading.Event()
        self._proc = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.ready.wait(5)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == winapi.WM_COMMAND and (wparam >> 16) == BN_CLICKED \
                and (wparam & 0xFFFF) == ID_BUTTON:
            self.clicks += 1
        elif msg == winapi.WM_RBUTTONUP:
            self.rclicks += 1
        elif msg == winapi.WM_MBUTTONUP:
            self.mclicks += 1
        elif msg == winapi.WM_MOUSEWHEEL:
            self.wheel += ctypes.c_short((wparam >> 16) & 0xFFFF).value // 120
        elif msg == winapi.WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self):
        self._proc = winapi.WNDPROC(self._wndproc)
        inst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._proc
        wc.hInstance = inst
        wc.lpszClassName = "AutoClickerTestTargetClass"
        wc.hCursor = user32.LoadCursorW(None, ctypes.cast(ctypes.c_void_p(32512),
                                                          wintypes.LPCWSTR))
        wc.hbrBackground = 6  # COLOR_WINDOW
        user32.RegisterClassW(ctypes.byref(wc))

        self.hwnd = user32.CreateWindowExW(
            0, "AutoClickerTestTargetClass", self.TITLE, WS_OVERLAPPEDWINDOW,
            80, 80, 460, 320, None, None, inst, None)
        self.button = user32.CreateWindowExW(
            0, "BUTTON", "Click me", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
            30, 30, 200, 60, self.hwnd, ctypes.c_void_p(ID_BUTTON), inst, None)
        self.edit = user32.CreateWindowExW(
            0, "EDIT", "", WS_CHILD | WS_VISIBLE | WS_BORDER | ES_MULTILINE | ES_AUTOVSCROLL,
            30, 110, 380, 140, self.hwnd, ctypes.c_void_p(ID_EDIT), inst, None)
        user32.ShowWindow(self.hwnd, SW_SHOW)
        self.ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def centre_of(self, child):
        rect = wintypes.RECT()
        user32.GetWindowRect(child, ctypes.byref(rect))
        return ((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)

    def edit_text(self):
        length = user32.SendMessageW(self.edit, WM_GETTEXTLENGTH, 0, 0)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.SendMessageW(self.edit, WM_GETTEXT, length + 1, ctypes.addressof(buf))
        return buf.value

    def clear_edit(self):
        buf = ctypes.create_unicode_buffer("")
        user32.SendMessageW(self.edit, WM_SETTEXT, 0, ctypes.addressof(buf))

    def topmost(self):
        user32.SetWindowPos(self.hwnd, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                            SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW)
        user32.SetForegroundWindow(self.hwnd)

    def close(self):
        user32.PostMessageW(self.hwnd, winapi.WM_CLOSE, 0, 0)
        user32.PostMessageW(self.hwnd, winapi.WM_DESTROY, 0, 0)




def _frame_point(self):
    """A screen point inside the client area that no child control covers.

    Right-clicking an EDIT opens a modal context menu that swallows every
    message posted afterwards, so frame tests must avoid the children.
    """
    rect = wintypes.RECT()
    user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
    for dy in range(30, 260, 10):
        for dx in range(30, 430, 20):
            x, y = rect.left + dx, rect.bottom - dy
            if winapi.deepest_child_at(self.hwnd, x, y) == self.hwnd:
                return x, y
    raise RuntimeError("no free spot on the frame")


Target.frame_point = _frame_point

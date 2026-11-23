"""Foreground-only Windows Raw Input: a physical mouse independent of pen position."""
import ctypes
from ctypes import wintypes as w
import sys
from PySide6.QtCore import QAbstractNativeEventFilter, QPointF
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication


class PenMouseFilter(QAbstractNativeEventFilter):
    """Keep the canvas cursor and discard promoted pen mouse messages.

    Physical mouse packets are never filtered on a timeout since a pen event.
    The documented MI_WP_SIGNATURE identifies promoted input at message dispatch.
    """
    def __init__(self, window, board):
        super().__init__()
        self.window, self.board = window, board
        self.user32 = None
        if sys.platform == 'win32':
            self.user32 = ctypes.WinDLL('user32')
            self.user32.GetMessageExtraInfo.argtypes = []
            self.user32.GetMessageExtraInfo.restype = ctypes.c_ssize_t
            self.user32.SetCursor.argtypes = [w.HANDLE]
            self.user32.SetCursor.restype = w.HANDLE

    def nativeEventFilter(self, event_type, message):
        if not self.user32:
            return False, 0
        msg = w.MSG.from_address(int(message))
        if msg.hWnd != int(self.window.winId()):
            return False, 0
        if msg.message == 0x0020:  # WM_SETCURSOR; WinTab may bypass Qt mouse-enter handling.
            if (msg.lParam & 0xFFFF == 1 and self.window.isActiveWindow()
                    and self.board.uses_brush_cursor()
                    and QApplication.widgetAt(QCursor.pos()) is self.board):
                self.user32.SetCursor(None)
                return True, 1
            # Qt/Windows owns cursors over controls, dialogs, other tools and windows.
            # Never change the global ShowCursor counter.
            return False, 0
        if not self.board.native_pen or msg.message not in (0x200, 0x201, 0x202, 0x203, 0x204, 0x205, 0x206):
            return False, 0
        if self.user32.GetMessageExtraInfo() & 0xFFFFFF00 != 0xFF515700:
            return False, 0
        x = ctypes.c_short(msg.lParam & 0xFFFF).value
        y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
        ratio = self.window.devicePixelRatioF()
        pos = self.board.mapFrom(self.window, QPointF(x/ratio, y/ratio))
        return (True, 0) if self.board.rect().contains(pos.toPoint()) else (False, 0)


class Header(ctypes.Structure):
    _fields_ = [('type', w.DWORD), ('size', w.DWORD), ('device', w.HANDLE), ('param', w.WPARAM)]


class Mouse(ctypes.Structure):
    _fields_ = [('flags', w.USHORT), ('buttons', w.ULONG), ('raw_buttons', w.ULONG), ('dx', w.LONG), ('dy', w.LONG), ('extra', w.ULONG)]


class Raw(ctypes.Structure):
    _fields_ = [('header', Header), ('mouse', Mouse)]


class Device(ctypes.Structure):
    _fields_ = [('page', w.USHORT), ('usage', w.USHORT), ('flags', w.DWORD), ('target', w.HWND)]


class RawMouse(QAbstractNativeEventFilter):
    def __init__(self, hwnd, callback):
        super().__init__()
        self.callback = callback
        self.active = False
        self.available = False
        self.names = {}
        self.events = 0
        self.error = ''
        if sys.platform != 'win32':
            self.error = 'Raw Input доступен только в Windows'
            return
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        self.user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(Device), w.UINT, w.UINT]
        self.user32.RegisterRawInputDevices.restype = w.BOOL
        self.user32.GetRawInputData.argtypes = [w.HANDLE, w.UINT, ctypes.c_void_p, ctypes.POINTER(w.UINT), w.UINT]
        self.user32.GetRawInputData.restype = w.UINT
        self.user32.GetRawInputDeviceInfoW.argtypes = [w.HANDLE, w.UINT, ctypes.c_void_p, ctypes.POINTER(w.UINT)]
        self.user32.GetRawInputDeviceInfoW.restype = w.UINT
        device = Device(1, 2, 0, hwnd)  # No INPUTSINK, no cursor warping, no global hook.
        self.available = bool(self.user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(Device)))
        if not self.available:
            self.error = f'Raw Input: ошибка {ctypes.get_last_error()}'

    def device_name(self, handle):
        if handle not in self.names:
            size = w.UINT(0)
            self.user32.GetRawInputDeviceInfoW(handle, 0x20000007, None, ctypes.byref(size))
            buf = ctypes.create_unicode_buffer(size.value + 1)
            self.user32.GetRawInputDeviceInfoW(handle, 0x20000007, buf, ctypes.byref(size))
            self.names[handle] = buf.value.lower()
        return self.names[handle]

    def nativeEventFilter(self, event_type, message):
        if not self.available or not self.active:
            return False, 0
        try:
            msg = w.MSG.from_address(int(message))
            if msg.message != 0x00FF:
                return False, 0
            size = w.UINT(0)
            self.user32.GetRawInputData(msg.lParam, 0x10000003, None, ctypes.byref(size), ctypes.sizeof(Header))
            if size.value < ctypes.sizeof(Raw) or size.value > 8192:
                return False, 0
            buf = ctypes.create_string_buffer(size.value)
            result = self.user32.GetRawInputData(msg.lParam, 0x10000003, buf, ctypes.byref(size), ctypes.sizeof(Header))
            if result == 0xFFFFFFFF:
                return False, 0
            raw = Raw.from_buffer(buf)
            if raw.header.type != 0 or not raw.header.device:
                return False, 0
            # Wacom's emulated mouse must never move the elastic's independent handle.
            name = self.device_name(raw.header.device)
            if 'vid_056a' in name or 'wacom' in name or raw.mouse.flags & 1:
                return False, 0
            if raw.mouse.extra & 0xFFFFFF00 == 0xFF515700:
                return False, 0
            if raw.mouse.dx or raw.mouse.dy:
                self.events += 1
                self.callback(raw.mouse.dx, raw.mouse.dy)
        except (ValueError, OSError, TypeError) as exc:
            self.error = str(exc)
        # Qt/DefWindowProc still owns WM_INPUT cleanup.
        return False, 0

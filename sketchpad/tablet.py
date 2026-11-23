"""Direct WinTab packets, independent of Windows' shared mouse cursor.

WinTab structure/constant definitions follow Wacom's public reference:
https://developer-docs.wacom.com/docs/icbt/windows/wintab/wintab-reference/
No driver preferences, global hooks, or system cursor settings are changed.
"""
import ctypes
from ctypes import wintypes as w
import math
import sys
from PySide6.QtCore import QObject, QTimer, Qt, QEvent, QPointF
from PySide6.QtWidgets import QApplication


class Axis(ctypes.Structure):
    _fields_ = [('minimum', w.LONG), ('maximum', w.LONG), ('units', w.UINT), ('resolution', w.DWORD)]


class Context(ctypes.Structure):
    _fields_ = [('name', w.WCHAR*40)] + [(key, w.UINT) for key in (
        'options', 'status', 'locks', 'msg_base', 'device', 'packet_rate', 'packet_data',
        'packet_mode', 'move_mask', 'button_down', 'button_up')] + [(key, w.LONG) for key in (
        'in_x', 'in_y', 'in_z', 'in_w', 'in_h', 'in_d', 'out_x', 'out_y', 'out_z',
        'out_w', 'out_h', 'out_d')] + [(key, w.DWORD) for key in (
        'sensitivity_x', 'sensitivity_y', 'sensitivity_z')] + [('system_mode', w.BOOL)] + [
        (key, ctypes.c_int) for key in ('system_x', 'system_y', 'system_w', 'system_h')] + [
        ('system_sensitivity_x', w.DWORD), ('system_sensitivity_y', w.DWORD)]


class Orientation(ctypes.Structure):
    _fields_ = [('azimuth', ctypes.c_int), ('altitude', ctypes.c_int), ('twist', ctypes.c_int)]


# Ordering is dictated by WinTab's packet mask, not by the order of flags in the mask.
class Packet(ctypes.Structure):
    _fields_ = [('status', w.UINT), ('time', w.UINT), ('cursor', w.UINT), ('buttons', w.DWORD),
                ('x', w.LONG), ('y', w.LONG), ('pressure', w.UINT), ('orientation', Orientation)]


PACKET_MASK = 0x0002 | 0x0004 | 0x0020 | 0x0040 | 0x0080 | 0x0100 | 0x0400 | 0x1000


def orientation_to_tilt(azimuth, altitude, azimuth_resolution, altitude_resolution):
    """FIX32 units/circle -> tilt from surface normal; zero tilt means upright."""
    az_units = azimuth_resolution/65536.
    alt_units = altitude_resolution/65536.
    if az_units <= 0 or alt_units <= 0:
        return 0., 0.
    az = azimuth/az_units*2*math.pi
    altitude = min(math.pi/2, abs(altitude)/alt_units*2*math.pi)
    cot = 1/math.tan(max(.0001, altitude))
    return (max(-85., min(85., math.degrees(math.atan(math.sin(az)*cot)))),
            max(-85., min(85., -math.degrees(math.atan(math.cos(az)*cot)))))


def open_driver():
    dll = ctypes.WinDLL('wintab32.dll')
    dll.WTInfoW.argtypes = [w.UINT, w.UINT, ctypes.c_void_p]
    dll.WTInfoW.restype = w.UINT
    return dll


def tablet_capabilities():
    result = {'available': False, 'devices': []}
    if sys.platform != 'win32':
        return result
    try:
        dll = open_driver()
        count = w.UINT()
        dll.WTInfoW(1, 4, ctypes.byref(count))
        for i in range(min(count.value, 16)):
            name = ctypes.create_unicode_buffer(256)
            dll.WTInfoW(100+i, 1, name)
            axes = (Axis*3)()
            valid = dll.WTInfoW(100+i, 17, axes)
            pressure = Axis()
            dll.WTInfoW(100+i, 15, ctypes.byref(pressure))
            result['devices'].append({'name': name.value, 'tilt_supported': bool(valid and axes[0].resolution and axes[1].resolution),
                'pressure_max': pressure.maximum,
                'orientation': [{'min': a.minimum, 'max': a.maximum, 'resolution': a.resolution} for a in axes]})
        result['available'] = bool(result['devices'])
    except (OSError, AttributeError) as exc:
        result['error'] = str(exc)
    return result


class NativeTablet(QObject):
    def __init__(self, window, board):
        super().__init__(window)
        self.window, self.board = window, board
        self.context = None
        self.available = False
        self.enabled = False
        self.active = False
        self.down = False
        self.captured = False
        self.must_lift = False
        self.last_pressure = 0.
        self.error = ''
        self.packets = 0
        self.polls = 0
        self.last_packet = {}
        self.enable_ok = False
        self.overlap_ok = False
        self.max_pressure = 0.
        self.max_tilt = 0.
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(4)
        self.timer.timeout.connect(self.poll)
        if sys.platform != 'win32':
            self.error = 'Прямой ввод Wacom доступен только в Windows'
            return
        try:
            self.dll = open_driver()
            self.dll.WTOpenW.argtypes = [w.HWND, ctypes.POINTER(Context), w.BOOL]
            self.dll.WTOpenW.restype = w.HANDLE
            self.dll.WTEnable.argtypes = [w.HANDLE, w.BOOL]
            self.dll.WTEnable.restype = w.BOOL
            self.dll.WTOverlap.argtypes = [w.HANDLE, w.BOOL]
            self.dll.WTOverlap.restype = w.BOOL
            self.dll.WTClose.argtypes = [w.HANDLE]
            self.dll.WTClose.restype = w.BOOL
            self.dll.WTPacketsGet.argtypes = [w.HANDLE, ctypes.c_int, ctypes.POINTER(Packet)]
            self.dll.WTPacketsGet.restype = ctypes.c_int
            self.dll.WTQueueSizeSet.argtypes = [w.HANDLE, ctypes.c_int]
            self.dll.WTQueueSizeSet.restype = w.BOOL
            self.user32 = ctypes.WinDLL('user32')
            self.user32.ScreenToClient.argtypes = [w.HWND, ctypes.POINTER(w.POINT)]
            self.user32.ScreenToClient.restype = w.BOOL
            self.ctx = Context()
            if not self.dll.WTInfoW(500, 0, ctypes.byref(self.ctx)) and not self.dll.WTInfoW(4, 0, ctypes.byref(self.ctx)):
                raise OSError('Драйвер не вернул контекст планшета')
            self.ctx.name = 'Atelier independent pen'
            # Keep the user's default input region and display mapping; no shared-cursor fallback.
            self.ctx.options = (self.ctx.options | 5) & ~8
            self.ctx.msg_base = 0x7E00
            self.ctx.packet_data = self.ctx.move_mask = PACKET_MASK
            self.ctx.packet_mode = 0
            # In WinTab a negative extent flips the axis within the output
            # rectangle; its origin stays at zero (not at the far edge).
            self.ctx.out_x, self.ctx.out_y = 0, 0
            self.ctx.out_w, self.ctx.out_h = 1_000_000, -1_000_000
            self.pressure_axis = Axis()
            self.orientation_axes = (Axis*3)()
            device_id = 0 if self.ctx.device == 0xFFFFFFFF else self.ctx.device
            self.dll.WTInfoW(100+device_id, 15, ctypes.byref(self.pressure_axis))
            self.dll.WTInfoW(100+device_id, 17, self.orientation_axes)
            if self.ctx.system_w <= 0 or self.ctx.system_h <= 0:
                raise OSError('Драйвер не вернул область экрана')
            if self.pressure_axis.maximum <= self.pressure_axis.minimum:
                raise OSError('Драйвер не вернул диапазон нажима')
            self.context = self.dll.WTOpenW(int(window.winId()), ctypes.byref(self.ctx), False)
            if not self.context:
                raise OSError('Не удалось открыть прямой канал Wacom')
            self.dll.WTQueueSizeSet(self.context, 256)
            self.buffer = (Packet*256)()
            self.available = True
        except (OSError, AttributeError) as exc:
            self.error = str(exc)

    def set_enabled(self, enabled):
        self.enabled = bool(enabled and self.available)
        self.board.native_pen = self.enabled
        self.board.pointer_up()
        self.down = self.captured = False
        if self.enabled:
            self.timer.start()
            self.poll()
        else:
            self.timer.stop()
            if self.context:
                self.dll.WTEnable(self.context, False)
            self.active = False

    def local_position(self, packet):
        sx = self.ctx.system_x+packet.x/1_000_000*self.ctx.system_w
        sy = self.ctx.system_y+packet.y/1_000_000*self.ctx.system_h
        point = w.POINT(round(sx), round(sy))
        self.user32.ScreenToClient(int(self.window.winId()), ctypes.byref(point))
        # ScreenToClient returns physical client pixels in this per-monitor DPI aware app.
        ratio = self.window.devicePixelRatioF()
        local = QPointF((point.x+sx-round(sx))/ratio, (point.y+sy-round(sy))/ratio)
        return self.board.mapFrom(self.window, local)

    def poll(self):
        self.polls += 1
        if not self.enabled:
            return
        active = getattr(self.window, 'tablet_input_active', self.window.isActiveWindow)
        foreground = active() and QApplication.activeModalWidget() is None
        if foreground != self.active:
            self.active = foreground
            self.enable_ok = bool(self.dll.WTEnable(self.context, foreground))
            if foreground:
                self.overlap_ok = bool(self.dll.WTOverlap(self.context, True))
                # Drop any stale events from another focus period.
                self.dll.WTPacketsGet(self.context, 256, self.buffer)
                self.must_lift = self.down
            else:
                self.board.pointer_up()
                self.down = self.captured = False
        if not foreground:
            return
        count = self.dll.WTPacketsGet(self.context, 256, self.buffer)
        for packet in self.buffer[:max(0, count)]:
            self.process_packet(packet)

    def process_packet(self, packet):
        self.packets += 1
        axis = self.pressure_axis
        pressure = max(0., min(1., (packet.pressure-axis.minimum)/(axis.maximum-axis.minimum)))
        contact = pressure > .001 and bool(packet.buttons & 1)
        if self.must_lift:
            if contact:
                return
            self.must_lift = False
        pos = self.local_position(packet)
        tx, ty = orientation_to_tilt(packet.orientation.azimuth, packet.orientation.altitude,
                                   self.orientation_axes[0].resolution, self.orientation_axes[1].resolution)
        self.max_pressure = max(getattr(self, 'max_pressure', 0.), pressure)
        self.max_tilt = max(getattr(self, 'max_tilt', 0.), abs(tx), abs(ty))
        self.last_packet = {'pressure': pressure, 'tilt_x': tx, 'tilt_y': ty, 'inside_board': self.board.rect().contains(pos.toPoint()),
                            'local_x': round(pos.x()), 'local_y': round(pos.y()), 'raw_x': packet.x, 'raw_y': packet.y, 'buttons': packet.buttons}
        eraser = bool(packet.status & 0x10) or packet.orientation.altitude < 0
        inside = self.board.rect().contains(pos.toPoint())
        window = getattr(self, 'window', None)
        if window is not None and not getattr(window, 'tablet_point_allowed', lambda position: True)(pos):
            if self.captured:
                self.board.pointer_up(pen=True)
            self.captured = False
            self.down = contact
            self.last_pressure = pressure
            return
        modifiers = QApplication.keyboardModifiers()
        if contact and not self.down:
            self.captured = inside
            if inside:
                if window is not None and not window.isActiveWindow():
                    window.activateWindow()
                self.board.pen_sample(QEvent.Type.TabletPress, pos, pressure, tx, ty, eraser, modifiers=modifiers)
        elif contact and self.captured:
            self.board.pen_sample(QEvent.Type.TabletMove, pos, pressure, tx, ty, eraser, modifiers=modifiers)
        elif not contact and self.down:
            if self.captured:
                self.board.pen_sample(QEvent.Type.TabletRelease, pos, 0., tx, ty, eraser, modifiers=modifiers)
            self.captured = False
        elif inside and not contact:
            self.board.pen_sample(QEvent.Type.TabletMove, pos, 0., tx, ty, eraser, modifiers=modifiers)
        self.down = contact
        self.last_pressure = pressure

    def close(self):
        self.set_enabled(False)
        if self.context:
            self.dll.WTClose(self.context)
            self.context = None

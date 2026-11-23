"""One brush cursor on canvas; ordinary cursors elsewhere and during navigation."""
import ctypes
from ctypes import wintypes as w
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication
from sketchpad.board import Board
from sketchpad.raw_input import PenMouseFilter

APP = QApplication.instance() or QApplication([])


class CursorTests(unittest.TestCase):
    def setUp(self):
        self.board = Board()

    def tearDown(self):
        self.board.deleteLater()

    def test_drawing_tools_hide_system_cursor_from_first_launch(self):
        b = self.board
        self.assertEqual(b.cursor().shape(), Qt.CursorShape.BlankCursor)
        for tool, shape in (('select', Qt.CursorShape.ArrowCursor),
                            ('eraser', Qt.CursorShape.BlankCursor),
                            ('hand', Qt.CursorShape.OpenHandCursor),
                            ('pin', Qt.CursorShape.CrossCursor),
                            ('band', Qt.CursorShape.CrossCursor),
                            ('pencil', Qt.CursorShape.BlankCursor)):
            b.set_tool(tool)
            self.assertEqual(b.cursor().shape(), shape)

    def test_pan_restores_hand_then_brush_cursor(self):
        b = self.board
        press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier)
        release = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier)
        b.keyPressEvent(press)
        self.assertEqual(b.cursor().shape(), Qt.CursorShape.OpenHandCursor)
        b.pointer_down(QPointF(100, 100), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.assertEqual(b.cursor().shape(), Qt.CursorShape.ClosedHandCursor)
        self.assertFalse(b.uses_brush_cursor())
        b.pointer_up()
        b.keyReleaseEvent(release)
        self.assertEqual(b.cursor().shape(), Qt.CursorShape.BlankCursor)
        b.set_dual(True)
        b.keyPressEvent(press)
        self.assertTrue(b.uses_brush_cursor())  # Space only pauses the independent mouse.
        self.assertEqual(b.cursor().shape(), Qt.CursorShape.BlankCursor)

    def test_tablet_press_alone_positions_brush_cursor(self):
        b = self.board
        pos = QPointF(300, 200)
        b.pen_sample(QEvent.Type.TabletPress, pos, .7, 60, 0)
        self.assertEqual(b.cursor_world, b.to_world(pos))
        self.assertEqual(b.cursor_tilt, (60, 0))

    def test_native_cursor_hiding_is_scoped_to_active_canvas(self):
        b = self.board
        window = SimpleNamespace(winId=lambda:123, isActiveWindow=lambda:True)
        filter = PenMouseFilter(window, b)
        hide = Mock()
        filter.user32 = SimpleNamespace(SetCursor=hide)
        msg = w.MSG()
        msg.hWnd, msg.message, msg.lParam = 123, 0x0020, (0x200<<16)|1
        with patch('sketchpad.raw_input.QApplication.widgetAt', return_value=b) as widget_at:
            for native in (False, True):
                b.native_pen = native
                self.assertEqual(filter.nativeEventFilter(None, ctypes.addressof(msg)), (True, 1))
            self.assertEqual(hide.call_count, 2)
            hide.assert_called_with(None)
            hide.reset_mock()
            # Sidebar, popup, window frame, inactive app and selection tool stay native.
            widget_at.return_value = None
            self.assertEqual(filter.nativeEventFilter(None, ctypes.addressof(msg)), (False, 0))
            widget_at.return_value = b
            msg.lParam = (0x200<<16)|2
            self.assertEqual(filter.nativeEventFilter(None, ctypes.addressof(msg)), (False, 0))
            msg.lParam = (0x200<<16)|1
            window.isActiveWindow = lambda:False
            self.assertEqual(filter.nativeEventFilter(None, ctypes.addressof(msg)), (False, 0))
            window.isActiveWindow = lambda:True
            b.set_tool('select')
            self.assertEqual(filter.nativeEventFilter(None, ctypes.addressof(msg)), (False, 0))
            hide.assert_not_called()


if __name__ == '__main__':
    unittest.main()

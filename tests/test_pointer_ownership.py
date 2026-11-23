"""Regression coverage for a real mouse moving guides while the pen hovers/draws."""
import unittest
from types import SimpleNamespace
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtWidgets import QApplication
from sketchpad.board import Board
from sketchpad.brush import image_array

APP = QApplication.instance() or QApplication([])


def mouse_event(pos, button=Qt.MouseButton.LeftButton):
    return SimpleNamespace(position=lambda:pos, button=lambda:button,
        modifiers=lambda:Qt.KeyboardModifier.NoModifier,
        source=lambda:Qt.MouseEventSource.MouseEventNotSynthesized)


class PointerOwnership(unittest.TestCase):
    def setUp(self):
        self.b = Board()
        self.b.zoom = 1
        self.b.offset = QPointF()

    def tearDown(self):
        self.b.deleteLater()

    def test_manual_pen_drag_of_guides_does_not_paint_or_erase(self):
        b = self.b
        # Put both guide types over actual ink, so accidental erasing is visible too.
        b.doc.papers[0].image.fill(0xff777777)
        for tool in ('pencil', 'eraser'):
            for target in (b.mouse_pin, b.doc.pins[0]):
                with self.subTest(tool=tool, target=type(target).__name__):
                    b.set_tool(tool)
                    target.x, target.y = 300, 300
                    before = image_array(b.doc.papers[0].image).copy()
                    b.pen_sample(QEvent.Type.TabletPress, QPointF(300, 300), .7, 0, 30)
                    b.pen_sample(QEvent.Type.TabletMove, QPointF(380, 360), .7, 0, 30)
                    b.pen_sample(QEvent.Type.TabletRelease, QPointF(380, 360), 0, 0, 30)
                    self.assertTrue((image_array(b.doc.papers[0].image) == before).all())
                    self.assertEqual((target.x, target.y), (380, 360))
                    b.history.undo()
                    self.assertEqual((target.x, target.y), (300, 300))
                    target.x, target.y = -100, -100

    def test_pen_draws_normally_after_releasing_guide(self):
        b = self.b
        ring = QPointF(b.mouse_pin.x, b.mouse_pin.y)
        b.pen_sample(QEvent.Type.TabletPress, ring, .7, 0, 30)
        b.pen_sample(QEvent.Type.TabletMove, ring+QPointF(100, 50), .7, 0, 30)
        b.pen_sample(QEvent.Type.TabletRelease, ring+QPointF(100, 50), 0, 0, 30)
        self.assertEqual(image_array(b.doc.papers[0].image)[..., 3].sum(), 0)
        b.pen_sample(QEvent.Type.TabletPress, QPointF(200, 200), .7, 0, 30)
        b.pen_sample(QEvent.Type.TabletMove, QPointF(250, 230), .7, 0, 30)
        b.pen_sample(QEvent.Type.TabletRelease, QPointF(250, 230), 0, 0, 30)
        self.assertGreater(image_array(b.doc.papers[0].image)[..., 3].sum(), 0)

    def test_mouse_emulation_before_pen_guide_press_does_not_paint(self):
        b = self.b
        ring = QPointF(b.mouse_pin.x, b.mouse_pin.y)
        end = ring+QPointF(100, 50)
        b.mousePressEvent(mouse_event(ring))
        b.pen_sample(QEvent.Type.TabletPress, ring, .7, 0, 30)
        b.mouseMoveEvent(mouse_event(end))
        b.pen_sample(QEvent.Type.TabletMove, end, .7, 0, 30)
        b.mouseReleaseEvent(mouse_event(end))
        b.pen_sample(QEvent.Type.TabletRelease, end, 0, 0, 30)
        self.assertEqual(image_array(b.doc.papers[0].image)[..., 3].sum(), 0)
        self.assertEqual(QPointF(b.mouse_pin.x, b.mouse_pin.y), end)

    def test_dual_mode_pen_draws_over_guide(self):
        b = self.b
        b.set_dual(True)
        ring = QPointF(b.mouse_pin.x, b.mouse_pin.y)
        b.pen_sample(QEvent.Type.TabletPress, ring, .7, 0, 30)
        b.pen_sample(QEvent.Type.TabletMove, ring+QPointF(100, 50), .7, 0, 30)
        b.pen_sample(QEvent.Type.TabletRelease, ring+QPointF(100, 50), 0, 0, 30)
        self.assertEqual(QPointF(b.mouse_pin.x, b.mouse_pin.y), ring)
        self.assertGreater(image_array(b.doc.papers[0].image)[..., 3].sum(), 0)

    def test_drawing_across_guide_does_not_grab_it(self):
        b = self.b
        ring = QPointF(b.mouse_pin.x, b.mouse_pin.y)
        b.pen_sample(QEvent.Type.TabletPress, ring-QPointF(50, 0), .7, 0, 30)
        b.pen_sample(QEvent.Type.TabletMove, ring, .7, 0, 30)
        b.pen_sample(QEvent.Type.TabletMove, ring+QPointF(50, 0), .7, 0, 30)
        b.pen_sample(QEvent.Type.TabletRelease, ring+QPointF(50, 0), 0, 0, 30)
        self.assertEqual(QPointF(b.mouse_pin.x, b.mouse_pin.y), ring)
        self.assertGreater(image_array(b.doc.papers[0].image)[..., 3].sum(), 0)

    def test_pen_hover_cannot_move_mouse_dragged_pin(self):
        b = self.b
        ring = QPointF(b.mouse_pin.x, b.mouse_pin.y)
        b.mousePressEvent(mouse_event(ring))
        b.pen_sample(QEvent.Type.TabletMove, QPointF(200, 200), 0, 20, 30)
        self.assertEqual(QPointF(b.mouse_pin.x, b.mouse_pin.y), ring)

    def test_mouse_release_is_not_dropped_after_pen_hover(self):
        b = self.b
        ring = QPointF(b.mouse_pin.x, b.mouse_pin.y)
        b.mousePressEvent(mouse_event(ring))
        b.pen_sample(QEvent.Type.TabletMove, QPointF(200, 200), 0, 20, 30)
        b.mouseReleaseEvent(mouse_event(ring))
        self.assertIsNone(b.drag)
        b.pen_sample(QEvent.Type.TabletPress, QPointF(200, 200), .6, 20, 30)
        b.pen_sample(QEvent.Type.TabletMove, QPointF(240, 220), .6, 20, 30)
        b.pen_sample(QEvent.Type.TabletRelease, QPointF(240, 220), 0, 20, 30)
        self.assertGreater(image_array(b.doc.papers[0].image)[..., 3].sum(), 0)
        self.assertEqual(QPointF(b.mouse_pin.x, b.mouse_pin.y), ring)

    def test_mouse_release_does_not_end_pen_stroke(self):
        b = self.b
        b.pen_sample(QEvent.Type.TabletPress, QPointF(200, 200), .7, 0, 30)
        b.mouseReleaseEvent(mouse_event(QPointF(100, 100)))
        self.assertIsNotNone(b.stroke)

    def test_pen_press_takes_over_unmarked_promoted_mouse_stroke(self):
        b = self.b
        b.mousePressEvent(mouse_event(QPointF(200, 200)))
        self.assertEqual(b.stroke_source, 'mouse')
        b.pen_sample(QEvent.Type.TabletPress, QPointF(200, 200), .7, 0, 30)
        self.assertEqual(b.stroke_source, 'pen')
        b.mouseReleaseEvent(mouse_event(QPointF(200, 200)))
        self.assertIsNotNone(b.stroke)

    def test_mouse_motion_does_not_draw_on_pen_stroke(self):
        b = self.b
        b.pen_sample(QEvent.Type.TabletPress, QPointF(200, 200), .7, 0, 30)
        previous = b.stroke.previous.copy()
        b.mouseMoveEvent(mouse_event(QPointF(900, 800)))
        self.assertEqual(tuple(b.stroke.previous), tuple(previous))

    def test_pause_mouse_does_not_block_new_pen_stroke(self):
        b = self.b
        b.set_dual(True)
        b.space = b.dual_paused = True
        b.pen_sample(QEvent.Type.TabletPress, QPointF(200, 200), .6, 0, 30)
        self.assertIsNotNone(b.stroke)

    def test_native_promoted_mouse_filter_keeps_physical_mouse(self):
        import ctypes
        from ctypes import wintypes as w
        from sketchpad.raw_input import PenMouseFilter
        window = SimpleNamespace(winId=lambda:123, devicePixelRatioF=lambda:1)
        board = SimpleNamespace(native_pen=True, mapFrom=lambda win,pos:pos, rect=self.b.rect)
        filter = PenMouseFilter(window, board)
        msg = w.MSG()
        msg.hWnd, msg.message, msg.lParam = 123, 0x202, (100<<16)|100
        filter.user32 = SimpleNamespace(GetMessageExtraInfo=lambda:0)
        self.assertEqual(filter.nativeEventFilter(None, ctypes.addressof(msg)), (False, 0))
        filter.user32.GetMessageExtraInfo = lambda:0xFF515700
        self.assertEqual(filter.nativeEventFilter(None, ctypes.addressof(msg)), (True, 0))
        msg.lParam = (2000<<16)|2000
        self.assertEqual(filter.nativeEventFilter(None, ctypes.addressof(msg)), (False, 0))

    def test_pen_release_does_not_end_mouse_pin_drag(self):
        b = self.b
        ring = QPointF(b.mouse_pin.x, b.mouse_pin.y)
        b.mousePressEvent(mouse_event(ring))
        b.pen_sample(QEvent.Type.TabletPress, QPointF(200, 200), .6, 0, 30)
        b.pen_sample(QEvent.Type.TabletRelease, QPointF(220, 200), 0, 0, 30)
        self.assertIsNotNone(b.drag)
        b.mouseMoveEvent(mouse_event(ring+QPointF(20, 10)))
        b.mouseReleaseEvent(mouse_event(ring+QPointF(20, 10)))
        self.assertEqual(QPointF(b.mouse_pin.x, b.mouse_pin.y), ring+QPointF(20, 10))
        self.assertIsNone(b.drag)

    def test_switching_modes_finishes_manual_drag(self):
        b = self.b
        b.mousePressEvent(mouse_event(QPointF(b.mouse_pin.x, b.mouse_pin.y)))
        b.set_dual(True)
        self.assertIsNone(b.drag)
        b.set_dual(False)
        b.pen_sample(QEvent.Type.TabletPress, QPointF(200, 200), .6, 0, 30)
        b.pen_sample(QEvent.Type.TabletMove, QPointF(250, 200), .6, 0, 30)
        b.pen_sample(QEvent.Type.TabletRelease, QPointF(250, 200), 0, 0, 30)
        self.assertGreater(image_array(b.doc.papers[0].image)[..., 3].sum(), 0)


if __name__ == '__main__':
    unittest.main()

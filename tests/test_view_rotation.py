import math
from types import SimpleNamespace
import unittest
import numpy as np
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication
from sketchpad.board import Board
from sketchpad.brush import image_array
from sketchpad.model import composite_paper

APP = QApplication.instance() or QApplication([])


class ViewRotationTests(unittest.TestCase):
    def setUp(self):
        self.b = Board()
        self.b.resize(1000,700)
        self.b.zoom = 1.
        self.b.offset = QPointF()

    def tearDown(self):
        self.b.deleteLater()

    def assertPoint(self, a, b):
        self.assertLess((a-b).manhattanLength(), 1e-7)

    def test_rotation_keeps_center_and_roundtrips_coordinates(self):
        b = self.b
        center = QPointF(500,350)
        world = b.to_world(center)
        for angle in (37, 90, -145, 180, 0):
            b.set_view_rotation(angle)
            self.assertPoint(b.to_world(center), world)
            point = QPointF(-80,740)
            self.assertPoint(b.to_world(b.to_screen(point)), point)

    def test_rotation_does_not_transform_document_or_export(self):
        b = self.b
        b.start_stroke(QPointF(400,300), .7, 0, 60)
        b.sample_stroke(QPointF(550,310), .7, 0, 60)
        b.finish_stroke()
        meta = b.doc.metadata()
        before = composite_paper(b.doc, b.doc.papers[0])
        count = b.history.count()
        b.set_view_rotation(67)
        b.reset_view_rotation()
        after = composite_paper(b.doc, b.doc.papers[0])
        self.assertEqual(b.doc.metadata(), meta)
        self.assertEqual(bytes(before.constBits()), bytes(after.constBits()))
        self.assertEqual(b.history.count(), count)

    def test_pen_position_and_tilt_stay_correct_at_rotated_view(self):
        outputs = []
        b = self.b
        for angle in (0, 37, 90, -125):
            b.doc.papers[0].image.fill(0)
            b.set_view_rotation(angle)
            tilt = math.tan(math.radians(60))
            tx = math.degrees(math.atan(-tilt*math.sin(math.radians(angle))))
            ty = math.degrees(math.atan(tilt*math.cos(math.radians(angle))))
            for i in range(11):
                kind = QEvent.Type.TabletPress if i == 0 else QEvent.Type.TabletMove
                b.pen_sample(kind, b.to_screen(QPointF(400+i*15,300)), .7, tx, ty)
            b.pen_sample(QEvent.Type.TabletRelease, b.to_screen(QPointF(550,300)), 0, tx, ty)
            outputs.append(image_array(b.doc.papers[0].image).copy())
        for out in outputs[1:]:
            self.assertLessEqual(np.abs(out.astype(int)-outputs[0].astype(int)).max(), 1)

    def test_rotated_pin_drag_does_not_draw(self):
        b = self.b
        b.mouse_pin.x, b.mouse_pin.y = 500,350
        b.set_view_rotation(53)
        start = b.to_screen(QPointF(b.mouse_pin.x,b.mouse_pin.y))
        end = start+QPointF(70,35)
        b.pen_sample(QEvent.Type.TabletPress, start, .7, 0, 50)
        b.pen_sample(QEvent.Type.TabletMove, end, .7, 0, 50)
        b.pen_sample(QEvent.Type.TabletRelease, end, 0, 0, 50)
        self.assertPoint(b.to_screen(QPointF(b.mouse_pin.x,b.mouse_pin.y)), end)
        self.assertEqual(image_array(b.doc.papers[0].image)[...,3].sum(), 0)

    def test_mouse_rotation_gesture_and_shift_snap(self):
        b = self.b
        b.set_tool('rotate')
        b.pointer_down(QPointF(700,350), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        b.pointer_move(QPointF(500,550))
        self.assertAlmostEqual(b.view_rotation, 90)
        b.pointer_move(QPointF(470,548), modifiers=Qt.KeyboardModifier.ShiftModifier)
        self.assertAlmostEqual(b.view_rotation, 105)
        b.pointer_up(pen=False)
        self.assertIsNone(b.drag)
        self.assertEqual(b.history.count(), 0)

    def test_held_r_returns_to_brush_after_pen_rotation(self):
        b = self.b
        b.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_R, Qt.KeyboardModifier.NoModifier))
        self.assertEqual(b.tool, 'rotate')
        b.pen_sample(QEvent.Type.TabletPress, QPointF(700,350), .5, 0, 0)
        b.pen_sample(QEvent.Type.TabletMove, QPointF(500,550), .5, 0, 0)
        b.pen_sample(QEvent.Type.TabletRelease, QPointF(500,550), 0, 0, 0)
        b.keyReleaseEvent(QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_R, Qt.KeyboardModifier.NoModifier))
        self.assertEqual(b.tool, 'pencil')
        self.assertAlmostEqual(b.view_rotation, 90)
        self.assertEqual(image_array(b.doc.papers[0].image)[...,3].sum(), 0)

    def test_zoom_and_fit_preserve_rotated_coordinates(self):
        b = self.b
        b.set_view_rotation(42)
        pos = QPointF(340,270)
        before = b.to_world(pos)
        b.wheelEvent(SimpleNamespace(position=lambda:pos, angleDelta=lambda:QPoint(0,120), accept=lambda:None))
        self.assertPoint(b.to_world(pos), before)
        b.fit()
        self.assertAlmostEqual(b.view_rotation, 42)
        for point in (QPointF(0,0), QPointF(1400,0), QPointF(1400,1000), QPointF(0,1000)):
            screen = b.to_screen(point)
            self.assertTrue(0 <= screen.x() <= b.width() and 0 <= screen.y() <= b.height())

    def test_independent_mouse_follows_screen_axes_when_rotated(self):
        class ActiveBoard(Board):
            def isActiveWindow(self):
                return True
        b = ActiveBoard()
        b.resize(1000,700)
        b.set_dual(True)
        b.set_view_rotation(90)
        before = b.to_screen(QPointF(b.mouse_pin.x,b.mouse_pin.y))
        b.raw_move(23,-17)
        after = b.to_screen(QPointF(b.mouse_pin.x,b.mouse_pin.y))
        self.assertPoint(after-before, QPointF(23,-17))
        b.set_tool('rotate')
        b.raw_move(50,60)
        self.assertPoint(b.to_screen(QPointF(b.mouse_pin.x,b.mouse_pin.y)), after)
        b.deleteLater()


if __name__ == '__main__':
    unittest.main()

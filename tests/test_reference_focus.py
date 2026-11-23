"""Floating-image gestures must never edit source sheets or their undo history."""
import unittest
from types import SimpleNamespace

from PySide6.QtCore import Qt, QEvent, QPoint, QPointF
from PySide6.QtGui import QColor, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from sketchpad.board import Board
from sketchpad.model import Document, Paper
from sketchpad.reference_window import ReferenceWindow

APP = QApplication.instance() or QApplication([])
LEFT, NONE = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier


class ReferenceFocusTests(unittest.TestCase):
    def setUp(self):
        self.source = Board()
        self.source.desk_name, self.source.desk_id = 'Focus test', 'focus-test'
        self.paper = Paper(name='Picture', width=400, height=240, kind='reference')
        self.paper.image.fill(QColor('#c04e32'))
        self.other = Paper(name='Other', x=500, width=200, height=240, kind='reference')
        self.other.image.fill(QColor('#4477bb'))
        self.source.doc = Document(False)
        self.source.doc.papers = [self.paper, self.other]
        self.source.set_selection(self.paper)
        self.ref = ReferenceWindow(SimpleNamespace(), self.source)
        self.ref.setGeometry(80, 100, 720, 520)
        self.ref.show()
        self.ref.activateWindow()
        APP.processEvents()
        self.ref.canvas.fit()
        self.before = self.source.doc.metadata()

    def tearDown(self):
        self.ref.close()
        self.ref.deleteLater()
        self.source.deleteLater()
        APP.processEvents()

    def enter_by_double_click(self, paper=None):
        paper = paper or self.paper
        point = self.ref.canvas.to_screen(paper.transform().map(QPointF(paper.width/2, paper.height/2)))
        QTest.mouseDClick(self.ref.canvas, LEFT, NONE, point.toPoint())
        APP.processEvents()
        self.assertIs(self.ref.focus_paper, paper)
        self.assertEqual(self.ref.focus_view.size(), self.ref.size())
        self.assertTrue(self.ref.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(self.ref.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertFalse(self.ref.canvas.isVisible())
        self.assertFalse(self.ref.toolbar.isVisible())
        self.assertFalse(self.ref.status.isVisible())

    def assert_source_untouched(self):
        self.assertEqual(self.source.doc.metadata(), self.before)
        self.assertEqual(self.source.history.count(), 0)
        self.assertEqual(self.paper.image.pixelColor(0, 0), QColor('#c04e32'))

    def test_only_picture_rendered_and_double_click_restores_camera(self):
        canvas = self.ref.canvas
        canvas.view_rotation = 9
        canvas.offset += QPointF(13, 8)
        camera = (canvas.zoom, QPointF(canvas.offset), canvas.view_rotation)
        geometry = self.ref.geometry()
        self.enter_by_double_click()
        image = self.ref.grab().toImage()
        for point in (QPoint(0, 0), image.rect().center(), image.rect().bottomRight()):
            self.assertEqual(image.pixelColor(point), QColor('#c04e32'))
        QTest.mouseDClick(self.ref.focus_view, LEFT, NONE, self.ref.focus_view.rect().center())
        APP.processEvents()
        self.assertIsNone(self.ref.focus_paper)
        self.assertEqual(self.ref.geometry(), geometry)
        self.assertEqual((canvas.zoom, canvas.offset, canvas.view_rotation), camera)
        self.assertTrue(canvas.isVisible() and self.ref.toolbar.isVisible() and self.ref.status.isVisible())
        self.assertFalse(self.ref.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assert_source_untouched()

    def test_drag_moves_window_and_wheel_scales_around_cursor(self):
        self.enter_by_double_click()
        view = self.ref.focus_view
        origin = self.ref.pos()
        local = QPointF(view.rect().center())
        global_pos = QPointF(view.mapToGlobal(local.toPoint()))
        QTest.mousePress(view, LEFT, NONE, local.toPoint())
        delta = QPointF(42, 29)
        event = QMouseEvent(QEvent.Type.MouseMove, local+delta, global_pos+delta,
                            Qt.MouseButton.NoButton, LEFT, NONE)
        QApplication.sendEvent(view, event)
        self.assertEqual(self.ref.pos(), origin+delta.toPoint())
        QTest.mouseRelease(view, LEFT, NONE, local.toPoint())
        self.assertIsNone(view.drag_anchor)
        size = self.ref.size()
        anchor = QPointF(self.ref.x()+self.ref.width()*.25, self.ref.y()+self.ref.height()*.75)
        wheel = QWheelEvent(anchor-QPointF(self.ref.pos()), anchor, QPoint(), QPoint(0, 120),
                            Qt.MouseButton.NoButton, NONE, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(view, wheel)
        APP.processEvents()
        self.assertGreater(self.ref.width(), size.width())
        self.assertLess(abs(self.ref.width()/self.ref.height()-400/240), .02)
        self.assertLess(abs((anchor.x()-self.ref.x())/self.ref.width()-.25), .005)
        self.assertLess(abs((anchor.y()-self.ref.y())/self.ref.height()-.75), .005)
        self.assert_source_untouched()

    def test_escape_and_close_return_to_normal_and_keep_top_setting(self):
        self.ref.top.setChecked(False)
        self.ref.toggle_top(False)
        self.ref.enter_focus()
        APP.processEvents()
        self.assertFalse(self.ref.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        QTest.keyClick(self.ref.focus_view, Qt.Key.Key_Escape)
        self.assertIsNone(self.ref.focus_paper)
        self.assertTrue(self.ref.isVisible())
        self.ref.enter_focus()
        self.ref.close()
        self.assertFalse(self.ref.isVisible())
        self.assertIsNone(self.ref.focus_paper)
        self.ref.show()
        self.assertTrue(self.ref.canvas.isVisible())
        self.assertFalse(self.ref.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assert_source_untouched()

    def test_deleting_target_or_replacing_document_exits_focus(self):
        self.enter_by_double_click()
        self.source.doc.papers.remove(self.paper)
        self.source.changed.emit()
        self.assertIsNone(self.ref.focus_paper)
        self.assertTrue(self.ref.canvas.isVisible())
        self.enter_by_double_click(self.other)
        self.source.doc = Document(False)
        self.source.changed.emit()
        self.assertIsNone(self.ref.focus_paper)
        self.assertIs(self.ref.canvas.doc, self.source.doc)

    def test_preview_tracks_source_edits_and_preserves_transparent_pixels(self):
        self.enter_by_double_click()
        self.paper.image.fill(QColor('#339977'))
        self.source.changed.emit()
        APP.processEvents()
        image = self.ref.grab().toImage()
        self.assertEqual(image.pixelColor(image.rect().center()), QColor('#339977'))
        self.paper.image.fill(Qt.GlobalColor.transparent)
        self.paper.image.setPixelColor(200, 120, QColor('#339977'))
        self.source.changed.emit()
        APP.processEvents()
        self.assertEqual(self.ref.grab().toImage().pixelColor(0, 0).alpha(), 0)

    def test_focus_ignores_edit_keys_and_handles_no_selection(self):
        self.ref.canvas.set_selection(None)
        self.ref.enter_focus()
        self.assertIsNone(self.ref.focus_paper)
        self.enter_by_double_click()
        for key, modifiers in ((Qt.Key.Key_Delete, NONE), (Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier),
                               (Qt.Key.Key_R, NONE), (Qt.Key.Key_Alt, NONE)):
            QTest.keyClick(self.ref.focus_view, key, modifiers)
        self.assert_source_untouched()
        self.assertEqual(self.ref.canvas.tool, 'select')


if __name__ == '__main__':
    unittest.main()

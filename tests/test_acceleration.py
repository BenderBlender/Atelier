import math
import unittest
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage
from sketchpad import brush, brush_native
from sketchpad.board import Board
from sketchpad.model import Paper

APP = QApplication.instance() or QApplication([])


class CompiledBrushTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not brush_native.warmup():
            raise unittest.SkipTest(brush_native.ERROR)

    def test_matches_reference_with_tilt_color_edges_and_both_erasers(self):
        for erase, shape in ((False, 'pencil'), (True, 'pencil'), (True, 'round')):
            results = []
            for native in (False, True):
                image = brush.empty_ink(260, 200)
                image.fill(QColor(60, 85, 120, 140))
                settings = brush.BrushSettings(size=5., sharpening_length=10.,
                    eraser=erase, eraser_shape=shape, color=(255, 255, 255))
                stroke = brush.GraphiteStroke(image, settings)
                stroke.native = native
                for i in range(60):
                    stroke.add(-15+i*4, 55+30*math.sin(i*.09), .2+.7*i/60, 60-i, 57.)
                results.append(brush.image_array(image).copy())
            a, b = results
            self.assertLessEqual(np.abs(a[..., 3].astype(int)-b[..., 3]).max(), 1)
            visible = [x[..., :3].astype(float)*x[..., 3:]/255 for x in results]
            self.assertLessEqual(np.abs(visible[0]-visible[1]).max(), 1.1)


class ViewportCacheTests(unittest.TestCase):
    def setUp(self):
        self.board = Board()
        self.board.resize(900, 600)
        self.board.zoom = .5
        self.board.offset = QPointF(80, 45)
        for i in range(8):
            p = Paper(x=i*8, y=i*4, paper_opacity=.15, tracing=True)
            p.image.fill(QColor(20+i*7, 80, 100, 30))
            self.board.doc.papers.append(p)
        self.board.selected = self.board.doc.papers[4]

    def tearDown(self):
        self.board.deleteLater()

    def test_unchanged_stacks_reused_active_ink_does_not_rebuild(self):
        b = self.board
        b.grab()
        builds = b.viewport_cache.builds
        b.selected.image.fill(QColor(255, 255, 255, 50))
        b.grab()
        self.assertEqual(b.viewport_cache.builds, builds)
        b.doc.papers[0].image.fill(QColor(200, 10, 20, 100))
        b.grab()
        self.assertEqual(b.viewport_cache.builds, builds+1)
        b.view_rotation = 17
        b.grab()
        self.assertEqual(b.viewport_cache.builds, builds+3)

    def test_transparent_stack_matches_uncached_view(self):
        results = []
        for cached in (False, True):
            self.board.use_viewport_cache = cached
            image = self.board.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            results.append(brush.image_array(image)[100:450, 140:700].copy())
        difference = np.abs(results[0].astype(int)-results[1])
        # Integer alpha composition can round differently when flattening a group.
        self.assertLessEqual(difference.max(), 4)
        self.assertLess(difference.mean(), 1.)

    def test_hidden_board_releases_view_cache(self):
        self.board.show()
        self.board.grab()
        self.assertTrue(self.board.viewport_cache.entries)
        self.board.hide()
        self.assertFalse(self.board.viewport_cache.entries)

import unittest
import numpy as np
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication
from sketchpad.brush import BrushSettings, GraphiteStroke, empty_ink, image_array, paper_tooth
from sketchpad.board import Board
from sketchpad.model import composite_paper

APP = QApplication.instance() or QApplication([])


def filled(alpha=180, color=(45,43,40), size=160):
    image = empty_ink(size, size)
    image_array(image)[...] = (*color, alpha)
    return image


class KneadedEraserTests(unittest.TestCase):
    def test_lifting_follows_paper_grain_and_preserves_pigment_color(self):
        image = filled()
        before = image_array(image).copy()
        stroke = GraphiteStroke(image, BrushSettings(eraser=True, size=55))
        stroke.dab(80, 80, .7, 0, 0)
        after = image_array(image)
        center = after[70:90, 70:90, 3]
        self.assertGreater(center.std(), 6)
        self.assertGreater(np.corrcoef(center.ravel(), paper_tooth()[70:90, 70:90].ravel())[0,1], .9)
        self.assertTrue((after[...,3] <= before[...,3]).all())
        np.testing.assert_array_equal(after[0], before[0])
        np.testing.assert_array_equal(after[...,:3], before[...,:3])
        self.assertGreater(center.min(), 0)

    def test_pressure_strength_and_new_passes_progressively_lift_tone(self):
        amounts = []
        for pressure, strength in ((.2,.65), (.9,.65), (.9,1.)):
            image = filled()
            s = GraphiteStroke(image, BrushSettings(eraser=True, size=50, eraser_strength=strength))
            s.dab(80, 80, pressure, 0, 0)
            amounts.append(image_array(image)[70:90,70:90,3].mean())
        self.assertGreater(amounts[0], amounts[1])
        self.assertGreater(amounts[1], amounts[2])
        first = amounts[-1]
        s = GraphiteStroke(image, BrushSettings(eraser=True, size=50, eraser_strength=1.))
        s.dab(80, 80, .9, 0, 0)
        self.assertLess(image_array(image)[70:90,70:90,3].mean(), first)
        self.assertGreater(image_array(image)[70:90,70:90,3].min(), 0)

    def test_gentle_lifting_accumulates_below_one_alpha_step(self):
        image = filled(alpha=20)
        s = GraphiteStroke(image, BrushSettings(eraser=True, size=70, eraser_strength=.2))
        for _ in range(240):
            s.dab(80, 80, .15, 0, 0, flow=.01)
        self.assertLess(image_array(image)[70:90,70:90,3].mean(), 19)

    def test_blank_pixels_and_zero_pressure_do_not_gain_marks(self):
        image = empty_ink(80,80)
        s = GraphiteStroke(image, BrushSettings(eraser=True, size=30))
        s.dab(40, 40, 1, 0, 0)
        self.assertEqual(image_array(image).sum(), 0)
        image = filled()
        before = bytes(image.constBits())
        s = GraphiteStroke(image, BrushSettings(eraser=True, size=30))
        s.dab(80, 80, 0, 0, 0)
        self.assertEqual(bytes(image.constBits()), before)

    def test_white_and_graphite_lift_equally_without_changing_paper(self):
        results = []
        for color in ((45,43,40), (255,255,255)):
            b = Board()
            paper = b.doc.papers[0]
            paper.image = filled(color=color)
            paper.width = paper.height = 160
            b.set_paper_style('kraft')
            b.set_tool('eraser')
            b.eraser_size = 45
            b.start_stroke(QPointF(80,80), .6, 0, 0)
            b.finish_stroke()
            self.assertEqual(paper.paper_style, 'kraft')
            results.append(image_array(paper.image)[...,3].copy())
            b.clear_paper()
            blank = composite_paper(b.doc, paper)
            self.assertGreater(blank.pixelColor(70,70).red(), 100)
            self.assertEqual(image_array(paper.image)[...,3].sum(), 0)
            b.deleteLater()
        np.testing.assert_array_equal(*results)

    def test_undo_redo_restores_exact_pixels_across_tile_edges(self):
        b = Board()
        paper = b.doc.papers[0]
        paper.image = filled(size=300)
        paper.width = paper.height = 300
        before = bytes(paper.image.constBits())
        b.set_tool('eraser')
        b.eraser_size = 65
        b.start_stroke(QPointF(100,100), .6, 0, 0)
        b.sample_stroke(QPointF(260,260), .6, 0, 0)
        b.finish_stroke()
        after = bytes(paper.image.constBits())
        self.assertNotEqual(before, after)
        b.history.undo()
        self.assertEqual(bytes(paper.image.constBits()), before)
        b.history.redo()
        self.assertEqual(bytes(paper.image.constBits()), after)
        b.deleteLater()


if __name__ == '__main__':
    unittest.main()

"""Subtractive pencil behavior, including shared controls and tablet paths."""
import math
import unittest
import numpy as np
from PySide6.QtCore import QEvent, QPointF
from PySide6.QtWidgets import QApplication
from sketchpad.board import Board
from sketchpad.brush import BrushSettings, GraphiteStroke, empty_ink, image_array

APP = QApplication.instance() or QApplication([])


def filled(size=240, color=(45, 43, 40)):
    image = empty_ink(size, size)
    image_array(image)[...] = (*color, 200)
    return image


def lift(tx=65, ty=0, **settings):
    image = filled()
    stroke = GraphiteStroke(image, BrushSettings(eraser=True, eraser_shape='pencil',
                                                size=10, grain=0, **settings))
    stroke.dab(120, 120, .7, tx, ty)
    return 200-image_array(image)[..., 3].copy()


class PencilEraserTests(unittest.TestCase):
    def test_tilt_lifts_a_tapered_flank_behind_stationary_nib(self):
        upright, tilted = lift(tx=0), lift()
        np.testing.assert_array_equal(upright[:, :120], tilted[:, :120])
        ys, xs = np.where(tilted > 0)
        self.assertLess(120-xs.min(), 5)
        self.assertGreater(xs.max()-120, 30)
        self.assertGreater(np.count_nonzero(tilted[:, 145]), np.count_nonzero(tilted[:, 120])*2)
        self.assertGreater(tilted[120, 120], tilted[120, 145])
        self.assertGreater(lift(tx=-65)[:, :100].sum(), 0)
        self.assertEqual(lift(tx=-65)[:, 125:].sum(), 0)

    def test_sharpness_changes_fine_erased_line_width(self):
        widths = []
        for sharpness in (0, .5, 1):
            image = filled()
            stroke = GraphiteStroke(image, BrushSettings(size=8, grain=0, smoothing=0,
                eraser=True, eraser_shape='pencil', tip_sharpness=sharpness))
            for x in range(20, 201):
                stroke.add(x, 100, .7)
            widths.append(np.count_nonzero(image_array(image)[:, 100, 3] < 200))
        self.assertGreater(widths[0], widths[1])
        self.assertGreater(widths[1], widths[2])

    def test_erased_and_drawn_paths_share_grain_and_shape(self):
        positive, negative = empty_ink(240, 240), filled()
        for image, eraser in ((positive, False), (negative, True)):
            stroke = GraphiteStroke(image, BrushSettings(size=9, smoothing=0, grain=.8,
                eraser=eraser, eraser_shape='pencil', eraser_strength=.35))
            for y in range(40, 200):
                stroke.add(100, y, .6, 65, 0)
        deposited = image_array(positive)[..., 3].astype(float)
        removed = 200-image_array(negative)[..., 3].astype(float)
        region = np.s_[60:180, 95:135]
        self.assertGreater(np.corrcoef(deposited[region].ravel(), removed[region].ravel())[0, 1], .95)
        # Neither broad shading nor removal leaks to the sharp side of the nib.
        self.assertEqual(removed[:, :96].sum(), 0)
        self.assertGreater(removed[region].std(), 5)

    def test_strength_pressure_and_repeated_passes_lift_more(self):
        remaining = []
        for pressure, strength in ((.2, .4), (.8, .4), (.8, 1.)):
            image = filled()
            settings = BrushSettings(size=12, eraser=True, eraser_shape='pencil', eraser_strength=strength)
            GraphiteStroke(image, settings).dab(120, 120, pressure, 0, 65)
            remaining.append(image_array(image)[..., 3].sum())
        self.assertGreater(remaining[0], remaining[1])
        self.assertGreater(remaining[1], remaining[2])
        GraphiteStroke(image, settings).dab(120, 120, .8, 0, 65)
        self.assertLess(image_array(image)[..., 3].sum(), remaining[-1])

    def test_blank_paper_and_pigment_colors_are_preserved(self):
        settings = BrushSettings(size=12, eraser=True, eraser_shape='pencil')
        blank = empty_ink(240, 240)
        GraphiteStroke(blank, settings).dab(120, 120, 1, 0, 65)
        self.assertEqual(image_array(blank).sum(), 0)
        results = []
        for color in ((45, 43, 40), (255, 255, 255)):
            image = filled(color=color)
            before = image_array(image).copy()
            GraphiteStroke(image, settings).dab(120, 120, .8, 0, 65)
            after = image_array(image)
            np.testing.assert_array_equal(after[..., :3], before[..., :3])
            self.assertTrue((after[..., 3] <= before[..., 3]).all())
            self.assertGreater(after[..., 3].min(), 0)
            results.append(after[..., 3].copy())
        np.testing.assert_array_equal(*results)

    def test_board_uses_shared_pencil_controls_for_tool_and_pen_reverse(self):
        b = Board()
        b.brush.size, b.brush.tip_sharpness, b.brush.smoothing = 9, .83, .4
        b.brush.eraser_shape = 'pencil'
        b.eraser_size = 80
        outputs = []
        for reverse in (False, True):
            b.doc.papers[0].image = filled()
            b.set_tool('pencil' if reverse else 'eraser')
            b.start_stroke(QPointF(120, 120), .7, 50, 20, eraser=reverse)
            self.assertEqual(b.stroke.settings.size, 9)
            self.assertEqual(b.stroke.settings.tip_sharpness, .83)
            self.assertEqual(b.stroke.settings.smoothing, .4)
            b.sample_stroke(QPointF(140, 155), .7, 50, 20)
            b.finish_stroke()
            outputs.append(image_array(b.doc.papers[0].image).copy())
        np.testing.assert_array_equal(*outputs)
        b.brush.eraser_shape = 'round'
        b.start_stroke(QPointF(120, 120), .7, 50, 20, eraser=True)
        self.assertEqual(b.stroke.settings.size, 80)
        self.assertEqual(b.stroke.settings.smoothing, 0)
        b.finish_stroke()
        b.deleteLater()

    def test_rotated_two_hand_pen_erases_and_undo_restores_exact_pixels(self):
        b = Board()
        b.resize(1000, 700)
        b.brush.eraser_shape = 'pencil'
        b.brush.size = 12
        b.set_tool('eraser')
        b.set_dual(True)
        outputs = []
        paper = b.doc.papers[0]
        paper.paper_style = 'anthracite'
        for angle in (0, 37, 90, -125):
            paper.image = filled(size=300)
            b.set_view_rotation(angle)
            before = bytes(paper.image.constBits())
            tilt = math.tan(math.radians(60))
            tx = math.degrees(math.atan(-tilt*math.sin(math.radians(angle))))
            ty = math.degrees(math.atan(tilt*math.cos(math.radians(angle))))
            for i in range(11):
                kind = QEvent.Type.TabletPress if i == 0 else QEvent.Type.TabletMove
                b.pen_sample(kind, b.to_screen(QPointF(90+i*16, 100)), .7, tx, ty)
            b.pen_sample(QEvent.Type.TabletRelease, b.to_screen(QPointF(250, 100)), 0, tx, ty)
            after = bytes(paper.image.constBits())
            self.assertNotEqual(before, after)
            self.assertEqual(paper.paper_style, 'anthracite')
            outputs.append(image_array(paper.image).copy())
            b.history.undo()
            self.assertEqual(bytes(paper.image.constBits()), before)
            b.history.redo()
            self.assertEqual(bytes(paper.image.constBits()), after)
        for out in outputs[1:]:
            self.assertLessEqual(np.abs(out.astype(int)-outputs[0].astype(int)).max(), 1)
        b.deleteLater()


if __name__ == '__main__':
    unittest.main()

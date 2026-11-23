"""Pixel-level requirements for a sharp nib and asymmetric side shading."""
import unittest
import numpy as np
from sketchpad.brush import BrushSettings, GraphiteStroke, empty_ink, image_array, pencil_contact


def stamp(tx=65, ty=0, pressure=.7, **settings):
    image = empty_ink(240, 240)
    stroke = GraphiteStroke(image, BrushSettings(size=10, grain=0, **settings))
    stroke.dab(120, 120, pressure, tx, ty)
    return image_array(image)[..., 3].copy()


class PencilContactTests(unittest.TestCase):
    def test_sharpness_changes_nib_without_resizing_open_flank(self):
        blunt, sharp = stamp(tip_sharpness=0), stamp(tip_sharpness=1)
        self.assertGreater(np.count_nonzero(blunt[:, 120]),
                           np.count_nonzero(sharp[:, 120])*3)
        a = pencil_contact(BrushSettings(size=10, tip_sharpness=0), .7, 65, 0)
        b = pencil_contact(BrushSettings(size=10, tip_sharpness=1), .7, 65, 0)
        self.assertAlmostEqual(a.width, b.width)
        self.assertAlmostEqual(a.length, b.length)

    def test_sharpness_changes_upright_line_width(self):
        widths = []
        for sharpness in (0, .5, 1):
            image = empty_ink(200, 100)
            stroke = GraphiteStroke(image, BrushSettings(size=8, grain=0, tip_sharpness=sharpness))
            for x in range(20, 181):
                stroke.add(x, 50, .7)
            widths.append(np.count_nonzero(image_array(image)[:, 100, 3]))
        self.assertGreater(widths[0], widths[1])
        self.assertGreater(widths[1], widths[2])

    def test_sharpness_does_not_change_eraser(self):
        outputs = []
        for sharpness in (0, 1):
            image = empty_ink(80, 80)
            image.fill(0xff888888)
            stroke = GraphiteStroke(image, BrushSettings(size=15, eraser=True, tip_sharpness=sharpness))
            stroke.dab(40, 40, .7, 65, 0)
            outputs.append(image_array(image).copy())
        np.testing.assert_array_equal(*outputs)

    def test_tilt_keeps_nib_edge_at_pointer_and_expands_only_behind_it(self):
        upright, tilted = stamp(tx=0), stamp()
        # All pixels on the sharp side of the nib are exactly unchanged by tilt.
        np.testing.assert_array_equal(upright[:, :120], tilted[:, :120])
        ys, xs = np.where(tilted > 0)
        self.assertLess(120-xs.min(), 5)
        self.assertGreater(xs.max()-120, 30)

    def test_contact_is_narrow_at_nib_and_broad_on_flank(self):
        alpha = stamp()
        nib_width = np.count_nonzero(alpha[:, 120])
        flank_width = np.count_nonzero(alpha[:, 145])
        self.assertGreater(flank_width, nib_width*2)
        self.assertGreater(alpha[120, 120], alpha[120, 145])

    def test_reversing_tilt_mirrors_contact_around_stationary_nib(self):
        np.testing.assert_array_equal(stamp(tx=65), np.fliplr(stamp(tx=-65)))

    def test_quarter_turn_rotates_contact_around_nib(self):
        np.testing.assert_array_equal(stamp(tx=65), stamp(tx=0, ty=65).T)

    def test_upright_and_disabled_tilt_have_round_contacts(self):
        upright = stamp(tx=0)
        np.testing.assert_array_equal(upright, np.fliplr(upright))
        np.testing.assert_array_equal(upright, upright.T)
        np.testing.assert_array_equal(upright, stamp(tilt_gain=0))

    def test_crosswise_shading_has_sharp_edge_at_pen_path(self):
        image = empty_ink(240, 240)
        stroke = GraphiteStroke(image, BrushSettings(size=8, grain=0, smoothing=0))
        for y in range(40, 201):
            stroke.add(100, y, .7, 65, 0)
        alpha = image_array(image)[120, :, 3]
        xs = np.flatnonzero(alpha)
        self.assertLess(100-xs.min(), 4)
        self.assertGreater(xs.max()-100, 25)
        self.assertGreater(int(alpha[100]), int(alpha[123]))

    def test_small_light_nib_still_leaves_a_mark(self):
        image = empty_ink(40, 40)
        stroke = GraphiteStroke(image, BrushSettings(size=.5, tip_sharpness=1))
        stroke.dab(20, 20, .1, 0, 0)
        self.assertGreater(image_array(image)[..., 3].sum(), 0)

    def test_eraser_remains_round_at_every_tilt(self):
        outputs = []
        for tx, ty in ((0, 0), (65, 0), (0, -65)):
            image = empty_ink(80, 80)
            image.fill(0xff888888)
            stroke = GraphiteStroke(image, BrushSettings(size=15, eraser=True))
            stroke.dab(40, 40, .7, tx, ty)
            outputs.append(image_array(image).copy())
        for alpha in outputs[1:]:
            np.testing.assert_array_equal(alpha, outputs[0])


if __name__ == '__main__':
    unittest.main()

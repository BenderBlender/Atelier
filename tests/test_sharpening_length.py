"""Exposed graphite length controls side shading independently of the nib."""
import unittest
import numpy as np
from sketchpad.brush import BrushSettings, GraphiteStroke, empty_ink, image_array, pencil_contact


def stamp(length, tx=65, **settings):
    image = empty_ink(320, 240)
    if settings.get('eraser'):
        image_array(image)[...] = (45, 43, 40, 200)
    stroke = GraphiteStroke(image, BrushSettings(size=10, grain=0, sharpening_length=length, **settings))
    stroke.dab(120, 120, .7, tx, 0)
    alpha = image_array(image)[..., 3].copy()
    return 200-alpha if settings.get('eraser') else alpha


class SharpeningLengthTests(unittest.TestCase):
    def test_upright_nib_and_disabled_tilt_remain_identical(self):
        for sharpness in (0, .5, 1):
            reference = stamp(1, tx=0, tip_sharpness=sharpness)
            for length in (.25, 2.5, 10):
                np.testing.assert_array_equal(reference, stamp(length, tx=0, tip_sharpness=sharpness))
                np.testing.assert_array_equal(reference, stamp(length, tilt_gain=0, tip_sharpness=sharpness))

    def test_longer_sharpening_extends_flank_without_thickening_nib(self):
        stamps = [stamp(length) for length in (.25, 1, 2.5)]
        for output in stamps[1:]:
            np.testing.assert_array_equal(output[:, :120], stamps[0][:, :120])
        extents = [np.where(output > 0)[1].max() for output in stamps]
        self.assertLess(extents[0], extents[1])
        self.assertLess(extents[1], extents[2])
        # The flank's transverse breadth stays constant while its length grows.
        breadths = [np.ptp(np.where(output > 0)[0]) for output in stamps]
        self.assertLessEqual(max(breadths)-min(breadths), 2)
        wide = stamp(1, tilt_gain=10)
        self.assertGreater(np.ptp(np.where(wide > 0)[0]), max(breadths))

    def test_side_shading_width_changes_in_both_directions_of_tilt(self):
        for tilt in (-65, 65):
            widths = []
            for length in (.25, 1, 2.5):
                image = empty_ink(340, 260)
                stroke = GraphiteStroke(image, BrushSettings(size=8, smoothing=0, grain=0, sharpening_length=length))
                for x in range(30, 310):
                    stroke.add(x, 125, .7, 0, tilt)
                widths.append(np.count_nonzero(image_array(image)[:, 160, 3]))
            self.assertGreater(widths[1], widths[0]*1.5)
            self.assertGreater(widths[2], widths[1]*1.5)

    def test_pencil_eraser_uses_length_and_round_eraser_is_unchanged(self):
        short = stamp(.25, eraser=True, eraser_shape='pencil')
        long = stamp(2.5, eraser=True, eraser_shape='pencil')
        np.testing.assert_array_equal(short[:, :120], long[:, :120])
        self.assertGreater(np.where(long > 0)[1].max(), np.where(short > 0)[1].max()+40)
        np.testing.assert_array_equal(stamp(.25, eraser=True), stamp(2.5, eraser=True))

    def test_1000_percent_extends_actual_ink_not_only_cursor(self):
        outputs = []
        for length in (2.5, 10):
            image = empty_ink(800, 240)
            settings = BrushSettings(size=10, grain=0, sharpening_length=length)
            stroke = GraphiteStroke(image, settings)
            stroke.dab(100, 120, .7, 65, 0)
            outputs.append(image_array(image)[..., 3].copy())
        np.testing.assert_array_equal(outputs[0][:, :100], outputs[1][:, :100])
        lengths = [np.where(output > 0)[1].max()-100 for output in outputs]
        self.assertGreater(lengths[1], lengths[0]*3.5)
        normal = pencil_contact(BrushSettings(sharpening_length=1), .7, 65, 0)
        long = pencil_contact(BrushSettings(sharpening_length=10), .7, 65, 0)
        self.assertAlmostEqual(long.length, normal.length*10)
        self.assertEqual(long.tip, normal.tip)
        self.assertEqual(long.width, normal.width)


if __name__ == '__main__':
    unittest.main()

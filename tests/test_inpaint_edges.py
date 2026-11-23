import tempfile
from pathlib import Path
import unittest
import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPainter, QImage
from PySide6.QtWidgets import QApplication
from sketchpad.brush import empty_ink, image_array
from sketchpad.inpaint import feather_alpha, merge_inpaint
from sketchpad.board import Board
from sketchpad.model import Paper, composite_paper, png_bytes
from sketchpad.assistant_panel import Source, place_result, saved_receipts, restore_receipts

APP = QApplication.instance() or QApplication([])


def rect_mask(width=240, height=200):
    mask = empty_ink(width, height)
    p = QPainter(mask)
    p.fillRect(20, 20, width-40, height-40, QColor('red'))
    p.end()
    return mask


class InpaintEdgeTests(unittest.TestCase):
    def test_contour_starts_near_zero_and_rises_smoothly_to_full_opacity(self):
        mask = rect_mask()
        alpha = feather_alpha(mask, 40)
        self.assertLess(alpha[100, 20], .001)
        self.assertEqual(alpha[100, 19], 0)
        self.assertEqual(alpha[100, 65], 1)
        row = alpha[100, 20:66]
        self.assertTrue(np.all(np.diff(row) >= 0))
        self.assertLess(np.diff(row).max(), .04)
        # The previous clipped blur started at roughly .5 at this boundary.

    def test_holes_disconnected_small_regions_and_full_canvas(self):
        mask = rect_mask()
        p = QPainter(mask)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.fillRect(95, 75, 50, 50, QColor('red'))
        p.end()
        image_array(mask)[2:4, 2:4] = (255, 0, 0, 255)
        alpha = feather_alpha(mask, 200)
        self.assertEqual(alpha[90, 100], 0)
        self.assertGreater(alpha[2:4, 2:4].max(), .99)
        self.assertTrue(np.all(alpha[image_array(mask)[..., 3] == 0] == 0))
        mask.fill(QColor('red'))
        np.testing.assert_array_equal(feather_alpha(mask, 200), np.ones((200, 240)))

    def test_disabled_softness_is_exact_and_inputs_are_not_mutated(self):
        old, new, mask = empty_ink(240, 200), empty_ink(240, 200), rect_mask()
        old.fill(QColor('#ddc9a9'))
        new.fill(QColor('#efdcbf'))
        before = (png_bytes(old), png_bytes(new), png_bytes(mask))
        result = merge_inpaint(old, new, mask, 0, True)
        active = image_array(mask)[..., 3] > 0
        np.testing.assert_array_equal(image_array(result)[active], image_array(new)[active])
        np.testing.assert_array_equal(image_array(result)[~active], image_array(old)[~active])
        merge_inpaint(old, new, mask, 40, True)
        self.assertEqual(before, (png_bytes(old), png_bytes(new), png_bytes(mask)))

    def test_tone_matching_reduces_background_drift_but_preserves_central_color(self):
        old, new, mask = empty_ink(400, 300), empty_ink(400, 300), rect_mask(400, 300)
        old.fill(QColor('#d8c7ab'))
        new.fill(QColor('#e6d5b9'))
        p = QPainter(new)
        p.fillRect(175, 125, 50, 50, QColor('#397b36'))
        p.end()
        plain = merge_inpaint(old, new, mask, 40, False)
        matched = merge_inpaint(old, new, mask, 40, True)
        baseline = image_array(old)[150, 45, :3].astype(int)
        plain_error = np.abs(image_array(plain)[150, 45, :3].astype(int)-baseline).mean()
        matched_error = np.abs(image_array(matched)[150, 45, :3].astype(int)-baseline).mean()
        self.assertLess(matched_error, plain_error*.5)
        np.testing.assert_array_equal(image_array(matched)[140:160, 190:210], image_array(new)[140:160, 190:210])

    def test_variant_is_above_previous_result_keeps_outside_strokes_and_undo(self):
        board = Board()
        original = Paper(width=240, height=200)
        board.doc.papers = [original]
        board.selected = original
        mask = rect_mask()
        source = Source(board, board.doc, original.id, 'source', 'edit', mask, 40, True)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'result.png'
            raw = empty_ink(240, 200)
            raw.fill(QColor('#e6d5b9'))
            raw.save(str(path))
            first = place_result(source, path, 'first')
            first_file = (path.parent/'inpaint.png').read_bytes()
            p = QPainter(first.image)
            p.fillRect(1, 1, 8, 8, QColor('red'))
            p.end()
            previous = png_bytes(first.image)
            source.feather = 100
            second = place_result(source, path, 'second', after=first, output_name='inpaint-soft-test.png')
            self.assertIs(board.doc.papers[-1], second)
            self.assertEqual(png_bytes(first.image), previous)
            np.testing.assert_array_equal(image_array(second.image)[1:9, 1:9], image_array(first.image)[1:9, 1:9])
            self.assertEqual(first_file, (path.parent/'inpaint.png').read_bytes())
            self.assertTrue(Path(second.inpaint_receipt['mask_path']).is_file())
            self.assertEqual(second.inpaint_receipt['raw_path'], str(path))
            records = saved_receipts(board)
            del second.inpaint_receipt
            restore_receipts(board, records, [Path(temp)])
            self.assertEqual(second.inpaint_receipt['raw_path'], str(path.resolve()))
            del second.inpaint_receipt
            restore_receipts(board, records, [Path(temp)/'unrelated'])
            self.assertFalse(hasattr(second, 'inpaint_receipt'))
            # Optional malformed metadata must not break project loading.
            restore_receipts(board, {second.id:{'source_id':[]}}, [Path(temp)])
            restore_receipts(board, records, [Path(temp)])
            board.history.undo()
            self.assertIs(board.selected, first)
            board.history.redo()
            self.assertIs(board.doc.papers[-1], second)
        board.deleteLater()


if __name__ == '__main__':
    unittest.main()

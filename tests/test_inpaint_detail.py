import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication
from sketchpad.board import Board
from sketchpad.brush import empty_ink, image_array
from sketchpad.inpaint import (detail_crop, prepare_inpaint_inputs, merge_inpaint,
                               frame_prompt, validate_crop, selection_bounds, api_mask)
from sketchpad.assistant_panel import Source, place_result, saved_receipts, restore_receipts
from sketchpad.model import Paper, png_bytes

APP = QApplication.instance() or QApplication([])


def rect_mask(width, height, *rects):
    mask = empty_ink(width, height)
    p = QPainter(mask)
    for rect in rects:
        p.fillRect(*rect, QColor('red'))
    p.end()
    return mask


class DetailTests(unittest.TestCase):
    def test_crop_contains_islands_edges_and_context(self):
        for rects in (((990, 710, 10, 90),), ((0, 0, 30, 20),),
                      ((220, 200, 30, 45), (450, 350, 20, 20)), ((0, 0, 1000, 800),)):
            mask = rect_mask(1000, 800, *rects)
            crop = detail_crop(mask)
            self.assertEqual(validate_crop(crop, mask.size()), crop)
            x, y, w, h = crop
            left, top, right, bottom = selection_bounds(mask)
            self.assertLessEqual(x, left)
            self.assertLessEqual(y, top)
            self.assertGreaterEqual(x+w, right)
            self.assertGreaterEqual(y+h, bottom)
            self.assertGreaterEqual(w, min(1000, right-left+128))
            self.assertGreaterEqual(h, min(800, bottom-top+128))

    def test_preparation_boosts_small_region_without_mutating_originals(self):
        source = empty_ink(1499, 1049)
        source.fill(QColor('#b8aa90'))
        mask = rect_mask(1499, 1049, (420, 160, 366, 301))
        before = (png_bytes(source), png_bytes(mask))
        normal, normal_mask, full = prepare_inpaint_inputs(source, mask)
        image, selected, crop = prepare_inpaint_inputs(source, mask, True)
        self.assertIsNone(full)
        self.assertEqual(normal.size(), source.size())
        self.assertEqual(normal_mask.size(), source.size())
        self.assertEqual(image.size(), selected.size())
        self.assertLess(crop[2], source.width())
        self.assertEqual(image.width(), 2048)
        self.assertLessEqual(image.width()/crop[2], 4)
        x, y, w, h = crop
        mask_api = api_mask(selected)
        sx = round((600-x)*image.width()/w)
        sy = round((300-y)*image.height()/h)
        self.assertEqual(mask_api.pixelColor(sx, sy).alpha(), 0)
        self.assertEqual(mask_api.pixelColor(0, 0).alpha(), 255)
        self.assertIn('enlarged detail crop', frame_prompt(mask, crop))
        self.assertEqual(before, (png_bytes(source), png_bytes(mask)))

    def test_large_sheet_tiny_region_and_empty_mask(self):
        source = empty_ink(3000, 2200)
        mask = rect_mask(3000, 2200, (1800, 1300, 20, 15))
        normal, selected, crop = prepare_inpaint_inputs(source, mask)
        self.assertEqual(normal.width(), 2048)
        self.assertEqual(normal.size(), selected.size())
        detail, selected, crop = prepare_inpaint_inputs(source, mask, True)
        self.assertEqual(detail.width(), crop[2]*4)
        self.assertEqual(detail.size(), selected.size())
        with self.assertRaises(ValueError):
            prepare_inpaint_inputs(source, empty_ink(3000, 2200), True)
        with self.assertRaises(ValueError):
            prepare_inpaint_inputs(source, empty_ink(10, 10), True)

    def test_enlarged_response_maps_back_exactly_and_preserves_outside(self):
        old = empty_ink(310, 230)
        pixels = image_array(old)
        yy, xx = np.indices((230, 310))
        pixels[..., 0] = xx % 256
        pixels[..., 1] = yy
        pixels[..., 2] = (xx+yy) % 256
        pixels[..., 3] = 255
        mask = rect_mask(310, 230, (110, 80, 31, 27), (150, 110, 14, 11))
        original = pixels.copy()
        crop = detail_crop(mask, 0)
        raw = old.copy(*crop).scaled(crop[2]*3, crop[3]*3, Qt.AspectRatioMode.IgnoreAspectRatio,
                                     Qt.TransformationMode.FastTransformation)
        # Exact integer up/down transform: no translation even for odd crop sizes.
        restored = merge_inpaint(old, raw, mask, 0, False, crop)
        np.testing.assert_array_equal(image_array(restored), original)
        raw.fill(QColor('blue'))
        for feather in (0, 40, 200):
            merged = merge_inpaint(old, raw, mask, feather, True, crop)
            protected = image_array(mask)[..., 3] == 0
            np.testing.assert_array_equal(image_array(merged)[protected], original[protected])
            self.assertTrue(np.any(image_array(merged)[~protected] != original[~protected]))
        np.testing.assert_array_equal(image_array(old), original)

    def test_invalid_crop_or_wrong_aspect_cannot_change_document(self):
        original = empty_ink(300, 240)
        mask = rect_mask(300, 240, (120, 100, 50, 40))
        for crop in ((-1, 0, 20, 20), (0, 0, 400, 30), (0, 0, 0, 3), [True, 0, 3, 3], 'x'):
            with self.assertRaises(ValueError):
                merge_inpaint(original, original, mask, crop=crop)
        with self.assertRaises(ValueError):
            merge_inpaint(original, original, mask, crop=(1, 1, 30, 30))
        with self.assertRaises(ValueError):
            merge_inpaint(original, empty_ink(200, 40), mask, crop=detail_crop(mask))

    def test_crop_recipe_roundtrip_and_local_reblend(self):
        board = Board()
        original = Paper(width=700, height=500, angle=27, scale=.75, x=81, y=-13)
        board.doc.papers = [original]
        board.selected = original
        mask = rect_mask(700, 500, (400, 300, 80, 50))
        crop = detail_crop(mask)
        source = Source(board, board.doc, original.id, 'source', 'detail', mask, 40, False, crop)
        with tempfile.TemporaryDirectory() as temp:
            raw = empty_ink(crop[2]*2, crop[3]*2)
            raw.fill(QColor('blue'))
            path = Path(temp)/'result.png'
            raw.save(str(path))
            first = place_result(source, path, 'detail')
            self.assertEqual((first.x, first.y, first.scale, first.angle), (81, -13, .75, 27))
            self.assertEqual(first.image.size(), original.image.size())
            records = json.loads(json.dumps(saved_receipts(board)))
            del first.inpaint_receipt
            restore_receipts(board, records, [Path(temp)])
            self.assertEqual(first.inpaint_receipt['crop'], crop)
            record = first.inpaint_receipt
            replay = Source(board, board.doc, record['source_id'], '', record['title'], mask, 80, True, record['crop'])
            second = place_result(replay, path, 'variant', after=first, output_name='variant.png')
            self.assertEqual(second.inpaint_receipt['crop'], crop)
            board.history.undo()
            self.assertIs(board.selected, first)
            board.history.redo()
            self.assertIs(board.selected, second)
            del first.inpaint_receipt
            records[first.id]['crop'] = [0, 0, 10000, 40]
            restore_receipts(board, records, [Path(temp)])
            self.assertFalse(hasattr(first, 'inpaint_receipt'))
        board.deleteLater()

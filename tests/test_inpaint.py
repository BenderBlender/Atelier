import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import Qt, QPointF, QEvent
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication
from sketchpad.board import Board
from sketchpad.brush import empty_ink, image_array
from sketchpad.model import Paper, composite_paper, png_bytes
from sketchpad.inpaint import api_mask, feather_alpha, merge_inpaint, region_guide
from sketchpad.assistant_panel import Source, place_result
from sketchpad import assistant_backend as backend

APP = QApplication.instance() or QApplication([])


class InpaintTests(unittest.TestCase):
    def setUp(self):
        self.board = Board()
        self.paper = Paper(width=240, height=180)
        self.board.doc.papers = [self.paper]
        self.board.selected = self.paper
        self.board.zoom = 1
        self.board.offset = QPointF()

    def tearDown(self):
        self.board.deleteLater()

    def rectangle(self, start=(40, 40), end=(160, 120)):
        b = self.board
        b.set_tool('mask_rect')
        b.pointer_down(b.to_screen(self.paper.transform().map(QPointF(*start))), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pen=True)
        b.pointer_move(b.to_screen(self.paper.transform().map(QPointF(*end))), pen=True)
        b.pointer_up(pen=True)

    def test_pen_selects_rotated_sheet_without_drawing_or_moving_pin(self):
        b = self.board
        self.paper.x, self.paper.y, self.paper.angle, self.paper.scale = 71, 29, 37, .7
        b.set_view_rotation(24)
        pin = b.doc.pins[0]
        pin.x, pin.y = self.paper.transform().map(QPointF(40, 40)).toTuple()
        before, before_pin = png_bytes(self.paper.image), (pin.x, pin.y)
        self.rectangle()
        self.assertEqual(png_bytes(self.paper.image), before)
        self.assertEqual((pin.x, pin.y), before_pin)
        self.assertEqual(b.history.count(), 0)
        self.assertEqual(b.inpaint.history.count(), 1)
        alpha = b.inpaint.alpha()
        self.assertEqual(alpha[60, 60], 255)
        self.assertEqual(alpha[130, 170], 0)

    def test_brush_subtraction_eraser_and_selection_undo(self):
        self.rectangle()
        b = self.board
        b.set_tool('mask_remove')
        b.inpaint.diameter = 24
        b.pointer_down(QPointF(80, 80), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        b.pointer_move(QPointF(120, 80))
        b.pointer_up(pen=False)
        self.assertEqual(b.inpaint.alpha()[80, 100], 0)
        b.inpaint.history.undo()
        self.assertEqual(b.inpaint.alpha()[80, 100], 255)
        b.inpaint.history.redo()
        self.assertEqual(b.inpaint.alpha()[80, 100], 0)
        b.inpaint.clear()
        self.assertFalse(b.inpaint.has_selection(self.paper))
        b.inpaint.history.undo()
        self.assertTrue(b.inpaint.has_selection(self.paper))

    def test_mouse_cannot_move_or_finish_pen_selection(self):
        b = self.board
        b.set_tool('mask_add')
        b.inpaint.diameter = 20
        b.pen_sample(QEvent.Type.TabletPress, QPointF(60, 60), .8, 25, 0)
        b.pointer_move(QPointF(200, 160), pen=False)
        b.pointer_up(pen=False)
        self.assertEqual(b.inpaint.owner, 'pen')
        b.pen_sample(QEvent.Type.TabletMove, QPointF(100, 60), .8, 25, 0)
        b.pen_sample(QEvent.Type.TabletRelease, QPointF(100, 60), 0, 25, 0)
        self.assertEqual(b.inpaint.alpha()[60, 90], 255)
        self.assertEqual(b.inpaint.alpha()[160, 200], 0)
        self.assertFalse(np.any(image_array(self.paper.image)))

    def test_mask_cannot_be_reused_on_different_paper_or_document(self):
        self.rectangle()
        self.assertFalse(self.board.inpaint.has_selection(Paper(width=240, height=180)))
        from sketchpad.model import Document
        self.board.doc = Document(False)
        self.board.doc.papers = [self.paper]
        self.assertFalse(self.board.inpaint.has_selection(self.paper))

    def test_api_mask_polarity_and_guidance_never_mutate_original(self):
        self.rectangle()
        selection = self.board.inpaint.image
        mask_image = api_mask(selection)
        mask = image_array(mask_image).copy()
        self.assertEqual(mask[60, 60, 3], 0)
        self.assertEqual(mask[10, 10, 3], 255)
        original = composite_paper(self.board.doc, self.paper)
        before = png_bytes(original)
        marked = region_guide(original, selection)
        self.assertNotEqual(png_bytes(marked), before)
        self.assertEqual(png_bytes(original), before)

    def test_exact_protection_and_inward_feather_even_if_model_changes_everything(self):
        self.rectangle()
        selection = self.board.inpaint.image
        original = empty_ink(240, 180)
        rng = np.random.default_rng(12)
        image_array(original)[...] = rng.integers(0, 256, (180, 240, 4), dtype=np.uint8)
        image_array(original)[..., 3] = 255
        before = image_array(original).copy()
        generated = empty_ink(480, 360)
        generated.fill(QColor('blue'))
        for feather in (0, 4, 40):
            result_image = merge_inpaint(original, generated, selection, feather)
            result = image_array(result_image).copy()
            protected = image_array(selection)[..., 3] == 0
            np.testing.assert_array_equal(result[protected], before[protected])
            self.assertTrue(np.any(result[~protected] != before[~protected]))
            np.testing.assert_array_equal(image_array(original), before)
        alpha = feather_alpha(selection, 12)
        self.assertEqual(alpha[10, 10], 0)
        self.assertGreater(alpha[70, 100], .99)
        self.assertLess(alpha[40, 40], .8)
        with self.assertRaises(ValueError):
            merge_inpaint(original, empty_ink(200, 200), selection)

    def test_local_result_preserves_new_outside_strokes_and_undo(self):
        self.rectangle()
        source = Source(self.board, self.board.doc, self.paper.id, 'source', 'local', self.board.inpaint.image.copy(), 4)
        # A stroke made after the job snapshot must survive outside the mask.
        painter = QPainter(self.paper.image)
        painter.fillRect(5, 5, 15, 15, QColor('red'))
        painter.end()
        baseline_image = composite_paper(self.board.doc, self.paper).convertToFormat(self.paper.image.format())
        baseline = image_array(baseline_image).copy()
        original = png_bytes(self.paper.image)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'result.png'
            image = empty_ink(240, 180)
            image.fill(QColor('blue'))
            image.save(str(path))
            result = place_result(source, path, 'local')
            protected = image_array(source.mask)[..., 3] == 0
            np.testing.assert_array_equal(image_array(result.image)[protected], baseline[protected])
            self.assertEqual(png_bytes(self.paper.image), original)
            self.assertTrue((path.parent/'inpaint.png').is_file())
            self.board.history.undo()
            self.assertEqual(len(self.board.doc.papers), 1)
            self.board.history.redo()
            self.assertIs(self.board.doc.papers[1], result)

    def test_local_prompt_allows_replacement_and_requires_instructions(self):
        prompt = backend.build_prompt(None, 'Replace the pot with a vase', inpaint=True)
        self.assertEqual('Replace the pot with a vase', prompt)
        self.assertNotIn('Preserve its recognizable subject, subject count', prompt)
        with self.assertRaises(backend.AssistantError):
            backend.build_prompt(None, '', inpaint=True)
        self.assertIn('ONLY inside the selected region', backend.build_prompt(backend.load_presets()[0], '', inpaint=True))

    def test_prompt_enhancement_keeps_local_edit_constraints(self):
        request = backend.Request(Path('source.png'), Path('job'), 'Replace the pot', provider='api', mask=Path('mask.png'))
        response = {'output':[{'type':'message', 'content':[{'type':'output_text', 'text':'Replace with a round pot.'}]}]}
        with patch.object(backend, 'api_post', return_value=response):
            prompt = backend.improve_prompt(request, backend.Cancellation())
        # Region constraints are added separately at transport time so the
        # text enhancer cannot rewrite them or override the requested edit.
        self.assertEqual(prompt, 'Replace with a round pot.')
        self.assertIn('round pot', prompt)

    def test_api_sends_native_mask_and_subscription_attaches_region_guide(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, mask, guide = [root/name for name in ('source.png', 'mask.png', 'region.png')]
            for path in (source, mask, guide):
                path.write_bytes(path.name.encode())
            request = backend.Request(source, root, 'replace locally', provider='api', key='secret', mask=mask, guide=guide)
            with patch.object(backend, 'api_post', return_value={'data':[{'b64_json':base64.b64encode(b'fixture').decode()}]}) as post:
                backend.generate(request, backend.Cancellation())
            payload = post.call_args.args[1]
            self.assertEqual(base64.b64decode(payload['mask']['image_url'].split(',')[1]), b'mask.png')
            self.assertNotIn('markup', payload)
            args = backend.generation_args(root, source, guide)
            self.assertEqual(args.count('--image'), 2)
            self.assertLess(args.index(str(source)), args.index(str(guide)))
            self.assertEqual(json.loads((root/'result.json').read_text())['mode'], 'inpaint')


if __name__ == '__main__':
    unittest.main()

"""Toned backgrounds remain independent of ink, including white highlights."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import numpy as np
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication
from sketchpad.board import Board
from sketchpad.brush import BrushSettings, GraphiteStroke, image_array
from sketchpad.model import (PAPER_STYLES, Document, Paper, composite_paper,
                             save_document, load_document, export_paper)

APP = QApplication.instance() or QApplication([])


class DarkPaperTests(unittest.TestCase):
    def test_all_papers_have_distinct_backgrounds_without_ink(self):
        doc = Document(False)
        paper = Paper(width=100, height=80)
        doc.papers = [paper]
        colors = set()
        for style in PAPER_STYLES:
            paper.paper_style = style
            out = composite_paper(doc, paper).convertToFormat(paper.image.format())
            values = image_array(out)
            colors.add(tuple(values[30, 30, :3]))
            self.assertTrue((values[..., 3] == 255).all())
            if style != 'ivory':
                self.assertGreater(np.std(values[..., 0]), .5)
                self.assertLess(values[..., :3].mean(), 150)
            self.assertEqual(image_array(paper.image)[..., 3].sum(), 0)
        self.assertEqual(len(colors), len(PAPER_STYLES))

    def test_white_highlights_brighten_with_pressure_and_repeated_passes(self):
        doc = Document(False)
        paper = Paper(width=180, height=120, paper_style='anthracite')
        doc.papers = [paper]
        baseline = composite_paper(doc, paper).pixelColor(85, 55).red()
        strengths = []
        for pressure in (.2, .9, .9):
            stroke = GraphiteStroke(paper.image, BrushSettings(size=8, color=(255,255,255), grain=0, smoothing=0))
            for x in range(25, 150):
                stroke.add(x, 55, pressure, 0, 0)
            strengths.append(composite_paper(doc, paper).pixelColor(85, 55).red())
        self.assertGreater(strengths[0], baseline)
        self.assertGreater(strengths[1], strengths[0])
        self.assertGreater(strengths[2], strengths[1])
        transparent = composite_paper(doc, paper, transparent=True)
        self.assertEqual(transparent.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(transparent.pixelColor(85, 55).red(), 255)

    def test_paper_change_undo_and_clear_do_not_recolor_ink(self):
        b = Board()
        paper = b.doc.papers[0]
        b.brush.color = (255,255,255)
        b.start_stroke(QPointF(100, 100), .8, 0, 50)
        b.sample_stroke(QPointF(200, 110), .8, 0, 50)
        b.finish_stroke()
        ink = bytes(paper.image.constBits())
        b.set_paper_style('slate')
        self.assertEqual(bytes(paper.image.constBits()), ink)
        b.history.undo()
        self.assertEqual(paper.paper_style, 'ivory')
        b.history.redo()
        self.assertEqual(paper.paper_style, 'slate')
        b.clear_paper()
        self.assertEqual(paper.paper_style, 'slate')
        self.assertEqual(image_array(paper.image)[..., 3].sum(), 0)
        b.history.undo()
        self.assertEqual(bytes(paper.image.constBits()), ink)
        b.deleteLater()

    def test_toned_papers_white_and_export_survive_project_roundtrip(self):
        doc = Document(False)
        doc.papers = [Paper(width=100, height=80, paper_style=style) for style in PAPER_STYLES]
        doc.papers[-1].image.setPixelColor(45, 40, QColor('white'))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'dark.atelier'
            save_document(doc, path, {'brush': {'color': [255,255,255]}})
            loaded, settings = load_document(path)
            self.assertEqual(loaded.metadata(), doc.metadata())
            self.assertEqual(settings['brush']['color'], [255,255,255])
            for old, new in zip(doc.papers, loaded.papers):
                old_image = composite_paper(doc, old)
                new_image = composite_paper(loaded, new)
                self.assertEqual(bytes(old_image.constBits()), bytes(new_image.constBits()))
            export = Path(temp)/'dark.png'
            export_paper(loaded.papers[-1], export, loaded)
            from PySide6.QtGui import QImage
            self.assertEqual(QImage(str(export)).pixelColor(45, 40), QColor('white'))

    def test_old_projects_without_paper_style_stay_ivory(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'old.atelier'
            doc = Document(False)
            doc.papers = [Paper(width=50, height=40)]
            save_document(doc, path)
            with zipfile.ZipFile(path) as z:
                files = {name: z.read(name) for name in z.namelist()}
            meta = json.loads(files['board.json'])
            del meta['papers'][0]['paper_style']
            for version in (1, 2):
                meta['version'] = version
                files['board.json'] = json.dumps(meta).encode()
                with zipfile.ZipFile(path, 'w') as z:
                    for name, data in files.items():
                        z.writestr(name, data)
                loaded, _ = load_document(path)
                self.assertEqual(loaded.papers[0].paper_style, 'ivory')
                self.assertEqual(composite_paper(loaded, loaded.papers[0]).pixelColor(10, 10), QColor('#f5f1e8'))

    def test_tracing_keeps_white_ink_full_strength_over_dark_paper(self):
        b = Board()
        b.set_paper_style('umber')
        base = b.doc.papers[0]
        dark = composite_paper(b.doc, base).pixelColor(50, 50).red()
        b.add_tracing()
        tracing = b.selected
        self.assertEqual(tracing.paper_style, 'ivory')
        tracing.image.setPixelColor(50, 50, QColor('white'))
        out = composite_paper(b.doc, tracing)
        self.assertEqual(out.pixelColor(50, 50), QColor('white'))
        self.assertGreater(out.pixelColor(51, 50).red(), dark)
        self.assertEqual(base.paper_style, 'umber')
        b.deleteLater()


if __name__ == '__main__':
    unittest.main()

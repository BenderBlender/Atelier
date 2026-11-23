import ctypes
from pathlib import Path
import tempfile
import unittest
import numpy as np
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QImage, QTabletEvent, QPointingDevice, QInputDevice
from PySide6.QtWidgets import QApplication
from sketchpad.brush import BrushSettings, GraphiteStroke, empty_ink, footprint, image_array
from sketchpad.model import Document, Paper, Pin, Band, save_document, load_document, export_paper, project_line
from sketchpad.board import Board
from sketchpad.raw_input import Header, Mouse, Raw

APP = QApplication.instance() or QApplication([])


def line(pressure=.7, ty=0, settings=None):
    image = empty_ink(400, 200)
    stroke = GraphiteStroke(image, settings or BrushSettings())
    for x in range(50, 350, 3):
        stroke.add(x, 100, pressure, 0, ty)
    return image


class BrushTests(unittest.TestCase):
    def test_pressure_deposits_more_graphite(self):
        a, b = image_array(line(.15)).copy(), image_array(line(.9)).copy()
        self.assertGreater(b[..., 3].sum(), a[..., 3].sum()*2)

    def test_tilt_widens_contact(self):
        upright, tilted = image_array(line(ty=0)).copy(), image_array(line(ty=65)).copy()
        a = np.count_nonzero(upright[:, 200, 3])
        b = np.count_nonzero(tilted[:, 200, 3])
        self.assertGreater(b, a*2)

    def test_grain_is_fixed_and_deterministic(self):
        a, b = line(), line()
        self.assertTrue(np.array_equal(image_array(a), image_array(b)))
        vals = image_array(a)[99:102, 80:320, 3]
        self.assertGreater(np.std(vals), 2)

    def test_repeated_strokes_darkening_and_eraser(self):
        image = line(ty=50)
        initial = int(image_array(image)[..., 3].sum())
        stroke = GraphiteStroke(image, BrushSettings())
        for x in range(50, 350, 3):
            stroke.add(x, 100, .7, 0, 50)
        dark = int(image_array(image)[..., 3].sum())
        self.assertGreater(dark, initial)
        eraser = GraphiteStroke(image, BrushSettings(size=25, eraser=True))
        for x in range(40, 360):
            eraser.add(x, 100, 1.)
        self.assertLess(image_array(image)[..., 3].sum(), dark*.3)

    def test_edges_clip_safely_and_zero_pressure_does_not_draw(self):
        image = empty_ink(20, 20)
        s = GraphiteStroke(image, BrushSettings())
        s.dab(10, 10, 0, 0, 0)
        self.assertEqual(image_array(image).sum(), 0)
        s.dab(-100, -100, .5, 0, 0)
        s.dab(0, 0, .7, 60, 60)
        self.assertGreater(image_array(image)[..., 3].sum(), 0)

    def test_rotation_of_footprint(self):
        a = footprint(BrushSettings(), .5, 60, 0)
        b = footprint(BrushSettings(), .5, 0, 60)
        self.assertAlmostEqual(a[0], b[0])
        self.assertAlmostEqual(b[2]-a[2], np.pi/2)


class DocumentTests(unittest.TestCase):
    def test_roundtrip_includes_references_pins_and_pixels(self):
        doc = Document()
        doc.papers[0].image = line()
        doc.papers[0].width, doc.papers[0].height = 400, 200
        doc.papers.append(Paper(name='Референс', width=400, height=200, kind='reference', image=line(.2)))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'test.atelier'
            save_document(doc, path, {'snap': True})
            loaded, settings = load_document(path)
            self.assertEqual(loaded.metadata(), doc.metadata())
            self.assertTrue(settings['snap'])
            self.assertEqual(bytes(loaded.papers[0].image.constBits()), bytes(doc.papers[0].image.constBits()))
            export_paper(loaded.papers[0], Path(temp)/'out.png')
            self.assertFalse(QImage(str(Path(temp)/'out.png')).isNull())

    def test_rotated_sheet_coordinates_roundtrip(self):
        paper = Paper(x=120, y=-75, scale=.75, angle=37)
        local = QPointF(380, 455)
        restored = paper.local(paper.transform().map(local))
        self.assertLess((restored-local).manhattanLength(), 1e-8)

    def test_projection_and_degenerate_pin(self):
        p, distance = project_line(QPointF(10, 5), QPointF(), QPointF(20, 0))
        self.assertEqual(p, QPointF(10, 0))
        self.assertEqual(distance, 5)
        _, distance = project_line(QPointF(10, 5), QPointF(), QPointF())
        self.assertEqual(distance, float('inf'))


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.board = Board()
        self.board.resize(1000, 700)
        self.board.zoom = 1
        self.board.offset = QPointF()

    def tearDown(self):
        self.board.deleteLater()

    def test_tile_undo_redo_restores_exact_pixels(self):
        board = self.board
        board.start_stroke(QPointF(100, 100), .8, 0, 50)
        for x in range(110, 410, 10):
            board.sample_stroke(QPointF(x, 120), .8, 0, 50)
        board.finish_stroke()
        paper = board.doc.papers[0]
        drawn = bytes(paper.image.constBits())
        self.assertGreater(image_array(paper.image)[..., 3].sum(), 0)
        board.history.undo()
        self.assertEqual(image_array(paper.image)[..., 3].sum(), 0)
        board.history.redo()
        self.assertEqual(bytes(paper.image.constBits()), drawn)

    def test_guide_locks_branch_and_projects_stroke(self):
        board = self.board
        a, b = board.doc.pins[:2]
        a.x, a.y = 20, 120
        b.x, b.y = 600, 120
        board.mouse_pin.x, board.mouse_pin.y = 300, 120
        board.snap = True
        board.start_stroke(QPointF(100, 125), .8, 0, 0)
        self.assertIsNotNone(board.stroke_line)
        board.sample_stroke(QPointF(250, 160), .8, 0, 0)
        board.finish_stroke()
        ys, _ = np.where(image_array(board.doc.papers[0].image)[..., 3] > 0)
        self.assertLess(abs(np.mean(ys)-120), 3)
        self.assertLess(ys.max()-ys.min(), 12)

    def test_standalone_pins_and_multiple_bands_undo(self):
        board = self.board
        pin = board.add_pin(QPointF(15, 30))
        board.add_band([pin.id, board.doc.pins[0].id])
        self.assertEqual(len(board.doc.bands), 3)
        self.assertEqual(len(board.doc.lines(board.band)), 1)
        board.set_selection(pin)
        board.delete_selected()
        self.assertNotIn(pin, board.doc.pins)
        self.assertEqual(len(board.doc.bands), 2)
        board.history.undo()
        self.assertIn(pin, board.doc.pins)
        self.assertEqual(len(board.doc.bands), 3)

    def test_tablet_events_draw_and_report_tilt(self):
        board = self.board
        device = QPointingDevice('Synthetic Wacom test', 101, QInputDevice.DeviceType.Stylus, QPointingDevice.PointerType.Pen,
            QInputDevice.Capability.Position | QInputDevice.Capability.Pressure | QInputDevice.Capability.XTilt | QInputDevice.Capability.YTilt, 1, 2)
        samples = []
        board.telemetry.connect(samples.append)
        for kind, x, pressure in [(QEvent.Type.TabletPress, 100, .5), (QEvent.Type.TabletMove, 150, .8), (QEvent.Type.TabletRelease, 170, 0)]:
            event = QTabletEvent(kind, device, QPointF(x, 100), QPointF(x, 100), pressure, 15, 55, 0, 0, 0, Qt.KeyboardModifier.NoModifier, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton if pressure else Qt.MouseButton.NoButton)
            QApplication.sendEvent(board, event)
        self.assertEqual(board.history.count(), 1)
        self.assertIsNone(board.stroke)
        self.assertTrue(samples)
        self.assertEqual(samples[0]['ty'], 55)
        self.assertGreater(image_array(board.doc.papers[0].image)[..., 3].sum(), 0)

    def test_win64_raw_input_layout(self):
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(Header), 24)
            self.assertEqual(Mouse.dx.offset, 12)
            self.assertEqual(ctypes.sizeof(Raw), 48)

    def test_dual_mouse_does_not_move_pen_or_end_its_stroke(self):
        class ActiveBoard(Board):
            def isActiveWindow(self):
                return True
        board = ActiveBoard()
        board.resize(1000, 700)
        board.zoom = 1
        board.offset = QPointF()
        board.set_dual(True)
        board.start_stroke(QPointF(200, 100), .8, 0, 20)
        previous = board.stroke.previous.copy()
        origin = (board.mouse_pin.x, board.mouse_pin.y)
        board.raw_move(25, -30)
        self.assertEqual((board.mouse_pin.x, board.mouse_pin.y), (origin[0]+25, origin[1]-30))
        self.assertTrue(np.array_equal(board.stroke.previous, previous))
        board.sample_stroke(QPointF(300, 100), .8, 0, 20)
        board.finish_stroke()
        self.assertGreater(image_array(board.doc.papers[0].image)[..., 3].sum(), 0)
        board.dual_paused = True
        origin = (board.mouse_pin.x, board.mouse_pin.y)
        board.raw_move(80, 80)
        self.assertEqual(origin, (board.mouse_pin.x, board.mouse_pin.y))
        board.flush_dual_move()
        board.history.undo()
        self.assertEqual((board.mouse_pin.x, board.mouse_pin.y), (700, 670))
        board.deleteLater()

    def test_locked_paper_cannot_be_painted(self):
        self.board.doc.papers[0].locked = True
        self.board.start_stroke(QPointF(100, 100), 1., 0, 0)
        self.assertIsNone(self.board.stroke)


if __name__ == '__main__':
    unittest.main()

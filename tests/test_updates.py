import ctypes
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
import zipfile
import numpy as np
from PySide6.QtCore import QEvent, QPointF
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import QApplication
from sketchpad.board import Board
from sketchpad.brush import image_array, footprint, BrushSettings
from sketchpad.model import Document, save_document, load_document, composite_paper
from sketchpad.tablet import orientation_to_tilt, Context, Packet, NativeTablet

APP = QApplication.instance() or QApplication([])


class Updates(unittest.TestCase):
    def test_clear_sheet_undo_redo_leaves_other_sheets_and_pins(self):
        b = Board()
        source = b.doc.papers[0]
        source.image.fill(QColor('#34404a'))
        expected = bytes(source.image.constBits())
        b.add_tracing()
        tracing = b.selected
        tracing.image.fill(QColor('#ba9988'))
        b.set_selection(source)
        b.clear_paper()
        self.assertEqual(image_array(source.image)[..., 3].sum(), 0)
        self.assertEqual(len(b.doc.pins), 3)
        self.assertEqual(tracing.image.pixelColor(0, 0), QColor('#ba9988'))
        b.history.undo()
        self.assertEqual(bytes(source.image.constBits()), expected)
        b.history.redo()
        self.assertEqual(image_array(source.image)[..., 3].sum(), 0)

    def test_tracing_keeps_geometry_and_does_not_modify_source(self):
        b = Board()
        p = b.doc.papers[0]
        p.angle, p.scale, p.x, p.y = 27, .7, 30, -20
        b.add_tracing()
        t = b.selected
        self.assertEqual(t.transform(), p.transform())
        self.assertTrue(t.tracing)
        self.assertAlmostEqual(t.paper_opacity, .25)
        center = t.transform().map(QPointF(100, 100))
        b.start_stroke(center, .8, 0, 60)
        self.assertIs(b.stroke_paper, t)
        b.finish_stroke()
        self.assertGreater(image_array(t.image)[..., 3].sum(), 0)
        self.assertEqual(image_array(p.image)[..., 3].sum(), 0)

    def test_tracing_composite_keeps_top_ink_full_strength(self):
        b = Board()
        base = b.doc.papers[0]
        base.image.fill(QColor('#000000'))
        b.add_tracing()
        t = b.selected
        p = QPainter(t.image)
        p.fillRect(10, 10, 20, 20, QColor('#bf3030'))
        p.end()
        out = composite_paper(b.doc, t)
        lower = out.pixelColor(0, 0)
        self.assertTrue(55 <= lower.red() <= 65)
        self.assertEqual(out.pixelColor(15, 15), QColor('#bf3030'))
        transparent = composite_paper(b.doc, t, transparent=True)
        self.assertEqual(transparent.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(transparent.pixelColor(15, 15).alpha(), 255)

    def test_transformed_tracing_export_alignment(self):
        b = Board()
        base = b.doc.papers[0]
        base.x, base.y, base.angle, base.scale = 70, 48, 35, .65
        p = QPainter(base.image)
        p.fillRect(120, 130, 30, 30, QColor('#ff0000'))
        p.end()
        b.add_tracing()
        t = b.selected
        t.paper_opacity = 0
        out = composite_paper(b.doc, t)
        self.assertEqual(out.pixelColor(130, 140), QColor('#ff0000'))

    def test_tracing_save_load_and_v1_compatibility(self):
        b = Board()
        b.add_tracing()
        b.selected.ink_opacity = .8
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'trace.atelier'
            save_document(b.doc, path)
            doc, _ = load_document(path)
            self.assertTrue(doc.papers[-1].tracing)
            self.assertAlmostEqual(doc.papers[-1].paper_opacity, .25)
            self.assertAlmostEqual(doc.papers[-1].ink_opacity, .8)
            with zipfile.ZipFile(path) as z:
                files = {name:z.read(name) for name in z.namelist()}
            meta = json.loads(files['board.json'])
            meta['version'] = 1
            for paper in meta['papers']:
                for key in ('paper_opacity', 'ink_opacity', 'tracing'):
                    paper.pop(key)
            files['board.json'] = json.dumps(meta).encode()
            with zipfile.ZipFile(path, 'w') as z:
                for name, data in files.items():
                    z.writestr(name, data)
            doc, _ = load_document(path)
            self.assertEqual(doc.papers[-1].paper_opacity, 1)
            self.assertFalse(doc.papers[-1].tracing)

    def test_mouse_pin_stays_selected_as_target_while_pen_draws(self):
        class Active(Board):
            def isActiveWindow(self):
                return True
        b = Active()
        b.zoom = 1
        pin = b.doc.pins[1]
        b.set_selection(pin)
        b.set_mouse_target_mode('pin')
        b.set_dual(True)
        b.start_stroke(QPointF(100, 100), .5, 0, 30)
        self.assertIs(b.mouse_target(), pin)
        self.assertIs(b.selected, b.doc.papers[0])
        old = (pin.x, pin.y)
        band_old = (b.doc.pins[0].x, b.doc.pins[0].y)
        b.raw_move(70, -30)
        self.assertEqual((pin.x, pin.y), (old[0]+70, old[1]-30))
        self.assertEqual((b.doc.pins[0].x, b.doc.pins[0].y), band_old)
        self.assertIsNotNone(b.stroke)
        b.finish_stroke()
        b.set_dual(False)
        b.raw_move(50, 50)
        self.assertEqual((pin.x, pin.y), (old[0]+70, old[1]-30))
        b.history.undo()
        self.assertEqual((pin.x, pin.y), old)

    def test_opacity_slider_coalesces_into_one_undo(self):
        b = Board()
        p = b.selected
        for value in (.9, .8, .7, .6):
            b.push_move(p, {'paper_opacity': p.paper_opacity}, {'paper_opacity':value}, 'Свойства листа')
        self.assertEqual(b.history.count(), 1)
        b.history.undo()
        self.assertEqual(p.paper_opacity, 1)
        b.history.redo()
        self.assertEqual(p.paper_opacity, .6)

    def test_native_orientation_units_and_inversion(self):
        resolution = 3600*65536
        tx, ty = orientation_to_tilt(0, 900, resolution, resolution)
        self.assertAlmostEqual(tx, 0)
        self.assertAlmostEqual(ty, 0)
        tx, ty = orientation_to_tilt(900, 300, resolution, resolution)
        self.assertAlmostEqual(tx, 60)
        self.assertAlmostEqual(ty, 0)
        self.assertEqual((tx, ty), orientation_to_tilt(900, -300, resolution, resolution))
        self.assertEqual(orientation_to_tilt(0, 0, 0, 0), (0., 0.))
        self.assertEqual(ctypes.sizeof(Context), 212)
        self.assertEqual(ctypes.sizeof(Packet), 40)

    def test_native_packet_pressure_tilt_and_stroke_lifecycle(self):
        b = Board()
        calls = []
        b.pen_sample = lambda *args, **kwargs: calls.append(args)
        fake = SimpleNamespace(packets=0, pressure_axis=SimpleNamespace(minimum=0, maximum=32767),
            must_lift=False, down=False, captured=False, board=b,
            orientation_axes=[SimpleNamespace(resolution=3600*65536)]*3,
            local_position=lambda packet:QPointF(100, 100))
        p = Packet()
        p.orientation.azimuth, p.orientation.altitude = 900, 300
        for pressure in (16384, 20000, 0):
            p.pressure = pressure
            p.buttons = 1 if pressure else 0
            NativeTablet.process_packet(fake, p)
        self.assertEqual([c[0] for c in calls], [QEvent.Type.TabletPress,QEvent.Type.TabletMove,QEvent.Type.TabletRelease])
        self.assertAlmostEqual(calls[0][2], .5, places=4)
        self.assertAlmostEqual(calls[0][3], 60.)
        self.assertFalse(fake.captured)

    def test_tilt_also_widens_parallel_strokes(self):
        upright = footprint(BrushSettings(), .6, 0, 0)
        tilted = footprint(BrushSettings(), .6, 65, 0)
        self.assertGreater(tilted[1], upright[1]*2)

    def test_floating_helper_blocks_pen_until_lift_and_can_return_focus(self):
        b = Board()
        calls, releases, activated = [], [], []
        b.pen_sample = lambda *args, **kwargs: calls.append(args)
        b.pointer_up = lambda **kwargs: releases.append(kwargs)
        covered = [True]
        window = SimpleNamespace(tablet_point_allowed=lambda pos:not covered[0],
                                 isActiveWindow=lambda:False, activateWindow=lambda:activated.append(True))
        fake = SimpleNamespace(packets=0, pressure_axis=SimpleNamespace(minimum=0, maximum=32767),
            must_lift=False, down=False, captured=False, board=b, window=window,
            orientation_axes=[SimpleNamespace(resolution=3600*65536)]*3,
            local_position=lambda packet:QPointF(100, 100))
        packet = Packet()
        packet.orientation.azimuth, packet.orientation.altitude = 900, 300
        packet.pressure, packet.buttons = 17000, 1
        NativeTablet.process_packet(fake, packet)
        covered[0] = False
        NativeTablet.process_packet(fake, packet)
        self.assertEqual(calls, [])  # Contact begun on the helper never draws.
        packet.pressure, packet.buttons = 0, 0
        NativeTablet.process_packet(fake, packet)
        packet.pressure, packet.buttons = 17000, 1
        NativeTablet.process_packet(fake, packet)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], QEvent.Type.TabletPress)
        self.assertEqual(activated, [True])
        covered[0] = True
        NativeTablet.process_packet(fake, packet)
        self.assertEqual(releases, [{'pen':True}])
        self.assertFalse(fake.captured)


if __name__ == '__main__':
    unittest.main()

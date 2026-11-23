from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPointF, Qt, QEvent
from PySide6.QtGui import QColor, QPainter, QImage, QKeyEvent, QFocusEvent
from sketchpad.perspective import (PerspectiveSettings, PerspectiveRenderer,
    PROJECTIONS, PRESETS, project, projected_segments, camera_points, geometry, preset, segment_domain_safe)
from sketchpad.model import Document, Paper, save_document, load_document, composite_paper
from sketchpad.board import Board
from sketchpad.brush import image_array

APP = QApplication.instance() or QApplication([])


class ProjectionTests(unittest.TestCase):
    def test_linear_shortens_with_depth_parallel_preserves_size(self):
        points = np.array([[1., 0., 4.], [1., 0., 8.]])
        s = PerspectiveSettings()
        uv, valid, _, _ = project(points, s, 1000, 1000)
        self.assertTrue(valid.all())
        self.assertAlmostEqual((uv[0, 0]-500)/(uv[1, 0]-500), 2.)
        uv, _, _, _ = project(points, replace(s, projection='parallel'), 1000, 1000)
        self.assertAlmostEqual(uv[0, 0], uv[1, 0])

    def test_fisheye_radial_laws_are_distinct(self):
        angle = np.radians([0., 30., 60., 90.])
        points = np.column_stack((np.sin(angle), np.zeros(4), np.cos(angle)))
        s = PerspectiveSettings(fov=180., projection='azimuthal')
        uv, _, _, _ = project(points, s, 1000, 1000)
        np.testing.assert_allclose((uv[:, 0]-500)/440, [0, 1/3, 2/3, 1], atol=1e-10)
        uv, _, _, _ = project(points, replace(s, projection='fish'), 1000, 1000)
        np.testing.assert_allclose((uv[:, 0]-500)/440, np.sin(angle), atol=1e-10)

    def test_cylinder_preserves_generators_and_handles_roll(self):
        points = np.array([[1., -1., 2.], [1., 0., 2.], [1., 1., 2.]])
        uv, valid, _, _ = project(points, PerspectiveSettings(projection='cylinder', fov=250), 900, 650)
        self.assertTrue(valid.all())
        np.testing.assert_allclose(uv[:, 0], uv[0, 0])
        self.assertAlmostEqual(uv[1, 1], (uv[0, 1]+uv[2, 1])/2)

    def test_perceptual_zero_strength_is_linear(self):
        points = np.array([[.5, .1, 1.], [1., 0., 8.], [1., .7, 20.]])
        a = project(points, PerspectiveSettings(), 900, 650)[0]
        b = project(points, PerspectiveSettings(projection='perceptual', perceptual_strength=0), 900, 650)[0]
        np.testing.assert_allclose(a, b)

    def test_perceptual_mid_plane_flattens_and_far_plane_returns_to_linear(self):
        points = np.array([[1., 0., 2.], [1., 0., 3.], [1., 0., 30.]])
        s = PerspectiveSettings(projection='perceptual', perceptual_strength=1.)
        uv = project(points, s, 1000, 1000)[0]
        self.assertAlmostEqual(uv[0, 0], uv[1, 0])
        linear = project(points, replace(s, projection='linear'), 1000, 1000)[0]
        self.assertAlmostEqual(uv[2, 0], linear[2, 0])

    def test_camera_and_deformation_are_applied_before_projection(self):
        points = np.array([[1., 0., -1.], [1., 0., 0.], [1., 0., 1.]])
        s = PerspectiveSettings(yaw=0, pitch=0, distance=0)
        base = camera_points(points, s)
        bent = camera_points(points, replace(s, bend=1.))
        self.assertGreater(bent[0, 0], base[0, 0])
        self.assertEqual(bent[1, 0], base[1, 0])
        moved = camera_points(points, replace(s, camera_x=.5))
        np.testing.assert_allclose(moved[:, 0], base[:, 0]-s.extent*.5)

    def test_all_presets_produce_bounded_finite_segments(self):
        for name in PRESETS:
            with self.subTest(name=name):
                s = PerspectiveSettings.read(preset(name))
                if s.structure == 'crystal':
                    from sketchpad.crystal import projected_faces
                    triangles,attrs,_ = projected_faces(s,900,650,True)
                    self.assertGreater(len(triangles),10)
                    self.assertTrue(np.isfinite(triangles).all())
                    self.assertTrue(np.isfinite(attrs).all())
                    continue
                (segments, _, _, depth), _ = projected_segments(s, 900, 650)
                self.assertGreater(len(segments), 10)
                self.assertTrue(np.isfinite(segments).all())
                self.assertTrue(np.isfinite(depth).all())
                self.assertGreaterEqual(segments.min(), -1e-7)
                self.assertLessEqual(segments[..., 0].max(), 900+1e-7)
                self.assertLessEqual(segments[..., 1].max(), 650+1e-7)

    def test_extreme_camera_near_plane_and_seams_remain_safe(self):
        for mode in PROJECTIONS:
            s = PerspectiveSettings(enabled=True, projection=mode, distance=0,
                yaw=175., pitch=88., fov=350., structure='box', bend=1.5,
                twist=120., taper=-.8, camera_x=.3)
            (segments, _, _, _), _ = projected_segments(s, 900, 650)
            self.assertTrue(np.isfinite(segments).all())
            if len(segments):
                self.assertGreaterEqual(segments.min(), -1e-7)
                self.assertLessEqual(segments.max(), 900+1e-7)
        points = np.array([[.001, 0, -1.], [-.001, 0, -1.]])
        _, valid, _, _ = project(points, PerspectiveSettings(projection='cylinder', fov=350), 900, 650)
        self.assertFalse(valid.any())

    def test_wide_fisheye_does_not_connect_across_rear_pole(self):
        pairs = np.array([[[-.1, 0., -1.], [.2, 0., -1.]],
                          [[-.1, 0., 1.], [.2, 0., 1.]]])
        for mode in ('azimuthal', 'stereo'):
            s = PerspectiveSettings(projection=mode, fov=350.)
            self.assertTrue(project(pairs, s, 900, 650)[1].all())
            np.testing.assert_array_equal(segment_domain_safe(pairs, s), [False, True])

    def test_settings_reject_invalid_values_and_bound_work(self):
        s = PerspectiveSettings.read({'nx': 1000000, 'ny': -10, 'fov': float('nan'),
            'projection': [], 'structure': {}, 'enabled': 'yes', 'beam': -1})
        self.assertEqual((s.nx, s.ny, s.fov, s.projection, s.enabled), (12, 1, 50., 'linear', False))
        lines, _, _, _, detail = geometry(replace(s, ny=12, nz=12, structure='beams', divisions=6))
        self.assertLess(len(lines), 30000)
        self.assertLess(detail, 6)


class PerspectiveIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.board = Board()
        self.board.resize(1000, 700)
        self.board.zoom = .6
        self.board.offset = QPointF(50, 40)
        self.paper = self.board.selected
        self.paper.perspective = preset('Решётка Chelsea')

    def tearDown(self):
        self.board.deleteLater()

    def press_r(self, modifiers=Qt.KeyboardModifier.NoModifier):
        self.board.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_R, modifiers))

    def release_r(self):
        self.board.keyReleaseEvent(QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_R, Qt.KeyboardModifier.NoModifier))

    def test_r_rolls_camera_without_changing_canvas_or_ink_and_undoes(self):
        b=self.board
        b.set_tool('perspective')
        b.set_view_rotation(23.)
        self.paper.angle=17.
        before=dict(self.paper.perspective)
        ink=bytes(self.paper.image.constBits())
        offset=QPointF(b.offset)
        self.press_r()
        self.assertEqual(b.tool,'perspective')
        b.pen_sample(QEvent.Type.TabletPress,QPointF(700,350),.7,20,40)
        b.pen_sample(QEvent.Type.TabletMove,QPointF(500,550),.7,20,40)
        b.pen_sample(QEvent.Type.TabletRelease,QPointF(500,550),0.,20,40)
        self.release_r()
        self.assertAlmostEqual(self.paper.perspective['roll'],90.)
        self.assertEqual({**self.paper.perspective,'roll':before['roll']},before)
        self.assertEqual(b.view_rotation,23.)
        self.assertEqual(b.offset,offset)
        self.assertEqual(self.paper.angle,17.)
        self.assertEqual(bytes(self.paper.image.constBits()),ink)
        self.assertFalse(b.perspective_roll_key)
        self.assertEqual(b.history.undoText(),'Крен камеры')
        b.history.undo();self.assertEqual(self.paper.perspective,before)
        b.history.redo();self.assertAlmostEqual(self.paper.perspective['roll'],90.)

    def test_r_shift_snaps_roll_and_keeps_pen_mouse_ownership(self):
        b=self.board;b.set_tool('perspective');self.press_r(Qt.KeyboardModifier.ShiftModifier)
        b.pointer_down(QPointF(700,350),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ShiftModifier)
        before=dict(self.paper.perspective)
        b.pointer_move(QPointF(200,200),pen=True);b.pointer_up(pen=True)
        self.assertEqual(self.paper.perspective,before)
        self.assertIsNotNone(b.drag)
        b.pointer_move(QPointF(470,548),modifiers=Qt.KeyboardModifier.ShiftModifier)
        b.pointer_up(pen=False);self.release_r()
        self.assertAlmostEqual(self.paper.perspective['roll'],105.)
        self.assertEqual(self.paper.perspective['offset_x'],before['offset_x'])
        self.assertEqual(self.paper.perspective['offset_y'],before['offset_y'])

    def test_r_release_during_drag_commits_then_returns_to_orbit(self):
        b=self.board;b.set_tool('perspective');self.press_r()
        b.pointer_down(QPointF(500,350),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,True)
        b.pointer_move(QPointF(600,350),pen=True)
        self.release_r()
        self.assertIsNone(b.drag)
        self.assertEqual(b.tool,'perspective')
        self.assertAlmostEqual(self.paper.perspective['roll'],40.)
        b.pointer_move(QPointF(700,350),pen=True);b.pointer_up(pen=True)
        self.assertAlmostEqual(self.paper.perspective['roll'],40.)
        before=dict(self.paper.perspective)
        b.pointer_down(QPointF(300,300),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,True)
        b.pointer_move(QPointF(350,320),pen=True);b.pointer_up(pen=True)
        self.assertNotEqual(self.paper.perspective['yaw'],before['yaw'])
        self.assertEqual(self.paper.perspective['roll'],before['roll'])

    def test_roll_respects_lock_and_clears_on_focus_or_tool_change(self):
        b=self.board;b.set_tool('perspective')
        self.paper.perspective={**self.paper.perspective,'locked':True}
        before=dict(self.paper.perspective)
        self.press_r()
        b.pointer_down(QPointF(700,350),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,True)
        b.pointer_move(QPointF(500,550),pen=True);b.pointer_up(pen=True)
        self.assertEqual(self.paper.perspective,before)
        b.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
        self.assertFalse(b.perspective_roll_key)
        self.assertEqual(b.tool,'perspective')
        self.press_r();b.set_tool('pencil');self.release_r()
        self.assertEqual(b.tool,'pencil')
        self.assertFalse(b.perspective_roll_key)
        self.assertEqual(b.view_rotation,0.)

    def test_drag_changes_only_guide_and_undo_restores_it(self):
        b = self.board
        b.set_tool('perspective')
        before, pixels = dict(self.paper.perspective), bytes(self.paper.image.constBits())
        b.pointer_down(QPointF(300, 300), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, True)
        b.pointer_move(QPointF(410, 340), pen=True, pressure=.8)
        b.pointer_up(pen=True)
        self.assertNotEqual(before, self.paper.perspective)
        self.assertEqual(pixels, bytes(self.paper.image.constBits()))
        self.assertIsNone(b.stroke)
        b.history.undo()
        self.assertEqual(before, self.paper.perspective)

    def test_shift_drag_uses_rotated_sheet_coordinates_and_lock(self):
        b = self.board
        self.paper.angle = 30.
        b.view_rotation = -25.
        b.set_tool('perspective')
        start, end = QPointF(300, 300), QPointF(370, 320)
        expected = self.paper.local(b.to_world(end))-self.paper.local(b.to_world(start))
        b.pointer_down(start, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, True)
        b.pointer_move(end, pen=True)
        b.pointer_up(pen=True)
        self.assertAlmostEqual(self.paper.perspective['offset_x'], expected.x()/self.paper.width)
        self.assertAlmostEqual(self.paper.perspective['offset_y'], expected.y()/self.paper.height)
        self.paper.perspective = {**self.paper.perspective, 'locked': True}
        before = dict(self.paper.perspective)
        b.pointer_down(start, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, True)
        b.pointer_move(end, pen=True)
        b.pointer_up(pen=True)
        self.assertEqual(before, self.paper.perspective)

    def test_pen_hover_does_not_take_over_mouse_grid_drag(self):
        b = self.board
        b.set_tool('perspective')
        b.pointer_down(QPointF(300, 300), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        before = dict(self.paper.perspective)
        b.pointer_move(QPointF(800, 600), pen=True)
        b.pointer_up(pen=True)
        self.assertEqual(before, self.paper.perspective)
        self.assertIsNotNone(b.drag)
        b.pointer_move(QPointF(370, 330))
        b.pointer_up(pen=False)
        self.assertNotEqual(before, self.paper.perspective)
        self.assertIsNone(b.stroke)

    def test_guide_cache_survives_strokes_and_export_excludes_grid(self):
        b = self.board
        b.grab()
        builds = b.perspective_renderer.builds
        b.start_stroke(QPointF(100, 100), .7, 40, 50)
        for x in range(110, 450, 15):
            b.sample_stroke(QPointF(x, 180), .7, 40, 50)
            b.grab()
        b.finish_stroke()
        self.assertEqual(builds, b.perspective_renderer.builds)
        exported = composite_paper(b.doc, self.paper)
        settings = self.paper.perspective
        self.paper.perspective = {}
        plain = composite_paper(b.doc, self.paper)
        self.assertEqual(bytes(exported.constBits()), bytes(plain.constBits()))
        self.paper.perspective = settings

    def test_roundtrip_and_independent_sheets(self):
        self.board.doc.papers.append(Paper(perspective=preset('Цилиндр · панорама')))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'space.atelier'
            save_document(self.board.doc, path)
            loaded, _ = load_document(path)
        self.assertEqual(loaded.metadata(), self.board.doc.metadata())
        self.assertIsNot(loaded.papers[0].perspective, loaded.papers[1].perspective)

    def test_guide_on_top_works_with_opaque_reference(self):
        self.paper.image.fill(QColor('#333333'))
        renderer = PerspectiveRenderer()
        from sketchpad.model import draw_paper_content
        outputs = []
        for on_top in (False, True):
            self.paper.perspective = {**self.paper.perspective, 'on_top': on_top}
            image = QImage(self.paper.width, self.paper.height, QImage.Format.Format_RGBA8888)
            painter = QPainter(image)
            draw_paper_content(painter, self.paper, renderer.draw)
            painter.end()
            outputs.append(image_array(image).copy())
        self.assertTrue(np.any(outputs[0] != outputs[1]))

    def test_dense_drag_preview_does_not_change_saved_structure(self):
        p = self.paper
        p.perspective = {**p.perspective, 'nx': 12, 'ny': 12, 'nz': 12}
        r = PerspectiveRenderer()
        r.preview_id = p.id
        preview = r.image(p, .6)
        r.preview_id = None
        complete = r.image(p, .6)
        self.assertIsNot(preview, complete)
        self.assertEqual(p.perspective['structure'], 'beams')
        self.assertEqual((p.perspective['nx'], p.perspective['ny'], p.perspective['nz']), (12, 12, 12))

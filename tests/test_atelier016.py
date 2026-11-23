import base64
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import numpy as np
from PySide6.QtCore import Qt, QPointF, QEvent
from PySide6.QtWidgets import QApplication
from sketchpad.board import Board
from sketchpad.model import Paper, Pin, Document, save_document, load_document, png_bytes, composite_paper
from sketchpad.brush import image_array, empty_ink
from sketchpad.perspective import PerspectiveSettings, PerspectiveRenderer, preset
from sketchpad.crystal import render_crystal, projected_faces
from sketchpad.crystal_native import raster, _raster_numpy
from sketchpad.autosave import RecoverySave
from sketchpad import assistant_backend as backend
from sketchpad.assistant_panel import Source, place_result

APP = QApplication.instance() or QApplication([])
ALT = Qt.KeyboardModifier.AltModifier
NONE = Qt.KeyboardModifier.NoModifier


class NewGuides(unittest.TestCase):
    def setUp(self):
        self.b = Board()
        self.b.zoom = 1
        self.b.offset = QPointF()
        self.b.guide_visible = False
        self.p = Paper(width=320,height=240)
        self.b.doc.papers = [self.p]
        self.b.selected = self.p

    def tearDown(self):
        self.b.deleteLater()

    def stroke(self, alt=True, y=40):
        self.b.pen_sample(QEvent.Type.TabletPress,QPointF(40,y),.7,10,40,modifiers=ALT if alt else NONE)
        self.b.pen_sample(QEvent.Type.TabletMove,QPointF(180,y),.7,10,40,modifiers=ALT if alt else NONE)
        self.b.pen_sample(QEvent.Type.TabletRelease,QPointF(180,y),0,10,40,modifiers=ALT if alt else NONE)

    def test_alt_keeps_main_ink_clean_with_independent_undo(self):
        self.stroke()
        layer = self.b.active_guide()
        self.assertIsNotNone(layer)
        self.assertEqual(image_array(self.p.image)[...,3].sum(),0)
        self.assertGreater(image_array(layer.image)[...,3].sum(),0)
        guide = png_bytes(layer.image)
        self.stroke(False,100)
        self.assertGreater(image_array(self.p.image)[...,3].sum(),0)
        self.assertEqual(guide,png_bytes(layer.image))
        self.b.history.undo()
        self.assertEqual(image_array(self.p.image)[...,3].sum(),0)
        self.b.history.undo()
        self.assertEqual(image_array(layer.image)[...,3].sum(),0)
        self.b.history.redo()
        self.assertEqual(guide,png_bytes(layer.image))

    def test_clear_current_guide_preserves_main_and_other_layers_with_undo(self):
        self.stroke()
        first = self.b.active_guide()
        second = self.b.add_guide_layer()
        self.stroke(y=75)
        self.stroke(False,120)
        self.p.active_guide_id = first.id
        main,other,guide = png_bytes(self.p.image),png_bytes(second.image),png_bytes(first.image)
        properties = first.meta()
        self.b.clear_paper(guide=True)
        self.assertEqual(image_array(first.image)[...,3].sum(),0)
        self.assertEqual(png_bytes(self.p.image),main)
        self.assertEqual(png_bytes(second.image),other)
        self.assertEqual(first.meta(),properties)
        self.p.active_guide_id = second.id
        self.b.history.undo()
        self.assertEqual(png_bytes(first.image),guide)
        self.b.history.redo()
        self.assertEqual(image_array(first.image)[...,3].sum(),0)
        self.assertEqual(png_bytes(second.image),other)
        self.assertEqual(png_bytes(self.p.image),main)

    def test_clear_guide_without_layer_never_clears_main(self):
        self.stroke(False)
        before,count = png_bytes(self.p.image),self.b.history.count()
        self.b.clear_paper(guide=True)
        self.assertEqual(png_bytes(self.p.image),before)
        self.assertEqual(self.b.history.count(),count)
        self.assertFalse(self.p.guide_layers)

    def test_plain_clear_preserves_guides_and_locked_sheet_rejects_both(self):
        self.stroke()
        self.stroke(False,100)
        layer = self.b.active_guide()
        before = png_bytes(layer.image)
        self.p.locked = True
        main = png_bytes(self.p.image)
        self.b.clear_paper(guide=True)
        self.b.clear_paper()
        self.assertEqual(png_bytes(layer.image),before)
        self.assertEqual(png_bytes(self.p.image),main)
        self.p.locked = False
        self.b.clear_paper()
        self.assertEqual(image_array(self.p.image)[...,3].sum(),0)
        self.assertEqual(png_bytes(layer.image),before)
        self.b.history.undo()
        self.assertEqual(png_bytes(self.p.image),main)

    def test_modifier_switch_mid_stroke_routes_both_targets(self):
        b = self.b
        b.pointer_down(QPointF(40,80),Qt.MouseButton.LeftButton,NONE,pen=True)
        b.pointer_move(QPointF(90,80),pen=True,modifiers=NONE)
        b.pointer_move(QPointF(140,80),pen=True,modifiers=ALT)
        b.pointer_move(QPointF(190,80),pen=True,modifiers=NONE)
        b.pointer_up(pen=True)
        self.assertGreater(image_array(self.p.image)[...,3].sum(),0)
        self.assertGreater(image_array(b.active_guide().image)[...,3].sum(),0)
        self.assertFalse(b.guide_drawing)

    def test_color_visibility_delete_export_and_save(self):
        clean = png_bytes(composite_paper(self.b.doc,self.p))
        self.stroke()
        layer = self.b.active_guide()
        self.b.set_guide_color('#e4bf35')
        pixels = image_array(layer.image)
        self.assertTrue(np.all(pixels[pixels[...,3]>0,:3] == [228,191,53]))
        self.assertEqual(clean,png_bytes(composite_paper(self.b.doc,self.p)))
        layer.opacity,layer.visible = .27,False
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'layers.atelier'
            save_document(self.b.doc,path)
            doc,_ = load_document(path)
            restored = doc.papers[0].guide_layers[0]
            self.assertEqual(restored.meta(),layer.meta())
            self.assertEqual(png_bytes(restored.image),png_bytes(layer.image))
            self.assertEqual(doc.papers[0].active_guide_id,layer.id)
        self.b.delete_guide_layer()
        self.assertFalse(self.p.guide_layers)
        self.b.history.undo()
        self.assertIs(self.b.active_guide(),layer)

    def test_autosave_is_immutable_for_guides(self):
        self.stroke()
        layer = self.b.active_guide()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'snapshot.atelier'
            task = RecoverySave(self.b,path,{})
            expected = png_bytes(layer.image)
            layer.image.fill(0)
            layer.visible = False
            task.run()
            doc,_ = load_document(path)
            self.assertTrue(doc.papers[0].guide_layers[0].visible)
            self.assertEqual(expected,png_bytes(doc.papers[0].guide_layers[0].image))

    def test_alt_inpaint_subtracts_without_creating_a_layer(self):
        self.b.set_tool('mask_rect')
        self.b.pointer_down(QPointF(20,20),Qt.MouseButton.LeftButton,ALT,pen=True)
        self.b.pointer_move(QPointF(100,100),pen=True,modifiers=ALT)
        self.b.pointer_up(pen=True)
        self.assertFalse(self.p.guide_layers)
        self.assertFalse(self.b.guide_drawing)

    def test_other_hand_mouse_cannot_change_pen_layer(self):
        b = self.b
        b.set_dual(True)
        b.pointer_down(QPointF(40,40),Qt.MouseButton.LeftButton,ALT,pen=True)
        stroke,target = b.stroke,b.stroke_target
        b.pointer_down(QPointF(80,80),Qt.MouseButton.LeftButton,NONE,pen=False)
        b.pointer_move(QPointF(90,90),pen=False,modifiers=NONE)
        b.pointer_up(pen=False)
        self.assertIs(b.stroke,stroke)
        self.assertIs(b.stroke_target,target)
        self.assertTrue(b.guide_drawing)
        b.pointer_up(pen=True)


class PinNetwork(unittest.TestCase):
    def test_fan_connections_delete_link_and_undo_pin(self):
        b = Board()
        b.doc.pins,b.doc.bands = [],[]
        pins = [b.add_pin(QPointF(50+i*80,100)) for i in range(4)]
        for pin in pins[1:]:
            b.add_band([pins[0].id,pin.id])
        self.assertEqual(len(b.doc.bands),3)
        first = b.doc.bands[0]
        b.add_band(list(reversed(first.pins)))
        self.assertEqual(len(b.doc.bands),3)
        b.delete_band()
        self.assertEqual(len(b.doc.bands),2)
        self.assertEqual(len(b.doc.pins),4)
        b.history.undo()
        self.assertEqual(len(b.doc.bands),3)
        b.set_selection(pins[0]);b.delete_selected()
        self.assertFalse(b.doc.bands)
        b.history.undo()
        self.assertEqual(len(b.doc.bands),3)
        pins[0].x += 40
        for band in b.doc.bands:
            self.assertEqual(b.doc.lines(band)[0][0].x(),pins[0].x)
        b.deleteLater()

    def test_legacy_ring_converts_without_changing_lines(self):
        doc = Document(False)
        doc.papers = [Paper(width=20,height=20)]
        doc.pins = [Pin(5,10),Pin(45,10)]
        meta = doc.metadata()
        meta.update(version=3,bands=[{'id':'old','pins':[p.id for p in doc.pins], 'x':25,'y':30,'name':'Две ветви'}])
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'legacy.atelier'
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('board.json',json.dumps(meta))
                z.writestr(f'images/{doc.papers[0].id}.png',png_bytes(doc.papers[0].image))
            restored,_=load_document(path)
            self.assertEqual(len(restored.pins),3)
            self.assertEqual(len(restored.bands),2)
            self.assertEqual([restored.lines(b)[0][1] for b in restored.bands],[QPointF(25,30)]*2)
            save_document(restored,path)
            again,_=load_document(path)
            self.assertEqual(len(again.pins),3)
            self.assertEqual(again.metadata()['bands'],restored.metadata()['bands'])


class CrystalAndZoom(unittest.TestCase):
    def test_hidden_lines_leave_depth_and_visible_geometry_unchanged(self):
        s=PerspectiveSettings.read(preset('Кристалл · снаружи'))
        base,depth,normals,cov=render_crystal(s,160,120,'ivory',return_buffers=True)
        shown,d2,n2,_=render_crystal(replace(s,hidden_opacity=.6),160,120,'ivory',return_buffers=True)
        self.assertTrue(np.array_equal(depth,d2))
        self.assertTrue(np.array_equal(normals,n2))
        a,b=image_array(base)[...,3],image_array(shown)[...,3]
        self.assertGreater(np.count_nonzero(b>a),100)
        self.assertTrue(np.all(b>=a))
        self.assertTrue(np.array_equal(a[normals<0],b[normals<0]))
        args=projected_faces(s,160,120,include_hidden=True)
        hidden=np.zeros_like(depth);fallback=np.zeros_like(depth)
        raster(*args,depth,hidden,normals,s.line_width,True)
        _raster_numpy(*args,depth,fallback,normals,s.line_width,True)
        self.assertTrue(np.allclose(hidden,fallback,atol=1e-5))

    def test_zoom_out_reuses_all_fifteen_guide_images(self):
        renderer=PerspectiveRenderer()
        papers=[Paper(width=160,height=120,perspective=replace(PerspectiveSettings(),enabled=True,yaw=i).data()) for i in range(15)]
        for p in papers:renderer.image(p,1.)
        builds=renderer.builds
        for scale in (.8,.5,.25):
            for p in papers:renderer.image(p,scale)
        self.assertEqual(renderer.builds,builds)
        self.assertLessEqual(sum(i.sizeInBytes() for i in renderer.cache.values()),64*1024*1024)

    def test_focus_keeps_only_overlapping_tracing_stack(self):
        b=Board()
        base=Paper(width=100,height=100)
        elsewhere=Paper(width=100,height=100,x=400)
        tracing=Paper(width=100,height=100,paper_opacity=.2)
        b.doc.papers=[base,elsewhere,tracing]
        b.focused_paper_id=tracing.id
        self.assertEqual(b.visible_papers(),[base,tracing])
        self.assertIsNone(b.paper_at(QPointF(450,30)))
        b.zoom=.08
        self.assertEqual(b.visible_papers(),[base,tracing])
        tracing.paper_opacity=1
        self.assertEqual(b.visible_papers(),[tracing])
        b.deleteLater()


class AssistantFidelity(unittest.TestCase):
    def test_user_text_is_verbatim_even_with_selected_style(self):
        text='  Убери жёлтый фон.\nСделай фото, а не рисунок!  '
        for style in backend.load_presets():
            self.assertEqual(backend.build_prompt(style,text),text)
        self.assertNotEqual(backend.build_prompt(style,text,force_style=True),text)

    def test_api_receives_exact_prompt_and_high_quality(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source.png';source.write_bytes(b'original')
            text='  Чистый белый фон\nСиняя птица  '
            req=backend.Request(source,root,text,provider='api',key='secret')
            with patch.object(backend,'api_post',return_value={'data':[{'b64_json':base64.b64encode(b'image').decode()}]}) as post:
                backend.generate(req,backend.Cancellation())
            self.assertEqual(post.call_count,1)
            self.assertEqual(post.call_args.args[1]['prompt'],text)
            self.assertEqual(post.call_args.args[1]['quality'],'high')

    def test_subscription_receives_original_reference_and_literal_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source.png';source.write_bytes(b'original')
            result=root/'generated.png';result.write_bytes(b'generated')
            text='  Синий свет.\nБез бумаги и карандашной стилизации.  '
            req=backend.Request(source,root/'job',text)
            with patch.object(backend,'subscription_status',return_value={'ready':True,'path':'codex.exe'}), \
                 patch.object(backend,'generated_file',return_value=result), \
                 patch.object(backend,'run_codex',return_value=(0,'',[{'type':'turn.completed'}])) as run:
                backend.generate(req,backend.Cancellation())
            sent=run.call_args.kwargs['prompt']
            encoded=sent.split('USER PROMPT (JSON-encoded only for transport; decode and preserve every character):\n')[1]
            self.assertEqual(json.loads(encoded),text)
            self.assertIn('verbatim',sent)
            self.assertNotIn(backend.BASE_BRIEF,sent)
            self.assertNotIn('refine the visual brief',sent)
            args=run.call_args.args[1]
            self.assertEqual(args[args.index('--image')+1],str(source))

    def test_whole_result_preserves_resolution_and_physical_footprint(self):
        b=Board();p=Paper(width=100,height=70,scale=.8)
        b.doc.papers=[p]
        image=empty_ink(400,280);image.fill(0xffffffff)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'result.png';image.save(str(path))
            out=place_result(Source(b,b.doc,p.id,p.name,'test'),path,'test')
        self.assertEqual(out.image.size(),image.size())
        self.assertEqual(out.width*out.scale,p.width*p.scale)
        self.assertEqual(png_bytes(out.image),png_bytes(image))
        b.deleteLater()

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from sketchpad.brush import PRESETS
from sketchpad.brush_library import BrushLibrary, FIRST_BRUSH


class BrushLibraryTests(unittest.TestCase):
    def test_separate_presets_persist_and_reset_one_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'brushes.json'
            lib = BrushLibrary(path)
            hb = replace(PRESETS[FIRST_BRUSH], size=8.7, sharpening_length=9.5, color=(255,255,255))
            charcoal = replace(PRESETS['Уголь'], size=12.2, tip_sharpness=.93)
            lib.remember(FIRST_BRUSH, hb)
            lib.remember('Уголь', charcoal)
            lib.select('Уголь')
            lib.remember_eraser(replace(hb, eraser_strength=.21, eraser_shape='pencil'), 71)
            lib.save()
            reopened = BrushLibrary(path)
            self.assertEqual(reopened.get(FIRST_BRUSH), hb)
            self.assertEqual(reopened.get('Уголь'), charcoal)
            self.assertEqual(reopened.active, 'Уголь')
            self.assertEqual(reopened.eraser, {'size':71, 'strength':.21, 'shape':'pencil'})
            reopened.reset('Уголь')
            reopened.save()
            reset = BrushLibrary(path)
            self.assertEqual(reset.get('Уголь'), PRESETS['Уголь'])
            self.assertEqual(reset.get(FIRST_BRUSH), hb)
            self.assertEqual(PRESETS[FIRST_BRUSH].size, 3.2)

    def test_malformed_preferences_use_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'brushes.json'
            path.write_text(json.dumps({'active':[], 'presets':{FIRST_BRUSH:{'size':-2, 'opacity':True,
                'sharpening_length':float('nan'), 'color':[999,0,0]}}, 'eraser':{'size':-100, 'shape':[]}}))
            lib = BrushLibrary(path)
            self.assertEqual(lib.get(FIRST_BRUSH), PRESETS[FIRST_BRUSH])
            self.assertEqual(lib.active, FIRST_BRUSH)
            self.assertEqual(lib.eraser['shape'], 'round')
            path.write_text('broken')
            self.assertEqual(BrushLibrary(path).get(FIRST_BRUSH), PRESETS[FIRST_BRUSH])

    def test_independent_windows_merge_untouched_presets(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'brushes.json'
            first, second = BrushLibrary(path), BrushLibrary(path)
            first.remember(FIRST_BRUSH, replace(PRESETS[FIRST_BRUSH], size=11))
            first.save()
            second.remember('Уголь', replace(PRESETS['Уголь'], size=14))
            second.save()
            reopened = BrushLibrary(path)
            self.assertEqual(reopened.get(FIRST_BRUSH).size, 11)
            self.assertEqual(reopened.get('Уголь').size, 14)

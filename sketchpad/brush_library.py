"""Personal brush presets, separate from artwork and immutable factory defaults."""
from dataclasses import asdict, replace
import json
import math
import os
from pathlib import Path
import uuid
from .brush import PRESETS

BRUSH_LIMITS = {'size':(.5,16), 'opacity':(.05,1), 'grain':(0,1), 'tilt_gain':(0,12),
                'pressure_gamma':(.3,2), 'smoothing':(0,.85), 'tip_sharpness':(0,1),
                'sharpening_length':(.25,10)}
FIRST_BRUSH = next(iter(PRESETS))


def read_library(path):
    try:
        if path.stat().st_size > 128_000:
            return {}
        value = json.loads(path.read_text(encoding='utf8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def brush_values(name, values):
    result = asdict(PRESETS[name])
    if isinstance(values, dict):
        for key, (low, high) in BRUSH_LIMITS.items():
            value = values.get(key)
            if type(value) in (int, float) and math.isfinite(value) and low <= value <= high:
                result[key] = value
        color = values.get('color')
        if isinstance(color, (list, tuple)) and len(color) == 3 and all(type(v) is int and 0 <= v <= 255 for v in color):
            result['color'] = tuple(color)
    return {k:result[k] for k in (*BRUSH_LIMITS, 'color')}


def eraser_values(values):
    result = {'size':22., 'strength':.65, 'shape':'round'}
    if isinstance(values, dict):
        for key, (low, high) in {'size':(3,100), 'strength':(.05,1)}.items():
            value = values.get(key)
            if type(value) in (int, float) and math.isfinite(value) and low <= value <= high:
                result[key] = value
        if values.get('shape') in ('round', 'pencil'):
            result['shape'] = values['shape']
    return result


class BrushLibrary:
    def __init__(self, path):
        self.path = Path(path)
        data = read_library(self.path)
        presets = data.get('presets', {})
        if not isinstance(presets, dict):
            presets = {}
        self.presets = {name:brush_values(name, presets.get(name)) for name in PRESETS}
        active = data.get('active')
        self.active = active if isinstance(active, str) and active in PRESETS else FIRST_BRUSH
        self.eraser = eraser_values(data.get('eraser'))
        self.pending = set()
        self.eraser_dirty = self.active_dirty = False

    def get(self, name):
        return replace(PRESETS[name], **self.presets[name])

    def select(self, name):
        if name in PRESETS:
            self.active, self.active_dirty = name, True

    def remember(self, name, brush):
        self.presets[name] = brush_values(name, asdict(brush))
        self.pending.add(name)

    def remember_eraser(self, brush, size):
        self.eraser = eraser_values({'size':size, 'strength':brush.eraser_strength, 'shape':brush.eraser_shape})
        self.eraser_dirty = True

    def reset(self, name):
        self.remember(name, PRESETS[name])

    def save(self):
        if not (self.pending or self.eraser_dirty or self.active_dirty):
            return
        # Merge only edited presets so independent application windows don't
        # overwrite each other's untouched brushes.
        disk = BrushLibrary(self.path)
        for name in self.pending:
            disk.presets[name] = self.presets[name]
        if self.active_dirty:
            disk.active = self.active
        if self.eraser_dirty:
            disk.eraser = self.eraser
        data = {'version':1, 'active':disk.active, 'presets':disk.presets, 'eraser':disk.eraser}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name('.brushes-'+uuid.uuid4().hex+'.tmp')
        try:
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)
        self.pending.clear()
        self.eraser_dirty = self.active_dirty = False

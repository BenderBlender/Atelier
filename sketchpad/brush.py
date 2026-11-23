"""Paper-anchored graphite: a sharp tip with one-sided, tapered flank contact."""
from dataclasses import dataclass, replace
from functools import lru_cache
import math
import numpy as np
from PySide6.QtGui import QImage
from . import brush_native


@dataclass
class BrushSettings:
    size: float = 3.2
    opacity: float = 0.74
    grain: float = 0.67
    tilt_gain: float = 6.5
    pressure_gamma: float = 0.85
    smoothing: float = 0.12
    color: tuple = (45, 43, 40)
    eraser: bool = False
    tip_sharpness: float = .5
    eraser_strength: float = .65
    eraser_shape: str = 'round'
    sharpening_length: float = 1.


PRESETS = {
    'Графит · HB': BrushSettings(),
    'Мягкий · 4B': BrushSettings(size=4.8, opacity=.87, grain=.58, tilt_gain=7.8, pressure_gamma=.7),
    'Точный · 2H': BrushSettings(size=2.0, opacity=.51, grain=.72, tilt_gain=4.8, pressure_gamma=1.1),
    'Уголь': BrushSettings(size=8.0, opacity=.82, grain=.86, tilt_gain=8.0, pressure_gamma=.7),
}


@lru_cache(maxsize=1)
def paper_tooth():
    """One immutable height map for visible paper texture and pigment contact."""
    rng = np.random.default_rng(731)
    tooth = rng.random((512, 512), dtype=np.float32)
    tooth = .82*tooth+.18*np.roll(tooth, 1, axis=0)
    tooth.setflags(write=False)
    return tooth


def image_array(image):
    """Writable RGBA view; caller must not keep it after QImage mutation/copy."""
    return np.frombuffer(image.bits(), np.uint8).reshape(image.height(), image.bytesPerLine())[:, :image.width()*4].reshape(image.height(), image.width(), 4)


@dataclass(frozen=True)
class PencilContact:
    # Local +X points from the nib toward the tilted barrel. The nib stays at
    # (0, 0); the growing contact must never be centred on the pointer.
    tip: float
    length: float
    width: float
    angle: float
    pressure: float

    def outline(self):
        """Same tapered silhouette as the deposition mask, in local pixels."""
        front = [(self.tip*math.cos(a), self.tip*math.sin(a))
                 for a in np.linspace(math.pi/2, 3*math.pi/2, 17)]
        back = [(self.length+self.width*math.cos(a), self.width*math.sin(a))
                for a in np.linspace(-math.pi/2, math.pi/2, 17)]
        return front+back

    def graphite_mask(self, u, v):
        """Antialiased cone contact and the local load along its exposed flank."""
        radius = np.hypot(u, v)
        if self.length < 1e-6:
            edge = np.clip(self.tip+.5-radius, 0, 1)
            profile = .56+.44*np.clip(1-(radius/self.tip)**2, 0, 1)
            return edge*profile, self.pressure
        along = np.clip(u/self.length, 0, 1)
        half_width = self.tip+(self.width-self.tip)*along
        slope = (self.width-self.tip)/self.length
        side_distance = (half_width-np.abs(v))/math.sqrt(1+slope*slope)
        back_distance = self.width-np.hypot(u-self.length, v)
        distance = np.where(u < 0, self.tip-radius,
                            np.where(u > self.length, back_distance, side_distance))
        edge = np.clip(distance+.5, 0, 1)
        flank = np.clip(u/(self.length+self.width), 0, 1)
        radial = np.where(u < 0, radius/self.tip,
                          np.where(u > self.length,
                                   np.hypot(u-self.length, v)/self.width,
                                   np.abs(v)/half_width))
        profile = .56+.44*np.clip(1-radial*radial, 0, 1)
        # Wide sections sweep across more pixels/dabs. Distribute their load
        # over that width so movement does not make the flank darker than the nib.
        load = (self.tip/half_width)**.85
        return edge*profile*(1-.58*flank)*load, self.pressure*(1-.42*flank)


def pencil_contact(settings, pressure, tx, ty):
    p = max(0., min(1., pressure)) ** settings.pressure_gamma
    # Qt reports tilt from the surface normal on each axis, in degrees.
    sx, sy = math.tan(math.radians(max(-85, min(85, tx)))), math.tan(math.radians(max(-85, min(85, ty))))
    tilt = math.atan(math.hypot(sx, sy)) / (math.pi / 2)
    angle = math.atan2(sy, sx) if sx or sy else 0.
    if settings.eraser and settings.eraser_shape != 'pencil':
        r = settings.size * (0.5 + 0.5 * p)
        return PencilContact(r, 0., r, 0., p)
    base_tip = max(.32, settings.size * (.10 + .28 * p))
    sharpness = max(0., min(1., settings.tip_sharpness))
    # 50% keeps the accepted 0.4 nib; higher values sharpen only its point.
    tip = max(.32, base_tip * 2**(3*(.5-sharpness)))
    flank = settings.size * settings.tilt_gain * tilt**1.65 * (.5+.5*math.sqrt(p))
    # Sharpening extends the exposed graphite along the barrel, independently
    # of its breadth. 100% preserves the accepted contact; an upright nib is
    # unchanged. Tilt gain still controls the overall opening of the flank.
    length = flank * max(.25, min(10., settings.sharpening_length))
    # Blend into the original flank width as the pencil leans. Upright stays
    # circular, and a blunt point can never be wider than its own contact body.
    blend = min(1., flank/(4*base_tip))
    blend = blend*blend*(3-2*blend)
    width = max(tip, tip+(base_tip-tip)*blend+.28*flank)
    return PencilContact(tip, length, width, angle, p)


def footprint(settings, pressure, tx, ty):
    """Contact extents, orientation and pressure; retained for brush consumers."""
    c = pencil_contact(settings, pressure, tx, ty)
    return c.length+c.width, c.width, c.angle, c.pressure


class GraphiteStroke:
    TILE = 128

    def __init__(self, image, settings):
        self.image = image
        self.settings = replace(settings)
        self.previous = None
        self.before = {}
        self.eraser_alpha = {}
        self.dabs = 0
        self.native = brush_native.raster is not None
        # Paper height is deterministic, fixed in paper space, and shared across strokes.
        self.tooth = paper_tooth()

    def remember(self, x0, y0, x1, y1):
        for ty in range(y0 // self.TILE, (y1 - 1) // self.TILE + 1):
            for tx in range(x0 // self.TILE, (x1 - 1) // self.TILE + 1):
                key = (tx * self.TILE, ty * self.TILE)
                if key not in self.before:
                    self.before[key] = self.image.copy(*key, min(self.TILE, self.image.width()-key[0]), min(self.TILE, self.image.height()-key[1]))
                    if self.settings.eraser:
                        initial = image_array(self.before[key])[..., 3].astype(np.float32)/255
                        self.eraser_alpha[key] = (initial, initial.copy())

    def lift_pigment(self, bounds, rgba, pickup, retention):
        """Retain fine lifting in float tiles until display/undo quantization.

        A faint grain of the original pigment remains in the paper pockets for
        this pass. Another stroke can lift it further; blank pixels stay blank.
        """
        x0, y0, x1, y1 = bounds
        for tile_y in range(y0//self.TILE, (y1-1)//self.TILE+1):
            for tile_x in range(x0//self.TILE, (x1-1)//self.TILE+1):
                tx, ty = tile_x*self.TILE, tile_y*self.TILE
                a, b = max(x0, tx), max(y0, ty)
                c, d = min(x1, tx+self.TILE), min(y1, ty+self.TILE)
                local = np.s_[b-y0:d-y0, a-x0:c-x0]
                tile = np.s_[b-ty:d-ty, a-tx:c-tx]
                original, work = self.eraser_alpha[(tx, ty)]
                alpha = work[tile]
                bound = original[tile]*retention[local]
                alpha -= np.maximum(alpha-bound, 0)*pickup[local]
                rgba[..., 3][local] = np.round(alpha*255).astype(np.uint8)

    def dab(self, x, y, pressure, tx, ty, flow=1.):
        if pressure <= 0:
            return
        s = self.settings
        c = pencil_contact(s, pressure, tx, ty)
        p = c.pressure
        co, si = math.cos(c.angle), math.sin(c.angle)
        bx, by = x+c.length*co, y+c.length*si
        x0 = max(0, math.floor(min(x-c.tip, bx-c.width)-1))
        y0 = max(0, math.floor(min(y-c.tip, by-c.width)-1))
        x1 = min(self.image.width(), math.ceil(max(x+c.tip, bx+c.width)+1))
        y1 = min(self.image.height(), math.ceil(max(y+c.tip, by+c.width)+1))
        if x1 <= x0 or y1 <= y0:
            return
        self.remember(x0, y0, x1, y1)
        if self.native:
            rgba = image_array(self.image)
            args = (float(x), float(y), c.tip, c.length, c.width, co, si,
                    p, float(s.grain), float(s.opacity), float(flow), tuple(float(v) for v in s.color),
                    (2 if s.eraser_shape == 'pencil' else 1) if s.eraser else 0,
                    max(0., min(1., s.eraser_strength)))
            if s.eraser:
                for tile_y in range(y0//self.TILE, (y1-1)//self.TILE+1):
                    for tile_x in range(x0//self.TILE, (x1-1)//self.TILE+1):
                        ox, oy = tile_x*self.TILE, tile_y*self.TILE
                        a, b = max(x0, ox), max(y0, oy)
                        d, e = min(x1, ox+self.TILE), min(y1, oy+self.TILE)
                        original, work = self.eraser_alpha[(ox, oy)]
                        tile = np.s_[b-oy:e-oy, a-ox:d-ox]
                        brush_native.raster(rgba[b:e, a:d], self.tooth, a, b, *args,
                                            original[tile], work[tile])
            else:
                brush_native.raster(rgba[y0:y1, x0:x1], self.tooth, x0, y0, *args,
                                    brush_native.EMPTY, brush_native.EMPTY)
            self.dabs += 1
            return
        yy, xx = np.ogrid[y0:y1, x0:x1]
        dx, dy = xx + .5 - x, yy + .5 - y
        if s.eraser:
            tooth = self.tooth[yy % 512, xx % 512]
            if s.eraser_shape == 'pencil':
                # Reuse the drawing nib, flank and paper-contact mask exactly;
                # the same footprint now removes pigment instead of depositing it.
                edge, local_pressure = c.graphite_mask(dx*co+dy*si, dy*co-dx*si)
                contact = np.clip((local_pressure*.74+.34-tooth)*3.4, 0, 1)
                fiber_pickup = (1-s.grain)+s.grain*contact
            else:
                r2 = (dx*dx+dy*dy)/(c.tip*c.tip)
                edge = np.clip((1-r2)*1.65, 0, 1)**1.4
                # Low tooth values are the fibers reached first by the pencil;
                # their loose pigment is also lifted first. Deeper pockets retain tone.
                fiber_pickup = .22+.78*(1-tooth)**1.3
            strength = max(0., min(1., s.eraser_strength))
            load = 2.6*strength*p**1.15*max(0., flow)
            alpha = 1-np.exp(-edge*fiber_pickup*load)
            retention = (.02+.18*tooth**2.2)*(1-.5*p)
        else:
            edge, local_pressure = c.graphite_mask(dx*co+dy*si, dy*co-dx*si)
            tooth = self.tooth[yy % 512, xx % 512]
            # Light pressure deposits only on the highest fibers; harder pressure fills valleys.
            contact = np.clip((local_pressure*.74 + .34 - tooth) * 3.4, 0, 1)
            grain = (1-s.grain) + s.grain*contact
            alpha = np.clip(edge * grain * s.opacity * (.055 + .19*p) * flow, 0, .92)
        rgba = image_array(self.image)[y0:y1, x0:x1]
        old_a = rgba[..., 3].astype(np.float32) / 255
        if s.eraser:
            self.lift_pigment((x0, y0, x1, y1), rgba, alpha, retention)
        else:
            out_a = alpha + old_a*(1-alpha)
            safe = np.maximum(out_a, 1e-8)
            for c in range(3):
                out = (s.color[c]*alpha + rgba[..., c]*old_a*(1-alpha))/safe
                rgba[..., c] = np.clip(np.round(out), 0, 255).astype(np.uint8)
            rgba[..., 3] = np.round(out_a*255).astype(np.uint8)
        self.dabs += 1

    def add(self, x, y, pressure, tx=0., ty=0., final=False):
        point = np.array([x, y, pressure, tx, ty], dtype=float)
        if self.previous is None:
            self.previous = point
            self.dab(*point)
            return
        prev = self.previous
        # No delayed queue: smoothing is a short spatial low-pass filter.
        if not final:
            blend = 1 - self.settings.smoothing*.72
            point[:2] = prev[:2] + (point[:2]-prev[:2])*blend
        distance = float(np.linalg.norm(point[:2]-prev[:2]))
        if distance < .12:
            return
        c = pencil_contact(self.settings, pressure, tx, ty)
        # Sample by the sharp nib, so broad shading never turns its edge into
        # a dotted line. Tilt must not dilute the graphite at that nib.
        round_eraser = self.settings.eraser and self.settings.eraser_shape != 'pencil'
        spacing = (max(.35, min(1.8, c.tip*.32)) if round_eraser
                   else max(.25, min(1.8, c.tip*.45)))
        steps = max(1, math.ceil(distance/spacing))
        for i in range(1, steps+1):
            sample = prev + (point-prev)*(i/steps)
            normalization = c.tip*(.55 if round_eraser else .75)
            self.dab(*sample, flow=(distance/steps)/max(.5, normalization))
        self.previous = point

    def patches(self):
        after = {key: self.image.copy(*key, value.width(), value.height()) for key, value in self.before.items()}
        return self.before, after


def empty_ink(w, h):
    image = QImage(w, h, QImage.Format.Format_RGBA8888)
    image.fill(0)
    return image

"""Editable 3D drawing guides. Geometry is projected, never painted into ink."""
from dataclasses import dataclass, asdict, replace
from collections import OrderedDict
import math
import numpy as np
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QImage, QPainter, QPainterPath, QColor, QPen

PROJECTIONS = {
    'linear': 'Обычная перспектива', 'parallel': 'Параллельная',
    'cylinder': 'Цилиндрическая', 'fish': 'Рыбий глаз · сфера',
    'azimuthal': 'Рыбий глаз · равноудалённая',
    'stereo': 'Стереографическая', 'perceptual': 'Перцептивная · художественная',
}
STRUCTURES = {'crystal': 'Кристалл Эшера · кубики',
              'lattice': 'Объёмная решётка', 'beams': 'Перекладины · прозрачные',
              'box': 'Куб с размеченными гранями', 'floor': 'Плоскость пола'}


@dataclass(frozen=True)
class PerspectiveSettings:
    enabled: bool = False
    locked: bool = False
    projection: str = 'linear'
    structure: str = 'lattice'
    nx: int = 4
    ny: int = 4
    nz: int = 4
    sx: float = 1.
    sy: float = 1.
    sz: float = 1.
    divisions: int = 2
    beam: float = .07
    crystal_divisions: int = 5
    face_tone: float = .12
    yaw: float = -28.
    pitch: float = 18.
    roll: float = 0.
    distance: float = 1.6
    camera_x: float = 0.
    camera_y: float = 0.
    fov: float = 50.
    scale: float = 1.
    offset_x: float = 0.
    offset_y: float = 0.
    cylinder_axis: float = 0.
    opacity: float = .48
    hidden_opacity: float = 0.
    fog: float = .75
    line_width: float = 1.
    axis_colors: bool = False
    on_top: bool = True
    bend: float = 0.
    twist: float = 0.
    taper: float = 0.
    shear: float = 0.
    perceptual_strength: float = .75
    perceptual_range: float = .8
    near_reverse: float = 0.

    @classmethod
    def read(cls, value):
        if not isinstance(value, dict):
            return cls()
        data = asdict(cls())
        limits = {
            **{k: (1, 12) for k in ('nx', 'ny', 'nz')},
            **{k: (.2, 4.) for k in ('sx', 'sy', 'sz')},
            'divisions': (1, 6), 'beam': (.02, .25),
            'crystal_divisions': (3, 12), 'face_tone': (0., .5),
            **{k: (-180., 180.) for k in ('yaw', 'pitch', 'roll', 'cylinder_axis')},
            'distance': (0., 6.), 'fov': (20., 360.), 'scale': (.2, 4.),
            'camera_x': (-2., 2.), 'camera_y': (-2., 2.),
            'offset_x': (-1., 1.), 'offset_y': (-1., 1.),
            'opacity': (.05, 1.), 'hidden_opacity': (0., 1.), 'fog': (0., 1.), 'line_width': (.5, 3.),
            'bend': (-2., 2.), 'twist': (-180., 180.), 'taper': (-.8, .8),
            'shear': (-2., 2.), 'perceptual_strength': (0., 1.),
            'perceptual_range': (.15, 3.), 'near_reverse': (0., 1.),
        }
        for key, default in data.items():
            candidate = value.get(key)
            if isinstance(default, bool):
                if isinstance(candidate, bool):
                    data[key] = candidate
            elif key in limits:
                if isinstance(candidate, (int, float)) and not isinstance(candidate, bool) and math.isfinite(candidate):
                    low, high = limits[key]
                    data[key] = max(low, min(high, candidate))
                    data[key] = int(round(data[key])) if isinstance(default, int) else float(data[key])
            elif key == 'projection' and isinstance(candidate, str) and candidate in PROJECTIONS:
                data[key] = candidate
            elif key == 'structure' and isinstance(candidate, str) and candidate in STRUCTURES:
                data[key] = candidate
        return cls(**data)

    def data(self):
        return asdict(self)

    @property
    def extent(self):
        return max(self.nx*self.sx, self.ny*self.sy, self.nz*self.sz)

    @property
    def effective_fov(self):
        return min(self.fov, 165. if self.projection in ('linear', 'perceptual') else
                   180. if self.projection == 'fish' else 360. if self.projection == 'cylinder' else 350.)


PRESETS = {
    'Кристалл Эшера': {'structure': 'crystal', 'nx': 5, 'ny': 5, 'nz': 7,
        'distance': 0., 'yaw': -30., 'pitch': -12., 'fov': 100., 'opacity': .7, 'fog': .65},
    'Кристалл · снаружи': {'structure': 'crystal', 'nx': 3, 'ny': 3, 'nz': 3,
        'distance': 1.6, 'yaw': -28., 'pitch': 18., 'fov': 50., 'opacity': .75, 'fog': .5},
    'Кристалл · цилиндр': {'structure': 'crystal', 'projection': 'cylinder',
        'nx': 5, 'ny': 5, 'nz': 7, 'distance': 0., 'yaw': -30., 'pitch': 0.,
        'fov': 250., 'opacity': .7, 'fog': .65},
    'Кристалл · рыбий глаз': {'structure': 'crystal', 'projection': 'fish',
        'nx': 5, 'ny': 5, 'nz': 7, 'distance': 0., 'yaw': -30., 'pitch': -12.,
        'fov': 180., 'opacity': .7, 'fog': .65},
    'Объём · три точки': {},
    'Комната · одна точка': {'structure': 'box', 'nx': 4, 'ny': 4, 'nz': 8,
        'yaw': 0., 'pitch': 0., 'distance': 0., 'fov': 100.},
    'Прозрачные перекладины · 0.14': {'structure': 'beams', 'nx': 3, 'ny': 3, 'nz': 5,
        'distance': 0., 'yaw': -15., 'pitch': -5., 'fov': 100., 'fog': .9},
    'Куб с делениями': {'structure': 'box'},
    'Цилиндр · панорама': {'structure': 'box', 'projection': 'cylinder', 'nx': 5,
        'ny': 3, 'nz': 5, 'distance': 0., 'yaw': -15., 'pitch': 0., 'fov': 250.},
    'Цилиндр · вертикальный': {'structure': 'box', 'projection': 'cylinder',
        'nx': 3, 'ny': 5, 'nz': 5, 'distance': 0., 'yaw': 0., 'pitch': -15.,
        'fov': 250., 'cylinder_axis': 90.},
    'Цилиндр · наклонный': {'structure': 'box', 'projection': 'cylinder',
        'nx': 5, 'ny': 3, 'nz': 5, 'distance': 0., 'yaw': -20., 'pitch': 0.,
        'fov': 300., 'cylinder_axis': 35.},
    'Рыбий глаз · сфера': {'structure': 'box', 'projection': 'fish', 'distance': 0.,
        'nx': 5, 'ny': 3, 'nz': 5, 'yaw': -12., 'pitch': 0., 'fov': 180.},
    'Рыбий глаз · широкий': {'structure': 'box', 'projection': 'azimuthal',
        'distance': 0., 'nx': 5, 'ny': 3, 'nz': 5, 'yaw': -12., 'pitch': 5., 'fov': 270.},
    'Параллельный объём': {'projection': 'parallel', 'yaw': -35., 'pitch': 30.},
    'Перцептивная': {'projection': 'perceptual', 'perceptual_strength': .85},
}


def preset(name):
    if name == 'Решётка Chelsea':
        name = 'Прозрачные перекладины · 0.14'
    return PerspectiveSettings.read({**PerspectiveSettings().data(), **PRESETS[name], 'enabled': True}).data()


def rotation(yaw, pitch, roll):
    y, p, r = np.radians([yaw, pitch, roll])
    cy, sy, cp, sp, cr, sr = math.cos(y), math.sin(y), math.cos(p), math.sin(p), math.cos(r), math.sin(r)
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    rz = np.array([[cr, -sr, 0], [sr, cr, 0], [0, 0, 1]])
    return rz @ rx @ ry


def camera_points(points, s):
    q = np.asarray(points, dtype=float).copy()
    z = q[..., 2]/max(.1, s.nz*s.sz/2)
    q[..., :2] *= (1+s.taper*z)[..., None]
    angle = np.radians(s.twist)*z
    x, y = q[..., 0].copy(), q[..., 1].copy()
    q[..., 0] = x*np.cos(angle)-y*np.sin(angle)
    q[..., 1] = x*np.sin(angle)+y*np.cos(angle)
    q[..., 0] += (s.bend*z*z+s.shear*z)*s.extent/2
    q = q @ rotation(s.yaw, s.pitch, s.roll).T
    q[..., 0] -= s.camera_x*s.extent
    q[..., 1] -= s.camera_y*s.extent
    q[..., 2] += s.distance*s.extent
    return q


def project(points, s, width, height):
    """Camera coordinates -> sheet pixels, valid domain, radial/camera depth."""
    x, y, z = np.moveaxis(np.asarray(points, dtype=float), -1, 0)
    radius = np.sqrt(x*x+y*y+z*z)
    half = math.radians(s.effective_fov)/2
    field = min(width, height)*.44*s.scale
    valid = radius > s.extent*1e-6
    seam = np.zeros_like(z)
    near = s.extent*.002
    if s.projection in ('linear', 'perceptual'):
        denominator = np.maximum(z, near)
        if s.projection == 'perceptual':
            reference = max(.35, s.distance)*s.extent
            delta = np.maximum(z-reference, 0)
            t = np.clip(delta/(reference*s.perceptual_range), 0, 1)
            smooth = t*t*(3-2*t)
            mapped = reference+delta*smooth
            mapped += s.near_reverse*np.maximum(reference-z, 0)**2/reference
            denominator = np.maximum(near, (1-s.perceptual_strength)*z+s.perceptual_strength*mapped)
        u, v = x/denominator/math.tan(half), y/denominator/math.tan(half)
        valid &= z >= near
    elif s.projection == 'parallel':
        valid = np.ones_like(z, dtype=bool)
        u, v = x/(s.extent*.65), y/(s.extent*.65)
    elif s.projection == 'cylinder':
        a = math.radians(s.cylinder_axis)
        co, si = math.cos(a), math.sin(a)
        xx, yy = x*co+y*si, y*co-x*si
        horizontal = np.hypot(xx, z)
        theta = np.arctan2(xx, z)
        uu, vv = theta/half, yy/np.maximum(horizontal, near)/half
        u, v = uu*co-vv*si, vv*co+uu*si
        valid &= (horizontal >= near) & (np.abs(theta) <= half)
        seam = theta
    else:
        rho = np.hypot(x, y)
        theta = np.arctan2(rho, z)
        if s.projection == 'fish':
            r = np.sin(theta)/math.sin(half)
        elif s.projection == 'stereo':
            r = np.tan(theta/2)/math.tan(half/2)
        else:
            r = theta/half
        u, v = r*x/np.maximum(rho, 1e-12), r*y/np.maximum(rho, 1e-12)
        valid &= theta <= half
    uv = np.stack((width*(.5+s.offset_x)+field*u,
                   height*(.5+s.offset_y)+field*v), axis=-1)
    valid &= np.isfinite(uv).all(axis=-1)
    return uv, valid, (z if s.projection == 'parallel' else radius), seam


def geometry(s):
    """Start/end, axis and detail weight for regular cells and tiled struts."""
    counts, steps = (s.nx, s.ny, s.nz), (s.sx, s.sy, s.sz)
    axes = [np.linspace(-n*d/2, n*d/2, n+1) for n, d in zip(counts, steps)]
    lines, labels, weights, samples = [], [], [], []
    def add(a, b, axis, weight=1., sample=32):
        lines.append((a, b)); labels.append(axis); weights.append(weight); samples.append(sample)
    # Surface grids have fine divisions; volumetric grids keep one line per cell.
    if s.structure in ('box', 'floor'):
        for normal in range(3):
            if s.structure == 'floor' and normal != 1:
                continue
            others = [i for i in range(3) if i != normal]
            faces = [axes[normal][-1]] if s.structure == 'floor' else [axes[normal][0], axes[normal][-1]]
            for face in faces:
                for along, across in (others, others[::-1]):
                    ticks = np.linspace(axes[across][0], axes[across][-1], counts[across]*s.divisions+1)
                    for index, tick in enumerate(ticks):
                        a, b = np.zeros(3), np.zeros(3)
                        a[normal] = b[normal] = face
                        a[across] = b[across] = tick
                        a[along], b[along] = axes[along][0], axes[along][-1]
                        add(a, b, along, 1. if index % s.divisions == 0 else .35)
        detail = s.divisions
    else:
        # Bound fine rings, not the requested cell count; dense struts remain
        # continuous. The panel reports when detail is automatically reduced.
        ring_cost = sum((counts[(i+1)%3]+1)*(counts[(i+2)%3]+1)*counts[i]*4 for i in range(3))
        detail = max(1, min(s.divisions, 12000//max(1, ring_cost)))
        for along in range(3):
            across = [i for i in range(3) if i != along]
            for u in axes[across[0]]:
                for v in axes[across[1]]:
                    a, b = np.zeros(3), np.zeros(3)
                    a[across] = b[across] = [u, v]
                    a[along], b[along] = axes[along][0], axes[along][-1]
                    if s.structure == 'lattice':
                        add(a, b, along)
                        continue
                    half_width = min(steps)*s.beam/2
                    offsets = []
                    for du, dv in ((-1, -1), (-1, 1), (1, 1), (1, -1)):
                        delta = np.zeros(3)
                        delta[across] = [du*half_width, dv*half_width]
                        offsets.append(delta)
                        add(a+delta, b+delta, along, .8)
                    for index, tick in enumerate(np.linspace(a[along], b[along], counts[along]*detail+1)):
                        center = a.copy(); center[along] = tick
                        for j in range(4):
                            add(center+offsets[j], center+offsets[(j+1)%4], along,
                                .75 if index % detail == 0 else .3, 2)
    return np.asarray(lines), np.asarray(labels), np.asarray(weights), np.asarray(samples), detail


def segment_domain_safe(pairs, s):
    """Do not join across the excluded rear cone of a >180-degree lens.

    Both endpoints can be visible while the segment crosses the back pole.
    A quadratic cone intersection detects this even away from its midpoint.
    """
    result = np.ones(pairs.shape[:-2], dtype=bool)
    if s.projection not in ('azimuthal', 'stereo') or s.effective_fov <= 180:
        return result
    a, d = pairs[..., 0, :], pairs[..., 1, :]-pairs[..., 0, :]
    k2 = math.tan(math.pi-math.radians(s.effective_fov)/2)**2
    quadratic = np.sum(d[..., :2]**2, axis=-1)-k2*d[..., 2]**2
    linear = 2*(np.sum(a[..., :2]*d[..., :2], axis=-1)-k2*a[..., 2]*d[..., 2])
    t = np.clip(-linear/(2*np.where(quadratic > 1e-12, quadratic, 1.)), 0, 1)
    closest = a+t[..., None]*d
    through_hole = (quadratic > 1e-12) & (closest[..., 2] < 0)
    through_hole &= np.sum(closest[..., :2]**2, axis=-1) < k2*closest[..., 2]**2
    return ~through_hole


def projected_segments(s, width, height):
    lines, axes, weights, samples, detail = geometry(s)
    output = []
    curved = s.projection not in ('linear', 'parallel', 'perceptual') or any((s.bend, s.twist, s.taper))
    for sample in np.unique(samples):
        mask = samples == sample
        selected = lines[mask]
        count = int(sample) if curved or s.projection == 'perceptual' else min(int(sample), 13) if s.fog > 0 else 2
        t = np.linspace(0, 1, count)
        points = selected[:, :1]+(selected[:, 1:]-selected[:, :1])*t[None, :, None]
        camera = camera_points(points, s)
        # Separate segment endpoints: a curve may enter and leave the near
        # plane around the same sample, requiring two different intersections.
        camera = np.stack((camera[:, :-1], camera[:, 1:]), axis=-2)
        if s.projection in ('linear', 'perceptual'):
            near = s.extent*.002
            z0, z1 = camera[..., 0, 2].copy(), camera[..., 1, 2].copy()
            crossing = (z0 < near) != (z1 < near)
            for endpoint, condition in ((0, z0 < near), (1, z1 < near)):
                hit = crossing & condition
                ratio = (near-z0[hit])/(z1[hit]-z0[hit])
                camera[hit, endpoint] = camera[hit, 0]+ratio[:, None]*(camera[hit, 1]-camera[hit, 0])
                camera[hit, endpoint, 2] = near
        uv, valid, depth, seam = project(camera, s, width, height)
        a, b = uv[..., 0, :], uv[..., 1, :]
        keep = valid[..., 0] & valid[..., 1]
        keep &= segment_domain_safe(camera, s)
        keep &= np.abs(seam[..., 1]-seam[..., 0]) < math.pi
        keep &= (np.maximum(a[..., 0], b[..., 0]) >= 0) & (np.minimum(a[..., 0], b[..., 0]) <= width)
        keep &= (np.maximum(a[..., 1], b[..., 1]) >= 0) & (np.minimum(a[..., 1], b[..., 1]) <= height)
        # Subpixel minor marks add noise and work without improving the guide.
        keep &= (weights[mask, None] >= .5) | (np.linalg.norm(b-a, axis=-1) >= 1.5)
        if sample == 2:
            keep &= np.linalg.norm(b-a, axis=-1) >= 2.
        # Keep QPainter's fixed-point rasterizer inside a safe numeric range.
        # Liang-Barsky clipping also retains segments crossing the entire sheet.
        aa, bb = a[keep], b[keep]
        delta = bb-aa
        lower, upper = np.zeros(len(aa)), np.ones(len(aa))
        good = np.ones(len(aa), dtype=bool)
        for coordinate, bound in ((0, width), (1, height)):
            d = delta[:, coordinate]
            parallel = np.abs(d) < 1e-12
            good &= ~parallel | ((aa[:, coordinate] >= 0) & (aa[:, coordinate] <= bound))
            safe = np.where(parallel, 1., d)
            t0, t1 = -aa[:, coordinate]/safe, (bound-aa[:, coordinate])/safe
            lower = np.maximum(lower, np.where(parallel, 0., np.minimum(t0, t1)))
            upper = np.minimum(upper, np.where(parallel, 1., np.maximum(t0, t1)))
        good &= upper >= lower
        clipped = np.stack((aa+lower[:, None]*delta, aa+upper[:, None]*delta), axis=1)[good]
        axis = np.broadcast_to(axes[mask, None], keep.shape)[keep][good]
        weight = np.broadcast_to(weights[mask, None], keep.shape)[keep][good]
        depths = depth.mean(axis=-1)[keep][good]
        output.append((clipped, axis, weight, depths))
    return tuple(np.concatenate([part[i] for part in output]) for i in range(4)), detail


class PerspectiveRenderer:
    """Bounded paper-space raster cache; ink updates do not invalidate guides."""
    def __init__(self):
        self.cache = OrderedDict()
        self.builds = 0
        self.last_detail = None
        self.preview_id = None

    def clear(self):
        self.cache.clear()

    def remember(self, key, image):
        # Retain one raster per sheet/view, under a byte budget. Fifteen small
        # previews should not evict each other as with a three-image LRU.
        for old in list(self.cache):
            if old[0] == key[0] and old[-1] == key[-1]:
                del self.cache[old]
        self.cache[key] = image
        total = sum(i.sizeInBytes() for i in self.cache.values())
        while total > 64*1024*1024 and len(self.cache) > 1:
            _, removed = self.cache.popitem(last=False)
            total -= removed.sizeInBytes()

    def image(self, paper, scale=1.):
        s = PerspectiveSettings.read(paper.perspective)
        if not s.enabled:
            return None
        if self.preview_id == paper.id and s.structure == 'beams' and max(s.nx, s.ny, s.nz) > 6:
            s = replace(s, structure='lattice')
        # Coarse resolution tiers, reusing any larger raster when zooming out.
        raster_scale = min(max(.25, math.ceil(scale*4)/4), 2., 2048/max(paper.width, paper.height))
        preview = self.preview_id == paper.id and s.structure == 'crystal'
        if preview:
            raster_scale = min(raster_scale, 640/max(paper.width,paper.height))
        key = (paper.id, s, paper.width, paper.height, paper.paper_style, preview)
        if key in self.cache:
            cached = self.cache[key]
            if cached.width() >= round(paper.width*raster_scale):
                self.cache.move_to_end(key)
                return cached
        if s.structure == 'crystal':
            from .crystal import render_crystal
            image = render_crystal(s, max(1,round(paper.width*raster_scale)),
                max(1,round(paper.height*raster_scale)), paper.paper_style, preview)
            self.remember(key, image)
            self.builds += 1
            self.last_detail = s.crystal_divisions
            return image
        (segments, axes, weights, depth), detail = projected_segments(s, paper.width, paper.height)
        image = QImage(max(1, round(paper.width*raster_scale)), max(1, round(paper.height*raster_scale)), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(raster_scale, raster_scale)
        if len(segments):
            near, far = np.min(depth), np.max(depth)
            levels = np.clip(((depth-near)/max(1e-6, far-near)*11).astype(int), 0, 11)
            dark = paper.paper_style != 'ivory'
            palettes = (('#e6a38b', '#9bcab4', '#91b9e1') if dark else
                        ('#bd6f55', '#558a79', '#597fa7')) if s.axis_colors else (('#a2cbd4' if dark else '#618d9b'),)*3
            for level in range(11, -1, -1):
                fade = (1-s.fog)+s.fog*math.exp(-3.2*level/11)
                for axis in range(3):
                    for minor in (True, False):
                        indices = np.flatnonzero((levels == level) & (axes == axis) & ((weights < .5) == minor))
                        if not len(indices):
                            continue
                        path = QPainterPath()
                        for a, b in segments[indices]:
                            path.moveTo(float(a[0]), float(a[1])); path.lineTo(float(b[0]), float(b[1]))
                        color = QColor(palettes[axis])
                        color.setAlphaF(s.opacity*fade*(.38 if minor else 1.))
                        pen = QPen(color, s.line_width*(.7 if minor else 1.)/raster_scale)
                        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                        painter.setPen(pen)
                        painter.drawPath(path)
        painter.end()
        self.remember(key, image)
        self.builds += 1
        self.last_detail = detail
        return image

    def draw(self, painter, paper, above):
        settings = PerspectiveSettings.read(paper.perspective)
        if not settings.enabled or settings.on_top != above:
            return
        transform = painter.deviceTransform()
        scale = math.hypot(transform.m11(), transform.m12())
        image = self.image(paper, scale)
        if image is not None:
            painter.save()
            painter.setOpacity(1.)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(QRectF(0, 0, paper.width, paper.height), image)
            painter.restore()

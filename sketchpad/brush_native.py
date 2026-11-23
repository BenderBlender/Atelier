"""Optional compiled CPU rasterizer. No GPU uploads or changes to dab spacing.

The NumPy implementation remains available if compilation is unavailable.
Strict floating point (no fastmath) keeps the pencil and paper contact model.
"""
import math
import os
import numpy as np

ERROR = ''
try:
    if os.environ.get('ATELIER_BRUSH_BACKEND') == 'numpy':
        raise ImportError('NumPy requested')
    from numba import njit
except Exception as exc:
    ERROR = str(exc)
    njit = None


def _raster(rgba, tooth, x0, y0, x, y, tip, length, width, co, si,
            pressure, grain, opacity, flow, color, mode, strength, original, work):
    slope = (width-tip)/max(length, 1e-6)
    side_norm = math.sqrt(1+slope*slope)
    extent = max(tip, width)+.5*side_norm
    load = 2.6*strength*pressure**1.15*max(0., flow)
    for row in range(rgba.shape[0]):
        dy = y0+row+.5-y
        # Intersect an oriented bounding rectangle with this scanline. For a
        # long diagonal nib, most of its axis-aligned rectangle is empty.
        lo, hi = 0., float(rgba.shape[1]-1)
        for a, b, lower, upper in ((co, (x0+.5-x)*co+dy*si, -tip-.5, length+width+.5),
                                    (-si, dy*co-(x0+.5-x)*si, -extent, extent)):
            if abs(a) < 1e-12:
                if b < lower or b > upper:
                    hi = -1.
            else:
                t0, t1 = (lower-b)/a, (upper-b)/a
                lo = max(lo, min(t0, t1))
                hi = min(hi, max(t0, t1))
        for col in range(max(0, math.ceil(lo)), min(rgba.shape[1], math.floor(hi)+1)):
            dx = x0+col+.5-x
            u, v = dx*co+dy*si, dy*co-dx*si
            local_pressure = pressure
            if mode == 1:
                r2 = (dx*dx+dy*dy)/(tip*tip)
                edge = max(0., min(1., (1-r2)*1.65))**1.4
            elif length < 1e-6:
                radius = math.hypot(u, v)
                edge = max(0., min(1., tip+.5-radius))
                edge *= .56+.44*max(0., min(1., 1-(radius/tip)**2))
            else:
                along = max(0., min(1., u/length))
                half_width = tip+(width-tip)*along
                if u < 0:
                    radius = math.hypot(u, v)
                    distance, radial = tip-radius, radius/tip
                elif u > length:
                    radius = math.hypot(u-length, v)
                    distance, radial = width-radius, radius/width
                else:
                    distance = (half_width-abs(v))/side_norm
                    radial = abs(v)/half_width
                edge = max(0., min(1., distance+.5))
                if edge == 0.:
                    continue
                flank = max(0., min(1., u/(length+width)))
                profile = .56+.44*max(0., min(1., 1-radial*radial))
                edge *= profile*(1-.58*flank)*(tip/half_width)**.85
                local_pressure *= 1-.42*flank
            if edge == 0.:
                continue
            height = float(tooth[(y0+row) % 512, (x0+col) % 512])
            contact = max(0., min(1., (local_pressure*.74+.34-height)*3.4))
            fibers = (1-grain)+grain*contact
            if mode:
                if mode == 1:
                    fibers = .22+.78*(1-height)**1.3
                pickup = 1-math.exp(-edge*fibers*load)
                retention = (.02+.18*height**2.2)*(1-.5*pressure)
                alpha = work[row, col]
                alpha -= max(alpha-original[row, col]*retention, 0.)*pickup
                work[row, col] = alpha
                rgba[row, col, 3] = round(float(work[row, col])*255)
            else:
                alpha = max(0., min(.92, edge*fibers*opacity*(.055+.19*pressure)*flow))
                # The reference stores old alpha as float32 before blending.
                old_a = float(np.float32(rgba[row, col, 3])/np.float32(255.))
                remainder = old_a*(1-alpha)
                out_a = alpha+remainder
                safe = max(out_a, 1e-8)
                for channel in range(3):
                    rgba[row, col, channel] = min(255, max(0, round(
                        (color[channel]*alpha+rgba[row, col, channel]*remainder)/safe)))
                rgba[row, col, 3] = round(out_a*255)


raster = njit(cache=True, nogil=True)(_raster) if njit else None
EMPTY = np.zeros((1, 1), dtype=np.float32)


def warmup():
    """Compile before accepting pen input; safely fall back on unsupported hosts."""
    global raster, ERROR
    if raster is None:
        return False
    try:
        rgba = np.zeros((4, 4, 4), dtype=np.uint8)
        tooth = np.zeros((512, 512), dtype=np.float32)
        tooth.setflags(write=False)
        # Sliced images and undo tiles have arbitrary row stride.
        raster(rgba[:2, :2], tooth, 0, 0, 1., 1., 1., 2., 1., 1., 0.,
               .5, .6, .7, 1., (45., 43., 40.), 0, .65, EMPTY, EMPTY)
        tile = np.zeros((4, 4), dtype=np.float32)[:2, :2]
        raster(rgba[:2, :2], tooth, 0, 0, 1., 1., 1., 2., 1., 1., 0.,
               .5, .6, .7, 1., (45., 43., 40.), 2, .65, tile, tile)
        return True
    except Exception as exc:
        ERROR = str(exc)
        raster = None
        return False

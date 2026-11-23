"""Depth-tested surface grids. Only the nearest surface contributes ink.

An unmarked face is transparent in the final overlay but still occludes other
guide faces. The user's drawing is never used as a depth or colour buffer.
"""
import math
import numpy as np
try:
    from numba import njit
except ImportError:
    njit = None


def _raster(triangles, attributes, labels, depth, coverage, normals, width, hidden=False):
    h, w = depth.shape
    for index in range(len(triangles)):
        p = triangles[index]
        x0, y0 = p[0, 0], p[0, 1]
        x1, y1 = p[1, 0], p[1, 1]
        x2, y2 = p[2, 0], p[2, 1]
        determinant = (y1-y2)*(x0-x2)+(x2-x1)*(y0-y2)
        if abs(determinant) < 1e-8:
            continue
        left = max(0, int(math.floor(min(x0, x1, x2))))
        right = min(w-1, int(math.ceil(max(x0, x1, x2))))
        top = max(0, int(math.floor(min(y0, y1, y2))))
        bottom = min(h-1, int(math.ceil(max(y0, y1, y2))))
        ax, ay = (y1-y2)/determinant, (x2-x1)/determinant
        bx, by = (y2-y0)/determinant, (x0-x2)/determinant
        # Attributes are (inverse depth, u/depth, v/depth). Affine projections
        # use 1, u, v and carry the actual depth separately in column 3.
        q = attributes[index]
        dx = ax*(q[0]-q[2])+bx*(q[1]-q[2])
        dy = ay*(q[0]-q[2])+by*(q[1]-q[2])
        for y in range(top, bottom+1):
            for x in range(left, right+1):
                a = ax*(x+.5-x2)+ay*(y+.5-y2)
                b = bx*(x+.5-x2)+by*(y+.5-y2)
                c = 1-a-b
                if min(a, b, c) < -1e-7:
                    continue
                inv = a*q[0,0]+b*q[1,0]+c*q[2,0]
                if inv <= 0:
                    continue
                z = (a*q[0,3]+b*q[1,3]+c*q[2,3])/inv
                if (hidden and (not math.isfinite(depth[y, x]) or z <= depth[y, x]+1e-4)) or (not hidden and z >= depth[y, x]):
                    continue
                u = (a*q[0,1]+b*q[1,1]+c*q[2,1])/inv
                v = (a*q[0,2]+b*q[1,2]+c*q[2,2])/inv
                du = math.hypot(dx[1]-u*dx[0], dy[1]-u*dy[0])/inv
                dv = math.hypot(dx[2]-v*dx[0], dy[2]-v*dy[0])/inv
                distance = min(abs(u-round(u))/max(1e-10, du),
                               abs(v-round(v))/max(1e-10, dv))
                # Suppress subpixel surface tiles rather than aliasing them
                # into solid dark moire. Silhouette edges remain visible.
                resolved = min(1., 1./max(du, dv, 1e-10))
                cov = max(0., min(1., width*.5+.5-distance))*resolved
                if hidden:
                    coverage[y, x] = max(coverage[y, x], cov)
                else:
                    coverage[y, x] = cov
                    depth[y, x] = z
                    normals[y, x] = labels[index]


def _raster_numpy(triangles, attributes, labels, depth, coverage, normals, width, hidden=False):
    """Portable fallback; vectorise each triangle's box instead of pixels."""
    h, w = depth.shape
    for p, q, label in zip(triangles, attributes, labels):
        left, top = np.maximum(0, np.floor(p.min(axis=0)).astype(int))
        right, bottom = np.minimum([w-1, h-1], np.ceil(p.max(axis=0)).astype(int))
        if left > right or top > bottom:
            continue
        x0,y0 = p[0]; x1,y1 = p[1]; x2,y2 = p[2]
        det = (y1-y2)*(x0-x2)+(x2-x1)*(y0-y2)
        if abs(det) < 1e-8:
            continue
        ax,ay,bx,by = (y1-y2)/det,(x2-x1)/det,(y2-y0)/det,(x0-x2)/det
        yy,xx = np.mgrid[top:bottom+1,left:right+1]
        a = ax*(xx+.5-x2)+ay*(yy+.5-y2)
        b = bx*(xx+.5-x2)+by*(yy+.5-y2)
        c = 1-a-b
        value = a[...,None]*q[0]+b[...,None]*q[1]+c[...,None]*q[2]
        inv = np.maximum(1e-15, value[...,0])
        z = value[...,3]/inv
        region = np.s_[top:bottom+1,left:right+1]
        visible = (np.isfinite(depth[region]) & (z > depth[region]+1e-4)) if hidden else (z < depth[region])
        keep = (np.minimum(np.minimum(a,b),c) >= -1e-7) & visible
        dx = ax*(q[0]-q[2])+bx*(q[1]-q[2])
        dy = ay*(q[0]-q[2])+by*(q[1]-q[2])
        u,v = value[...,1]/inv, value[...,2]/inv
        du = np.hypot(dx[1]-u*dx[0],dy[1]-u*dy[0])/inv
        dv = np.hypot(dx[2]-v*dx[0],dy[2]-v*dy[0])/inv
        dist = np.minimum(abs(u-np.rint(u))/np.maximum(du,1e-10),abs(v-np.rint(v))/np.maximum(dv,1e-10))
        cov = np.clip(width*.5+.5-dist,0,1)*np.minimum(1.,1/np.maximum(np.maximum(du,dv),1e-10))
        if hidden:
            coverage[region][keep] = np.maximum(coverage[region][keep], cov[keep])
        else:
            depth[region][keep] = z[keep]
            coverage[region][keep] = cov[keep]
            normals[region][keep] = label


_compiled = njit(cache=True, nogil=True)(_raster) if njit else None
ERROR = ''


def raster(*args):
    global _compiled, ERROR
    if _compiled is not None:
        try:
            return _compiled(*args)
        except Exception as exc:
            ERROR = str(exc)
            _compiled = None
    return _raster_numpy(*args)


def warmup():
    args = (np.zeros((0,3,2)), np.zeros((0,3,4)), np.zeros(0,dtype=np.int8),
            np.full((1,1),np.inf,dtype=np.float32), np.zeros((1,1),dtype=np.float32),
            np.full((1,1),-1,dtype=np.int8), 1.)
    raster(*args)
    raster(*args, True)

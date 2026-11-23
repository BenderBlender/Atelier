"""Solid cubic space division inspired by Escher and Chelsea's drawing grids.

The union has cubic junctions and bars one small cube thick. Shared internal
faces are removed. A depth buffer hides rear faces AND obstructed front faces;
this is deliberately different from merely culling back-facing wire edges.
"""
from functools import lru_cache
import math
import numpy as np
from PySide6.QtGui import QImage, QColor
from .perspective import camera_points, project, segment_domain_safe, rotation
from .crystal_native import raster


@lru_cache(maxsize=4)
def solid_faces(counts, steps, divisions):
    """Exposed union boundary, with texture coordinates measured in cubes."""
    coordinates = []
    for count, step in zip(counts, steps):
        centers = np.arange(count+1)*step-count*step/2
        half = step/(2*divisions)
        coordinates.append(np.column_stack((centers-half,centers+half)).ravel())
    shape = tuple(2*n+1 for n in counts)
    indices = np.indices(shape)
    filled = np.sum(indices % 2 == 0,axis=0) >= 2
    quads, texcoords, normals = [], [], []
    for axis in range(3):
        u,v = (axis+1)%3,(axis+2)%3
        for sign in (-1,1):
            neighbor = np.roll(filled,-sign,axis=axis)
            boundary = [slice(None)]*3; boundary[axis] = -1 if sign>0 else 0
            neighbor[tuple(boundary)] = False
            cells = np.argwhere(filled & ~neighbor)
            points = np.zeros((len(cells),4,3))
            points[:,:,axis] = coordinates[axis][cells[:,axis]+(sign>0),None]
            for k,(du,dv) in enumerate(((0,0),(1,0),(1,1),(0,1))):
                points[:,k,u] = coordinates[u][cells[:,u]+du]
                points[:,k,v] = coordinates[v][cells[:,v]+dv]
            uv = points[:,:,[u,v]].copy()
            for j,a in enumerate((u,v)):
                uv[:,:,j] = (uv[:,:,j]+counts[a]*steps[a]/2)/(steps[a]/divisions)+.5
            quads.append(points);texcoords.append(uv)
            normals.append(np.full(len(cells),axis*2+(sign>0),dtype=np.int8))
    return np.concatenate(quads),np.concatenate(texcoords),np.concatenate(normals)


@lru_cache(maxsize=2)
def surface_mesh(counts,steps,divisions,tess):
    quads,uv,labels = solid_faces(counts,steps,divisions)
    # Measure subdivision relative to each axis, not the shortest axis:
    # stretching a cell must not multiply tessellation by its aspect ratio.
    spans = np.stack((np.linalg.norm(uv[:,1]-uv[:,0],axis=1),
                      np.linalg.norm(uv[:,3]-uv[:,0],axis=1)),axis=1)/divisions
    lengths = np.maximum(1,np.ceil(spans*tess-1e-8).astype(int))
    points_out,uv_out,labels_out = [],[],[]
    for nu,nv in np.unique(lengths,axis=0):
        select = (lengths[:,0]==nu)&(lengths[:,1]==nv)
        q,t = quads[select],uv[select]
        ij = np.indices((nu,nv)).reshape(2,-1).T
        corners = ij[:,None,:]+np.array([[0,0],[1,0],[1,1],[0,1]])[None,:,:]
        fractions = corners/np.array([nu,nv])
        positions = q[:,None,:1]+fractions[None,:,:,0,None]*(q[:,None,1:2]-q[:,None,:1])+fractions[None,:,:,1,None]*(q[:,None,3:4]-q[:,None,:1])
        texture = t[:,None,:1]+fractions[None,:,:,0,None]*(t[:,None,1:2]-t[:,None,:1])+fractions[None,:,:,1,None]*(t[:,None,3:4]-t[:,None,:1])
        split = np.array([[0,1,2],[0,2,3]])
        points_out.append(positions[:,:,split,:].reshape(-1,3,3))
        uv_out.append(texture[:,:,split,:].reshape(-1,3,2))
        labels_out.append(np.repeat(labels[select],nu*nv*2))
    return np.concatenate(points_out),np.concatenate(uv_out),np.concatenate(labels_out)


def camera_inside_solid(s):
    q=np.array([s.camera_x*s.extent,s.camera_y*s.extent,-s.distance*s.extent])@rotation(s.yaw,s.pitch,s.roll)
    if abs(q[2]) > s.nz*s.sz/2+s.sz/(2*s.crystal_divisions):
        return False
    z=q[2]/max(.1,s.nz*s.sz/2)
    if abs(1+s.taper*z)<1e-8:
        return True
    q[0]-=(s.bend*z*z+s.shear*z)*s.extent/2
    a=-np.radians(s.twist)*z
    q[:2]=np.array([q[0]*np.cos(a)-q[1]*np.sin(a),q[0]*np.sin(a)+q[1]*np.cos(a)])/(1+s.taper*z)
    nodes=0
    for position,count,step in zip(q,(s.nx,s.ny,s.nz),(s.sx,s.sy,s.sz)):
        tick=position/step+count/2
        half=.5/s.crystal_divisions
        if tick < -half or tick > count+half:
            return False
        nodes += abs(tick-round(tick))<=half
    return nodes>=2


def surface_triangles(s, preview=False, include_hidden=False):
    counts,steps=(s.nx,s.ny,s.nz),(s.sx,s.sy,s.sz)
    curved=s.projection not in ('linear','parallel') or any((s.bend,s.twist,s.taper))
    tess=1 if not curved else (2 if preview else 6 if max(counts)<=6 else 4)
    points,uv,labels=surface_mesh(counts,steps,s.crystal_divisions,tess)
    camera=camera_points(points,s)
    unfolded=abs(s.taper)*(1+1/(s.nz*s.crystal_divisions))<.99
    if not include_hidden and unfolded and (s.projection=='parallel' or not camera_inside_solid(s)):
        normals=np.cross(camera[:,1]-camera[:,0],camera[:,2]-camera[:,0])
        facing=normals[:,2] if s.projection=='parallel' else np.einsum('ij,ij->i',normals,camera.mean(axis=1))
        keep=facing*(2*(labels%2)-1)<1e-10
        camera,uv,labels=camera[keep],uv[keep],labels[keep]
    return camera,uv,labels


def clip_near(camera,uv,labels,near):
    valid = camera[:,:,2] >= near
    whole,partial = valid.all(axis=1),valid.any(axis=1)&~valid.all(axis=1)
    points,textures,tags = [camera[whole]],[uv[whole]],[labels[whole]]
    extra_p,extra_t,extra_l = [],[],[]
    for p,t,label in zip(camera[partial],uv[partial],labels[partial]):
        polygon,texture = [],[]
        for i in range(3):
            a,b = p[i-1],p[i]; ta,tb = t[i-1],t[i]
            if (a[2]>=near)!=(b[2]>=near):
                fraction = (near-a[2])/(b[2]-a[2])
                hit = a+fraction*(b-a);hit[2]=near
                polygon.append(hit);texture.append(ta+fraction*(tb-ta))
            if b[2]>=near:
                polygon.append(b);texture.append(tb)
        for i in range(1,len(polygon)-1):
            extra_p.append([polygon[0],polygon[i],polygon[i+1]])
            extra_t.append([texture[0],texture[i],texture[i+1]])
            extra_l.append(label)
    if extra_p:
        points.append(np.array(extra_p));textures.append(np.array(extra_t));tags.append(np.array(extra_l,dtype=np.int8))
    return np.concatenate(points),np.concatenate(textures),np.concatenate(tags)


def triangle_domain_safe(camera,s):
    safe=segment_domain_safe(camera[:,[[0,1],[1,2],[2,0]],:],s).all(axis=1)
    if s.projection in ('azimuthal','stereo') and s.effective_fov>180:
        # A triangle can surround the excluded rear pole even when none of
        # its three edges crosses the excluded cone. Refine that interior too.
        a,b,c=camera[:,0],camera[:,1],camera[:,2]
        ab,ac=b-a,c-a
        determinant=ab[:,0]*ac[:,1]-ab[:,1]*ac[:,0]
        divisor=np.where(abs(determinant)>1e-12,determinant,1.)
        u=(-a[:,0]*ac[:,1]+a[:,1]*ac[:,0])/divisor
        v=(-ab[:,0]*a[:,1]+ab[:,1]*a[:,0])/divisor
        z=a[:,2]+u*ab[:,2]+v*ac[:,2]
        contains=(abs(determinant)>1e-12)&(u>=0)&(v>=0)&(u+v<=1)&(z<0)
        safe &= ~contains
    return safe


def projected_faces(s,width,height,preview=False,include_hidden=False):
    camera,texture,labels = surface_triangles(s,preview,include_hidden)
    if s.projection in ('linear','perceptual'):
        camera,texture,labels = clip_near(camera,texture,labels,s.extent*.002)
    curved = s.projection not in ('linear','parallel')
    results = []
    # Refine lens boundaries, never connect across
    # a cylindrical seam or the excluded back pole of a wide fisheye.
    for level in range(7):
        screen,valid,_,seam = project(camera,s,width,height)
        pairs = camera[:,[[0,1],[1,2],[2,0]],:]
        safe = triangle_domain_safe(camera,s)
        safe &= np.ptp(seam,axis=1) < math.pi
        keep = valid.all(axis=1)&safe
        refine = np.zeros(len(camera),dtype=bool)
        if curved:
            mids = pairs.mean(axis=2)
            mid_screen,mid_valid,_,_ = project(mids,s,width,height)
            # Interior patches use a conforming subdivision: independently
            # refining their triangles would open cracks along curved edges.
            refine = ~keep
            # Ignore patches entirely outside the lens and viewport. Midpoint
            # checks retain faces that enter the lens between their vertices.
            visible = valid.any(axis=1)|mid_valid.any(axis=1)
            bounds = np.concatenate((screen,mid_screen),axis=1)
        else:
            visible = keep
            bounds = screen
        visible &= (bounds[:,:,0].max(axis=1)>=0)&(bounds[:,:,0].min(axis=1)<=width)
        visible &= (bounds[:,:,1].max(axis=1)>=0)&(bounds[:,:,1].min(axis=1)<=height)
        refine &= visible
        if level == 6 or len(camera)>180000:
            refine[:] = False
        accepted = keep&visible&~refine
        if np.any(accepted):
            p = camera[accepted]
            if s.projection == 'parallel':
                inv = np.ones(p.shape[:2]);depth = p[:,:,2]
            else:
                depth = p[:,:,2] if s.projection=='linear' else np.linalg.norm(p,axis=2)
                inv = 1/np.maximum(depth,1e-8)
            attrs = np.concatenate((inv[:,:,None],texture[accepted]*inv[:,:,None],(depth*inv)[:,:,None]),axis=2)
            results.append((screen[accepted],attrs,labels[accepted]))
        if not refine.any():
            break
        c,t,l = camera[refine],texture[refine],labels[refine]
        cm,tm = (c+np.roll(c,-1,axis=1))/2,(t+np.roll(t,-1,axis=1))/2
        lookup = np.array([[0,3,5],[3,1,4],[5,4,2],[3,4,5]])
        camera = np.concatenate((c,cm),axis=1)[:,lookup].reshape(-1,3,3)
        texture = np.concatenate((t,tm),axis=1)[:,lookup].reshape(-1,3,2)
        labels = np.repeat(l,4)
    if not results:
        return np.zeros((0,3,2)),np.zeros((0,3,4)),np.zeros(0,dtype=np.int8)
    return tuple(np.ascontiguousarray(np.concatenate([part[i] for part in results])) for i in range(3))


def render_crystal(s,width,height,paper_style,preview=False,return_buffers=False):
    triangles,attrs,labels = projected_faces(s,width,height,preview)
    depth = np.full((height,width),np.inf,dtype=np.float32)
    coverage = np.zeros_like(depth)
    normals = np.full((height,width),-1,dtype=np.int8)
    raster(triangles,attrs,labels,depth,coverage,normals,s.line_width)
    hit = normals>=0
    # Contours of the union and discontinuities between surfaces. Shared
    # coplanar mesh triangles never acquire a diagonal or a construction seam.
    padded_n = np.pad(normals,1,constant_values=-1)
    padded_d = np.pad(depth,1,constant_values=np.inf)
    edge = np.zeros_like(hit)
    for dy,dx in ((-1,0),(1,0),(0,-1),(0,1)):
        neighbor_n = padded_n[1+dy:1+dy+height,1+dx:1+dx+width]
        neighbor_d = padded_d[1+dy:1+dy+height,1+dx:1+dx+width]
        edge |= hit&((neighbor_n!=normals)|(neighbor_d>depth+max(s.sx,s.sy,s.sz)*.3))
    coverage[edge] = np.maximum(coverage[edge],.85)
    fade = np.zeros_like(depth)
    if hit.any():
        near,far = depth[hit].min(),depth[hit].max()
        fade[hit] = (1-s.fog)+s.fog*np.exp(-3.2*(depth[hit]-near)/max(1e-6,far-near))
    dark = paper_style != 'ivory'
    palette = (('#e6a38b','#9bcab4','#91b9e1') if dark else ('#bd6f55','#558a79','#597fa7')) if s.axis_colors else (('#a2cbd4' if dark else '#618d9b'),)*3
    colors = np.array([[QColor(c).red(),QColor(c).green(),QColor(c).blue()] for c in palette],dtype=np.uint8)
    rgba = np.zeros((height,width,4),dtype=np.uint8)
    rgba[:,:,:3] = colors[np.maximum(normals,0)//2]
    shade = np.array([.8,.65,1.,.45,.6,.75])[np.maximum(normals,0)]
    rgba[:,:,3] = np.rint(255*s.opacity*fade*np.maximum(coverage,s.face_tone*shade)).astype(np.uint8)
    if s.hidden_opacity > 0:
        # X-ray lines only behind the nearest surface. Max coverage keeps
        # dense overlapping rear faces from becoming an opaque dark patch.
        hidden = np.zeros_like(depth)
        back = projected_faces(s,width,height,preview,include_hidden=True)
        raster(*back,depth,hidden,normals,s.line_width,True)
        rear_alpha = s.opacity*s.hidden_opacity*(1-.7*s.fog)*hidden
        front_alpha = rgba[:,:,3].astype(np.float32)/255
        rgba[:,:,3] = np.rint(255*(front_alpha+rear_alpha*(1-front_alpha))).astype(np.uint8)
    result = QImage(rgba.data,width,height,rgba.strides[0],QImage.Format.Format_RGBA8888).copy()
    return (result,depth,normals,coverage) if return_buffers else result

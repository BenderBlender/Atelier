from dataclasses import replace
from collections import Counter
import unittest
import numpy as np
from PySide6.QtWidgets import QApplication
from sketchpad.perspective import PerspectiveSettings, preset, rotation, PerspectiveRenderer
from sketchpad.crystal import solid_faces, surface_triangles, render_crystal, projected_faces, triangle_domain_safe, camera_inside_solid
from sketchpad.crystal_native import raster, _raster_numpy
from sketchpad.model import Paper

APP = QApplication.instance() or QApplication([])


def ray_depth(s,width,height,pixels):
    """Independent first ray/box intersection against the union of solid bars."""
    pixels = np.asarray(pixels,float)+.5
    field = min(width,height)*.44*s.scale
    uv = (pixels-[width*(.5+s.offset_x),height*(.5+s.offset_y)])/field
    half = np.radians(s.effective_fov)/2
    if s.projection=='linear':
        dirs = np.column_stack((uv*np.tan(half),np.ones(len(uv))))
    elif s.projection=='cylinder':
        angle=np.radians(s.cylinder_axis);c=np.cos(angle);sn=np.sin(angle)
        u,v=uv[:,0]*c+uv[:,1]*sn,uv[:,1]*c-uv[:,0]*sn
        xx,yy,z=np.sin(u*half),v*half,np.cos(u*half)
        dirs=np.column_stack((xx*c-yy*sn,yy*c+xx*sn,z))
    else:
        r=np.linalg.norm(uv,axis=1)
        theta=np.arcsin(np.clip(r*np.sin(half),0,1))
        dirs=np.column_stack((uv*np.sin(theta)[:,None]/np.maximum(r[:,None],1e-12),np.cos(theta)))
    rotate=rotation(s.yaw,s.pitch,s.roll)
    origin=np.array([s.camera_x*s.extent,s.camera_y*s.extent,-s.distance*s.extent])@rotate
    world=dirs@rotate
    counts=(s.nx,s.ny,s.nz);steps=(s.sx,s.sy,s.sz)
    nearest=np.full(len(uv),np.inf)
    for along in range(3):
        across=[i for i in range(3) if i!=along]
        for a in range(counts[across[0]]+1):
            for b in range(counts[across[1]]+1):
                lo=np.zeros(3);hi=np.zeros(3)
                lo[along]=-counts[along]*steps[along]/2-steps[along]/(2*s.crystal_divisions)
                hi[along]=-lo[along]
                for axis,index in zip(across,(a,b)):
                    center=(index-counts[axis]/2)*steps[axis]
                    lo[axis]=center-steps[axis]/(2*s.crystal_divisions)
                    hi[axis]=center+steps[axis]/(2*s.crystal_divisions)
                safe=np.where(abs(world)<1e-14,1e-14,world)
                t0,t1=(lo-origin)/safe,(hi-origin)/safe
                enter=np.maximum(np.minimum(t0,t1).max(axis=1),0)
                leave=np.maximum(t0,t1).min(axis=1)
                nearest=np.minimum(nearest,np.where(leave>=enter,enter,np.inf))
    return nearest if s.projection=='linear' else nearest*np.linalg.norm(dirs,axis=1)


class CrystalTests(unittest.TestCase):
    def test_triangle_cannot_fill_excluded_rear_pole(self):
        camera=np.array([[[.3,0.,-1.],[-.15,.26,-1.],[-.15,-.26,-1.]],
                         [[.3,0.,1.],[-.15,.26,1.],[-.15,-.26,1.]]])
        for mode in ('azimuthal','stereo'):
            s=PerspectiveSettings(projection=mode,fov=350)
            np.testing.assert_array_equal(triangle_domain_safe(camera,s),[False,True])

    def test_camera_inside_node_retains_exit_faces(self):
        s=PerspectiveSettings(structure='crystal',nx=2,ny=2,nz=2,distance=0.,yaw=0.,pitch=0.)
        self.assertTrue(camera_inside_solid(s))
        _,depth,normals,_=render_crystal(s,120,100,'ivory',return_buffers=True)
        self.assertGreater(np.count_nonzero(normals>=0),10000)
        self.assertTrue(np.isfinite(depth).all())

    def test_union_is_closed_without_internal_shared_faces(self):
        quads,uv,labels=solid_faces((2,2,2),(1.,1.,1.),5)
        faces=Counter(tuple(sorted(tuple(x) for x in np.round(q,7))) for q in quads)
        self.assertTrue(all(n==1 for n in faces.values()))
        edges=Counter()
        for q in np.round(quads,7):
            for a,b in zip(q,np.roll(q,-1,axis=0)):
                edges[tuple(sorted((tuple(a),tuple(b))))]+=1
        self.assertTrue(all(n==2 for n in edges.values()))
        self.assertEqual(set(labels),set(range(6)))
        np.testing.assert_allclose(uv,np.rint(uv),atol=1e-12)

    def test_nearest_surface_hides_lines_even_without_face_fill(self):
        triangles=np.array([[[0.,0.],[8.,0.],[0.,8.]]]*2)
        # Near surface has no mark. Far surface has a line covering its face.
        attrs=np.array([[[1.,.37,.37,1.]]*3,[[.5,0.,0.,1.]]*3])
        for order in ([0,1],[1,0]):
            d=np.full((8,8),np.inf,dtype=np.float32);c=np.zeros_like(d);n=np.full((8,8),-1,dtype=np.int8)
            raster(triangles[order],attrs[order],np.array([0,4],dtype=np.int8)[order],d,c,n,1.)
            self.assertEqual(d[2,2],1.)
            self.assertEqual(c[2,2],0.)
            self.assertEqual(n[2,2],0)

    def test_raster_fallback_matches_compiled_depth_and_coverage(self):
        s=PerspectiveSettings.read(preset('Кристалл · снаружи'))
        triangles,attrs,labels=projected_faces(replace(s,nx=1,ny=1,nz=1),80,60)
        results=[]
        for kernel in (raster,_raster_numpy):
            d=np.full((60,80),np.inf,dtype=np.float32);c=np.zeros_like(d);n=np.full((60,80),-1,dtype=np.int8)
            kernel(triangles,attrs,labels,d,c,n,1.)
            results.append((d,c,n))
        np.testing.assert_allclose(results[0][0],results[1][0],atol=1e-6)
        np.testing.assert_allclose(results[0][1],results[1][1],atol=1e-6)
        np.testing.assert_array_equal(results[0][2],results[1][2])

    def test_depth_matches_independent_solid_ray_intersections(self):
        pixels=np.array([(x,y) for y in range(20,230,13) for x in range(30,290,17)])
        s=PerspectiveSettings.read(preset('Кристалл Эшера'))
        for mode,fov in [('linear',100),('cylinder',250),('fish',180)]:
            setting=replace(s,projection=mode,fov=fov,face_tone=0.)
            _,depth,_,_=render_crystal(setting,320,250,'ivory',return_buffers=True)
            expected=ray_depth(setting,320,250,pixels)
            measured=depth[pixels[:,1],pixels[:,0]]
            # Outside the fisheye disk there is no image domain.
            field=250*.44
            domain=np.linalg.norm((pixels+.5-[160,125])/field,axis=1)<=.97 if mode=='fish' else np.ones(len(pixels),bool)
            if mode=='cylinder':
                domain &= abs((pixels[:,0]+.5-160)/field)<=1.
            compare=np.isfinite(expected)&domain
            good=np.isfinite(measured[compare])&(abs(measured[compare]-expected[compare])<.025*expected[compare]+.005)
            self.assertGreater(good.mean(),.97,(mode,good.mean()))

    def test_density_and_stretch_do_not_explode_surface_work(self):
        s=PerspectiveSettings(structure='crystal',nx=12,ny=12,nz=12,
            sx=.2,sy=4.,sz=4.,projection='cylinder',crystal_divisions=3)
        camera,uv,labels=surface_triangles(s)
        self.assertLess(len(camera),600000)
        self.assertTrue(np.isfinite(camera).all())
        self.assertEqual(len(camera),len(labels))

    def test_all_lenses_and_deformation_produce_valid_images(self):
        s=PerspectiveSettings.read(preset('Кристалл Эшера'))
        for mode in ('linear','parallel','cylinder','fish','azimuthal','stereo','perceptual'):
            setting=replace(s,nx=3,ny=3,nz=3,projection=mode,fov=330,
                            twist=25,bend=.1,shear=.1,taper=.15)
            image,depth,normals,_=render_crystal(setting,200,160,'ivory',True,True)
            self.assertFalse(image.isNull())
            self.assertFalse(np.isnan(depth).any())
            self.assertGreater(np.count_nonzero(normals>=0),100)

    def test_cache_and_legacy_guide_are_independent(self):
        paper=Paper(width=400,height=300,perspective=preset('Кристалл Эшера'))
        renderer=PerspectiveRenderer()
        image=renderer.image(paper)
        self.assertIs(renderer.image(paper),image)
        self.assertEqual(renderer.builds,1)
        renderer.preview_id=paper.id
        self.assertIsNot(renderer.image(paper),image)
        renderer.preview_id=None
        self.assertIs(renderer.image(paper),image)
        paper.perspective=preset('Решётка Chelsea')
        self.assertEqual(paper.perspective['structure'],'beams')
        self.assertIsNot(renderer.image(paper),image)

    def test_fading_and_face_tone_do_not_change_visibility(self):
        s=PerspectiveSettings.read(preset('Кристалл Эшера'))
        a,depth_a,n_a,_=render_crystal(s,200,160,'ivory',return_buffers=True)
        b,depth_b,n_b,_=render_crystal(replace(s,face_tone=0.,fog=0.),200,160,'ivory',return_buffers=True)
        np.testing.assert_array_equal(depth_a,depth_b)
        np.testing.assert_array_equal(n_a,n_b)
        self.assertNotEqual(bytes(a.constBits()),bytes(b.constBits()))

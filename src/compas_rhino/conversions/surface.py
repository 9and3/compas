from __future__ import print_function
from __future__ import absolute_import
from __future__ import division

import Rhino
import compas_rhino

from compas.utilities import memoize, geometric_key
from compas.datastructures import Mesh
from compas.datastructures import meshes_join

from ._primitives import point_to_compas

from ._geometry import RhinoGeometry
from ._exceptions import ConversionError


class RhinoSurface(RhinoGeometry):
    """Wrapper for Rhino surfaces."""

    @property
    def geometry(self):
        return self._geometry

    @geometry.setter
    def geometry(self, geometry):
        """Set the geometry of the wrapper.

        Parameters
        ----------
        geometry : :rhino:`Rhino_Geometry_Surface`
            The geometry object defining a surface.

        Raises
        ------
        :class:`ConversionError`
            If the geometry cannot be converted to a surface.

        """
        if not isinstance(geometry, Rhino.Geometry.Surface):
            if not isinstance(geometry, Rhino.Geometry.Brep):
                raise TypeError("Geometry cannot be interpreted as a `Rhino.Geometry.Surface`: {}".format(type(geometry)))
        self._geometry = geometry

    def to_compas(self):
        """Convert the surface to a COMPAS surface.

        Returns
        -------
        :class:`~compas_rhino.geometry.RhinoNurbsSurface`

        Raises
        ------
        :class:`ConversionError`
            If the surface BRep contains more than one face.

        """
        from compas_rhino.geometry import RhinoNurbsSurface

        brep = Rhino.Geometry.Brep.TryConvertBrep(self.geometry)
        if brep.Surfaces.Count > 1:
            raise ConversionError('Conversion of a BRep with multiple underlying surface is currently not supported.')
        for geometry in brep.Surfaces:
            return RhinoNurbsSurface.from_rhino(geometry)

    def to_compas_mesh(self, cls=None, facefilter=None, cleanup=False):
        """Convert the surface b-rep loops to a COMPAS mesh.

        Parameters
        ----------
        cls : :class:`~compas.datastructures.Mesh`, optional
            The type of COMPAS mesh.
        facefilter : callable, optional
            A filter for selection which Brep faces to include.
            If provided, the filter should return True or False per face.
            A very simple filter that includes all faces is ``def facefilter(face): return True``.
            Default parameter value is None in which case all faces are included.
        cleanup : bool, optional
            Flag indicating to clean up the result.
            Cleaning up means to remove isolated faces and unused vertices.
            Default is False.

        Returns
        -------
        :class:`~compas.datastructures.Mesh`
            The resulting mesh.

        Examples
        --------
        >>> import compas_rhino
        >>> from compas_rhino.geometry import RhinoSurface
        >>> from compas_rhino.artists import MeshArtist

        >>> def facefilter(face):
        ...     success, w, h = face.GetSurfaceSize()
        ...     if success:
        ...         if w > 10 and h > 10:
        ...             return True
        ...     return False
        ...

        >>> guid = compas_rhino.select_surface()
        >>> surf = RhinoSurface.from_guid(guid)
        >>> mesh = surf.to_compas(facefilter=facefilter)

        >>> artist = MeshArtist(mesh, layer="Blocks")
        >>> artist.clear_layer()
        >>> artist.draw()

        """
        if not self.geometry.HasBrepForm:
            return
        brep = Rhino.Geometry.Brep.TryConvertBrep(self.geometry)
        if facefilter and callable(facefilter):
            brepfaces = [face for face in brep.Faces if facefilter(face)]
        else:
            brepfaces = brep.Faces
        # vertex maps and face lists
        gkey_xyz = {}
        faces = []
        for face in brepfaces:
            loop = face.OuterLoop
            curve = loop.To3dCurve()
            segments = curve.Explode()
            a = segments[0].PointAtStart
            b = segments[0].PointAtEnd
            a_gkey = geometric_key(a)
            b_gkey = geometric_key(b)
            gkey_xyz[a_gkey] = a
            gkey_xyz[b_gkey] = b
            face = [a_gkey, b_gkey]
            for segment in segments[1:-1]:
                b = segment.PointAtEnd
                b_gkey = geometric_key(b)
                face.append(b_gkey)
                gkey_xyz[b_gkey] = b
            faces.append(face)
        # vertices and faces
        gkey_index = {gkey: index for index, gkey in enumerate(gkey_xyz)}
        vertices = [list(xyz) for gkey, xyz in gkey_xyz.items()]
        faces = [[gkey_index[gkey] for gkey in face] for face in faces]
        # remove duplicates from vertexlist
        polygons = []
        for temp in faces:
            face = []
            for vertex in temp:
                if vertex not in face:
                    face.append(vertex)
            polygons.append(face)
        # define mesh type
        cls = cls or Mesh
        # create mesh
        mesh = cls.from_vertices_and_faces(vertices, polygons)
        mesh.name = self.name
        # remove isolated faces
        if cleanup:
            if mesh.number_of_faces() > 1:
                for face in list(mesh.faces()):
                    if not mesh.face_neighbors(face):
                        mesh.delete_face(face)
            mesh.remove_unused_vertices()
        return mesh

    def to_compas_quadmesh(self, nu, nv=None, weld=False, facefilter=None, cls=None):
        """Convert the surface to a COMPAS mesh.

        Parameters
        ----------
        nu: int
            The number of faces in the u direction.
        nv: int, optional
            The number of faces in the v direction.
            Default is the same as the u direction.
        weld: bool, optional
            Weld the vertices of the mesh.
            Default is False.
        facefilter: callable, optional
            A filter for selection which Brep faces to include.
            If provided, the filter should return True or False per face.
            A very simple filter that includes all faces is ``def facefilter(face): return True``.
            Default parameter value is None in which case all faces are included.
        cls: :class:`~compas.geometry.Mesh`, optional
            The type of COMPAS mesh.

        Returns
        -------
        :class:`~compas.geometry.Mesh`
        """
        nv = nv or nu
        cls = cls or Mesh

        if not self.geometry.HasBrepForm:
            return

        brep = Rhino.Geometry.Brep.TryConvertBrep(self.geometry)

        if facefilter and callable(facefilter):
            faces = [face for face in brep.Faces if facefilter(face)]
        else:
            faces = brep.Faces

        meshes = []
        for face in faces:
            domain_u = face.Domain(0)
            domain_v = face.Domain(1)
            du = (domain_u[1] - domain_u[0]) / (nu)
            dv = (domain_v[1] - domain_v[0]) / (nv)

            @memoize
            def point_at(i, j):
                return point_to_compas(face.PointAt(i, j))

            quads = []
            for i in range(nu):
                for j in range(nv):
                    a = point_at(domain_u[0] + (i + 0) * du, domain_v[0] + (j + 0) * dv)
                    b = point_at(domain_u[0] + (i + 1) * du, domain_v[0] + (j + 0) * dv)
                    c = point_at(domain_u[0] + (i + 1) * du, domain_v[0] + (j + 1) * dv)
                    d = point_at(domain_u[0] + (i + 0) * du, domain_v[0] + (j + 1) * dv)
                    quads.append([a, b, c, d])

            meshes.append(cls.from_polygons(quads))

        return meshes_join(meshes, cls=cls)

    def closest_point(self, xyz):
        """Return the XYZ coordinates of the closest point on the surface from input XYZ-coordinates.

        Parameters
        ----------
        xyz : list
            XYZ coordinates.

        Returns
        -------
        list
            The XYZ coordinates of the closest point on the surface.

        """
        return compas_rhino.rs.EvaluateSurface(self.guid, * compas_rhino.rs.SurfaceClosestPoint(self.guid, xyz))

    def closest_points(self, points):
        return [self.closest_point(point) for point in points]

    # def closest_point_on_boundaries(self, xyz):
    #     """Return the XYZ coordinates of the closest point on the boundaries of the surface from input XYZ-coordinates.

    #     Parameters
    #     ----------
    #     xyz : list
    #         XYZ coordinates.

    #     Returns
    #     -------
    #     list
    #         The XYZ coordinates of the closest point on the boundaries of the surface.

    #     """
    #     from compas_rhino.geometry.curve import RhinoCurve
    #     borders = self.borders(type=0)
    #     proj_dist = {tuple(proj_xyz): distance_point_point(xyz, proj_xyz) for proj_xyz in [RhinoCurve(border).closest_point(xyz) for border in borders]}
    #     compas_rhino.delete_objects(borders)
    #     return min(proj_dist, key=proj_dist.get)

    # def closest_points_on_boundaries(self, points):
    #     return [self.closest_point_on_boundaries(point) for point in points]

    # def space(self, density=(10, 10)):
    #     """Construct a parameter grid overt the UV space of the surface.

    #     Parameters
    #     ----------
    #     density : tuple, optional
    #         The density in the U and V directions of the parameter space.
    #         Default is ``10`` in both directions.

    #     Returns
    #     -------
    #     list
    #         A list of UV parameter tuples.
    #     """
    #     rs = compas_rhino.rs
    #     rs.EnableRedraw(False)
    #     try:
    #         du, dv = density
    #     except TypeError:
    #         du = density
    #         dv = density
    #     density_u = int(du)
    #     density_v = int(dv)
    #     if rs.IsPolysurface(self.guid):
    #         faces = rs.ExplodePolysurfaces(self.guid)
    #     elif rs.IsSurface(self.guid):
    #         faces = [self.guid]
    #     else:
    #         raise Exception('Object is not a (poly)surface.')
    #     uv = []
    #     for face in faces:
    #         domain_u = rs.SurfaceDomain(face, 0)
    #         domain_v = rs.SurfaceDomain(face, 1)
    #         du = (domain_u[1] - domain_u[0]) / (density_u - 1)
    #         dv = (domain_v[1] - domain_v[0]) / (density_v - 1)
    #         for i, j in product(range(density_u), range(density_v)):
    #             uv.append((domain_u[0] + i * du, domain_v[0] + j * dv))
    #     if len(faces) > 1:
    #         rs.DeleteObjects(faces)
    #     rs.EnableRedraw(True)
    #     return uv

    # def heightfield(self, density=(10, 10), over_space=True):
    #     """Construct a point grid over the surface.

    #     Parameters
    #     ----------
    #     density : tuple, optional
    #         The density in the U and V directions of the grid.
    #         Default is ``10`` in both directions.
    #     over_space : bool, optional
    #         Construct the grid over the UV space of the surface.
    #         Default is True.

    #     Returns
    #     -------
    #     list
    #         List of grid points.

    #     """
    #     rs = compas_rhino.rs
    #     rs.EnableRedraw(False)
    #     try:
    #         du, dv = density
    #     except TypeError:
    #         du = density
    #         dv = density
    #     du = int(du)
    #     dv = int(dv)
    #     if rs.IsPolysurface(self.guid):
    #         faces = rs.ExplodePolysurfaces(self.guid)
    #     elif rs.IsSurface(self.guid):
    #         faces = [self.guid]
    #     else:
    #         raise Exception('Object is not a surface.')
    #     xyz = []
    #     if over_space:
    #         for guid in faces:
    #             face = RhinoSurface.from_guid(guid)
    #             uv = face.space(density)
    #             for u, v in uv:
    #                 xyz.append(list(rs.EvaluateSurface(face.guid, u, v)))
    #     else:
    #         for guid in faces:
    #             bbox = rs.BoundingBox(guid)
    #             xmin = bbox[0][0]
    #             xmax = bbox[1][0]
    #             ymin = bbox[0][1]
    #             ymax = bbox[3][1]
    #             xstep = 1.0 * (xmax - xmin) / (du - 1)
    #             ystep = 1.0 * (ymax - ymin) / (dv - 1)
    #             seeds = []
    #             for i in range(du):
    #                 for j in range(dv):
    #                     seed = xmin + i * xstep, ymin + j * ystep, 0
    #                     seeds.append(seed)
    #             points = map(list, rs.ProjectPointToSurface(seeds, guid, [0, 0, 1]))
    #             xyz += points
    #     if len(faces) > 1:
    #         rs.DeleteObjects(faces)
    #     rs.EnableRedraw(True)
    #     return xyz

    # def descent(self, points=None):
    #     """"""
    #     rs = compas_rhino.rs
    #     if not points:
    #         points = self.heightfield()
    #     tol = rs.UnitAbsoluteTolerance()
    #     descent = []
    #     if rs.IsPolysurface(self.guid):
    #         rs.EnableRedraw(False)
    #         faces = {}
    #         for p0 in points:
    #             p = p0[:]
    #             p[2] -= 2 * tol
    #             bcp = rs.BrepClosestPoint(self.guid, p)
    #             uv = bcp[1]
    #             index = bcp[2][1]
    #             try:
    #                 face = faces[index]
    #             except (TypeError, IndexError):
    #                 face = rs.ExtractSurface(self.guid, index, True)
    #                 faces[index] = face
    #             p1 = rs.EvaluateSurface(face, uv[0], uv[1])
    #             vector = [p1[_] - p0[_] for _ in range(3)]
    #             descent.append((p0, vector))
    #         rs.DeleteObjects(faces.values())
    #         rs.EnableRedraw(True)
    #     elif rs.IsSurface(self.guid):
    #         for p0 in points:
    #             p = p0[:]
    #             p[2] -= 2 * tol
    #             bcp = rs.BrepClosestPoint(self.guid, p)
    #             uv = bcp[1]
    #             p1 = rs.EvaluateSurface(self.guid, uv[0], uv[1])
    #             vector = [p1[_] - p0[_] for _ in range(3)]
    #             descent.append((p0, vector))
    #     else:
    #         raise Exception('Object is not a surface.')
    #     return descent

    # def curvature(self, points=None):
    #     """"""
    #     rs = compas_rhino.rs
    #     if not points:
    #         points = self.heightfield()
    #     curvature = []
    #     if rs.IsPolysurface(self.guid):
    #         rs.EnableRedraw(False)
    #         faces = {}
    #         for point in points:
    #             bcp = rs.BrepClosestPoint(self.guid, point)
    #             uv = bcp[1]
    #             index = bcp[2][1]
    #             try:
    #                 face = faces[index]
    #             except (TypeError, IndexError):
    #                 face = rs.ExtractSurface(self.guid, index, True)
    #                 faces[index] = face
    #             props = rs.SurfaceCurvature(face, uv)
    #             curvature.append((point, (props[1], props[3], props[5])))
    #         rs.DeleteObjects(faces.values())
    #         rs.EnableRedraw(False)
    #     elif rs.IsSurface(self.guid):
    #         for point in points:
    #             bcp = rs.BrepClosestPoint(self.guid, point)
    #             uv = bcp[1]
    #             props = rs.SurfaceCurvature(self.guid, uv)
    #             curvature.append((point, (props[1], props[3], props[5])))
    #     else:
    #         raise Exception('Object is not a surface.')
    #     return curvature

    # def borders(self, border_type=1):
    #     """Duplicate the borders of the surface.

    #     Parameters
    #     ----------
    #     border_type : {0, 1, 2}
    #         The type of border.

    #         * 0: All borders
    #         * 1: The exterior borders.
    #         * 2: The interior borders.

    #     Returns
    #     -------
    #     list
    #         The GUIDs of the extracted border curves.
    #     """
    #     rs = compas_rhino.rs
    #     border = rs.DuplicateSurfaceBorder(self.guid, type=border_type)
    #     curves = rs.ExplodeCurves(border, delete_input=True)
    #     return curves

    # def kinks(self, threshold=1e-3):
    #     """Return the XYZ coordinates of kinks, i.e. tangency discontinuities, along the surface's boundaries.

    #     Returns
    #     -------
    #     list
    #         The list of XYZ coordinates of surface boundary kinks.
    #     """
    #     from compas_rhino.geometry.curve import RhinoCurve
    #     rs = compas_rhino.rs
    #     kinks = []
    #     borders = self.borders(border_type=0)
    #     for border in borders:
    #         border = RhinoCurve(border)
    #         extremities = map(lambda x: rs.EvaluateCurve(border.guid, rs.CurveParameter(border.guid, x)), [0., 1.])
    #         if border.is_closed():
    #             start_tgt, end_tgt = border.tangents(extremities)
    #             if angle_vectors(start_tgt, end_tgt) > threshold:
    #                 kinks += extremities
    #         else:
    #             kinks += extremities
    #     return list(set(kinks))

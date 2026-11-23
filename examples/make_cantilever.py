"""Generate a tagged test mesh shaped like BlendGmsh output.

Box cantilever L x b x h (mm), physical groups:
  fixed  : x = 0 face
  tip    : x = L face
  top    : z = h face        (overlaps nothing; used for pressure tests)
  _untagged : remaining faces
  domain : volume

Usage:  python make_cantilever.py out.msh [L b h size]
"""

import sys

import gmsh


def make(path, L=100.0, b=10.0, h=10.0, size=2.5):
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("cantilever")
    gmsh.model.occ.addBox(0, 0, 0, L, b, h)
    gmsh.model.occ.synchronize()
    faces = {}
    for _, s in gmsh.model.getEntities(2):
        c = gmsh.model.occ.getCenterOfMass(2, s)
        if abs(c[0]) < 1e-9:
            faces.setdefault("fixed", []).append(s)
        elif abs(c[0] - L) < 1e-9:
            faces.setdefault("tip", []).append(s)
        elif abs(c[2] - h) < 1e-9:
            faces.setdefault("top", []).append(s)
        else:
            faces.setdefault("_untagged", []).append(s)
    for name, tags in faces.items():
        gmsh.model.addPhysicalGroup(2, tags, name=name)
    gmsh.model.addPhysicalGroup(3, [1], name="domain")
    gmsh.option.setNumber("Mesh.MeshSizeMax", size)
    gmsh.model.mesh.generate(3)
    gmsh.option.setNumber("Mesh.MshFileVersion", 4.1)
    gmsh.write(path)
    gmsh.finalize()


if __name__ == "__main__":
    args = [float(a) for a in sys.argv[2:]]
    make(sys.argv[1], *args)

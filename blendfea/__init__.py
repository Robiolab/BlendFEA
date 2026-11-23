"""BlendFEA: part -> face groups -> volume mesh -> supports & loads -> solve -> results.

    mesh/    steps 1-3  import, face groups, Gmsh volume mesh   (formerly BlendGmsh)
    fea/     steps 4-7  material, BCs, FEniCSx server, results   (formerly FEniCSx CAE)
    lib/     the mesher (matching_library, step_converter), run in a subprocess
    compat   clash check with the old add-ons
"""

try:
    import bpy  # noqa: F401
    _BPY_AVAILABLE = True
except ModuleNotFoundError:   # bpy-free parts are imported by the unit tests
    _BPY_AVAILABLE = False

# FeniCSx-server API versions this release was tested against (see scripts/sync_fxclient.py)
SUPPORTED_SERVER_API = (1,)

if _BPY_AVAILABLE:
    from . import compat, fea, mesh
    _parts = (compat, mesh, fea)
else:
    _parts = ()


def register():
    for part in _parts:
        part.register()


def unregister():
    for part in reversed(_parts):
        part.unregister()

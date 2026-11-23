"""BlendFEA steps 4-7: material, supports & loads, solve on a FEniCSx server, results
(formerly the FEniCSx CAE add-on). core.py is bpy-free and unit-tested."""

try:
    import bpy  # noqa: F401
    _BPY_AVAILABLE = True
except ModuleNotFoundError:
    _BPY_AVAILABLE = False

if _BPY_AVAILABLE:
    from . import ops, props, ui
    _modules = (props, ops, ui)
else:
    _modules = ()


def register():
    for m in _modules:
        m.register()


def unregister():
    for m in reversed(_modules):
        m.unregister()

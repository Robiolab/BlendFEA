"""BlendFEA steps 1-3: part import, face groups, volume mesh (formerly BlendGmsh).

Scene data keeps BlendGmsh's names (scene.fembc_main, fembc_groups,
fembc_groups_active_index) so .blend files made with BlendGmsh open unchanged.
"""
from __future__ import annotations

try:
    import bpy  # type: ignore  # available inside Blender runtime
    _BPY_AVAILABLE = True
except ModuleNotFoundError:   # bpy-free modules (vertex_groups, mesh_import) are unit-tested
    _BPY_AVAILABLE = False

if _BPY_AVAILABLE:
    from .properties import BlendFEAMeshProps, BlendFEAGroupItem
    from .operators import (
        BLENDFEA_OT_ImportSTEP,
        BLENDFEA_OT_ImportMesh,
        BLENDFEA_OT_AddGroup,
        BLENDFEA_OT_RemoveGroup,
        BLENDFEA_OT_AssignFaces,
        BLENDFEA_OT_UnassignFaces,
        BLENDFEA_OT_SelectGroup,
        BLENDFEA_OT_EditFaces,
        BLENDFEA_OT_ExportJSON,
        BLENDFEA_OT_GenerateMesh,
        BLENDFEA_OT_OpenMeshLog,
        BLENDFEA_OT_OpenMeshFolder,
    )
    from .panels import BLENDFEA_UL_GroupList, BLENDFEA_PT_MainPanel

    classes = (
        BlendFEAMeshProps,
        BlendFEAGroupItem,
        BLENDFEA_OT_ImportSTEP,
        BLENDFEA_OT_ImportMesh,
        BLENDFEA_OT_AddGroup,
        BLENDFEA_OT_RemoveGroup,
        BLENDFEA_OT_AssignFaces,
        BLENDFEA_OT_UnassignFaces,
        BLENDFEA_OT_SelectGroup,
        BLENDFEA_OT_EditFaces,
        BLENDFEA_OT_ExportJSON,
        BLENDFEA_OT_GenerateMesh,
        BLENDFEA_OT_OpenMeshLog,
        BLENDFEA_OT_OpenMeshFolder,
        BLENDFEA_UL_GroupList,
        BLENDFEA_PT_MainPanel,
    )

    @bpy.app.handlers.persistent
    def _clear_stale_mesh_status(_dummy):
        """A .blend saved mid-run still says 'Generating mesh...' with no process behind it."""
        for scene in bpy.data.scenes:
            p = getattr(scene, "fembc_main", None)
            if p is not None and p.mesh_status == "Generating mesh...":
                p.mesh_status = "Mesh generation was interrupted - generate again"
else:
    classes = ()  # type: ignore[assignment]


def register_scene_props():
    """(Re)attach the scene data. Also used after disabling the old BlendGmsh add-on,
    whose unregister deletes these same attributes."""
    bpy.types.Scene.fembc_main = bpy.props.PointerProperty(type=BlendFEAMeshProps)
    bpy.types.Scene.fembc_groups = bpy.props.CollectionProperty(type=BlendFEAGroupItem)
    bpy.types.Scene.fembc_groups_active_index = bpy.props.IntProperty(default=0)


def register():
    if not _BPY_AVAILABLE:
        return
    for cls in classes:
        bpy.utils.register_class(cls)
    register_scene_props()
    if _clear_stale_mesh_status not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_clear_stale_mesh_status)


def unregister():
    if not _BPY_AVAILABLE:
        return
    if _clear_stale_mesh_status in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_clear_stale_mesh_status)
    for name in ("fembc_groups_active_index", "fembc_groups", "fembc_main"):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

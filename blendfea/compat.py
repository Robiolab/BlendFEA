"""Living next to the add-ons BlendFEA replaces.

BlendFEA keeps their scene data names (scene.fembc_main, scene.fx_cae, ...) so old
.blend files open unchanged. The flip side: if BlendGmsh or FEniCSx CAE is still
enabled, both register those same attributes and whichever loaded last wins. The
panels therefore check first and offer a one-click fix that also carries the server
settings (URL, API token) over from FEniCSx CAE.
"""
import bpy

# last component of the add-on module name -> what to call it
OLD_ADDONS = {
    "blendgmsh": "BlendGmsh",
    "blender_addon": "BlendGmsh (development link)",
    "fenicsx_cae": "FEniCSx CAE",
}
_PREF_FIELDS = ("server_url", "token", "results_dir", "paraview_path", "auto_download",
                "auto_import")


def old_addons_enabled(context=None):
    addons = (context or bpy.context).preferences.addons
    return [m for m in addons.keys()
            if m != __package__ and m.rpartition(".")[2] in OLD_ADDONS]


def draw_conflict(layout, context):
    """Draw the clash warning; True if one was drawn (the panel should stop there)."""
    old = old_addons_enabled(context)
    if not old:
        return False
    box = layout.box()
    box.alert = True
    names = ", ".join(sorted({OLD_ADDONS[m.rpartition(".")[2]] for m in old}))
    for line in ("BlendFEA replaces " + names + ".", "With both enabled their panels and",
                 "data clash. Disable the old one(s):"):
        box.label(text=line, icon="ERROR" if line.startswith("BlendFEA") else "BLANK1")
    box.operator("blendfea.disable_old_addons", icon="CHECKMARK")
    return True


def _old_server_prefs(context):
    for m in old_addons_enabled(context):
        if m.rpartition(".")[2] == "fenicsx_cae":
            pr = context.preferences.addons[m].preferences
            return {f: getattr(pr, f) for f in _PREF_FIELDS if hasattr(pr, f)}
    return {}


class BLENDFEA_OT_disable_old_addons(bpy.types.Operator):
    bl_idname = "blendfea.disable_old_addons"
    bl_label = "Disable old add-ons"
    bl_description = ("Disable BlendGmsh / FEniCSx CAE (they stay installed) and copy the "
                      "server URL and API token into BlendFEA")

    def execute(self, context):
        import addon_utils
        old = old_addons_enabled(context)
        saved = _old_server_prefs(context)
        for m in old:
            addon_utils.disable(m, default_set=True)
        # Their unregister() deleted the scene attributes we share: put ours back.
        from .mesh import register_scene_props as mesh_props
        from .fea.props import register_scene_props as fea_props
        mesh_props()
        fea_props()
        ours = context.preferences.addons[__package__].preferences
        copied = []
        if saved and not ours.token:
            for f, v in saved.items():
                setattr(ours, f, v)
            copied = ["server URL", "API token"]
        context.preferences.is_dirty = True
        self.report({'INFO'}, f"Disabled {len(old)} old add-on(s)"
                    + (f"; copied {' and '.join(copied)}" if copied else ""))
        return {'FINISHED'}


classes = (BLENDFEA_OT_disable_old_addons,)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)

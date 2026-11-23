"""Add-on preferences and scene properties for steps 4-7 (formerly the FEniCSx CAE add-on).

Scene data keeps the old name (scene.fx_cae) so existing .blend files open unchanged."""

import bpy
from bpy.props import (BoolProperty, BoolVectorProperty, CollectionProperty, EnumProperty,
                       FloatProperty, FloatVectorProperty, IntProperty, PointerProperty,
                       StringProperty)

# Mirrors fxserver/materials.py (names only; values live on the server)
MATERIAL_ITEMS = [
    ("PLA", "PLA (FDM)", "PLA, 100% infill, layer-aware failure check"),
    ("PLA+", "PLA+ (FDM)", "Toughened PLA"),
    ("PETG", "PETG (FDM)", ""),
    ("ABS", "ABS (FDM)", ""),
    ("ASA", "ASA (FDM)", ""),
    ("PA12-CF", "PA12-CF (FDM)", "Carbon filled nylon"),
    ("Resin-Standard", "Standard resin (SLA)", "Brittle, Rankine criterion"),
    ("Al-6061-T6", "Aluminium 6061-T6", "von Mises"),
    ("Steel-S235", "Steel S235", "von Mises"),
    ("Steel-S355", "Steel S355", "von Mises"),
    ("SS-304", "Stainless 304", "von Mises"),
]

BC_TYPES = [
    ("fixed", "Fixed", "All displacements zero", "PINNED", 0),
    ("displacement", "Displacement / roller",
     "Prescribe chosen components (others free). Use 0 for a roller/symmetry", "CON_LOCLIKE", 1),
    ("force", "Force", "Total force [N] spread uniformly over the faces", "FORCE_FORCE", 2),
    ("pressure", "Pressure", "Pressure [MPa], positive pushes into the surface", "FORCE_WIND", 3),
]


def _conn_changed(self, context):
    from .ops import check_connection
    check_connection(context)


# Preferences belong to the extension itself (e.g. "bl_ext.user_default.blendfea"),
# not to this sub-package.
ADDON_ID = __package__.rpartition(".")[0]


class BlendFEAPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    server_url: StringProperty(name="Server URL", default="https://fea.roboticos.co", update=_conn_changed,
                               description="FEniCSx server, e.g. https://fea.roboticos.co or "
                                           "http://192.168.1.20:8010 on the LAN")
    token: StringProperty(name="API token", default="", subtype="PASSWORD", update=_conn_changed,
                          description="FX_TOKEN from the server's .env file")
    results_dir: StringProperty(name="Results folder", default="//fea_results", subtype="DIR_PATH",
                                description="Where downloaded results go (// = next to the .blend)")
    paraview_path: StringProperty(name="ParaView executable", default="", subtype="FILE_PATH",
                                  description="Leave empty to find ParaView automatically (PATH, "
                                              r"C:\Program Files\ParaView *\bin, /Applications, /opt)")
    auto_download: BoolProperty(name="Download when finished", default=True)
    auto_import: BoolProperty(name="Import result surface when finished", default=True)

    def draw(self, context):
        col = self.layout.column()
        row = col.row(align=True)
        row.prop(self, "server_url")
        row.operator("blendfea.check_server", text="Test", icon="URL")
        from .ops import _conn
        if _conn["msg"]:
            r = col.row()
            r.alert = _conn["state"] not in ("ok", "checking")
            r.label(text=_conn["msg"][:120],
                    icon="CHECKMARK" if _conn["state"] == "ok" else ("TIME" if _conn["state"] == "checking" else "ERROR"))
        col.prop(self, "token")
        col.prop(self, "results_dir")
        col.prop(self, "paraview_path")
        if not self.paraview_path:
            from .ops import find_paraview
            exe = find_paraview()
            r = col.row()
            r.alert = not exe
            r.label(text=f"Auto-detected: {exe}" if exe else "ParaView not found automatically; set it above",
                    icon="CHECKMARK" if exe else "ERROR")
        col.prop(self, "auto_download")
        col.prop(self, "auto_import")


FORCE_DIR_ITEMS = [
    ("NEG_Z", "-Z (down)", "", "SORT_ASC", 0), ("POS_Z", "+Z (up)", "", "SORT_DESC", 1),
    ("NEG_X", "-X", "", "BACK", 2), ("POS_X", "+X", "", "FORWARD", 3),
    ("NEG_Y", "-Y", "", "BACK", 4), ("POS_Y", "+Y", "", "FORWARD", 5),
    ("NORMAL", "Push on face", "Along the faces' mean inward normal (flat-ish faces)", "ORIENTATION_NORMAL", 6),
    ("CUSTOM", "Custom vector", "Enter Fx, Fy, Fz", "EMPTY_ARROWS", 7),
]


def _msh_changed(self, context):
    from .ops import scan_groups
    try:
        scan_groups(self)
    except Exception:
        pass


def _deform_changed(self, context):
    from .ops import deform_scale_changed
    deform_scale_changed(self, context)


class BlendFEAMshGroup(bpy.types.PropertyGroup):
    """A surface physical group found in the .msh (name is inherited)."""
    dim: IntProperty(default=2)
    area: FloatProperty(default=-1.0)   # mm^2, known after a mesh check


class BlendFEABC(bpy.types.PropertyGroup):
    group: StringProperty(name="Group", description="Physical group (face set) in the mesh")
    type: EnumProperty(name="Type", items=BC_TYPES, default="fixed")
    force_mag: FloatProperty(name="Force [N]", default=10.0, min=0.0,
                             description="Total force on the group, spread evenly over its area")
    force_dir: EnumProperty(name="Direction", items=FORCE_DIR_ITEMS, default="NEG_Z")
    force: FloatVectorProperty(name="Force [N]", size=3, default=(0.0, 0.0, -10.0), subtype="XYZ")
    pressure: FloatProperty(name="Pressure [MPa]", default=0.1, step=1, precision=4)
    disp: FloatVectorProperty(name="Displacement [mm]", size=3, default=(0.0, 0.0, 0.0),
                              subtype="XYZ", precision=4)
    disp_mask: BoolVectorProperty(name="Constrain", size=3, default=(False, False, True),
                                  subtype="XYZ")
    enabled: BoolProperty(name="Enabled", default=True)


class BlendFEAJobProps(bpy.types.PropertyGroup):
    msh_path: StringProperty(name="Mesh (.msh)", subtype="FILE_PATH", update=_msh_changed,
                             description="Tagged Gmsh volume mesh (normally the one made in step 3)")
    msh_mtime: StringProperty(default="")       # repr(os.path.getmtime) when last read
    msh_scanned_path: StringProperty(default="")   # file the groups/size were read from
    msh_bbox: StringProperty(default="")           # JSON [dx, dy, dz] in file units, read locally
    follow_blendgmsh: BoolProperty(
        name="Use the mesh from step 3", default=True,
        description="Use (and re-read) the mesh each time 'Generate Mesh' makes one. "
                    "Off: pick any tagged .msh file")
    source_object: StringProperty(default="", description="Object hidden while results show")
    mesh_info: StringProperty(default="")      # JSON from the last mesh check
    jobs_cache: StringProperty(default="")     # JSON list from the server
    last_analysis: StringProperty(default="static")
    job_name: StringProperty(name="Job name", default="")
    units: EnumProperty(name="Mesh units", default="mm", items=[
        ("mm", "mm", ""), ("cm", "cm", ""), ("m", "m", ""), ("in", "inch", "")],
        description="Length unit of the mesh coordinates. Loads are always N / MPa / mm")
    analysis: EnumProperty(name="Analysis", default="static", items=[
        ("static", "Linear static", "Solve and check failure"),
        ("mesh_check", "Mesh check only", "Load mesh, report groups/areas/mass - no solve")])
    material: EnumProperty(name="Material", items=MATERIAL_ITEMS, default="PLA")
    stiffness_scale: FloatProperty(name="Stiffness scale", default=1.0, min=0.01, max=2.0,
                                   description="Multiply E (e.g. 0.7-0.9 for partial infill)")
    strength_scale: FloatProperty(name="Strength scale", default=1.0, min=0.01, max=2.0,
                                  description="Multiply all strengths")
    target_sf: FloatProperty(name="Target SF", default=0.0, min=0.0,
                             description="Required safety factor (0 = material default)")
    build_axis: EnumProperty(name="Build direction", default="Z", items=[
        ("X", "X", ""), ("Y", "Y", ""), ("Z", "Z", ""), ("CUSTOM", "Custom", "")],
        description="Layer stacking direction when printed (FDM materials)")
    build_custom: FloatVectorProperty(name="Build vector", size=3, default=(0, 0, 1), subtype="XYZ")
    element_order: EnumProperty(name="Elements", default="2", items=[
        ("2", "Quadratic (accurate)", ""), ("1", "Linear (fast, stiff)", "")])
    gravity: BoolProperty(name="Self weight", default=False)
    gravity_vec: FloatVectorProperty(name="Acceleration [g]", size=3, default=(0, 0, -1),
                                     subtype="XYZ")
    np: IntProperty(name="MPI processes", default=1, min=1, max=64)
    solver: EnumProperty(name="Solver", default="auto", items=[
        ("auto", "Auto", ""), ("direct", "Direct (MUMPS)", ""), ("iterative", "Iterative (CG+GAMG)", "")])

    bcs: CollectionProperty(type=BlendFEABC)
    bcs_index: IntProperty(default=0)
    groups: CollectionProperty(type=BlendFEAMshGroup)

    # job state (mirrored from the background thread by a timer)
    job_id: StringProperty(default="")
    job_state: StringProperty(default="")
    job_stage: StringProperty(default="")
    job_error: StringProperty(default="")
    job_summary: StringProperty(default="")   # JSON
    job_keep: BoolProperty(default=False)
    transfer: StringProperty(default="")      # "Uploading 3.1 / 12.0 MB"
    results_path: StringProperty(default="", subtype="DIR_PATH")
    show_advanced: BoolProperty(default=False)
    color_field: EnumProperty(name="Show", default="sf", items=[
        ("sf", "Safety factor", ""), ("von_mises", "von Mises", ""),
        ("disp_magnitude", "Displacement", ""), ("bc", "BCs", "")])
    deform_scale: FloatProperty(
        name="Deformation ×", default=1.0, min=0.0, soft_max=10000.0, precision=1, step=100,
        update=_deform_changed,
        description="How much the displacement is exaggerated on the result: "
                    "0 = undeformed, 1 = true scale, 50 = fifty times")


classes = (BlendFEAPreferences, BlendFEAMshGroup, BlendFEABC, BlendFEAJobProps)


def register_scene_props():
    """(Re)attach the scene data. Also used after disabling the old FEniCSx CAE add-on,
    whose unregister deletes this same attribute."""
    bpy.types.Scene.fx_cae = PointerProperty(type=BlendFEAJobProps)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    register_scene_props()


def unregister():
    if hasattr(bpy.types.Scene, "fx_cae"):
        del bpy.types.Scene.fx_cae
    for c in reversed(classes):
        bpy.utils.unregister_class(c)

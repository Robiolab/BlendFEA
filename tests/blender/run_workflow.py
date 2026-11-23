"""End-to-end test of an installed BlendFEA package, in a background Blender.

    BLENDER_USER_RESOURCES=<empty folder> blender -b --factory-startup --python-exit-code 1 \
        --python tests/blender/run_workflow.py -- dist/blendfea-<ver>-<platform>.zip

Use an empty BLENDER_USER_RESOURCES so nothing else is installed. Ideally run it with a
Blender whose own Python has no gmsh, to prove the bundled wheel is what gets used.
Exits non-zero on failure. Uses examples/box.step; writes only to a temp folder.
"""
import json
import os
import sys
import tempfile
import time
import traceback

import bpy
import bmesh
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARG = sys.argv[sys.argv.index("--") + 1]
LINKED = ARG == "linked"
ZIP = None if LINKED else os.path.abspath(ARG)
STEP = os.path.join(ROOT, "examples", "box.step")
WORK = tempfile.mkdtemp(prefix="blendfea_test_")
ICONS = set(bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys())
fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


class FakeLayout:
    """Stands in for UILayout: accepts any call, checks icon names, records text."""
    texts = []

    def __getattr__(self, name):
        def call(*a, **kw):
            icon = kw.get("icon")
            if icon and icon not in ICONS:
                fails.append(f"bad icon {icon!r} in {name}")
            if kw.get("text"):
                FakeLayout.texts.append(kw["text"])
            elif name == "prop" and len(a) > 1:
                FakeLayout.texts.append(f"<{a[1]}>")
            return FakeLayout()
        return call

    def __setattr__(self, k, v):
        pass


def draw(cls):
    """Run a panel's draw() against FakeLayout; returns the visible text."""
    FakeLayout.texts = []

    class Shim:
        layout = FakeLayout()

        def __getattr__(self, k):
            v = cls.__dict__.get(k)
            if v is None or isinstance(v, (staticmethod, classmethod)):
                return getattr(cls, k)
            return v.__get__(self) if callable(v) else v
    try:
        cls.draw(Shim(), bpy.context)
    except Exception:
        fails.append(f"draw {cls.__name__} raised")
        traceback.print_exc()
    return " | ".join(FakeLayout.texts)


def select_polys(obj, idxs):
    bpy.ops.mesh.select_all(action='DESELECT')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    for i in idxs:
        bm.faces[i].select_set(True)
    bmesh.update_edit_mesh(obj.data)


def wait_for_mesh(ops, limit=600):
    t0 = time.time()
    while ops.mesh_running() and time.time() - t0 < limit:
        ops._poll_mesh_subprocess()
        time.sleep(0.3)
    ops._poll_mesh_subprocess()


# --------------------------------------------------------------------------- install
print("\n== " + ("enable the dev link" if LINKED else f"install {os.path.basename(ZIP)}"))
print("  user resources:", bpy.utils.resource_path('USER'))
ADDON = "bl_ext.user_default.blendfea"
if LINKED:
    import addon_utils
    bpy.ops.extensions.repo_refresh_all()
    addon_utils.enable(ADDON, default_set=True)
else:
    r = bpy.ops.extensions.package_install_files(filepath=ZIP, repo="user_default", enable_on_install=True)
    check(r == {'FINISHED'}, "package installs")
check(ADDON in bpy.context.preferences.addons, "BlendFEA enabled")
import importlib  # noqa: E402
mops = importlib.import_module(ADDON + ".mesh.operators")
mpanels = importlib.import_module(ADDON + ".mesh.panels")
fops = importlib.import_module(ADDON + ".fea.ops")
fui = importlib.import_module(ADDON + ".fea.ui")
import gmsh  # noqa: E402
print("  gmsh:", gmsh.__version__, gmsh.__file__)
if not LINKED:   # a dev link uses the gmsh in Blender's own Python
    check("extensions" in gmsh.__file__.replace("\\", "/"), "gmsh comes from the bundled wheel")
pr = bpy.context.preferences.addons[ADDON].preferences
check(pr.server_url == "https://fea.roboticos.co", f"default server {pr.server_url}")

C = bpy.context
scene = C.scene
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob)

# --------------------------------------------------------------------------- steps 1-2
print("\n== steps 1-2: import STEP, groups")
blend = os.path.join(WORK, "part.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend)          # meshes should land in //fea/
r = bpy.ops.blendfea.import_step(filepath=STEP)
obj = C.active_object
check(r == {'FINISHED'} and mops._is_brep(obj), f"STEP imported in-process (gmsh): {obj and obj.name}")
bpy.ops.blendfea.add_group()
bpy.ops.blendfea.add_group()
check([g.name for g in scene.fembc_groups] == ["support", "load"], "default group names")
check({p.material_index for p in obj.data.polygons} == {0}, "all faces grey before assigning")
check("No faces assigned" in mops.generate_blocker(C), "Generate explains why it's disabled")
ptags = mops._poly_tags(obj.data)
tags = sorted(set(ptags))
bpy.ops.blendfea.edit_faces()
scene.fembc_groups_active_index = 0
select_polys(obj, [ptags.index(tags[0])])
bpy.ops.blendfea.assign_faces()
scene.fembc_groups_active_index = 1
select_polys(obj, [ptags.index(tags[-1])])
bpy.ops.blendfea.assign_faces()
bpy.ops.object.mode_set(mode='OBJECT')
check([g.assigned for g in scene.fembc_groups] == [1, 1], "one CAD face per group")
scene.fembc_groups[0].name = "fixed"
check(mpanels.BLENDFEA_PT_MainPanel and "1. Part" in draw(mpanels.BLENDFEA_PT_MainPanel), "mesh panel draws")

# --------------------------------------------------------------------------- step 3 + hand-off
print("\n== step 3: generate mesh in the subprocess (bundled gmsh)")
r = bpy.ops.blendfea.generate_mesh()
check(r == {'FINISHED'}, "generate started")
wait_for_mesh(mops)
p = scene.fembc_main
print("  status:", p.mesh_status)
if "failed" in p.mesh_status and os.path.isfile(p.mesh_log_path):
    print(open(p.mesh_log_path, encoding="utf-8", errors="replace").read()[-2000:])
check(p.mesh_status.startswith("Mesh ready"), "mesh generated")
expect_dir = os.path.join(WORK, "fea")
check(os.path.dirname(p.msh_output_path) == expect_dir, f"mesh saved next to the .blend: {p.msh_output_path}")
found = mops.read_msh_surface_groups(p.msh_output_path)
check({"fixed", "load"} <= set(found), f"groups in the .msh: {found}")
fx = scene.fx_cae
check(bpy.path.abspath(fx.msh_path) == p.msh_output_path, "job panel received the mesh directly (no polling)")
check({g.name for g in fx.groups} >= {"fixed", "load"}, f"job panel groups: {[g.name for g in fx.groups]}")
txt = draw(fui.BLENDFEA_PT_main)
check("box.msh" in txt, "job panel shows the mesh file")
check("BlendFEA replaces" not in txt, "no clash warning on a clean install")

# --------------------------------------------------------------------------- results
print("\n== step 7: results and deformation factor")
pts = np.array([[x, y, z] for x in (0, 10) for y in (0, 100) for z in (0, 10)], float)
tris = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                 [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
disp = np.zeros_like(pts)
disp[:, 2] = -0.116 * (pts[:, 1] / 100) ** 2
res = os.path.join(WORK, "job_abc123")
os.makedirs(res)
np.savez(os.path.join(res, "surface_results.npz"), points=pts, triangles=tris, sf=np.full(8, 3.0),
         von_mises=np.linspace(0, 20, 8), disp_magnitude=np.abs(disp[:, 2]), bc=np.zeros(12, int),
         displacement=disp, unit_factor=1.0)
json.dump({"summary": {"max_displacement_mm": 0.116}}, open(os.path.join(res, "result.json"), "w"))
fx.job_state = "done"
fx.job_summary = json.dumps({"summary": {"max_displacement_mm": 0.116}})
fops.import_results(C, res)
C.view_layer.update()
check(fx.deform_scale == 50.0, f"auto deformation x{fx.deform_scale:g}")
txt = draw(fui.BLENDFEA_PT_results)
check("Max displacement 0.116 mm (shown ×50)" in txt, "results panel shows the real factor")

# --------------------------------------------------------------------------- old add-on clash
print("\n== clash with an old add-on")
compat = importlib.import_module(ADDON + ".compat")
check(compat.old_addons_enabled() == [], "nothing old enabled")

print("\nRESULT:", "PASS" if not fails else f"{len(fails)} FAIL(S): " + "; ".join(fails), flush=True)
sys.exit(1 if fails else 0)

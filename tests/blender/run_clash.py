"""Upgrade test: BlendFEA installed while the add-ons it replaces are still enabled.

    BLENDER_USER_RESOURCES=<empty folder> blender -b --factory-startup --python-exit-code 1 \
        --python tests/blender/run_clash.py -- dist/blendfea-<ver>-<platform>.zip OLD.zip [OLD.zip]

OLD.zip: a BlendGmsh and/or FEniCSx CAE package. They are installed first, BlendFEA last.
Checks the clash warning, then the one-click fix: old add-ons disabled, scene data
working again, server URL and API token carried over.
"""
import importlib
import os
import sys

import bpy

args = [os.path.abspath(a) for a in sys.argv[sys.argv.index("--") + 1:]]
ZIP, OLD = args[0], args[1:]
ADDON = "bl_ext.user_default.blendfea"
fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


class FakeLayout:
    texts = []

    def __getattr__(self, name):
        def call(*a, **kw):
            if kw.get("text"):
                FakeLayout.texts.append(kw["text"])
            return FakeLayout()
        return call

    def __setattr__(self, k, v):
        pass


def panel_text(cls):
    FakeLayout.texts = []

    class Shim:
        layout = FakeLayout()

        def __getattr__(self, k):
            return getattr(cls, k)
    cls.draw(Shim(), bpy.context)
    return " | ".join(FakeLayout.texts)


print("\n== install old add-ons, then BlendFEA")
for z in OLD + [ZIP]:
    r = bpy.ops.extensions.package_install_files(filepath=z, repo="user_default", enable_on_install=True)
    check(r == {'FINISHED'}, f"installed {os.path.basename(z)}")
prefs = bpy.context.preferences
old_fx = next((m for m in prefs.addons.keys() if m.endswith(".fenicsx_cae")), None)
if old_fx:
    prefs.addons[old_fx].preferences.token = "secret-token-123"
    prefs.addons[old_fx].preferences.server_url = "http://192.168.1.20:8010"

compat = importlib.import_module(ADDON + ".compat")
mpanels = importlib.import_module(ADDON + ".mesh.panels")
fui = importlib.import_module(ADDON + ".fea.ui")
old = compat.old_addons_enabled()
print("  old enabled:", old)
check(len(old) == len(OLD), "both old add-ons detected")
txt = panel_text(mpanels.BLENDFEA_PT_MainPanel)
check("BlendFEA replaces" in txt and "1. Part" not in txt, "mesh panel shows only the clash warning")
txt = panel_text(fui.BLENDFEA_PT_main)
check("BlendFEA replaces" in txt, "job panel shows the clash warning")
check(not fui.BLENDFEA_PT_bcs.poll(bpy.context), "job sub-panels hidden during the clash")

print("\n== one-click fix")
r = bpy.ops.blendfea.disable_old_addons()
check(r == {'FINISHED'}, "operator ran")
check(compat.old_addons_enabled() == [], "old add-ons disabled")
check(all(m in [a for a in prefs.addons.keys()] for m in [ADDON]), "BlendFEA still enabled")
sc = bpy.context.scene
check(type(sc.fembc_main).__name__ == "BlendFEAMeshProps", f"mesh scene data is BlendFEA's: {type(sc.fembc_main).__name__}")
check(type(sc.fx_cae).__name__ == "BlendFEAJobProps", f"job scene data is BlendFEA's: {type(sc.fx_cae).__name__}")
check(hasattr(sc.fx_cae, "deform_scale") and hasattr(sc.fembc_main, "groups_object"), "scene data usable")
ours = prefs.addons[ADDON].preferences
if old_fx:
    check(ours.token == "secret-token-123", "API token carried over")
    check(ours.server_url == "http://192.168.1.20:8010", "server URL carried over")
txt = panel_text(mpanels.BLENDFEA_PT_MainPanel)
check("1. Part" in txt and "BlendFEA replaces" not in txt, "panels back to normal")
bpy.ops.blendfea.add_group.poll()   # operators still registered after the old ones left

print("\nRESULT:", "PASS" if not fails else f"{len(fails)} FAIL(S): " + "; ".join(fails), flush=True)
sys.exit(1 if fails else 0)

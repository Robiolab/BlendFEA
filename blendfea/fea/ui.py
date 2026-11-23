"""N-panel UI (tab 'FEA')."""

import json
import os
import textwrap

import bpy

from . import core, ops
from ..mesh import operators as mesh_ops
from .. import compat
from .props import BC_TYPES

BC_ICON = {t[0]: t[3] for t in BC_TYPES}
STATE_ICON = {"done": "CHECKMARK", "failed": "ERROR", "error": "ERROR", "cancelled": "CANCEL",
              "running": "SORTTIME", "queued": "TIME", "uploading": "EXPORT", "loading": "IMPORT"}
VERDICT_ICON = {"PASS": "CHECKMARK", "MARGINAL": "ERROR", "FAIL": "CANCEL"}
FDM = {"PLA", "PLA+", "PETG", "ABS", "ASA", "PA12-CF"}

# Colour swatch icons: STRIP_COLOR_* since Blender 4.4, SEQUENCE_COLOR_* before
_ICONS = bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys()
_SW = "STRIP_COLOR_" if "STRIP_COLOR_01" in _ICONS else "SEQUENCE_COLOR_"
RED, ORANGE, YELLOW, GREEN, BLUE, GREY = (_SW + n for n in ("01", "02", "03", "04", "05", "09"))


def _wrap(layout, text, width=44, icon="NONE", alert=False):
    col = layout.column(align=True)
    col.alert = alert
    for i, line in enumerate(textwrap.wrap(text, width) or [""]):
        col.label(text=line, icon=icon if i == 0 else ("BLANK1" if icon != "NONE" else "NONE"))
    return col


def _fmt_size(b):
    return " x ".join(f"{c:.4g}" for c in b)


def part_size_mm(p):
    """([dx, dy, dz] in mm, mass_kg or None). The size comes from the .msh itself (always
    current) in the chosen units; the mass from the last server check, only while that
    check matches this file and units."""
    info = _json(p.mesh_info, {})
    local = _json(p.msh_bbox, None)
    if local:
        b = [c * core.UNIT_TO_MM[p.units] for c in local]
    elif info.get("bbox_size"):
        b = info["bbox_size"]
    else:
        return None, None
    sb = info.get("bbox_size")
    same = sb and all(abs(x - y) <= 0.01 * max(abs(y), 1e-9) for x, y in zip(sb, b))
    return b, (info.get("mass_kg") if same else None)


def _size_warning(L, p):
    """Run-panel box when the part size makes the units look wrong (doesn't block)."""
    b, _ = part_size_mm(p)
    if not b:
        return
    diag = sum(c * c for c in b) ** 0.5
    if 1.0 <= diag <= 1e5:
        return
    box = L.box()
    _wrap(box, f"Part is {diag:.3g} mm across ({_fmt_size(b)} mm) - check Mesh units",
          icon="ERROR", alert=True)
    local = _json(p.msh_bbox, None)
    u = core.suggest_units(local, p.units) if local else None
    if u:
        r = box.row(align=True)
        r.label(text=f"In {u}: {_fmt_size([c * core.UNIT_TO_MM[u] for c in local])} mm",
                icon="BLANK1")
        op = r.operator("wm.context_set_enum", text=f"Use {u}")
        op.data_path, op.value = "scene.fx_cae.units", u


def _json(s, default):
    try:
        return json.loads(s) if s else default
    except json.JSONDecodeError:
        return default


class BLENDFEA_UL_bcs(bpy.types.UIList):
    bl_idname = "BLENDFEA_UL_bcs"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        sub = row.row()
        sub.active = item.enabled
        known = {g.name for g in data.groups}
        sub.alert = bool(item.group) and bool(known) and item.group not in known
        sub.label(text=item.group or "(pick a group)", icon=BC_ICON.get(item.type, "DOT"))
        sub.label(text=core.bc_label(item))


class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "FEA"

    @classmethod
    def poll(cls, context):
        # While an old add-on owns the shared scene data, only the main panel draws (the warning)
        return not compat.old_addons_enabled(context)


class BLENDFEA_PT_main(_Base, bpy.types.Panel):
    bl_label = "FEniCSx Job"
    bl_order = 10    # below "Mesh & Face Groups" (steps 1-3) in the same tab

    @classmethod
    def poll(cls, context):
        return True

    def draw(self, context):
        if compat.draw_conflict(self.layout, context):
            return
        p = context.scene.fx_cae
        L = self.layout
        self._connection(L)
        col = L.column(align=True)
        col.prop(p, "follow_blendgmsh")
        path = bpy.path.abspath(p.msh_path)
        if p.follow_blendgmsh:
            mesh_status = context.scene.fembc_main.mesh_status
            row = col.row(align=True)
            if mesh_ops.mesh_running():
                row.label(text="Generating the mesh (step 3) ...", icon="TIME")
            elif p.msh_path:
                row.label(text=os.path.basename(path), icon="MESH_DATA")
            else:
                row.label(text="Waiting for 'Generate Mesh' (step 3)", icon="TIME")
            row.operator("blendfea.scan_groups", text="", icon="FILE_REFRESH")
            if "failed" in mesh_status:
                _wrap(col, "Step 3: " + mesh_status, icon="ERROR", alert=True)
        else:
            row = col.row(align=True)
            row.prop(p, "msh_path", text="")
            row.operator("blendfea.scan_groups", text="", icon="FILE_REFRESH")
        if p.msh_path and not os.path.isfile(path):
            col.label(text="Mesh file not found", icon="ERROR")

        L.prop(p, "job_name", text="Name")
        row = L.row(align=True)
        row.prop(p, "units", text="Units")
        b, mass = part_size_mm(p)
        if b:
            row.label(text=_fmt_size(b) + " mm")
            if mass:
                L.label(text=f"Mass {mass * 1000:.3g} g (last check)", icon="BLANK1")


    @staticmethod
    def _connection(L):
        c = ops._conn
        st = c["state"]
        if st == "ok":
            return       # shown compactly in the Run panel
        box = L.box()
        if st in ("unknown", "checking"):
            box.label(text="Checking server connection ...", icon="TIME")
            return
        box.alert = True
        title = {"auth": "Server rejected the API token",
                 "unreachable": "Can't reach the FEniCSx server"}.get(st, "Server problem")
        box.label(text=title, icon="ERROR")
        sub = box.column()
        sub.alert = False
        _wrap(sub, c["msg"], 44)
        row = box.row(align=True)
        row.operator("blendfea.open_prefs", icon="PREFERENCES")
        row.operator("blendfea.check_server", text="Retry", icon="FILE_REFRESH")


class BLENDFEA_PT_material(_Base, bpy.types.Panel):
    bl_label = "Material"
    bl_parent_id = "BLENDFEA_PT_main"

    def draw(self, context):
        p = context.scene.fx_cae
        L = self.layout
        L.prop(p, "material", text="")
        m = ops._materials.get(p.material)
        if m:
            if m["criterion"] == "fdm":
                txt = f"E {m['E']:g} · along layers {m['Xt']:g} · across {m['Zt']:g} MPa"
            elif m["criterion"] == "von_mises":
                txt = f"E {m['E']:g} · yield {m['Sy']:g} MPa"
            else:
                txt = f"E {m['E']:g} · tensile {m['Xt']:g} MPa"
            L.label(text=txt, icon="INFO")
        if p.material in FDM:
            L.label(text="Layers stack along (print direction):")
            row = L.row()
            row.prop(p, "build_axis", expand=True)
            if p.build_axis == "CUSTOM":
                L.prop(p, "build_custom", text="")
        col = L.column(align=True)
        col.prop(p, "stiffness_scale", text="Stiffness x")
        col.prop(p, "strength_scale", text="Strength x")
        col.prop(p, "target_sf", text="Target SF (0 = default)")


class BLENDFEA_PT_bcs(_Base, bpy.types.Panel):
    bl_label = "Supports & Loads"
    bl_parent_id = "BLENDFEA_PT_main"

    def draw(self, context):
        p = context.scene.fx_cae
        L = self.layout
        groups = [g for g in p.groups if g.name != "_untagged"]
        if groups:
            box = L.box()
            box.label(text="Face groups - click to add:", icon="GROUP_VERTEX")
            on = {}
            for b in p.bcs:
                if b.enabled:
                    on.setdefault(b.group, []).append(b.type)
            for g in groups:
                row = box.row(align=True)
                used = on.get(g.name, [])
                icon = BC_ICON[used[0]] if used else "DOT"
                txt = g.name + (f"  ({g.area:.4g} mm²)" if g.area >= 0 else "")
                row.label(text=txt, icon=icon)
                for t in ("fixed", "force", "pressure"):
                    op = row.operator("blendfea.bc_add", text="", icon=BC_ICON[t])
                    op.type, op.group = t, g.name
            if any(g.name == "_untagged" for g in p.groups):
                box.label(text="All other faces are free", icon="BLANK1")
        elif p.msh_path:
            _wrap(L, "The mesh has no tagged faces, so there is nothing to put supports or "
                     "loads on. In 'Mesh & Face Groups', assign faces to groups and "
                     "Generate Mesh again", icon="ERROR", alert=True)

        row = L.row()
        row.template_list("BLENDFEA_UL_bcs", "", p, "bcs", p, "bcs_index", rows=3)
        col = row.column(align=True)
        for t, _, _, icon, _ in BC_TYPES:
            col.operator("blendfea.bc_add", text="", icon=icon).type = t
        col.separator()
        col.operator("blendfea.bc_remove", text="", icon="REMOVE")

        if p.bcs and 0 <= p.bcs_index < len(p.bcs):
            bc = p.bcs[p.bcs_index]
            box = L.box()
            if p.groups:
                box.prop_search(bc, "group", p, "groups", text="Group")
            else:
                box.prop(bc, "group")
            box.prop(bc, "type", text="")
            if bc.type == "force":
                box.prop(bc, "force_dir")
                if bc.force_dir == "CUSTOM":
                    box.prop(bc, "force", text="")
                else:
                    box.prop(bc, "force_mag")
                box.label(text="Total force, spread evenly over the group", icon="INFO")
            elif bc.type == "pressure":
                box.prop(bc, "pressure")
                box.label(text="1 MPa = 1 N/mm². Positive pushes in", icon="INFO")
            elif bc.type == "displacement":
                for i in range(3):
                    r = box.row(align=True)
                    r.prop(bc, "disp_mask", index=i, text=f"Hold {'XYZ'[i]}", toggle=True)
                    sub = r.row()
                    sub.enabled = bc.disp_mask[i]
                    sub.prop(bc, "disp", index=i, text="mm")
                box.label(text="Unheld directions slide freely", icon="INFO")
        row = L.row(align=True)
        row.prop(p, "gravity")
        if p.gravity:
            row.prop(p, "gravity_vec", text="")


class BLENDFEA_PT_run(_Base, bpy.types.Panel):
    bl_label = "Run"
    bl_parent_id = "BLENDFEA_PT_main"

    def draw(self, context):
        p = context.scene.fx_cae
        L = self.layout
        probs = core.checklist(p, os.path.isfile(bpy.path.abspath(p.msh_path)))
        if probs:
            box = L.box()
            for pr in probs:
                _wrap(box, pr, icon="ERROR")
        _size_warning(L, p)
        if not bpy.app.online_access:
            _wrap(L, "Turn on Preferences > System > Network > Allow Online Access",
                  icon="ERROR", alert=True)

        if ops._conn["state"] == "ok":
            host = ops._conn.get("server", "").split("://")[-1]
            L.label(text=f"Server: {host}", icon="LINKED")
        row = L.row(align=True)
        row.scale_y = 1.5
        row.operator("blendfea.check", icon="HIDE_OFF")
        row.operator("blendfea.submit", icon="PLAY")
        if p.job_state in ("queued", "running"):
            row.operator("blendfea.cancel", text="", icon="CANCEL")

        if p.job_state:
            stage = p.job_stage if p.job_state == "running" and p.job_stage else ""
            kind = "check" if p.last_analysis == "mesh_check" else "solve"
            row = L.row(align=True)
            row.label(text=f"{kind}: {p.job_state}" + (f" · {stage}" if stage else ""),
                      icon=STATE_ICON.get(p.job_state, "INFO"))
            if p.job_id and p.job_state not in ("uploading",):
                row.label(text=p.job_id[-6:])
        if p.transfer:
            L.label(text=p.transfer, icon="SORTTIME")
        if p.job_error:
            box = L.box()
            _wrap(box, p.job_error, alert=True)
            r = box.row(align=True)
            r.operator("blendfea.open_log", icon="TEXT")
            if p.job_state == "error" and p.job_id:
                r.operator("blendfea.load_job", text="Retry status", icon="FILE_REFRESH").job_id = p.job_id
        elif p.job_state in ("queued", "running") and not ops.busy():
            # e.g. the .blend was reopened while the job ran
            L.operator("blendfea.load_job", text="Resume status updates", icon="FILE_REFRESH").job_id = p.job_id

        L.prop(p, "show_advanced", text="Solver options", emboss=False,
               icon="DISCLOSURE_TRI_DOWN" if p.show_advanced else "DISCLOSURE_TRI_RIGHT")
        if p.show_advanced:
            col = L.column(align=True)
            col.prop(p, "element_order", text="")
            col.prop(p, "solver", text="")
            col.prop(p, "np")


class BLENDFEA_PT_results(_Base, bpy.types.Panel):
    bl_label = "Results"
    bl_parent_id = "BLENDFEA_PT_main"

    @classmethod
    def poll(cls, context):
        return _Base.poll.__func__(cls, context) and context.scene.fx_cae.job_state == "done"

    def draw(self, context):
        p = context.scene.fx_cae
        L = self.layout
        d = _json(p.job_summary, {})
        s, warns = d.get("summary") or {}, d.get("warnings") or []
        if p.last_analysis == "mesh_check":
            _wrap(L, "BC check: blue = supports, red = loads, grey = free. "
                     "Check the size above is right, then Solve.", icon="HIDE_OFF")
        if s:
            box = L.box()
            v = s.get("verdict", "")
            r = box.row()
            r.alert = v == "FAIL"
            r.label(text=v, icon=VERDICT_ICON.get(v, "INFO"))
            if s.get("headline"):
                _wrap(box, s["headline"], 46)

        if s or p.last_analysis == "mesh_check":
            self._legend(L, p, s)
        for w in warns:
            _wrap(L, w, 46, icon="ERROR")

        res = [o for o in ops._result_objects() if o.name in context.view_layer.objects]
        newest = max(res, key=lambda o: o["fea_job"]) if res else None
        if newest is not None and newest.data.shape_keys and "Deformed" in newest.data.shape_keys.key_blocks:
            col = L.column(align=True)
            col.prop(p, "deform_scale")
            row = col.row(align=True)
            for label, v in (("Off", 0.0), ("True", 1.0),
                             (f"Auto ×{newest.get('fea_auto_scale', 1.0):g}",
                              float(newest.get("fea_auto_scale", 1.0)))):
                op = row.operator("wm.context_set_float", text=label, depress=p.deform_scale == v)
                op.data_path, op.value = "scene.fx_cae.deform_scale", v
            umax = s.get("max_displacement_mm")
            if isinstance(umax, (int, float)):
                shown = (f"shown ×{p.deform_scale:g}" if p.deform_scale > 0 else "not shown")
                L.label(text=f"Max displacement {umax:.3g} mm ({shown})", icon="INFO")
        if bpy.data.objects.get(p.source_object):
            L.operator("blendfea.toggle_original", icon="HIDE_OFF")

        row = L.row(align=True)
        row.operator("blendfea.open_report", icon="TEXT")
        row.operator("blendfea.open_paraview", icon="VIEW3D")
        row.operator("blendfea.open_folder", text="", icon="FILE_FOLDER")
        row = L.row(align=True)
        row.operator("blendfea.download", icon="IMPORT")
        row.operator("blendfea.import_results", icon="MESH_DATA")
        L.operator("blendfea.keep", text="Kept on server" if p.job_keep else "Keep on server",
                   icon="SOLO_ON" if p.job_keep else "SOLO_OFF", depress=p.job_keep)

    def _legend(self, L, p, s):
        if p.last_analysis != "mesh_check":
            row = L.row(align=True)
            for f, lab in (("sf", "Safety"), ("von_mises", "Stress"),
                           ("disp_magnitude", "Displ."), ("bc", "BCs")):
                row.operator("blendfea.color_by", text=lab, depress=p.color_field == f).field = f
        field = "bc" if p.last_analysis == "mesh_check" else p.color_field
        col = L.column(align=True)
        if field == "sf":
            t = s.get("target_safety_factor") or 2.0
            items = [(RED, "below 1: fails"), (ORANGE, f"1 - {t:g}: below target"),
                     (YELLOW, f"about {t:g}: at target"), (GREEN, f"{2 * t:g}+: comfortable")]
        elif field == "von_mises":
            vm = s.get("max_von_mises_excl_supports_MPa") or s.get("max_von_mises_MPa") or 0
            items = [(BLUE, "0 MPa"), (GREEN, f"{vm / 2:.3g}"),
                     (RED, f"{vm:.3g} MPa and above")]
        elif field == "disp_magnitude":
            u = s.get("max_displacement_mm") or 0
            items = [(BLUE, "0 mm"), (GREEN, f"{u / 2:.3g}"),
                     (RED, f"{u:.3g} mm (max)")]
        else:
            items = [(BLUE, "support"), (RED, "load"),
                     (GREY, "free")]
        for icon, text in items:
            col.label(text=text, icon=icon)


class BLENDFEA_PT_jobs(_Base, bpy.types.Panel):
    bl_label = "Job history"
    bl_parent_id = "BLENDFEA_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        p = context.scene.fx_cae
        L = self.layout
        L.operator("blendfea.fetch_jobs", icon="FILE_REFRESH")
        for j in _json(p.jobs_cache, []):
            s = j.get("summary") or {}
            row = L.row(align=True)
            row.label(text=f"{j.get('name', '')[:18]}" + (" ★" if j.get("keep") else ""),
                      icon=STATE_ICON.get(j["state"], "INFO"))
            sf = s.get("min_safety_factor_excl_supports")
            row.label(text=(f"SF {sf:.2f}" if isinstance(sf, (int, float))
                            else ("check" if j.get("analysis") == "mesh_check" else j["state"])))
            op = row.operator("blendfea.load_job", text="", icon="IMPORT")
            op.job_id = j["id"]


classes = (BLENDFEA_UL_bcs, BLENDFEA_PT_main, BLENDFEA_PT_material, BLENDFEA_PT_bcs, BLENDFEA_PT_run, BLENDFEA_PT_results, BLENDFEA_PT_jobs)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)

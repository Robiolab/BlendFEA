"""Operators: mesh/groups, BC list, submit/poll (background thread), results."""

import json
import os
import shutil
import subprocess
import sys
import threading
import webbrowser

import bpy
import numpy as np

from . import core, fxclient
from .props import ADDON_ID

# Shared with the network thread. Only timers touch bpy data.
_job = {"lock": threading.Lock(), "thread": None, "status": None, "error": None,
        "downloaded": None, "scene": None, "imported": False}
_materials: dict = {}   # cached from the server: name -> properties
# Connection state shown in the panel: unknown / checking / ok / auth / unreachable / error
_conn = {"state": "unknown", "msg": "", "thread": None, "server": ""}


def prefs(context=None):
    return (context or bpy.context).preferences.addons[ADDON_ID].preferences


def client(context=None):
    pr = prefs(context)
    return fxclient.Client(pr.server_url, pr.token, timeout=120)


def results_root(context=None):
    return bpy.path.abspath(prefs(context).results_dir) or os.path.expanduser("~/fea_results")


def busy():
    t = _job["thread"]
    return bool(t and t.is_alive())


def _redraw():
    wm = bpy.context.window_manager
    for win in (wm.windows if wm else []):
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


# ------------------------------------------------------------------ mesh & groups
def msh_changed(p, path):
    """Has the file been modified since it was last read? The time is kept as a string:
    a (32-bit) FloatProperty rounds a Unix timestamp to ~2 minutes."""
    try:
        return repr(os.path.getmtime(path)) != p.msh_mtime
    except OSError:
        return False


def scan_groups(p):
    """(Re)read surface group names and size from p.msh_path.

    Areas, size and mass from the last server job are kept only while the file is
    the same one; a new or regenerated mesh makes them stale."""
    path = bpy.path.abspath(p.msh_path)
    if not os.path.isfile(path):
        p.groups.clear()
        p.msh_bbox = p.mesh_info = ""
        return []
    mtime = os.path.getmtime(path)
    changed = path != p.msh_scanned_path or msh_changed(p, path)
    names = [n for d, n in core.read_msh_groups(path) if d == 2]
    areas = {} if changed else {g.name: g.area for g in p.groups}
    p.groups.clear()
    for n in names:
        g = p.groups.add()
        g.name = n
        g.area = areas.get(n, -1.0)
    if changed or not p.msh_bbox:
        p.mesh_info = "" if changed else p.mesh_info
        bbox = core.read_msh_bbox(path)
        p.msh_bbox = json.dumps(bbox) if bbox else ""
    p.msh_scanned_path = path
    p.msh_mtime = repr(mtime)
    if not p.job_name:
        p.job_name = os.path.splitext(os.path.basename(path))[0]
    return names


def on_mesh_generated(scene, path):
    """Called by step 3 (Generate Mesh) when a new .msh is ready: use it for the job."""
    p = scene.fx_cae
    if not p.follow_blendgmsh or not os.path.isfile(path):
        return
    if bpy.path.abspath(p.msh_path) != path:
        p.msh_path = path          # update callback re-reads groups and size
    else:
        scan_groups(p)
    _redraw()


class BLENDFEA_OT_scan_groups(bpy.types.Operator):
    bl_idname = "blendfea.scan_groups"
    bl_label = "Re-read groups"
    bl_description = "Read the face group names from the .msh file again"

    def execute(self, context):
        p = context.scene.fx_cae
        if not os.path.isfile(bpy.path.abspath(p.msh_path)):
            self.report({"ERROR"}, f"Mesh file not found: {p.msh_path}")
            return {"CANCELLED"}
        names = scan_groups(p)
        self.report({"INFO"}, f"{len(names)} face groups: {', '.join(names)}")
        return {"FINISHED"}


class BLENDFEA_OT_bc_add(bpy.types.Operator):
    bl_idname = "blendfea.bc_add"
    bl_label = "Add support / load"
    bl_description = "Add a support or load"
    type: bpy.props.StringProperty(default="fixed")
    group: bpy.props.StringProperty(default="")

    @classmethod
    def description(cls, context, props):
        what = {"fixed": "a fixed support", "displacement": "a displacement / roller support",
                "force": "a force", "pressure": "a pressure"}.get(props.type, props.type)
        return f"Add {what}" + (f" on '{props.group}'" if props.group else "")

    def execute(self, context):
        p = context.scene.fx_cae
        bc = p.bcs.add()
        bc.type = self.type
        if self.group:
            bc.group = self.group
        else:
            used = {b.group for b in p.bcs}
            free = [g.name for g in p.groups if g.name not in used and g.name != "_untagged"]
            bc.group = free[0] if free else ""
        p.bcs_index = len(p.bcs) - 1
        return {"FINISHED"}


class BLENDFEA_OT_bc_remove(bpy.types.Operator):
    bl_idname = "blendfea.bc_remove"
    bl_label = "Remove"
    bl_description = "Remove the selected support / load"

    @classmethod
    def poll(cls, context):
        return len(context.scene.fx_cae.bcs) > 0

    def execute(self, context):
        p = context.scene.fx_cae
        p.bcs.remove(p.bcs_index)
        p.bcs_index = max(0, min(p.bcs_index, len(p.bcs) - 1))
        return {"FINISHED"}


def check_connection(context=None):
    """Check server + token in the background; the panel shows the outcome."""
    t = _conn["thread"]
    if t and t.is_alive():
        return
    try:
        cl = client(context)
    except Exception:   # add-on not fully registered yet
        return
    _conn.update(state="checking", msg=f"Checking {cl.server} ...", server=cl.server)

    def work():
        kind, msg, _ = cl.check()
        if kind == "ok":
            try:
                _materials.update(cl.materials())
            except Exception:
                pass
        _conn.update(state=kind, msg=msg)

    _conn["thread"] = threading.Thread(target=work, daemon=True)
    _conn["thread"].start()

    def redraw():
        if _conn["thread"].is_alive():
            return 0.3
        _redraw()
        return None
    bpy.app.timers.register(redraw, first_interval=0.3)


def _initial_check():
    check_connection()
    return None


class BLENDFEA_OT_check_server(bpy.types.Operator):
    bl_idname = "blendfea.check_server"
    bl_label = "Test connection"
    bl_description = "Check that the FEniCSx server answers and accepts the API token"

    def execute(self, context):
        kind, msg, _ = client(context).check()
        _conn.update(state=kind, msg=msg, server=client(context).server)
        if kind == "ok":
            try:
                _materials.update(client(context).materials())
            except fxclient.FxError:
                pass
            self.report({"INFO"}, msg)
            return {"FINISHED"}
        self.report({"ERROR"}, msg)
        return {"CANCELLED"}


class BLENDFEA_OT_open_prefs(bpy.types.Operator):
    bl_idname = "blendfea.open_prefs"
    bl_label = "Settings"
    bl_description = "Open the add-on preferences (server URL, API token, folders)"

    def execute(self, context):
        bpy.ops.screen.userpref_show()
        context.preferences.active_section = "ADDONS"
        bpy.ops.preferences.addon_show(module=ADDON_ID)
        return {"FINISHED"}


# ------------------------------------------------------------------ jobs (thread + timer)
def _worker(cl, out_root, auto_download, spec=None, mesh_path=None, job_id=None):
    def put(**kw):
        with _job["lock"]:
            _job.update(kw)
    try:
        if not _materials:
            try:
                _materials.update(cl.materials())
            except Exception:
                pass
        def prog(label):
            return lambda done, total: put(transfer=(
                f"{label} {done / 1e6:.1f}" + (f" / {total / 1e6:.1f} MB" if total else " MB")))
        if spec is not None:
            st = cl.submit(spec, mesh_path, progress=prog("Uploading"))
            put(transfer="")
            job_id = st["id"]
            put(status=st)
        st = cl.wait(job_id, poll=1.5, callback=lambda s: put(status=s))
        if st["state"] == "done" and auto_download:
            out = os.path.join(out_root, st["id"])
            if not os.path.isfile(os.path.join(out, "result.json")):
                cl.download(st["id"], out, progress=prog("Downloading results"))
            put(transfer="", downloaded=out)
    except fxclient.FxError as e:
        if e.code in (401, "unreachable", "ssl", 502, 503, 504, 530):
            _conn.update(state="auth" if e.code == 401 else "unreachable", msg=str(e))
        put(error=str(e), transfer="")
    except Exception as e:
        put(error=f"{type(e).__name__}: {e}", transfer="")


def _apply_mesh_info(p, mesh):
    if not mesh:
        return
    p.mesh_info = json.dumps(mesh)
    areas = mesh.get("group_areas_mm2") or {}
    for g in p.groups:
        if g.name in areas:
            g.area = areas[g.name]


def _timer():
    with _job["lock"]:
        st, err, dl = _job["status"], _job["error"], _job["downloaded"]
        transfer = _job.get("transfer", "")
    alive = busy()
    scene = bpy.data.scenes.get(_job["scene"] or "")
    if scene is None:
        return None
    p = scene.fx_cae
    p.transfer = transfer or ""
    if st:
        p.job_keep = bool(st.get("keep"))
        p.job_id = st["id"]
        p.job_state = st["state"]
        p.job_stage = (st.get("progress") or {}).get("stage", "")
        p.job_error = st.get("error") or ""
        p.last_analysis = st.get("analysis", p.last_analysis)
        p.job_summary = json.dumps({"summary": st.get("summary") or {},
                                    "warnings": st.get("warnings") or []})
        _apply_mesh_info(p, st.get("mesh"))
    if err:
        p.job_state = "error"
        p.job_error = err
    if dl and p.results_path != dl:
        p.results_path = dl
        if prefs().auto_import and not _job["imported"]:
            _job["imported"] = True
            try:
                if p.last_analysis == "mesh_check":
                    p.color_field = "bc"
                elif p.color_field == "bc":
                    p.color_field = "sf"
                import_results(bpy.context, dl, scene)
            except Exception as e:  # keep the timer alive
                p.job_error = f"Result import failed: {e}"
    _redraw()
    return 1.0 if alive else None


def _start(context, **kw):
    with _job["lock"]:
        _job.update(status=None, error=None, downloaded=None, scene=context.scene.name,
                    imported=False, transfer="")
    t = threading.Thread(target=_worker, daemon=True, kwargs=dict(
        cl=client(context), out_root=results_root(context),
        auto_download=prefs(context).auto_download, **kw))
    _job["thread"] = t
    t.start()
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=0.5)


class _SubmitBase:
    """Shared by Solve and Check (registered bpy classes must not subclass each other)."""
    ANALYSIS = "static"

    @classmethod
    def poll(cls, context):
        if busy():
            cls.poll_message_set("A job is still running")
            return False
        p = context.scene.fx_cae
        probs = core.checklist(p, os.path.isfile(bpy.path.abspath(p.msh_path)), cls.ANALYSIS)
        if probs:
            cls.poll_message_set(probs[0])
            return False
        return True

    def execute(self, context):
        p = context.scene.fx_cae
        path = bpy.path.abspath(p.msh_path)
        if msh_changed(p, path):
            scan_groups(p)  # the file changed under us
        probs = core.checklist(p, True, self.ANALYSIS)
        if probs:
            self.report({"ERROR"}, probs[0])
            return {"CANCELLED"}
        spec = core.build_spec(p, {"blender": bpy.app.version_string,
                                   "blend": bpy.data.filepath, "mesh": path}, self.ANALYSIS)
        ob = context.active_object
        if ob is not None and "fea_job" not in ob:
            p.source_object = ob.name
        p.job_state, p.job_stage, p.job_error, p.job_id, p.job_summary = "uploading", "", "", "", ""
        p.results_path, p.last_analysis = "", self.ANALYSIS
        _start(context, spec=spec, mesh_path=path)
        return {"FINISHED"}


class BLENDFEA_OT_submit(_SubmitBase, bpy.types.Operator):
    bl_idname = "blendfea.submit"
    bl_label = "Solve"
    bl_description = "Upload the mesh, supports and loads to the FEniCSx server and solve"
    ANALYSIS = "static"


class BLENDFEA_OT_check(_SubmitBase, bpy.types.Operator):
    bl_idname = "blendfea.check"
    bl_label = "Check BCs"
    bl_description = ("Quick check without solving (a few seconds): shows supports in blue and "
                      "loads in red on the part, plus group areas, part size and mass. "
                      "Catches unit and tagging mistakes before a long solve")
    ANALYSIS = "mesh_check"


class BLENDFEA_OT_load_job(bpy.types.Operator):
    bl_idname = "blendfea.load_job"
    bl_label = "Load job"
    bl_description = "Follow this job (resumes status updates) and load its results when done"
    job_id: bpy.props.StringProperty(default="")

    @classmethod
    def poll(cls, context):
        return not busy()

    def execute(self, context):
        p = context.scene.fx_cae
        jid = self.job_id or p.job_id
        if not jid:
            return {"CANCELLED"}
        p.job_id, p.job_state, p.job_error, p.results_path = jid, "loading", "", ""
        _start(context, job_id=jid)
        return {"FINISHED"}


class BLENDFEA_OT_fetch_jobs(bpy.types.Operator):
    bl_idname = "blendfea.fetch_jobs"
    bl_label = "Refresh job list"

    def execute(self, context):
        try:
            jobs = client(context).list()[:12]
        except fxclient.FxError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        context.scene.fx_cae.jobs_cache = json.dumps(jobs)
        return {"FINISHED"}


class BLENDFEA_OT_keep(bpy.types.Operator):
    bl_idname = "blendfea.keep"
    bl_label = "Keep on server"
    bl_description = ("Finished jobs are deleted from the server after a while (or when it runs "
                      "short of space). Keep exempts this job. Downloaded copies are never deleted")

    @classmethod
    def poll(cls, context):
        return context.scene.fx_cae.job_state in ("done", "failed")

    def execute(self, context):
        p = context.scene.fx_cae
        try:
            st = client(context).keep(p.job_id, not p.job_keep)
        except fxclient.FxError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        p.job_keep = bool(st.get("keep"))
        return {"FINISHED"}


class BLENDFEA_OT_cancel(bpy.types.Operator):
    bl_idname = "blendfea.cancel"
    bl_label = "Cancel job"
    bl_description = "Stop the running job on the server"

    @classmethod
    def poll(cls, context):
        return context.scene.fx_cae.job_state in ("queued", "running")

    def execute(self, context):
        try:
            client(context).cancel(context.scene.fx_cae.job_id)
        except fxclient.FxError as e:
            self.report({"ERROR"}, str(e))
        return {"FINISHED"}


class BLENDFEA_OT_download(bpy.types.Operator):
    bl_idname = "blendfea.download"
    bl_label = "Download"
    bl_description = "Download the result files again"

    @classmethod
    def poll(cls, context):
        return context.scene.fx_cae.job_state == "done"

    def execute(self, context):
        p = context.scene.fx_cae
        out = os.path.join(results_root(context), p.job_id)
        try:
            client(context).download(p.job_id, out)
        except fxclient.FxError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        p.results_path = out
        self.report({"INFO"}, f"Results in {out}")
        return {"FINISHED"}


def auto_deform_scale(x):
    """Round an exaggeration factor down to a 1-2-5 step (6.9 -> 5, 340 -> 200), min 1."""
    import math
    if not x > 1.0:
        return 1.0
    e = 10.0 ** math.floor(math.log10(x))
    m = x / e
    return (5.0 if m >= 5 else 2.0 if m >= 2 else 1.0) * e


def apply_deformation(obj, factor):
    """Show obj (an FEA result) deformed by `factor` x the real displacement."""
    keys = obj.data.shape_keys
    if keys is None or "Deformed" not in keys.key_blocks or "Undeformed" not in keys.key_blocks:
        return
    me, kb = obj.data, keys.key_blocks
    n = len(me.vertices)
    base = np.empty(n * 3, np.float32)
    kb["Undeformed"].data.foreach_get("co", base)
    a = me.attributes.get("fea_disp")
    if a is None:
        # Imported by an older version: the key holds base + disp * fea_deform_scale
        cur = np.empty(n * 3, np.float32)
        kb["Deformed"].data.foreach_get("co", cur)
        disp = (cur - base) / float(obj.get("fea_deform_scale", 1.0) or 1.0)
        a = me.attributes.new("fea_disp", "FLOAT_VECTOR", "POINT")
        a.data.foreach_set("vector", disp)
        if "fea_auto_scale" not in obj:
            obj["fea_auto_scale"] = auto_deform_scale(float(obj.get("fea_deform_scale", 1.0)))
    else:
        disp = np.empty(n * 3, np.float32)
        a.data.foreach_get("vector", disp)
    kb["Deformed"].data.foreach_set("co", base + disp * np.float32(factor))
    kb["Deformed"].slider_max = 1.0
    kb["Deformed"].value = 1.0 if factor > 0 else 0.0
    obj["fea_deform_scale"] = float(factor)
    me.update()


def deform_scale_changed(p, context):
    for o in _result_objects():
        try:
            apply_deformation(o, p.deform_scale)
        except Exception as e:   # never break the property update
            print("[BlendFEA] deformation:", e)


def _has_results(context):
    rp = context.scene.fx_cae.results_path
    return bool(rp) and os.path.isdir(rp)


class BLENDFEA_OT_open_report(bpy.types.Operator):
    bl_idname = "blendfea.open_report"
    bl_label = "Report"
    bl_description = "Open the HTML report in the web browser (downloads the results first if needed)"

    @classmethod
    def poll(cls, context):
        return context.scene.fx_cae.job_state == "done"

    def execute(self, context):
        p = context.scene.fx_cae
        local = os.path.join(p.results_path, "report.html") if p.results_path else ""
        if not (local and os.path.isfile(local)):
            # The server needs a sign-in for browsers; the local copy does not.
            out = os.path.join(results_root(context), p.job_id)
            try:
                client(context).download(p.job_id, out)
            except fxclient.FxError as e:
                self.report({"ERROR"}, str(e))
                return {"CANCELLED"}
            p.results_path = out
            local = os.path.join(out, "report.html")
        webbrowser.open("file:///" + os.path.abspath(local).replace("\\", "/").lstrip("/"))
        return {"FINISHED"}


class BLENDFEA_OT_open_log(bpy.types.Operator):
    bl_idname = "blendfea.open_log"
    bl_label = "Solver log"
    bl_description = "Fetch the solver log into a Blender text block (see the Text Editor)"

    @classmethod
    def poll(cls, context):
        return bool(context.scene.fx_cae.job_id)

    def execute(self, context):
        jid = context.scene.fx_cae.job_id
        try:
            text = client(context).log(jid)
        except fxclient.FxError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        name = f"fea_log_{jid[-6:]}"
        tb = bpy.data.texts.get(name) or bpy.data.texts.new(name)
        tb.clear()
        tb.write(text or "(empty log)")
        shown = False
        for area in context.screen.areas if context.screen else []:
            if area.type == "TEXT_EDITOR":
                area.spaces.active.text = tb
                shown = True
        self.report({"INFO"}, f"Log in text block '{name}'" + ("" if shown else " - open a Text Editor to read it"))
        return {"FINISHED"}


def _version_key(path):
    """Sort key so 'ParaView 5.13.0' beats 'ParaView 5.9.1'."""
    import re
    m = re.search(r"paraview[-_ ]?v?(\d+(?:\.\d+)*)", path, re.IGNORECASE)
    return [int(n) for n in m.group(1).split(".")] if m else []


_paraview_cache = {}


def find_paraview(configured="", refresh=False):
    """The configured ParaView executable if it exists, else the newest install found
    on PATH or in the usual install locations (cached; `refresh` searches again).
    None if nothing is found."""
    if configured:
        exe = bpy.path.abspath(configured)
        if os.path.isfile(exe):
            return exe
    cached = _paraview_cache.get("auto", "")
    if refresh or cached == "" or (cached and not os.path.isfile(cached)):
        _paraview_cache["auto"] = _search_paraview()
    return _paraview_cache["auto"]


def _search_paraview():
    import glob
    exe = shutil.which("paraview")
    if exe:
        return exe
    if sys.platform == "win32":
        roots = {os.environ.get(v) for v in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")}
        roots.add(r"C:\Program Files")
        patterns = [os.path.join(r, "ParaView*", "bin", "paraview.exe") for r in roots if r]
        patterns.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "ParaView*",
                                     "bin", "paraview.exe"))
    elif sys.platform == "darwin":
        patterns = ["/Applications/ParaView*.app/Contents/MacOS/paraview",
                    os.path.expanduser("~/Applications/ParaView*.app/Contents/MacOS/paraview")]
    else:
        patterns = ["/opt/ParaView*/bin/paraview", "/opt/paraview*/bin/paraview",
                    os.path.expanduser("~/ParaView*/bin/paraview"),
                    "/var/lib/flatpak/exports/bin/org.paraview.ParaView"]
    found = {f for pat in patterns for f in glob.glob(pat) if os.path.isfile(f)}
    return max(found, key=_version_key) if found else None


class BLENDFEA_OT_open_paraview(bpy.types.Operator):
    bl_idname = "blendfea.open_paraview"
    bl_label = "ParaView"
    bl_description = "Open results.vtu in ParaView"

    @classmethod
    def poll(cls, context):
        return _has_results(context)

    def execute(self, context):
        p = context.scene.fx_cae
        exe = find_paraview(prefs(context).paraview_path)
        if not exe:
            exe = find_paraview(refresh=True)   # maybe installed since the last search
        if not exe:
            self.report({"ERROR"}, "ParaView not found; set its executable in the add-on preferences")
            return {"CANCELLED"}
        f = os.path.join(p.results_path, "results.vtu")
        if not os.path.isfile(f):
            f = os.path.join(p.results_path, "mesh.vtu")
        kw = {"creationflags": 0x00000008} if sys.platform == "win32" else {"start_new_session": True}
        subprocess.Popen([exe, f], **kw)
        return {"FINISHED"}


class BLENDFEA_OT_open_folder(bpy.types.Operator):
    bl_idname = "blendfea.open_folder"
    bl_label = "Folder"
    bl_description = "Open the folder with the downloaded result files"

    @classmethod
    def poll(cls, context):
        return _has_results(context)

    def execute(self, context):
        bpy.ops.wm.path_open(filepath=context.scene.fx_cae.results_path)
        return {"FINISHED"}


# ------------------------------------------------------------------ results in Blender
def _set_colors(me, name, rgb):
    rgba = np.ones((len(rgb), 4), np.float32)
    rgba[:, :3] = core.srgb_to_linear(rgb)
    attr = me.color_attributes.get(name) or me.color_attributes.new(name, "FLOAT_COLOR", "POINT")
    attr.data.foreach_set("color", rgba.ravel())


def _result_objects():
    return [o for o in bpy.data.objects if "fea_job" in o]


def import_results(context, folder, scene=None):
    scene = scene or context.scene
    p = scene.fx_cae
    d = np.load(os.path.join(folder, "surface_results.npz"))
    pts, tris = d["points"], d["triangles"]
    job = os.path.basename(os.path.normpath(folder))
    summ = {}
    try:
        with open(os.path.join(folder, "result.json")) as fh:
            summ = json.load(fh).get("summary") or {}
    except OSError:
        pass
    kind = "BCs" if "sf" not in d else "FEA"
    name = f"{kind} {p.job_name or 'job'} {job[-6:]}"
    me = bpy.data.meshes.new(name)
    me.from_pydata(pts.tolist(), [], tris.tolist())
    me.update()

    target = summ.get("target_safety_factor") or 2.0
    if "sf" in d:
        _set_colors(me, "sf", core.sf_colors(d["sf"], target))
    if "von_mises" in d:
        _set_colors(me, "von_mises", core.seq_colors(d["von_mises"],
                                                     summ.get("max_von_mises_excl_supports_MPa")))
    if "disp_magnitude" in d:
        _set_colors(me, "disp_magnitude", core.seq_colors(d["disp_magnitude"]))
    bcp = np.zeros(len(pts), int)   # per-triangle BC id spread to points
    np.maximum.at(bcp, tris.ravel(), np.repeat(d["bc"], 3))
    _set_colors(me, "bc", core.bc_colors(bcp))
    for k in ("sf", "von_mises", "disp_magnitude", "s1", "s3", "layer_normal", "layer_shear"):
        if k in d:
            a = me.attributes.new(f"fea_{k}", "FLOAT", "POINT")
            a.data.foreach_set("value", d[k].astype(np.float32))
    active = p.color_field if p.color_field in me.color_attributes else "bc"
    me.color_attributes.active_color = me.color_attributes[active]

    obj = bpy.data.objects.new(name, me)
    coll = bpy.data.collections.get("FEA Results") or bpy.data.collections.new("FEA Results")
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)
    coll.objects.link(obj)

    if "displacement" in d:   # deformed shape: "Deformed" key = undeformed + factor * disp
        disp_units = d["displacement"] / float(d["unit_factor"])
        diag = float(np.linalg.norm(np.ptp(pts, axis=0))) or 1.0
        umax = float(np.max(np.linalg.norm(disp_units, axis=1))) or 1.0
        a = me.attributes.new("fea_disp", "FLOAT_VECTOR", "POINT")
        a.data.foreach_set("vector", disp_units.astype(np.float32).ravel())
        obj.shape_key_add(name="Undeformed")
        obj.shape_key_add(name="Deformed")
        # Auto: largest displacement shows as ~8% of the part size, rounded to 1-2-5
        obj["fea_auto_scale"] = auto_deform_scale(0.08 * diag / umax)
        p.deform_scale = obj["fea_auto_scale"]
        apply_deformation(obj, p.deform_scale)

    me.materials.append(bpy.data.materials.get("FEA_result") or _make_material())
    obj["fea_job"] = job
    obj["fea_summary"] = json.dumps(summ)

    # Only the newest result is visible; the original part is hidden underneath
    # (it would z-fight with the result surface). "Show original" toggles it.
    for o in _result_objects():
        if o is not obj and o.name in context.view_layer.objects:
            o.hide_set(True)
    src = bpy.data.objects.get(p.source_object)
    if src and src.name in context.view_layer.objects:
        src.hide_set(True)
    _shade_vertex_colors()
    return obj


def _make_material():
    mat = bpy.data.materials.new("FEA_result")
    try:
        mat.use_nodes = True
    except Exception:
        pass
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    attr = nt.nodes.new("ShaderNodeVertexColor")  # uses the active color attribute
    attr.location = (-300, 200)
    if bsdf:
        nt.links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = 0.8
    return mat


def _shade_vertex_colors():
    wm = bpy.context.window_manager
    for win in (wm.windows if wm else []):
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                for sp in area.spaces:
                    if sp.type == "VIEW_3D" and sp.shading.type == "SOLID":
                        sp.shading.color_type = "VERTEX"


class BLENDFEA_OT_import_results(bpy.types.Operator):
    bl_idname = "blendfea.import_results"
    bl_label = "Import again"
    bl_description = "Add the result surface again (colored, with a 'Deformed' shape key)"

    @classmethod
    def poll(cls, context):
        rp = context.scene.fx_cae.results_path
        return bool(rp) and os.path.isfile(os.path.join(rp, "surface_results.npz"))

    def execute(self, context):
        obj = import_results(context, context.scene.fx_cae.results_path)
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {"FINISHED"}


class BLENDFEA_OT_color_by(bpy.types.Operator):
    bl_idname = "blendfea.color_by"
    bl_label = "Color results by"
    bl_description = "Choose which result the colors show"
    field: bpy.props.StringProperty()

    def execute(self, context):
        context.scene.fx_cae.color_field = self.field
        for o in _result_objects():
            ca = o.data.color_attributes
            if self.field in ca:
                ca.active_color = ca[self.field]
                o.data.update()
        _shade_vertex_colors()
        return {"FINISHED"}


class BLENDFEA_OT_toggle_original(bpy.types.Operator):
    bl_idname = "blendfea.toggle_original"
    bl_label = "Show original"
    bl_description = "Switch between the original part and the result surface"

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.objects.get(context.scene.fx_cae.source_object))

    def execute(self, context):
        src = bpy.data.objects.get(context.scene.fx_cae.source_object)
        show_src = src.hide_get()
        src.hide_set(not show_src)
        res = [o for o in _result_objects() if o.name in context.view_layer.objects]
        if res:
            newest = max(res, key=lambda o: o["fea_job"])
            newest.hide_set(show_src)
        return {"FINISHED"}


classes = (BLENDFEA_OT_scan_groups, BLENDFEA_OT_bc_add, BLENDFEA_OT_bc_remove, BLENDFEA_OT_check_server, BLENDFEA_OT_open_prefs, BLENDFEA_OT_keep,
           BLENDFEA_OT_submit, BLENDFEA_OT_check, BLENDFEA_OT_load_job, BLENDFEA_OT_fetch_jobs, BLENDFEA_OT_cancel, BLENDFEA_OT_download,
           BLENDFEA_OT_open_report, BLENDFEA_OT_open_log, BLENDFEA_OT_open_paraview, BLENDFEA_OT_open_folder,
           BLENDFEA_OT_import_results, BLENDFEA_OT_color_by, BLENDFEA_OT_toggle_original)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.app.timers.register(_initial_check, first_interval=1.5)


def unregister():
    for t in (_timer, _initial_check):
        if bpy.app.timers.is_registered(t):
            bpy.app.timers.unregister(t)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)

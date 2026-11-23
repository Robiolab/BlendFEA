"""bpy-free helpers (unit-testable outside Blender)."""

from __future__ import annotations

import re

import numpy as np

try:
    from . import fxclient
except ImportError:          # imported as a plain module (tests)
    import fxclient


def read_msh_groups(path: str) -> list[tuple[int, str]]:
    """Physical names from a Gmsh .msh ($PhysicalNames is ASCII even in binary files)."""
    out = []
    with open(path, "rb") as fh:
        inside = False
        for raw in fh:
            line = raw.strip()
            if line == b"$PhysicalNames":
                inside = True
                next(fh)  # count
                continue
            if line == b"$EndPhysicalNames":
                break
            if inside:
                m = re.match(rb'(\d+)\s+(\d+)\s+"(.*)"', line)
                if m:
                    out.append((int(m.group(1)), m.group(3).decode(errors="replace")))
            elif line.startswith(b"$Nodes"):
                break  # no names section before the mesh data
    return out


UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}


def read_msh_bbox(path: str) -> list[float] | None:
    """Bounding-box size [dx, dy, dz] of an ASCII Gmsh .msh (v2 or v4), in the file's
    own units. None for binary files or anything unreadable."""
    try:
        with open(path, "rb") as fh:
            version, binary = None, False
            for raw in fh:
                line = raw.strip()
                if line == b"$MeshFormat":
                    fmt = next(fh).split()
                    version, binary = fmt[0].decode(), fmt[1] != b"0"
                    if binary:
                        return None
                elif line == b"$Nodes":
                    break
            else:
                return None
            body = []
            for raw in fh:
                if raw.startswith(b"$EndNodes"):
                    break
                body.append(raw)
    except (OSError, StopIteration, IndexError):
        return None
    try:
        if version and version.startswith("4"):
            # v4: header, then per entity block: "dim tag parametric n", n tag lines, n coord lines
            n_blocks = int(body[0].split()[0])
            coords, i = [], 1
            for _ in range(n_blocks):
                n = int(body[i].split()[3])
                i += 1 + n                      # skip the block header and the node tags
                coords.extend(r.split()[:3] for r in body[i:i + n])
                i += n
        else:
            coords = [r.split()[1:4] for r in body[1:]]   # v2: "tag x y z"
        if not coords:
            return None
        pts = np.asarray(coords, dtype=float)
        return (pts.max(axis=0) - pts.min(axis=0)).tolist()
    except (ValueError, IndexError):
        return None


def suggest_units(bbox_file_units, current: str) -> str | None:
    """A unit that makes the part a plausible 1 mm .. 5 m across, when the current one
    does not (e.g. a Blender model in metres read as mm). None if current is fine."""
    diag = float(np.linalg.norm(bbox_file_units))
    if diag <= 0:
        return None
    if 1.0 <= diag * UNIT_TO_MM[current] <= 5000.0:
        return None
    for u in ("m", "mm", "cm", "in"):
        if 1.0 <= diag * UNIT_TO_MM[u] <= 5000.0:
            return u
    return None


FORCE_DIRS = {"NEG_Z": (0, 0, -1), "POS_Z": (0, 0, 1), "NEG_X": (-1, 0, 0),
              "POS_X": (1, 0, 0), "NEG_Y": (0, -1, 0), "POS_Y": (0, 1, 0)}
DIR_LABEL = {"NEG_Z": "-Z", "POS_Z": "+Z", "NEG_X": "-X", "POS_X": "+X", "NEG_Y": "-Y",
             "POS_Y": "+Y", "NORMAL": "push", "CUSTOM": ""}


def force_value(bc):
    """Spec value for a force BC: vector [N], or scalar [N] for 'push on face'."""
    if bc.force_dir == "NORMAL":
        return float(bc.force_mag)
    if bc.force_dir == "CUSTOM":
        return [float(c) for c in bc.force]
    return [float(bc.force_mag) * c for c in FORCE_DIRS[bc.force_dir]]


def bc_label(bc) -> str:
    if bc.type == "force":
        if bc.force_dir == "CUSTOM":
            return "(" + ", ".join(f"{c:g}" for c in bc.force) + ") N"
        return f"{bc.force_mag:g} N {DIR_LABEL[bc.force_dir]}"
    if bc.type == "pressure":
        return f"{bc.pressure:g} MPa"
    if bc.type == "displacement":
        return " ".join(f"{'xyz'[i]}={bc.disp[i]:g}" if bc.disp_mask[i] else f"{'xyz'[i]} free"
                        for i in range(3))
    return "fixed"


def build_spec(p, client_info: dict | None = None, analysis: str | None = None) -> dict:
    """Job spec dict from an BlendFEAJobProps-like object (attribute access)."""
    axis = {"X": [1, 0, 0], "Y": [0, 1, 0], "Z": [0, 0, 1]}.get(p.build_axis)
    bd = axis or [float(c) for c in p.build_custom]
    bcs = []
    for bc in p.bcs:
        if not bc.enabled or not bc.group:
            continue
        if bc.type == "fixed":
            bcs.append({"group": bc.group, "type": "fixed"})
        elif bc.type == "displacement":
            val = [float(bc.disp[i]) if bc.disp_mask[i] else None for i in range(3)]
            bcs.append({"group": bc.group, "type": "displacement", "value": val})
        elif bc.type == "force":
            bcs.append({"group": bc.group, "type": "force", "value": force_value(bc)})
        elif bc.type == "pressure":
            bcs.append({"group": bc.group, "type": "pressure", "value": float(bc.pressure)})
    overrides = {}
    if abs(p.stiffness_scale - 1.0) > 1e-9:
        overrides["stiffness_scale"] = float(p.stiffness_scale)
    if abs(p.strength_scale - 1.0) > 1e-9:
        overrides["strength_scale"] = float(p.strength_scale)
    spec = {
        "api_version": fxclient.API_VERSION,
        "name": p.job_name or "blender job",
        "units": p.units,
        "analysis": analysis or p.analysis,
        "material": {"preset": p.material, "overrides": overrides},
        "build_direction": bd,
        "element_order": int(p.element_order),
        "bcs": bcs,
        "acceleration_g": [float(c) for c in p.gravity_vec] if p.gravity else None,
        "target_safety_factor": float(p.target_sf) if p.target_sf > 0 else None,
        "solver": {"type": p.solver, "np": int(p.np)},
        "client": client_info or {},
    }
    return spec


def validate_spec(spec: dict, mesh_groups: list[str] | None) -> list[str]:
    errs = []
    if spec["analysis"] == "static":
        if not any(b["type"] in ("fixed", "displacement") for b in spec["bcs"]):
            errs.append("Add at least one Fixed or Displacement support")
        if not any(b["type"] in ("force", "pressure") for b in spec["bcs"]) and not spec["acceleration_g"]:
            errs.append("No loads defined (force, pressure or self weight)")
    if mesh_groups is not None:
        for b in spec["bcs"]:
            if b["group"] not in mesh_groups:
                errs.append(f"Group '{b['group']}' is not in the mesh ({', '.join(mesh_groups)})")
    return errs


def checklist(p, mesh_ok: bool, analysis="static") -> list[str]:
    """Problems that block a submit (empty list = ready). Cheap: called from draw()."""
    probs = []
    if not mesh_ok:
        return ["No mesh yet - Generate Mesh in step 3 (or pick a tagged .msh file)"]
    groups = [g.name for g in p.groups]
    if not [g for g in groups if g != "_untagged"]:
        return ["No tagged face groups in the mesh - in step 2, select faces, "
                "'Assign Selected Faces' to a group, then Generate Mesh again"]
    active = [b for b in p.bcs if b.enabled]
    for b in active:
        if not b.group:
            probs.append(f"A {b.type} entry has no group")
        elif b.group not in groups:
            probs.append(f"Group '{b.group}' is not in this mesh")
    if analysis == "static":
        if not any(b.type in ("fixed", "displacement") for b in active):
            probs.append("Add a support (Fixed or Displacement)")
        if not any(b.type in ("force", "pressure") for b in active) and not p.gravity:
            probs.append("Add a load (Force, Pressure or Self weight)")
    return probs


# ---------------------------------------------------------------- colormaps
def _ramp(t, stops):
    t = np.clip(np.asarray(t, float), 0.0, 1.0)
    xs = np.array([s[0] for s in stops])
    cols = np.array([s[1] for s in stops], float)
    return np.stack([np.interp(t, xs, cols[:, i]) for i in range(3)], axis=1)


def sf_colors(sf, target):
    """Red below 1, orange to yellow up to target, green above 2*target."""
    sf = np.asarray(sf, float)
    t = np.where(sf < 1.0, 0.25 * sf,
                 np.where(sf < target, 0.25 + 0.35 * (sf - 1) / max(target - 1, 1e-9),
                          0.6 + 0.4 * (sf - target) / max(target, 1e-9)))
    stops = [(0.0, (0.55, 0.0, 0.0)), (0.249, (0.9, 0.05, 0.05)), (0.25, (0.95, 0.45, 0.05)),
             (0.6, (0.95, 0.85, 0.1)), (1.0, (0.1, 0.65, 0.2))]
    return _ramp(t, stops)


def seq_colors(v, vmax=None):
    v = np.asarray(v, float)
    vmax = vmax or (float(np.max(v)) if len(v) else 1.0) or 1.0
    stops = [(0.0, (0.05, 0.05, 0.45)), (0.25, (0.0, 0.45, 0.9)), (0.5, (0.1, 0.8, 0.4)),
             (0.75, (0.95, 0.8, 0.1)), (1.0, (0.85, 0.1, 0.05))]
    return _ramp(v / vmax, stops)


def bc_colors(bc):
    lut = np.array([(0.75, 0.75, 0.75), (0.15, 0.4, 0.9), (0.9, 0.2, 0.15)])
    return lut[np.clip(np.asarray(bc, int), 0, 2)]


def srgb_to_linear(c):
    c = np.asarray(c, float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)

"""Build BlendFEA extension packages, one per platform, with gmsh bundled.

    python scripts/build.py [--blender PATH] [--fxclient PATH/TO/FeniCSx-server]

Steps:
  1. Download the gmsh wheels (cached in build/wheels_src/).
  2. Repack each one for Blender (see repack_gmsh_wheel).
  3. Stage blendfea/ in build/stage/, add `platforms` and `wheels` to its manifest.
  4. `blender --command extension build --split-platforms` -> dist/blendfea-<ver>-<platform>.zip

Needs: Python 3.10+, pip, and Blender 5.0+ (found automatically or via --blender / $BLENDER).
"""
from __future__ import annotations

import argparse
import base64
import csv
import glob
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "blendfea")
BUILD = os.path.join(ROOT, "build")
DIST = os.path.join(ROOT, "dist")

GMSH_VERSION = "4.15.2"
LOCAL_TAG = "blendfea1"    # bump when the repacking changes
# Blender platform -> pip platform tag of a gmsh wheel. gmsh publishes no wheels for
# Windows ARM64 or Linux ARM64, so BlendFEA can't mesh there (x64 Blender under
# emulation works on Windows ARM).
PLATFORMS = {
    "windows-x64": "win_amd64",
    "linux-x64": "manylinux_2_24_x86_64",
    "macos-x64": "macosx_10_15_x86_64",
    "macos-arm64": "macosx_12_0_arm64",
}
# What gmsh.py loads (it looks next to itself first). Everything else in the wheel
# (Julia bindings, Windows import library, headers, CLI scripts) is left out.
NATIVE_LIB = re.compile(r"^gmsh-[\d.]+\.data/data/lib/(gmsh-[\d.]+\.dll|libgmsh\.[\d.]+\.dylib|libgmsh\.so\.[\d.]+)$")


def log(msg):
    print(f"[build] {msg}", flush=True)


def download_wheels():
    cache = os.path.join(BUILD, "wheels_src")
    os.makedirs(cache, exist_ok=True)
    out = {}
    for plat, tag in PLATFORMS.items():
        found = glob.glob(os.path.join(cache, f"gmsh-{GMSH_VERSION}-*-{tag}.whl"))
        if not found:
            log(f"downloading gmsh {GMSH_VERSION} for {plat}")
            subprocess.run([sys.executable, "-m", "pip", "download", "--quiet", "--no-deps",
                            "--only-binary=:all:", "--platform", tag, "--python-version", "3.13",
                            f"gmsh=={GMSH_VERSION}", "-d", cache], check=True)
            found = glob.glob(os.path.join(cache, f"gmsh-{GMSH_VERSION}-*-{tag}.whl"))
        if not found:
            raise SystemExit(f"no gmsh wheel for {plat} ({tag})")
        out[plat] = found[0]
    return out


def _record_line(path, data):
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return [path, f"sha256={digest}", str(len(data))]


def repack_gmsh_wheel(src, dst_dir):
    """Blender's wheel installer only remaps a wheel's purelib/platlib folders; gmsh keeps
    its native library under gmsh-X.data/data/lib/, which would stay there and never be
    found. Move the library next to gmsh.py (the first place gmsh.py looks) and drop
    what Blender doesn't need. The local version tag keeps this wheel apart from a stock
    gmsh wheel another extension might ship."""
    name = os.path.basename(src)
    m = re.match(r"gmsh-([^-]+)-(.+)\.whl$", name)
    version, tags = m.group(1), m.group(2)
    new_version = f"{version}+{LOCAL_TAG}"
    old_info, new_info = f"gmsh-{version}.dist-info", f"gmsh-{new_version}.dist-info"
    files = {}
    with zipfile.ZipFile(src) as zin:
        for info in zin.infolist():
            fn = info.filename
            if fn == "gmsh.py":
                files[fn] = zin.read(info)
            elif NATIVE_LIB.match(fn):
                files[fn.rsplit("/", 1)[1]] = zin.read(info)
            elif fn.startswith(old_info + "/") and fn.rsplit("/", 1)[1] in ("METADATA", "WHEEL", "top_level.txt"):
                data = zin.read(info)
                if fn.endswith("METADATA"):
                    data = re.sub(rb"^Version: .*$", f"Version: {new_version}".encode(), data, count=1, flags=re.M)
                files[new_info + "/" + fn.rsplit("/", 1)[1]] = data
    libs = [f for f in files if f not in ("gmsh.py",) and "/" not in f]
    if "gmsh.py" not in files or len(libs) != 1:
        raise SystemExit(f"unexpected gmsh wheel layout in {name}: {sorted(files)}")
    rows = [_record_line(p, d) for p, d in files.items()] + [[new_info + "/RECORD", "", ""]]
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    files[new_info + "/RECORD"] = buf.getvalue().encode()

    out = os.path.join(dst_dir, f"gmsh-{new_version}-{tags}.whl")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for path, data in files.items():
            zi = zipfile.ZipInfo(path, date_time=(2020, 1, 1, 0, 0, 0))
            zi.external_attr = (0o755 if path in libs else 0o644) << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(zi, data)
    log(f"repacked {name} -> {os.path.basename(out)} ({os.path.getsize(out) / 1e6:.0f} MB, lib {libs[0]})")
    return out


def sync_fxclient(server_repo):
    src = os.path.join(server_repo, "client", "fxclient.py")
    dst = os.path.join(SRC, "fea", "fxclient.py")
    shutil.copyfile(src, dst)
    log(f"fxclient.py synced from {src}")


def check_api():
    sys.path.insert(0, ROOT)
    from blendfea import SUPPORTED_SERVER_API
    from blendfea.fea import fxclient
    if fxclient.API_VERSION not in SUPPORTED_SERVER_API:
        raise SystemExit(f"fxclient API v{fxclient.API_VERSION} is not in SUPPORTED_SERVER_API "
                         f"{SUPPORTED_SERVER_API} (blendfea/__init__.py): test against that server, then add it")
    log(f"server API v{fxclient.API_VERSION} (supported: {SUPPORTED_SERVER_API})")


def stage(wheels):
    st = os.path.join(BUILD, "stage", "blendfea")
    if os.path.isdir(st):
        shutil.rmtree(st)
    shutil.copytree(SRC, st, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "wheels", "*.egg-info"))
    os.makedirs(os.path.join(st, "wheels"))
    names = []
    for plat in PLATFORMS:
        names.append(os.path.basename(shutil.copy(wheels[plat], os.path.join(st, "wheels"))))
    manifest = os.path.join(st, "blender_manifest.toml")
    text = open(manifest, encoding="utf-8").read()
    extra = ("platforms = [" + ", ".join(f'"{p}"' for p in PLATFORMS) + "]\n"
             "wheels = [\n" + "".join(f'  "./wheels/{n}",\n' for n in names) + "]\n")
    # top-level keys must come before the first [table]
    text = text.replace("\n[permissions]", "\n" + extra + "\n[permissions]", 1)
    open(manifest, "w", encoding="utf-8", newline="\n").write(text)
    return st


def find_blender(arg):
    for cand in (arg, os.environ.get("BLENDER"), shutil.which("blender")):
        if cand and os.path.isfile(cand):
            return cand
    pats = [r"C:\Program Files\Blender Foundation\Blender *\blender.exe",
            "/Applications/Blender.app/Contents/MacOS/Blender", "/opt/blender*/blender",
            os.path.expanduser("~/blender*/blender")]
    found = sorted((f for p in pats for f in glob.glob(p)), reverse=True)
    if not found:
        raise SystemExit("Blender not found: pass --blender PATH or set $BLENDER")
    return found[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--blender", help="Blender 5.0+ executable")
    ap.add_argument("--fxclient", metavar="SERVER_REPO", help="copy client/fxclient.py from this FeniCSx-server checkout first")
    args = ap.parse_args()

    if args.fxclient:
        sync_fxclient(args.fxclient)
    check_api()
    src_wheels = download_wheels()
    repacked_dir = os.path.join(BUILD, "wheels")
    os.makedirs(repacked_dir, exist_ok=True)
    wheels = {p: repack_gmsh_wheel(w, repacked_dir) for p, w in src_wheels.items()}
    st = stage(wheels)
    os.makedirs(DIST, exist_ok=True)
    blender = find_blender(args.blender)
    log(f"packaging with {blender}")
    subprocess.run([blender, "--factory-startup", "--command", "extension", "build",
                    "--split-platforms", "--source-dir", st, "--output-dir", DIST], check=True)
    for f in sorted(glob.glob(os.path.join(DIST, "blendfea-*.zip"))):
        log(f"{os.path.relpath(f, ROOT)}  {os.path.getsize(f) / 1e6:.0f} MB")


if __name__ == "__main__":
    main()

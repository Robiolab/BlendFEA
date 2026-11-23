"""Link blendfea/ into Blender for development, so edits take effect on add-on reload.

    python scripts/dev_link.py [--blender-version 5.1] [--remove]

Creates <Blender user extensions>/user_default/blendfea -> <repo>/blendfea (a junction on
Windows, which needs no admin rights; a symlink elsewhere). Then enable "BlendFEA" in
Preferences > Add-ons (Refresh first if it doesn't show).

A dev link uses the gmsh in Blender's own Python, not the bundled wheel (only built
packages carry it): install it once with
    <Blender>/<ver>/python/bin/python -m pip install gmsh==4.15.2
The VS Code "Blender Development" extension works too; this script just doesn't need it.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "blendfea")


def user_extensions_dir(version):
    if sys.platform == "win32":
        base = os.path.join(os.environ["APPDATA"], "Blender Foundation", "Blender")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support/Blender")
    else:
        base = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "blender")
    if os.environ.get("BLENDER_USER_RESOURCES"):
        return os.path.join(os.environ["BLENDER_USER_RESOURCES"], "extensions", "user_default")
    if version is None:
        versions = sorted((v for v in os.listdir(base) if v[:1].isdigit()),
                          key=lambda v: [int(x) for x in v.split(".") if x.isdigit()]) if os.path.isdir(base) else []
        if not versions:
            raise SystemExit(f"No Blender config found in {base}; pass --blender-version")
        version = versions[-1]
    return os.path.join(base, version, "extensions", "user_default")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--blender-version", help="e.g. 5.1 (default: newest found)")
    ap.add_argument("--remove", action="store_true", help="remove the link")
    args = ap.parse_args()

    dst = os.path.join(user_extensions_dir(args.blender_version), "blendfea")
    is_link = os.path.islink(dst) or (sys.platform == "win32" and os.path.isdir(dst)
                                      and os.path.realpath(dst) != os.path.abspath(dst))
    if args.remove:
        if not is_link:
            raise SystemExit(f"{dst} is not a link; not touching it")
        os.rmdir(dst) if sys.platform == "win32" else os.unlink(dst)   # removes the link only
        print(f"removed link {dst}")
        return
    if os.path.lexists(dst):
        if is_link and os.path.realpath(dst) == os.path.realpath(SRC):
            print(f"already linked: {dst} -> {SRC}")
            return
        raise SystemExit(f"{dst} already exists (an installed BlendFEA?). Uninstall it in "
                         "Blender first, or use --remove if it is an old link.")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(SRC, dst)
    else:
        os.symlink(SRC, dst, target_is_directory=True)
    print(f"linked {dst} -> {SRC}")


if __name__ == "__main__":
    main()

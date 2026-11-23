# BlendFEA

A Blender extension that takes a part from CAD to FEA results in one sidebar tab
(**View3D > Sidebar > FEA**):

1. **Part**: import a STEP (whole CAD faces) or STL/OBJ, or use any mesh object
2. **Face groups**: click faces, assign them to named groups (`support`, `load`, ...)
3. **Volume mesh**: Gmsh tetrahedral mesh with one physical group per face group
4. **Material**, 5. **Supports & loads**, 6. **Run** on a [FeniCSx-server](https://fea.roboticos.co),
7. **Results**: coloured safety factor / stress / displacement, deformation ×, report, ParaView

It merges two earlier add-ons, BlendGmsh (steps 1-3) and FEniCSx CAE (steps 4-7).
The mesher also works on its own, without Blender or a server (see *Mesher without Blender*).

## Install

Requires **Blender 5.0 or newer**. Download the package for your platform and use
*Edit > Preferences > Get Extensions > ⌄ > Install from Disk*:

| Platform | Package |
|---|---|
| Windows x64 | `blendfea-<ver>-windows_x64.zip` |
| Linux x64 | `blendfea-<ver>-linux_x64.zip` |
| macOS Apple Silicon | `blendfea-<ver>-macos_arm64.zip` |
| macOS Intel | `blendfea-<ver>-macos_x64.zip` |

Gmsh comes inside the package; nothing is pip-installed. Then, in the add-on's preferences,
enter the **API token** of your server. The server URL defaults to `https://fea.roboticos.co`
and ParaView is found automatically. Turn on *Preferences > System > Network > Allow Online Access*.

**Linux:** gmsh needs a few desktop libraries that minimal installs may lack:

    sudo apt install libglu1-mesa libxcursor1 libxft2 libxinerama1 libgomp1     # Debian/Ubuntu
    sudo dnf install mesa-libGLU libXcursor libXft libXinerama libgomp           # Fedora

**Windows on ARM:** gmsh publishes no ARM64 build, so use the x64 Blender (it runs under
emulation) with the Windows x64 package.

### Upgrading from BlendGmsh / FEniCSx CAE

Install BlendFEA, then click **Disable old add-ons** in the warning the FEA panels show.
That disables the old ones (they stay installed; remove them whenever you like) and copies
your server URL and API token over. Existing .blend files keep their face groups,
supports, loads, material and job state: BlendFEA reads the same scene data.

Meshes are now saved in a `fea/` folder next to the .blend (unsaved files: the temp folder)
instead of always in the temp folder.

## Mesher without Blender

    pip install .                 # package "blendfea-mesher": gmsh + numpy
    blendfea-mesh run groups.json part.step part.msh
    blendfea-mesh inspect part.msh

`groups.json` follows `blendfea/lib/matching_library/schema/bc_groups_v1.json` (the add-on's
*More > Export Groups to JSON* writes one). `pip install .[validate]` adds schema validation.

## Development

    python scripts/dev_link.py            # link blendfea/ into Blender's user extensions
    python scripts/dev_link.py --remove

Then enable *BlendFEA* in Preferences > Add-ons and reload it after edits. A dev link uses
the gmsh in Blender's own Python (packages bundle their own), so install it once:
`<Blender>/<ver>/python/bin/python -m pip install gmsh==4.15.2`.
The VS Code *Blender Development* extension works as well.

### Layout

```
blendfea/                 the extension (exactly what ships, plus gmsh wheels when built)
  __init__.py             registers compat, mesh, fea; SUPPORTED_SERVER_API
  compat.py               clash check and one-click switch from the old add-ons
  mesh/                   steps 1-3  (scene data: scene.fembc_main, fembc_groups)
  fea/                    steps 4-7  (scene data: scene.fx_cae); core.py is bpy-free
  fea/fxclient.py         server client, synced from FeniCSx-server (see below)
  lib/matching_library/   the mesher, run as `python -m matching_library` in a subprocess
  lib/step_converter/     STEP tessellation for the face picker
tests/lib, tests/core     pytest (needs gmsh + numpy; no Blender)
tests/blender/            end-to-end tests run inside Blender
scripts/                  build.py, dev_link.py
examples/                 box.step, cylinder.step, make_cantilever.py
```

### Tests

    pip install gmsh==4.15.2 numpy pytest jsonschema
    pytest                                              # tests/lib + tests/core

    # end to end, in a background Blender with an empty profile:
    BLENDER_USER_RESOURCES=/tmp/empty blender -b --factory-startup --python-exit-code 1 \
        --python tests/blender/run_workflow.py -- dist/blendfea-0.4.0-windows_x64.zip
    # (or `-- linked` after scripts/dev_link.py)

    # upgrade path: old add-on packages installed first
    BLENDER_USER_RESOURCES=/tmp/empty2 blender -b --factory-startup --python-exit-code 1 \
        --python tests/blender/run_clash.py -- dist/blendfea-...zip blendgmsh.zip fenicsx_cae-0.1.0.zip

`--python-exit-code 1` matters: without it Blender exits 0 even when the script crashes.

### Build

    python scripts/build.py [--blender PATH] [--fxclient ../FeniCSx-server]

Downloads the gmsh wheels (cached in `build/`), repacks them for Blender, stages
`blendfea/` and runs `blender --command extension build --split-platforms`, giving one
package per platform in `dist/`.

The repack is needed because Blender's wheel installer only unpacks a wheel's
`purelib`/`platlib` folders. Stock gmsh wheels keep their native library under
`gmsh-X.data/data/lib/`, where `gmsh.py` would never find it. The repacked wheel
(`gmsh-4.15.2+blendfea1`) has the library next to `gmsh.py` and leaves out what Blender
doesn't need.

### Server contract

`FeniCSx-server/client/fxclient.py` is the source of truth for the client. `--fxclient`
copies it in before building. The build stops if its `API_VERSION` isn't in
`SUPPORTED_SERVER_API` (`blendfea/__init__.py`). Test against that server, then add
the version. At runtime, *Test connection* warns about a mismatch.

## Status

Tested on Windows x64 (Blender 5.1): unit tests; install into an empty profile in a
Blender whose Python has no gmsh/scipy; STEP → groups → mesh → job panel → results;
upgrade from the old add-ons; old .blend files; dev link. **Linux and macOS packages are
built but not yet tested on those systems.**

## License

MIT

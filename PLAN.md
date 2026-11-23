# BlendFEA: merge plan

One Blender extension that takes a part from CAD to FEA results:
**part → face groups → volume mesh → material → supports & loads → solve → results**.

It merges two add-ons:

| Today | Repo | Becomes |
|---|---|---|
| BlendGmsh (tagging + meshing UI) | `C:\git\BlendGmsh\blender_addon` | `blendfea/` steps 1-3 |
| `matching_library`, `step_converter` | `C:\git\BlendGmsh` | `blendfea/lib/` (also a pip package) |
| FEniCSx CAE (jobs + results UI) | `C:\git\FeniCSx-server\blender\fenicsx_cae` | `blendfea/` steps 4-7 |
| `fxclient.py` | `C:\git\FeniCSx-server\client` | vendored copy, server repo stays the source |

Status (2026-11-23): phases 1-3 done, phase 4 open (see *Progress* at the end).
Decisions: Blender 5.0+, scipy replaced by numpy, BlendGmsh repo archived by hand,
local only for now (no remote), all platforms Blender supports where gmsh has wheels.

---

## 1. Goals and non-goals

Goals
- One install, one version, one sidebar tab. No more mismatched pairs, like the April
  BlendGmsh zip next to the VS Code link.
- Steps hand data to each other directly. No polling of `%TEMP%` or module-name lookups
  between add-ons.
- No pip at install time: dependencies ship as wheels inside per-platform packages.
- The mesher stays usable without Blender (CLI / pip package) and without a server:
  BlendFEA must also work as "just make me a tagged .msh".

Non-goals (for this migration)
- No solver or server changes. The FEniCSx server keeps its own repo, API and release cycle.
- No visual redesign beyond merging the panels. The UX work from 2026-11-23 carries over as is.

---

## 2. Repository layout

```
BlendFEA/
  blendfea/                       <- the Blender extension (exactly what ships)
    blender_manifest.toml         id = "blendfea", wheels = [...], permissions: files + network
    __init__.py                   register order, conflict check, migrations, handlers
    prefs.py                      server URL, API token, results folder, ParaView path
    props.py                      scene state (keeps the old property names, see section 5)
    core.py                       bpy-free helpers (today's fenicsx_cae/core.py), unit-tested
    fxclient.py                   vendored from FeniCSx-server (section 4), committed
    ops/
      geometry.py                 STEP / STL / OBJ import, native objects
      groups.py                   add/remove/assign/unassign/select, colours, rename sync
      mesh.py                     Generate Mesh subprocess, log, group check
      job.py                      check/solve/cancel/poll, server connection
      results.py                  import, colour fields, deformation, ParaView/report
    ui/
      panels.py                   one tab "FEA": 1 Part, 2 Face groups, 3 Volume mesh,
                                  4 Material, 5 Supports & loads, 6 Run, 7 Results, History
    lib/                          plain top-level packages, run as `python -m matching_library`
      matching_library/
      step_converter/
    schema/bc_groups_v1.json
    wheels/                       filled by the build script, not committed
  tests/
    lib/                          today's BlendGmsh pytest suite (needs gmsh)
    core/                         bpy-free tests (test_blender_core from the server repo)
    blender/                      headless Blender tests: ux, stale-size, deformation
                                  (the scripts used on 2026-11-23), run via blender -b
  scripts/
    build.py                      wheels + manifest + `blender --command extension build --split-platforms`
    sync_fxclient.py              copy fxclient.py from a FeniCSx-server checkout, check API_VERSION
    dev_link.py                   link blendfea/ into Blender for development (replaces VS Code-only setup)
  visualization/                  optional pyvista tools (dev extra, not shipped)
  examples/                       box.step, cylinder.step, cantilever generator
  pyproject.toml                  pip package "blendfea-mesher" = blendfea/lib/* (+ CLI `blendfea-mesh`)
  README.md, LICENSE (MIT, same as both sources)
```

Why `lib/` lives inside the extension folder: the mesher runs in a subprocess
(`python -m matching_library`) because Gmsh needs the main thread. With the libraries in
one fixed place, the zip, a dev link and a source checkout all have the same layout.
The two-location `lib_dir()` workaround added for the VS Code link can then be removed.

---

## 3. Dependencies and packaging

What the code actually imports (outside tests):

| Package | Used for | Plan |
|---|---|---|
| numpy | everywhere | Blender ships it: no wheel |
| gmsh | meshing, STEP tessellation | bundle wheel (`py3-none-<platform>`, one per OS, Python-version independent) |
| scipy | one `KDTree` nearest-centroid lookup | **decide** (section 9): bundle, or replace with a small numpy grid search |
| jsonschema | validating `bc_groups.json` | make optional: validate when importable (CLI/tests), skip inside Blender |
| pyvista | `visualization/` | dev extra only, not shipped |

Build (`scripts/build.py`):
1. `pip download --only-binary=:all: --platform <tag> --python-version <ver> gmsh [scipy]`
   for win_amd64, manylinux x86_64, macOS arm64 and x86_64.
2. Write the `wheels = [...]` list into `blender_manifest.toml`.
3. Copy the latest `fxclient.py` (section 4).
4. `blender --command extension build --split-platforms --source-dir blendfea --output-dir dist`.
   This replaces `export.ps1`, whose `Compress-Archive` stores `\` paths and produces
   broken installs on Linux and macOS.

Code changes that come with wheels:
- Remove `_ensure_dependencies()`. It pip-installs into Blender's own Python
  (`C:\Program Files\...\site-packages`), which needs admin rights on a fresh machine and
  freezes Blender with no message.
- The mesher subprocess does not see the wheel folder Blender adds at runtime
  (`extensions/.local/lib/python3.x/site-packages`). Pass it in `PYTHONPATH` together with
  `lib/`.

Sizes to expect: the gmsh wheel is tens of MB per platform. scipy adds about 30-40 MB per
platform and Python version. `--split-platforms` keeps each download to one OS.

---

## 4. The client-server contract

Today the add-on lives in the server repo, so `fxclient.py` is a symlink and a test checks
that `API_VERSION` matches. After the split:

- `FeniCSx-server/client/fxclient.py` stays the **source of truth**. It also serves as the
  CLI, so it stays dependency-free.
- BlendFEA commits a copy (`blendfea/fxclient.py`) so dev links work, and
  `scripts/sync_fxclient.py` refreshes it from a server checkout or tag. The build fails if
  the copy's `API_VERSION` is not in a new `SUPPORTED_SERVER_API` list kept in
  `blendfea/__init__.py`. That list records which server versions this release was tested against.
- At runtime, "Test connection" already compares API versions. Make a mismatch visible in
  the connection box, not only in the message text.
- The job-spec format stays defined by `server/fxserver/spec.py`. BlendFEA's `core.build_spec`
  gets a test against a spec example exported from the server repo
  (`tests/core/spec_examples/*.json`, refreshed by the sync script).

---

## 5. State and settings migration

Scene data in existing .blend files:
- **Keep the property names** `scene.fembc_main`, `scene.fembc_groups`,
  `scene.fembc_groups_active_index` and `scene.fx_cae`. They are invisible to users, and
  keeping them means existing files open with their groups, BCs and job state intact.
  Zero migration code.
- Operator ids (`fembc.*`, `fx_cae.*`) can be renamed to `blendfea.*`, because .blend files
  don't store them. Do it in phase 5, not during the move.

Add-on preferences (server URL, **API token**, results folder, ParaView path) are stored
per extension id, so `blendfea` starts empty:
- On first register, if the old `fenicsx_cae` add-on is installed, copy its preferences.
- Otherwise, show a one-time "enter your API token" box in the connection panel. The server
  URL already defaults to `https://fea.roboticos.co` and ParaView is found automatically,
  so the token is the only field.

Conflict with the old add-ons:
- Old BlendGmsh and BlendFEA would both register `Scene.fembc_main`. Before registering,
  check whether BlendGmsh or FEniCSx CAE is enabled. If so, register nothing but a panel
  that says "Disable BlendGmsh and FEniCSx CAE, then re-enable BlendFEA", with a button
  that does it.

---

## 6. What gets simpler after the merge

| Workaround today | After |
|---|---|
| FEA add-on polls `fembc_main.msh_output_path` every 2 s | Generate Mesh sets the job's mesh directly |
| `_blendgmsh_running()` searches `sys.modules` for a module with the right functions | direct call |
| `bl_order` to stack two add-ons' panels | one panel list, one order |
| `.msh` written to `%TEMP%\<name>.msh` (shared between projects, overwritten) | `//fea/<name>.msh` next to the .blend (temp only for unsaved files) |
| `lib_dir()` checks two folders | one fixed `lib/` |
| two zips, two versions | one package per platform, one version |

---

## 7. Phases

**Phase 0: land current work** (in the old repos, so history carries it)
- BlendGmsh: commit the uncommitted native-object WIP and the 2026-11-23 UX changes.
- FeniCSx-server: commit the add-on changes (ParaView lookup, default server,
  size/units, deformation factor, etc.).
- Tag both (`pre-blendfea`).

**Phase 1: create the repo, no behaviour change**
- `git init C:\git\BlendFEA`. Import history with `git filter-repo`: all of BlendGmsh, plus
  `blender/fenicsx_cae` and `tests/test_solver.py::test_blender_core` from FeniCSx-server.
  Then move files into the layout above.
- One `__init__.py` registers both halves. Extension id `blendfea`, version 0.4.0.
- Replace the cross-add-on workarounds in section 6.
- Done when: the three headless Blender test scripts pass against a dev link, and the
  Rolling Joint STEP goes import → tag → mesh → Check BCs against fea.roboticos.co.

**Phase 2: packaging**
- `scripts/build.py` with wheels and split platforms. Remove `_ensure_dependencies`.
  Fix the subprocess `PYTHONPATH`. Make jsonschema optional. Apply the scipy decision.
- Done when: a clean Blender profile with no add-ons and no pip-installed gmsh/scipy
  (`BLENDER_USER_RESOURCES` pointing at an empty folder) installs the zip and passes the
  test scripts on Windows. Linux checked in a container. macOS when a machine is available.

**Phase 3: migration UX**
- Conflict check, preferences import, README upgrade section.
- Done when: opening a .blend saved with the old add-ons keeps groups, BCs and job, and the
  token carries over.

**Phase 4: clean up the old repos**
- FeniCSx-server: delete `blender/`, `scripts/build_addon.py` and `dist/`. Point the README
  at BlendFEA. Keep `client/`.
- BlendGmsh: archive with a README pointer (or keep it as the standalone mesher, see
  section 9).

**Phase 5: polish** (optional, after a release)
- `blendfea.*` operator ids, one shared `_wrap`/layout helper, CI that downloads Blender
  and runs the headless tests.

---

## 8. Risks

- **gmsh wheel on Linux** needs some system libraries (GLU, X11 font and cursor libs) even
  headless. Check in phase 2 and list them in the README if needed.
- **macOS** wheels and Gatekeeper: untested here (Windows-only machine).
- **Download size** if scipy is kept for two Python versions.
- **Two add-ons enabled at once** during the upgrade: handled by the conflict check, but it
  is the most likely support issue.
- **History import:** `git filter-repo` is an extra tool to install. The alternative is
  `git subtree add`, which keeps history less cleanly.

---

## 9. Decisions needed

1. **Minimum Blender version.** 5.0+ means one Python (3.13), one set of wheels, smaller
   packages. 4.2+ means two Python versions (3.11 and 3.13) and twice the scipy wheels.
   Recommendation: **5.0+**. That is what you run, and extensions are new enough that
   4.2 support buys little.
2. **scipy.** Bundle it, or replace the one `KDTree` lookup with a numpy grid search
   (about 40 lines, tested against the current matcher). Recommendation: **replace**. It
   removes the biggest wheel and the only Python-version-specific dependency.
3. **BlendGmsh repo afterwards.** Archive it, or keep the mesher there as the standalone
   pip package and have BlendFEA depend on it. Recommendation: **archive**, with
   `blendfea-mesher` from the new repo as the pip package. One place to fix bugs.
4. **Hosting.** GitHub (public or private?) under the same account as roboticos_tunnel?
5. **Platforms for the first release.** Windows only, or Windows + Linux + macOS?

---

## Progress (2026-11-23)

Sources: BlendGmsh `f024591` + its uncommitted `lib_dir()` fix; FeniCSx-server `2baefb1`.
No history was imported (both repos had 2-3 initial commits); see those commits instead.

Done
- Phase 1: repo layout, one extension (`blendfea`, 0.4.0), direct mesh hand-off (no
  polling), meshes in `//fea/`, one fixed `lib/` folder.
- Phase 2: `scripts/build.py` (repacked gmsh wheels, `--split-platforms`), no pip at
  install, subprocess `PYTHONPATH`, jsonschema optional, scipy replaced (`spatial.py`).
- Phase 3: clash check + one-click switch with URL/token carry-over, old .blend files
  open unchanged, README.

Changes from the plan
- **Layout:** `blendfea/mesh/` and `blendfea/fea/` keep the two halves' modules as they
  were, instead of a split by step (`ops/`, `ui/`). That's less churn; the split can come
  in phase 5.
- **Operator and class names:** renamed now (`blendfea.*`, `BLENDFEA_*`), not in phase 5.
  With the old add-ons enabled during an upgrade, identical names would replace each
  other. Scene data names stay unchanged (old .blend files).
- **Clash handling:** BlendFEA always registers. Its panels show the warning and the fix
  button, and sub-panels hide, while an old add-on is enabled. That covers either load order.
- **fxclient sync:** `build.py --fxclient <server repo>` instead of a separate script.
- **Platforms:** windows-x64, linux-x64, macos-x64, macos-arm64. gmsh has no wheels for
  Windows ARM64 or Linux ARM64.
- **Schema:** moved to `matching_library/schema/` so the pip package carries it.
- **Removed `tests/lib/test_generate_mesh.py`:** it tested a copy of the old output-path
  logic, never the add-on, and failed on Windows. `tests/blender/run_workflow.py`
  checks the real behaviour.

Verified on Windows x64, Blender 5.1
- pytest: 102 passed, 1 skipped (pyvista), in an environment without scipy.
- Windows package in an empty profile, in a Blender whose Python has no gmsh/scipy/jsonschema:
  bundled gmsh loads in Blender and in the mesher subprocess; box.step → groups → mesh in
  `//fea/` → job panel → results.
- Upgrade: old BlendGmsh + FEniCSx CAE packages, then BlendFEA: warning, one-click
  switch, token and URL copied, scene data restored.
- A .blend saved with the old add-ons opens with groups, BCs, material, units intact.
- Dev link (`scripts/dev_link.py`) end to end.
- pip package `blendfea-mesher` installs and runs with gmsh + numpy only.

Open
- **Linux and macOS packages:** built, but not run on those systems. Linux needs the
  libraries in the README; the add-on now names them if gmsh fails to load.
- **Phase 4 (FeniCSx-server):** remove `blender/`, `scripts/build_addon.py` and `dist/`,
  and point the README at BlendFEA. Also, the `2baefb1` commit added `__pycache__` files
  and `dist/*.zip`; add a `.gitignore`.
- Phase 5 polish.

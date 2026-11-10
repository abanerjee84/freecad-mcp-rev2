# Changes on top of upstream v0.1.30

Three fixes, each found by running the thing rather than reading it. Everything else is
upstream `ec8abf2` untouched.

## 1. A screenshot could come back solid black and report success

`rpc_server/view_manager.py` reads whatever the 3D view last rendered. A view whose MDI
window has never been exposed — the first capture after FreeCAD starts with only the Start
page, or the first capture of a freshly created document — has rendered nothing, so
`saveImage()` writes a flat viewport and the tool still returns `True`. Nothing distinguishes
that from an empty scene.

`save_active_screenshot` now forces `view.redraw()` before the read-back, samples the PNG
through `QImage` (PySide ships with FreeCAD, so no new dependency), and redraws, re-frames
and re-shoots once if the frame comes back uniform. The Wayland `Framebuffer` read and the
FreeCAD < 1.0 fallback moved into `_capture_view_image` unchanged.

Honest caveat: the black capture was seen three times and never reproduced — a control run
with the untouched upstream file captured correctly in the same fresh-start state. The patch
detects the condition and recovers from it rather than assuming a cause. An empty scene also
reads uniform, which costs one extra redraw.

Tests: `tests/test_screenshot_redraw.py` — flat frame re-shot once, rendered frame shot once,
`redraw` ordered before the first read-back, views without `redraw` still capture.

## 2. `execute_code_headless` could not find FreeCAD on Windows

`detect_freecadcmd` looked at `PATH`, then Flatpak. The Windows installer ships
`bin\freecadcmd.exe` and never adds it to `PATH`, and the macOS bundle keeps it under
`Contents/bin`, so both reported "freecadcmd not found" against a working install unless you
passed `--freecadcmd`. Detection now also searches install directories — `Program Files`,
`Program Files (x86)`, `LOCALAPPDATA` on Windows; `/Applications`, `/opt`, `/usr/local`, `~`
on POSIX — newest directory first, with `FREECAD_DIR` to pin one. `PATH` still wins.

Verified here without the flag: headless returned exit 0 against `FreeCAD 1.1`.

Tests: `tests/test_headless.py` — install directory found when `PATH` is empty, newest wins,
`FREECAD_DIR` overrides, `PATH` beats an install directory.

## 3. Installing the addon was manual, and starting RPC was a click

`scripts/install_addon.py` finds FreeCAD's real Mod directory (the directory holding
`user.cfg`, which for a versioned install is `…/FreeCAD/v1-1/`), copies the addon so
`InitGui.py` sits directly inside `Mod/FreeCADMCP`, and writes `freecad_mcp_settings.json`
with `auto_start_rpc` — merging instead of overwriting so an auth token and allowed-IP list
survive. `--check` reports the installed versus checkout version; `--dest` handles a FreeCAD
the probe cannot find.

## Version

`addon/FreeCADMCP/package.xml` bumped to **0.1.31**, which is the single source for both the
addon and the server package, so `get_rpc_status` reports `version_check: ok` on this pair.
`PROTOCOL_VERSION` is unchanged at 1: the RPC contract did not move.

## Docs

`docs/tools.md` gained the real argument names (`doc_name`, `obj_type`, `obj_name`,
`obj_properties`, `execute_code_headless(code=…)`) because the prose names produce pydantic
validation errors. `docs/configuration.md` covers headless discovery and the flat-frame
behaviour; `docs/installation.md` covers the installer script.

## Checking it

```bash
uv sync --locked
uv run --locked pytest -q --basetemp=.ptemp     # 333 passed, 4 skipped
python scripts/install_addon.py --auto-start   # then restart FreeCAD
node scripts/smoke_test.mjs                    # live MCP calls against FreeCAD
```

`--basetemp` is needed on this machine: pytest's default temp base is permission-locked and
74 tests error with `WinError 5` before reaching any code.

`scripts/smoke_test.mjs` is the part pytest cannot cover — it drives the server over stdio
against the running GUI process: status, document, object, edit, get, view, `execute_code`,
`execute_code_headless`, cleanup. Screenshots land in `out/`. It exits non-zero if any call
comes back with `isError`. Budget 300s: `execute_code_headless` starts a second full FreeCAD.
It exits explicitly — a killed server leaves its stdio pipes open, which otherwise leaves
Node waiting and the run hanging after every result has printed.

## Local wiring on this machine

Addon installed at `%APPDATA%/FreeCAD/v1-1/Mod/FreeCADMCP`, RPC auto-starting on
`127.0.0.1:9875`. pi launches the fork from this checkout via
`uv --directory …/freecad-mcp run --locked freecad-mcp` in `~/.pi/agent/mcp.json`.

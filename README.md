# FreeCAD MCP (revised)

Control [FreeCAD](https://www.freecad.org) from an MCP client — Claude Desktop, pi, or any
Model Context Protocol host. Create and edit parametric models, run Python inside FreeCAD,
take screenshots of the viewport, inspect documents, run headless scripts, and run FEM
analyses with CalculiX.

This is a fork of [neka-nat/freecad-mcp](https://github.com/neka-nat/freecad-mcp) at
upstream commit `ec8abf2` (v0.1.30). It carries **three fixes found by running the thing
against a real FreeCAD on Windows**, plus an installer script and an end-to-end test
harness. Everything else is upstream, unchanged. See [FORK_NOTES.md](FORK_NOTES.md) for the
commit-by-commit detail.

Demos of the upstream design: [flange, toy car, modelling from a 2D drawing](docs/examples.md).

## What this fork changes

| | Upstream | Here |
| --- | --- | --- |
| First screenshot of a view that has never rendered | solid black frame, tool reports success | forced redraw, flat-frame detection, one re-shoot |
| `execute_code_headless` on a Windows/macOS GUI install | `freecadcmd not found` unless `--freecadcmd` passed | discovers the executable inside the install directory |
| Installing the addon | copy the folder by hand, then click **Start RPC Server** after every launch | `python scripts/install_addon.py --auto-start` |
| Tool argument names | undocumented; the prose names fail pydantic validation | listed in [docs/tools.md](docs/tools.md) |
| Live end-to-end test | none | `scripts/smoke_test.mjs`, exits non-zero on any failed call |
| Version | 0.1.30 | 0.1.31 (`addon/FreeCADMCP/package.xml`, the single source for addon and server) |

## Architecture

Two components, two Python environments:

- **The addon** runs inside FreeCAD's GUI process and serves XML-RPC on `127.0.0.1:9875`.
  Everything touching the document or the viewport is dispatched to the GUI thread.
- **The MCP server** is a stdio process your client launches. It translates tool calls into
  XML-RPC and returns text plus optional screenshots. It needs Python 3.12+; the addon runs
  on whatever FreeCAD bundles (1.1 ships 3.11). Do not install this package into FreeCAD's
  own Python.

```
MCP client ──stdio──> freecad-mcp server ──XML-RPC :9875──> FreeCAD GUI thread
                                    └──subprocess──> freecadcmd (headless)
```

## Requirements

- FreeCAD 1.0 or 1.1 (this fork was verified against FreeCAD 1.1 on Windows).
- [uv](https://docs.astral.sh/uv/guides/tools/) for the server, or any Python 3.12+.
- Node 18+ only for the smoke harness.

## Install

```bash
git clone <this repo> && cd freecad-mcp-rev2

# 1. Addon into FreeCAD's user Mod directory, RPC auto-starting
python scripts/install_addon.py --auto-start

# 2. Server environment
uv sync --locked

# 3. Restart FreeCAD. The RPC server comes up on 127.0.0.1:9875 by itself.
```

`install_addon.py` picks the Mod directory that actually holds `user.cfg` — for a versioned
install that is `%APPDATA%/FreeCAD/v1-1/Mod` on Windows,
`~/Library/Application Support/FreeCAD/v1-1/Mod` on macOS, `~/.local/share/FreeCAD/v1-1/Mod`
or `~/.FreeCAD/Mod` on Linux — and copies the addon so `InitGui.py` sits directly inside
`Mod/FreeCADMCP`, which is what FreeCAD's package loader requires. It merges
`freecad_mcp_settings.json` rather than overwriting it, so an auth token and an allowed-IP
list survive. `--check` compares what is installed with this checkout; `--dest` overrides the
directory.

Manual copy and platform-specific paths: [docs/installation.md](docs/installation.md).

### Connect a client

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "freecad": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/freecad-mcp-rev2", "run", "--locked", "freecad-mcp"]
    }
  }
}
```

pi (`~/.pi/agent/mcp.json`):

```json
{
  "freecad": {
    "type": "local",
    "command": ["uv", "--directory", "E:/path/to/freecad-mcp-rev2", "run", "--locked", "freecad-mcp"],
    "enabled": true,
    "callTimeout": 300000
  }
}
```

The long `callTimeout` matters: `execute_code_headless` starts a second full FreeCAD process
and takes 30-60s, and `run_fem_analysis` defaults to a 600s budget.

Restart the client after editing its config. Keep FreeCAD open.

### Flags

| Flag / env | Effect |
| --- | --- |
| `--only-text-feedback` | drop optional screenshots from every reply, far fewer tokens |
| `--host <addr>` | point at FreeCAD on another machine (see [remote connections](docs/configuration.md#remote-connections)) |
| `--auth-token <t>` / `FREECAD_MCP_TOKEN` | token the addon's server requires |
| `--freecadcmd <cmd>` | explicit headless FreeCAD command; overrides discovery |
| `FREECAD_DIR` | a FreeCAD install directory to search for `freecadcmd` |

## Tools

17 tools. Argument names exactly as declared — a wrong name is a pydantic validation error,
not something to re-guess. `*` marks required.

| Tool | Arguments |
| --- | --- |
| `create_document` | `*name` |
| `list_documents` | — |
| `reload_document` | `*doc_name` |
| `create_object` | `*doc_name`, `*obj_type`, `*obj_name`, `obj_properties`, `analysis_name`, `include_screenshot`, `view_name` |
| `edit_object` | `*doc_name`, `*obj_name`, `*obj_properties`, `include_screenshot`, `view_name` |
| `delete_object` | `*doc_name`, `*obj_name`, `include_screenshot`, `view_name` |
| `get_objects` | `*doc_name`, `include_screenshot`, `view_name` |
| `get_object` | `*doc_name`, `*obj_name`, `include_screenshot`, `view_name` |
| `get_view` | `*view_name`, `width`, `height`, `focus_object` |
| `execute_code` | `*code`, `include_screenshot`, `view_name`, `timeout` |
| `execute_code_async` | `*code` |
| `get_async_status` | `job_id` |
| `execute_code_headless` | `*code`, `timeout` |
| `get_rpc_status` | — |
| `insert_part_from_library` | `*relative_path`, `include_screenshot`, `view_name` |
| `get_parts_list` | — |
| `run_fem_analysis` | `*doc_name`, `*analysis_name`, `timeout`, `include_screenshot`, `view_name` |

`view_name` accepts `Isometric`, `Front`, `Top`, `Right`, `Back`, `Left`, `Bottom`,
`Dimetric`, `Trimetric`. Screenshots are on by default for the object tools; pass
`include_screenshot: false` for intermediate steps. Full behaviour, including FEM unit rules
and the async job lifecycle: [docs/tools.md](docs/tools.md) and
[docs/execution.md](docs/execution.md).

## Headless execution

`execute_code_headless` runs your script in a separate `freecadcmd` process, so an OpenCascade
segfault in a heavy sweep or boolean kills only the helper, not FreeCAD and its unsaved
documents. Discovery order: `PATH` → install directory (`Program Files\FreeCAD 1.1\bin\`,
`FreeCAD.app/Contents/bin/`, `/opt`, `/usr/local`, `~`) → Flatpak. The Windows installer ships
`bin\freecadcmd.exe` and never adds it to `PATH`, which is why upstream reported "freecadcmd
not found" against a working install. Budget 300s for the call.

## Checking it

```bash
uv sync --locked
uv run --locked pytest -q --basetemp=.ptemp     # 333 passed, 4 skipped
python scripts/install_addon.py --check         # installed == this checkout
node scripts/smoke_test.mjs                     # live calls against running FreeCAD
```

`--basetemp` is needed on Windows machines where pytest's default temp base is
permission-locked; without it the run errors with `WinError 5` before reaching any code.

`scripts/smoke_test.mjs` covers what pytest cannot: it launches the server over stdio and
drives the addon inside the GUI process — status, document, object, edit, get, viewport
screenshot, `execute_code`, `execute_code_headless`, cleanup. Screenshots land in `out/`, any
failed call makes it exit non-zero, and it prints `all calls succeeded`. Modes:

```bash
node scripts/smoke_test.mjs list
node scripts/smoke_test.mjs call get_view '{"view_name":"Top","focus_object":"Box"}'
node scripts/smoke_test.mjs code 'import FreeCAD; print(list(FreeCAD.listDocuments()))'
FREECAD_MCP_CMD="uvx freecad-mcp" node scripts/smoke_test.mjs   # test a different server build
```

## Security

The XML-RPC endpoint has **no authentication by default** and no encryption: anything that can
reach port 9875 can run arbitrary Python inside FreeCAD as your user. Keep it on loopback. If
you enable remote connections, set a token in the addon's **Set Auth Token** toolbar button
and give the server the same value through `FREECAD_MCP_TOKEN`, allow only specific IPs, and
prefer an SSH tunnel. The server also refuses requests that look like they came from a web
browser, so a page cannot drive it. Details: [docs/configuration.md](docs/configuration.md#remote-connections).

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Workbench "MCP Addon" missing after restart | wrong copy depth — `InitGui.py` must sit directly in `Mod/FreeCADMCP/`; check Report view |
| Port 9875 in use at startup | something else holds it; the addon releases the listener so you can retry |
| `EFTYPE` / `uv_spawn` on Windows | launch through `cmd`: `["cmd", "/c", "freecad-mcp"]`, absolute path to `freecad-mcp.exe` |
| Client connects but no model appears | FreeCAD is closed, or the RPC server was never started — `get_rpc_status` |
| `execute_code` blocks every later call | it runs on the GUI thread (90s default, 1800s max); use `execute_code_async` for long work |
| Screenshot comes back one flat colour | empty scene. A view that has never rendered now gets a forced redraw and one re-shoot |
| Version warning on first tool reply | addon and server differ; reinstall the addon from this checkout (`--check` compares) |

## Versioning

`addon/FreeCADMCP/package.xml` is the single version source for both the addon and the server
package. `PROTOCOL_VERSION` stays at 1 — the RPC contract did not move in this fork. Tag
`v0.1.31-fork` marks the fork tip; the base commit is upstream `ec8abf2` (v0.1.30).

## Licence and attribution

MIT, as upstream. The design, the RPC protocol, and all of the addon's dispatch, validation,
FEM and parts-library machinery are [neka-nat's](https://github.com/neka-nat/freecad-mcp/graphs/contributors);
this fork only adds the fixes and tooling listed above. Upstream is MIT © neka-nat.

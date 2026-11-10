"""Install the FreeCADMCP addon into FreeCAD's user addon directory.

Replaces "find your Mod directory, copy the folder, restart FreeCAD, click
Start RPC Server". FreeCAD loads an addon only when ``Mod/<name>/InitGui.py``
sits directly inside it, and the RPC server's auto-start is a plain JSON file
in the same app-data directory, so both can be written here.

    python scripts/install_addon.py                 # copy into the Mod directory
    python scripts/install_addon.py --auto-start     # and start RPC on every launch
    python scripts/install_addon.py --check          # report, change nothing
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "addon" / "FreeCADMCP"
SETTINGS = "freecad_mcp_settings.json"
_DEFAULT_SETTINGS = {
    "remote_enabled": False,
    "allowed_ips": "127.0.0.1",
    "auto_start_rpc": False,
    "auth_token": "",
}


def app_data_dirs() -> list[Path]:
    """FreeCAD user app-data directories, newest version first."""
    if os.name == "nt":
        bases = [Path(os.environ["APPDATA"]) / "FreeCAD"] if os.environ.get("APPDATA") else []
    elif sys.platform == "darwin":
        bases = [Path.home() / "Library/Application Support/FreeCAD"]
    else:
        bases = [
            Path.home() / ".FreeCAD",
            Path.home() / ".local/share/FreeCAD",
            Path.home() / ".var/app/org.freecad.FreeCAD/data/FreeCAD",
        ]

    found: list[Path] = []
    for base in bases:
        if not base.is_dir():
            continue
        # A versioned install keeps its config in v1-1/; the real Mod directory
        # is a sibling of user.cfg, not of the bare base.
        configs = [d.parent for d in sorted(base.glob("*/user.cfg"), reverse=True)]
        configs += [d.parent for d in sorted(base.glob("v*/user.cfg"), reverse=True)]
        for candidate in configs:
            if candidate not in found:
                found.append(candidate)
        if not found:
            found.append(base)
    return found


def mod_dir(create: bool) -> Path | None:
    """The Mod directory FreeCAD actually loads addons from."""
    for base in app_data_dirs():
        candidate = base / "Mod"
        if candidate.is_dir() or create:
            return candidate
    return None


def installed_version(mod: Path) -> str | None:
    metadata = mod / "FreeCADMCP/package.xml"
    if not metadata.is_file():
        return None
    text = metadata.read_text(encoding="utf-8")
    start, end = text.find("<version>"), text.find("</version>")
    return text[start + 9:end] if start != -1 and end > start else None


def source_version() -> str:
    text = (ADDON / "package.xml").read_text(encoding="utf-8")
    start, end = text.find("<version>"), text.find("</version>")
    return text[start + 9:end]


def write_settings(app_data: Path, auto_start: bool) -> Path:
    """Set auto-start without clobbering a token or an allowed-IP list."""
    path = app_data / SETTINGS
    settings = dict(_DEFAULT_SETTINGS)
    if path.is_file():
        try:
            settings.update(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError) as exc:
            print(f"warning: keeping defaults, {path} unreadable ({exc})", file=sys.stderr)
    settings["auto_start_rpc"] = auto_start
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--auto-start", action="store_true", help="start the RPC server when FreeCAD launches")
    parser.add_argument("--no-auto-start", dest="auto_start", action="store_false", help="require the toolbar button instead")
    parser.add_argument("--dest", type=Path, help="a Mod directory other than FreeCAD's own")
    parser.add_argument("--check", action="store_true", help="report the current install and exit")
    args = parser.parse_args()

    target = args.dest or mod_dir(create=not args.check)
    if target is None:
        print("No FreeCAD user directory found. Start FreeCAD once, or pass --dest.", file=sys.stderr)
        return 1

    if args.check:
        here = installed_version(target) if target.is_dir() else None
        print(f"addon directory: {target}")
        print(f"installed:       {here or 'nothing'}")
        print(f"this checkout:   {source_version()}")
        settings = target.parent / SETTINGS
        print(f"settings:        {settings if settings.is_file() else 'not written'}")
        return 0 if here == source_version() else 2

    if not (ADDON / "InitGui.py").is_file():
        print(f"missing addon source: {ADDON}", file=sys.stderr)
        return 1

    destination = target / "FreeCADMCP"
    if destination.is_dir():
        shutil.rmtree(destination)
    shutil.copytree(ADDON, destination)

    # FreeCAD's package loader looks for InitGui.py directly inside Mod/<name>.
    if not (destination / "InitGui.py").is_file():
        print(f"copy is nested wrong: {destination}/InitGui.py absent", file=sys.stderr)
        return 1

    settings_path = write_settings(target.parent, args.auto_start)
    print(f"installed FreeCADMCP {source_version()} -> {destination}")
    print(f"settings -> {settings_path} (auto_start_rpc={args.auto_start})")
    print("Restart FreeCAD for the addon to load.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

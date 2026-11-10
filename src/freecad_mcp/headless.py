"""Run a FreeCAD script in a separate ``freecadcmd`` process.

Heavy OCCT work (helical sweeps, lofts, many-tool booleans) can segfault
OpenCascade. Inside the GUI process that kills FreeCAD together with every
unsaved document; here it only kills the helper process, and the caller gets
the exit status and the script's output back.
"""

import logging
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("FreeCADMCPserver")

FLATPAK_APP = "org.freecad.FreeCAD"
_NOISE = ("%)", "Importing project files", "Postprocessing", "FreeCAD 1.", "(C) 2001", "LGPL")

# Set FREECAD_DIR to a FreeCAD install folder to override the search below.
FREECAD_DIR_ENV = "FREECAD_DIR"

# Root-relative globs, newest install first ("FreeCAD 1.2" sorts above
# "FreeCAD 1.1" and "FreeCAD 0.19" by name, which is the usual order).
_WIN_ROOTS_ENV = ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")
_WIN_PATTERNS = (
    "FreeCAD*/bin/freecadcmd.exe",
    "FreeCAD*/bin/FreeCADCmd.exe",
    "FreeCAD*/bin/freecad.cmd",
    "Programs/FreeCAD*/bin/freecadcmd.exe",
)
_POSIX_ROOTS = ("/Applications", "/opt", "/usr/local", "~")
_POSIX_PATTERNS = (
    "FreeCAD.app/Contents/bin/freecadcmd",
    "FreeCAD*/Contents/bin/freecadcmd",
    "FreeCAD*/Contents/MacOS/freecadcmd",
    "freecad*/bin/freecadcmd",
    "FreeCAD*/bin/freecadcmd",
)


def _install_roots() -> list[Path]:
    """Directories that plausibly hold a FreeCAD installation."""
    override = os.environ.get(FREECAD_DIR_ENV)
    if override:
        return [Path(override)]
    if os.name == "nt":
        return [Path(value) for value in (os.environ.get(name) for name in _WIN_ROOTS_ENV) if value]
    return [Path(entry).expanduser() for entry in _POSIX_ROOTS]


def _install_patterns() -> tuple[str, ...]:
    return _WIN_PATTERNS if os.name == "nt" else _POSIX_PATTERNS


def _bundled_freecadcmd() -> list[str] | None:
    """Find the executable inside a GUI install that never touches PATH.

    The Windows installer ships ``bin\freecadcmd.exe`` but does not add it to
    PATH, and the macOS bundle keeps it inside ``Contents/bin``, so PATH
    lookups miss both and ``execute_code_headless`` reported "freecadcmd not
    found" for a perfectly good install.
    """
    patterns = _install_patterns()
    for root in _install_roots():
        for pattern in patterns:
            try:
                matches = sorted(root.glob(pattern), key=lambda p: str(p), reverse=True)
            except (OSError, ValueError):
                continue
            for match in matches:
                if match.is_file():
                    return [str(match)]
    return None


def detect_freecadcmd() -> list[str] | None:
    """Find a headless FreeCAD executable: PATH, then an install directory, then Flatpak."""
    for name in ("freecadcmd", "FreeCADCmd", "freecadcmd.exe", "freecad.cmd"):
        path = shutil.which(name)
        if path:
            return [path]
    bundled = _bundled_freecadcmd()
    if bundled:
        return bundled
    flatpak = shutil.which("flatpak")
    if flatpak:
        try:
            probe = subprocess.run(
                [flatpak, "info", FLATPAK_APP], capture_output=True, timeout=30
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if probe.returncode == 0:
            return [flatpak, "run", f"--command=freecadcmd", FLATPAK_APP]
    return None


def parse_command(value: str | None) -> list[str] | None:
    if not value:
        return None
    # --freecadcmd can be an executable path already passed as one CLI argument.
    # Shell splitting would consume Windows backslashes and split its spaces.
    try:
        if Path(value).is_file():
            return [value]
    except OSError:
        # A command line can be an invalid/overlong filename on some platforms.
        pass
    return shlex.split(value)


def _script_dir() -> Path:
    # Flatpak sandboxes usually see $HOME but not /tmp, so keep scripts under HOME.
    d = Path.home() / ".cache" / "freecad-mcp" / "headless"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _clean(output: str | bytes | None) -> str:
    # TimeoutExpired retains captured bytes even when run(text=True) is used.
    # A timeout can also cut a multibyte character in half.
    if output is None:
        return ""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    lines = [ln for ln in output.splitlines() if ln.strip() and not any(n in ln for n in _NOISE)]
    return "\n".join(lines)


def run_headless(code: str, timeout: float, command: list[str] | None) -> dict[str, Any]:
    """Execute ``code`` with ``command -c "exec(open(script).read())"``.

    Returns ``success``, ``returncode``, ``output`` (stdout+stderr, noise
    filtered), and flags ``crashed`` (killed by a signal) / ``timed_out``.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        return {"success": False, "error": "timeout must be a positive finite number"}
    command = command or detect_freecadcmd()
    if not command:
        return {
            "success": False,
            "error": (
                "freecadcmd not found: install FreeCAD, set FREECAD_DIR to its install "
                "directory, or pass --freecadcmd to freecad-mcp"
            ),
        }
    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", dir=_script_dir(), delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        script = f.name
    # freecadcmd block-buffers stdout into a pipe and does not flush it when the
    # script raises, exits, aborts or is killed; PYTHONUNBUFFERED has no effect.
    # Line buffering keeps everything printed before such an exit.
    # The comprehension keeps the script's namespace free of helper names.
    unbuffer = (
        "[getattr(s, 'reconfigure', lambda **k: None)(line_buffering=True) "
        "for s in (__import__('sys').stdout, __import__('sys').stderr)]; "
    )
    argv = command + ["-c", unbuffer + f"exec(open({script!r}, encoding='utf-8').read())"]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as e:
        out = "\n".join(part for part in (_clean(e.stdout), _clean(e.stderr)) if part)
        return {
            "success": False,
            "timed_out": True,
            "error": f"headless FreeCAD did not finish within {timeout:g} s",
            "output": out,
        }
    except OSError as e:
        return {"success": False, "error": f"could not start headless FreeCAD: {e}"}
    finally:
        try:
            os.unlink(script)
        except OSError:
            pass
    output = _clean(proc.stdout + "\n" + proc.stderr)
    result: dict[str, Any] = {
        "success": proc.returncode == 0,
        "returncode": proc.returncode,
        "output": output,
    }
    if proc.returncode < 0:
        sig = -proc.returncode
        try:
            name = signal.Signals(sig).name
        except ValueError:
            name = str(sig)
        result["crashed"] = True
        result["error"] = f"headless FreeCAD crashed with {name} (OCCT native crash; the GUI is unaffected)"
    elif proc.returncode != 0:
        # FreeCAD's own signal handler can print a native backtrace and exit 1,
        # hiding the signal from the OS exit status. Label this as a report
        # from FreeCAD, distinct from an OS-observed negative return code.
        crash = re.search(r"(?m)^Program received signal (SIG[A-Z0-9]+),", output)
        if crash:
            result["crashed"] = True
            result["error"] = (
                f"headless FreeCAD reported a crash with {crash.group(1)} "
                f"(exit code {proc.returncode}; the GUI is unaffected)"
            )
        else:
            result["error"] = f"script failed (exit code {proc.returncode})"
    return result

"""A 3D view that has never rendered hands back a flat frame — re-shoot it."""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

VIEW_PATH = Path(__file__).resolve().parents[1] / "addon/FreeCADMCP/rpc_server/view_manager.py"


def _pyside(uniform: bool) -> types.ModuleType:
    """A PySide stub whose QImage reports a flat or a varied captured frame."""
    class QImage:
        def __init__(self, _path: str) -> None:
            pass

        def isNull(self) -> bool:
            return False

        def width(self) -> int:
            return 64

        def height(self) -> int:
            return 64

        def pixelColor(self, x: int, y: int):
            colour = (34, 34, 34) if uniform else (x * 3 % 251, y * 5 % 247, (x + y) % 239)
            return types.SimpleNamespace(
                red=lambda: colour[0], green=lambda: colour[1], blue=lambda: colour[2]
            )

    module = types.ModuleType("PySide")
    module.QtGui = types.SimpleNamespace(QImage=QImage)
    return module


def _view_module(monkeypatch: pytest.MonkeyPatch, view: object, uniform: bool) -> types.ModuleType:
    gui = types.SimpleNamespace(
        ActiveDocument=types.SimpleNamespace(ActiveView=view),
        activeView=Mock(return_value=view),
        Selection=Mock(),
        SendMsgToActiveView=Mock(),
        runCommand=Mock(),
    )
    monkeypatch.setitem(sys.modules, "FreeCADGui", gui)
    monkeypatch.setitem(sys.modules, "FreeCAD", types.SimpleNamespace(
        Console=types.SimpleNamespace(PrintWarning=Mock()), ActiveDocument=None,
    ))
    monkeypatch.setitem(sys.modules, "rpc_server.gui_dispatch", types.SimpleNamespace(_flush_gui_events=Mock()))
    monkeypatch.setitem(sys.modules, "PySide", _pyside(uniform))
    spec = importlib.util.spec_from_file_location("_redraw_test", VIEW_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _view(tmp_path: Path) -> Mock:
    def shoot(path: str, *_args: object) -> None:
        Path(path).write_bytes(b"png")

    view = Mock()
    view.saveImage.side_effect = shoot
    view.getSize.side_effect = AttributeError("no size")
    return view


def test_a_flat_frame_is_redrawn_and_captured_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    view = _view(tmp_path)
    result = _view_module(monkeypatch, view, uniform=True).save_active_screenshot(
        str(tmp_path / "shot.png"), "Isometric", 640, 480
    )
    assert result is True
    assert view.saveImage.call_count == 2
    assert view.redraw.call_count == 2


def test_a_rendered_frame_is_captured_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    view = _view(tmp_path)
    result = _view_module(monkeypatch, view, uniform=False).save_active_screenshot(
        str(tmp_path / "shot.png"), "Isometric", 640, 480
    )
    assert result is True
    assert view.saveImage.call_count == 1


def test_the_redraw_happens_before_the_first_read_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = _view(tmp_path)
    _view_module(monkeypatch, view, uniform=False).save_active_screenshot(
        str(tmp_path / "shot.png"), "Isometric", 640, 480
    )
    names = [call[0] for call in view.mock_calls]
    assert names.index("redraw") < names.index("saveImage")


def test_a_view_without_redraw_still_captures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`redraw` belongs to View3DInventorPy; other view types must not break."""
    shots: list[str] = []

    view = types.SimpleNamespace(
        viewIsometric=lambda: None,
        fitAll=lambda: None,
        getSize=lambda: (_ for _ in ()).throw(AttributeError("no size")),
        saveImage=lambda path, *_args: shots.append(path),
    )
    result = _view_module(monkeypatch, view, uniform=False).save_active_screenshot(
        str(tmp_path / "shot.png"), "Isometric", 640, 480
    )
    assert result is True
    assert len(shots) == 1

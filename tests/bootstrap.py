"""Test bootstrap: stub HA-dependent parent packages.

Import this before any ``custom_components.parceltracker.*`` module. The real
integration ``__init__.py`` imports Home Assistant, which is not installed in
the test environment; the pure modules are loaded from the stub package path.
"""

from __future__ import annotations

from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "parceltracker"


def _package(name: str, path: Path) -> None:
    if name in sys.modules:
        return
    module = types.ModuleType(name)
    module.__path__ = [str(path)]  # type: ignore[attr-defined]
    sys.modules[name] = module


_package("custom_components", ROOT / "custom_components")
_package("custom_components.parceltracker", COMPONENT)

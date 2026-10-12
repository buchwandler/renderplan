from __future__ import annotations

try:
    from ._version import __version__
except ImportError:  # source tree before setuptools-scm generated the module
    try:
        from importlib.metadata import version

        __version__ = version("renderplan")
    except Exception:  # pragma: no cover - local source tree fallback
        __version__ = "0.0.dev0"

__all__ = ["__version__"]

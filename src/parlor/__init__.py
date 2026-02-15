"""Personal AI Chat Web UI."""

from importlib.metadata import version as _v

try:
    __version__ = _v("parlor")
except Exception:
    __version__ = "0.0.0"

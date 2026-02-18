"""Cross-platform path resolution utilities.

On Windows, os.path.realpath() and Path.resolve() resolve mapped network drive
letters (e.g., X:\\) to their underlying UNC paths (e.g., \\\\server\\share).
In enterprise environments, the UNC path may be blocked by network policy even
though the mapped drive letter works fine.

This module provides safe_resolve() and safe_resolve_pathlib() which avoid UNC
resolution on Windows while still normalizing paths (collapsing .., making
absolute). On POSIX, behavior is unchanged — os.path.realpath() is used for
full symlink resolution.

SECURITY-REVIEW: On Windows, safe_resolve() uses os.path.abspath() which does
NOT resolve symlinks. This is acceptable because Windows symlinks require
SeCreateSymbolicLinkPrivilege (elevated permissions) and are rare in enterprise
environments. The security-critical property — collapsing '..' to prevent path
traversal — is preserved by os.path.abspath() on all platforms.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_IS_WINDOWS = sys.platform == "win32"


def safe_resolve(path: str) -> str:
    """Resolve a path to an absolute, normalized form.

    On Windows: uses os.path.abspath() to avoid resolving mapped drive letters
    to UNC paths. On POSIX: uses os.path.realpath() for full symlink resolution.
    """
    if _IS_WINDOWS:
        return os.path.normpath(os.path.abspath(path))
    return os.path.realpath(path)


def safe_resolve_pathlib(p: Path) -> Path:
    """Resolve a Path object using safe_resolve().

    Returns a Path with the same resolution behavior as safe_resolve().
    """
    return Path(safe_resolve(str(p)))

"""Git-based pack source cache management.

Manages cloning, pulling, and caching of git repositories that contain
pack definitions. All git operations shell out to the developer's ``git``
binary — no libgit2, no gitpython, no auth handling.

Cache layout::

    ~/.anteroom/cache/sources/
        <sha256(url)[:12]>/          # one directory per source repo
            .source_url              # contains the original URL
            .source_branch           # contains the branch name
            <repo contents>
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

CLONE_TIMEOUT = 60  # seconds
PULL_TIMEOUT = 30  # seconds
_SOURCE_URL_FILE = ".source_url"
_SOURCE_BRANCH_FILE = ".source_branch"

# URL scheme allowlist — reject ext:: (arbitrary command exec) and file:// (local FS read)
_ALLOWED_SCHEMES = ("https://", "git://", "ssh://", "http://")
_GIT_AT_PATTERN = re.compile(r"^[\w.-]+@[\w.-]+:")  # git@host:path SSH shorthand

# Regex to strip embedded credentials from URLs in error messages
_CREDENTIAL_PATTERN = re.compile(r"((?:https?|ssh|git)://)[^@/\s:]+(?::[^@/\s]+)?@", re.IGNORECASE)


def _validate_url_scheme(url: str) -> str | None:
    """Validate that a URL uses an allowed scheme. Returns error message or None."""
    if any(url.startswith(scheme) for scheme in _ALLOWED_SCHEMES):
        if url.startswith("http://"):
            logger.warning("Pack source URL uses plaintext HTTP (MITM risk): %s", _sanitize_url(url))
        return None
    if _GIT_AT_PATTERN.match(url):
        return None
    return f"URL scheme not allowed: {url.split(':', 1)[0]}. Use https://, ssh://, or git@host:path"


def _sanitize_git_stderr(stderr: str) -> str:
    """Strip embedded credentials from git stderr before surfacing in errors."""
    sanitized = _CREDENTIAL_PATTERN.sub(r"\1***@", stderr)
    sanitized = re.sub(r"Username for '[^']*'", "Username for '***'", sanitized)
    return sanitized


def _sanitize_url(url: str) -> str:
    """Strip embedded credentials from a URL for safe logging."""
    return _CREDENTIAL_PATTERN.sub(r"\1***@", url)


_CACHE_DIR_NAME = "cache"
_SOURCES_DIR_NAME = "sources"


@dataclass
class PackSourceResult:
    """Result of a pack source git operation."""

    success: bool
    path: Path | None = None
    error: str = ""
    changed: bool = False


@dataclass
class CachedSource:
    """A cached pack source repository."""

    url: str
    branch: str
    path: Path
    ref: str


def _cache_root(data_dir: Path) -> Path:
    """Return the root directory for cached pack sources."""
    return data_dir / _CACHE_DIR_NAME / _SOURCES_DIR_NAME


def resolve_cache_path(url: str, data_dir: Path) -> Path:
    """Return the deterministic cache directory for a source URL."""
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:12]
    return _cache_root(data_dir) / url_hash


def check_git_available() -> bool:
    """Check whether the ``git`` binary is available."""
    try:
        result = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def clone_source(
    url: str,
    branch: str,
    data_dir: Path,
    *,
    timeout: int = CLONE_TIMEOUT,
) -> PackSourceResult:
    """Shallow-clone a pack source repository into the cache.

    If the cache directory already exists, returns success with the
    existing path (use :func:`pull_source` to update).
    """
    url_error = _validate_url_scheme(url)
    if url_error:
        return PackSourceResult(success=False, error=url_error)

    cache_path = resolve_cache_path(url, data_dir)

    if cache_path.is_dir():
        logger.debug("Cache hit for %s at %s", url, cache_path)
        return PackSourceResult(success=True, path=cache_path)

    cache_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "-b", branch, "--", url, str(cache_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return PackSourceResult(success=False, error="git binary not found")
    except subprocess.TimeoutExpired:
        # Clean up partial clone
        if cache_path.exists():
            shutil.rmtree(cache_path, ignore_errors=True)
        return PackSourceResult(
            success=False,
            error=f"git clone timed out after {timeout}s",
        )
    except OSError as e:
        return PackSourceResult(success=False, error=f"OS error: {e}")

    if result.returncode != 0:
        # Clean up partial clone
        if cache_path.exists():
            shutil.rmtree(cache_path, ignore_errors=True)
        stderr = _sanitize_git_stderr(result.stderr.strip())
        return PackSourceResult(
            success=False,
            error=f"git clone failed (exit {result.returncode}): {stderr}",
        )

    # Write metadata files
    (cache_path / _SOURCE_URL_FILE).write_text(url, encoding="utf-8")
    (cache_path / _SOURCE_BRANCH_FILE).write_text(branch, encoding="utf-8")

    logger.info("Cloned pack source %s (branch: %s) into %s", _sanitize_url(url), branch, cache_path)
    return PackSourceResult(success=True, path=cache_path)


def pull_source(
    cache_path: Path,
    *,
    timeout: int = PULL_TIMEOUT,
) -> PackSourceResult:
    """Pull updates for a cached pack source repository.

    Returns a result with ``changed=True`` if new commits were fetched.
    """
    if not cache_path.is_dir():
        return PackSourceResult(
            success=False,
            error=f"cache directory does not exist: {cache_path}",
        )

    # Get ref before pull
    ref_before = get_source_ref(cache_path)

    try:
        result = subprocess.run(
            ["git", "-C", str(cache_path), "pull", "--ff-only"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return PackSourceResult(success=False, error="git binary not found")
    except subprocess.TimeoutExpired:
        return PackSourceResult(
            success=False,
            error=f"git pull timed out after {timeout}s",
        )
    except OSError as e:
        return PackSourceResult(success=False, error=f"OS error: {e}")

    if result.returncode != 0:
        stderr = _sanitize_git_stderr(result.stderr.strip())
        return PackSourceResult(
            success=False,
            error=f"git pull failed (exit {result.returncode}): {stderr}",
        )

    ref_after = get_source_ref(cache_path)
    changed = ref_before is not None and ref_after is not None and ref_before != ref_after

    logger.info(
        "Pulled pack source at %s (changed: %s)",
        cache_path,
        changed,
    )
    return PackSourceResult(success=True, path=cache_path, changed=changed)


def get_source_ref(cache_path: Path) -> str | None:
    """Return the current commit SHA for a cached source, or ``None`` on failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(cache_path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0:
        return None

    return result.stdout.strip()


def list_cached_sources(data_dir: Path) -> list[CachedSource]:
    """List all cached pack source repositories."""
    root = _cache_root(data_dir)
    if not root.is_dir():
        return []

    sources: list[CachedSource] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        url_file = entry / _SOURCE_URL_FILE
        if not url_file.is_file():
            logger.debug("Skipping cache entry %s: no %s file", entry.name, _SOURCE_URL_FILE)
            continue
        url = url_file.read_text(encoding="utf-8").strip()
        branch_file = entry / _SOURCE_BRANCH_FILE
        branch = branch_file.read_text(encoding="utf-8").strip() if branch_file.is_file() else "main"
        ref = get_source_ref(entry) or ""
        sources.append(CachedSource(url=url, branch=branch, path=entry, ref=ref))

    return sources


def remove_cached_source(url: str, data_dir: Path) -> bool:
    """Remove a cached pack source repository.

    Returns ``True`` if the cache directory was removed, ``False`` if it
    did not exist.
    """
    cache_path = resolve_cache_path(url, data_dir)
    if not cache_path.is_dir():
        return False

    shutil.rmtree(cache_path, ignore_errors=True)
    logger.info("Removed cached pack source for %s at %s", _sanitize_url(url), cache_path)
    return True


def ensure_source(
    url: str,
    branch: str,
    data_dir: Path,
    *,
    clone_timeout: int = CLONE_TIMEOUT,
    pull_timeout: int = PULL_TIMEOUT,
) -> PackSourceResult:
    """Ensure a pack source is cloned and up to date.

    Clones the source if it is not cached, otherwise pulls updates.
    """
    cache_path = resolve_cache_path(url, data_dir)
    already_cached = cache_path.is_dir()

    clone_result = clone_source(url, branch, data_dir, timeout=clone_timeout)
    if not clone_result.success:
        return clone_result

    # Only pull if the repo was already cached (clone_source returned
    # immediately on cache hit).  A fresh clone is already up to date.
    if already_cached:
        return pull_source(cache_path, timeout=pull_timeout)

    return clone_result

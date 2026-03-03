"""Space bootstrap — first-load cloning and pack installation."""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .pack_sources import _sanitize_git_stderr, _validate_url_scheme

_IS_WINDOWS = sys.platform == "win32"
_WINDOWS_FLAGS: dict = {"creationflags": 0x08000000} if _IS_WINDOWS else {}

logger = logging.getLogger(__name__)


@dataclass
class CloneResult:
    url: str
    local_path: str = ""
    success: bool = True
    error: str = ""


@dataclass
class BootstrapResult:
    clone_results: list[CloneResult] = field(default_factory=list)
    installed_packs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _extract_repo_name(url: str) -> str:
    name = url.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name or "repo"


def clone_repos(repos: list[str], repos_root: Path) -> list[CloneResult]:
    results: list[CloneResult] = []
    repos_root.mkdir(parents=True, exist_ok=True)

    for url in repos:
        err = _validate_url_scheme(url)
        if err:
            results.append(CloneResult(url=url, success=False, error=err))
            continue

        name = _extract_repo_name(url)
        dest = repos_root / name

        if dest.is_dir():
            results.append(CloneResult(url=url, local_path=str(dest), success=True))
            logger.info("Repo already exists: %s", dest)
            continue

        try:
            subprocess.run(
                ["git", "clone", "--depth=1", url, str(dest)],
                capture_output=True,
                text=True,
                timeout=120,
                check=True,
                **_WINDOWS_FLAGS,
            )
            results.append(CloneResult(url=url, local_path=str(dest), success=True))
            logger.info("Cloned %s to %s", url, dest)
        except subprocess.CalledProcessError as exc:
            stderr = _sanitize_git_stderr(exc.stderr or "")
            results.append(CloneResult(url=url, success=False, error=f"git clone failed: {stderr[:200]}"))
        except subprocess.TimeoutExpired:
            results.append(CloneResult(url=url, success=False, error="git clone timed out (120s)"))

    return results


def install_space_packs(
    db: Any,
    pack_sources: list[Any],
    packs: list[str],
    data_dir: Path,
) -> list[str]:
    installed: list[str] = []

    for pack_ref in packs:
        parts = pack_ref.split("/", 1)
        if len(parts) == 2:
            installed.append(pack_ref)
            logger.info("Pack queued for install: %s", pack_ref)
        else:
            logger.warning("Invalid pack reference (expected namespace/name): %s", pack_ref)

    return installed


def bootstrap_space(
    db: Any,
    space_config: Any,
    local_config: Any | None,
    data_dir: Path,
) -> BootstrapResult:
    result = BootstrapResult()

    repos_root = data_dir / "repos"
    if local_config and local_config.repos_root:
        repos_root = Path(local_config.repos_root)

    if space_config.repos:
        result.clone_results = clone_repos(space_config.repos, repos_root)
        for cr in result.clone_results:
            if not cr.success:
                result.errors.append(f"Clone failed for {cr.url}: {cr.error}")

    if space_config.packs:
        result.installed_packs = install_space_packs(db, space_config.pack_sources, space_config.packs, data_dir)

    return result

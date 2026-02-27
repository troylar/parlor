"""Unit tests for services/space_bootstrap.py — Space bootstrap."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from anteroom.services.space_bootstrap import (
    BootstrapResult,
    CloneResult,
    _extract_repo_name,
    bootstrap_space,
    clone_repos,
)
from anteroom.services.spaces import SpaceConfig


class TestExtractRepoName:
    def test_https_url(self) -> None:
        assert _extract_repo_name("https://github.com/org/repo.git") == "repo"

    def test_trailing_slash(self) -> None:
        assert _extract_repo_name("https://github.com/org/repo/") == "repo"

    def test_no_git_suffix(self) -> None:
        assert _extract_repo_name("https://github.com/org/repo") == "repo"


class TestCloneRepos:
    def test_skips_existing(self, tmp_path: Path) -> None:
        (tmp_path / "repo").mkdir()
        results = clone_repos(["https://github.com/org/repo.git"], tmp_path)
        assert len(results) == 1
        assert results[0].success is True

    def test_bad_url_scheme(self, tmp_path: Path) -> None:
        results = clone_repos(["ext::ssh://evil"], tmp_path)
        assert len(results) == 1
        assert results[0].success is False
        assert "URL scheme not allowed" in results[0].error

    @patch("anteroom.services.space_bootstrap.subprocess.run")
    def test_clone_success(self, mock_run: object, tmp_path: Path) -> None:
        results = clone_repos(["https://github.com/org/newrepo.git"], tmp_path)
        assert len(results) == 1
        assert results[0].success is True


class TestBootstrapSpace:
    @patch("anteroom.services.space_bootstrap.clone_repos")
    def test_bootstrap_with_repos(self, mock_clone: object, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        mock_clone.return_value = [CloneResult(url="https://github.com/org/repo.git", success=True)]
        cfg = SpaceConfig(name="test", repos=["https://github.com/org/repo.git"])
        result = bootstrap_space(MagicMock(), cfg, None, tmp_path)
        assert isinstance(result, BootstrapResult)
        assert len(result.clone_results) == 1
        assert result.errors == []

    @patch("anteroom.services.space_bootstrap.clone_repos")
    def test_bootstrap_records_errors(self, mock_clone: object, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        mock_clone.return_value = [CloneResult(url="https://bad.git", success=False, error="fail")]
        cfg = SpaceConfig(name="test", repos=["https://bad.git"])
        result = bootstrap_space(MagicMock(), cfg, None, tmp_path)
        assert len(result.errors) == 1

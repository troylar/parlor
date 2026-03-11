"""Tests for pack source git operations and cache management."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from anteroom.config import PackSourceConfig
from anteroom.services.pack_sources import (
    _sanitize_git_stderr,
    _validate_url_scheme,
    check_git_available,
    clone_source,
    ensure_source,
    get_source_ref,
    list_cached_sources,
    pull_source,
    remove_cached_source,
    resolve_cache_path,
)

_MODULE = "anteroom.services.pack_sources"
_SUBPROCESS_RUN = f"{_MODULE}.subprocess.run"

SAMPLE_URL = "https://github.com/anteroom-official/packs.git"
SAMPLE_URL_SSH = "git@github.com:anteroom-official/packs.git"
SAMPLE_BRANCH = "main"
SAMPLE_SHA = "abc123def456789012345678901234567890abcd"


def _completed(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class TestPackSourceConfig:
    def test_defaults(self) -> None:
        cfg = PackSourceConfig(url="https://example.com/repo.git")
        assert cfg.url == "https://example.com/repo.git"
        assert cfg.branch == "main"
        assert cfg.refresh_interval == 30

    def test_custom_values(self) -> None:
        cfg = PackSourceConfig(url="https://example.com/repo.git", branch="develop", refresh_interval=60)
        assert cfg.branch == "develop"
        assert cfg.refresh_interval == 60

    def test_refresh_interval_zero_is_manual(self) -> None:
        cfg = PackSourceConfig(url="https://example.com/repo.git", refresh_interval=0)
        assert cfg.refresh_interval == 0

    def test_refresh_interval_below_minimum_is_clamped(self) -> None:
        cfg = PackSourceConfig(url="https://example.com/repo.git", refresh_interval=2)
        assert cfg.refresh_interval == 5

    def test_auto_attach_default_true(self) -> None:
        cfg = PackSourceConfig(url="https://example.com/repo.git")
        assert cfg.auto_attach is True

    def test_auto_attach_explicit_false(self) -> None:
        cfg = PackSourceConfig(url="https://example.com/repo.git", auto_attach=False)
        assert cfg.auto_attach is False

    def test_priority_default_50(self) -> None:
        cfg = PackSourceConfig(url="https://example.com/repo.git")
        assert cfg.priority == 50

    def test_priority_custom(self) -> None:
        cfg = PackSourceConfig(url="https://example.com/repo.git", priority=10)
        assert cfg.priority == 10

    def test_priority_out_of_range_clamped(self) -> None:
        cfg = PackSourceConfig(url="https://example.com/repo.git", priority=200)
        assert cfg.priority == 50

    def test_priority_zero_clamped(self) -> None:
        cfg = PackSourceConfig(url="https://example.com/repo.git", priority=0)
        assert cfg.priority == 50


class TestResolveCachePath:
    def test_deterministic(self, tmp_path: Path) -> None:
        path1 = resolve_cache_path(SAMPLE_URL, tmp_path)
        path2 = resolve_cache_path(SAMPLE_URL, tmp_path)
        assert path1 == path2

    def test_different_urls_produce_different_paths(self, tmp_path: Path) -> None:
        path1 = resolve_cache_path(SAMPLE_URL, tmp_path)
        path2 = resolve_cache_path(SAMPLE_URL_SSH, tmp_path)
        assert path1 != path2

    def test_uses_sha256_prefix(self, tmp_path: Path) -> None:
        expected_hash = hashlib.sha256(SAMPLE_URL.encode()).hexdigest()[:12]
        path = resolve_cache_path(SAMPLE_URL, tmp_path)
        assert path.name == expected_hash

    def test_cache_path_structure(self, tmp_path: Path) -> None:
        path = resolve_cache_path(SAMPLE_URL, tmp_path)
        assert path.parent.name == "sources"
        assert path.parent.parent.name == "cache"


class TestCheckGitAvailable:
    def test_git_available(self) -> None:
        with patch(_SUBPROCESS_RUN, return_value=_completed(stdout="git version 2.40.0")) as mock:
            assert check_git_available() is True
            mock.assert_called_once()

    def test_git_not_found(self) -> None:
        with patch(_SUBPROCESS_RUN, side_effect=FileNotFoundError):
            assert check_git_available() is False

    def test_git_timeout(self) -> None:
        with patch(_SUBPROCESS_RUN, side_effect=subprocess.TimeoutExpired(cmd="git", timeout=10)):
            assert check_git_available() is False

    def test_git_nonzero_exit(self) -> None:
        with patch(_SUBPROCESS_RUN, return_value=_completed(returncode=1)):
            assert check_git_available() is False


class TestCloneSource:
    def test_successful_clone(self, tmp_path: Path) -> None:
        cache_path = resolve_cache_path(SAMPLE_URL, tmp_path)

        def fake_clone(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            cache_path.mkdir(parents=True, exist_ok=True)
            return _completed()

        with patch(_SUBPROCESS_RUN, side_effect=fake_clone):
            result = clone_source(SAMPLE_URL, SAMPLE_BRANCH, tmp_path)

        assert result.success is True
        assert result.path == cache_path
        assert (cache_path / ".source_url").read_text() == SAMPLE_URL
        assert (cache_path / ".source_branch").read_text() == SAMPLE_BRANCH

    def test_cache_hit_returns_existing(self, tmp_path: Path) -> None:
        cache_path = resolve_cache_path(SAMPLE_URL, tmp_path)
        cache_path.mkdir(parents=True)

        with patch(_SUBPROCESS_RUN) as mock:
            result = clone_source(SAMPLE_URL, SAMPLE_BRANCH, tmp_path)

        assert result.success is True
        assert result.path == cache_path
        mock.assert_not_called()

    def test_git_not_found(self, tmp_path: Path) -> None:
        with patch(_SUBPROCESS_RUN, side_effect=FileNotFoundError):
            result = clone_source(SAMPLE_URL, SAMPLE_BRANCH, tmp_path)

        assert result.success is False
        assert "git binary not found" in result.error

    def test_git_timeout_cleans_up(self, tmp_path: Path) -> None:
        cache_path = resolve_cache_path(SAMPLE_URL, tmp_path)

        def fake_timeout(*args: object, **kwargs: object) -> None:
            cache_path.mkdir(parents=True, exist_ok=True)
            raise subprocess.TimeoutExpired(cmd="git", timeout=60)

        with patch(_SUBPROCESS_RUN, side_effect=fake_timeout):
            result = clone_source(SAMPLE_URL, SAMPLE_BRANCH, tmp_path)

        assert result.success is False
        assert "timed out" in result.error
        assert not cache_path.exists()

    def test_nonzero_exit_cleans_up(self, tmp_path: Path) -> None:
        cache_path = resolve_cache_path(SAMPLE_URL, tmp_path)

        def fake_fail(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            cache_path.mkdir(parents=True, exist_ok=True)
            return _completed(returncode=128, stderr="fatal: repository not found")

        with patch(_SUBPROCESS_RUN, side_effect=fake_fail):
            result = clone_source(SAMPLE_URL, SAMPLE_BRANCH, tmp_path)

        assert result.success is False
        assert "repository not found" in result.error
        assert not cache_path.exists()

    def test_os_error(self, tmp_path: Path) -> None:
        with patch(_SUBPROCESS_RUN, side_effect=OSError("permission denied")):
            result = clone_source(SAMPLE_URL, SAMPLE_BRANCH, tmp_path)

        assert result.success is False
        assert "OS error" in result.error

    def test_clone_uses_correct_args(self, tmp_path: Path) -> None:
        cache_path = resolve_cache_path(SAMPLE_URL, tmp_path)

        def fake_clone(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            cache_path.mkdir(parents=True, exist_ok=True)
            return _completed()

        with patch(_SUBPROCESS_RUN, side_effect=fake_clone) as mock:
            clone_source(SAMPLE_URL, "develop", tmp_path)

        call_args = mock.call_args[0][0]
        assert call_args == ["git", "clone", "--depth", "1", "-b", "develop", "--", SAMPLE_URL, str(cache_path)]


class TestPullSource:
    def test_successful_pull_no_change(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "repo"
        cache_path.mkdir()

        with patch(_SUBPROCESS_RUN, return_value=_completed()):
            with patch(f"{_MODULE}.get_source_ref", return_value=SAMPLE_SHA):
                result = pull_source(cache_path)

        assert result.success is True
        assert result.changed is False

    def test_successful_pull_with_change(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "repo"
        cache_path.mkdir()

        refs = iter([SAMPLE_SHA, "new_sha_after_pull"])

        with patch(_SUBPROCESS_RUN, return_value=_completed()):
            with patch(f"{_MODULE}.get_source_ref", side_effect=lambda _: next(refs)):
                result = pull_source(cache_path)

        assert result.success is True
        assert result.changed is True

    def test_cache_dir_missing(self, tmp_path: Path) -> None:
        result = pull_source(tmp_path / "nonexistent")
        assert result.success is False
        assert "does not exist" in result.error

    def test_git_not_found(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "repo"
        cache_path.mkdir()

        with patch(f"{_MODULE}.get_source_ref", return_value=SAMPLE_SHA):
            with patch(_SUBPROCESS_RUN, side_effect=FileNotFoundError):
                result = pull_source(cache_path)

        assert result.success is False
        assert "git binary not found" in result.error

    def test_git_timeout(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "repo"
        cache_path.mkdir()

        with patch(f"{_MODULE}.get_source_ref", return_value=SAMPLE_SHA):
            with patch(_SUBPROCESS_RUN, side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30)):
                result = pull_source(cache_path)

        assert result.success is False
        assert "timed out" in result.error

    def test_nonzero_exit(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "repo"
        cache_path.mkdir()

        with patch(f"{_MODULE}.get_source_ref", return_value=SAMPLE_SHA):
            with patch(_SUBPROCESS_RUN, return_value=_completed(returncode=1, stderr="error: merge conflict")):
                result = pull_source(cache_path)

        assert result.success is False
        assert "merge conflict" in result.error

    def test_pull_uses_correct_args(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "repo"
        cache_path.mkdir()

        with patch(_SUBPROCESS_RUN, return_value=_completed()) as mock:
            with patch(f"{_MODULE}.get_source_ref", return_value=SAMPLE_SHA):
                pull_source(cache_path)

        call_args = mock.call_args[0][0]
        assert call_args == ["git", "-C", str(cache_path), "pull", "--ff-only"]


class TestGetSourceRef:
    def test_returns_sha(self, tmp_path: Path) -> None:
        with patch(_SUBPROCESS_RUN, return_value=_completed(stdout=f"  {SAMPLE_SHA}\n")):
            ref = get_source_ref(tmp_path)

        assert ref == SAMPLE_SHA

    def test_returns_none_on_failure(self, tmp_path: Path) -> None:
        with patch(_SUBPROCESS_RUN, return_value=_completed(returncode=128)):
            ref = get_source_ref(tmp_path)

        assert ref is None

    def test_returns_none_on_file_not_found(self, tmp_path: Path) -> None:
        with patch(_SUBPROCESS_RUN, side_effect=FileNotFoundError):
            ref = get_source_ref(tmp_path)

        assert ref is None

    def test_returns_none_on_timeout(self, tmp_path: Path) -> None:
        with patch(_SUBPROCESS_RUN, side_effect=subprocess.TimeoutExpired(cmd="git", timeout=10)):
            ref = get_source_ref(tmp_path)

        assert ref is None

    def test_uses_correct_args(self, tmp_path: Path) -> None:
        with patch(_SUBPROCESS_RUN, return_value=_completed(stdout=SAMPLE_SHA)) as mock:
            get_source_ref(tmp_path)

        call_args = mock.call_args[0][0]
        assert call_args == ["git", "-C", str(tmp_path), "rev-parse", "HEAD"]


class TestListCachedSources:
    def test_empty_cache(self, tmp_path: Path) -> None:
        sources = list_cached_sources(tmp_path)
        assert sources == []

    def test_lists_sources_with_metadata(self, tmp_path: Path) -> None:
        root = tmp_path / "cache" / "sources"
        root.mkdir(parents=True)

        # Create two cached source dirs
        dir1 = root / "abc123456789"
        dir1.mkdir()
        (dir1 / ".source_url").write_text(SAMPLE_URL)
        (dir1 / ".source_branch").write_text("main")

        dir2 = root / "def987654321"
        dir2.mkdir()
        (dir2 / ".source_url").write_text(SAMPLE_URL_SSH)
        (dir2 / ".source_branch").write_text("develop")

        with patch(f"{_MODULE}.get_source_ref", return_value=SAMPLE_SHA):
            sources = list_cached_sources(tmp_path)

        assert len(sources) == 2
        assert sources[0].url == SAMPLE_URL
        assert sources[0].branch == "main"
        assert sources[0].ref == SAMPLE_SHA
        assert sources[1].url == SAMPLE_URL_SSH
        assert sources[1].branch == "develop"

    def test_skips_dirs_without_source_url(self, tmp_path: Path) -> None:
        root = tmp_path / "cache" / "sources"
        root.mkdir(parents=True)

        # Dir without .source_url
        orphan = root / "orphan123456"
        orphan.mkdir()

        # Dir with .source_url
        valid = root / "valid1234567"
        valid.mkdir()
        (valid / ".source_url").write_text(SAMPLE_URL)

        with patch(f"{_MODULE}.get_source_ref", return_value=SAMPLE_SHA):
            sources = list_cached_sources(tmp_path)

        assert len(sources) == 1
        assert sources[0].url == SAMPLE_URL

    def test_defaults_branch_to_main(self, tmp_path: Path) -> None:
        root = tmp_path / "cache" / "sources"
        root.mkdir(parents=True)

        dir1 = root / "abc123456789"
        dir1.mkdir()
        (dir1 / ".source_url").write_text(SAMPLE_URL)
        # No .source_branch file

        with patch(f"{_MODULE}.get_source_ref", return_value=""):
            sources = list_cached_sources(tmp_path)

        assert len(sources) == 1
        assert sources[0].branch == "main"

    def test_skips_files_in_cache_root(self, tmp_path: Path) -> None:
        root = tmp_path / "cache" / "sources"
        root.mkdir(parents=True)
        (root / "some_file.txt").write_text("not a directory")

        sources = list_cached_sources(tmp_path)
        assert sources == []


class TestRemoveCachedSource:
    def test_removes_existing_cache(self, tmp_path: Path) -> None:
        cache_path = resolve_cache_path(SAMPLE_URL, tmp_path)
        cache_path.mkdir(parents=True)
        (cache_path / ".source_url").write_text(SAMPLE_URL)
        (cache_path / "some_file.txt").write_text("content")

        result = remove_cached_source(SAMPLE_URL, tmp_path)

        assert result is True
        assert not cache_path.exists()

    def test_returns_false_for_nonexistent(self, tmp_path: Path) -> None:
        result = remove_cached_source(SAMPLE_URL, tmp_path)
        assert result is False


class TestEnsureSource:
    def test_clones_when_missing(self, tmp_path: Path) -> None:
        cache_path = resolve_cache_path(SAMPLE_URL, tmp_path)

        call_count = 0

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # clone creates the directory
                cache_path.mkdir(parents=True, exist_ok=True)
            return _completed()

        with patch(_SUBPROCESS_RUN, side_effect=fake_run):
            with patch(f"{_MODULE}.get_source_ref", return_value=SAMPLE_SHA):
                result = ensure_source(SAMPLE_URL, SAMPLE_BRANCH, tmp_path)

        assert result.success is True

    def test_pulls_when_exists(self, tmp_path: Path) -> None:
        cache_path = resolve_cache_path(SAMPLE_URL, tmp_path)
        cache_path.mkdir(parents=True)

        with patch(_SUBPROCESS_RUN, return_value=_completed()):
            with patch(f"{_MODULE}.get_source_ref", return_value=SAMPLE_SHA):
                result = ensure_source(SAMPLE_URL, SAMPLE_BRANCH, tmp_path)

        assert result.success is True

    def test_clone_failure_propagates(self, tmp_path: Path) -> None:
        with patch(_SUBPROCESS_RUN, side_effect=FileNotFoundError):
            result = ensure_source(SAMPLE_URL, SAMPLE_BRANCH, tmp_path)

        assert result.success is False
        assert "git binary not found" in result.error

    def test_pull_failure_of_existing_cache(self, tmp_path: Path) -> None:
        cache_path = resolve_cache_path(SAMPLE_URL, tmp_path)
        cache_path.mkdir(parents=True)
        (cache_path / ".source_url").write_text(SAMPLE_URL)
        (cache_path / ".source_branch").write_text(SAMPLE_BRANCH)

        # clone_source sees cache hit and returns immediately (no subprocess call)
        # pull_source makes the subprocess call which fails
        pull_result = _completed(returncode=1, stderr="network unreachable")
        with patch(_SUBPROCESS_RUN, return_value=pull_result):
            with patch(f"{_MODULE}.get_source_ref", return_value=SAMPLE_SHA):
                result = ensure_source(SAMPLE_URL, SAMPLE_BRANCH, tmp_path)

        assert result.success is False
        assert "git pull failed" in result.error


class TestValidateUrlScheme:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/org/repo.git",
            "git://github.com/org/repo.git",
            "ssh://git@github.com/org/repo.git",
            "git@github.com:org/repo.git",
            "git@bitbucket.org:team/packs.git",
        ],
    )
    def test_allowed_schemes(self, url: str) -> None:
        assert _validate_url_scheme(url) is None

    @pytest.mark.parametrize(
        "url",
        [
            "http://internal.corp/repo.git",
            "ext::sh -c evil_command",
            "file:///etc/passwd",
            "ftp://example.com/repo.git",
            "data:text/plain,hello",
        ],
    )
    def test_rejected_schemes(self, url: str) -> None:
        error = _validate_url_scheme(url)
        assert error is not None


class TestSanitizeGitStderr:
    def test_strips_https_credentials(self) -> None:
        stderr = "fatal: could not read from https://oauth2:ghp_secret123@github.com/org/repo.git"
        sanitized = _sanitize_git_stderr(stderr)
        assert "ghp_secret123" not in sanitized
        assert "***@" in sanitized

    def test_strips_http_credentials(self) -> None:
        stderr = "fatal: Authentication failed for 'http://user:pass@internal.corp/repo.git'"
        sanitized = _sanitize_git_stderr(stderr)
        assert "pass" not in sanitized
        assert "***@" in sanitized

    def test_no_credentials_unchanged(self) -> None:
        stderr = "fatal: repository 'https://github.com/org/repo.git' not found"
        assert _sanitize_git_stderr(stderr) == stderr

    def test_empty_string(self) -> None:
        assert _sanitize_git_stderr("") == ""


class TestCloneSourceUrlValidation:
    def test_rejects_ext_scheme(self, tmp_path: Path) -> None:
        result = clone_source("ext::sh -c evil", "main", tmp_path)
        assert result.success is False
        assert "not allowed" in result.error

    def test_rejects_file_scheme(self, tmp_path: Path) -> None:
        result = clone_source("file:///etc/passwd", "main", tmp_path)
        assert result.success is False
        assert "not allowed" in result.error


class TestConfigParsing:
    _AI_BLOCK = "ai:\n  api_key: test\n  base_url: https://api.example.com\n"

    def test_pack_sources_parsed_from_config(self, tmp_path: Path) -> None:
        from anteroom.config import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._AI_BLOCK + "pack_sources:\n"
            "  - url: https://github.com/org/packs.git\n"
            "    branch: develop\n"
            "    refresh_interval: 60\n"
            "  - url: git@github.com:team/packs.git\n"
        )

        config, _ = load_config(config_file)

        assert len(config.pack_sources) == 2
        assert config.pack_sources[0].url == "https://github.com/org/packs.git"
        assert config.pack_sources[0].branch == "develop"
        assert config.pack_sources[0].refresh_interval == 60
        assert config.pack_sources[1].url == "git@github.com:team/packs.git"
        assert config.pack_sources[1].branch == "main"
        assert config.pack_sources[1].refresh_interval == 30

    def test_empty_pack_sources(self, tmp_path: Path) -> None:
        from anteroom.config import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text(self._AI_BLOCK)

        config, _ = load_config(config_file)

        assert config.pack_sources == []

    def test_invalid_pack_sources_skipped(self, tmp_path: Path) -> None:
        from anteroom.config import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._AI_BLOCK + "pack_sources:\n  - not_a_dict\n  - url: ''\n  - url: https://valid.com/repo.git\n"
        )

        config, _ = load_config(config_file)

        assert len(config.pack_sources) == 1
        assert config.pack_sources[0].url == "https://valid.com/repo.git"

    def test_auto_attach_and_priority_parsed(self, tmp_path: Path) -> None:
        from anteroom.config import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            self._AI_BLOCK + "pack_sources:\n"
            "  - url: https://github.com/org/packs.git\n"
            "    auto_attach: false\n"
            "    priority: 10\n"
            "  - url: https://github.com/team/packs.git\n"
        )

        config, _ = load_config(config_file)

        assert len(config.pack_sources) == 2
        assert config.pack_sources[0].auto_attach is False
        assert config.pack_sources[0].priority == 10
        # Second source uses defaults
        assert config.pack_sources[1].auto_attach is True
        assert config.pack_sources[1].priority == 50


class TestAddPackSourceSerialization:
    def test_add_pack_source_includes_auto_attach_and_priority(self, tmp_path: Path) -> None:
        import yaml

        from anteroom.services.pack_sources import add_pack_source

        config_path = tmp_path / "config.yaml"
        with patch("anteroom.config._get_config_path", return_value=config_path):
            result = add_pack_source("https://github.com/org/packs.git")

        assert result.ok is True
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        source = raw["pack_sources"][0]
        assert source["auto_attach"] is True
        assert source["priority"] == 50
        assert source["url"] == "https://github.com/org/packs.git"

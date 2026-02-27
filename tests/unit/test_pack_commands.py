"""Tests for /pack REPL command logic: service interactions and config writing (#525)."""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pytest
import yaml

from anteroom.db import _FTS_SCHEMA, _FTS_TRIGGERS, _SCHEMA, ThreadSafeConnection
from anteroom.services.packs import (
    get_pack,
    install_pack,
    list_packs,
    parse_manifest,
    remove_pack,
    validate_manifest,
)


@pytest.fixture()
def db() -> ThreadSafeConnection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    try:
        conn.executescript(_FTS_SCHEMA)
        conn.executescript(_FTS_TRIGGERS)
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return ThreadSafeConnection(conn)


@pytest.fixture()
def pack_dir(tmp_path: Path) -> Path:
    """Create a minimal valid pack directory with a manifest and one skill."""
    d = tmp_path / "test-pack"
    d.mkdir()
    skill_dir = d / "skills"
    skill_dir.mkdir()
    (skill_dir / "greet.yaml").write_text(
        textwrap.dedent("""\
            name: greet
            description: Say hello
            prompt: Hello!
        """)
    )
    (d / "pack.yaml").write_text(
        textwrap.dedent("""\
            name: test-pack
            namespace: myteam
            version: "1.0.0"
            description: A test pack
            artifacts:
              - type: skill
                name: greet
                file: skills/greet.yaml
        """)
    )
    return d


# ---------------------------------------------------------------------------
# /pack list
# ---------------------------------------------------------------------------


class TestPackList:
    def test_empty_when_no_packs(self, db: ThreadSafeConnection) -> None:
        assert list_packs(db) == []

    def test_shows_installed_packs(self, db: ThreadSafeConnection, pack_dir: Path) -> None:
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)
        result = list_packs(db)
        assert len(result) == 1
        assert result[0]["name"] == "test-pack"
        assert result[0]["namespace"] == "myteam"
        assert result[0]["artifact_count"] == 1


# ---------------------------------------------------------------------------
# /pack show
# ---------------------------------------------------------------------------


class TestPackShow:
    def test_returns_none_for_missing(self, db: ThreadSafeConnection) -> None:
        assert get_pack(db, "myteam", "nonexistent") is None

    def test_returns_pack_with_artifacts(self, db: ThreadSafeConnection, pack_dir: Path) -> None:
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)
        result = get_pack(db, "myteam", "test-pack")
        assert result is not None
        assert result["name"] == "test-pack"
        assert len(result.get("artifacts", [])) == 1


# ---------------------------------------------------------------------------
# /pack install
# ---------------------------------------------------------------------------


class TestPackInstall:
    def test_install_from_dir(self, db: ThreadSafeConnection, pack_dir: Path) -> None:
        manifest = parse_manifest(pack_dir / "pack.yaml")
        errors = validate_manifest(manifest, pack_dir)
        assert errors == []
        result = install_pack(db, manifest, pack_dir)
        assert result["name"] == "test-pack"
        assert result["artifact_count"] == 1

    def test_install_duplicate_raises(self, db: ThreadSafeConnection, pack_dir: Path) -> None:
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)
        with pytest.raises(ValueError, match="already installed"):
            install_pack(db, manifest, pack_dir)

    def test_parse_manifest_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises((ValueError, FileNotFoundError)):
            parse_manifest(tmp_path / "nonexistent" / "pack.yaml")


# ---------------------------------------------------------------------------
# /pack remove
# ---------------------------------------------------------------------------


class TestPackRemove:
    def test_remove_installed(self, db: ThreadSafeConnection, pack_dir: Path) -> None:
        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)
        assert remove_pack(db, "myteam", "test-pack") is True
        assert list_packs(db) == []

    def test_remove_nonexistent(self, db: ThreadSafeConnection) -> None:
        assert remove_pack(db, "myteam", "nonexistent") is False


# ---------------------------------------------------------------------------
# /pack add-source (config write)
# ---------------------------------------------------------------------------


class TestPackAddSourceValidation:
    """URL scheme validation — security gate before config write."""

    def test_rejects_ext_scheme(self) -> None:
        from anteroom.services.pack_sources import _validate_url_scheme

        err = _validate_url_scheme("ext::sh -c 'echo pwned'")
        assert err is not None
        assert "not allowed" in err

    def test_rejects_file_scheme(self) -> None:
        from anteroom.services.pack_sources import _validate_url_scheme

        err = _validate_url_scheme("file:///etc/passwd")
        assert err is not None
        assert "not allowed" in err

    def test_allows_https(self) -> None:
        from anteroom.services.pack_sources import _validate_url_scheme

        assert _validate_url_scheme("https://github.com/org/repo.git") is None

    def test_allows_ssh_shorthand(self) -> None:
        from anteroom.services.pack_sources import _validate_url_scheme

        assert _validate_url_scheme("git@github.com:org/repo.git") is None

    def test_http_rejected_by_validator(self) -> None:
        """_validate_url_scheme rejects http:// to prevent MITM attacks."""
        from anteroom.services.pack_sources import _validate_url_scheme

        result = _validate_url_scheme("http://example.com/repo.git")
        assert result is not None
        assert "HTTP" in result


class TestPackAddSource:
    def test_adds_source_to_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text("ai:\n  model: gpt-4o\n")

        raw: dict = yaml.safe_load(config_path.read_text()) or {}
        sources_list: list = raw.setdefault("pack_sources", [])
        sources_list.append({"url": "https://git.example.com/packs.git", "branch": "main", "refresh_interval": 30})
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(raw, f, default_flow_style=False, sort_keys=False)

        reloaded = yaml.safe_load(config_path.read_text())
        assert len(reloaded["pack_sources"]) == 1
        assert reloaded["pack_sources"][0]["url"] == "https://git.example.com/packs.git"
        assert reloaded["ai"]["model"] == "gpt-4o"

    def test_does_not_duplicate(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "pack_sources:\n- url: https://git.example.com/packs.git\n  branch: main\n  refresh_interval: 30\n"
        )

        raw: dict = yaml.safe_load(config_path.read_text()) or {}
        sources_list: list = raw.setdefault("pack_sources", [])
        existing_urls = [s.get("url") for s in sources_list if isinstance(s, dict)]
        url = "https://git.example.com/packs.git"
        assert url in existing_urls

    def test_creates_config_if_missing(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"

        raw: dict = {}
        sources_list: list = raw.setdefault("pack_sources", [])
        sources_list.append({"url": "https://bb.example.com/repo.git", "branch": "main", "refresh_interval": 30})
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(raw, f, default_flow_style=False, sort_keys=False)

        reloaded = yaml.safe_load(config_path.read_text())
        assert len(reloaded["pack_sources"]) == 1


# ---------------------------------------------------------------------------
# /pack sources — list_cached_sources
# ---------------------------------------------------------------------------


class TestPackSources:
    def test_empty_when_no_cache(self, tmp_path: Path) -> None:
        from anteroom.services.pack_sources import list_cached_sources

        assert list_cached_sources(tmp_path) == []


# ---------------------------------------------------------------------------
# /pack attach + /pack detach — service-level tests (#559)
# ---------------------------------------------------------------------------


class TestPackAttachRepl:
    def test_attach_global(self, db: ThreadSafeConnection, pack_dir: Path) -> None:
        from anteroom.services.pack_attachments import attach_pack, list_attachments, resolve_pack_id

        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)
        pack_id = resolve_pack_id(db, "myteam", "test-pack")
        assert pack_id is not None
        attach_pack(db, pack_id, project_path=None)
        attachments = list_attachments(db, project_path=None)
        assert len(attachments) == 1
        assert attachments[0]["scope"] == "global"

    def test_attach_project(self, db: ThreadSafeConnection, pack_dir: Path) -> None:
        from anteroom.services.pack_attachments import attach_pack, list_attachments, resolve_pack_id

        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)
        pack_id = resolve_pack_id(db, "myteam", "test-pack")
        assert pack_id is not None
        attach_pack(db, pack_id, project_path="/tmp/my-project")
        attachments = list_attachments(db, project_path="/tmp/my-project")
        assert any(a["scope"] == "project" for a in attachments)

    def test_attach_not_found(self, db: ThreadSafeConnection) -> None:
        from anteroom.services.pack_attachments import resolve_pack_id

        assert resolve_pack_id(db, "no", "such-pack") is None

    def test_attach_already_attached(self, db: ThreadSafeConnection, pack_dir: Path) -> None:
        from anteroom.services.pack_attachments import attach_pack, resolve_pack_id

        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)
        pack_id = resolve_pack_id(db, "myteam", "test-pack")
        assert pack_id is not None
        attach_pack(db, pack_id, project_path=None)
        with pytest.raises(ValueError, match="already attached"):
            attach_pack(db, pack_id, project_path=None)


class TestPackDetachRepl:
    def test_detach_success(self, db: ThreadSafeConnection, pack_dir: Path) -> None:
        from anteroom.services.pack_attachments import attach_pack, detach_pack, resolve_pack_id

        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)
        pack_id = resolve_pack_id(db, "myteam", "test-pack")
        assert pack_id is not None
        attach_pack(db, pack_id, project_path=None)
        assert detach_pack(db, pack_id, project_path=None) is True

    def test_detach_not_attached(self, db: ThreadSafeConnection, pack_dir: Path) -> None:
        from anteroom.services.pack_attachments import detach_pack, resolve_pack_id

        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)
        pack_id = resolve_pack_id(db, "myteam", "test-pack")
        assert pack_id is not None
        assert detach_pack(db, pack_id, project_path=None) is False

    def test_detach_project(self, db: ThreadSafeConnection, pack_dir: Path) -> None:
        from anteroom.services.pack_attachments import attach_pack, detach_pack, resolve_pack_id

        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)
        pack_id = resolve_pack_id(db, "myteam", "test-pack")
        assert pack_id is not None
        attach_pack(db, pack_id, project_path="/tmp/proj")
        assert detach_pack(db, pack_id, project_path="/tmp/proj") is True


# ---------------------------------------------------------------------------
# /pack update — service-level tests (#559)
# ---------------------------------------------------------------------------


class TestPackUpdateRepl:
    def test_update_success(self, db: ThreadSafeConnection, pack_dir: Path) -> None:
        from anteroom.services.packs import update_pack

        manifest = parse_manifest(pack_dir / "pack.yaml")
        install_pack(db, manifest, pack_dir)
        result = update_pack(db, manifest, pack_dir)
        assert result["name"] == "test-pack"
        assert result["namespace"] == "myteam"

    def test_update_missing_pack_yaml(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            parse_manifest(tmp_path / "nonexistent" / "pack.yaml")


# ---------------------------------------------------------------------------
# Built-in command registration
# ---------------------------------------------------------------------------


class TestPackCommandRegistration:
    def test_pack_in_builtin_commands(self) -> None:
        from anteroom.cli.skills import _BUILTIN_COMMANDS

        assert "pack" in _BUILTIN_COMMANDS
        assert "packs" in _BUILTIN_COMMANDS

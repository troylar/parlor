"""Unit tests for services/spaces.py — Space file parser."""

from __future__ import annotations

from pathlib import Path

from anteroom.services.spaces import (
    _NAME_PATTERN,
    SpaceConfig,
    SpaceLocalConfig,
    SpacePackSource,
    SpaceSource,
    file_hash,
    get_spaces_dir,
    is_local_space,
    parse_local_file,
    parse_space_file,
    slugify_dir_name,
    validate_space,
    write_local_file,
    write_space_file,
    write_space_template,
)


class TestNamePattern:
    def test_valid_names(self) -> None:
        for name in ["myspace", "my-space", "my_space", "A1", "a" * 64]:
            assert _NAME_PATTERN.match(name), f"{name!r} should be valid"

    def test_invalid_names(self) -> None:
        for name in ["", "-start", "_start", "a" * 65, "has space", "has/slash"]:
            assert not _NAME_PATTERN.match(name), f"{name!r} should be invalid"


class TestParseSpaceFile:
    def test_minimal(self, tmp_path: Path) -> None:
        f = tmp_path / "test.yaml"
        f.write_text("name: myspace\n")
        cfg = parse_space_file(f)
        assert cfg.name == "myspace"
        assert cfg.version == "1"
        assert cfg.repos == []

    def test_full(self, tmp_path: Path) -> None:
        f = tmp_path / "test.yaml"
        f.write_text(
            "name: myspace\n"
            "version: '2'\n"
            "repos:\n"
            "  - https://github.com/org/repo.git\n"
            "pack_sources:\n"
            "  - url: https://github.com/org/packs.git\n"
            "    branch: dev\n"
            "packs:\n"
            "  - org/pack1\n"
            "sources:\n"
            "  - path: /tmp/notes.md\n"
            "  - url: https://example.com/doc.md\n"
            "instructions: Be helpful\n"
            "config:\n"
            "  ai:\n"
            "    model: gpt-4\n"
        )
        cfg = parse_space_file(f)
        assert cfg.name == "myspace"
        assert cfg.version == "2"
        assert len(cfg.repos) == 1
        assert cfg.pack_sources[0].branch == "dev"
        assert cfg.packs == ["org/pack1"]
        assert cfg.sources[0].path == "/tmp/notes.md"
        assert cfg.sources[1].url == "https://example.com/doc.md"
        assert cfg.instructions == "Be helpful"
        assert cfg.config["ai"]["model"] == "gpt-4"

    def test_pack_sources_string_shorthand(self, tmp_path: Path) -> None:
        f = tmp_path / "test.yaml"
        f.write_text("name: myspace\npack_sources:\n  - https://github.com/org/packs.git\n")
        cfg = parse_space_file(f)
        assert cfg.pack_sources[0].url == "https://github.com/org/packs.git"
        assert cfg.pack_sources[0].branch == "main"

    def test_sources_string_shorthand(self, tmp_path: Path) -> None:
        f = tmp_path / "test.yaml"
        f.write_text("name: myspace\nsources:\n  - /tmp/file.md\n")
        cfg = parse_space_file(f)
        assert cfg.sources[0].path == "/tmp/file.md"

    def test_missing_file(self, tmp_path: Path) -> None:
        import pytest

        with pytest.raises(FileNotFoundError):
            parse_space_file(tmp_path / "nope.yaml")

    def test_not_a_mapping(self, tmp_path: Path) -> None:
        import pytest

        f = tmp_path / "test.yaml"
        f.write_text("- item\n")
        with pytest.raises(ValueError, match="YAML mapping"):
            parse_space_file(f)

    def test_bad_name(self, tmp_path: Path) -> None:
        import pytest

        f = tmp_path / "test.yaml"
        f.write_text("name: ''\n")
        with pytest.raises(ValueError, match="Invalid space name"):
            parse_space_file(f)

    def test_file_too_large(self, tmp_path: Path) -> None:
        import pytest

        f = tmp_path / "test.yaml"
        f.write_text("name: big\n" + "x" * (256 * 1024 + 1))
        with pytest.raises(ValueError, match="256KB"):
            parse_space_file(f)


class TestParseLocalFile:
    def test_parse(self, tmp_path: Path) -> None:
        f = tmp_path / "test.local.yaml"
        f.write_text("repos_root: /home/user/repos\npaths:\n  myrepo: /custom/path\n")
        lc = parse_local_file(f)
        assert lc.repos_root == "/home/user/repos"
        assert lc.paths["myrepo"] == "/custom/path"

    def test_empty(self, tmp_path: Path) -> None:
        f = tmp_path / "test.local.yaml"
        f.write_text("{}\n")
        lc = parse_local_file(f)
        assert lc.repos_root == ""
        assert lc.paths == {}


class TestWriteSpaceFile:
    def test_roundtrip(self, tmp_path: Path) -> None:
        f = tmp_path / "out.yaml"
        cfg = SpaceConfig(
            name="myspace",
            repos=["https://github.com/org/repo.git"],
            pack_sources=[SpacePackSource(url="https://github.com/org/packs.git")],
            packs=["org/pack1"],
            sources=[SpaceSource(path="/tmp/notes.md")],
            instructions="Be helpful",
            config={"ai": {"model": "gpt-4"}},
        )
        write_space_file(f, cfg)
        parsed = parse_space_file(f)
        assert parsed.name == cfg.name
        assert parsed.repos == cfg.repos
        assert parsed.packs == cfg.packs
        assert parsed.instructions == cfg.instructions


class TestWriteLocalFile:
    def test_roundtrip(self, tmp_path: Path) -> None:
        f = tmp_path / "out.local.yaml"
        lc = SpaceLocalConfig(repos_root="/home/user/repos", paths={"repo": "/path"})
        write_local_file(f, lc)
        parsed = parse_local_file(f)
        assert parsed.repos_root == lc.repos_root
        assert parsed.paths == lc.paths


class TestValidateSpace:
    def test_valid(self) -> None:
        cfg = SpaceConfig(name="ok", repos=["https://github.com/org/repo.git"])
        assert validate_space(cfg) == []

    def test_bad_url_scheme(self) -> None:
        cfg = SpaceConfig(name="ok", repos=["ext::ssh://evil"])
        errors = validate_space(cfg)
        assert len(errors) == 1
        assert "URL scheme not allowed" in errors[0]

    def test_path_traversal_in_sources(self) -> None:
        cfg = SpaceConfig(name="ok", sources=[SpaceSource(path="../../../etc/passwd")])
        errors = validate_space(cfg)
        assert any("path traversal" in e for e in errors)


class TestHelpers:
    def test_get_spaces_dir(self) -> None:
        d = get_spaces_dir()
        assert d.name == "spaces"
        assert d.parent.name == ".anteroom"

    def test_file_hash(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello")
        h = file_hash(f)
        assert isinstance(h, str)
        assert len(h) == 64

    def test_frozen_dataclass(self) -> None:
        import pytest

        cfg = SpaceConfig(name="test")
        with pytest.raises(AttributeError):
            cfg.name = "other"  # type: ignore[misc]


class TestSlugifyDirName:
    def test_simple_name(self) -> None:
        assert slugify_dir_name("myproject") == "myproject"

    def test_strips_leading_dots(self) -> None:
        assert slugify_dir_name(".hidden") == "hidden"

    def test_strips_leading_hyphens(self) -> None:
        assert slugify_dir_name("--start") == "start"

    def test_replaces_spaces(self) -> None:
        assert slugify_dir_name("my project") == "my-project"

    def test_collapses_hyphens(self) -> None:
        assert slugify_dir_name("a   b") == "a-b"

    def test_empty_after_strip(self) -> None:
        assert slugify_dir_name("...") == ""

    def test_all_spaces(self) -> None:
        assert slugify_dir_name("   ") == ""

    def test_truncates_to_64(self) -> None:
        result = slugify_dir_name("a" * 100)
        assert len(result) == 64

    def test_special_chars(self) -> None:
        assert slugify_dir_name("my@project!v2") == "my-project-v2"

    def test_empty_string(self) -> None:
        assert slugify_dir_name("") == ""

    def test_underscores_preserved(self) -> None:
        assert slugify_dir_name("my_project") == "my_project"


class TestIsLocalSpace:
    def test_global_path(self) -> None:
        global_path = str(get_spaces_dir() / "myspace.yaml")
        assert is_local_space(global_path) is False

    def test_local_path(self, tmp_path: Path) -> None:
        local_path = str(tmp_path / ".anteroom" / "space.yaml")
        assert is_local_space(local_path) is True

    def test_empty_string(self) -> None:
        assert is_local_space("") is True


class TestWriteSpaceTemplate:
    def test_creates_file(self, tmp_path: Path) -> None:
        target = tmp_path / ".anteroom" / "space.yaml"
        write_space_template(target, "myproject")
        assert target.exists()

    def test_contains_name(self, tmp_path: Path) -> None:
        target = tmp_path / "space.yaml"
        write_space_template(target, "myproject")
        content = target.read_text()
        assert "name: myproject" in content

    def test_parseable_yaml(self, tmp_path: Path) -> None:
        target = tmp_path / "space.yaml"
        write_space_template(target, "myproject")
        cfg = parse_space_file(target)
        assert cfg.name == "myproject"
        assert cfg.version == "1"
        assert cfg.instructions == ""

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        target = tmp_path / "deep" / "nested" / "space.yaml"
        write_space_template(target, "test")
        assert target.exists()

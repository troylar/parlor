"""Tests for project-scoped configuration."""

from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from anteroom.services.project_config import discover_project_config, load_project_config


def _r(p: str | Path) -> Path:
    return Path(p).resolve()


def _trust_config(path: Path, data_dir: Path) -> None:
    """Pre-trust a config file in the trust store."""
    from anteroom.services.trust import compute_content_hash, save_trust_decision

    content = path.read_text(encoding="utf-8")
    content_hash = compute_content_hash(content)
    save_trust_decision(str(path.resolve()), content_hash, recursive=False, data_dir=data_dir)


class TestDiscoverProjectConfig:
    def test_finds_anteroom_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _r(tmpdir) / ".anteroom" / "config.yaml"
            cfg.parent.mkdir(parents=True)
            cfg.write_text("ai:\n  model: gpt-4\n")
            result = discover_project_config(tmpdir)
            assert result == cfg

    def test_finds_claude_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _r(tmpdir) / ".claude" / "config.yaml"
            cfg.parent.mkdir(parents=True)
            cfg.write_text("ai:\n  model: gpt-4\n")
            result = discover_project_config(tmpdir)
            assert result == cfg

    def test_anteroom_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            anteroom = _r(tmpdir) / ".anteroom" / "config.yaml"
            anteroom.parent.mkdir(parents=True)
            anteroom.write_text("ai:\n  model: anteroom\n")
            claude = _r(tmpdir) / ".claude" / "config.yaml"
            claude.parent.mkdir(parents=True)
            claude.write_text("ai:\n  model: claude\n")
            result = discover_project_config(tmpdir)
            assert result == anteroom

    def test_walks_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _r(tmpdir) / ".anteroom" / "config.yaml"
            cfg.parent.mkdir(parents=True)
            cfg.write_text("ai:\n  model: gpt-4\n")
            child = _r(tmpdir) / "src" / "module"
            child.mkdir(parents=True)
            result = discover_project_config(str(child))
            assert result == cfg

    def test_returns_none_when_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = discover_project_config(tmpdir)
            assert result is None


class TestLoadProjectConfig:
    def _setup(self, tmpdir: str) -> tuple[Path, Path]:
        """Create data dir and project config dir, return (data_dir, proj_dir)."""
        data_dir = _r(tmpdir) / "data"
        data_dir.mkdir(parents=True)
        proj_dir = _r(tmpdir) / "project" / ".anteroom"
        proj_dir.mkdir(parents=True)
        return data_dir, proj_dir

    def test_loads_valid_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir, proj_dir = self._setup(tmpdir)
            cfg = proj_dir / "config.yaml"
            cfg.write_text("ai:\n  model: llama3\n")
            _trust_config(cfg, data_dir)

            raw, required = load_project_config(cfg, data_dir)
            assert raw["ai"]["model"] == "llama3"
            assert required == []

    def test_extracts_required_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir, proj_dir = self._setup(tmpdir)
            cfg = proj_dir / "config.yaml"
            cfg.write_text(
                yaml.dump(
                    {
                        "required": [
                            {"path": "ai.api_key", "description": "Your API key"},
                            {"path": "ai.base_url", "description": "API endpoint"},
                        ],
                    }
                )
            )
            _trust_config(cfg, data_dir)

            raw, required = load_project_config(cfg, data_dir)
            assert len(required) == 2
            assert required[0]["path"] == "ai.api_key"
            assert required[0]["description"] == "Your API key"
            assert "required" not in raw

    def test_skips_untrusted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir, proj_dir = self._setup(tmpdir)
            cfg = proj_dir / "config.yaml"
            cfg.write_text("ai:\n  model: llama3\n")

            raw, required = load_project_config(cfg, data_dir, interactive=False)
            assert raw == {}
            assert required == []

    def test_skips_invalid_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir, proj_dir = self._setup(tmpdir)
            cfg = proj_dir / "config.yaml"
            cfg.write_text("not: valid: yaml: [[[")
            _trust_config(cfg, data_dir)

            raw, required = load_project_config(cfg, data_dir)
            assert raw == {}
            assert required == []

    def test_skips_non_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir, proj_dir = self._setup(tmpdir)
            cfg = proj_dir / "config.yaml"
            cfg.write_text("- just\n- a\n- list\n")
            _trust_config(cfg, data_dir)

            raw, required = load_project_config(cfg, data_dir)
            assert raw == {}

    def test_invalid_required_entries_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir, proj_dir = self._setup(tmpdir)
            cfg = proj_dir / "config.yaml"
            cfg.write_text(
                yaml.dump(
                    {
                        "required": [
                            {"path": "ai.api_key", "description": "Valid"},
                            "just a string",
                            {"no_path_key": True},
                        ],
                    }
                )
            )
            _trust_config(cfg, data_dir)

            raw, required = load_project_config(cfg, data_dir)
            assert len(required) == 1
            assert required[0]["path"] == "ai.api_key"


class TestProjectConfigIntegration:
    """Integration tests: load_config uses path.parent as the trust store data_dir."""

    def test_project_config_overlays_personal(self) -> None:
        from anteroom.config import load_config

        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)

            # Personal config — trust store lives in its parent dir
            personal = base / "config.yaml"
            personal.write_text(
                yaml.dump(
                    {
                        "ai": {"base_url": "http://personal:8080", "api_key": "personal-key", "model": "gpt-4"},
                    }
                )
            )

            proj_dir = base / "project" / ".anteroom"
            proj_dir.mkdir(parents=True)
            proj_cfg = proj_dir / "config.yaml"
            proj_cfg.write_text(
                yaml.dump(
                    {
                        "ai": {"model": "llama3"},
                    }
                )
            )
            # Trust against personal config's parent (base), which is what load_config uses
            _trust_config(proj_cfg, base)

            cfg, _ = load_config(
                personal,
                project_config_path=proj_cfg,
            )
            assert cfg.ai.model == "llama3"
            assert cfg.ai.base_url == "http://personal:8080"

    def test_team_enforcement_overrides_project(self) -> None:
        from anteroom.config import load_config
        from anteroom.services.trust import compute_content_hash, save_trust_decision

        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)

            team_path = base / "team.yaml"
            team_content = yaml.dump(
                {
                    "ai": {"base_url": "http://team:8080", "api_key": "team-key"},
                    "enforce": ["ai.base_url"],
                }
            )
            team_path.write_text(team_content)
            team_hash = compute_content_hash(team_content)
            save_trust_decision(str(team_path), team_hash, recursive=False, data_dir=base)

            personal = base / "config.yaml"
            personal.write_text(
                yaml.dump(
                    {
                        "ai": {"base_url": "http://personal:8080", "api_key": "personal-key"},
                    }
                )
            )

            proj_dir = base / "project" / ".anteroom"
            proj_dir.mkdir(parents=True)
            proj_cfg = proj_dir / "config.yaml"
            proj_cfg.write_text(
                yaml.dump(
                    {
                        "ai": {"base_url": "http://project:9090", "model": "llama3"},
                    }
                )
            )
            _trust_config(proj_cfg, base)

            cfg, enforced = load_config(
                personal,
                team_config_path=team_path,
                project_config_path=proj_cfg,
                interactive=False,
            )
            assert cfg.ai.base_url == "http://team:8080"
            assert cfg.ai.model == "llama3"
            assert "ai.base_url" in enforced

    def test_mcp_servers_from_project(self) -> None:
        from anteroom.config import load_config

        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)

            personal = base / "config.yaml"
            personal.write_text(
                yaml.dump(
                    {
                        "ai": {"base_url": "http://localhost:8080", "api_key": "key"},
                        "mcp_servers": [
                            {"name": "global-fs", "transport": "stdio", "command": "npx"},
                        ],
                    }
                )
            )

            proj_dir = base / "project" / ".anteroom"
            proj_dir.mkdir(parents=True)
            proj_cfg = proj_dir / "config.yaml"
            proj_cfg.write_text(
                yaml.dump(
                    {
                        "mcp_servers": [
                            {"name": "project-db", "transport": "stdio", "command": "db-tool"},
                        ],
                    }
                )
            )
            _trust_config(proj_cfg, base)

            cfg, _ = load_config(
                personal,
                project_config_path=proj_cfg,
            )
            names = [s.name for s in cfg.mcp_servers]
            assert "project-db" in names

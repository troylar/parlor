"""Tests for references config section."""

from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from anteroom.config import ReferencesConfig, load_config


def _r(p: str | Path) -> Path:
    return Path(p).resolve()


class TestReferencesConfig:
    def test_defaults_empty(self) -> None:
        cfg = ReferencesConfig()
        assert cfg.instructions == []
        assert cfg.rules == []
        assert cfg.skills == []

    def test_loads_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            cfg_path = base / "config.yaml"
            cfg_path.write_text(
                yaml.dump(
                    {
                        "ai": {"base_url": "http://localhost:8080", "api_key": "k"},
                        "references": {
                            "instructions": ["team/instructions.md", "team/setup.md"],
                            "rules": ["team/rules/no-eval.md"],
                            "skills": ["team/skills/deploy.md"],
                        },
                    }
                )
            )

            config, _ = load_config(cfg_path)
            assert config.references.instructions == ["team/instructions.md", "team/setup.md"]
            assert config.references.rules == ["team/rules/no-eval.md"]
            assert config.references.skills == ["team/skills/deploy.md"]

    def test_missing_references_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            cfg_path = base / "config.yaml"
            cfg_path.write_text(
                yaml.dump(
                    {
                        "ai": {"base_url": "http://localhost:8080", "api_key": "k"},
                    }
                )
            )

            config, _ = load_config(cfg_path)
            assert config.references.instructions == []
            assert config.references.rules == []
            assert config.references.skills == []

    def test_filters_non_string_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            cfg_path = base / "config.yaml"
            cfg_path.write_text(
                yaml.dump(
                    {
                        "ai": {"base_url": "http://localhost:8080", "api_key": "k"},
                        "references": {
                            "instructions": ["valid.md", 123, None, "", "also-valid.md"],
                        },
                    }
                )
            )

            config, _ = load_config(cfg_path)
            assert config.references.instructions == ["valid.md", "also-valid.md"]

    def test_invalid_references_section_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            cfg_path = base / "config.yaml"
            cfg_path.write_text(
                yaml.dump(
                    {
                        "ai": {"base_url": "http://localhost:8080", "api_key": "k"},
                        "references": "not-a-dict",
                    }
                )
            )

            config, _ = load_config(cfg_path)
            assert config.references.instructions == []

    def test_partial_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)
            cfg_path = base / "config.yaml"
            cfg_path.write_text(
                yaml.dump(
                    {
                        "ai": {"base_url": "http://localhost:8080", "api_key": "k"},
                        "references": {
                            "rules": ["rule1.md"],
                        },
                    }
                )
            )

            config, _ = load_config(cfg_path)
            assert config.references.instructions == []
            assert config.references.rules == ["rule1.md"]
            assert config.references.skills == []

    def test_references_from_project_config(self) -> None:
        from anteroom.services.trust import compute_content_hash, save_trust_decision

        with tempfile.TemporaryDirectory() as tmpdir:
            base = _r(tmpdir)

            personal = base / "config.yaml"
            personal.write_text(
                yaml.dump(
                    {
                        "ai": {"base_url": "http://localhost:8080", "api_key": "k"},
                    }
                )
            )

            proj_dir = base / "project" / ".anteroom"
            proj_dir.mkdir(parents=True)
            proj_cfg = proj_dir / "config.yaml"
            proj_cfg.write_text(
                yaml.dump(
                    {
                        "references": {
                            "instructions": ["project-instructions.md"],
                            "skills": ["project-skill.md"],
                        },
                    }
                )
            )

            content = proj_cfg.read_text(encoding="utf-8")
            content_hash = compute_content_hash(content)
            save_trust_decision(str(proj_cfg.resolve()), content_hash, recursive=False, data_dir=base)

            config, _ = load_config(personal, project_config_path=proj_cfg)
            assert "project-instructions.md" in config.references.instructions
            assert "project-skill.md" in config.references.skills
